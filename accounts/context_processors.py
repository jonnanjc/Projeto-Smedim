from django.urls import reverse

from .models import ConfiguracaoTema
from .permissoes import PAGINAS_FAVORITAVEIS


def tema_do_mes(request):
    """Disponibiliza a campanha de conscientização ativa (se houver) para qualquer template."""
    config = ConfiguracaoTema.carregar()
    return {"tema_mes": config.dados_campanha}


def favoritos_resolvidos(request):
    """
    Traduz Usuario.paginas_favoritas (lista de ids) em {id, label, url, ativo} prontos
    pro menu lateral — resolvendo a URL e checando a permissão a cada request, nunca
    confiando no que foi salvo no banco (perfil pode ter perdido a permissão depois
    de favoritar, ou a página pode ter sido removida de PAGINAS_FAVORITAVEIS).
    """
    usuario = getattr(request, "user", None)
    if usuario is None or not usuario.is_authenticated:
        return {}

    salvos = set(usuario.paginas_favoritas or [])
    resolvidos = []
    for pagina_id, info in PAGINAS_FAVORITAVEIS.items():
        if pagina_id not in salvos:
            continue
        if not usuario.has_perm(info["permissao"]):
            continue
        resolvidos.append({
            "id": pagina_id,
            "label": info["label"],
            "url": reverse(info["url_name"]),
            "ativo": request.resolver_match and request.resolver_match.view_name == info["url_name"],
        })
    return {"favoritos_resolvidos": resolvidos}
