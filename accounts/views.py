from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .paginacao import paginar
from .forms import (
    SENHA_PADRAO_NOVO_USUARIO,
    LoginForm,
    MeuPerfilForm,
    PerfilForm,
    RedefinirSenhaUsuarioForm,
    TrocarSenhaForm,
    UsuarioCriarForm,
    UsuarioEditarForm,
)
from .models import LogAuditoria
from .permissoes import DEPARTAMENTOS_PERMISSOES, PAGINAS_FAVORITAVEIS

Usuario = get_user_model()


def _ip_do_cliente(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _registrar_log(request, usuario, acao):
    LogAuditoria.objects.create(usuario=usuario, acao=acao, ip=_ip_do_cliente(request))


def login_view(request):
    if request.user.is_authenticated:
        return redirect("home")

    max_tentativas = getattr(settings, "MAX_TENTATIVAS_LOGIN", 5)
    form = LoginForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        username = form.cleaned_data["username"]
        password = form.cleaned_data["password"]

        try:
            usuario_existente = Usuario.objects.get(username=username)
        except Usuario.DoesNotExist:
            usuario_existente = None

        # Conta já bloqueada: nem tenta autenticar, só avisa.
        if usuario_existente is not None and usuario_existente.status == Usuario.Status.BLOQUEADO:
            messages.error(
                request,
                "Este usuário está bloqueado por excesso de tentativas inválidas. "
                "Procure o administrador do sistema para desbloquear.",
            )
            return render(request, "accounts/login.html", {"form": form})

        usuario_autenticado = authenticate(request, username=username, password=password)

        if usuario_autenticado is not None:
            if usuario_autenticado.status == Usuario.Status.INATIVO:
                messages.error(request, "Este usuário está inativo. Procure o administrador do sistema.")
                return render(request, "accounts/login.html", {"form": form})

            usuario_autenticado.registrar_login_valido()
            auth_login(request, usuario_autenticado)
            _registrar_log(request, usuario_autenticado, "Login realizado com sucesso")

            if usuario_autenticado.exigir_troca_senha:
                messages.info(request, "Por segurança, defina uma nova senha para continuar.")
                return redirect("trocar_senha")
            return redirect("home")

        # Credenciais inválidas: se o usuário existe, conta a tentativa.
        if usuario_existente is not None:
            bloqueou_agora = usuario_existente.registrar_tentativa_invalida(max_tentativas)
            if bloqueou_agora:
                _registrar_log(request, usuario_existente, "Usuário bloqueado automaticamente por excesso de tentativas inválidas")
                messages.error(
                    request,
                    "Usuário ou senha inválidos. Esta foi a última tentativa permitida — "
                    "a conta foi bloqueada. Procure o administrador do sistema.",
                )
            else:
                restantes = max_tentativas - usuario_existente.tentativas_login_invalidas
                messages.error(request, f"Usuário ou senha inválidos. Tentativas restantes: {restantes}.")
        else:
            messages.error(request, "Usuário ou senha inválidos.")

    return render(request, "accounts/login.html", {"form": form})


@login_required
def logout_view(request):
    _registrar_log(request, request.user, "Logout realizado")
    auth_logout(request)
    messages.success(request, "Sessão encerrada com sucesso.")
    return redirect("login")


@login_required
def meu_perfil_view(request):
    if request.method == "POST":
        form = MeuPerfilForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Foto de perfil atualizada com sucesso.")
            return redirect("meu_perfil")
    else:
        form = MeuPerfilForm(instance=request.user)
    return render(request, "accounts/meu_perfil.html", {"form": form})


@login_required
def trocar_senha_view(request):
    if request.method == "POST":
        form = TrocarSenhaForm(user=request.user, data=request.POST)
        if form.is_valid():
            form.save()
            request.user.exigir_troca_senha = False
            request.user.save(update_fields=["exigir_troca_senha"])
            # Mantém a sessão válida mesmo após trocar a senha.
            update_session_auth_hash(request, request.user)
            _registrar_log(request, request.user, "Senha alterada pelo próprio usuário")
            messages.success(request, "Senha alterada com sucesso.")
            return redirect("home")
    else:
        form = TrocarSenhaForm(user=request.user)

    return render(request, "accounts/trocar_senha.html", {"form": form})


@login_required
def alternar_tema_view(request):
    """
    Alterna claro/escuro para o usuário logado.

    Chamado via fetch() em background pelo JS do base.html (não é mais um
    <form> que recarrega a página) — assim trocar o tema não derruba dados
    que a pessoa esteja digitando em algum formulário na tela (ex: a grade de
    Horários de atendimento). Quando chamado assim, devolve um JSON simples
    em vez de redirecionar. Mantemos o redirect como plano B, caso algo ainda
    chame esta view de forma tradicional (form/link direto).
    """
    if request.method == "POST":
        Usuario_ = get_user_model()
        novo_tema = Usuario_.Tema.ESCURO if request.user.tema == Usuario_.Tema.CLARO else Usuario_.Tema.CLARO
        request.user.tema = novo_tema
        request.user.save(update_fields=["tema"])

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"tema": novo_tema})

    destino = request.META.get("HTTP_REFERER") or "home"
    return redirect(destino)


@login_required
def alternar_favorito_view(request):
    """
    Fixa/desafixa uma página em "Favoritos", no topo do menu lateral. Diferente do
    tema (alternar_tema_view acima), aqui recarrega a página ao final — a lista de
    favoritos muda de tamanho e de conteúdo, então não dá pra só trocar uma classe
    CSS no clique como no tema.
    """
    if request.method == "POST":
        pagina_id = request.POST.get("pagina", "")
        if pagina_id in PAGINAS_FAVORITAVEIS and request.user.has_perm(PAGINAS_FAVORITAVEIS[pagina_id]["permissao"]):
            favoritas = list(request.user.paginas_favoritas or [])
            if pagina_id in favoritas:
                favoritas.remove(pagina_id)
            else:
                favoritas.append(pagina_id)
            request.user.paginas_favoritas = favoritas
            request.user.save(update_fields=["paginas_favoritas"])

    destino = request.META.get("HTTP_REFERER") or "home"
    return redirect(destino)


@login_required
def home_view(request):
    contexto = {}
    if request.user.profissional_id:
        # Painel rápido do dia pro profissional — contagem por situação dos
        # PRÓPRIOS atendimentos de hoje (ver agenda.models.Agendamento.Status).
        # Import local pra evitar import circular entre accounts e agenda no
        # carregamento do módulo (agenda não importa accounts.views).
        from django.db.models import Sum

        from agenda.models import Agendamento, AgendamentoItem

        agendamentos_hoje = Agendamento.objects.filter(
            profissional_id=request.user.profissional_id,
            data_hora_inicio__date=timezone.localdate(),
        ).exclude(status=Agendamento.Status.CANCELADO)
        contexto["stats_profissional"] = {
            "agendados": agendamentos_hoje.filter(status=Agendamento.Status.AGENDADO).count(),
            "pre_confirmados": agendamentos_hoje.filter(status=Agendamento.Status.PRE_CONFIRMADO).count(),
            "confirmados": agendamentos_hoje.filter(status=Agendamento.Status.CONFIRMADO).count(),
            "atendidos": agendamentos_hoje.filter(status=Agendamento.Status.REALIZADO).count(),
            # Procedimentos dos já atendidos (atendimento com 2 procedimentos conta 2) — pedido da clínica, 26/10/2026.
            "procedimentos": AgendamentoItem.objects.filter(
                agendamento__in=agendamentos_hoje.filter(status=Agendamento.Status.REALIZADO)
            ).aggregate(total=Sum("quantidade"))["total"]
            or 0,
            "total": agendamentos_hoje.count(),
        }
        # Faixa "Agora" no Início: o próximo atendimento do dia que ainda não
        # foi realizado — pra responder "quem vem agora" sem abrir a agenda.
        contexto["proximo_atendimento"] = (
            agendamentos_hoje
            .exclude(status=Agendamento.Status.REALIZADO)
            .exclude(status=Agendamento.Status.FALTOU)
            .select_related("paciente")
            .order_by("data_hora_inicio", "pk")
            .first()
        )
    return render(request, "accounts/home.html", contexto)


# =====================================================================
# Usuários (tela própria, fora do /admin/) — quem pode gerenciar usuários
# tem a permissão custom "accounts.usuario_gerenciar" (ver seed_perfis).
# =====================================================================

@permission_required("accounts.usuario_gerenciar", raise_exception=True)
def usuario_lista(request):
    usuarios = Usuario.objects.all().prefetch_related("groups").order_by("username")
    return render(request, "accounts/usuario_lista.html", {"pagina": paginar(request, usuarios)})


@permission_required("accounts.usuario_gerenciar", raise_exception=True)
def usuario_novo(request):
    permissoes_individuais_selecionadas = set()
    if request.method == "POST":
        form = UsuarioCriarForm(request.POST)
        # Lido já aqui (antes do is_valid) para que, se o formulário for
        # inválido, o template reabra com as mesmas marcações que a pessoa
        # tinha feito — em vez de resetar tudo, como acontecia antes.
        permissoes_individuais_selecionadas = set(request.POST.getlist("permissoes_individuais"))
        if form.is_valid():
            usuario = form.save()
            if request.user.is_superuser:
                usuario.user_permissions.set(
                    Permission.objects.filter(codename__in=permissoes_individuais_selecionadas)
                )
            _registrar_log(request, request.user, f"Usuário '{usuario.username}' criado")
            messages.success(
                request,
                f"Usuário '{usuario.username}' criado com sucesso. Senha inicial: "
                f"'{SENHA_PADRAO_NOVO_USUARIO}' — repasse a ele(a); no primeiro login será exigida a troca.",
            )
            return redirect("usuario_lista")
    else:
        form = UsuarioCriarForm()
    return render(
        request,
        "accounts/usuario_form.html",
        {
            "form": form,
            "titulo": "Novo usuário",
            "departamentos": DEPARTAMENTOS_PERMISSOES,
            "permissoes_individuais_selecionadas": permissoes_individuais_selecionadas,
        },
    )


@permission_required("accounts.usuario_gerenciar", raise_exception=True)
def usuario_editar(request, pk):
    usuario = get_object_or_404(Usuario, pk=pk)
    if request.method == "POST":
        form = UsuarioEditarForm(request.POST, instance=usuario)
        # Lido já aqui (antes do is_valid) para que, se o formulário for
        # inválido, o template reabra com as mesmas marcações que a pessoa
        # tinha feito — em vez de voltar ao que já estava salvo no banco.
        permissoes_individuais_selecionadas = set(request.POST.getlist("permissoes_individuais"))
        if form.is_valid():
            form.save()
            if request.user.is_superuser:
                usuario.user_permissions.set(
                    Permission.objects.filter(codename__in=permissoes_individuais_selecionadas)
                )
            _registrar_log(request, request.user, f"Usuário '{usuario.username}' editado")
            messages.success(request, "Usuário atualizado com sucesso.")
            return redirect("usuario_lista")
    else:
        form = UsuarioEditarForm(instance=usuario)
        permissoes_individuais_selecionadas = set(usuario.user_permissions.values_list("codename", flat=True))
    return render(
        request,
        "accounts/usuario_form.html",
        {
            "form": form,
            "titulo": f"Editar usuário: {usuario.username}",
            "departamentos": DEPARTAMENTOS_PERMISSOES,
            "permissoes_individuais_selecionadas": permissoes_individuais_selecionadas,
        },
    )


@permission_required("accounts.usuario_gerenciar", raise_exception=True)
def usuario_alternar_status(request, pk):
    """Ativa/inativa o usuário — evitamos exclusão física para preservar o histórico (logs, agenda etc.)."""
    usuario = get_object_or_404(Usuario, pk=pk)
    if request.method == "POST":
        if usuario.pk == request.user.pk:
            messages.error(request, "Você não pode desativar o seu próprio usuário.")
            return redirect("usuario_lista")

        if usuario.status == Usuario.Status.ATIVO:
            usuario.status = Usuario.Status.INATIVO
            usuario.is_active = False
            acao = "desativado"
        else:
            usuario.status = Usuario.Status.ATIVO
            usuario.is_active = True
            usuario.tentativas_login_invalidas = 0
            acao = "reativado"
        usuario.save(update_fields=["status", "is_active", "tentativas_login_invalidas"])
        _registrar_log(request, request.user, f"Usuário '{usuario.username}' {acao}")
        messages.success(request, f"Usuário '{usuario.username}' {acao} com sucesso.")
    return redirect("usuario_lista")


@permission_required("accounts.usuario_gerenciar", raise_exception=True)
def usuario_redefinir_senha(request, pk):
    """Um administrador define uma nova senha para outro usuário (ex: ele esqueceu a senha)."""
    usuario = get_object_or_404(Usuario, pk=pk)
    if request.method == "POST":
        form = RedefinirSenhaUsuarioForm(user=usuario, data=request.POST)
        if form.is_valid():
            form.save()
            usuario.exigir_troca_senha = True
            usuario.save(update_fields=["exigir_troca_senha"])
            _registrar_log(request, request.user, f"Senha do usuário '{usuario.username}' redefinida pelo administrador")
            messages.success(request, f"Senha de '{usuario.username}' redefinida. Ele(a) vai precisar trocá-la no próximo acesso.")
            return redirect("usuario_lista")
    else:
        form = RedefinirSenhaUsuarioForm(user=usuario)
    return render(request, "accounts/usuario_form.html", {"form": form, "titulo": f"Redefinir senha: {usuario.username}"})


@permission_required("accounts.usuario_gerenciar", raise_exception=True)
def usuario_excluir(request, pk):
    """
    Exclusão definitiva — só permitida se o usuário já estiver Inativo (ver
    usuario_alternar_status). Isso evita excluir por engano um usuário que
    ainda está em uso; primeiro desativa, depois (se quiser) exclui de vez,
    por exemplo para não deixar a lista de usuários crescendo sem necessidade.
    """
    usuario = get_object_or_404(Usuario, pk=pk)

    if usuario.pk == request.user.pk:
        messages.error(request, "Você não pode excluir o seu próprio usuário.")
        return redirect("usuario_lista")

    if usuario.status != Usuario.Status.INATIVO:
        messages.error(request, "Só é possível excluir um usuário que já esteja Inativo. Desative primeiro.")
        return redirect("usuario_lista")

    if request.method == "POST":
        nome = usuario.username
        usuario.delete()
        _registrar_log(request, request.user, f"Usuário '{nome}' excluído definitivamente")
        messages.success(request, f"Usuário '{nome}' excluído definitivamente.")
        return redirect("usuario_lista")

    return render(
        request,
        "cadastros/confirmar_exclusao.html",
        {
            "objeto": usuario,
            "voltar_url": reverse("usuario_lista"),
            "aviso_extra": "Esta ação apaga o login de vez (diferente de desativar) e não pode ser desfeita.",
        },
    )


# =====================================================================
# Perfis (Groups do Django) — tela própria pra montar/editar o que cada
# perfil pode fazer, com as permissões organizadas por departamento
# (ver accounts/permissoes.py). Some com as permissões individuais de um
# usuário (ver usuario_novo/usuario_editar acima): o que vale pra cada
# usuário é a SOMA das permissões do(s) perfil(is) dele com as extras
# marcadas só pra ele — assim já funciona pelo jeito nativo do Django
# (User.has_perm junta permissões de grupo + permissões diretas do usuário).
#
# Restrito a is_superuser (não a uma permission) de propósito: uma permission
# como "perfil_gerenciar" pode ser autoconcedida por quem já a tem — inclusive
# pela própria tela de Usuários, via "permissões extras" — então ela não
# protege de verdade quem pode editar o que os outros podem ou não fazer.
# is_superuser é o único sinal do sistema que não é editável pela própria UI.
# =====================================================================

def _somente_super_admin(view_func):
    @wraps(view_func)
    @login_required
    def _verificado(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return _verificado


@_somente_super_admin
def perfil_lista(request):
    perfis = Group.objects.all().order_by("name")
    return render(request, "accounts/perfil_lista.html", {"perfis": perfis})


@_somente_super_admin
def perfil_novo(request):
    permissoes_selecionadas = set()
    if request.method == "POST":
        form = PerfilForm(request.POST)
        # Lido já aqui (antes do is_valid) para que, se o formulário for
        # inválido — incluindo o erro de "nome já existe" abaixo, que só
        # aparece depois do is_valid — o template reabra com as mesmas
        # marcações que a pessoa tinha feito, em vez de resetar tudo.
        permissoes_selecionadas = set(request.POST.getlist("permissoes"))
        if form.is_valid():
            nome = form.cleaned_data["nome"]
            if Group.objects.filter(name=nome).exists():
                form.add_error("nome", "Já existe um perfil com esse nome.")
            else:
                grupo = Group.objects.create(name=nome)
                grupo.permissions.set(Permission.objects.filter(codename__in=permissoes_selecionadas))
                _registrar_log(request, request.user, f"Perfil '{nome}' criado")
                messages.success(request, f"Perfil '{nome}' criado com sucesso.")
                return redirect("perfil_lista")
    else:
        form = PerfilForm()
    return render(
        request,
        "accounts/perfil_form.html",
        {
            "form": form,
            "titulo": "Novo perfil",
            "departamentos": DEPARTAMENTOS_PERMISSOES,
            "permissoes_selecionadas": permissoes_selecionadas,
        },
    )


@_somente_super_admin
def perfil_editar(request, pk):
    grupo = get_object_or_404(Group, pk=pk)
    if request.method == "POST":
        form = PerfilForm(request.POST)
        # Lido já aqui (antes do is_valid) pelo mesmo motivo do perfil_novo:
        # preservar as marcações da pessoa em qualquer erro, em vez de
        # voltar ao que já estava salvo no banco.
        permissoes_selecionadas = set(request.POST.getlist("permissoes"))
        if form.is_valid():
            novo_nome = form.cleaned_data["nome"]
            if Group.objects.exclude(pk=grupo.pk).filter(name=novo_nome).exists():
                form.add_error("nome", "Já existe outro perfil com esse nome.")
            else:
                grupo.name = novo_nome
                grupo.save(update_fields=["name"])
                grupo.permissions.set(Permission.objects.filter(codename__in=permissoes_selecionadas))
                _registrar_log(request, request.user, f"Perfil '{novo_nome}' editado")
                messages.success(request, "Perfil atualizado com sucesso.")
                return redirect("perfil_lista")
    else:
        form = PerfilForm(initial={"nome": grupo.name})
        permissoes_selecionadas = set(grupo.permissions.values_list("codename", flat=True))
    return render(
        request,
        "accounts/perfil_form.html",
        {
            "form": form,
            "titulo": f"Editar perfil: {grupo.name}",
            "departamentos": DEPARTAMENTOS_PERMISSOES,
            "permissoes_selecionadas": permissoes_selecionadas,
        },
    )


@_somente_super_admin
def perfil_excluir(request, pk):
    grupo = get_object_or_404(Group, pk=pk)
    qtd_usuarios = grupo.user_set.count()
    if request.method == "POST":
        nome = grupo.name
        grupo.delete()
        _registrar_log(request, request.user, f"Perfil '{nome}' excluído")
        messages.success(request, f"Perfil '{nome}' excluído.")
        return redirect("perfil_lista")
    return render(
        request,
        "cadastros/confirmar_exclusao.html",
        {
            "objeto": grupo,
            "voltar_url": reverse("perfil_lista"),
            "aviso_extra": (
                f"{qtd_usuarios} usuário(s) usam esse perfil e vão perder essas permissões."
                if qtd_usuarios
                else "Nenhum usuário está usando esse perfil no momento."
            ),
        },
    )


@permission_required("accounts.log_auditoria_ver", raise_exception=True)
def log_auditoria_lista(request):
    """Consulta ao histórico de ações sensíveis (cancelamento de agendamento, estorno,
    pagamento, login, gestão de usuários/perfis etc. — ver LogAuditoria.registrar e
    accounts.views._registrar_log, os dois pontos que gravam aqui)."""
    logs = LogAuditoria.objects.select_related("usuario").all()
    busca = request.GET.get("busca", "").strip()
    if busca:
        logs = logs.filter(Q(acao__icontains=busca) | Q(usuario__username__icontains=busca))
    pagina = Paginator(logs, 50).get_page(request.GET.get("pagina"))
    return render(request, "accounts/log_auditoria_lista.html", {"pagina": pagina, "busca": busca})
