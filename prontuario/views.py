from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.models import LogAuditoria
from accounts.paginacao import paginar
from agenda.models import Agendamento
from cadastros.busca_pacientes import buscar_ids_pacientes
from cadastros.models import Paciente, Profissional
from documentos.models import DocumentoEmitido

from .forms import EntradaProntuarioForm
from .models import EntradaProntuario, usa_prontuario_simplificado


def _exige_profissional(view_func):
    """
    Prontuário é uma área EXCLUSIVA de quem está vinculado a um cadastro de
    Profissional (Usuario.profissional_id) — pedido explícito do cliente: não
    é uma permissão configurável por perfil, nem o Administrador tem acesso
    por perfil (ver accounts/permissoes.py, onde "Prontuário" foi removido de
    DEPARTAMENTOS_PERMISSOES de propósito).
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.profissional_id:
            raise PermissionDenied("O prontuário é uma área exclusiva do profissional vinculado ao atendimento.")
        return view_func(request, *args, **kwargs)

    return wrapper


def _bloqueia_paciente_inativo(request, paciente):
    """
    Pedido do cliente: ao inativar um paciente (alternativa à exclusão, que
    esbarra nos agendamentos protegidos — ver cadastros.views.paciente_excluir),
    o prontuário dele "some" também — deixa de aparecer na busca e no acesso
    direto por URL. Os registros continuam no banco (nunca apagamos histórico
    clínico), só ficam inacessíveis enquanto o paciente estiver inativo;
    reativar o cadastro devolve o acesso. Devolve True (e já manda a mensagem)
    quando o acesso deve ser bloqueado.
    """
    if not paciente.ativo:
        messages.error(
            request,
            f"'{paciente.nome_completo}' está com o cadastro inativo — o prontuário não fica disponível "
            "enquanto isso. Reative o cadastro em Pacientes → Editar se precisar acessá-lo.",
        )
        return True
    return False


@_exige_profissional
def prontuario_lista(request):
    """
    Busca de pacientes para o profissional abrir o prontuário diretamente —
    sem precisar passar por um atendimento específico marcado como Realizado
    (ver entrada_nova). Mesma busca por nome/CPF usada em
    cadastros.paciente_lista, mas sem os links de editar/excluir cadastro
    (isso é exclusivo da Recepção) — aqui só o acesso ao prontuário.

    Só lista pacientes ativos — paciente inativado "some" do prontuário
    também (ver _bloqueia_paciente_inativo).
    """
    termo = request.GET.get("q", "").strip()
    # unificado_em: cópia de cadastro duplicado, já sem histórico (foi tudo pra ficha
    # principal — ver cadastros unificar_pacientes_duplicados). Cadastro do sistema antigo
    # só com o nome continua aparecendo aqui de propósito: o profissional consulta tudo.
    filtro = Q(ativo=True, unificado_em__isnull=True)
    if not termo:
        pacientes = Paciente.objects.filter(filtro).select_related("convenio").order_by("nome_completo")
        return render(request, "prontuario/prontuario_lista.html", {"pagina": paginar(request, pacientes), "termo": termo})
    # Busca sem diferenciar acento, palavras em qualquer ordem e CPF com ou sem pontuação
    # (clínica, 28/10/2026: "joao" não achava "João" — ver cadastros.busca_pacientes).
    pagina = paginar(request, buscar_ids_pacientes(termo, filtro, "prontuario"))
    por_id = Paciente.objects.select_related("convenio").in_bulk(pagina.object_list)
    pagina.object_list = [por_id[pk] for pk in pagina.object_list if pk in por_id]
    return render(request, "prontuario/prontuario_lista.html", {"pagina": pagina, "termo": termo})


@_exige_profissional
def prontuario_paciente(request, paciente_pk):
    """
    Linha do tempo do prontuário do paciente, agrupada por data/especialidade/
    profissional — respeita a regra de visibilidade (ver topo de prontuario/models.py
    e EntradaProntuarioQuerySet.visiveis_para): registro médico é visível a todo
    profissional; o da equipe multi e o de saúde mental só o autor vê — os outros nem
    sabem que ele existe.
    """
    paciente = get_object_or_404(Paciente, pk=paciente_pk)
    if paciente.unificado_em_id:
        # Link antigo pra uma cópia unificada — o histórico inteiro está na ficha principal.
        return redirect("prontuario:prontuario_paciente", paciente_pk=paciente.unificado_em_id)
    if _bloqueia_paciente_inativo(request, paciente):
        return redirect("prontuario:prontuario_lista")
    entradas = (
        EntradaProntuario.objects.filter(paciente=paciente)
        .visiveis_para(request.user)
        .select_related("profissional", "agendamento")
    )
    documentos = (
        DocumentoEmitido.objects.filter(paciente=paciente)
        .visiveis_para(request.user)
        .select_related("profissional")
    )
    return render(
        request,
        "prontuario/prontuario_paciente.html",
        {
            "paciente": paciente,
            "atendimento_autorizado": _atendimento_autorizado(paciente, request.user.profissional_id),
            "entradas": entradas,
            "documentos": documentos,
            # Quem chegou aqui já é um profissional (ver _exige_profissional) — pode
            # sempre criar um registro novo; editar um já existente é restrito a quem
            # é o autor dele (ver entrada_editar e o próprio template, que só mostra o
            # link "Editar" quando entrada.profissional_id == profissional_logado_id).
            "pode_editar": True,
            "profissional_logado_id": request.user.profissional_id,
        },
    )


def _atendimento_autorizado(paciente, profissional_id):
    """Atendimento cujo plano autorizado aparece no topo do prontuário (pedido da clínica,
    23/10/2026): o de hoje com o profissional logado; senão qualquer um de hoje; senão o
    último autorizado do paciente. Só conta Autorizado/Realizado (STATUS_PRODUCAO)."""
    autorizados = (
        Agendamento.objects.filter(paciente=paciente, status__in=Agendamento.STATUS_PRODUCAO)
        .select_related("convenio", "profissional")
        .order_by("-data_hora_inicio")
    )
    de_hoje = autorizados.filter(data_hora_inicio__date=timezone.localdate())
    return (
        de_hoje.filter(profissional_id=profissional_id).first() or de_hoje.first() or autorizados.first()
    )


def _voltar_url(request, padrao):
    """Tela de onde o profissional veio (ex.: Minha agenda), pra voltar depois de salvar.
    No GET vem do Referer; no POST vem do campo oculto 'voltar' do formulário — o Referer
    do POST é a própria tela do formulário, e voltar pra ela mostrava o formulário vazio de
    novo depois de salvar (parecia que não tinha gravado, convidando a registrar em dobro)."""
    candidato = (
        request.POST.get("voltar") if request.method == "POST"
        # ?voltar= vem do link de trocar entre formulário simplificado/completo, cujo Referer é o próprio formulário.
        else request.GET.get("voltar") or request.META.get("HTTP_REFERER")
    )
    if candidato and url_has_allowed_host_and_scheme(
        candidato, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ) and candidato != request.build_absolute_uri() and candidato != request.get_full_path():
        return candidato
    return padrao


def _completo_escolhido(request):
    return bool((request.POST if request.method == "POST" else request.GET).get("completo"))


def _simplificado(request, especialidade):
    """Equipe multi usa o formulário de campo único (ver usa_prontuario_simplificado), mas
    pode abrir o completo pelo link do formulário (?completo=1, mantido no POST)."""
    if _completo_escolhido(request):
        return False
    return usa_prontuario_simplificado(especialidade)


def _profissional_do_usuario(request):
    if request.user.profissional_id:
        return Profissional.objects.filter(pk=request.user.profissional_id).first()
    return None


@_exige_profissional
def entrada_nova(request, agendamento_pk):
    """
    Registro no prontuário a partir de um agendamento já marcado como
    "Realizado" — preenche paciente/profissional/agendamento/especialidade
    automaticamente. É sempre OPCIONAL (o atendimento já foi marcado como
    realizado antes de chegar aqui; ninguém é impedido de pular esta etapa).
    """
    agendamento = get_object_or_404(Agendamento, pk=agendamento_pk)
    if _bloqueia_paciente_inativo(request, agendamento.paciente):
        return redirect(request.META.get("HTTP_REFERER") or reverse("agenda:agenda_dia"))
    profissional = _profissional_do_usuario(request) or agendamento.profissional
    voltar_url = _voltar_url(request, reverse("prontuario:prontuario_paciente", args=[agendamento.paciente_id]))
    simplificado = _simplificado(request, agendamento.especialidade_exibicao)
    data_padrao = min(timezone.localdate(agendamento.data_hora_inicio), timezone.localdate())

    if request.method == "POST":
        form = EntradaProntuarioForm(request.POST, simplificado=simplificado, data_padrao=data_padrao)
        if form.is_valid():
            entrada = form.save(commit=False)
            entrada.paciente = agendamento.paciente
            entrada.profissional = profissional
            # Especialidade DESTE atendimento (não mais "a" especialidade do profissional —
            # ele pode ter mais de uma, ver cadastros.models.Profissional.especialidades_pares):
            # Agendamento.especialidade já resolve qual das duas vale aqui.
            entrada.especialidade = agendamento.especialidade_exibicao
            entrada.agendamento = agendamento
            entrada.save()
            messages.success(request, "Registro adicionado ao prontuário.")
            return redirect(voltar_url)
    else:
        form = EntradaProntuarioForm(simplificado=simplificado, data_padrao=data_padrao)

    return render(
        request,
        "prontuario/entrada_form.html",
        {
            "form": form,
            "titulo": "Registro no prontuário",
            "agendamento": agendamento,
            "paciente": agendamento.paciente,
            "profissional": profissional,
            "voltar_url": voltar_url,
            "simplificado": simplificado,
            "completo_escolhido": _completo_escolhido(request),
        },
    )


@_exige_profissional
def entrada_nova_paciente(request, paciente_pk):
    """Mesmo formulário acima, mas iniciado direto da tela de prontuário do paciente
    (sem vínculo com um agendamento específico) — usado quando o profissional quer
    registrar algo que não corresponde a um atendimento agendado (ex: anotação avulsa)."""
    paciente = get_object_or_404(Paciente, pk=paciente_pk)
    if _bloqueia_paciente_inativo(request, paciente):
        return redirect("prontuario:prontuario_lista")
    profissional = _profissional_do_usuario(request)

    voltar_url = reverse("prontuario:prontuario_paciente", args=[paciente.pk])
    simplificado = _simplificado(request, profissional.especialidade_exibicao)

    if request.method == "POST":
        form = EntradaProntuarioForm(request.POST, simplificado=simplificado)
        if form.is_valid():
            entrada = form.save(commit=False)
            entrada.paciente = paciente
            entrada.profissional = profissional
            entrada.especialidade = profissional.especialidade_exibicao
            # Clínica, 28/10/2026: o profissional muitas vezes evolui por aqui, informando a
            # data, em vez do "Evoluir" da Minha agenda — se houver atendimento dele com este
            # paciente nesse dia ainda sem registro, o registro fica vinculado a ele (sai da
            # lista de pendentes e leva a especialidade daquele atendimento).
            agendamento = _atendimento_sem_registro(paciente, profissional, entrada.data_atendimento)
            if agendamento:
                entrada.agendamento = agendamento
                entrada.especialidade = agendamento.especialidade_exibicao
            entrada.save()
            messages.success(request, "Registro adicionado ao prontuário.")
            return redirect(voltar_url)
    else:
        form = EntradaProntuarioForm(simplificado=simplificado)

    return render(
        request,
        "prontuario/entrada_form.html",
        {
            "form": form,
            "titulo": "Registro no prontuário",
            "agendamento": None,
            "paciente": paciente,
            "profissional": profissional,
            "voltar_url": voltar_url,
            "simplificado": simplificado,
            "completo_escolhido": _completo_escolhido(request),
        },
    )


def _atendimento_sem_registro(paciente, profissional, data):
    return (
        Agendamento.objects.filter(
            paciente=paciente,
            profissional=profissional,
            status__in=Agendamento.STATUS_PRODUCAO,
            data_hora_inicio__date=data,
        )
        .exclude(entradas_prontuario__profissional=profissional)
        .order_by("data_hora_inicio")
        .first()
    )


@_exige_profissional
def entrada_editar(request, pk):
    """
    Edição de um registro já existente — restrita a quem o criou (pedido do
    cliente: o profissional pode errar ao lançar e precisa poder corrigir,
    mas só o PRÓPRIO registro, nunca o de outro profissional). Paciente,
    profissional, especialidade e agendamento continuam travados (não fazem
    parte do form, ver EntradaProntuarioForm) — só os temas clínicos podem
    mudar; quem editou e quando fica em `atualizado_em` (auto_now).
    """
    entrada = get_object_or_404(EntradaProntuario, pk=pk)
    if entrada.somente_leitura:
        messages.error(request, "Registros trazidos do sistema antigo são somente para consulta e não podem ser editados.")
        return redirect("prontuario:prontuario_paciente", paciente_pk=entrada.paciente_id)
    if entrada.profissional_id != request.user.profissional_id:
        raise PermissionDenied("Só quem criou este registro pode editá-lo.")
    if _bloqueia_paciente_inativo(request, entrada.paciente):
        return redirect("prontuario:prontuario_lista")

    voltar_url = reverse("prontuario:prontuario_paciente", args=[entrada.paciente_id])
    simplificado = _simplificado(request, entrada.especialidade)

    if request.method == "POST":
        form = EntradaProntuarioForm(request.POST, instance=entrada, simplificado=simplificado)
        if form.is_valid():
            form.save()
            messages.success(request, "Registro do prontuário atualizado.")
            return redirect(voltar_url)
    else:
        form = EntradaProntuarioForm(instance=entrada, simplificado=simplificado)

    return render(
        request,
        "prontuario/entrada_form.html",
        {
            "form": form,
            "titulo": "Editar registro do prontuário",
            "agendamento": entrada.agendamento,
            "paciente": entrada.paciente,
            "profissional": entrada.profissional,
            "voltar_url": voltar_url,
            "simplificado": simplificado,
            "completo_escolhido": _completo_escolhido(request),
            "entrada": entrada,
        },
    )


@_exige_profissional
def entrada_excluir(request, pk):
    """
    Apaga um registro feito errado (ex.: no paciente errado) — pedido da clínica, 26/10/2026.
    Mesmas travas de entrada_editar: só o autor, nunca registro do sistema antigo. Pede
    confirmação (GET mostra a tela, POST apaga). Fica no log de auditoria quem apagou, de qual
    paciente e de que data — sem o texto clínico.
    """
    entrada = get_object_or_404(EntradaProntuario.objects.select_related("paciente"), pk=pk)
    if entrada.somente_leitura:
        messages.error(request, "Registros trazidos do sistema antigo são somente para consulta e não podem ser apagados.")
        return redirect("prontuario:prontuario_paciente", paciente_pk=entrada.paciente_id)
    if entrada.profissional_id != request.user.profissional_id:
        raise PermissionDenied("Só quem criou este registro pode apagá-lo.")
    if _bloqueia_paciente_inativo(request, entrada.paciente):
        return redirect("prontuario:prontuario_lista")

    if request.method == "POST":
        descricao = (
            f"Apagou registro do prontuário de '{entrada.paciente.nome_completo}' "
            f"({entrada.especialidade}, atendimento de {entrada.data_exibicao:%d/%m/%Y}, "
            f"digitado em {timezone.localtime(entrada.criado_em):%d/%m/%Y %H:%M})"
        )
        paciente_pk = entrada.paciente_id
        entrada.delete()
        LogAuditoria.registrar(request, request.user, descricao)
        messages.success(request, "Registro apagado do prontuário.")
        return redirect("prontuario:prontuario_paciente", paciente_pk=paciente_pk)

    return render(request, "prontuario/entrada_excluir.html", {"entrada": entrada})
