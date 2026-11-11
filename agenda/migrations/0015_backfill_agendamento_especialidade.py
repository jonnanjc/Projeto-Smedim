"""
Backfill de dados: preenche Agendamento.especialidade/especialidade_outra (campos novos,
ver 0014_agendamento_especialidade_and_more) a partir da especialidade PRINCIPAL do
profissional de cada agendamento já existente. Na época em que todo este histórico foi
criado, cada profissional só podia ter uma especialidade mesmo (múltiplas especialidades
por profissional é uma funcionalidade posterior — ver cadastros.models.
ProfissionalEspecialidade), então usar sempre a principal aqui é exato, não uma
aproximação, mesmo para profissionais que hoje já tenham especialidades adicionais.
"""
from django.db import migrations


def preencher_especialidade(apps, schema_editor):
    Agendamento = apps.get_model("agenda", "Agendamento")

    atualizar = []
    for agendamento in Agendamento.objects.filter(especialidade="").select_related("profissional"):
        profissional = agendamento.profissional
        if profissional and profissional.especialidade:
            agendamento.especialidade = profissional.especialidade
            agendamento.especialidade_outra = profissional.especialidade_outra
            atualizar.append(agendamento)
    Agendamento.objects.bulk_update(atualizar, ["especialidade", "especialidade_outra"], batch_size=500)


def reverter(apps, schema_editor):
    Agendamento = apps.get_model("agenda", "Agendamento")
    Agendamento.objects.update(especialidade="", especialidade_outra="")


class Migration(migrations.Migration):

    dependencies = [
        ("agenda", "0014_agendamento_especialidade_and_more"),
    ]

    operations = [
        migrations.RunPython(preencher_especialidade, reverter),
    ]
