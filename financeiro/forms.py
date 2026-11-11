from decimal import Decimal

from django import forms

from accounts.moeda import formatar_moeda, preparar_campos_moeda
from agenda.models import Agendamento, AgendamentoItem, PagamentoParticular
from cadastros.models import Convenio, Profissional

from .models import LoteFaturamento, MovimentacaoCaixa, RepassePagamento, RetornoLoteItem


class EstiloCampoMixin:
    """Aplica a classe CSS 'campo' (usada em accounts/base.html) a todo campo de texto/número/data/seleção."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            if isinstance(campo.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                continue
            classes = campo.widget.attrs.get("class", "")
            campo.widget.attrs["class"] = (classes + " campo").strip()
        preparar_campos_moeda(self)  # dinheiro digitado/mostrado como 150.000,00


class LancarValorConvenioForm(forms.ModelForm):
    """
    Preenche/corrige manualmente o valor a receber de UM ITEM de agendamento por
    convênio — usada quando cadastros.models.Convenio.valor_para não conseguiu
    calcular sozinho (convênio sem preço configurado, ou que calcula por desconto
    sobre a tabela particular, que ainda não existe no sistema — ver comentário em
    Convenio.valor_para). Um agendamento pode ter mais de um item (ver
    agenda.models.AgendamentoItem) — financeiro.views.contas_receber_lancar_valor
    monta um desses forms por item ainda sem preço, não um só pro agendamento
    inteiro, já que cada item pode ter um procedimento (e portanto um preço)
    diferente.
    """

    class Meta:
        model = AgendamentoItem
        fields = ["valor_convenio"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["valor_convenio"].required = True
        self.fields["valor_convenio"].widget.attrs["class"] = "campo"
        self.fields["valor_convenio"].label = self.instance.procedimento.nome
        preparar_campos_moeda(self)


class EstornoPagamentoForm(forms.ModelForm):
    """Captura o motivo ao estornar uma linha de PagamentoParticular — ver
    financeiro.views.pagamento_estornar."""

    class Meta:
        model = PagamentoParticular
        fields = ["motivo_estorno"]
        widgets = {"motivo_estorno": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["motivo_estorno"].required = True
        self.fields["motivo_estorno"].label = "Motivo do estorno"
        self.fields["motivo_estorno"].widget.attrs["class"] = "campo"


class LoteFaturamentoForm(EstiloCampoMixin, forms.ModelForm):
    """
    Cabeçalho do lote (convênio + dados de envio) — a escolha de QUAIS atendimentos entram
    no lote acontece à parte, via checkboxes na própria tela (ver
    financeiro.views.lote_novo/lote_editar), não neste form.
    """

    class Meta:
        model = LoteFaturamento
        fields = ["convenio", "data_envio", "numero_lote", "valor_total_enviado"]
        # format="%Y-%m-%d" força o valor inicial a sempre sair em ISO (independente do locale
        # pt-br, que por padrão usaria "%d/%m/%Y") — <input type="date"> só reconhece ISO; sem
        # isso o navegador rejeita o valor pré-preenchido ao editar um lote existente (o campo
        # aparenta estar vazio), mesmo bug já corrigido em agenda.forms.AgendamentoForm ('data').
        widgets = {"data_envio": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *args, convenio_travado=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["convenio"].queryset = Convenio.objects.filter(ativo=True).order_by("nome")
        # 'convenio_travado' vem da 1ª etapa da tela de montar lote (financeiro.views.lote_novo),
        # que já fixou o convênio antes de carregar a lista de atendimentos elegíveis — mesmo
        # padrão de AgendamentoForm com 'data_travada' (agenda/forms.py). Ao EDITAR um lote já
        # existente (self.instance.pk), o convênio também fica travado — trocá-lo invalidaria os
        # atendimentos já incluídos, que são sempre do convênio original. 'disabled=True' (em vez
        # de só ocultar o campo) faz o Django ignorar qualquer valor postado e usar sempre o
        # 'initial'/valor da instância — protege contra adulteração do POST.
        if convenio_travado:
            self.fields["convenio"].initial = convenio_travado.pk
            self.fields["convenio"].disabled = True
        elif self.instance.pk:
            self.fields["convenio"].disabled = True


class RetornoLoteItemForm(EstiloCampoMixin, forms.ModelForm):
    """Retorno do convênio para UM atendimento do lote — ver financeiro.models.RetornoLoteItem
    e financeiro.views.retorno_lancar ('lote'/'agendamento' já vêm fixados pela URL, por isso
    ficam de fora deste form — só o que o convênio de fato informou é digitado aqui)."""

    class Meta:
        model = RetornoLoteItem
        fields = ["valor_pago", "valor_glosado", "motivo_glosa", "data_retorno"]
        widgets = {
            # format="%Y-%m-%d" — mesmo motivo de LoteFaturamentoForm.data_envio acima: sem
            # isso, o navegador mostra o campo vazio ao corrigir um retorno já lançado.
            "data_retorno": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "motivo_glosa": forms.Textarea(attrs={"rows": 3}),
        }


class MovimentacaoCaixaForm(EstiloCampoMixin, forms.ModelForm):
    """Entrada/saída avulsa de caixa — ver financeiro.models.MovimentacaoCaixa. 'profissional' é
    opcional (só quando a movimentação se refere ao repasse de alguém específico)."""

    class Meta:
        model = MovimentacaoCaixa
        fields = ["tipo", "valor", "data", "descricao", "profissional"]
        widgets = {
            # format="%Y-%m-%d" — mesmo motivo de LoteFaturamentoForm.data_envio: sem isso, o
            # navegador mostra o campo vazio ao editar uma movimentação já lançada.
            "data": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "descricao": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["profissional"].queryset = Profissional.objects.filter(ativo=True).order_by("nome")
        self.fields["profissional"].required = False


class RepassePagamentoForm(EstiloCampoMixin, forms.ModelForm):
    """Pagamento (total ou parcial) de repasse particular a um profissional — ver
    financeiro.models.RepassePagamento. 'saldo' é o saldo pendente do profissional no momento
    (todas as semanas): não deixa pagar mais do que se deve."""

    class Meta:
        model = RepassePagamento
        fields = ["valor", "forma", "data", "observacao"]
        widgets = {
            "data": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }
        help_texts = {
            "forma": (
                "Dinheiro do caixa lança automaticamente uma saída de caixa. Pix/transferência feito pelo "
                "proprietário não mexe no caixa."
            ),
        }

    def __init__(self, *args, saldo, **kwargs):
        super().__init__(*args, **kwargs)
        self.saldo = saldo

    def clean_valor(self):
        valor = self.cleaned_data["valor"]
        if valor is not None and valor > self.saldo:
            raise forms.ValidationError(
                f"O valor é maior que o saldo pendente deste profissional (R$ {formatar_moeda(self.saldo)})."
            )
        return valor


class CorrigirPagamentoForm(EstiloCampoMixin, forms.Form):
    """Correção de um pagamento particular registrado errado (pedido da clínica, 23/10/2026) —
    ver financeiro.views.pagamento_corrigir. Até 2 formas, como no registro
    (agenda.forms.ConfirmarPagamentoForm): corrige forma, divisão entre as formas, valor recebido
    do cartão, parcelas e também o TOTAL (liberado pela clínica no mesmo dia) — mudar o total
    exige motivo e não vale com venda de pacote no pagamento (permite_mudar_total=False). Cortesia
    fica de fora: não é "forma" de dinheiro recebido."""

    def __init__(self, *args, total, permite_mudar_total=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.total = total
        self.permite_mudar_total = permite_mudar_total
        self.fields["motivo"] = forms.CharField(
            label="Motivo da mudança do total", required=False, max_length=200,
            help_text="Obrigatório só se o total mudar (ex.: faltou aplicar o desconto autorizado).",
        )
        escolhas = [("", "---------")] + [
            (codigo, rotulo)
            for codigo, rotulo in Agendamento.FormaPagamento.choices
            if codigo != Agendamento.FormaPagamento.CORTESIA
        ]
        for n in (1, 2):
            self.fields[f"forma_{n}"] = forms.ChoiceField(
                label=f"{n}ª forma de pagamento", choices=escolhas, required=(n == 1)
            )
            self.fields[f"valor_{n}"] = forms.DecimalField(
                label=f"Valor da {n}ª forma", required=False, min_value=Decimal("0.01"), max_digits=8, decimal_places=2
            )
            self.fields[f"valor_recebido_{n}"] = forms.DecimalField(
                label="Valor recebido (cartão, já sem a taxa)", required=False, min_value=Decimal("0.01"),
                max_digits=8, decimal_places=2,
            )
            self.fields[f"parcelas_{n}"] = forms.IntegerField(label="Parcelas (crédito)", required=False, min_value=1)
            for campo in ("forma", "valor", "valor_recebido", "parcelas"):
                self.fields[f"{campo}_{n}"].widget.attrs["class"] = "campo"
        preparar_campos_moeda(self)  # campos criados acima, depois do mixin

    def clean(self):
        dados = super().clean()
        linhas = []
        for n in (1, 2):
            forma = dados.get(f"forma_{n}")
            if not forma:
                continue
            valor = dados.get(f"valor_{n}")
            if valor is None:
                if n == 1 and not dados.get("forma_2"):
                    valor = self.total  # forma única: o valor é o total
                else:
                    self.add_error(f"valor_{n}", "Informe o valor desta forma.")
                    continue
            eh_cartao = forma in (Agendamento.FormaPagamento.CARTAO_DEBITO, Agendamento.FormaPagamento.CARTAO_CREDITO)
            recebido = dados.get(f"valor_recebido_{n}") if eh_cartao else None
            if recebido is not None and recebido > valor:
                self.add_error(f"valor_recebido_{n}", "O valor recebido não pode passar do valor da forma.")
            parcelas = dados.get(f"parcelas_{n}") if forma == Agendamento.FormaPagamento.CARTAO_CREDITO else None
            linhas.append({"forma_pagamento": forma, "valor": valor, "valor_recebido": recebido, "parcelas": parcelas})
        if len(linhas) == 2 and linhas[0]["forma_pagamento"] == linhas[1]["forma_pagamento"]:
            raise forms.ValidationError("As duas formas são iguais — use uma forma só.")
        self.novo_total = sum((l["valor"] for l in linhas), Decimal("0"))
        if not self.errors and self.novo_total != self.total:
            if not self.permite_mudar_total:
                raise forms.ValidationError(
                    f"Este pagamento inclui venda de pacote — a soma das formas tem que fechar R$ {formatar_moeda(self.total)}. "
                    "Para mudar o valor, estorne o pagamento."
                )
            if not (dados.get("motivo") or "").strip():
                self.add_error("motivo", f"O total mudou (de R$ {formatar_moeda(self.total)} para R$ {formatar_moeda(self.novo_total)}) — informe o motivo.")
        self.linhas = linhas
        return dados