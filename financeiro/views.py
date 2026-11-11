import calendar
from functools import wraps
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, F, Q, Sum, prefetch_related_objects
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from accounts.models import LogAuditoria
from accounts.paginacao import POR_PAGINA, paginar
from agenda.models import Agendamento, AgendamentoItem, GuiaConvenio, PacoteVendido, PagamentoParticular
from cadastros.models import Convenio, Profissional

from .forms import (
    CorrigirPagamentoForm,
    EstornoPagamentoForm,
    LancarValorConvenioForm,
    LoteFaturamentoForm,
    MovimentacaoCaixaForm,
    RepassePagamentoForm,
    RetornoLoteItemForm,
)
from .models import LoteFaturamento, MovimentacaoCaixa, RepassePagamento, RetornoLoteItem
from accounts.moeda import formatar_moeda


def _semana_de(data):
    """Segunda a domingo da semana que contém 'data' — mesmo cálculo usado em
    agenda.views._semana_de, pro Painel financeiro usar a mesma navegação semanal
    já validada na tela de Repasses."""
    inicio = data - timedelta(days=data.weekday())
    fim = inicio + timedelta(days=6)
    return inicio, fim


def _produzido_num(agendamento):
    """Valor produzido do item em formato JS-seguro (ponto decimal, sem localização) — os
    templates de retorno usam isto num atributo 'data-produzido' pro JS comparar contra
    Pago+Glosado enquanto o usuário digita (interpolar '{{ valor }}' direto no template usaria
    vírgula decimal por causa do LANGUAGE_CODE pt-br, o que quebraria parseFloat no navegador)."""
    return f"{(agendamento.valor_convenio or Decimal('0.00')):.2f}"


@permission_required("accounts.financeiro_ver_painel", raise_exception=True)
def painel_financeiro(request):
    """
    Visão geral da semana: quanto entrou de particular (por forma de pagamento),
    quanto ainda falta repassar aos profissionais (resumo — detalhe continua em
    Agenda → Repasses semanais), quantos atendimentos foram cortesia (sem
    cobrança), quanto de convênio está a receber x já recebido, e a posição de caixa
    da semana — esta é a tela interna do financeiro/dono (nunca impressa ou entregue a
    um profissional), por isso é aqui, e não no Fechamento diário/mensal, que mostra
    "caixa" e "estornos" (ver _contexto_fechamento, que não calcula mais isso).
    """
    hoje = timezone.localdate()
    data_referencia = parse_date(request.GET.get("data") or "") or hoje
    intervalo_inicio, intervalo_fim = _semana_de(data_referencia)

    realizados_semana = Agendamento.objects.filter(
        status__in=Agendamento.STATUS_PRODUCAO,
        data_hora_inicio__date__range=(intervalo_inicio, intervalo_fim),
    )

    # Cada forma de pagamento de um atendimento particular é uma linha própria (ver
    # agenda.models.PagamentoParticular) — um atendimento com 2 formas conta as duas
    # separadamente; cortesia entra na contagem, mas fica de fora dos totais monetários
    # e da tabela "recebido por forma" (mostrar "Cortesia: R$ 0,00" ali só confundiria).
    pagamentos_semana_qs = PagamentoParticular.objects.filter(
        agendamento__status__in=Agendamento.STATUS_PRODUCAO,
        agendamento__data_hora_inicio__date__range=(intervalo_inicio, intervalo_fim),
    ).exclude(estornado=True)
    pagamentos_reais_qs = pagamentos_semana_qs.exclude(forma_pagamento=Agendamento.FormaPagamento.CORTESIA)
    # Coalesce(valor_recebido, valor): cartão débito/crédito usa o valor real recebido (já
    # descontada a taxa da maquininha) quando informado; as demais formas (e cartões registrados
    # antes desta feature existir, sem valor_recebido preenchido) caem pro valor cheio — ver
    # agenda.models.PagamentoParticular.valor_recebido_efetivo (mesma regra, versão Python).
    # Esta é a tela do proprietário/financeiro (nunca do profissional, ver docstring da view),
    # por isso mostra o valor real recebido, diferente de repasse_pendente_total logo abaixo.
    valor_recebido_db = Coalesce("valor_recebido", "valor")
    recebido_particular_total = pagamentos_reais_qs.aggregate(total=Sum(valor_recebido_db))["total"] or Decimal("0.00")
    recebido_por_forma = [
        (rotulo, pagamentos_reais_qs.filter(forma_pagamento=codigo).aggregate(total=Sum(valor_recebido_db))["total"] or Decimal("0.00"))
        for codigo, rotulo in Agendamento.FormaPagamento.choices
        if codigo != Agendamento.FormaPagamento.CORTESIA
    ]
    cortesia_qtd = pagamentos_semana_qs.filter(forma_pagamento=Agendamento.FormaPagamento.CORTESIA).count()

    # Retorno grátis (ver AgendamentoItem.eh_retorno/retorno_de) — calculado a partir dos
    # itens do próprio Agendamento, não de PagamentoParticular/cortesia_qtd acima:
    # convênio nunca cria uma linha de PagamentoParticular, então uma contagem baseada em
    # cortesia perderia os retornos de convênio silenciosamente. distinct() conta
    # agendamentos (não itens) — um agendamento com mais de um item de retorno (incomum)
    # ainda conta uma vez só, mesma unidade de contagem de sempre.
    retorno_qtd = realizados_semana.filter(AgendamentoItem.filtro_retorno("itens__")).distinct().count()

    # Saldo LÍQUIDO ainda a repassar dos atendimentos da semana (dinheiro também, desde
    # 26/10/2026), já descontando pagamentos parciais (ver
    # financeiro.models.RepassePagamento) — mesma conta de agenda.views.relatorio_repasses_semanal.
    repasse_pendente = list(
        pagamentos_reais_qs.filter(Agendamento.filtro_pago_como_particular("agendamento__"))
        # Proprietário não recebe repasse (mesma regra de repasse_pagamento_lista).
        .exclude(agendamento__profissional__eh_proprietario=True)
        .filter(repasse_pago=False)
        .select_related("agendamento__profissional")
    )
    repasse_pendente_total = sum((p.saldo_repasse for p in repasse_pendente), Decimal("0.00"))
    repasse_pendente_qtd = len(repasse_pendente)

    convenio_qs = realizados_semana.filter(Agendamento.filtro_convenio_a_faturar())
    # itens__retorno_de__isnull=True: exige pelo menos um item que NÃO é retorno grátis —
    # um agendamento com TODOS os itens em retorno nunca tem nada a receber do convênio
    # (valor sempre R$ 0,00, forçado em agendamento_mudar_status) — sem essa exclusão, ele
    # passaria em valor_convenio__isnull=False (0.00 não é NULL) e infla a contagem de "a
    # receber" mesmo não havendo nada pendente de verdade. Um agendamento MISTO (retorno +
    # item pago) continua contando, porque o item pago ainda tem algo a receber.
    convenio_a_receber_qs = convenio_qs.filter(
        AgendamentoItem.filtro_nao_retorno("itens__"), convenio_recebido=False, valor_convenio__isnull=False
    ).distinct()
    convenio_a_receber_total = convenio_a_receber_qs.aggregate(total=Sum("valor_convenio"))["total"] or Decimal("0.00")
    convenio_a_receber_qtd = convenio_a_receber_qs.count()
    convenio_pendente_lancamento_qtd = convenio_qs.filter(valor_convenio__isnull=True).count()
    # Sessões de 1h por convênio já atendidas com 2ª guia ainda não autorizada — sem
    # recorte de semana: a guia pendente precisa ser cobrada antes de faturar, seja de
    # quando for (ver agenda.views.guias_pendentes).
    guias_sessao_pendentes_qtd = (
        AgendamentoItem.com_guia_pendente()
        .filter(agendamento__status__in=Agendamento.STATUS_PRODUCAO)
        .aggregate(total=Sum(F("quantidade") - F("guias_extras_autorizadas") - 1))["total"]
        or 0
    )

    # status=REALIZADO aqui é necessário: cancelar um agendamento (ver
    # agenda.views.agendamento_cancelar) só muda o status pra CANCELADO, não mexe em
    # convenio_recebido/valor_convenio (que continuam com os valores de quando foi
    # Realizado) — sem este filtro, um atendimento cancelado DEPOIS de já marcado como
    # recebido continuava contando aqui pra sempre (só desaparecia se o registro fosse
    # excluído da agenda, não ao cancelar — bug relatado pelo cliente).
    convenio_recebido_qs = Agendamento.objects.filter(
        Agendamento.filtro_convenio_a_faturar(),
        status__in=Agendamento.STATUS_PRODUCAO,
        convenio_recebido=True,
        convenio_recebido_em__date__range=(intervalo_inicio, intervalo_fim),
    )
    convenio_recebido_total = convenio_recebido_qs.aggregate(total=Sum("valor_convenio"))["total"] or Decimal("0.00")

    # Posição de caixa da semana: "caixa" aqui é dinheiro físico (cartão/Pix cai em conta, o
    # quanto entrou por eles já aparece em "recebido_por_forma" acima) — ver _dinheiro_no_caixa.
    linhas_semana, _ = _producao_por_profissional(intervalo_inicio, intervalo_fim)
    estornos_semana, estornos_semana_total = _estornos_do_periodo(intervalo_inicio, intervalo_fim)
    avulsas_semana, avulsas_entradas_semana, avulsas_saidas_semana = _movimentacoes_avulsas_do_periodo(
        intervalo_inicio, intervalo_fim
    )
    caixa_dinheiro_semana = _dinheiro_no_caixa(
        linhas_semana, intervalo_inicio, intervalo_fim, avulsas_entradas_semana, avulsas_saidas_semana
    )

    return render(
        request,
        "financeiro/painel.html",
        {
            "data_referencia": data_referencia,
            "intervalo_inicio": intervalo_inicio,
            "intervalo_fim": intervalo_fim,
            "semana_anterior": intervalo_inicio - timedelta(days=7),
            "semana_seguinte": intervalo_inicio + timedelta(days=7),
            "total_atendimentos_realizados": realizados_semana.count(),
            "recebido_particular_total": recebido_particular_total,
            "recebido_por_forma": recebido_por_forma,
            "cortesia_qtd": cortesia_qtd,
            "retorno_qtd": retorno_qtd,
            "repasse_pendente_total": repasse_pendente_total,
            "repasse_pendente_qtd": repasse_pendente_qtd,
            "convenio_a_receber_total": convenio_a_receber_total,
            "convenio_a_receber_qtd": convenio_a_receber_qtd,
            "convenio_pendente_lancamento_qtd": convenio_pendente_lancamento_qtd,
            "guias_sessao_pendentes_qtd": guias_sessao_pendentes_qtd,
            "convenio_recebido_total": convenio_recebido_total,
            "caixa_dinheiro_semana": caixa_dinheiro_semana,
            "estornos_semana_total": estornos_semana_total,
            "estornos_semana_qtd": len(estornos_semana),
            "avulsas_entradas_semana": avulsas_entradas_semana,
            "avulsas_saidas_semana": avulsas_saidas_semana,
            "avulsas_qtd_semana": len(avulsas_semana),
        },
    )


def _bloqueio_correcao_pagamento(pagamentos, hoje):
    """Por que o pagamento deste atendimento NÃO pode ser corrigido (texto), ou None se pode.
    Pedido da clínica (23/10/2026): corrigir lançamento errado — sem limite de data (no começo
    era só hoje/ontem; a clínica pediu pra abrir). Se o dinheiro já andou (repasse pago ao
    profissional, estorno), a correção vira acerto manual/estorno, pra não mexer em valor já entregue."""
    if not pagamentos:
        return "não há pagamento registrado."
    if any(p.estornado for p in pagamentos):
        return "tem pagamento estornado."
    if any(p.forma_pagamento == Agendamento.FormaPagamento.CORTESIA for p in pagamentos):
        return "é cortesia/retorno (sem valor a corrigir)."
    if any(p.repasse_pago or p.repasse_valor_pago for p in pagamentos):
        return "o repasse deste pagamento já foi pago (total ou em parte) ao profissional."
    return None


def _descrever_pagamentos(linhas):
    return " + ".join(
        f"{Agendamento.FormaPagamento(l['forma_pagamento']).label} R$ {formatar_moeda(l['valor'])}"
        + (f" (recebido R$ {formatar_moeda(l['valor_recebido'])})" if l.get("valor_recebido") is not None else "")
        + (f" {l['parcelas']}x" if l.get("parcelas") else "")
        for l in linhas
    )


@permission_required("accounts.fila_autorizar_financeiro", raise_exception=True)
def pagamento_correcao_lista(request):
    """Pagamentos registrados num período (pela data do registro, não do atendimento — padrão:
    últimos 7 dias), pra conferir e corrigir um lançamento errado — ver pagamento_corrigir.
    Buscando pelo nome do paciente, procura em qualquer data (pedido da clínica, 23/10/2026)."""
    hoje = timezone.localdate()
    termo = (request.GET.get("q") or "").strip()
    de = parse_date(request.GET.get("de") or "") or hoje - timedelta(days=6)
    ate = parse_date(request.GET.get("ate") or "") or hoje
    if ate < de:
        de, ate = ate, de
    pagamentos_qs = PagamentoParticular.objects.all()
    if not termo:
        pagamentos_qs = pagamentos_qs.filter(criado_em__date__range=(de, ate))
    ids = pagamentos_qs.values("agendamento_id")
    agendamentos = (
        Agendamento.objects.filter(pk__in=ids)
        .select_related("paciente", "profissional", "convenio", "autorizado_por")
        .prefetch_related("pagamentos")
        .order_by("-autorizado_em", "-pk")
    )
    for palavra in termo.split():
        agendamentos = agendamentos.filter(paciente__nome_completo__icontains=palavra)
    # 50 por página (pedido da clínica, 03/11/2026) — antes eram os 300 mais recentes numa página só.
    pagina = paginar(request, agendamentos)
    linhas = []
    for agendamento in pagina.object_list:
        pagamentos = sorted(agendamento.pagamentos.all(), key=lambda p: p.pk)
        linhas.append({
            "agendamento": agendamento,
            "pagamentos": pagamentos,
            "total": sum((p.valor for p in pagamentos), Decimal("0.00")),
            "registrado_em": max(p.criado_em for p in pagamentos),
            "bloqueio": _bloqueio_correcao_pagamento(pagamentos, hoje),
        })
    return render(
        request,
        "financeiro/pagamento_correcao_lista.html",
        {
            "linhas": linhas, "pagina": pagina, "termo": termo, "de": de, "ate": ate,
            "hoje": hoje, "ontem": hoje - timedelta(days=1), "inicio_mes": hoje.replace(day=1),
            "sete_dias": hoje - timedelta(days=6),
        },
    )


@permission_required("accounts.fila_autorizar_financeiro", raise_exception=True)
def pagamento_corrigir(request, agendamento_pk):
    """Corrige o pagamento de um atendimento registrado errado hoje ou ontem: forma, divisão
    entre 2 formas, valor recebido do cartão, parcelas e o TOTAL (ver CorrigirPagamentoForm).
    Mudando o total, o valor cobrado dos procedimentos (AgendamentoItem.valor_cobrado, que o
    fechamento soma por procedimento) é reajustado na mesma proporção, pra os relatórios
    continuarem batendo; não vale com venda de pacote no pagamento nem sem procedimento cobrável.
    Repasse fixo (profissional que cobra valor fixo) mantém o total congelado e é repartido de
    novo entre as linhas, proporcional ao valor. Registra o antes/depois (e o motivo) no log."""
    agendamento = get_object_or_404(Agendamento.objects.select_related("paciente", "profissional", "convenio"), pk=agendamento_pk)
    destino = reverse("financeiro:pagamento_correcao_lista")
    hoje = timezone.localdate()
    pagamentos = list(agendamento.pagamentos.order_by("pk"))
    bloqueio = _bloqueio_correcao_pagamento(pagamentos, hoje)
    # Gravado em dobro por envio duplo (clínica, 26/10/2026): a tela trabalha só com a 1ª cópia
    # (total = o que foi cobrado, sem pedir motivo) e salvar apaga as outras.
    copias = []
    blocos = None if bloqueio else PagamentoParticular.copias_em_dobro(agendamento, pagamentos)
    if blocos:
        pagamentos = blocos[0]
        copias = [p for bloco in blocos[1:] for p in bloco]
    total = sum((p.valor for p in pagamentos), Decimal("0.00"))
    itens = list(agendamento.itens.select_related("procedimento").order_by("pk"))
    itens_cobraveis = [item for item in itens if not item.eh_retorno and not item.pacote_vendido_id]
    pacotes_vendidos = list(agendamento.pacotes_vendidos.order_by("pk"))
    permite_mudar_total = bool(itens_cobraveis) and not pacotes_vendidos
    fixos = [p.valor_repasse_fixo for p in pagamentos]
    repasse_fixo_total = sum(fixos, Decimal("0.00")) if fixos and all(f is not None for f in fixos) else None

    inicial = {}
    for n, pagamento in enumerate(pagamentos[:2], start=1):
        inicial.update({
            f"forma_{n}": pagamento.forma_pagamento,
            f"valor_{n}": pagamento.valor,
            f"valor_recebido_{n}": pagamento.valor_recebido,
            f"parcelas_{n}": pagamento.parcelas,
        })

    if request.method == "POST" and not bloqueio:
        form = CorrigirPagamentoForm(request.POST, total=total, permite_mudar_total=permite_mudar_total)
        if form.is_valid() and repasse_fixo_total is not None and form.novo_total < repasse_fixo_total:
            form.add_error(
                None,
                f"O novo total (R$ {formatar_moeda(form.novo_total)}) fica menor que o repasse fixo do profissional "
                f"(R$ {formatar_moeda(repasse_fixo_total)}) — confira o valor.",
            )
        if form.is_valid():
            antes = _descrever_pagamentos(
                [
                    {"forma_pagamento": p.forma_pagamento, "valor": p.valor, "valor_recebido": p.valor_recebido,
                     "parcelas": p.parcelas}
                    for p in pagamentos
                ]
            )
            novas = form.linhas
            novo_total = form.novo_total
            # Repasse fixo: mesmo total, repartido pelo valor de cada linha nova (resto na última).
            if repasse_fixo_total is not None:
                acumulado = Decimal("0.00")
                for i, linha in enumerate(novas):
                    if i == len(novas) - 1:
                        linha["valor_repasse_fixo"] = repasse_fixo_total - acumulado
                    else:
                        parte = (repasse_fixo_total * linha["valor"] / novo_total).quantize(Decimal("0.01"))
                        linha["valor_repasse_fixo"] = parte
                        acumulado += parte
            with transaction.atomic():
                for i, linha in enumerate(novas):
                    if i < len(pagamentos):
                        pagamento = pagamentos[i]
                        for campo, valor in linha.items():
                            setattr(pagamento, campo, valor)
                        pagamento.save()
                    else:
                        PagamentoParticular.objects.create(agendamento=agendamento, **linha)
                for pagamento in pagamentos[len(novas):] + copias:
                    pagamento.delete()
                if novo_total != total:
                    # Valor cobrado dos procedimentos acompanha o novo total, proporcional ao que
                    # cada um tinha (sem base, tudo no 1º); resto do arredondamento no último.
                    diferenca = novo_total - total
                    bases = [item.valor_cobrado or Decimal("0.00") for item in itens_cobraveis]
                    soma_bases = sum(bases, Decimal("0.00"))
                    acumulado = Decimal("0.00")
                    for i, (item, base) in enumerate(zip(itens_cobraveis, bases)):
                        if i == len(itens_cobraveis) - 1:
                            parte = diferenca - acumulado
                        elif soma_bases:
                            parte = (diferenca * base / soma_bases).quantize(Decimal("0.01"))
                        else:
                            parte = diferenca if i == 0 else Decimal("0.00")
                        acumulado += parte
                        item.valor_cobrado = max(base + parte, Decimal("0.00"))
                    AgendamentoItem.objects.bulk_update(itens_cobraveis, ["valor_cobrado"])
            depois = _descrever_pagamentos(novas)
            if copias:
                antes += f" (gravado em dobro: {len(copias)} linha(s) repetida(s) apagada(s))"
            if novo_total != total:
                depois += f" — total R$ {formatar_moeda(total)} → R$ {formatar_moeda(novo_total)} (motivo: {form.cleaned_data['motivo'].strip()})"
            LogAuditoria.registrar(
                request,
                request.user,
                f"Corrigiu o pagamento de '{agendamento.paciente.nome_completo}' "
                f"({timezone.localtime(agendamento.data_hora_inicio):%d/%m/%Y}): antes {antes}; depois {depois}",
            )
            messages.success(request, f"Pagamento de '{agendamento.paciente.nome_completo}' corrigido: {depois}.")
            return redirect(destino)
    else:
        form = CorrigirPagamentoForm(initial=inicial, total=total, permite_mudar_total=permite_mudar_total)

    return render(
        request,
        "financeiro/pagamento_corrigir.html",
        {"agendamento": agendamento, "pagamentos": pagamentos + copias, "copias": copias, "total": total, "form": form, "bloqueio": bloqueio,
         "permite_mudar_total": permite_mudar_total, "voltar_url": destino, "itens": itens,
         "pacotes_vendidos": pacotes_vendidos},
    )


@permission_required("accounts.fila_autorizar_financeiro", raise_exception=True)
def fila_pagamento_lista(request):
    """
    Agendamentos em 'Pré-autorizado' — a recepção encaminhou pro financeiro
    e está esperando o pagamento ser registrado (ver agenda.views.
    agendamento_mudar_status/agendamento_confirmar_pagamento). Sem recorte de
    data de propósito: é uma fila de trabalho do dia a dia, não um histórico —
    tende a ficar vazia rápido conforme o financeiro vai processando.
    """
    agendamentos = (
        Agendamento.objects.filter(status=Agendamento.Status.PRE_AUTORIZADO)
        .select_related("paciente", "profissional", "convenio")
        .prefetch_related("itens__procedimento")
        .order_by("data_hora_inicio")
    )
    return render(request, "financeiro/fila_pagamento_lista.html", {"pagina": paginar(request, agendamentos)})


@permission_required("accounts.financeiro_ver_contas_receber", raise_exception=True)
def contas_receber_lista(request):
    """
    Atendimentos por convênio já Realizados, agrupados por convênio — sem recorte
    semanal de propósito: repasse ao profissional é semanal por natureza, mas contas
    a receber de convênio envelhece pelo prazo de pagamento do próprio convênio
    (cadastros.models.Convenio.prazo_pagamento_dias), que passa de mês facilmente —
    uma janela de 1 semana esconderia a maioria dos pendentes.
    """
    ver_recebidos = request.GET.get("ver") == "recebidos"
    convenio_id = request.GET.get("convenio") or ""

    base_qs = (
        Agendamento.objects.filter(
            AgendamentoItem.filtro_nao_retorno("itens__"),
            Agendamento.filtro_convenio_a_faturar(),
            status__in=Agendamento.STATUS_PRODUCAO,
            # Retorno grátis (ver AgendamentoItem.eh_retorno) nunca tem nada a receber do
            # convênio (valor sempre R$ 0,00, forçado em agendamento_mudar_status) — um
            # agendamento cujos itens são TODOS retorno é excluído aqui, porque esta tela é
            # só sobre dinheiro pendente/a lançar, e não haveria ação nenhuma cabível (nem
            # lançar valor, nem conciliar). Um agendamento MISTO (ex.: consulta-retorno +
            # exame pago) continua aparecendo, porque o item pago ainda tem algo a receber
            # — daí o filtro ser "existe pelo menos 1 item não-retorno", não "não é retorno"
            # (filtro_nao_retorno, acima — inclui o retorno marcado à mão).
        )
        .select_related("paciente", "profissional", "convenio")
        .prefetch_related("itens__procedimento")
        .distinct()
        .order_by("convenio__nome", "data_hora_inicio")
    )
    if convenio_id:
        base_qs = base_qs.filter(convenio_id=convenio_id)

    linhas = base_qs.filter(convenio_recebido=ver_recebidos)
    # Totais de cada convênio somam TUDO (não só a página) — o "já recebidos" cresce pra sempre.
    totais = {
        linha["convenio_id"]: linha["total"] or Decimal("0.00")
        for linha in Agendamento.objects.filter(pk__in=linhas.values("pk"))
        .values("convenio_id")
        .annotate(total=Sum("valor_convenio"))
    }
    # 50 por página (pedido da clínica, 03/11/2026), na mesma ordem (convênio, data) — um
    # convênio pode continuar na página seguinte.
    pagina = paginar(request, linhas)

    grupos = {}
    for agendamento in pagina.object_list:
        grupo = grupos.setdefault(
            agendamento.convenio_id,
            {"convenio": agendamento.convenio, "itens": [], "total": totais.get(agendamento.convenio_id, Decimal("0.00"))},
        )
        # Trava contra baixa duplicada pelo caminho antigo (ver claude/faturamento-lote-convenio.md):
        # enquanto este atendimento está dentro de um LoteFaturamento ainda sem retorno lançado para
        # ele, "Marcar como recebido" fica indisponível aqui (ver template) — evita dar baixa manual
        # em Contas a receber enquanto o mesmo atendimento espera o retorno de um lote. Só relevante
        # na aba "A receber" (ver_recebidos=False); histórico de já recebidos não precisa da checagem.
        agendamento.trava_lote = None if ver_recebidos else LoteFaturamento.travando(agendamento)
        grupo["itens"].append(agendamento)

    grupos_ordenados = sorted(grupos.values(), key=lambda g: g["convenio"].nome)
    total_geral = sum(totais.values(), Decimal("0.00"))

    return render(
        request,
        "financeiro/contas_receber_lista.html",
        {
            "grupos": grupos_ordenados,
            "pagina": pagina,
            "total_geral": total_geral,
            "ver_recebidos": ver_recebidos,
            "convenios": Convenio.objects.filter(ativo=True).order_by("nome"),
            "convenio_selecionado": convenio_id,
        },
    )


@permission_required("accounts.financeiro_lancar", raise_exception=True)
def contas_receber_lancar_valor(request, pk):
    """
    Preenche/corrige manualmente o valor a receber de cada item NÃO-retorno deste
    agendamento — usada quando Convenio.valor_para não encontrou um preço
    configurado (convênio sem tabela de preço cadastrada, ou que calcula por
    desconto sobre a tabela particular). Um agendamento pode ter mais de um item
    (ver agenda.models.AgendamentoItem) — monta um LancarValorConvenioForm por
    item não-retorno (retorno é sempre R$ 0,00, travado, nunca precisa de
    lançamento manual), e ao salvar recalcula Agendamento.valor_convenio como a
    soma de todos os itens.
    """
    agendamento = get_object_or_404(Agendamento, pk=pk, forma_atendimento=Agendamento.FormaAtendimento.CONVENIO)
    itens_cobraveis = list(agendamento.itens.select_related("procedimento").filter(AgendamentoItem.filtro_nao_retorno()))
    if not itens_cobraveis:
        messages.error(
            request,
            "Este atendimento é um retorno (detectado automaticamente) — não é possível "
            "lançar valor, ele nunca gera cobrança ao convênio.",
        )
        return redirect("financeiro:contas_receber_lista")

    if request.method == "POST":
        forms_itens = [
            LancarValorConvenioForm(request.POST, instance=item, prefix=f"item{item.pk}")
            for item in itens_cobraveis
        ]
        if all(form.is_valid() for form in forms_itens):
            for form in forms_itens:
                form.save()
            agendamento.valor_convenio = sum(
                (item.valor_convenio or Decimal("0.00") for item in agendamento.itens.all()), Decimal("0.00")
            )
            agendamento.save(update_fields=["valor_convenio"])
            messages.success(request, f"Valor lançado para '{agendamento.paciente.nome_completo}'.")
            return redirect("financeiro:contas_receber_lista")
    else:
        forms_itens = [LancarValorConvenioForm(instance=item, prefix=f"item{item.pk}") for item in itens_cobraveis]

    return render(
        request,
        "financeiro/lancar_valor.html",
        {"forms_itens": forms_itens, "agendamento": agendamento, "voltar_url": reverse("financeiro:contas_receber_lista")},
    )


@permission_required("accounts.financeiro_conciliar", raise_exception=True)
def contas_receber_marcar_recebido(request, pk):
    """'Dar baixa' — marca convenio_recebido=True e registra a data/hora. Só aceita
    POST (ação que muda estado); GET direto só redireciona de volta."""
    agendamento = get_object_or_404(Agendamento, pk=pk)
    destino = request.META.get("HTTP_REFERER") or reverse("financeiro:contas_receber_lista")
    if request.method == "POST":
        # Mesma trava de contas_receber_lista, reforçada aqui no servidor (o botão já some na
        # tela, mas isso sozinho não impede um POST direto) — ver LoteFaturamento.travando.
        trava = LoteFaturamento.travando(agendamento)
        if trava:
            messages.error(
                request,
                f"Não é possível marcar como recebido — este atendimento está no lote "
                f"{trava.identificacao} ({trava.convenio.nome}, enviado em {trava.data_envio:%d/%m/%Y}), "
                "aguardando retorno do convênio.",
            )
            return redirect(destino)
        agendamento.convenio_recebido = True
        agendamento.convenio_recebido_em = timezone.now()
        agendamento.save(update_fields=["convenio_recebido", "convenio_recebido_em"])
        messages.success(
            request,
            f"Pagamento do convênio de '{agendamento.paciente.nome_completo}' "
            f"({agendamento.convenio.nome}) marcado como recebido.",
        )
    return redirect(destino)


@permission_required("accounts.financeiro_estornar", raise_exception=True)
def contas_receber_estornar(request, pk):
    """Desfaz uma baixa dada por engano — devolve o atendimento pra aba 'A receber'."""
    agendamento = get_object_or_404(Agendamento, pk=pk)
    destino = request.META.get("HTTP_REFERER") or reverse("financeiro:contas_receber_lista")
    if request.method == "POST":
        agendamento.convenio_recebido = False
        agendamento.convenio_recebido_em = None
        agendamento.save(update_fields=["convenio_recebido", "convenio_recebido_em"])
        LogAuditoria.registrar(
            request,
            request.user,
            f"Estornou baixa de convênio de '{agendamento.paciente.nome_completo}' ({agendamento.convenio.nome})",
        )
        messages.success(
            request,
            f"Baixa do convênio de '{agendamento.paciente.nome_completo}' estornada — voltou para 'A receber'.",
        )
    return redirect(destino)


@permission_required("accounts.financeiro_estornar_pagamento", raise_exception=True)
def pagamento_estornar(request, pk):
    """
    Estorna uma linha de PagamentoParticular — devolve ao paciente o valor pago
    (ex.: imprevisto depois do pagamento já registrado). Diferente de
    contas_receber_estornar (que só desfaz uma baixa de convênio marcada por
    engano, sem motivo), aqui é dinheiro de volta pro paciente, então exige
    motivo e mostra tela de confirmação antes de executar.

    As guardas (já estornado / cortesia sem cobrança) ficam ANTES do
    `if request.method == "POST"`, mesmo padrão de
    agenda.views.agendamento_confirmar_pagamento — diferente de
    agendamento_cancelar (reexecutar lá é inofensivo), reexecutar aqui
    sobrescreveria estornado_em/estornado_por do estorno original.
    """
    pagamento = get_object_or_404(PagamentoParticular, pk=pk)
    # Vem do campo oculto "voltar_url" (ver template) quando é um POST desta própria tela —
    # não dá pra confiar no Referer aí, porque o form posta pra si mesma: depois de estornar
    # com sucesso, o redirect abaixo cairia de novo nesta view com Referer ainda apontando
    # pra ela mesma, e o guard de "já estornado" logo abaixo criaria um loop de redirect.
    destino = request.POST.get("voltar_url") or request.META.get("HTTP_REFERER") or reverse("agenda:agenda_dia")

    if pagamento.estornado:
        messages.error(request, "Este pagamento já foi estornado.")
        return redirect(destino)
    if pagamento.forma_pagamento == Agendamento.FormaPagamento.CORTESIA:
        messages.error(request, "Cortesia não gerou cobrança — não há o que estornar.")
        return redirect(destino)

    if request.method == "POST":
        form = EstornoPagamentoForm(request.POST, instance=pagamento)
        if form.is_valid():
            # repasse_valor_pago > 0 cobre também o repasse pago só PARCIALMENTE (ver
            # financeiro.models.RepassePagamento) — o profissional já levou uma parte.
            repasse_ja_estava_pago = pagamento.repasse_pago or pagamento.repasse_valor_pago > 0
            pagamento = form.save(commit=False)
            pagamento.estornado = True
            pagamento.estornado_em = timezone.now()
            pagamento.estornado_por = request.user
            pagamento.repasse_pago = False
            pagamento.repasse_pago_em = None
            pagamento.repasse_valor_pago = Decimal("0.00")
            pagamento.save()
            LogAuditoria.registrar(
                request,
                request.user,
                f"Estornou pagamento de '{pagamento.agendamento.paciente.nome_completo}' "
                f"(R$ {formatar_moeda(pagamento.valor)}, {pagamento.get_forma_pagamento_display()}) — motivo: {pagamento.motivo_estorno}",
            )
            messages.success(
                request,
                f"Pagamento de '{pagamento.agendamento.paciente.nome_completo}' "
                f"(R$ {formatar_moeda(pagamento.valor)}, {pagamento.get_forma_pagamento_display()}) estornado.",
            )
            if repasse_ja_estava_pago:
                messages.warning(
                    request,
                    "O repasse deste pagamento já estava pago (total ou parcialmente) e foi desfeito no sistema — "
                    "se o valor já foi entregue em mãos ao profissional, acerte a diferença com ele diretamente.",
                )
            _desativar_pacotes_estornados(request, pagamento.agendamento)
            return redirect(destino)
    else:
        form = EstornoPagamentoForm(instance=pagamento)

    return render(
        request,
        "financeiro/pagamento_estornar.html",
        {"form": form, "pagamento": pagamento, "voltar_url": destino},
    )


def _desativar_pacotes_estornados(request, agendamento):
    """Pacote vendido neste atendimento cujo pagamento foi todo estornado deixa de valer (ver
    agenda.models.PacoteVendido.ativos): os atendimentos FUTUROS que iam usar o saldo voltam a
    ser cobrados normalmente. O que já foi atendido pelo pacote fica como está (histórico)."""
    for venda in agendamento.pacotes_vendidos.all():
        if venda.cadastro_manual:
            # Pacote do sistema antigo cobrado aqui: continua valendo e volta a ficar "a pagar".
            if venda.pago_em and not venda.pagamento_valido:
                venda.agendamento_venda = None
                venda.pago_em = None
                venda.save(update_fields=["agendamento_venda", "pago_em"])
                messages.warning(
                    request,
                    f"O pacote do sistema antigo '{venda.nome}' cobrado neste atendimento voltou a ficar a pagar.",
                )
            continue
        if venda.esta_ativo:
            continue
        futuros = venda.itens_agendamento.exclude(
            agendamento__status__in=list(Agendamento.STATUS_PRODUCAO)
        )
        quantidade = futuros.update(pacote_vendido=None)
        messages.warning(
            request,
            f"O pacote '{venda.nome}' vendido neste atendimento ficou sem pagamento e deixou de valer"
            + (f" — {quantidade} item(ns) de atendimentos futuros voltam a ser cobrados normalmente." if quantidade else "."),
        )


# =====================================================================
# Fechamento diário / mensal + Relatório Detalhado
# =====================================================================

def _mes_calendario(ano, mes):
    """(primeiro dia, último dia) do mês — mês calendário inteiro, conforme decisão do cliente."""
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    return date(ano, mes, 1), date(ano, mes, ultimo_dia)


def _profissional_do_usuario(request):
    """Mesmo idioma já usado em prontuario.views._profissional_do_usuario — duplicado aqui por
    app, mesmo padrão já aceito no projeto (_semana_de também é duplicada entre agenda/financeiro)."""
    if request.user.profissional_id:
        return Profissional.objects.filter(pk=request.user.profissional_id).first()
    return None


def _producao_por_profissional(data_inicio, data_fim, profissional=None):
    """
    PagamentoParticular de atendimentos em produção (particular ou convênio "funciona como
    particular" — ver Agendamento.filtro_pago_como_particular) com
    agendamento__data_hora_inicio__date entre data_inicio e data_fim (ambos inclusive).
    O convênio marcado "funciona como particular" pago na clínica (decisão da clínica,
    23/10/2026) vai pro repasse semanal e pro caixa, e sai do faturamento ao plano (ver
    Agendamento.filtro_convenio_a_faturar); os demais convênios pagos na hora ficam de fora. Mesmo
    critério de agenda.views.relatorio_repasses_semanal. Cortesia entra na contagem de atendimentos (contribui 0 ao bruto,
    mas ainda é um atendimento do profissional).

    Agrupa por profissional: bruto (soma de TODAS as linhas do período, incluindo as
    que foram estornadas depois — de propósito: assim, se o fechamento for reimpresso
    depois de um estorno tardio, continua mostrando o valor original + o que foi
    devolvido, em vez de só encolher silenciosamente), estornado (soma só das
    estornadas), líquido = bruto - estornado, percentual aplicado (0 quando
    profissional.eh_proprietario, senão profissional.percentual_desconto), retido =
    líquido * percentual / 100, repasse = líquido - retido. Exceção: linhas com
    PagamentoParticular.valor_repasse_fixo (profissional que cobra valor fixo no particular)
    ficam fora da base do percentual — o retido delas é valor − repasse fixo.

    Os mesmos campos também saem duplicados com sufixo "_dinheiro" e "_outras" (dinheiro x
    cartão/Pix). retido_outras
    sai por subtração (retido - retido_dinheiro) em vez de calculado direto, pra garantir
    que as duas partes somem exatamente o "retido" total já exibido, sem deriva de
    arredondamento entre os dois quantize() separados. Esta dupla "_dinheiro"/"_outras" é
    SEMPRE literal (forma_pagamento == DINHEIRO) — é o que alimenta
    financeiro.views.painel_financeiro's caixa_dinheiro_semana (posição de dinheiro físico
    da clínica), que não pode ser afetada por acordo de repasse com o profissional.

    Só os campos "repasse_ja_pago"/"repasse_pendente" é que devem ser usados para MOSTRAR ao
    usuário o que já foi repassado x o que ainda falta repassar (meu_fechamento.html,
    _fechamento_secoes.html). repasse_pendente é a soma de PagamentoParticular.saldo_repasse
    das linhas ainda em aberto (dinheiro também, desde 26/10/2026) — já considera pagamento
    PARCIAL (ver financeiro.models.RepassePagamento), mesma conta de
    agenda.views.relatorio_repasses_semanal. repasse_ja_pago sai por subtração (repasse −
    pendente).

    repasse_dinheiro_entregue: parte do profissional nas linhas em dinheiro da regra antiga
    (registrado antes de 26/10/2026, entregue na hora sem registro de saída — ver
    PagamentoParticular.dinheiro_entregue_na_hora). É o que sai do caixa físico sem
    MovimentacaoCaixa; usado por _dinheiro_no_caixa.

    bruto_recebido/estornado_recebido/liquido_recebido são um terceiro trio, adicional aos
    de cima, calculado com PagamentoParticular.valor_recebido_efetivo (valor real recebido,
    já descontada a taxa da maquininha, quando informado) em vez de 'valor' — usado SÓ pela
    visão do proprietário (_visao_proprietario), nunca pelo repasse do profissional.
    retido/repasse (e todas as variantes _dinheiro/_outras/_ja_pago/_pendente) continuam 100%
    em cima de 'valor' de propósito: a taxa da maquininha é um custo que a clínica absorve
    sozinha, nunca reduz o que o profissional recebe.

    Este terceiro trio também sai duplicado com sufixo "_dinheiro"/"_outras" (mesma regra
    literal forma_pagamento==DINHEIRO de cima) — usado por _visao_proprietario pra separar,
    na posição do proprietário, o que ficou em dinheiro físico do que caiu em conta
    (cartão/Pix), decisão do cliente (2026-07-29). liquido_recebido_dinheiro +
    liquido_recebido_outras soma exatamente liquido_recebido (as duas partes são tracked
    direto, igual bruto_dinheiro/bruto_outras — sem quantize() envolvido aqui, então não há
    risco de deriva de arredondamento, diferente de retido_outras).

    profissional=<Profissional> filtra a query no banco (usado por 'meu fechamento'
    quando o usuário logado não é o proprietário); None traz todos (fechamentos gerais,
    e 'meu fechamento' do proprietário, que precisa somar a retenção de todos os outros).

    Retorna (linhas, totais): linhas é uma lista de dicts ordenada por nome do
    profissional; totais é um dict com os mesmos campos somados (sem "percentual").
    """
    qs = (
        PagamentoParticular.objects.filter(
            # Particular + convênio "funciona como particular" (23/10/2026).
            Agendamento.filtro_pago_como_particular("agendamento__"),
            agendamento__status__in=Agendamento.STATUS_PRODUCAO,
            agendamento__data_hora_inicio__date__range=(data_inicio, data_fim),
        )
        .select_related("agendamento__profissional")
    )
    if profissional is not None:
        qs = qs.filter(agendamento__profissional=profissional)

    grupos = {}
    for pagamento in qs:
        prof = pagamento.agendamento.profissional
        grupo = grupos.setdefault(
            prof.pk,
            {
                "profissional": prof,
                "agendamento_ids": set(),
                "bruto": Decimal("0.00"),
                "estornado": Decimal("0.00"),
                "bruto_recebido": Decimal("0.00"),
                "estornado_recebido": Decimal("0.00"),
                "bruto_recebido_dinheiro": Decimal("0.00"),
                "estornado_recebido_dinheiro": Decimal("0.00"),
                "bruto_recebido_outras": Decimal("0.00"),
                "estornado_recebido_outras": Decimal("0.00"),
                "bruto_dinheiro": Decimal("0.00"),
                "estornado_dinheiro": Decimal("0.00"),
                "bruto_outras": Decimal("0.00"),
                "estornado_outras": Decimal("0.00"),
                "repasse_pendente": Decimal("0.00"),
                "repasse_dinheiro_entregue": Decimal("0.00"),
                "liquido_fixo": Decimal("0.00"),
                "repasse_fixo": Decimal("0.00"),
                "liquido_fixo_dinheiro": Decimal("0.00"),
                "repasse_fixo_dinheiro": Decimal("0.00"),
                "tem_valor_fixo": False,
            },
        )
        eh_dinheiro = pagamento.forma_pagamento == Agendamento.FormaPagamento.DINHEIRO
        # Linha com repasse fixo congelado (profissional que cobra valor fixo no particular —
        # ver PagamentoParticular.valor_repasse_fixo) fica fora da base do percentual: o repasse
        # dela é o valor fixo, e a retenção da clínica é o que sobra (valor − repasse fixo).
        if pagamento.valor_repasse_fixo is not None and not prof.eh_proprietario:
            grupo["tem_valor_fixo"] = True
            if not pagamento.estornado:
                grupo["liquido_fixo"] += pagamento.valor
                grupo["repasse_fixo"] += pagamento.valor_repasse_fixo
                if eh_dinheiro:
                    grupo["liquido_fixo_dinheiro"] += pagamento.valor
                    grupo["repasse_fixo_dinheiro"] += pagamento.valor_repasse_fixo
        # Proprietário não recebe repasse — nada dele fica "pendente".
        if (
            not pagamento.estornado
            and not prof.eh_proprietario
            and pagamento.forma_pagamento != Agendamento.FormaPagamento.CORTESIA
        ):
            grupo["repasse_pendente"] += pagamento.saldo_repasse
        if not pagamento.estornado and not prof.eh_proprietario and pagamento.dinheiro_entregue_na_hora:
            grupo["repasse_dinheiro_entregue"] += pagamento.valor_repasse
        grupo["agendamento_ids"].add(pagamento.agendamento_id)
        grupo["bruto"] += pagamento.valor
        grupo["bruto_recebido"] += pagamento.valor_recebido_efetivo
        grupo["bruto_recebido_dinheiro" if eh_dinheiro else "bruto_recebido_outras"] += pagamento.valor_recebido_efetivo
        grupo["bruto_dinheiro" if eh_dinheiro else "bruto_outras"] += pagamento.valor
        if pagamento.estornado:
            grupo["estornado"] += pagamento.valor
            grupo["estornado_recebido"] += pagamento.valor_recebido_efetivo
            grupo["estornado_recebido_dinheiro" if eh_dinheiro else "estornado_recebido_outras"] += (
                pagamento.valor_recebido_efetivo
            )
            grupo["estornado_dinheiro" if eh_dinheiro else "estornado_outras"] += pagamento.valor

    linhas = []
    totais = {
        "atendimentos": 0,
        "bruto": Decimal("0.00"),
        "estornado": Decimal("0.00"),
        "bruto_recebido": Decimal("0.00"),
        "estornado_recebido": Decimal("0.00"),
        "liquido_recebido": Decimal("0.00"),
        "bruto_recebido_dinheiro": Decimal("0.00"),
        "estornado_recebido_dinheiro": Decimal("0.00"),
        "liquido_recebido_dinheiro": Decimal("0.00"),
        "bruto_recebido_outras": Decimal("0.00"),
        "estornado_recebido_outras": Decimal("0.00"),
        "liquido_recebido_outras": Decimal("0.00"),
        "retido": Decimal("0.00"),
        "repasse": Decimal("0.00"),
        "bruto_dinheiro": Decimal("0.00"),
        "estornado_dinheiro": Decimal("0.00"),
        "retido_dinheiro": Decimal("0.00"),
        "repasse_dinheiro": Decimal("0.00"),
        "bruto_outras": Decimal("0.00"),
        "estornado_outras": Decimal("0.00"),
        "retido_outras": Decimal("0.00"),
        "repasse_outras": Decimal("0.00"),
        "repasse_ja_pago": Decimal("0.00"),
        "repasse_pendente": Decimal("0.00"),
        "repasse_dinheiro_entregue": Decimal("0.00"),
    }
    for grupo in sorted(grupos.values(), key=lambda g: g["profissional"].nome):
        liquido = grupo["bruto"] - grupo["estornado"]
        liquido_recebido = grupo["bruto_recebido"] - grupo["estornado_recebido"]
        liquido_recebido_dinheiro = grupo["bruto_recebido_dinheiro"] - grupo["estornado_recebido_dinheiro"]
        liquido_recebido_outras = grupo["bruto_recebido_outras"] - grupo["estornado_recebido_outras"]
        liquido_dinheiro = grupo["bruto_dinheiro"] - grupo["estornado_dinheiro"]
        liquido_outras = grupo["bruto_outras"] - grupo["estornado_outras"]
        percentual = Decimal("0") if grupo["profissional"].eh_proprietario else grupo["profissional"].percentual_desconto
        liquido_percentual = liquido - grupo["liquido_fixo"]
        liquido_percentual_dinheiro = liquido_dinheiro - grupo["liquido_fixo_dinheiro"]
        retido = (liquido_percentual * percentual / Decimal("100")).quantize(Decimal("0.01")) + (
            grupo["liquido_fixo"] - grupo["repasse_fixo"]
        )
        retido_dinheiro = (liquido_percentual_dinheiro * percentual / Decimal("100")).quantize(Decimal("0.01")) + (
            grupo["liquido_fixo_dinheiro"] - grupo["repasse_fixo_dinheiro"]
        )
        retido_outras = retido - retido_dinheiro
        repasse = liquido - retido
        repasse_dinheiro = liquido_dinheiro - retido_dinheiro
        repasse_outras = liquido_outras - retido_outras
        repasse_pendente = grupo["repasse_pendente"]
        # Proprietário não tem repasse a pagar/receber: fica fora do "já pago" também.
        repasse_ja_pago = Decimal("0.00") if grupo["profissional"].eh_proprietario else repasse - repasse_pendente
        linhas.append({
            "profissional": grupo["profissional"],
            "atendimentos": len(grupo["agendamento_ids"]),
            "bruto": grupo["bruto"],
            "estornado": grupo["estornado"],
            "liquido": liquido,
            "bruto_recebido": grupo["bruto_recebido"],
            "estornado_recebido": grupo["estornado_recebido"],
            "liquido_recebido": liquido_recebido,
            "bruto_recebido_dinheiro": grupo["bruto_recebido_dinheiro"],
            "estornado_recebido_dinheiro": grupo["estornado_recebido_dinheiro"],
            "liquido_recebido_dinheiro": liquido_recebido_dinheiro,
            "bruto_recebido_outras": grupo["bruto_recebido_outras"],
            "estornado_recebido_outras": grupo["estornado_recebido_outras"],
            "liquido_recebido_outras": liquido_recebido_outras,
            "percentual": percentual,
            # Pra tela mostrar "valor fixo" no lugar do % (ver _fechamento_secoes.html).
            "tem_valor_fixo": grupo["tem_valor_fixo"],
            "so_valor_fixo": grupo["tem_valor_fixo"] and not liquido_percentual,
            "retido": retido,
            "repasse": repasse,
            "bruto_dinheiro": grupo["bruto_dinheiro"],
            "estornado_dinheiro": grupo["estornado_dinheiro"],
            "liquido_dinheiro": liquido_dinheiro,
            "retido_dinheiro": retido_dinheiro,
            "repasse_dinheiro": repasse_dinheiro,
            "bruto_outras": grupo["bruto_outras"],
            "estornado_outras": grupo["estornado_outras"],
            "liquido_outras": liquido_outras,
            "retido_outras": retido_outras,
            "repasse_outras": repasse_outras,
            "repasse_ja_pago": repasse_ja_pago,
            "repasse_pendente": repasse_pendente,
            "repasse_dinheiro_entregue": grupo["repasse_dinheiro_entregue"],
        })
        totais["atendimentos"] += len(grupo["agendamento_ids"])
        totais["bruto"] += grupo["bruto"]
        totais["estornado"] += grupo["estornado"]
        totais["bruto_recebido"] += grupo["bruto_recebido"]
        totais["estornado_recebido"] += grupo["estornado_recebido"]
        totais["bruto_recebido_dinheiro"] += grupo["bruto_recebido_dinheiro"]
        totais["estornado_recebido_dinheiro"] += grupo["estornado_recebido_dinheiro"]
        totais["bruto_recebido_outras"] += grupo["bruto_recebido_outras"]
        totais["estornado_recebido_outras"] += grupo["estornado_recebido_outras"]
        totais["retido"] += retido
        totais["repasse"] += repasse
        totais["bruto_dinheiro"] += grupo["bruto_dinheiro"]
        totais["estornado_dinheiro"] += grupo["estornado_dinheiro"]
        totais["retido_dinheiro"] += retido_dinheiro
        totais["repasse_dinheiro"] += repasse_dinheiro
        totais["bruto_outras"] += grupo["bruto_outras"]
        totais["estornado_outras"] += grupo["estornado_outras"]
        totais["retido_outras"] += retido_outras
        totais["repasse_outras"] += repasse_outras
        totais["repasse_ja_pago"] += repasse_ja_pago
        totais["repasse_pendente"] += repasse_pendente
        totais["repasse_dinheiro_entregue"] += grupo["repasse_dinheiro_entregue"]
    totais["liquido"] = totais["bruto"] - totais["estornado"]
    totais["liquido_recebido"] = totais["bruto_recebido"] - totais["estornado_recebido"]
    totais["liquido_recebido_dinheiro"] = totais["bruto_recebido_dinheiro"] - totais["estornado_recebido_dinheiro"]
    totais["liquido_recebido_outras"] = totais["bruto_recebido_outras"] - totais["estornado_recebido_outras"]
    totais["liquido_dinheiro"] = totais["bruto_dinheiro"] - totais["estornado_dinheiro"]
    totais["liquido_outras"] = totais["bruto_outras"] - totais["estornado_outras"]

    return linhas, totais


def _estornos_do_periodo(data_inicio, data_fim, profissional=None):
    """
    PagamentoParticular estornado cujo estornado_em caiu dentro de [data_inicio, data_fim]
    — filtra por QUANDO o estorno aconteceu, não pela data do atendimento original (pode
    ser diferente: um estorno de hoje pode ser de um atendimento de dias atrás). É essa a
    métrica de "quanto saiu do caixa nesta janela". Sem nome de paciente de propósito —
    ver Relatório Detalhado pra esse nível de detalhe. profissional=<Profissional> filtra
    só os estornos de atendimentos dele. Retorna (itens, total).
    """
    qs = PagamentoParticular.objects.filter(estornado=True, estornado_em__date__range=(data_inicio, data_fim))
    if profissional is not None:
        qs = qs.filter(agendamento__profissional=profissional)
    itens = list(qs.select_related("agendamento__profissional").order_by("agendamento__profissional__nome", "estornado_em"))
    total = sum((item.valor for item in itens), Decimal("0.00"))
    return itens, total


def _movimentacoes_avulsas_do_periodo(data_inicio, data_fim):
    """
    Entradas/saídas avulsas de caixa (ver financeiro.models.MovimentacaoCaixa) com 'data' dentro
    de [data_inicio, data_fim] — mesmo espírito de _estornos_do_periodo acima: o que importa é a
    data DA MOVIMENTAÇÃO, não a de criação do registro. Sem filtro de profissional (diferente das
    funções de produção acima) porque avulsa não é produção de ninguém — é só caixa da clínica.

    Usada em 3 lugares: painel_financeiro (card "Caixa da semana"), _visao_proprietario (linha
    "Entradas/Saídas avulsas" no resultado do dono) e a própria listagem (movimentacao_avulsa_lista)
    — um único ponto de verdade pro cálculo, pra nunca divergir entre as telas.

    Retorna (itens, total_entradas, total_saidas).
    """
    qs = MovimentacaoCaixa.objects.filter(data__range=(data_inicio, data_fim)).select_related("profissional", "criado_por")
    itens = list(qs.order_by("data", "criado_em"))
    total_entradas = sum((i.valor for i in itens if i.tipo == MovimentacaoCaixa.Tipo.ENTRADA), Decimal("0.00"))
    total_saidas = sum((i.valor for i in itens if i.tipo == MovimentacaoCaixa.Tipo.SAIDA), Decimal("0.00"))
    return itens, total_entradas, total_saidas


def _dinheiro_no_caixa(linhas_profissionais, data_inicio, data_fim, avulsas_entradas, avulsas_saidas):
    """Dinheiro físico no caixa do período (pedido da clínica, 26/10/2026 — "o certo era ter em
    caixa"): todo dinheiro recebido, de qualquer profissional, + entradas − TODAS as saídas
    avulsas (inclusive repasse pago em dinheiro do caixa, que gera saída sozinho).

    - A parte dos profissionais no dinheiro fica no caixa até o repasse ser registrado; só a regra
      antiga (registrado antes de 26/10/2026) desconta o que foi entregue na hora sem registro
      (repasse_dinheiro_entregue, ver _producao_por_profissional).
    - Convênio que NÃO "funciona como particular" mas foi pago na clínica em dinheiro também
      entra: não é produção particular de ninguém, mas o dinheiro está na gaveta.

    linhas_profissionais: as de _producao_por_profissional SEM filtro de profissional."""
    particular = sum(
        (l["liquido_recebido_dinheiro"] - l["repasse_dinheiro_entregue"] for l in linhas_profissionais),
        Decimal("0.00"),
    )
    convenio = (
        PagamentoParticular.objects.filter(
            forma_pagamento=Agendamento.FormaPagamento.DINHEIRO,
            estornado=False,
            agendamento__status__in=Agendamento.STATUS_PRODUCAO,
            agendamento__data_hora_inicio__date__range=(data_inicio, data_fim),
        )
        .exclude(Agendamento.filtro_pago_como_particular("agendamento__"))
        .aggregate(total=Sum("valor"))["total"]
        or Decimal("0.00")
    )
    return (particular + convenio + avulsas_entradas - avulsas_saidas).quantize(Decimal("0.01"))


def _convenios_por_procedimento(data_inicio, data_fim, profissional=None):
    """
    AgendamentoItem de atendimentos CONVENIO/REALIZADO no período, agrupado por convênio
    e, dentro de cada um, por procedimento — qtd soma 'quantidade' de cada item (uma
    unidade por vez, não conta linhas), nenhum valor em R$ (faturamento de convênio é
    conciliado à parte, módulo Contas a Receber). profissional=<Profissional> filtra só os
    atendimentos dele (usado no fechamento individual de um profissional — ele também
    precisa ver os convênios que atendeu, só não entram no cálculo de repasse).

    Retorno grátis (ver AgendamentoItem.eh_retorno) sai como um item "Retorno" separado do
    procedimento real (ex.: "Consulta"), em vez de somado junto — decisão do cliente
    (2026-07-21): só a consulta de fato é cobrada, então a contagem de "Consulta" não pode
    incluir silenciosamente os retornos gratuitos. O total do grupo (grupo["total"]) continua
    somando os dois — é "quantas unidades atendidas", não "quantas cobradas".

    Valor (pedido da clínica, 23/10/2026 — o fechamento passa a mostrar quanto foi produzido
    em convênio): soma AgendamentoItem.valor_convenio, o valor da Tabela de Preços congelado na
    autorização (já considera a 2ª guia, retorno = 0). Item sem preço cadastrado (None) não
    soma e é contado em "sem_valor", pra tela avisar em vez de mostrar um total por baixo.

    Retorna (grupos, total_geral); grupos é uma lista de dicts
    {"nome": str, "itens": [{"procedimento": str, "qtd": int, "valor": Decimal, "sem_valor": int}],
    "total": int, "valor": Decimal, "sem_valor": int}.
    """
    qs = AgendamentoItem.objects.filter(
        agendamento__forma_atendimento=Agendamento.FormaAtendimento.CONVENIO,
        agendamento__status__in=Agendamento.STATUS_PRODUCAO,
        agendamento__data_hora_inicio__date__range=(data_inicio, data_fim),
    ).select_related("agendamento__convenio", "procedimento")
    if profissional is not None:
        qs = qs.filter(agendamento__profissional=profissional)
    # Convênio "funciona como particular" pago na clínica (decisão da clínica, 23/10/2026): continua listado aqui, marcado,
    # mas o valor não soma no "a receber do plano" — o que o paciente pagou já está no
    # Particular (ver _particular_por_procedimento e Agendamento.filtro_convenio_a_faturar).
    pagos_na_clinica = set(
        PagamentoParticular.objects.filter(
            agendamento_id__in=qs.values("agendamento_id"), agendamento__convenio__funciona_como_particular=True
        ).values_list(
            "agendamento_id", flat=True
        )
    )

    grupos = {}
    for item in qs:
        ag = item.agendamento
        pago_na_clinica = ag.pk in pagos_na_clinica
        grupo = grupos.setdefault(
            ag.convenio_id,
            {
                "nome": ag.convenio.nome if ag.convenio else "Sem convênio definido",
                "itens": {},
                "total": 0,
                "valor": Decimal("0.00"),
                "sem_valor": 0,
            },
        )
        nome_item = "Retorno" if item.eh_retorno else item.procedimento.nome
        if pago_na_clinica and not item.eh_retorno:
            nome_item += " (pago na clínica)"
        bucket = grupo["itens"].setdefault(
            nome_item,
            {
                "procedimento": nome_item,
                "qtd": 0,
                "valor": Decimal("0.00"),
                "sem_valor": 0,
                "retorno": item.eh_retorno,
                "pago_na_clinica": pago_na_clinica,
            },
        )
        # quantidade_produzida: sessão de 1h por convênio só conta a 2ª guia depois de
        # autorizada (ver agenda.models.AgendamentoItem.controla_guias).
        bucket["qtd"] += item.quantidade_produzida
        grupo["total"] += item.quantidade_produzida
        if item.eh_retorno or pago_na_clinica:
            continue
        if item.valor_convenio is None:
            bucket["sem_valor"] += 1
            grupo["sem_valor"] += 1
        else:
            bucket["valor"] += item.valor_convenio
            grupo["valor"] += item.valor_convenio

    grupos_ordenados = [
        {
            "nome": grupo["nome"],
            "itens": sorted(grupo["itens"].values(), key=lambda i: -i["qtd"]),
            "total": grupo["total"],
            "valor": grupo["valor"].quantize(Decimal("0.01")),
            "sem_valor": grupo["sem_valor"],
        }
        for grupo in sorted(grupos.values(), key=lambda g: g["nome"])
    ]
    for grupo in grupos_ordenados:
        for bucket in grupo["itens"]:
            bucket["valor"] = bucket["valor"].quantize(Decimal("0.01"))
    total_geral = sum(g["total"] for g in grupos_ordenados)
    return grupos_ordenados, total_geral


def _retornos_por_especialidade(data_inicio, data_fim, profissional=None):
    """
    Quantidade de retornos grátis (AgendamentoItem.retorno_de preenchido) Realizados no
    período, agrupados pela especialidade do profissional QUE ATENDEU O RETORNO (pode
    ser diferente do profissional da consulta original — retorno vale pra qualquer
    profissional da mesma especialidade, ver AgendamentoItem.buscar_item_para_retorno).
    Só contagem, nunca valor em R$ (retorno por definição não gera receita) — mesmo
    espírito de _convenios_por_procedimento acima. profissional=<Profissional> filtra só
    os retornos atendidos por ele (usado em 'meu fechamento'); None traz todos. Retorna
    (linhas, total) — linhas é uma lista de dicts {"nome": str, "qtd": int}.
    """
    qs = AgendamentoItem.objects.filter(
        AgendamentoItem.filtro_retorno(),
        agendamento__status__in=Agendamento.STATUS_PRODUCAO,
        agendamento__data_hora_inicio__date__range=(data_inicio, data_fim),
    ).select_related("agendamento__profissional")
    if profissional is not None:
        qs = qs.filter(agendamento__profissional=profissional)

    contagem = {}
    for item in qs:
        nome = item.agendamento.especialidade_exibicao
        contagem[nome] = contagem.get(nome, 0) + item.quantidade
    linhas = sorted(({"nome": nome, "qtd": qtd} for nome, qtd in contagem.items()), key=lambda l: l["nome"])
    return linhas, sum(l["qtd"] for l in linhas)


def _itens_particular(data_inicio, data_fim, profissional=None):
    """AgendamentoItem que entram no Particular do período — base comum de
    _particular_por_procedimento e _particular_por_forma_pagamento."""
    qs = AgendamentoItem.objects.filter(
        # Particular + convênio "funciona como particular" pago na clínica — decisão da clínica, 23/10/2026.
        Q(agendamento__forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR)
        | Q(
            agendamento__convenio__funciona_como_particular=True,
            agendamento_id__in=PagamentoParticular.objects.values("agendamento_id"),
        ),
        agendamento__status__in=Agendamento.STATUS_PRODUCAO,
        agendamento__data_hora_inicio__date__range=(data_inicio, data_fim),
    ).exclude(
        # Item que já usa o pacote vendido no PRÓPRIO atendimento da venda conta dentro da
        # linha "Venda de pacote" (clínica, 27/10/2026: vender o pacote na sessão de hoje
        # contava 2 procedimentos — a venda e a sessão — quando foi um só).
        pacote_vendido__agendamento_venda=F("agendamento"),
    )
    if profissional is not None:
        qs = qs.filter(agendamento__profissional=profissional)
    return qs


def _vendas_pacote_particular(data_inicio, data_fim, profissional=None):
    """Vendas de pacote que entram no Particular do período (dia do atendimento da venda)."""
    vendas = PacoteVendido.pagos().filter(
        agendamento_venda__status__in=Agendamento.STATUS_PRODUCAO,
        agendamento_venda__data_hora_inicio__date__range=(data_inicio, data_fim),
    )
    if profissional is not None:
        vendas = vendas.filter(profissional=profissional)
    return vendas


def _particular_por_procedimento(data_inicio, data_fim, profissional=None):
    """
    AgendamentoItem de atendimentos PARTICULAR/REALIZADO do período, agrupado por
    procedimento — qtd soma 'quantidade' de cada item (não conta linhas) e valor soma
    AgendamentoItem.valor_cobrado, congelado no momento do pagamento (ver
    agenda.views.agendamento_confirmar_pagamento) — nunca recalculado pela tabela de
    preços atual, mesmo motivo de _producao_por_profissional (o efeito de mudanças de
    preço não deve fazer um relatório de período passado divergir). profissional=
    <Profissional> filtra só os atendimentos dele (usado no fechamento individual de
    um profissional).

    Retorno grátis (ver AgendamentoItem.eh_retorno) sai como um item "Retorno" separado do
    procedimento real, mesmo espírito de _convenios_por_procedimento acima — só a consulta
    de fato é cobrada, e o retorno sempre entra com valor R$ 0,00 (cortesia obrigatória),
    então não deve ficar escondido dentro da contagem de "Consulta".
    """
    qs = _itens_particular(data_inicio, data_fim, profissional).select_related(
        "procedimento", "retorno_de", "agendamento__convenio"
    )

    grupos = {}
    for item in qs:
        if item.eh_retorno:
            nome_item = "Retorno"
        elif item.pacote_vendido_id:
            # Uso do saldo de pacote (R$ 0,00 — o valor entrou na venda, linha própria abaixo).
            nome_item = f"{item.procedimento.nome} (pelo pacote)"
        elif item.agendamento.forma_atendimento == Agendamento.FormaAtendimento.CONVENIO:
            nome_item = f"{item.procedimento.nome} ({item.agendamento.convenio.nome if item.agendamento.convenio else 'convênio'}, pago na clínica)"
        else:
            nome_item = item.procedimento.nome
        grupo = grupos.setdefault(nome_item, {"procedimento": nome_item, "qtd": 0, "valor": Decimal("0.00")})
        grupo["qtd"] += item.quantidade
        grupo["valor"] += item.valor_cobrado or Decimal("0.00")

    # Venda de pacote (agenda.models.PacoteVendido): o preço fechado entra no dia do atendimento
    # em que foi pago. Pagamento estornado tira a venda daqui, igual ao resto do particular.
    for venda in _vendas_pacote_particular(data_inicio, data_fim, profissional):
        nome_item = f"Venda de pacote: {venda.nome}"
        grupo = grupos.setdefault(nome_item, {"procedimento": nome_item, "qtd": 0, "valor": Decimal("0.00")})
        grupo["qtd"] += 1
        grupo["valor"] += venda.valor

    linhas = [
        {"procedimento": grupo["procedimento"], "qtd": grupo["qtd"], "valor": grupo["valor"].quantize(Decimal("0.01"))}
        for grupo in grupos.values()
    ]
    linhas.sort(key=lambda l: -l["valor"])
    return linhas


def _particular_por_forma_pagamento(data_inicio, data_fim, profissional=None, valor_recebido=False):
    """Mesma base de _particular_por_procedimento, pivotado por forma_pagamento, na ordem
    fixa de Agendamento.FormaPagamento.choices. profissional=<Profissional> filtra só os
    atendimentos dele.

    qtd conta PROCEDIMENTOS, não atendimentos (pedido da clínica, 26/10/2026 — atendimento com
    2 procedimentos conta 2, igual à seção "por procedimento"). Atendimento pago em mais de uma
    forma conta os procedimentos só na forma de maior valor, pra soma das linhas bater com o
    total de procedimentos.

    valor_recebido=True soma PagamentoParticular.valor_recebido_efetivo (valor real recebido,
    já descontada a taxa da maquininha em cartão) em vez de 'valor' — usado só pelo Fechamento
    diário/mensal geral (ver _contexto_fechamento), nunca por 'Meu fechamento'
    (_contexto_meu_fechamento sempre chama sem este parâmetro, ou seja, sempre bruto)."""
    qs = PagamentoParticular.objects.filter(
        # Particular + convênio "funciona como particular" (23/10/2026).
        Agendamento.filtro_pago_como_particular("agendamento__"),
        agendamento__status__in=Agendamento.STATUS_PRODUCAO,
        agendamento__data_hora_inicio__date__range=(data_inicio, data_fim),
    )
    if profissional is not None:
        qs = qs.filter(agendamento__profissional=profissional)
    soma = Sum(Coalesce("valor_recebido", "valor")) if valor_recebido else Sum("valor")
    por_codigo = {linha["forma_pagamento"]: linha for linha in qs.values("forma_pagamento").annotate(valor=soma)}

    procedimentos_por_agendamento = {
        linha["agendamento_id"]: linha["qtd"] or 0
        for linha in _itens_particular(data_inicio, data_fim, profissional)
        .values("agendamento_id")
        .annotate(qtd=Sum("quantidade"))
    }
    for venda in _vendas_pacote_particular(data_inicio, data_fim, profissional):
        procedimentos_por_agendamento[venda.agendamento_venda_id] = (
            procedimentos_por_agendamento.get(venda.agendamento_venda_id, 0) + 1
        )
    forma_principal = {}
    for pagamento in qs.order_by("agendamento_id", "-valor", "id").values("agendamento_id", "forma_pagamento"):
        forma_principal.setdefault(pagamento["agendamento_id"], pagamento["forma_pagamento"])
    qtd_por_codigo = {}
    for agendamento_id, codigo in forma_principal.items():
        qtd_por_codigo[codigo] = qtd_por_codigo.get(codigo, 0) + procedimentos_por_agendamento.get(agendamento_id, 0)

    return [
        {
            "forma": rotulo,
            "qtd": qtd_por_codigo.get(codigo, 0),
            # Mesmo ajuste de _particular_por_procedimento — Sum() no SQLite não preserva as 2 casas
            # decimais do campo.
            "valor": (por_codigo[codigo]["valor"] if codigo in por_codigo else Decimal("0")).quantize(Decimal("0.01")),
        }
        for codigo, rotulo in Agendamento.FormaPagamento.choices
    ]


def _visao_proprietario(linhas_profissionais, data_inicio, data_fim):
    """A partir das linhas já calculadas por _producao_por_profissional (sem filtro de
    profissional), monta a visão do dono: produção própria (0% de retenção, já vem do
    repasse dele) + soma da retenção de TODOS os outros profissionais +/- entradas/saídas
    avulsas de caixa do período (ver financeiro.models.MovimentacaoCaixa —
    decisão do cliente, 2026-07-22: uma saída avulsa, ex. repasse pago em dinheiro na mão,
    precisa aparecer no resultado do dono) = resultado do proprietário no período. Busca o
    proprietário independente de ele aparecer ou não em linhas_profissionais (pode não ter
    atendido ninguém no período e ainda assim ter retenção dos demais a mostrar). None
    quando não há nenhum profissional marcado como proprietário cadastrado.

    Diferente de _contexto_fechamento (que não mostra caixa/estornos no fechamento de um
    profissional comum, de propósito), avulsas SÓ entram aqui, na visão do proprietário —
    nunca na linha de um profissional comum.

    producao_propria/retencao_outros usam liquido_recebido (valor real recebido, já descontado
    a taxa da maquininha em cartão — ver _producao_por_profissional) em vez de liquido/bruto:
    é a visão do PROPRIETÁRIO, que deve refletir o que de fato entrou na conta da clínica.
    retencao_outros continua calculada a partir de 'repasse' (o repasse de cada profissional
    comum, sempre bruto/sobre o valor cadastrado, nunca afetado pela taxa) — então a taxa de
    cartão absorvida nas transações deles reduz sozinha a retenção real da clínica, sem nunca
    reduzir o que é repassado a eles. Como repasse ≡ liquido - retido por definição, estas duas
    fórmulas se reduzem exatamente às de antes desta feature (repasse/retido) sempre que não
    houver valor_recebido preenchido em nenhuma linha do período (todo o histórico anterior a
    esta feature, por exemplo) — ver financeiro.tests para o teste que fixa essa garantia.

    dinheiro_total/banco_total: decisão do cliente (2026-07-29) — o proprietário precisa ver
    separado o que está em dinheiro físico do que está em conta. dinheiro_total é o dinheiro no
    caixa (_dinheiro_no_caixa, mesmo valor do Painel — desde 26/10/2026 inclui a parte dos
    profissionais ainda não repassada e o dinheiro de convênio pago na clínica, e desconta todas
    as saídas, inclusive repasse pago em dinheiro). banco_total é a parte do proprietário em
    cartão/Pix (produção própria + retenção dos outros). Por isso as duas NÃO somam mais o
    'resultado': o caixa físico guarda dinheiro que ainda é dos profissionais.

    avulsas_saidas_itens: as próprias MovimentacaoCaixa (tipo=SAIDA) do período, pra exibir o
    motivo de cada uma (campo 'descricao') — decisão do cliente (2026-07-29): o fechamento
    diário deve mostrar o que foi cada saída, não só o total (o mensal não usa este campo, ver
    _fechamento_secoes.html/meu_fechamento.html)."""
    proprietario = Profissional.objects.filter(eh_proprietario=True).first()
    if not proprietario:
        return None
    linha_propria = next((l for l in linhas_profissionais if l["profissional"].pk == proprietario.pk), None)
    producao_propria = (linha_propria["liquido_recebido"] - linha_propria["retido"]) if linha_propria else Decimal("0.00")
    producao_propria_dinheiro = (
        (linha_propria["liquido_recebido_dinheiro"] - linha_propria["retido_dinheiro"])
        if linha_propria
        else Decimal("0.00")
    )
    outros = [l for l in linhas_profissionais if l["profissional"].pk != proprietario.pk]
    retencao_outros = sum((l["liquido_recebido"] - l["repasse"] for l in outros), Decimal("0.00"))
    retencao_outros_dinheiro = sum(
        (l["liquido_recebido_dinheiro"] - l["repasse_dinheiro"] for l in outros), Decimal("0.00")
    )
    avulsas_itens, avulsas_entradas, avulsas_saidas_caixa = _movimentacoes_avulsas_do_periodo(data_inicio, data_fim)
    # Saída gerada por repasse particular pago em dinheiro (RepassePagamento.movimentacao): tira
    # dinheiro do caixa, mas NÃO muda o resultado — a parte do profissional já fica fora da
    # retenção. Antes ela era descontada de novo aqui (relatado pela clínica, 26/10/2026).
    ids_repasse = set(
        RepassePagamento.objects.filter(movimentacao__in=avulsas_itens).values_list("movimentacao_id", flat=True)
    )
    saidas = [item for item in avulsas_itens if item.tipo == MovimentacaoCaixa.Tipo.SAIDA]
    avulsas_saidas_itens = [item for item in saidas if item.pk not in ids_repasse]
    repasses_dinheiro_itens = [item for item in saidas if item.pk in ids_repasse]
    avulsas_saidas = sum((item.valor for item in avulsas_saidas_itens), Decimal("0.00"))
    repasses_dinheiro = sum((item.valor for item in repasses_dinheiro_itens), Decimal("0.00"))
    dinheiro_total = _dinheiro_no_caixa(linhas_profissionais, data_inicio, data_fim, avulsas_entradas, avulsas_saidas_caixa)
    banco_total = (producao_propria - producao_propria_dinheiro) + (retencao_outros - retencao_outros_dinheiro)
    return {
        "profissional": proprietario,
        "producao_propria": producao_propria,
        "retencao_outros": retencao_outros,
        "avulsas_entradas": avulsas_entradas,
        "avulsas_saidas": avulsas_saidas,
        "avulsas_saidas_itens": avulsas_saidas_itens,
        "repasses_dinheiro": repasses_dinheiro,
        "repasses_dinheiro_itens": repasses_dinheiro_itens,
        "dinheiro_total": dinheiro_total,
        "banco_total": banco_total,
        "resultado": producao_propria + retencao_outros + avulsas_entradas - avulsas_saidas,
    }


def _contexto_fechamento(data_inicio, data_fim, profissional_detalhe=None, profissional_repasse=None):
    """Monta o contexto comum aos fechamentos diário e mensal (seções de convênio,
    particular e repasse por profissional/visão do proprietário) — só muda o período (um
    dia ou o mês inteiro). Estornos e posição de caixa NÃO entram aqui de propósito: esta
    é a tela que fica impressa/entregue ao profissional, e aquilo é movimento interno da
    clínica — quem precisa disso usa o Painel financeiro (ver painel_financeiro). Não é
    usado por 'meu fechamento' de quem não é proprietário, que só precisa da própria linha.

    Dois parâmetros de profissional, porque "de quem são os procedimentos exibidos" e
    "quem entra na tabela de repasse" respondem perguntas diferentes quando o filtro
    selecionado é o PRÓPRIO proprietário:

    - profissional_detalhe filtra Convênios/Particular por procedimento/por forma —
      qualquer seleção do filtro (inclusive o proprietário) restringe às dele; None
      (ninguém selecionado, "Geral") traz a clínica inteira.
    - profissional_repasse filtra a tabela "Repasse por profissional" e decide se calcula
      'visão do proprietário' — aqui selecionar o proprietário CONTINUA contando como "sem
      filtro" (ver _resolver_filtro_profissional), porque ele deve continuar vendo a
      retenção de todos os outros ali ("repasse por profissional já basta" pra ele
      acompanhar o que os demais fizeram, sem precisar repetir os procedimentos de todo
      mundo nas seções de detalhe acima). Só um profissional comum de fato restringe essa
      tabela a uma única linha (a dele).
    """
    linhas_profissionais, totais_profissionais = _producao_por_profissional(
        data_inicio, data_fim, profissional=profissional_repasse
    )
    convenios, convenios_total = _convenios_por_procedimento(data_inicio, data_fim, profissional=profissional_detalhe)
    # Filtrado por um profissional comum (tela entregue a ele): convênio já com o desconto da
    # clínica, o valor que de fato é dele — pedido da clínica, 26/10/2026. Proprietário e
    # visão geral continuam com o valor cheio da tabela.
    convenio_desconto = profissional_repasse.percentual_desconto if profissional_repasse is not None else None
    if convenio_desconto:
        _descontar_convenios(convenios, convenio_desconto)
    retornos, retornos_total = _retornos_por_especialidade(data_inicio, data_fim, profissional=profissional_detalhe)
    convenios_valor = sum((g["valor"] for g in convenios), Decimal("0.00"))
    particular_procedimento = _particular_por_procedimento(data_inicio, data_fim, profissional=profissional_detalhe)
    # "Produção do período" = particular (valor cobrado, por procedimento) + convênio (valor
    # da tabela, a receber do plano) — pedido da clínica, 23/10/2026.
    particular_valor = sum((l["valor"] for l in particular_procedimento), Decimal("0.00"))
    # Profissional comum: particular também já com a retenção da clínica (= repasse líquido da
    # tabela abaixo, que já trata valor fixo e estorno) — pedido da clínica, 27/10/2026.
    particular_descontado = profissional_repasse is not None
    if particular_descontado:
        particular_valor = totais_profissionais["repasse"]

    return {
        "linhas_profissionais": linhas_profissionais,
        "totais_profissionais": totais_profissionais,
        "convenios": convenios,
        "convenios_total": convenios_total,
        "convenios_valor": convenios_valor,
        "convenios_sem_valor": sum(g["sem_valor"] for g in convenios),
        "convenio_desconto": convenio_desconto,
        "particular_valor": particular_valor,
        "particular_descontado": particular_descontado,
        "producao_total": particular_valor + convenios_valor,
        "retornos": retornos,
        "retornos_total": retornos_total,
        "particular_procedimento": particular_procedimento,
        "particular_procedimentos_total": sum(l["qtd"] for l in particular_procedimento),
        # valor_recebido=True só na visão SEM filtro de profissional (geral) — mesma regra do
        # visao_proprietario logo abaixo: filtrado por um profissional específico (tela que às
        # vezes é impressa e entregue a ele) volta ao valor cheio, pra nunca revelar a taxa da
        # maquininha/margem da clínica pra quem recebe esse papel (decisão do cliente).
        "particular_forma": _particular_por_forma_pagamento(
            data_inicio, data_fim, profissional=profissional_detalhe, valor_recebido=(profissional_repasse is None)
        ),
        "visao_proprietario": (
            _visao_proprietario(linhas_profissionais, data_inicio, data_fim) if profissional_repasse is None else None
        ),
    }


def _descontar_convenios(convenios, percentual):
    """Aplica o percentual de retenção da clínica aos valores de _convenios_por_procedimento (no
    lugar) — mesma conta de _repasse_convenio_por_profissional, por item e por grupo."""
    fator = (Decimal("100") - percentual) / Decimal("100")
    for grupo in convenios:
        for bucket in grupo["itens"]:
            bucket["valor"] = (bucket["valor"] * fator).quantize(Decimal("0.01"))
        grupo["valor"] = (grupo["valor"] * fator).quantize(Decimal("0.01"))


def _resolver_filtro_profissional(request):
    """Lê ?profissional= da querystring e resolve o Profissional selecionado (ou None se
    vazio/'todos'). Usado por fechamento_diario/fechamento_mensal — selecionar o próprio
    proprietário é tratado como 'sem filtro' (visão geral), porque a visão dele já é a da
    clínica inteira; só um profissional comum de fato filtra os dados. Retorna
    (profissional_selecionado, filtro) — o primeiro é só pra marcar o <select> e mostrar o
    nome no título, o segundo é o que efetivamente entra em _contexto_fechamento."""
    profissional_id = request.GET.get("profissional") or ""
    profissional_selecionado = None
    if profissional_id:
        profissional_selecionado = get_object_or_404(Profissional, pk=profissional_id)
    filtro = profissional_selecionado if (profissional_selecionado and not profissional_selecionado.eh_proprietario) else None
    return profissional_selecionado, filtro


@permission_required("accounts.financeiro_ver_fechamento_diario", raise_exception=True)
def fechamento_diario(request):
    hoje = timezone.localdate()
    data = parse_date(request.GET.get("data") or "") or hoje
    profissional_selecionado, filtro = _resolver_filtro_profissional(request)

    contexto = _contexto_fechamento(data, data, profissional_detalhe=profissional_selecionado, profissional_repasse=filtro)
    contexto.update({
        "data": data,
        "dia_anterior": data - timedelta(days=1),
        "dia_seguinte": data + timedelta(days=1),
        "profissionais": Profissional.objects.filter(ativo=True).order_by("nome"),
        "profissional_selecionado": profissional_selecionado,
    })
    return render(request, "financeiro/fechamento_diario.html", contexto)


@permission_required("accounts.financeiro_ver_fechamento_mensal", raise_exception=True)
def fechamento_mensal(request):
    hoje = timezone.localdate()
    ano = int(request.GET.get("ano") or hoje.year)
    mes = int(request.GET.get("mes") or hoje.month)
    data_inicio, data_fim = _mes_calendario(ano, mes)
    mes_anterior_ano, mes_anterior_mes = (ano - 1, 12) if mes == 1 else (ano, mes - 1)
    mes_seguinte_ano, mes_seguinte_mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    profissional_selecionado, filtro = _resolver_filtro_profissional(request)

    contexto = _contexto_fechamento(
        data_inicio, data_fim, profissional_detalhe=profissional_selecionado, profissional_repasse=filtro
    )
    contexto.update({
        "ano": ano,
        "mes": mes,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "mes_anterior_ano": mes_anterior_ano,
        "mes_anterior_mes": mes_anterior_mes,
        "mes_seguinte_ano": mes_seguinte_ano,
        "mes_seguinte_mes": mes_seguinte_mes,
        "profissionais": Profissional.objects.filter(ativo=True).order_by("nome"),
        "profissional_selecionado": profissional_selecionado,
    })
    return render(request, "financeiro/fechamento_mensal.html", contexto)


def _pode_ver_meu_fechamento(view_func):
    """'Meu fechamento' é de todo profissional (clínica, 28/10/2026: o profissional precisa
    conferir o próprio fechamento diário/mensal) — basta o usuário estar vinculado a um
    cadastro de Profissional, igual ao Prontuário; a permissão financeiro_ver_proprio
    continua valendo pra quem já a tinha."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not (request.user.profissional_id or request.user.has_perm("accounts.financeiro_ver_proprio")):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper


def _contexto_meu_fechamento(profissional, data_inicio, data_fim):
    """'Meu fechamento' — sempre a própria produção detalhada (convênio por procedimento,
    particular por procedimento/forma, igual ao fechamento diário/mensal), mais, se for o
    proprietário, a retenção da clínica sobre os demais. As seções de detalhe usam sempre
    profissional=<self> (mesmo pro proprietário — a visão com todo mundo já existe em
    Fechamento diário/mensal, pra quem tiver financeiro_ver_fechamento_diario/_mensal). Sem
    estornos gerais/caixa da clínica, que são movimento interno — ver painel_financeiro."""
    if profissional.eh_proprietario:
        linhas, _totais = _producao_por_profissional(data_inicio, data_fim)
        linha_propria = next((l for l in linhas if l["profissional"].pk == profissional.pk), None)
        visao_proprietario = _visao_proprietario(linhas, data_inicio, data_fim)
    else:
        linhas, _totais = _producao_por_profissional(data_inicio, data_fim, profissional=profissional)
        linha_propria = linhas[0] if linhas else None
        visao_proprietario = None

    convenios, convenios_total = _convenios_por_procedimento(data_inicio, data_fim, profissional=profissional)
    # Clínica, 28/10/2026: o profissional confere aqui quanto leva no dia/mês — convênio com
    # valor e já com o desconto da clínica (mesma conta do fechamento filtrado por ele, ver
    # _contexto_fechamento); o proprietário vê o valor cheio (não tem retenção).
    convenio_desconto = None if profissional.eh_proprietario else profissional.percentual_desconto
    if convenio_desconto:
        _descontar_convenios(convenios, convenio_desconto)
    convenios_valor = sum((g["valor"] for g in convenios), Decimal("0.00"))
    particular_liquido = linha_propria["repasse"] if linha_propria else Decimal("0.00")
    retornos, retornos_total = _retornos_por_especialidade(data_inicio, data_fim, profissional=profissional)
    particular_procedimento = _particular_por_procedimento(data_inicio, data_fim, profissional=profissional)
    return {
        "profissional": profissional,
        "linha_propria": linha_propria,
        "visao_proprietario": visao_proprietario,
        "convenios": convenios,
        "convenios_total": convenios_total,
        "convenios_valor": convenios_valor,
        "convenios_sem_valor": sum(g["sem_valor"] for g in convenios),
        "convenio_desconto": convenio_desconto,
        "particular_liquido": particular_liquido,
        "total_liquido": particular_liquido + convenios_valor,
        "retornos": retornos,
        "retornos_total": retornos_total,
        "particular_procedimento": particular_procedimento,
        "particular_procedimentos_total": sum(l["qtd"] for l in particular_procedimento),
        "particular_forma": _particular_por_forma_pagamento(data_inicio, data_fim, profissional=profissional),
    }


@_pode_ver_meu_fechamento
def meu_fechamento_diario(request):
    profissional = _profissional_do_usuario(request)
    if profissional is None:
        messages.error(request, "Seu usuário não está vinculado a um cadastro de profissional.")
        return redirect("home")

    hoje = timezone.localdate()
    data = parse_date(request.GET.get("data") or "") or hoje
    contexto = _contexto_meu_fechamento(profissional, data, data)
    contexto.update({
        "periodo": "diario",
        "data": data,
        "dia_anterior": data - timedelta(days=1),
        "dia_seguinte": data + timedelta(days=1),
    })
    return render(request, "financeiro/meu_fechamento.html", contexto)


@_pode_ver_meu_fechamento
def meu_fechamento_mensal(request):
    profissional = _profissional_do_usuario(request)
    if profissional is None:
        messages.error(request, "Seu usuário não está vinculado a um cadastro de profissional.")
        return redirect("home")

    hoje = timezone.localdate()
    ano = int(request.GET.get("ano") or hoje.year)
    mes = int(request.GET.get("mes") or hoje.month)
    data_inicio, data_fim = _mes_calendario(ano, mes)
    mes_anterior_ano, mes_anterior_mes = (ano - 1, 12) if mes == 1 else (ano, mes - 1)
    mes_seguinte_ano, mes_seguinte_mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)

    contexto = _contexto_meu_fechamento(profissional, data_inicio, data_fim)
    contexto.update({
        "periodo": "mensal",
        "ano": ano,
        "mes": mes,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "mes_anterior_ano": mes_anterior_ano,
        "mes_anterior_mes": mes_anterior_mes,
        "mes_seguinte_ano": mes_seguinte_ano,
        "mes_seguinte_mes": mes_seguinte_mes,
    })
    return render(request, "financeiro/meu_fechamento.html", contexto)


@permission_required("accounts.financeiro_ver_relatorio_detalhado", raise_exception=True)
def relatorio_detalhado(request):
    """
    Tela separada dos fechamentos — aqui sim aparece paciente e procedimento por
    atendimento (fechamento diário/mensal são resumos agrupados, sem nome de paciente).

    Filtros iguais aos da tela de Autorizações do sistema antigo (pedido da clínica,
    03/11/2026): período (de/até), convênio (ou "particular"), nome do paciente e
    profissional. Os links antigos continuam valendo: ?data=AAAA-MM-DD (um dia, ex.: o do
    Fechamento diário) e ?modo=mes&ano=&mes= (mês inteiro, mesmo recorte de
    _mes_calendario). Período com mais de um dia ganha a coluna "Data" (ver template).

    A coluna "Guia" mostra a posição da sessão na guia do convênio ("3/10" — ver
    GuiaConvenio.faixas_dos_itens), o que no sistema antigo a recepção anotava à mão na
    observação.

    Desempenho: tudo por linha sai dos prefetch (as properties tem_item_cobravel/
    coberto_por_pacote/valor_cobrado do model fazem uma consulta cada, e o template chamava
    várias por linha — num mês inteiro eram milhares de consultas).
    """
    hoje = timezone.localdate()
    profissional_id = request.GET.get("profissional") or ""
    convenio_id = request.GET.get("convenio") or ""
    paciente_nome = (request.GET.get("paciente") or "").strip()

    de = parse_date(request.GET.get("de") or "")
    ate = parse_date(request.GET.get("ate") or "")
    if de or ate:
        data_inicio, data_fim = de or ate, ate or de
    elif request.GET.get("modo") == "mes":
        data_inicio, data_fim = _mes_calendario(
            int(request.GET.get("ano") or hoje.year), int(request.GET.get("mes") or hoje.month)
        )
    else:
        data_inicio = data_fim = parse_date(request.GET.get("data") or "") or hoje
    if data_fim < data_inicio:
        data_inicio, data_fim = data_fim, data_inicio

    agendamentos_qs = (
        Agendamento.objects.filter(status__in=Agendamento.STATUS_PRODUCAO, data_hora_inicio__date__range=(data_inicio, data_fim))
        .select_related("paciente", "profissional", "convenio")
        .prefetch_related("pagamentos")
        .order_by("data_hora_inicio", "pk")
    )
    if profissional_id.isdigit():
        agendamentos_qs = agendamentos_qs.filter(profissional_id=profissional_id)
    if convenio_id == "particular":
        agendamentos_qs = agendamentos_qs.filter(forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR)
    elif convenio_id.isdigit():
        agendamentos_qs = agendamentos_qs.filter(convenio_id=convenio_id)
    if paciente_nome:
        agendamentos_qs = agendamentos_qs.filter(paciente__nome_completo__icontains=paciente_nome)

    agendamentos = list(agendamentos_qs)

    # Totais do rodapé: o período INTEIRO (não só a página) — só precisam dos pagamentos.
    particular_liquido_total = Decimal("0.00")
    particular_recebido_total = Decimal("0.00")
    convenio_total = Decimal("0.00")
    for agendamento in agendamentos:
        pagamentos = agendamento.pagamentos.all()
        # Convênio "funciona como particular" pago na clínica soma com o particular, não como a receber do plano.
        if agendamento.forma_atendimento == Agendamento.FormaAtendimento.PARTICULAR or (
            pagamentos and agendamento.convenio_id and agendamento.convenio.funciona_como_particular
        ):
            # = Agendamento.valor_cobrado (líquido de estorno), sem a consulta.
            particular_liquido_total += sum((p.valor for p in pagamentos if not p.estornado), Decimal("0.00"))
            # Exclui estornado, igual valor_cobrado (líquido de estorno) — diferente do
            # valor_recebido_total por linha abaixo, que propositalmente não exclui.
            particular_recebido_total += sum(
                (p.valor_recebido_efetivo for p in pagamentos if not p.estornado), Decimal("0.00")
            )
        elif agendamento.valor_convenio:
            convenio_total += agendamento.valor_convenio

    # A tabela é paginada de 50 em 50 (pedido da clínica, 03/11/2026: convênio com centenas
    # de atendimentos no mês numa página só) — itens/guias/pacotes só da página.
    # ?tudo=1 (link "Abrir as N linhas para imprimir") traz tudo numa página só.
    pagina = paginar(request, agendamentos, max(len(agendamentos), 1) if request.GET.get("tudo") == "1" else POR_PAGINA)
    prefetch_related_objects(pagina.object_list, "itens__procedimento", "itens__guia_convenio", "pacotes_vendidos")
    faixas = GuiaConvenio.faixas_dos_itens([item for ag in pagina.object_list for item in ag.itens.all()])

    linhas = []
    for agendamento in pagina.object_list:
        pagamentos = list(agendamento.pagamentos.all())
        itens = list(agendamento.itens.all())
        vendas_pacote = list(agendamento.pacotes_vendidos.all())
        tem_estorno = any(p.estornado for p in pagamentos)
        valor_bruto = sum((p.valor for p in pagamentos), Decimal("0.00"))
        # Mesmo espírito de valor_bruto acima (soma TODAS as linhas, incluindo estornadas, pra
        # não encolher silenciosamente se a tela for reimpressa depois de um estorno tardio).
        valor_recebido_total = sum((p.valor_recebido_efetivo for p in pagamentos), Decimal("0.00"))
        # Mesmas regras de Agendamento.tem_item_cobravel / coberto_por_pacote, sem consulta.
        tem_item_cobravel = bool(vendas_pacote) or any(not i.eh_retorno and not i.pacote_vendido_id for i in itens)
        coberto_por_pacote = any(i.pacote_vendido_id for i in itens) and not tem_item_cobravel

        guias = []
        for item in itens:
            if item.pk in faixas:
                inicio, fim, total = faixas[item.pk]
                guias.append({
                    "numero": item.guia_convenio.numero,
                    "sessoes": f"{inicio}" if inicio == fim else f"{inicio}–{fim}",
                    "total": total,
                    "ultima": fim >= total,
                })

        linhas.append({
            "agendamento": agendamento,
            "itens_resumo": ", ".join(str(i) for i in itens) or "—",
            "vendas_pacote": vendas_pacote,
            "pagamentos": pagamentos,
            "tem_estorno": tem_estorno,
            "valor_bruto": valor_bruto,
            "valor_recebido_total": valor_recebido_total,
            "tem_item_cobravel": tem_item_cobravel,
            "coberto_por_pacote": coberto_por_pacote,
            "guias": guias,
        })

    # Atalhos ‹ anterior / seguinte › — só quando o período é um dia ou um mês calendário inteiro.
    navegacao = None
    if data_inicio == data_fim:
        navegacao = {
            "rotulo": "Dia",
            "anterior": (data_inicio - timedelta(days=1),) * 2,
            "seguinte": (data_inicio + timedelta(days=1),) * 2,
        }
    elif (data_inicio, data_fim) == _mes_calendario(data_inicio.year, data_inicio.month):
        mes_anterior = data_inicio - timedelta(days=1)
        mes_seguinte = data_fim + timedelta(days=1)
        navegacao = {
            "rotulo": "Mês",
            "anterior": _mes_calendario(mes_anterior.year, mes_anterior.month),
            "seguinte": _mes_calendario(mes_seguinte.year, mes_seguinte.month),
        }

    contexto = {
        "linhas": linhas,
        "pagina": pagina,
        "particular_liquido_total": particular_liquido_total,
        "particular_recebido_total": particular_recebido_total,
        "convenio_total": convenio_total,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "um_dia": data_inicio == data_fim,
        "navegacao": navegacao,
        "hoje": hoje,
        "mes_atual": _mes_calendario(hoje.year, hoje.month),
        "profissionais": Profissional.objects.filter(ativo=True).order_by("nome"),
        "convenios": Convenio.objects.filter(ativo=True).order_by("nome"),
        "profissional_selecionado": profissional_id,
        "convenio_selecionado": convenio_id,
        "paciente_nome": paciente_nome,
        # Os demais filtros, pra manter nos atalhos de período.
        "filtros_qs": urlencode({
            chave: valor
            for chave, valor in (("convenio", convenio_id), ("paciente", paciente_nome), ("profissional", profissional_id))
            if valor
        }),
    }
    return render(request, "financeiro/relatorio_detalhado.html", contexto)


# =====================================================================
# Repasse aos profissionais (convênio) + Produção por convênio
# =====================================================================

def _resolver_mes(request):
    """Lê ano/mes da querystring (padrão: mês atual) e devolve o intervalo do mês + os pares
    ano/mês anterior e seguinte — mesmo cálculo já usado em fechamento_mensal/
    meu_fechamento_mensal/relatorio_detalhado (modo=mes), reunido aqui porque repasse_convenio_lista
    e producao_por_convenio precisam do mesmo bloco e já seriam a 4ª e 5ª cópia dele no arquivo."""
    hoje = timezone.localdate()
    ano = int(request.GET.get("ano") or hoje.year)
    mes = int(request.GET.get("mes") or hoje.month)
    data_inicio, data_fim = _mes_calendario(ano, mes)
    mes_anterior_ano, mes_anterior_mes = (ano - 1, 12) if mes == 1 else (ano, mes - 1)
    mes_seguinte_ano, mes_seguinte_mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return {
        "ano": ano,
        "mes": mes,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "mes_anterior_ano": mes_anterior_ano,
        "mes_anterior_mes": mes_anterior_mes,
        "mes_seguinte_ano": mes_seguinte_ano,
        "mes_seguinte_mes": mes_seguinte_mes,
    }


def _repasse_convenio_por_profissional(data_inicio, data_fim, convenio=None):
    """
    Repasse de CONVÊNIO ao profissional — diferente de _producao_por_profissional (que é só
    particular). Decisões confirmadas com o cliente: convênio aqui é sempre tratado como já
    recebido pela clínica (não existe "aguardando convênio" nesta tela — essa distinção é de
    Contas a receber, à parte); "pago" é Agendamento.convenio_repasse_pago, mas a ação de marcar
    (ver repasse_convenio_marcar_pago) sempre paga de uma vez o valor completo pendente do
    profissional, nunca convênio por convênio — por isso um grupo de convênio só conta como
    "pendente" (não pago) se tiver PELO MENOS UM atendimento com convenio_repasse_pago=False.

    O proprietário fica de fora (eh_proprietario=True): produção própria não gera repasse dele
    pra ele mesmo. convenio=<Convenio> filtra só aquele; None traz todos.

    Retorno grátis (ver AgendamentoItem.eh_retorno) sai como um item "Retorno" próprio
    dentro de cada convênio, separado do procedimento real — mesmo motivo de
    _convenios_por_procedimento: o bruto do retorno é sempre R$ 0,00, então ele não pode
    ficar escondido dentro da contagem/bruto de "Consulta" na tabela de repasse.

    Iterado por AgendamentoItem (não mais por Agendamento inteiro): cada item pode ter um
    procedimento diferente, com valor_convenio próprio — atribuir o valor_convenio inteiro
    do agendamento a um único procedimento (como a versão anterior fazia) ficaria errado
    assim que um agendamento tiver mais de um item de convênio.

    Retorna uma lista de dicts ordenada por nome do profissional (pedido do cliente: sempre
    alfabética — quem não tem atendimento de convênio no período simplesmente não entra na
    lista, sem precisar de filtro de profissional na tela). Cada dict tem: profissional,
    percentual, atendimentos, a_repassar, repassado, e "convenios" — lista de
    {convenio, itens: [{procedimento, qtd, bruto}], bruto, retido, liquido, pendente}.
    """
    qs = (
        AgendamentoItem.objects.filter(
            Agendamento.filtro_convenio_a_faturar("agendamento__"),
            agendamento__status__in=Agendamento.STATUS_PRODUCAO,
            agendamento__data_hora_inicio__date__range=(data_inicio, data_fim),
            valor_convenio__isnull=False,
        )
        .exclude(agendamento__profissional__eh_proprietario=True)
        .select_related("agendamento__profissional", "agendamento__convenio", "procedimento")
    )
    if convenio is not None:
        qs = qs.filter(agendamento__convenio=convenio)

    profissionais = {}
    for item in qs:
        ag = item.agendamento
        grupo_prof = profissionais.setdefault(
            ag.profissional_id,
            {"profissional": ag.profissional, "convenios": {}, "agendamento_ids": set()},
        )
        grupo_prof["agendamento_ids"].add(ag.pk)
        grupo_conv = grupo_prof["convenios"].setdefault(
            ag.convenio_id, {"convenio": ag.convenio, "itens": {}, "bruto": Decimal("0.00"), "pendente": False}
        )
        nome_item = "Retorno" if item.eh_retorno else item.procedimento.nome
        bucket = grupo_conv["itens"].setdefault(nome_item, {"procedimento": nome_item, "qtd": 0, "bruto": Decimal("0.00")})
        bucket["qtd"] += item.quantidade_produzida  # só guias autorizadas (ver controla_guias)
        bucket["bruto"] += item.valor_convenio
        grupo_conv["bruto"] += item.valor_convenio
        if not ag.convenio_repasse_pago:
            grupo_conv["pendente"] = True

    linhas = []
    for grupo_prof in sorted(profissionais.values(), key=lambda g: g["profissional"].nome):
        prof = grupo_prof["profissional"]
        percentual = prof.percentual_desconto
        convenios = []
        a_repassar = Decimal("0.00")
        repassado = Decimal("0.00")
        for grupo_conv in sorted(grupo_prof["convenios"].values(), key=lambda g: g["convenio"].nome):
            bruto = grupo_conv["bruto"]
            retido = (bruto * percentual / Decimal("100")).quantize(Decimal("0.01"))
            liquido = bruto - retido
            if grupo_conv["pendente"]:
                a_repassar += liquido
            else:
                repassado += liquido
            itens_ordenados = sorted(grupo_conv["itens"].values(), key=lambda i: i["procedimento"])
            convenios.append({
                "convenio": grupo_conv["convenio"],
                "itens": itens_ordenados,
                "bruto": bruto,
                "retido": retido,
                "liquido": liquido,
                "pendente": grupo_conv["pendente"],
            })
        linhas.append({
            "profissional": prof,
            "percentual": percentual,
            "atendimentos": len(grupo_prof["agendamento_ids"]),
            "convenios": convenios,
            "a_repassar": a_repassar,
            "repassado": repassado,
        })

    return linhas


@permission_required("accounts.financeiro_ver_repasse_convenio", raise_exception=True)
def repasse_convenio_lista(request):
    """
    'Repasse aos profissionais' de convênio — só proprietário e financeiro
    (financeiro_ver_repasse_convenio, própria desde a divisão de 2026-07-28 — antes era a
    mesma financeiro_ver_completo dos outros fechamentos). Sempre por mês (pedido do cliente:
    precisa saber quanto será repassado NAQUELE mês); sem filtro de profissional de propósito.
    """
    periodo = _resolver_mes(request)
    convenio_id = request.GET.get("convenio") or ""
    convenio_selecionado = get_object_or_404(Convenio, pk=convenio_id) if convenio_id else None

    linhas = _repasse_convenio_por_profissional(periodo["data_inicio"], periodo["data_fim"], convenio=convenio_selecionado)
    # Particular aparece por profissional só como informação de controle (pedido do cliente) —
    # o repasse dele de verdade já é calculado à parte em _producao_por_profissional/Fechamento,
    # por isso não entra em nenhum dos totais desta tela (a_repassar/repassado/bruto/retido).
    for linha in linhas:
        linha["particular"] = _particular_por_procedimento(
            periodo["data_inicio"], periodo["data_fim"], profissional=linha["profissional"]
        )
        linha["particular_total"] = sum((item["valor"] for item in linha["particular"]), Decimal("0.00"))

    total_a_repassar = sum((linha["a_repassar"] for linha in linhas), Decimal("0.00"))
    total_repassado = sum((linha["repassado"] for linha in linhas), Decimal("0.00"))
    total_bruto = sum((grupo["bruto"] for linha in linhas for grupo in linha["convenios"]), Decimal("0.00"))
    total_retido = sum((grupo["retido"] for linha in linhas for grupo in linha["convenios"]), Decimal("0.00"))

    contexto = {
        "linhas": linhas,
        "total_a_repassar": total_a_repassar,
        "total_repassado": total_repassado,
        "total_bruto": total_bruto,
        "total_retido": total_retido,
        "convenios": Convenio.objects.filter(ativo=True).order_by("nome"),
        "convenio_selecionado": convenio_id,
    }
    contexto.update(periodo)
    return render(request, "financeiro/repasse_convenio_lista.html", contexto)


@permission_required("accounts.financeiro_conciliar", raise_exception=True)
def repasse_convenio_marcar_pago(request, profissional_id):
    """'Marcar tudo como repassado' — paga de uma vez todos os atendimentos de convênio ainda
    pendentes deste profissional no mês informado (decisão do cliente: o pagamento ao
    profissional é sempre do valor completo, não convênio por convênio). Só aceita POST."""
    profissional = get_object_or_404(Profissional, pk=profissional_id)
    hoje = timezone.localdate()
    ano = int(request.POST.get("ano") or hoje.year)
    mes = int(request.POST.get("mes") or hoje.month)
    data_inicio, data_fim = _mes_calendario(ano, mes)
    destino = f"{reverse('financeiro:repasse_convenio_lista')}?ano={ano}&mes={mes}"

    if request.method == "POST":
        atualizados = Agendamento.objects.filter(
            Agendamento.filtro_convenio_a_faturar(),
            profissional=profissional,
            status__in=Agendamento.STATUS_PRODUCAO,
            data_hora_inicio__date__range=(data_inicio, data_fim),
            valor_convenio__isnull=False,
            convenio_repasse_pago=False,
        ).update(convenio_repasse_pago=True, convenio_repasse_pago_em=timezone.now())
        if atualizados:
            messages.success(
                request,
                f"Repasse de convênio de '{profissional.nome}' marcado como pago "
                f"({atualizados} atendimento(s), referente a {mes:02d}/{ano}).",
            )
        else:
            messages.info(request, f"Não havia repasse de convênio pendente para '{profissional.nome}' neste período.")
    return redirect(destino)


def _producao_por_convenio(data_inicio, data_fim, convenio=None):
    """
    'Produção por convênio' — controle interno de quanto a clínica fez por PLANO, quebrado por
    especialidade e procedimento, com valores (diferente de _convenios_por_procedimento, que só
    conta quantidade, sem valor nem especialidade — usado no Fechamento). Sem repasse nem
    retenção aqui: é só produção. Particular entra como mais um grupo (pseudo-convênio,
    "eh_particular": True), sempre por último, informativo — dá a visão completa do mês (pedido
    do cliente) sem se misturar com o repasse de verdade (tela separada, repasse_convenio_lista).
    Só aparece quando convenio=None (filtrar por um convênio específico não faz sentido incluir
    particular junto).

    Retorno grátis (ver AgendamentoItem.eh_retorno) sai como um item "Retorno" próprio
    dentro de cada especialidade, separado do procedimento real (ex.: "Consulta") — mesmo
    motivo de _convenios_por_procedimento: só a consulta de fato é cobrada, o retorno
    sempre soma R$ 0,00 (hard lock), então não pode ficar escondido dentro da
    contagem/valor de "Consulta". O "qtd"/"valor" de cada especialidade e grupo continuam
    somando os dois — é "quanto a clínica atendeu", o retorno só deixa de estar disfarçado
    de Consulta.

    Iterado por AgendamentoItem (não mais por Agendamento/PagamentoParticular inteiros):
    cada item pode ter um procedimento diferente. A metade particular passa a somar
    AgendamentoItem.valor_cobrado (congelado no pagamento) em vez de PagamentoParticular.valor
    — diferente da versão anterior desta função, não exclui mais pagamento estornado
    (não há como ligar de volta "qual forma de pagamento foi estornada" a "qual item", já
    que pagamento continua sendo por agendamento inteiro, não por item — mesma política já
    usada em _particular_por_procedimento, mantendo as duas seções consistentes entre si).

    Retorna (grupos, total_geral). grupos: lista de dicts {nome, eh_particular, especialidades:
    [{nome, itens: [{procedimento, qtd, valor}], qtd, valor}], qtd, valor} — convênios em ordem
    alfabética, Particular por último.
    """
    qs = (
        AgendamentoItem.objects.filter(
            agendamento__forma_atendimento=Agendamento.FormaAtendimento.CONVENIO,
            agendamento__status__in=Agendamento.STATUS_PRODUCAO,
            agendamento__data_hora_inicio__date__range=(data_inicio, data_fim),
            valor_convenio__isnull=False,
        )
        .select_related("agendamento__profissional", "agendamento__convenio", "procedimento")
    )
    if convenio is not None:
        qs = qs.filter(agendamento__convenio=convenio)

    def _nome_item(item):
        return "Retorno" if item.eh_retorno else item.procedimento.nome

    grupos = {}
    for item in qs:
        ag = item.agendamento
        grupo = grupos.setdefault(
            ("convenio", ag.convenio_id), {"nome": ag.convenio.nome, "eh_particular": False, "especialidades": {}}
        )
        especialidade = grupo["especialidades"].setdefault(ag.especialidade_exibicao, {})
        nome_item = _nome_item(item)
        bucket = especialidade.setdefault(nome_item, {"procedimento": nome_item, "qtd": 0, "valor": Decimal("0.00")})
        bucket["qtd"] += item.quantidade_produzida  # só guias autorizadas (ver controla_guias)
        bucket["valor"] += item.valor_convenio

    if convenio is None:
        particular_qs = (
            AgendamentoItem.objects.filter(
                agendamento__forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
                agendamento__status__in=Agendamento.STATUS_PRODUCAO,
                agendamento__data_hora_inicio__date__range=(data_inicio, data_fim),
            )
            .select_related("agendamento__profissional", "procedimento")
        )
        for item in particular_qs:
            ag = item.agendamento
            grupo = grupos.setdefault(("particular",), {"nome": "Particular", "eh_particular": True, "especialidades": {}})
            especialidade = grupo["especialidades"].setdefault(ag.especialidade_exibicao, {})
            nome_item = _nome_item(item)
            bucket = especialidade.setdefault(nome_item, {"procedimento": nome_item, "qtd": 0, "valor": Decimal("0.00")})
            bucket["qtd"] += item.quantidade
            bucket["valor"] += item.valor_cobrado or Decimal("0.00")

    grupos_prontos = []
    for grupo in grupos.values():
        especialidades_prontas = []
        qtd_grupo = 0
        valor_grupo = Decimal("0.00")
        for nome_especialidade, itens in sorted(grupo["especialidades"].items()):
            itens_ordenados = sorted(itens.values(), key=lambda i: i["procedimento"])
            qtd_especialidade = sum(i["qtd"] for i in itens_ordenados)
            valor_especialidade = sum((i["valor"] for i in itens_ordenados), Decimal("0.00"))
            especialidades_prontas.append({
                "nome": nome_especialidade or "Não informada",
                "itens": itens_ordenados,
                "qtd": qtd_especialidade,
                "valor": valor_especialidade,
            })
            qtd_grupo += qtd_especialidade
            valor_grupo += valor_especialidade
        grupos_prontos.append({
            "nome": grupo["nome"],
            "eh_particular": grupo["eh_particular"],
            "especialidades": especialidades_prontas,
            "qtd": qtd_grupo,
            "valor": valor_grupo,
            "retorno_qtd": sum(i["qtd"] for e in especialidades_prontas for i in e["itens"] if i["procedimento"] == "Retorno"),
        })

    grupos_prontos.sort(key=lambda g: (g["eh_particular"], g["nome"]))
    total_geral = sum((g["valor"] for g in grupos_prontos), Decimal("0.00"))
    return grupos_prontos, total_geral


@permission_required("accounts.financeiro_ver_producao_convenio", raise_exception=True)
def producao_por_convenio(request):
    """Controle interno: quanto a clínica produziu por convênio, por especialidade e
    procedimento, com valores — ver _producao_por_convenio."""
    periodo = _resolver_mes(request)
    convenio_id = request.GET.get("convenio") or ""
    convenio_selecionado = get_object_or_404(Convenio, pk=convenio_id) if convenio_id else None

    grupos, total_geral = _producao_por_convenio(periodo["data_inicio"], periodo["data_fim"], convenio=convenio_selecionado)
    total_convenio = sum((g["valor"] for g in grupos if not g["eh_particular"]), Decimal("0.00"))
    total_atendimentos = sum(g["qtd"] for g in grupos)
    convenios_no_periodo = sum(1 for g in grupos if not g["eh_particular"])
    total_retorno_qtd = sum(g["retorno_qtd"] for g in grupos)

    contexto = {
        "grupos": grupos,
        "total_geral": total_geral,
        "total_convenio": total_convenio,
        "total_atendimentos": total_atendimentos,
        "convenios_no_periodo": convenios_no_periodo,
        "total_retorno_qtd": total_retorno_qtd,
        "convenios": Convenio.objects.filter(ativo=True).order_by("nome"),
        "convenio_selecionado": convenio_id,
    }
    contexto.update(periodo)
    return render(request, "financeiro/producao_por_convenio.html", contexto)


# =====================================================================
# Faturamento em lote (convênio) — produção x envio x retorno
# Ver claude/faturamento-lote-convenio.md.
# =====================================================================

@permission_required("accounts.faturamento_lote_visualizar", raise_exception=True)
def lote_lista(request):
    """Lista de lotes lançados, com os totais já calculados (produzido/pago/glosado, ver
    LoteFaturamento) — filtrável por convênio e situação."""
    convenio_id = request.GET.get("convenio") or ""
    status = request.GET.get("status") or ""

    lotes = LoteFaturamento.objects.select_related("convenio").order_by("-data_envio", "-criado_em")
    if convenio_id:
        lotes = lotes.filter(convenio_id=convenio_id)
    if status:
        lotes = lotes.filter(status=status)

    return render(
        request,
        "financeiro/lote_lista.html",
        {
            "pagina": paginar(request, lotes),
            "convenios": Convenio.objects.filter(ativo=True).order_by("nome"),
            "convenio_selecionado": convenio_id,
            "status_selecionado": status,
            "status_choices": LoteFaturamento.Status.choices,
        },
    )


@permission_required("accounts.faturamento_lote_lancar", raise_exception=True)
def lote_novo(request):
    """
    Montar um lote novo — fluxo em 2 passos na mesma tela (ver financeiro/lote_form.html):
    1) escolhe o convênio (GET ?convenio=<id>), que carrega a lista de atendimentos elegíveis
       (ver LoteFaturamento.agendamentos_elegiveis); 2) preenche data/número/valor enviado e
       marca os atendimentos a incluir, então envia por POST. O convênio fica travado (hidden,
       ver LoteFaturamentoForm) assim que escolhido, pra não desalinhar da lista já carregada.
    """
    convenio_id = request.POST.get("convenio") if request.method == "POST" else request.GET.get("convenio")
    convenio = get_object_or_404(Convenio, pk=convenio_id) if convenio_id else None

    form = None
    agendamentos_elegiveis = []
    if convenio:
        agendamentos_elegiveis = LoteFaturamento.agendamentos_elegiveis(convenio)
        ids_validos = {a.pk for a in agendamentos_elegiveis}
        if request.method == "POST":
            form = LoteFaturamentoForm(request.POST, convenio_travado=convenio)
            selecionados = [int(v) for v in request.POST.getlist("agendamentos") if v.isdigit() and int(v) in ids_validos]
            if form.is_valid():
                if not selecionados:
                    form.add_error(None, "Selecione ao menos um atendimento elegível para incluir no lote.")
                else:
                    lote = form.save(commit=False)
                    lote.criado_por = request.user
                    lote.save()
                    lote.agendamentos.set(selecionados)
                    messages.success(
                        request,
                        f"Lote criado para '{convenio.nome}' com {len(selecionados)} atendimento(s) — "
                        f"total enviado R$ {formatar_moeda(lote.valor_total_enviado)}.",
                    )
                    return redirect("financeiro:lote_detalhe", pk=lote.pk)
        else:
            form = LoteFaturamentoForm(convenio_travado=convenio)

    return render(
        request,
        "financeiro/lote_form.html",
        {
            "form": form,
            "convenio": convenio,
            "convenios": Convenio.objects.filter(ativo=True).order_by("nome"),
            "agendamentos_elegiveis": agendamentos_elegiveis,
            "ja_incluidos_ids": set(),
            "lote": None,
        },
    )


@permission_required("accounts.faturamento_lote_lancar", raise_exception=True)
def lote_editar(request, pk):
    """
    Edita cabeçalho + atendimentos de um lote já existente — só permitido enquanto NENHUM
    retorno foi lançado ainda (status ENVIADO), pra não desincluir/incluir atendimento depois
    que o convênio já começou a responder (ver LoteFaturamento.atualizar_status).
    """
    lote = get_object_or_404(LoteFaturamento, pk=pk)
    if lote.status != LoteFaturamento.Status.ENVIADO:
        messages.error(
            request,
            "Não é possível editar um lote que já tem retorno lançado — só lotes ainda "
            "'Enviado (aguardando retorno)' podem ser editados.",
        )
        return redirect("financeiro:lote_detalhe", pk=lote.pk)

    ja_incluidos = list(
        lote.agendamentos.select_related("paciente", "profissional")
        .prefetch_related("itens__procedimento")
        .order_by("data_hora_inicio")
    )
    ja_incluidos_ids = {a.pk for a in ja_incluidos}
    elegiveis = LoteFaturamento.agendamentos_elegiveis(lote.convenio, excluir_lote_pk=lote.pk)
    # Une elegíveis (livres de outro lote) + os que já estão NESTE lote (senão sumiriam da
    # lista só por já estarem aqui, já que 'agendamentos_elegiveis' com excluir_lote_pk=lote.pk
    # os trata como livres, mas a query de elegibilidade em si não os traria de volta se algum
    # outro campo mudou nesse meio tempo) — sem duplicar.
    ids_vistos = {a.pk for a in elegiveis}
    opcoes = elegiveis + [a for a in ja_incluidos if a.pk not in ids_vistos]
    opcoes.sort(key=lambda a: a.data_hora_inicio)
    ids_validos = {a.pk for a in opcoes}

    if request.method == "POST":
        form = LoteFaturamentoForm(request.POST, instance=lote, convenio_travado=lote.convenio)
        selecionados = [int(v) for v in request.POST.getlist("agendamentos") if v.isdigit() and int(v) in ids_validos]
        if form.is_valid():
            if not selecionados:
                form.add_error(None, "Selecione ao menos um atendimento para o lote.")
            else:
                form.save()
                lote.agendamentos.set(selecionados)
                messages.success(request, "Lote atualizado.")
                return redirect("financeiro:lote_detalhe", pk=lote.pk)
    else:
        form = LoteFaturamentoForm(instance=lote, convenio_travado=lote.convenio)

    return render(
        request,
        "financeiro/lote_form.html",
        {
            "form": form,
            "convenio": lote.convenio,
            "agendamentos_elegiveis": opcoes,
            "ja_incluidos_ids": ja_incluidos_ids,
            "lote": lote,
        },
    )


@permission_required("accounts.faturamento_lote_lancar", raise_exception=True)
def lote_excluir(request, pk):
    """
    Exclui um lote — só permitido enquanto NENHUM retorno foi lançado ainda (status ENVIADO),
    mesma trava de lote_editar: um retorno já lançado marcou convenio_recebido no(s)
    Agendamento(s) correspondente(s) (ver RetornoLoteItem.save()), e excluir o lote não
    desfaria isso — ficaria um atendimento "recebido" sem nenhum lote/retorno que explique
    por quê. Os atendimentos incluídos só perdem o vínculo (M2M) e voltam a ficar elegíveis
    para entrar em outro lote.
    """
    lote = get_object_or_404(LoteFaturamento.objects.select_related("convenio"), pk=pk)
    if lote.status != LoteFaturamento.Status.ENVIADO:
        messages.error(
            request,
            "Não é possível excluir um lote que já tem retorno lançado — só lotes ainda "
            "'Enviado (aguardando retorno)' podem ser excluídos.",
        )
        return redirect("financeiro:lote_detalhe", pk=lote.pk)

    if request.method == "POST":
        descricao = (
            f"Excluiu lote {lote.identificacao} — {lote.convenio.nome}, "
            f"{lote.quantidade_itens} atendimento(s), R$ {formatar_moeda(lote.valor_total_enviado)}"
        )
        lote.delete()
        LogAuditoria.registrar(request, request.user, descricao)
        messages.success(request, "Lote excluído.")
        return redirect("financeiro:lote_lista")

    return render(request, "financeiro/lote_excluir.html", {"lote": lote})


@permission_required("accounts.faturamento_lote_visualizar", raise_exception=True)
def lote_detalhe(request, pk):
    """Detalhe de um lote: totais (produzido/enviado/pago/glosado) e, por atendimento
    incluído, o retorno já lançado (ou o link pra lançar, ver retorno_lancar)."""
    lote = get_object_or_404(LoteFaturamento.objects.select_related("convenio"), pk=pk)
    agendamentos = (
        lote.agendamentos.select_related("paciente", "profissional")
        .prefetch_related("itens__procedimento")
        .order_by("data_hora_inicio")
    )
    retornos_por_agendamento = {r.agendamento_id: r for r in lote.retornos.all()}
    linhas = [{"agendamento": a, "retorno": retornos_por_agendamento.get(a.pk)} for a in agendamentos]
    return render(request, "financeiro/lote_detalhe.html", {"lote": lote, "linhas": linhas})


@permission_required("accounts.faturamento_retorno_lancar", raise_exception=True)
def retorno_lancar(request, lote_pk, agendamento_pk):
    """
    Lança (ou corrige, se já existir — ver Meta.constraints de RetornoLoteItem) o retorno do
    convênio para UM atendimento do lote. Salvar com valor_pago>0 marca automaticamente
    convenio_recebido no Agendamento, e o status do lote é recalculado sozinho — ver
    RetornoLoteItem.save()/LoteFaturamento.atualizar_status.
    """
    lote = get_object_or_404(LoteFaturamento, pk=lote_pk)
    agendamento = get_object_or_404(Agendamento, pk=agendamento_pk, lotes_faturamento=lote)
    item = RetornoLoteItem.objects.filter(lote=lote, agendamento=agendamento).first()
    if item is None:
        item = RetornoLoteItem(lote=lote, agendamento=agendamento, data_retorno=timezone.localdate())

    if request.method == "POST":
        form = RetornoLoteItemForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, f"Retorno lançado para '{agendamento.paciente.nome_completo}'.")
            return redirect("financeiro:lote_detalhe", pk=lote.pk)
    else:
        form = RetornoLoteItemForm(instance=item)

    return render(
        request,
        "financeiro/retorno_lancar.html",
        {"form": form, "lote": lote, "agendamento": agendamento, "produzido_num": _produzido_num(agendamento)},
    )


@permission_required("accounts.faturamento_retorno_lancar", raise_exception=True)
def lote_dar_baixa(request, pk):
    """
    Dá baixa no lote inteiro numa tela só — um RetornoLoteItemForm por atendimento do lote,
    prefixado por agendamento (mesmo padrão de contas_receber_lancar_valor: lista de
    ModelForms com prefixo, não formset), em vez de precisar abrir retorno_lancar atendimento
    por atendimento (pedido do cliente — só existia a opção por item). Reaproveita o retorno já
    lançado como instância inicial quando existir, então também serve pra corrigir vários de
    uma vez.

    Cada linha nova nasce zerada (0,00/0,00, mesmo default de retorno_lancar — nunca presume
    'pago integralmente' sozinho, pra não marcar como recebido algo que não foi de fato
    conferido) com a data de hoje. No POST, uma linha só é gravada se: já existia (o usuário
    está corrigindo algo já lançado, grava do jeito que veio) OU tem valor_pago ou
    valor_glosado diferente de zero (o usuário de fato informou algo pra ela) — uma linha nova
    deixada 0,00/0,00 é ignorada, o atendimento continua 'Aguardando retorno' pra lançar depois,
    permitindo dar baixa só numa parte do lote nesta passada (mesmo espírito do status
    RETORNO_PARCIAL, ver LoteFaturamento.atualizar_status).
    """
    lote = get_object_or_404(LoteFaturamento.objects.select_related("convenio"), pk=pk)
    agendamentos = list(
        lote.agendamentos.select_related("paciente", "profissional")
        .prefetch_related("itens__procedimento")
        .order_by("data_hora_inicio")
    )
    if not agendamentos:
        messages.error(request, "Este lote não tem atendimentos incluídos.")
        return redirect("financeiro:lote_detalhe", pk=lote.pk)

    retornos_existentes = {r.agendamento_id: r for r in lote.retornos.all()}

    def _instancia(agendamento):
        return retornos_existentes.get(agendamento.pk) or RetornoLoteItem(
            lote=lote, agendamento=agendamento, data_retorno=timezone.localdate()
        )

    if request.method == "POST":
        linhas = [
            {
                "agendamento": agendamento,
                "ja_existia": agendamento.pk in retornos_existentes,
                "produzido_num": _produzido_num(agendamento),
                "form": RetornoLoteItemForm(
                    request.POST, instance=_instancia(agendamento), prefix=f"ag{agendamento.pk}"
                ),
            }
            for agendamento in agendamentos
        ]
        if all(linha["form"].is_valid() for linha in linhas):
            qtd = 0
            for linha in linhas:
                form = linha["form"]
                tem_valor = form.cleaned_data["valor_pago"] or form.cleaned_data["valor_glosado"]
                if linha["ja_existia"] or tem_valor:
                    form.save()
                    qtd += 1
            if qtd:
                messages.success(request, f"Retorno lançado para {qtd} atendimento(s) do lote {lote.identificacao}.")
            else:
                messages.warning(request, "Nenhum valor informado — nada foi lançado.")
            return redirect("financeiro:lote_detalhe", pk=lote.pk)
    else:
        linhas = [
            {
                "agendamento": agendamento,
                "produzido_num": _produzido_num(agendamento),
                "form": RetornoLoteItemForm(instance=_instancia(agendamento), prefix=f"ag{agendamento.pk}"),
            }
            for agendamento in agendamentos
        ]

    return render(request, "financeiro/lote_dar_baixa.html", {"lote": lote, "linhas": linhas})


def _comparativo_faturamento_lote(data_inicio, data_fim, convenio=None):
    """
    Produção x envio x retorno por convênio (ver claude/faturamento-lote-convenio.md).
    'Produzido' reaproveita o mesmo filtro já usado em _producao_por_convenio (Agendamento
    Realizado/Convênio com valor_convenio lançado — retorno grátis nunca entra aqui porque
    nunca tem valor_convenio preenchido, ver agenda.views.agendamento_mudar_status). Cada
    métrica é filtrada pela data do PRÓPRIO evento (atendimento / envio do lote / retorno do
    convênio) — não pela data do atendimento original —, mesmo espírito de
    _estornos_do_periodo (o que importa é "quando aconteceu", não "de quando é a origem").

    'Pendente de lote' é o produzido do período que ainda não entrou em NENHUM
    LoteFaturamento (Agendamento.lotes_faturamento vazio) — uma vez incluído num lote, some
    daqui mesmo que o lote ainda esteja só 'Enviado' sem retorno nenhum.

    Retorna (linhas, totais); linhas é uma lista de dicts {convenio, produzido, enviado, pago,
    glosado, pendente_lote} ordenada por nome do convênio — só entram convênios com alguma
    movimentação (produção, envio ou retorno) no período.
    """
    produzido_qs = Agendamento.objects.filter(
        Agendamento.filtro_convenio_a_faturar(),
        status__in=Agendamento.STATUS_PRODUCAO,
        data_hora_inicio__date__range=(data_inicio, data_fim),
        valor_convenio__isnull=False,
    )
    lotes_qs = LoteFaturamento.objects.filter(data_envio__range=(data_inicio, data_fim))
    retornos_qs = RetornoLoteItem.objects.filter(data_retorno__range=(data_inicio, data_fim))
    if convenio is not None:
        produzido_qs = produzido_qs.filter(convenio=convenio)
        lotes_qs = lotes_qs.filter(convenio=convenio)
        retornos_qs = retornos_qs.filter(lote__convenio=convenio)

    convenio_ids = set(produzido_qs.values_list("convenio_id", flat=True))
    convenio_ids |= set(lotes_qs.values_list("convenio_id", flat=True))
    convenio_ids |= set(retornos_qs.values_list("lote__convenio_id", flat=True))
    convenio_ids.discard(None)
    convenios_por_id = {c.pk: c for c in Convenio.objects.filter(pk__in=convenio_ids)}

    linhas = []
    for convenio_id in convenio_ids:
        conv = convenios_por_id.get(convenio_id)
        if conv is None:
            continue
        produzido = produzido_qs.filter(convenio_id=convenio_id).aggregate(total=Sum("valor_convenio"))["total"] or Decimal("0.00")
        enviado = lotes_qs.filter(convenio_id=convenio_id).aggregate(total=Sum("valor_total_enviado"))["total"] or Decimal("0.00")
        pago = retornos_qs.filter(lote__convenio_id=convenio_id).aggregate(total=Sum("valor_pago"))["total"] or Decimal("0.00")
        glosado = retornos_qs.filter(lote__convenio_id=convenio_id).aggregate(total=Sum("valor_glosado"))["total"] or Decimal("0.00")
        pendente_lote = (
            produzido_qs.filter(convenio_id=convenio_id, lotes_faturamento__isnull=True).aggregate(total=Sum("valor_convenio"))["total"]
            or Decimal("0.00")
        )
        linhas.append(
            {
                "convenio": conv,
                "produzido": produzido,
                "enviado": enviado,
                "pago": pago,
                "glosado": glosado,
                "pendente_lote": pendente_lote,
            }
        )

    linhas.sort(key=lambda l: l["convenio"].nome)
    totais = {
        campo: sum((linha[campo] for linha in linhas), Decimal("0.00"))
        for campo in ("produzido", "enviado", "pago", "glosado", "pendente_lote")
    }
    return linhas, totais


@permission_required("accounts.faturamento_comparativo_visualizar", raise_exception=True)
def faturamento_comparativo(request):
    """Produção x envio x retorno por convênio, por mês — ver _comparativo_faturamento_lote."""
    periodo = _resolver_mes(request)
    convenio_id = request.GET.get("convenio") or ""
    convenio_selecionado = get_object_or_404(Convenio, pk=convenio_id) if convenio_id else None

    linhas, totais = _comparativo_faturamento_lote(
        periodo["data_inicio"], periodo["data_fim"], convenio=convenio_selecionado
    )

    contexto = {
        "linhas": linhas,
        "totais": totais,
        "convenios": Convenio.objects.filter(ativo=True).order_by("nome"),
        "convenio_selecionado": convenio_id,
    }
    contexto.update(periodo)
    return render(request, "financeiro/faturamento_comparativo.html", contexto)


# =====================================================================
# Movimentação de caixa avulsa (entradas/saídas fora de atendimento)
# Ver financeiro.models.MovimentacaoCaixa.
# =====================================================================

@permission_required("accounts.financeiro_ver_movimentacoes_avulsas", raise_exception=True)
def movimentacao_avulsa_lista(request):
    """Lista semanal de entradas/saídas avulsas — mesma navegação por semana (?data=) de
    painel_financeiro/agenda.views.relatorio_repasses_semanal, pra bater exatamente com o
    período mostrado no card "Caixa da semana" do Painel financeiro."""
    hoje = timezone.localdate()
    data_referencia = parse_date(request.GET.get("data") or "") or hoje
    intervalo_inicio, intervalo_fim = _semana_de(data_referencia)
    itens, total_entradas, total_saidas = _movimentacoes_avulsas_do_periodo(intervalo_inicio, intervalo_fim)

    return render(
        request,
        "financeiro/movimentacao_avulsa_lista.html",
        {
            "pagina": paginar(request, itens),
            "total_entradas": total_entradas,
            "total_saidas": total_saidas,
            "saldo": total_entradas - total_saidas,
            "data_referencia": data_referencia,
            "intervalo_inicio": intervalo_inicio,
            "intervalo_fim": intervalo_fim,
            "semana_anterior": intervalo_inicio - timedelta(days=7),
            "semana_seguinte": intervalo_inicio + timedelta(days=7),
        },
    )


@permission_required("accounts.financeiro_lancar", raise_exception=True)
def movimentacao_avulsa_nova(request):
    """Lança uma entrada/saída avulsa nova — ver financeiro.models.MovimentacaoCaixa. Sem
    vínculo automático com nenhuma tela de repasse (decisão do cliente): quem pagou um
    repasse em dinheiro registra aqui à parte, só o item e o valor."""
    if request.method == "POST":
        form = MovimentacaoCaixaForm(request.POST)
        if form.is_valid():
            movimentacao = form.save(commit=False)
            movimentacao.criado_por = request.user
            movimentacao.save()
            LogAuditoria.registrar(
                request,
                request.user,
                f"Lançou {movimentacao.get_tipo_display().lower()} avulsa de R$ {formatar_moeda(movimentacao.valor)} — {movimentacao.descricao}",
            )
            messages.success(request, f"{movimentacao.get_tipo_display()} avulsa de R$ {formatar_moeda(movimentacao.valor)} lançada.")
            return redirect(f"{reverse('financeiro:movimentacao_avulsa_lista')}?data={movimentacao.data.isoformat()}")
    else:
        form = MovimentacaoCaixaForm(initial={"data": timezone.localdate()})

    return render(request, "financeiro/movimentacao_avulsa_form.html", {"form": form, "movimentacao": None})


def _bloquear_avulsa_de_repasse(request, movimentacao):
    """Saída gerada automaticamente por um pagamento de repasse (ver RepassePagamento.movimentacao)
    não é editada/excluída por aqui — senão o caixa e o abatimento do repasse ficariam divergentes.
    Corrige-se excluindo o pagamento de repasse, que desfaz os dois juntos."""
    if RepassePagamento.objects.filter(movimentacao=movimentacao).exists():
        messages.error(
            request,
            "Esta saída foi gerada por um pagamento de repasse. Para corrigir, exclua o pagamento em "
            "Financeiro → Pagamentos de repasse — a saída de caixa é desfeita junto.",
        )
        return True
    return False


@permission_required("accounts.financeiro_lancar", raise_exception=True)
def movimentacao_avulsa_editar(request, pk):
    """Corrige uma movimentação avulsa já lançada — edição direta, sem fluxo de estorno com
    motivo (decisão do cliente: manter isto leve, diferente do resto do financeiro)."""
    movimentacao = get_object_or_404(MovimentacaoCaixa, pk=pk)
    if _bloquear_avulsa_de_repasse(request, movimentacao):
        return redirect(f"{reverse('financeiro:movimentacao_avulsa_lista')}?data={movimentacao.data.isoformat()}")
    if request.method == "POST":
        form = MovimentacaoCaixaForm(request.POST, instance=movimentacao)
        if form.is_valid():
            form.save()
            LogAuditoria.registrar(
                request,
                request.user,
                f"Editou {movimentacao.get_tipo_display().lower()} avulsa de R$ {formatar_moeda(movimentacao.valor)} — {movimentacao.descricao}",
            )
            messages.success(request, "Movimentação avulsa atualizada.")
            return redirect(f"{reverse('financeiro:movimentacao_avulsa_lista')}?data={movimentacao.data.isoformat()}")
    else:
        form = MovimentacaoCaixaForm(instance=movimentacao)

    return render(request, "financeiro/movimentacao_avulsa_form.html", {"form": form, "movimentacao": movimentacao})


@permission_required("accounts.financeiro_lancar", raise_exception=True)
def movimentacao_avulsa_excluir(request, pk):
    """Exclui de vez uma movimentação avulsa lançada por engano — mesmo padrão de
    financeiro.views.lote_excluir (confirmação em tela própria, exclusão de verdade no POST)."""
    movimentacao = get_object_or_404(MovimentacaoCaixa, pk=pk)
    if _bloquear_avulsa_de_repasse(request, movimentacao):
        return redirect(f"{reverse('financeiro:movimentacao_avulsa_lista')}?data={movimentacao.data.isoformat()}")
    if request.method == "POST":
        data_da_movimentacao = movimentacao.data
        descricao_log = (
            f"Excluiu {movimentacao.get_tipo_display().lower()} avulsa de R$ {formatar_moeda(movimentacao.valor)} — {movimentacao.descricao}"
        )
        movimentacao.delete()
        LogAuditoria.registrar(request, request.user, descricao_log)
        messages.success(request, "Movimentação avulsa excluída.")
        return redirect(f"{reverse('financeiro:movimentacao_avulsa_lista')}?data={data_da_movimentacao.isoformat()}")

    return render(request, "financeiro/movimentacao_avulsa_excluir.html", {"movimentacao": movimentacao})


# =====================================================================
# Pagamento de repasse particular (total ou parcial) — ver
# financeiro.models.RepassePagamento.
# =====================================================================

def _pode_registrar_repasse(usuario):
    """Quem registra/desfaz pagamento de repasse particular: quem concilia no financeiro OU o
    próprio proprietário da clínica (pedido da clínica, 2026-10-16 — é ele quem paga)."""
    return usuario.has_perm("accounts.financeiro_conciliar") or usuario.eh_proprietario


@login_required
def repasse_pagamento_lista(request):
    """Saldo de repasse particular pendente por profissional (todas as semanas somadas) +
    histórico dos pagamentos registrados, com opção de desfazer um lançamento errado.

    O proprietário não aparece nos saldos: a produção dele é da própria clínica, não há repasse
    a pagar a ele (mesma regra de _repasse_convenio_por_profissional)."""
    if not (request.user.has_perm("accounts.financeiro_ver_repasses_semanais") or request.user.eh_proprietario):
        raise PermissionDenied
    pendentes = (
        PagamentoParticular.objects.filter(
            # Particular + convênio "funciona como particular" (23/10/2026).
            Agendamento.filtro_pago_como_particular("agendamento__"),
            agendamento__status__in=Agendamento.STATUS_PRODUCAO,
            repasse_pago=False,
            estornado=False,
        )
        .exclude(forma_pagamento=Agendamento.FormaPagamento.CORTESIA)
        .exclude(agendamento__profissional__eh_proprietario=True)
        .select_related("agendamento__profissional")
    )
    saldos = {}
    for pagamento in pendentes:
        profissional = pagamento.agendamento.profissional
        item = saldos.setdefault(
            profissional.pk,
            {"profissional": profissional, "saldo": Decimal("0.00"), "qtd": 0, "mais_antigo": None},
        )
        item["saldo"] += pagamento.saldo_repasse
        item["qtd"] += 1
        data_atendimento = timezone.localtime(pagamento.agendamento.data_hora_inicio).date()
        if item["mais_antigo"] is None or data_atendimento < item["mais_antigo"]:
            item["mais_antigo"] = data_atendimento
    saldos_ordenados = sorted((i for i in saldos.values() if i["saldo"] > 0), key=lambda i: i["profissional"].nome)

    historico = RepassePagamento.objects.select_related("profissional", "criado_por").order_by("-data", "-criado_em")
    profissional_id = request.GET.get("profissional") or ""
    if profissional_id.isdigit():
        historico = historico.filter(profissional_id=profissional_id)

    return render(
        request,
        "financeiro/repasse_pagamento_lista.html",
        {
            "saldos": saldos_ordenados,
            "saldo_total": sum((i["saldo"] for i in saldos_ordenados), Decimal("0.00")),
            "pagina": paginar(request, historico),
            "profissionais": Profissional.objects.filter(repasses_pagos__isnull=False).distinct().order_by("nome"),
            "profissional_selecionado": profissional_id,
            "pode_registrar": _pode_registrar_repasse(request.user),
        },
    )


@login_required
def repasse_pagamento_novo(request, profissional_id):
    """Registra quanto foi pago de repasse a um profissional — pode ser só uma parte do saldo
    (caixa sem dinheiro suficiente, ou o profissional quis levar só uma parte). O valor é abatido
    das linhas mais antigas primeiro (RepassePagamento.alocar); em dinheiro do caixa, lança a
    saída de caixa automaticamente."""
    if not _pode_registrar_repasse(request.user):
        raise PermissionDenied
    profissional = get_object_or_404(Profissional, pk=profissional_id)
    if profissional.eh_proprietario:
        messages.info(request, f"{profissional.nome} é o proprietário — não há repasse a pagar a ele.")
        return redirect("financeiro:repasse_pagamento_lista")
    pendentes = list(RepassePagamento.pendentes_do_profissional(profissional))
    for pagamento in pendentes:
        pagamento.valor_liquido = pagamento.valor_repasse
        pagamento.saldo = pagamento.saldo_repasse
    saldo = sum((p.saldo for p in pendentes), Decimal("0.00"))
    # Vem do campo oculto "voltar_url" num POST desta própria tela (mesmo motivo de
    # pagamento_estornar: o Referer do POST apontaria pra esta mesma tela).
    destino = request.POST.get("voltar_url") or request.META.get("HTTP_REFERER") or reverse(
        "financeiro:repasse_pagamento_lista"
    )

    if request.method == "POST":
        if saldo <= 0:
            messages.error(request, f"{profissional.nome} não tem repasse particular pendente.")
            return redirect(destino)
        form = RepassePagamentoForm(request.POST, saldo=saldo)
        if form.is_valid():
            with transaction.atomic():
                repasse = form.save(commit=False)
                repasse.profissional = profissional
                repasse.criado_por = request.user
                if repasse.forma == RepassePagamento.Forma.DINHEIRO:
                    repasse.movimentacao = MovimentacaoCaixa.objects.create(
                        tipo=MovimentacaoCaixa.Tipo.SAIDA,
                        valor=repasse.valor,
                        data=repasse.data,
                        descricao=f"Repasse particular pago em dinheiro — {profissional.nome}",
                        profissional=profissional,
                        criado_por=request.user,
                    )
                repasse.save()
                repasse.alocar()
            restante = saldo - repasse.valor
            LogAuditoria.registrar(
                request,
                request.user,
                f"Registrou pagamento de repasse de R$ {formatar_moeda(repasse.valor)} a {profissional.nome} "
                f"({repasse.get_forma_display()}) — saldo restante R$ {formatar_moeda(restante)}",
            )
            if restante > 0:
                messages.success(
                    request,
                    f"Pagamento de R$ {formatar_moeda(repasse.valor)} a {profissional.nome} registrado. "
                    f"Ainda falta repassar R$ {formatar_moeda(restante)}.",
                )
            else:
                messages.success(
                    request, f"Pagamento de R$ {formatar_moeda(repasse.valor)} a {profissional.nome} registrado — repasse quitado."
                )
            return redirect(destino)
    else:
        form = RepassePagamentoForm(saldo=saldo, initial={"data": timezone.localdate(), "valor": saldo or None})

    return render(
        request,
        "financeiro/repasse_pagamento_form.html",
        {
            "form": form,
            "profissional": profissional,
            "pendentes": pendentes,
            "saldo": saldo,
            "voltar_url": destino,
        },
    )


@login_required
def repasse_pagamento_excluir(request, pk):
    """Desfaz um pagamento de repasse lançado por engano: devolve o saldo às linhas abatidas e
    exclui a saída de caixa gerada (se foi em dinheiro)."""
    if not _pode_registrar_repasse(request.user):
        raise PermissionDenied
    repasse = get_object_or_404(RepassePagamento.objects.select_related("profissional", "movimentacao"), pk=pk)
    if request.method == "POST":
        descricao_log = (
            f"Excluiu pagamento de repasse de R$ {formatar_moeda(repasse.valor)} a {repasse.profissional.nome} "
            f"({repasse.get_forma_display()}, {repasse.data:%d/%m/%Y})"
        )
        with transaction.atomic():
            repasse.desfazer_alocacoes()
            movimentacao = repasse.movimentacao
            repasse.delete()
            if movimentacao is not None:
                movimentacao.delete()
        LogAuditoria.registrar(request, request.user, descricao_log)
        messages.success(request, "Pagamento de repasse excluído — o valor voltou a ficar pendente.")
        return redirect("financeiro:repasse_pagamento_lista")

    return render(
        request,
        "financeiro/repasse_pagamento_excluir.html",
        {"repasse": repasse, "alocacoes": repasse.alocacoes.select_related("pagamento__agendamento__paciente")},
    )
