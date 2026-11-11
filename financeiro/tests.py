"""
Testes do módulo de Lote de faturamento (convênio) — ver claude/faturamento-lote-convenio.md.
Cobrem as regras de negócio que o documento marca como decisão do cliente (elegibilidade,
trava de dupla inclusão, efeito automático em Agendamento.convenio_recebido, transição de
status do lote) e a integração com Contas a receber (trava contra baixa duplicada).

Roda numa base de dados de teste isolada (padrão do Django TestCase, em memória para
SQLite) — não toca em db.sqlite3.
"""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from agenda.models import Agendamento, AgendamentoItem, GuiaConvenio, PagamentoParticular
from cadastros.models import Convenio, Paciente, Procedimento, Profissional

from .models import LoteFaturamento, MovimentacaoCaixa, RepassePagamento, RetornoLoteItem

Usuario = get_user_model()


class LoteFaturamentoTestBase(TestCase):
    """Fixtures comuns a todos os testes deste módulo."""

    def setUp(self):
        self.convenio = Convenio.objects.create(nome="Convênio Teste")
        self.outro_convenio = Convenio.objects.create(nome="Outro Convênio Teste")
        self.paciente = Paciente.objects.create(nome_completo="Paciente Teste")
        self.profissional = Profissional.objects.create(
            nome="Profissional Teste", registro_profissional="CRM-TESTE", percentual_desconto=Decimal("30.00")
        )
        self.procedimento = Procedimento.objects.create(nome="Consulta Teste", eh_consulta=True)
        self._proximo_horario = timezone.make_aware(datetime(2026, 7, 20, 8, 0))  # segunda-feira

    def _criar_agendamento(
        self,
        convenio=None,
        valor_convenio=Decimal("100.00"),
        retorno_de=None,
        status=Agendamento.Status.REALIZADO,
    ):
        """'retorno_de', quando informado, é o Agendamento (não o item) de origem — mesma
        assinatura de antes, pra não precisar mudar todo teste que já chama isto; por baixo,
        liga ao único AgendamentoItem daquele agendamento (sempre 1 item por fixture aqui)."""
        inicio = self._proximo_horario
        self._proximo_horario += timedelta(hours=1)
        agendamento = Agendamento.objects.create(
            paciente=self.paciente,
            profissional=self.profissional,
            forma_atendimento=Agendamento.FormaAtendimento.CONVENIO,
            convenio=convenio or self.convenio,
            data_hora_inicio=inicio,
            data_hora_fim=inicio + timedelta(minutes=30),
            status=status,
        )
        AgendamentoItem.objects.create(
            agendamento=agendamento,
            procedimento=self.procedimento,
            valor_convenio=valor_convenio,
            retorno_de=retorno_de.itens.first() if retorno_de else None,
        )
        # Agendamento.valor_convenio é uma coluna própria, congelada como a SOMA dos itens —
        # não sincroniza sozinha (na tela real isso acontece em
        # agenda.views.agendamento_mudar_status ao marcar Realizado, ver comentário em
        # agenda.models.AgendamentoItem.valor_convenio). Fixture sempre cria 1 item só, então
        # espelha aqui o mesmo total que a view calcularia.
        agendamento.valor_convenio = valor_convenio
        agendamento.save(update_fields=["valor_convenio"])
        return agendamento

    def _criar_lote(self, convenio=None, valor_total_enviado=Decimal("100.00")):
        return LoteFaturamento.objects.create(
            convenio=convenio or self.convenio,
            data_envio=timezone.localdate(),
            valor_total_enviado=valor_total_enviado,
        )


class ElegibilidadeTests(LoteFaturamentoTestBase):
    def test_agendamento_realizado_convenio_e_elegivel(self):
        agendamento = self._criar_agendamento()
        self.assertIn(agendamento, LoteFaturamento.agendamentos_elegiveis(self.convenio))

    def test_retorno_gratis_nunca_e_elegivel(self):
        original = self._criar_agendamento()
        retorno = self._criar_agendamento(valor_convenio=Decimal("0.00"), retorno_de=original)
        self.assertNotIn(retorno, LoteFaturamento.agendamentos_elegiveis(self.convenio))

    def test_agendamento_de_outro_convenio_nao_aparece(self):
        agendamento = self._criar_agendamento(convenio=self.outro_convenio)
        self.assertNotIn(agendamento, LoteFaturamento.agendamentos_elegiveis(self.convenio))

    def test_agendamento_autorizado_ja_e_elegivel(self):
        # Autorizado já conta como produção (Agendamento.STATUS_PRODUCAO) — não depende do
        # profissional marcar "atendido".
        agendamento = self._criar_agendamento(status=Agendamento.Status.AUTORIZADO)
        self.assertIn(agendamento, LoteFaturamento.agendamentos_elegiveis(self.convenio))

    def test_agendamento_ainda_nao_autorizado_nao_e_elegivel(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.CONFIRMADO)
        self.assertNotIn(agendamento, LoteFaturamento.agendamentos_elegiveis(self.convenio))


class TravaDuplaInclusaoTests(LoteFaturamentoTestBase):
    def test_agendamento_em_lote_enviado_fica_travado(self):
        agendamento = self._criar_agendamento()
        lote = self._criar_lote()
        lote.agendamentos.add(agendamento)

        self.assertEqual(LoteFaturamento.travando(agendamento), lote)
        self.assertNotIn(agendamento, LoteFaturamento.agendamentos_elegiveis(self.convenio))

    def test_excluir_lote_pk_libera_o_proprio_lote(self):
        agendamento = self._criar_agendamento()
        lote = self._criar_lote()
        lote.agendamentos.add(agendamento)

        self.assertIsNone(LoteFaturamento.travando(agendamento, excluir_lote_pk=lote.pk))
        self.assertIn(agendamento, LoteFaturamento.agendamentos_elegiveis(self.convenio, excluir_lote_pk=lote.pk))

    def test_apos_retorno_lancado_agendamento_fica_livre(self):
        agendamento = self._criar_agendamento()
        lote = self._criar_lote()
        lote.agendamentos.add(agendamento)
        RetornoLoteItem.objects.create(
            lote=lote, agendamento=agendamento, valor_pago=Decimal("100.00"), data_retorno=timezone.localdate()
        )

        self.assertIsNone(LoteFaturamento.travando(agendamento))


class RetornoLoteItemTests(LoteFaturamentoTestBase):
    def setUp(self):
        super().setUp()
        self.agendamento = self._criar_agendamento(valor_convenio=Decimal("150.00"))
        self.lote = self._criar_lote(valor_total_enviado=Decimal("150.00"))
        self.lote.agendamentos.add(self.agendamento)

    def test_valor_pago_marca_convenio_recebido(self):
        RetornoLoteItem.objects.create(
            lote=self.lote, agendamento=self.agendamento, valor_pago=Decimal("150.00"), data_retorno=timezone.localdate()
        )
        self.agendamento.refresh_from_db()
        self.assertTrue(self.agendamento.convenio_recebido)
        self.assertIsNotNone(self.agendamento.convenio_recebido_em)

    def test_so_glosado_nao_marca_convenio_recebido(self):
        RetornoLoteItem.objects.create(
            lote=self.lote,
            agendamento=self.agendamento,
            valor_pago=Decimal("0.00"),
            valor_glosado=Decimal("150.00"),
            motivo_glosa="Glosa de teste",
            data_retorno=timezone.localdate(),
        )
        self.agendamento.refresh_from_db()
        self.assertFalse(self.agendamento.convenio_recebido)

    def test_lote_fecha_quando_todos_os_itens_tem_retorno(self):
        self.assertEqual(self.lote.status, LoteFaturamento.Status.ENVIADO)
        RetornoLoteItem.objects.create(
            lote=self.lote, agendamento=self.agendamento, valor_pago=Decimal("150.00"), data_retorno=timezone.localdate()
        )
        self.lote.refresh_from_db()
        self.assertEqual(self.lote.status, LoteFaturamento.Status.FECHADO)

    def test_lote_fica_parcial_quando_falta_item_sem_retorno(self):
        outro_agendamento = self._criar_agendamento(valor_convenio=Decimal("80.00"))
        self.lote.agendamentos.add(outro_agendamento)

        RetornoLoteItem.objects.create(
            lote=self.lote, agendamento=self.agendamento, valor_pago=Decimal("150.00"), data_retorno=timezone.localdate()
        )
        self.lote.refresh_from_db()
        self.assertEqual(self.lote.status, LoteFaturamento.Status.RETORNO_PARCIAL)

    def test_unico_retorno_por_agendamento_no_lote(self):
        RetornoLoteItem.objects.create(
            lote=self.lote, agendamento=self.agendamento, valor_pago=Decimal("150.00"), data_retorno=timezone.localdate()
        )
        with self.assertRaises(IntegrityError):
            RetornoLoteItem.objects.create(
                lote=self.lote, agendamento=self.agendamento, valor_pago=Decimal("10.00"), data_retorno=timezone.localdate()
            )

    def test_diferenca_produzido_zero_quando_pago_bate_com_produzido(self):
        RetornoLoteItem.objects.create(
            lote=self.lote, agendamento=self.agendamento, valor_pago=Decimal("150.00"), data_retorno=timezone.localdate()
        )
        retorno = RetornoLoteItem.objects.get(lote=self.lote, agendamento=self.agendamento)
        self.assertEqual(retorno.diferenca_produzido, Decimal("0.00"))
        self.lote.refresh_from_db()
        self.assertEqual(self.lote.diferenca_enviado, Decimal("0.00"))

    def test_diferenca_produzido_sinaliza_pago_menor_sem_glosa_lancada(self):
        """Caso relatado pelo cliente em 2026-07-27: deu baixa com 'Pago' menor que o produzido
        sem preencher 'Glosado' — a diferença tem que ficar sinalizada, não sumir sozinha."""
        RetornoLoteItem.objects.create(
            lote=self.lote, agendamento=self.agendamento, valor_pago=Decimal("100.00"), data_retorno=timezone.localdate()
        )
        retorno = RetornoLoteItem.objects.get(lote=self.lote, agendamento=self.agendamento)
        self.assertEqual(retorno.diferenca_produzido, Decimal("-50.00"))
        self.assertEqual(retorno.diferenca_absoluta, Decimal("50.00"))
        self.lote.refresh_from_db()
        self.assertEqual(self.lote.diferenca_enviado, Decimal("-50.00"))

    def test_diferenca_enviado_sinaliza_mesmo_quando_produzido_ja_bate(self):
        """Regressão: um lote real (Hapvida #13546) tinha valor_total_enviado (250) diferente
        do Produzido (200) por arredondamento no cabeçalho — SEM relação com o retorno em si —
        e Pago (200) batia certinho com o Produzido (200), então comparar diferenca_enviado
        contra Produzido não sinalizava nada, apesar de faltar R$ 50 explicar frente ao que foi
        de fato enviado ao convênio. diferenca_enviado tem que comparar contra
        valor_total_enviado, não contra valor_produzido, senão esse caso passa batido."""
        agendamento = self._criar_agendamento(valor_convenio=Decimal("150.00"))
        lote = self._criar_lote(valor_total_enviado=Decimal("250.00"))
        lote.agendamentos.add(agendamento)
        RetornoLoteItem.objects.create(
            lote=lote, agendamento=agendamento, valor_pago=Decimal("150.00"), data_retorno=timezone.localdate()
        )
        lote.refresh_from_db()
        self.assertEqual(lote.valor_produzido, Decimal("150.00"))
        self.assertEqual(lote.valor_pago, Decimal("150.00"))
        # Pago bate 100% com Produzido — mas ainda faltam R$ 100 pra explicar o Enviado.
        self.assertEqual(lote.diferenca_enviado, Decimal("-100.00"))

    def test_diferenca_produzido_zera_ao_corrigir_preenchendo_glosado(self):
        RetornoLoteItem.objects.create(
            lote=self.lote, agendamento=self.agendamento, valor_pago=Decimal("100.00"), data_retorno=timezone.localdate()
        )
        retorno = RetornoLoteItem.objects.get(lote=self.lote, agendamento=self.agendamento)
        retorno.valor_glosado = Decimal("50.00")
        retorno.save()
        retorno.refresh_from_db()
        self.assertEqual(retorno.diferenca_produzido, Decimal("0.00"))


class ContasReceberIntegracaoTests(LoteFaturamentoTestBase):
    """Trava contra baixa duplicada pelo caminho antigo (ver claude/faturamento-lote-convenio.md)."""

    def setUp(self):
        super().setUp()
        self.usuario = Usuario.objects.create_user(
            username="financeiro_teste", password="senha-teste-123", exigir_troca_senha=False
        )
        codenames = [
            "financeiro_ver_contas_receber",
            "financeiro_conciliar",
            "financeiro_lancar",
            "faturamento_lote_lancar",
            "faturamento_lote_visualizar",
            "faturamento_retorno_lancar",
        ]
        self.usuario.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
        self.client.force_login(self.usuario)

    def test_atendimento_travado_nao_mostra_botao_marcar_recebido(self):
        agendamento = self._criar_agendamento(valor_convenio=Decimal("100.00"))
        lote = self._criar_lote()
        lote.agendamentos.add(agendamento)

        resposta = self.client.get(reverse("financeiro:contas_receber_lista"))
        self.assertContains(resposta, "Está no lote")
        self.assertNotContains(resposta, "Marcar como recebido")

    def test_marcar_recebido_bloqueado_no_servidor_quando_travado(self):
        agendamento = self._criar_agendamento(valor_convenio=Decimal("100.00"))
        lote = self._criar_lote()
        lote.agendamentos.add(agendamento)

        self.client.post(reverse("financeiro:contas_receber_marcar_recebido", args=[agendamento.pk]))
        agendamento.refresh_from_db()
        self.assertFalse(agendamento.convenio_recebido)

    def test_atendimento_livre_continua_podendo_ser_marcado(self):
        agendamento = self._criar_agendamento(valor_convenio=Decimal("100.00"))
        self.client.post(reverse("financeiro:contas_receber_marcar_recebido", args=[agendamento.pk]))
        agendamento.refresh_from_db()
        self.assertTrue(agendamento.convenio_recebido)


class LoteViewsTests(LoteFaturamentoTestBase):
    def setUp(self):
        super().setUp()
        self.usuario = Usuario.objects.create_user(
            username="financeiro_teste2", password="senha-teste-123", exigir_troca_senha=False
        )
        codenames = [
            "faturamento_lote_lancar",
            "faturamento_lote_visualizar",
            "faturamento_comparativo_visualizar",
            "faturamento_retorno_lancar",
        ]
        self.usuario.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
        self.client.force_login(self.usuario)

    def test_criar_lote_via_view(self):
        agendamento = self._criar_agendamento(valor_convenio=Decimal("200.00"))
        resposta = self.client.post(
            reverse("financeiro:lote_novo"),
            {
                "convenio": self.convenio.pk,
                "data_envio": timezone.localdate().isoformat(),
                "numero_lote": "L-001",
                "valor_total_enviado": "200.00",
                "agendamentos": [str(agendamento.pk)],
            },
        )
        self.assertEqual(LoteFaturamento.objects.count(), 1)
        lote = LoteFaturamento.objects.get()
        self.assertEqual(list(lote.agendamentos.all()), [agendamento])
        self.assertRedirects(resposta, reverse("financeiro:lote_detalhe", args=[lote.pk]))

    def test_nao_deixa_incluir_agendamento_ja_travado_em_outro_lote(self):
        agendamento = self._criar_agendamento(valor_convenio=Decimal("200.00"))
        self._criar_lote().agendamentos.add(agendamento)

        resposta = self.client.post(
            reverse("financeiro:lote_novo"),
            {
                "convenio": self.convenio.pk,
                "data_envio": timezone.localdate().isoformat(),
                "numero_lote": "L-002",
                "valor_total_enviado": "200.00",
                "agendamentos": [str(agendamento.pk)],
            },
        )
        # Sem nenhum item elegível marcado de verdade (o único candidato já estava travado),
        # a view rejeita a criação em vez de gerar um lote vazio.
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(LoteFaturamento.objects.count(), 1)

    def test_retorno_lancar_marca_recebido_e_fecha_lote(self):
        agendamento = self._criar_agendamento(valor_convenio=Decimal("100.00"))
        lote = self._criar_lote()
        lote.agendamentos.add(agendamento)

        resposta = self.client.post(
            reverse("financeiro:retorno_lancar", args=[lote.pk, agendamento.pk]),
            {"valor_pago": "100.00", "valor_glosado": "0.00", "motivo_glosa": "", "data_retorno": timezone.localdate().isoformat()},
        )
        self.assertRedirects(resposta, reverse("financeiro:lote_detalhe", args=[lote.pk]))
        agendamento.refresh_from_db()
        lote.refresh_from_db()
        self.assertTrue(agendamento.convenio_recebido)
        self.assertEqual(lote.status, LoteFaturamento.Status.FECHADO)

    def test_dar_baixa_lote_lanca_retorno_para_varios_itens_de_uma_vez(self):
        """Pedido do cliente: dar baixa no lote inteiro numa tela só, não item por item."""
        pago_total = self._criar_agendamento(valor_convenio=Decimal("100.00"))
        pago_parcial = self._criar_agendamento(valor_convenio=Decimal("100.00"))
        lote = self._criar_lote(valor_total_enviado=Decimal("200.00"))
        lote.agendamentos.add(pago_total, pago_parcial)
        hoje = timezone.localdate().isoformat()

        resposta = self.client.post(
            reverse("financeiro:lote_dar_baixa", args=[lote.pk]),
            {
                f"ag{pago_total.pk}-valor_pago": "100.00",
                f"ag{pago_total.pk}-valor_glosado": "0.00",
                f"ag{pago_total.pk}-motivo_glosa": "",
                f"ag{pago_total.pk}-data_retorno": hoje,
                f"ag{pago_parcial.pk}-valor_pago": "60.00",
                f"ag{pago_parcial.pk}-valor_glosado": "40.00",
                f"ag{pago_parcial.pk}-motivo_glosa": "Glosa parcial de teste",
                f"ag{pago_parcial.pk}-data_retorno": hoje,
            },
        )
        self.assertRedirects(resposta, reverse("financeiro:lote_detalhe", args=[lote.pk]))
        self.assertEqual(RetornoLoteItem.objects.filter(lote=lote).count(), 2)
        pago_total.refresh_from_db()
        pago_parcial.refresh_from_db()
        lote.refresh_from_db()
        self.assertTrue(pago_total.convenio_recebido)
        self.assertTrue(pago_parcial.convenio_recebido)
        self.assertEqual(lote.status, LoteFaturamento.Status.FECHADO)

    def test_dar_baixa_lote_ignora_linha_zerada_e_deixa_atendimento_pendente(self):
        """Deixar uma linha em 0,00/0,00 não lança retorno pra ela — permite dar baixa só numa
        parte do lote nesta passada, sem forçar glosa total no que ainda não voltou."""
        resolvido = self._criar_agendamento(valor_convenio=Decimal("100.00"))
        ainda_sem_retorno = self._criar_agendamento(valor_convenio=Decimal("80.00"))
        lote = self._criar_lote(valor_total_enviado=Decimal("180.00"))
        lote.agendamentos.add(resolvido, ainda_sem_retorno)
        hoje = timezone.localdate().isoformat()

        resposta = self.client.post(
            reverse("financeiro:lote_dar_baixa", args=[lote.pk]),
            {
                f"ag{resolvido.pk}-valor_pago": "100.00",
                f"ag{resolvido.pk}-valor_glosado": "0.00",
                f"ag{resolvido.pk}-motivo_glosa": "",
                f"ag{resolvido.pk}-data_retorno": hoje,
                f"ag{ainda_sem_retorno.pk}-valor_pago": "0.00",
                f"ag{ainda_sem_retorno.pk}-valor_glosado": "0.00",
                f"ag{ainda_sem_retorno.pk}-motivo_glosa": "",
                f"ag{ainda_sem_retorno.pk}-data_retorno": hoje,
            },
        )
        self.assertRedirects(resposta, reverse("financeiro:lote_detalhe", args=[lote.pk]))
        self.assertEqual(RetornoLoteItem.objects.filter(lote=lote).count(), 1)
        self.assertFalse(RetornoLoteItem.objects.filter(lote=lote, agendamento=ainda_sem_retorno).exists())
        lote.refresh_from_db()
        self.assertEqual(lote.status, LoteFaturamento.Status.RETORNO_PARCIAL)

    def test_dar_baixa_lote_permite_corrigir_retorno_ja_lancado(self):
        agendamento = self._criar_agendamento(valor_convenio=Decimal("100.00"))
        lote = self._criar_lote(valor_total_enviado=Decimal("100.00"))
        lote.agendamentos.add(agendamento)
        RetornoLoteItem.objects.create(
            lote=lote, agendamento=agendamento, valor_pago=Decimal("100.00"), data_retorno=timezone.localdate()
        )

        resposta = self.client.post(
            reverse("financeiro:lote_dar_baixa", args=[lote.pk]),
            {
                f"ag{agendamento.pk}-valor_pago": "80.00",
                f"ag{agendamento.pk}-valor_glosado": "20.00",
                f"ag{agendamento.pk}-motivo_glosa": "Corrigido em lote",
                f"ag{agendamento.pk}-data_retorno": timezone.localdate().isoformat(),
            },
        )
        self.assertRedirects(resposta, reverse("financeiro:lote_detalhe", args=[lote.pk]))
        self.assertEqual(RetornoLoteItem.objects.filter(lote=lote).count(), 1)
        retorno = RetornoLoteItem.objects.get(lote=lote, agendamento=agendamento)
        self.assertEqual(retorno.valor_pago, Decimal("80.00"))
        self.assertEqual(retorno.valor_glosado, Decimal("20.00"))

    def test_lote_detalhe_avisa_quando_pago_mais_glosado_nao_bate_com_produzido(self):
        agendamento = self._criar_agendamento(valor_convenio=Decimal("100.00"))
        lote = self._criar_lote(valor_total_enviado=Decimal("100.00"))
        lote.agendamentos.add(agendamento)
        RetornoLoteItem.objects.create(
            lote=lote, agendamento=agendamento, valor_pago=Decimal("70.00"), data_retorno=timezone.localdate()
        )

        resposta = self.client.get(reverse("financeiro:lote_detalhe", args=[lote.pk]))
        self.assertContains(resposta, "não bate com o produzido")

    def test_lote_detalhe_sem_aviso_quando_pago_mais_glosado_bate(self):
        agendamento = self._criar_agendamento(valor_convenio=Decimal("100.00"))
        lote = self._criar_lote(valor_total_enviado=Decimal("100.00"))
        lote.agendamentos.add(agendamento)
        RetornoLoteItem.objects.create(
            lote=lote,
            agendamento=agendamento,
            valor_pago=Decimal("70.00"),
            valor_glosado=Decimal("30.00"),
            data_retorno=timezone.localdate(),
        )

        resposta = self.client.get(reverse("financeiro:lote_detalhe", args=[lote.pk]))
        self.assertNotContains(resposta, "não bate com o produzido")

    def test_paginas_novas_carregam_sem_erro(self):
        """Smoke test — só confere que cada tela nova responde 200 (renderiza sem estourar),
        nos principais estados de navegação (sem filtro, com convênio escolhido, editando)."""
        agendamento = self._criar_agendamento(valor_convenio=Decimal("120.00"))
        lote = self._criar_lote(valor_total_enviado=Decimal("120.00"))
        lote.agendamentos.add(agendamento)

        for url in [
            reverse("financeiro:lote_lista"),
            reverse("financeiro:lote_lista") + f"?convenio={self.convenio.pk}&status=ENVIADO",
            reverse("financeiro:lote_novo"),
            reverse("financeiro:lote_novo") + f"?convenio={self.convenio.pk}",
            reverse("financeiro:lote_detalhe", args=[lote.pk]),
            reverse("financeiro:lote_editar", args=[lote.pk]),
            reverse("financeiro:retorno_lancar", args=[lote.pk, agendamento.pk]),
            reverse("financeiro:lote_dar_baixa", args=[lote.pk]),
            reverse("financeiro:faturamento_comparativo"),
            reverse("financeiro:faturamento_comparativo") + f"?convenio={self.convenio.pk}",
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_lote_editar_pre_marca_itens_ja_incluidos_e_permite_editar(self):
        incluido = self._criar_agendamento(valor_convenio=Decimal("100.00"))
        outro_elegivel = self._criar_agendamento(valor_convenio=Decimal("50.00"))
        lote = self._criar_lote(valor_total_enviado=Decimal("100.00"))
        lote.agendamentos.add(incluido)

        resposta_get = self.client.get(reverse("financeiro:lote_editar", args=[lote.pk]))
        conteudo = resposta_get.content.decode()
        checkbox_incluido = re.search(rf'name="agendamentos" value="{incluido.pk}"[^>]*>', conteudo, re.DOTALL)
        checkbox_outro = re.search(rf'name="agendamentos" value="{outro_elegivel.pk}"[^>]*>', conteudo, re.DOTALL)
        self.assertIsNotNone(checkbox_incluido)
        self.assertIn("checked", checkbox_incluido.group())
        self.assertIsNotNone(checkbox_outro)
        self.assertNotIn("checked", checkbox_outro.group())

        resposta_post = self.client.post(
            reverse("financeiro:lote_editar", args=[lote.pk]),
            {
                "data_envio": timezone.localdate().isoformat(),
                "numero_lote": "L-EDITADO",
                "valor_total_enviado": "150.00",
                "agendamentos": [str(incluido.pk), str(outro_elegivel.pk)],
            },
        )
        self.assertRedirects(resposta_post, reverse("financeiro:lote_detalhe", args=[lote.pk]))
        lote.refresh_from_db()
        self.assertEqual(lote.numero_lote, "L-EDITADO")
        self.assertCountEqual(list(lote.agendamentos.all()), [incluido, outro_elegivel])

    def test_lote_editar_bloqueado_apos_retorno_lancado(self):
        agendamento = self._criar_agendamento(valor_convenio=Decimal("100.00"))
        lote = self._criar_lote()
        lote.agendamentos.add(agendamento)
        RetornoLoteItem.objects.create(
            lote=lote, agendamento=agendamento, valor_pago=Decimal("100.00"), data_retorno=timezone.localdate()
        )

        resposta = self.client.get(reverse("financeiro:lote_editar", args=[lote.pk]))
        self.assertRedirects(resposta, reverse("financeiro:lote_detalhe", args=[lote.pk]))


class PermissaoTests(LoteFaturamentoTestBase):
    def test_usuario_sem_permissao_recebe_403(self):
        usuario = Usuario.objects.create_user(
            username="sem_permissao_teste", password="senha-teste-123", exigir_troca_senha=False
        )
        self.client.force_login(usuario)
        resposta = self.client.get(reverse("financeiro:lote_lista"))
        self.assertEqual(resposta.status_code, 403)

    def test_usuario_so_com_permissao_de_lote_ve_o_menu_financeiro(self):
        """Regressão: um perfil customizado com só faturamento_lote_visualizar (sem
        nenhuma outra permissão financeiro_*) precisa continuar enxergando o menu
        FINANCEIRO abrir, com o link do lote dentro — ver base.html."""
        usuario = Usuario.objects.create_user(
            username="so_lote_teste", password="senha-teste-123", exigir_troca_senha=False
        )
        usuario.user_permissions.add(Permission.objects.get(codename="faturamento_lote_visualizar"))
        self.client.force_login(usuario)

        resposta = self.client.get(reverse("home"))
        self.assertContains(resposta, reverse("financeiro:lote_lista"))


class MovimentacaoCaixaTestBase(TestCase):
    """Fixtures comuns aos testes de entrada/saída avulsa de caixa (ver
    financeiro.models.MovimentacaoCaixa)."""

    def setUp(self):
        self.profissional_comum = Profissional.objects.create(
            nome="Profissional Comum", registro_profissional="CRM-COMUM", percentual_desconto=Decimal("30.00")
        )
        self.proprietario = Profissional.objects.create(
            nome="Dono da Clínica",
            registro_profissional="CRM-DONO",
            percentual_desconto=Decimal("0.00"),
            eh_proprietario=True,
        )
        self.usuario_financeiro = Usuario.objects.create_user(
            username="financeiro_avulsa_teste", password="senha-teste-123", exigir_troca_senha=False
        )
        self.usuario_financeiro.user_permissions.add(
            *Permission.objects.filter(
                codename__in=[
                    "financeiro_ver_painel",
                    "financeiro_ver_movimentacoes_avulsas",
                    "financeiro_ver_fechamento_diario",
                    "financeiro_lancar",
                ]
            )
        )

    def _criar_movimentacao(
        self,
        tipo=MovimentacaoCaixa.Tipo.ENTRADA,
        valor=Decimal("50.00"),
        data=None,
        descricao="Movimentação de teste",
        profissional=None,
    ):
        return MovimentacaoCaixa.objects.create(
            tipo=tipo, valor=valor, data=data or timezone.localdate(), descricao=descricao, profissional=profissional
        )


class MovimentacaoCaixaViewsTests(MovimentacaoCaixaTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario_financeiro)

    def test_criar_entrada_via_view(self):
        hoje = timezone.localdate()
        resposta = self.client.post(
            reverse("financeiro:movimentacao_avulsa_nova"),
            {
                "tipo": MovimentacaoCaixa.Tipo.ENTRADA,
                "valor": "200.00",
                "data": hoje.isoformat(),
                "descricao": "Aporte pra cobrir repasse",
            },
        )
        self.assertEqual(MovimentacaoCaixa.objects.count(), 1)
        movimentacao = MovimentacaoCaixa.objects.get()
        self.assertEqual(movimentacao.tipo, MovimentacaoCaixa.Tipo.ENTRADA)
        self.assertEqual(movimentacao.valor, Decimal("200.00"))
        self.assertEqual(movimentacao.criado_por, self.usuario_financeiro)
        self.assertRedirects(resposta, reverse("financeiro:movimentacao_avulsa_lista") + f"?data={hoje.isoformat()}")

    def test_criar_saida_vinculada_a_profissional_via_view(self):
        hoje = timezone.localdate()
        resposta = self.client.post(
            reverse("financeiro:movimentacao_avulsa_nova"),
            {
                "tipo": MovimentacaoCaixa.Tipo.SAIDA,
                "valor": "70.00",
                "data": hoje.isoformat(),
                "descricao": "Repasse pago em dinheiro na mão",
                "profissional": self.profissional_comum.pk,
            },
        )
        self.assertEqual(resposta.status_code, 302)
        movimentacao = MovimentacaoCaixa.objects.get()
        self.assertEqual(movimentacao.tipo, MovimentacaoCaixa.Tipo.SAIDA)
        self.assertEqual(movimentacao.profissional, self.profissional_comum)

    def test_editar_movimentacao_via_view(self):
        movimentacao = self._criar_movimentacao(valor=Decimal("10.00"), descricao="Original")
        resposta = self.client.post(
            reverse("financeiro:movimentacao_avulsa_editar", args=[movimentacao.pk]),
            {
                "tipo": movimentacao.tipo,
                "valor": "25.00",
                "data": movimentacao.data.isoformat(),
                "descricao": "Corrigido",
            },
        )
        self.assertEqual(resposta.status_code, 302)
        movimentacao.refresh_from_db()
        self.assertEqual(movimentacao.valor, Decimal("25.00"))
        self.assertEqual(movimentacao.descricao, "Corrigido")

    def test_excluir_movimentacao_via_view(self):
        movimentacao = self._criar_movimentacao()
        resposta = self.client.post(reverse("financeiro:movimentacao_avulsa_excluir", args=[movimentacao.pk]))
        self.assertEqual(resposta.status_code, 302)
        self.assertFalse(MovimentacaoCaixa.objects.filter(pk=movimentacao.pk).exists())

    def test_paginas_novas_carregam_sem_erro(self):
        movimentacao = self._criar_movimentacao()
        for url in [
            reverse("financeiro:movimentacao_avulsa_lista"),
            reverse("financeiro:movimentacao_avulsa_nova"),
            reverse("financeiro:movimentacao_avulsa_editar", args=[movimentacao.pk]),
            reverse("financeiro:movimentacao_avulsa_excluir", args=[movimentacao.pk]),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)


class MovimentacaoCaixaPermissaoTests(MovimentacaoCaixaTestBase):
    def test_sem_financeiro_lancar_nao_pode_criar(self):
        usuario = Usuario.objects.create_user(
            username="so_ver_avulsa_teste", password="senha-teste-123", exigir_troca_senha=False
        )
        usuario.user_permissions.add(Permission.objects.get(codename="financeiro_ver_movimentacoes_avulsas"))
        self.client.force_login(usuario)

        resposta = self.client.get(reverse("financeiro:movimentacao_avulsa_nova"))
        self.assertEqual(resposta.status_code, 403)

    def test_sem_financeiro_ver_movimentacoes_avulsas_nao_pode_ver_lista(self):
        usuario = Usuario.objects.create_user(
            username="sem_permissao_avulsa_teste", password="senha-teste-123", exigir_troca_senha=False
        )
        self.client.force_login(usuario)

        resposta = self.client.get(reverse("financeiro:movimentacao_avulsa_lista"))
        self.assertEqual(resposta.status_code, 403)


class PainelFinanceiroAvulsasTests(MovimentacaoCaixaTestBase):
    """Card "Caixa da semana" do Painel financeiro precisa refletir entradas/saídas avulsas —
    ver financeiro.views._movimentacoes_avulsas_do_periodo."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario_financeiro)

    def test_caixa_da_semana_soma_entrada_e_subtrai_saida(self):
        hoje = timezone.localdate()
        self._criar_movimentacao(tipo=MovimentacaoCaixa.Tipo.ENTRADA, valor=Decimal("200.00"), data=hoje)
        self._criar_movimentacao(tipo=MovimentacaoCaixa.Tipo.SAIDA, valor=Decimal("80.00"), data=hoje)

        resposta = self.client.get(reverse("financeiro:painel"))
        self.assertEqual(resposta.context["avulsas_entradas_semana"], Decimal("200.00"))
        self.assertEqual(resposta.context["avulsas_saidas_semana"], Decimal("80.00"))
        # Sem nenhum PagamentoParticular no fixture, retido_dinheiro é 0 — sobra só o líquido das avulsas.
        self.assertEqual(resposta.context["caixa_dinheiro_semana"], Decimal("120.00"))

    def test_movimentacao_fora_da_semana_nao_conta(self):
        fora_da_semana = timezone.localdate() - timedelta(days=30)
        self._criar_movimentacao(tipo=MovimentacaoCaixa.Tipo.SAIDA, valor=Decimal("500.00"), data=fora_da_semana)

        resposta = self.client.get(reverse("financeiro:painel"))
        self.assertEqual(resposta.context["caixa_dinheiro_semana"], Decimal("0.00"))


class VisaoProprietarioAvulsasTests(MovimentacaoCaixaTestBase):
    """'Resultado do proprietário' (Fechamento diário/mensal e Meu fechamento) precisa
    descontar saídas avulsas e somar entradas avulsas do período — decisão do cliente
    (2026-07-22) — e nunca aparecer no fechamento de um profissional comum."""

    def setUp(self):
        super().setUp()
        self.usuario_dono = Usuario.objects.create_user(
            username="dono_avulsa_teste",
            password="senha-teste-123",
            exigir_troca_senha=False,
            profissional_id=self.proprietario.pk,
        )
        self.usuario_dono.user_permissions.add(Permission.objects.get(codename="financeiro_ver_proprio"))

    def test_resultado_do_proprietario_no_meu_fechamento(self):
        hoje = timezone.localdate()
        self._criar_movimentacao(tipo=MovimentacaoCaixa.Tipo.ENTRADA, valor=Decimal("50.00"), data=hoje)
        self._criar_movimentacao(
            tipo=MovimentacaoCaixa.Tipo.SAIDA, valor=Decimal("30.00"), data=hoje, profissional=self.profissional_comum
        )

        self.client.force_login(self.usuario_dono)
        resposta = self.client.get(reverse("financeiro:meu_fechamento_diario"))
        visao = resposta.context["visao_proprietario"]
        self.assertEqual(visao["avulsas_entradas"], Decimal("50.00"))
        self.assertEqual(visao["avulsas_saidas"], Decimal("30.00"))
        # Sem nenhum atendimento no fixture, producao_propria/retencao_outros são 0 — resultado
        # é só o líquido das avulsas (50 - 30 = 20).
        self.assertEqual(visao["resultado"], Decimal("20.00"))

    def test_resultado_do_proprietario_no_fechamento_diario_geral(self):
        hoje = timezone.localdate()
        self._criar_movimentacao(tipo=MovimentacaoCaixa.Tipo.SAIDA, valor=Decimal("15.00"), data=hoje)

        self.client.force_login(self.usuario_financeiro)
        resposta = self.client.get(reverse("financeiro:fechamento_diario"))
        visao = resposta.context["visao_proprietario"]
        self.assertEqual(visao["avulsas_saidas"], Decimal("15.00"))
        self.assertEqual(visao["resultado"], Decimal("-15.00"))

    def test_profissional_comum_nao_ve_avulsas_no_proprio_fechamento(self):
        usuario_comum = Usuario.objects.create_user(
            username="comum_avulsa_teste",
            password="senha-teste-123",
            exigir_troca_senha=False,
            profissional_id=self.profissional_comum.pk,
        )
        usuario_comum.user_permissions.add(Permission.objects.get(codename="financeiro_ver_proprio"))
        self._criar_movimentacao(tipo=MovimentacaoCaixa.Tipo.SAIDA, valor=Decimal("999.00"), data=timezone.localdate())

        self.client.force_login(usuario_comum)
        resposta = self.client.get(reverse("financeiro:meu_fechamento_diario"))
        self.assertIsNone(resposta.context["visao_proprietario"])


class RepasseParcialTests(TestCase):
    """Pagamento PARCIAL de repasse particular (financeiro.models.RepassePagamento, pedido do
    cliente 2026-09-28): a produção do profissional em cartão/Pix às vezes é maior que o dinheiro
    em caixa, então ele leva só uma parte e o proprietário paga o restante depois (Pix). O
    financeiro informa QUANTO pagou; o sistema abate das linhas mais antigas (FIFO) já com a
    retenção descontada, e o saldo continua pendente em todas as telas de repasse."""

    def setUp(self):
        self.paciente = Paciente.objects.create(nome_completo="Paciente Teste")
        self.profissional = Profissional.objects.create(
            nome="Profissional Parcial", registro_profissional="CRM-PARC", percentual_desconto=Decimal("20.00")
        )
        self.usuario_financeiro = Usuario.objects.create_user(
            username="financeiro_parcial_teste", password="senha-teste-123", exigir_troca_senha=False
        )
        self.usuario_financeiro.user_permissions.add(
            *Permission.objects.filter(
                codename__in=[
                    "financeiro_ver_painel",
                    "financeiro_ver_repasses_semanais",
                    "financeiro_ver_fechamento_diario",
                    "financeiro_conciliar",
                    "financeiro_lancar",
                    "financeiro_estornar_pagamento",
                ]
            )
        )
        self.client.force_login(self.usuario_financeiro)

    def _criar_pagamento(self, valor=Decimal("100.00"), forma=Agendamento.FormaPagamento.PIX, minutos_atras=0):
        inicio = timezone.now() - timedelta(minutes=minutos_atras)
        agendamento = Agendamento.objects.create(
            paciente=self.paciente,
            profissional=self.profissional,
            forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
            data_hora_inicio=inicio,
            data_hora_fim=inicio + timedelta(minutes=10),
            status=Agendamento.Status.REALIZADO,
        )
        return PagamentoParticular.objects.create(agendamento=agendamento, forma_pagamento=forma, valor=valor)

    def _registrar(self, valor, forma=RepassePagamento.Forma.DINHEIRO):
        return self.client.post(
            reverse("financeiro:repasse_pagamento_novo", args=[self.profissional.pk]),
            {"valor": valor, "forma": forma, "data": timezone.localdate().isoformat(), "observacao": ""},
        )

    def test_pagamento_parcial_abate_do_mais_antigo_e_deixa_saldo(self):
        antigo = self._criar_pagamento(minutos_atras=60)  # líquido 80,00
        novo = self._criar_pagamento(minutos_atras=10)  # líquido 80,00

        self._registrar("100.00")

        antigo.refresh_from_db()
        novo.refresh_from_db()
        self.assertTrue(antigo.repasse_pago)
        self.assertEqual(antigo.repasse_valor_pago, Decimal("80.00"))
        self.assertFalse(novo.repasse_pago)
        self.assertEqual(novo.repasse_valor_pago, Decimal("20.00"))
        self.assertEqual(novo.saldo_repasse, Decimal("60.00"))
        self.assertEqual(RepassePagamento.saldo_pendente(self.profissional), Decimal("60.00"))

    def test_nao_aceita_valor_maior_que_o_saldo(self):
        self._criar_pagamento()  # líquido 80,00
        resposta = self._registrar("80.01")
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(RepassePagamento.objects.exists())

    def test_dinheiro_gera_saida_de_caixa_e_pix_nao(self):
        self._criar_pagamento(valor=Decimal("200.00"))  # líquido 160,00
        self._registrar("50.00", RepassePagamento.Forma.DINHEIRO)
        self._registrar("30.00", RepassePagamento.Forma.PIX)

        saidas = MovimentacaoCaixa.objects.filter(tipo=MovimentacaoCaixa.Tipo.SAIDA)
        self.assertEqual(saidas.count(), 1)
        self.assertEqual(saidas.get().valor, Decimal("50.00"))
        self.assertEqual(saidas.get().profissional, self.profissional)
        resposta = self.client.get(reverse("financeiro:painel"))
        self.assertEqual(resposta.context["caixa_dinheiro_semana"], Decimal("-50.00"))

    def test_saldo_aparece_no_relatorio_semanal_painel_e_fechamento(self):
        self._criar_pagamento(minutos_atras=10)  # líquido 80,00
        # Dinheiro também fica pendente desde 26/10/2026 (pedido da clínica).
        self._criar_pagamento(forma=Agendamento.FormaPagamento.DINHEIRO)  # líquido 80,00
        self._registrar("30.00", RepassePagamento.Forma.PIX)

        semanal = self.client.get(reverse("agenda:relatorio_repasses_semanal"))
        grupo = semanal.context["grupos"][0]
        self.assertEqual(grupo["total"], Decimal("130.00"))
        self.assertEqual(grupo["itens"][0].saldo, Decimal("50.00"))
        self.assertEqual(grupo["itens"][1].get_forma_pagamento_display(), "Dinheiro")
        self.assertEqual(grupo["itens"][1].saldo, Decimal("80.00"))

        painel = self.client.get(reverse("financeiro:painel"))
        self.assertEqual(painel.context["repasse_pendente_total"], Decimal("130.00"))

        fechamento = self.client.get(reverse("financeiro:fechamento_diario"))
        linha = fechamento.context["linhas_profissionais"][0]
        self.assertEqual(linha["repasse"], Decimal("160.00"))
        self.assertEqual(linha["repasse_pendente"], Decimal("130.00"))
        self.assertEqual(linha["repasse_ja_pago"], Decimal("30.00"))
        self.assertIn("já pago R$ 30,00 · pendente R$ 130,00", fechamento.content.decode())

    def test_dinheiro_registrado_antes_de_26_10_conta_como_entregue_na_hora(self):
        from agenda.models import PagamentoParticular as PP

        antigo = self._criar_pagamento(forma=Agendamento.FormaPagamento.DINHEIRO)
        PP.objects.filter(pk=antigo.pk).update(criado_em=timezone.make_aware(datetime(2026, 10, 25, 17, 0)))
        antigo.refresh_from_db()
        self.assertTrue(antigo.dinheiro_entregue_na_hora)
        novo = self._criar_pagamento(forma=Agendamento.FormaPagamento.DINHEIRO)
        self.assertFalse(novo.dinheiro_entregue_na_hora)

    def test_migracao_marca_so_dinheiro_registrado_antes_de_26_10(self):
        import importlib

        from django.apps import apps as django_apps

        from agenda.models import PagamentoParticular as PP

        migracao = importlib.import_module("agenda.migrations.0033_repasse_dinheiro_registrado")
        antigo = self._criar_pagamento(forma=Agendamento.FormaPagamento.DINHEIRO)
        pix_antigo = self._criar_pagamento()
        novo = self._criar_pagamento(forma=Agendamento.FormaPagamento.DINHEIRO)
        PP.objects.filter(pk__in=[antigo.pk, pix_antigo.pk]).update(
            criado_em=timezone.make_aware(datetime(2026, 10, 25, 17, 0))
        )

        migracao.marcar_antigos(django_apps, None)

        antigo.refresh_from_db()
        pix_antigo.refresh_from_db()
        novo.refresh_from_db()
        self.assertTrue(antigo.repasse_pago)
        self.assertEqual(antigo.repasse_valor_pago, Decimal("80.00"))
        self.assertFalse(pix_antigo.repasse_pago)  # Pix sempre foi pendente
        self.assertFalse(novo.repasse_pago)

    def test_quitar_o_saldo_move_para_ja_pagos(self):
        pagamento = self._criar_pagamento()
        self._registrar("30.00")
        self._registrar("50.00", RepassePagamento.Forma.PIX)

        pagamento.refresh_from_db()
        self.assertTrue(pagamento.repasse_pago)
        self.assertEqual(RepassePagamento.saldo_pendente(self.profissional), Decimal("0.00"))
        semanal = self.client.get(reverse("agenda:relatorio_repasses_semanal"), {"ver": "pagos"})
        self.assertEqual(semanal.context["grupos"][0]["total"], Decimal("80.00"))

    def test_excluir_pagamento_devolve_saldo_e_remove_saida_de_caixa(self):
        pagamento = self._criar_pagamento()
        self._registrar("80.00")
        repasse = RepassePagamento.objects.get()

        self.client.post(reverse("financeiro:repasse_pagamento_excluir", args=[repasse.pk]))

        pagamento.refresh_from_db()
        self.assertFalse(pagamento.repasse_pago)
        self.assertEqual(pagamento.repasse_valor_pago, Decimal("0.00"))
        self.assertFalse(RepassePagamento.objects.exists())
        self.assertFalse(MovimentacaoCaixa.objects.exists())

    def test_saida_gerada_pelo_repasse_nao_pode_ser_excluida_pelas_avulsas(self):
        self._criar_pagamento()
        self._registrar("40.00")
        saida = MovimentacaoCaixa.objects.get()

        self.client.post(reverse("financeiro:movimentacao_avulsa_excluir", args=[saida.pk]))
        self.assertTrue(MovimentacaoCaixa.objects.filter(pk=saida.pk).exists())

    def test_estorno_de_linha_parcialmente_paga_zera_o_abatimento(self):
        pagamento = self._criar_pagamento()
        self._registrar("30.00")

        self.client.post(
            reverse("financeiro:pagamento_estornar", args=[pagamento.pk]), {"motivo_estorno": "Teste"}
        )
        pagamento.refresh_from_db()
        self.assertTrue(pagamento.estornado)
        self.assertEqual(pagamento.repasse_valor_pago, Decimal("0.00"))
        self.assertEqual(RepassePagamento.saldo_pendente(self.profissional), Decimal("0.00"))

    def test_meu_fechamento_mostra_ja_recebido_e_pendente(self):
        self._criar_pagamento()
        self._registrar("30.00")
        usuario_prof = Usuario.objects.create_user(
            username="prof_parcial_teste",
            password="senha-teste-123",
            exigir_troca_senha=False,
            profissional_id=self.profissional.pk,
        )
        usuario_prof.user_permissions.add(Permission.objects.get(codename="financeiro_ver_proprio"))
        self.client.force_login(usuario_prof)

        resposta = self.client.get(reverse("financeiro:meu_fechamento_diario"))
        conteudo = resposta.content.decode()
        self.assertIn('Repasse já recebido</span><span class="valor fonte-num">R$ 30,00', conteudo)
        self.assertIn('Repasse pendente (a receber depois)</span><span class="valor fonte-num">R$ 50,00', conteudo)


class ValorRecebidoCartaoTestBase(TestCase):
    """Fixtures comuns aos testes de valor_recebido/parcelas em PagamentoParticular (taxa da
    maquininha em cartão débito/crédito, sem tabela de taxas no sistema — valor real recebido é
    digitado à mão no momento do pagamento, ver agenda.forms.ConfirmarPagamentoForm). Cobrem os
    relatórios do PROPRIETÁRIO (painel, fechamento geral, relatório detalhado, visão do
    proprietário), que devem refletir o valor líquido de taxa, e o repasse do profissional
    (_producao_por_profissional, Meu fechamento), que nunca pode ser afetado por ela."""

    def setUp(self):
        self.paciente = Paciente.objects.create(nome_completo="Paciente Teste Taxa")
        self.procedimento = Procedimento.objects.create(
            nome="Consulta Taxa Teste", eh_consulta=True, valor_particular=Decimal("100.00")
        )
        self.profissional_comum = Profissional.objects.create(
            nome="Profissional Comum Taxa", registro_profissional="CRM-TAXA-COMUM", percentual_desconto=Decimal("30.00")
        )
        self.proprietario = Profissional.objects.create(
            nome="Dono da Clínica Taxa",
            registro_profissional="CRM-TAXA-DONO",
            percentual_desconto=Decimal("0.00"),
            eh_proprietario=True,
        )
        self.usuario_financeiro = Usuario.objects.create_user(
            username="financeiro_taxa_teste", password="senha-teste-123", exigir_troca_senha=False
        )
        self.usuario_financeiro.user_permissions.add(
            *Permission.objects.filter(
                codename__in=[
                    "financeiro_ver_painel",
                    "financeiro_ver_fechamento_diario",
                    "financeiro_ver_relatorio_detalhado",
                ]
            )
        )
        self.client.force_login(self.usuario_financeiro)
        self._proximo_horario = timezone.now()

    def _criar_pagamento(
        self,
        profissional,
        valor=Decimal("100.00"),
        valor_recebido=None,
        forma_pagamento=Agendamento.FormaPagamento.CARTAO_CREDITO,
        parcelas=1,
        estornado=False,
        com_item=False,
    ):
        inicio = self._proximo_horario
        self._proximo_horario += timedelta(hours=1)
        agendamento = Agendamento.objects.create(
            paciente=self.paciente,
            profissional=profissional,
            forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
            data_hora_inicio=inicio,
            data_hora_fim=inicio + timedelta(minutes=30),
            status=Agendamento.Status.REALIZADO,
        )
        if com_item:
            AgendamentoItem.objects.create(
                agendamento=agendamento, procedimento=self.procedimento, quantidade=1, valor_cobrado=valor
            )
        return PagamentoParticular.objects.create(
            agendamento=agendamento,
            forma_pagamento=forma_pagamento,
            valor=valor,
            valor_recebido=valor_recebido,
            parcelas=parcelas if forma_pagamento == Agendamento.FormaPagamento.CARTAO_CREDITO else None,
            estornado=estornado,
        )

    def _login_profissional(self, profissional, username):
        usuario = Usuario.objects.create_user(
            username=username, password="senha-teste-123", exigir_troca_senha=False, profissional_id=profissional.pk
        )
        usuario.user_permissions.add(Permission.objects.get(codename="financeiro_ver_proprio"))
        self.client.force_login(usuario)
        return usuario


class PainelFinanceiroValorRecebidoTests(ValorRecebidoCartaoTestBase):
    def test_usa_valor_recebido_no_total_e_por_forma(self):
        self._criar_pagamento(
            self.profissional_comum, valor=Decimal("100.00"), valor_recebido=Decimal("97.00"),
            forma_pagamento=Agendamento.FormaPagamento.CARTAO_CREDITO, parcelas=2,
        )
        self._criar_pagamento(
            self.profissional_comum, valor=Decimal("50.00"), forma_pagamento=Agendamento.FormaPagamento.DINHEIRO
        )

        resposta = self.client.get(reverse("financeiro:painel"))
        self.assertEqual(resposta.context["recebido_particular_total"], Decimal("147.00"))
        por_forma = dict(resposta.context["recebido_por_forma"])
        self.assertEqual(por_forma["Cartão crédito"], Decimal("97.00"))
        self.assertEqual(por_forma["Dinheiro"], Decimal("50.00"))

    def test_repasse_pendente_ignora_taxa_da_maquininha(self):
        """Regressão crítica: repasse_pendente_total é dinheiro devido ao profissional — nunca
        pode refletir o desconto da maquininha, mesmo com valor_recebido preenchido. Desde o
        pagamento parcial (2026-09-28) é o saldo LÍQUIDO (valor − retenção de 30%), calculado
        sobre 'valor' (100), não sobre valor_recebido (97)."""
        self._criar_pagamento(
            self.profissional_comum, valor=Decimal("100.00"), valor_recebido=Decimal("97.00"),
            forma_pagamento=Agendamento.FormaPagamento.CARTAO_CREDITO,
        )

        resposta = self.client.get(reverse("financeiro:painel"))
        self.assertEqual(resposta.context["repasse_pendente_total"], Decimal("70.00"))
        self.assertEqual(resposta.context["repasse_pendente_qtd"], 1)


class FechamentoValorRecebidoTests(ValorRecebidoCartaoTestBase):
    def test_geral_sem_filtro_usa_valor_recebido_na_particular_por_forma(self):
        self._criar_pagamento(
            self.profissional_comum, valor=Decimal("100.00"), valor_recebido=Decimal("97.00"),
            forma_pagamento=Agendamento.FormaPagamento.CARTAO_CREDITO,
        )

        resposta = self.client.get(reverse("financeiro:fechamento_diario"))
        por_forma = {item["forma"]: item["valor"] for item in resposta.context["particular_forma"]}
        self.assertEqual(por_forma["Cartão crédito"], Decimal("97.00"))

    def test_particular_conta_procedimentos_nao_atendimentos(self):
        """Pedido da clínica (26/10/2026): atendimento com 2 procedimentos conta 2 no total e
        na forma de pagamento."""
        pagamento = self._criar_pagamento(
            self.profissional_comum, valor=Decimal("100.00"), forma_pagamento=Agendamento.FormaPagamento.PIX, com_item=True
        )
        AgendamentoItem.objects.create(
            agendamento=pagamento.agendamento,
            procedimento=Procedimento.objects.create(nome="Exame Teste"),
            quantidade=1,
            valor_cobrado=Decimal("0.00"),
        )
        self._criar_pagamento(
            self.profissional_comum, valor=Decimal("50.00"), forma_pagamento=Agendamento.FormaPagamento.DINHEIRO, com_item=True
        )

        resposta = self.client.get(reverse("financeiro:fechamento_diario"))
        self.assertEqual(resposta.context["particular_procedimentos_total"], 3)
        por_forma = {item["forma"]: item["qtd"] for item in resposta.context["particular_forma"]}
        self.assertEqual(por_forma["Pix"], 2)
        self.assertEqual(por_forma["Dinheiro"], 1)
        self.assertContains(resposta, "Total particular · 3 procedimentos")

    def test_filtrado_por_profissional_volta_ao_valor_cheio(self):
        """Decisão do cliente (2026-07-29): filtrado por um profissional específico — tela que
        às vezes é impressa e entregue a ele — nunca deve revelar a taxa da maquininha."""
        self._criar_pagamento(
            self.profissional_comum, valor=Decimal("100.00"), valor_recebido=Decimal("97.00"),
            forma_pagamento=Agendamento.FormaPagamento.CARTAO_CREDITO,
        )

        resposta = self.client.get(reverse("financeiro:fechamento_diario"), {"profissional": self.profissional_comum.pk})
        por_forma = {item["forma"]: item["valor"] for item in resposta.context["particular_forma"]}
        self.assertEqual(por_forma["Cartão crédito"], Decimal("100.00"))

    def test_producao_por_profissional_expoe_liquido_recebido_sem_alterar_repasse(self):
        self._criar_pagamento(
            self.profissional_comum, valor=Decimal("100.00"), valor_recebido=Decimal("97.00"),
            forma_pagamento=Agendamento.FormaPagamento.CARTAO_CREDITO,
        )

        resposta = self.client.get(reverse("financeiro:fechamento_diario"))
        linhas = {linha["profissional"].pk: linha for linha in resposta.context["linhas_profissionais"]}
        linha = linhas[self.profissional_comum.pk]
        self.assertEqual(linha["bruto"], Decimal("100.00"))
        self.assertEqual(linha["repasse"], Decimal("70.00"))
        self.assertEqual(linha["liquido_recebido"], Decimal("97.00"))

    def test_visao_proprietario_desconta_taxa_da_retencao_de_outros(self):
        """Exemplo do plano: profissional a 30%, valor=100/valor_recebido=97 — o repasse dele
        continua 70.00 (cheio), mas a retenção real da clínica cai pra 27.00 (não 30.00), porque
        a taxa de 3.00 é absorvida pela clínica, nunca pelo profissional."""
        self._criar_pagamento(
            self.profissional_comum, valor=Decimal("100.00"), valor_recebido=Decimal("97.00"),
            forma_pagamento=Agendamento.FormaPagamento.CARTAO_CREDITO,
        )

        resposta = self.client.get(reverse("financeiro:fechamento_diario"))
        linhas = {linha["profissional"].pk: linha for linha in resposta.context["linhas_profissionais"]}
        self.assertEqual(linhas[self.profissional_comum.pk]["repasse"], Decimal("70.00"))
        self.assertEqual(resposta.context["visao_proprietario"]["retencao_outros"], Decimal("27.00"))

    def test_visao_proprietario_sem_valor_recebido_e_identico_a_formula_antiga(self):
        """Garantia de compatibilidade retroativa: sem NENHUM valor_recebido preenchido no
        período (todo o histórico anterior a esta feature, por exemplo), retencao_outros e
        producao_propria batem exatamente com a fórmula de antes (retido/repasse puros)."""
        self._criar_pagamento(
            self.profissional_comum, valor=Decimal("100.00"), forma_pagamento=Agendamento.FormaPagamento.DINHEIRO
        )
        self._criar_pagamento(
            self.proprietario, valor=Decimal("50.00"), forma_pagamento=Agendamento.FormaPagamento.DINHEIRO
        )

        resposta = self.client.get(reverse("financeiro:fechamento_diario"))
        # 30% de 100 = 30.00 (fórmula antiga, sem nenhum ajuste de taxa).
        self.assertEqual(resposta.context["visao_proprietario"]["retencao_outros"], Decimal("30.00"))
        # Proprietário fica com 100% da própria produção (0% de retenção) = 50.00.
        self.assertEqual(resposta.context["visao_proprietario"]["producao_propria"], Decimal("50.00"))


class MeuFechamentoValorRecebidoTests(ValorRecebidoCartaoTestBase):
    def test_profissional_comum_sempre_mostra_valor_cheio(self):
        self._criar_pagamento(
            self.profissional_comum, valor=Decimal("100.00"), valor_recebido=Decimal("97.00"),
            forma_pagamento=Agendamento.FormaPagamento.CARTAO_CREDITO,
        )
        self._login_profissional(self.profissional_comum, "prof_comum_taxa_teste")

        resposta = self.client.get(reverse("financeiro:meu_fechamento_diario"))
        por_forma = {item["forma"]: item["valor"] for item in resposta.context["particular_forma"]}
        self.assertEqual(por_forma["Cartão crédito"], Decimal("100.00"))
        self.assertEqual(resposta.context["linha_propria"]["repasse"], Decimal("70.00"))

    def test_proprietario_particular_forma_bruto_mas_visao_propria_e_liquida(self):
        """Mesma página ('Meu fechamento' do próprio dono): a quebra por forma de pagamento
        continua cheia, só a caixinha 'Visão do proprietário' reflete o valor com desconto."""
        self._criar_pagamento(
            self.proprietario, valor=Decimal("100.00"), valor_recebido=Decimal("95.00"),
            forma_pagamento=Agendamento.FormaPagamento.CARTAO_CREDITO,
        )
        self._login_profissional(self.proprietario, "dono_taxa_teste")

        resposta = self.client.get(reverse("financeiro:meu_fechamento_diario"))
        por_forma = {item["forma"]: item["valor"] for item in resposta.context["particular_forma"]}
        self.assertEqual(por_forma["Cartão crédito"], Decimal("100.00"))
        self.assertEqual(resposta.context["visao_proprietario"]["producao_propria"], Decimal("95.00"))


class RelatorioDetalhadoValorRecebidoTests(ValorRecebidoCartaoTestBase):
    def test_footer_soma_recebido_excluindo_estornado(self):
        self._criar_pagamento(
            self.profissional_comum, valor=Decimal("100.00"), valor_recebido=Decimal("97.00"),
            forma_pagamento=Agendamento.FormaPagamento.CARTAO_CREDITO,
        )
        self._criar_pagamento(
            self.profissional_comum, valor=Decimal("50.00"), valor_recebido=Decimal("48.00"),
            forma_pagamento=Agendamento.FormaPagamento.CARTAO_DEBITO, estornado=True,
        )

        resposta = self.client.get(reverse("financeiro:relatorio_detalhado"))
        self.assertEqual(resposta.context["particular_recebido_total"], Decimal("97.00"))
        # Por linha, o valor estornado continua aparecendo (mesmo espírito de valor_bruto —
        # não encolhe silenciosamente se a tela for reimpressa depois de um estorno).
        valores_por_linha = [linha["valor_recebido_total"] for linha in resposta.context["linhas"]]
        self.assertIn(Decimal("48.00"), valores_por_linha)

    def test_particular_liquido_total_nao_e_afetado_por_valor_recebido(self):
        """Regressão: particular_liquido_total (baseado em AgendamentoItem.valor_cobrado, líquido
        de estorno) é um total diferente e não deve mudar por causa de valor_recebido."""
        self._criar_pagamento(
            self.profissional_comum, valor=Decimal("100.00"), valor_recebido=Decimal("90.00"),
            forma_pagamento=Agendamento.FormaPagamento.CARTAO_CREDITO, com_item=True,
        )

        resposta = self.client.get(reverse("financeiro:relatorio_detalhado"))
        self.assertEqual(resposta.context["particular_liquido_total"], Decimal("100.00"))
        self.assertEqual(resposta.context["particular_recebido_total"], Decimal("90.00"))


class RelatorioDetalhadoFiltrosTests(ValorRecebidoCartaoTestBase):
    """Filtros de período/convênio/paciente/profissional e a coluna "Guia" (sessão X/Y) —
    pedido da clínica, 03/11/2026."""

    def setUp(self):
        super().setUp()
        self.convenio = Convenio.objects.create(nome="Bradesco Teste")
        self.sessao = Procedimento.objects.create(nome="Fono Teste", permite_repeticao_semanal=True)
        self.outro_paciente = Paciente.objects.create(nome_completo="Calebe Max Teste")
        self.guia = GuiaConvenio.objects.create(
            paciente=self.outro_paciente, convenio=self.convenio, procedimento=self.sessao,
            numero="KNZEM94", quantidade_sessoes=10,
        )
        self.dia1 = timezone.make_aware(datetime(2026, 9, 1, 9, 0))
        for dias in (0, 7):
            agendamento = Agendamento.objects.create(
                paciente=self.outro_paciente, profissional=self.profissional_comum,
                forma_atendimento=Agendamento.FormaAtendimento.CONVENIO, convenio=self.convenio,
                data_hora_inicio=self.dia1 + timedelta(days=dias),
                data_hora_fim=self.dia1 + timedelta(days=dias, minutes=30),
                status=Agendamento.Status.AUTORIZADO, autorizado_em=self.dia1 + timedelta(days=dias),
            )
            AgendamentoItem.objects.create(
                agendamento=agendamento, procedimento=self.sessao, quantidade=1, guia_convenio=self.guia
            )
        particular = self._criar_pagamento(self.profissional_comum, com_item=True)
        Agendamento.objects.filter(pk=particular.agendamento_id).update(data_hora_inicio=self.dia1 + timedelta(days=2))

    def _get(self, **params):
        return self.client.get(reverse("financeiro:relatorio_detalhado"), {"de": "2026-09-01", "ate": "2026-09-30", **params})

    def test_periodo_traz_todos_e_coluna_guia_mostra_sessao_x_de_y(self):
        resposta = self._get()
        self.assertEqual(len(resposta.context["linhas"]), 3)
        guias = [linha["guias"] for linha in resposta.context["linhas"] if linha["guias"]]
        self.assertEqual([g[0]["sessoes"] for g in guias], ["1", "2"])
        self.assertContains(resposta, "2/10")

    def test_filtra_por_convenio_paciente_e_profissional(self):
        self.assertEqual(len(self._get(convenio=self.convenio.pk).context["linhas"]), 2)
        self.assertEqual(len(self._get(convenio="particular").context["linhas"]), 1)
        self.assertEqual(len(self._get(paciente="calebe").context["linhas"]), 2)
        self.assertEqual(len(self._get(profissional=self.proprietario.pk).context["linhas"]), 0)

    def test_links_antigos_dia_e_mes_continuam_valendo(self):
        url = reverse("financeiro:relatorio_detalhado")
        self.assertEqual(len(self.client.get(url, {"data": "2026-09-08"}).context["linhas"]), 1)
        self.assertEqual(len(self.client.get(url, {"modo": "mes", "ano": 2026, "mes": 9}).context["linhas"]), 3)

    def test_numero_de_consultas_nao_cresce_por_linha(self):
        self._get()  # 1º request do dia roda o middleware de status automático da agenda
        with CaptureQueriesContext(connection) as poucas:
            self._get()
        for _ in range(5):
            self._criar_pagamento(self.profissional_comum, com_item=True)
            Agendamento.objects.filter(data_hora_inicio__gt=self.dia1 + timedelta(days=60)).update(
                data_hora_inicio=self.dia1 + timedelta(days=3)
            )
        with CaptureQueriesContext(connection) as muitas:
            resposta = self._get()
        self.assertEqual(len(resposta.context["linhas"]), 8)
        self.assertEqual(len(muitas), len(poucas))


class VisaoProprietarioDinheiroBancoTests(MovimentacaoCaixaTestBase):
    """Separação 'em dinheiro' x 'em banco' (cartão/Pix) na Visão do proprietário, e a lista
    detalhada de saídas avulsas (só no fechamento diário, nunca no mensal) — decisão do
    cliente (2026-07-29)."""

    def setUp(self):
        super().setUp()
        self.paciente = Paciente.objects.create(nome_completo="Paciente Dinheiro Banco Teste")
        self.usuario_financeiro.user_permissions.add(
            *Permission.objects.filter(codename__in=["financeiro_ver_fechamento_mensal", "financeiro_ver_proprio"])
        )
        self.client.force_login(self.usuario_financeiro)

    def _criar_pagamento(self, profissional, valor, forma_pagamento, agendamento=None, convenio=None):
        agora = timezone.now()
        if agendamento is None:
            agendamento = Agendamento.objects.create(
                paciente=self.paciente,
                profissional=profissional,
                forma_atendimento=(
                    Agendamento.FormaAtendimento.CONVENIO if convenio else Agendamento.FormaAtendimento.PARTICULAR
                ),
                convenio=convenio,
                data_hora_inicio=agora,
                data_hora_fim=agora + timedelta(minutes=30),
                status=Agendamento.Status.REALIZADO,
            )
        return PagamentoParticular.objects.create(agendamento=agendamento, forma_pagamento=forma_pagamento, valor=valor)

    def test_dinheiro_e_caixa_fisico_e_banco_e_parte_do_proprietario(self):
        hoje = timezone.localdate()
        # Produção própria do proprietário: 100 em dinheiro + 40 em cartão ("banco").
        self._criar_pagamento(self.proprietario, Decimal("100.00"), Agendamento.FormaPagamento.DINHEIRO)
        self._criar_pagamento(self.proprietario, Decimal("40.00"), Agendamento.FormaPagamento.CARTAO_CREDITO)
        # Produção de outro profissional (30% de retenção): 100 em dinheiro + 60 em Pix.
        self._criar_pagamento(self.profissional_comum, Decimal("100.00"), Agendamento.FormaPagamento.DINHEIRO)
        self._criar_pagamento(self.profissional_comum, Decimal("60.00"), Agendamento.FormaPagamento.PIX)
        self._criar_movimentacao(tipo=MovimentacaoCaixa.Tipo.ENTRADA, valor=Decimal("20.00"), data=hoje)
        self._criar_movimentacao(tipo=MovimentacaoCaixa.Tipo.SAIDA, valor=Decimal("5.00"), data=hoje)

        resposta = self.client.get(reverse("financeiro:fechamento_diario"))
        visao = resposta.context["visao_proprietario"]
        self.assertEqual(visao["producao_propria"], Decimal("140.00"))
        self.assertEqual(visao["retencao_outros"], Decimal("48.00"))  # 30% de 100 + 30% de 60
        # dinheiro = caixa físico: 100 + 100 (a parte do profissional fica no caixa até o repasse) + 20 − 5.
        self.assertEqual(visao["dinheiro_total"], Decimal("215.00"))
        # banco = 40 (produção própria) + 18 (retenção sobre os 60 em Pix).
        self.assertEqual(visao["banco_total"], Decimal("58.00"))
        self.assertEqual(visao["resultado"], Decimal("203.00"))
        # Mesmo valor no Painel ("Caixa da semana").
        self.assertEqual(self.client.get(reverse("financeiro:painel")).context["caixa_dinheiro_semana"], Decimal("215.00"))

        conteudo = resposta.content.decode()
        self.assertIn("Em dinheiro", conteudo)
        self.assertIn("Em banco", conteudo)
        self.assertIn("R$ 215,00", conteudo)
        self.assertIn("R$ 58,00", conteudo)

    def test_caso_da_clinica_26_10_pagamento_misto_convenio_e_repasse_em_dinheiro(self):
        """Prints da clínica (26/10/2026): o dinheiro de pagamento dividido e de convênio pago na
        clínica precisa estar no caixa, e o repasse pago em dinheiro do caixa não pode ser
        descontado duas vezes do resultado."""
        from cadastros.models import Convenio

        hoje = timezone.localdate()
        aspeb = Convenio.objects.create(nome="ASPEB Teste")  # não "funciona como particular"
        # Proprietário: Pix 90 + Dinheiro 110 (mesmo atendimento) e Dinheiro 200.
        misto = self._criar_pagamento(self.proprietario, Decimal("90.00"), Agendamento.FormaPagamento.PIX)
        self._criar_pagamento(self.proprietario, Decimal("110.00"), Agendamento.FormaPagamento.DINHEIRO, misto.agendamento)
        self._criar_pagamento(self.proprietario, Decimal("200.00"), Agendamento.FormaPagamento.DINHEIRO)
        # Convênio pago na clínica: Dinheiro 150 + Pix 10.
        conv = self._criar_pagamento(self.proprietario, Decimal("150.00"), Agendamento.FormaPagamento.DINHEIRO, convenio=aspeb)
        self._criar_pagamento(self.proprietario, Decimal("10.00"), Agendamento.FormaPagamento.PIX, conv.agendamento)
        # Outro profissional (30%): Dinheiro 230, Pix 230 + 230.
        self._criar_pagamento(self.profissional_comum, Decimal("230.00"), Agendamento.FormaPagamento.DINHEIRO)
        self._criar_pagamento(self.profissional_comum, Decimal("230.00"), Agendamento.FormaPagamento.PIX)
        self._criar_pagamento(self.profissional_comum, Decimal("230.00"), Agendamento.FormaPagamento.PIX)
        self._criar_movimentacao(tipo=MovimentacaoCaixa.Tipo.SAIDA, valor=Decimal("116.00"), data=hoje, descricao="Casa")
        # Repasse de R$ 300 pago em dinheiro do caixa (como a tela Repasses faz).
        saida = self._criar_movimentacao(
            tipo=MovimentacaoCaixa.Tipo.SAIDA, valor=Decimal("300.00"), data=hoje,
            descricao="Repasse particular pago em dinheiro", profissional=self.profissional_comum,
        )
        repasse = RepassePagamento.objects.create(
            profissional=self.profissional_comum, valor=Decimal("300.00"), data=hoje, movimentacao=saida
        )
        repasse.alocar()

        visao = self.client.get(reverse("financeiro:fechamento_diario")).context["visao_proprietario"]
        # Caixa físico: 110 + 200 + 150 + 230 − 116 − 300.
        self.assertEqual(visao["dinheiro_total"], Decimal("274.00"))
        self.assertEqual(visao["producao_propria"], Decimal("400.00"))
        self.assertEqual(visao["retencao_outros"], Decimal("207.00"))
        self.assertEqual(visao["avulsas_saidas"], Decimal("116.00"))
        self.assertEqual(visao["repasses_dinheiro"], Decimal("300.00"))
        self.assertEqual(visao["resultado"], Decimal("491.00"))  # 400 + 207 − 116 (sem os 300)
        self.assertEqual(visao["banco_total"], Decimal("228.00"))  # 90 + 30% de 460
        # Saldo do profissional: 70% de 690 = 483, menos os 300 pagos.
        self.assertEqual(RepassePagamento.saldo_pendente(self.profissional_comum), Decimal("183.00"))

    def test_saidas_avulsas_detalhadas_so_no_fechamento_diario(self):
        hoje = timezone.localdate()
        self._criar_movimentacao(
            tipo=MovimentacaoCaixa.Tipo.SAIDA,
            valor=Decimal("30.00"),
            data=hoje,
            descricao="Repasse pago em dinheiro — Dra. Beltrana",
            profissional=self.profissional_comum,
        )

        resposta_diaria = self.client.get(reverse("financeiro:fechamento_diario"))
        conteudo_diario = resposta_diaria.content.decode()
        self.assertIn("Repasse pago em dinheiro — Dra. Beltrana", conteudo_diario)

        resposta_mensal = self.client.get(
            reverse("financeiro:fechamento_mensal"), {"ano": hoje.year, "mes": hoje.month}
        )
        conteudo_mensal = resposta_mensal.content.decode()
        self.assertNotIn("Repasse pago em dinheiro — Dra. Beltrana", conteudo_mensal)
        # O total continua aparecendo nos dois, só o item-a-item some do mensal.
        self.assertIn("Saídas avulsas de caixa", conteudo_mensal)

    def test_meu_fechamento_saidas_detalhadas_so_no_periodo_diario(self):
        hoje = timezone.localdate()
        self._criar_movimentacao(
            tipo=MovimentacaoCaixa.Tipo.SAIDA,
            valor=Decimal("30.00"),
            data=hoje,
            descricao="Aporte retirado para conserto do ar-condicionado",
        )
        usuario_dono = Usuario.objects.create_user(
            username="dono_dinheiro_banco_teste",
            password="senha-teste-123",
            exigir_troca_senha=False,
            profissional_id=self.proprietario.pk,
        )
        usuario_dono.user_permissions.add(Permission.objects.get(codename="financeiro_ver_proprio"))
        self.client.force_login(usuario_dono)

        resposta_diaria = self.client.get(reverse("financeiro:meu_fechamento_diario"))
        self.assertIn("Aporte retirado para conserto do ar-condicionado", resposta_diaria.content.decode())

        resposta_mensal = self.client.get(reverse("financeiro:meu_fechamento_mensal"))
        self.assertNotIn("Aporte retirado para conserto do ar-condicionado", resposta_mensal.content.decode())


class RepassePeloProprietarioTests(ValorRecebidoCartaoTestBase):
    """Pedido da clínica (2026-10-16): o proprietário registra os pagamentos de repasse
    particular (mesmo sem a permissão do financeiro) e não aparece nos saldos — não há repasse
    a pagar a ele."""

    def setUp(self):
        super().setUp()
        self._criar_pagamento(self.profissional_comum)
        self._criar_pagamento(self.proprietario)
        self.client.force_login(
            Usuario.objects.create_user(
                username="dono", password="x", exigir_troca_senha=False, profissional_id=self.proprietario.pk
            )
        )

    def test_proprietario_ve_saldos_sem_ele_mesmo_e_registra_pagamento(self):
        tela = self.client.get(reverse("financeiro:repasse_pagamento_lista"))
        self.assertEqual(tela.status_code, 200)
        self.assertEqual([i["profissional"] for i in tela.context["saldos"]], [self.profissional_comum])
        self.assertTrue(tela.context["pode_registrar"])
        self.assertContains(tela, "Saldos e pagamentos de repasse")  # link no menu

        saldo = tela.context["saldos"][0]["saldo"]
        self.client.post(
            reverse("financeiro:repasse_pagamento_novo", args=[self.profissional_comum.pk]),
            {"valor": str(saldo), "forma": RepassePagamento.Forma.PIX, "data": timezone.localdate().isoformat()},
        )
        repasse = RepassePagamento.objects.get()
        self.assertEqual((repasse.profissional, repasse.valor), (self.profissional_comum, saldo))

    def test_nao_registra_pagamento_para_o_proprio_proprietario(self):
        resposta = self.client.get(reverse("financeiro:repasse_pagamento_novo", args=[self.proprietario.pk]))
        self.assertRedirects(resposta, reverse("financeiro:repasse_pagamento_lista"), fetch_redirect_response=False)
        self.assertFalse(RepassePagamento.objects.exists())

    def test_outro_profissional_sem_permissao_nao_entra(self):
        self.client.force_login(
            Usuario.objects.create_user(
                username="comum", password="x", exigir_troca_senha=False, profissional_id=self.profissional_comum.pk
            )
        )
        self.assertEqual(self.client.get(reverse("financeiro:repasse_pagamento_lista")).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("financeiro:repasse_pagamento_novo", args=[self.profissional_comum.pk])).status_code,
            403,
        )


class FechamentoConvenioComValorTests(LoteFaturamentoTestBase):
    """Pedido da clínica (23/10/2026): o fechamento mostra o que se produziu em convênio
    também em R$ (valor da tabela congelado na autorização), e a produção total do dia."""

    def setUp(self):
        super().setUp()
        usuario = Usuario.objects.create_user(username="fech_conv", password="x", exigir_troca_senha=False)
        usuario.user_permissions.add(Permission.objects.get(codename="financeiro_ver_fechamento_diario"))
        self.client.force_login(usuario)

    def test_convenio_aparece_com_valor_e_entra_na_producao(self):
        self._criar_agendamento(valor_convenio=Decimal("100.00"))
        self._criar_agendamento(valor_convenio=Decimal("60.50"), status=Agendamento.Status.AUTORIZADO)
        self._criar_agendamento(valor_convenio=Decimal("999.00"), status=Agendamento.Status.CANCELADO)
        sem_preco = self._criar_agendamento(valor_convenio=None)
        resposta = self.client.get(reverse("financeiro:fechamento_diario"), {"data": "2026-07-20"})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["convenios_valor"], Decimal("160.50"))
        self.assertEqual(resposta.context["convenios_sem_valor"], 1)
        self.assertEqual(resposta.context["producao_total"], Decimal("160.50"))
        self.assertContains(resposta, "R$ 160,50")
        self.assertContains(resposta, "sem preço")
        self.assertIsNotNone(sem_preco)

    def test_retorno_nao_soma_valor(self):
        original = self._criar_agendamento(valor_convenio=Decimal("100.00"))
        self._criar_agendamento(valor_convenio=Decimal("0.00"), retorno_de=original)
        resposta = self.client.get(reverse("financeiro:fechamento_diario"), {"data": "2026-07-20"})
        self.assertEqual(resposta.context["convenios_valor"], Decimal("100.00"))
        self.assertEqual(resposta.context["convenios_total"], 2)

class ConvenioPagoNaClinicaTests(LoteFaturamentoTestBase):
    """Decisão da clínica (23/10/2026): convênio marcado "funciona como particular", pago na
    clínica, vai pro repasse semanal e pro caixa do proprietário, e sai do faturamento ao plano.
    Os demais convênios pagos na hora continuam como antes (sem repasse semanal)."""

    def setUp(self):
        super().setUp()
        self.convenio.funciona_como_particular = True
        self.convenio.save()
        self.agendamento = self._criar_agendamento(valor_convenio=Decimal("100.00"), status=Agendamento.Status.AUTORIZADO)
        self.agendamento.itens.update(valor_cobrado=Decimal("100.00"))
        PagamentoParticular.objects.create(
            agendamento=self.agendamento, forma_pagamento=Agendamento.FormaPagamento.PIX, valor=Decimal("100.00")
        )
        self.faturado = self._criar_agendamento(valor_convenio=Decimal("80.00"), status=Agendamento.Status.AUTORIZADO)

    def test_entra_no_repasse_semanal_com_o_percentual(self):
        from .models import RepassePagamento
        from .views import _producao_por_profissional

        pendentes = list(RepassePagamento.pendentes_do_profissional(self.profissional))
        self.assertEqual([p.agendamento_id for p in pendentes], [self.agendamento.pk])
        self.assertEqual(pendentes[0].saldo_repasse, Decimal("70.00"))  # 30% de retenção
        linhas, totais = _producao_por_profissional(date(2026, 7, 20), date(2026, 7, 20))
        self.assertEqual(totais["bruto"], Decimal("100.00"))
        self.assertEqual(totais["repasse_pendente"], Decimal("70.00"))

    def test_sai_do_faturamento_ao_convenio(self):
        from .views import _repasse_convenio_por_profissional

        elegiveis = LoteFaturamento.agendamentos_elegiveis(self.convenio)
        self.assertNotIn(self.agendamento, elegiveis)
        self.assertIn(self.faturado, elegiveis)
        repasse = _repasse_convenio_por_profissional(date(2026, 7, 20), date(2026, 7, 20))
        self.assertEqual(sum(l["atendimentos"] for l in repasse), 1)

    def test_fechamento_nao_conta_em_dobro(self):
        usuario = Usuario.objects.create_user(username="fech_pago_clinica", password="x", exigir_troca_senha=False)
        usuario.user_permissions.add(Permission.objects.get(codename="financeiro_ver_fechamento_diario"))
        self.client.force_login(usuario)
        resposta = self.client.get(reverse("financeiro:fechamento_diario"), {"data": "2026-07-20"})
        self.assertEqual(resposta.context["convenios_valor"], Decimal("80.00"))
        self.assertEqual(resposta.context["particular_valor"], Decimal("100.00"))
        self.assertEqual(resposta.context["producao_total"], Decimal("180.00"))
        self.assertContains(resposta, "pago na clínica")

    def test_convenio_nao_marcado_pago_na_hora_continua_como_convenio(self):
        from .models import RepassePagamento

        outro = self._criar_agendamento(
            convenio=self.outro_convenio, valor_convenio=Decimal("50.00"), status=Agendamento.Status.AUTORIZADO
        )
        PagamentoParticular.objects.create(agendamento=outro, forma_pagamento=Agendamento.FormaPagamento.PIX, valor=Decimal("50.00"))
        pendentes = [p.agendamento_id for p in RepassePagamento.pendentes_do_profissional(self.profissional)]
        self.assertNotIn(outro.pk, pendentes)
        self.assertIn(outro, LoteFaturamento.agendamentos_elegiveis(self.outro_convenio))

    def test_ao_marcar_o_convenio_o_ja_repassado_nao_volta_como_pendente(self):
        from .models import RepassePagamento

        antigo = self._criar_agendamento(
            convenio=self.outro_convenio, valor_convenio=Decimal("50.00"), status=Agendamento.Status.AUTORIZADO
        )
        Agendamento.objects.filter(pk=antigo.pk).update(convenio_repasse_pago=True, convenio_repasse_pago_em=timezone.now())
        linha = PagamentoParticular.objects.create(
            agendamento=antigo, forma_pagamento=Agendamento.FormaPagamento.PIX, valor=Decimal("50.00")
        )
        self.outro_convenio.funciona_como_particular = True
        self.outro_convenio.save()
        linha.refresh_from_db()
        self.assertTrue(linha.repasse_pago)
        pendentes = [p.agendamento_id for p in RepassePagamento.pendentes_do_profissional(self.profissional)]
        self.assertNotIn(antigo.pk, pendentes)


class CorrigirRepasseFixoTests(TestCase):
    """Caso da clínica (23/10/2026): Dra. Elaine cobra fixo (R$ 230, repassa R$ 150). Um pagamento
    ficou gravado pelo percentual (30%) — o fechamento mostrava retenção R$ 149 em vez de R$ 160."""

    def setUp(self):
        from cadastros.models import ValorFixoProfissional

        self.paciente = Paciente.objects.create(nome_completo="Paciente Fixo")
        self.elaine = Profissional.objects.create(
            nome="Dra. Elaine", registro_profissional="9182", percentual_desconto=Decimal("30.00"),
            cobra_valor_fixo_particular=True,
        )
        self.consulta = Procedimento.objects.create(nome="Consulta Dermatologista", eh_consulta=True)
        ValorFixoProfissional.objects.create(
            profissional=self.elaine, procedimento=self.consulta, valor_cobrado=Decimal("230"), valor_repasse=Decimal("150")
        )
        self._hora = timezone.make_aware(datetime(2026, 10, 20, 8, 0))

    def _pago(self, forma, repasse_fixo):
        inicio = self._hora
        self._hora += timedelta(hours=1)
        ag = Agendamento.objects.create(
            paciente=self.paciente, profissional=self.elaine, forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
            data_hora_inicio=inicio, data_hora_fim=inicio, status=Agendamento.Status.AUTORIZADO,
        )
        AgendamentoItem.objects.create(agendamento=ag, procedimento=self.consulta, quantidade=1, valor_cobrado=Decimal("230"))
        pagamento = PagamentoParticular.objects.create(
            agendamento=ag, forma_pagamento=forma, valor=Decimal("230"), valor_repasse_fixo=repasse_fixo
        )
        # Registrado no próprio 20/10 — dinheiro dessa época foi entregue na hora (como a agenda.0033 deixa).
        PagamentoParticular.objects.filter(pk=pagamento.pk).update(criado_em=inicio)
        pagamento.refresh_from_db()
        if pagamento.dinheiro_entregue_na_hora:
            pagamento.repasse_pago = True
            pagamento.repasse_valor_pago = pagamento.valor_repasse
            pagamento.save(update_fields=["repasse_pago", "repasse_valor_pago"])
        return pagamento

    def test_lista_e_corrige_o_pagamento_gravado_pelo_percentual(self):
        from io import StringIO

        from django.core.management import call_command

        from .views import _producao_por_profissional

        self._pago(Agendamento.FormaPagamento.DINHEIRO, Decimal("150.00"))
        debito = self._pago(Agendamento.FormaPagamento.CARTAO_DEBITO, None)
        _, totais = _producao_por_profissional(date(2026, 10, 20), date(2026, 10, 20))
        self.assertEqual(totais["retido"], Decimal("149.00"))  # o número estranho do print

        saida = StringIO()
        call_command("corrigir_repasse_fixo", stdout=saida)
        self.assertIn("A CORRIGIR", saida.getvalue())
        debito.refresh_from_db()
        self.assertIsNone(debito.valor_repasse_fixo)  # simulação não altera

        call_command("corrigir_repasse_fixo", "--confirmar", stdout=StringIO())
        debito.refresh_from_db()
        self.assertEqual(debito.valor_repasse_fixo, Decimal("150.00"))
        _, totais = _producao_por_profissional(date(2026, 10, 20), date(2026, 10, 20))
        self.assertEqual(totais["retido"], Decimal("160.00"))  # R$ 80 por consulta
        self.assertEqual(totais["repasse_pendente"], Decimal("150.00"))

    def test_dinheiro_fica_para_acerto_manual(self):
        from io import StringIO

        from django.core.management import call_command

        dinheiro = self._pago(Agendamento.FormaPagamento.DINHEIRO, None)
        saida = StringIO()
        call_command("corrigir_repasse_fixo", "--confirmar", stdout=saida)
        self.assertIn("ACERTO MANUAL", saida.getvalue())
        dinheiro.refresh_from_db()
        self.assertIsNone(dinheiro.valor_repasse_fixo)

class CorrigirPagamentoTests(TestCase):
    """Pedido da clínica (23/10/2026): corrigir um pagamento lançado errado (sem limite de data)."""

    def setUp(self):
        self.paciente = Paciente.objects.create(nome_completo="Paciente Corrige")
        self.profissional = Profissional.objects.create(
            nome="Prof Corrige", registro_profissional="CRM-C", percentual_desconto=Decimal("30.00")
        )
        self.procedimento = Procedimento.objects.create(nome="Consulta Corrige", eh_consulta=True)
        usuario = Usuario.objects.create_user(username="fin_corrige", password="x", exigir_troca_senha=False)
        usuario.user_permissions.add(Permission.objects.get(codename="fila_autorizar_financeiro"))
        self.client.force_login(usuario)
        inicio = timezone.now()
        self.agendamento = Agendamento.objects.create(
            paciente=self.paciente, profissional=self.profissional,
            forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
            data_hora_inicio=inicio, data_hora_fim=inicio + timedelta(minutes=30), status=Agendamento.Status.AUTORIZADO,
        )
        self.url = reverse("financeiro:pagamento_corrigir", args=[self.agendamento.pk])

    def _pagar(self, forma, valor, **extra):
        return PagamentoParticular.objects.create(agendamento=self.agendamento, forma_pagamento=forma, valor=Decimal(valor), **extra)

    def _linhas(self):
        return [(p.forma_pagamento, p.valor, p.valor_recebido, p.parcelas) for p in self.agendamento.pagamentos.order_by("pk")]

    def test_lista_mostra_o_de_hoje_e_troca_a_forma(self):
        from accounts.models import LogAuditoria

        self._pagar(Agendamento.FormaPagamento.PIX, "100.00")
        resposta = self.client.get(reverse("financeiro:pagamento_correcao_lista"))
        self.assertContains(resposta, self.url)
        resposta = self.client.post(self.url, {"forma_1": "CARTAO_CREDITO", "valor_recebido_1": "95.00", "parcelas_1": "2"})
        self.assertRedirects(resposta, reverse("financeiro:pagamento_correcao_lista"), fetch_redirect_response=False)
        self.assertEqual(self._linhas(), [("CARTAO_CREDITO", Decimal("100.00"), Decimal("95.00"), 2)])
        self.assertTrue(LogAuditoria.objects.filter(acao__contains="Corrigiu o pagamento").exists())

    def test_tela_mostra_o_procedimento_cobrado(self):
        AgendamentoItem.objects.create(
            agendamento=self.agendamento, procedimento=self.procedimento, quantidade=1, valor_cobrado=Decimal("100.00")
        )
        self._pagar(Agendamento.FormaPagamento.PIX, "100.00")
        resposta = self.client.get(self.url)
        self.assertContains(resposta, "O que foi cobrado")
        self.assertContains(resposta, "<li>Consulta Corrige —")

    def test_divide_e_junta_formas(self):
        self._pagar(Agendamento.FormaPagamento.DINHEIRO, "100.00")
        self.client.post(self.url, {"forma_1": "DINHEIRO", "valor_1": "40", "forma_2": "PIX", "valor_2": "60"})
        self.assertEqual(
            self._linhas(), [("DINHEIRO", Decimal("40.00"), None, None), ("PIX", Decimal("60.00"), None, None)]
        )
        self.client.post(self.url, {"forma_1": "PIX"})
        self.assertEqual(self._linhas(), [("PIX", Decimal("100.00"), None, None)])

    def test_muda_o_total_com_motivo_e_ajusta_os_procedimentos(self):
        from accounts.models import LogAuditoria

        exame = Procedimento.objects.create(nome="Exame Corrige")
        consulta = AgendamentoItem.objects.create(
            agendamento=self.agendamento, procedimento=self.procedimento, quantidade=1, valor_cobrado=Decimal("60.00")
        )
        item_exame = AgendamentoItem.objects.create(
            agendamento=self.agendamento, procedimento=exame, quantidade=1, valor_cobrado=Decimal("40.00")
        )
        self._pagar(Agendamento.FormaPagamento.PIX, "100.00")

        resposta = self.client.post(self.url, {"forma_1": "PIX", "valor_1": "90"})
        self.assertContains(resposta, "informe o motivo")
        self.assertEqual(self._linhas(), [("PIX", Decimal("100.00"), None, None)])

        self.client.post(self.url, {"forma_1": "PIX", "valor_1": "90", "motivo": "Faltou o desconto autorizado"})
        self.assertEqual(self._linhas(), [("PIX", Decimal("90.00"), None, None)])
        consulta.refresh_from_db()
        item_exame.refresh_from_db()
        self.assertEqual((consulta.valor_cobrado, item_exame.valor_cobrado), (Decimal("54.00"), Decimal("36.00")))
        self.assertTrue(LogAuditoria.objects.filter(acao__contains="Faltou o desconto autorizado").exists())

    def test_com_venda_de_pacote_o_total_nao_muda(self):
        from cadastros.models import Pacote

        from agenda.models import PacoteVendido

        AgendamentoItem.objects.create(
            agendamento=self.agendamento, procedimento=self.procedimento, quantidade=1, valor_cobrado=Decimal("100.00")
        )
        pacote = Pacote.objects.create(nome="Pacote Corrige", valor=Decimal("300.00"))
        PacoteVendido.objects.create(
            pacote=pacote, nome=pacote.nome, paciente=self.paciente, profissional=self.profissional,
            agendamento_venda=self.agendamento, valor=pacote.valor, pago_em=timezone.now(),
        )
        self._pagar(Agendamento.FormaPagamento.PIX, "400.00")
        resposta = self.client.post(self.url, {"forma_1": "PIX", "valor_1": "350", "motivo": "x"})
        self.assertContains(resposta, "venda de pacote")
        self.assertEqual(self._linhas(), [("PIX", Decimal("400.00"), None, None)])

    def test_bloqueia_repasse_ja_pago_mas_libera_lancamento_antigo(self):
        linha = self._pagar(Agendamento.FormaPagamento.PIX, "100.00", repasse_valor_pago=Decimal("10.00"))
        self.client.post(self.url, {"forma_1": "DINHEIRO"})
        self.assertEqual(self._linhas()[0][0], "PIX")  # repasse já pago: não corrige
        # Lançamento de 10 dias atrás (a clínica pediu para abrir além de hoje/ontem).
        PagamentoParticular.objects.filter(pk=linha.pk).update(
            repasse_valor_pago=Decimal("0"), criado_em=timezone.now() - timedelta(days=10)
        )
        lista = self.client.get(reverse("financeiro:pagamento_correcao_lista"))
        self.assertNotContains(lista, self.url)  # fora dos últimos 7 dias (padrão)
        lista = self.client.get(reverse("financeiro:pagamento_correcao_lista"), {"q": "Corrige"})
        self.assertContains(lista, self.url)  # pelo nome do paciente acha em qualquer data
        self.client.post(self.url, {"forma_1": "DINHEIRO"})
        self.assertEqual(self._linhas()[0][0], "DINHEIRO")

    def test_repasse_fixo_e_repartido_de_novo(self):
        self._pagar(Agendamento.FormaPagamento.PIX, "230.00", valor_repasse_fixo=Decimal("150.00"))
        self.client.post(self.url, {"forma_1": "DINHEIRO", "valor_1": "100", "forma_2": "PIX", "valor_2": "130"})
        fixos = list(self.agendamento.pagamentos.order_by("pk").values_list("valor_repasse_fixo", flat=True))
        self.assertEqual(fixos, [Decimal("65.22"), Decimal("84.78")])

    def test_sem_permissao_nao_acessa(self):
        self._pagar(Agendamento.FormaPagamento.PIX, "100.00")
        self.client.force_login(Usuario.objects.create_user(username="sem_perm_corrige", password="x", exigir_troca_senha=False))
        self.assertEqual(self.client.get(self.url).status_code, 403)

class PagamentoEmDobroTests(TestCase):
    """Pagamento gravado em dobro por envio duplo (clínica, 26/10/2026): a tela Corrigir pagamento
    reconhece e apaga as cópias; o comando remover_pagamentos_duplicados faz o mesmo em lote."""

    def setUp(self):
        self.paciente = Paciente.objects.create(nome_completo="Paciente Dobro")
        self.profissional = Profissional.objects.create(
            nome="Prof Dobro", registro_profissional="CRM-D", percentual_desconto=Decimal("30.00")
        )
        procedimento = Procedimento.objects.create(nome="Consulta Dobro", eh_consulta=True)
        usuario = Usuario.objects.create_user(username="fin_dobro", password="x", exigir_troca_senha=False)
        usuario.user_permissions.add(Permission.objects.get(codename="fila_autorizar_financeiro"))
        self.client.force_login(usuario)
        inicio = timezone.now()
        self.agendamento = Agendamento.objects.create(
            paciente=self.paciente, profissional=self.profissional,
            forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
            data_hora_inicio=inicio, data_hora_fim=inicio + timedelta(minutes=30), status=Agendamento.Status.AUTORIZADO,
        )
        self.item = AgendamentoItem.objects.create(
            agendamento=self.agendamento, procedimento=procedimento, quantidade=1, valor_cobrado=Decimal("200.00")
        )
        for _ in range(2):
            self._pagar(Agendamento.FormaPagamento.PIX, "90.00")
            self._pagar(Agendamento.FormaPagamento.DINHEIRO, "110.00")
        self.url = reverse("financeiro:pagamento_corrigir", args=[self.agendamento.pk])

    def _pagar(self, forma, valor):
        return PagamentoParticular.objects.create(agendamento=self.agendamento, forma_pagamento=forma, valor=Decimal(valor))

    def _linhas(self):
        return [(p.forma_pagamento, p.valor) for p in self.agendamento.pagamentos.order_by("pk")]

    def test_corrigir_mostra_o_aviso_e_apaga_as_copias_sem_mexer_no_valor_cobrado(self):
        resposta = self.client.get(self.url)
        self.assertContains(resposta, "gravado em dobro")
        self.assertContains(resposta, "Total registrado: R$ 200,00")

        self.client.post(self.url, {"forma_1": "PIX", "valor_1": "90", "forma_2": "DINHEIRO", "valor_2": "110"})
        self.assertEqual(self._linhas(), [("PIX", Decimal("90.00")), ("DINHEIRO", Decimal("110.00"))])
        self.item.refresh_from_db()
        self.assertEqual(self.item.valor_cobrado, Decimal("200.00"))

    def test_comando_lista_e_depois_apaga(self):
        from io import StringIO

        from django.core.management import call_command

        saida = StringIO()
        call_command("remover_pagamentos_duplicados", stdout=saida)
        self.assertIn("DUPLICADO", saida.getvalue())
        self.assertEqual(len(self._linhas()), 4)

        call_command("remover_pagamentos_duplicados", "--confirmar", stdout=StringIO())
        self.assertEqual(self._linhas(), [("PIX", Decimal("90.00")), ("DINHEIRO", Decimal("110.00"))])

    def test_comando_mantem_a_copia_que_ja_teve_repasse(self):
        from io import StringIO

        from django.core.management import call_command

        copia_dinheiro = self.agendamento.pagamentos.order_by("pk")[3]
        copia_dinheiro.repasse_pago = True
        copia_dinheiro.save()
        call_command("remover_pagamentos_duplicados", "--confirmar", stdout=StringIO())
        self.assertEqual(
            sorted(self.agendamento.pagamentos.values_list("pk", flat=True)),
            sorted([self.agendamento.pagamentos.order_by("pk")[0].pk, copia_dinheiro.pk]),
        )

    def test_pagamento_dividido_normal_nao_e_tocado(self):
        from io import StringIO

        from django.core.management import call_command

        PagamentoParticular.objects.filter(pk__in=list(self.agendamento.pagamentos.order_by("pk").values_list("pk", flat=True)[2:])).delete()
        call_command("remover_pagamentos_duplicados", "--confirmar", stdout=StringIO())
        self.assertEqual(len(self._linhas()), 2)
        self.assertNotContains(self.client.get(self.url), "gravado em dobro")


class ProprietarioSemRepassePendenteTests(MovimentacaoCaixaTestBase):
    """Pedido da clínica (26/10/2026): o pagamento do proprietário não fica como repasse pendente."""

    def setUp(self):
        super().setUp()
        self.paciente = Paciente.objects.create(nome_completo="Paciente Dono")
        self.usuario_financeiro.user_permissions.add(
            *Permission.objects.filter(codename__in=["financeiro_ver_repasses_semanais"])
        )
        self.client.force_login(self.usuario_financeiro)

    def _pagar(self, profissional, forma):
        agora = timezone.now()
        agendamento = Agendamento.objects.create(
            paciente=self.paciente, profissional=profissional, forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
            data_hora_inicio=agora, data_hora_fim=agora + timedelta(minutes=30), status=Agendamento.Status.REALIZADO,
        )
        return PagamentoParticular.objects.create(agendamento=agendamento, forma_pagamento=forma, valor=Decimal("100.00"))

    def test_proprietario_fora_dos_pendentes(self):
        self._pagar(self.proprietario, Agendamento.FormaPagamento.PIX)
        self._pagar(self.proprietario, Agendamento.FormaPagamento.DINHEIRO)
        self._pagar(self.profissional_comum, Agendamento.FormaPagamento.PIX)

        semanal = self.client.get(reverse("agenda:relatorio_repasses_semanal"))
        self.assertEqual([g["profissional"] for g in semanal.context["grupos"]], [self.profissional_comum])
        self.assertEqual(RepassePagamento.saldo_pendente(self.proprietario), Decimal("0.00"))

        fechamento = self.client.get(reverse("financeiro:fechamento_diario"))
        linha_dono = next(l for l in fechamento.context["linhas_profissionais"] if l["profissional"] == self.proprietario)
        self.assertEqual(linha_dono["repasse_pendente"], Decimal("0.00"))
        self.assertEqual(linha_dono["repasse_ja_pago"], Decimal("0.00"))
        self.assertEqual(fechamento.context["totais_profissionais"]["repasse_pendente"], Decimal("70.00"))


class FechamentoProfissionalConvenioComDescontoTests(MovimentacaoCaixaTestBase):
    """Pedido da clínica (26/10/2026): no fechamento de um profissional comum, convênio já sai
    com o desconto da clínica; na visão geral, continua o valor cheio da tabela."""

    def setUp(self):
        super().setUp()
        self.paciente = Paciente.objects.create(nome_completo="Paciente Convênio Desconto")
        self.convenio = Convenio.objects.create(nome="Plano Desconto")
        procedimento = Procedimento.objects.create(nome="Sessão Desconto")
        agora = timezone.now()
        agendamento = Agendamento.objects.create(
            paciente=self.paciente, profissional=self.profissional_comum,
            forma_atendimento=Agendamento.FormaAtendimento.CONVENIO, convenio=self.convenio,
            data_hora_inicio=agora, data_hora_fim=agora + timedelta(minutes=30), status=Agendamento.Status.REALIZADO,
        )
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=procedimento, valor_convenio=Decimal("100.00"))
        self.client.force_login(self.usuario_financeiro)

    def test_filtrado_pelo_profissional_mostra_com_desconto(self):
        resposta = self.client.get(reverse("financeiro:fechamento_diario"), {"profissional": self.profissional_comum.pk})
        self.assertEqual(resposta.context["convenios_valor"], Decimal("70.00"))
        self.assertEqual(resposta.context["convenios"][0]["itens"][0]["valor"], Decimal("70.00"))
        self.assertContains(resposta, "já com o desconto de 30")

    def test_visao_geral_continua_valor_cheio(self):
        resposta = self.client.get(reverse("financeiro:fechamento_diario"))
        self.assertEqual(resposta.context["convenios_valor"], Decimal("100.00"))
        self.assertIsNone(resposta.context["convenio_desconto"])

    def _pagar_particular(self):
        agora = timezone.now()
        agendamento = Agendamento.objects.create(
            paciente=self.paciente, profissional=self.profissional_comum,
            forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
            data_hora_inicio=agora, data_hora_fim=agora + timedelta(minutes=30), status=Agendamento.Status.REALIZADO,
        )
        AgendamentoItem.objects.create(
            agendamento=agendamento, procedimento=Procedimento.objects.create(nome="Consulta Particular Desconto"),
            valor_cobrado=Decimal("200.00"),
        )
        PagamentoParticular.objects.create(
            agendamento=agendamento, forma_pagamento=Agendamento.FormaPagamento.PIX, valor=Decimal("200.00")
        )

    def test_filtrado_pelo_profissional_particular_com_desconto(self):
        """Pedido da clínica (27/10/2026): na Produção, o particular também sai com os 30%."""
        self._pagar_particular()
        resposta = self.client.get(reverse("financeiro:fechamento_diario"), {"profissional": self.profissional_comum.pk})
        self.assertEqual(resposta.context["particular_valor"], Decimal("140.00"))
        self.assertEqual(resposta.context["producao_total"], Decimal("210.00"))

    def test_visao_geral_particular_valor_cobrado(self):
        self._pagar_particular()
        resposta = self.client.get(reverse("financeiro:fechamento_diario"))
        self.assertEqual(resposta.context["particular_valor"], Decimal("200.00"))
        self.assertEqual(resposta.context["producao_total"], Decimal("300.00"))


class MeuFechamentoDeTodoProfissionalTests(TestCase):
    """Clínica, 28/10/2026: todo profissional confere o próprio fechamento diário/mensal —
    basta o usuário estar vinculado a um Profissional, sem marcar permissão no perfil."""

    def test_profissional_sem_permissao_ve_meu_fechamento(self):
        profissional = Profissional.objects.create(
            nome="Fisio Fechamento", registro_profissional="CREFITO-9", especialidade="fisioterapeuta",
            percentual_desconto=0,
        )
        usuario = Usuario.objects.create_user(
            username="fisio_fechamento", password="x", profissional_id=profissional.pk, exigir_troca_senha=False
        )
        self.client.force_login(usuario)
        resposta = self.client.get(reverse("financeiro:meu_fechamento_diario"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, reverse("financeiro:meu_fechamento_mensal"))
        self.assertEqual(self.client.get(reverse("financeiro:meu_fechamento_mensal")).status_code, 200)

    def test_usuario_sem_profissional_nem_permissao_nao_ve(self):
        usuario = Usuario.objects.create_user(username="recepcao_x", password="x", exigir_troca_senha=False)
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(reverse("financeiro:meu_fechamento_diario")).status_code, 403)


class MeuFechamentoComValoresTests(LoteFaturamentoTestBase):
    """Clínica, 28/10/2026: no Meu fechamento o profissional vê quanto leva — convênio com valor
    e particular, os dois já com o desconto da clínica (30% na fixture)."""

    def test_quanto_eu_levo_no_dia_ja_com_desconto(self):
        convenio = self._criar_agendamento(valor_convenio=Decimal("100.00"))
        inicio = convenio.data_hora_inicio + timedelta(hours=2)
        particular = Agendamento.objects.create(
            paciente=self.paciente, profissional=self.profissional,
            forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
            data_hora_inicio=inicio, data_hora_fim=inicio + timedelta(minutes=30), status=Agendamento.Status.REALIZADO,
        )
        PagamentoParticular.objects.create(
            agendamento=particular, forma_pagamento=Agendamento.FormaPagamento.PIX, valor=Decimal("200.00")
        )
        usuario = Usuario.objects.create_user(
            username="prof_quanto_levo", password="x", profissional_id=self.profissional.pk, exigir_troca_senha=False
        )
        self.client.force_login(usuario)

        resposta = self.client.get(reverse("financeiro:meu_fechamento_diario"), {"data": "2026-07-20"})
        self.assertEqual(resposta.context["convenios_valor"], Decimal("70.00"))
        self.assertEqual(resposta.context["particular_liquido"], Decimal("140.00"))
        self.assertEqual(resposta.context["total_liquido"], Decimal("210.00"))
        self.assertContains(resposta, "Quanto eu levo hoje")
        self.assertContains(resposta, "R$ 210,00")

        mensal = self.client.get(reverse("financeiro:meu_fechamento_mensal"), {"ano": 2026, "mes": 7})
        self.assertEqual(mensal.context["total_liquido"], Decimal("210.00"))


class RelatorioDetalhadoPaginadoTests(ValorRecebidoCartaoTestBase):
    """Tabela paginada de 50 em 50, mas os totais do rodapé somam o período inteiro
    (pedido da clínica, 03/11/2026)."""

    def test_pagina_de_50_e_totais_do_periodo_todo(self):
        for _ in range(55):
            self._criar_pagamento(self.profissional_comum, valor=Decimal("10.00"), com_item=True)
        Agendamento.objects.update(data_hora_inicio=timezone.make_aware(datetime(2026, 9, 10, 9, 0)))
        url = reverse("financeiro:relatorio_detalhado")
        resposta = self.client.get(url, {"de": "2026-09-01", "ate": "2026-09-30"})
        self.assertEqual(len(resposta.context["linhas"]), 50)
        self.assertEqual(resposta.context["particular_liquido_total"], Decimal("550.00"))
        self.assertContains(resposta, "Página 1 de 2 (55 registros)")
        self.assertEqual(len(self.client.get(url, {"de": "2026-09-01", "ate": "2026-09-30", "pagina": 2}).context["linhas"]), 5)
        self.assertEqual(len(self.client.get(url, {"de": "2026-09-01", "ate": "2026-09-30", "tudo": 1}).context["linhas"]), 55)
