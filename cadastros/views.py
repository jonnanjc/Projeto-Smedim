import calendar
import csv
import io
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ProtectedError, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_time

from accounts.forms import SENHA_PADRAO_NOVO_USUARIO
from accounts.models import LogAuditoria
from accounts.paginacao import paginar
from agenda.models import Agendamento

from .forms import (
    ConvenioForm,
    GuicheForm,
    ImportarProcedimentosForm,
    ImportarTabelaPrecoForm,
    LimiteDiarioProcedimentoForm,
    PacienteForm,
    PacoteForm,
    PacoteItemFormSet,
    ProcedimentoForm,
    ProfissionalEspecialidadeFormSet,
    ProfissionalForm,
    SalaForm,
    TabelaPrecoForm,
    ValorFixoProfissionalFormSet,
)
from .models import (
    Convenio,
    DIAS_SEMANA_ATENDIMENTO,
    ExcecaoAtendimento,
    Guiche,
    HorarioAtendimento,
    LimiteDiarioProcedimento,
    Paciente,
    Pacote,
    Procedimento,
    Profissional,
    Sala,
    TabelaPrecoConvenio,
)

try:
    import openpyxl
except ImportError:  # biblioteca instalada via requirements.txt — ver README
    openpyxl = None

Usuario = get_user_model()


# =====================================================================
# Pacientes
# =====================================================================

@permission_required("accounts.paciente_cadastrar", raise_exception=True)
def paciente_lista(request):
    termo = request.GET.get("q", "").strip()
    # Cópias unificadas (Paciente.unificado_em) nunca aparecem; cadastros do sistema antigo
    # só com o nome ficam numa lista à parte (?ocultos=1), pra quem quiser completar —
    # ver Paciente.filtro_visivel_recepcao. Inativos continuam na lista (pra reativar).
    ver_ocultos = request.GET.get("ocultos") == "1"
    base = Paciente.objects.filter(unificado_em__isnull=True)
    sem_dados = base.exclude(Paciente.filtro_dados_minimos())
    pacientes = (sem_dados if ver_ocultos else base.filter(Paciente.filtro_dados_minimos())).select_related("convenio")
    if termo:
        pacientes = pacientes.filter(Q(nome_completo__icontains=termo) | Q(cpf__icontains=termo))
    return render(
        request,
        "cadastros/paciente_lista.html",
        {
            "pagina": paginar(request, pacientes),
            "termo": termo,
            "ver_ocultos": ver_ocultos,
            "total_ocultos": sem_dados.count(),
        },
    )


@permission_required("accounts.paciente_cadastrar", raise_exception=True)
def paciente_novo(request):
    # 'popup=1' é usado quando esta tela é aberta em uma janela pop-up a partir
    # do formulário de agendamento (botão "+ Cadastrar paciente") — nesse caso,
    # ao salvar, devolve uma página mínima que avisa a janela "mãe" via
    # postMessage e se fecha sozinha, em vez de redirecionar para a listagem.
    eh_popup = request.GET.get("popup") == "1" or request.POST.get("popup") == "1"
    usar = request.GET.get("usar", "")
    if eh_popup and usar.isdigit():
        # "Usar este" no aviso de nome repetido (PacienteForm.clean): devolve ao agendamento
        # a ficha que já existia, em vez de criar outra.
        paciente = get_object_or_404(Paciente, pk=usar, unificado_em__isnull=True)
        return render(request, "cadastros/paciente_popup_criado.html", {"paciente": paciente})
    if request.method == "POST":
        form = PacienteForm(request.POST)
        if form.is_valid():
            paciente = form.save()
            if eh_popup:
                return render(request, "cadastros/paciente_popup_criado.html", {"paciente": paciente})
            messages.success(request, "Paciente cadastrado com sucesso.")
            return redirect("cadastros:paciente_lista")
    else:
        form = PacienteForm()
    return render(
        request,
        "cadastros/form_generico.html",
        {
            "form": form,
            "titulo": "Novo paciente",
            "voltar_url": reverse("cadastros:paciente_lista"),
            "eh_popup": eh_popup,
        },
    )


@permission_required("accounts.paciente_editar", raise_exception=True)
def paciente_editar(request, pk):
    paciente = get_object_or_404(Paciente, pk=pk)
    if request.method == "POST":
        form = PacienteForm(request.POST, instance=paciente)
        if form.is_valid():
            form.save()
            messages.success(request, "Paciente atualizado com sucesso.")
            return redirect("cadastros:paciente_lista")
    else:
        form = PacienteForm(instance=paciente)
    return render(
        request,
        "cadastros/form_generico.html",
        {"form": form, "titulo": f"Editar paciente: {paciente.nome_completo}", "voltar_url": reverse("cadastros:paciente_lista")},
    )


@permission_required("accounts.paciente_excluir", raise_exception=True)
def paciente_excluir(request, pk):
    paciente = get_object_or_404(Paciente, pk=pk)
    if request.method == "POST":
        nome = paciente.nome_completo
        try:
            paciente.delete()
        except ProtectedError:
            # Paciente tem agendamentos vinculados (FK protegida, de propósito —
            # não dá pra perder o histórico de atendimento excluindo o paciente).
            # Em vez de deixar o erro 500 estourar, orienta a inativar o cadastro,
            # que já é uma opção existente na tela de edição.
            messages.error(
                request,
                f"Não foi possível excluir '{nome}': existem agendamentos vinculados a este paciente "
                "(passados ou futuros) e o histórico não pode ser perdido. Se o paciente não frequenta "
                "mais a clínica, edite o cadastro dele e desmarque a opção \"Ativo\" em vez de excluir.",
            )
            return redirect("cadastros:paciente_lista")
        messages.success(request, f"Paciente '{nome}' excluído.")
        return redirect("cadastros:paciente_lista")
    return render(
        request,
        "cadastros/confirmar_exclusao.html",
        {"objeto": paciente, "voltar_url": reverse("cadastros:paciente_lista")},
    )


@permission_required("accounts.paciente_excluir", raise_exception=True)
def paciente_duplicados(request):
    """Possíveis duplicados (pedido da clínica, 06/11/2026): fichas com o mesmo nome (sem
    diferenciar acento/maiúscula — "Raissa" x "Raíssa"), agrupadas, pra alguém conferir e
    unificar com um clique (ver cadastros/unificacao.py). Os de certeza (nome + nascimento
    iguais) também dá pra fazer em lote pelo comando unificar_pacientes_duplicados. Exige a
    permissão de excluir paciente: unificar tira a cópia de circulação, como uma exclusão."""
    from .models import DuplicidadeIgnorada
    from .unificacao import TIPOS, UnificacaoInvalida, contar_historico, cpfs_conflitantes, sugerir_principal, unificar

    voltar = request.POST.get("voltar") or request.get_full_path()
    if request.method == "POST":
        acao = request.POST.get("acao")
        if acao == "unificar":
            ids_juntar = {int(pk) for pk in request.POST.getlist("juntar") if pk.isdigit()}
            principal = get_object_or_404(Paciente, pk=request.POST.get("principal") or 0)
            copias = list(Paciente.objects.filter(pk__in=ids_juntar - {principal.pk}))
            try:
                movidos = unificar(principal, copias, usuario=request.user, request=request)
            except UnificacaoInvalida as erro:
                messages.error(request, str(erro))
            else:
                messages.success(
                    request,
                    f"{len(copias) + 1} fichas unificadas em '{principal.nome_completo}' "
                    f"({movidos} registro{'s' if movidos != 1 else ''} de histórico juntado{'s' if movidos != 1 else ''}).",
                )
        elif acao == "ignorar":
            ids = [int(pk) for pk in request.POST.get("ids", "").split(",") if pk.isdigit()]
            if len(ids) > 1:
                nome = Paciente.objects.filter(pk=ids[0]).values_list("nome_completo", flat=True).first() or ""
                DuplicidadeIgnorada.objects.get_or_create(
                    ids=DuplicidadeIgnorada.chave(ids), defaults={"nome": nome[:150], "usuario": request.user}
                )
                LogAuditoria.registrar(
                    request, request.user, f"Marcou fichas {DuplicidadeIgnorada.chave(ids)} ({nome}) como pessoas diferentes"[:255]
                )
                messages.success(request, f"'{nome}': marcado como pessoas diferentes — não aparece mais aqui.")
        if not voltar.startswith("/"):
            voltar = reverse("cadastros:paciente_duplicados")
        return redirect(voltar)

    termo = request.GET.get("q", "").strip()
    tipo = request.GET.get("tipo", "")
    from .unificacao import grupos_por_nome

    ignorados = set(DuplicidadeIgnorada.objects.values_list("ids", flat=True))
    grupos = [g for g in grupos_por_nome(termo) if DuplicidadeIgnorada.chave(g["ids"]) not in ignorados]
    contagem = {chave: 0 for chave in TIPOS}
    for grupo in grupos:
        contagem[grupo["tipo"]] += 1
    if tipo in TIPOS:
        grupos = [g for g in grupos if g["tipo"] == tipo]
    pagina = paginar(request, grupos, por_pagina=20)

    ids_pagina = [pk for grupo in pagina.object_list for pk in grupo["ids"]]
    fichas = Paciente.objects.select_related("convenio").in_bulk(ids_pagina)
    historico = contar_historico(ids_pagina)
    for grupo in pagina.object_list:
        lista = [fichas[pk] for pk in grupo["ids"] if pk in fichas]
        principal = sugerir_principal(lista, historico)
        grupo["principal_id"] = principal.pk
        grupo["chave_ids"] = DuplicidadeIgnorada.chave(grupo["ids"])
        grupo["tipo_nome"] = TIPOS[grupo["tipo"]]
        cpfs = cpfs_conflitantes(lista)
        grupo["cpfs_diferentes"] = len(cpfs) > 1
        grupo["fichas"] = []
        for ficha in sorted(lista, key=lambda f: (f.pk != principal.pk, f.data_nascimento or date.max, f.pk)):
            # Já vem marcada pra juntar só a ficha com o mesmo nascimento da principal — nos
            # casos de dúvida (nascimento diferente ou faltando) quem confere marca o resto.
            pre_marcada = ficha.pk != principal.pk and bool(ficha.data_nascimento) and ficha.data_nascimento == principal.data_nascimento
            grupo["fichas"].append({"ficha": ficha, "historico": historico[ficha.pk], "marcada": pre_marcada})
    return render(
        request,
        "cadastros/paciente_duplicados.html",
        {
            "pagina": pagina,
            "termo": termo,
            "tipo": tipo,
            "tipos": [(chave, nome, contagem[chave]) for chave, nome in TIPOS.items()],
            "total": sum(contagem.values()),
        },
    )


# =====================================================================
# Profissionais
# =====================================================================

@permission_required("accounts.profissional_gerenciar", raise_exception=True)
def profissional_lista(request):
    profissionais = Profissional.objects.all().prefetch_related("especialidades_adicionais")
    return render(request, "cadastros/profissional_lista.html", {"pagina": paginar(request, profissionais)})


def _gerar_username_profissional(profissional):
    """
    Vira o login só o número do conselho, sem a sigla do conselho nem a UF
    (ex: 'CRM 12345-SP' -> login '12345'). Em caso de conflito (dois
    profissionais com o mesmo número), acrescenta 's' no final até virar
    único (ex: '12345s', '12345ss').
    """
    base = re.sub(r"\D", "", profissional.registro_profissional or "")
    if not base:
        base = re.sub(r"[^a-z0-9]", "", profissional.nome.lower())[:20] or f"profissional{profissional.pk}"

    username = base
    while Usuario.objects.filter(username=username).exists():
        username += "s"
    return username


def _criar_login_automatico_profissional(request, profissional):
    """
    Cria o login do profissional automaticamente, usando o número do conselho como usuário
    (ex: CRM 12345-SP -> login 'crm12345-sp') e a senha padrão da clínica. Só roda se o
    profissional ainda não tiver um usuário vinculado (evita recriar em cada edição).
    """
    if not profissional.registro_profissional:
        messages.warning(
            request,
            f"'{profissional.nome}' foi salvo, mas sem 'Registro profissional' preenchido não deu para "
            "gerar o login automático — edite o cadastro dele, informe o registro e salve de novo.",
        )
        return

    if Usuario.objects.filter(profissional_id=profissional.pk).exists():
        return  # já tem login vinculado, não mexe

    username = _gerar_username_profissional(profissional)
    partes_nome = profissional.nome.split(" ", 1)
    usuario = Usuario(
        username=username,
        first_name=partes_nome[0],
        last_name=partes_nome[1] if len(partes_nome) > 1 else "",
        profissional_id=profissional.pk,
        exigir_troca_senha=True,
    )
    usuario.set_password(SENHA_PADRAO_NOVO_USUARIO)
    usuario.save()

    grupo_medico = Group.objects.filter(name="Médico / Profissional de saúde").first()
    if grupo_medico:
        usuario.groups.add(grupo_medico)

    messages.success(
        request,
        f"Login criado automaticamente para '{profissional.nome}': usuário '{username}', senha inicial "
        f"'{SENHA_PADRAO_NOVO_USUARIO}' (será exigida a troca no primeiro acesso).",
    )


@permission_required("accounts.profissional_gerenciar", raise_exception=True)
def profissional_novo(request):
    if request.method == "POST":
        form = ProfissionalForm(request.POST)
        formset = ProfissionalEspecialidadeFormSet(request.POST, instance=form.instance, profissional_form=form)
        formset_valores = ValorFixoProfissionalFormSet(request.POST, instance=form.instance)
        form_valido = form.is_valid()
        formset_valido = formset.is_valid()
        valores_validos = formset_valores.is_valid()
        if form_valido and formset_valido and valores_validos:
            with transaction.atomic():
                profissional = form.save()
                formset.instance = profissional
                formset.save()
                formset_valores.instance = profissional
                formset_valores.save()
            messages.success(request, "Profissional cadastrado com sucesso.")
            _criar_login_automatico_profissional(request, profissional)
            return redirect("cadastros:profissional_lista")
    else:
        form = ProfissionalForm()
        formset = ProfissionalEspecialidadeFormSet(instance=form.instance, profissional_form=form)
        formset_valores = ValorFixoProfissionalFormSet(instance=form.instance)
    return render(
        request,
        "cadastros/profissional_form.html",
        {
            "form": form,
            "formset": formset,
            "formset_valores": formset_valores,
            "titulo": "Novo profissional",
            "voltar_url": reverse("cadastros:profissional_lista"),
        },
    )


@permission_required("accounts.profissional_gerenciar", raise_exception=True)
def profissional_editar(request, pk):
    profissional = get_object_or_404(Profissional, pk=pk)
    if request.method == "POST":
        form = ProfissionalForm(request.POST, instance=profissional)
        formset = ProfissionalEspecialidadeFormSet(request.POST, instance=profissional, profissional_form=form)
        formset_valores = ValorFixoProfissionalFormSet(request.POST, instance=profissional)
        form_valido = form.is_valid()
        formset_valido = formset.is_valid()
        valores_validos = formset_valores.is_valid()
        if form_valido and formset_valido and valores_validos:
            with transaction.atomic():
                form.save()
                formset.save()
                formset_valores.save()
            messages.success(request, "Profissional atualizado com sucesso.")
            # Cobre profissionais cadastrados antes deste recurso existir (ainda sem login vinculado).
            _criar_login_automatico_profissional(request, profissional)
            return redirect("cadastros:profissional_lista")
    else:
        form = ProfissionalForm(instance=profissional)
        formset = ProfissionalEspecialidadeFormSet(instance=profissional, profissional_form=form)
        formset_valores = ValorFixoProfissionalFormSet(instance=profissional)
    return render(
        request,
        "cadastros/profissional_form.html",
        {
            "form": form,
            "formset": formset,
            "formset_valores": formset_valores,
            "titulo": f"Editar profissional: {profissional.nome}",
            "voltar_url": reverse("cadastros:profissional_lista"),
        },
    )


@permission_required("accounts.profissional_gerenciar", raise_exception=True)
def profissional_desativar(request, pk):
    """
    Em vez de excluir o profissional de verdade, marcamos ele (e o login dele, se
    existir) como inativos. Isso evita perder o histórico quando o profissional sai
    da clínica mas ainda tem produção/pagamentos pendentes vinculados a ele.
    """
    profissional = get_object_or_404(Profissional, pk=pk)
    if request.method == "POST":
        profissional.ativo = False
        profissional.save(update_fields=["ativo"])

        usuario = profissional.usuario_vinculado
        if usuario is not None:
            usuario.status = Usuario.Status.INATIVO
            usuario.is_active = False
            usuario.save(update_fields=["status", "is_active"])

        messages.success(
            request,
            f"Profissional '{profissional.nome}' desativado (o cadastro e o login dele foram marcados como "
            "inativos, sem apagar nada do histórico).",
        )
        return redirect("cadastros:profissional_lista")
    return render(
        request,
        "cadastros/confirmar_exclusao.html",
        {
            "objeto": profissional,
            "voltar_url": reverse("cadastros:profissional_lista"),
            "acao_texto": "desativar",
            "aviso_extra": (
                "O cadastro e o login dele serão marcados como inativos — nada é apagado, "
                "então não há risco de perder produção ou pagamentos pendentes."
            ),
        },
    )


def _procedimentos_do_profissional_para_bloco(profissional):
    """
    Lista de procedimentos disponível no checklist de cada bloco de horário
    (telas de Horários e Exceções): só os que esse profissional está
    habilitado a fazer (ver Procedimento.profissionais_habilitados) — assim
    um nutricionista, por exemplo, não vê procedimentos de outras
    especialidades na lista. Pedido do cliente: SEM fallback pra "todos os
    procedimentos da clínica" quando o profissional ainda não tem nenhum
    habilitado configurado — nesse caso a lista vem vazia mesmo (o template
    orienta a vincular os procedimentos do profissional primeiro, em
    Cadastros → Procedimentos → editar → "profissionais habilitados").

    Ultrassom é tratado à parte: em vez de listar cada tipo de exame de
    ultrassom (abdominal, obstétrico...) como uma checkbox separada, todos
    aparecem agrupados num único "USG" (ver Procedimento.eh_ultrassom) —
    devolve (procedimentos_normais, ids_usg).
    """
    disponiveis = profissional.procedimentos_habilitados.filter(ativo=True)
    procedimentos_normais = disponiveis.exclude(eh_ultrassom=True).order_by("nome")
    ids_usg = set(disponiveis.filter(eh_ultrassom=True).values_list("pk", flat=True))
    return procedimentos_normais, ids_usg


def _resolver_procedimentos_marcados(valores_marcados, ids_usg):
    """
    Troca o marcador especial "usg" (do checkbox único de ultrassom) pelos
    IDs reais dos procedimentos de ultrassom habilitados, antes de salvar em
    HorarioAtendimento.procedimentos_permitidos / ExcecaoAtendimento.procedimentos_permitidos.
    """
    valores = set(valores_marcados)
    if "usg" in valores:
        valores.discard("usg")
        valores.update(str(pk) for pk in ids_usg)
    return valores


@permission_required("accounts.profissional_gerenciar", raise_exception=True)
def profissional_horarios(request, pk):
    """
    Tela de "Horários de atendimento" de um profissional: uma grade de dia da
    semana (segunda a sexta) x turno (manhã/tarde/noite), onde dá para marcar
    em quais blocos ele atende e ajustar o horário exato de início/término de
    cada um.

    Isso é usado pela Central de Atendimento para só oferecer como "vago" um
    horário em que o profissional realmente atende (ver
    agenda.views._monta_disponibilidade) — evita marcar em cima de um horário
    que ele nem trabalha.
    """
    profissional = get_object_or_404(Profissional, pk=pk)
    turnos = list(HorarioAtendimento.Turno)
    procedimentos_normais, ids_usg = _procedimentos_do_profissional_para_bloco(profissional)

    if request.method == "POST":
        erros = []
        for dia, _rotulo in DIAS_SEMANA_ATENDIMENTO:
            for turno in turnos:
                chave = f"{dia}_{turno.value}"
                existente = HorarioAtendimento.objects.filter(
                    profissional=profissional, dia_semana=dia, turno=turno
                ).first()

                if request.POST.get(f"ativo_{chave}"):
                    inicio_str = request.POST.get(f"inicio_{chave}")
                    fim_str = request.POST.get(f"fim_{chave}")
                    inicio = parse_time(inicio_str) if inicio_str else None
                    fim = parse_time(fim_str) if fim_str else None
                    if not inicio or not fim:
                        erros.append(f"{dict(DIAS_SEMANA_ATENDIMENTO)[dia]} — {turno.label}: informe início e término.")
                        continue
                    horario = existente or HorarioAtendimento(
                        profissional=profissional, dia_semana=dia, turno=turno
                    )
                    horario.horario_inicio = inicio
                    horario.horario_fim = fim
                    try:
                        horario.full_clean()
                        horario.save()
                        marcados = _resolver_procedimentos_marcados(
                            request.POST.getlist(f"procedimentos_{chave}"), ids_usg
                        )
                        horario.procedimentos_permitidos.set(marcados)
                    except ValidationError as erro:
                        erros.append(f"{dict(DIAS_SEMANA_ATENDIMENTO)[dia]} — {turno.label}: {'; '.join(erro.messages)}")
                elif existente:
                    existente.delete()

        if erros:
            for erro in erros:
                messages.error(request, erro)
        else:
            messages.success(request, f"Horários de atendimento de '{profissional.nome}' atualizados.")
            return redirect("cadastros:profissional_lista")

    horarios_existentes = {
        (horario.dia_semana, horario.turno): horario
        for horario in HorarioAtendimento.objects.filter(profissional=profissional).prefetch_related(
            "procedimentos_permitidos"
        )
    }

    grade = []
    for dia, rotulo_dia in DIAS_SEMANA_ATENDIMENTO:
        colunas = []
        for turno in turnos:
            horario = horarios_existentes.get((dia, turno.value))
            padrao_inicio, padrao_fim = HorarioAtendimento.HORARIO_PADRAO_POR_TURNO[turno]
            selecionados = {p.pk for p in horario.procedimentos_permitidos.all()} if horario else set()
            colunas.append(
                {
                    "turno": turno,
                    "chave": f"{dia}_{turno.value}",
                    "marcado": horario is not None,
                    "inicio": horario.horario_inicio.strftime("%H:%M") if horario else padrao_inicio,
                    "fim": horario.horario_fim.strftime("%H:%M") if horario else padrao_fim,
                    "procedimentos_selecionados": selecionados,
                    "usg_marcado": bool(selecionados & ids_usg),
                }
            )
        grade.append({"dia": dia, "rotulo": rotulo_dia, "colunas": colunas})

    return render(
        request,
        "cadastros/profissional_horarios.html",
        {
            "profissional": profissional,
            "grade": grade,
            "procedimentos_normais": procedimentos_normais,
            "tem_usg": bool(ids_usg),
            "voltar_url": reverse("cadastros:profissional_lista"),
        },
    )


MODOS_REPETICAO_DATA = [
    ("", "Não repetir"),
    ("dia_mes", "Mesmo dia do mês"),
    ("semana_mes", "Mesma semana e dia da semana do mês"),
    ("ultima_semana_mes", "Último mesmo dia da semana do mês"),
]


def _data_repetida(data_base, modo, meses_depois):
    """
    Data equivalente a `data_base`, `meses_depois` meses para frente, conforme o
    modo de repetição escolhido na tela de datas específicas:

    - "dia_mes": mesmo número de dia (ex: todo dia 15);
    - "semana_mes": mesma ocorrência do dia da semana (ex: toda 2ª terça-feira);
    - "ultima_semana_mes": última ocorrência do dia da semana (ex: última sexta).

    Devolve None quando a data não existe naquele mês (ex: dia 31 em
    novembro, ou uma "5ª quinta-feira" num mês que só tem 4).
    """
    total = data_base.month - 1 + meses_depois
    ano, mes = data_base.year + total // 12, total % 12 + 1
    dias_no_mes = calendar.monthrange(ano, mes)[1]
    if modo == "dia_mes":
        return date(ano, mes, data_base.day) if data_base.day <= dias_no_mes else None
    if modo == "semana_mes":
        ocorrencia = (data_base.day - 1) // 7  # 0 = primeira
        primeiro = date(ano, mes, 1)
        dia = 1 + (data_base.weekday() - primeiro.weekday()) % 7 + ocorrencia * 7
        return date(ano, mes, dia) if dia <= dias_no_mes else None
    if modo == "ultima_semana_mes":
        ultimo = date(ano, mes, dias_no_mes)
        return ultimo - timedelta(days=(ultimo.weekday() - data_base.weekday()) % 7)
    return None


def _ler_blocos_data_especifica(request, turnos, ids_usg):
    """Lê do POST os turnos marcados na tela de datas específicas. Devolve (blocos, erros)."""
    blocos, erros = [], []
    for turno in turnos:
        chave = turno.value
        if not request.POST.get(f"ativo_{chave}"):
            continue
        inicio_str = request.POST.get(f"inicio_{chave}")
        fim_str = request.POST.get(f"fim_{chave}")
        inicio = parse_time(inicio_str) if inicio_str else None
        fim = parse_time(fim_str) if fim_str else None
        if not inicio or not fim:
            erros.append(f"{turno.label}: informe início e término.")
            continue
        if fim <= inicio:
            erros.append(f"{turno.label}: o horário de término deve ser depois do horário de início.")
            continue
        procedimentos = _resolver_procedimentos_marcados(request.POST.getlist(f"procedimentos_{chave}"), ids_usg)
        blocos.append({"turno": turno, "inicio": inicio, "fim": fim, "procedimentos": procedimentos})
    return blocos, erros


def _gravar_blocos_na_data(profissional, data, blocos):
    """Substitui tudo que existia na data pelos blocos informados (lista vazia = só apaga)."""
    ExcecaoAtendimento.objects.filter(profissional=profissional, data=data).delete()
    for bloco in blocos:
        excecao = ExcecaoAtendimento.objects.create(
            profissional=profissional,
            data=data,
            turno=bloco["turno"],
            horario_inicio=bloco["inicio"],
            horario_fim=bloco["fim"],
        )
        excecao.procedimentos_permitidos.set(bloco["procedimentos"])


def _agendamentos_ativos_na_data(profissional, data):
    return (
        Agendamento.objects.filter(profissional=profissional, data_hora_inicio__date=data)
        .exclude(status=Agendamento.Status.CANCELADO)
        .select_related("paciente")
        .order_by("data_hora_inicio")
    )


@permission_required("accounts.profissional_gerenciar", raise_exception=True)
def profissional_excecoes(request, pk):
    """
    Tela de "Datas específicas / exceções" de um profissional. Serve para:

    - profissional sem dia fixo, que só vem em datas marcadas no mês (ex: todo
      dia 15) — com opção de já repetir a data nos próximos meses; cada data
      repetida vira um registro independente, que dá pra editar/remover/
      remanejar sozinho depois;
    - substituir, só numa data, o horário fixo semanal (ver
      profissional_horarios acima) — ex: "no dia 20/07, esse médico vai fazer
      ultrassom à tarde em vez de manhã".

    `?editar=AAAA-MM-DD` abre o formulário já preenchido com o que está
    cadastrado nessa data (salvar substitui a data inteira).
    """
    profissional = get_object_or_404(Profissional, pk=pk)
    turnos = list(HorarioAtendimento.Turno)
    procedimentos_normais, ids_usg = _procedimentos_do_profissional_para_bloco(profissional)

    if request.method == "POST":
        data = parse_date(request.POST.get("data") or "")
        modo_repeticao = request.POST.get("repetir_modo") or ""
        try:
            meses_repeticao = max(1, min(12, int(request.POST.get("repetir_meses") or 1)))
        except ValueError:
            meses_repeticao = 1
        if not data:
            messages.error(request, "Informe uma data válida.")
        elif data.weekday() >= 5:
            messages.error(request, "A clínica não funciona aos sábados e domingos — escolha uma data de segunda a sexta.")
        else:
            blocos, erros = _ler_blocos_data_especifica(request, turnos, ids_usg)
            if erros:
                for erro in erros:
                    messages.error(request, erro)
            elif not blocos:
                with transaction.atomic():
                    _gravar_blocos_na_data(profissional, data, [])
                messages.success(
                    request, f"Nenhum bloco marcado — {data:%d/%m/%Y} volta a seguir o horário fixo semanal."
                )
                return redirect("cadastros:profissional_excecoes", pk=profissional.pk)
            else:
                criadas, puladas = [], []
                with transaction.atomic():
                    _gravar_blocos_na_data(profissional, data, blocos)
                    if modo_repeticao and modo_repeticao in dict(MODOS_REPETICAO_DATA):
                        for meses_depois in range(1, meses_repeticao + 1):
                            nova = _data_repetida(data, modo_repeticao, meses_depois)
                            if nova is None:
                                puladas.append(f"{meses_depois}º mês seguinte (essa data não existe no mês)")
                            elif nova.weekday() >= 5:
                                puladas.append(f"{nova:%d/%m/%Y} (cai num fim de semana)")
                            elif ExcecaoAtendimento.objects.filter(profissional=profissional, data=nova).exists():
                                puladas.append(
                                    f"{nova:%d/%m/%Y} (já tinha horário cadastrado — edite essa data se precisar)"
                                )
                            else:
                                _gravar_blocos_na_data(profissional, nova, blocos)
                                criadas.append(nova)
                messages.success(request, f"Data {data:%d/%m/%Y} salva.")
                if criadas:
                    messages.success(
                        request, "Também cadastrada em: " + ", ".join(f"{d:%d/%m/%Y}" for d in criadas) + "."
                    )
                for pulada in puladas:
                    messages.warning(request, f"Não repetida em {pulada}.")
                return redirect("cadastros:profissional_excecoes", pk=profissional.pk)

    data_editando = parse_date(request.GET.get("editar") or "")
    existentes = {}
    if data_editando:
        existentes = {
            excecao.turno: excecao
            for excecao in ExcecaoAtendimento.objects.filter(
                profissional=profissional, data=data_editando
            ).prefetch_related("procedimentos_permitidos")
        }
        if not existentes:
            data_editando = None

    grade_nova = []
    for turno in turnos:
        padrao_inicio, padrao_fim = HorarioAtendimento.HORARIO_PADRAO_POR_TURNO[turno]
        existente = existentes.get(turno.value)
        selecionados = {p.pk for p in existente.procedimentos_permitidos.all()} if existente else set()
        grade_nova.append(
            {
                "turno": turno,
                "chave": turno.value,
                "marcado": existente is not None,
                "inicio": existente.horario_inicio.strftime("%H:%M") if existente else padrao_inicio,
                "fim": existente.horario_fim.strftime("%H:%M") if existente else padrao_fim,
                "procedimentos_selecionados": selecionados,
                "usg_marcado": bool(selecionados & ids_usg),
            }
        )

    hoje = timezone.localdate()
    excecoes_futuras = (
        ExcecaoAtendimento.objects.filter(profissional=profissional, data__gte=hoje)
        .prefetch_related("procedimentos_permitidos")
        .order_by("data", "horario_inicio")
    )
    excecoes_por_data = {}
    for excecao in excecoes_futuras:
        procs = list(excecao.procedimentos_permitidos.all())
        nomes = [p.nome for p in procs if not p.eh_ultrassom]
        if any(p.eh_ultrassom for p in procs):
            nomes.append("USG")
        excecao.nomes_procedimentos_exibicao = nomes
        excecoes_por_data.setdefault(excecao.data, []).append(excecao)

    # Quantos pacientes já estão marcados em cada data — mostrado no cartão da data,
    # pra ninguém remover/remanejar sem saber que tem gente agendada.
    contagem_agendamentos = {}
    if excecoes_por_data:
        inicios = (
            Agendamento.objects.filter(profissional=profissional, data_hora_inicio__date__in=list(excecoes_por_data))
            .exclude(status=Agendamento.Status.CANCELADO)
            .values_list("data_hora_inicio", flat=True)
        )
        for inicio in inicios:
            dia = timezone.localtime(inicio).date()
            contagem_agendamentos[dia] = contagem_agendamentos.get(dia, 0) + 1

    return render(
        request,
        "cadastros/profissional_excecoes.html",
        {
            "profissional": profissional,
            "grade_nova": grade_nova,
            "data_editando": data_editando,
            "modos_repeticao": MODOS_REPETICAO_DATA,
            "procedimentos_normais": procedimentos_normais,
            "tem_usg": bool(ids_usg),
            "datas_cadastradas": [
                (data, blocos, contagem_agendamentos.get(data, 0))
                for data, blocos in sorted(excecoes_por_data.items())
            ],
            "voltar_url": reverse("cadastros:profissional_lista"),
        },
    )


@permission_required("accounts.profissional_gerenciar", raise_exception=True)
def profissional_excecao_remover(request, pk, data):
    profissional = get_object_or_404(Profissional, pk=pk)
    data_convertida = parse_date(data)
    if not data_convertida:
        return redirect("cadastros:profissional_excecoes", pk=profissional.pk)
    if request.method == "POST":
        ExcecaoAtendimento.objects.filter(profissional=profissional, data=data_convertida).delete()
        messages.success(
            request, f"Data {data_convertida:%d/%m/%Y} removida — volta a seguir o horário fixo semanal."
        )
        return redirect("cadastros:profissional_excecoes", pk=profissional.pk)
    qtd_agendamentos = _agendamentos_ativos_na_data(profissional, data_convertida).count()
    aviso_extra = ""
    if qtd_agendamentos:
        aviso_extra = (
            f"ATENÇÃO: existem {qtd_agendamentos} agendamento(s) de pacientes nessa data, que NÃO serão "
            'cancelados nem movidos. Se o profissional mudou de data, use "Remanejar" em vez de remover.'
        )
    return render(
        request,
        "cadastros/confirmar_exclusao.html",
        {
            "objeto": profissional,
            "voltar_url": reverse("cadastros:profissional_excecoes", args=[profissional.pk]),
            "acao_texto": f"remover a data {data_convertida:%d/%m/%Y} de",
            "aviso_extra": aviso_extra,
        },
    )


# Situações de agendamento que podem ser remanejadas junto com a data: o fluxo do dia
# (recepção/financeiro) ainda não começou. Pré-confirmado/Confirmado voltam para
# Agendado, porque o paciente confirmou a data ANTIGA e precisa ser avisado de novo.
STATUS_REMANEJAVEIS = ("AGENDADO", "PRE_CONFIRMADO", "CONFIRMADO")


@permission_required("accounts.profissional_gerenciar", raise_exception=True)
def profissional_excecao_remanejar(request, pk, data):
    """
    O profissional avisou que não vem mais na data X e remarcou para a data Y:
    move os blocos de horário de X para Y e leva junto todos os agendamentos
    dos pacientes de X, no MESMO horário, para Y. Tudo ou nada — se algum
    agendamento não couber em Y (ex: conflito com outro atendimento já
    marcado lá), nada é alterado e a tela mostra o motivo.

    No fim, mostra a lista de pacientes remanejados (com telefone) para a
    recepção avisar cada um da nova data.
    """
    profissional = get_object_or_404(Profissional, pk=pk)
    if not request.user.has_perm("accounts.agenda_editar"):
        messages.error(
            request,
            "Remanejar uma data mexe na agenda dos pacientes — precisa também da permissão de editar agendamentos.",
        )
        return redirect("cadastros:profissional_excecoes", pk=profissional.pk)

    data_antiga = parse_date(data)
    blocos_antigos = []
    if data_antiga:
        blocos_antigos = list(
            ExcecaoAtendimento.objects.filter(profissional=profissional, data=data_antiga).prefetch_related(
                "procedimentos_permitidos"
            )
        )
    if not blocos_antigos:
        messages.error(request, "Essa data não tem horário cadastrado para remanejar.")
        return redirect("cadastros:profissional_excecoes", pk=profissional.pk)

    agendamentos = list(_agendamentos_ativos_na_data(profissional, data_antiga))
    bloqueados = [a for a in agendamentos if a.status not in STATUS_REMANEJAVEIS]
    erros = []
    remanejados = None
    data_nova = None

    if bloqueados:
        erros.append(
            "Alguns pacientes dessa data já passaram pela recepção/financeiro (ou já foram atendidos) — "
            "não dá para remanejar a data inteira automaticamente. Ajuste esses agendamentos pela agenda."
        )
    elif request.method == "POST":
        data_nova = parse_date(request.POST.get("data_nova") or "")
        if not data_nova:
            erros.append("Informe a nova data.")
        elif data_nova.weekday() >= 5:
            erros.append("A clínica não funciona aos sábados e domingos — escolha uma data de segunda a sexta.")
        elif data_nova < timezone.localdate():
            erros.append("A nova data não pode estar no passado.")
        elif data_nova == data_antiga:
            erros.append("A nova data é igual à data atual.")
        elif ExcecaoAtendimento.objects.filter(profissional=profissional, data=data_nova).exists():
            erros.append(
                f"{profissional.nome} já tem horário cadastrado em {data_nova:%d/%m/%Y}. Escolha outra data, "
                "ou remova/edite a data existente antes."
            )
        else:
            diferenca = data_nova - data_antiga
            nota = f"Remanejado de {data_antiga:%d/%m/%Y} para {data_nova:%d/%m/%Y} (profissional mudou a data)."
            try:
                with transaction.atomic():
                    ExcecaoAtendimento.objects.filter(pk__in=[b.pk for b in blocos_antigos]).update(data=data_nova)
                    for agendamento in agendamentos:
                        agendamento.data_hora_inicio += diferenca
                        agendamento.data_hora_fim += diferenca
                        agendamento.status = Agendamento.Status.AGENDADO
                        agendamento.observacoes = f"{agendamento.observacoes}\n{nota}".strip()
                        try:
                            agendamento.full_clean()
                        except ValidationError as erro:
                            hora = timezone.localtime(agendamento.data_hora_inicio)
                            raise ValidationError(
                                f"{agendamento.paciente.nome_completo} ({hora:%H:%M}): " + "; ".join(erro.messages)
                            )
                        agendamento.save()
                    LogAuditoria.registrar(
                        request,
                        request.user,
                        f"Remanejou a data de atendimento de {profissional.nome} de {data_antiga:%d/%m/%Y} para "
                        f"{data_nova:%d/%m/%Y} ({len(agendamentos)} agendamento(s) movido(s))",
                    )
                remanejados = agendamentos
            except ValidationError as erro:
                erros.append("Nada foi alterado — um agendamento não coube na nova data: " + "; ".join(erro.messages))

    for erro in erros:
        messages.error(request, erro)

    return render(
        request,
        "cadastros/profissional_excecao_remanejar.html",
        {
            "profissional": profissional,
            "data_antiga": data_antiga,
            "data_nova": data_nova,
            "blocos_antigos": blocos_antigos,
            "agendamentos": agendamentos,
            "bloqueados": bloqueados,
            "remanejados": remanejados,
            "voltar_url": reverse("cadastros:profissional_excecoes", args=[profissional.pk]),
        },
    )


# =====================================================================
# Procedimentos
# =====================================================================

@permission_required("accounts.procedimento_gerenciar", raise_exception=True)
def procedimento_lista(request):
    termo = request.GET.get("q", "").strip()
    procedimentos = Procedimento.objects.prefetch_related("profissionais_habilitados").all()
    if termo:
        procedimentos = procedimentos.filter(Q(nome__icontains=termo) | Q(codigo__icontains=termo))
    return render(
        request, "cadastros/procedimento_lista.html", {"pagina": paginar(request, procedimentos), "termo": termo}
    )


@permission_required("accounts.procedimento_gerenciar", raise_exception=True)
def procedimento_novo(request):
    if request.method == "POST":
        form = ProcedimentoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Procedimento cadastrado com sucesso.")
            return redirect("cadastros:procedimento_lista")
    else:
        form = ProcedimentoForm()
    return render(
        request,
        "cadastros/form_generico.html",
        {"form": form, "titulo": "Novo procedimento", "voltar_url": reverse("cadastros:procedimento_lista")},
    )


@permission_required("accounts.procedimento_gerenciar", raise_exception=True)
def procedimento_editar(request, pk):
    procedimento = get_object_or_404(Procedimento, pk=pk)
    if request.method == "POST":
        form = ProcedimentoForm(request.POST, instance=procedimento)
        if form.is_valid():
            form.save()
            messages.success(request, "Procedimento atualizado com sucesso.")
            return redirect("cadastros:procedimento_lista")
    else:
        form = ProcedimentoForm(instance=procedimento)
    return render(
        request,
        "cadastros/form_generico.html",
        {"form": form, "titulo": f"Editar procedimento: {procedimento.nome}", "voltar_url": reverse("cadastros:procedimento_lista")},
    )


@permission_required("accounts.procedimento_gerenciar", raise_exception=True)
def procedimento_excluir(request, pk):
    procedimento = get_object_or_404(Procedimento, pk=pk)
    if request.method == "POST":
        nome = procedimento.nome
        try:
            procedimento.delete()
        except ProtectedError:
            # AgendamentoItem.procedimento é PROTECT (ver comentário em agenda.models) — mesmo
            # padrão de paciente_excluir acima: orienta inativar em vez de excluir quando há
            # histórico de agendamento vinculado.
            messages.error(
                request,
                f"Não foi possível excluir '{nome}': existem agendamentos (passados ou futuros) com "
                "este procedimento e o histórico não pode ser perdido. Edite o cadastro dele e "
                "desmarque a opção \"Ativo\" em vez de excluir.",
            )
            return redirect("cadastros:procedimento_lista")
        messages.success(request, f"Procedimento '{nome}' excluído.")
        return redirect("cadastros:procedimento_lista")
    return render(
        request,
        "cadastros/confirmar_exclusao.html",
        {"objeto": procedimento, "voltar_url": reverse("cadastros:procedimento_lista")},
    )


@permission_required("accounts.procedimento_gerenciar", raise_exception=True)
def baixar_planilha_procedimentos(request):
    """
    Planilha com o catálogo de procedimentos atual (ou um modelo em branco, se ainda
    não houver nenhum) — inclui o valor particular, pra dar pra editar em massa e
    reimportar (ver procedimento_importar, que também lê essa coluna de volta).
    """
    if openpyxl is None:
        messages.error(request, "Suporte a .xlsx não está instalado no servidor (biblioteca 'openpyxl' ausente).")
        return redirect("cadastros:procedimento_lista")

    pasta_trabalho = openpyxl.Workbook()
    planilha = pasta_trabalho.active
    planilha.title = "Procedimentos"
    planilha.append(["Procedimento", "Código", "Valor particular"])

    procedimentos = Procedimento.objects.order_by("nome")
    if procedimentos.exists():
        for procedimento in procedimentos:
            planilha.append([
                procedimento.nome,
                procedimento.codigo,
                float(procedimento.valor_particular) if procedimento.valor_particular is not None else None,
            ])
    else:
        planilha.append(["Consulta de rotina", "10101012", None])
        planilha.append(["Eletrocardiograma", "40301012", None])

    buffer = io.BytesIO()
    pasta_trabalho.save(buffer)
    buffer.seek(0)

    resposta = HttpResponse(
        buffer.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resposta["Content-Disposition"] = "attachment; filename=procedimentos.xlsx"
    return resposta


@permission_required("accounts.procedimento_gerenciar", raise_exception=True)
def procedimento_importar(request):
    """
    Cadastra/atualiza vários procedimentos de uma vez a partir de uma
    planilha (Procedimento/Nome + Código opcional + Valor particular
    opcional) — útil para dar entrada rápida em um catálogo grande, ou pra
    baixar o catálogo atual (ver baixar_planilha_procedimentos), editar os
    valores particulares numa planilha e reimportar de uma vez.
    Procedimentos com o mesmo nome (sem diferenciar maiúsculas) só têm o
    código atualizado se estava em branco, mas o valor particular É
    sobrescrito sempre que a planilha trouxer um valor pra aquela linha —
    é assim que dá pra "alterar os valores" reimportando a planilha editada.
    """
    if request.method == "POST":
        form = ImportarProcedimentosForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                linhas_brutas = _linhas_brutas_da_planilha(form.cleaned_data["arquivo"])
            except ValueError as erro:
                messages.error(request, str(erro))
                return redirect("cadastros:procedimento_importar")

            cabecalho = [str(c).strip().lower() if c is not None else "" for c in linhas_brutas[0]]

            def indice(*nomes_possiveis):
                for nome in nomes_possiveis:
                    if nome in cabecalho:
                        return cabecalho.index(nome)
                return None

            idx_nome = indice("procedimento", "nome")
            idx_codigo = indice("código", "codigo", "tuss", "tiss", "cbhpm")
            idx_valor = indice("valor particular", "valor", "preço particular", "preco particular", "preço", "preco")
            linha_inicial = 1
            if idx_nome is None:
                idx_nome, idx_codigo, idx_valor, linha_inicial = 0, 1, 2, 0

            criados, atualizados = 0, 0
            ignoradas = []
            for numero_linha, linha in enumerate(linhas_brutas[linha_inicial:], start=linha_inicial + 1):
                if all(c in (None, "") for c in linha):
                    continue
                nome = str(linha[idx_nome]).strip() if idx_nome < len(linha) and linha[idx_nome] not in (None, "") else ""
                if not nome:
                    continue
                codigo = ""
                if idx_codigo is not None and idx_codigo < len(linha) and linha[idx_codigo] not in (None, ""):
                    codigo = str(linha[idx_codigo]).strip()

                valor = None
                if idx_valor is not None and idx_valor < len(linha) and linha[idx_valor] not in (None, ""):
                    try:
                        valor = Decimal(str(linha[idx_valor]).replace(",", "."))
                    except InvalidOperation:
                        ignoradas.append(f"linha {numero_linha} ('{nome}'): valor particular inválido")

                procedimento = Procedimento.objects.filter(nome__iexact=nome).first()
                if procedimento is None:
                    Procedimento.objects.create(nome=nome, codigo=codigo, valor_particular=valor)
                    criados += 1
                else:
                    campos_alterados = []
                    if codigo and not procedimento.codigo:
                        procedimento.codigo = codigo
                        campos_alterados.append("codigo")
                    if valor is not None and procedimento.valor_particular != valor:
                        procedimento.valor_particular = valor
                        campos_alterados.append("valor_particular")
                    if campos_alterados:
                        procedimento.save(update_fields=campos_alterados)
                    atualizados += 1

            messages.success(request, f"Importação concluída: {criados} procedimento(s) novo(s) e {atualizados} já existente(s) conferido(s).")
            if ignoradas:
                messages.warning(request, "Linhas ignoradas: " + "; ".join(ignoradas))
            return redirect("cadastros:procedimento_lista")
    else:
        form = ImportarProcedimentosForm()
    return render(request, "cadastros/procedimento_importar.html", {"form": form})


# =====================================================================
# Convênios (+ tabela de preços)
# =====================================================================

@permission_required("accounts.convenio_gerenciar", raise_exception=True)
def convenio_lista(request):
    convenios = Convenio.objects.all()
    return render(request, "cadastros/convenio_lista.html", {"pagina": paginar(request, convenios)})


@permission_required("accounts.convenio_gerenciar", raise_exception=True)
def convenio_novo(request):
    if request.method == "POST":
        form = ConvenioForm(request.POST)
        if form.is_valid():
            convenio = form.save()
            messages.success(request, "Convênio cadastrado com sucesso.")
            return redirect("cadastros:convenio_detalhe", pk=convenio.pk)
    else:
        form = ConvenioForm()
    return render(
        request,
        "cadastros/form_generico.html",
        {"form": form, "titulo": "Novo convênio", "voltar_url": reverse("cadastros:convenio_lista")},
    )


@permission_required("accounts.convenio_gerenciar", raise_exception=True)
def convenio_editar(request, pk):
    convenio = get_object_or_404(Convenio, pk=pk)
    if request.method == "POST":
        form = ConvenioForm(request.POST, instance=convenio)
        if form.is_valid():
            form.save()
            messages.success(request, "Convênio atualizado com sucesso.")
            return redirect("cadastros:convenio_detalhe", pk=convenio.pk)
    else:
        form = ConvenioForm(instance=convenio)
    return render(
        request,
        "cadastros/form_generico.html",
        {"form": form, "titulo": f"Editar convênio: {convenio.nome}", "voltar_url": reverse("cadastros:convenio_detalhe", args=[pk])},
    )


@permission_required("accounts.convenio_gerenciar", raise_exception=True)
def convenio_excluir(request, pk):
    convenio = get_object_or_404(Convenio, pk=pk)
    if request.method == "POST":
        nome = convenio.nome
        convenio.delete()
        messages.success(request, f"Convênio '{nome}' excluído.")
        return redirect("cadastros:convenio_lista")
    return render(
        request,
        "cadastros/confirmar_exclusao.html",
        {"objeto": convenio, "voltar_url": reverse("cadastros:convenio_lista")},
    )


@permission_required("accounts.convenio_gerenciar", raise_exception=True)
def convenio_detalhe(request, pk):
    """Tabela de preços paginada de 50 em 50, com busca — convênio com centenas de
    procedimentos (ex.: 648) carregava tudo numa página só, e devagar (pedido da clínica,
    03/11/2026)."""
    convenio = get_object_or_404(Convenio, pk=pk)
    termo = (request.GET.get("q") or "").strip()
    precos = convenio.tabela_precos.select_related("procedimento", "profissional").order_by(
        "procedimento__nome", "profissional__nome", "pk"
    )
    if termo:
        precos = precos.filter(
            Q(procedimento__nome__icontains=termo) | Q(procedimento__codigo__icontains=termo)
            | Q(profissional__nome__icontains=termo)
        )
    return render(
        request,
        "cadastros/convenio_detalhe.html",
        {
            "convenio": convenio,
            "pagina": paginar(request, precos),
            "termo": termo,
            "tem_precos": convenio.tabela_precos.exists(),
        },
    )


@permission_required("accounts.convenio_gerenciar", raise_exception=True)
def preco_novo(request, pk):
    convenio = get_object_or_404(Convenio, pk=pk)
    if request.method == "POST":
        form = TabelaPrecoForm(request.POST, convenio=convenio)
        if form.is_valid():
            preco = form.save(commit=False)
            preco.convenio = convenio
            preco.save()
            messages.success(request, "Preço adicionado com sucesso.")
            return redirect("cadastros:convenio_detalhe", pk=pk)
    else:
        form = TabelaPrecoForm(convenio=convenio)
    return render(
        request,
        "cadastros/form_generico.html",
        {"form": form, "titulo": f"Novo preço — {convenio.nome}", "voltar_url": reverse("cadastros:convenio_detalhe", args=[pk])},
    )


@permission_required("accounts.convenio_gerenciar", raise_exception=True)
def preco_editar(request, pk):
    preco = get_object_or_404(TabelaPrecoConvenio, pk=pk)
    convenio = preco.convenio
    if request.method == "POST":
        form = TabelaPrecoForm(request.POST, instance=preco, convenio=convenio)
        if form.is_valid():
            form.save()
            messages.success(request, "Preço atualizado com sucesso.")
            return redirect("cadastros:convenio_detalhe", pk=convenio.pk)
    else:
        form = TabelaPrecoForm(instance=preco, convenio=convenio)
    return render(
        request,
        "cadastros/form_generico.html",
        {
            "form": form,
            "titulo": f"Editar preço — {convenio.nome}",
            "voltar_url": reverse("cadastros:convenio_detalhe", args=[convenio.pk]),
        },
    )


@permission_required("accounts.convenio_gerenciar", raise_exception=True)
def preco_excluir(request, pk):
    preco = get_object_or_404(TabelaPrecoConvenio, pk=pk)
    convenio_pk = preco.convenio_id
    if request.method == "POST":
        preco.delete()
        messages.success(request, "Preço removido.")
        return redirect("cadastros:convenio_detalhe", pk=convenio_pk)
    return render(
        request,
        "cadastros/confirmar_exclusao.html",
        {"objeto": preco, "voltar_url": reverse("cadastros:convenio_detalhe", args=[convenio_pk])},
    )


# --- Importação de planilha de preços -----------------------------------

def _linhas_brutas_da_planilha(arquivo):
    """Lê um .xlsx ou .csv e devolve a matriz de linhas (lista de listas), sem interpretar colunas ainda."""
    nome_arquivo = arquivo.name.lower()

    if nome_arquivo.endswith(".xlsx"):
        if openpyxl is None:
            raise ValueError(
                "Suporte a arquivos .xlsx não está instalado no servidor (biblioteca 'openpyxl' ausente). "
                "Envie um arquivo .csv, ou peça para instalar 'openpyxl' (veja o requirements.txt)."
            )
        try:
            pasta_trabalho = openpyxl.load_workbook(arquivo, data_only=True)
        except Exception as erro:
            raise ValueError(f"Não foi possível ler o arquivo Excel: {erro}")
        planilha = pasta_trabalho.active
        linhas_brutas = [list(linha) for linha in planilha.iter_rows(values_only=True)]
    elif nome_arquivo.endswith(".csv"):
        conteudo = arquivo.read().decode("utf-8-sig", errors="replace")
        separador = ";" if conteudo.count(";") > conteudo.count(",") else ","
        linhas_brutas = list(csv.reader(io.StringIO(conteudo), delimiter=separador))
    else:
        raise ValueError("Formato de arquivo não suportado. Envie um arquivo .xlsx ou .csv.")

    linhas_brutas = [linha for linha in linhas_brutas if linha]
    if not linhas_brutas:
        raise ValueError("O arquivo está vazio.")
    return linhas_brutas


def _ler_linhas_planilha(arquivo):
    """
    Lê uma planilha de preços e devolve uma lista de tuplas
    (número da linha, (nome_procedimento, código, profissional, valor)).
    Reconhece o cabeçalho (Procedimento / Código / Profissional / Valor); se
    não encontrar um cabeçalho reconhecível, assume a ordem fixa: A =
    procedimento, B = código, C = valor (sem coluna de profissional), sem
    linha de cabeçalho.
    """
    linhas_brutas = _linhas_brutas_da_planilha(arquivo)

    cabecalho = [str(celula).strip().lower() if celula is not None else "" for celula in linhas_brutas[0]]

    def indice(*nomes_possiveis):
        for nome in nomes_possiveis:
            if nome in cabecalho:
                return cabecalho.index(nome)
        return None

    idx_nome = indice("procedimento", "nome")
    idx_codigo = indice("código", "codigo", "tuss", "tiss", "cbhpm")
    idx_profissional = indice("profissional", "médico", "medico")
    idx_valor = indice("valor", "preço", "preco")

    linha_inicial = 1
    if idx_nome is None or idx_valor is None:
        idx_nome, idx_codigo, idx_profissional, idx_valor = 0, 1, None, 2
        linha_inicial = 0

    def valor_em(linha, indice_coluna):
        if indice_coluna is None or indice_coluna >= len(linha):
            return None
        return linha[indice_coluna]

    resultado = []
    for numero_linha, linha in enumerate(linhas_brutas[linha_inicial:], start=linha_inicial + 1):
        if all(celula in (None, "") for celula in linha):
            continue
        resultado.append((
            numero_linha,
            (
                valor_em(linha, idx_nome),
                valor_em(linha, idx_codigo),
                valor_em(linha, idx_profissional),
                valor_em(linha, idx_valor),
            ),
        ))
    return resultado


@permission_required("accounts.convenio_gerenciar", raise_exception=True)
def convenio_importar_precos(request, pk):
    convenio = get_object_or_404(Convenio, pk=pk)
    if request.method == "POST":
        form = ImportarTabelaPrecoForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                linhas = _ler_linhas_planilha(form.cleaned_data["arquivo"])
            except ValueError as erro:
                messages.error(request, str(erro))
                return redirect("cadastros:convenio_importar_precos", pk=pk)

            criados, atualizados, ignoradas = 0, 0, []
            for numero_linha, (nome_proc, codigo, nome_profissional, valor_bruto) in linhas:
                nome_proc = (str(nome_proc).strip() if nome_proc is not None else "")
                if not nome_proc:
                    continue

                # Linha sem valor preenchido: normal em planilhas geradas a partir do
                # catálogo (ainda não precificadas para este convênio) — pula sem avisar.
                if valor_bruto in (None, ""):
                    continue

                try:
                    valor = Decimal(str(valor_bruto).replace(",", "."))
                except InvalidOperation:
                    ignoradas.append(f"linha {numero_linha} ('{nome_proc}'): valor inválido")
                    continue

                codigo_str = str(codigo).strip() if codigo not in (None, "") else ""
                nome_profissional_str = str(nome_profissional).strip() if nome_profissional not in (None, "") else ""

                profissional = None
                if nome_profissional_str:
                    profissional = Profissional.objects.filter(nome__iexact=nome_profissional_str).first()
                    if profissional is None:
                        ignoradas.append(f"linha {numero_linha} ('{nome_proc}'): profissional '{nome_profissional_str}' não encontrado")
                        continue

                procedimento = Procedimento.objects.filter(nome__iexact=nome_proc).first()

                # Preço específico de um profissional só para procedimento vinculado a
                # ele — mesma regra de TabelaPrecoForm.clean() (tela manual). Preço já
                # salvo com a mesma combinação pode continuar sendo atualizado. Checado
                # antes de criar procedimento novo, pra não deixar procedimento órfão
                # cadastrado por uma linha que acabou ignorada.
                if profissional is not None:
                    vinculado = procedimento is not None and (
                        profissional.procedimentos_habilitados.filter(pk=procedimento.pk).exists()
                        or TabelaPrecoConvenio.objects.filter(
                            convenio=convenio, procedimento=procedimento, profissional=profissional
                        ).exists()
                    )
                    if not vinculado:
                        ignoradas.append(
                            f"linha {numero_linha} ('{nome_proc}'): procedimento não vinculado a "
                            f"'{profissional}' (Cadastros → Procedimentos → profissionais habilitados)"
                        )
                        continue

                if procedimento is None:
                    procedimento = Procedimento.objects.create(nome=nome_proc, codigo=codigo_str)
                elif codigo_str and not procedimento.codigo:
                    procedimento.codigo = codigo_str
                    procedimento.save(update_fields=["codigo"])

                _, preco_foi_criado = TabelaPrecoConvenio.objects.update_or_create(
                    convenio=convenio, procedimento=procedimento, profissional=profissional, defaults={"valor": valor}
                )
                if preco_foi_criado:
                    criados += 1
                else:
                    atualizados += 1

            messages.success(request, f"Importação concluída: {criados} preço(s) novo(s) e {atualizados} atualizado(s).")
            if ignoradas:
                messages.warning(request, "Linhas ignoradas: " + "; ".join(ignoradas[:10]) + ("..." if len(ignoradas) > 10 else ""))
            return redirect("cadastros:convenio_detalhe", pk=pk)
    else:
        form = ImportarTabelaPrecoForm()
    return render(request, "cadastros/convenio_importar_precos.html", {"form": form, "convenio": convenio})


@permission_required("accounts.convenio_gerenciar", raise_exception=True)
def baixar_modelo_precos(request):
    """
    Planilha para começar a precificar um convênio: já vem com todos os
    procedimentos hoje cadastrados no catálogo (Procedimento/Código), com as
    colunas Profissional e Valor em branco para você preencher. Se um
    procedimento tiver o mesmo valor pra todo mundo, deixe "Profissional" em
    branco naquela linha; se for diferente para um profissional específico
    (ex: consulta com fonoaudiólogo x consulta com médico), duplique a linha
    do procedimento e informe o nome do profissional na coluna Profissional
    — não precisa cadastrar "Consulta Fono", "Consulta Cardio" etc. como
    procedimentos separados.
    """
    if openpyxl is None:
        messages.error(request, "Suporte a .xlsx não está instalado no servidor (biblioteca 'openpyxl' ausente).")
        return redirect("cadastros:convenio_lista")

    pasta_trabalho = openpyxl.Workbook()
    planilha = pasta_trabalho.active
    planilha.title = "Precos"
    planilha.append(["Procedimento", "Código", "Profissional (opcional)", "Valor"])

    procedimentos = Procedimento.objects.filter(ativo=True).order_by("nome")
    if procedimentos.exists():
        for procedimento in procedimentos:
            planilha.append([procedimento.nome, procedimento.codigo, "", ""])
    else:
        planilha.append(["Consulta de rotina", "10101012", "", "150.00"])
        planilha.append(["Consulta de rotina", "10101012", "Dra. Exemplo Fonoaudióloga", "120.00"])
        planilha.append(["Eletrocardiograma", "40301012", "", "80.00"])

    buffer = io.BytesIO()
    pasta_trabalho.save(buffer)
    buffer.seek(0)

    resposta = HttpResponse(
        buffer.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resposta["Content-Disposition"] = "attachment; filename=modelo_tabela_precos.xlsx"
    return resposta


def _nome_arquivo_seguro(texto):
    """Vira um nome de arquivo simples (sem acentos/espaços/símbolos) para o Content-Disposition."""
    import re
    import unicodedata

    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9_-]+", "_", sem_acento).strip("_").lower() or "convenio"


@permission_required("accounts.convenio_gerenciar", raise_exception=True)
def convenio_exportar_precos(request, pk):
    """
    Gera uma planilha com a tabela de preços ATUAL deste convênio já preenchida
    (Procedimento, Código, Valor). A ideia é: baixar aqui, atualizar os valores
    reajustados na própria planilha (sem precisar digitar tudo de novo) e depois
    enviar de volta em "Importar planilha" — como cada preço já existe para este
    convênio, a importação apenas atualiza o valor, sem duplicar nada.
    """
    convenio = get_object_or_404(Convenio, pk=pk)

    if openpyxl is None:
        messages.error(request, "Suporte a .xlsx não está instalado no servidor (biblioteca 'openpyxl' ausente).")
        return redirect("cadastros:convenio_detalhe", pk=pk)

    precos = convenio.tabela_precos.select_related("procedimento", "profissional").order_by("procedimento__nome", "profissional__nome")

    pasta_trabalho = openpyxl.Workbook()
    planilha = pasta_trabalho.active
    planilha.title = "Precos"
    planilha.append(["Procedimento", "Código", "Profissional (opcional)", "Valor"])
    for preco in precos:
        planilha.append([
            preco.procedimento.nome,
            preco.procedimento.codigo,
            preco.profissional.nome if preco.profissional_id else "",
            float(preco.valor),
        ])

    buffer = io.BytesIO()
    pasta_trabalho.save(buffer)
    buffer.seek(0)

    resposta = HttpResponse(
        buffer.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    nome_arquivo = f"precos_{_nome_arquivo_seguro(convenio.nome)}.xlsx"
    resposta["Content-Disposition"] = f"attachment; filename={nome_arquivo}"
    return resposta


# =====================================================================
# Salas (cadastro simples — preparação para fila/senha por sala)
# =====================================================================

@permission_required("accounts.sala_gerenciar", raise_exception=True)
def sala_lista(request):
    salas = Sala.objects.all()
    return render(request, "cadastros/sala_lista.html", {"salas": salas})


@permission_required("accounts.sala_gerenciar", raise_exception=True)
def sala_novo(request):
    if request.method == "POST":
        form = SalaForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Sala cadastrada com sucesso.")
            return redirect("cadastros:sala_lista")
    else:
        form = SalaForm()
    return render(
        request,
        "cadastros/form_generico.html",
        {"form": form, "titulo": "Nova sala", "voltar_url": reverse("cadastros:sala_lista")},
    )


@permission_required("accounts.sala_gerenciar", raise_exception=True)
def sala_editar(request, pk):
    sala = get_object_or_404(Sala, pk=pk)
    if request.method == "POST":
        form = SalaForm(request.POST, instance=sala)
        if form.is_valid():
            form.save()
            messages.success(request, "Sala atualizada com sucesso.")
            return redirect("cadastros:sala_lista")
    else:
        form = SalaForm(instance=sala)
    return render(
        request,
        "cadastros/form_generico.html",
        {"form": form, "titulo": f"Editar sala: {sala.nome}", "voltar_url": reverse("cadastros:sala_lista")},
    )


@permission_required("accounts.sala_gerenciar", raise_exception=True)
def sala_excluir(request, pk):
    sala = get_object_or_404(Sala, pk=pk)
    if request.method == "POST":
        nome = sala.nome
        sala.delete()
        messages.success(request, f"Sala '{nome}' excluída.")
        return redirect("cadastros:sala_lista")
    return render(
        request,
        "cadastros/confirmar_exclusao.html",
        {"objeto": sala, "voltar_url": reverse("cadastros:sala_lista")},
    )


# =====================================================================
# Guichês (cadastro simples — preparação para fila/senha na recepção)
# =====================================================================

@permission_required("accounts.guiche_gerenciar", raise_exception=True)
def guiche_lista(request):
    guiches = Guiche.objects.all()
    return render(request, "cadastros/guiche_lista.html", {"guiches": guiches})


@permission_required("accounts.guiche_gerenciar", raise_exception=True)
def guiche_novo(request):
    if request.method == "POST":
        form = GuicheForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Guichê cadastrado com sucesso.")
            return redirect("cadastros:guiche_lista")
    else:
        form = GuicheForm()
    return render(
        request,
        "cadastros/form_generico.html",
        {"form": form, "titulo": "Novo guichê", "voltar_url": reverse("cadastros:guiche_lista")},
    )


@permission_required("accounts.guiche_gerenciar", raise_exception=True)
def guiche_editar(request, pk):
    guiche = get_object_or_404(Guiche, pk=pk)
    if request.method == "POST":
        form = GuicheForm(request.POST, instance=guiche)
        if form.is_valid():
            form.save()
            messages.success(request, "Guichê atualizado com sucesso.")
            return redirect("cadastros:guiche_lista")
    else:
        form = GuicheForm(instance=guiche)
    return render(
        request,
        "cadastros/form_generico.html",
        {"form": form, "titulo": f"Editar guichê: {guiche.nome}", "voltar_url": reverse("cadastros:guiche_lista")},
    )


@permission_required("accounts.guiche_gerenciar", raise_exception=True)
def guiche_excluir(request, pk):
    guiche = get_object_or_404(Guiche, pk=pk)
    if request.method == "POST":
        nome = guiche.nome
        guiche.delete()
        messages.success(request, f"Guichê '{nome}' excluído.")
        return redirect("cadastros:guiche_lista")
    return render(
        request,
        "cadastros/confirmar_exclusao.html",
        {"objeto": guiche, "voltar_url": reverse("cadastros:guiche_lista")},
    )


# =====================================================================
# API auxiliar (JavaScript) — procedimentos vinculados a um profissional
# =====================================================================

@login_required
def procedimentos_do_profissional(request, pk):
    """
    Endpoint usado via JavaScript nas telas que têm 'profissional' e
    'procedimento' no mesmo formulário (Limite diário, Tabela de preços do
    convênio — ver cadastros/templates/cadastros/form_generico.html):
    devolve só os procedimentos que aquele profissional está habilitado a
    realizar (campo 'profissionais habilitados', em Cadastros →
    Procedimentos), pra escolher o profissional já restringir a lista de
    procedimentos em vez de mostrar o cadastro inteiro.

    Sem a trava de preço aplicada no endpoint equivalente da tela de
    Agendamento (agenda.views.procedimentos_do_profissional) — lá é preciso
    ter preço cadastrado pra poder agendar; aqui o contexto costuma ser
    justamente cadastrar esse preço ou definir um limite, então exigir preço
    prévio não faz sentido.

    Pedido do cliente: SEM fallback pra "todos os procedimentos da clínica"
    quando o profissional ainda não tem nenhum habilitado configurado — a
    lista vem vazia mesmo e o form_generico.html avisa pra vincular os
    procedimentos primeiro (mesmo critério das telas de Horários, ver
    _procedimentos_do_profissional_para_bloco).
    """
    profissional = get_object_or_404(Profissional, pk=pk)
    procedimentos = profissional.procedimentos_habilitados.filter(ativo=True).order_by("nome")
    dados = [{"id": p.pk, "nome": p.nome} for p in procedimentos]
    return JsonResponse({"procedimentos": dados})


# =====================================================================
# Limites diários de procedimento por profissional (Central de Atendimento)
# =====================================================================

@permission_required("accounts.limite_diario_gerenciar", raise_exception=True)
def limite_diario_lista(request):
    limites = LimiteDiarioProcedimento.objects.select_related("profissional", "procedimento", "convenio").all()
    return render(request, "cadastros/limite_diario_lista.html", {"limites": limites})


@permission_required("accounts.limite_diario_gerenciar", raise_exception=True)
def limite_diario_novo(request):
    if request.method == "POST":
        form = LimiteDiarioProcedimentoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Limite diário cadastrado com sucesso.")
            return redirect("cadastros:limite_diario_lista")
    else:
        form = LimiteDiarioProcedimentoForm()
    return render(
        request,
        "cadastros/form_generico.html",
        {"form": form, "titulo": "Novo limite diário", "voltar_url": reverse("cadastros:limite_diario_lista")},
    )


@permission_required("accounts.limite_diario_gerenciar", raise_exception=True)
def limite_diario_editar(request, pk):
    limite = get_object_or_404(LimiteDiarioProcedimento, pk=pk)
    if request.method == "POST":
        form = LimiteDiarioProcedimentoForm(request.POST, instance=limite)
        if form.is_valid():
            form.save()
            messages.success(request, "Limite diário atualizado com sucesso.")
            return redirect("cadastros:limite_diario_lista")
    else:
        form = LimiteDiarioProcedimentoForm(instance=limite)
    return render(
        request,
        "cadastros/form_generico.html",
        {"form": form, "titulo": "Editar limite diário", "voltar_url": reverse("cadastros:limite_diario_lista")},
    )


@permission_required("accounts.limite_diario_gerenciar", raise_exception=True)
def limite_diario_excluir(request, pk):
    limite = get_object_or_404(LimiteDiarioProcedimento, pk=pk)
    if request.method == "POST":
        limite.delete()
        messages.success(request, "Limite diário excluído.")
        return redirect("cadastros:limite_diario_lista")
    return render(
        request,
        "cadastros/confirmar_exclusao.html",
        {"objeto": limite, "voltar_url": reverse("cadastros:limite_diario_lista")},
    )


# =====================================================================
# Pacotes / combos particulares (preço fechado, pago de uma vez)
# =====================================================================

@permission_required("accounts.procedimento_gerenciar", raise_exception=True)
def pacote_lista(request):
    pacotes = Pacote.objects.select_related("profissional").prefetch_related("itens__procedimento")
    return render(request, "cadastros/pacote_lista.html", {"pacotes": pacotes})


def _pacote_form(request, pacote, titulo):
    if request.method == "POST":
        form = PacoteForm(request.POST, instance=pacote)
        formset = PacoteItemFormSet(request.POST, instance=form.instance)
        form_valido = form.is_valid()
        formset_valido = formset.is_valid()
        if form_valido and formset_valido:
            with transaction.atomic():
                pacote = form.save()
                formset.instance = pacote
                formset.save()
            messages.success(request, f"Pacote '{pacote.nome}' salvo.")
            return redirect("cadastros:pacote_lista")
    else:
        form = PacoteForm(instance=pacote)
        formset = PacoteItemFormSet(instance=form.instance)
    # profissional_id -> ids dos procedimentos habilitados, pro JavaScript de pacote_form.html
    # mostrar só o que o profissional escolhido realiza (a trava de verdade é no formset).
    habilitados_por_profissional = {}
    for profissional_id, procedimento_id in Procedimento.profissionais_habilitados.through.objects.values_list(
        "profissional_id", "procedimento_id"
    ):
        habilitados_por_profissional.setdefault(str(profissional_id), []).append(procedimento_id)
    return render(
        request,
        "cadastros/pacote_form.html",
        {
            "form": form,
            "formset": formset,
            "titulo": titulo,
            "voltar_url": reverse("cadastros:pacote_lista"),
            "habilitados_por_profissional": habilitados_por_profissional,
        },
    )


@permission_required("accounts.procedimento_gerenciar", raise_exception=True)
def pacote_novo(request):
    return _pacote_form(request, None, "Novo pacote")


@permission_required("accounts.procedimento_gerenciar", raise_exception=True)
def pacote_editar(request, pk):
    pacote = get_object_or_404(Pacote, pk=pk)
    return _pacote_form(request, pacote, f"Editar pacote: {pacote.nome}")


@permission_required("accounts.procedimento_gerenciar", raise_exception=True)
def pacote_excluir(request, pk):
    pacote = get_object_or_404(Pacote, pk=pk)
    if request.method == "POST":
        nome = pacote.nome
        try:
            pacote.delete()
        except ProtectedError:
            messages.error(
                request,
                f"Não foi possível excluir '{nome}': ele já foi vendido a pacientes e o histórico não pode "
                "ser perdido. Edite o pacote e desmarque \"Ativo\" para parar de oferecê-lo.",
            )
            return redirect("cadastros:pacote_lista")
        messages.success(request, f"Pacote '{nome}' excluído.")
        return redirect("cadastros:pacote_lista")
    return render(
        request,
        "cadastros/confirmar_exclusao.html",
        {"objeto": pacote, "voltar_url": reverse("cadastros:pacote_lista")},
    )
