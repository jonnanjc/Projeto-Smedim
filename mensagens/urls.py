from django.urls import path

from . import views

app_name = "mensagens"

urlpatterns = [
    path("", views.lista_conversas, name="lista_conversas"),
    path("<int:usuario_id>/", views.conversa, name="conversa"),
    path("<int:usuario_id>/novas/", views.api_mensagens_novas, name="api_mensagens_novas"),
    path("api/nao-lidas/", views.api_contador_nao_lidas, name="api_contador_nao_lidas"),
]
