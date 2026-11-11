from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from agenda.models import Agendamento
from cadastros.models import Paciente, Profissional

from .forms import AtestadoForm, ItemReceituarioFormSet, ReceituarioForm
from .models import DocumentoEmitido

# Endereço/telefone impressos no cabeçalho/rodapé dos documentos (ver templates
# documentos/documento_imprimir_base.html) — não existe cadastro de "dados da clínica"
# no sistema ainda, então ficam fixos aqui. Se mudar de endereço, é só editar aqui.
ENDERECO_CLINICA = "R. Três de Dezembro, Nº 123 — Centro, Barcarena/PA"
TELEFONE_CLINICA = "(91) 9 8098-8613"


def _exige_profissional(view_func):
    """
    Emitir atestado/receituário é uma área EXCLUSIVA de quem está vinculado a um
    cadastro de Profissional (Usuario.profissional_id) — mesma regra do prontuário
    (ver prontuario.views._exige_profissional): não é permissão configurável por
    perfil, é sempre o profissional autenticado emitindo em nome próprio.
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.profissional_id:
            raise PermissionDenied("Emitir atestado/receituário é uma área exclusiva do profissional vinculado ao atendimento.")
        return view_func(request, *args, **kwargs)

    return wrapper


def _profissional_do_usuario(request):
    if request.user.profissional_id:
        return Profissional.objects.filter(pk=request.user.profissional_id).first()
    return None


def _bloqueia_paciente_inativo(request, paciente):
    """Mesmo bloqueio do prontuário (ver prontuario.views._bloqueia_paciente_inativo)
    — paciente inativado não pode receber novo documento nem ter o histórico acessado."""
    if not paciente.ativo:
        messages.error(
            request,
            f"'{paciente.nome_completo}' está com o cadastro inativo — não é possível emitir documentos "
            "enquanto isso. Reative o cadastro em Pacientes → Editar se precisar.",
        )
        return True
    return False


def _idade(data_nascimento, data_referencia):
    if not data_nascimento:
        return None
    completou_aniversario = (data_referencia.month, data_referencia.day) >= (data_nascimento.month, data_nascimento.day)
    return data_referencia.year - data_nascimento.year - (0 if completou_aniversario else 1)


def _identificacao_paciente(paciente):
    if paciente.cpf:
        return f"CPF {paciente.cpf}"
    if paciente.rg:
        return f"RG {paciente.rg}"
    return ""


def _contexto_emissao(agendamento_pk=None, paciente_pk=None, *, request):
    """
    Resolve paciente/profissional/especialidade/data de referência a partir de um
    agendamento (atestado/receituário pedido a partir de um atendimento específico,
    ver agenda_propria.html) ou direto do paciente (pedido avulso, a partir do
    prontuário — ver prontuario_paciente.html). Mesma dupla de entrada usada em
    prontuario.views.entrada_nova / entrada_nova_paciente.
    """
    if agendamento_pk is not None:
        agendamento = get_object_or_404(Agendamento, pk=agendamento_pk)
        paciente = agendamento.paciente
        profissional = _profissional_do_usuario(request) or agendamento.profissional
        especialidade = agendamento.especialidade_exibicao
        data_referencia = agendamento.data_hora_inicio.date()
    else:
        agendamento = None
        paciente = get_object_or_404(Paciente, pk=paciente_pk)
        profissional = _profissional_do_usuario(request)
        especialidade = profissional.especialidade_exibicao if profissional else ""
        data_referencia = timezone.localdate()
    return agendamento, paciente, profissional, especialidade, data_referencia


@_exige_profissional
def atestado_novo(request, agendamento_pk=None, paciente_pk=None):
    agendamento, paciente, profissional, especialidade, data_referencia = _contexto_emissao(
        agendamento_pk, paciente_pk, request=request
    )
    if _bloqueia_paciente_inativo(request, paciente):
        return redirect(request.META.get("HTTP_REFERER") or reverse("prontuario:prontuario_lista"))
    voltar_url = request.META.get("HTTP_REFERER") or reverse("prontuario:prontuario_paciente", args=[paciente.pk])

    if request.method == "POST":
        form = AtestadoForm(request.POST)
        if form.is_valid():
            documento = form.save(commit=False)
            documento.tipo = DocumentoEmitido.Tipo.ATESTADO
            documento.paciente = paciente
            documento.profissional = profissional
            documento.especialidade = especialidade
            documento.agendamento = agendamento
            documento.save()
            messages.success(request, "Atestado emitido.")
            return redirect("documentos:documento_imprimir", documento.pk)
    else:
        form = AtestadoForm(initial={
            "documento_identificacao": _identificacao_paciente(paciente),
            "dias_afastamento": 1,
            "data_inicio_afastamento": data_referencia,
        })

    return render(request, "documentos/atestado_form.html", {
        "form": form,
        "paciente": paciente,
        "profissional": profissional,
        "agendamento": agendamento,
        "idade": _idade(paciente.data_nascimento, data_referencia),
        "voltar_url": voltar_url,
    })


@_exige_profissional
def receituario_novo(request, agendamento_pk=None, paciente_pk=None):
    agendamento, paciente, profissional, especialidade, data_referencia = _contexto_emissao(
        agendamento_pk, paciente_pk, request=request
    )
    if _bloqueia_paciente_inativo(request, paciente):
        return redirect(request.META.get("HTTP_REFERER") or reverse("prontuario:prontuario_lista"))
    voltar_url = request.META.get("HTTP_REFERER") or reverse("prontuario:prontuario_paciente", args=[paciente.pk])

    documento_novo = DocumentoEmitido(
        tipo=DocumentoEmitido.Tipo.RECEITUARIO,
        paciente=paciente,
        profissional=profissional,
        especialidade=especialidade,
        agendamento=agendamento,
    )

    if request.method == "POST":
        form = ReceituarioForm(request.POST, instance=documento_novo)
        formset = ItemReceituarioFormSet(request.POST, instance=documento_novo)
        if form.is_valid() and formset.is_valid():
            documento = form.save()
            formset.instance = documento
            formset.save()
            messages.success(request, "Receituário emitido.")
            return redirect("documentos:documento_imprimir", documento.pk)
    else:
        form = ReceituarioForm(instance=documento_novo)
        formset = ItemReceituarioFormSet(instance=documento_novo)

    return render(request, "documentos/receituario_form.html", {
        "form": form,
        "formset": formset,
        "paciente": paciente,
        "profissional": profissional,
        "agendamento": agendamento,
        "idade": _idade(paciente.data_nascimento, data_referencia),
        "voltar_url": voltar_url,
    })


@_exige_profissional
def documento_imprimir(request, pk):
    documento = get_object_or_404(
        DocumentoEmitido.objects.select_related("paciente", "profissional").prefetch_related("itens"), pk=pk
    )
    if _bloqueia_paciente_inativo(request, documento.paciente):
        return redirect("prontuario:prontuario_lista")
    if not documento.visivel_para(request.user):
        raise PermissionDenied("Este documento é sigiloso — só quem o emitiu pode reabri-lo.")

    data_referencia = documento.agendamento.data_hora_inicio.date() if documento.agendamento else documento.criado_em.date()
    contexto = {
        "documento": documento,
        "paciente": documento.paciente,
        "profissional": documento.profissional,
        "idade": _idade(documento.paciente.data_nascimento, data_referencia),
        "endereco_clinica": ENDERECO_CLINICA,
        "telefone_clinica": TELEFONE_CLINICA,
    }
    template = (
        "documentos/atestado_imprimir.html"
        if documento.tipo == DocumentoEmitido.Tipo.ATESTADO
        else "documentos/receituario_imprimir.html"
    )
    return render(request, template, contexto)
