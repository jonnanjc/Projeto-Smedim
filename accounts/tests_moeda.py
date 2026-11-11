"""Dinheiro com ponto de milhar (150.000,00) — pedido da clínica, 06/11/2026. Ver accounts/moeda.py."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.template import Context, Template
from django.test import SimpleTestCase, TestCase

from .moeda import formatar_moeda, ler_moeda


class FormatarELerTests(SimpleTestCase):
    def test_formatar(self):
        self.assertEqual(formatar_moeda(Decimal("150000")), "150.000,00")
        self.assertEqual(formatar_moeda(Decimal("1234567.8")), "1.234.567,80")
        self.assertEqual(formatar_moeda(Decimal("0.5")), "0,50")
        self.assertEqual(formatar_moeda(Decimal("-1500")), "-1.500,00")
        self.assertEqual(formatar_moeda(None), "")

    def test_ler_aceita_os_jeitos_comuns_de_digitar(self):
        casos = {
            "150.000,00": "150000.00",
            "150000,00": "150000.00",
            "150000.00": "150000.00",
            "1.500": "1500",
            "150.5": "150.5",
            "R$ 10,50": "10.50",
            "1.234.567,8": "1234567.8",
            "0,99": "0.99",
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(ler_moeda(texto), Decimal(esperado))
        self.assertIsNone(ler_moeda(""))
        with self.assertRaises(ValidationError):
            ler_moeda("abc")


class TelaTests(SimpleTestCase):
    def test_decimal_ganha_ponto_de_milhar_e_inteiro_nao(self):
        html = Template(
            '{{ valor }}|{{ codigo }}|{{ percentual }}|{% load l10n %}{{ valor|unlocalize }}|{{ valor|stringformat:"s" }}'
        ).render(
            Context({"valor": Decimal("150000.00"), "codigo": 37123, "percentual": Decimal("30.00")})
        )
        # Código/ano/contagem (inteiro) não muda — vai em link e JavaScript.
        self.assertEqual(html, "150.000,00|37123|30,00|150000.00|150000.00")


class CampoDeValorTests(TestCase):
    def _form(self, **dados):
        from financeiro.forms import MovimentacaoCaixaForm

        base = {"tipo": "ENTRADA", "data": "2026-11-06", "descricao": "Teste"}
        return MovimentacaoCaixaForm(data={**base, **dados})

    def test_aceita_valor_com_ponto_de_milhar(self):
        form = self._form(valor="150.000,00")
        form.is_valid()
        self.assertNotIn("valor", form.errors)
        self.assertEqual(form.cleaned_data["valor"], Decimal("150000.00"))

    def test_campo_mostra_valor_com_ponto_de_milhar(self):
        from financeiro.forms import MovimentacaoCaixaForm
        from financeiro.models import MovimentacaoCaixa

        html = str(MovimentacaoCaixaForm(instance=MovimentacaoCaixa(valor=Decimal("150000.00")))["valor"])
        self.assertIn('value="150.000,00"', html)
        self.assertIn('type="text"', html)
        self.assertIn("campo-moeda", html)

    def test_valor_invalido_da_erro(self):
        self.assertIn("valor", self._form(valor="abc").errors)

    def test_percentual_continua_numero(self):
        from cadastros.forms import ProfissionalForm

        html = str(ProfissionalForm()["percentual_desconto"])
        self.assertIn('type="number"', html)
        self.assertNotIn("campo-moeda", html)
