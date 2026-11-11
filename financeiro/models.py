"""
Módulo Financeiro — Fase 1 (contas a receber de convênio + painel geral).

Sem modelo próprio por enquanto: os dados usados aqui vivem em
agenda.models.Agendamento (campos valor_convenio/convenio_recebido/
convenio_recebido_em) e em agenda.models.PagamentoParticular (pagamento
particular, que pode vir em até 2 formas — ver esse model) — este app só
reúne as telas de consulta e conciliação em cima desses dados, do mesmo
jeito que agenda.views já fazia para "Repasses semanais (particular)".

Fase 2 (LoteFaturamento/RetornoLoteItem, abaixo) — primeiro modelo próprio
do app: registra o LOTE/remessa enviada a um convênio (o que a clínica
mandou cobrar) e o retorno pago/glosado de cada atendimento do lote,
separado da produção (Agendamento.valor_convenio) e do "recebido" que já
existia (Agendamento.convenio_recebido). Ver claude/faturamento-lote-convenio.md.

Fase 3 (MovimentacaoCaixa, abaixo) — entrada/saída avulsa de caixa, sem ligação
com nenhum atendimento (ex.: aporte pra cobrir um repasse, repasse pago em
dinheiro na mão mesmo quando o atendimento de origem não foi pago em dinheiro).
Decisão do cliente (2026-07-22): fica desacoplada do repasse de convênio
(financeiro.views.repasse_convenio_marcar_pago), que continua só marcando
pago/não pago. O repasse PARTICULAR passou a gerar a saída automaticamente
quando pago em dinheiro do caixa (RepassePagamento, abaixo — pedido de
2026-09-28, junto com o pagamento parcial). Corrigir um lançamento errado é edição/exclusão
direta, sem fluxo de estorno com motivo (mais simples de propósito, diferente do
resto do sistema).
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from accounts.moeda import formatar_moeda


class LoteFaturamento(models.Model):
    """
    Lote/remessa enviada a um convênio — registra o que a clínica MANDOU cobrar
    (data_envio + valor_total_enviado), separado do que foi PRODUZIDO
    (Agendamento.valor_convenio, ver financeiro.views._producao_por_convenio) e do
    que voltou PAGO/GLOSADO (RetornoLoteItem, abaixo). Sempre vinculado a UM
    convênio específico — decisão do cliente, não existe lote "geral" misturando
    convênios diferentes (ver claude/faturamento-lote-convenio.md).
    """

    class Status(models.TextChoices):
        ENVIADO = "ENVIADO", "Enviado (aguardando retorno)"
        RETORNO_PARCIAL = "RETORNO_PARCIAL", "Retorno parcial"
        FECHADO = "FECHADO", "Fechado (retorno completo)"

    convenio = models.ForeignKey(
        "cadastros.Convenio", verbose_name="convênio", on_delete=models.PROTECT, related_name="lotes_faturamento"
    )
    data_envio = models.DateField("data de envio")
    numero_lote = models.CharField(
        "número do lote",
        max_length=40,
        blank=True,
        help_text="Identificação que o convênio usa para este lote/remessa, se exigir.",
    )
    valor_total_enviado = models.DecimalField(
        "valor total enviado",
        max_digits=10,
        decimal_places=2,
        help_text=(
            "Valor de fato enviado ao convênio — pode ser diferente da soma dos atendimentos "
            "incluídos abaixo (ex.: arredondamento da própria clínica)."
        ),
    )
    status = models.CharField("situação", max_length=16, choices=Status.choices, default=Status.ENVIADO)
    agendamentos = models.ManyToManyField(
        "agenda.Agendamento",
        verbose_name="atendimentos incluídos",
        related_name="lotes_faturamento",
        blank=True,
    )
    criado_por = models.ForeignKey(
        "accounts.Usuario",
        verbose_name="criado por",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lotes_faturamento_criados",
    )
    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Lote de faturamento"
        verbose_name_plural = "Lotes de faturamento"
        ordering = ["-data_envio", "-criado_em"]

    def __str__(self):
        return f"Lote {self.identificacao} — {self.convenio.nome} ({self.data_envio:%d/%m/%Y})"

    @property
    def identificacao(self):
        """Rótulo curto pra identificar o lote nas telas/avisos (número informado pelo
        convênio, se houver, senão '#pk') — ver contas_receber_lista, que avisa 'Está no
        lote nº X' quando um atendimento está travado por um lote ainda sem retorno."""
        return self.numero_lote or f"#{self.pk}"

    @property
    def valor_produzido(self):
        """Soma de Agendamento.valor_convenio dos atendimentos incluídos neste lote — 'quanto a
        clínica produziu' entre os itens deste lote especificamente (diferente de
        valor_total_enviado, que é o valor de fato mandado ao convênio, editável à parte)."""
        return self.agendamentos.aggregate(total=models.Sum("valor_convenio"))["total"] or Decimal("0.00")

    @property
    def valor_pago(self):
        return self.retornos.aggregate(total=models.Sum("valor_pago"))["total"] or Decimal("0.00")

    @property
    def valor_glosado(self):
        return self.retornos.aggregate(total=models.Sum("valor_glosado"))["total"] or Decimal("0.00")

    @property
    def diferenca_enviado(self):
        """Pago + Glosado (somados de todos os retornos já lançados) menos valor_total_enviado
        do lote — 0 quando os dois lançamentos já explicam tudo que foi de fato cobrado do
        convênio; != 0 sinaliza sobra não explicada (o caso relatado pelo cliente em
        2026-07-27: deu baixa com 'Pago' menor que o enviado, sem nada lançado em 'Glosado'
        pra cobrir a diferença — ver lote_detalhe.html, que destaca isto no topo da tela).

        Compara contra valor_total_enviado, NÃO contra valor_produzido — os dois podem
        divergir de propósito (valor_total_enviado é digitado à parte, pode levar
        arredondamento da própria clínica, ver o campo lá em cima), então comparar com
        Produzido aqui geraria alerta falso num lote sem nenhum problema de retorno, só
        porque o cabeçalho do lote não bate exatamente com a soma dos itens (ver
        RetornoLoteItem.diferenca_produzido, que é o comparativo por item contra o Produzido
        de CADA atendimento — esse sim é sempre esperado bater).

        Pode ficar != 0 mesmo com status FECHADO — FECHADO só olha se todo item tem retorno
        lançado, não se os valores batem (ver atualizar_status abaixo)."""
        return (self.valor_pago + self.valor_glosado) - self.valor_total_enviado

    @property
    def quantidade_itens(self):
        return self.agendamentos.count()

    @property
    def quantidade_com_retorno(self):
        return self.retornos.values("agendamento_id").distinct().count()

    def atualizar_status(self):
        """
        Recalcula ENVIADO/RETORNO_PARCIAL/FECHADO a partir de quantos atendimentos do lote já
        têm um RetornoLoteItem lançado — chamado automaticamente sempre que um retorno é salvo
        (ver RetornoLoteItem.save() abaixo). FECHADO quando TODO item do lote já tem retorno
        (mesmo que só glosado — "resolvido" não é sinônimo de "pago"); RETORNO_PARCIAL quando
        só uma parte tem; ENVIADO quando nenhum ainda tem.
        """
        total = self.quantidade_itens
        com_retorno = self.quantidade_com_retorno
        if total and com_retorno >= total:
            novo_status = self.Status.FECHADO
        elif com_retorno:
            novo_status = self.Status.RETORNO_PARCIAL
        else:
            novo_status = self.Status.ENVIADO
        if novo_status != self.status:
            self.status = novo_status
            self.save(update_fields=["status"])

    @classmethod
    def travando(cls, agendamento, excluir_lote_pk=None):
        """
        Outro LoteFaturamento (diferente de excluir_lote_pk, usado ao editar um lote já
        existente) que já contém este agendamento e ainda NÃO tem retorno lançado para ele
        (ver RetornoLoteItem) — é o que trava a dupla inclusão do mesmo atendimento em dois
        lotes ao mesmo tempo (decisão do cliente, obrigatória). Devolve o lote travando (pra
        exibir "Está no lote nº X, enviado em Y") ou None se este agendamento estiver livre.

        Usada tanto na tela de montar/editar lote (elegibilidade, ver agendamentos_elegiveis)
        quanto em financeiro.views.contas_receber_lista/contas_receber_marcar_recebido, pra
        impedir a baixa manual duplicada enquanto o mesmo atendimento aguarda retorno de lote.
        """
        qs = agendamento.lotes_faturamento.all()
        if excluir_lote_pk:
            qs = qs.exclude(pk=excluir_lote_pk)
        for lote in qs:
            if not RetornoLoteItem.objects.filter(lote=lote, agendamento=agendamento).exists():
                return lote
        return None

    @classmethod
    def agendamentos_elegiveis(cls, convenio, excluir_lote_pk=None):
        """
        Atendimentos que podem entrar num lote deste convênio (ver
        claude/faturamento-lote-convenio.md): Realizado, forma_atendimento=Convênio, deste
        convênio específico, com pelo menos um item que não é retorno grátis (retorno nunca
        gera cobrança ao convênio — mesmo filtro já usado em
        financeiro.views.contas_receber_lista; um agendamento com todos os itens em retorno
        não tem nada a faturar, mas um MISTO com pelo menos um item cobrável continua
        elegível) e não travado em outro lote ainda sem retorno (ver 'travando' acima).
        'excluir_lote_pk' é o pk do próprio lote sendo editado — permite reexibir/manter os
        itens que já estão nele, que senão apareceriam travados por ele mesmo.
        """
        from agenda.models import Agendamento, AgendamentoItem

        candidatos = (
            Agendamento.objects.filter(
                AgendamentoItem.filtro_nao_retorno("itens__"),
                Agendamento.filtro_convenio_a_faturar(),
                status__in=Agendamento.STATUS_PRODUCAO,
                convenio=convenio,
            )
            .select_related("paciente", "profissional")
            .prefetch_related("itens__procedimento")
            .distinct()
            .order_by("data_hora_inicio")
        )
        return [a for a in candidatos if cls.travando(a, excluir_lote_pk=excluir_lote_pk) is None]


class RetornoLoteItem(models.Model):
    """
    Retorno do convênio para UM atendimento específico dentro de um lote — não é só um valor
    total do lote porque glosa costuma ser parcial (alguns itens pagos, outros negados). Um
    atendimento só pode ter um retorno por lote (ver Meta.constraints) — corrigir um lançamento
    errado reedita esta mesma linha (ver financeiro.views.retorno_lancar), não cria outra.
    """

    lote = models.ForeignKey(LoteFaturamento, verbose_name="lote", on_delete=models.CASCADE, related_name="retornos")
    agendamento = models.ForeignKey(
        "agenda.Agendamento", verbose_name="atendimento", on_delete=models.CASCADE, related_name="retornos_lote"
    )
    valor_pago = models.DecimalField("valor pago", max_digits=8, decimal_places=2, default=Decimal("0.00"))
    valor_glosado = models.DecimalField("valor glosado", max_digits=8, decimal_places=2, default=Decimal("0.00"))
    motivo_glosa = models.TextField("motivo da glosa", blank=True, help_text="Motivo informado pelo convênio, se houver.")
    data_retorno = models.DateField("data do retorno")

    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Retorno de lote (item)"
        verbose_name_plural = "Retornos de lote (itens)"
        ordering = ["-data_retorno"]
        constraints = [
            models.UniqueConstraint(fields=["lote", "agendamento"], name="unico_retorno_por_agendamento_no_lote"),
        ]

    def __str__(self):
        return f"{self.agendamento.paciente.nome_completo} — pago R$ {formatar_moeda(self.valor_pago)} / glosado R$ {formatar_moeda(self.valor_glosado)}"

    @property
    def diferenca_produzido(self):
        """Pago + Glosado deste item menos o Produzido do atendimento (Agendamento.valor_convenio)
        — 0 quando os dois lançamentos explicam o valor inteiro cobrado do convênio; != 0
        sinaliza sobra não explicada (o caso mais comum é 'Pago' lançado a menos sem preencher
        'Glosado' — o sistema não cruza isso sozinho, ver RetornoLoteItemForm). Ver
        lote_detalhe.html/lote_dar_baixa.html, que destacam a linha quando isto é != 0."""
        produzido = self.agendamento.valor_convenio or Decimal("0.00")
        return (self.valor_pago + self.valor_glosado) - produzido

    @property
    def diferenca_absoluta(self):
        return abs(self.diferenca_produzido)

    def clean(self):
        if self.lote_id and self.agendamento_id and not self.lote.agendamentos.filter(pk=self.agendamento_id).exists():
            raise ValidationError("Este atendimento não está incluído neste lote.")

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Ao registrar QUALQUER valor pago (mesmo que parcial/com glosa do restante), marca o
        # atendimento como recebido do convênio — mesmo efeito que hoje só acontece manualmente
        # em financeiro.views.contas_receber_marcar_recebido, só que disparado automaticamente
        # pelo lançamento do retorno do lote (decisão do cliente, ver
        # claude/faturamento-lote-convenio.md). Só glosado (valor_pago=0) NÃO marca como
        # recebido — fica pendente de propósito (o que fazer com o valor glosado — reenviar,
        # cobrar do paciente etc. — fica fora do escopo deste módulo).
        if self.valor_pago and self.valor_pago > 0:
            self.agendamento.convenio_recebido = True
            self.agendamento.convenio_recebido_em = timezone.now()
            self.agendamento.save(update_fields=["convenio_recebido", "convenio_recebido_em"])
        self.lote.atualizar_status()


class MovimentacaoCaixa(models.Model):
    """
    Entrada ou saída avulsa de caixa — dinheiro que entra/sai fora de um atendimento (ex.: aporte
    pra cobrir um repasse, repasse de profissional pago em dinheiro na mão mesmo quando o
    atendimento de origem foi pago em cartão/Pix/convênio). 'valor' é sempre positivo, o sinal
    vem de 'tipo' — evita confusão de digitar número negativo no formulário.

    Sem vínculo direto com PagamentoParticular/Agendamento (decisão do cliente, 2026-07-22). A
    única exceção é a saída gerada automaticamente por RepassePagamento (repasse particular pago
    em dinheiro do caixa, ver abaixo) — essa fica ligada a ele e só é desfeita por lá. 'profissional' é opcional — só preenchido quando a movimentação se
    refere a repasse de alguém específico (ver financeiro.views._movimentacoes_avulsas_do_periodo,
    usada tanto no Painel financeiro quanto na Visão do proprietário do Fechamento/Meu fechamento).

    Corrigir um lançamento errado é edição/exclusão direta — sem estorno com motivo obrigatório
    (diferente do resto do sistema financeiro, decisão explícita do cliente pra manter isto leve).
    """

    class Tipo(models.TextChoices):
        ENTRADA = "ENTRADA", "Entrada"
        SAIDA = "SAIDA", "Saída"

    tipo = models.CharField("tipo", max_length=7, choices=Tipo.choices)
    valor = models.DecimalField(
        "valor", max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    data = models.DateField("data", default=timezone.localdate)
    descricao = models.CharField(
        "descrição", max_length=255, help_text="Ex.: 'Aporte pra cobrir repasse do Dr. Fulano', 'Repasse pago em dinheiro — Dra. Beltrana'."
    )
    profissional = models.ForeignKey(
        "cadastros.Profissional",
        verbose_name="profissional relacionado",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="movimentacoes_caixa",
        help_text="Preencha quando a movimentação for repasse pago/recebido em dinheiro deste profissional.",
    )
    criado_por = models.ForeignKey(
        "accounts.Usuario",
        verbose_name="lançado por",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimentacoes_caixa_criadas",
    )
    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Movimentação de caixa (avulsa)"
        verbose_name_plural = "Movimentações de caixa (avulsas)"
        ordering = ["-data", "-criado_em"]

    def __str__(self):
        return f"{self.get_tipo_display()} — R$ {formatar_moeda(self.valor)} ({self.data:%d/%m/%Y}) — {self.descricao}"


class RepassePagamento(models.Model):
    """
    Pagamento de repasse particular (cartão débito/crédito, Pix) a UM profissional — total ou
    PARCIAL. Pedido do cliente (2026-09-28): muitas vezes a produção do profissional é maior que
    o dinheiro que entrou no caixa, então ele leva só uma parte no dia (ou prefere levar só uma
    parte), e o restante o proprietário paga depois, normalmente por Pix. Por isso o financeiro
    informa QUANTO pagou, e o sistema abate dos atendimentos pendentes mais antigos primeiro
    (FIFO, ver alocar()), deixando o saldo pendente pros próximos pagamentos.

    Pago em dinheiro do caixa gera automaticamente uma SAÍDA em MovimentacaoCaixa (ligada em
    'movimentacao'), pra "Caixa da semana" do Painel financeiro fechar sem lançamento manual —
    isso substitui, só para esta tela, a decisão de 2026-07-22 de manter o repasse desacoplado
    do caixa. Pix/transferência do proprietário não mexe no caixa físico.

    Excluir um RepassePagamento lançado por engano desfaz o abatimento nas linhas
    (RepasseAlocacao guarda quanto foi em cada uma) e exclui a saída de caixa gerada — ver
    financeiro.views.repasse_pagamento_excluir.
    """

    class Forma(models.TextChoices):
        DINHEIRO = "DINHEIRO", "Dinheiro do caixa"
        PIX = "PIX", "Pix/transferência (proprietário)"

    profissional = models.ForeignKey(
        "cadastros.Profissional",
        verbose_name="profissional",
        on_delete=models.PROTECT,
        related_name="repasses_pagos",
    )
    data = models.DateField("data do pagamento", default=timezone.localdate)
    valor = models.DecimalField(
        "valor pago", max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    forma = models.CharField("pago como", max_length=10, choices=Forma.choices, default=Forma.DINHEIRO)
    observacao = models.CharField("observação", max_length=255, blank=True)
    movimentacao = models.OneToOneField(
        MovimentacaoCaixa,
        verbose_name="saída de caixa gerada",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="repasse_pagamento",
    )
    criado_por = models.ForeignKey(
        "accounts.Usuario",
        verbose_name="lançado por",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="repasses_pagos_lancados",
    )
    criado_em = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Pagamento de repasse (particular)"
        verbose_name_plural = "Pagamentos de repasse (particular)"
        ordering = ["-data", "-criado_em"]

    def __str__(self):
        return f"Repasse {self.profissional.nome} — R$ {formatar_moeda(self.valor)} ({self.data:%d/%m/%Y}, {self.get_forma_display()})"

    @staticmethod
    def pendentes_do_profissional(profissional):
        """Linhas de PagamentoParticular deste profissional com repasse ainda em aberto (total
        ou parcial), da mais antiga pra mais nova — é a ordem de abatimento (FIFO). Mesmos
        filtros de agenda.views.relatorio_repasses_semanal: particular, em produção, não
        estornado, não cortesia (não gera repasse). Dinheiro entra desde 26/10/2026 (ver
        PagamentoParticular.REPASSE_DINHEIRO_REGISTRADO_DESDE)."""
        from agenda.models import Agendamento, PagamentoParticular

        return (
            PagamentoParticular.objects.filter(
                Agendamento.filtro_pago_como_particular("agendamento__"),
                agendamento__profissional=profissional,
                agendamento__status__in=Agendamento.STATUS_PRODUCAO,
                repasse_pago=False,
                estornado=False,
            )
            .exclude(forma_pagamento=Agendamento.FormaPagamento.CORTESIA)
            # Proprietário não recebe repasse (mesma regra de repasse_pagamento_lista).
            .exclude(agendamento__profissional__eh_proprietario=True)
            .select_related("agendamento__paciente", "agendamento__profissional")
            .order_by("agendamento__data_hora_inicio", "id")
        )

    @classmethod
    def saldo_pendente(cls, profissional):
        return sum((p.saldo_repasse for p in cls.pendentes_do_profissional(profissional)), Decimal("0.00"))

    def alocar(self):
        """Abate self.valor das linhas pendentes do profissional, da mais antiga pra mais nova,
        registrando cada abatimento em RepasseAlocacao. Chamar dentro de transaction.atomic()
        logo depois de salvar — a view valida antes que valor <= saldo_pendente."""
        restante = self.valor
        agora = timezone.now()
        for pagamento in self.pendentes_do_profissional(self.profissional):
            if restante <= 0:
                break
            abatido = min(restante, pagamento.saldo_repasse)
            if abatido <= 0:
                continue
            pagamento.repasse_valor_pago += abatido
            if pagamento.repasse_valor_pago >= pagamento.valor_repasse:
                pagamento.repasse_pago = True
                pagamento.repasse_pago_em = agora
            pagamento.save(update_fields=["repasse_valor_pago", "repasse_pago", "repasse_pago_em"])
            RepasseAlocacao.objects.create(repasse=self, pagamento=pagamento, valor=abatido)
            restante -= abatido
        return restante

    def desfazer_alocacoes(self):
        """Devolve às linhas o que este pagamento tinha abatido (usado ao excluir um lançamento
        errado). Linha estornada depois do abatimento já teve o repasse zerado no estorno
        (financeiro.views.pagamento_estornar) — não volta a ficar negativa aqui."""
        for alocacao in self.alocacoes.select_related("pagamento"):
            pagamento = alocacao.pagamento
            pagamento.repasse_valor_pago = max(pagamento.repasse_valor_pago - alocacao.valor, Decimal("0.00"))
            if pagamento.repasse_valor_pago < pagamento.valor_repasse:
                pagamento.repasse_pago = False
                pagamento.repasse_pago_em = None
            pagamento.save(update_fields=["repasse_valor_pago", "repasse_pago", "repasse_pago_em"])


class RepasseAlocacao(models.Model):
    """Quanto de um RepassePagamento foi abatido em cada linha de PagamentoParticular — é o que
    permite mostrar 'pago parcialmente' por atendimento e desfazer um lançamento errado."""

    repasse = models.ForeignKey(RepassePagamento, on_delete=models.CASCADE, related_name="alocacoes")
    pagamento = models.ForeignKey(
        "agenda.PagamentoParticular", on_delete=models.CASCADE, related_name="alocacoes_repasse"
    )
    valor = models.DecimalField("valor abatido", max_digits=8, decimal_places=2)

    class Meta:
        verbose_name = "Abatimento de repasse"
        verbose_name_plural = "Abatimentos de repasse"
        ordering = ["id"]
