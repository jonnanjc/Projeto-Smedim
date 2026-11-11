"""
Autorizado passou a contar como produção (ver Agendamento.STATUS_PRODUCAO) e o valor do
convênio agora é preenchido já na autorização (agenda.views._preencher_valor_convenio).
Atendimentos de convênio que JÁ estavam Autorizados antes desta regra nunca tiveram o
valor preenchido (isso só acontecia ao marcar "atendido") — sem este backfill eles
entrariam na produção sem valor e ficariam de fora dos relatórios que exigem
valor_convenio. Mesma regra da view, reimplementada com os models históricos: retorno
grátis = 0,00; demais itens só se ainda vazios, pelo preço do profissional > preço
geral > desconto sobre o valor particular.
"""
from decimal import Decimal

from django.db import migrations


def _valor_para(convenio, procedimento, profissional):
    if not procedimento:
        return None
    preco = convenio.tabela_precos.filter(procedimento=procedimento, profissional=profissional).first()
    if preco:
        return preco.valor
    preco_geral = convenio.tabela_precos.filter(procedimento=procedimento, profissional__isnull=True).first()
    if preco_geral:
        return preco_geral.valor
    if convenio.desconto_percentual_particular is not None and procedimento.valor_particular is not None:
        fator_restante = (Decimal("100") - convenio.desconto_percentual_particular) / Decimal("100")
        return (procedimento.valor_particular * fator_restante).quantize(Decimal("0.01"))
    return None


def preencher(apps, schema_editor):
    Agendamento = apps.get_model("agenda", "Agendamento")
    AgendamentoItem = apps.get_model("agenda", "AgendamentoItem")

    autorizados = Agendamento.objects.filter(status="AUTORIZADO", forma_atendimento="CONVENIO").select_related(
        "convenio", "profissional"
    )
    for agendamento in autorizados:
        itens = list(AgendamentoItem.objects.filter(agendamento=agendamento).select_related("procedimento"))
        alterados = []
        for item in itens:
            if item.retorno_de_id is not None:
                if item.valor_convenio != Decimal("0.00"):
                    item.valor_convenio = Decimal("0.00")
                    alterados.append(item)
            elif item.valor_convenio is None and agendamento.convenio_id:
                unitario = _valor_para(agendamento.convenio, item.procedimento, agendamento.profissional)
                if unitario is not None:
                    item.valor_convenio = (unitario * item.quantidade).quantize(Decimal("0.01"))
                    alterados.append(item)
        if alterados:
            AgendamentoItem.objects.bulk_update(alterados, ["valor_convenio"])
        if itens and all(item.valor_convenio is not None for item in itens):
            total = sum((item.valor_convenio for item in itens), Decimal("0.00"))
            if agendamento.valor_convenio != total:
                agendamento.valor_convenio = total
                agendamento.save(update_fields=["valor_convenio"])


class Migration(migrations.Migration):

    dependencies = [
        ("agenda", "0017_agendamento_lote_recepcao"),
        ("cadastros", "0022_sala_lugares_recepcao_propria"),
    ]

    operations = [
        migrations.RunPython(preencher, migrations.RunPython.noop),
    ]
