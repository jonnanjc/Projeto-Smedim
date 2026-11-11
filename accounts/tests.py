from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

Usuario = get_user_model()


class PermissoesPorDepartamentoRenderizacaoTests(TestCase):
    """
    Regressão: a tela de Perfis (e, pelo mesmo template `_permissoes_por_departamento.html`,
    a seção "Permissões extras" de Usuários) precisa mostrar 1 checkbox por página real do
    sistema, sem bundles escondendo várias páginas atrás de 1 permissão só — foi exatamente
    a falta disso que o cliente relatou em 2026-07-28 (financeiro tinha 13 páginas no menu,
    mas só 6 checkboxes, porque financeiro_ver_completo bundlava 9 delas juntas). Ver
    accounts/permissoes.py e claude/detalhe-agenda-financeiro.md.
    """

    def setUp(self):
        self.superuser = Usuario.objects.create_superuser(
            username="superuser_teste_permissoes", password="senha-teste-123", exigir_troca_senha=False
        )
        self.client.force_login(self.superuser)

    def test_perfil_novo_mostra_uma_permissao_por_pagina_do_financeiro(self):
        resposta = self.client.get(reverse("perfil_novo"))
        conteudo = resposta.content.decode()

        for codigo in [
            "financeiro_ver_painel",
            "financeiro_ver_movimentacoes_avulsas",
            "financeiro_ver_contas_receber",
            "financeiro_ver_repasses_semanais",
            "financeiro_ver_fechamento_diario",
            "financeiro_ver_fechamento_mensal",
            "financeiro_ver_relatorio_detalhado",
            "financeiro_ver_repasse_convenio",
            "financeiro_ver_producao_convenio",
            "faturamento_comparativo_visualizar",
        ]:
            with self.subTest(codigo=codigo):
                self.assertIn(f'value="{codigo}"', conteudo)

        # A permissão antiga que bundlava as 9 páginas acima não existe mais.
        self.assertNotIn('value="financeiro_ver_completo"', conteudo)

    def test_perfil_novo_nao_perde_faturamento_lote_visualizar(self):
        """faturamento_lote_visualizar não foi removida, só ficou mais estreita (perdeu o
        comparativo) — continua tendo seu próprio checkbox."""
        resposta = self.client.get(reverse("perfil_novo"))
        self.assertIn('value="faturamento_lote_visualizar"', resposta.content.decode())


class InicioProfissionalProcedimentosTests(TestCase):
    """Pedido da clínica (26/10/2026): o Início do profissional mostra, além dos já atendidos,
    quantos procedimentos eles somam (atendimento com 2 procedimentos conta 2)."""

    def test_conta_procedimentos_dos_atendidos(self):
        from datetime import timedelta

        from django.utils import timezone

        from agenda.models import Agendamento, AgendamentoItem
        from cadastros.models import Paciente, Procedimento, Profissional

        profissional = Profissional.objects.create(nome="Prof Início", registro_profissional="CRM-INI", percentual_desconto=0)
        paciente = Paciente.objects.create(nome_completo="Paciente Início")
        procedimento = Procedimento.objects.create(nome="Consulta Início", eh_consulta=True)
        inicio = timezone.make_aware(timezone.datetime.combine(timezone.localdate(), timezone.datetime.min.time()))
        inicio += timedelta(hours=8)
        for status, qtd_itens in [(Agendamento.Status.REALIZADO, 2), (Agendamento.Status.CONFIRMADO, 1)]:
            agendamento = Agendamento.objects.create(
                paciente=paciente, profissional=profissional, forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
                data_hora_inicio=inicio, data_hora_fim=inicio + timedelta(minutes=30), status=status,
            )
            for n in range(qtd_itens):
                AgendamentoItem.objects.create(
                    agendamento=agendamento,
                    procedimento=procedimento if n == 0 else Procedimento.objects.create(nome=f"Exame Início {n}"),
                    quantidade=1,
                )
            inicio += timedelta(hours=1)
        usuario = Usuario.objects.create_user(
            username="prof_inicio", password="senha-teste-123", exigir_troca_senha=False, profissional_id=profissional.pk
        )
        self.client.force_login(usuario)

        resposta = self.client.get(reverse("home"))
        self.assertEqual(resposta.context["stats_profissional"]["atendidos"], 1)
        self.assertEqual(resposta.context["stats_profissional"]["procedimentos"], 2)
        self.assertContains(resposta, "Procedimentos realizados")
