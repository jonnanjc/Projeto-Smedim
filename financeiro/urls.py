from django.urls import path

from . import views

app_name = "financeiro"

urlpatterns = [
    path("", views.painel_financeiro, name="painel"),
    path("fila-pagamento/", views.fila_pagamento_lista, name="fila_pagamento_lista"),
    path("contas-receber/", views.contas_receber_lista, name="contas_receber_lista"),
    path("contas-receber/<int:pk>/lancar/", views.contas_receber_lancar_valor, name="contas_receber_lancar"),
    path(
        "contas-receber/<int:pk>/marcar-recebido/",
        views.contas_receber_marcar_recebido,
        name="contas_receber_marcar_recebido",
    ),
    path("contas-receber/<int:pk>/estornar/", views.contas_receber_estornar, name="contas_receber_estornar"),
    path("pagamentos/<int:pk>/estornar/", views.pagamento_estornar, name="pagamento_estornar"),
    path("pagamentos/corrigir/", views.pagamento_correcao_lista, name="pagamento_correcao_lista"),
    path("pagamentos/corrigir/<int:agendamento_pk>/", views.pagamento_corrigir, name="pagamento_corrigir"),
    path("fechamento/diario/", views.fechamento_diario, name="fechamento_diario"),
    path("fechamento/mensal/", views.fechamento_mensal, name="fechamento_mensal"),
    path("meu-fechamento/diario/", views.meu_fechamento_diario, name="meu_fechamento_diario"),
    path("meu-fechamento/mensal/", views.meu_fechamento_mensal, name="meu_fechamento_mensal"),
    path("relatorio-detalhado/", views.relatorio_detalhado, name="relatorio_detalhado"),
    path("repasse-convenio/", views.repasse_convenio_lista, name="repasse_convenio_lista"),
    path(
        "repasse-convenio/<int:profissional_id>/marcar-pago/",
        views.repasse_convenio_marcar_pago,
        name="repasse_convenio_marcar_pago",
    ),
    path("producao-convenio/", views.producao_por_convenio, name="producao_por_convenio"),
    path("lotes/", views.lote_lista, name="lote_lista"),
    path("lotes/novo/", views.lote_novo, name="lote_novo"),
    path("lotes/<int:pk>/", views.lote_detalhe, name="lote_detalhe"),
    path("lotes/<int:pk>/dar-baixa/", views.lote_dar_baixa, name="lote_dar_baixa"),
    path("lotes/<int:pk>/editar/", views.lote_editar, name="lote_editar"),
    path("lotes/<int:pk>/excluir/", views.lote_excluir, name="lote_excluir"),
    path("lotes/<int:lote_pk>/retorno/<int:agendamento_pk>/", views.retorno_lancar, name="retorno_lancar"),
    path("comparativo-lote/", views.faturamento_comparativo, name="faturamento_comparativo"),
    path("repasses-pagos/", views.repasse_pagamento_lista, name="repasse_pagamento_lista"),
    path(
        "repasses-pagos/novo/<int:profissional_id>/",
        views.repasse_pagamento_novo,
        name="repasse_pagamento_novo",
    ),
    path("repasses-pagos/<int:pk>/excluir/", views.repasse_pagamento_excluir, name="repasse_pagamento_excluir"),
    path("movimentacoes/", views.movimentacao_avulsa_lista, name="movimentacao_avulsa_lista"),
    path("movimentacoes/nova/", views.movimentacao_avulsa_nova, name="movimentacao_avulsa_nova"),
    path("movimentacoes/<int:pk>/editar/", views.movimentacao_avulsa_editar, name="movimentacao_avulsa_editar"),
    path("movimentacoes/<int:pk>/excluir/", views.movimentacao_avulsa_excluir, name="movimentacao_avulsa_excluir"),
]
