from types import SimpleNamespace
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import LogAuditoria
from agenda.models import Agendamento
from cadastros.models import (
    AtendimentoGuiche,
    AtendimentoProfissional,
    Profissional,
    Sala,
    especialidade_a_partir_da_chave,
    especialidade_emoji,
    especialidade_rotulo,
)

from . import voz
from .models import Senha

# Ícone/rótulo em linguagem simples de cada motivo — o ícone é só um apoio
# visual de leitura rápida, o texto é sempre a informação principal. "Pessoa
# obesa ou pessoa com mobilidade reduzida" fica sem ícone por não existir um
# símbolo neutro/respeitoso consagrado para isso. O ícone do Autismo é o
# caminho de uma imagem em static/ (símbolo do espectro autista fornecido
# pela clínica) em vez de um emoji — por isso tem "/" — e a tela de
# prioridade (totem_prioridade.html) decide entre <img> e texto olhando pra
# isso.
_ICONE_ROTULO_MOTIVO = {
    Senha.Motivo.IDOSO_80: ("🧓", "Idoso + 80"),
    Senha.Motivo.DEFICIENCIA: ("♿", "Pessoa com deficiência ou cadeirantes"),
    Senha.Motivo.AUTISMO: ("accounts/img/simbolo_autismo.png", "Pessoas com transtorno do espectro autista"),
    Senha.Motivo.IDOSO_60: ("🧓", "Idoso (60 anos ou mais)"),
    Senha.Motivo.GESTANTE: ("🤰", "Gestantes"),
    Senha.Motivo.LACTANTE: ("🤱", "Lactantes"),
    Senha.Motivo.CRIANCA_COLO: ("👶", "Acompanhada de criança de colo"),
    Senha.Motivo.OBESIDADE: ("", "Pessoa obesa ou Pessoa com mobilidade reduzida"),
}

# (valor, ícone, rótulo) — ordem dos cartões na tela de prioridade do totem.
# Vem de Senha.ORDEM_PRIORIDADE (a mesma ordem que chamar_proxima usa de
# verdade pra decidir quem chamar primeiro, Lei nº 10.048/2000) em vez de uma
# lista própria — pra tela e chamada real nunca mais desalinharem entre si
# (é o que tinha acontecido antes de 2026-07-20).
MOTIVOS_PRIORIDADE = [(motivo, *_ICONE_ROTULO_MOTIVO[motivo]) for motivo in Senha.ORDEM_PRIORIDADE]


def _hora_agora():
    """Hora local agora — o totem só oferece especialidade de quem ainda não terminou o
    horário de atendimento (pedido da clínica, 23/10/2026; ver Profissional.atende_ainda_em)."""
    return timezone.localtime().time()


def totem_especialidade(request):
    """
    Primeira tela do totem: o paciente escolhe a especialidade que veio
    buscar. Só oferece especialidades com pelo menos um profissional
    atendendo HOJE (ver Profissional.especialidades_ativas_em) — mesma fonte
    do cadastro de profissional (dias/especialidades em que ele atende),
    nunca uma lista fixa.

    Mostra também, à parte, um cartão por EXAME AVULSO com agendamento de
    verdade hoje (ver Agendamento.exames_avulsos_agendados_em/cadastros.models.
    Procedimento.exame_sem_profissional — ex: MAPA) — decisão da clínica,
    2026-07-30: esses exames são feitos pela equipe mesmo em dias sem nenhum
    profissional daquela especialidade atendendo, então não podem depender de
    especialidades_ativas_em pra aparecer. Cada cartão de exame carrega, além
    da especialidade, o id do Procedimento (ver totem_emitir) — é o que marca
    a senha como "Exame: X" em vez de uma consulta normal.
    """
    hoje = timezone.localdate()
    especialidades = [
        (chave, rotulo, especialidade_emoji(especialidade_a_partir_da_chave(chave)[0]))
        for chave, rotulo in Profissional.especialidades_ativas_em(hoje, _hora_agora())
    ]
    exames = [
        {
            "procedimento_id": exame["procedimento"].pk,
            "nome": exame["procedimento"].nome,
            "especialidade_chave": f"{exame['especialidade']}|{exame['especialidade_outra']}",
            "emoji": especialidade_emoji(exame["especialidade"]),
        }
        for exame in Agendamento.exames_avulsos_agendados_em(hoje)
    ]
    return render(request, "fila/totem_inicio.html", {"especialidades": especialidades, "exames": exames})


def _profissionais_para_escolher(especialidade_chave, data):
    """Profissionais atendendo hoje nesta especialidade, quando são 2 ou mais — é aí que o
    totem pergunta "Com qual profissional?" (pedido da clínica, 2026-10-16). Com 0 ou 1,
    devolve [] e o totem segue direto, como antes."""
    profissionais = Profissional.presentes_por_especialidade(data, _hora_agora()).get(especialidade_chave, [])
    return profissionais if len(profissionais) > 1 else []


def totem_profissional(request):
    """Tela "Com qual profissional?" — só aparece (ver totem_tipo) quando a especialidade
    escolhida tem 2+ profissionais hoje. "Não sei / qualquer um" segue sem profissional:
    qualquer atendente daquela especialidade chama."""
    especialidade = request.GET.get("especialidade", "")
    profissionais = _profissionais_para_escolher(especialidade, timezone.localdate())
    if not profissionais:
        return redirect(f"{reverse('fila:totem_tipo')}?{urlencode({'especialidade': especialidade, 'profissional': '0'})}")
    contexto = {
        "especialidade": especialidade,
        "rotulo_especialidade": especialidade_rotulo(*especialidade_a_partir_da_chave(especialidade)),
        "profissionais": profissionais,
    }
    return render(request, "fila/totem_profissional.html", contexto)


def totem_tipo(request):
    especialidade = request.GET.get("especialidade", "")
    procedimento = request.GET.get("procedimento", "")
    # "profissional" ausente = ainda não passou pela pergunta; "0" = "Não sei"/sem pergunta.
    profissional = request.GET.get("profissional")
    if profissional is None and not procedimento and _profissionais_para_escolher(especialidade, timezone.localdate()):
        return redirect(f"{reverse('fila:totem_profissional')}?{urlencode({'especialidade': especialidade})}")
    contexto = {
        "tipo_normal": Senha.Tipo.NORMAL,
        "tipo_exame_preparo": Senha.Tipo.EXAME_PREPARO,
        # Senha "Exame com preparo" (pedido da clínica, 2026-10-26) só aparece onde há de
        # fato um exame desses marcado hoje — ver Agendamento.especialidades_com_exame_com_preparo_em.
        "oferece_exame_preparo": not procedimento
        and especialidade in Agendamento.especialidades_com_exame_com_preparo_em(timezone.localdate()),
        "especialidade": especialidade,
        "procedimento": procedimento,
        "profissional": profissional or "",
    }
    return render(request, "fila/totem_tipo.html", contexto)


def totem_prioridade(request):
    especialidade = request.GET.get("especialidade", "")
    procedimento = request.GET.get("procedimento", "")
    contexto = {
        "motivos": MOTIVOS_PRIORIDADE,
        "tipo_prioritario": Senha.Tipo.PRIORITARIO,
        "especialidade": especialidade,
        "procedimento": procedimento,
        "profissional": request.GET.get("profissional", ""),
    }
    return render(request, "fila/totem_prioridade.html", contexto)


@require_POST
def totem_emitir(request):
    """
    Único jeito de criar uma Senha. Sempre POST (nunca GET), assim um
    refresh/"voltar" do navegador na tela seguinte (totem_senha_ver) não gera
    uma senha nova — só reexibe a mesma pelo pk na URL.
    """
    tipo = request.POST.get("tipo")
    if tipo not in Senha.Tipo.values:
        return redirect("fila:totem_inicio")

    motivo = request.POST.get("motivo", "")
    if tipo == Senha.Tipo.PRIORITARIO:
        if motivo not in Senha.Motivo.values:
            return redirect("fila:totem_prioridade")
    else:
        motivo = ""

    hoje = timezone.localdate()
    procedimento_id = request.POST.get("procedimento", "")
    if tipo == Senha.Tipo.EXAME_PREPARO and (
        procedimento_id
        or request.POST.get("especialidade", "") not in Agendamento.especialidades_com_exame_com_preparo_em(hoje)
    ):
        # Mesma reconferência no servidor das demais: só onde há exame com preparo marcado hoje.
        return redirect("fila:totem_inicio")

    if procedimento_id:
        # Exame avulso (cartão "Exame" de totem_inicio.html, ver Procedimento.
        # exame_sem_profissional) — reconfere no servidor contra os exames com
        # agendamento de VERDADE hoje (mesmo espírito da checagem de especialidade
        # abaixo: nunca confia no que veio marcado na tela anterior). especialidade/
        # especialidade_outra vêm DAQUI, nunca de um campo "especialidade" submetido
        # pelo cliente — o exame já sabe a especialidade certa (Agendamento.
        # exames_avulsos_agendados_em), não precisa (nem deve) confiar em outra fonte.
        exames_hoje = {str(exame["procedimento"].pk): exame for exame in Agendamento.exames_avulsos_agendados_em(hoje)}
        exame = exames_hoje.get(procedimento_id)
        if not exame:
            return redirect("fila:totem_inicio")

        senha = Senha.emitir(
            tipo=tipo,
            motivo_prioridade=motivo,
            especialidade=exame["especialidade"],
            especialidade_outra=exame["especialidade_outra"],
            procedimento_exame=exame["procedimento"],
        )
        return redirect("fila:totem_senha_ver", pk=senha.pk)

    # Reconfere no servidor contra as especialidades disponíveis HOJE (mesmo
    # espírito da checagem de tipo/motivo acima) — nunca confia só no que veio
    # marcado na tela anterior, já que a lista pode ter mudado entre uma
    # requisição e outra.
    especialidade_chave = request.POST.get("especialidade", "")
    especialidades_hoje = dict(Profissional.especialidades_ativas_em(hoje, _hora_agora()))
    if especialidade_chave not in especialidades_hoje:
        return redirect("fila:totem_inicio")
    especialidade, especialidade_outra = especialidade_a_partir_da_chave(especialidade_chave)

    # Profissional escolhido no totem: reconfere que ele atende hoje nesta especialidade
    # (mesmo cuidado acima). Qualquer valor que não bata ("0" = "Não sei") fica sem profissional.
    profissional_id = request.POST.get("profissional", "")
    profissional = next(
        (p for p in _profissionais_para_escolher(especialidade_chave, hoje) if str(p.pk) == profissional_id), None
    )

    senha = Senha.emitir(
        tipo=tipo,
        motivo_prioridade=motivo,
        especialidade=especialidade,
        especialidade_outra=especialidade_outra,
        profissional=profissional,
    )
    return redirect("fila:totem_senha_ver", pk=senha.pk)


def totem_senha_ver(request, pk):
    senha = get_object_or_404(Senha, pk=pk)
    contexto = {
        "senha": senha,
        "segundos_redirecionar": settings.FILA_TOTEM_REDIRECT_SEGUNDOS,
    }
    return render(request, "fila/totem_senha.html", contexto)


def _nomes_profissionais(ids):
    return ", ".join(Profissional.objects.filter(pk__in=ids).order_by("nome").values_list("nome", flat=True))


@permission_required("accounts.fila_chamar_senha", raise_exception=True)
def painel_chamada(request):
    """
    Painel da atendente pra chamar a próxima senha e concluir o atendimento.
    Guichê e especialidades de hoje continuam só configurados na Agenda do
    dia (agenda.views.agenda_dia, formulários "estou no guichê"/"especialidades
    de hoje") — esta tela só lê o que já foi escolhido lá (AtendimentoGuiche.
    guiche_em / AtendimentoProfissional.cobertura_em), pra não duplicar
    essa escolha em dois lugares. Sem @never_cache: accounts.middleware.
    DesabilitarCacheMiddleware já cobre toda resposta do sistema.
    """
    hoje = timezone.localdate()
    guiche_de_hoje = AtendimentoGuiche.guiche_em(request.user, hoje)
    # (especialidade, outra, profissionais) — os profissionais marcados restringem a fila
    # daquela especialidade (ver AtendimentoProfissional, pedido da clínica de 2026-10-16).
    especialidades_hoje = AtendimentoProfissional.cobertura_em(request.user, hoje)

    senha_atual = (
        Senha.objects.filter(chamado_por=request.user, status=Senha.Status.CHAMADA, data=hoje)
        .order_by("-chamado_em")
        .first()
    )

    if request.method == "POST":
        acao = request.POST.get("acao")

        if acao == "chamar" and not senha_atual and guiche_de_hoje and especialidades_hoje:
            senha_atual = Senha.chamar_proxima(request.user, guiche_de_hoje, especialidades_hoje, hoje)
            if senha_atual:
                messages.success(request, f"Senha {senha_atual.codigo} chamada — {guiche_de_hoje.nome}.")
            else:
                messages.info(request, "Não há nenhuma senha aguardando nas suas especialidades de hoje.")

        elif acao == "concluir" and senha_atual:
            messages.success(request, f"Atendimento da senha {senha_atual.codigo} concluído.")
            senha_atual.concluir_atendimento()
            senha_atual = None

        elif acao == "chamar_novamente" and senha_atual:
            senha_atual.chamar_novamente()
            messages.success(request, f"Senha {senha_atual.codigo} chamada novamente.")

        elif acao == "rechamar":
            # Qualquer senha já chamada hoje (lista abaixo), por quem tem acesso a esta tela — volta
            # ao painel da TV com o guichê de quem a chamou antes (pedido da clínica, 26/10/2026).
            senha = Senha.objects.filter(
                pk=request.POST.get("senha_id") or 0, data=hoje, chamado_em__isnull=False
            ).select_related("guiche").first()
            if senha:
                senha.chamar_novamente()
                destino = f" — {senha.guiche.nome}" if senha.guiche else ""
                messages.success(request, f"Senha {senha.codigo} chamada novamente{destino}.")

        return redirect("fila:painel_chamada")

    chamadas_hoje = (
        Senha.objects.filter(data=hoje, chamado_em__isnull=False)
        .select_related("guiche", "chamado_por", "procedimento_exame", "profissional")
        .order_by("-chamado_em")
    )

    contexto = {
        **_contexto_fila_espera(request, hoje, guiche_de_hoje, senha_atual),
        "guiche_de_hoje": guiche_de_hoje,
        "especialidades_hoje_rotulos": [
            especialidade_rotulo(e, o) + (f" (só {_nomes_profissionais(ids)})" if ids else "")
            for e, o, ids in especialidades_hoje
        ],
        "pode_chamar": bool(guiche_de_hoje and especialidades_hoje),
        "senha_atual": senha_atual,
        "chamadas_hoje": chamadas_hoje,
    }
    return render(request, "fila/painel_chamada.html", contexto)


def _senha_em_atendimento(usuario, data):
    return (
        Senha.objects.filter(chamado_por=usuario, status=Senha.Status.CHAMADA, data=data)
        .order_by("-chamado_em")
        .first()
    )


def _contexto_fila_espera(request, hoje, guiche_de_hoje=None, senha_atual=None):
    """
    Lista "Fila de espera" (pedido da clínica, 10/11/2026): quem ainda aguarda ser chamado
    hoje, por ordem de chegada, com a hora em que tirou a senha e há quanto tempo espera.
    Só aparece pra quem tem `fila_ver_aguardando` (ativada por usuário em Usuários/Perfis).
    O botão "Chamar esta" (chamada manual, fora da ordem automática) exige também
    `fila_chamar_manual` + `fila_chamar_senha` (é no painel de chamada que se conclui), um
    guichê escolhido hoje e nenhuma senha em atendimento pela pessoa.
    """
    usuario = request.user
    if not usuario.has_perm("accounts.fila_ver_aguardando"):
        return {"mostra_fila_espera": False}

    agora = timezone.now()
    aguardando = list(
        Senha.objects.filter(data=hoje, status=Senha.Status.AGUARDANDO)
        .select_related("procedimento_exame", "profissional")
        .order_by("criado_em", "pk")
    )
    for senha in aguardando:
        minutos = max(0, int((agora - senha.criado_em).total_seconds() // 60))
        senha.espera_texto = f"{minutos // 60}h{minutos % 60:02d}" if minutos >= 60 else f"{minutos} min"

    pode_chamar_manual = usuario.has_perm("accounts.fila_chamar_manual") and usuario.has_perm(
        "accounts.fila_chamar_senha"
    )
    if pode_chamar_manual and guiche_de_hoje is None:
        guiche_de_hoje = AtendimentoGuiche.guiche_em(usuario, hoje)
    if pode_chamar_manual and senha_atual is None:
        senha_atual = _senha_em_atendimento(usuario, hoje)

    aviso_manual = ""
    if pode_chamar_manual and not guiche_de_hoje:
        aviso_manual = "Para chamar uma senha manualmente, escolha antes o seu guichê de hoje na Agenda do dia."
    elif pode_chamar_manual and senha_atual:
        aviso_manual = f"Conclua o atendimento da senha {senha_atual.codigo} antes de chamar outra."

    return {
        "mostra_fila_espera": True,
        "fila_aguardando": aguardando,
        "pode_chamar_manual": pode_chamar_manual,
        "chamada_manual_liberada": pode_chamar_manual and not aviso_manual,
        "aviso_chamada_manual": aviso_manual,
    }


@permission_required("accounts.fila_ver_aguardando", raise_exception=True)
def fila_espera(request):
    """Tela própria da "Fila de espera" — pra quem acompanha a fila sem usar o painel de
    chamada (ex.: coordenação). Recarrega sozinha a cada 30s (ver template)."""
    hoje = timezone.localdate()
    return render(request, "fila/fila_espera.html", _contexto_fila_espera(request, hoje))


@require_POST
@permission_required(["accounts.fila_chamar_manual", "accounts.fila_chamar_senha"], raise_exception=True)
def chamar_manual(request):
    """Chama a senha escolhida na Fila de espera (ver Senha.chamar_especifica) e leva ao
    painel de chamada, onde a atendente conclui o atendimento. Fica no log de auditoria."""
    hoje = timezone.localdate()
    guiche = AtendimentoGuiche.guiche_em(request.user, hoje)
    senha_atual = _senha_em_atendimento(request.user, hoje)
    if not guiche:
        messages.error(request, "Escolha o seu guichê de hoje na Agenda do dia antes de chamar uma senha.")
    elif senha_atual:
        messages.error(request, f"Conclua o atendimento da senha {senha_atual.codigo} antes de chamar outra.")
    else:
        senha = Senha.chamar_especifica(request.user, guiche, request.POST.get("senha_id") or 0, hoje)
        if senha:
            LogAuditoria.registrar(
                request, request.user, f"Chamou manualmente a senha {senha.codigo} (fora da ordem) — {guiche.nome}"
            )
            messages.success(request, f"Senha {senha.codigo} chamada manualmente — {guiche.nome}.")
        else:
            messages.info(request, "Essa senha não está mais aguardando (já foi chamada por alguém).")
    return redirect("fila:painel_chamada")


def _chamadas_recepcao_propria(data):
    """
    Uma chamada por lote (Agendamento.lote_recepcao) ainda com paciente Autorizado, pra o
    painel de espera anunciar a leva inteira de uma vez: quem entra no consultório (o de
    menor `ordem_no_lote`) e quem aguarda sentado na Recepção 2. `chamado_em` do lote é o
    mais recente entre os membros — uma nova leva no mesmo lote, ou uma nova chamada de um
    membro, faz o painel anunciar de novo. Não é um model: só um objeto com os atributos que
    painel_espera.html lê (mesmo espírito do 'tipo_chamada' em memória, ver painel_espera).
    """
    membros = list(
        Agendamento.objects.filter(
            lote_recepcao__isnull=False,
            chamado_em__date=data,
            status=Agendamento.Status.AUTORIZADO,
        )
        .select_related("paciente", "profissional", "sala_chamada")
        .order_by("ordem_no_lote")
    )
    lotes = {}
    for membro in membros:
        lotes.setdefault(membro.lote_recepcao, []).append(membro)

    chamadas = []
    for lote, integrantes in lotes.items():
        chamadas.append(
            SimpleNamespace(
                pk=str(lote),
                tipo_chamada="recepcao_propria",
                chamado_em=max(m.chamado_em for m in integrantes),
                nome_recepcao=Sala.NOME_RECEPCAO_PROPRIA,
                sala_chamada=integrantes[0].sala_chamada,
                profissional=integrantes[0].profissional,
                primeiro=integrantes[0].paciente,
                demais=[m.paciente for m in integrantes[1:]],
            )
        )
    return chamadas


def painel_espera(request):
    """
    Painel público de espera (TV/monitor da sala de espera) — sem login,
    mesmo espírito público do totem. Mostra a chamada mais recente em
    destaque e o histórico das últimas chamadas de hoje. Atualiza sozinho a
    cada 5s via fetch da própria página (ver template — recarregar a página
    inteira bloquearia a voz/bipe do anúncio). Sem @never_cache:
    accounts.middleware.DesabilitarCacheMiddleware já cobre toda resposta.

    Mescla dois tipos de chamada, que nunca se tocam no banco (fila.Senha e
    agenda.Agendamento são modelos independentes): as senhas do totem,
    chamadas pela recepção (fila:painel_chamada), e os agendamentos chamados
    direto pelo profissional (agenda.views.agendamento_chamar). Cada item
    ganha um atributo 'tipo_chamada' (não é campo de banco, só marcação em
    memória pra esta tela) pra o template decidir o estilo visual. Mesclado e
    ordenado em Python (não dá pra fazer um único ORDER BY entre duas tabelas
    diferentes) — volume de um dia de clínica é pequeno, sem necessidade de
    UNION em SQL bruto.
    """
    hoje = timezone.localdate()

    chamadas_senha = list(
        Senha.objects.filter(data=hoje, chamado_em__isnull=False)
        .select_related("guiche", "procedimento_exame", "profissional")
        .order_by("-chamado_em")[:5]
    )
    for chamada in chamadas_senha:
        chamada.tipo_chamada = "senha"

    # Chamada em lote pra recepção própria de uma sala (agenda.views.agendamento_chamar_recepcao)
    # fica de fora das chamadas individuais: sai daqui como UMA chamada só, abaixo.
    chamadas_agendamento = list(
        Agendamento.objects.filter(chamado_em__isnull=False, chamado_em__date=hoje, lote_recepcao__isnull=True)
        .select_related("paciente", "profissional", "sala_chamada")
        .order_by("-chamado_em")[:5]
    )
    for chamada in chamadas_agendamento:
        chamada.tipo_chamada = "agendamento"

    chamadas_recepcao_propria = _chamadas_recepcao_propria(hoje)

    # Últimas 5 chamadas de hoje no total (1 em destaque + histórico dos
    # últimos 4), não 5 de cada tipo — client pediu histórico combinado.
    chamadas = sorted(chamadas_senha + chamadas_agendamento + chamadas_recepcao_propria, key=lambda c: c.chamado_em, reverse=True)[:5]

    contexto = {
        "chamada_atual": chamadas[0] if chamadas else None,
        "chamadas_anteriores": chamadas[1:],
    }
    voz.aquecer_em_segundo_plano()
    return render(request, "fila/painel_espera.html", contexto)


def painel_voz(request, tipo, chave):
    """
    Áudio (.wav) da chamada `tipo`/`chave` de hoje, falado pelo servidor (ver fila/voz.py) —
    o painel da TV toca isso em vez da voz do navegador. Público como o painel. 404 se a
    chamada não existe; 503 se o servidor não tem a voz instalada (o painel então cai pra
    voz do navegador).
    """
    texto = voz.texto_da_chamada(tipo, chave)
    if texto is None:
        raise Http404
    dados = voz.gerar_wav(texto)
    if dados is None:
        return HttpResponse("Voz do servidor não instalada.", status=503, content_type="text/plain; charset=utf-8")
    return HttpResponse(dados, content_type="audio/wav")
