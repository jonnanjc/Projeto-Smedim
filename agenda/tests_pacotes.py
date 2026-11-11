"""
Testes de pacotes/combos particulares (cadastros.models.Pacote / agenda.models.PacoteVendido):
venda na tela de pagamento, uso automático do saldo, falta devolvendo saldo, estorno
desativando o pacote, repasse (percentual e fixo) e relatórios.
"""
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.urls import reverse

from cadastros.models import Pacote, PacoteItem, Procedimento, Profissional, ValorFixoProfissional

from .models import Agendamento, AgendamentoItem, PacoteVendido, PagamentoParticular, vincular_pacotes
from .tests import AgendaTestBase, Usuario


class PacoteTestBase(AgendaTestBase):
    def setUp(self):
        super().setUp()
        self.sessao = Procedimento.objects.create(
            nome="Sessão Teste", permite_repeticao_semanal=True, valor_particular=Decimal("100.00")
        )
        self.sessao.profissionais_habilitados.add(self.profissional)
        self.combo = Pacote.objects.create(nome="Consulta + Exame", valor=Decimal("200.00"))
        PacoteItem.objects.create(pacote=self.combo, procedimento=self.consulta, quantidade=1)
        PacoteItem.objects.create(pacote=self.combo, procedimento=self.exame, quantidade=1)
        self.dez_sessoes = Pacote.objects.create(nome="10 sessões", valor=Decimal("800.00"))
        PacoteItem.objects.create(pacote=self.dez_sessoes, procedimento=self.sessao, quantidade=10)

        self.usuario = Usuario.objects.create_user(
            username="financeiro_pacote", password="senha-teste-123", exigir_troca_senha=False
        )
        for codigo in ("fila_autorizar_financeiro", "fila_autorizar_recepcao", "agenda_editar", "financeiro_estornar_pagamento"):
            self.usuario.user_permissions.add(Permission.objects.get(codename=codigo))
        self.client.force_login(self.usuario)

    def _atendimento(self, *procedimentos, status=Agendamento.Status.PRE_AUTORIZADO, **kwargs):
        agendamento = self._criar_agendamento(status=status, **kwargs)
        for procedimento in procedimentos:
            AgendamentoItem.objects.create(agendamento=agendamento, procedimento=procedimento, quantidade=1)
        vincular_pacotes(agendamento)
        return agendamento

    def _url_pagamento(self, agendamento):
        return reverse("agenda:agendamento_confirmar_pagamento", args=[agendamento.pk])

    def _vender(self, agendamento, pacote, pagamento=None):
        self.client.post(self._url_pagamento(agendamento), {"acao": "incluir_pacote", "pacote": pacote.pk})
        resposta = self.client.post(self._url_pagamento(agendamento), pagamento or {"forma_pagamento_1": "PIX"})
        self.assertEqual(resposta.status_code, 302)
        return PacoteVendido.objects.get(agendamento_venda=agendamento)

    def _encaminhar_ao_financeiro(self, agendamento):
        return self.client.post(
            reverse("agenda:agendamento_mudar_status", args=[agendamento.pk, Agendamento.Status.PRE_AUTORIZADO])
        )


class VendaDePacoteTests(PacoteTestBase):
    def test_combo_consulta_mais_exame_cobra_preco_fechado(self):
        agendamento = self._atendimento(self.consulta, self.exame)
        self.client.post(self._url_pagamento(agendamento), {"acao": "incluir_pacote", "pacote": self.combo.pk})
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.valor_a_cobrar, Decimal("200.00"))  # não 150 + 80

        self.client.post(self._url_pagamento(agendamento), {"forma_pagamento_1": "DINHEIRO"})
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.status, Agendamento.Status.AUTORIZADO)
        [pagamento] = PagamentoParticular.objects.filter(agendamento=agendamento)
        self.assertEqual(pagamento.valor, Decimal("200.00"))
        self.assertEqual(pagamento.valor_repasse, Decimal("140.00"))  # 30% de retenção
        self.assertEqual(set(agendamento.itens.values_list("valor_cobrado", flat=True)), {Decimal("0.00")})
        venda = PacoteVendido.objects.get()
        self.assertIsNotNone(venda.pago_em)
        self.assertEqual(venda.total_restante, 0)

    def test_sessoes_ja_marcadas_antes_da_venda_passam_a_usar_o_pacote(self):
        primeira = self._atendimento(self.sessao)
        ja_marcadas = [self._atendimento(self.sessao, status=Agendamento.Status.AGENDADO) for _ in range(3)]
        self.assertFalse(any(ag.itens.get().pacote_vendido_id for ag in ja_marcadas))
        venda = self._vender(primeira, self.dez_sessoes)
        self.assertTrue(all(ag.itens.get().pacote_vendido_id == venda.pk for ag in ja_marcadas))
        self.assertEqual(venda.total_restante, 6)

    def test_pacote_so_vale_depois_de_pago(self):
        agendamento = self._atendimento(self.sessao)
        self.client.post(self._url_pagamento(agendamento), {"acao": "incluir_pacote", "pacote": self.dez_sessoes.pk})
        outro = self._atendimento(self.sessao, status=Agendamento.Status.AGENDADO)
        self.assertFalse(outro.itens.get().pacote_vendido_id)

    def test_clique_duplo_nao_inclui_o_mesmo_pacote_duas_vezes(self):
        agendamento = self._atendimento(self.sessao)
        for _ in range(2):
            self.client.post(self._url_pagamento(agendamento), {"acao": "incluir_pacote", "pacote": self.dez_sessoes.pk})
        self.assertEqual(PacoteVendido.objects.count(), 1)
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.valor_a_cobrar, Decimal("800.00"))

    def test_retirar_pacote_antes_de_pagar_volta_ao_preco_normal(self):
        agendamento = self._atendimento(self.consulta, self.exame)
        self.client.post(self._url_pagamento(agendamento), {"acao": "incluir_pacote", "pacote": self.combo.pk})
        venda = PacoteVendido.objects.get()
        self.client.post(self._url_pagamento(agendamento), {"acao": "remover_pacote", "venda": venda.pk})
        agendamento.refresh_from_db()
        self.assertFalse(PacoteVendido.objects.exists())
        self.assertEqual(agendamento.valor_a_cobrar, Decimal("230.00"))

    def test_cortesia_com_pacote_e_bloqueada(self):
        agendamento = self._atendimento(self.sessao)
        self.client.post(self._url_pagamento(agendamento), {"acao": "incluir_pacote", "pacote": self.dez_sessoes.pk})
        resposta = self.client.post(self._url_pagamento(agendamento), {"cortesia": "on"})
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "não pode ser vendido como cortesia")

    def test_tela_so_oferece_pacote_que_o_profissional_realiza(self):
        agendamento = self._atendimento(self.consulta)
        self.sessao.profissionais_habilitados.clear()
        resposta = self.client.get(self._url_pagamento(agendamento))
        self.assertContains(resposta, "Consulta + Exame")
        self.assertNotContains(resposta, "10 sessões")

    def test_convenio_nao_oferece_pacote(self):
        agendamento = self._atendimento(
            self.consulta, forma_atendimento=Agendamento.FormaAtendimento.CONVENIO, convenio=self.convenio
        )
        self.client.post(self._url_pagamento(agendamento), {"acao": "incluir_pacote", "pacote": self.combo.pk})
        self.assertFalse(PacoteVendido.objects.exists())

    def test_profissional_valor_fixo_exige_repasse_do_pacote_e_congela(self):
        self.profissional.cobra_valor_fixo_particular = True
        self.profissional.save()
        ValorFixoProfissional.objects.create(
            profissional=self.profissional, procedimento=self.sessao,
            valor_cobrado=Decimal("120.00"), valor_repasse=Decimal("90.00"),
        )
        agendamento = self._atendimento(self.sessao)
        self.client.post(self._url_pagamento(agendamento), {"acao": "incluir_pacote", "pacote": self.dez_sessoes.pk})
        self.assertFalse(PacoteVendido.objects.exists())

        self.dez_sessoes.valor_repasse = Decimal("600.00")
        self.dez_sessoes.save()
        venda = self._vender(agendamento, self.dez_sessoes)
        self.assertEqual(venda.valor_repasse_fixo, Decimal("600.00"))
        [pagamento] = PagamentoParticular.objects.filter(agendamento=agendamento)
        self.assertEqual(pagamento.valor, Decimal("800.00"))
        self.assertEqual(pagamento.valor_repasse, Decimal("600.00"))


class UsoDoSaldoTests(PacoteTestBase):
    def setUp(self):
        super().setUp()
        self.venda = self._vender(self._atendimento(self.sessao), self.dez_sessoes)

    def test_proxima_sessao_usa_o_saldo_e_pula_o_financeiro(self):
        self.assertEqual(self.venda.total_restante, 9)
        agendamento = self._atendimento(self.sessao, status=Agendamento.Status.CONFIRMADO)
        self.assertEqual(agendamento.itens.get().pacote_vendido, self.venda)

        self._encaminhar_ao_financeiro(agendamento)
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.status, Agendamento.Status.AUTORIZADO)
        self.assertIsNotNone(agendamento.autorizado_em)
        self.assertFalse(agendamento.pagamentos.exists())
        self.assertEqual(self.venda.total_restante, 8)

    def test_botao_da_agenda_diz_autorizar_quando_o_pacote_cobre(self):
        """Pedido da clínica (06/11/2026): o botão dizia "Encaminhar para pagamento" mesmo
        com o pacote já pago e confundia a recepção."""
        from django.utils import timezone

        self.usuario.user_permissions.add(Permission.objects.get(codename="agenda_ver_dia"))
        coberto = self._atendimento(self.sessao, status=Agendamento.Status.CONFIRMADO)
        dia = timezone.localtime(coberto.data_hora_inicio).date().isoformat()
        resposta = self.client.get(reverse("agenda:agenda_dia"), {"data": dia})
        self.assertContains(resposta, "Autorizar (pacote já pago)")
        self.assertNotContains(resposta, "Encaminhar para pagamento")

        # Sem pacote cobrindo, continua indo ao financeiro.
        self._atendimento(self.exame, status=Agendamento.Status.CONFIRMADO)
        resposta = self.client.get(reverse("agenda:agenda_dia"), {"data": dia})
        self.assertContains(resposta, "Encaminhar para pagamento")

    def test_falta_e_cancelamento_devolvem_a_sessao(self):
        faltou = self._atendimento(self.sessao, status=Agendamento.Status.AGENDADO)
        cancelado = self._atendimento(self.sessao, status=Agendamento.Status.AGENDADO)
        self.assertEqual(self.venda.total_restante, 7)
        Agendamento.objects.filter(pk=faltou.pk).update(status=Agendamento.Status.FALTOU)
        self.client.post(reverse("agenda:agendamento_cancelar", args=[cancelado.pk]))
        self.assertEqual(self.venda.total_restante, 9)

    def test_saldo_esgotado_volta_a_cobrar(self):
        for _ in range(9):
            self._atendimento(self.sessao, status=Agendamento.Status.AGENDADO)
        self.assertEqual(self.venda.total_restante, 0)
        decimo_primeiro = self._atendimento(self.sessao, status=Agendamento.Status.CONFIRMADO)
        self.assertFalse(decimo_primeiro.itens.get().pacote_vendido_id)
        self._encaminhar_ao_financeiro(decimo_primeiro)
        decimo_primeiro.refresh_from_db()
        self.assertEqual(decimo_primeiro.status, Agendamento.Status.PRE_AUTORIZADO)
        self.assertEqual(decimo_primeiro.valor_a_cobrar, Decimal("100.00"))

    def test_outro_profissional_nao_usa_o_pacote(self):
        outro = Profissional.objects.create(
            nome="Outro", registro_profissional="CRM-2", percentual_desconto=Decimal("30"), especialidade="medico_clinico_geral"
        )
        self.sessao.profissionais_habilitados.add(outro)
        agendamento = self._criar_agendamento(status=Agendamento.Status.AGENDADO)
        agendamento.profissional = outro
        agendamento.save()
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.sessao, quantidade=1)
        vincular_pacotes(agendamento)
        self.assertFalse(agendamento.itens.get().pacote_vendido_id)

    def test_convenio_nao_usa_o_pacote(self):
        agendamento = self._atendimento(
            self.sessao, status=Agendamento.Status.AGENDADO,
            forma_atendimento=Agendamento.FormaAtendimento.CONVENIO, convenio=self.convenio,
        )
        self.assertFalse(agendamento.itens.get().pacote_vendido_id)

    def test_estorno_da_venda_desativa_o_pacote(self):
        futuro = self._atendimento(self.sessao, status=Agendamento.Status.AGENDADO)
        self.assertTrue(futuro.itens.get().pacote_vendido_id)
        pagamento = self.venda.agendamento_venda.pagamentos.get()
        self.client.post(reverse("financeiro:pagamento_estornar", args=[pagamento.pk]), {"motivo_estorno": "engano"})
        pagamento.refresh_from_db()
        self.assertTrue(pagamento.estornado)
        self.assertFalse(futuro.itens.get().pacote_vendido_id)
        self.assertFalse(PacoteVendido.ativos().exists())

    def test_agendar_pela_tela_avisa_que_ja_esta_pago(self):
        self.usuario.user_permissions.add(Permission.objects.get(codename="agenda_criar"))
        resposta = self.client.post(
            reverse("agenda:agendamento_novo"),
            {
                "paciente": str(self.paciente.pk),
                "profissional": str(self.profissional.pk),
                "forma_atendimento": "PARTICULAR",
                "data": "2026-07-27",
                "hora_inicio": "10:00",
                "duracao_minutos": "30",
                "itens-TOTAL_FORMS": "1",
                "itens-INITIAL_FORMS": "0",
                "itens-MIN_NUM_FORMS": "1",
                "itens-MAX_NUM_FORMS": "1000",
                "itens-0-procedimento": str(self.sessao.pk),
                "itens-0-quantidade": "1",
            },
            follow=True,
        )
        self.assertContains(resposta, "já está pago no pacote")
        self.assertEqual(self.venda.total_restante, 8)

    def test_relatorio_por_procedimento_mostra_venda_e_uso(self):
        from financeiro.views import _particular_por_procedimento

        agendamento = self._atendimento(self.sessao, status=Agendamento.Status.CONFIRMADO)
        self._encaminhar_ao_financeiro(agendamento)
        linhas = {linha["procedimento"]: linha for linha in _particular_por_procedimento(
            self.venda.agendamento_venda.data_hora_inicio.date(), agendamento.data_hora_inicio.date()
        )}
        self.assertEqual(linhas["Venda de pacote: 10 sessões"]["valor"], Decimal("800.00"))
        # A sessão do dia da venda conta dentro da venda (clínica, 27/10/2026), não de novo.
        self.assertEqual(linhas["Venda de pacote: 10 sessões"]["qtd"], 1)
        self.assertEqual(linhas["Sessão Teste (pelo pacote)"]["qtd"], 1)

    def test_venda_com_sessao_do_dia_conta_um_procedimento(self):
        from financeiro.views import _particular_por_forma_pagamento, _particular_por_procedimento

        dia = self.venda.agendamento_venda.data_hora_inicio.date()
        linhas = _particular_por_procedimento(dia, dia)
        self.assertEqual([(l["procedimento"], l["qtd"]) for l in linhas], [("Venda de pacote: 10 sessões", 1)])
        pix = next(l for l in _particular_por_forma_pagamento(dia, dia) if l["forma"] == "Pix")
        self.assertEqual(pix["qtd"], 1)

    def test_tela_de_pacotes_dos_pacientes(self):
        resposta = self.client.get(reverse("agenda:pacotes_vendidos"), {"q": "Paciente"})
        self.assertContains(resposta, "Sessão Teste: restam 9 de 10")


class PacoteDoSistemaAntigoTests(PacoteTestBase):
    """Pacote cadastrado direto no paciente (PacoteVendido.cadastro_manual — clínica, 27/10/2026)."""

    def setUp(self):
        super().setUp()
        self.usuario.user_permissions.add(Permission.objects.get(codename="pacote_paciente_cadastrar"))

    def test_sem_a_permissao_nao_cadastra(self):
        self.usuario.user_permissions.remove(Permission.objects.get(codename="pacote_paciente_cadastrar"))
        self.usuario = Usuario.objects.get(pk=self.usuario.pk)  # limpa o cache de permissões
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(reverse("agenda:pacote_cadastrar_manual")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("agenda:pacotes_vendidos")), "Cadastrar pacote do sistema antigo")

    def test_administrador_ja_tem_a_permissao(self):

        from accounts.management.commands.seed_perfis import PERFIS

        self.assertIn("pacote_paciente_cadastrar", PERFIS["Administrador"])

    def _cadastrar(self, situacao="a_pagar", usadas="3", valor="700.00"):
        item = self.dez_sessoes.itens.get()
        resposta = self.client.post(
            reverse("agenda:pacote_cadastrar_manual"),
            {
                "paciente": str(self.paciente.pk), "profissional": str(self.profissional.pk),
                "pacote": str(self.dez_sessoes.pk), "valor": valor, "situacao": situacao,
                f"usadas_{item.pk}": usadas,
            },
        )
        self.assertEqual(resposta.status_code, 302)
        return PacoteVendido.objects.get(cadastro_manual=True)

    def test_cadastro_ja_vale_e_desconta_as_usadas(self):
        futuro = self._atendimento(self.sessao, status=Agendamento.Status.AGENDADO)
        venda = self._cadastrar()
        self.assertIsNone(venda.agendamento_venda)
        self.assertTrue(venda.pagamento_pendente)
        self.assertEqual(futuro.itens.get().pacote_vendido, venda)
        self.assertEqual(venda.saldo_resumo, "Sessão Teste: restam 6 de 10")
        resposta = self.client.get(reverse("agenda:pacotes_vendidos"))
        self.assertContains(resposta, "A pagar")

    def test_tela_de_cadastro_abre(self):
        resposta = self.client.get(reverse("agenda:pacote_cadastrar_manual"))
        self.assertContains(resposta, "Já usadas no sistema antigo")
        self.assertContains(resposta, "10 sessões")

    def test_usadas_acima_do_contratado_e_recusado(self):
        item = self.dez_sessoes.itens.get()
        resposta = self.client.post(
            reverse("agenda:pacote_cadastrar_manual"),
            {
                "paciente": str(self.paciente.pk), "profissional": str(self.profissional.pk),
                "pacote": str(self.dez_sessoes.pk), "valor": "800.00", "situacao": "a_pagar",
                f"usadas_{item.pk}": "11",
            },
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(PacoteVendido.objects.exists())

    def test_a_pagar_passa_pelo_financeiro_e_pode_seguir_sem_cobrar(self):
        venda = self._cadastrar()
        agendamento = self._atendimento(self.sessao, status=Agendamento.Status.CONFIRMADO)
        self._encaminhar_ao_financeiro(agendamento)
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.status, Agendamento.Status.PRE_AUTORIZADO)
        self.assertContains(self.client.get(self._url_pagamento(agendamento)), "Cobrar pacote agora")

        self.client.post(self._url_pagamento(agendamento), {})
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.status, Agendamento.Status.AUTORIZADO)
        self.assertFalse(agendamento.pagamentos.exists())
        venda.refresh_from_db()
        self.assertTrue(venda.pagamento_pendente)
        self.assertEqual(venda.total_restante, 6)

    def test_cobrar_depois_entra_no_caixa_do_dia_e_estorno_volta_a_pagar(self):
        from financeiro.views import _particular_por_procedimento

        venda = self._cadastrar()
        agendamento = self._atendimento(self.sessao)
        self.client.post(self._url_pagamento(agendamento), {"acao": "cobrar_pacote", "venda": venda.pk})
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.valor_a_cobrar, Decimal("700.00"))
        self.client.post(self._url_pagamento(agendamento), {"forma_pagamento_1": "PIX"})
        venda.refresh_from_db()
        self.assertEqual(venda.agendamento_venda, agendamento)
        self.assertFalse(venda.pagamento_pendente)
        dia = agendamento.data_hora_inicio.date()
        linhas = _particular_por_procedimento(dia, dia)
        self.assertEqual([(l["procedimento"], l["qtd"], l["valor"]) for l in linhas],
                         [("Venda de pacote: 10 sessões", 1, Decimal("700.00"))])

        pagamento = agendamento.pagamentos.get()
        self.client.post(reverse("financeiro:pagamento_estornar", args=[pagamento.pk]), {"motivo_estorno": "engano"})
        venda.refresh_from_db()
        self.assertTrue(venda.pagamento_pendente)
        self.assertIsNone(venda.agendamento_venda)
        self.assertIn(venda.pk, PacoteVendido.ativos().values_list("pk", flat=True))

    def test_retirar_cobranca_nao_apaga_o_pacote(self):
        venda = self._cadastrar()
        agendamento = self._atendimento(self.sessao)
        self.client.post(self._url_pagamento(agendamento), {"acao": "cobrar_pacote", "venda": venda.pk})
        self.client.post(self._url_pagamento(agendamento), {"acao": "remover_pacote", "venda": venda.pk})
        venda.refresh_from_db()
        self.assertIsNone(venda.agendamento_venda)
        self.assertTrue(venda.pagamento_pendente)

    def test_ja_pago_no_sistema_antigo_autoriza_direto(self):
        self._cadastrar(situacao="pago")
        agendamento = self._atendimento(self.sessao, status=Agendamento.Status.CONFIRMADO)
        self._encaminhar_ao_financeiro(agendamento)
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.status, Agendamento.Status.AUTORIZADO)
        self.assertNotContains(self.client.get(self._url_pagamento(self._atendimento(self.consulta))), "Cobrar pacote agora")


class CadastroDePacoteTests(PacoteTestBase):
    def test_cadastra_pacote_com_itens(self):
        self.usuario.user_permissions.add(Permission.objects.get(codename="procedimento_gerenciar"))
        resposta = self.client.post(
            reverse("cadastros:pacote_novo"),
            {
                "nome": "5 sessões", "profissional": "", "valor": "450.00", "valor_repasse": "", "ativo": "on",
                "itens-TOTAL_FORMS": "1", "itens-INITIAL_FORMS": "0",
                "itens-0-procedimento": str(self.sessao.pk), "itens-0-quantidade": "5",
            },
        )
        self.assertEqual(resposta.status_code, 302)
        pacote = Pacote.objects.get(nome="5 sessões")
        self.assertEqual(pacote.itens_resumo, "5x Sessão Teste")

    def test_pacote_vendido_nao_pode_ser_excluido(self):
        self.usuario.user_permissions.add(Permission.objects.get(codename="procedimento_gerenciar"))
        self._vender(self._atendimento(self.sessao), self.dez_sessoes)
        self.client.post(reverse("cadastros:pacote_excluir", args=[self.dez_sessoes.pk]))
        self.assertTrue(Pacote.objects.filter(pk=self.dez_sessoes.pk).exists())


class CancelarPacoteDoSistemaAntigoTests(PacoteDoSistemaAntigoTests):
    """Cancelar pacote cadastrado por engano direto no paciente (pedido da clínica, 10/11/2026).
    Ver PacoteVendido.motivo_nao_cancela / agenda.views.pacote_cancelar_manual."""

    def _cancelar(self, venda):
        return self.client.post(reverse("agenda:pacote_cancelar_manual", args=[venda.pk]))

    def test_cancela_e_atendimento_marcado_volta_a_ser_cobrado(self):
        from accounts.models import LogAuditoria

        futuro = self._atendimento(self.sessao, status=Agendamento.Status.AGENDADO)
        venda = self._cadastrar()
        self.assertContains(self.client.get(reverse("agenda:pacotes_vendidos")), "Cancelar pacote")
        resposta = self._cancelar(venda)
        self.assertEqual(resposta.status_code, 302)
        self.assertFalse(PacoteVendido.objects.exists())
        self.assertIsNone(futuro.itens.get().pacote_vendido_id)
        futuro.refresh_from_db()
        self.assertEqual(futuro.valor_a_cobrar, Decimal("100.00"))
        self.assertTrue(LogAuditoria.objects.filter(acao__startswith="Cancelou o pacote do sistema antigo").exists())

    def test_sem_a_permissao_nao_cancela(self):
        venda = self._cadastrar()
        self.usuario.user_permissions.remove(Permission.objects.get(codename="pacote_paciente_cadastrar"))
        self.usuario = Usuario.objects.get(pk=self.usuario.pk)
        self.client.force_login(self.usuario)
        self.assertEqual(self._cancelar(venda).status_code, 403)
        self.assertTrue(PacoteVendido.objects.exists())

    def test_ja_usado_em_atendimento_autorizado_nao_cancela(self):
        venda = self._cadastrar(situacao="pago")
        agendamento = self._atendimento(self.sessao, status=Agendamento.Status.CONFIRMADO)
        self._encaminhar_ao_financeiro(agendamento)
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.status, Agendamento.Status.AUTORIZADO)
        self._cancelar(venda)
        self.assertTrue(PacoteVendido.objects.filter(pk=venda.pk).exists())

    def test_sendo_cobrado_num_atendimento_nao_cancela(self):
        venda = self._cadastrar()
        agendamento = self._atendimento(self.sessao)
        self.client.post(self._url_pagamento(agendamento), {"acao": "cobrar_pacote", "venda": venda.pk})
        self._cancelar(venda)
        self.assertTrue(PacoteVendido.objects.filter(pk=venda.pk).exists())

    def test_pacote_vendido_no_pagamento_nao_cancela_por_aqui(self):
        venda = self._vender(self._atendimento(self.sessao), self.dez_sessoes)
        self._cancelar(venda)
        self.assertTrue(PacoteVendido.objects.filter(pk=venda.pk).exists())
