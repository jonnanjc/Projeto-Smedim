import calendar
import io
import uuid
from urllib.parse import urlencode, urlsplit
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.dateparse import parse_date, parse_time
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from cadastros.busca_pacientes import buscar_ids_pacientes
from cadastros.models import (
    AtendimentoEspecialidade,
    AtendimentoGuiche,
    AtendimentoProfissional,
    AtendimentoSala,
    Convenio,
    EspecialidadePadraoUsuario,
    HorarioAtendimento,
    LimiteDiarioProcedimento,
    Pacote,
    Paciente,
    Procedimento,
    Profissional,
    Sala,
    especialidade_a_partir_da_chave,
    especialidade_rotulo,
)

from accounts.models import LogAuditoria
from accounts.paginacao import paginar
from financeiro.models import RepassePagamento
from prontuario.models import EntradaProntuario, eh_equipe_multi

from .forms import (
    AgendamentoForm,
    AgendamentoItemFormSet,
    ConfirmarPagamentoForm,
    EspecialidadesDoDiaForm,
    GuicheDoDiaForm,
    SalaDoDiaForm,
)
from .forms import AutorizacaoConvenioForm, GuiaConvenioForm, GuiaSessaoForm, PacoteManualForm
from .models import (
    Agendamento,
    AgendamentoItem,
    GuiaConvenio,
    GuiaExtraAutorizada,
    PacoteVendido,
    PacoteVendidoItem,
    PagamentoParticular,
    vincular_pacotes,
    vincular_pacotes_futuros,
)
from accounts.moeda import formatar_moeda

try:
    import openpyxl
except ImportError:  # biblioteca instalada via requirements.txt — ver README
    openpyxl = None


def _url_agenda_dia(agendamento):
    return reverse("agenda:agenda_dia") + f"?data={timezone.localtime(agendamento.data_hora_inicio).date().isoformat()}"


def _pode_ver_agenda_dia(user):
    """Mesma regra de acesso de agenda_dia — fonte única pra quem decide se pode mandar o usuário pra lá."""
    return (
        user.has_perm("accounts.agenda_ver_dia")
        or user.has_perm("accounts.agenda_criar")
        or user.has_perm("accounts.agenda_editar")
    )


def _url_depois_do_pagamento(request, agendamento):
    """Pra onde agendamento_confirmar_pagamento volta. O perfil Financeiro padrão não
    enxerga a Agenda do dia (ver seed_perfis) — mandá-lo pra lá dava 403 logo depois de
    um pagamento registrado com sucesso; volta então pra Fila de pagamento, de onde ele veio."""
    if _pode_ver_agenda_dia(request.user):
        return _url_agenda_dia(agendamento)
    return reverse("financeiro:fila_pagamento_lista")


# --- Grade de horários (visões Dia/Semana, estilo Google Agenda) -----------

GRADE_HORA_INICIO = 7  # 07:00
GRADE_HORA_FIM = 20  # 20:00 — grade cobre das 7h às 20h
PIXELS_POR_HORA = 56


def _horas_da_grade():
    return list(range(GRADE_HORA_INICIO, GRADE_HORA_FIM))


def _altura_total_grade():
    return (GRADE_HORA_FIM - GRADE_HORA_INICIO) * PIXELS_POR_HORA


def _bloco_tempo(agendamento):
    """Calcula a posição (topo/altura em px) do 'cartão' de um agendamento
    dentro da grade de horários, a partir do horário real dele."""
    inicio_local = timezone.localtime(agendamento.data_hora_inicio)
    fim_local = timezone.localtime(agendamento.data_hora_fim)
    inicio_min = inicio_local.hour * 60 + inicio_local.minute
    fim_min = fim_local.hour * 60 + fim_local.minute
    if fim_min <= inicio_min:
        # Consulta/exame não têm hora marcada (só sessão tem — ver Agendamento.eh_sessao):
        # o card da lista do turno (ver _blocos_da_grade) ocupa 1h só pra ser visível.
        fim_min = inicio_min + 60

    limite_inicio = GRADE_HORA_INICIO * 60
    limite_fim = GRADE_HORA_FIM * 60
    inicio_visivel = min(max(inicio_min, limite_inicio), limite_fim)
    fim_visivel = min(max(fim_min, limite_inicio), limite_fim)
    if fim_visivel <= inicio_visivel:
        fim_visivel = min(inicio_visivel + 20, limite_fim)

    top_px = (inicio_visivel - limite_inicio) * PIXELS_POR_HORA / 60
    altura_px = max(22, (fim_visivel - inicio_visivel) * PIXELS_POR_HORA / 60)

    return {
        "agendamento": agendamento,
        # Formatados aqui como texto (com "." de decimal) em vez de deixar o
        # template imprimir o número puro: como o site está em pt-br, o Django
        # troca o "." por "," ao formatar números no template (ex: exibiria
        # "280,0" em vez de "280.0"), o que quebra o CSS (o navegador não
        # entende "top:280,0px" e ignora a posição, jogando o card lá pro
        # topo). Formatando como string aqui, o valor já sai pronto e nunca
        # passa pela localização de números do template.
        "top": f"{top_px:.1f}",
        "altura": f"{altura_px:.1f}",
        "inicio_min": inicio_min,
        "fim_min": fim_min,
        "coluna": 0,
        "total_colunas": 1,
        # Texto de horário calculado a partir do MESMO horário local usado pra
        # posicionar o card (inicio_local/fim_local acima) — em vez de deixar
        # o template formatar `agendamento.data_hora_inicio` de novo por conta
        # própria. Evita qualquer chance de o texto mostrado (ex: "08:00") e a
        # posição vertical do card ficarem calculados a partir de bases
        # diferentes e "descolarem" um do outro.
        "hora_inicio_texto": inicio_local.strftime("%H:%M"),
        "hora_fim_texto": fim_local.strftime("%H:%M") if agendamento.tem_horario_fim else "",
    }


def _blocos_da_grade(agendamentos):
    """Cards da grade: um por sessão (horário marcado) e UM por lista de turno — consulta/
    exame do mesmo profissional no mesmo turno viram um card só ("Manhã · 20 pacientes"),
    em vez de 20 cards empilhados no mesmo horário (ver Agendamento.sem_hora_marcada)."""
    blocos = []
    listas = {}
    for agendamento in agendamentos:
        if not agendamento.sem_hora_marcada:
            blocos.append(_bloco_tempo(agendamento))
            continue
        chave = (agendamento.profissional_id, agendamento.data_hora_inicio)
        if chave not in listas:
            bloco = _bloco_tempo(agendamento)
            bloco["lista"] = []
            listas[chave] = bloco
            blocos.append(bloco)
        listas[chave]["lista"].append(agendamento)
    for bloco in listas.values():
        bloco["data_iso"] = timezone.localtime(bloco["agendamento"].data_hora_inicio).date().isoformat()
        bloco["nomes"] = ", ".join(
            f"{n}º {a.paciente.primeiro_nome}" for n, a in enumerate(bloco["lista"], start=1)
        )
    return blocos


def _distribuir_colunas(blocos):
    """
    Distribui blocos que se sobrepõem no tempo lado a lado (mesma lógica que
    o Google Agenda usa quando há mais de um compromisso no mesmo horário),
    usando um algoritmo guloso clássico de alocação de colunas por intervalo.
    """
    blocos_ordenados = sorted(blocos, key=lambda b: b["inicio_min"])
    colunas_fim = []
    grupo_atual = []
    grupo_fim_max = None
    grupos = []

    for bloco in blocos_ordenados:
        if grupo_fim_max is not None and bloco["inicio_min"] >= grupo_fim_max:
            grupos.append(grupo_atual)
            grupo_atual = []
            colunas_fim = []
            grupo_fim_max = None

        colocado = False
        for i, fim_coluna in enumerate(colunas_fim):
            if bloco["inicio_min"] >= fim_coluna:
                colunas_fim[i] = bloco["fim_min"]
                bloco["coluna"] = i
                colocado = True
                break
        if not colocado:
            bloco["coluna"] = len(colunas_fim)
            colunas_fim.append(bloco["fim_min"])

        grupo_atual.append(bloco)
        grupo_fim_max = max(grupo_fim_max or 0, bloco["fim_min"])

    if grupo_atual:
        grupos.append(grupo_atual)

    for grupo in grupos:
        total = max(b["coluna"] for b in grupo) + 1
        for b in grupo:
            b["total_colunas"] = total

    for bloco in blocos_ordenados:
        largura = 100 / bloco["total_colunas"]
        # Mesmo motivo do "top"/"altura" em _bloco_tempo: formata como texto
        # com "." pra não correr o risco do template trocar por "," (pt-br) e
        # quebrar o CSS.
        bloco["largura"] = f"{largura:.2f}"
        bloco["esquerda"] = f"{bloco['coluna'] * largura:.2f}"

    return blocos_ordenados


def _monta_grade_dia(data, profissionais_ids):
    """
    Visão Dia: uma coluna por profissional (como colunas de agenda por
    'calendário' no Google Agenda) — evita sobrepor visualmente compromissos
    de profissionais diferentes no mesmo horário.

    `profissionais_ids`: None = sem restrição (mostra quem tem agendamento no
    dia); lista de ids = mostra só esses profissionais, com ou sem agendamento
    (usado tanto pelo filtro de Profissional quanto pelas Especialidades de
    hoje — ver agenda_dia).
    """
    todos = list(
        Agendamento.objects.filter(data_hora_inicio__date=data)
        .exclude(status=Agendamento.Status.CANCELADO)
        .select_related("paciente", "profissional", "convenio")
        .order_by("data_hora_inicio", "pk")
    )
    if profissionais_ids is not None:
        ids_str = {str(pid) for pid in profissionais_ids}
        todos = [a for a in todos if str(a.profissional_id) in ids_str]
        profissionais_dia = list(Profissional.objects.filter(pk__in=profissionais_ids).order_by("nome"))
    else:
        ids_com_agendamento = sorted({a.profissional_id for a in todos})
        profissionais_dia = list(Profissional.objects.filter(pk__in=ids_com_agendamento).order_by("nome"))

    colunas = []
    for profissional in profissionais_dia:
        agendamentos_prof = [a for a in todos if a.profissional_id == profissional.pk]
        blocos = _blocos_da_grade(agendamentos_prof)
        for bloco in blocos:
            bloco["largura"] = "100.0"
            bloco["esquerda"] = "0.0"
        colunas.append({"profissional": profissional, "blocos": blocos})
    return colunas


def _monta_grade_semana(intervalo_inicio, intervalo_fim, profissionais_ids):
    """Visão Semana: uma coluna por dia (domingo a sábado), com sobreposição
    resolvida lado a lado dentro de cada dia.

    `profissionais_ids`: None = sem restrição; lista de ids = só agendamentos
    desses profissionais (ver _monta_grade_dia)."""
    todos = list(
        Agendamento.objects.filter(data_hora_inicio__date__range=(intervalo_inicio, intervalo_fim))
        .exclude(status=Agendamento.Status.CANCELADO)
        .select_related("paciente", "profissional", "convenio")
        .order_by("data_hora_inicio", "pk")
    )
    if profissionais_ids is not None:
        ids_str = {str(pid) for pid in profissionais_ids}
        todos = [a for a in todos if str(a.profissional_id) in ids_str]

    hoje = timezone.localdate()
    dias = []
    dia_atual = intervalo_inicio
    while dia_atual <= intervalo_fim:
        agendamentos_dia = [a for a in todos if timezone.localtime(a.data_hora_inicio).date() == dia_atual]
        blocos = _distribuir_colunas(_blocos_da_grade(agendamentos_dia))
        dias.append({"data": dia_atual, "blocos": blocos, "hoje": dia_atual == hoje})
        dia_atual += timedelta(days=1)
    return dias


def _verificar_limite_diario(agendamento):
    """
    Confere se este agendamento estourou algum limite diário configurado
    (ver cadastros.models.LimiteDiarioProcedimento) pra esse profissional,
    na mesma data. É um AVISO (soft warning) — nunca bloqueia o agendamento
    — sugerindo à recepção considerar outro profissional/dia disponível.
    Devolve o texto do aviso, ou None se não houver limite configurado ou
    ele não tiver sido atingido.

    Um limite pode ser configurado por procedimento, por convênio (para
    limitar o total de atendimentos de um plano que paga pouco, independente
    do procedimento), pelos dois combinados, ou nenhum dos dois (limite geral
    de atendimentos do profissional no dia) — ver o modelo. Quando mais de um
    limite combina com este agendamento, o mais específico (mais campos
    preenchidos) prevalece.

    Um agendamento pode ter vários itens agora (ver AgendamentoItem) — um
    limite POR PROCEDIMENTO soma a 'quantidade' de cada item que bate com ele
    (não conta linhas/agendamentos), já que um item com quantidade=3 do
    procedimento limitado vale 3 pro limite, não 1. Um limite geral (sem
    procedimento) continua contando agendamentos, não itens.
    """
    if not agendamento.profissional_id:
        return None

    # "Todos os ultrassons" (LimiteDiarioProcedimento.todos_ultrassons, pedido da clínica
    # 23/10/2026): soma todo procedimento marcado como USG. Mais específico que o limite geral,
    # menos que o de um procedimento só.
    def alcance(limite):
        if limite.procedimento_id:
            return agendamento.itens.filter(procedimento_id=limite.procedimento_id).exists()
        if limite.todos_ultrassons:
            return agendamento.itens.filter(procedimento__eh_ultrassom=True).exists()
        return True

    candidatos = [
        limite
        for limite in LimiteDiarioProcedimento.objects.filter(
            profissional_id=agendamento.profissional_id
        ).select_related("procedimento", "convenio")
        if alcance(limite) and (limite.convenio_id is None or limite.convenio_id == agendamento.convenio_id)
    ]
    if not candidatos:
        return None

    limite = max(
        candidatos,
        key=lambda l: (2 if l.procedimento_id else 1 if l.todos_ultrassons else 0) + (2 if l.convenio_id else 0),
    )

    dia = timezone.localtime(agendamento.data_hora_inicio).date()
    agendamentos_do_dia = Agendamento.objects.filter(
        profissional_id=agendamento.profissional_id, data_hora_inicio__date=dia
    ).exclude(status=Agendamento.Status.CANCELADO)
    if limite.convenio_id:
        agendamentos_do_dia = agendamentos_do_dia.filter(convenio_id=limite.convenio_id)

    if limite.procedimento_id or limite.todos_ultrassons:
        itens = AgendamentoItem.objects.filter(agendamento__in=agendamentos_do_dia)
        if limite.procedimento_id:
            itens = itens.filter(procedimento_id=limite.procedimento_id)
        else:
            itens = itens.filter(procedimento__eh_ultrassom=True)
        quantidade = itens.aggregate(total=Sum("quantidade"))["total"] or 0
    else:
        quantidade = agendamentos_do_dia.count()

    if quantidade > limite.limite_diario:
        alvo = (
            limite.procedimento.nome if limite.procedimento_id
            else "ultrassons (USG)" if limite.todos_ultrassons
            else "atendimentos"
        )
        if limite.convenio_id:
            alvo += f" do convênio {limite.convenio.nome}"
        return (
            f"Atenção: {agendamento.profissional.nome} já tem {quantidade} {alvo} em {dia:%d/%m/%Y} — "
            f"acima do limite diário configurado ({limite.limite_diario}). Considere agendar com outro "
            "profissional disponível ou em outro dia."
        )
    return None


# --- Central de atendimento (agendamento assistido por especialidade) ------

# Pedido do cliente (20/07/2026): quebra de 15 em 15 min, em vez de 30 — a duração de
# cada atendimento continua livre (ver DURACAO_CHOICES em agenda/forms.py), isto só
# controla de quanto em quanto tempo a grade de disponibilidade mostra uma linha.
SLOT_MINUTOS = 15


def _semana_dias_uteis(data):
    """Segunda a sexta da semana que contém 'data' (a clínica não funciona no fim de
    semana) — mesma semana civil usada em agenda_dia/agenda_propria (visão 'semana').

    Antes a Central mostrava os '6 próximos dias úteis a partir de hoje': quem abria a
    tela numa quarta-feira via quarta-a-quarta da semana seguinte, nunca a semana
    inteira, e o intervalo mudava de cara dependendo do dia em que a recepção olhava.
    Pedido do cliente: mostrar sempre a semana civil completa (segunda a sexta),
    independente de qual dia da semana é hoje — ver central_atendimento."""
    inicio = data - timedelta(days=data.weekday())
    return [inicio + timedelta(days=i) for i in range(5)]


def _janelas_do_dia(profissional, dia):
    """
    Devolve os blocos de horário do profissional para essa DATA específica,
    em minutos desde a meia-noite — ex: [(480, 720), (840, 1080)] = atende
    das 8h às 12h e das 14h às 18h.

    Uma exceção cadastrada para essa data (ver cadastros.models.
    ExcecaoAtendimento) substitui completamente o horário fixo semanal só
    nesse dia — mesma regra usada em Profissional.bloco_atendimento_em().
    """
    excecoes = list(profissional.excecoes_atendimento.filter(data=dia))
    blocos = excecoes if excecoes else list(profissional.horarios_atendimento.filter(dia_semana=dia.weekday()))
    return [
        (bloco.horario_inicio.hour * 60 + bloco.horario_inicio.minute, bloco.horario_fim.hour * 60 + bloco.horario_fim.minute)
        for bloco in blocos
    ]


def _monta_disponibilidade(profissional, dias):
    """
    Monta uma grade de horários (de SLOT_MINUTOS em SLOT_MINUTOS) para os dias
    informados (ver _semana_dias_uteis) do profissional, marcando cada horário como
    livre, ocupado, ou fora do horário de atendimento dele — usada na
    Central de Atendimento para a recepção/call center encontrar rapidamente
    um horário vago sem precisar adivinhar a agenda.

    Só considera os horários que o profissional realmente atende (cadastrados
    em "Profissionais > Horários", com exceções pontuais tendo prioridade —
    ver "Profissionais > Exceções"), para não oferecer como "vago" um horário
    em que ele nem trabalha. Se o profissional não tiver nenhum horário
    cadastrado ainda, devolve None (o template mostra um aviso pedindo para
    cadastrar antes).
    """
    if not profissional.horarios_atendimento.exists() and not profissional.excecoes_atendimento.exists():
        return None

    janelas_por_dia = {dia: _janelas_do_dia(profissional, dia) for dia in dias}

    # Eixo de horários da grade = do início mais cedo ao término mais tarde
    # entre todas as janelas dos dias mostrados (evita mostrar linhas de
    # horário que nenhum dia usa, ex: um profissional só de manhã não
    # precisa de linhas até as 20h).
    todos_inicios = [inicio for janelas in janelas_por_dia.values() for inicio, _fim in janelas]
    todos_fins = [fim for janelas in janelas_por_dia.values() for _inicio, fim in janelas]
    if not todos_inicios:
        return None
    minuto_inicial = min(todos_inicios)
    minuto_final = max(todos_fins)

    horarios = []
    minuto = minuto_inicial
    while minuto < minuto_final:
        horarios.append(minuto)
        minuto += SLOT_MINUTOS

    agendamentos = list(
        Agendamento.objects.filter(
            profissional=profissional,
            data_hora_inicio__date__range=(dias[0], dias[-1]),
        ).exclude(status=Agendamento.Status.CANCELADO)
    )

    ocupados_por_dia = {}
    for agendamento in agendamentos:
        inicio_local = timezone.localtime(agendamento.data_hora_inicio)
        fim_local = timezone.localtime(agendamento.data_hora_fim)
        ocupados_por_dia.setdefault(inicio_local.date(), []).append(
            (inicio_local.hour * 60 + inicio_local.minute, fim_local.hour * 60 + fim_local.minute)
        )

    hoje = timezone.localdate()
    agora = timezone.localtime()
    agora_minutos = agora.hour * 60 + agora.minute

    linhas = []
    for minutos_slot in horarios:
        celulas = []
        for dia in dias:
            janelas_do_dia = janelas_por_dia.get(dia, [])
            atende = any(
                inicio_janela <= minutos_slot < fim_janela for inicio_janela, fim_janela in janelas_do_dia
            )
            ocupado = any(
                minutos_slot < fim_ocupado and inicio_ocupado < minutos_slot + SLOT_MINUTOS
                for inicio_ocupado, fim_ocupado in ocupados_por_dia.get(dia, [])
            )
            # dia < hoje é possível agora que a grade sempre mostra a semana civil
            # inteira (ver _semana_dias_uteis) — numa quarta-feira, segunda e terça
            # dessa mesma semana aparecem na grade só pra completar a visão da semana,
            # mas já estão inteiramente no passado (nenhum horário delas fica "livre").
            passou = dia < hoje or (dia == hoje and minutos_slot < agora_minutos)
            celulas.append(
                {
                    "data": dia,
                    "hora": f"{minutos_slot // 60:02d}:{minutos_slot % 60:02d}",
                    "atende": atende,
                    "livre": atende and not ocupado and not passou,
                    # Expostos à parte (em vez de só um "livre" booleano) porque o template
                    # precisa DISTINGUIR "ocupado de verdade" de "já passou" — antes as duas
                    # situações caíam na mesma célula cinza "—", e cancelar um atendimento cujo
                    # horário já passou fazia a célula continuar cinza (só que agora por ter
                    # passado, não por estar ocupado), dando a falsa impressão de que o
                    # cancelamento não liberou o horário.
                    "ocupado": ocupado,
                    "passou": passou,
                }
            )
        linhas.append({"hora": f"{minutos_slot // 60:02d}:{minutos_slot % 60:02d}", "celulas": celulas})

    return {"dias": dias, "linhas": linhas}


def _ultimo_agendamento_do_usuario(usuario):
    """Último agendamento (não cancelado, de profissional ativo) que este usuário marcou —
    base do botão "Repetir último agendamento" da Central e do formulário."""
    return (
        Agendamento.objects.filter(criado_por=usuario, profissional__ativo=True)
        .exclude(status=Agendamento.Status.CANCELADO)
        .select_related("profissional", "convenio", "paciente")
        .prefetch_related("itens__procedimento")
        .order_by("-criado_em", "-pk")
        .first()
    )


def _iniciais_repetindo(base, com_turno=True):
    """Campos de um novo agendamento copiados de `base` (tudo menos paciente, data e
    horário): profissional, especialidade, forma de atendimento, convênio, turno/duração
    e a lista de procedimentos."""
    inicial = {
        "profissional": base.profissional_id,
        "forma_atendimento": base.forma_atendimento,
        "especialidade": base.especialidade,
        "especialidade_outra": base.especialidade_outra,
    }
    if base.convenio_id:
        inicial["convenio"] = base.convenio_id
    if base.sem_hora_marcada:
        if com_turno:
            inicial["turno"] = base.turno
    else:
        inicial["duracao_minutos"] = int((base.data_hora_fim - base.data_hora_inicio).total_seconds() // 60) or 30
    itens = [{"procedimento": item.procedimento_id, "quantidade": item.quantidade} for item in base.itens.all()]
    return inicial, itens


# @never_cache: sem isso, o botão "voltar" do navegador pode reexibir uma versão desta
# página guardada no cache ANTES de um cancelamento/agendamento feito em outra tela — a
# recepção vê o horário antigo (ex: ainda "ocupado" depois de já ter cancelado), porque o
# navegador nem chegou a pedir a página de novo ao servidor (bug relatado em 20/07/2026).
@never_cache
@permission_required("accounts.central_atendimento_usar", raise_exception=True)
def central_atendimento(request):
    """
    Tela pensada para quem agenda por telefone (recepção/call center):
    escolhe a especialidade, depois o profissional, e vê os horários livres
    já destacados da semana (segunda a sexta, com navegação de semana
    anterior/seguinte), em vez de precisar abrir a agenda de cada
    profissional manualmente para procurar um horário vago.

    Permissão própria ('central_atendimento_usar'), separada de 'agenda_criar'
    — dá pra liberar essa tela de busca sem necessariamente deixar a pessoa
    criar agendamentos por qualquer outro caminho do sistema (o clique em
    'Vago' que efetivamente cria o agendamento continua exigindo
    'agenda_criar' normalmente, checado em agendamento_novo).
    """
    profissionais_ativos = (
        Profissional.objects.filter(ativo=True).prefetch_related("especialidades_adicionais").order_by("nome")
    )

    # Cada profissional entra em TODAS as suas especialidades (principal + adicionais,
    # ver Profissional.especialidades_pares) — não só a principal, senão um profissional
    # com 2+ especialidades só apareceria numa delas aqui.
    especialidades = {}
    for profissional in profissionais_ativos:
        for especialidade, especialidade_outra in profissional.especialidades_pares:
            chave = f"{especialidade}|{especialidade_outra}"
            if chave not in especialidades:
                especialidades[chave] = {"rotulo": especialidade_rotulo(especialidade, especialidade_outra), "profissionais": []}
            especialidades[chave]["profissionais"].append(profissional)
    lista_especialidades = sorted(
        [(chave, dados) for chave, dados in especialidades.items()], key=lambda item: item[1]["rotulo"]
    )

    especialidade_selecionada = request.GET.get("especialidade") or ""
    profissional_id = request.GET.get("profissional") or ""

    # Profissional escolhido direto na busca (sem passar pela especialidade): cai na
    # primeira especialidade dele, pra continuar mostrando os cartões do passo 2.
    if profissional_id:
        especialidade_do_profissional = [
            chave for chave, dados in especialidades.items()
            if any(str(p.pk) == profissional_id for p in dados["profissionais"])
        ]
        if especialidade_do_profissional and especialidade_selecionada not in especialidade_do_profissional:
            especialidade_selecionada = especialidade_do_profissional[0]

    profissionais_da_especialidade = []
    if especialidade_selecionada in especialidades:
        profissionais_da_especialidade = especialidades[especialidade_selecionada]["profissionais"]

    hoje = timezone.localdate()
    semana_atual_inicio = hoje - timedelta(days=hoje.weekday())
    data_referencia = parse_date(request.GET.get("inicio") or "") or hoje
    dias_semana = _semana_dias_uteis(data_referencia)
    # Nunca deixa "voltar" pra uma semana inteira no passado — a Central é pra achar
    # horário vago daqui pra frente, não pra revisar o passado (a semana ATUAL sempre
    # aparece inteira, segunda a sexta, mesmo que hoje já seja no meio dela — ver
    # _semana_dias_uteis e o ajuste de 'passou' em _monta_disponibilidade).
    if dias_semana[0] < semana_atual_inicio:
        dias_semana = _semana_dias_uteis(hoje)

    profissional_selecionado = None
    disponibilidade = None
    semana_anterior = None
    proxima_semana = None
    tem_horario_cadastrado = False
    proximas_datas_avulsas = []
    if profissional_id:
        profissional_selecionado = Profissional.objects.filter(pk=profissional_id, ativo=True).first()
        if profissional_selecionado:
            tem_horario_semanal = profissional_selecionado.horarios_atendimento.exists()
            # Datas específicas cadastradas em "Profissionais > Datas / exceções" — para
            # profissionais que não têm dia fixo na semana e só vêm em datas marcadas
            # no mês (ex: "vem todo dia 15"), é só por aqui que a recepção descobre
            # quando ele atende.
            proximas_datas_avulsas = list(
                profissional_selecionado.excecoes_atendimento.filter(data__gte=hoje)
                .order_by("data")
                .values_list("data", flat=True)
                .distinct()[:8]
            )
            tem_horario_cadastrado = tem_horario_semanal or profissional_selecionado.excecoes_atendimento.exists()

            # Profissional só de datas avulsas, abrindo a Central sem escolher semana:
            # já pula direto para a semana da próxima data dele, em vez de mostrar
            # uma semana vazia.
            if not request.GET.get("inicio") and not tem_horario_semanal and proximas_datas_avulsas:
                dias_semana = _semana_dias_uteis(proximas_datas_avulsas[0])

            disponibilidade = _monta_disponibilidade(profissional_selecionado, dias_semana)
            if tem_horario_cadastrado:
                candidato_anterior = dias_semana[0] - timedelta(days=7)
                semana_anterior = candidato_anterior if candidato_anterior >= semana_atual_inicio else None
                proxima_semana = dias_semana[0] + timedelta(days=7)

    # "Repetir último agendamento" (pedido da clínica, 23/10/2026): marcar vários pacientes
    # seguidos com o mesmo profissional/procedimento sem escolher tudo de novo.
    ultimo_agendamento = _ultimo_agendamento_do_usuario(request.user)
    url_repetir_ultimo = ""
    if ultimo_agendamento:
        chave_ultimo = (
            f"{ultimo_agendamento.especialidade}|"
            f"{ultimo_agendamento.especialidade_outra if ultimo_agendamento.especialidade == 'outra' else ''}"
        )
        parametros = {
            "especialidade": chave_ultimo,
            "profissional": ultimo_agendamento.profissional_id,
            "repetir": ultimo_agendamento.pk,
        }
        data_ultimo = timezone.localtime(ultimo_agendamento.data_hora_inicio).date()
        if data_ultimo > hoje:
            parametros["inicio"] = data_ultimo.isoformat()
        url_repetir_ultimo = "?" + urlencode(parametros)
    repetir_id = request.GET.get("repetir") or ""
    repetir_qs = ""
    if (
        ultimo_agendamento
        and profissional_selecionado
        and repetir_id == str(ultimo_agendamento.pk)
        and ultimo_agendamento.profissional_id == profissional_selecionado.pk
    ):
        repetir_qs = f"&repetir={ultimo_agendamento.pk}"

    contexto = {
        "ultimo_agendamento": ultimo_agendamento,
        "url_repetir_ultimo": url_repetir_ultimo,
        "repetir_qs": repetir_qs,
        "profissionais_ativos": profissionais_ativos,
        "lista_especialidades": lista_especialidades,
        "especialidade_selecionada": especialidade_selecionada,
        "profissionais_da_especialidade": profissionais_da_especialidade,
        "profissional_selecionado": profissional_selecionado,
        "disponibilidade": disponibilidade,
        "semana_anterior": semana_anterior,
        "proxima_semana": proxima_semana,
        "tem_horario_cadastrado": tem_horario_cadastrado,
        "proximas_datas_avulsas": proximas_datas_avulsas,
        "semana_exibida": dias_semana,
    }
    return render(request, "agenda/central_atendimento.html", contexto)


def _monta_grade_calendario(data_selecionada, profissionais_ids):
    """
    Monta as semanas do mês (cada dia com sua contagem de agendamentos) para
    o calendário visual da Agenda do dia, em vez de só um campo de data.

    `profissionais_ids`: None = sem restrição; lista de ids = só contagem
    desses profissionais (ver _monta_grade_dia).
    """
    cal = calendar.Calendar(firstweekday=6)  # semana começa no domingo
    semanas_datas = cal.monthdatescalendar(data_selecionada.year, data_selecionada.month)
    primeiro_dia_grade = semanas_datas[0][0]
    ultimo_dia_grade = semanas_datas[-1][-1]

    contagem_qs = Agendamento.objects.filter(
        data_hora_inicio__date__range=(primeiro_dia_grade, ultimo_dia_grade)
    ).exclude(status=Agendamento.Status.CANCELADO)
    if profissionais_ids is not None:
        contagem_qs = contagem_qs.filter(profissional_id__in=profissionais_ids)

    contagem_por_dia = {}
    for inicio in contagem_qs.values_list("data_hora_inicio", flat=True):
        dia = timezone.localtime(inicio).date()
        contagem_por_dia[dia] = contagem_por_dia.get(dia, 0) + 1

    hoje = timezone.localdate()
    semanas = []
    for semana in semanas_datas:
        semana_montada = []
        for dia in semana:
            quantidade = contagem_por_dia.get(dia, 0)
            semana_montada.append(
                {
                    "data": dia,
                    "no_mes": dia.month == data_selecionada.month,
                    "selecionado": dia == data_selecionada,
                    "hoje": dia == hoje,
                    "quantidade": quantidade,
                    # Lista pronta pro template desenhar até 4 bolinhas sem
                    # precisar fazer conta dentro do template.
                    "pontos": range(min(quantidade, 4)),
                    "pontos_extra": quantidade - 4 if quantidade > 4 else 0,
                }
            )
        semanas.append(semana_montada)

    primeiro_dia_mes = data_selecionada.replace(day=1)
    mes_anterior = (primeiro_dia_mes - timedelta(days=1)).replace(day=1)
    if primeiro_dia_mes.month == 12:
        mes_seguinte = primeiro_dia_mes.replace(year=primeiro_dia_mes.year + 1, month=1)
    else:
        mes_seguinte = primeiro_dia_mes.replace(month=primeiro_dia_mes.month + 1)

    return {
        "semanas_calendario": semanas,
        "mes_atual": data_selecionada,
        "mes_anterior": mes_anterior,
        "mes_seguinte": mes_seguinte,
    }


CHAVE_SESSAO_PROFISSIONAL_AGENDA = "agenda_dia_profissional"


@never_cache  # ver comentário em central_atendimento — mesmo motivo (agenda muda o tempo todo).
@login_required
def agenda_dia(request):
    """
    Visão da agenda com TODOS os profissionais — usada pela recepção e pelo
    apoio clínico. Suporta três layouts via ?visao=dia|semana|mes, no
    estilo Google Agenda: 'dia' e 'semana' mostram uma grade por horário
    (compromissos como cartões posicionados pelo horário real), e 'mes'
    mostra o calendário do mês inteiro como conteúdo principal.

    Também trata o POST de dois formulários "de hoje" da recepção — cada um
    com seu próprio botão/form, distinguidos pelo campo oculto 'form':

    - "estou no guichê: [X]" (ver GuicheDoDiaForm) — grava um AtendimentoGuiche
      para hoje, ligado ao usuário logado. Só quem tem 'fila_autorizar_recepcao'
      vê/usa esse formulário.
    - "especialidades de hoje" (ver EspecialidadesDoDiaForm) — grava o
      conjunto de AtendimentoEspecialidade do dia (substituindo o que valia
      antes), pré-marcado a partir de EspecialidadePadraoUsuario na primeira
      vez que a tela é aberta no dia. Só quem tem
      'fila_especialidade_configurar' vê/usa esse formulário.

    Preparação para o futuro painel de fila/senha (guichê e especialidade que
    cada atendente está cobrindo).
    """
    if not _pode_ver_agenda_dia(request.user):
        raise PermissionDenied

    def _redirect_agenda_dia():
        destino = reverse("agenda:agenda_dia")
        parametros = []
        if request.POST.get("data"):
            parametros.append(f"data={request.POST['data']}")
        if request.POST.get("visao"):
            parametros.append(f"visao={request.POST['visao']}")
        if parametros:
            destino += "?" + "&".join(parametros)
        return redirect(destino)

    pode_escolher_guiche = request.user.has_perm("accounts.fila_autorizar_recepcao")
    pode_configurar_especialidade = request.user.has_perm("accounts.fila_especialidade_configurar")
    hoje = timezone.localdate()

    form_guiche = GuicheDoDiaForm() if pode_escolher_guiche else None
    form_especialidades = None
    especialidades_hoje = []
    if pode_configurar_especialidade:
        especialidades_hoje = AtendimentoEspecialidade.especialidades_em(request.user, hoje)
        especialidades_iniciais = especialidades_hoje or EspecialidadePadraoUsuario.especialidades_de(request.user)
        form_especialidades = EspecialidadesDoDiaForm(
            initial={
                "especialidades": [f"{especialidade}|{outra}" for especialidade, outra in especialidades_iniciais],
                "profissionais": [str(pk) for pk in AtendimentoProfissional.profissionais_em(request.user, hoje)],
            }
        )

    if request.method == "POST" and request.POST.get("form") == "guiche" and pode_escolher_guiche:
        form_guiche = GuicheDoDiaForm(request.POST)
        if form_guiche.is_valid():
            AtendimentoGuiche.objects.create(
                usuario=request.user, guiche=form_guiche.cleaned_data["guiche"], data=hoje
            )
            messages.success(request, f"Guichê de hoje definido: {form_guiche.cleaned_data['guiche'].nome}.")
            return _redirect_agenda_dia()

    elif request.method == "POST" and request.POST.get("form") == "especialidades" and pode_configurar_especialidade:
        form_especialidades = EspecialidadesDoDiaForm(request.POST)
        if form_especialidades.is_valid():
            pares = [
                especialidade_a_partir_da_chave(chave) for chave in form_especialidades.cleaned_data["especialidades"]
            ]
            AtendimentoEspecialidade.definir(usuario=request.user, data=hoje, especialidades=pares)
            AtendimentoProfissional.definir(
                usuario=request.user, data=hoje, profissionais_ids=form_especialidades.cleaned_data["profissionais"]
            )
            messages.success(request, "Especialidades de hoje atualizadas.")
            return _redirect_agenda_dia()

    data = parse_date(request.GET.get("data") or "") or hoje
    visao = request.GET.get("visao") or "dia"
    if visao not in ("dia", "semana", "mes"):
        visao = "dia"

    # O profissional escolhido no filtro fica guardado na sessão (pedido da clínica,
    # 23/10/2026): trocar o dia por um caminho que não leva o ?profissional= (voltar
    # depois de salvar um agendamento, "trocar o dia", menu) desmarcava o filtro.
    # Sem o parâmetro na URL, vale o último escolhido; "Todos" chega como
    # ?profissional= vazio e limpa a lembrança.
    if "profissional" in request.GET:
        profissional_id = request.GET.get("profissional") or ""
        request.session[CHAVE_SESSAO_PROFISSIONAL_AGENDA] = profissional_id
    else:
        profissional_id = request.session.get(CHAVE_SESSAO_PROFISSIONAL_AGENDA) or ""
    if profissional_id and not (
        str(profissional_id).isdigit() and Profissional.objects.filter(pk=profissional_id, ativo=True).exists()
    ):
        profissional_id = ""
        request.session.pop(CHAVE_SESSAO_PROFISSIONAL_AGENDA, None)

    # Restringe a grade a uma lista de profissionais — None = sem restrição
    # (mostra a clínica inteira). Um Profissional escolhido no filtro é mais
    # específico e tem prioridade; sem ele, se o usuário logado tiver
    # Especialidades de hoje definidas (AtendimentoEspecialidade), a grade já
    # nasce restrita a elas. Antes disso, "Especialidades de hoje" não tinha
    # nenhum efeito aqui — só alimentava o futuro painel de fila (ver
    # docstring desta view).
    profissionais_permitidos_ids = None
    especialidades_hoje_rotulos = []
    if profissional_id:
        profissionais_permitidos_ids = [profissional_id]
    elif especialidades_hoje:
        # Numa especialidade em que a atendente marcou profissionais, a grade mostra só eles.
        profissionais_permitidos_ids = []
        for especialidade, outra, ids in AtendimentoProfissional.cobertura_em(request.user, hoje):
            profissionais_permitidos_ids += (
                list(ids)
                if ids
                else Profissional.ativos_com_especialidades([(especialidade, outra)]).values_list("pk", flat=True)
            )
        profissionais_permitidos_ids = list(set(profissionais_permitidos_ids))
        especialidades_hoje_rotulos = [
            especialidade_rotulo(especialidade, outra) for especialidade, outra in especialidades_hoje
        ]

    contexto = {
        "data": data,
        "visao": visao,
        "profissionais": Profissional.objects.filter(ativo=True).order_by("nome"),
        "profissional_selecionado": profissional_id,
        "especialidades_hoje_rotulos": especialidades_hoje_rotulos,
        "horas_grade": _horas_da_grade(),
        "pixels_por_hora": PIXELS_POR_HORA,
        "altura_total_grade": _altura_total_grade(),
        "form_guiche": form_guiche,
        "guiche_de_hoje": AtendimentoGuiche.guiche_em(request.user, hoje) if pode_escolher_guiche else None,
        "form_especialidades": form_especialidades,
    }
    contexto.update(_monta_grade_calendario(data, profissionais_permitidos_ids))

    if visao == "semana":
        # Segunda a sexta apenas — a clínica não funciona aos fins de semana
        # (weekday(): Segunda=0 ... Domingo=6).
        intervalo_inicio = data - timedelta(days=data.weekday())
        intervalo_fim = intervalo_inicio + timedelta(days=4)
        contexto.update(
            {
                "intervalo_inicio": intervalo_inicio,
                "intervalo_fim": intervalo_fim,
                "semana_anterior": data - timedelta(days=7),
                "semana_seguinte": data + timedelta(days=7),
                "dias_semana": _monta_grade_semana(intervalo_inicio, intervalo_fim, profissionais_permitidos_ids),
            }
        )
    elif visao == "dia":
        contexto["colunas_dia"] = _monta_grade_dia(data, profissionais_permitidos_ids)
        # Lista do dia com os botões de ação rápida (Confirmar/Realizado/Faltou/
        # Editar/Cancelar/Excluir) — mostrada abaixo da grade visual, inclui
        # também os cancelados (a grade acima só mostra os não cancelados).
        agendamentos_do_dia = (
            Agendamento.objects.filter(data_hora_inicio__date=data)
            .select_related("paciente", "profissional", "convenio", "criado_por")
            .prefetch_related("itens__procedimento", "itens__retorno_de__agendamento")
            .order_by("data_hora_inicio", "pk")
        )
        if profissionais_permitidos_ids is not None:
            agendamentos_do_dia = agendamentos_do_dia.filter(profissional_id__in=profissionais_permitidos_ids)
        # Consulta/exame: posição na lista do turno (ordem de agendamento) — ver
        # Agendamento.numerar_ordem_lista/horario_exibicao.
        contexto["agendamentos"] = Agendamento.numerar_ordem_lista(list(agendamentos_do_dia))
        contexto["motivos_prioridade"] = _MOTIVOS_PRIORIDADE.items()
        if profissional_id:
            contexto["resumo_profissional"] = _resumo_profissional_no_dia(profissional_id, data)

    return render(request, "agenda/agenda_dia.html", contexto)


def _resumo_profissional_no_dia(profissional_id, data):
    """
    Faixa de resumo mostrada na Agenda do dia (visão Dia) quando um profissional é
    escolhido no filtro — pedido da clínica (13/10/2026) pra recepção/central bater o
    olho no dia dele sem ler a grade: quantos agendados, por situação, o próximo
    paciente (só quando o dia é hoje) e a lista de procedimentos marcados.

    Agrupamento das situações: "a confirmar" = Agendado/Pré-confirmado; "confirmados"
    = Confirmado/Pré-autorizado (ainda aguardando o financeiro); "atendidos" =
    Agendamento.STATUS_PRODUCAO (Autorizado/Realizado — Realizado sozinho não conta,
    ver decisão do cliente no modelo). Cancelados não entram no total.
    """
    try:
        profissional_id = int(profissional_id)
    except (TypeError, ValueError):
        return None

    Status = Agendamento.Status
    agendamentos = list(
        Agendamento.objects.filter(profissional_id=profissional_id, data_hora_inicio__date=data)
        .select_related("paciente")
        .order_by("data_hora_inicio", "pk")
    )
    ativos = [a for a in agendamentos if a.status != Status.CANCELADO]

    proximo = None
    if data == timezone.localdate():
        agora = timezone.now()
        proximo = next(
            (
                a
                for a in ativos
                if a.data_hora_inicio >= agora
                and a.status not in (Status.FALTOU, *Agendamento.STATUS_PRODUCAO)
            ),
            None,
        )

    # Retorno contado à parte do mesmo procedimento (pedido da clínica, 06/11/2026: antes a
    # consulta e o retorno dela saíam somados num número só).
    procedimentos = (
        AgendamentoItem.objects.filter(agendamento__in=[a.pk for a in ativos])
        .values("procedimento__nome")
        .annotate(
            total=Sum("quantidade"),
            normais=Sum("quantidade", filter=AgendamentoItem.filtro_nao_retorno()),
            retornos=Sum("quantidade", filter=AgendamentoItem.filtro_retorno()),
        )
        .order_by("-total", "procedimento__nome")
    )
    lista_procedimentos = []
    for p in procedimentos:
        if p["normais"]:
            lista_procedimentos.append({"nome": p["procedimento__nome"], "total": p["normais"], "retorno": False})
        if p["retornos"]:
            lista_procedimentos.append({"nome": p["procedimento__nome"], "total": p["retornos"], "retorno": True})

    def contar(*situacoes):
        return sum(1 for a in ativos if a.status in situacoes)

    return {
        "total": len(ativos),
        "a_confirmar": contar(Status.AGENDADO, Status.PRE_CONFIRMADO),
        "confirmados": contar(Status.CONFIRMADO, Status.PRE_AUTORIZADO),
        "atendidos": contar(*Agendamento.STATUS_PRODUCAO),
        "faltas": contar(Status.FALTOU),
        "cancelados": len(agendamentos) - len(ativos),
        "proximo": proximo,
        "procedimentos": lista_procedimentos,
        "retornos": sum(p["total"] for p in lista_procedimentos if p["retorno"]),
    }


def _ordena_fila_de_chamada(agendamentos):
    """
    Ordem dos agendamentos de hoje na 'Minha agenda' do profissional. A base é a ordem real
    da sala de espera: `autorizado_em` (quando cada um teve a vez liberada), com fallback pra
    `data_hora_inicio` em quem ainda não foi autorizado (sem o fallback, misturar None com
    datetime no sort estoura TypeError).

    Entre os AUTORIZADOS, quem já foi chamado (chamado_em preenchido, ainda Autorizado porque
    não foi marcado como atendido) fica sempre à frente: está em atendimento. Os autorizados
    só trocam de lugar entre as posições que já ocupavam.

    Exame com preparo NÃO fura mais a fila aqui (pedido da clínica, 2026-10-26: passava na
    frente de quem tinha sido autorizado antes). A prioridade dele agora é na recepção: senha
    própria no totem, chamada antes das demais (fila.models.Senha.Tipo.EXAME_PREPARO) — chega
    autorizado mais cedo e entra na ordem normal de autorização.

    Particular (ou convênio pago na hora) guarda a vez desde que foi encaminhado pro
    financeiro, não desde o pagamento (pedido da clínica, 23/10/2026 — ver
    Agendamento.momento_na_fila): senão perdia a vez pra convênio autorizado enquanto pagava.

    Prioritário (Agendamento.motivo_prioridade, pedido da clínica, 29/10/2026 — é o que a
    recepção já fala pro paciente: "tem 4 na sua frente, desde que não chegue prioridade")
    passa na frente de normal já autorizado, alternando 1 prioritário e 1 normal entre os que
    ainda aguardam chamada — ver _intercala_prioritarios.

    Acupuntura (Agendamento.eh_acupuntura, pedido da clínica, 06/11/2026) vai pro começo de
    quem aguarda, na frente até de prioritário — SÓ pro profissional com
    Profissional.acupuntura_primeiro_na_fila marcado (um específico da clínica): ele aplica as
    agulhas e atende os outros enquanto o paciente fica na sessão. Entre acupunturas, vale a
    ordem de autorização.
    """
    def momento(agendamento):
        return agendamento.momento_na_fila

    ordenados = sorted(agendamentos, key=momento)
    posicoes = [i for i, a in enumerate(ordenados) if a.status == Agendamento.Status.AUTORIZADO]
    autorizados = [ordenados[i] for i in posicoes]
    em_atendimento = [a for a in autorizados if a.chamado_em is not None]
    aguardando = [a for a in autorizados if a.chamado_em is None]
    acupuntura = [a for a in aguardando if a.eh_acupuntura and a.profissional.acupuntura_primeiro_na_fila]
    demais = [a for a in aguardando if a not in acupuntura]
    reordenados = (
        em_atendimento
        + acupuntura
        + _intercala_prioritarios(demais, _ultimo_chamado_foi_prioritario(agendamentos))
    )
    for posicao, agendamento in zip(posicoes, reordenados):
        ordenados[posicao] = agendamento
    return ordenados


_MOTIVOS_PRIORIDADE = dict(Agendamento._meta.get_field("motivo_prioridade").choices)


@require_POST
@permission_required("accounts.fila_autorizar_recepcao", raise_exception=True)
def agendamento_prioridade(request, pk):
    """Corrige a prioridade (Agendamento.motivo_prioridade) depois de encaminhar/autorizar —
    a recepção esqueceu de marcar ou marcou errado. Só enquanto o paciente ainda não foi
    chamado pelo profissional: depois disso a ordem já não muda nada."""
    agendamento = get_object_or_404(Agendamento.objects.select_related("paciente"), pk=pk)
    destino = (request.META.get("HTTP_REFERER") or reverse("agenda:agenda_dia")).split("#")[0]
    destino += f"#agendamento-{agendamento.pk}"
    if (
        agendamento.status not in (Agendamento.Status.PRE_AUTORIZADO, Agendamento.Status.AUTORIZADO)
        or agendamento.chamado_em
    ):
        messages.error(request, "A prioridade só pode ser alterada antes de o paciente ser chamado.")
        return redirect(destino)
    motivo = request.POST.get("motivo_prioridade", "")
    if motivo not in _MOTIVOS_PRIORIDADE:
        motivo = ""
    if motivo != agendamento.motivo_prioridade:
        agendamento.motivo_prioridade = motivo
        agendamento.save(update_fields=["motivo_prioridade"])
        rotulo = _MOTIVOS_PRIORIDADE.get(motivo, "normal (sem prioridade)")
        LogAuditoria.registrar(
            request, request.user, f"Alterou a prioridade de '{agendamento.paciente.nome_completo}' para {rotulo}"
        )
        messages.success(request, f"Prioridade de '{agendamento.paciente.nome_completo}': {rotulo}.")
    return redirect(destino)


def _ultimo_chamado_foi_prioritario(agendamentos):
    """Se o último paciente chamado hoje por estes profissionais (o dia dos agendamentos
    recebidos) era prioritário — define de quem é a vez na alternância 1 P : 1 N. Consulta o
    banco porque a lista recebida pode não trazer quem já foi atendido (ver
    agendamento_chamar_recepcao)."""
    if not agendamentos:
        return False
    ultimo = (
        Agendamento.objects.filter(
            profissional_id__in={a.profissional_id for a in agendamentos},
            data_hora_inicio__date__in={timezone.localtime(a.data_hora_inicio).date() for a in agendamentos},
            chamado_em__isnull=False,
        )
        .order_by("-chamado_em", "-pk")
        .values_list("motivo_prioridade", flat=True)
        .first()
    )
    return bool(ultimo)


def _intercala_prioritarios(aguardando, ultimo_foi_prioritario):
    """Ordem de chamada de quem aguarda (já na ordem de momento_na_fila): alterna 1 prioritário
    e 1 normal, começando pelo prioritário se o último chamado foi normal. Na vez do normal, um
    prioritário que entrou na fila ANTES dele vai primeiro (mesma regra da senha na recepção —
    fila.models.Senha._escolher_proxima): prioritário só passa na frente de quem chegou antes
    dele. Acabando um dos grupos, segue o outro na ordem."""
    prioritarios = [a for a in aguardando if a.prioritario]
    normais = [a for a in aguardando if not a.prioritario]
    vez_do_prioritario = not ultimo_foi_prioritario
    resultado = []
    while prioritarios or normais:
        if prioritarios and (
            vez_do_prioritario or not normais or prioritarios[0].momento_na_fila < normais[0].momento_na_fila
        ):
            resultado.append(prioritarios.pop(0))
            vez_do_prioritario = False
        else:
            resultado.append(normais.pop(0))
            vez_do_prioritario = True
    return resultado


@never_cache  # ver comentário em central_atendimento — mesmo motivo (agenda muda o tempo todo).
@login_required
def agenda_propria(request):
    """
    Agenda do próprio profissional logado — visão semanal em grade (mesmo
    estilo de blocos posicionados por horário da Agenda do dia/semana, ver
    _monta_grade_semana), com navegação de semana anterior/seguinte, e uma
    lista abaixo com as ações restritas de sempre (Marcar como atendido/
    Desmarcar), mais o link para "Adicionar registro no prontuário" e o
    indicador de encaixe.

    Também trata o POST do formulário "estou atendendo na sala: [X]"
    (ver SalaDoDiaForm) — grava um AtendimentoSala para hoje, preparação
    para o futuro painel de fila/senha por sala.
    """
    if not request.user.has_perm("accounts.agenda_ver_propria"):
        raise PermissionDenied

    profissional = None
    if request.user.profissional_id:
        profissional = Profissional.objects.filter(pk=request.user.profissional_id).first()

    if profissional is None:
        messages.error(
            request,
            "Seu usuário não está vinculado a um cadastro de profissional — "
            "peça para o administrador vincular (campo 'profissional vinculado' no seu usuário).",
        )
        return render(request, "agenda/agenda_propria.html", {"agendamentos": [], "profissional": None})

    if request.method == "POST":
        form_sala = SalaDoDiaForm(request.POST)
        if form_sala.is_valid():
            AtendimentoSala.objects.create(
                profissional=profissional, sala=form_sala.cleaned_data["sala"], data=timezone.localdate()
            )
            messages.success(request, f"Sala de hoje definida: {form_sala.cleaned_data['sala'].nome}.")
            destino = reverse("agenda:agenda_propria")
            data_atual = request.POST.get("data")
            if data_atual:
                destino += f"?data={data_atual}"
            return redirect(destino)
    else:
        form_sala = SalaDoDiaForm()

    hoje = timezone.localdate()
    data = parse_date(request.GET.get("data") or "") or hoje
    # Semana de segunda a sexta (a clínica não funciona aos fins de semana —
    # mesma regra usada em _monta_grade_semana/Agendamento.clean()).
    intervalo_inicio = data - timedelta(days=data.weekday())
    intervalo_fim = intervalo_inicio + timedelta(days=4)

    dias_semana = _monta_grade_semana(intervalo_inicio, intervalo_fim, [profissional.pk])

    # A lista de ações abaixo do calendário é só de HOJE (sempre o dia real de
    # hoje, não o 'data' navegável do calendário acima) — pedido do cliente:
    # é uma fila de trabalho pro agora, não um resumo da semana. Ordenada por
    # autorizado_em (quando cada um teve a vez liberada, refletindo a ordem
    # real da sala de espera) em vez de data_hora_inicio (hora agendada, que
    # pode não bater com a ordem real de chegada/liberação). Fallback pra
    # data_hora_inicio pra quem ainda não foi autorizado (autorizado_em=None)
    # — sem o fallback, misturar None com datetime no sort estoura TypeError.
    agendamentos_hoje = list(
        Agendamento.objects.filter(profissional=profissional, data_hora_inicio__date=hoje)
        .exclude(status=Agendamento.Status.CANCELADO)
        .select_related("paciente", "convenio")
        .prefetch_related("itens__procedimento", "itens__retorno_de__agendamento")
    )
    agendamentos_hoje = _ordena_fila_de_chamada(Agendamento.numerar_ordem_lista(agendamentos_hoje))

    # Marca a posição direto na instância (mesmos objetos da lista, não cópias)
    # — o template só olha 'ordem_chamada_hoje': presente = disponível pra
    # chamar hoje, e já dá o número; ausente = nem aparece o botão.
    autorizados_hoje = [a for a in agendamentos_hoje if a.status == Agendamento.Status.AUTORIZADO]
    for posicao, a in enumerate(autorizados_hoje):
        a.ordem_chamada_hoje = posicao + 1
    # Pedido do cliente (2026-10-16): a lista do profissional mostra só quem está liberado pra
    # atender (Autorizado) — agendados, faltas e já atendidos deixavam a lista grande demais.
    # A grade da semana acima continua mostrando tudo.
    agendamentos_hoje = autorizados_hoje

    # Recepção própria da sala de hoje (ver agendamento_chamar_recepcao): só existe quando a
    # sala escolhida tem lugares_recepcao_propria. 'posicao_recepcao' marca quem está lá agora
    # (1 = no consultório, 2+ = sentado esperando), pelo mesmo critério do painel de espera.
    sala_de_hoje = profissional.sala_em(hoje)
    recepcao_propria = None
    if sala_de_hoje and sala_de_hoje.tem_recepcao_propria:
        ocupantes = list(_ocupantes_recepcao_propria(sala_de_hoje, hoje))
        posicoes = {o.pk: n for n, o in enumerate(ocupantes, start=1)}
        for a in agendamentos_hoje:
            a.posicao_recepcao = posicoes.get(a.pk)
        livres = max(sala_de_hoje.lugares_recepcao_propria - len(ocupantes), 0)
        aguardando_chamada = sum(1 for a in autorizados_hoje if not a.chamado_em)
        recepcao_propria = {
            "nome": Sala.NOME_RECEPCAO_PROPRIA,
            "lugares": sala_de_hoje.lugares_recepcao_propria,
            "ocupados": len(ocupantes),
            "a_chamar": min(livres, aguardando_chamada),
        }

    return render(
        request,
        "agenda/agenda_propria.html",
        {
            "profissional": profissional,
            "sala_de_hoje": sala_de_hoje,
            "recepcao_propria": recepcao_propria,
            "form_sala": form_sala,
            "sem_evolucao": _atendimentos_sem_evolucao(profissional, hoje),
            "data": data,
            "intervalo_inicio": intervalo_inicio,
            "intervalo_fim": intervalo_fim,
            "semana_anterior": data - timedelta(days=7),
            "semana_seguinte": data + timedelta(days=7),
            "dias_semana": dias_semana,
            "horas_grade": _horas_da_grade(),
            "pixels_por_hora": PIXELS_POR_HORA,
            "altura_total_grade": _altura_total_grade(),
            "agendamentos": agendamentos_hoje,
        },
    )


def filas_dos_profissionais(data):
    """
    Fila de cada profissional hoje, depois da recepção (pedido da clínica, 10/11/2026: o
    paciente que já passou pela recepção pergunta "quantos tem na minha frente?"). Mesma ordem
    da Minha agenda (_ordena_fila_de_chamada — prioritário alternando, acupuntura etc.), pra
    recepção responder exatamente o que o profissional vai chamar. Só Autorizados, como na
    Minha agenda: quem já foi chamado aparece à parte (em atendimento); para quem aguarda,
    'na_frente' = quantos ainda aguardam antes dele.
    """
    agendamentos = list(
        Agendamento.objects.filter(data_hora_inicio__date=data)
        .exclude(status=Agendamento.Status.CANCELADO)
        .select_related("paciente", "profissional")
        .prefetch_related("itens__procedimento")
    )
    Agendamento.numerar_ordem_lista(agendamentos)
    por_profissional = {}
    for agendamento in agendamentos:
        por_profissional.setdefault(agendamento.profissional, []).append(agendamento)

    filas = []
    for profissional, lista in por_profissional.items():
        autorizados = [
            a for a in _ordena_fila_de_chamada(lista) if a.status == Agendamento.Status.AUTORIZADO
        ]
        em_atendimento = [a for a in autorizados if a.chamado_em]
        aguardando = [a for a in autorizados if not a.chamado_em]
        for na_frente, agendamento in enumerate(aguardando):
            agendamento.na_frente = na_frente
            agendamento.entrou_na_fila = timezone.localtime(agendamento.momento_na_fila)
        if em_atendimento or aguardando:
            filas.append({"profissional": profissional, "em_atendimento": em_atendimento, "aguardando": aguardando})
    filas.sort(key=lambda f: f["profissional"].nome)
    return filas


@never_cache
@login_required
def fila_profissionais(request):
    """Tela da recepção "Fila dos profissionais" — ver filas_dos_profissionais. Liberada pra
    quem autoriza na recepção ou tem a permissão de ver a fila de espera das senhas."""
    if not (
        request.user.has_perm("accounts.fila_autorizar_recepcao")
        or request.user.has_perm("accounts.fila_ver_aguardando")
    ):
        raise PermissionDenied
    return render(
        request, "agenda/fila_profissionais.html", {"filas": filas_dos_profissionais(timezone.localdate())}
    )


DIAS_EVOLUCAO_ATRASADA = 30


def _atendimentos_sem_evolucao(profissional, hoje):
    """Atendimentos dos últimos DIAS_EVOLUCAO_ATRASADA dias (antes de hoje) deste profissional,
    autorizados/realizados, que ainda não têm registro dele no prontuário — pedido da clínica
    (26/10/2026): a equipe multi muitas vezes evolui dias depois, e a lista de ações da Minha
    agenda é só de hoje. O link abre prontuario:entrada_nova com a data do atendimento.

    Ajustes (clínica, 28/10/2026): só atendimento da equipe multi entra (médico usa o
    formulário completo na hora e não tinha essa lista — ver usa_prontuario_simplificado);
    e conta como evoluído também o registro feito pela busca do Prontuário, sem passar pelo
    "Evoluir": mesmo paciente com registro deste profissional na data do atendimento.

    06/11/2026 (clínica: "lista gigante"): "equipe multi" passou a ser uma lista positiva
    (prontuario.models.eh_equipe_multi), não mais "tudo que não é médico". Atendimento sem
    especialidade gravada usa a do profissional. E profissional com QUALQUER especialidade
    médica não tem a lista (caso relatado: médico que também atende como acupunturista)."""
    from cadastros.models import especialidade_rotulo
    from prontuario.models import eh_especialidade_medica

    if any(eh_especialidade_medica(especialidade_rotulo(*par)) for par in profissional.especialidades_pares):
        return []
    inicio = hoje - timedelta(days=DIAS_EVOLUCAO_ATRASADA)
    atendimentos = (
        Agendamento.objects.filter(
            profissional=profissional,
            status__in=Agendamento.STATUS_PRODUCAO,
            paciente__ativo=True,
            data_hora_inicio__date__range=(inicio, hoje - timedelta(days=1)),
        )
        .exclude(entradas_prontuario__profissional=profissional)
        .select_related("paciente")
        .order_by("-data_hora_inicio")
    )
    evoluidos = {
        (entrada.paciente_id, entrada.data_exibicao)
        for entrada in EntradaProntuario.objects.filter(profissional=profissional, criado_em__date__gte=inicio).only(
            "paciente_id", "data_atendimento", "criado_em"
        )
    }
    return [
        a
        for a in atendimentos
        if eh_equipe_multi(a.especialidade_exibicao if a.especialidade else profissional.especialidade_exibicao)
        and (a.paciente_id, timezone.localdate(a.data_hora_inicio)) not in evoluidos
    ]


@login_required
def procedimentos_do_profissional(request, pk):
    """
    Endpoint usado via JavaScript na tela de agendamento: devolve só os
    procedimentos que aquele profissional está habilitado a realizar (campo
    'profissionais habilitados', configurado em Cadastros → Procedimentos) E
    que têm preço cadastrado pra forma de atendimento escolhida — só é
    possível agendar o que tem valor: 'valor_particular' preenchido pra
    particular (aceita ?forma_atendimento=PARTICULAR ou nenhum parâmetro, já
    que é o padrão do formulário), ou uma linha na Tabela de Preços do
    convênio informado em ?forma_atendimento=CONVENIO&convenio=<id> (mesma
    trava aplicada no servidor em AgendamentoForm.clean(), pra quem
    desabilitar o JavaScript não conseguir contornar). Devolve também
    'convenios': os convênios que o profissional atende e que dá pra agendar.

    SEM fallback pra "todos os procedimentos da clínica" quando o profissional ainda
    não tem nenhum vinculado: a lista vem vazia e o formulário avisa pra vincular
    primeiro ('sem_vinculo' na resposta). O fallback antigo fazia aparecer na hora de
    agendar procedimentos que não eram do profissional (relatado pela clínica em
    13/10/2026) — mesmo critério que cadastros.views já usava a pedido do cliente.
    """
    profissional = get_object_or_404(Profissional, pk=pk)
    procedimentos = profissional.procedimentos_habilitados.filter(ativo=True)
    sem_vinculo = not procedimentos.exists()

    # Convênios que aparecem no agendamento: só os que o profissional atende e que têm preço
    # para algum procedimento dele (pedido do cliente, 2026-10-14 — ver
    # Profissional.convenios_para_agendar). O procedimento só aparece no convênio em que tem preço.
    convenios = list(profissional.convenios_para_agendar())
    if request.GET.get("forma_atendimento") == "CONVENIO":
        convenio = next((c for c in convenios if str(c.pk) == request.GET.get("convenio")), None)
        procedimentos = (
            profissional.procedimentos_com_preco_no(convenio) if convenio else Procedimento.objects.none()
        )
    elif profissional.cobra_valor_fixo_particular:
        # Valor fixo no particular: só o que está na tabela própria dele (mesma trava de
        # AgendamentoItemFormSet.clean() no servidor).
        procedimentos = procedimentos.filter(valores_fixos__profissional=profissional)
    else:
        procedimentos = procedimentos.filter(valor_particular__isnull=False)

    procedimentos = procedimentos.order_by("nome")
    dados = [
        {"id": p.pk, "nome": p.nome, "permite_repeticao_semanal": p.permite_repeticao_semanal}
        for p in procedimentos
    ]
    # Especialidades deste profissional (principal + adicionais) — usado por
    # agendamento_form.html para mostrar/exigir a escolha de "especialidade deste
    # atendimento" só quando ele tiver 2+ (ver AgendamentoForm.clean()).
    especialidades = [
        {"chave": f"{especialidade}|{especialidade_outra}", "rotulo": especialidade_rotulo(especialidade, especialidade_outra)}
        for especialidade, especialidade_outra in profissional.especialidades_pares
    ]
    return JsonResponse({
        "procedimentos": dados,
        "especialidades": especialidades,
        "sem_vinculo": sem_vinculo,
        "convenios": [{"id": c.pk, "nome": c.nome} for c in convenios],
    })


def _proxima_data_dia_semana(data_base, dia_semana_alvo):
    """dia_semana_alvo segue date.weekday(): Segunda=0 ... Domingo=6."""
    delta = (dia_semana_alvo - data_base.weekday()) % 7
    return data_base + timedelta(days=delta)


def _criar_serie_semanal(base, quantidade, request, comeca_em=1):
    """Cria (quantidade - comeca_em + 1... na prática 'quantidade - 1' quando comeca_em=1) sessões
    semanais a partir de 'base' (um Agendamento já salvo, com 'serie_recorrencia' já preenchido),
    pulando a própria 'base'. Cada sessão criada aqui herda o mesmo 'serie_recorrencia' de 'base' —
    é isso que depois permite, em agendamento_cancelar, cancelar 'esta e todas as próximas' da mesma
    repetição de uma vez. Copia o conjunto INTEIRO de itens de 'base' (procedimento + quantidade)
    pra cada sessão gerada — retorno_de nunca é copiado (nem era antes de existir AgendamentoItem):
    procedimentos com permite_repeticao_semanal=True são sempre do tipo sessão, nunca consulta, e só
    consulta (eh_consulta=True) é elegível a retorno grátis, então essa combinação nunca ocorre na
    prática. Devolve (quantidade_criada, lista_de_datas_puladas_por_conflito)."""
    itens_base = list(base.itens.all())
    criados = 0
    puladas = []
    for i in range(comeca_em, quantidade):
        nova = Agendamento(
            paciente=base.paciente,
            profissional=base.profissional,
            forma_atendimento=base.forma_atendimento,
            convenio=base.convenio,
            observacoes=base.observacoes,
            criado_por=request.user,
            serie_recorrencia=base.serie_recorrencia,
            data_hora_inicio=base.data_hora_inicio + timedelta(weeks=i),
            data_hora_fim=base.data_hora_fim + timedelta(weeks=i),
        )
        # Série semanal só existe pra sessão (ver agendamento_novo) — os itens são copiados
        # logo depois do save, então a checagem de conflito precisa saber disso já aqui.
        nova.eh_sessao = True
        try:
            nova.full_clean()
            nova.save()
            AgendamentoItem.objects.bulk_create(
                AgendamentoItem(agendamento=nova, procedimento=item.procedimento, quantidade=item.quantidade)
                for item in itens_base
            )
            vincular_pacotes(nova)
            criados += 1
        except ValidationError:
            puladas.append(nova.data_hora_inicio)
    return criados, puladas


@login_required
def buscar_pacientes(request):
    """
    Busca de paciente (por nome ou CPF) usada pela caixa "Paciente" da tela de
    agendamento (ver agendamento_form.html) — substitui o antigo <select> com
    todos os pacientes, que ficou inviável depois da importação do sistema antigo
    (ver AgendamentoForm.__init__). Todas as palavras digitadas precisam aparecer
    no nome, em qualquer ordem, sem diferenciar acento/maiúscula; nomes que
    começam com o texto digitado vêm primeiro. Devolve no máximo 30.
    """
    if not (
        request.user.has_perm("accounts.agenda_criar")
        or request.user.has_perm("accounts.agenda_editar")
        or _pode_cadastrar_pacote_manual(request.user)
    ):
        raise PermissionDenied
    # Só quem aparece pra recepção — cadastro do sistema antigo só com o nome e cópia
    # unificada ficam de fora (ver Paciente.filtro_visivel_recepcao).
    ids = buscar_ids_pacientes(request.GET.get("q"), Paciente.filtro_visivel_recepcao(), "recepcao", limite=30)

    por_id = Paciente.objects.select_related("convenio").in_bulk(ids)
    dados = []
    for pk in ids:
        paciente = por_id.get(pk)
        if not paciente:
            continue
        detalhes = []
        if paciente.data_nascimento:
            detalhes.append(f"nasc. {paciente.data_nascimento:%d/%m/%Y}")
        if paciente.cpf:
            detalhes.append(f"CPF {paciente.cpf}")
        dados.append(
            {
                "id": paciente.pk,
                "nome": paciente.nome_completo,
                "detalhes": " · ".join(detalhes),
                "convenio_id": paciente.convenio_id,
                "convenio_nome": paciente.convenio.nome if paciente.convenio_id else "",
            }
        )
    return JsonResponse({"pacientes": dados})


def _avisar_pacotes(request, agendamento):
    """Aviso pra recepção dos itens que já estão pagos num pacote (ver
    agenda.models.vincular_pacotes) — mesmo espírito do aviso de retorno grátis."""
    for item in agendamento.itens.filter(pacote_vendido__isnull=False).select_related("procedimento", "pacote_vendido"):
        messages.info(
            request,
            f"'{item.procedimento.nome}' já está pago no pacote '{item.pacote_vendido.nome}' do paciente "
            f"({item.pacote_vendido.saldo_resumo}) — este item não gera cobrança.",
        )


def _avisar_retorno_alem_do_limite(request, agendamento, formset):
    """Retorno além do limite diário (ver AgendamentoItemFormSet._limitar_retornos_por_dia):
    foi salvo como encaixe — com o motivo já escrito, ou (edição no dia do atendimento) sem
    justificativa, pra justificar depois. Avisa a recepção."""
    justificado = getattr(formset, "retorno_encaixe_justificado", None)
    if justificado is not None:
        messages.info(
            request,
            f"{agendamento.profissional.nome} já tinha {justificado} retorno(s) nesse dia (limite de "
            f"{settings.AGENDA_RETORNOS_POR_DIA}) — este retorno foi marcado como ENCAIXE, com o motivo "
            "das observações.",
        )
    ja_marcados = getattr(formset, "retorno_alem_do_limite", None)
    if ja_marcados is None:
        return
    messages.warning(
        request,
        f"{agendamento.profissional.nome} já tem {ja_marcados} retorno(s) em "
        f"{timezone.localtime(agendamento.data_hora_inicio):%d/%m/%Y} (limite de "
        f"{settings.AGENDA_RETORNOS_POR_DIA} por dia) — este retorno foi marcado como ENCAIXE. "
        "Justifique depois: edite o agendamento e escreva o motivo nas observações.",
    )


def _resumo_erros(form, formset):
    """Todos os motivos que impediram salvar o agendamento, pra mostrar no TOPO do formulário
    (pedido da clínica, 23/10/2026 — "a alteração não salva"): o aviso ficava só ao lado do
    campo, muitas vezes abaixo da dobra da tela, ou num campo escondido pelo JavaScript
    (horário da sessão, especialidade, convênio em "Particular") — a tela voltava igual e
    parecia que não tinha gravado."""
    if not form.is_bound or (not form.errors and not formset.total_error_count()):
        return []
    resumo = list(form.non_field_errors())
    for nome, erros in form.errors.items():
        if nome == "__all__":
            continue
        rotulo = form.fields[nome].label if nome in form.fields else nome
        resumo += [f"{rotulo}: {erro}" for erro in erros]
    resumo += list(formset.non_form_errors())
    for indice, item_form in enumerate(formset.forms, start=1):
        for nome, erros in item_form.errors.items():
            resumo += [f"Procedimento (linha {indice}): {erro}" for erro in erros]
    return resumo


@permission_required("accounts.agenda_criar", raise_exception=True)
def agendamento_novo(request):
    contexto_extra = {}
    if request.method == "POST":
        form = AgendamentoForm(request.POST)
        formset = AgendamentoItemFormSet(request.POST, instance=form.instance, agendamento_form=form)
        form_valido = form.is_valid()
        formset_valido = formset.is_valid()

        if (
            form_valido
            and formset_valido
            and form.cleaned_data.get("repetir_semanalmente")
            and formset.algum_item_nao_repete
        ):
            form.add_error(
                "repetir_semanalmente",
                "Um ou mais procedimentos escolhidos são do tipo consulta e não se repetem "
                "automaticamente (só procedimentos marcados como 'sessão' permitem) — desmarque "
                "'repetir semanalmente' ou remova-os.",
            )
            form_valido = False

        if form_valido and formset_valido:
            with transaction.atomic():
                agendamento = form.save(commit=False)
                agendamento.criado_por = request.user
                agendamento.save()
                formset.instance = agendamento
                formset.save()
                vincular_pacotes(agendamento)

            if agendamento.carteirinha_vencida_no_atendimento:
                messages.warning(
                    request,
                    f"Atenção: a carteirinha de '{agendamento.paciente.nome_completo}' está vencida "
                    f"(validade {agendamento.paciente.validade_carteirinha:%d/%m/%Y}) e este atendimento foi "
                    "marcado como convênio.",
                )

            for item in agendamento.itens.filter(retorno_de__isnull=False).select_related(
                "procedimento", "retorno_de__agendamento"
            ):
                messages.info(
                    request,
                    f"'{item.procedimento.nome}' detectado automaticamente como RETORNO da consulta de "
                    f"{timezone.localtime(item.retorno_de.agendamento.data_hora_inicio):%d/%m/%Y} — este "
                    "item não vai gerar cobrança (nem particular, nem convênio).",
                )

            _avisar_pacotes(request, agendamento)

            aviso_limite = _verificar_limite_diario(agendamento)
            if aviso_limite:
                messages.warning(request, aviso_limite)
            _avisar_retorno_alem_do_limite(request, agendamento, formset)

            repetir = form.cleaned_data.get("repetir_semanalmente")
            quantidade = form.cleaned_data.get("quantidade_sessoes") or 1
            total_criados = 1
            todas_puladas = []
            serie_segundo_dia = None

            if repetir and quantidade > 1:
                # Cada "cadeia" semanal (a principal, e a do segundo dia se houver) ganha seu
                # próprio identificador de série — é o que depois permite cancelar "esta e as
                # próximas" de uma repetição sem mexer na outra cadeia (ver serie_recorrencia
                # no modelo e agendamento_cancelar).
                agendamento.serie_recorrencia = uuid.uuid4()
                agendamento.save(update_fields=["serie_recorrencia"])

                criados, puladas = _criar_serie_semanal(agendamento, quantidade, request, comeca_em=1)
                total_criados += criados
                todas_puladas.extend(puladas)

                # Segundo dia da semana opcional (ex.: paciente vem terça E sexta,
                # com horário diferente em cada dia).
                if form.cleaned_data.get("tem_segundo_dia"):
                    dia_base = timezone.localtime(agendamento.data_hora_inicio).date()
                    primeira_data_segundo = _proxima_data_dia_semana(
                        dia_base, int(form.cleaned_data["segundo_dia_semana"])
                    )
                    duracao_segundo = form.cleaned_data.get("segundo_duracao_minutos") or (
                        agendamento.data_hora_fim - agendamento.data_hora_inicio
                    ).total_seconds() // 60
                    inicio_ingenuo = datetime.combine(
                        primeira_data_segundo, form.cleaned_data["segundo_horario"]
                    )
                    inicio_segundo = (
                        timezone.make_aware(inicio_ingenuo) if timezone.is_naive(inicio_ingenuo) else inicio_ingenuo
                    )
                    fim_segundo = inicio_segundo + timedelta(minutes=int(duracao_segundo))

                    primeira_do_segundo_dia = Agendamento(
                        paciente=agendamento.paciente,
                        profissional=agendamento.profissional,
                        forma_atendimento=agendamento.forma_atendimento,
                        convenio=agendamento.convenio,
                        observacoes=agendamento.observacoes,
                        criado_por=request.user,
                        serie_recorrencia=uuid.uuid4(),
                        data_hora_inicio=inicio_segundo,
                        data_hora_fim=fim_segundo,
                    )
                    primeira_do_segundo_dia.eh_sessao = True
                    try:
                        primeira_do_segundo_dia.full_clean()
                        primeira_do_segundo_dia.save()
                        AgendamentoItem.objects.bulk_create(
                            AgendamentoItem(
                                agendamento=primeira_do_segundo_dia,
                                procedimento=item.procedimento,
                                # O segundo dia pode ter outra duração — sessão por convênio
                                # recalcula o nº de guias por ela (1h = 2, ver
                                # AgendamentoItem.quantidade_guias_sessao).
                                quantidade=AgendamentoItem.quantidade_guias_sessao(
                                    item.procedimento, agendamento.forma_atendimento, duracao_segundo
                                )
                                or item.quantidade,
                            )
                            for item in agendamento.itens.all()
                        )
                        vincular_pacotes(primeira_do_segundo_dia)
                        serie_segundo_dia = primeira_do_segundo_dia.serie_recorrencia
                        total_criados += 1
                        criados_2, puladas_2 = _criar_serie_semanal(
                            primeira_do_segundo_dia, quantidade, request, comeca_em=1
                        )
                        total_criados += criados_2
                        todas_puladas.extend(puladas_2)
                    except ValidationError:
                        todas_puladas.append(inicio_segundo)

            if repetir and quantidade > 1:
                messages.success(
                    request,
                    f"{total_criados} sessões agendadas para '{agendamento.paciente.nome_completo}'.",
                )
                series = [serie for serie in (agendamento.serie_recorrencia, serie_segundo_dia) if serie]
                cobertas = (
                    Agendamento.objects.filter(serie_recorrencia__in=series, itens__pacote_vendido__isnull=False)
                    .exclude(pk=agendamento.pk)
                    .distinct()
                    .count()
                )
                if cobertas:
                    messages.info(
                        request,
                        f"{cobertas} das outras sessões também já estão pagas pelo pacote do paciente "
                        "(as que passarem do saldo do pacote serão cobradas normalmente).",
                    )
                if todas_puladas:
                    datas_puladas = ", ".join(
                        d.strftime("%d/%m/%Y %H:%M") for d in sorted(todas_puladas)
                    )
                    messages.warning(
                        request,
                        f"{len(todas_puladas)} sessão(ões) não foram criadas por conflito de horário do "
                        f"profissional: {datas_puladas}. Agende-as manualmente em outro horário.",
                    )
            else:
                messages.success(request, "Agendamento criado com sucesso.")

            return redirect(_url_agenda_dia(agendamento))
    else:
        inicial = {}
        data_inicial = None
        data_str = request.GET.get("data")
        if data_str:
            data_inicial = parse_date(data_str)
            if data_inicial:
                inicial["data"] = data_inicial
        profissional_str = request.GET.get("profissional")
        if profissional_str:
            inicial["profissional"] = profissional_str
        hora_str = request.GET.get("hora")
        if hora_str:
            hora_inicial = parse_time(hora_str)
            if hora_inicial:
                inicial["hora_inicio"] = hora_inicial

        # ?repetir=<pk>: "Repetir último agendamento" (Central / botão deste formulário) —
        # puxa tudo do agendamento anterior menos paciente, data e horário. Só vale se o
        # profissional pedido (o do horário clicado) é o mesmo daquele agendamento.
        ultimo_agendamento = _ultimo_agendamento_do_usuario(request.user)
        repetindo = None
        itens_iniciais = []
        repetir_str = request.GET.get("repetir") or ""
        if (
            ultimo_agendamento
            and repetir_str == str(ultimo_agendamento.pk)
            and (not profissional_str or profissional_str == str(ultimo_agendamento.profissional_id))
        ):
            repetindo = ultimo_agendamento
            copiados, itens_iniciais = _iniciais_repetindo(repetindo, com_turno=not inicial.get("hora_inicio"))
            inicial.update(copiados)
        url_repetir_ultimo = ""
        if ultimo_agendamento and not repetindo:
            parametros = request.GET.copy()
            parametros["repetir"] = ultimo_agendamento.pk
            parametros["profissional"] = ultimo_agendamento.profissional_id
            url_repetir_ultimo = "?" + parametros.urlencode()

        form = AgendamentoForm(initial=inicial, data_travada=data_inicial)
        formset = AgendamentoItemFormSet(
            instance=form.instance, agendamento_form=form, initial=itens_iniciais or None
        )
        if len(itens_iniciais) > 1:
            formset.extra = len(itens_iniciais) - 1
        contexto_extra = {
            "repetindo": repetindo,
            "ultimo_agendamento": None if repetindo else ultimo_agendamento,
            "url_repetir_ultimo": url_repetir_ultimo,
        }
    return render(
        request,
        "agenda/agendamento_form.html",
        {
            "form": form,
            "formset": formset,
            "erros_resumo": _resumo_erros(form, formset),
            "titulo": "Novo agendamento",
            "voltar_url": reverse("agenda:agenda_dia"),
            **contexto_extra,
        },
    )


@permission_required("accounts.agenda_editar", raise_exception=True)
def agendamento_editar(request, pk):
    agendamento = get_object_or_404(Agendamento, pk=pk)
    if request.method == "POST":
        form = AgendamentoForm(request.POST, instance=agendamento)
        formset = AgendamentoItemFormSet(request.POST, instance=agendamento, agendamento_form=form)
        form_valido = form.is_valid()
        formset_valido = formset.is_valid()
        if form_valido and formset_valido:
            with transaction.atomic():
                agendamento = form.save()
                formset.save()
                vincular_pacotes(agendamento)

            for item in agendamento.itens.filter(retorno_de__isnull=False).select_related(
                "procedimento", "retorno_de__agendamento"
            ):
                messages.info(
                    request,
                    f"'{item.procedimento.nome}' detectado automaticamente como retorno da consulta de "
                    f"{timezone.localtime(item.retorno_de.agendamento.data_hora_inicio):%d/%m/%Y}.",
                )
            _avisar_pacotes(request, agendamento)
            _avisar_retorno_alem_do_limite(request, agendamento, formset)
            messages.success(request, "Agendamento atualizado com sucesso.")
            return redirect(_url_agenda_dia(agendamento))
    else:
        form = AgendamentoForm(instance=agendamento)
        formset = AgendamentoItemFormSet(instance=agendamento, agendamento_form=form)
    return render(
        request,
        "agenda/agendamento_form.html",
        {
            "form": form,
            "formset": formset,
            "erros_resumo": _resumo_erros(form, formset),
            "titulo": f"Editar agendamento — {agendamento.paciente.nome_completo}",
            "voltar_url": _url_agenda_dia(agendamento),
        },
    )


def _tem_pagamento_pendente(alvos):
    """Cancelar só é permitido depois que o financeiro já estornou qualquer pagamento
    particular de verdade (não cortesia) associado — decisão do cliente: evita cancelar um
    atendimento "por baixo" de um pagamento que ainda não foi devolvido/tratado (ver
    financeiro.views.pagamento_estornar, que exige financeiro_estornar_pagamento)."""
    return (
        PagamentoParticular.objects.filter(agendamento__in=alvos, estornado=False)
        .exclude(forma_pagamento=Agendamento.FormaPagamento.CORTESIA)
        .exists()
    )


@permission_required("accounts.agenda_editar", raise_exception=True)
def agendamento_cancelar(request, pk):
    """
    Cancelamento de AGENDA — só o que ainda não foi autorizado (pedido da clínica,
    23/10/2026: o já autorizado se cancela em Autorizações, com permissão própria — ver
    agendamento_cancelar_autorizado). Quando faz parte de uma série recorrente (criada com
    "repetir semanalmente" — ver serie_recorrencia no modelo), oferece a opção de cancelar
    só este ou "este e todos os próximos da mesma repetição" de uma vez — bem útil quando
    o paciente desmarca o tratamento inteiro. Sessões da série já autorizadas ficam de fora.
    """
    agendamento = get_object_or_404(Agendamento, pk=pk)
    destino = _url_agenda_dia(agendamento)

    if agendamento.ja_autorizado:
        messages.error(
            request,
            f"O atendimento de '{agendamento.paciente.nome_completo}' já foi autorizado — o cancelamento "
            "dele é feito em Agenda → Autorizações, por quem tem permissão para isso.",
        )
        return redirect(destino)

    proximos_da_serie = None
    if agendamento.serie_recorrencia:
        proximos_da_serie = (
            Agendamento.objects.filter(
                serie_recorrencia=agendamento.serie_recorrencia,
                data_hora_inicio__gte=agendamento.data_hora_inicio,
            )
            .exclude(status=Agendamento.Status.CANCELADO)
            .exclude(status__in=Agendamento.STATUS_JA_AUTORIZADO)
            .order_by("data_hora_inicio", "pk")
        )

    if request.method == "POST":
        modo = request.POST.get("modo") or "somente_este"
        if modo == "serie_futura" and proximos_da_serie is not None:
            alvos = list(proximos_da_serie)
        else:
            alvos = [agendamento]

        if _tem_pagamento_pendente(alvos):
            messages.error(
                request,
                "Não é possível cancelar: há pagamento particular registrado que ainda não foi "
                "estornado. Peça para o financeiro estornar o pagamento antes de cancelar.",
            )
            return redirect(destino)

        # Pacote incluído na tela de pagamento mas nunca pago: a venda não aconteceu.
        PacoteVendido.desfazer_nao_pagos(alvos)

        if modo == "serie_futura" and proximos_da_serie is not None:
            quantidade = len(alvos)
            Agendamento.objects.filter(pk__in=[a.pk for a in alvos]).update(status=Agendamento.Status.CANCELADO)
            messages.success(
                request,
                f"{quantidade} atendimento(s) desta repetição, a partir de "
                f"{agendamento.data_hora_inicio:%d/%m/%Y}, foram cancelados.",
            )
        else:
            agendamento.status = Agendamento.Status.CANCELADO
            agendamento.save(update_fields=["status"])
            messages.success(request, "Agendamento cancelado.")

        for alvo in alvos:
            LogAuditoria.registrar(
                request,
                request.user,
                f"Cancelou agendamento #{alvo.pk} de '{alvo.paciente.nome_completo}' "
                f"({alvo.data_hora_inicio:%d/%m/%Y %H:%M})",
            )
        return redirect(destino)

    return render(
        request,
        "agenda/agendamento_cancelar.html",
        {
            "agendamento": agendamento,
            "voltar_url": destino,
            # Só faz sentido oferecer a escolha se sobrar mais alguma sessão
            # futura além desta mesma (senão as duas opções dariam no mesmo).
            "quantidade_futuros_serie": proximos_da_serie.count() if proximos_da_serie is not None else 0,
        },
    )


@permission_required("accounts.agenda_cancelar_autorizado", raise_exception=True)
def agendamento_cancelar_autorizado(request, pk):
    """
    Cancelamento de atendimento JÁ AUTORIZADO (Autorizado/Realizado) — pedido da clínica,
    23/10/2026: sai da agenda e fica na tela Autorizações, restrito a quem tem a permissão
    'agenda_cancelar_autorizado' (marcada por perfil). Exige motivo, que vai pro log de
    auditoria e pras observações do agendamento. Guia de sessões do plano volta a ter saldo
    sozinha (só Autorizado/Realizado consome — ver GuiaConvenio).

    Bloqueado quando o atendimento já entrou no financeiro do convênio (lote de faturamento,
    recebido ou repasse pago) — aí precisa desfazer lá antes — e, como no cancelamento de
    agenda, com pagamento particular ainda não estornado.
    """
    agendamento = get_object_or_404(Agendamento.objects.select_related("paciente", "profissional", "convenio"), pk=pk)
    destino = _destino_seguro(request, reverse("agenda:autorizacoes"))

    if not agendamento.ja_autorizado:
        messages.error(
            request,
            f"O atendimento de '{agendamento.paciente.nome_completo}' não está autorizado — cancele pela agenda.",
        )
        return redirect(destino)

    impedimento = None
    if agendamento.lotes_faturamento.exists():
        impedimento = "ele já foi incluído em um lote de faturamento do convênio. Retire-o do lote antes."
    elif agendamento.convenio_recebido:
        impedimento = "o pagamento do convênio já foi baixado como recebido. Estorne a baixa no financeiro antes."
    elif agendamento.convenio_repasse_pago:
        impedimento = "o repasse deste atendimento ao profissional já foi pago."
    elif _tem_pagamento_pendente([agendamento]):
        impedimento = (
            "há pagamento particular registrado que ainda não foi estornado. Peça para o financeiro "
            "estornar o pagamento antes."
        )

    erro_motivo = None
    if request.method == "POST" and not impedimento:
        motivo = (request.POST.get("motivo") or "").strip()
        if not motivo:
            erro_motivo = "Informe o motivo do cancelamento."
        else:
            status_anterior = agendamento.get_status_display()
            agora = timezone.localtime()
            quem = request.user.get_full_name() or request.user.username
            nota = f"Cancelado após autorização ({status_anterior}) por {quem} em {agora:%d/%m/%Y %H:%M}: {motivo}"
            agendamento.observacoes = f"{agendamento.observacoes}\n{nota}".strip()
            agendamento.status = Agendamento.Status.CANCELADO
            with transaction.atomic():
                PacoteVendido.desfazer_nao_pagos([agendamento])
                agendamento.save(update_fields=["status", "observacoes"])
            LogAuditoria.registrar(
                request,
                request.user,
                f"Cancelou atendimento JÁ AUTORIZADO #{agendamento.pk} ({status_anterior}) de "
                f"'{agendamento.paciente.nome_completo}' "
                f"({timezone.localtime(agendamento.data_hora_inicio):%d/%m/%Y %H:%M}). Motivo: {motivo}",
            )
            messages.success(request, f"Atendimento de '{agendamento.paciente.nome_completo}' cancelado.")
            return redirect(destino)

    return render(
        request,
        "agenda/agendamento_cancelar_autorizado.html",
        {
            "agendamento": agendamento,
            "impedimento": impedimento,
            "erro_motivo": erro_motivo,
            "motivo": request.POST.get("motivo", ""),
            "destino": destino,
        },
    )


@permission_required("accounts.agenda_editar", raise_exception=True)
def agendamento_excluir(request, pk):
    """
    Exclusão definitiva — só permitida quando o agendamento já está Cancelado
    (mesmo padrão usado para excluir Usuario: primeiro cancela/desativa,
    só depois é possível excluir de vez). Existe para não deixar a agenda
    cheia de linhas canceladas acumuladas ao longo do tempo.

    Quando faz parte de uma série recorrente (serie_recorrencia), também
    oferece excluir "esta e todas as próximas já canceladas" de uma vez — só
    afeta sessões que já estejam Canceladas (as que ainda estiverem ativas
    dentro da mesma série ficam de fora, por segurança: exclusão definitiva
    nunca cancela sozinha, só apaga o que já foi cancelado).
    """
    agendamento = get_object_or_404(Agendamento, pk=pk)
    destino = _url_agenda_dia(agendamento)

    if agendamento.status != Agendamento.Status.CANCELADO:
        messages.error(request, "Só é possível excluir um agendamento que já esteja Cancelado. Cancele primeiro.")
        return redirect(destino)

    proximos_cancelados_serie = None
    if agendamento.serie_recorrencia:
        proximos_cancelados_serie = Agendamento.objects.filter(
            serie_recorrencia=agendamento.serie_recorrencia,
            data_hora_inicio__gte=agendamento.data_hora_inicio,
            status=Agendamento.Status.CANCELADO,
        ).order_by("data_hora_inicio", "pk")

    if request.method == "POST":
        modo = request.POST.get("modo") or "somente_este"
        if modo == "serie_futura" and proximos_cancelados_serie is not None:
            quantidade, _ = proximos_cancelados_serie.delete()
            messages.success(
                request,
                f"{quantidade} agendamento(s) cancelado(s) desta repetição, a partir de "
                f"{agendamento.data_hora_inicio:%d/%m/%Y}, foram excluídos definitivamente.",
            )
        else:
            nome_paciente = agendamento.paciente.nome_completo
            agendamento.delete()
            messages.success(request, f"Agendamento cancelado de '{nome_paciente}' excluído definitivamente.")
        return redirect(destino)

    return render(
        request,
        "agenda/agendamento_excluir.html",
        {
            "agendamento": agendamento,
            "voltar_url": destino,
            "quantidade_futuros_cancelados_serie": (
                proximos_cancelados_serie.count() if proximos_cancelados_serie is not None else 0
            ),
        },
    )


def _preencher_valor_convenio(agendamento):
    """
    Preenche o valor a receber do convênio por ITEM (cada item pode ter um
    procedimento diferente, com preço diferente na Tabela de Preços) e depois
    soma pro total do agendamento, que continua sendo a coluna que Financeiro
    (Contas a receber, LoteFaturamento) usa. Mesma regra de sempre, por item:
    retorno grátis força 0,00 SEMPRE (hard lock, nunca consulta
    Convenio.valor_para); os demais só preenchem se ainda None, nunca
    sobrescrevem um valor já congelado/corrigido à mão.

    Só faz algo em forma CONVÊNIO. Salva os itens; o total fica em
    agendamento.valor_convenio e quem chama salva o agendamento — retorna True
    quando esse total mudou.
    """
    if agendamento.forma_atendimento != Agendamento.FormaAtendimento.CONVENIO:
        return False
    itens = list(agendamento.itens.select_related("procedimento"))
    itens_alterados = []
    for item in itens:
        if item.eh_retorno:
            if item.valor_convenio != Decimal("0.00"):
                item.valor_convenio = Decimal("0.00")
                itens_alterados.append(item)
        elif item.valor_convenio is None and agendamento.convenio_id:
            valor_unitario = agendamento.convenio.valor_para(item.procedimento, agendamento.profissional)
            if valor_unitario is not None:
                # quantidade_produzida: sessão com 2ª guia ainda não autorizada só conta a 1ª
                # (ver AgendamentoItem.controla_guias e guias_pendentes).
                item.valor_convenio = (valor_unitario * item.quantidade_produzida).quantize(Decimal("0.01"))
                itens_alterados.append(item)
    if itens_alterados:
        AgendamentoItem.objects.bulk_update(itens_alterados, ["valor_convenio"])

    if itens and all(item.valor_convenio is not None for item in itens):
        novo_total = sum((item.valor_convenio for item in itens), Decimal("0.00"))
        if agendamento.valor_convenio != novo_total:
            agendamento.valor_convenio = novo_total
            return True
    return False


@login_required
@transaction.atomic
def agendamento_mudar_status(request, pk, novo_status):
    """
    Usada pelos botões 'Pré-confirmar', 'Confirmar', 'Faltou', 'Pré-autorizar'/
    'Autorizar' e 'Realizado'. Cada etapa tem seu dono:

    - Pré-confirmar / Confirmar / Faltou: livre pra quem tem 'agenda_editar'
      (recepção) — sem checagem de status atual, como sempre foi.
    - Pré-autorizado / Autorizado: exclusivo de quem tem 'fila_autorizar_recepcao',
      só a partir de 'Confirmado'. Pré-autorizado (encaminha pro financeiro) só
      faz sentido pra atendimento PARTICULAR, ou CONVÊNIO com
      'permite_pagamento_na_hora' marcado quando a recepção decide, na
      hora, que esse atendimento específico vai ser pago no ato — nesses casos,
      só dá pra chegar em 'Autorizado' depois que o financeiro registrar o
      pagamento (ver agendamento_confirmar_pagamento; pedir 'Autorizado' direto
      com o agendamento em 'Pré-autorizado' só redireciona pra lá). Autorizado
      direto (sem passar pelo financeiro) é o caminho normal de convênio —
      tabela fixa, ou convênio-com-pagamento-na-hora quando a recepção decide faturar
      pelo convênio em vez de cobrar na hora.
    - Realizado (marcar/desmarcar "atendido"): exclusivo de quem tem
      'atendimento_realizar' (o profissional, ou o Administrador) — só a partir
      de 'Autorizado' (pra qualquer forma de atendimento, decisão do cliente:
      recepção/financeiro sempre autorizam antes do profissional atender), e só
      no PRÓPRIO atendimento quando a pessoa não tiver 'agenda_editar' também.
      "Desmarcar" é o caminho inverso (Realizado → Autorizado de novo).

    Tudo travado aqui no back-end, não só escondido no template — sem isso,
    bastaria digitar a URL com outro status pra contornar as telas.

    No POST o agendamento fica travado até o fim (mesmo motivo de agendamento_confirmar_pagamento:
    clique duplo gravava duas vezes — ex.: a linha de cortesia do retorno grátis). O segundo
    envio, que já encontra a mudança feita, só volta pra tela sem mexer em nada.
    """
    pode_editar_agenda = request.user.has_perm("accounts.agenda_editar")
    pode_atender = request.user.has_perm("accounts.atendimento_realizar")
    pode_autorizar_recepcao = request.user.has_perm("accounts.fila_autorizar_recepcao")

    # CANCELADO só pelas telas próprias (agendamento_cancelar / agendamento_cancelar_autorizado),
    # que conferem a etapa, o pagamento e a permissão de cada caso.
    if novo_status not in dict(Agendamento.Status.choices) or novo_status == Agendamento.Status.CANCELADO:
        raise PermissionDenied

    consulta = Agendamento.objects.select_for_update() if request.method == "POST" else Agendamento.objects
    agendamento = get_object_or_404(consulta, pk=pk)

    ja_feito = agendamento.status == novo_status or (
        novo_status == Agendamento.Status.PRE_AUTORIZADO
        and agendamento.status in (Agendamento.Status.AUTORIZADO, Agendamento.Status.REALIZADO)
    )
    if request.method == "POST" and ja_feito:
        destino = request.META.get("HTTP_REFERER") or reverse("agenda:agenda_dia")
        return redirect(destino.split("#")[0] + f"#agendamento-{agendamento.pk}")

    if novo_status == Agendamento.Status.REALIZADO or (
        agendamento.status == Agendamento.Status.REALIZADO and novo_status == Agendamento.Status.AUTORIZADO
    ):
        if not pode_atender:
            raise PermissionDenied
        if novo_status == Agendamento.Status.REALIZADO and agendamento.status != Agendamento.Status.AUTORIZADO:
            raise PermissionDenied
        if not pode_editar_agenda and agendamento.profissional_id != request.user.profissional_id:
            raise PermissionDenied

    elif novo_status in (Agendamento.Status.PRE_AUTORIZADO, Agendamento.Status.AUTORIZADO):
        if agendamento.status == Agendamento.Status.PRE_AUTORIZADO:
            # Só a tela de registrar pagamento avança de Pré-autorizado pra Autorizado.
            return redirect(reverse("agenda:agendamento_confirmar_pagamento", args=[agendamento.pk]))
        if not pode_autorizar_recepcao:
            raise PermissionDenied
        if agendamento.status != Agendamento.Status.CONFIRMADO:
            raise PermissionDenied
        permite_pagamento_na_hora = bool(agendamento.convenio_id and agendamento.convenio.permite_pagamento_na_hora)
        if novo_status == Agendamento.Status.PRE_AUTORIZADO:
            eh_particular = agendamento.forma_atendimento == Agendamento.FormaAtendimento.PARTICULAR
            if not (eh_particular or permite_pagamento_na_hora):
                raise PermissionDenied
        elif agendamento.forma_atendimento == Agendamento.FormaAtendimento.PARTICULAR:
            # Autorizado direto (sem financeiro) nunca vale pra particular.
            raise PermissionDenied
        else:
            # Convênio: sempre pela tela de autorização (observação, nº da guia, guia de
            # sessões com saldo — ver agendamento_autorizar).
            url = reverse("agenda:agendamento_autorizar", args=[agendamento.pk])
            origem = urlsplit(request.META.get("HTTP_REFERER") or "")
            if origem.path:
                url += "?" + urlencode({"proximo": origem.path + (f"?{origem.query}" if origem.query else "")})
            return redirect(url)

    else:
        if not pode_editar_agenda:
            raise PermissionDenied
        if novo_status == Agendamento.Status.FALTOU and agendamento.ja_autorizado:
            # "Faltou" também tira da produção — depois de autorizado vale a mesma regra do
            # cancelamento (pedido da clínica, 23/10/2026): só em Autorizações, com permissão.
            messages.error(
                request,
                f"O atendimento de '{agendamento.paciente.nome_completo}' já foi autorizado — não dá para "
                "marcar falta. Se for o caso, cancele em Agenda → Autorizações (com permissão para isso).",
            )
            return redirect((request.META.get("HTTP_REFERER") or reverse("agenda:agenda_dia")).split("#")[0])

    if request.method == "POST":
        status_anterior = agendamento.status
        agendamento.status = novo_status
        campos_alterados = ["status"]
        coberto_por_pacote = False

        if (
            novo_status == Agendamento.Status.PRE_AUTORIZADO
            and status_anterior == Agendamento.Status.CONFIRMADO
            and agendamento.encaminhado_pagamento_em is None
        ):
            # A vez na fila de chamada conta daqui, não do pagamento (ver momento_na_fila).
            agendamento.encaminhado_pagamento_em = timezone.now()
            campos_alterados.append("encaminhado_pagamento_em")

        if novo_status == Agendamento.Status.PRE_AUTORIZADO and status_anterior == Agendamento.Status.CONFIRMADO:
            # Prioridade marcada no botão "Encaminhar" (ver Agendamento.motivo_prioridade) —
            # valor fora da lista conta como normal.
            motivo = request.POST.get("motivo_prioridade", "")
            agendamento.motivo_prioridade = motivo if motivo in _MOTIVOS_PRIORIDADE else ""
            campos_alterados.append("motivo_prioridade")

        if (
            novo_status == Agendamento.Status.PRE_AUTORIZADO
            and status_anterior == Agendamento.Status.CONFIRMADO
            and agendamento.forma_atendimento == Agendamento.FormaAtendimento.PARTICULAR
        ):
            # Pacote (ver agenda.models.PacoteVendido): o saldo é conferido de novo aqui (pode
            # ter mudado desde o agendamento — falta, pacote comprado depois...). Se tudo já está
            # pago no pacote, não há o que o financeiro cobrar nem repassar: autoriza direto.
            # Pacote do sistema antigo ainda a pagar: vai ao financeiro, que pode cobrá-lo agora.
            vincular_pacotes(agendamento)
            if agendamento.coberto_por_pacote and not agendamento.usa_pacote_a_pagar:
                coberto_por_pacote = True
                agendamento.status = novo_status = Agendamento.Status.AUTORIZADO
                agendamento.itens.update(valor_cobrado=Decimal("0.00"))

        so_retorno = False
        if (
            novo_status == Agendamento.Status.PRE_AUTORIZADO
            and status_anterior == Agendamento.Status.CONFIRMADO
            and agendamento.so_retorno_gratis
        ):
            # Retorno grátis — automático ou do sistema antigo (pedido da clínica, 23/10/2026): não há o que
            # cobrar, então não passa pela fila do financeiro — autoriza direto. Grava a mesma
            # linha de cortesia R$ 0,00 que o financeiro gravaria ao "Confirmar retorno e
            # autorizar" (ver ConfirmarPagamentoForm), pra relatórios contarem igual.
            so_retorno = True
            agendamento.status = novo_status = Agendamento.Status.AUTORIZADO
            agendamento.itens.update(valor_cobrado=Decimal("0.00"))
            PagamentoParticular.objects.create(
                agendamento=agendamento, forma_pagamento=Agendamento.FormaPagamento.CORTESIA, valor=Decimal("0")
            )

        if novo_status == Agendamento.Status.AUTORIZADO and status_anterior == Agendamento.Status.CONFIRMADO:
            # Só o pacote já pago e o retorno grátis caem aqui (convênio vai por agendamento_autorizar; o outro
            # caminho pra AUTORIZADO é agendamento_confirmar_pagamento, abaixo). Sem o filtro
            # por status_anterior, "Desmarcar" (REALIZADO -> AUTORIZADO de novo) regravaria
            # autorizado_em pra agora, jogando o paciente pro fim da ordem de chamada só por
            # causa de um clique desfeito.
            agendamento.autorizado_em = timezone.now()
            agendamento.autorizado_por = request.user
            campos_alterados += ["autorizado_em", "autorizado_por"]

        # Autorizado já conta como produção (ver Agendamento.STATUS_PRODUCAO), então o
        # valor do convênio é preenchido já na autorização — não mais só ao marcar
        # "atendido". Continua rodando em Realizado também (idempotente) pra cobrir
        # quem foi autorizado antes desta regra.
        if novo_status in Agendamento.STATUS_PRODUCAO and _preencher_valor_convenio(agendamento):
            campos_alterados.append("valor_convenio")

        agendamento.save(update_fields=campos_alterados)
        if coberto_por_pacote:
            LogAuditoria.registrar(
                request,
                request.user,
                f"Autorizou '{agendamento.paciente.nome_completo}' pelo pacote já pago (sem passar pelo financeiro)",
            )
            messages.success(
                request,
                f"'{agendamento.paciente.nome_completo}' já pagou este atendimento no pacote — autorizado direto, "
                "sem passar pelo financeiro.",
            )
        elif so_retorno:
            LogAuditoria.registrar(
                request,
                request.user,
                f"Autorizou '{agendamento.paciente.nome_completo}' — retorno grátis (sem passar pelo financeiro)",
            )
            messages.success(
                request,
                f"'{agendamento.paciente.nome_completo}' é retorno, sem cobrança — autorizado direto, "
                "sem passar pelo financeiro.",
            )
        else:
            messages.success(
                request,
                f"Situação do agendamento de '{agendamento.paciente.nome_completo}' atualizada para "
                f"{agendamento.get_status_display()}.",
            )
    destino = request.META.get("HTTP_REFERER") or reverse("agenda:agenda_dia")
    # Volta pra mesma linha da lista (âncora id="agendamento-<pk>" na Agenda do dia), não pro
    # topo da página — a recepção perdia de vista quem acabou de pré-confirmar/confirmar.
    # Tela sem essa âncora simplesmente ignora o "#...".
    return redirect(destino.split("#")[0] + f"#agendamento-{agendamento.pk}")


def _destino_seguro(request, padrao):
    destino = request.POST.get("proximo") or request.GET.get("proximo") or padrao
    if not destino.startswith("/") or destino.startswith("//"):
        return padrao
    return destino


def _guias_do_item(item, data):
    """(abertas, encerradas) — guias de sessões do paciente para este convênio/procedimento.
    Aberta = não vencida na data do atendimento e com saldo para a sessão inteira (1h = 2)."""
    agendamento = item.agendamento
    abertas, encerradas = [], []
    guias = GuiaConvenio.objects.filter(
        paciente_id=agendamento.paciente_id,
        convenio_id=agendamento.convenio_id,
        procedimento_id=item.procedimento_id,
    ).order_by("criada_em")
    for guia in guias:
        if not guia.vencida_em(data) and guia.saldo >= item.quantidade:
            abertas.append(guia)
        else:
            encerradas.append(guia)
    return abertas, encerradas


class _GuiaRecusada(Exception):
    def __init__(self, form, mensagem):
        super().__init__(mensagem)
        self.form = form
        self.mensagem = mensagem


@permission_required("accounts.fila_autorizar_recepcao", raise_exception=True)
def agendamento_autorizar(request, pk):
    """
    Autorizar atendimento por CONVÊNIO (pedido da clínica, 15/10/2026 — antes era um clique
    direto). Registra quem autorizou, uma observação livre e o número da guia de consulta/
    exame (opcional). Sessão por convênio precisa de uma guia de sessões do plano
    (GuiaConvenio): escolhe uma aberta do paciente ou cadastra a nova; guia esgotada ou
    vencida bloqueia (decisão do cliente). A guia é travada (select_for_update, sem junção —
    ver guia_autorizar) pra duas autorizações ao mesmo tempo não estourarem o saldo.
    """
    agendamento = get_object_or_404(
        Agendamento.objects.select_related("paciente", "profissional", "convenio"), pk=pk
    )
    destino = _destino_seguro(request, reverse("agenda:agenda_dia"))
    if agendamento.forma_atendimento != Agendamento.FormaAtendimento.CONVENIO:
        raise PermissionDenied
    if agendamento.status != Agendamento.Status.CONFIRMADO:
        messages.info(
            request,
            f"O atendimento de '{agendamento.paciente.nome_completo}' não está aguardando autorização "
            f"(situação: {agendamento.get_status_display()}).",
        )
        return redirect(destino)

    data = timezone.localtime(agendamento.data_hora_inicio).date()
    itens = list(agendamento.itens.select_related("procedimento", "agendamento"))
    dados = request.POST if request.method == "POST" else None
    form = AutorizacaoConvenioForm(dados)
    pares = []
    for item in itens:
        if not item.precisa_guia_convenio:
            continue
        abertas, encerradas = _guias_do_item(item, data)
        guia_form = GuiaSessaoForm(dados, item=item, guias_abertas=abertas, prefix=f"item{item.pk}")
        guia_form.encerradas = [(guia, guia.situacao_em(data)) for guia in encerradas]
        pares.append((item, guia_form))

    if request.method == "POST" and form.is_valid() and all(f.is_valid() for _, f in pares):
        try:
            with transaction.atomic():
                travado = Agendamento.objects.select_for_update().get(pk=agendamento.pk)
                if travado.status != Agendamento.Status.CONFIRMADO:
                    messages.info(request, "Este atendimento já foi autorizado por outra pessoa.")
                    return redirect(destino)
                reservado = {}
                usos = []
                for item, guia_form in pares:
                    escolha = guia_form.cleaned_data["guia"]
                    if escolha == GuiaSessaoForm.NOVA:
                        guia = GuiaConvenio.objects.create(
                            paciente=agendamento.paciente,
                            convenio=agendamento.convenio,
                            procedimento=item.procedimento,
                            numero=guia_form.cleaned_data["novo_numero"].strip(),
                            quantidade_sessoes=guia_form.cleaned_data["nova_quantidade"],
                            validade=guia_form.cleaned_data.get("nova_validade"),
                            criada_por=request.user,
                        )
                        if guia.vencida_em(data):
                            raise _GuiaRecusada(guia_form, "A validade informada já passou na data do atendimento.")
                    else:
                        guia = (
                            GuiaConvenio.objects.select_for_update()
                            .filter(
                                pk=escolha,
                                paciente_id=agendamento.paciente_id,
                                convenio_id=agendamento.convenio_id,
                                procedimento_id=item.procedimento_id,
                            )
                            .first()
                        )
                        if guia is None:
                            raise _GuiaRecusada(guia_form, "Guia não encontrada — escolha de novo.")
                        if guia.vencida_em(data):
                            raise _GuiaRecusada(
                                guia_form, f"A guia {guia.numero} venceu em {guia.validade:%d/%m/%Y} — cadastre a guia nova."
                            )
                    saldo = guia.saldo - reservado.get(guia.pk, 0)
                    if saldo < item.quantidade:
                        raise _GuiaRecusada(
                            guia_form,
                            f"A guia {guia.numero} tem só {saldo} sessão(ões) e esta sessão usa {item.quantidade} — "
                            "cadastre a guia nova liberada pelo plano.",
                        )
                    reservado[guia.pk] = reservado.get(guia.pk, 0) + item.quantidade
                    item.guia_convenio = guia
                    item.save(update_fields=["guia_convenio"])
                    usos.append((item, guia, guia.sessoes_usadas + reservado[guia.pk] - item.quantidade + 1))

                travado.status = Agendamento.Status.AUTORIZADO
                travado.autorizado_em = timezone.now()
                travado.autorizado_por = request.user
                travado.observacao_autorizacao = form.cleaned_data["observacao"].strip()
                travado.numero_guia = form.cleaned_data["numero_guia"].strip()
                travado.motivo_prioridade = form.cleaned_data["motivo_prioridade"]
                campos = [
                    "status", "autorizado_em", "autorizado_por", "observacao_autorizacao", "numero_guia",
                    "motivo_prioridade",
                ]
                if _preencher_valor_convenio(travado):
                    campos.append("valor_convenio")
                travado.save(update_fields=campos)
                # Sem assinatura: vale pra 1ª guia de cada item cobrado do plano (retorno não tem guia).
                if form.cleaned_data["sem_assinatura"]:
                    for item in itens:
                        if not item.eh_retorno:
                            item.sem_assinatura = True
                            item.save(update_fields=["sem_assinatura"])
        except _GuiaRecusada as recusa:
            recusa.form.add_error(None, recusa.mensagem)
        else:
            detalhes = [
                f"guia {guia.numero} ({item.procedimento.nome}): sessão "
                + (f"{inicio}" if item.quantidade == 1 else f"{inicio}–{inicio + item.quantidade - 1}")
                + f" de {guia.quantidade_sessoes}"
                for item, guia, inicio in usos
            ]
            if travado.numero_guia:
                detalhes.insert(0, f"guia {travado.numero_guia}")
            if travado.prioritario:
                detalhes.append(f"prioridade: {travado.get_motivo_prioridade_display()}")
            if travado.observacao_autorizacao:
                detalhes.append(f"obs.: {travado.observacao_autorizacao}")
            if form.cleaned_data["sem_assinatura"]:
                detalhes.append("SEM ASSINATURA — pendente em Guias pendentes")
            LogAuditoria.registrar(
                request,
                request.user,
                f"Autorizou '{agendamento.paciente.nome_completo}' pelo convênio {agendamento.convenio.nome}"
                + (f" — {'; '.join(detalhes)}" if detalhes else ""),
            )
            messages.success(
                request,
                f"'{agendamento.paciente.nome_completo}' autorizado(a)"
                + (f" — {'; '.join(detalhes)}" if detalhes else "") + ".",
            )
            for item, guia, inicio in usos:
                restantes = guia.quantidade_sessoes - (inicio + item.quantidade - 1)
                if restantes <= 2:
                    messages.warning(
                        request,
                        f"A guia {guia.numero} de {item.procedimento.nome} "
                        + ("acabou" if restantes <= 0 else f"tem só mais {restantes} sessão(ões)")
                        + " — peça a renovação ao plano.",
                    )
            return redirect(destino)

    return render(
        request,
        "agenda/agendamento_autorizar.html",
        {
            "agendamento": agendamento,
            "itens": itens,
            "form": form,
            "pares": pares,
            "tem_item_sem_guia": any(not item.precisa_guia_convenio for item in itens),
            "destino": destino,
        },
    )


def _repartir_desconto(itens, desconto):
    """Reparte o desconto do pagamento entre os itens cobrados, proporcional ao valor de cada
    um (o último fica com a sobra do arredondamento) — assim AgendamentoItem.valor_cobrado,
    que os relatórios por procedimento somam, bate com o que o paciente pagou de fato."""
    valores = [item.valor_a_cobrar or Decimal("0.00") for item in itens]
    total = sum(valores, Decimal("0.00"))
    if not desconto or not total:
        return valores
    resultado = []
    restante = desconto
    cobraveis = [indice for indice, valor in enumerate(valores) if valor]
    for indice, valor in enumerate(valores):
        if not valor:
            resultado.append(valor)
            continue
        parte = restante if indice == cobraveis[-1] else (desconto * valor / total).quantize(Decimal("0.01"))
        restante -= parte
        resultado.append(valor - parte)
    return resultado


def _repartir_repasse_fixo(itens, linhas, eh_cortesia, vendas_pacote=(), repasse_acordado=None):
    """
    Repasse fixo de cada linha de pagamento a criar (mesma ordem de 'linhas'), para
    profissional que cobra valor fixo no particular (cadastros.models.ValorFixoProfissional).
    Devolve [None, ...] quando o repasse segue o percentual. O total fixo do atendimento é
    repartido entre as linhas proporcional ao valor de cada uma (pagamento dividido em 2
    formas), com a última linha ficando com a sobra do arredondamento — assim a soma bate
    sempre com o total, e estornar uma das linhas tira só a parte dela do repasse.
    Cortesia não gera repasse (R$ 0,00). Pacote vendido no atendimento soma o repasse fixo
    congelado na venda (PacoteVendido.valor_repasse_fixo; os itens cobertos por ele entram
    com R$ 0,00). Com desconto, vale o 'repasse_acordado' informado no pagamento.
    """
    repasses_itens = [item.valor_repasse_fixo_a_calcular for item in itens]
    repasses_itens += [venda.valor_repasse_fixo for venda in vendas_pacote]
    if not itens or any(valor is None for valor in repasses_itens):
        return [None] * len(linhas)
    if eh_cortesia:
        total_repasse = Decimal("0.00")
    elif repasse_acordado is not None:
        total_repasse = repasse_acordado
    else:
        total_repasse = sum(repasses_itens, Decimal("0.00"))
    total_cobrado = sum((linha["valor"] for linha in linhas), Decimal("0.00"))
    resultado = []
    restante = total_repasse
    for indice, linha in enumerate(linhas):
        if indice == len(linhas) - 1:
            parte = restante
        elif total_cobrado:
            parte = (total_repasse * linha["valor"] / total_cobrado).quantize(Decimal("0.01"))
        else:
            parte = Decimal("0.00")
        resultado.append(parte)
        restante -= parte
    return resultado


def _cancelar_na_hora_do_pagamento(request, agendamento):
    """Paciente desistiu no caixa (pedido da clínica, 23/10/2026): o financeiro cancela ali
    mesmo, sem precisar da recepção. Ainda é Pré-autorizado — cancelamento "de agenda", não
    do já autorizado (ver agendamento_cancelar_autorizado). Pacote incluído nesta tela e não
    pago é desfeito; o motivo é opcional e vai pras observações e pro log de auditoria."""
    destino = _url_depois_do_pagamento(request, agendamento)
    if _tem_pagamento_pendente([agendamento]):
        messages.error(request, "Não é possível cancelar: há pagamento registrado que ainda não foi estornado.")
        return redirect(destino)
    motivo = (request.POST.get("motivo_cancelamento") or "").strip()
    quem = request.user.get_full_name() or request.user.username
    nota = f"Cancelado na hora do pagamento por {quem} em {timezone.localtime():%d/%m/%Y %H:%M}: " + (
        motivo or "paciente desistiu"
    )
    agendamento.observacoes = f"{agendamento.observacoes}\n{nota}".strip()
    agendamento.status = Agendamento.Status.CANCELADO
    with transaction.atomic():
        PacoteVendido.desfazer_nao_pagos([agendamento])
        agendamento.save(update_fields=["status", "observacoes"])
    LogAuditoria.registrar(
        request,
        request.user,
        f"Cancelou na hora do pagamento o agendamento #{agendamento.pk} de '{agendamento.paciente.nome_completo}' "
        f"({timezone.localtime(agendamento.data_hora_inicio):%d/%m/%Y %H:%M}). Motivo: {motivo or 'paciente desistiu'}",
    )
    messages.success(request, f"Atendimento de '{agendamento.paciente.nome_completo}' cancelado — saiu da fila de pagamento.")
    return redirect(destino)


@permission_required("accounts.fila_autorizar_financeiro", raise_exception=True)
@transaction.atomic
def agendamento_confirmar_pagamento(request, pk):
    """
    Tela de "registrar pagamento" — exclusiva do financeiro (etapa
    'Pré-autorizado', ver agendamento_mudar_status: recepção encaminhou o
    atendimento pra cá). Registrar o pagamento (ou marcar cortesia) sempre
    avança o status pra 'Autorizado'.

    Pagamento duplicado (clínica, 26/10/2026 — 4 linhas no lugar de 2): dois envios quase
    juntos (clique duplo, Enter + clique, dois computadores) liam os dois 'Pré-autorizado' e
    gravavam as linhas duas vezes. No POST o agendamento fica travado (select_for_update, sem
    junção — ver guia_autorizar) até o fim da transação: o segundo envio espera, já vê
    'Autorizado' e só volta com o aviso de que o pagamento já foi registrado.
    """
    consulta = Agendamento.objects.select_for_update() if request.method == "POST" else Agendamento.objects
    agendamento = get_object_or_404(consulta, pk=pk)
    if agendamento.status == Agendamento.Status.PRE_AUTORIZADO:
        # Confere o saldo de pacote de novo (pode ter mudado depois de encaminhado pra cá).
        vincular_pacotes(agendamento)
    agendamento = get_object_or_404(
        Agendamento.objects.prefetch_related(
            "itens__procedimento", "itens__retorno_de__agendamento", "itens__pacote_vendido", "pacotes_vendidos__itens"
        ),
        pk=pk,
    )
    if agendamento.status != Agendamento.Status.PRE_AUTORIZADO:
        if agendamento.status in Agendamento.STATUS_PRODUCAO and agendamento.pagamentos.exists():
            messages.info(
                request,
                f"O pagamento de '{agendamento.paciente.nome_completo}' já está registrado "
                f"(situação atual: {agendamento.get_status_display()}).",
            )
        else:
            messages.error(
                request,
                f"'{agendamento.paciente.nome_completo}' não está aguardando pagamento no momento "
                f"(situação atual: {agendamento.get_status_display()}).",
            )
        return redirect(_url_depois_do_pagamento(request, agendamento))

    acao = request.POST.get("acao") if request.method == "POST" else None
    if acao == "cancelar_atendimento":
        return _cancelar_na_hora_do_pagamento(request, agendamento)
    if acao == "incluir_pacote":
        _incluir_pacote(request, agendamento, request.POST.get("pacote"))
        return redirect(request.get_full_path())
    if acao == "remover_pacote":
        venda = get_object_or_404(
            PacoteVendido, pk=request.POST.get("venda"), agendamento_venda=agendamento, pago_em__isnull=True
        )
        if venda.cadastro_manual:
            # Pacote do sistema antigo: só deixa de ser cobrado aqui, volta a ficar "a pagar".
            venda.agendamento_venda = None
            venda.save(update_fields=["agendamento_venda"])
        else:
            venda.delete()
        vincular_pacotes(agendamento)
        messages.success(request, f"Pacote '{venda.nome}' retirado deste pagamento.")
        return redirect(request.get_full_path())
    if acao == "cobrar_pacote":
        venda = _pacotes_a_receber(agendamento).filter(pk=request.POST.get("venda") or None).first()
        if venda is None:
            messages.error(request, "Escolha um pacote a pagar deste paciente com este profissional.")
        else:
            venda.agendamento_venda = agendamento
            venda.save(update_fields=["agendamento_venda"])
            vincular_pacotes(agendamento)
            messages.success(
                request,
                f"Pacote '{venda.nome}' incluído neste pagamento (R$ {formatar_moeda(venda.valor)}).",
            )
        return redirect(request.get_full_path())

    if request.method == "POST":
        form = ConfirmarPagamentoForm(request.POST, agendamento=agendamento)
        if form.is_valid():
            # Congela o valor de cada item no momento do pagamento (R$ 0,00 se retorno ou
            # cortesia, senão o valor calculado) — é isso que os relatórios por
            # procedimento (financeiro.views) somam depois, sem depender de recalcular
            # pela tabela de preços atual (que pode ter mudado desde então).
            eh_cortesia = form.cleaned_data.get("cortesia")
            desconto = form.cleaned_data.get("desconto")
            itens = list(agendamento.itens.all())
            valores_itens = _repartir_desconto(itens, desconto)
            for item, valor in zip(itens, valores_itens):
                item.valor_cobrado = Decimal("0.00") if eh_cortesia else (valor if item.valor_a_cobrar is not None else None)
            AgendamentoItem.objects.bulk_update(itens, ["valor_cobrado"])

            # Tudo já pago num pacote anterior: nada a cobrar, nenhuma linha de pagamento.
            linhas = [] if agendamento.coberto_por_pacote else form.linhas_pagamento()
            vendas_pacote = list(agendamento.pacotes_vendidos.all())
            repasses_fixos = _repartir_repasse_fixo(
                itens, linhas, eh_cortesia, vendas_pacote, form.cleaned_data.get("repasse_acordado")
            )
            for linha, repasse_fixo in zip(linhas, repasses_fixos):
                PagamentoParticular.objects.create(
                    agendamento=agendamento,
                    forma_pagamento=linha["forma_pagamento"],
                    valor=linha["valor"],
                    valor_recebido=linha["valor_recebido"],
                    parcelas=linha["parcelas"],
                    valor_repasse_fixo=repasse_fixo,
                )
            # Venda de pacote: passa a valer a partir de agora (ver PacoteVendido.pago_em).
            agendamento.pacotes_vendidos.filter(pago_em__isnull=True).update(pago_em=timezone.now())
            agendamento.status = Agendamento.Status.AUTORIZADO
            agendamento.autorizado_em = timezone.now()
            agendamento.autorizado_por = request.user
            agendamento.desconto = desconto
            agendamento.motivo_desconto = (form.cleaned_data.get("motivo_desconto") or "").strip()
            campos_alterados = ["status", "autorizado_em", "autorizado_por", "desconto", "motivo_desconto"]
            # Convênio-com-desconto pago na hora continua forma CONVÊNIO — mesmo
            # preenchimento que antes só acontecia ao marcar "atendido".
            if _preencher_valor_convenio(agendamento):
                campos_alterados.append("valor_convenio")
            agendamento.save(update_fields=campos_alterados)
            resumo = ", ".join(
                f"{Agendamento.FormaPagamento(linha['forma_pagamento']).label} R$ {formatar_moeda(linha['valor'])}"
                + (f" (recebido R$ {formatar_moeda(linha['valor_recebido'])})" if linha["valor_recebido"] is not None else "")
                for linha in linhas
            ) or "coberto por pacote"
            if vendas_pacote:
                resumo += " — venda de pacote: " + ", ".join(f"{venda.nome} R$ {formatar_moeda(venda.valor)}" for venda in vendas_pacote)
            if desconto:
                resumo += f" — desconto de R$ {formatar_moeda(desconto)} ({agendamento.motivo_desconto})"
                if form.cleaned_data.get("repasse_acordado") is not None:
                    resumo += f", repasse acordado R$ {formatar_moeda(form.cleaned_data['repasse_acordado'])}"
            LogAuditoria.registrar(
                request,
                request.user,
                f"Registrou pagamento de '{agendamento.paciente.nome_completo}' ({resumo})",
            )
            messages.success(
                request,
                f"Pagamento registrado — '{agendamento.paciente.nome_completo}' está Autorizado(a).",
            )
            if vendas_pacote:
                cobertos = vincular_pacotes_futuros(agendamento.paciente, agendamento.profissional)
                if cobertos:
                    messages.info(
                        request,
                        f"{cobertos} atendimento(s) que o paciente já tinha marcado(s) com "
                        f"{agendamento.profissional.nome} passaram a usar o pacote (não vão gerar cobrança).",
                    )
            return redirect(_url_depois_do_pagamento(request, agendamento))
    else:
        form = ConfirmarPagamentoForm(agendamento=agendamento)

    return render(
        request,
        "agenda/agendamento_confirmar_pagamento.html",
        {
            "form": form,
            "agendamento": agendamento,
            "itens": agendamento.itens.all(),
            "valor_a_cobrar": agendamento.valor_a_cobrar,
            "voltar_url": _url_depois_do_pagamento(request, agendamento),
            "vendas_pacote": agendamento.pacotes_vendidos.all(),
            "pacotes_disponiveis": _pacotes_a_venda(agendamento).exclude(
                pk__in=agendamento.pacotes_vendidos.values("pacote_id")
            ),
            "pacotes_do_paciente": [
                venda
                for venda in PacoteVendido.ativos()
                .filter(paciente=agendamento.paciente, profissional=agendamento.profissional)
                .exclude(agendamento_venda=agendamento)
                .exclude(cadastro_manual=True, pago_em__isnull=True)
                .prefetch_related("itens")
                if venda.total_restante
            ],
            "pacotes_a_receber": _pacotes_a_receber(agendamento),
        },
    )


def _pacotes_a_receber(agendamento):
    """Pacotes do sistema antigo deste paciente com este profissional que ainda faltam pagar
    (ver PacoteVendido.cadastro_manual) — o financeiro pode cobrá-los em qualquer atendimento
    particular dele."""
    if agendamento.forma_atendimento != Agendamento.FormaAtendimento.PARTICULAR:
        return PacoteVendido.objects.none()
    return PacoteVendido.objects.filter(
        paciente=agendamento.paciente,
        profissional=agendamento.profissional,
        cadastro_manual=True,
        pago_em__isnull=True,
        agendamento_venda__isnull=True,
    ).prefetch_related("itens__procedimento")


def _pacotes_a_venda(agendamento):
    """Pacotes (cadastros.models.Pacote) que podem ser vendidos neste atendimento: só
    particular, ativos, do profissional dele ou de 'qualquer profissional', e só os que ele
    realiza por inteiro (o pacote fica preso a ele)."""
    if agendamento.forma_atendimento != Agendamento.FormaAtendimento.PARTICULAR:
        return Pacote.objects.none()
    nao_habilitados = Procedimento.objects.exclude(profissionais_habilitados=agendamento.profissional)
    return (
        Pacote.objects.filter(ativo=True)
        .filter(Q(profissional__isnull=True) | Q(profissional=agendamento.profissional))
        .exclude(itens__procedimento__in=nao_habilitados)
        .prefetch_related("itens__procedimento")
        .order_by("nome")
    )


def _erro_pacote_do_profissional(pacote, itens_pacote, profissional):
    """Por que este pacote não pode ficar preso a este profissional (None se pode)."""
    if not itens_pacote:
        return f"O pacote '{pacote.nome}' não tem nenhum procedimento cadastrado."
    nao_habilitados = [
        item.procedimento.nome
        for item in itens_pacote
        if not profissional.procedimentos_habilitados.filter(pk=item.procedimento_id).exists()
    ]
    if nao_habilitados:
        return (
            f"{profissional.nome} não está habilitado para: {', '.join(nao_habilitados)} — o pacote "
            "fica preso ao profissional, então ele precisa realizar tudo o que está no pacote."
        )
    cobra_fixo = profissional.cobra_valor_fixo_particular and not profissional.eh_proprietario
    if cobra_fixo and pacote.valor_repasse is None:
        return (
            f"{profissional.nome} cobra valor fixo no particular — cadastre o repasse fixo do pacote "
            f"'{pacote.nome}' (Cadastros → Pacotes) antes de vender."
        )
    return None


def _incluir_pacote(request, agendamento, pacote_id):
    """Inclui a venda de um pacote no pagamento deste atendimento (ainda sem pagar — ver
    PacoteVendido.pago_em). Os itens do atendimento que o pacote cobre passam a usá-lo já."""
    pacote = _pacotes_a_venda(agendamento).filter(pk=pacote_id or None).first()
    if pacote is None:
        messages.error(request, "Escolha um pacote válido para este atendimento (só particular).")
        return
    # Clique duplo em "Incluir pacote" vendia o mesmo pacote duas vezes.
    if agendamento.pacotes_vendidos.filter(pacote=pacote).exists():
        messages.error(request, f"O pacote '{pacote.nome}' já está incluído neste pagamento.")
        return
    profissional = agendamento.profissional
    itens_pacote = list(pacote.itens.all())
    erro = _erro_pacote_do_profissional(pacote, itens_pacote, profissional)
    if erro:
        messages.error(request, erro)
        return
    cobra_fixo = profissional.cobra_valor_fixo_particular and not profissional.eh_proprietario
    with transaction.atomic():
        venda = PacoteVendido.objects.create(
            pacote=pacote,
            nome=pacote.nome,
            paciente=agendamento.paciente,
            profissional=profissional,
            agendamento_venda=agendamento,
            valor=pacote.valor,
            valor_repasse_fixo=pacote.valor_repasse if cobra_fixo else None,
            criado_por=request.user,
        )
        PacoteVendidoItem.objects.bulk_create(
            PacoteVendidoItem(pacote_vendido=venda, procedimento=item.procedimento, quantidade=item.quantidade)
            for item in itens_pacote
        )
        vincular_pacotes(agendamento)
    messages.success(
        request,
        f"Pacote '{pacote.nome}' incluído neste pagamento (R$ {formatar_moeda(pacote.valor)}). Ele só passa a valer depois "
        "que o pagamento for registrado.",
    )


@permission_required("accounts.fila_chamar_senha", raise_exception=True)
def agendamento_chamar(request, pk):
    """
    Chamada do paciente pelo próprio profissional, em 'Minha agenda' — dispensa
    a recepção nessa etapa (ver painel público em fila.views.painel_espera).
    Reaproveita a mesma permissão do painel de senha da recepção
    ('fila_chamar_senha'), mas é uma ação própria e independente: exclusiva do
    profissional dono do atendimento (sem bypass por 'agenda_editar' — a
    recepção continua chamando pelo painel de senha, não por aqui), só a
    partir de Autorizado, só pra hoje, e só depois que o profissional definir
    a sala do dia (mesma tela). Não muda o status do agendamento — chamar de
    novo em cima de uma linha já chamada só regrava chamado_em/sala_chamada
    ("chamar novamente").
    """
    agendamento = get_object_or_404(
        Agendamento.objects.select_related("paciente", "profissional"), pk=pk
    )

    if agendamento.profissional_id != request.user.profissional_id:
        raise PermissionDenied
    if timezone.localtime(agendamento.data_hora_inicio).date() != timezone.localdate():
        raise PermissionDenied

    if request.method == "POST":
        if agendamento.status != Agendamento.Status.AUTORIZADO:
            messages.error(
                request,
                f"'{agendamento.paciente.nome_completo}' não está aguardando chamada no momento "
                f"(situação atual: {agendamento.get_status_display()}).",
            )
            return redirect(reverse("agenda:agenda_propria"))

        sala_de_hoje = agendamento.profissional.sala_em(timezone.localdate())
        if not sala_de_hoje:
            messages.error(
                request, "Defina em qual sala você está atendendo hoje antes de chamar o paciente."
            )
            return redirect(reverse("agenda:agenda_propria"))

        agendamento.chamado_por = request.user
        agendamento.chamado_em = timezone.now()
        agendamento.sala_chamada = sala_de_hoje
        agendamento.save(update_fields=["chamado_por", "chamado_em", "sala_chamada"])

        LogAuditoria.registrar(
            request,
            request.user,
            f"Chamou o paciente '{agendamento.paciente.nome_completo}' para {sala_de_hoje.nome}",
        )
        messages.success(request, f"Paciente chamado(a) — {sala_de_hoje.nome}.")

    destino = request.META.get("HTTP_REFERER") or reverse("agenda:agenda_propria")
    return redirect(destino)


def _ocupantes_recepcao_propria(sala, data):
    """Pacientes chamados em lote pra recepção própria desta sala em `data` e que ainda não
    foram atendidos (status Autorizado), na ordem de entrada no consultório
    (`ordem_no_lote`) — quem ocupa lugar na Recepção 2 neste momento, incluindo o que já
    está no consultório (a capacidade da sala conta quem ainda não terminou)."""
    return Agendamento.objects.filter(
        lote_recepcao__isnull=False,
        sala_chamada=sala,
        chamado_em__date=data,
        status=Agendamento.Status.AUTORIZADO,
    ).order_by("ordem_no_lote")


@permission_required("accounts.fila_chamar_senha", raise_exception=True)
def agendamento_chamar_recepcao(request):
    """
    Chamada em lote pra segunda recepção (decisão da clínica, 2026-09-19): quem atende numa
    sala com recepção própria (cadastros.models.Sala.lugares_recepcao_propria) chama, de uma
    vez, tantos pacientes Autorizados quantos couberem nos lugares livres dela. O primeiro
    da fila (mesma ordem de _ordena_fila_de_chamada) entra no consultório e os demais esperam sentados — não existe tela na Recepção
    2, então tudo é anunciado no painel público da recepção 1 (fila.views.painel_espera).

    Vale só pra fila do próprio profissional, só pra hoje e só pra quem ainda não foi
    chamado (a chamada individual, agendamento_chamar, segue existindo em paralelo). Se já
    há gente na Recepção 2 (leva anterior ainda não atendida), a nova chamada entra no mesmo
    lote, só preenchendo os lugares que sobraram, e ninguém "fura" a ordem de entrada.
    """
    destino = reverse("agenda:agenda_propria")
    if request.method != "POST":
        return redirect(destino)

    profissional = None
    if request.user.profissional_id:
        profissional = Profissional.objects.filter(pk=request.user.profissional_id).first()
    if profissional is None:
        raise PermissionDenied

    hoje = timezone.localdate()
    sala = profissional.sala_em(hoje)
    if not sala:
        messages.error(request, "Defina em qual sala você está atendendo hoje antes de chamar pacientes.")
        return redirect(destino)
    if not sala.tem_recepcao_propria:
        messages.error(request, f"A sala '{sala.nome}' não tem {Sala.NOME_RECEPCAO_PROPRIA}.")
        return redirect(destino)

    with transaction.atomic():
        ocupantes = list(_ocupantes_recepcao_propria(sala, hoje).select_for_update())
        livres = sala.lugares_recepcao_propria - len(ocupantes)
        if livres <= 0:
            messages.error(
                request,
                f"A {Sala.NOME_RECEPCAO_PROPRIA} está cheia ({sala.lugares_recepcao_propria} lugares) — "
                "aguarde alguém ser atendido para chamar mais pacientes.",
            )
            return redirect(destino)

        aguardando = list(
            Agendamento.objects.filter(
                profissional=profissional,
                data_hora_inicio__date=hoje,
                status=Agendamento.Status.AUTORIZADO,
                chamado_em__isnull=True,
            )
            .select_for_update()
            .prefetch_related("itens__procedimento")
        )
        escolhidos = _ordena_fila_de_chamada(aguardando)[:livres]
        if not escolhidos:
            messages.info(request, "Não há nenhum paciente Autorizado aguardando chamada.")
            return redirect(destino)

        if ocupantes:
            lote = ocupantes[0].lote_recepcao
            proxima_ordem = max(o.ordem_no_lote for o in ocupantes) + 1
        else:
            lote = uuid.uuid4()
            proxima_ordem = 1

        agora = timezone.now()
        for ordem, agendamento in enumerate(escolhidos, start=proxima_ordem):
            agendamento.lote_recepcao = lote
            agendamento.ordem_no_lote = ordem
            agendamento.chamado_por = request.user
            agendamento.chamado_em = agora
            agendamento.sala_chamada = sala
            agendamento.save(
                update_fields=["lote_recepcao", "ordem_no_lote", "chamado_por", "chamado_em", "sala_chamada"]
            )

    nomes = ", ".join(a.paciente.nome_completo for a in escolhidos)
    LogAuditoria.registrar(
        request,
        request.user,
        f"Chamou {len(escolhidos)} paciente(s) para a {Sala.NOME_RECEPCAO_PROPRIA} ({sala.nome}): {nomes}",
    )
    if ocupantes:
        messages.success(
            request,
            f"{len(escolhidos)} paciente(s) chamado(s) para a {Sala.NOME_RECEPCAO_PROPRIA} — aguardam sentados.",
        )
    else:
        messages.success(
            request,
            f"{len(escolhidos)} paciente(s) chamado(s) para a {Sala.NOME_RECEPCAO_PROPRIA} — "
            f"{escolhidos[0].paciente.nome_completo} entra no consultório, os demais aguardam sentados.",
        )
    return redirect(destino)


# =====================================================================
# Ocupação de salas (grade sala x horário + exportação em planilha)
# =====================================================================

def _monta_ocupacao_salas(data):
    """
    Monta a grade Sala (linha) x horário (coluna) para a data informada,
    combinando a sala escolhida por cada profissional naquele dia (ver
    cadastros.models.AtendimentoSala/Profissional.sala_em) com os
    Agendamentos daquele profissional na mesma data. Como a escolha de sala
    é por DIA (não por horário específico — ver decisão do item 4), um
    profissional que atenda em mais de um horário no dia aparece em todos os
    horários dele dentro da sala escolhida.
    """
    salas = list(Sala.objects.filter(ativa=True).order_by("nome"))
    profissionais_ativos = Profissional.objects.filter(ativo=True).order_by("nome")

    profissionais_por_sala = {sala.pk: [] for sala in salas}
    for profissional in profissionais_ativos:
        sala = profissional.sala_em(data)
        if sala and sala.pk in profissionais_por_sala:
            profissionais_por_sala[sala.pk].append(profissional)

    minuto_inicio = GRADE_HORA_INICIO * 60
    minuto_fim = GRADE_HORA_FIM * 60
    horarios_min = list(range(minuto_inicio, minuto_fim, SLOT_MINUTOS))

    linhas = []
    for sala in salas:
        profissionais_sala = profissionais_por_sala[sala.pk]
        agendamentos = []
        if profissionais_sala:
            agendamentos = list(
                Agendamento.objects.filter(profissional__in=profissionais_sala, data_hora_inicio__date=data)
                .exclude(status=Agendamento.Status.CANCELADO)
                .select_related("paciente", "profissional")
            )

        ocupacao_por_slot = {}
        for agendamento in agendamentos:
            inicio_local = timezone.localtime(agendamento.data_hora_inicio)
            fim_local = timezone.localtime(agendamento.data_hora_fim)
            inicio_min = inicio_local.hour * 60 + inicio_local.minute
            # max(): consulta/exame não têm duração (fim = início), mas ocupam a sala no slot.
            fim_min = max(fim_local.hour * 60 + fim_local.minute, inicio_min + 1)
            for slot in horarios_min:
                if inicio_min < slot + SLOT_MINUTOS and slot < fim_min:
                    ocupacao_por_slot.setdefault(slot, []).append(agendamento)

        celulas = []
        for slot in horarios_min:
            celulas.append(
                {
                    "hora": f"{slot // 60:02d}:{slot % 60:02d}",
                    "ocupantes": ocupacao_por_slot.get(slot, []),
                }
            )
        linhas.append({"sala": sala, "profissionais": profissionais_sala, "celulas": celulas})

    return {
        "horarios": [f"{slot // 60:02d}:{slot % 60:02d}" for slot in horarios_min],
        "linhas": linhas,
    }


@permission_required("accounts.agenda_ver_dia", raise_exception=True)
def ocupacao_salas(request):
    data = parse_date(request.GET.get("data") or "") or timezone.localdate()
    grade = _monta_ocupacao_salas(data)

    if request.GET.get("exportar") == "1":
        if openpyxl is None:
            messages.error(request, "Suporte a .xlsx não está instalado no servidor (biblioteca 'openpyxl' ausente).")
            return redirect(f"{reverse('agenda:ocupacao_salas')}?data={data.isoformat()}")

        pasta_trabalho = openpyxl.Workbook()
        planilha = pasta_trabalho.active
        planilha.title = "Ocupação de salas"
        planilha.append(["Sala"] + grade["horarios"])
        for linha in grade["linhas"]:
            valores_linha = [linha["sala"].nome]
            for celula in linha["celulas"]:
                nomes = "; ".join(
                    f"{o.profissional.nome} — {o.paciente.nome_completo}" for o in celula["ocupantes"]
                )
                valores_linha.append(nomes)
            planilha.append(valores_linha)

        buffer = io.BytesIO()
        pasta_trabalho.save(buffer)
        buffer.seek(0)
        resposta = HttpResponse(
            buffer.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        resposta["Content-Disposition"] = f"attachment; filename=ocupacao_salas_{data.isoformat()}.xlsx"
        return resposta

    return render(request, "agenda/ocupacao_salas.html", {"data": data, "grade": grade})


# =====================================================================
# Relatório semanal de repasses (particular não pago em dinheiro)
# =====================================================================

def _semana_de(data):
    """Segunda a domingo da semana que contém 'data' (semana de calendário — o
    relatório financeiro considera a semana toda, não só os dias úteis da clínica)."""
    inicio = data - timedelta(days=data.weekday())
    fim = inicio + timedelta(days=6)
    return inicio, fim


@permission_required("accounts.financeiro_ver_repasses_semanais", raise_exception=True)
def relatorio_repasses_semanal(request):
    """
    Lista as formas de pagamento (agenda.models.PagamentoParticular) de
    atendimentos PARTICULARES realizados numa semana, cujo repasse ao
    profissional ainda está pendente, agrupadas por profissional. CORTESIA
    nunca gera repasse e fica de fora. Dinheiro: até 25/10/2026 era acertado
    na hora (essas linhas foram marcadas como pagas, agenda.0033); desde
    26/10/2026 fica no caixa e entra aqui como as demais formas, até o
    financeiro registrar o pagamento (pedido da clínica).

    Pagamento parcial (pedido do cliente, 2026-09-28): quando o caixa não tem
    dinheiro suficiente pra repassar tudo, o financeiro registra QUANTO pagou
    ao profissional (financeiro.models.RepassePagamento, botão "Registrar
    pagamento" de cada grupo) e o sistema abate das linhas mais antigas — a
    linha parcialmente paga continua em "A pagar", mostrando o saldo. O
    "saldo total" de cada profissional soma TODAS as semanas em aberto, não só
    a exibida, porque o abatimento também não se limita à semana.

    Cada linha aqui é uma FORMA DE PAGAMENTO, não um atendimento inteiro — um
    agendamento pago em 2 formas (ex.: metade dinheiro, metade cartão) aparece
    em 2 linhas, e pode ter uma forma já repassada enquanto a outra ainda está
    pendente.
    """
    hoje = timezone.localdate()
    data_referencia = parse_date(request.GET.get("data") or "") or hoje
    intervalo_inicio, intervalo_fim = _semana_de(data_referencia)
    ver_pagos = request.GET.get("ver") == "pagos"

    base_qs = (
        PagamentoParticular.objects.filter(
            # Particular + convênio "funciona como particular" (23/10/2026).
            Agendamento.filtro_pago_como_particular("agendamento__"),
            agendamento__status__in=Agendamento.STATUS_PRODUCAO,
            agendamento__data_hora_inicio__date__range=(intervalo_inicio, intervalo_fim),
        )
        .exclude(forma_pagamento=Agendamento.FormaPagamento.CORTESIA)
        .exclude(estornado=True)
        # Proprietário não recebe repasse: a produção dele é da própria clínica (pedido da clínica, 26/10/2026).
        .exclude(agendamento__profissional__eh_proprietario=True)
        .select_related("agendamento__paciente", "agendamento__profissional")
        .order_by("agendamento__profissional__nome", "agendamento__data_hora_inicio")
    )
    linhas = base_qs.filter(repasse_pago=ver_pagos)

    # valor_liquido já desconta o percentual de retenção do profissional (ver
    # PagamentoParticular.valor_repasse: proprietário não sofre desconto) — é o valor que de
    # fato deve ser entregue no repasse, não o valor cheio pago pelo paciente. "total" do grupo
    # é o SALDO (líquido menos o que já foi pago parcialmente) na aba "A pagar", e o líquido
    # cheio no histórico.
    grupos = {}
    for pagamento in linhas:
        profissional = pagamento.agendamento.profissional
        pagamento.valor_liquido = pagamento.valor_repasse
        pagamento.saldo = pagamento.saldo_repasse
        grupo = grupos.setdefault(
            profissional.pk,
            {"profissional": profissional, "itens": [], "total_bruto": Decimal("0.00"), "total": Decimal("0.00")},
        )
        grupo["itens"].append(pagamento)
        grupo["total_bruto"] += pagamento.valor
        grupo["total"] += pagamento.valor_liquido if ver_pagos else pagamento.saldo

    grupos_ordenados = sorted(grupos.values(), key=lambda g: g["profissional"].nome)
    if not ver_pagos:
        for grupo in grupos_ordenados:
            grupo["saldo_todas_semanas"] = RepassePagamento.saldo_pendente(grupo["profissional"])
    total_geral = sum((g["total"] for g in grupos_ordenados), Decimal("0.00"))
    total_geral_bruto = sum((g["total_bruto"] for g in grupos_ordenados), Decimal("0.00"))

    if request.GET.get("exportar") == "1":
        if openpyxl is None:
            messages.error(request, "Suporte a .xlsx não está instalado no servidor (biblioteca 'openpyxl' ausente).")
            return redirect(request.path + f"?data={data_referencia.isoformat()}")

        pasta_trabalho = openpyxl.Workbook()
        planilha = pasta_trabalho.active
        planilha.title = "Repasses semanais"
        planilha.append(
            ["Profissional", "Paciente", "Data", "Forma de pagamento", "Valor bruto", "Valor líquido",
             "Já pago", "Saldo a pagar", "Repasse pago"]
        )
        for grupo in grupos_ordenados:
            for item in grupo["itens"]:
                planilha.append([
                    grupo["profissional"].nome,
                    item.agendamento.paciente.nome_completo,
                    timezone.localtime(item.agendamento.data_hora_inicio).strftime("%d/%m/%Y"),
                    item.get_forma_pagamento_display(),
                    float(item.valor),
                    float(item.valor_liquido),
                    float(item.repasse_valor_pago),
                    float(item.saldo),
                    "Sim" if item.repasse_pago else "Não",
                ])
            planilha.append([
                "", "", "", "Total " + grupo["profissional"].nome, float(grupo["total_bruto"]), "", "",
                float(grupo["total"]), ""
            ])

        buffer = io.BytesIO()
        pasta_trabalho.save(buffer)
        buffer.seek(0)
        resposta = HttpResponse(
            buffer.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        resposta["Content-Disposition"] = f"attachment; filename=repasses_{intervalo_inicio.isoformat()}.xlsx"
        return resposta

    return render(
        request,
        "agenda/relatorio_repasses_semanal.html",
        {
            "data_referencia": data_referencia,
            "intervalo_inicio": intervalo_inicio,
            "intervalo_fim": intervalo_fim,
            "semana_anterior": intervalo_inicio - timedelta(days=7),
            "semana_seguinte": intervalo_inicio + timedelta(days=7),
            "grupos": grupos_ordenados,
            "total_geral": total_geral,
            "total_geral_bruto": total_geral_bruto,
            "ver_pagos": ver_pagos,
        },
    )


# =====================================================================
# Meu desempenho (padrão de atendimento do próprio profissional)
# =====================================================================

def _mes_calendario(ano, mes):
    """(primeiro dia, último dia) do mês — mesmo cálculo de financeiro.views._mes_calendario,
    duplicado aqui por app (mesmo padrão já aceito no projeto, ver _semana_de acima)."""
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    return date(ano, mes, 1), date(ano, mes, ultimo_dia)


DIAS_SEMANA_ROTULOS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta"]


def _padrao_atendimento(qs):
    """
    A partir de um queryset de Agendamento (já filtrado por profissional, status=
    REALIZADO e, se for o caso, por período — ver meu_desempenho), monta a contagem
    por dia da semana, por período do dia (manhã/tarde/noite, corte fixo às 12h/18h,
    sobre o horário LOCAL de início) e por convênio — Particular entra como mais uma
    categoria na lista de convênios, pra dar a proporção completa de como o
    profissional atende, não só entre os convênios entre si.

    Só CONTAGEM de atendimentos, nunca valor em R$ — isso já existe em financeiro (ver
    meu_fechamento_diario/mensal); aqui é sobre PADRÃO (quando/pra quem atende), não
    sobre dinheiro.
    """
    total = 0
    total_retorno = 0
    contagem_dia_semana = [0, 0, 0, 0, 0]  # Segunda(0) .. Sexta(4) — clínica não abre fim de semana
    contagem_periodo = {"manha": 0, "tarde": 0, "noite": 0}
    contagem_convenio = {}

    for agendamento in qs.select_related("convenio").prefetch_related("itens"):
        total += 1
        if agendamento.tem_item_retorno:
            total_retorno += 1
        inicio_local = timezone.localtime(agendamento.data_hora_inicio)
        if inicio_local.weekday() < 5:
            contagem_dia_semana[inicio_local.weekday()] += 1
        if inicio_local.hour < 12:
            contagem_periodo["manha"] += 1
        elif inicio_local.hour < 18:
            contagem_periodo["tarde"] += 1
        else:
            contagem_periodo["noite"] += 1

        if agendamento.forma_atendimento == Agendamento.FormaAtendimento.CONVENIO and agendamento.convenio_id:
            chave, nome = agendamento.convenio_id, agendamento.convenio.nome
        else:
            chave, nome = "particular", "Particular"
        item = contagem_convenio.setdefault(chave, {"nome": nome, "quantidade": 0})
        item["quantidade"] += 1

    def com_percentual(quantidade):
        return round(quantidade * 100 / total) if total else 0

    dias_semana = [
        {"rotulo": rotulo, "quantidade": quantidade, "percentual": com_percentual(quantidade)}
        for rotulo, quantidade in zip(DIAS_SEMANA_ROTULOS, contagem_dia_semana)
    ]
    periodos = [
        {"rotulo": rotulo, "quantidade": contagem_periodo[chave], "percentual": com_percentual(contagem_periodo[chave])}
        for chave, rotulo in [("manha", "Manhã"), ("tarde", "Tarde"), ("noite", "Noite")]
    ]
    convenios = sorted(
        (
            {"nome": item["nome"], "quantidade": item["quantidade"], "percentual": com_percentual(item["quantidade"])}
            for item in contagem_convenio.values()
        ),
        key=lambda item: -item["quantidade"],
    )
    dia_mais_frequente = max(dias_semana, key=lambda d: d["quantidade"])
    if not dia_mais_frequente["quantidade"]:
        dia_mais_frequente = None

    return {
        "total": total,
        "total_retorno": total_retorno,
        "dias_semana": dias_semana,
        "dia_mais_frequente": dia_mais_frequente,
        "periodos": periodos,
        "convenios": convenios,
    }


@permission_required("accounts.agenda_ver_propria", raise_exception=True)
def listagem_atendidos(request):
    """
    "Listagem de atendidos" do PRÓPRIO profissional (pedido da clínica, 23/10/2026): a lista
    de hoje em Minha agenda só mostra quem ainda está Autorizado (quem foi atendido sai), e ele
    não tinha onde conferir o que já atendeu. Mostra os atendimentos dele já autorizados no
    período (Autorizado/Realizado — STATUS_PRODUCAO, a mesma régua da produção), com a
    situação ("marcado como atendido" ou só autorizado). Sem valores: dinheiro fica em
    "Meu fechamento". Filtros: período (padrão hoje), nome do paciente e situação.
    """
    profissional = None
    if request.user.profissional_id:
        profissional = Profissional.objects.filter(pk=request.user.profissional_id).first()
    if profissional is None:
        messages.error(
            request,
            "Seu usuário não está vinculado a um cadastro de profissional — "
            "peça para o administrador vincular (campo 'profissional vinculado' no seu usuário).",
        )
        return render(request, "agenda/listagem_atendidos.html", {"profissional": None})

    hoje = timezone.localdate()
    de = parse_date(request.GET.get("de") or "") or hoje
    ate = parse_date(request.GET.get("ate") or "") or de
    if ate < de:
        de, ate = ate, de
    paciente_nome = (request.GET.get("paciente") or "").strip()
    situacao = request.GET.get("situacao") or ""

    qs = (
        Agendamento.objects.filter(
            profissional=profissional,
            status__in=Agendamento.STATUS_PRODUCAO,
            data_hora_inicio__date__gte=de,
            data_hora_inicio__date__lte=ate,
        )
        .select_related("paciente", "convenio")
        .prefetch_related("itens__procedimento", "itens__retorno_de")
        .order_by("-data_hora_inicio", "-pk")
    )
    if situacao == "atendido":
        qs = qs.filter(status=Agendamento.Status.REALIZADO)
    elif situacao == "nao_marcado":
        qs = qs.filter(status=Agendamento.Status.AUTORIZADO)
    for palavra in paciente_nome.split():
        qs = qs.filter(paciente__nome_completo__icontains=palavra)

    # 50 por página (pedido da clínica, 03/11/2026) — antes eram até 500 numa página só.
    pagina = paginar(request, qs)
    return render(
        request,
        "agenda/listagem_atendidos.html",
        {
            "profissional": profissional,
            "linhas": list(pagina.object_list),
            "pagina": pagina,
            "de": de,
            "ate": ate,
            "hoje": hoje,
            "ontem": hoje - timedelta(days=1),
            "inicio_mes": hoje.replace(day=1),
            "paciente_nome": paciente_nome,
            "situacao": situacao,
            "total_atendidos": qs.filter(status=Agendamento.Status.REALIZADO).count(),
        },
    )


@permission_required("accounts.agenda_ver_propria", raise_exception=True)
def meu_desempenho(request):
    """
    Painel do PRÓPRIO profissional logado (pedido do cliente): quando ele mais
    atende (dia da semana / período do dia) e qual convênio ele mais atende, tanto no
    mês quanto historicamente. Mesma permissão de "Minha agenda" (agenda_ver_propria)
    — é uma leitura complementar sobre a própria agenda, por isso mora aqui em vez de
    financeiro (que já tem "Meu fechamento" pra a parte de dinheiro).
    """
    profissional = None
    if request.user.profissional_id:
        profissional = Profissional.objects.filter(pk=request.user.profissional_id).first()
    if profissional is None:
        messages.error(
            request,
            "Seu usuário não está vinculado a um cadastro de profissional — "
            "peça para o administrador vincular (campo 'profissional vinculado' no seu usuário).",
        )
        return render(request, "agenda/meu_desempenho.html", {"profissional": None})

    hoje = timezone.localdate()
    ano = int(request.GET.get("ano") or hoje.year)
    mes = int(request.GET.get("mes") or hoje.month)
    mes_inicio, mes_fim = _mes_calendario(ano, mes)
    mes_anterior_ano, mes_anterior_mes = (ano - 1, 12) if mes == 1 else (ano, mes - 1)
    mes_seguinte_ano, mes_seguinte_mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)

    realizados = Agendamento.objects.filter(profissional=profissional, status__in=Agendamento.STATUS_PRODUCAO)

    return render(
        request,
        "agenda/meu_desempenho.html",
        {
            "profissional": profissional,
            "ano": ano,
            "mes": mes,
            "mes_inicio": mes_inicio,
            "mes_anterior_ano": mes_anterior_ano,
            "mes_anterior_mes": mes_anterior_mes,
            "mes_seguinte_ano": mes_seguinte_ano,
            "mes_seguinte_mes": mes_seguinte_mes,
            "no_mes": _padrao_atendimento(realizados.filter(data_hora_inicio__date__range=(mes_inicio, mes_fim))),
            "historico": _padrao_atendimento(realizados),
        },
    )


# =====================================================================
# Guias de sessão por convênio (2ª guia em diante)
# =====================================================================

def _trava_financeira_guia(agendamento):
    """Motivo pelo qual as guias deste atendimento não podem mais mudar — None quando
    está livre. Só trava depois do REPASSE ao profissional pago: aí o dinheiro já saiu, e
    aumentar a produção depois criaria uma diferença que ninguém veria.

    Lote de faturamento / convênio recebido NÃO travam (decisão da clínica, 13/10/2026):
    são a cobrança clínica → convênio, processo à parte da produção do profissional — a 2ª
    guia autorizada depois de o atendimento entrar num lote continua contando na produção
    (no dia do atendimento, não no dia da autorização). O 'produzido' do lote acompanha;
    o valor ENVIADO ao convênio é digitado à parte e não muda."""
    if agendamento.convenio_repasse_pago:
        return "o repasse ao profissional já foi pago"
    return None


def _atualizar_valor_convenio_por_guias(item, produzida_antes):
    """Depois de autorizar/desfazer uma guia: o valor do item já congelado na autorização
    é reescalado pelo nº de guias que contam agora (mantém o valor UNITÁRIO de quando foi
    congelado — nunca relê a tabela de preços atual). Item sem valor ainda (atendimento
    não autorizado) fica como está — é preenchido na autorização, já com as guias."""
    if item.valor_convenio is None or item.eh_retorno or not produzida_antes:
        return
    unitario = item.valor_convenio / produzida_antes
    item.valor_convenio = (unitario * item.quantidade_produzida).quantize(Decimal("0.01"))
    item.save(update_fields=["valor_convenio", "guias_extras_autorizadas"])
    agendamento = item.agendamento
    valores = [i.valor_convenio for i in agendamento.itens.all()]
    if all(v is not None for v in valores):
        agendamento.valor_convenio = sum(valores, Decimal("0.00"))
        agendamento.save(update_fields=["valor_convenio"])


@login_required
def guias_pendentes(request):
    """
    Tela "Guias pendentes" — pedido da clínica (13/10/2026): o convênio paga a sessão em
    unidades de 30 min (sessão de 1h = 2 sessões cobradas), mas no dia do atendimento só
    autoriza a 1ª guia. Aqui quem cobre a agenda do profissional vê as guias que ainda
    faltam autorizar (podem ser autorizadas no decorrer da semana, ou até antes do
    atendimento) e marca cada uma como autorizada — só então ela conta na produção.
    """
    ve_tudo = request.user.has_perm("accounts.fila_autorizar_recepcao") or request.user.has_perm(
        "accounts.financeiro_ver_contas_receber"
    )
    # Pedido da clínica (2026-10-16): a profissional vê o que está pendente de assinatura/
    # autorização dos PRÓPRIOS atendimentos (só consulta — sem botões).
    so_proprios = not ve_tudo and bool(request.user.profissional_id) and request.user.has_perm(
        "accounts.agenda_ver_propria"
    )
    if not (ve_tudo or so_proprios):
        raise PermissionDenied

    situacao = request.GET.get("situacao") or ""
    pendente_guia = AgendamentoItem.com_guia_pendente().values("pk")
    pendente_assinatura = AgendamentoItem.com_assinatura_pendente().values("pk")
    if situacao == "todas":
        # Inclui as já totalmente autorizadas (pra conferir ou desfazer um engano) — só as
        # recentes, senão a lista cresce pra sempre.
        base = AgendamentoItem.objects.filter(
            Q(
                agendamento__forma_atendimento=Agendamento.FormaAtendimento.CONVENIO,
                procedimento__permite_repeticao_semanal=True,
                quantidade__gt=1,
                agendamento__data_hora_inicio__date__gte=timezone.localdate() - timedelta(days=60),
            )
            | Q(pk__in=pendente_assinatura)
            # Assinaturas registradas nos últimos 60 dias também, pra conferir o registro.
            | Q(assinada_em__gte=timezone.localdate() - timedelta(days=60))
            | Q(guias_extras__assinada_em__gte=timezone.localdate() - timedelta(days=60))
        ).distinct().exclude(agendamento__status__in=[Agendamento.Status.CANCELADO, Agendamento.Status.FALTOU])
    elif situacao == "assinatura":
        base = AgendamentoItem.objects.filter(pk__in=pendente_assinatura)
    elif situacao == "autorizacao":
        base = AgendamentoItem.objects.filter(pk__in=pendente_guia)
    else:
        base = AgendamentoItem.objects.filter(Q(pk__in=pendente_guia) | Q(pk__in=pendente_assinatura))
    itens = base.select_related(
        "procedimento", "agendamento__paciente", "agendamento__profissional", "agendamento__convenio"
    ).prefetch_related("guias_extras__assinatura_registrada_por").select_related(
        "assinatura_registrada_por"
    ).order_by("agendamento__data_hora_inicio")
    profissional_id = request.GET.get("profissional") or ""
    if so_proprios:
        profissional_id = str(request.user.profissional_id)
    if profissional_id.isdigit():
        itens = itens.filter(agendamento__profissional_id=profissional_id)
    # 50 por página (pedido da clínica, 03/11/2026). Os botões voltam pra mesma página
    # (campo "proximo" = URL atual, com ?pagina=).
    pagina = paginar(request, itens)
    linhas = list(pagina.object_list)
    for item in linhas:
        item.trava_financeira = _trava_financeira_guia(item.agendamento)
        item.atendimento_autorizado = item.agendamento.status in Agendamento.STATUS_PRODUCAO
    return render(
        request,
        "agenda/guias_pendentes.html",
        {
            "itens": linhas,
            "profissionais": Profissional.objects.filter(ativo=True).order_by("nome"),
            "profissional_selecionado": profissional_id,
            "situacao": situacao,
            "pode_autorizar": request.user.has_perm("accounts.fila_autorizar_recepcao"),
            "so_proprios": so_proprios,
            "hoje": timezone.localdate(),
        },
    )


@permission_required("accounts.fila_autorizar_recepcao", raise_exception=True)
def guia_autorizar(request, item_pk, acao):
    """POST: 'autorizar' marca a próxima guia pendente do item, com a data em que o plano
    autorizou ('autorizada_em' — pode ser antes do atendimento; padrão hoje); 'desfazer'
    desmarca a última (engano). Recalcula o valor do convênio se o atendimento já estiver na
    produção."""
    if request.method != "POST":
        return redirect("agenda:guias_pendentes")
    destino = request.POST.get("proximo") or reverse("agenda:guias_pendentes")
    if not destino.startswith("/") or destino.startswith("//"):
        destino = reverse("agenda:guias_pendentes")
    hoje = timezone.localdate()

    if acao == "assinou":
        # Paciente assinou uma guia autorizada "sem assinatura" (pedido da clínica, 2026-10-16).
        # Só registro de controle — não mexe em produção/valor, por isso não passa pela trava
        # financeira abaixo.
        item = get_object_or_404(
            AgendamentoItem.objects.select_related("procedimento", "agendamento__paciente"), pk=item_pk
        )
        numero = request.POST.get("numero", "")
        # Dia em que o paciente assinou (padrão hoje) — registro pedido pela clínica.
        assinada_em = parse_date(request.POST.get("assinada_em") or "") or hoje
        if assinada_em > hoje:
            messages.error(request, "A data da assinatura não pode ser no futuro.")
            return redirect(destino)
        if numero == "1" and item.sem_assinatura:
            item.sem_assinatura = False
            item.assinada_em = assinada_em
            item.assinatura_registrada_por = request.user
            item.save(update_fields=["sem_assinatura", "assinada_em", "assinatura_registrada_por"])
        elif numero.isdigit() and item.guias_extras.filter(numero=int(numero), sem_assinatura=True).exists():
            item.guias_extras.filter(numero=int(numero)).update(
                sem_assinatura=False, assinada_em=assinada_em, assinatura_registrada_por=request.user
            )
        else:
            messages.info(request, "Esta guia não está pendente de assinatura.")
            return redirect(destino)
        agendamento = item.agendamento
        descricao = (
            f"assinatura da {numero}ª guia de '{item.procedimento.nome}' — {agendamento.paciente.nome_completo} "
            f"({timezone.localtime(agendamento.data_hora_inicio):%d/%m/%Y %H:%M}), assinada em {assinada_em:%d/%m/%Y}"
        )
        LogAuditoria.registrar(request, request.user, f"Registrou a {descricao}")
        messages.success(request, f"Registrada a {descricao}.")
        return redirect(destino)

    autorizada_em = hoje
    if acao == "autorizar" and request.POST.get("autorizada_em"):
        autorizada_em = parse_date(request.POST["autorizada_em"])
        if autorizada_em is None:
            messages.error(request, "Data da autorização inválida.")
            return redirect(destino)
        if autorizada_em > hoje:
            messages.error(request, "A data da autorização não pode ser no futuro.")
            return redirect(destino)

    with transaction.atomic():
        # Sem select_related aqui: o PostgreSQL recusa FOR UPDATE com junção num FK que pode
        # ser vazio (agendamento.convenio) — erro 500 relatado pela clínica em 14/10/2026 (o
        # SQLite dos testes ignora o FOR UPDATE e não pega isso).
        item = get_object_or_404(AgendamentoItem.objects.select_for_update(), pk=item_pk)
        agendamento = item.agendamento
        if not item.controla_guias or agendamento.status in (Agendamento.Status.CANCELADO, Agendamento.Status.FALTOU):
            messages.error(request, "Este atendimento não tem guias de sessão a controlar.")
            return redirect(destino)
        trava = _trava_financeira_guia(agendamento)
        if trava:
            messages.error(
                request,
                f"Não é possível alterar as guias deste atendimento: {trava}. Ajuste o valor pelo Financeiro.",
            )
            return redirect(destino)

        produzida_antes = item.quantidade_produzida
        if acao == "autorizar":
            if not item.guias_pendentes:
                messages.info(request, "Todas as guias deste atendimento já estão autorizadas.")
                return redirect(destino)
            item.guias_extras_autorizadas = item.guias_autorizadas  # = extras + 1 (a próxima)
            numero = item.guias_autorizadas
            texto = "Autorizou"
            GuiaExtraAutorizada.objects.filter(item=item, numero=numero).delete()
            sem_assinatura = bool(request.POST.get("sem_assinatura"))
            GuiaExtraAutorizada.objects.create(
                item=item, numero=numero, autorizada_em=autorizada_em, registrada_por=request.user,
                sem_assinatura=sem_assinatura,
            )
            if sem_assinatura:
                texto = "Autorizou (sem assinatura)"
        elif acao == "desfazer":
            if item.guias_autorizadas <= 1:
                messages.info(request, "Nenhuma guia extra autorizada para desfazer.")
                return redirect(destino)
            numero = item.guias_autorizadas
            item.guias_extras_autorizadas = item.guias_autorizadas - 2
            texto = "Desfez a autorização da"
            GuiaExtraAutorizada.objects.filter(item=item, numero=numero).delete()
        else:
            raise PermissionDenied

        item.save(update_fields=["guias_extras_autorizadas"])
        if agendamento.status in Agendamento.STATUS_PRODUCAO:
            _atualizar_valor_convenio_por_guias(item, produzida_antes)

    descricao = (
        f"{numero}ª guia de '{item.procedimento.nome}' — {agendamento.paciente.nome_completo} "
        f"({timezone.localtime(agendamento.data_hora_inicio):%d/%m/%Y %H:%M})"
    )
    if acao == "autorizar":
        descricao += f", autorizada pelo plano em {autorizada_em:%d/%m/%Y}"
    LogAuditoria.registrar(request, request.user, f"{texto} {descricao}")
    messages.success(request, f"{texto} {descricao}.")
    return redirect(destino)


# =====================================================================
# Autorizações e guias de sessões do convênio (pedido da clínica, 15/10/2026)
# =====================================================================

def _pode_ver_autorizacoes(usuario):
    return any(
        usuario.has_perm(f"accounts.{perm}")
        for perm in ("fila_autorizar_recepcao", "financeiro_ver_contas_receber", "agenda_cancelar_autorizado")
    )


@login_required
def autorizacoes(request):
    """Tela "Autorizações" (igual à do sistema antigo): o que já foi autorizado em um período,
    com quem autorizou, a observação e a guia usada ("sessões 3–4 de 10"). Atendidos em verde."""
    if not _pode_ver_autorizacoes(request.user):
        raise PermissionDenied
    hoje = timezone.localdate()
    de = parse_date(request.GET.get("de") or "") or hoje
    ate = parse_date(request.GET.get("ate") or "") or de
    if ate < de:
        de, ate = ate, de
    convenio_id = request.GET.get("convenio") or ""
    profissional_id = request.GET.get("profissional") or ""
    paciente_nome = (request.GET.get("paciente") or "").strip()

    qs = (
        Agendamento.objects.filter(
            status__in=Agendamento.STATUS_PRODUCAO,
            data_hora_inicio__date__gte=de,
            data_hora_inicio__date__lte=ate,
        )
        .select_related("paciente", "profissional", "convenio", "autorizado_por")
        .prefetch_related("itens__procedimento", "itens__guia_convenio")
        .order_by("data_hora_inicio", "autorizado_em", "pk")
    )
    if convenio_id == "particular":
        qs = qs.filter(forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR)
    elif convenio_id.isdigit():
        qs = qs.filter(convenio_id=convenio_id)
    if profissional_id.isdigit():
        qs = qs.filter(profissional_id=profissional_id)
    if paciente_nome:
        qs = qs.filter(paciente__nome_completo__icontains=paciente_nome)

    # 50 por página (pedido da clínica, 03/11/2026) — antes eram até 1000 numa página só.
    pagina = paginar(request, qs)
    linhas = list(pagina.object_list)
    faixas = GuiaConvenio.faixas_dos_itens([item for ag in linhas for item in ag.itens.all()])
    for ag in linhas:
        ag.guias_usadas = []
        for item in ag.itens.all():
            if item.guia_convenio_id and item.pk in faixas:
                inicio, fim, total = faixas[item.pk]
                sessoes = f"{inicio}" if inicio == fim else f"{inicio}–{fim}"
                ag.guias_usadas.append(f"{item.guia_convenio.numero}: sessão {sessoes} de {total}")
    return render(
        request,
        "agenda/autorizacoes.html",
        {
            "linhas": linhas,
            "pagina": pagina,
            "de": de,
            "ate": ate,
            "convenio_selecionado": convenio_id,
            "profissional_selecionado": profissional_id,
            "paciente_nome": paciente_nome,
            "convenios": Convenio.objects.filter(ativo=True).order_by("nome"),
            "profissionais": Profissional.objects.filter(ativo=True).order_by("nome"),
            "pode_cancelar": request.user.has_perm("accounts.agenda_cancelar_autorizado"),
        },
    )


@login_required
def guias_convenio(request):
    """Guias de sessões do plano (GuiaConvenio) com o saldo de cada uma — por padrão só as que
    ainda podem ser usadas. As guias nascem na tela de autorização; aqui dá pra conferir e corrigir."""
    if not _pode_ver_autorizacoes(request.user):
        raise PermissionDenied
    hoje = timezone.localdate()
    situacao = request.GET.get("situacao") or ""
    paciente_nome = (request.GET.get("paciente") or "").strip()
    convenio_id = request.GET.get("convenio") or ""

    qs = (
        GuiaConvenio.objects.select_related("paciente", "convenio", "procedimento", "criada_por")
        .annotate(
            usadas=Sum("itens__quantidade", filter=Q(itens__agendamento__status__in=Agendamento.STATUS_PRODUCAO))
        )
        .order_by("paciente__nome_completo", "-criada_em")
    )
    if paciente_nome:
        qs = qs.filter(paciente__nome_completo__icontains=paciente_nome)
    if convenio_id.isdigit():
        qs = qs.filter(convenio_id=convenio_id)
    if situacao != "todas":
        # Só as abertas (= GuiaConvenio.situacao_em "Aberta": não vencida e com saldo), já no
        # banco — pra paginar de 50 em 50 (pedido da clínica, 03/11/2026; antes eram até 2000).
        qs = qs.filter(Q(validade__isnull=True) | Q(validade__gte=hoje)).filter(
            quantidade_sessoes__gt=Coalesce("usadas", 0)
        )
    pagina = paginar(request, qs)
    guias = list(pagina.object_list)
    for guia in guias:
        guia._sessoes_usadas = guia.usadas or 0
        guia.situacao = guia.situacao_em(hoje)
    return render(
        request,
        "agenda/guias_convenio.html",
        {
            "guias": guias,
            "pagina": pagina,
            "situacao": situacao,
            "paciente_nome": paciente_nome,
            "convenio_selecionado": convenio_id,
            "convenios": Convenio.objects.filter(ativo=True).order_by("nome"),
            "pode_editar": request.user.has_perm("accounts.fila_autorizar_recepcao"),
        },
    )


@permission_required("accounts.fila_autorizar_recepcao", raise_exception=True)
def guia_convenio_editar(request, pk):
    guia = get_object_or_404(GuiaConvenio.objects.select_related("paciente", "convenio", "procedimento"), pk=pk)
    destino = _destino_seguro(request, reverse("agenda:guias_convenio"))
    if request.method == "POST":
        form = GuiaConvenioForm(request.POST, instance=guia)
        if form.is_valid():
            form.save()
            LogAuditoria.registrar(
                request,
                request.user,
                f"Editou a guia {guia.numero} ({guia.procedimento.nome}, {guia.convenio.nome}) de "
                f"'{guia.paciente.nome_completo}': {guia.quantidade_sessoes} sessões"
                + (f", válida até {guia.validade:%d/%m/%Y}" if guia.validade else ""),
            )
            messages.success(request, f"Guia {guia.numero} atualizada.")
            return redirect(destino)
    else:
        form = GuiaConvenioForm(instance=guia)
    return render(request, "agenda/guia_convenio_form.html", {"form": form, "guia": guia, "destino": destino})


# =====================================================================
# Pacotes vendidos (saldo por paciente)
# =====================================================================

def _restante_por_pacote(ids):
    """{pacote.pk: sessões restantes} — mesma conta de PacoteVendido.saldo/total_restante
    (usadas = itens de atendimento não cancelado/falta + usadas_antes, por procedimento),
    só que pra muitos pacotes em 2 consultas em vez de 2 por pacote."""
    usados = {
        (linha["pacote_vendido_id"], linha["procedimento_id"]): linha["total"]
        for linha in AgendamentoItem.objects.filter(pacote_vendido_id__in=ids)
        .exclude(agendamento__status__in=[Agendamento.Status.CANCELADO, Agendamento.Status.FALTOU])
        .values("pacote_vendido_id", "procedimento_id")
        .annotate(total=Sum("quantidade"))
    }
    restantes = {}
    for item in PacoteVendidoItem.objects.filter(pacote_vendido_id__in=ids).values(
        "pacote_vendido_id", "procedimento_id", "quantidade", "usadas_antes"
    ):
        usado = usados.get((item["pacote_vendido_id"], item["procedimento_id"]), 0) + item["usadas_antes"]
        restantes[item["pacote_vendido_id"]] = restantes.get(item["pacote_vendido_id"], 0) + max(
            item["quantidade"] - usado, 0
        )
    return restantes


@login_required
def pacotes_vendidos(request):
    """Pacotes já vendidos (ver agenda.models.PacoteVendido), com o saldo e o histórico de uso
    de cada um — pra recepção/financeiro responderem "quantas sessões ainda tenho?". Filtra
    por nome do paciente; por padrão esconde os já usados por inteiro."""
    usuario = request.user
    if not any(
        usuario.has_perm(f"accounts.{perm}")
        for perm in (
            "agenda_ver_dia", "agenda_criar", "agenda_editar", "fila_autorizar_financeiro", "pacote_paciente_cadastrar"
        )
    ):
        raise PermissionDenied
    termo = (request.GET.get("q") or "").strip()
    mostrar_todos = request.GET.get("todos") == "1"
    vendas = (
        PacoteVendido.objects.filter(Q(pago_em__isnull=False) | Q(cadastro_manual=True))
        .select_related("paciente", "profissional", "agendamento_venda")
        .prefetch_related("itens__procedimento", "itens_agendamento__agendamento", "itens_agendamento__procedimento")
        .order_by("-criado_em", "-pk")
    )
    if termo:
        for palavra in termo.split():
            vendas = vendas.filter(paciente__nome_completo__icontains=palavra)
    ativos = set(PacoteVendido.ativos().values_list("pk", flat=True))
    ids = list(vendas.values_list("pk", flat=True))
    restantes = _restante_por_pacote(ids)
    if not mostrar_todos:
        ids = [pk for pk in ids if pk in ativos and restantes.get(pk)]
    # 50 por página (pedido da clínica, 03/11/2026) — antes eram os 300 mais recentes numa
    # página só. O saldo de todos sai de 2 consultas (_restante_por_pacote); usos e saldo
    # detalhado, só dos 50 da página.
    pagina = paginar(request, ids)
    por_pk = vendas.in_bulk(list(pagina.object_list))
    linhas = []
    for pk in pagina.object_list:
        venda = por_pk[pk]
        ativo = pk in ativos
        restante = restantes.get(pk, 0) if ativo else 0
        usos = sorted(
            (item for item in venda.itens_agendamento.all()),
            key=lambda item: item.agendamento.data_hora_inicio,
        )
        linhas.append({"venda": venda, "ativo": ativo, "restante": restante, "usos": usos})
    return render(
        request,
        "agenda/pacotes_vendidos.html",
        {
            "linhas": linhas,
            "pagina": pagina,
            "termo": termo,
            "mostrar_todos": mostrar_todos,
            "pode_cadastrar": _pode_cadastrar_pacote_manual(usuario),
        },
    )


@require_POST
@login_required
def pacote_cancelar_manual(request, pk):
    """Cancela (apaga) um pacote do sistema antigo cadastrado por engano no paciente (pedido da
    clínica, 10/11/2026). Regras em PacoteVendido.motivo_nao_cancela. Os atendimentos futuros
    que usavam o saldo dele são revinculados (vincular_pacotes): ficam com outro pacote do
    paciente, se houver saldo, ou voltam a ser cobrados normalmente. Fica no log de auditoria."""
    if not _pode_cadastrar_pacote_manual(request.user):
        raise PermissionDenied
    venda = get_object_or_404(PacoteVendido.objects.select_related("paciente", "profissional"), pk=pk)
    destino = request.META.get("HTTP_REFERER") or reverse("agenda:pacotes_vendidos")
    motivo = venda.motivo_nao_cancela()
    if motivo:
        messages.error(request, motivo)
        return redirect(destino)
    with transaction.atomic():
        afetados = list(
            Agendamento.objects.filter(itens__pacote_vendido=venda).distinct()
        )
        descricao = (
            f"pacote do sistema antigo '{venda.nome}' de '{venda.paciente.nome_completo}' com "
            f"{venda.profissional.nome} (R$ {formatar_moeda(venda.valor)}; {venda.saldo_resumo})"
        )
        venda.delete()  # itens do pacote em CASCADE; AgendamentoItem.pacote_vendido fica NULL
        for agendamento in afetados:
            vincular_pacotes(agendamento)
    LogAuditoria.registrar(request, request.user, f"Cancelou o {descricao}")
    texto = f"Pacote '{venda.nome}' de {venda.paciente.nome_completo} cancelado."
    if afetados:
        texto += f" {len(afetados)} atendimento(s) marcado(s) que usavam este pacote voltam a ser cobrados normalmente."
    messages.success(request, texto)
    return redirect(destino)


def _pode_cadastrar_pacote_manual(usuario):
    # Permissão própria, marcada por perfil/usuário (pedido da clínica, 27/10/2026).
    return usuario.has_perm("accounts.pacote_paciente_cadastrar")


@login_required
def pacote_cadastrar_manual(request):
    """Pacote do sistema antigo cadastrado direto no paciente, sem passar por um atendimento
    (clínica, 27/10/2026 — pacientes que vieram com pacote do sistema antigo e só vão pagar
    depois de alguns atendimentos). Ver PacoteVendido.cadastro_manual."""
    if not _pode_cadastrar_pacote_manual(request.user):
        raise PermissionDenied
    pacotes = list(Pacote.objects.filter(ativo=True).prefetch_related("itens__procedimento").order_by("nome"))
    form = PacoteManualForm(request.POST or None)
    usadas = {}
    erros_usadas = []
    if request.method == "POST" and form.is_valid():
        pacote = form.cleaned_data["pacote"]
        profissional = form.cleaned_data["profissional"]
        paciente = form.cleaned_data["paciente"]
        itens_pacote = list(pacote.itens.select_related("procedimento"))
        for item in itens_pacote:
            texto = (request.POST.get(f"usadas_{item.pk}") or "0").strip()
            if not texto.isdigit() or int(texto) > item.quantidade:
                erros_usadas.append(
                    f"{item.procedimento.nome}: informe quantas já foram usadas, de 0 a {item.quantidade}."
                )
            else:
                usadas[item.pk] = int(texto)
        erro = _erro_pacote_do_profissional(pacote, itens_pacote, profissional)
        if erro:
            erros_usadas.append(erro)
        if not erros_usadas:
            cobra_fixo = profissional.cobra_valor_fixo_particular and not profissional.eh_proprietario
            ja_pago = form.cleaned_data["situacao"] == PacoteManualForm.PAGO
            with transaction.atomic():
                venda = PacoteVendido.objects.create(
                    pacote=pacote,
                    nome=pacote.nome,
                    paciente=paciente,
                    profissional=profissional,
                    agendamento_venda=None,
                    cadastro_manual=True,
                    valor=form.cleaned_data["valor"],
                    valor_repasse_fixo=pacote.valor_repasse if cobra_fixo else None,
                    pago_em=timezone.now() if ja_pago else None,
                    criado_por=request.user,
                )
                PacoteVendidoItem.objects.bulk_create(
                    PacoteVendidoItem(
                        pacote_vendido=venda,
                        procedimento=item.procedimento,
                        quantidade=item.quantidade,
                        usadas_antes=usadas[item.pk],
                    )
                    for item in itens_pacote
                )
                cobertos = vincular_pacotes_futuros(paciente, profissional)
            LogAuditoria.registrar(
                request,
                request.user,
                f"Cadastrou pacote do sistema antigo '{venda.nome}' para '{paciente.nome_completo}' com "
                f"{profissional.nome} (R$ {formatar_moeda(venda.valor)}, {'já pago' if ja_pago else 'a pagar'}; {venda.saldo_resumo})",
            )
            messages.success(
                request,
                f"Pacote '{venda.nome}' cadastrado para '{paciente.nome_completo}' ({venda.saldo_resumo})"
                + ("." if ja_pago else " — fica a pagar: o financeiro cobra num próximo atendimento.")
                + (f" {cobertos} atendimento(s) já marcado(s) passaram a usar o pacote." if cobertos else ""),
            )
            return redirect(reverse("agenda:pacotes_vendidos") + "?" + urlencode({"q": paciente.nome_completo}))
    for pacote in pacotes:
        for item in pacote.itens.all():
            item.usadas_digitadas = request.POST.get(f"usadas_{item.pk}", "0")
    return render(
        request,
        "agenda/pacote_cadastrar_manual.html",
        {
            "form": form,
            "pacotes": pacotes,
            "erros_usadas": erros_usadas,
            "paciente_escolhido": form.cleaned_data.get("paciente") if form.is_bound else None,
        },
    )
