# Substitui o campo único 'texto' por temas pré-definidos (ver
# prontuario.models.EntradaProntuario.CAMPOS_TEMATICOS) — pedido do cliente
# de um prontuário "mais completo". O conteúdo já digitado em 'texto' é
# preservado em 'observacoes_adicionais' (ver RunPython abaixo), não é
# perdido na migração.
from django.db import migrations, models


def copiar_texto_para_observacoes(apps, schema_editor):
    EntradaProntuario = apps.get_model("prontuario", "EntradaProntuario")
    EntradaProntuario.objects.update(observacoes_adicionais=models.F("texto"))


def copiar_observacoes_para_texto(apps, schema_editor):
    """Reverso — usado só se esta migration for revertida."""
    EntradaProntuario = apps.get_model("prontuario", "EntradaProntuario")
    EntradaProntuario.objects.update(texto=models.F("observacoes_adicionais"))


class Migration(migrations.Migration):

    dependencies = [
        ("prontuario", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="entradaprontuario",
            name="queixa_principal",
            field=models.TextField(blank=True, default="", verbose_name="queixa principal / motivo do atendimento"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="entradaprontuario",
            name="historico",
            field=models.TextField(blank=True, default="", verbose_name="histórico (anamnese)"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="entradaprontuario",
            name="avaliacao",
            field=models.TextField(blank=True, default="", verbose_name="avaliação / exame"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="entradaprontuario",
            name="hipotese_diagnostica",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Inclua o CID aqui também, se aplicável — não há campo separado para o código.",
                verbose_name="hipótese diagnóstica",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="entradaprontuario",
            name="conduta",
            field=models.TextField(blank=True, default="", verbose_name="conduta / plano terapêutico"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="entradaprontuario",
            name="observacoes_adicionais",
            field=models.TextField(blank=True, default="", verbose_name="observações adicionais"),
            preserve_default=False,
        ),
        migrations.RunPython(copiar_texto_para_observacoes, copiar_observacoes_para_texto),
        migrations.RemoveField(
            model_name="entradaprontuario",
            name="texto",
        ),
    ]
