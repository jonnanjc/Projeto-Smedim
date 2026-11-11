from django import forms

from .models import DocumentoEmitido, ItemReceituario

# Quantas linhas de medicamento aparecem prontas no formulário de receituário — dá
# pra deixar em branco a que não for usada (o formset descarta linha vazia sozinho,
# ver ItemReceituarioFormSet abaixo). Sem JS de "+ adicionar linha" de propósito:
# mantém o formulário simples, e 8 cobre a esmagadora maioria das receitas.
LINHAS_RECEITUARIO = 8


class EstiloCampoMixin:
    """Mesma convenção usada em agenda/forms.py, cadastros/forms.py e prontuario/forms.py."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            if isinstance(campo.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                continue
            classes = campo.widget.attrs.get("class", "")
            campo.widget.attrs["class"] = (classes + " campo").strip()


class AtestadoForm(EstiloCampoMixin, forms.ModelForm):
    """
    Formulário de emissão de atestado — paciente/profissional/especialidade/agendamento
    vêm pré-preenchidos e travados a partir do contexto de onde o profissional clicou em
    "Emitir atestado" (ver documentos.views.atestado_novo). Identificação (CPF/RG) e
    início do afastamento já chegam sugeridos (ver initial na view), mas ficam editáveis.
    """

    # format="%Y-%m-%d" força o valor inicial a sempre sair em ISO (independente do locale
    # pt-br, que por padrão usaria "%d/%m/%Y") — <input type="date"> só reconhece ISO;
    # sem isso o navegador rejeita a data pré-preenchida do atendimento e o campo aparenta
    # estar vazio (mesmo bug documentado em agenda/forms.py e financeiro/forms.py).
    data_inicio_afastamento = forms.DateField(
        label="A partir de",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
    )

    class Meta:
        model = DocumentoEmitido
        fields = ["documento_identificacao", "dias_afastamento", "data_inicio_afastamento", "cid"]
        labels = {
            "documento_identificacao": "CPF ou RG do paciente",
            "dias_afastamento": "Dias de afastamento",
            "cid": "CID",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["dias_afastamento"].required = True
        self.fields["cid"].required = True
        self.fields["cid"].widget.attrs["placeholder"] = "Ex.: J06.9"


class ReceituarioForm(EstiloCampoMixin, forms.ModelForm):
    class Meta:
        model = DocumentoEmitido
        fields = ["observacoes"]
        widgets = {
            "observacoes": forms.Textarea(attrs={"rows": 3, "placeholder": "Opcional — orientações gerais além dos itens abaixo."}),
        }
        labels = {"observacoes": "Observações gerais (opcional)"}


class ItemReceituarioForm(EstiloCampoMixin, forms.ModelForm):
    class Meta:
        model = ItemReceituario
        fields = ["descricao", "posologia"]
        widgets = {
            "descricao": forms.TextInput(attrs={"placeholder": "Medicamento e dose — ex.: Amoxicilina 500mg"}),
            "posologia": forms.TextInput(attrs={"placeholder": "Posologia — ex.: 1 cápsula, via oral, de 8/8h, por 7 dias"}),
        }
        labels = {"descricao": "Medicamento", "posologia": "Posologia / instruções"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["descricao"].required = False
        self.fields["posologia"].required = False


ItemReceituarioFormSet = forms.inlineformset_factory(
    DocumentoEmitido,
    ItemReceituario,
    form=ItemReceituarioForm,
    extra=LINHAS_RECEITUARIO,
    can_delete=False,
)
