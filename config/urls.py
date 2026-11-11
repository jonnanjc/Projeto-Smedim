from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("cadastros/", include("cadastros.urls", namespace="cadastros")),
    path("fila/", include("fila.urls", namespace="fila")),
    path("agenda/", include("agenda.urls", namespace="agenda")),
    path("mensagens/", include("mensagens.urls", namespace="mensagens")),
    path("prontuario/", include("prontuario.urls", namespace="prontuario")),
    path("financeiro/", include("financeiro.urls", namespace="financeiro")),
    path("documentos/", include("documentos.urls", namespace="documentos")),
    # Fotos de perfil e outros uploads (MEDIA_ROOT). Servido pelo próprio Django,
    # de propósito — diferente de STATIC_URL, não tem alias dedicado no Nginx de
    # produção (ver LEIA-ME-INSTALACAO-CLINICA.md Parte 11), então precisa
    # continuar respondendo mesmo com DEBUG=False (por isso não está atrás de um
    # "if settings.DEBUG", como o Django costuma recomendar para MEDIA em dev).
    path(f"{settings.MEDIA_URL.lstrip('/')}<path:path>", serve, {"document_root": settings.MEDIA_ROOT}),
]
