"""
Pedido do cliente, 2026-09-28: sai o "desconto (%) sobre a tabela particular" do convênio
(nem todo procedimento desses convênios tinha desconto), e todo convênio passa a ter os
valores cadastrados na Tabela de Preços.

Pra não quebrar nada no dia da atualização, os convênios que usavam o desconto:
- ganham na Tabela de Preços um preço geral para cada procedimento ativo com "valor
  particular" que ainda não tinha preço geral nesse convênio — com o mesmo valor que o
  sistema calculava até hoje (a clínica depois corrige os que não têm desconto);
- ficam com "permite pagamento na hora" marcado, que era o outro efeito do desconto
  (botão "Encaminhar para pagamento (na hora)" na agenda).
"""
from decimal import Decimal

from django.db import migrations, models


def copiar_desconto_para_tabela(apps, schema_editor):
    Convenio = apps.get_model("cadastros", "Convenio")
    Procedimento = apps.get_model("cadastros", "Procedimento")
    TabelaPrecoConvenio = apps.get_model("cadastros", "TabelaPrecoConvenio")

    procedimentos = list(Procedimento.objects.filter(ativo=True, valor_particular__isnull=False))
    for convenio in Convenio.objects.filter(desconto_percentual_particular__isnull=False):
        convenio.permite_pagamento_na_hora = True
        convenio.save(update_fields=["permite_pagamento_na_hora"])

        fator_restante = (Decimal("100") - convenio.desconto_percentual_particular) / Decimal("100")
        ja_tem_preco_geral = set(
            TabelaPrecoConvenio.objects.filter(convenio=convenio, profissional__isnull=True).values_list(
                "procedimento_id", flat=True
            )
        )
        TabelaPrecoConvenio.objects.bulk_create(
            [
                TabelaPrecoConvenio(
                    convenio=convenio,
                    procedimento=procedimento,
                    profissional=None,
                    valor=(procedimento.valor_particular * fator_restante).quantize(Decimal("0.01")),
                )
                for procedimento in procedimentos
                if procedimento.pk not in ja_tem_preco_geral
            ]
        )


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0025_convenio_prazo_retorno_dias"),
    ]

    operations = [
        migrations.AddField(
            model_name="convenio",
            name="permite_pagamento_na_hora",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Marque se, para este convênio, a recepção pode escolher na hora entre encaminhar o "
                    "paciente para pagar no financeiro ou autorizar faturando pelo convênio."
                ),
                verbose_name="permite pagamento na hora",
            ),
        ),
        migrations.RunPython(copiar_desconto_para_tabela, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="convenio",
            name="desconto_percentual_particular",
        ),
    ]
