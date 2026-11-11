"""
Backfill de dados: popula AgendamentoItem a partir dos campos legados
Agendamento.procedimento/retorno_de (ver comentários 'LEGADO' em agenda/models.py).

Duas passadas, nesta ordem (a 2ª depende dos itens que a 1ª acabou de criar):
1. Um AgendamentoItem por Agendamento com procedimento_id preenchido (quantidade=1).
   Convênio: copia valor_convenio direto (nunca recalcula pela tabela de preços atual,
   pra não divergir de um valor já faturado/recebido). Particular: valor_cobrado é
   preenchido com a soma dos PagamentoParticular não estornados deste agendamento —
   um backfill EXATO, não aproximado: como nesta passada existe sempre exatamente 1
   item por agendamento, "o que já foi cobrado no agendamento inteiro" É "o que foi
   cobrado deste item" (mesmo valor que Agendamento.valor_cobrado, property, já
   representa em todo o resto do sistema). None quando o agendamento nunca teve
   pagamento registrado (ainda não chegou em Realizado).
2. Religa AgendamentoItem.retorno_de item-a-item, a partir de Agendamento.retorno_de —
   importante mesmo para agendamentos ainda abertos (não Realizado/Cancelado/Faltou):
   um retorno já vinculado precisa continuar "ocupando a vaga" depois do corte, senão
   AgendamentoItem.buscar_item_para_retorno poderia liberar a mesma consulta de novo.
"""
from django.db import migrations, models


def preencher_itens(apps, schema_editor):
    Agendamento = apps.get_model("agenda", "Agendamento")
    AgendamentoItem = apps.get_model("agenda", "AgendamentoItem")
    PagamentoParticular = apps.get_model("agenda", "PagamentoParticular")

    novos_itens = []
    for agendamento in Agendamento.objects.filter(procedimento_id__isnull=False).only(
        "id", "procedimento_id", "valor_convenio", "forma_atendimento"
    ):
        valor_cobrado = None
        if agendamento.forma_atendimento == "PARTICULAR":
            valor_cobrado = PagamentoParticular.objects.filter(
                agendamento_id=agendamento.id, estornado=False
            ).aggregate(total=models.Sum("valor"))["total"]
        novos_itens.append(
            AgendamentoItem(
                agendamento_id=agendamento.id,
                procedimento_id=agendamento.procedimento_id,
                quantidade=1,
                valor_convenio=agendamento.valor_convenio if agendamento.forma_atendimento == "CONVENIO" else None,
                valor_cobrado=valor_cobrado,
            )
        )
    AgendamentoItem.objects.bulk_create(novos_itens)


def religar_retorno_de(apps, schema_editor):
    Agendamento = apps.get_model("agenda", "Agendamento")
    AgendamentoItem = apps.get_model("agenda", "AgendamentoItem")

    # 1 item por agendamento é verdade neste ponto exato da migração (acabaram de ser
    # criados pela passada 1, antes de qualquer edição via o formset novo).
    item_id_por_agendamento = dict(AgendamentoItem.objects.values_list("agendamento_id", "id"))

    for agendamento in Agendamento.objects.filter(retorno_de_id__isnull=False).only("id", "retorno_de_id"):
        item_atual_id = item_id_por_agendamento.get(agendamento.id)
        item_origem_id = item_id_por_agendamento.get(agendamento.retorno_de_id)
        if item_atual_id and item_origem_id:
            AgendamentoItem.objects.filter(pk=item_atual_id).update(retorno_de_id=item_origem_id)


def reverter(apps, schema_editor):
    AgendamentoItem = apps.get_model("agenda", "AgendamentoItem")
    AgendamentoItem.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("agenda", "0012_agendamentoitem"),
    ]

    operations = [
        migrations.RunPython(preencher_itens, reverter),
        migrations.RunPython(religar_retorno_de, migrations.RunPython.noop),
    ]
