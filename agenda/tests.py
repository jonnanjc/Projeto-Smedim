"""
Testes do suporte a múltiplos procedimentos por agendamento (ver AgendamentoItem em
agenda/models.py) — cobrem a agregação de valor/retorno por item, a validação do
formset de itens (agenda/forms.py), o congelamento de valor_convenio/valor_cobrado por
item ao mudar de status/confirmar pagamento, e o ConfirmarPagamentoForm com itens
mistos (retorno + cobrável no mesmo agendamento).

Roda numa base de dados de teste isolada (padrão do Django TestCase, em memória para
SQLite) — não toca em db.sqlite3.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import LogAuditoria
from cadastros.models import Convenio, Paciente, Procedimento, Profissional, TabelaPrecoConvenio, ValorFixoProfissional

from .forms import ConfirmarPagamentoForm
from .models import Agendamento, AgendamentoItem, PagamentoParticular

Usuario = get_user_model()


class AgendaTestBase(TestCase):
    """Fixtures comuns a todos os testes deste módulo."""

    def setUp(self):
        self.paciente = Paciente.objects.create(nome_completo="Paciente Teste")
        self.profissional = Profissional.objects.create(
            nome="Profissional Teste",
            registro_profissional="CRM-TESTE",
            percentual_desconto=Decimal("30.00"),
            especialidade="medico_clinico_geral",
        )
        self.consulta = Procedimento.objects.create(
            nome="Consulta Teste", eh_consulta=True, valor_particular=Decimal("150.00")
        )
        self.exame = Procedimento.objects.create(nome="Exame Teste", valor_particular=Decimal("80.00"))
        # Só procedimento vinculado ao profissional pode ser agendado com ele (sem fallback
        # pra "todos" quando não há nenhum vinculado — ver procedimentos_do_profissional).
        self.consulta.profissionais_habilitados.add(self.profissional)
        self.exame.profissionais_habilitados.add(self.profissional)
        self.convenio = Convenio.objects.create(nome="Convênio Teste")
        TabelaPrecoConvenio.objects.create(convenio=self.convenio, procedimento=self.consulta, valor=Decimal("100.00"))
        TabelaPrecoConvenio.objects.create(convenio=self.convenio, procedimento=self.exame, valor=Decimal("60.00"))
        self._proximo_horario = timezone.make_aware(datetime(2026, 7, 20, 8, 0))  # segunda-feira

    def _criar_agendamento(
        self,
        forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
        convenio=None,
        status=Agendamento.Status.AGENDADO,
    ):
        inicio = self._proximo_horario
        self._proximo_horario += timedelta(hours=1)
        return Agendamento.objects.create(
            paciente=self.paciente,
            profissional=self.profissional,
            forma_atendimento=forma_atendimento,
            convenio=convenio,
            data_hora_inicio=inicio,
            data_hora_fim=inicio + timedelta(minutes=30),
            status=status,
        )


class AgendamentoItemModelTests(AgendaTestBase):
    def test_valor_a_cobrar_particular_multiplica_quantidade(self):
        agendamento = self._criar_agendamento()
        item = AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.exame, quantidade=2)
        self.assertEqual(item.valor_a_cobrar, Decimal("160.00"))

    def test_valor_a_cobrar_convenio_usa_tabela_precos(self):
        agendamento = self._criar_agendamento(
            forma_atendimento=Agendamento.FormaAtendimento.CONVENIO, convenio=self.convenio
        )
        item = AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)
        self.assertEqual(item.valor_a_cobrar, Decimal("100.00"))

    def test_agendamento_valor_a_cobrar_soma_itens(self):
        agendamento = self._criar_agendamento()
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.exame, quantidade=1)
        self.assertEqual(agendamento.valor_a_cobrar, Decimal("230.00"))

    def test_agendamento_valor_a_cobrar_none_quando_um_item_sem_preco(self):
        sem_preco = Procedimento.objects.create(nome="Sem preço cadastrado")
        agendamento = self._criar_agendamento()
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=sem_preco, quantidade=1)
        self.assertIsNone(agendamento.valor_a_cobrar)

    def test_retorno_zera_so_o_proprio_item(self):
        original = self._criar_agendamento(status=Agendamento.Status.REALIZADO)
        item_original = AgendamentoItem.objects.create(agendamento=original, procedimento=self.consulta, quantidade=1)

        novo = self._criar_agendamento()
        item_retorno = AgendamentoItem.objects.create(
            agendamento=novo, procedimento=self.consulta, quantidade=1, retorno_de=item_original
        )
        item_pago = AgendamentoItem.objects.create(agendamento=novo, procedimento=self.exame, quantidade=1)

        self.assertTrue(item_retorno.eh_retorno)
        self.assertEqual(item_retorno.valor_a_cobrar, Decimal("0.00"))
        self.assertEqual(item_pago.valor_a_cobrar, Decimal("80.00"))
        self.assertEqual(novo.valor_a_cobrar, Decimal("80.00"))
        self.assertTrue(novo.tem_item_retorno)
        self.assertTrue(novo.tem_item_cobravel)

    def test_agendamento_todo_retorno_nao_tem_item_cobravel(self):
        original = self._criar_agendamento(status=Agendamento.Status.REALIZADO)
        item_original = AgendamentoItem.objects.create(agendamento=original, procedimento=self.consulta, quantidade=1)
        novo = self._criar_agendamento()
        AgendamentoItem.objects.create(
            agendamento=novo, procedimento=self.consulta, quantidade=1, retorno_de=item_original
        )
        self.assertTrue(novo.tem_item_retorno)
        self.assertFalse(novo.tem_item_cobravel)

    def test_itens_resumo(self):
        agendamento = self._criar_agendamento()
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.exame, quantidade=2)
        self.assertEqual(agendamento.itens_resumo, "Consulta Teste, Exame Teste x2")


class BuscarItemParaRetornoTests(AgendaTestBase):
    def test_encontra_consulta_recente_mesma_especialidade(self):
        original = self._criar_agendamento(status=Agendamento.Status.REALIZADO)
        item_original = AgendamentoItem.objects.create(agendamento=original, procedimento=self.consulta, quantidade=1)

        encontrado = AgendamentoItem.buscar_item_para_retorno(
            self.paciente, self.profissional.especialidade, self.profissional.especialidade_outra,
            original.data_hora_inicio + timedelta(days=10),
        )
        self.assertEqual(encontrado, item_original)

    def test_fora_da_janela_de_30_dias_nao_encontra(self):
        original = self._criar_agendamento(status=Agendamento.Status.REALIZADO)
        AgendamentoItem.objects.create(agendamento=original, procedimento=self.consulta, quantidade=1)

        encontrado = AgendamentoItem.buscar_item_para_retorno(
            self.paciente, self.profissional.especialidade, self.profissional.especialidade_outra,
            original.data_hora_inicio + timedelta(days=31),
        )
        self.assertIsNone(encontrado)

    def _consulta_original_convenio(self, prazo_retorno_dias):
        self.convenio.prazo_retorno_dias = prazo_retorno_dias
        self.convenio.save()
        original = self._criar_agendamento(
            forma_atendimento=Agendamento.FormaAtendimento.CONVENIO,
            convenio=self.convenio,
            status=Agendamento.Status.REALIZADO,
        )
        item = AgendamentoItem.objects.create(agendamento=original, procedimento=self.consulta, quantidade=1)
        return original, item

    def test_convenio_com_prazo_de_15_dias_encontra_dentro_do_prazo(self):
        original, item_original = self._consulta_original_convenio(15)
        encontrado = AgendamentoItem.buscar_item_para_retorno(
            self.paciente, self.profissional.especialidade, self.profissional.especialidade_outra,
            original.data_hora_inicio + timedelta(days=14),
        )
        self.assertEqual(encontrado, item_original)

    def test_convenio_com_prazo_de_15_dias_nao_encontra_depois_do_prazo(self):
        original, _ = self._consulta_original_convenio(15)
        encontrado = AgendamentoItem.buscar_item_para_retorno(
            self.paciente, self.profissional.especialidade, self.profissional.especialidade_outra,
            original.data_hora_inicio + timedelta(days=20),
        )
        self.assertIsNone(encontrado)

    def test_convenio_com_prazo_maior_que_30_dias(self):
        original, item_original = self._consulta_original_convenio(45)
        encontrado = AgendamentoItem.buscar_item_para_retorno(
            self.paciente, self.profissional.especialidade, self.profissional.especialidade_outra,
            original.data_hora_inicio + timedelta(days=40),
        )
        self.assertEqual(encontrado, item_original)

    def test_convenio_com_prazo_zero_nao_da_retorno(self):
        original, _ = self._consulta_original_convenio(0)
        encontrado = AgendamentoItem.buscar_item_para_retorno(
            self.paciente, self.profissional.especialidade, self.profissional.especialidade_outra,
            original.data_hora_inicio + timedelta(days=1),
        )
        self.assertIsNone(encontrado)

    def test_consulta_ja_usada_por_retorno_nao_conta_de_novo(self):
        original = self._criar_agendamento(status=Agendamento.Status.REALIZADO)
        item_original = AgendamentoItem.objects.create(agendamento=original, procedimento=self.consulta, quantidade=1)
        retorno_agendamento = self._criar_agendamento(status=Agendamento.Status.REALIZADO)
        AgendamentoItem.objects.create(
            agendamento=retorno_agendamento, procedimento=self.consulta, quantidade=1, retorno_de=item_original
        )

        encontrado = AgendamentoItem.buscar_item_para_retorno(
            self.paciente, self.profissional.especialidade, self.profissional.especialidade_outra,
            original.data_hora_inicio + timedelta(days=20),
        )
        self.assertIsNone(encontrado)

    def test_exame_nao_da_direito_a_retorno(self):
        original = self._criar_agendamento(status=Agendamento.Status.REALIZADO)
        AgendamentoItem.objects.create(agendamento=original, procedimento=self.exame, quantidade=1)

        encontrado = AgendamentoItem.buscar_item_para_retorno(
            self.paciente, self.profissional.especialidade, self.profissional.especialidade_outra,
            original.data_hora_inicio + timedelta(days=10),
        )
        self.assertIsNone(encontrado)


class AgendamentoNovoViewTests(AgendaTestBase):
    def setUp(self):
        super().setUp()
        self.usuario = Usuario.objects.create_user(
            username="recepcao_teste", password="senha-teste-123", exigir_troca_senha=False
        )
        self.usuario.user_permissions.add(Permission.objects.get(codename="agenda_criar"))
        self.client.force_login(self.usuario)

    def _dados_base(self, **kwargs):
        dados = {
            "paciente": str(self.paciente.pk),
            "profissional": str(self.profissional.pk),
            "forma_atendimento": "PARTICULAR",
            "data": "2026-07-27",  # segunda-feira
            "hora_inicio": "10:00",
            "duracao_minutos": "30",
            "itens-TOTAL_FORMS": "2",
            "itens-INITIAL_FORMS": "0",
            "itens-MIN_NUM_FORMS": "1",
            "itens-MAX_NUM_FORMS": "1000",
            "itens-0-procedimento": str(self.consulta.pk),
            "itens-0-quantidade": "1",
            "itens-1-procedimento": str(self.exame.pk),
            "itens-1-quantidade": "1",
        }
        dados.update(kwargs)
        return dados

    def test_cria_agendamento_com_dois_itens(self):
        resposta = self.client.post(reverse("agenda:agendamento_novo"), self._dados_base())
        self.assertEqual(resposta.status_code, 302)
        agendamento = Agendamento.objects.get(paciente=self.paciente)
        self.assertEqual(agendamento.itens.count(), 2)
        nomes = set(agendamento.itens.values_list("procedimento__nome", flat=True))
        self.assertEqual(nomes, {"Consulta Teste", "Exame Teste"})

    def test_procedimento_duplicado_e_rejeitado(self):
        dados = self._dados_base()
        dados["itens-1-procedimento"] = str(self.consulta.pk)
        resposta = self.client.post(reverse("agenda:agendamento_novo"), dados)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Agendamento.objects.count(), 0)

    def test_sem_nenhum_procedimento_e_rejeitado(self):
        dados = self._dados_base(**{"itens-0-procedimento": "", "itens-1-procedimento": ""})
        resposta = self.client.post(reverse("agenda:agendamento_novo"), dados)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Agendamento.objects.count(), 0)


class AgendamentoMudarStatusConvenioTests(AgendaTestBase):
    def setUp(self):
        super().setUp()
        self.usuario = Usuario.objects.create_user(
            username="medico_teste", password="senha-teste-123", exigir_troca_senha=False
        )
        for codename in ("agenda_editar", "atendimento_realizar"):
            self.usuario.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(self.usuario)

    def test_valor_convenio_por_item_multiplica_quantidade_ao_realizar(self):
        """Regressão: valor_convenio precisa multiplicar por 'quantidade', não só copiar o
        valor unitário da tabela de preços (bug encontrado testando este exato cenário)."""
        agendamento = self._criar_agendamento(
            forma_atendimento=Agendamento.FormaAtendimento.CONVENIO,
            convenio=self.convenio,
            status=Agendamento.Status.AUTORIZADO,
        )
        item = AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.exame, quantidade=3)

        self.client.post(reverse("agenda:agendamento_mudar_status", args=[agendamento.pk, "REALIZADO"]))

        item.refresh_from_db()
        agendamento.refresh_from_db()
        self.assertEqual(item.valor_convenio, Decimal("180.00"))  # 60.00 * 3
        self.assertEqual(agendamento.valor_convenio, Decimal("180.00"))

    def test_valor_convenio_preenchido_ja_ao_autorizar(self):
        """Autorizado já conta como produção (Agendamento.STATUS_PRODUCAO) — o valor do
        convênio não pode ficar esperando o profissional marcar "atendido"."""
        self.usuario.user_permissions.add(Permission.objects.get(codename="fila_autorizar_recepcao"))
        agendamento = self._criar_agendamento(
            forma_atendimento=Agendamento.FormaAtendimento.CONVENIO,
            convenio=self.convenio,
            status=Agendamento.Status.CONFIRMADO,
        )
        item = AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.exame, quantidade=2)

        self.client.post(reverse("agenda:agendamento_autorizar", args=[agendamento.pk]))

        item.refresh_from_db()
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.status, Agendamento.Status.AUTORIZADO)
        self.assertEqual(item.valor_convenio, Decimal("120.00"))  # 60.00 * 2
        self.assertEqual(agendamento.valor_convenio, Decimal("120.00"))

    def test_retorno_forca_zero_e_nao_afeta_outro_item(self):
        original = self._criar_agendamento(
            forma_atendimento=Agendamento.FormaAtendimento.CONVENIO,
            convenio=self.convenio,
            status=Agendamento.Status.REALIZADO,
        )
        item_original = AgendamentoItem.objects.create(agendamento=original, procedimento=self.consulta, quantidade=1)

        agendamento = self._criar_agendamento(
            forma_atendimento=Agendamento.FormaAtendimento.CONVENIO,
            convenio=self.convenio,
            status=Agendamento.Status.AUTORIZADO,
        )
        item_retorno = AgendamentoItem.objects.create(
            agendamento=agendamento, procedimento=self.consulta, quantidade=1, retorno_de=item_original
        )
        item_pago = AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.exame, quantidade=1)

        self.client.post(reverse("agenda:agendamento_mudar_status", args=[agendamento.pk, "REALIZADO"]))

        item_retorno.refresh_from_db()
        item_pago.refresh_from_db()
        agendamento.refresh_from_db()
        self.assertEqual(item_retorno.valor_convenio, Decimal("0.00"))
        self.assertEqual(item_pago.valor_convenio, Decimal("60.00"))
        self.assertEqual(agendamento.valor_convenio, Decimal("60.00"))

    def test_encaminhar_para_pagamento_so_com_permite_pagamento_na_hora(self):
        self.usuario.user_permissions.add(Permission.objects.get(codename="fila_autorizar_recepcao"))
        agendamento = self._criar_agendamento(
            forma_atendimento=Agendamento.FormaAtendimento.CONVENIO,
            convenio=self.convenio,
            status=Agendamento.Status.CONFIRMADO,
        )
        url = reverse("agenda:agendamento_mudar_status", args=[agendamento.pk, "PRE_AUTORIZADO"])

        self.assertEqual(self.client.post(url).status_code, 403)

        self.convenio.permite_pagamento_na_hora = True
        self.convenio.save()
        self.client.post(url)
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.status, Agendamento.Status.PRE_AUTORIZADO)


class ConfirmarPagamentoFormTests(AgendaTestBase):
    def test_cobra_soma_dos_itens(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.exame, quantidade=1)

        form = ConfirmarPagamentoForm({"forma_pagamento_1": "DINHEIRO"}, agendamento=agendamento)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["valor_1"], Decimal("230.00"))

    def test_forca_cortesia_quando_todos_os_itens_sao_retorno(self):
        original = self._criar_agendamento(status=Agendamento.Status.REALIZADO)
        item_original = AgendamentoItem.objects.create(agendamento=original, procedimento=self.consulta, quantidade=1)
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(
            agendamento=agendamento, procedimento=self.consulta, quantidade=1, retorno_de=item_original
        )

        form = ConfirmarPagamentoForm({}, agendamento=agendamento)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.cleaned_data["cortesia"])
        self.assertEqual(
            form.linhas_pagamento(),
            [{
                "forma_pagamento": Agendamento.FormaPagamento.CORTESIA,
                "valor": Decimal("0"),
                "valor_recebido": None,
                "parcelas": None,
            }],
        )

    def test_agendamento_misto_cobra_so_o_item_nao_retorno(self):
        original = self._criar_agendamento(status=Agendamento.Status.REALIZADO)
        item_original = AgendamentoItem.objects.create(agendamento=original, procedimento=self.consulta, quantidade=1)
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(
            agendamento=agendamento, procedimento=self.consulta, quantidade=1, retorno_de=item_original
        )
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.exame, quantidade=1)

        form = ConfirmarPagamentoForm({"forma_pagamento_1": "DINHEIRO"}, agendamento=agendamento)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["valor_1"], Decimal("80.00"))

    def test_dinheiro_nao_exige_valor_recebido_nem_parcelas(self):
        """Regressão: valor_recebido/parcelas só existem pra cartão — dinheiro/Pix não devem
        nunca exigi-los, mesmo com os campos de cartão completamente ausentes do POST."""
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)

        form = ConfirmarPagamentoForm({"forma_pagamento_1": "DINHEIRO"}, agendamento=agendamento)
        self.assertTrue(form.is_valid(), form.errors)
        linha = form.linhas_pagamento()[0]
        self.assertIsNone(linha["valor_recebido"])
        self.assertIsNone(linha["parcelas"])

    def test_exige_valor_recebido_para_cartao_debito(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)

        form = ConfirmarPagamentoForm({"forma_pagamento_1": "CARTAO_DEBITO"}, agendamento=agendamento)
        self.assertFalse(form.is_valid())
        self.assertIn("valor_recebido_1", form.errors)

    def test_nao_exige_parcelas_para_cartao_debito(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)

        form = ConfirmarPagamentoForm(
            {"forma_pagamento_1": "CARTAO_DEBITO", "valor_recebido_1": "145.00"}, agendamento=agendamento
        )
        self.assertTrue(form.is_valid(), form.errors)
        linha = form.linhas_pagamento()[0]
        self.assertEqual(linha["valor_recebido"], Decimal("145.00"))
        self.assertIsNone(linha["parcelas"])

    def test_exige_valor_recebido_e_parcelas_para_cartao_credito(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)

        form = ConfirmarPagamentoForm({"forma_pagamento_1": "CARTAO_CREDITO"}, agendamento=agendamento)
        self.assertFalse(form.is_valid())
        self.assertIn("valor_recebido_1", form.errors)
        self.assertIn("parcelas_1", form.errors)

    def test_cartao_credito_com_valor_recebido_e_parcelas_e_valido(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)

        form = ConfirmarPagamentoForm(
            {"forma_pagamento_1": "CARTAO_CREDITO", "valor_recebido_1": "140.00", "parcelas_1": "3"},
            agendamento=agendamento,
        )
        self.assertTrue(form.is_valid(), form.errors)
        linha = form.linhas_pagamento()[0]
        self.assertEqual(linha["valor_recebido"], Decimal("140.00"))
        self.assertEqual(linha["parcelas"], 3)

    def test_valor_recebido_nao_pode_exceder_valor_cobrado(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)

        form = ConfirmarPagamentoForm(
            {"forma_pagamento_1": "CARTAO_DEBITO", "valor_recebido_1": "999.00"}, agendamento=agendamento
        )
        self.assertFalse(form.is_valid())
        self.assertIn("valor_recebido_1", form.errors)

    def test_pagamento_dividido_exige_valor_recebido_so_na_parte_cartao(self):
        """Metade dinheiro/metade cartão: só a linha de cartão deve exigir valor_recebido."""
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)

        form = ConfirmarPagamentoForm(
            {
                "forma_pagamento_1": "DINHEIRO", "valor_1": "100.00",
                "forma_pagamento_2": "CARTAO_CREDITO", "valor_2": "50.00",
            },
            agendamento=agendamento,
        )
        self.assertFalse(form.is_valid())
        self.assertNotIn("valor_recebido_1", form.errors)
        self.assertIn("valor_recebido_2", form.errors)
        self.assertIn("parcelas_2", form.errors)

        form_valido = ConfirmarPagamentoForm(
            {
                "forma_pagamento_1": "DINHEIRO", "valor_1": "100.00",
                "forma_pagamento_2": "CARTAO_CREDITO", "valor_2": "50.00",
                "valor_recebido_2": "48.50", "parcelas_2": "2",
            },
            agendamento=agendamento,
        )
        self.assertTrue(form_valido.is_valid(), form_valido.errors)
        linhas = form_valido.linhas_pagamento()
        self.assertIsNone(linhas[0]["valor_recebido"])
        self.assertEqual(linhas[1]["valor_recebido"], Decimal("48.50"))
        self.assertEqual(linhas[1]["parcelas"], 2)


class ConfirmarPagamentoViewTests(AgendaTestBase):
    def setUp(self):
        super().setUp()
        self.usuario = Usuario.objects.create_user(
            username="financeiro_teste_agenda", password="senha-teste-123", exigir_troca_senha=False
        )
        self.usuario.user_permissions.add(Permission.objects.get(codename="fila_autorizar_financeiro"))
        self.client.force_login(self.usuario)

    def test_congela_valor_cobrado_por_item_ao_pagar(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        item1 = AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)
        item2 = AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.exame, quantidade=1)

        resposta = self.client.post(
            reverse("agenda:agendamento_confirmar_pagamento", args=[agendamento.pk]),
            {"forma_pagamento_1": "DINHEIRO"},
        )
        self.assertEqual(resposta.status_code, 302)

        item1.refresh_from_db()
        item2.refresh_from_db()
        agendamento.refresh_from_db()
        self.assertEqual(item1.valor_cobrado, Decimal("150.00"))
        self.assertEqual(item2.valor_cobrado, Decimal("80.00"))
        self.assertEqual(agendamento.status, Agendamento.Status.AUTORIZADO)
        self.assertEqual(PagamentoParticular.objects.get(agendamento=agendamento).valor, Decimal("230.00"))

    def test_envio_duplo_nao_grava_o_pagamento_duas_vezes(self):
        """Clínica, 26/10/2026: o mesmo 'Registrar e autorizar' chegando duas vezes gravava 4 linhas."""
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)
        url = reverse("agenda:agendamento_confirmar_pagamento", args=[agendamento.pk])
        dados = {"forma_pagamento_1": "PIX", "valor_1": "60.00", "forma_pagamento_2": "DINHEIRO", "valor_2": "90.00"}

        self.client.post(url, dados)
        resposta = self.client.post(url, dados, follow=True)
        self.assertEqual(PagamentoParticular.objects.filter(agendamento=agendamento).count(), 2)
        self.assertContains(resposta, "já está registrado")

    def test_registra_valor_recebido_e_parcelas_no_pagamento_cartao_credito(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)

        resposta = self.client.post(
            reverse("agenda:agendamento_confirmar_pagamento", args=[agendamento.pk]),
            {"forma_pagamento_1": "CARTAO_CREDITO", "valor_recebido_1": "144.00", "parcelas_1": "3"},
        )
        self.assertEqual(resposta.status_code, 302)

        pagamento = PagamentoParticular.objects.get(agendamento=agendamento)
        # 'valor' continua o valor cadastrado/cobrado (150.00) — é ele que alimenta o repasse
        # ao profissional, nunca o valor_recebido, que só existe pros relatórios do proprietário.
        self.assertEqual(pagamento.valor, Decimal("150.00"))
        self.assertEqual(pagamento.valor_recebido, Decimal("144.00"))
        self.assertEqual(pagamento.parcelas, 3)

    def test_registra_valor_recebido_sem_parcelas_no_pagamento_cartao_debito(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)

        resposta = self.client.post(
            reverse("agenda:agendamento_confirmar_pagamento", args=[agendamento.pk]),
            {"forma_pagamento_1": "CARTAO_DEBITO", "valor_recebido_1": "147.00"},
        )
        self.assertEqual(resposta.status_code, 302)

        pagamento = PagamentoParticular.objects.get(agendamento=agendamento)
        self.assertEqual(pagamento.valor, Decimal("150.00"))
        self.assertEqual(pagamento.valor_recebido, Decimal("147.00"))
        self.assertIsNone(pagamento.parcelas)

    def test_bloqueia_pagamento_cartao_sem_valor_recebido(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)

        resposta = self.client.post(
            reverse("agenda:agendamento_confirmar_pagamento", args=[agendamento.pk]),
            {"forma_pagamento_1": "CARTAO_CREDITO"},
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(PagamentoParticular.objects.filter(agendamento=agendamento).exists())
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.status, Agendamento.Status.PRE_AUTORIZADO)

    def test_resumo_do_log_auditoria_inclui_valor_recebido(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)

        self.client.post(
            reverse("agenda:agendamento_confirmar_pagamento", args=[agendamento.pk]),
            {"forma_pagamento_1": "CARTAO_DEBITO", "valor_recebido_1": "146.50"},
        )

        ultimo_log = LogAuditoria.objects.filter(usuario=self.usuario).latest("data_hora")
        self.assertIn("recebido R$ 146,50", ultimo_log.acao)  # formato brasileiro desde 06/11/2026

    def test_sem_acesso_a_agenda_volta_para_fila_de_pagamento(self):
        """Perfil Financeiro padrão não vê a Agenda do dia — voltar pra lá dava 403 logo
        depois de um pagamento registrado com sucesso."""
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)

        resposta = self.client.post(
            reverse("agenda:agendamento_confirmar_pagamento", args=[agendamento.pk]),
            {"forma_pagamento_1": "DINHEIRO"},
            follow=True,
        )
        self.assertRedirects(resposta, reverse("financeiro:fila_pagamento_lista"))
        self.assertContains(resposta, "Pagamento registrado")

    def test_com_acesso_a_agenda_continua_voltando_para_agenda_do_dia(self):
        self.usuario.user_permissions.add(Permission.objects.get(codename="agenda_ver_dia"))
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)

        resposta = self.client.post(
            reverse("agenda:agendamento_confirmar_pagamento", args=[agendamento.pk]),
            {"forma_pagamento_1": "DINHEIRO"},
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(resposta["Location"].startswith(reverse("agenda:agenda_dia")))


class AgendaDiaTelefoneTests(AgendaTestBase):
    """Lista do dia mostra o telefone do cadastro do paciente, pra recepção confirmar."""

    def setUp(self):
        super().setUp()
        usuario = Usuario.objects.create_user(username="recepcao", password="x", exigir_troca_senha=False)
        usuario.user_permissions.add(Permission.objects.get(codename="agenda_ver_dia"))
        self.client.force_login(usuario)

    def test_lista_do_dia_mostra_telefone_do_paciente(self):
        self.paciente.telefone = "(91) 98888-7777"
        self.paciente.save(update_fields=["telefone"])
        agendamento = self._criar_agendamento()

        resposta = self.client.get(reverse("agenda:agenda_dia"), {"data": agendamento.data_hora_inicio.date().isoformat()})
        self.assertContains(resposta, "<th>Telefone</th>", html=True)
        self.assertContains(resposta, "(91) 98888-7777")


class StatusAutomaticoDoDiaTests(AgendaTestBase):
    """Decisão do cliente (2026-10-16): no dia, Agendado vira Pré-confirmado sozinho; na
    virada do dia, quem não foi confirmado nem atendido vira Faltou. Ver
    agenda.models.atualizar_status_do_dia e Agendamento.save()."""

    def _em(self, dias, status):
        inicio = timezone.make_aware(
            datetime.combine(timezone.localdate() + timedelta(days=dias), datetime.min.time())
        ) + timedelta(hours=10)
        agendamento = Agendamento.objects.create(
            paciente=self.paciente,
            profissional=self.profissional,
            forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
            data_hora_inicio=inicio,
            data_hora_fim=inicio + timedelta(minutes=30),
            status=status,
        )
        # Grava o status pedido mesmo que o save() tenha pré-confirmado (simula agendamento
        # de hoje que ainda está "Agendado" porque foi criado antes do dia chegar).
        Agendamento.objects.filter(pk=agendamento.pk).update(status=status)
        agendamento.refresh_from_db()
        return agendamento

    def _status(self, agendamento):
        agendamento.refresh_from_db()
        return agendamento.status

    def test_encaixe_marcado_para_hoje_ja_nasce_pre_confirmado(self):
        inicio = timezone.localtime().replace(hour=15, minute=0, second=0, microsecond=0)
        encaixe = Agendamento.objects.create(
            paciente=self.paciente,
            profissional=self.profissional,
            forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
            data_hora_inicio=inicio,
            data_hora_fim=inicio + timedelta(minutes=30),
            encaixe=True,
        )
        self.assertEqual(self._status(encaixe), Agendamento.Status.PRE_CONFIRMADO)

    def test_agendado_para_amanha_continua_agendado(self):
        inicio = timezone.localtime().replace(hour=15, minute=0, second=0, microsecond=0) + timedelta(days=1)
        amanha = Agendamento.objects.create(
            paciente=self.paciente,
            profissional=self.profissional,
            forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
            data_hora_inicio=inicio,
            data_hora_fim=inicio + timedelta(minutes=30),
        )
        self.assertEqual(self._status(amanha), Agendamento.Status.AGENDADO)

    def test_save_com_update_fields_tambem_grava_o_status(self):
        hoje = self._em(0, Agendamento.Status.AGENDADO)
        hoje.observacoes = "ligar antes"
        hoje.save(update_fields=["observacoes"])
        self.assertEqual(self._status(hoje), Agendamento.Status.PRE_CONFIRMADO)

    def test_varredura_do_dia(self):
        from .models import atualizar_status_do_dia

        hoje_agendado = self._em(0, Agendamento.Status.AGENDADO)
        hoje_confirmado = self._em(0, Agendamento.Status.CONFIRMADO)
        ontem_agendado = self._em(-1, Agendamento.Status.AGENDADO)
        ontem_pre = self._em(-1, Agendamento.Status.PRE_CONFIRMADO)
        ontem_confirmado = self._em(-1, Agendamento.Status.CONFIRMADO)
        ontem_autorizado = self._em(-1, Agendamento.Status.AUTORIZADO)
        ontem_cancelado = self._em(-1, Agendamento.Status.CANCELADO)
        amanha_agendado = self._em(1, Agendamento.Status.AGENDADO)

        self.assertEqual(atualizar_status_do_dia(forcar=True), (1, 2))

        self.assertEqual(self._status(hoje_agendado), Agendamento.Status.PRE_CONFIRMADO)
        self.assertEqual(self._status(hoje_confirmado), Agendamento.Status.CONFIRMADO)
        self.assertEqual(self._status(ontem_agendado), Agendamento.Status.FALTOU)
        self.assertEqual(self._status(ontem_pre), Agendamento.Status.FALTOU)
        self.assertEqual(self._status(ontem_confirmado), Agendamento.Status.CONFIRMADO)
        self.assertEqual(self._status(ontem_autorizado), Agendamento.Status.AUTORIZADO)
        self.assertEqual(self._status(ontem_cancelado), Agendamento.Status.CANCELADO)
        self.assertEqual(self._status(amanha_agendado), Agendamento.Status.AGENDADO)

    def test_middleware_roda_uma_vez_por_dia(self):
        from django.test import override_settings

        from . import models as agenda_models

        agenda_models._ultima_atualizacao_status = None
        ontem = self._em(-1, Agendamento.Status.AGENDADO)
        with override_settings(AGENDA_STATUS_AUTOMATICO=True):
            self.client.get(reverse("login"))
            self.assertEqual(self._status(ontem), Agendamento.Status.FALTOU)

            # Já rodou hoje: um novo atrasado só é pego na próxima virada de dia.
            outro = self._em(-2, Agendamento.Status.AGENDADO)
            self.client.get(reverse("login"))
            self.assertEqual(self._status(outro), Agendamento.Status.AGENDADO)
        agenda_models._ultima_atualizacao_status = None


class AgendadoPorTests(AgendaTestBase):
    """Pedido da clínica (2026-10-16): obs pequena de qual usuário fez o agendamento."""

    def test_lista_do_dia_e_edicao_mostram_quem_agendou(self):
        usuario = Usuario.objects.create_user(
            username="maria", first_name="Maria", last_name="Recepção", password="x", exigir_troca_senha=False
        )
        for codename in ("agenda_ver_dia", "agenda_editar"):
            usuario.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(usuario)
        agendamento = self._criar_agendamento()
        agendamento.criado_por = usuario
        agendamento.save(update_fields=["criado_por"])

        lista = self.client.get(reverse("agenda:agenda_dia"), {"data": "2026-07-20"})
        self.assertContains(lista, "agendado por Maria Recepção")
        edicao = self.client.get(reverse("agenda:agendamento_editar", args=[agendamento.pk]))
        self.assertContains(edicao, "Agendado por Maria Recepção")


class MudarStatusVoltaParaLinhaTests(AgendaTestBase):
    """Pré-confirmar/Confirmar volta pra mesma linha da Agenda do dia (âncora), não pro topo."""

    def test_volta_para_a_ancora_da_linha(self):
        usuario = Usuario.objects.create_user(username="recepcao", password="x", exigir_troca_senha=False)
        for codename in ("agenda_ver_dia", "agenda_editar"):
            usuario.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(usuario)
        agendamento = self._criar_agendamento()
        origem = reverse("agenda:agenda_dia") + "?data=2026-07-20"

        resposta = self.client.post(
            reverse("agenda:agendamento_mudar_status", args=[agendamento.pk, "CONFIRMADO"]),
            HTTP_REFERER="http://testserver" + origem,
        )
        self.assertEqual(resposta["Location"], f"http://testserver{origem}#agendamento-{agendamento.pk}")

        lista = self.client.get(origem)
        self.assertContains(lista, f'id="agendamento-{agendamento.pk}"')


class PagamentoParticularModelTests(AgendaTestBase):
    def test_valor_recebido_efetivo_usa_valor_recebido_quando_preenchido(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.REALIZADO)
        pagamento = PagamentoParticular.objects.create(
            agendamento=agendamento,
            forma_pagamento=Agendamento.FormaPagamento.CARTAO_CREDITO,
            valor=Decimal("150.00"),
            valor_recebido=Decimal("144.00"),
            parcelas=3,
        )
        self.assertEqual(pagamento.valor_recebido_efetivo, Decimal("144.00"))

    def test_valor_recebido_efetivo_cai_para_valor_quando_nulo(self):
        """Cobre tanto dinheiro/Pix (nunca têm valor_recebido) quanto cartão registrado antes
        desta feature existir (valor_recebido=NULL em dado histórico)."""
        agendamento = self._criar_agendamento(status=Agendamento.Status.REALIZADO)
        pagamento = PagamentoParticular.objects.create(
            agendamento=agendamento, forma_pagamento=Agendamento.FormaPagamento.DINHEIRO, valor=Decimal("100.00")
        )
        self.assertEqual(pagamento.valor_recebido_efetivo, Decimal("100.00"))


class FilaDeChamadaExameComPreparoTests(AgendaTestBase):
    """Exame com preparo (Procedimento.exame_com_preparo) NÃO fura mais a fila na 'Minha
    agenda' (pedido da clínica, 2026-10-26): vale a ordem de autorização — a prioridade dele
    agora é a senha própria no totem. Ver agenda.views._ordena_fila_de_chamada."""

    def setUp(self):
        super().setUp()
        self.exame_preparo = Procedimento.objects.create(
            nome="Exame com Preparo", exame_com_preparo=True, valor_particular=Decimal("90.00")
        )
        self._autorizado_base = timezone.make_aware(datetime(2026, 7, 20, 7, 0))
        self._contador_autorizacao = 0

    def _autorizado(self, procedimento, chamado=False):
        agendamento = self._criar_agendamento(status=Agendamento.Status.AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=procedimento, quantidade=1)
        self._contador_autorizacao += 1
        agendamento.autorizado_em = self._autorizado_base + timedelta(minutes=self._contador_autorizacao)
        if chamado:
            agendamento.chamado_em = agendamento.autorizado_em + timedelta(seconds=30)
        agendamento.save()
        return agendamento

    def _ordem(self, *agendamentos):
        from .views import _ordena_fila_de_chamada

        recarregados = Agendamento.objects.filter(pk__in=[a.pk for a in agendamentos]).prefetch_related(
            "itens__procedimento"
        )
        return [a.pk for a in _ordena_fila_de_chamada(list(recarregados))]

    def test_tem_exame_com_preparo_olha_qualquer_item(self):
        agendamento = self._criar_agendamento()
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)
        self.assertFalse(agendamento.tem_exame_com_preparo)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.exame_preparo, quantidade=1)
        self.assertTrue(agendamento.tem_exame_com_preparo)

    def test_exame_com_preparo_nao_passa_na_frente_de_quem_foi_autorizado_antes(self):
        a = self._autorizado(self.consulta)
        b = self._autorizado(self.consulta)
        c = self._autorizado(self.exame_preparo)  # autorizado por último
        self.assertEqual(self._ordem(a, b, c), [a.pk, b.pk, c.pk])

    def test_quem_ja_foi_chamado_fica_a_frente(self):
        aguardando = self._autorizado(self.consulta)
        em_atendimento = self._autorizado(self.consulta, chamado=True)
        preparo = self._autorizado(self.exame_preparo)
        self.assertEqual(self._ordem(aguardando, em_atendimento, preparo), [em_atendimento.pk, aguardando.pk, preparo.pk])

    def test_sem_exame_com_preparo_a_ordem_nao_muda(self):
        a = self._autorizado(self.consulta)
        b = self._autorizado(self.exame)
        c = self._autorizado(self.consulta)
        self.assertEqual(self._ordem(a, b, c), [a.pk, b.pk, c.pk])

    def test_outros_status_nao_sao_reordenados(self):
        agendado = self._criar_agendamento(status=Agendamento.Status.AGENDADO)  # 1ª hora do dia, nunca autorizado
        AgendamentoItem.objects.create(agendamento=agendado, procedimento=self.exame_preparo, quantidade=1)
        a = self._autorizado(self.consulta)
        b = self._autorizado(self.exame_preparo)
        ordem = self._ordem(agendado, a, b)
        self.assertEqual([pk for pk in ordem if pk != agendado.pk], [a.pk, b.pk])

    def test_minha_agenda_lista_so_autorizados(self):
        """Pedido do cliente (2026-10-16): a lista de hoje do profissional mostra só Autorizados."""
        usuario = Usuario.objects.create_user(
            username="prof", password="x", profissional_id=self.profissional.pk, exigir_troca_senha=False
        )
        usuario.user_permissions.add(Permission.objects.get(codename="agenda_ver_propria"))
        self.client.force_login(usuario)
        base = timezone.make_aware(datetime.combine(timezone.localdate(), datetime.min.time())) + timedelta(hours=8)
        for minuto, (nome, status) in enumerate(
            [
                ("Ana Autorizada", Agendamento.Status.AUTORIZADO),
                ("Bia Confirmada", Agendamento.Status.CONFIRMADO),
                ("Caio Faltou", Agendamento.Status.FALTOU),
                ("Duda Atendida", Agendamento.Status.REALIZADO),
            ]
        ):
            inicio = base + timedelta(minutes=minuto * 10)
            Agendamento.objects.create(
                paciente=Paciente.objects.create(nome_completo=nome),
                profissional=self.profissional,
                forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
                data_hora_inicio=inicio,
                data_hora_fim=inicio + timedelta(minutes=10),
                status=status,
                autorizado_em=inicio,
            )

        resposta = self.client.get(reverse("agenda:agenda_propria"))
        self.assertEqual([a.paciente.nome_completo for a in resposta.context["agendamentos"]], ["Ana Autorizada"])

    def test_faixa_agora_do_inicio_leva_pra_minha_agenda(self):
        """Regressão: 'Ver agenda do dia' na faixa "Agora" do Início apontava pra Agenda do
        dia geral (permissão de recepção) e dava 403 pro perfil Profissional."""
        usuario = Usuario.objects.create_user(
            username="prof", password="x", profissional_id=self.profissional.pk, exigir_troca_senha=False
        )
        usuario.user_permissions.add(Permission.objects.get(codename="agenda_ver_propria"))
        self.client.force_login(usuario)
        inicio = timezone.make_aware(datetime.combine(timezone.localdate(), datetime.min.time())) + timedelta(hours=23)
        Agendamento.objects.create(
            paciente=self.paciente,
            profissional=self.profissional,
            forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
            data_hora_inicio=inicio,
            data_hora_fim=inicio + timedelta(minutes=10),
            status=Agendamento.Status.AUTORIZADO,
            autorizado_em=inicio,
        )

        conteudo = self.client.get(reverse("home")).content.decode()
        self.assertIn(f'href="{reverse("agenda:agenda_propria")}" class="botao-agora"', conteudo)
        self.assertEqual(self.client.get(reverse("agenda:agenda_propria")).status_code, 200)

    def test_tela_marca_ordem_de_chamada_com_prioridade(self):
        hoje = timezone.localdate()
        usuario = Usuario.objects.create_user(
            username="prof", password="x", profissional_id=self.profissional.pk, exigir_troca_senha=False
        )
        for codename in ("agenda_ver_propria", "fila_chamar_senha", "atendimento_realizar"):
            usuario.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(usuario)

        def _cria(procedimento, minuto):
            inicio = timezone.make_aware(datetime.combine(hoje, datetime.min.time())) + timedelta(hours=8, minutes=minuto)
            agendamento = Agendamento.objects.create(
                paciente=self.paciente,
                profissional=self.profissional,
                forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
                data_hora_inicio=inicio,
                data_hora_fim=inicio + timedelta(minutes=10),
                status=Agendamento.Status.AUTORIZADO,
                autorizado_em=inicio,
            )
            AgendamentoItem.objects.create(agendamento=agendamento, procedimento=procedimento, quantidade=1)
            return agendamento

        normal = _cria(self.consulta, 0)
        preparo = _cria(self.exame_preparo, 20)

        resposta = self.client.get(reverse("agenda:agenda_propria"))
        self.assertEqual(resposta.status_code, 200)
        lista = resposta.context["agendamentos"]
        self.assertEqual([a.pk for a in lista], [normal.pk, preparo.pk])
        self.assertEqual([a.ordem_chamada_hoje for a in lista], [1, 2])
        self.assertContains(resposta, "Exame com preparo")


class RecepcaoPropriaTests(AgendaTestBase):
    """Chamada em lote pra Recepção 2 (Sala.lugares_recepcao_propria) — ver
    agenda.views.agendamento_chamar_recepcao e o painel público fila.views.painel_espera."""

    def setUp(self):
        super().setUp()
        from cadastros.models import AtendimentoSala, Sala

        self.sala_com_recepcao = Sala.objects.create(nome="Consultório 9", lugares_recepcao_propria=5)
        self.sala_comum = Sala.objects.create(nome="Consultório 1")
        self.AtendimentoSala = AtendimentoSala
        self.hoje = timezone.localdate()
        self.exame_preparo = Procedimento.objects.create(
            nome="Exame com Preparo", exame_com_preparo=True, valor_particular=Decimal("90.00")
        )
        self.usuario = Usuario.objects.create_user(
            username="prof", password="x", profissional_id=self.profissional.pk, exigir_troca_senha=False
        )
        for codename in ("agenda_ver_propria", "fila_chamar_senha", "atendimento_realizar"):
            self.usuario.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(self.usuario)
        self._n = 0

    def _escolhe_sala(self, sala):
        self.AtendimentoSala.objects.create(profissional=self.profissional, sala=sala, data=self.hoje)

    def _autorizado(self, procedimento=None, nome=None):
        self._n += 1
        paciente = Paciente.objects.create(nome_completo=nome or f"Paciente Numero{self._n}")
        inicio = timezone.make_aware(datetime.combine(self.hoje, datetime.min.time())) + timedelta(hours=7, minutes=self._n)
        agendamento = Agendamento.objects.create(
            paciente=paciente,
            profissional=self.profissional,
            forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
            data_hora_inicio=inicio,
            data_hora_fim=inicio + timedelta(minutes=10),
            status=Agendamento.Status.AUTORIZADO,
            autorizado_em=inicio,
        )
        AgendamentoItem.objects.create(
            agendamento=agendamento, procedimento=procedimento or self.consulta, quantidade=1
        )
        return agendamento

    def _chamar(self):
        return self.client.post(reverse("agenda:agendamento_chamar_recepcao"))

    def test_sala_sem_recepcao_propria_nao_chama_ninguem(self):
        self._escolhe_sala(self.sala_comum)
        a = self._autorizado()
        self._chamar()
        a.refresh_from_db()
        self.assertIsNone(a.chamado_em)
        self.assertIsNone(a.lote_recepcao)

    def test_sem_sala_definida_nao_chama_ninguem(self):
        a = self._autorizado()
        self._chamar()
        a.refresh_from_db()
        self.assertIsNone(a.chamado_em)

    def test_chama_ate_a_capacidade_em_um_lote_so(self):
        self._escolhe_sala(self.sala_com_recepcao)
        agendamentos = [self._autorizado() for _ in range(7)]
        self._chamar()
        for a in agendamentos:
            a.refresh_from_db()
        chamados = [a for a in agendamentos if a.chamado_em]
        self.assertEqual(len(chamados), 5)
        self.assertEqual(len({a.lote_recepcao for a in chamados}), 1)
        self.assertEqual([a.ordem_no_lote for a in chamados], [1, 2, 3, 4, 5])
        self.assertTrue(all(a.sala_chamada == self.sala_com_recepcao and a.chamado_por == self.usuario for a in chamados))
        # os 2 últimos da fila seguem aguardando
        self.assertTrue(all(a.chamado_em is None for a in agendamentos[5:]))

    def test_lote_segue_a_ordem_de_autorizacao_mesmo_com_exame_com_preparo(self):
        self._escolhe_sala(self.sala_com_recepcao)
        comum = self._autorizado()
        preparo = self._autorizado(self.exame_preparo)
        self._chamar()
        comum.refresh_from_db()
        preparo.refresh_from_db()
        self.assertEqual(comum.ordem_no_lote, 1)
        self.assertEqual(preparo.ordem_no_lote, 2)

    def test_recepcao_cheia_nao_chama_mais_e_vaga_liberada_entra_no_mesmo_lote(self):
        self._escolhe_sala(self.sala_com_recepcao)
        agendamentos = [self._autorizado() for _ in range(7)]
        self._chamar()
        self._chamar()  # cheia: nada muda
        for a in agendamentos:
            a.refresh_from_db()
        self.assertEqual(sum(1 for a in agendamentos if a.chamado_em), 5)

        primeiro = agendamentos[0]
        primeiro.status = Agendamento.Status.REALIZADO
        primeiro.save()
        self._chamar()  # 1 vaga livre -> chama só 1
        for a in agendamentos:
            a.refresh_from_db()
        self.assertEqual(sum(1 for a in agendamentos if a.chamado_em), 6)
        sexto = agendamentos[5]
        self.assertEqual(sexto.lote_recepcao, agendamentos[1].lote_recepcao)
        self.assertEqual(sexto.ordem_no_lote, 6)
        self.assertIsNone(agendamentos[6].chamado_em)

    def test_ignora_quem_ja_foi_chamado_individualmente(self):
        self._escolhe_sala(self.sala_com_recepcao)
        ja_chamado = self._autorizado()
        ja_chamado.chamado_em = timezone.now()
        ja_chamado.save()
        outro = self._autorizado()
        self._chamar()
        ja_chamado.refresh_from_db()
        outro.refresh_from_db()
        self.assertIsNone(ja_chamado.lote_recepcao)
        self.assertEqual(outro.ordem_no_lote, 1)

    def test_painel_espera_mostra_a_leva_como_uma_chamada(self):
        self._escolhe_sala(self.sala_com_recepcao)
        self._autorizado(nome="Ana Souza Lima")
        self._autorizado(nome="Bruno Costa Reis")
        self._autorizado(nome="Carla Dias Melo")
        self._chamar()
        self.client.logout()  # painel é público
        resposta = self.client.get(reverse("fila:painel_espera"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Recepção 2")
        self.assertContains(resposta, "Entrar no Consultório 9")
        # nome público = primeiro + último nome; 1º em destaque, os demais na lista de espera
        self.assertContains(resposta, "Ana Lima")
        self.assertContains(resposta, "Aguardem sentados")
        self.assertContains(resposta, "Bruno Reis")
        self.assertContains(resposta, "Carla Melo")
        chamada = resposta.context["chamada_atual"]
        self.assertEqual(chamada.tipo_chamada, "recepcao_propria")

    def test_voz_da_leva_chama_todos_os_pacientes(self):
        """A voz chamava só o 1º — os demais não eram avisados pra aguardar na Recepção 2."""
        from fila import voz

        self._escolhe_sala(self.sala_com_recepcao)
        ana = self._autorizado(nome="Ana Souza Lima")
        self._autorizado(nome="Bruno Costa Reis")
        self._autorizado(nome="Carla Dias Melo")
        self._chamar()
        ana.refresh_from_db()
        lote = str(ana.lote_recepcao)
        self.assertEqual(
            voz.texto_da_chamada("recepcao_propria", lote),
            "Ana. Entrar no Consultório 9. Bruno, Carla. Aguardar na Recepção 2.",
        )

        # Ana atendida: a voz passa a falar quem está de fato entrando agora, não ela de novo.
        ana.status = Agendamento.Status.REALIZADO
        ana.save()
        self.assertEqual(
            voz.texto_da_chamada("recepcao_propria", lote),
            "Bruno. Entrar no Consultório 9. Carla. Aguardar na Recepção 2.",
        )

    def test_tela_mostra_botao_e_posicoes(self):
        self._escolhe_sala(self.sala_com_recepcao)
        self._autorizado()
        self._autorizado()
        resposta = self.client.get(reverse("agenda:agenda_propria"))
        self.assertContains(resposta, "Chamar 2 pacientes para a Recepção 2")
        self._chamar()
        resposta = self.client.get(reverse("agenda:agenda_propria"))
        self.assertContains(resposta, "2 de 5 lugares ocupados")
        self.assertContains(resposta, "No consultório")
        self.assertContains(resposta, "Recepção 2 · 2º")

    def test_tela_de_sala_comum_nao_tem_botao(self):
        self._escolhe_sala(self.sala_comum)
        self._autorizado()
        resposta = self.client.get(reverse("agenda:agenda_propria"))
        self.assertNotContains(resposta, "para a Recepção 2")


class ValorFixoParticularTests(AgendaTestBase):
    """Profissional que cobra valor próprio no particular e recebe repasse fixo por
    procedimento, sem percentual (cadastros.models.ValorFixoProfissional)."""

    def setUp(self):
        super().setUp()
        self.profissional.cobra_valor_fixo_particular = True
        self.profissional.save()
        ValorFixoProfissional.objects.create(
            profissional=self.profissional, procedimento=self.consulta,
            valor_cobrado=Decimal("200.00"), valor_repasse=Decimal("150.00"),
        )
        self.usuario = Usuario.objects.create_user(
            username="financeiro_valor_fixo", password="senha-teste-123", exigir_troca_senha=False
        )
        self.usuario.user_permissions.add(Permission.objects.get(codename="fila_autorizar_financeiro"))
        self.client.force_login(self.usuario)

    def _pagar(self, agendamento, dados):
        resposta = self.client.post(reverse("agenda:agendamento_confirmar_pagamento", args=[agendamento.pk]), dados)
        self.assertEqual(resposta.status_code, 302)
        return list(PagamentoParticular.objects.filter(agendamento=agendamento).order_by("id"))

    def test_valor_a_cobrar_usa_tabela_do_profissional(self):
        agendamento = self._criar_agendamento()
        item = AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=2)
        self.assertEqual(item.valor_a_cobrar, Decimal("400.00"))

    def test_procedimento_fora_da_tabela_fica_sem_preco(self):
        # O exame tem valor_particular padrão, mas não está na tabela do profissional.
        agendamento = self._criar_agendamento()
        item = AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.exame, quantidade=1)
        self.assertIsNone(item.valor_a_cobrar)

    def test_lista_de_procedimentos_so_traz_a_tabela_do_profissional(self):
        resposta = self.client.get(reverse("agenda:procedimentos_do_profissional", args=[self.profissional.pk]))
        ids = [p["id"] for p in resposta.json()["procedimentos"]]
        self.assertEqual(ids, [self.consulta.pk])

    def test_pagamento_congela_repasse_fixo(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)
        [pagamento] = self._pagar(agendamento, {"forma_pagamento_1": "PIX"})
        self.assertEqual(pagamento.valor, Decimal("200.00"))
        self.assertEqual(pagamento.valor_repasse_fixo, Decimal("150.00"))
        self.assertEqual(pagamento.valor_repasse, Decimal("150.00"))
        # Mudar a tabela depois não reescreve o repasse já registrado.
        self.profissional.valores_fixos.update(valor_repasse=Decimal("10.00"))
        pagamento.refresh_from_db()
        self.assertEqual(pagamento.valor_repasse, Decimal("150.00"))

    def test_pagamento_dividido_reparte_repasse_proporcional(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)
        linhas = self._pagar(agendamento, {
            "forma_pagamento_1": "DINHEIRO", "valor_1": "50.00",
            "forma_pagamento_2": "PIX", "valor_2": "150.00",
        })
        self.assertEqual([p.valor_repasse for p in linhas], [Decimal("37.50"), Decimal("112.50")])

    def test_cortesia_nao_gera_repasse(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)
        [pagamento] = self._pagar(agendamento, {"cortesia": "on"})
        self.assertEqual(pagamento.valor_repasse, Decimal("0.00"))

    def test_fechamento_usa_repasse_fixo_e_nao_percentual(self):
        from financeiro.views import _producao_por_profissional

        agendamento = self._criar_agendamento(status=Agendamento.Status.PRE_AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)
        self._pagar(agendamento, {"forma_pagamento_1": "PIX"})
        dia = agendamento.data_hora_inicio.date()
        [linha], _totais = _producao_por_profissional(dia, dia)
        self.assertEqual(linha["bruto"], Decimal("200.00"))
        self.assertEqual(linha["repasse"], Decimal("150.00"))
        self.assertEqual(linha["retido"], Decimal("50.00"))
        self.assertTrue(linha["so_valor_fixo"])
        self.assertEqual(linha["repasse_pendente"], Decimal("150.00"))

    def test_cadastro_do_profissional_salva_tabela_de_valores_fixos(self):
        self.usuario.user_permissions.add(Permission.objects.get(codename="profissional_gerenciar"))
        url = reverse("cadastros:profissional_editar", args=[self.profissional.pk])
        self.assertContains(self.client.get(url), "Valores fixos no particular")
        existente = self.profissional.valores_fixos.get()
        dados = {
            "nome": self.profissional.nome, "registro_profissional": self.profissional.registro_profissional,
            "especialidade": self.profissional.especialidade, "percentual_desconto": "30.00",
            "cobra_valor_fixo_particular": "on", "limite_atendimento_simultaneo": "1", "ativo": "on",
            "especialidades_adicionais-TOTAL_FORMS": "0", "especialidades_adicionais-INITIAL_FORMS": "0",
            "valores_fixos-TOTAL_FORMS": "2", "valores_fixos-INITIAL_FORMS": "1",
            "valores_fixos-0-id": existente.pk, "valores_fixos-0-procedimento": self.consulta.pk,
            "valores_fixos-0-valor_cobrado": "200.00", "valores_fixos-0-valor_repasse": "150.00",
            "valores_fixos-1-procedimento": self.exame.pk,
            "valores_fixos-1-valor_cobrado": "90.00", "valores_fixos-1-valor_repasse": "95.00",
        }
        resposta = self.client.post(url, dados)
        self.assertEqual(resposta.status_code, 200)  # repasse > cobrado: volta com erro
        self.assertContains(resposta, "O repasse não pode ser maior")
        dados["valores_fixos-1-valor_repasse"] = "70.00"
        self.assertEqual(self.client.post(url, dados).status_code, 302)
        self.assertEqual(self.profissional.valor_fixo_para(self.exame).valor_repasse, Decimal("70.00"))


class CentralAtendimentoDatasAvulsasTests(AgendaTestBase):
    """
    Profissional sem horário fixo semanal, que só vem em datas específicas do mês
    (cadastradas como ExcecaoAtendimento) — a Central de Atendimento precisa levar
    a recepção até essas datas, em vez de dizer que ele não tem horário cadastrado.
    """

    def setUp(self):
        super().setUp()
        from datetime import time

        from cadastros.models import ExcecaoAtendimento

        self.usuario = Usuario.objects.create_user(
            username="central_teste", password="senha-teste-123", exigir_troca_senha=False
        )
        self.usuario.user_permissions.add(Permission.objects.get(codename="central_atendimento_usar"))
        self.client.force_login(self.usuario)

        hoje = timezone.localdate()
        # Uma data de semana útil daqui a ~5 semanas (garante que não é a semana atual).
        data = hoje + timedelta(days=35)
        while data.weekday() >= 5:
            data += timedelta(days=1)
        self.data_avulsa = data
        ExcecaoAtendimento.objects.create(
            profissional=self.profissional,
            data=data,
            turno="MANHA",
            horario_inicio=time(8, 0),
            horario_fim=time(12, 0),
        )
        self.url = reverse("agenda:central_atendimento")
        self.params = {"especialidade": "medico_clinico_geral|", "profissional": self.profissional.pk}

    def test_sem_semana_escolhida_pula_para_a_proxima_data(self):
        resposta = self.client.get(self.url, self.params)
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.context["tem_horario_cadastrado"])
        self.assertEqual(resposta.context["proximas_datas_avulsas"], [self.data_avulsa])
        self.assertIn(self.data_avulsa, resposta.context["disponibilidade"]["dias"])

    def test_semana_sem_atendimento_mantem_navegacao(self):
        hoje = timezone.localdate()
        resposta = self.client.get(self.url, {**self.params, "inicio": hoje.isoformat()})
        self.assertEqual(resposta.status_code, 200)
        self.assertIsNone(resposta.context["disponibilidade"])
        self.assertIsNotNone(resposta.context["proxima_semana"])
        self.assertContains(resposta, "não atende na semana")
        self.assertNotContains(resposta, "ainda não tem horário de atendimento cadastrado")


class BuscaPacientesAgendamentoTests(AgendaTestBase):
    """O formulário de agendamento não lista mais todos os pacientes no <select> (eram
    dezenas de milhares depois da importação do sistema antigo e a tela travava) — a
    escolha passa pela busca agenda:buscar_pacientes."""

    def setUp(self):
        super().setUp()
        self.usuario = Usuario.objects.create_user(
            username="recepcao_busca", password="senha-teste-123", exigir_troca_senha=False
        )
        self.usuario.user_permissions.add(Permission.objects.get(codename="agenda_criar"))
        self.client.force_login(self.usuario)
        self.joao = Paciente.objects.create(nome_completo="JOÃO da Silva", convenio=self.convenio)
        self.maria = Paciente.objects.create(nome_completo="Maria Joana Souza")
        Paciente.objects.create(nome_completo="João Inativo", ativo=False)

    def _buscar(self, termo):
        resposta = self.client.get(reverse("agenda:buscar_pacientes"), {"q": termo})
        self.assertEqual(resposta.status_code, 200)
        return [p["nome"] for p in resposta.json()["pacientes"]]

    def test_busca_ignora_acento_maiuscula_e_ordem_das_palavras(self):
        self.assertEqual(self._buscar("joao"), ["JOÃO da Silva"])
        self.assertEqual(self._buscar("silva JOÃO"), ["JOÃO da Silva"])

    def test_nome_que_comeca_com_o_termo_vem_primeiro_e_inativo_fica_de_fora(self):
        self.assertEqual(self._buscar("jo"), ["JOÃO da Silva", "Maria Joana Souza"])

    def test_busca_devolve_convenio_do_paciente(self):
        resposta = self.client.get(reverse("agenda:buscar_pacientes"), {"q": "joao"})
        paciente = resposta.json()["pacientes"][0]
        self.assertEqual(paciente["convenio_id"], self.convenio.pk)
        self.assertEqual(paciente["convenio_nome"], "Convênio Teste")

    def test_busca_exige_permissao_de_agenda(self):
        outro = Usuario.objects.create_user(username="sem_perm", password="senha-teste-123", exigir_troca_senha=False)
        self.client.force_login(outro)
        resposta = self.client.get(reverse("agenda:buscar_pacientes"), {"q": "joao"})
        self.assertEqual(resposta.status_code, 403)

    def test_tela_nova_nao_lista_todos_os_pacientes(self):
        resposta = self.client.get(reverse("agenda:agendamento_novo"))
        self.assertNotContains(resposta, "Maria Joana Souza")
        self.assertNotContains(resposta, "JOÃO da Silva")

    def test_paciente_escolhido_continua_no_select_apos_erro(self):
        dados = {"paciente": str(self.joao.pk), "profissional": str(self.profissional.pk)}
        resposta = self.client.post(reverse("agenda:agendamento_novo"), dados)
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, f'value="{self.joao.pk}" selected data-convenio-nome="Convênio Teste"')
        self.assertNotContains(resposta, "Maria Joana Souza")


class ProcedimentosVinculadosAoProfissionalTests(AgendaTestBase):
    """Na hora de agendar só aparecem (e só são aceitos) os procedimentos vinculados ao
    profissional — antes, profissional sem nenhum vínculo liberava todos os da clínica,
    e a página chegava com todos no <select> até o JavaScript filtrar."""

    def setUp(self):
        super().setUp()
        self.usuario = Usuario.objects.create_user(
            username="recepcao_vinculo", password="senha-teste-123", exigir_troca_senha=False
        )
        self.usuario.user_permissions.add(Permission.objects.get(codename="agenda_criar"))
        self.client.force_login(self.usuario)
        self.outro = Procedimento.objects.create(nome="Procedimento De Outro", valor_particular=Decimal("50.00"))
        self.sem_vinculo = Profissional.objects.create(
            nome="Sem Vinculo", registro_profissional="CRM-SV", especialidade="medico_clinico_geral",
            percentual_desconto=Decimal("30.00"),
        )

    def _lista_api(self, profissional):
        resposta = self.client.get(reverse("agenda:procedimentos_do_profissional", args=[profissional.pk]))
        return resposta.json()

    def test_api_so_traz_vinculados(self):
        dados = self._lista_api(self.profissional)
        self.assertEqual({p["nome"] for p in dados["procedimentos"]}, {"Consulta Teste", "Exame Teste"})
        self.assertFalse(dados["sem_vinculo"])

    def test_api_sem_vinculo_nao_cai_em_todos(self):
        dados = self._lista_api(self.sem_vinculo)
        self.assertEqual(dados["procedimentos"], [])
        self.assertTrue(dados["sem_vinculo"])

    def test_pagina_ja_chega_so_com_os_vinculados(self):
        resposta = self.client.get(reverse("agenda:agendamento_novo"), {"profissional": self.profissional.pk})
        self.assertContains(resposta, "Consulta Teste")
        self.assertNotContains(resposta, "Procedimento De Outro")

    def test_pagina_sem_profissional_nao_lista_procedimentos(self):
        resposta = self.client.get(reverse("agenda:agendamento_novo"))
        self.assertNotContains(resposta, "Procedimento De Outro")
        self.assertNotContains(resposta, "Consulta Teste")

    def _post(self, profissional, procedimento):
        return self.client.post(
            reverse("agenda:agendamento_novo"),
            {
                "paciente": str(self.paciente.pk),
                "profissional": str(profissional.pk),
                "forma_atendimento": "PARTICULAR",
                "data": "2026-07-27",
                "hora_inicio": "10:00",
                "duracao_minutos": "30",
                "itens-TOTAL_FORMS": "1",
                "itens-INITIAL_FORMS": "0",
                "itens-MIN_NUM_FORMS": "1",
                "itens-MAX_NUM_FORMS": "1000",
                "itens-0-procedimento": str(procedimento.pk),
                "itens-0-quantidade": "1",
            },
        )

    def test_servidor_barra_procedimento_nao_vinculado(self):
        resposta = self._post(self.profissional, self.outro)
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "não está habilitado")
        self.assertEqual(Agendamento.objects.count(), 0)

    def test_servidor_barra_profissional_sem_nenhum_vinculo(self):
        resposta = self._post(self.sem_vinculo, self.consulta)
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "não está habilitado")
        self.assertEqual(Agendamento.objects.count(), 0)


class ResumoProfissionalAgendaDiaTests(AgendaTestBase):
    """Faixa de resumo da Agenda do dia (visão Dia) ao filtrar um profissional."""

    def setUp(self):
        super().setUp()
        usuario = Usuario.objects.create_user(username="resumo_dia", password="senha-teste-123", exigir_troca_senha=False)
        usuario.is_superuser = True
        usuario.save()
        self.client.force_login(usuario)
        self.dia = timezone.localtime(self._proximo_horario).date()
        S = Agendamento.Status
        for status in (S.AGENDADO, S.PRE_CONFIRMADO, S.CONFIRMADO, S.AUTORIZADO, S.FALTOU, S.CANCELADO):
            agendamento = self._criar_agendamento(status=status)
            AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.exame)  # do cancelado: não conta

    def _get(self, **params):
        params.setdefault("data", self.dia.isoformat())
        return self.client.get(reverse("agenda:agenda_dia"), params)

    def test_contagens_e_procedimentos(self):
        resumo = self._get(profissional=self.profissional.pk).context["resumo_profissional"]
        self.assertEqual(resumo["total"], 5)
        self.assertEqual(resumo["a_confirmar"], 2)
        self.assertEqual(resumo["confirmados"], 1)
        self.assertEqual(resumo["atendidos"], 1)
        self.assertEqual(resumo["faltas"], 1)
        self.assertEqual(resumo["cancelados"], 1)
        self.assertEqual(resumo["procedimentos"], [{"nome": "Consulta Teste", "total": 5, "retorno": False}])
        self.assertEqual(resumo["retornos"], 0)
        self.assertIsNone(resumo["proximo"])  # dia no passado

    def test_retorno_contado_separado_da_consulta(self):
        """Pedido da clínica (06/11/2026): retorno (automático ou do sistema antigo) aparece
        à parte, não somado nas consultas."""
        AgendamentoItem.objects.filter(agendamento__status=Agendamento.Status.AGENDADO).update(retorno_manual=True)
        original = AgendamentoItem.objects.get(agendamento__status=Agendamento.Status.AUTORIZADO)
        AgendamentoItem.objects.filter(agendamento__status=Agendamento.Status.FALTOU).update(retorno_de=original)
        resposta = self._get(profissional=self.profissional.pk)
        resumo = resposta.context["resumo_profissional"]
        self.assertEqual(
            resumo["procedimentos"],
            [
                {"nome": "Consulta Teste", "total": 3, "retorno": False},
                {"nome": "Consulta Teste", "total": 2, "retorno": True},
            ],
        )
        self.assertEqual(resumo["retornos"], 2)
        self.assertContains(resposta, "Consulta Teste ×3")
        self.assertContains(resposta, "Consulta Teste – retorno ×2")

    def test_resumo_aparece_na_pagina(self):
        resposta = self._get(profissional=self.profissional.pk)
        self.assertContains(resposta, "5 agendados")
        self.assertContains(resposta, "Consulta Teste ×5")

    def test_sem_filtro_ou_fora_da_visao_dia_nao_tem_resumo(self):
        self.assertNotIn("resumo_profissional", self._get().context)
        self.assertNotIn("resumo_profissional", self._get(profissional=self.profissional.pk, visao="semana").context)

    def test_proximo_paciente_quando_o_dia_e_hoje(self):
        Agendamento.objects.all().delete()
        self._proximo_horario = timezone.now() + timedelta(hours=1)
        agendamento = self._criar_agendamento()
        resumo = self._get(
            profissional=self.profissional.pk, data=timezone.localtime(agendamento.data_hora_inicio).date().isoformat()
        ).context["resumo_profissional"]
        self.assertEqual(resumo["proximo"], agendamento)


class GuiasSessaoConvenioTests(AgendaTestBase):
    """Sessão por convênio paga em unidades de 30 min: 1h = 2 sessões/guias. A 1ª guia vale
    junto com a autorização do atendimento; a 2ª é autorizada depois (tela Guias pendentes)
    e só então conta na produção (quantidade e valor do convênio)."""

    def setUp(self):
        super().setUp()
        self.sessao = Procedimento.objects.create(nome="Sessão Fono", permite_repeticao_semanal=True)
        self.sessao.profissionais_habilitados.add(self.profissional)
        TabelaPrecoConvenio.objects.create(convenio=self.convenio, procedimento=self.sessao, valor=Decimal("50.00"))
        self.profissional.convenios_atendidos.add(self.convenio)
        self.paciente.convenio = self.convenio
        self.paciente.save()
        self.usuario = Usuario.objects.create_user(username="recepcao_guias", password="senha-teste-123", exigir_troca_senha=False)
        for codename in ("agenda_criar", "agenda_editar", "fila_autorizar_recepcao", "financeiro_ver_contas_receber"):
            self.usuario.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(self.usuario)

    def _novo(self, duracao, forma="CONVENIO", quantidade="1"):
        return self.client.post(
            reverse("agenda:agendamento_novo"),
            {
                "paciente": str(self.paciente.pk),
                "profissional": str(self.profissional.pk),
                "forma_atendimento": forma,
                "data": "2026-07-27",
                "hora_inicio": "10:00",
                "duracao_minutos": str(duracao),
                "itens-TOTAL_FORMS": "1",
                "itens-INITIAL_FORMS": "0",
                "itens-MIN_NUM_FORMS": "1",
                "itens-MAX_NUM_FORMS": "1000",
                "itens-0-procedimento": str(self.sessao.pk),
                "itens-0-quantidade": quantidade,
            },
        )

    def _sessao_1h(self, status=Agendamento.Status.CONFIRMADO):
        agendamento = self._criar_agendamento(
            forma_atendimento=Agendamento.FormaAtendimento.CONVENIO, convenio=self.convenio, status=status
        )
        item = AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.sessao, quantidade=2)
        return agendamento, item

    def _autorizar_atendimento(self, agendamento):
        # Sessão por convênio precisa de uma guia de sessões do plano (ver GuiaConvenio).
        dados = {}
        for item in agendamento.itens.all():
            dados.update({
                f"item{item.pk}-guia": "nova",
                f"item{item.pk}-novo_numero": "G-123",
                f"item{item.pk}-nova_quantidade": "10",
            })
        self.client.post(reverse("agenda:agendamento_autorizar", args=[agendamento.pk]), dados)

    def _guia(self, item, acao):
        return self.client.post(reverse("agenda:guia_autorizar", args=[item.pk, acao]))

    def test_quantidade_sai_da_duracao_da_sessao_por_convenio(self):
        self._novo(60)
        self.assertEqual(AgendamentoItem.objects.get().quantidade, 2)

    def test_45_minutos_e_uma_guia(self):
        self._novo(45)
        self.assertEqual(AgendamentoItem.objects.get().quantidade, 1)

    def test_particular_mantem_a_quantidade_digitada(self):
        self.sessao.valor_particular = Decimal("80.00")
        self.sessao.save()
        self._novo(60, forma="PARTICULAR", quantidade="1")
        self.assertEqual(AgendamentoItem.objects.get().quantidade, 1)

    def test_ao_autorizar_so_conta_a_primeira_guia(self):
        agendamento, item = self._sessao_1h()
        self._autorizar_atendimento(agendamento)
        item.refresh_from_db()
        agendamento.refresh_from_db()
        self.assertEqual(item.valor_convenio, Decimal("50.00"))
        self.assertEqual(agendamento.valor_convenio, Decimal("50.00"))
        self.assertEqual(item.guias_pendentes, 1)
        self.assertEqual(agendamento.guias_pendentes, 1)

    def test_marcar_segunda_guia_soma_na_producao_e_desfazer_volta(self):
        agendamento, item = self._sessao_1h()
        self._autorizar_atendimento(agendamento)

        self._guia(item, "autorizar")
        item.refresh_from_db()
        agendamento.refresh_from_db()
        self.assertEqual(item.guias_pendentes, 0)
        self.assertEqual(item.valor_convenio, Decimal("100.00"))
        self.assertEqual(agendamento.valor_convenio, Decimal("100.00"))
        self.assertTrue(LogAuditoria.objects.filter(acao__contains="2ª guia").exists())

        self._guia(item, "desfazer")
        item.refresh_from_db()
        agendamento.refresh_from_db()
        self.assertEqual(item.guias_pendentes, 1)
        self.assertEqual(agendamento.valor_convenio, Decimal("50.00"))

    def test_segunda_guia_autorizada_antes_do_atendimento(self):
        agendamento, item = self._sessao_1h()
        self._guia(item, "autorizar")
        item.refresh_from_db()
        self.assertIsNone(item.valor_convenio)  # ainda não é produção
        self._autorizar_atendimento(agendamento)
        item.refresh_from_db()
        self.assertEqual(item.valor_convenio, Decimal("100.00"))

    def test_convenio_recebido_nao_trava_a_guia(self):
        """Recebido/lote é a cobrança clínica → convênio, à parte da produção do profissional."""
        agendamento, item = self._sessao_1h()
        self._autorizar_atendimento(agendamento)
        Agendamento.objects.filter(pk=agendamento.pk).update(convenio_recebido=True)
        self._guia(item, "autorizar")
        item.refresh_from_db()
        self.assertEqual(item.valor_convenio, Decimal("100.00"))

    def test_repasse_pago_trava_a_guia(self):
        agendamento, item = self._sessao_1h()
        self._autorizar_atendimento(agendamento)
        Agendamento.objects.filter(pk=agendamento.pk).update(convenio_repasse_pago=True)
        self._guia(item, "autorizar")
        item.refresh_from_db()
        self.assertEqual(item.guias_extras_autorizadas, 0)
        self.assertEqual(item.valor_convenio, Decimal("50.00"))

    def test_producao_conta_no_dia_do_atendimento(self):
        """2ª guia autorizada no mês seguinte conta na produção do mês do ATENDIMENTO."""
        from financeiro.views import _convenios_por_procedimento

        agendamento, item = self._sessao_1h()
        self._autorizar_atendimento(agendamento)
        self._guia(item, "autorizar")  # "hoje", meses depois do atendimento
        dia = timezone.localtime(agendamento.data_hora_inicio).date()
        grupos, _ = _convenios_por_procedimento(dia.replace(day=1), dia)
        self.assertEqual(grupos[0]["total"], 2)

    def test_migracao_converte_so_sessoes_futuras_ainda_nao_autorizadas(self):
        import importlib

        from django.apps import apps as django_apps

        migracao = importlib.import_module("agenda.migrations.0024_converter_sessoes_futuras_em_guias")
        S = Agendamento.Status
        casos = {}
        for nome, futuro, status, minutos in [
            ("futura_1h", True, S.CONFIRMADO, 60),
            ("futura_1h30", True, S.AGENDADO, 90),
            ("futura_30min", True, S.AGENDADO, 30),
            ("futura_autorizada", True, S.AUTORIZADO, 60),
            ("passada", False, S.CONFIRMADO, 60),
            ("cancelada", True, S.CANCELADO, 60),
        ]:
            inicio = timezone.now() + (timedelta(days=3) if futuro else -timedelta(days=3))
            agendamento = Agendamento.objects.create(
                paciente=self.paciente, profissional=self.profissional, forma_atendimento="CONVENIO",
                convenio=self.convenio, status=status, encaixe=True, observacoes="teste",
                data_hora_inicio=inicio, data_hora_fim=inicio + timedelta(minutes=minutos),
            )
            casos[nome] = AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.sessao)

        migracao.converter(django_apps, None)

        esperado = {"futura_1h": 2, "futura_1h30": 3, "futura_30min": 1, "futura_autorizada": 1, "passada": 1, "cancelada": 1}
        for nome, quantidade in esperado.items():
            casos[nome].refresh_from_db()
            self.assertEqual(casos[nome].quantidade, quantidade, nome)

    def test_lista_de_pendentes(self):
        self._proximo_horario = timezone.now()  # "todas" mostra só os últimos 60 dias
        agendamento, item = self._sessao_1h()
        cancelado, _ = self._sessao_1h(status=Agendamento.Status.CANCELADO)
        resposta = self.client.get(reverse("agenda:guias_pendentes"))
        self.assertEqual([i.pk for i in resposta.context["itens"]], [item.pk])
        self._guia(item, "autorizar")
        resposta = self.client.get(reverse("agenda:guias_pendentes"))
        self.assertEqual(list(resposta.context["itens"]), [])
        resposta = self.client.get(reverse("agenda:guias_pendentes"), {"situacao": "todas"})
        self.assertEqual([i.pk for i in resposta.context["itens"]], [item.pk])

    def test_relatorio_conta_so_guias_autorizadas(self):
        from financeiro.views import _convenios_por_procedimento

        agendamento, item = self._sessao_1h()
        self._autorizar_atendimento(agendamento)
        dia = timezone.localtime(agendamento.data_hora_inicio).date()
        grupos, _ = _convenios_por_procedimento(dia, dia)
        self.assertEqual(grupos[0]["total"], 1)
        self._guia(item, "autorizar")
        grupos, _ = _convenios_por_procedimento(dia, dia)
        self.assertEqual(grupos[0]["total"], 2)

    def test_sem_permissao_nao_autoriza_guia(self):
        _agendamento, item = self._sessao_1h()
        outro = Usuario.objects.create_user(username="sem_guia", password="senha-teste-123", exigir_troca_senha=False)
        self.client.force_login(outro)
        self.assertEqual(self._guia(item, "autorizar").status_code, 403)


class DuracaoSoParaSessaoTests(AgendaTestBase):
    """Só sessão tem hora marcada/duração/conflito de horário (pedido da clínica, 13/10/2026):
    consulta e exame entram na lista do turno do profissional, na ordem de agendamento."""

    def setUp(self):
        super().setUp()
        self.sessao = Procedimento.objects.create(
            nome="Sessão Fisio", permite_repeticao_semanal=True, valor_particular=Decimal("70.00")
        )
        self.sessao.profissionais_habilitados.add(self.profissional)
        self.usuario = Usuario.objects.create_user(username="recepcao_janela", password="senha-teste-123", exigir_troca_senha=False)
        self.usuario.user_permissions.add(Permission.objects.get(codename="agenda_criar"))
        self.client.force_login(self.usuario)

    def _novo(self, procedimento, hora="08:00", duracao="", turno="", paciente=None):
        return self.client.post(
            reverse("agenda:agendamento_novo"),
            {
                "paciente": str((paciente or self.paciente).pk),
                "profissional": str(self.profissional.pk),
                "forma_atendimento": "PARTICULAR",
                "data": "2026-07-27",
                "turno": turno,
                "hora_inicio": hora,
                "duracao_minutos": duracao,
                "itens-TOTAL_FORMS": "1",
                "itens-INITIAL_FORMS": "0",
                "itens-MIN_NUM_FORMS": "1",
                "itens-MAX_NUM_FORMS": "1000",
                "itens-0-procedimento": str(procedimento.pk),
                "itens-0-quantidade": "1",
            },
        )

    def test_consulta_sem_duracao_e_varias_no_mesmo_horario(self):
        for _ in range(5):
            self.assertEqual(self._novo(self.consulta).status_code, 302)
        self.assertEqual(Agendamento.objects.count(), 5)
        for agendamento in Agendamento.objects.all():
            self.assertEqual(agendamento.data_hora_fim, agendamento.data_hora_inicio)
            self.assertFalse(agendamento.tem_horario_fim)

    def test_sessao_exige_duracao(self):
        resposta = self._novo(self.sessao)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Agendamento.objects.count(), 0)

    def test_sessao_conflita_com_sessao_mas_nao_com_consulta(self):
        self.assertEqual(self._novo(self.consulta, hora="08:00").status_code, 302)
        self.assertEqual(self._novo(self.sessao, hora="08:00", duracao="30").status_code, 302)
        resposta = self._novo(self.sessao, hora="08:15", duracao="30")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Conflito de agenda")
        self.assertEqual(Agendamento.objects.count(), 2)

    def test_consulta_por_turno_entra_na_ordem_de_agendamento(self):
        from cadastros.models import HorarioAtendimento

        HorarioAtendimento.objects.create(
            profissional=self.profissional, dia_semana=0, turno="TARDE",
            horario_inicio=datetime(2026, 1, 1, 13, 30).time(), horario_fim=datetime(2026, 1, 1, 17, 0).time(),
        )
        nomes = ["Primeiro", "Segundo", "Terceiro"]
        for nome in nomes:
            paciente = Paciente.objects.create(nome_completo=nome)
            self.assertEqual(self._novo(self.consulta, hora="", turno="TARDE", paciente=paciente).status_code, 302)
        agendamentos = Agendamento.numerar_ordem_lista(list(Agendamento.objects.all()))
        self.assertEqual([a.paciente.nome_completo for a in agendamentos], nomes)
        self.assertEqual([a.ordem_lista for a in agendamentos], [1, 2, 3])
        # Grava o início do turno do próprio profissional (13h30), sem hora marcada.
        self.assertEqual(timezone.localtime(agendamentos[0].data_hora_inicio).time(), datetime(2026, 1, 1, 13, 30).time())
        self.assertEqual(agendamentos[2].horario_exibicao, "Tarde · 3º")

    def test_turno_em_que_o_profissional_nao_atende_e_recusado(self):
        from cadastros.models import HorarioAtendimento

        HorarioAtendimento.objects.create(
            profissional=self.profissional, dia_semana=0, turno="MANHA",
            horario_inicio=datetime(2026, 1, 1, 8, 0).time(), horario_fim=datetime(2026, 1, 1, 12, 0).time(),
        )
        resposta = self._novo(self.consulta, hora="", turno="TARDE")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "não atende no turno da tarde")
        self.assertEqual(Agendamento.objects.count(), 0)

    def test_consulta_sem_turno_nem_horario_e_recusada(self):
        resposta = self._novo(self.consulta, hora="", turno="")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Agendamento.objects.count(), 0)


class AgendaDiaLembraProfissionalTests(AgendaTestBase):
    """Trocar o dia sem ?profissional= (voltar depois de salvar, "trocar o dia", menu)
    não desmarca mais o profissional escolhido no filtro (pedido da clínica, 23/10/2026)."""

    def setUp(self):
        super().setUp()
        usuario = Usuario.objects.create_user(username="lembra_prof", password="senha-teste-123", exigir_troca_senha=False)
        usuario.is_superuser = True
        usuario.save()
        self.client.force_login(usuario)
        self.url = reverse("agenda:agenda_dia")

    def test_mantem_profissional_ao_trocar_o_dia(self):
        self.client.get(self.url, {"data": "2026-07-20", "profissional": self.profissional.pk})
        resposta = self.client.get(self.url, {"data": "2026-07-21"})
        self.assertEqual(resposta.context["profissional_selecionado"], str(self.profissional.pk))

    def test_todos_limpa_a_lembranca(self):
        self.client.get(self.url, {"data": "2026-07-20", "profissional": self.profissional.pk})
        self.client.get(self.url, {"data": "2026-07-20", "profissional": ""})
        resposta = self.client.get(self.url, {"data": "2026-07-21"})
        self.assertEqual(resposta.context["profissional_selecionado"], "")

    def test_profissional_desativado_nao_fica_preso(self):
        self.client.get(self.url, {"data": "2026-07-20", "profissional": self.profissional.pk})
        Profissional.objects.filter(pk=self.profissional.pk).update(ativo=False)
        resposta = self.client.get(self.url, {"data": "2026-07-21"})
        self.assertEqual(resposta.context["profissional_selecionado"], "")


class RepetirUltimoAgendamentoTests(AgendaTestBase):
    """Botão "Repetir último agendamento" da Central / formulário (pedido da clínica,
    23/10/2026): puxa profissional, forma, convênio e procedimentos — não o paciente."""

    def setUp(self):
        super().setUp()
        self.usuario = Usuario.objects.create_user(
            username="repetir_teste", password="senha-teste-123", exigir_troca_senha=False
        )
        for codename in ("agenda_criar", "central_atendimento_usar"):
            self.usuario.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(self.usuario)
        self.ultimo = self._criar_agendamento(
            forma_atendimento=Agendamento.FormaAtendimento.CONVENIO, convenio=self.convenio
        )
        self.ultimo.criado_por = self.usuario
        self.ultimo.save()
        AgendamentoItem.objects.create(agendamento=self.ultimo, procedimento=self.consulta)
        AgendamentoItem.objects.create(agendamento=self.ultimo, procedimento=self.exame)

    def test_central_mostra_botao(self):
        resposta = self.client.get(reverse("agenda:central_atendimento"))
        self.assertEqual(resposta.context["ultimo_agendamento"], self.ultimo)
        self.assertIn(f"repetir={self.ultimo.pk}", resposta.context["url_repetir_ultimo"])
        self.assertContains(resposta, "Repetir último agendamento")

    def test_central_profissional_sem_especialidade_cai_na_dele(self):
        resposta = self.client.get(reverse("agenda:central_atendimento"), {"profissional": self.profissional.pk})
        self.assertEqual(resposta.context["especialidade_selecionada"], "medico_clinico_geral|")
        self.assertEqual(resposta.context["profissional_selecionado"], self.profissional)

    def test_formulario_pre_preenchido(self):
        resposta = self.client.get(
            reverse("agenda:agendamento_novo"),
            {"data": "2026-07-27", "profissional": self.profissional.pk, "repetir": self.ultimo.pk},
        )
        self.assertEqual(resposta.context["repetindo"], self.ultimo)
        form = resposta.context["form"]
        self.assertEqual(form["forma_atendimento"].value(), "CONVENIO")
        self.assertEqual(form["convenio"].value(), self.convenio.pk)
        self.assertFalse(form["paciente"].value())
        procedimentos = [f["procedimento"].value() for f in resposta.context["formset"].forms]
        self.assertEqual(sorted(procedimentos), sorted([self.consulta.pk, self.exame.pk]))

    def test_outro_profissional_nao_repete(self):
        outro = Profissional.objects.create(nome="Outro", registro_profissional="CRM-2", percentual_desconto=Decimal("30.00"))
        resposta = self.client.get(
            reverse("agenda:agendamento_novo"), {"profissional": outro.pk, "repetir": self.ultimo.pk}
        )
        self.assertIsNone(resposta.context["repetindo"])
        self.assertEqual(len(resposta.context["formset"].forms), 1)


class FilaDeChamadaPrioritarioTests(AgendaTestBase):
    """Prioritário na fila do profissional (pedido da clínica, 29/10/2026): passa na frente de
    normal já autorizado, alternando 1 prioritário e 1 normal. Ver agenda.views.
    _ordena_fila_de_chamada/_intercala_prioritarios."""

    def setUp(self):
        super().setUp()
        self._base = timezone.make_aware(datetime(2026, 7, 20, 7, 0))
        self._minuto = 0

    def _autorizado(self, motivo="", chamado_em=None):
        agendamento = self._criar_agendamento(status=Agendamento.Status.AUTORIZADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)
        self._minuto += 1
        agendamento.autorizado_em = self._base + timedelta(minutes=self._minuto)
        agendamento.motivo_prioridade = motivo
        agendamento.chamado_em = chamado_em
        agendamento.save()
        return agendamento

    def _ordem(self, *agendamentos):
        from .views import _ordena_fila_de_chamada

        recarregados = Agendamento.objects.filter(pk__in=[a.pk for a in agendamentos])
        return [a.pk for a in _ordena_fila_de_chamada(list(recarregados))]

    def test_sem_prioritario_vale_ordem_de_autorizacao(self):
        a, b, c = self._autorizado(), self._autorizado(), self._autorizado()
        self.assertEqual(self._ordem(a, b, c), [a.pk, b.pk, c.pk])

    def test_prioritario_alterna_um_a_um_com_os_normais(self):
        n1, n2, n3, n4 = (self._autorizado() for _ in range(4))
        p1, p2, p3 = (self._autorizado("idoso_60") for _ in range(3))
        self.assertEqual(
            self._ordem(n1, n2, n3, n4, p1, p2, p3),
            [p1.pk, n1.pk, p2.pk, n2.pk, p3.pk, n3.pk, n4.pk],
        )

    def test_se_o_ultimo_chamado_foi_prioritario_a_vez_e_do_normal(self):
        self._autorizado("gestante", chamado_em=self._base + timedelta(hours=1))
        n1 = self._autorizado()
        p1 = self._autorizado("idoso_60")
        ordem = self._ordem(n1, p1)
        self.assertEqual(ordem, [n1.pk, p1.pk])

    def test_prioritario_que_chegou_antes_nao_perde_a_vez_pro_normal(self):
        self._autorizado(chamado_em=self._base + timedelta(hours=1))  # último chamado: normal
        p1 = self._autorizado("lactante")
        p2 = self._autorizado("idoso_60")
        n1 = self._autorizado()
        # P1 (vez do P), depois vez do N — mas P2 chegou antes de N1, então vai primeiro.
        self.assertEqual(self._ordem(p1, p2, n1), [p1.pk, p2.pk, n1.pk])

    def test_em_atendimento_continua_no_topo(self):
        n1 = self._autorizado()
        em_atendimento = self._autorizado(chamado_em=self._base + timedelta(hours=1))
        p1 = self._autorizado("deficiencia")
        self.assertEqual(self._ordem(n1, em_atendimento, p1), [em_atendimento.pk, p1.pk, n1.pk])

    def _acupuntura_e_normal(self):
        from cadastros.models import Procedimento

        acupuntura = Procedimento.objects.create(nome="Acupuntura", permite_repeticao_semanal=True)
        n1 = self._autorizado()
        acu = self._autorizado()
        acu.itens.update(procedimento=acupuntura)
        return n1, acu

    def test_acupuntura_so_fura_fila_do_profissional_marcado(self):
        """Pedido da clínica (06/11/2026): só para um profissional específico."""
        n1, acu = self._acupuntura_e_normal()
        self.assertEqual(self._ordem(n1, acu), [n1.pk, acu.pk])

    def test_acupuntura_vai_pro_comeco_da_fila(self):
        """Pedido da clínica (06/11/2026): o profissional aplica as agulhas e atende os outros
        enquanto o paciente fica na sessão — acupuntura passa na frente de quem aguarda, até
        de prioritário, mas não de quem já está em atendimento."""
        from cadastros.models import Procedimento

        self.profissional.acupuntura_primeiro_na_fila = True
        self.profissional.save()
        acupuntura = Procedimento.objects.create(nome="Acupuntura", permite_repeticao_semanal=True)
        em_atendimento = self._autorizado(chamado_em=self._base + timedelta(hours=1))
        n1 = self._autorizado()
        p1 = self._autorizado("idoso_60")
        acu1, acu2 = self._autorizado(), self._autorizado()
        for agendamento in (acu1, acu2):
            agendamento.itens.update(procedimento=acupuntura)
        self.assertEqual(
            self._ordem(em_atendimento, n1, p1, acu1, acu2),
            [em_atendimento.pk, acu1.pk, acu2.pk, p1.pk, n1.pk],
        )


class MarcarPrioridadeTests(AgendaTestBase):
    """A recepção marca a prioridade ao encaminhar/autorizar e pode corrigir antes da chamada."""

    def setUp(self):
        super().setUp()
        self.usuario = Usuario.objects.create_user(username="recepcao_p", password="senha-teste-123", exigir_troca_senha=False)
        for codename in ("agenda_editar", "fila_autorizar_recepcao"):
            self.usuario.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(self.usuario)

    def test_encaminhar_para_pagamento_grava_prioridade(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.CONFIRMADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)
        self.client.post(
            reverse("agenda:agendamento_mudar_status", args=[agendamento.pk, "PRE_AUTORIZADO"]),
            {"motivo_prioridade": "gestante"},
        )
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.status, Agendamento.Status.PRE_AUTORIZADO)
        self.assertEqual(agendamento.motivo_prioridade, "gestante")

    def test_motivo_invalido_vira_normal(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.CONFIRMADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)
        self.client.post(
            reverse("agenda:agendamento_mudar_status", args=[agendamento.pk, "PRE_AUTORIZADO"]),
            {"motivo_prioridade": "qualquer"},
        )
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.motivo_prioridade, "")

    def test_autorizar_convenio_grava_prioridade(self):
        agendamento = self._criar_agendamento(
            forma_atendimento=Agendamento.FormaAtendimento.CONVENIO,
            convenio=self.convenio,
            status=Agendamento.Status.CONFIRMADO,
        )
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)
        self.client.post(
            reverse("agenda:agendamento_autorizar", args=[agendamento.pk]),
            {"numero_guia": "", "observacao": "", "motivo_prioridade": "idoso_80"},
        )
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.status, Agendamento.Status.AUTORIZADO)
        self.assertEqual(agendamento.motivo_prioridade, "idoso_80")

    def test_corrigir_prioridade_antes_da_chamada(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.AUTORIZADO)
        self.client.post(reverse("agenda:agendamento_prioridade", args=[agendamento.pk]), {"motivo_prioridade": "autismo"})
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.motivo_prioridade, "autismo")
        self.client.post(reverse("agenda:agendamento_prioridade", args=[agendamento.pk]), {"motivo_prioridade": ""})
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.motivo_prioridade, "")

    def test_nao_corrige_depois_de_chamado(self):
        agendamento = self._criar_agendamento(status=Agendamento.Status.AUTORIZADO)
        agendamento.chamado_em = timezone.now()
        agendamento.save()
        self.client.post(reverse("agenda:agendamento_prioridade", args=[agendamento.pk]), {"motivo_prioridade": "autismo"})
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.motivo_prioridade, "")
    def test_agenda_do_dia_mostra_seletor_de_prioridade(self):
        self.usuario.user_permissions.add(Permission.objects.get(codename="agenda_ver_dia"))
        agendamento = self._criar_agendamento(status=Agendamento.Status.CONFIRMADO)
        AgendamentoItem.objects.create(agendamento=agendamento, procedimento=self.consulta, quantidade=1)
        resposta = self.client.get(reverse("agenda:agenda_dia"), {"data": "2026-07-20"})
        self.assertContains(resposta, 'name="motivo_prioridade"')
        self.assertContains(resposta, "P · Idoso + 80")

class FilaDosProfissionaisTests(FilaDeChamadaPrioritarioTests):
    """Tela da recepção "Fila dos profissionais" (pedido da clínica, 10/11/2026): quantos estão
    na frente de cada paciente que já passou pela recepção — mesma ordem da Minha agenda."""

    def _fila(self):
        from .views import filas_dos_profissionais

        return filas_dos_profissionais(date(2026, 7, 20))

    def test_conta_quantos_estao_na_frente_na_ordem_da_minha_agenda(self):
        em_atendimento = self._autorizado(chamado_em=self._base + timedelta(hours=1))
        n1, n2 = self._autorizado(), self._autorizado()
        p1 = self._autorizado("idoso_60")
        fila = self._fila()
        self.assertEqual(len(fila), 1)
        self.assertEqual([a.pk for a in fila[0]["em_atendimento"]], [em_atendimento.pk])
        # Último chamado foi normal → vez do prioritário: P1, N1, N2.
        self.assertEqual([(a.pk, a.na_frente) for a in fila[0]["aguardando"]], [(p1.pk, 0), (n1.pk, 1), (n2.pk, 2)])

    def test_nao_autorizado_nao_entra(self):
        self._criar_agendamento(status=Agendamento.Status.AGENDADO)
        self.assertEqual(self._fila(), [])

    def test_tela_exige_permissao_da_recepcao(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        n1 = self._autorizado()
        Agendamento.objects.filter(pk=n1.pk).update(data_hora_inicio=timezone.now())
        usuario = get_user_model().objects.create_user(username="recep_fila", password="x", exigir_troca_senha=False)
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(reverse("agenda:fila_profissionais")).status_code, 403)

        usuario.user_permissions.add(Permission.objects.get(codename="fila_autorizar_recepcao"))
        tela = self.client.get(reverse("agenda:fila_profissionais"))
        self.assertContains(tela, "Paciente Teste")
        self.assertContains(tela, "É o próximo")
