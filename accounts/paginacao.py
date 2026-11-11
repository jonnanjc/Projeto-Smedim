from django.core.paginator import Paginator

POR_PAGINA = 50


def paginar(request, itens, por_pagina=POR_PAGINA):
    """Página atual de `itens` (queryset ou lista). `pagina.qs_base` traz os demais
    parâmetros da URL (busca, filtros) para os links do rodapé não os perderem."""
    pagina = Paginator(itens, por_pagina).get_page(request.GET.get("pagina"))
    parametros = request.GET.copy()
    parametros.pop("pagina", None)
    pagina.qs_base = parametros.urlencode()
    return pagina
