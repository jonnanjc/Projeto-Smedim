from django import forms
from django.utils import timezone

from .models import EntradaProntuario


class EstiloCampoMixin:
    """Mesma convenção usada em agenda/forms.py e cadastros/forms.py."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            if isinstance(campo.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                continue
            classes = campo.widget.attrs.get("class", "")
            campo.widget.attrs["class"] = (classes + " campo").strip()


class EntradaProntuarioForm(EstiloCampoMixin, forms.ModelForm):
    """
    Formulário de registro no prontuário — paciente, profissional,
    especialidade e agendamento vêm pré-preenchidos e travados (campos
    ocultos) a partir do contexto de onde o profissional clicou em
    "Adicionar registro no prontuário" (ver prontuario.views.entrada_nova);
    só os temas clínicos (ver EntradaProntuario.CAMPOS_TEMATICOS) são
    digitados de fato — todos opcionais, cada profissional preenche só o
    que fizer sentido pra especialidade/atendimento dele.

    `simplificado=True` (equipe multi, ver models.usa_prontuario_simplificado) mostra só o
    campo de evolução. Em qualquer dos dois modos, um campo que JÁ tem texto no registro
    sendo editado continua aparecendo — senão ficaria gravado sem ninguém conseguir corrigir.

    `data_atendimento` (pedido da clínica, 2026-10-26) vem sempre primeiro: a equipe multi
    às vezes evolui dias depois e escolhe aqui o dia do atendimento. `data_padrao` é o valor
    inicial num registro novo (dia do agendamento, ou hoje) e vale também se vier em branco.
    """

    class Meta:
        model = EntradaProntuario
        fields = ["data_atendimento", *EntradaProntuario.CAMPOS_EXIBICAO]
        widgets = {
            "data_atendimento": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            **{campo: forms.Textarea(attrs={"rows": 4}) for campo in EntradaProntuario.CAMPOS_EXIBICAO},
        }

    def __init__(self, *args, simplificado=False, data_padrao=None, **kwargs):
        super().__init__(*args, **kwargs)
        data_campo = self.fields["data_atendimento"]
        data_campo.widget.attrs["max"] = timezone.localdate().isoformat()
        data_campo.help_text = "Dia em que o atendimento aconteceu — mude se estiver registrando depois."
        if self.instance.pk:
            self._data_padrao = self.instance.data_exibicao
        else:
            self._data_padrao = data_padrao or timezone.localdate()
        if not self.instance.data_atendimento:
            data_campo.initial = self._data_padrao
        usados = {"data_atendimento"} | (
            {EntradaProntuario.CAMPO_SIMPLIFICADO} if simplificado else set(EntradaProntuario.CAMPOS_TEMATICOS)
        )
        for nome in list(self.fields):
            if nome not in usados and not getattr(self.instance, nome):
                del self.fields[nome]
        if simplificado:
            self.fields[EntradaProntuario.CAMPO_SIMPLIFICADO].widget.attrs["rows"] = 8

    def clean_data_atendimento(self):
        data = self.cleaned_data["data_atendimento"] or self._data_padrao
        if data > timezone.localdate():
            raise forms.ValidationError("A data do atendimento não pode ser no futuro.")
        return data
