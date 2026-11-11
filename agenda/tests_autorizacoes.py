"""
Autorização de convênio com observação, quem autorizou e guia de sessões do plano (pedido da
clínica, 15/10/2026 — ver agenda.views.agendamento_autorizar e agenda.models.GuiaConvenio).
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.urls import reverse

from cadastros.models import Procedimento, TabelaPrecoConvenio

from .models import Agendamento, AgendamentoItem, GuiaConvenio
from .tests import AgendaTestBase, Usuario


class _AutorizacaoBase(AgendaTestBase):
    """Fixtures comuns (sem testes próprios, pra não rodarem de novo em cada subclasse)."""

    def setUp(self):
        super().setUp()
        self.sessao = Procedimento.objects.create(nome="Sessão Fono", permite_repeticao_semanal=True)
        self.sessao.profissionais_habilitados.add(self.profissional)
        TabelaPrecoConvenio.objects.create(convenio=self.convenio, procedimento=self.sessao, valor=Decimal("50.00"))
        self.usuario = Usuario.objects.create_user(
            username="recepcao_aut", password="senha-teste-123", exigir_troca_senha=False, first_name="Jonnan"
        )
        for codename in ("fila_autorizar_recepcao", "agenda_editar"):
            self.usuario.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(self.usuario)

    def _atendimento(self, procedimento, quantidade=1):
        agendamento = self._criar_agendamento(
            forma_atendimento=Agendamento.FormaAtendimento.CONVENIO,
            convenio=self.convenio,
            status=Agendamento.Status.CONFIRMADO,
        )
        item = AgendamentoItem.objects.create(agendamento=agendamento, procedimento=procedimento, quantidade=quantidade)
        return agendamento, item

    def _autorizar(self, agendamento, **dados):
        return self.client.post(reverse("agenda:agendamento_autorizar", args=[agendamento.pk]), dados)

    def _guia(self, quantidade=10, validade=None):
        return GuiaConvenio.objects.create(
            paciente=self.paciente, convenio=self.convenio, procedimento=self.sessao,
            numero="2013000387", quantidade_sessoes=quantidade, validade=validade,
        )


class AutorizacaoConvenioTests(_AutorizacaoBase):
    def test_consulta_grava_observacao_numero_e_quem_autorizou(self):
        agendamento, _ = self._atendimento(self.consulta)
        self._autorizar(agendamento, numero_guia="1948291510", observacao="AUT. NO CRM DENNIS")
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.status, Agendamento.Status.AUTORIZADO)
        self.assertEqual(agendamento.numero_guia, "1948291510")
        self.assertEqual(agendamento.observacao_autorizacao, "AUT. NO CRM DENNIS")
        self.assertEqual(agendamento.autorizado_por, self.usuario)

    def test_botao_antigo_leva_para_a_tela_de_autorizacao(self):
        agendamento, _ = self._atendimento(self.consulta)
        resposta = self.client.post(reverse("agenda:agendamento_mudar_status", args=[agendamento.pk, "AUTORIZADO"]))
        self.assertRedirects(
            resposta, reverse("agenda:agendamento_autorizar", args=[agendamento.pk]), fetch_redirect_response=False
        )
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.status, Agendamento.Status.CONFIRMADO)

    def test_sessao_sem_guia_nao_autoriza(self):
        agendamento, item = self._atendimento(self.sessao)
        resposta = self._autorizar(agendamento, **{f"item{item.pk}-guia": "nova"})
        self.assertEqual(resposta.status_code, 200)
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.status, Agendamento.Status.CONFIRMADO)

    def test_sessao_de_1h_consome_2_e_bloqueia_guia_estourada(self):
        guia = self._guia(quantidade=3)
        primeiro, item1 = self._atendimento(self.sessao, quantidade=2)
        self._autorizar(primeiro, **{f"item{item1.pk}-guia": str(guia.pk)})
        primeiro.refresh_from_db()
        self.assertEqual(primeiro.status, Agendamento.Status.AUTORIZADO)
        self.assertEqual(GuiaConvenio.objects.get().sessoes_usadas, 2)

        segundo, item2 = self._atendimento(self.sessao, quantidade=2)
        resposta = self._autorizar(segundo, **{f"item{item2.pk}-guia": str(guia.pk)})
        self.assertContains(resposta, "não pode mais ser usada")
        self.assertContains(resposta, "saldo menor que esta sessão")
        segundo.refresh_from_db()
        self.assertEqual(segundo.status, Agendamento.Status.CONFIRMADO)

    def test_guia_vencida_bloqueia(self):
        guia = self._guia(validade=date(2026, 7, 1))  # atendimentos de teste são em 20/07/2026
        agendamento, item = self._atendimento(self.sessao)
        resposta = self._autorizar(agendamento, **{f"item{item.pk}-guia": str(guia.pk)})
        self.assertContains(resposta, "não pode mais ser usada")
        self.assertContains(resposta, "2013000387 (vencida")
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.status, Agendamento.Status.CONFIRMADO)

    def test_guia_nova_cadastrada_na_autorizacao(self):
        agendamento, item = self._atendimento(self.sessao)
        self._autorizar(agendamento, **{
            f"item{item.pk}-guia": "nova",
            f"item{item.pk}-novo_numero": "777",
            f"item{item.pk}-nova_quantidade": "10",
        })
        guia = GuiaConvenio.objects.get()
        self.assertEqual((guia.numero, guia.quantidade_sessoes, guia.sessoes_usadas), ("777", 10, 1))
        item.refresh_from_db()
        self.assertEqual(item.guia_convenio, guia)

    def test_cancelado_devolve_as_sessoes(self):
        guia = self._guia(quantidade=10)
        agendamento, item = self._atendimento(self.sessao, quantidade=2)
        self._autorizar(agendamento, **{f"item{item.pk}-guia": str(guia.pk)})
        self.assertEqual(GuiaConvenio.objects.get().sessoes_usadas, 2)
        Agendamento.objects.filter(pk=agendamento.pk).update(status=Agendamento.Status.CANCELADO)
        self.assertEqual(GuiaConvenio.objects.get().sessoes_usadas, 0)

    def test_tela_autorizacoes_mostra_a_sessao_da_guia(self):
        guia = self._guia(quantidade=10)
        anterior, item0 = self._atendimento(self.sessao)
        self._autorizar(anterior, **{f"item{item0.pk}-guia": str(guia.pk)})
        agendamento, item = self._atendimento(self.sessao, quantidade=2)
        self._autorizar(agendamento, observacao="sessão da semana", **{f"item{item.pk}-guia": str(guia.pk)})
        resposta = self.client.get(reverse("agenda:autorizacoes"), {"de": "2026-07-20"})
        self.assertContains(resposta, "2013000387: sessão 2–3 de 10")
        self.assertContains(resposta, "sessão da semana")
        self.assertContains(resposta, "Jonnan")

    def test_editar_guia_nao_libera_menos_que_o_usado(self):
        guia = self._guia(quantidade=10)
        agendamento, item = self._atendimento(self.sessao, quantidade=2)
        self._autorizar(agendamento, **{f"item{item.pk}-guia": str(guia.pk)})
        resposta = self.client.post(
            reverse("agenda:guia_convenio_editar", args=[guia.pk]), {"numero": "2013000387", "quantidade_sessoes": "1"}
        )
        self.assertContains(resposta, "já tem 2 sessões usadas")
        self.assertContains(self.client.get(reverse("agenda:guias_convenio")), "2013000387")


class DataDaGuiaExtraTests(AutorizacaoConvenioTests):
    """Data em que o plano autorizou a 2ª guia (pode ser antes do atendimento)."""

    def _sessao_autorizada(self, quantidade=2):
        agendamento, item = self._atendimento(self.sessao, quantidade=quantidade)
        Agendamento.objects.filter(pk=agendamento.pk).update(status=Agendamento.Status.AUTORIZADO)
        return item

    def _marcar(self, item, **dados):
        return self.client.post(reverse("agenda:guia_autorizar", args=[item.pk, "autorizar"]), dados)

    def test_grava_a_data_informada(self):
        item = self._sessao_autorizada(quantidade=3)  # 1h30: continua pendente na lista
        self._marcar(item, autorizada_em="2026-07-18")
        item.refresh_from_db()
        self.assertEqual(item.guias_autorizadas, 2)
        self.assertEqual(item.guias_extras.get().autorizada_em, date(2026, 7, 18))
        self.assertContains(
            self.client.get(reverse("agenda:guias_pendentes")), "2ª guia autorizada em 18/07/2026"
        )

    def test_data_no_futuro_nao_marca(self):
        item = self._sessao_autorizada()
        self._marcar(item, autorizada_em="2999-01-01")
        item.refresh_from_db()
        self.assertEqual(item.guias_autorizadas, 1)
        self.assertFalse(item.guias_extras.exists())

    def test_desfazer_apaga_a_data(self):
        item = self._sessao_autorizada()
        self._marcar(item)  # sem data: vale hoje
        self.assertEqual(item.guias_extras.count(), 1)
        self.client.post(reverse("agenda:guia_autorizar", args=[item.pk, "desfazer"]))
        item.refresh_from_db()
        self.assertEqual(item.guias_autorizadas, 1)
        self.assertFalse(item.guias_extras.exists())


class SemAssinaturaTests(_AutorizacaoBase):
    """Pedido da clínica (2026-10-16): autorizar "sem assinatura" — 1ª guia e guias extras —
    e ver em Guias pendentes (inclusive a profissional, só os dela) até marcar "Assinou"."""

    def test_1a_guia_sem_assinatura_aparece_em_guias_pendentes_ate_assinar(self):
        guia = self._guia()
        agendamento, item = self._atendimento(self.sessao, quantidade=2)
        self._autorizar(agendamento, sem_assinatura="on", **{f"item{item.pk}-guia": str(guia.pk)})
        item.refresh_from_db()
        self.assertTrue(item.sem_assinatura)
        self.assertEqual(item.assinaturas_pendentes, [1])

        tela = self.client.get(reverse("agenda:guias_pendentes"), {"situacao": "assinatura"})
        self.assertContains(tela, "1ª guia sem assinatura")

        from django.utils import timezone

        hoje = timezone.localdate()
        # Data no futuro é recusada.
        self.client.post(
            reverse("agenda:guia_autorizar", args=[item.pk, "assinou"]),
            {"numero": "1", "assinada_em": (hoje + timedelta(days=1)).isoformat()},
        )
        item.refresh_from_db()
        self.assertTrue(item.sem_assinatura)

        ontem = hoje - timedelta(days=1)
        self.client.post(
            reverse("agenda:guia_autorizar", args=[item.pk, "assinou"]), {"numero": "1", "assinada_em": ontem.isoformat()}
        )
        item.refresh_from_db()
        self.assertFalse(item.sem_assinatura)
        self.assertEqual((item.assinada_em, item.assinatura_registrada_por), (ontem, self.usuario))
        self.assertNotIn(item, AgendamentoItem.com_assinatura_pendente())

        # O registro continua visível em "Pendentes e já autorizadas/assinadas".
        tela = self.client.get(reverse("agenda:guias_pendentes"), {"situacao": "todas"})
        self.assertContains(tela, f"1ª guia assinada em {ontem:%d/%m/%Y} (registrado por Jonnan)")

    def test_consulta_sem_assinatura_tambem_fica_pendente(self):
        agendamento, item = self._atendimento(self.consulta)
        self._autorizar(agendamento, sem_assinatura="on")
        self.assertEqual(list(AgendamentoItem.com_assinatura_pendente()), [item])

    def test_2a_guia_sem_assinatura(self):
        guia = self._guia()
        agendamento, item = self._atendimento(self.sessao, quantidade=2)
        self._autorizar(agendamento, **{f"item{item.pk}-guia": str(guia.pk)})
        self.client.post(reverse("agenda:guia_autorizar", args=[item.pk, "autorizar"]), {"sem_assinatura": "1"})
        item.refresh_from_db()
        self.assertEqual(item.guias_autorizadas, 2)
        self.assertEqual(item.assinaturas_pendentes, [2])
        self.client.post(reverse("agenda:guia_autorizar", args=[item.pk, "assinou"]), {"numero": "2"})
        item.refresh_from_db()
        self.assertEqual(item.assinaturas_pendentes, [])
        registro = item.assinaturas_registradas
        self.assertEqual([(n, quem) for n, _data, quem in registro], [(2, self.usuario)])

    def test_profissional_ve_so_as_proprias_e_sem_botoes(self):
        from cadastros.models import Profissional

        agendamento, item = self._atendimento(self.consulta)
        self._autorizar(agendamento, sem_assinatura="on")
        outro = Profissional.objects.create(nome="Outra", registro_profissional="X", percentual_desconto=Decimal("30"))
        outro_ag = self._criar_agendamento(
            forma_atendimento=Agendamento.FormaAtendimento.CONVENIO, convenio=self.convenio,
            status=Agendamento.Status.AUTORIZADO,
        )
        outro_ag.profissional = outro
        outro_ag.save()
        AgendamentoItem.objects.create(agendamento=outro_ag, procedimento=self.consulta, sem_assinatura=True)

        prof = Usuario.objects.create_user(
            username="prof_ass", password="x", profissional_id=self.profissional.pk, exigir_troca_senha=False
        )
        prof.user_permissions.add(Permission.objects.get(codename="agenda_ver_propria"))
        self.client.force_login(prof)
        tela = self.client.get(reverse("agenda:guias_pendentes"), {"profissional": str(outro.pk)})
        self.assertEqual([i.pk for i in tela.context["itens"]], [item.pk])
        self.assertNotContains(tela, ">Assinou<")
        self.assertContains(tela, "Minhas guias pendentes")

    def test_quem_nao_e_profissional_nem_recepcao_nao_entra(self):
        sem_nada = Usuario.objects.create_user(username="nada", password="x", exigir_troca_senha=False)
        self.client.force_login(sem_nada)
        self.assertEqual(self.client.get(reverse("agenda:guias_pendentes")).status_code, 403)


class RetornoSistemaAntigoTests(_AutorizacaoBase):
    """Pedido da clínica (2026-10-16, provisório): retorno de consulta feita no sistema antigo."""

    def test_item_marcado_vale_como_retorno_gratis(self):
        agendamento, item = self._atendimento(self.consulta)
        item.retorno_manual = True
        item.save()
        self.assertTrue(item.eh_retorno)
        self.assertEqual(item.valor_a_cobrar, Decimal("0.00"))
        self.assertFalse(agendamento.tem_item_cobravel)
        self.assertTrue(agendamento.tem_item_retorno)
        self.assertFalse(item.precisa_guia_convenio)

    def test_consulta_com_retorno_manual_nao_da_direito_a_outro_retorno(self):
        agendamento, item = self._atendimento(self.consulta)
        item.retorno_manual = True
        item.save()
        Agendamento.objects.filter(pk=agendamento.pk).update(status=Agendamento.Status.AUTORIZADO)
        encontrado = AgendamentoItem.buscar_item_para_retorno(
            self.paciente, "medico_clinico_geral", "", agendamento.data_hora_inicio + timedelta(days=5)
        )
        self.assertIsNone(encontrado)

    def test_formulario_marca_so_o_item_de_consulta(self):
        self.usuario.user_permissions.add(Permission.objects.get(codename="agenda_criar"))
        dados = {
            "paciente": self.paciente.pk,
            "profissional": self.profissional.pk,
            "forma_atendimento": "PARTICULAR",
            "data": "2026-07-21",
            "turno": "MANHA",
            "retorno_sistema_antigo": "on",
            "itens-TOTAL_FORMS": "2",
            "itens-INITIAL_FORMS": "0",
            "itens-MIN_NUM_FORMS": "0",
            "itens-MAX_NUM_FORMS": "1000",
            "itens-0-procedimento": self.consulta.pk,
            "itens-0-quantidade": "1",
            "itens-1-procedimento": self.exame.pk,
            "itens-1-quantidade": "1",
        }
        resposta = self.client.post(reverse("agenda:agendamento_novo"), dados)
        self.assertEqual(resposta.status_code, 302, getattr(resposta, "context", None) and resposta.context.get("formset").non_form_errors())
        agendamento = Agendamento.objects.latest("pk")
        marcados = {i.procedimento_id: i.retorno_manual for i in agendamento.itens.all()}
        self.assertEqual(marcados, {self.consulta.pk: True, self.exame.pk: False})


class CancelamentoEmDoisCriteriosTests(AgendaTestBase):
    """Pedido da clínica (23/10/2026): na agenda só se cancela o que ainda não foi
    autorizado; o já autorizado se cancela em Autorizações, com permissão própria."""

    def setUp(self):
        super().setUp()
        self.recepcao = Usuario.objects.create_user(username="recepcao_cancel", password="x", exigir_troca_senha=False)
        for codename in ("agenda_editar", "agenda_ver_dia", "fila_autorizar_recepcao"):
            self.recepcao.user_permissions.add(Permission.objects.get(codename=codename))
        self.supervisor = Usuario.objects.create_user(username="supervisor_cancel", password="x", exigir_troca_senha=False)
        self.supervisor.user_permissions.add(Permission.objects.get(codename="agenda_cancelar_autorizado"))

    def _status(self, agendamento):
        agendamento.refresh_from_db()
        return agendamento.status

    def test_agenda_cancela_o_que_nao_foi_autorizado(self):
        self.client.force_login(self.recepcao)
        confirmado = self._criar_agendamento(status=Agendamento.Status.CONFIRMADO)
        self.client.post(reverse("agenda:agendamento_cancelar", args=[confirmado.pk]))
        self.assertEqual(self._status(confirmado), Agendamento.Status.CANCELADO)

    def test_agenda_nao_cancela_o_ja_autorizado(self):
        self.client.force_login(self.recepcao)
        for status in (Agendamento.Status.AUTORIZADO, Agendamento.Status.REALIZADO):
            agendamento = self._criar_agendamento(status=status)
            self.client.post(reverse("agenda:agendamento_cancelar", args=[agendamento.pk]))
            self.assertEqual(self._status(agendamento), status)
            # nem pelo atalho de mudar status direto na URL
            resposta = self.client.post(
                reverse("agenda:agendamento_mudar_status", args=[agendamento.pk, Agendamento.Status.CANCELADO])
            )
            self.assertEqual(resposta.status_code, 403)
            self.assertEqual(self._status(agendamento), status)

    def test_faltou_so_antes_da_autorizacao(self):
        self.client.force_login(self.recepcao)
        confirmado = self._criar_agendamento(status=Agendamento.Status.CONFIRMADO)
        autorizado = self._criar_agendamento(status=Agendamento.Status.AUTORIZADO)
        for agendamento in (confirmado, autorizado):
            self.client.post(reverse("agenda:agendamento_mudar_status", args=[agendamento.pk, Agendamento.Status.FALTOU]))
        self.assertEqual(self._status(confirmado), Agendamento.Status.FALTOU)
        self.assertEqual(self._status(autorizado), Agendamento.Status.AUTORIZADO)

        resposta = self.client.get(reverse("agenda:agenda_dia"), {"data": "2026-07-20"})
        self.assertNotContains(
            resposta, reverse("agenda:agendamento_mudar_status", args=[autorizado.pk, Agendamento.Status.FALTOU])
        )

    def test_agenda_do_dia_nao_mostra_cancelar_no_autorizado(self):
        self.client.force_login(self.recepcao)
        autorizado = self._criar_agendamento(status=Agendamento.Status.AUTORIZADO)
        confirmado = self._criar_agendamento(status=Agendamento.Status.CONFIRMADO)
        resposta = self.client.get(reverse("agenda:agenda_dia"), {"data": "2026-07-20"})
        self.assertContains(resposta, reverse("agenda:agendamento_cancelar", args=[confirmado.pk]))
        self.assertNotContains(resposta, reverse("agenda:agendamento_cancelar", args=[autorizado.pk]))

    def test_serie_futura_pula_sessoes_ja_autorizadas(self):
        import uuid

        self.client.force_login(self.recepcao)
        serie = uuid.uuid4()
        sessoes = [self._criar_agendamento(status=Agendamento.Status.CONFIRMADO) for _ in range(3)]
        sessoes[1].status = Agendamento.Status.AUTORIZADO
        sessoes[1].save()
        Agendamento.objects.filter(pk__in=[s.pk for s in sessoes]).update(serie_recorrencia=serie)
        self.client.post(reverse("agenda:agendamento_cancelar", args=[sessoes[0].pk]), {"modo": "serie_futura"})
        self.assertEqual(
            [self._status(s) for s in sessoes],
            [Agendamento.Status.CANCELADO, Agendamento.Status.AUTORIZADO, Agendamento.Status.CANCELADO],
        )

    def test_cancelar_autorizado_exige_permissao(self):
        self.client.force_login(self.recepcao)  # recepção comum, sem a permissão nova
        autorizado = self._criar_agendamento(status=Agendamento.Status.AUTORIZADO)
        url = reverse("agenda:agendamento_cancelar_autorizado", args=[autorizado.pk])
        self.assertEqual(self.client.post(url, {"motivo": "x"}).status_code, 403)
        self.assertEqual(self._status(autorizado), Agendamento.Status.AUTORIZADO)
        resposta = self.client.get(reverse("agenda:autorizacoes"), {"de": "2026-07-20"})
        self.assertNotContains(resposta, url)

    def test_cancelar_autorizado_com_motivo_em_autorizacoes(self):
        from accounts.models import LogAuditoria

        self.client.force_login(self.supervisor)
        autorizado = self._criar_agendamento(status=Agendamento.Status.AUTORIZADO)
        url = reverse("agenda:agendamento_cancelar_autorizado", args=[autorizado.pk])
        resposta = self.client.get(reverse("agenda:autorizacoes"), {"de": "2026-07-20"})
        self.assertContains(resposta, url)

        resposta = self.client.post(url, {"motivo": ""})
        self.assertEqual(resposta.status_code, 200)  # sem motivo não cancela
        self.assertEqual(self._status(autorizado), Agendamento.Status.AUTORIZADO)

        resposta = self.client.post(url, {"motivo": "Paciente passou mal e foi embora"})
        self.assertRedirects(resposta, reverse("agenda:autorizacoes"), fetch_redirect_response=False)
        self.assertEqual(self._status(autorizado), Agendamento.Status.CANCELADO)
        self.assertIn("Paciente passou mal", autorizado.observacoes)
        self.assertTrue(LogAuditoria.objects.filter(acao__contains="JÁ AUTORIZADO").exists())

    def test_cancelar_autorizado_nao_vale_para_o_que_nao_foi_autorizado(self):
        self.client.force_login(self.supervisor)
        confirmado = self._criar_agendamento(status=Agendamento.Status.CONFIRMADO)
        self.client.post(reverse("agenda:agendamento_cancelar_autorizado", args=[confirmado.pk]), {"motivo": "x"})
        self.assertEqual(self._status(confirmado), Agendamento.Status.CONFIRMADO)

    def test_autorizado_em_lote_de_faturamento_nao_cancela(self):
        from datetime import date

        from financeiro.models import LoteFaturamento

        self.client.force_login(self.supervisor)
        autorizado = self._criar_agendamento(
            forma_atendimento=Agendamento.FormaAtendimento.CONVENIO, convenio=self.convenio,
            status=Agendamento.Status.AUTORIZADO,
        )
        lote = LoteFaturamento.objects.create(convenio=self.convenio, data_envio=date(2026, 7, 31), valor_total_enviado=Decimal("100.00"))
        lote.agendamentos.add(autorizado)
        url = reverse("agenda:agendamento_cancelar_autorizado", args=[autorizado.pk])
        self.assertContains(self.client.post(url, {"motivo": "x"}), "lote de faturamento")
        self.assertEqual(self._status(autorizado), Agendamento.Status.AUTORIZADO)

class VezGuardadaNoPagamentoTests(AgendaTestBase):
    """Pedido da clínica (23/10/2026): particular encaminhado pro financeiro guarda a vez —
    não perde pra um convênio autorizado enquanto está pagando."""

    def test_quem_foi_pagar_continua_na_frente(self):
        from .views import _ordena_fila_de_chamada

        usuario = Usuario.objects.create_user(username="recepcao_vez", password="x", exigir_troca_senha=False)
        for codename in ("agenda_editar", "fila_autorizar_recepcao"):
            usuario.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(usuario)

        particular = self._criar_agendamento(status=Agendamento.Status.CONFIRMADO)
        convenio = self._criar_agendamento(
            forma_atendimento=Agendamento.FormaAtendimento.CONVENIO, convenio=self.convenio,
            status=Agendamento.Status.CONFIRMADO,
        )
        AgendamentoItem.objects.create(agendamento=particular, procedimento=self.consulta, quantidade=1)

        self.client.post(reverse("agenda:agendamento_mudar_status", args=[particular.pk, Agendamento.Status.PRE_AUTORIZADO]))
        particular.refresh_from_db()
        self.assertEqual(particular.status, Agendamento.Status.PRE_AUTORIZADO)
        encaminhado = particular.encaminhado_pagamento_em
        self.assertIsNotNone(encaminhado)

        # convênio autorizado enquanto o particular está no caixa; o pagamento sai depois
        Agendamento.objects.filter(pk=convenio.pk).update(
            status=Agendamento.Status.AUTORIZADO, autorizado_em=encaminhado + timedelta(minutes=1)
        )
        Agendamento.objects.filter(pk=particular.pk).update(
            status=Agendamento.Status.AUTORIZADO, autorizado_em=encaminhado + timedelta(minutes=10)
        )
        ordem = _ordena_fila_de_chamada(
            list(Agendamento.objects.filter(pk__in=[particular.pk, convenio.pk]).prefetch_related("itens__procedimento"))
        )
        self.assertEqual([a.pk for a in ordem], [particular.pk, convenio.pk])

class CancelarNaHoraDoPagamentoTests(AgendaTestBase):
    """Pedido da clínica (23/10/2026): paciente que desiste no caixa é cancelado ali mesmo."""

    def setUp(self):
        super().setUp()
        self.financeiro = Usuario.objects.create_user(username="financeiro_cancel", password="x", exigir_troca_senha=False)
        self.financeiro.user_permissions.add(Permission.objects.get(codename="fila_autorizar_financeiro"))
        self.client.force_login(self.financeiro)
        self.agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=self.agendamento, procedimento=self.consulta, quantidade=1)
        self.url = reverse("agenda:agendamento_confirmar_pagamento", args=[self.agendamento.pk])

    def test_tela_mostra_o_botao(self):
        self.assertContains(self.client.get(self.url), 'value="cancelar_atendimento"')

    def test_cancela_e_sai_da_fila(self):
        resposta = self.client.post(self.url, {"acao": "cancelar_atendimento", "motivo_cancelamento": "Achou caro"})
        self.assertRedirects(resposta, reverse("financeiro:fila_pagamento_lista"), fetch_redirect_response=False)
        self.agendamento.refresh_from_db()
        self.assertEqual(self.agendamento.status, Agendamento.Status.CANCELADO)
        self.assertIn("Achou caro", self.agendamento.observacoes)

    def test_sem_motivo_registra_desistencia(self):
        self.client.post(self.url, {"acao": "cancelar_atendimento"})
        self.agendamento.refresh_from_db()
        self.assertEqual(self.agendamento.status, Agendamento.Status.CANCELADO)
        self.assertIn("paciente desistiu", self.agendamento.observacoes)

class ListagemAtendidosTests(AgendaTestBase):
    """Pedido da clínica (23/10/2026): o profissional confere o que já atendeu."""

    def setUp(self):
        super().setUp()
        from cadastros.models import Profissional

        self.usuario = Usuario.objects.create_user(
            username="prof_atendidos", password="x", exigir_troca_senha=False, profissional_id=self.profissional.pk
        )
        self.usuario.user_permissions.add(Permission.objects.get(codename="agenda_ver_propria"))
        self.client.force_login(self.usuario)
        self.outro = Profissional.objects.create(
            nome="Outro Prof", registro_profissional="CRM-OUTRO", percentual_desconto=Decimal("30.00"),
            especialidade="medico_clinico_geral",
        )
        self.url = reverse("agenda:listagem_atendidos")

    def _lista(self, **filtros):
        return self.client.get(self.url, {"de": "2026-07-20", **filtros}).context["linhas"]

    def test_mostra_so_os_proprios_ja_autorizados(self):
        realizado = self._criar_agendamento(status=Agendamento.Status.REALIZADO)
        autorizado = self._criar_agendamento(status=Agendamento.Status.AUTORIZADO)
        self._criar_agendamento(status=Agendamento.Status.CONFIRMADO)
        self._criar_agendamento(status=Agendamento.Status.CANCELADO)
        de_outro = self._criar_agendamento(status=Agendamento.Status.REALIZADO)
        de_outro.profissional = self.outro
        de_outro.save()
        self.assertEqual({a.pk for a in self._lista()}, {realizado.pk, autorizado.pk})
        self.assertEqual([a.pk for a in self._lista(situacao="atendido")], [realizado.pk])
        self.assertEqual([a.pk for a in self._lista(situacao="nao_marcado")], [autorizado.pk])

    def test_outro_dia_nao_aparece_e_menu_tem_o_link(self):
        self._criar_agendamento(status=Agendamento.Status.REALIZADO)
        self.assertEqual(self._lista(de="2026-07-21"), [])
        self.assertContains(self.client.get(self.url), "Listagem de atendidos")

    def test_sem_permissao_nao_acessa(self):
        sem = Usuario.objects.create_user(username="sem_perm_atendidos", password="x", exigir_troca_senha=False)
        self.client.force_login(sem)
        self.assertEqual(self.client.get(self.url).status_code, 403)

class RetornoSistemaAntigoSemFinanceiroTests(AgendaTestBase):
    """Pedido da clínica (23/10/2026): retorno do sistema antigo (sem cobrança) é autorizado
    direto ao encaminhar, sem entrar na fila do financeiro."""

    def setUp(self):
        super().setUp()
        usuario = Usuario.objects.create_user(username="recepcao_ret_antigo", password="x", exigir_troca_senha=False)
        for codename in ("agenda_editar", "agenda_ver_dia", "fila_autorizar_recepcao"):
            usuario.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(usuario)

    def _encaminhar(self, agendamento):
        self.client.post(reverse("agenda:agendamento_mudar_status", args=[agendamento.pk, Agendamento.Status.PRE_AUTORIZADO]))
        agendamento.refresh_from_db()
        return agendamento

    def test_retorno_antigo_vai_direto_para_autorizado(self):
        from .models import PagamentoParticular

        agendamento = self._criar_agendamento(status=Agendamento.Status.CONFIRMADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1, retorno_manual=True)
        resposta = self.client.get(reverse("agenda:agenda_dia"), {"data": "2026-07-20"})
        self.assertContains(resposta, "Autorizar (retorno, sem cobrança)")

        agendamento = self._encaminhar(agendamento)
        self.assertEqual(agendamento.status, Agendamento.Status.AUTORIZADO)
        self.assertIsNotNone(agendamento.autorizado_em)
        pagamento = PagamentoParticular.objects.get(agendamento=agendamento)
        self.assertEqual(pagamento.forma_pagamento, Agendamento.FormaPagamento.CORTESIA)
        self.assertEqual(pagamento.valor, Decimal("0"))

    def test_com_item_cobravel_continua_indo_para_o_financeiro(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.CONFIRMADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1, retorno_manual=True)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.exame, quantidade=1)
        self.assertEqual(self._encaminhar(agendamento).status, Agendamento.Status.PRE_AUTORIZADO)

    def test_consulta_normal_continua_indo_para_o_financeiro(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.CONFIRMADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)
        self.assertEqual(self._encaminhar(agendamento).status, Agendamento.Status.PRE_AUTORIZADO)

    def test_retorno_automatico_tambem_vai_direto(self):
        original = self._criar_agendamento(status=Agendamento.Status.REALIZADO)
        item_original = AgendamentoItem.objects.create(agendamento=original, procedimento=self.consulta, quantidade=1)
        agendamento = self._criar_agendamento(status=Agendamento.Status.CONFIRMADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1, retorno_de=item_original)
        self.assertEqual(self._encaminhar(agendamento).status, Agendamento.Status.AUTORIZADO)

class RetornosPorDiaTests(AgendaTestBase):
    """Pedido da clínica (23/10/2026): até 5 retornos por profissional por dia; o 6º em
    diante vira encaixe sozinho, pra ser justificado depois."""

    def setUp(self):
        super().setUp()
        usuario = Usuario.objects.create_user(username="recepcao_retornos", password="x", exigir_troca_senha=False)
        for codename in ("agenda_criar", "agenda_editar", "agenda_ver_dia"):
            usuario.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(usuario)

    def _retornos_existentes(self, quantidade, dia=None):
        from datetime import date as d
        from datetime import datetime as dt

        from django.utils import timezone as tz

        for _ in range(quantidade):
            inicio = tz.make_aware(dt.combine(dia or d(2026, 7, 21), dt.min.time().replace(hour=8)))
            ag = Agendamento.objects.create(
                paciente=self.paciente, profissional=self.profissional, data_hora_inicio=inicio, data_hora_fim=inicio,
            )
            AgendamentoItem.objects.create(agendamento=ag, procedimento=self.consulta, quantidade=1, retorno_manual=True)

    def _novo_retorno(self, **extra):
        dados = {
            **extra,
            "paciente": self.paciente.pk,
            "profissional": self.profissional.pk,
            "forma_atendimento": "PARTICULAR",
            "data": "2026-07-21",
            "turno": "MANHA",
            "retorno_sistema_antigo": "on",
            "itens-TOTAL_FORMS": "1",
            "itens-INITIAL_FORMS": "0",
            "itens-MIN_NUM_FORMS": "0",
            "itens-MAX_NUM_FORMS": "1000",
            "itens-0-procedimento": self.consulta.pk,
            "itens-0-quantidade": "1",
        }
        resposta = self.client.post(reverse("agenda:agendamento_novo"), dados, follow=True)
        return Agendamento.objects.latest("pk"), resposta

    def test_quinto_retorno_e_normal(self):
        self._retornos_existentes(4)
        agendamento, _ = self._novo_retorno()
        self.assertFalse(agendamento.encaixe)

    def test_editar_so_marcando_retorno_antigo_salva(self):
        """Relato da clínica (06/11/2026): esqueceram de marcar o retorno ao agendar e, ao
        editar marcando só o checkbox (sem mexer no procedimento), não salvava."""
        from datetime import datetime as dt

        from django.utils import timezone as tz

        inicio = tz.make_aware(dt(2026, 7, 21, 8, 0))
        ag = Agendamento.objects.create(
            paciente=self.paciente, profissional=self.profissional, data_hora_inicio=inicio, data_hora_fim=inicio,
        )
        item = AgendamentoItem.objects.create(agendamento=ag, procedimento=self.consulta, quantidade=1)
        dados = {
            "paciente": self.paciente.pk,
            "profissional": self.profissional.pk,
            "forma_atendimento": "PARTICULAR",
            "data": "2026-07-21",
            "turno": "MANHA",
            "itens-TOTAL_FORMS": "1",
            "itens-INITIAL_FORMS": "1",
            "itens-MIN_NUM_FORMS": "0",
            "itens-MAX_NUM_FORMS": "1000",
            "itens-0-id": item.pk,
            "itens-0-agendamento": ag.pk,
            "itens-0-procedimento": self.consulta.pk,
            "itens-0-quantidade": "1",
        }
        url = reverse("agenda:agendamento_editar", args=[ag.pk])
        self.client.post(url, {**dados, "retorno_sistema_antigo": "on"})
        item.refresh_from_db()
        self.assertTrue(item.retorno_manual)

        # E desmarcar também tem que valer.
        self.client.post(url, dados)
        item.refresh_from_db()
        self.assertFalse(item.retorno_manual)

    def test_sexto_retorno_trava_sem_motivo(self):
        """Desde 06/11/2026 (pedido da clínica): acima do limite só salva com o motivo."""
        self._retornos_existentes(5)
        total_antes = Agendamento.objects.count()
        _, resposta = self._novo_retorno()
        self.assertEqual(Agendamento.objects.count(), total_antes)
        self.assertContains(resposta, "Escreva nas observações o motivo do encaixe")

    def test_sexto_retorno_com_motivo_salva_como_encaixe(self):
        self._retornos_existentes(5)
        agendamento, resposta = self._novo_retorno(observacoes="Paciente com dor, médico autorizou")
        self.assertTrue(agendamento.encaixe)
        self.assertEqual(agendamento.observacoes, "Paciente com dor, médico autorizou")
        self.assertContains(resposta, "marcado como ENCAIXE")

    def _agendamento_hoje(self, encaixe=False):
        from django.utils import timezone as tz

        inicio = tz.make_aware(datetime.combine(tz.localdate(), time(8, 0)))
        ag = Agendamento.objects.create(
            paciente=self.paciente, profissional=self.profissional, data_hora_inicio=inicio, data_hora_fim=inicio,
            encaixe=encaixe,
        )
        item = AgendamentoItem.objects.create(agendamento=ag, procedimento=self.consulta, quantidade=1)
        return ag, item

    def _dados_edicao(self, ag, item, **extra):
        from django.utils import timezone as tz

        return {
            "paciente": self.paciente.pk,
            "profissional": self.profissional.pk,
            "forma_atendimento": "PARTICULAR",
            "data": tz.localdate().isoformat(),
            "turno": "MANHA",
            "itens-TOTAL_FORMS": "1",
            "itens-INITIAL_FORMS": "1",
            "itens-MIN_NUM_FORMS": "0",
            "itens-MAX_NUM_FORMS": "1000",
            "itens-0-id": item.pk,
            "itens-0-agendamento": ag.pk,
            "itens-0-procedimento": self.consulta.pk,
            "itens-0-quantidade": "1",
            **extra,
        }

    def test_edicao_no_dia_acima_do_limite_nao_trava(self):
        """Edição no dia do atendimento (corrigir algo errado) não para o fluxo: vira encaixe
        sem motivo, com "justificar" na Agenda do dia."""
        from django.utils import timezone as tz

        self._retornos_existentes(5, dia=tz.localdate())
        ag, item = self._agendamento_hoje()
        resposta = self.client.post(
            reverse("agenda:agendamento_editar", args=[ag.pk]),
            self._dados_edicao(ag, item, retorno_sistema_antigo="on"),
            follow=True,
        )
        ag.refresh_from_db()
        item.refresh_from_db()
        self.assertTrue(item.retorno_manual)
        self.assertTrue(ag.encaixe)
        self.assertEqual(ag.observacoes, "")
        self.assertContains(resposta, "Justifique depois")
        agenda = self.client.get(reverse("agenda:agenda_dia"), {"data": tz.localdate().isoformat()})
        self.assertContains(agenda, "justificar")

    def test_edicao_no_dia_de_encaixe_sem_motivo_nao_trava(self):
        ag, item = self._agendamento_hoje(encaixe=True)
        self.client.post(
            reverse("agenda:agendamento_editar", args=[ag.pk]),
            self._dados_edicao(ag, item, encaixe="on", turno="TARDE"),
        )
        ag.refresh_from_db()
        self.assertEqual(ag.turno, "TARDE")

    def test_edicao_em_outro_dia_acima_do_limite_trava(self):
        self._retornos_existentes(5)
        from datetime import datetime as dt

        from django.utils import timezone as tz

        inicio = tz.make_aware(dt(2026, 7, 21, 8, 0))
        ag = Agendamento.objects.create(
            paciente=self.paciente, profissional=self.profissional, data_hora_inicio=inicio, data_hora_fim=inicio,
        )
        item = AgendamentoItem.objects.create(agendamento=ag, procedimento=self.consulta, quantidade=1)
        dados = self._dados_edicao(ag, item, retorno_sistema_antigo="on", data="2026-07-21")
        resposta = self.client.post(reverse("agenda:agendamento_editar", args=[ag.pk]), dados)
        self.assertContains(resposta, "Escreva nas observações o motivo do encaixe")
        item.refresh_from_db()
        self.assertFalse(item.retorno_manual)

    def test_cancelado_nao_conta(self):
        self._retornos_existentes(5)
        um = Agendamento.objects.filter(profissional=self.profissional).first()
        Agendamento.objects.filter(pk=um.pk).update(status=Agendamento.Status.CANCELADO)
        agendamento, _ = self._novo_retorno()
        self.assertFalse(agendamento.encaixe)


class LimiteDiarioTodosUltrassonsTests(AgendaTestBase):
    """Pedido da clínica (23/10/2026): um limite só para todos os USG."""

    def test_soma_todos_os_ultrassons(self):
        from cadastros.models import LimiteDiarioProcedimento, Procedimento

        from .views import _verificar_limite_diario

        usg_abd = Procedimento.objects.create(nome="USG Abdome", eh_ultrassom=True, valor_particular=Decimal("100"))
        usg_tir = Procedimento.objects.create(nome="USG Tireoide", eh_ultrassom=True, valor_particular=Decimal("100"))
        LimiteDiarioProcedimento.objects.create(profissional=self.profissional, todos_ultrassons=True, limite_diario=2)
        avisos = []
        for procedimento in (usg_abd, usg_tir, usg_abd):
            ag = self._criar_agendamento()
            AgendamentoItem.objects.create(agendamento=ag, procedimento=procedimento, quantidade=1)
            avisos.append(_verificar_limite_diario(ag))
        self.assertIsNone(avisos[1])  # 2 USG de 2 (abdome + tireoide somados)
        self.assertIn("ultrassons (USG)", avisos[2])
        # consulta não é USG: o limite não se aplica a ela
        consulta = self._criar_agendamento()
        AgendamentoItem.objects.create(agendamento=consulta, procedimento=self.consulta, quantidade=1)
        self.assertIsNone(_verificar_limite_diario(consulta))

class ResumoDeErrosNoFormularioTests(AgendaTestBase):
    """Pedido da clínica (23/10/2026): "a alteração não salva" — o motivo ficava só ao lado do
    campo (fora da tela ou escondido). Agora aparece um resumo no topo."""

    def test_motivo_aparece_no_topo(self):
        usuario = Usuario.objects.create_user(username="rec_resumo", password="x", exigir_troca_senha=False)
        for codename in ("agenda_editar", "agenda_ver_dia"):
            usuario.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(usuario)
        agendamento = self._criar_agendamento(status=Agendamento.Status.CONFIRMADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)
        item = agendamento.itens.first()
        dados = {
            "paciente": self.paciente.pk, "profissional": self.profissional.pk, "forma_atendimento": "PARTICULAR",
            "data": "2026-07-20", "turno": "MANHA",
            "itens-TOTAL_FORMS": "1", "itens-INITIAL_FORMS": "1", "itens-MIN_NUM_FORMS": "0", "itens-MAX_NUM_FORMS": "1000",
            "itens-0-id": item.pk, "itens-0-procedimento": "", "itens-0-quantidade": "1",  # sem procedimento
            "encaixe": "on",  # encaixe sem observação
        }
        resposta = self.client.post(reverse("agenda:agendamento_editar", args=[agendamento.pk]), dados)
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Não foi possível salvar")
        resumo = resposta.context["erros_resumo"]
        self.assertTrue(any(e.startswith("Observações:") for e in resumo), resumo)

class PaginacaoGuiasEAutorizacoesTests(_AutorizacaoBase):
    """Listas longas paginadas de 50 em 50 (pedido da clínica, 03/11/2026)."""

    def test_guias_do_convenio_so_abertas_filtradas_no_banco_e_paginadas(self):
        aberta = self._guia(quantidade=10)
        vencida = self._guia(validade=date(2020, 1, 1))
        esgotada = self._guia(quantidade=1)
        agendamento, item = self._atendimento(self.sessao)
        self._autorizar(agendamento, **{f"item{item.pk}-guia": str(esgotada.pk)})
        url = reverse("agenda:guias_convenio")
        self.assertEqual([g.pk for g in self.client.get(url).context["guias"]], [aberta.pk])
        todas = {g.pk for g in self.client.get(url, {"situacao": "todas"}).context["guias"]}
        self.assertEqual(todas, {aberta.pk, vencida.pk, esgotada.pk})

        GuiaConvenio.objects.bulk_create([
            GuiaConvenio(paciente=self.paciente, convenio=self.convenio, procedimento=self.sessao,
                         numero=f"G{i}", quantidade_sessoes=5)
            for i in range(60)
        ])
        resposta = self.client.get(url)
        self.assertEqual(len(resposta.context["guias"]), 50)
        self.assertContains(resposta, "Página 1 de 2 (61 registros)")

    def test_autorizacoes_paginadas(self):
        for _ in range(55):
            agendamento, _item = self._atendimento(self.consulta)
            Agendamento.objects.filter(pk=agendamento.pk).update(status=Agendamento.Status.AUTORIZADO)
        resposta = self.client.get(reverse("agenda:autorizacoes"), {"de": "2026-07-01", "ate": "2026-12-31"})
        self.assertEqual(len(resposta.context["linhas"]), 50)
        self.assertContains(resposta, "55 autorização(ões) no período")
        segunda = self.client.get(reverse("agenda:autorizacoes"), {"de": "2026-07-01", "ate": "2026-12-31", "pagina": 2})
        self.assertEqual(len(segunda.context["linhas"]), 5)
