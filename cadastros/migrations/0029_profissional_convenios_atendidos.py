"""
Novo campo Profissional.convenios_atendidos (pedido do cliente, 2026-10-14): na hora de
agendar, só aparecem os convênios que o profissional atende.

Sem preenchimento inicial (decisão do cliente — clínica ainda em implantação): todo
profissional começa sem convênio marcado (= só particular) até a clínica marcar no cadastro.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cadastros', '0028_pacote'),
    ]

    operations = [
        migrations.AddField(
            model_name='profissional',
            name='convenios_atendidos',
            field=models.ManyToManyField(blank=True, help_text='Marque os convênios/planos que este profissional atende. Na hora de agendar, só aparecem estes convênios (e só os procedimentos com preço cadastrado no convênio escolhido). Nenhum marcado = atende só particular.', related_name='profissionais_que_atendem', to='cadastros.convenio', verbose_name='convênios que atende'),
        ),
    ]
