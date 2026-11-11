from django.urls import path

from . import views

app_name = "cadastros"

urlpatterns = [
    # Pacientes
    path("pacientes/", views.paciente_lista, name="paciente_lista"),
    path("pacientes/novo/", views.paciente_novo, name="paciente_novo"),
    path("pacientes/<int:pk>/editar/", views.paciente_editar, name="paciente_editar"),
    path("pacientes/<int:pk>/excluir/", views.paciente_excluir, name="paciente_excluir"),
    path("pacientes/duplicados/", views.paciente_duplicados, name="paciente_duplicados"),

    # Profissionais
    path("profissionais/", views.profissional_lista, name="profissional_lista"),
    path("profissionais/novo/", views.profissional_novo, name="profissional_novo"),
    path("profissionais/<int:pk>/editar/", views.profissional_editar, name="profissional_editar"),
    path("profissionais/<int:pk>/desativar/", views.profissional_desativar, name="profissional_desativar"),
    path("profissionais/<int:pk>/horarios/", views.profissional_horarios, name="profissional_horarios"),
    path("profissionais/<int:pk>/excecoes/", views.profissional_excecoes, name="profissional_excecoes"),
    path(
        "profissionais/<int:pk>/excecoes/<str:data>/remover/",
        views.profissional_excecao_remover,
        name="profissional_excecao_remover",
    ),
    path(
        "profissionais/<int:pk>/excecoes/<str:data>/remanejar/",
        views.profissional_excecao_remanejar,
        name="profissional_excecao_remanejar",
    ),

    # Procedimentos
    path("procedimentos/", views.procedimento_lista, name="procedimento_lista"),
    path("procedimentos/novo/", views.procedimento_novo, name="procedimento_novo"),
    path("procedimentos/<int:pk>/editar/", views.procedimento_editar, name="procedimento_editar"),
    path("procedimentos/<int:pk>/excluir/", views.procedimento_excluir, name="procedimento_excluir"),
    path("procedimentos/planilha/", views.baixar_planilha_procedimentos, name="baixar_planilha_procedimentos"),
    path("procedimentos/importar/", views.procedimento_importar, name="procedimento_importar"),

    # Pacotes / combos particulares
    path("pacotes/", views.pacote_lista, name="pacote_lista"),
    path("pacotes/novo/", views.pacote_novo, name="pacote_novo"),
    path("pacotes/<int:pk>/editar/", views.pacote_editar, name="pacote_editar"),
    path("pacotes/<int:pk>/excluir/", views.pacote_excluir, name="pacote_excluir"),

    # Convênios
    path("convenios/", views.convenio_lista, name="convenio_lista"),
    path("convenios/novo/", views.convenio_novo, name="convenio_novo"),
    path("convenios/<int:pk>/", views.convenio_detalhe, name="convenio_detalhe"),
    path("convenios/<int:pk>/editar/", views.convenio_editar, name="convenio_editar"),
    path("convenios/<int:pk>/excluir/", views.convenio_excluir, name="convenio_excluir"),

    # Tabela de preços do convênio
    path("convenios/<int:pk>/precos/novo/", views.preco_novo, name="preco_novo"),
    path("precos/<int:pk>/editar/", views.preco_editar, name="preco_editar"),
    path("precos/<int:pk>/excluir/", views.preco_excluir, name="preco_excluir"),

    # Importação/exportação de planilha de preços
    path("convenios/<int:pk>/precos/importar/", views.convenio_importar_precos, name="convenio_importar_precos"),
    path("convenios/<int:pk>/precos/exportar/", views.convenio_exportar_precos, name="convenio_exportar_precos"),
    path("precos/modelo/", views.baixar_modelo_precos, name="baixar_modelo_precos"),

    # Salas
    path("salas/", views.sala_lista, name="sala_lista"),
    path("salas/novo/", views.sala_novo, name="sala_novo"),
    path("salas/<int:pk>/editar/", views.sala_editar, name="sala_editar"),
    path("salas/<int:pk>/excluir/", views.sala_excluir, name="sala_excluir"),

    # Guichês
    path("guiches/", views.guiche_lista, name="guiche_lista"),
    path("guiches/novo/", views.guiche_novo, name="guiche_novo"),
    path("guiches/<int:pk>/editar/", views.guiche_editar, name="guiche_editar"),
    path("guiches/<int:pk>/excluir/", views.guiche_excluir, name="guiche_excluir"),

    # Limites diários de procedimento (Central de Atendimento)
    path("limites-diarios/", views.limite_diario_lista, name="limite_diario_lista"),
    path("limites-diarios/novo/", views.limite_diario_novo, name="limite_diario_novo"),
    path("limites-diarios/<int:pk>/editar/", views.limite_diario_editar, name="limite_diario_editar"),
    path("limites-diarios/<int:pk>/excluir/", views.limite_diario_excluir, name="limite_diario_excluir"),

    # API auxiliar (JavaScript) — procedimentos vinculados a um profissional
    path(
        "api/profissionais/<int:pk>/procedimentos/",
        views.procedimentos_do_profissional,
        name="procedimentos_do_profissional",
    ),
]
