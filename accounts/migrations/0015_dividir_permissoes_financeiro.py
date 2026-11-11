from django.apps import apps as global_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations

# codename usado até 2026-07-28 pra liberar, de uma vez só, as 9 páginas abaixo — dividido a
# pedido do cliente, pra dar pra escolher página por página (ver accounts/permissoes.py).
FINANCEIRO_VER_COMPLETO = "financeiro_ver_completo"
NOVAS_FINANCEIRO_VER = [
    "financeiro_ver_painel",
    "financeiro_ver_movimentacoes_avulsas",
    "financeiro_ver_contas_receber",
    "financeiro_ver_repasses_semanais",
    "financeiro_ver_fechamento_diario",
    "financeiro_ver_fechamento_mensal",
    "financeiro_ver_relatorio_detalhado",
    "financeiro_ver_repasse_convenio",
    "financeiro_ver_producao_convenio",
]
FATURAMENTO_LOTE_VISUALIZAR = "faturamento_lote_visualizar"
FATURAMENTO_COMPARATIVO_VISUALIZAR = "faturamento_comparativo_visualizar"

# Ancora estável pra descobrir o content_type do proxy PermissoesSistema nos dois sentidos
# desta migration — nunca é criada, removida nem renomeada por ela.
PERMISSAO_ANCORA = "financeiro_ver_proprio"


def _garantir_permissoes_do_codigo_atual(using):
    """
    As permissões novas (as 9 de NOVAS_FINANCEIRO_VER + FATURAMENTO_COMPARATIVO_VISUALIZAR) só
    existem em accounts.models.PermissoesSistema.Meta.permissions (código já editado) — o
    signal post_migrate que costuma criar as linhas de Permission só dispara depois que TODAS
    as migrations desta rodada terminarem, então dentro desta migration elas ainda não têm
    linha no banco. 'global_apps' (o app registry real, não o 'apps' histórico que o
    RunPython recebe) já reflete o models.py atual — chamar create_permissions com ele força
    a criação agora, antes de usarmos essas permissões abaixo. Padrão documentado do Django
    pra este exato cenário (permissão custom criada e usada na mesma migration).
    """
    create_permissions(global_apps.get_app_config("accounts"), verbosity=0, using=using)


def dividir_permissoes_financeiro(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    Usuario = apps.get_model("accounts", "Usuario")
    using = schema_editor.connection.alias

    _garantir_permissoes_do_codigo_atual(using)

    novas_financeiro = list(Permission.objects.using(using).filter(codename__in=NOVAS_FINANCEIRO_VER))
    permissao_antiga = Permission.objects.using(using).filter(codename=FINANCEIRO_VER_COMPLETO).first()
    comparativo = Permission.objects.using(using).filter(codename=FATURAMENTO_COMPARATIVO_VISUALIZAR).first()

    # Quem já tinha "financeiro_ver_completo" (grupo OU permissão individual) recebe as 9
    # novas automaticamente, pra ninguém perder acesso que já tinha — só depois disso a
    # permissão antiga é removida de quem a tinha e apagada (não faz mais sentido sozinha,
    # e não está mais em Meta.permissions).
    if permissao_antiga is not None:
        for grupo in Group.objects.using(using).filter(permissions=permissao_antiga):
            grupo.permissions.add(*novas_financeiro)
            grupo.permissions.remove(permissao_antiga)
        for usuario in Usuario.objects.using(using).filter(user_permissions=permissao_antiga):
            usuario.user_permissions.add(*novas_financeiro)
            usuario.user_permissions.remove(permissao_antiga)
        permissao_antiga.delete()

    # Mesma lógica pro par faturamento_lote_visualizar/faturamento_comparativo_visualizar —
    # aqui o codename antigo continua existindo (só ficou mais estreito: agora só libera
    # Lotes de faturamento, não mais o comparativo junto), então só ADICIONA a nova pra quem
    # já tinha a antiga, sem remover nada.
    if comparativo is not None:
        permissao_lote = Permission.objects.using(using).filter(codename=FATURAMENTO_LOTE_VISUALIZAR).first()
        if permissao_lote is not None:
            for grupo in Group.objects.using(using).filter(permissions=permissao_lote):
                grupo.permissions.add(comparativo)
            for usuario in Usuario.objects.using(using).filter(user_permissions=permissao_lote):
                usuario.user_permissions.add(comparativo)


def reverter(apps, schema_editor):
    """
    Best-effort: NÃO tenta reproduzir o estado anterior byte a byte, só devolver um acesso
    equivalente caso esta migration seja revertida. Quem tem qualquer uma das 9 novas volta a
    ter "financeiro_ver_completo" (e perde as 9); quem tem faturamento_comparativo_visualizar
    simplesmente perde essa permissão (a codename antiga já continua valendo pra Lotes).
    """
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    Usuario = apps.get_model("accounts", "Usuario")
    using = schema_editor.connection.alias

    ancora = Permission.objects.using(using).get(codename=PERMISSAO_ANCORA)
    permissao_antiga, _ = Permission.objects.using(using).get_or_create(
        codename=FINANCEIRO_VER_COMPLETO,
        content_type=ancora.content_type,
        defaults={"name": "Pode ver o financeiro completo da clínica"},
    )
    novas_financeiro = list(Permission.objects.using(using).filter(codename__in=NOVAS_FINANCEIRO_VER))
    comparativo = Permission.objects.using(using).filter(codename=FATURAMENTO_COMPARATIVO_VISUALIZAR).first()

    for grupo in Group.objects.using(using).filter(permissions__in=novas_financeiro).distinct():
        grupo.permissions.add(permissao_antiga)
    for usuario in Usuario.objects.using(using).filter(user_permissions__in=novas_financeiro).distinct():
        usuario.user_permissions.add(permissao_antiga)
    for permissao in novas_financeiro:
        permissao.delete()

    if comparativo is not None:
        comparativo.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0014_alter_permissoessistema_options"),
    ]

    operations = [
        migrations.RunPython(dividir_permissoes_financeiro, reverter),
    ]
