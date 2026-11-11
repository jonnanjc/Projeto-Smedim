"""
Converte as sessões por convênio JÁ AGENDADAS (futuras) para a regra de guias de
0023_guias_extras_sessao: cada 30 min de sessão = 1 guia (1h = 2), ver
AgendamentoItem.quantidade_guias_sessao. Até aqui, uma sessão de 1h era criada com
quantidade 1 — sem esta conversão, as sessões já marcadas (inclusive as séries semanais)
nunca apareceriam em "Guias pendentes", nem daria pra autorizar a 2ª guia antes do
atendimento (pedido da clínica, 13/10/2026).

Só mexe no que ainda NÃO conta na produção (antes de Autorizado) e que ainda vai
acontecer: atendimentos passados ou já autorizados ficam como estão — o valor deles pode
já ter sido faturado ou repassado. Cancelados/faltas também ficam de fora.
"""
from django.db import migrations
from django.utils import timezone

MINUTOS_POR_GUIA_SESSAO = 30  # cópia congelada de agenda.models (migração não importa models)


def converter(apps, schema_editor):
    AgendamentoItem = apps.get_model("agenda", "AgendamentoItem")
    itens = AgendamentoItem.objects.filter(
        agendamento__forma_atendimento="CONVENIO",
        agendamento__status__in=["AGENDADO", "PRE_CONFIRMADO", "CONFIRMADO", "PRE_AUTORIZADO"],
        agendamento__data_hora_inicio__gte=timezone.now(),
        procedimento__permite_repeticao_semanal=True,
    ).select_related("agendamento")
    alterados = []
    for item in itens:
        duracao = (item.agendamento.data_hora_fim - item.agendamento.data_hora_inicio).total_seconds() // 60
        guias = max(1, int(duracao) // MINUTOS_POR_GUIA_SESSAO)
        if item.quantidade != guias:
            item.quantidade = guias
            alterados.append(item)
    AgendamentoItem.objects.bulk_update(alterados, ["quantidade"])
    if alterados:
        print(f"\n  {len(alterados)} sessão(ões) futura(s) por convênio convertida(s) para guias de 30 min.")


class Migration(migrations.Migration):
    dependencies = [
        ("agenda", "0023_guias_extras_sessao"),
    ]

    operations = [
        migrations.RunPython(converter, migrations.RunPython.noop),
    ]
