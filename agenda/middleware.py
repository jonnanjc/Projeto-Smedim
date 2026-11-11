from django.conf import settings

from .models import atualizar_status_do_dia


class AtualizarStatusDoDiaMiddleware:
    """Roda atualizar_status_do_dia() antes de cada requisição — na prática só faz algo no
    primeiro acesso de cada dia (por processo); nas demais é só uma comparação de data.
    Middleware (e não context processor) pra valer também no painel, totem e telas sem login.
    Desligável por settings.AGENDA_STATUS_AUTOMATICO (desligado nos testes)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.AGENDA_STATUS_AUTOMATICO:
            atualizar_status_do_dia()
        return self.get_response(request)
