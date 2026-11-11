from django.urls import path

from . import views

app_name = "prontuario"

urlpatterns = [
    path("", views.prontuario_lista, name="prontuario_lista"),
    path("paciente/<int:paciente_pk>/", views.prontuario_paciente, name="prontuario_paciente"),
    path("paciente/<int:paciente_pk>/nova/", views.entrada_nova_paciente, name="entrada_nova_paciente"),
    path("agendamento/<int:agendamento_pk>/nova/", views.entrada_nova, name="entrada_nova"),
    path("entrada/<int:pk>/editar/", views.entrada_editar, name="entrada_editar"),
    path("entrada/<int:pk>/apagar/", views.entrada_excluir, name="entrada_excluir"),
]
