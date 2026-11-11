"""
Desconto no registro do pagamento (pedido da clínica, 15/10/2026): valor cobrado = cadastro −
desconto, motivo obrigatório; para quem cobra valor fixo no particular, o repasse acordado é
informado na hora. Também: a tabela "Valores fixos no particular" do cadastro do profissional
só oferece os procedimentos habilitados para ele.
"""
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.urls import reverse

from cadastros.forms import ValorFixoProfissionalFormSet
from cadastros.models import Procedimento, ValorFixoProfissional

from .models import Agendamento, AgendamentoItem, PagamentoParticular
from .tests import AgendaTestBase, Usuario


class DescontoBase(AgendaTestBase):
    def setUp(self):
        super().setUp()
        self.usuario = Usuario.objects.create_user(
            username="financeiro_desconto", password="senha-teste-123", exigir_troca_senha=False
        )
        self.usuario.user_permissions.add(Permission.objects.get(codename="fila_autorizar_financeiro"))
        self.client.force_login(self.usuario)

    def _atendimento(self, *itens):
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        for procedimento in itens:
            AgendamentoItem.objects.create(agendamento=agendamento, procedimento=procedimento, quantidade=1)
        return agendamento

    def _pagar(self, agendamento, dados):
        return self.client.post(reverse("agenda:agendamento_confirmar_pagamento", args=[agendamento.pk]), dados)


class DescontoPercentualTests(DescontoBase):
    def test_desconto_reduz_valor_e_reparte_nos_itens(self):
        agendamento = self._atendimento(self.consulta, self.exame)  # 150 + 80 = 230
        resposta = self._pagar(agendamento, {
            "forma_pagamento_1": "PIX", "desconto": "23.00", "motivo_desconto": "Dr. autorizou",
        })
        self.assertEqual(resposta.status_code, 302)
        [pagamento] = PagamentoParticular.objects.filter(agendamento=agendamento)
        self.assertEqual(pagamento.valor, Decimal("207.00"))
        # Repasse por percentual (30% retido) sobre o valor com desconto.
        self.assertEqual(pagamento.valor_repasse, Decimal("144.90"))
        valores = sorted(i.valor_cobrado for i in agendamento.itens.all())
        self.assertEqual(valores, [Decimal("72.00"), Decimal("135.00")])
        agendamento.refresh_from_db()
        self.assertEqual((agendamento.desconto, agendamento.motivo_desconto), (Decimal("23.00"), "Dr. autorizou"))
        self.assertEqual(agendamento.status, Agendamento.Status.AUTORIZADO)

    def test_desconto_exige_motivo(self):
        agendamento = self._atendimento(self.consulta)
        resposta = self._pagar(agendamento, {"forma_pagamento_1": "PIX", "desconto": "10.00"})
        self.assertContains(resposta, "Informe o motivo do desconto")
        self.assertFalse(PagamentoParticular.objects.exists())

    def test_desconto_do_valor_todo_pede_cortesia(self):
        agendamento = self._atendimento(self.consulta)
        resposta = self._pagar(agendamento, {"forma_pagamento_1": "PIX", "desconto": "150.00", "motivo_desconto": "x"})
        self.assertContains(resposta, "marque cortesia")

    def test_pagamento_dividido_soma_o_valor_com_desconto(self):
        agendamento = self._atendimento(self.consulta)
        resposta = self._pagar(agendamento, {
            "desconto": "50.00", "motivo_desconto": "Dr. autorizou",
            "forma_pagamento_1": "DINHEIRO", "valor_1": "50.00", "forma_pagamento_2": "PIX", "valor_2": "50.00",
        })
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(
            sum(p.valor for p in PagamentoParticular.objects.filter(agendamento=agendamento)), Decimal("100.00")
        )

    def test_percentual_nao_mostra_repasse_acordado(self):
        agendamento = self._atendimento(self.consulta)
        resposta = self.client.get(reverse("agenda:agendamento_confirmar_pagamento", args=[agendamento.pk]))
        self.assertContains(resposta, "id_desconto")
        self.assertNotContains(resposta, "id_repasse_acordado")


class DescontoValorFixoTests(DescontoBase):
    def setUp(self):
        super().setUp()
        self.profissional.cobra_valor_fixo_particular = True
        self.profissional.save()
        ValorFixoProfissional.objects.create(
            profissional=self.profissional, procedimento=self.consulta,
            valor_cobrado=Decimal("200.00"), valor_repasse=Decimal("150.00"),
        )

    def test_mostra_repasse_padrao_como_referencia(self):
        agendamento = self._atendimento(self.consulta)
        resposta = self.client.get(reverse("agenda:agendamento_confirmar_pagamento", args=[agendamento.pk]))
        self.assertContains(resposta, "id_repasse_acordado")
        self.assertContains(resposta, "R$ 150")

    def test_desconto_exige_repasse_acordado(self):
        agendamento = self._atendimento(self.consulta)
        resposta = self._pagar(agendamento, {"forma_pagamento_1": "PIX", "desconto": "40.00", "motivo_desconto": "Dr."})
        self.assertContains(resposta, "informe quanto ficou acordado")
        self.assertFalse(PagamentoParticular.objects.exists())

    def test_repasse_acordado_vale_no_pagamento(self):
        agendamento = self._atendimento(self.consulta)
        resposta = self._pagar(agendamento, {
            "forma_pagamento_1": "PIX", "desconto": "40.00", "motivo_desconto": "Dr. autorizou",
            "repasse_acordado": "120.00",
        })
        self.assertEqual(resposta.status_code, 302)
        [pagamento] = PagamentoParticular.objects.filter(agendamento=agendamento)
        self.assertEqual(pagamento.valor, Decimal("160.00"))
        self.assertEqual(pagamento.valor_repasse, Decimal("120.00"))

    def test_repasse_acordado_maior_que_valor_cobrado_bloqueia(self):
        agendamento = self._atendimento(self.consulta)
        resposta = self._pagar(agendamento, {
            "forma_pagamento_1": "PIX", "desconto": "100.00", "motivo_desconto": "Dr.", "repasse_acordado": "150.00",
        })
        self.assertContains(resposta, "não pode ser maior que o valor cobrado com desconto")

    def test_sem_desconto_repasse_continua_o_da_tabela(self):
        agendamento = self._atendimento(self.consulta)
        self._pagar(agendamento, {"forma_pagamento_1": "PIX", "repasse_acordado": "1.00"})
        [pagamento] = PagamentoParticular.objects.filter(agendamento=agendamento)
        self.assertEqual(pagamento.valor_repasse, Decimal("150.00"))


class ValoresFixosSoProcedimentosDoProfissionalTests(AgendaTestBase):
    def test_lista_so_os_habilitados(self):
        fora = Procedimento.objects.create(nome="Procedimento de outro profissional", valor_particular=Decimal("10"))
        formset = ValorFixoProfissionalFormSet(instance=self.profissional)
        opcoes = set(formset.empty_form.fields["procedimento"].queryset)
        self.assertEqual(opcoes, {self.consulta, self.exame})
        self.assertNotIn(fora, opcoes)

    def test_profissional_novo_nao_lista_nada(self):
        from cadastros.models import Profissional

        formset = ValorFixoProfissionalFormSet(instance=Profissional())
        self.assertFalse(formset.empty_form.fields["procedimento"].queryset.exists())
