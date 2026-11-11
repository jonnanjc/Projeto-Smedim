"""
Dinheiro passa a ter repasse registrado (pedido da clínica, 26/10/2026): o dinheiro do particular
fica no caixa e a parte do profissional entra no repasse pendente, como cartão/Pix, até o
financeiro registrar o pagamento. Antes, dinheiro contava como entregue na hora, sem registro.

As linhas em dinheiro REGISTRADAS antes de 26/10/2026 seguiram a regra antiga (o profissional
já levou na hora) — são marcadas aqui como repasse pago, com a data do próprio pagamento, pra
não aparecerem de repente como saldo a pagar. Mesma ideia de agenda.0020.
"""
from datetime import date
from decimal import Decimal

from django.db import migrations

DESDE = date(2026, 10, 26)  # = PagamentoParticular.REPASSE_DINHEIRO_REGISTRADO_DESDE


def _repasse(pagamento):
    profissional = pagamento.agendamento.profissional
    if profissional.eh_proprietario:
        return pagamento.valor
    if pagamento.valor_repasse_fixo is not None:
        return pagamento.valor_repasse_fixo
    percentual = profissional.percentual_desconto
    return (pagamento.valor * (Decimal("100") - percentual) / Decimal("100")).quantize(Decimal("0.01"))


def marcar_antigos(apps, schema_editor):
    PagamentoParticular = apps.get_model("agenda", "PagamentoParticular")
    antigos = PagamentoParticular.objects.filter(
        forma_pagamento="DINHEIRO",
        estornado=False,
        repasse_pago=False,
        criado_em__date__lt=DESDE,
    ).select_related("agendamento__profissional")
    for pagamento in antigos:
        pagamento.repasse_pago = True
        pagamento.repasse_pago_em = pagamento.criado_em
        pagamento.repasse_valor_pago = _repasse(pagamento)
        pagamento.save(update_fields=["repasse_pago", "repasse_pago_em", "repasse_valor_pago"])


class Migration(migrations.Migration):

    dependencies = [
        ("agenda", "0032_agendamento_encaminhado_pagamento_em"),
    ]

    operations = [
        migrations.RunPython(marcar_antigos, migrations.RunPython.noop),
    ]
