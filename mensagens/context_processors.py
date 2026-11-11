from .models import Mensagem, apagar_mensagens_antigas


def mensagens_nao_lidas(request):
    """Disponibiliza o total de mensagens não lidas do usuário logado para o menu lateral (badge)."""
    usuario = getattr(request, "user", None)
    if usuario is None or not usuario.is_authenticated:
        return {}
    # Roda no máximo uma vez por dia (por processo): apaga o histórico de ontem para trás.
    apagar_mensagens_antigas()
    total = Mensagem.objects.filter(destinatario=usuario, lida=False).count()
    return {"mensagens_nao_lidas_total": total}
