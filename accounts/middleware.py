from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers


class ForcarTrocaSenhaMiddleware:
    """
    Se o usuário logado estiver marcado com "exigir_troca_senha" (por exemplo,
    porque o administrador acabou de definir uma senha provisória), redireciona
    qualquer navegação para a tela de troca de senha, até que ele a troque.
    """

    CAMINHOS_LIVRES = ("/trocar-senha/", "/logout/", "/admin/logout/", "/static/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, "user", None)
        if (
            usuario is not None
            and usuario.is_authenticated
            and getattr(usuario, "exigir_troca_senha", False)
            and not request.path.startswith(self.CAMINHOS_LIVRES)
        ):
            return redirect(reverse("trocar_senha"))
        return self.get_response(request)


class AtualizarUltimoAcessoMiddleware:
    """
    Atualiza Usuario.ultimo_acesso a cada navegação do usuário logado, usado
    só para saber quem está "online" no chat interno (mensagens).

    Para não gerar um UPDATE no banco a cada clique, só grava de novo quando
    já se passou pelo menos MINUTOS_MINIMOS_ENTRE_GRAVACOES minutos desde a
    última gravação — é só para status "online/offline", não precisa de
    precisão de segundos.
    """

    MINUTOS_MINIMOS_ENTRE_GRAVACOES = 1

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, "user", None)
        if usuario is not None and usuario.is_authenticated:
            agora = timezone.now()
            precisa_gravar = (
                usuario.ultimo_acesso is None
                or (agora - usuario.ultimo_acesso).total_seconds() >= self.MINUTOS_MINIMOS_ENTRE_GRAVACOES * 60
            )
            if precisa_gravar:
                usuario.ultimo_acesso = agora
                usuario.save(update_fields=["ultimo_acesso"])
        return self.get_response(request)


class DesabilitarCacheMiddleware:
    """
    Adiciona os cabeçalhos "nunca guardar em cache" (os mesmos do decorator
    @never_cache do Django: Cache-Control: no-store etc.) em toda resposta
    do sistema, não só nas views da agenda que já tinham isso manualmente.

    Motivo: a clínica tem dados mudando o tempo todo (agenda, prontuário,
    financeiro...) em praticamente toda tela. Sem isso, o botão "voltar" do
    navegador pode reexibir uma versão antiga da página guardada em cache,
    sem pedir a página de novo ao servidor — já causou pelo menos um caso de
    "cancelei o horário mas a tela continua mostrando ocupado" (20/07/2026,
    ver comentário em agenda.views.central_atendimento).

    Não mexe em /static/ (CSS/JS/imagens podem continuar cacheados
    normalmente) nem em respostas que já definiram seu próprio Cache-Control
    (ex.: as views da agenda com @never_cache, ou o próprio admin do
    Django, que já protege as views dele).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith("/static/") and not response.has_header("Cache-Control"):
            add_never_cache_headers(response)
        return response
