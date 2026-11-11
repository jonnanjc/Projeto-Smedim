"""
Prepara o pagamento parcial de repasse (financeiro.models.RepassePagamento, 2026-09-28):

1. Profissionais com "sempre recebe no dia" (campo removido logo depois, em
   cadastros.0023) nunca tiveram cartão/Pix listado como pendente — a clínica já
   acertava no dia. Sem o campo, essas linhas passariam a aparecer como repasse
   pendente; por isso são marcadas como pagas aqui, com a data do próprio pagamento.
2. Linhas já marcadas como repasse pago recebem repasse_valor_pago = repasse líquido
   (valor menos a retenção do profissional), pra ficarem coerentes com o novo campo.
"""
from decimal import Decimal

from django.db import migrations


def _liquido(pagamento):
    profissional = pagamento.agendamento.profissional
    percentual = Decimal("0") if profissional.eh_proprietario else profissional.percentual_desconto
    return (pagamento.valor * (Decimal("100") - percentual) / Decimal("100")).quantize(Decimal("0.01"))


def preencher(apps, schema_editor):
    PagamentoParticular = apps.get_model("agenda", "PagamentoParticular")

    acertados_no_dia = (
        PagamentoParticular.objects.filter(
            agendamento__profissional__sempre_recebe_no_dia=True,
            repasse_pago=False,
            estornado=False,
        )
        .exclude(forma_pagamento__in=["DINHEIRO", "CORTESIA"])
    )
    for pagamento in acertados_no_dia:
        pagamento.repasse_pago = True
        pagamento.repasse_pago_em = pagamento.criado_em
        pagamento.save(update_fields=["repasse_pago", "repasse_pago_em"])

    for pagamento in PagamentoParticular.objects.filter(repasse_pago=True).select_related("agendamento__profissional"):
        pagamento.repasse_valor_pago = _liquido(pagamento)
        pagamento.save(update_fields=["repasse_valor_pago"])


class Migration(migrations.Migration):

    dependencies = [
        ("agenda", "0019_repasse_parcial"),
        ("cadastros", "0022_sala_lugares_recepcao_propria"),
    ]

    operations = [
        migrations.RunPython(preencher, migrations.RunPython.noop),
    ]
