"""
Atestados e receituários emitidos pelo profissional direto do sistema — ver
documentos.views.atestado_novo/receituario_novo. Cada emissão fica registrada
aqui (pedido do cliente: precisa dar pra reimprimir depois e servir de
comprovante), a página impressa em si é só um "retrato" desses dados no
momento da emissão (ver templates documentos/atestado_imprimir.html e
documentos/receituario_imprimir.html).
"""
from django.db import models


class DocumentoEmitidoQuerySet(models.QuerySet):
    def visiveis_para(self, usuario):
        """
        Diferente do prontuário (onde só as especialidades de saúde mental são
        sigilosas — ver prontuario.models.EntradaProntuarioQuerySet.visiveis_para):
        pedido explícito do cliente é que TODO atestado/receituário fique restrito a
        quem o emitiu, qualquer que seja a especialidade — nenhum profissional vê ou
        reabre um documento emitido por outro colega, nem sabe que ele existe.
        """
        profissional_id = getattr(usuario, "profissional_id", None)
        if not profissional_id:
            return self.none()
        return self.filter(profissional_id=profissional_id)


class DocumentoEmitido(models.Model):
    """Um atestado ou receituário emitido para um paciente — ver Tipo abaixo."""

    class Tipo(models.TextChoices):
        ATESTADO = "ATESTADO", "Atestado"
        RECEITUARIO = "RECEITUARIO", "Receituário"

    tipo = models.CharField("tipo", max_length=12, choices=Tipo.choices)

    paciente = models.ForeignKey(
        "cadastros.Paciente", verbose_name="paciente", on_delete=models.PROTECT, related_name="documentos_emitidos"
    )
    profissional = models.ForeignKey(
        "cadastros.Profissional",
        verbose_name="profissional",
        on_delete=models.PROTECT,
        related_name="documentos_emitidos",
    )
    especialidade = models.CharField(
        "especialidade",
        max_length=100,
        help_text="Preenchida automaticamente com a especialidade do profissional no momento da emissão.",
    )
    agendamento = models.ForeignKey(
        "agenda.Agendamento",
        verbose_name="agendamento relacionado",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documentos_emitidos",
        help_text="Vínculo opcional com o atendimento específico que originou este documento.",
    )

    # --- Campos do Atestado (em branco quando tipo=RECEITUARIO) --------------------
    documento_identificacao = models.CharField(
        "CPF ou RG do paciente",
        max_length=40,
        blank=True,
        help_text="Ex.: \"CPF 123.456.789-00\" — pré-preenchido a partir do cadastro, editável.",
    )
    dias_afastamento = models.PositiveSmallIntegerField("dias de afastamento", null=True, blank=True)
    data_inicio_afastamento = models.DateField("início do afastamento", null=True, blank=True)
    cid = models.CharField("CID", max_length=20, blank=True)

    # --- Campo do Receituário (em branco quando tipo=ATESTADO) ---------------------
    observacoes = models.TextField("observações", blank=True)

    criado_em = models.DateTimeField("criado em", auto_now_add=True)

    objects = DocumentoEmitidoQuerySet.as_manager()

    class Meta:
        verbose_name = "Documento emitido"
        verbose_name_plural = "Documentos emitidos"
        ordering = ["-criado_em"]

    def __str__(self):
        return f"{self.get_tipo_display()} — {self.paciente.nome_completo} ({self.criado_em:%d/%m/%Y})"

    def visivel_para(self, usuario):
        """Segunda camada de proteção (ex: acesso direto por URL), além do filtro em
        `visiveis_para` — só quem emitiu pode reabrir, ver `visiveis_para` acima."""
        return getattr(usuario, "profissional_id", None) == self.profissional_id


class ItemReceituario(models.Model):
    """Uma linha do receituário — um medicamento/orientação, na ordem em que aparece
    na impressão (ver DocumentoEmitido.itens, usado só quando tipo=RECEITUARIO)."""

    documento = models.ForeignKey(DocumentoEmitido, on_delete=models.CASCADE, related_name="itens")
    ordem = models.PositiveSmallIntegerField("ordem", default=0)
    descricao = models.CharField("medicamento", max_length=200)
    posologia = models.CharField("posologia / instruções", max_length=200, blank=True)

    class Meta:
        verbose_name = "Item do receituário"
        verbose_name_plural = "Itens do receituário"
        ordering = ["ordem", "pk"]

    def __str__(self):
        return self.descricao
