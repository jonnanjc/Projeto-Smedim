from django.urls import path

from . import views

app_name = "fila"

urlpatterns = [
    # "totem_inicio" continua sendo a URL raiz do totem (é o que fica configurado
    # como tela ociosa no hardware do totem) — só a tela por trás dela mudou, de
    # "Normal/Prioritário" pra "escolha de especialidade" (ver totem_especialidade).
    path("totem/", views.totem_especialidade, name="totem_inicio"),
    path("totem/profissional/", views.totem_profissional, name="totem_profissional"),
    path("totem/tipo/", views.totem_tipo, name="totem_tipo"),
    path("totem/prioridade/", views.totem_prioridade, name="totem_prioridade"),
    path("totem/emitir/", views.totem_emitir, name="totem_emitir"),
    path("totem/senha/<int:pk>/", views.totem_senha_ver, name="totem_senha_ver"),
    path("painel/", views.painel_chamada, name="painel_chamada"),
    path("fila-de-espera/", views.fila_espera, name="fila_espera"),
    path("painel/chamar-manual/", views.chamar_manual, name="chamar_manual"),
    path("painel/espera/",views.painel_espera, name="painel_espera"),
    path("painel/voz/<str:tipo>/<str:chave>/", views.painel_voz, name="painel_voz"),
]
