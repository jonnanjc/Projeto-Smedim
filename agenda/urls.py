from django.urls import path

from . import views

app_name = "agenda"

urlpatterns = [
    path("dia/", views.agenda_dia, name="agenda_dia"),
    path(
        "api/profissionais/<int:pk>/procedimentos/",
        views.procedimentos_do_profissional,
        name="procedimentos_do_profissional",
    ),
    path("minha/", views.agenda_propria, name="agenda_propria"),
    path("fila-profissionais/", views.fila_profissionais, name="fila_profissionais"),
    path("meu-desempenho/", views.meu_desempenho, name="meu_desempenho"),
    path("atendidos/", views.listagem_atendidos, name="listagem_atendidos"),
    path("api/pacientes/", views.buscar_pacientes, name="buscar_pacientes"),
    path("guias-pendentes/", views.guias_pendentes, name="guias_pendentes"),
    path("guias/<int:item_pk>/<str:acao>/", views.guia_autorizar, name="guia_autorizar"),
    path("central/", views.central_atendimento, name="central_atendimento"),
    path("novo/", views.agendamento_novo, name="agendamento_novo"),
    path("<int:pk>/editar/", views.agendamento_editar, name="agendamento_editar"),
    path("<int:pk>/cancelar/", views.agendamento_cancelar, name="agendamento_cancelar"),
    path("<int:pk>/cancelar-autorizado/", views.agendamento_cancelar_autorizado, name="agendamento_cancelar_autorizado"),
    path("<int:pk>/excluir/", views.agendamento_excluir, name="agendamento_excluir"),
    path("<int:pk>/status/<str:novo_status>/", views.agendamento_mudar_status, name="agendamento_mudar_status"),
    path("<int:pk>/autorizar/", views.agendamento_autorizar, name="agendamento_autorizar"),
    path("<int:pk>/prioridade/", views.agendamento_prioridade, name="agendamento_prioridade"),
    path("autorizacoes/", views.autorizacoes, name="autorizacoes"),
    path("guias-convenio/", views.guias_convenio, name="guias_convenio"),
    path("guias-convenio/<int:pk>/editar/", views.guia_convenio_editar, name="guia_convenio_editar"),
    path(
        "<int:pk>/confirmar-pagamento/",
        views.agendamento_confirmar_pagamento,
        name="agendamento_confirmar_pagamento",
    ),
    path("<int:pk>/chamar/", views.agendamento_chamar, name="agendamento_chamar"),
    path("chamar-recepcao/", views.agendamento_chamar_recepcao, name="agendamento_chamar_recepcao"),
    path("ocupacao-salas/", views.ocupacao_salas, name="ocupacao_salas"),
    path("repasses/", views.relatorio_repasses_semanal, name="relatorio_repasses_semanal"),
    path("pacotes/", views.pacotes_vendidos, name="pacotes_vendidos"),
    path("pacotes/cadastrar/", views.pacote_cadastrar_manual, name="pacote_cadastrar_manual"),
    path("pacotes/<int:pk>/cancelar/", views.pacote_cancelar_manual, name="pacote_cancelar_manual"),
]
