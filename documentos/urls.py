from django.urls import path

from . import views

app_name = "documentos"

urlpatterns = [
    path("atestado/agendamento/<int:agendamento_pk>/novo/", views.atestado_novo, name="atestado_novo"),
    path("atestado/paciente/<int:paciente_pk>/novo/", views.atestado_novo, name="atestado_novo_paciente"),
    path("receituario/agendamento/<int:agendamento_pk>/novo/", views.receituario_novo, name="receituario_novo"),
    path("receituario/paciente/<int:paciente_pk>/novo/", views.receituario_novo, name="receituario_novo_paciente"),
    path("<int:pk>/imprimir/", views.documento_imprimir, name="documento_imprimir"),
]
