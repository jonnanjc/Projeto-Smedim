"""
Convênios que o profissional atende (pedido do cliente, 2026-10-14 — ver
cadastros.models.Profissional.convenios_atendidos): no agendamento só aparecem os convênios
que ele atende e que têm preço para algum procedimento dele, e só os procedimentos com preço
no convênio escolhido. Também cobre a trava de pacote preso a profissional no cadastro.
"""
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.urls import reverse

from cadastros.forms import PacoteForm, PacoteItemFormSet
from cadastros.models import Convenio, Pacote, Procedimento, Profissional, TabelaPrecoConvenio

from .models import Agendamento, AgendamentoItem
from .tests import AgendaTestBase, Usuario


class ConveniosDoProfissionalTests(AgendaTestBase):
    def setUp(self):
        super().setUp()
        # Acupuntura tem preço no Bradesco, mas não no ASPEB.
        self.bradesco = Convenio.objects.create(nome="Bradesco")
        self.aspeb = Convenio.objects.create(nome="ASPEB")
        self.acupuntura = Procedimento.objects.create(nome="Acupuntura")
        self.acupuntura.profissionais_habilitados.add(self.profissional)
        TabelaPrecoConvenio.objects.create(convenio=self.bradesco, procedimento=self.acupuntura, valor=Decimal("70"))
        self.profissional.convenios_atendidos.add(self.bradesco, self.aspeb, self.convenio)
        self.usuario = Usuario.objects.create_user(
            username="recepcao_conv", password="senha-teste-123", exigir_troca_senha=False
        )
        for codename in ("agenda_criar", "agenda_editar"):
            self.usuario.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(self.usuario)

    def _api(self, **params):
        url = reverse("agenda:procedimentos_do_profissional", args=[self.profissional.pk])
        return self.client.get(url, params).json()

    def _novo(self, convenio, procedimento):
        return self.client.post(
            reverse("agenda:agendamento_novo"),
            {
                "paciente": str(self.paciente.pk),
                "profissional": str(self.profissional.pk),
                "forma_atendimento": "CONVENIO",
                "convenio": str(convenio.pk),
                "data": "2026-07-27",
                "turno": "MANHA",
                "itens-TOTAL_FORMS": "1",
                "itens-INITIAL_FORMS": "0",
                "itens-MIN_NUM_FORMS": "1",
                "itens-MAX_NUM_FORMS": "1000",
                "itens-0-procedimento": str(procedimento.pk),
                "itens-0-quantidade": "1",
            },
        )

    def test_so_lista_convenios_que_atende_e_com_preco(self):
        nomes = [c["nome"] for c in self._api()["convenios"]]
        # ASPEB fica de fora: ele atende, mas nenhum procedimento dele tem preço lá.
        self.assertEqual(nomes, ["Bradesco", "Convênio Teste"])
        self.profissional.convenios_atendidos.remove(self.bradesco)
        self.assertEqual([c["nome"] for c in self._api()["convenios"]], ["Convênio Teste"])

    def test_procedimentos_so_do_convenio_escolhido(self):
        dados = self._api(forma_atendimento="CONVENIO", convenio=self.bradesco.pk)
        self.assertEqual([p["nome"] for p in dados["procedimentos"]], ["Acupuntura"])

    def test_preco_especifico_de_outro_profissional_nao_conta(self):
        outro = Profissional.objects.create(nome="Outro", registro_profissional="X", percentual_desconto=Decimal("30"))
        TabelaPrecoConvenio.objects.create(
            convenio=self.aspeb, procedimento=self.acupuntura, profissional=outro, valor=Decimal("90")
        )
        self.assertNotIn("ASPEB", [c["nome"] for c in self._api()["convenios"]])
        self.assertEqual(self._api(forma_atendimento="CONVENIO", convenio=self.aspeb.pk)["procedimentos"], [])

    def test_agenda_convenio_diferente_do_cadastro_do_paciente(self):
        # Paciente sem convênio cadastrado: antes só dava particular.
        resposta = self._novo(self.bradesco, self.acupuntura)
        self.assertEqual(resposta.status_code, 302)
        agendamento = Agendamento.objects.get()
        self.assertEqual(agendamento.convenio, self.bradesco)
        self.assertEqual(AgendamentoItem.objects.get().procedimento, self.acupuntura)

    def test_bloqueia_convenio_que_o_profissional_nao_atende(self):
        self.profissional.convenios_atendidos.remove(self.bradesco)
        resposta = self._novo(self.bradesco, self.acupuntura)
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "não atende o convênio")
        self.assertFalse(Agendamento.objects.exists())

    def test_bloqueia_procedimento_sem_preco_no_convenio(self):
        resposta = self._novo(self.aspeb, self.acupuntura)
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(Agendamento.objects.exists())


class PacotePresoAoProfissionalTests(AgendaTestBase):
    def _validar(self, profissional, procedimento):
        dados = {
            "nome": "Combo",
            "profissional": str(profissional.pk) if profissional else "",
            "valor": "200",
            "ativo": "on",
            "itens-TOTAL_FORMS": "1",
            "itens-INITIAL_FORMS": "0",
            "itens-MIN_NUM_FORMS": "0",
            "itens-MAX_NUM_FORMS": "1000",
            "itens-0-procedimento": str(procedimento.pk),
            "itens-0-quantidade": "1",
        }
        form = PacoteForm(dados, instance=Pacote())
        formset = PacoteItemFormSet(dados, instance=form.instance)
        return form.is_valid() and formset.is_valid(), formset

    def test_procedimento_nao_habilitado_bloqueia(self):
        fora = Procedimento.objects.create(nome="Eletro", valor_particular=Decimal("50"))
        valido, formset = self._validar(self.profissional, fora)
        self.assertFalse(valido)
        self.assertIn("não está habilitado", str(formset.non_form_errors()))

    def test_procedimento_habilitado_passa(self):
        self.assertTrue(self._validar(self.profissional, self.consulta)[0])

    def test_qualquer_profissional_passa(self):
        fora = Procedimento.objects.create(nome="Eletro", valor_particular=Decimal("50"))
        self.assertTrue(self._validar(None, fora)[0])
