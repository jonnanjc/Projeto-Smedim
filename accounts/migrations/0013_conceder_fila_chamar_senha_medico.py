from django.db import migrations


def conceder_fila_chamar_senha_ao_medico(apps, schema_editor):
    """
    Concede 'fila_chamar_senha' já para o perfil 'Médico / Profissional de
    saúde' existente hoje — decisão do cliente: todo profissional já sai
    habilitado a chamar paciente em 'Minha agenda' (ver
    agenda.views.agendamento_chamar), sem precisar de um passo manual na tela
    de Perfis. 'seed_perfis.py' também foi atualizado (pra qualquer reseed
    futuro/instalação nova já vir certo), mas só isso não bastaria pros
    perfis já existentes neste banco.

    Só ADICIONA (.add(), nunca .set()) — ao contrário de rodar o comando
    seed_perfis inteiro de novo, que reaplicaria a lista completa de TODOS os
    perfis (arriscado se algum tiver sido ajustado manualmente pela tela de
    Perfis desde a última vez que o comando rodou). Idempotente: adicionar
    uma permissão que o grupo já tem não faz nada.
    """
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    grupo = Group.objects.filter(name="Médico / Profissional de saúde").first()
    permissao = Permission.objects.filter(codename="fila_chamar_senha").first()
    if grupo and permissao:
        grupo.permissions.add(permissao)


def reverter(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    grupo = Group.objects.filter(name="Médico / Profissional de saúde").first()
    permissao = Permission.objects.filter(codename="fila_chamar_senha").first()
    if grupo and permissao:
        grupo.permissions.remove(permissao)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_alter_permissoessistema_options"),
    ]

    operations = [
        migrations.RunPython(conceder_fila_chamar_senha_ao_medico, reverter),
    ]
