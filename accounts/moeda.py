"""
Dinheiro no formato brasileiro com ponto de milhar: 150.000,00 (pedido da clínica, 06/11/2026 —
o financeiro via "150000,00" e não conseguia ler direito os valores grandes).

Três partes, usadas pelo sistema todo:

- formatar_moeda(): texto "150.000,00" pra mensagens montadas no Python (antes saía
  "150000.00", com ponto decimal).
- Telas (templates): todo Decimal impresso com {{ }} ganha ponto de milhar — ver
  instalar_milhar_em_decimais(), chamado em accounts.apps. No sistema, dinheiro é sempre Decimal;
  contagens e códigos (ex.: id do paciente, que passa de 37 mil e vai em links/JavaScript) são
  inteiros e NÃO mudam. Por isso não se usa USE_THOUSAND_SEPARATOR global.
- Campos de digitação: preparar_campos_moeda() troca os DecimalField de dinheiro dos formulários
  por campo de texto que mostra e aceita "150.000,00" (o <input type="number"> do navegador não
  mostra ponto de milhar). O JavaScript em accounts/base.html formata ao sair do campo.
"""
from decimal import Decimal, InvalidOperation

from django import forms
from django.core.exceptions import ValidationError
from django.utils import numberformat


def formatar_moeda(valor, casas=2):
    """150000 → "150.000,00". None/"" → "" (quem chama decide o que mostrar)."""
    if valor is None or valor == "":
        return ""
    try:
        valor = Decimal(str(valor))
    except InvalidOperation:
        return str(valor)
    return numberformat.format(
        valor, ",", decimal_pos=casas, grouping=3, thousand_sep=".", force_grouping=True, use_l10n=False
    )


def ler_moeda(texto):
    """Texto digitado → Decimal (None se vazio). Aceita "150.000,00", "150000,00", "150000.00",
    "1.500" (mil e quinhentos) e "R$ 10,50". Regra do ponto: com vírgula, ponto é sempre milhar;
    sem vírgula, um único ponto seguido de 1 ou 2 dígitos é decimal (ex.: "150.5")."""
    if texto is None:
        return None
    if isinstance(texto, (Decimal, int, float)):
        return Decimal(str(texto))
    texto = str(texto).replace("R$", "").replace(" ", "").replace(" ", "").strip()
    if not texto:
        return None
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif texto.count(".") == 1 and len(texto.split(".")[1]) in (1, 2):
        pass
    else:
        texto = texto.replace(".", "")
    try:
        return Decimal(texto)
    except InvalidOperation:
        raise ValidationError("Informe um valor válido (ex.: 1.500,00).", code="invalid")


class EntradaMoeda(forms.TextInput):
    def format_value(self, value):
        if value is None or value == "":
            return None
        if isinstance(value, str):
            # Valor que voltou do POST (formulário com erro): mostra como a pessoa digitou.
            try:
                return formatar_moeda(ler_moeda(value))
            except ValidationError:
                return value
        return formatar_moeda(value)


class CampoMoeda(forms.DecimalField):
    """DecimalField que lê/mostra "150.000,00" (ver ler_moeda/formatar_moeda)."""

    widget = EntradaMoeda

    def to_python(self, value):
        if value in self.empty_values:
            return None
        valor = ler_moeda(value)
        if valor is None:
            return None
        if not valor.is_finite():
            raise ValidationError(self.error_messages["invalid"], code="invalid")
        return valor

    def widget_attrs(self, widget):
        # Nada de step/min/max do DecimalField: não é mais <input type="number">.
        return forms.Field.widget_attrs(self, widget)


def eh_campo_dinheiro(nome, campo):
    return (
        isinstance(campo, forms.DecimalField)
        and campo.decimal_places == 2
        and "percentual" not in nome
    )


def preparar_campos_moeda(form):
    """Troca os DecimalField de dinheiro do form por CampoMoeda, mantendo rótulo, obrigatoriedade,
    limites (validators) e classes CSS. Percentual fica como está."""
    for nome, campo in form.fields.items():
        if not eh_campo_dinheiro(nome, campo) or isinstance(campo, CampoMoeda):
            continue
        attrs = {
            chave: valor
            for chave, valor in campo.widget.attrs.items()
            if chave not in ("step", "min", "max", "type")
        }
        campo.__class__ = CampoMoeda
        campo.widget = EntradaMoeda(attrs=attrs)
        campo.widget.is_required = campo.required
        classes = campo.widget.attrs.get("class", "")
        campo.widget.attrs["class"] = (classes + " campo-moeda").strip()
        campo.widget.attrs.setdefault("inputmode", "decimal")
        campo.widget.attrs.setdefault("autocomplete", "off")
        campo.widget.attrs.setdefault("placeholder", "0,00")


def instalar_milhar_em_decimais():
    """Faz {{ valor }} de Decimal sair com ponto de milhar nas telas (150.000,00), sem mexer em
    inteiros. O Django não tem opção de agrupar só um tipo, então envolvemos a função que a
    renderização de template usa (django.template.base.localize). Respeita
    {% localize off %}/|unlocalize (use_l10n=False). Só a renderização de template é afetada."""
    import django.template.base as template_base
    from django.utils import formats

    original = formats.localize
    if getattr(template_base.localize, "_smedim_milhar", False):
        return

    def localize(value, use_l10n=None):
        if isinstance(value, Decimal) and use_l10n is not False and value.is_finite():
            return formats.number_format(value, use_l10n=use_l10n, force_grouping=True)
        return original(value, use_l10n=use_l10n)

    localize._smedim_milhar = True
    template_base.localize = localize
