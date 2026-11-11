from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import permission_required
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .models import Mensagem, apagar_mensagens_antigas

Usuario = get_user_model()

# Considera "online" quem navegou no sistema nos últimos N minutos (ver
# accounts.middleware.AtualizarUltimoAcessoMiddleware, que grava
# Usuario.ultimo_acesso a cada requisição).
MINUTOS_PARA_CONSIDERAR_ONLINE = 3


def _esta_online(usuario):
    if not usuario.ultimo_acesso:
        return False
    diferenca = timezone.now() - usuario.ultimo_acesso
    return diferenca.total_seconds() <= MINUTOS_PARA_CONSIDERAR_ONLINE * 60


@permission_required("accounts.mensagem_usar", raise_exception=True)
def lista_conversas(request):
    """Lista todos os outros usuários ativos do sistema, para escolher com quem conversar."""
    usuarios = (
        Usuario.objects.filter(is_active=True)
        .exclude(pk=request.user.pk)
        .order_by("first_name", "last_name", "username")
    )

    # Conta não lidas por remetente (uma query só, sem N+1 por usuário da lista).
    contagem = (
        Mensagem.objects.filter(destinatario=request.user, lida=False)
        .values("remetente_id")
        .annotate(total=Count("id"))
    )
    nao_lidas_por_remetente = {linha["remetente_id"]: linha["total"] for linha in contagem}

    ultima_mensagem_por_usuario = {}
    for mensagem in Mensagem.objects.filter(remetente=request.user) | Mensagem.objects.filter(
        destinatario=request.user
    ):
        outro_id = mensagem.destinatario_id if mensagem.remetente_id == request.user.pk else mensagem.remetente_id
        anterior = ultima_mensagem_por_usuario.get(outro_id)
        if anterior is None or mensagem.criado_em > anterior.criado_em:
            ultima_mensagem_por_usuario[outro_id] = mensagem

    contatos = []
    for usuario in usuarios:
        contatos.append(
            {
                "usuario": usuario,
                "online": _esta_online(usuario),
                "nao_lidas": nao_lidas_por_remetente.get(usuario.pk, 0),
                "ultima_mensagem": ultima_mensagem_por_usuario.get(usuario.pk),
            }
        )

    # Quem tem mensagem não lida aparece primeiro, depois quem tem conversa mais recente.
    contatos.sort(
        key=lambda item: (
            item["nao_lidas"] == 0,
            -(item["ultima_mensagem"].criado_em.timestamp() if item["ultima_mensagem"] else 0),
        )
    )

    # Separa em duas listas para a tela não ficar cheia: offline fica recolhido por padrão.
    contatos_online = [item for item in contatos if item["online"]]
    contatos_offline = [item for item in contatos if not item["online"]]

    return render(
        request,
        "mensagens/lista_conversas.html",
        {
            "contatos_online": contatos_online,
            "contatos_offline": contatos_offline,
            # Se alguém offline deixou mensagem não lida, a lista de offline já abre expandida.
            "offline_tem_nao_lidas": any(item["nao_lidas"] for item in contatos_offline),
        },
    )


@permission_required("accounts.mensagem_usar", raise_exception=True)
def conversa(request, usuario_id):
    """Tela de uma conversa 1-a-1. A atualização de mensagens novas é feita por polling via api_mensagens_novas."""
    outro_usuario = get_object_or_404(Usuario, pk=usuario_id, is_active=True)

    if outro_usuario.pk == request.user.pk:
        return redirect(reverse("mensagens:lista_conversas"))

    if request.method == "POST":
        texto = (request.POST.get("texto") or "").strip()
        if texto:
            Mensagem.objects.create(remetente=request.user, destinatario=outro_usuario, texto=texto)
        return redirect(reverse("mensagens:conversa", args=[outro_usuario.pk]))

    mensagens_da_conversa = Mensagem.objects.filter(
        remetente__in=[request.user, outro_usuario], destinatario__in=[request.user, outro_usuario]
    ).order_by("criado_em")

    # Marca como lidas as que o outro usuário me enviou, já que estou abrindo a conversa agora.
    Mensagem.objects.filter(remetente=outro_usuario, destinatario=request.user, lida=False).update(lida=True)

    ultima_id = mensagens_da_conversa.last().pk if mensagens_da_conversa.exists() else 0

    return render(
        request,
        "mensagens/conversa.html",
        {
            "outro_usuario": outro_usuario,
            "outro_online": _esta_online(outro_usuario),
            "mensagens_da_conversa": mensagens_da_conversa,
            "ultima_id": ultima_id,
        },
    )


@permission_required("accounts.mensagem_usar", raise_exception=True)
def api_mensagens_novas(request, usuario_id):
    """Endpoint de polling: devolve mensagens novas (id maior que 'depois_de') dessa conversa, em JSON."""
    outro_usuario = get_object_or_404(Usuario, pk=usuario_id)
    depois_de = request.GET.get("depois_de") or 0
    try:
        depois_de = int(depois_de)
    except ValueError:
        depois_de = 0

    novas = Mensagem.objects.filter(
        remetente__in=[request.user, outro_usuario],
        destinatario__in=[request.user, outro_usuario],
        pk__gt=depois_de,
    ).order_by("criado_em")

    # Quem me enviou e ainda não tinha sido marcado como lido, marca agora (a tela já está aberta).
    novas.filter(remetente=outro_usuario, destinatario=request.user, lida=False).update(lida=True)

    dados = [
        {
            "id": mensagem.pk,
            "minha": mensagem.remetente_id == request.user.pk,
            "texto": mensagem.texto,
            "hora": timezone.localtime(mensagem.criado_em).strftime("%H:%M"),
        }
        for mensagem in novas
    ]

    return JsonResponse(
        {
            "mensagens": dados,
            "ultima_id": dados[-1]["id"] if dados else depois_de,
            "outro_online": _esta_online(outro_usuario),
        }
    )


@permission_required("accounts.mensagem_usar", raise_exception=True)
def api_contador_nao_lidas(request):
    """Endpoint de polling leve, só para atualizar o número do badge no menu lateral sem recarregar a página."""
    apagar_mensagens_antigas()
    total = Mensagem.objects.filter(destinatario=request.user, lida=False).count()
    return JsonResponse({"total": total})
