"""
Testes da tela de "Datas específicas / exceções" de um profissional (ver
cadastros.views.profissional_excecoes): repetir uma data nos próximos meses,
editar uma data já cadastrada e remanejar uma data (com os agendamentos dos
pacientes) para outra.
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from agenda.models import Agendamento, AgendamentoItem

from .forms import LimiteDiarioProcedimentoForm, TabelaPrecoForm
from .models import Convenio, ExcecaoAtendimento, Paciente, Procedimento, Profissional, TabelaPrecoConvenio
from .views import _data_repetida

Usuario = get_user_model()


class DataRepetidaTests(TestCase):
    def test_mesmo_dia_do_mes(self):
        self.assertEqual(_data_repetida(date(2026, 10, 15), "dia_mes", 1), date(2026, 11, 15))
        self.assertEqual(_data_repetida(date(2026, 12, 15), "dia_mes", 2), date(2027, 2, 15))

    def test_dia_que_nao_existe_no_mes(self):
        self.assertIsNone(_data_repetida(date(2026, 10, 30), "dia_mes", 4))  # 30/02 não existe

    def test_mesma_ocorrencia_do_dia_da_semana(self):
        # 13/10/2026 = 2ª terça-feira de outubro → 2ª terça de novembro = 10/11/2026
        self.assertEqual(_data_repetida(date(2026, 10, 13), "semana_mes", 1), date(2026, 11, 10))

    def test_quinta_ocorrencia_inexistente(self):
        # 29/10/2026 = 5ª quinta-feira de outubro; novembro/2026 só tem 4 quintas
        self.assertIsNone(_data_repetida(date(2026, 10, 29), "semana_mes", 1))

    def test_ultima_ocorrencia_do_dia_da_semana(self):
        # 30/10/2026 = última sexta de outubro → última sexta de novembro = 27/11/2026
        self.assertEqual(_data_repetida(date(2026, 10, 30), "ultima_semana_mes", 1), date(2026, 11, 27))


class DatasEspecificasTestBase(TestCase):
    def setUp(self):
        self.profissional = Profissional.objects.create(
            nome="Dra. Datas", registro_profissional="CRM-DATAS", percentual_desconto=Decimal("30.00"),
            especialidade="medico_clinico_geral",
        )
        self.paciente = Paciente.objects.create(nome_completo="Paciente Remanejo", telefone="(11) 99999-0000")
        self.usuario = Usuario.objects.create_user(
            username="gestor_datas", password="senha-teste-123", exigir_troca_senha=False
        )
        for codename in ("profissional_gerenciar", "agenda_editar"):
            self.usuario.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(self.usuario)
        self.url = reverse("cadastros:profissional_excecoes", args=[self.profissional.pk])

    def _proxima_util(self, dias):
        data = timezone.localdate() + timedelta(days=dias)
        while data.weekday() >= 5:
            data += timedelta(days=1)
        return data

    def _cadastrar(self, data, inicio=time(8, 0), fim=time(12, 0)):
        return ExcecaoAtendimento.objects.create(
            profissional=self.profissional, data=data, turno="MANHA", horario_inicio=inicio, horario_fim=fim
        )


class RepetirEEditarDataTests(DatasEspecificasTestBase):
    def test_repetir_cria_datas_independentes_e_pula_as_ja_cadastradas(self):
        base = date(2030, 10, 15)  # terça-feira
        ja_existente = self._cadastrar(date(2030, 11, 15), time(14, 0), time(18, 0))  # sexta
        resposta = self.client.post(
            self.url,
            {
                "data": "2030-10-15", "ativo_MANHA": "on", "inicio_MANHA": "08:00", "fim_MANHA": "12:00",
                "repetir_modo": "dia_mes", "repetir_meses": "3",
            },
        )
        self.assertEqual(resposta.status_code, 302)
        datas = set(ExcecaoAtendimento.objects.filter(profissional=self.profissional).values_list("data", flat=True))
        # 15/11 já existia (não é sobrescrita); 15/12/2030 é domingo (pulada); 15/01/2031 = quarta
        self.assertEqual(datas, {base, date(2030, 11, 15), date(2031, 1, 15)})
        ja_existente.refresh_from_db()
        self.assertEqual(ja_existente.horario_inicio, time(14, 0))

    def test_editar_substitui_so_a_data_escolhida(self):
        data = self._proxima_util(10)
        outra = self._proxima_util(40)
        self._cadastrar(data)
        self._cadastrar(outra)

        resposta = self.client.get(self.url, {"editar": data.isoformat()})
        self.assertEqual(resposta.context["data_editando"], data)
        manha = next(c for c in resposta.context["grade_nova"] if c["chave"] == "MANHA")
        self.assertTrue(manha["marcado"])

        self.client.post(
            self.url, {"data": data.isoformat(), "ativo_TARDE": "on", "inicio_TARDE": "14:00", "fim_TARDE": "17:00"}
        )
        self.assertEqual(
            list(ExcecaoAtendimento.objects.filter(data=data).values_list("turno", flat=True)), ["TARDE"]
        )
        self.assertTrue(ExcecaoAtendimento.objects.filter(data=outra, turno="MANHA").exists())


class RemanejarDataTests(DatasEspecificasTestBase):
    def setUp(self):
        super().setUp()
        self.data_antiga = self._proxima_util(10)
        self.data_nova = self._proxima_util(20)
        self._cadastrar(self.data_antiga)
        self.agendamento = Agendamento.objects.create(
            paciente=self.paciente,
            profissional=self.profissional,
            data_hora_inicio=timezone.make_aware(datetime.combine(self.data_antiga, time(9, 0))),
            data_hora_fim=timezone.make_aware(datetime.combine(self.data_antiga, time(9, 30))),
            status=Agendamento.Status.CONFIRMADO,
        )
        self.url_remanejar = reverse(
            "cadastros:profissional_excecao_remanejar", args=[self.profissional.pk, self.data_antiga.isoformat()]
        )

    def test_move_horario_e_pacientes_para_a_nova_data(self):
        resposta = self.client.post(self.url_remanejar, {"data_nova": self.data_nova.isoformat()})
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Paciente Remanejo")
        self.assertFalse(ExcecaoAtendimento.objects.filter(data=self.data_antiga).exists())
        self.assertTrue(ExcecaoAtendimento.objects.filter(data=self.data_nova, turno="MANHA").exists())

        self.agendamento.refresh_from_db()
        inicio = timezone.localtime(self.agendamento.data_hora_inicio)
        self.assertEqual(inicio.date(), self.data_nova)
        self.assertEqual(inicio.time(), time(9, 0))
        self.assertEqual(self.agendamento.status, Agendamento.Status.AGENDADO)
        self.assertIn("Remanejado de", self.agendamento.observacoes)

    def test_conflito_na_nova_data_nao_altera_nada(self):
        # Só sessão conflita (consulta/exame não têm duração — ver Agendamento.eh_sessao).
        sessao = Procedimento.objects.create(nome="Sessão Remanejo", permite_repeticao_semanal=True)
        AgendamentoItem.objects.create(agendamento=self.agendamento, procedimento=sessao)
        outro_paciente = Paciente.objects.create(nome_completo="Já Marcado")
        ja_marcado = Agendamento.objects.create(
            paciente=outro_paciente,
            profissional=self.profissional,
            data_hora_inicio=timezone.make_aware(datetime.combine(self.data_nova, time(9, 0))),
            data_hora_fim=timezone.make_aware(datetime.combine(self.data_nova, time(9, 30))),
        )
        AgendamentoItem.objects.create(agendamento=ja_marcado, procedimento=sessao)
        self.client.post(self.url_remanejar, {"data_nova": self.data_nova.isoformat()})
        self.assertTrue(ExcecaoAtendimento.objects.filter(data=self.data_antiga).exists())
        self.assertFalse(ExcecaoAtendimento.objects.filter(data=self.data_nova).exists())
        self.agendamento.refresh_from_db()
        self.assertEqual(timezone.localtime(self.agendamento.data_hora_inicio).date(), self.data_antiga)
        self.assertEqual(self.agendamento.status, Agendamento.Status.CONFIRMADO)

    def test_nao_remaneja_para_data_ja_cadastrada(self):
        self._cadastrar(self.data_nova)
        self.client.post(self.url_remanejar, {"data_nova": self.data_nova.isoformat()})
        self.agendamento.refresh_from_db()
        self.assertEqual(timezone.localtime(self.agendamento.data_hora_inicio).date(), self.data_antiga)

    def test_exige_permissao_de_editar_agenda(self):
        self.usuario.user_permissions.remove(Permission.objects.get(codename="agenda_editar"))
        resposta = self.client.post(self.url_remanejar, {"data_nova": self.data_nova.isoformat()})
        self.assertEqual(resposta.status_code, 302)
        self.agendamento.refresh_from_db()
        self.assertEqual(timezone.localtime(self.agendamento.data_hora_inicio).date(), self.data_antiga)


class ProcedimentoListaBuscaTests(TestCase):
    """Pedido da clínica (27/10/2026): pesquisar na lista de procedimentos por nome ou código."""

    def setUp(self):
        Procedimento.objects.create(nome="Ultrassom de Abdome", codigo="40901122")
        Procedimento.objects.create(nome="Consulta Cardiologia", codigo="10101012")
        usuario = Usuario.objects.create_user(username="gestor_busca", password="x", exigir_troca_senha=False)
        usuario.user_permissions.add(Permission.objects.get(codename="procedimento_gerenciar"))
        self.client.force_login(usuario)

    def _nomes(self, termo):
        resposta = self.client.get(reverse("cadastros:procedimento_lista"), {"q": termo})
        return [p.nome for p in resposta.context["pagina"]]

    def test_busca_por_nome_e_codigo(self):
        self.assertEqual(self._nomes("ultras"), ["Ultrassom de Abdome"])
        self.assertEqual(self._nomes("1010"), ["Consulta Cardiologia"])
        self.assertEqual(len(self._nomes("")), 2)


class ProcedimentosVinculadosAoProfissionalTests(TestCase):
    """
    Limite diário, Tabela de preços (tela e importação por planilha): só aceitam
    procedimento vinculado ao profissional ("profissionais habilitados"), sem
    fallback pro cadastro inteiro de procedimentos.
    """

    def setUp(self):
        self.profissional = Profissional.objects.create(nome="Dra. Vinculo", registro_profissional="CRM-VINC", percentual_desconto=Decimal("30.00"))
        self.vinculado = Procedimento.objects.create(nome="Consulta Vinculada")
        self.vinculado.profissionais_habilitados.add(self.profissional)
        self.outro = Procedimento.objects.create(nome="Exame De Outro")
        self.convenio = Convenio.objects.create(nome="Convênio Vinculo")
        self.usuario = Usuario.objects.create_user(
            username="gestor_vinculo", password="senha-teste-123", exigir_troca_senha=False
        )
        self.usuario.user_permissions.add(Permission.objects.get(codename="convenio_gerenciar"))
        self.client.force_login(self.usuario)

    def test_limite_diario_lista_so_procedimentos_do_profissional(self):
        self.assertFalse(LimiteDiarioProcedimentoForm().fields["procedimento"].queryset.exists())
        form = LimiteDiarioProcedimentoForm(data={"profissional": self.profissional.pk})
        self.assertEqual(list(form.fields["procedimento"].queryset), [self.vinculado])

    def test_limite_diario_recusa_procedimento_nao_vinculado(self):
        form = LimiteDiarioProcedimentoForm(
            data={"profissional": self.profissional.pk, "procedimento": self.outro.pk, "limite_diario": 5}
        )
        self.assertIn("procedimento", form.errors)

    def test_endpoint_sem_vinculo_devolve_lista_vazia(self):
        sem_vinculo = Profissional.objects.create(nome="Dr. Sem Vinculo", registro_profissional="CRM-SV", percentual_desconto=Decimal("30.00"))
        resposta = self.client.get(reverse("cadastros:procedimentos_do_profissional", args=[sem_vinculo.pk]))
        self.assertEqual(resposta.json()["procedimentos"], [])

    def test_tabela_preco_recusa_procedimento_nao_vinculado(self):
        dados = {"procedimento": self.outro.pk, "profissional": self.profissional.pk, "valor": "50.00"}
        self.assertIn("procedimento", TabelaPrecoForm(data=dados, convenio=self.convenio).errors)
        dados["profissional"] = ""
        self.assertTrue(TabelaPrecoForm(data=dados, convenio=self.convenio).is_valid())

    def test_importacao_ignora_linha_de_procedimento_nao_vinculado(self):
        planilha = (
            "Procedimento;Código;Profissional;Valor\n"
            "Consulta Vinculada;;Dra. Vinculo;100\n"
            "Exame De Outro;;Dra. Vinculo;80\n"
            "Procedimento Novo;;Dra. Vinculo;70\n"
            "Exame De Outro;;;60\n"
        ).encode("utf-8")
        arquivo = SimpleUploadedFile("precos.csv", planilha, content_type="text/csv")
        self.client.post(reverse("cadastros:convenio_importar_precos", args=[self.convenio.pk]), {"arquivo": arquivo})

        precos = set(TabelaPrecoConvenio.objects.values_list("procedimento__nome", "profissional__nome"))
        self.assertEqual(precos, {("Consulta Vinculada", "Dra. Vinculo"), ("Exame De Outro", None)})
        self.assertFalse(Procedimento.objects.filter(nome="Procedimento Novo").exists())


class PacientesImportadosVisibilidadeEUnificacaoTests(TestCase):
    """Pedido da clínica (13/10/2026): da importação do sistema antigo, a recepção só vê
    quem tem dado além do nome (CPF, nascimento ou telefone); duplicados (mesmo nome +
    nascimento) são unificados numa ficha só, com todo o prontuário — e o profissional
    continua vendo o prontuário de todos."""

    def setUp(self):
        from prontuario.models import EntradaProntuario

        self.EntradaProntuario = EntradaProntuario
        nasc = date(1980, 5, 1)
        self.so_nome = Paciente.objects.create(nome_completo="Sem Dados Legado", origem_legado_id="1")
        self.com_tel = Paciente.objects.create(nome_completo="Com Telefone", origem_legado_id="2", telefone="(91)99999-0000")
        self.manual = Paciente.objects.create(nome_completo="Cadastro Manual Novo")
        self.principal = Paciente.objects.create(nome_completo="ANDREA NEVES", origem_legado_id="3", data_nascimento=nasc)
        self.copia = Paciente.objects.create(
            nome_completo="Andréa  Neves", origem_legado_id="4", data_nascimento=nasc, telefone="(91)98888-1111", cpf="52998224725"
        )
        self.homonimo = Paciente.objects.create(nome_completo="Andrea Neves", origem_legado_id="5", data_nascimento=date(1990, 1, 1))
        for paciente in (self.principal, self.principal, self.copia, self.so_nome):
            EntradaProntuario.objects.create(paciente=paciente, especialidade="medico_clinico_geral")

    def _unificar(self, confirmar=True):
        import os
        import tempfile

        from django.core.management import call_command

        caminho = os.path.join(tempfile.mkdtemp(), "relatorio.csv")
        args = ["--relatorio", caminho] + (["--confirmar"] if confirmar else [])
        call_command("unificar_pacientes_duplicados", *args, stdout=open(os.devnull, "w"))

    def test_regra_da_recepcao(self):
        visiveis = set(Paciente.objects.filter(Paciente.filtro_visivel_recepcao()).values_list("pk", flat=True))
        self.assertNotIn(self.so_nome.pk, visiveis)
        self.assertIn(self.com_tel.pk, visiveis)
        self.assertIn(self.manual.pk, visiveis)  # cadastro feito aqui sempre aparece
        self.assertTrue(self.so_nome.oculto_recepcao)
        self.assertFalse(self.manual.oculto_recepcao)

    def test_simulacao_nao_grava(self):
        self._unificar(confirmar=False)
        self.copia.refresh_from_db()
        self.assertIsNone(self.copia.unificado_em_id)
        self.assertEqual(self.EntradaProntuario.objects.filter(paciente=self.copia).count(), 1)

    def test_unifica_move_prontuario_e_completa_dados(self):
        self._unificar()
        self.copia.refresh_from_db()
        self.principal.refresh_from_db()
        self.homonimo.refresh_from_db()
        # A principal é a com mais histórico (2 registros x 1).
        self.assertEqual(self.copia.unificado_em, self.principal)
        self.assertEqual(self.EntradaProntuario.objects.filter(paciente=self.principal).count(), 3)
        self.assertEqual(self.EntradaProntuario.objects.filter(paciente=self.copia).count(), 0)
        self.assertEqual(self.principal.telefone, "(91)98888-1111")
        self.assertEqual(self.principal.cpf, "52998224725")
        self.assertIsNone(self.copia.cpf)
        self.assertEqual(self.principal.nome_completo, "ANDREA NEVES")  # nunca sobrescreve
        self.assertIsNone(self.homonimo.unificado_em_id)  # outro nascimento: outra pessoa

    def test_reexecutar_nao_faz_nada(self):
        self._unificar()
        self._unificar()
        self.assertEqual(Paciente.objects.filter(unificado_em__isnull=False).count(), 1)

    def _usuario(self, *codenames, profissional=None):
        usuario = get_user_model().objects.create_user(
            username="u" + "_".join(codenames), password="senha-teste-123", exigir_troca_senha=False
        )
        for codename in codenames:
            usuario.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(usuario)
        return usuario

    def test_lista_de_pacientes_esconde_so_nome_e_copias(self):
        self._unificar()
        self._usuario("paciente_cadastrar")
        resposta = self.client.get(reverse("cadastros:paciente_lista"))
        nomes = {p.nome_completo for p in resposta.context["pagina"]}
        self.assertNotIn("Sem Dados Legado", nomes)
        self.assertNotIn("Andréa  Neves", nomes)
        self.assertIn("ANDREA NEVES", nomes)
        self.assertEqual(resposta.context["total_ocultos"], 1)
        resposta = self.client.get(reverse("cadastros:paciente_lista"), {"ocultos": "1"})
        self.assertEqual([p.nome_completo for p in resposta.context["pagina"]], ["Sem Dados Legado"])

    def test_busca_do_agendamento_esconde_so_nome_e_copias(self):
        self._unificar()
        self._usuario("agenda_criar")
        nomes = [p["nome"] for p in self.client.get(reverse("agenda:buscar_pacientes"), {"q": "neves"}).json()["pacientes"]]
        self.assertEqual(sorted(nomes), ["ANDREA NEVES", "Andrea Neves"])
        nomes = [p["nome"] for p in self.client.get(reverse("agenda:buscar_pacientes"), {"q": "sem dados"}).json()["pacientes"]]
        self.assertEqual(nomes, [])


class ConvenioDetalhePaginadoTests(TestCase):
    """Tabela de preços do convênio paginada de 50 em 50, com busca (pedido da clínica,
    03/11/2026: convênio com 648 procedimentos carregava tudo numa página só)."""

    def setUp(self):
        self.convenio = Convenio.objects.create(nome="Convênio Grande")
        TabelaPrecoConvenio.objects.bulk_create([
            TabelaPrecoConvenio(
                convenio=self.convenio,
                procedimento=Procedimento.objects.create(nome=f"Procedimento {i:03d}", codigo=f"C{i:03d}"),
                valor=Decimal("10.00"),
            )
            for i in range(120)
        ])
        usuario = Usuario.objects.create_user(username="conv_pag", password="senha-teste-123", exigir_troca_senha=False)
        usuario.user_permissions.add(Permission.objects.get(codename="convenio_gerenciar"))
        self.client.force_login(usuario)
        self.url = reverse("cadastros:convenio_detalhe", args=[self.convenio.pk])

    def test_mostra_50_por_pagina(self):
        resposta = self.client.get(self.url)
        self.assertEqual(len(resposta.context["pagina"]), 50)
        self.assertContains(resposta, "Página 1 de 3 (120 registros)")
        self.assertEqual(len(self.client.get(self.url, {"pagina": 3}).context["pagina"]), 20)

    def test_busca_por_nome_ou_codigo(self):
        self.assertEqual(len(self.client.get(self.url, {"q": "Procedimento 01"}).context["pagina"]), 10)
        self.assertEqual(len(self.client.get(self.url, {"q": "C119"}).context["pagina"]), 1)


class PacienteFormDataTests(TestCase):
    """Relato da clínica (06/11/2026): ao editar o cadastro (ex.: só incluir o convênio), a
    data de nascimento sumia — o <input type="date"> recebia dd/mm/aaaa e ficava vazio."""

    def test_datas_vem_preenchidas_no_formato_do_campo_de_data(self):
        from .forms import PacienteForm

        paciente = Paciente(
            nome_completo="Fulano", data_nascimento=date(1980, 3, 25), validade_carteirinha=date(2027, 1, 31)
        )
        html = str(PacienteForm(instance=paciente))
        self.assertIn('value="1980-03-25"', html)
        self.assertIn('value="2027-01-31"', html)