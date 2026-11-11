"""
Testes do prontuário importado do sistema antigo: só para consulta, ninguém edita.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from cadastros.models import Paciente, Profissional

from .models import EntradaProntuario

Usuario = get_user_model()


class EntradaNovaRedirecionamentoTests(TestCase):
    """Depois de salvar um registro a partir do atendimento, volta pra tela de onde o
    profissional veio — não pro próprio formulário vazio (parecia que não tinha salvado)."""

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        from agenda.models import Agendamento

        self.paciente = Paciente.objects.create(nome_completo="Paciente Teste")
        self.profissional = Profissional.objects.create(
            nome="Dra. Teste", registro_profissional="CRM-1", especialidade="medico_clinico_geral",
            percentual_desconto=0
        )
        usuario = Usuario.objects.create_user(
            username="dra", password="senha-forte-123", profissional_id=self.profissional.pk,
            exigir_troca_senha=False
        )
        self.client.force_login(usuario)
        inicio = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        while inicio.weekday() >= 5:
            inicio -= timedelta(days=1)
        self.agendamento = Agendamento.objects.create(
            paciente=self.paciente, profissional=self.profissional, data_hora_inicio=inicio,
            data_hora_fim=inicio + timedelta(minutes=30), status=Agendamento.Status.REALIZADO,
        )
        self.url = reverse("prontuario:entrada_nova", args=[self.agendamento.pk])

    def test_volta_para_a_tela_de_origem_depois_de_salvar(self):
        origem = reverse("agenda:agenda_propria")
        resposta = self.client.get(self.url, HTTP_REFERER="http://testserver" + origem)
        self.assertContains(resposta, 'name="voltar" value="http://testserver' + origem + '"')

        # No POST o Referer é o próprio formulário — é o campo oculto que guarda a origem.
        resposta = self.client.post(
            self.url,
            {"queixa_principal": "Dor", "voltar": "http://testserver" + origem},
            HTTP_REFERER="http://testserver" + self.url,
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta["Location"], "http://testserver" + origem)
        self.assertEqual(EntradaProntuario.objects.filter(paciente=self.paciente).count(), 1)

    def test_sem_origem_vai_para_o_prontuario_do_paciente(self):
        resposta = self.client.post(self.url, {"queixa_principal": "Dor"}, HTTP_REFERER="http://testserver" + self.url)
        self.assertRedirects(
            resposta, reverse("prontuario:prontuario_paciente", args=[self.paciente.pk]), fetch_redirect_response=False
        )

    def test_ignora_destino_de_outro_site(self):
        resposta = self.client.post(self.url, {"queixa_principal": "Dor", "voltar": "https://site-malicioso.example/"})
        self.assertRedirects(
            resposta, reverse("prontuario:prontuario_paciente", args=[self.paciente.pk]), fetch_redirect_response=False
        )


class ProntuarioLegadoSomenteLeituraTests(TestCase):
    def setUp(self):
        self.paciente = Paciente.objects.create(nome_completo="Paciente Teste")
        self.profissional = Profissional.objects.create(
            nome="Dra. Teste", registro_profissional="CRM-1", especialidade="medico_clinico_geral",
            percentual_desconto=0
        )
        self.usuario = Usuario.objects.create_user(
            username="dra", password="senha-forte-123", profissional_id=self.profissional.pk,
            exigir_troca_senha=False
        )
        self.client.force_login(self.usuario)

    def _entrada(self, **extra):
        return EntradaProntuario.objects.create(
            paciente=self.paciente,
            especialidade="Medicina",
            queixa_principal="Dor de cabeça",
            **extra,
        )

    def test_entrada_legado_sem_profissional_local_mostra_nome_antigo(self):
        entrada = self._entrada(origem_legado_id="10", profissional_legado_nome="Dr. Antigo")
        self.assertTrue(entrada.somente_leitura)
        self.assertEqual(entrada.profissional_exibicao, "Dr. Antigo")
        resposta = self.client.get(reverse("prontuario:prontuario_paciente", args=[self.paciente.pk]))
        self.assertContains(resposta, "Dr. Antigo")
        self.assertContains(resposta, "somente consulta")
        self.assertNotContains(resposta, reverse("prontuario:entrada_editar", args=[entrada.pk]))

    def test_nem_o_autor_edita_entrada_legado(self):
        entrada = self._entrada(origem_legado_id="11", profissional=self.profissional)
        url = reverse("prontuario:entrada_editar", args=[entrada.pk])
        resposta = self.client.post(url, {"queixa_principal": "Alterada"})
        self.assertEqual(resposta.status_code, 302)
        entrada.refresh_from_db()
        self.assertEqual(entrada.queixa_principal, "Dor de cabeça")

    def test_entrada_criada_no_sistema_continua_editavel_pelo_autor(self):
        entrada = self._entrada(profissional=self.profissional)
        self.assertFalse(entrada.somente_leitura)
        resposta = self.client.get(reverse("prontuario:prontuario_paciente", args=[self.paciente.pk]))
        self.assertContains(resposta, reverse("prontuario:entrada_editar", args=[entrada.pk]))
        self.client.post(
            reverse("prontuario:entrada_editar", args=[entrada.pk]), {"queixa_principal": "Alterada"}
        )
        entrada.refresh_from_db()
        self.assertEqual(entrada.queixa_principal, "Alterada")


class PacientesOcultosNaRecepcaoTests(TestCase):
    """Cadastro do sistema antigo só com o nome some da recepção, mas o profissional
    continua achando e vendo o prontuário; cópia unificada redireciona pra ficha principal."""

    def setUp(self):
        self.profissional = Profissional.objects.create(
            nome="Dra. Teste", registro_profissional="CRM-1", especialidade="medico_clinico_geral", percentual_desconto=0
        )
        usuario = Usuario.objects.create_user(
            username="dra_ocultos", password="senha-forte-123", profissional_id=self.profissional.pk,
            exigir_troca_senha=False
        )
        self.client.force_login(usuario)
        self.so_nome = Paciente.objects.create(nome_completo="Somente Nome Legado", origem_legado_id="1")
        self.principal = Paciente.objects.create(nome_completo="Ficha Principal", origem_legado_id="2")
        self.copia = Paciente.objects.create(nome_completo="Ficha Copia", origem_legado_id="3", unificado_em=self.principal)

    def test_profissional_acha_quem_so_tem_nome(self):
        resposta = self.client.get(reverse("prontuario:prontuario_lista"), {"q": "somente"})
        self.assertEqual([p.pk for p in resposta.context["pagina"]], [self.so_nome.pk])
        resposta = self.client.get(reverse("prontuario:prontuario_paciente", args=[self.so_nome.pk]))
        self.assertEqual(resposta.status_code, 200)

    def test_copia_unificada_some_da_lista_e_redireciona(self):
        resposta = self.client.get(reverse("prontuario:prontuario_lista"), {"q": "ficha"})
        self.assertEqual([p.pk for p in resposta.context["pagina"]], [self.principal.pk])
        resposta = self.client.get(reverse("prontuario:prontuario_paciente", args=[self.copia.pk]))
        self.assertRedirects(resposta, reverse("prontuario:prontuario_paciente", args=[self.principal.pk]))


class DadosDoPacienteNoProntuarioTests(TestCase):
    """Pedido da clínica (23/10/2026): idade (ou "pendente cadastro"), plano do cadastro e
    plano com que o atendimento foi autorizado no topo do prontuário."""

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        from agenda.models import Agendamento
        from cadastros.models import Convenio

        self.Agendamento = Agendamento
        self.profissional = Profissional.objects.create(
            nome="Dra. Teste", registro_profissional="CRM-1", especialidade="medico_clinico_geral", percentual_desconto=0
        )
        usuario = Usuario.objects.create_user(
            username="dra_dados", password="senha-forte-123", profissional_id=self.profissional.pk,
            exigir_troca_senha=False
        )
        self.client.force_login(usuario)
        self.unimed = Convenio.objects.create(nome="Unimed")
        hoje = timezone.localdate()
        self.paciente = Paciente.objects.create(
            nome_completo="Paciente Com Dados", data_nascimento=hoje.replace(year=hoje.year - 30) - timedelta(days=1),
            convenio=self.unimed,
        )
        self.inicio = timezone.now().replace(minute=0, second=0, microsecond=0)

    def _url(self, paciente):
        return reverse("prontuario:prontuario_paciente", args=[paciente.pk])

    def test_idade_e_plano_do_cadastro(self):
        resposta = self.client.get(self._url(self.paciente))
        self.assertContains(resposta, "30 anos")
        self.assertContains(resposta, "Unimed")
        self.assertContains(resposta, "nenhum atendimento autorizado")

    def test_sem_nascimento_mostra_pendente_cadastro(self):
        sem_dados = Paciente.objects.create(nome_completo="Sem Nascimento")
        resposta = self.client.get(self._url(sem_dados))
        self.assertContains(resposta, "(pendente cadastro)")
        self.assertContains(resposta, "Particular (sem convênio no cadastro)")

    def test_plano_autorizado_pode_diferir_do_cadastro(self):
        from datetime import timedelta

        self.Agendamento.objects.create(
            paciente=self.paciente, profissional=self.profissional, data_hora_inicio=self.inicio,
            data_hora_fim=self.inicio + timedelta(minutes=30), status=self.Agendamento.Status.AUTORIZADO,
            forma_atendimento=self.Agendamento.FormaAtendimento.PARTICULAR,
        )
        resposta = self.client.get(self._url(self.paciente))
        self.assertEqual(resposta.context["atendimento_autorizado"].plano_exibicao, "Particular")
        self.assertContains(resposta, "Unimed")  # o do cadastro continua aparecendo

    def test_formulario_do_atendimento_mostra_o_plano_dele(self):
        from datetime import timedelta

        agendamento = self.Agendamento.objects.create(
            paciente=self.paciente, profissional=self.profissional, data_hora_inicio=self.inicio,
            data_hora_fim=self.inicio + timedelta(minutes=30), status=self.Agendamento.Status.REALIZADO,
            forma_atendimento=self.Agendamento.FormaAtendimento.CONVENIO, convenio=self.unimed,
        )
        resposta = self.client.get(reverse("prontuario:entrada_nova", args=[agendamento.pk]))
        self.assertContains(resposta, "Convênio — Unimed")
        self.assertContains(resposta, "30 anos")

class FormularioSimplificadoMultiTests(TestCase):
    """Equipe multi (não médico) registra num campo único de evolução; médico mantém os temas."""

    def _logar(self, especialidade, registro):
        profissional = Profissional.objects.create(
            nome=f"Prof {registro}", registro_profissional=registro, especialidade=especialidade,
            percentual_desconto=0,
        )
        usuario = Usuario.objects.create_user(
            username=registro, password="senha-forte-123", profissional_id=profissional.pk,
            exigir_troca_senha=False,
        )
        self.client.force_login(usuario)
        return profissional

    def setUp(self):
        self.paciente = Paciente.objects.create(nome_completo="Paciente Multi")
        self.url = reverse("prontuario:entrada_nova_paciente", args=[self.paciente.pk])

    def test_multi_ve_so_o_campo_de_evolucao(self):
        self._logar("fisioterapeuta", "CREFITO-1")
        resposta = self.client.get(self.url)
        self.assertContains(resposta, 'name="evolucao"')
        self.assertNotContains(resposta, 'name="queixa_principal"')
        self.assertNotContains(resposta, 'name="conduta"')

        self.client.post(self.url, {"evolucao": "Sessão sem intercorrências."})
        entrada = EntradaProntuario.objects.get(paciente=self.paciente)
        self.assertEqual(entrada.evolucao, "Sessão sem intercorrências.")
        self.assertEqual(entrada.secoes_preenchidas, [("evolução / registro do atendimento", "Sessão sem intercorrências.")])

    def test_multi_pode_abrir_o_formulario_completo(self):
        self._logar("psicologo", "CRP-1")
        resposta = self.client.get(self.url + "?completo=1")
        self.assertContains(resposta, 'name="queixa_principal"')
        self.assertNotContains(resposta, 'name="evolucao"')
        self.client.post(self.url, {"completo": "1", "conduta": "Manter"})
        self.assertEqual(EntradaProntuario.objects.get(paciente=self.paciente).conduta, "Manter")

    def test_medico_continua_com_formulario_completo(self):
        self._logar("medico_clinico_geral", "CRM-9")
        resposta = self.client.get(self.url)
        self.assertContains(resposta, 'name="queixa_principal"')
        self.assertNotContains(resposta, 'name="evolucao"')

    def test_editar_registro_antigo_da_multi_mostra_campos_ja_preenchidos(self):
        profissional = self._logar("fisioterapeuta", "CREFITO-2")
        entrada = EntradaProntuario.objects.create(
            paciente=self.paciente, profissional=profissional, especialidade="Fisioterapeuta",
            queixa_principal="Texto", conduta="Texto",
        )
        resposta = self.client.get(reverse("prontuario:entrada_editar", args=[entrada.pk]))
        self.assertContains(resposta, 'name="evolucao"')
        self.assertContains(resposta, 'name="queixa_principal"')
        self.assertContains(resposta, 'name="conduta"')
        self.assertNotContains(resposta, 'name="historico"')


class VisibilidadeEntreMedicosTests(TestCase):
    """Registro médico é visível a todos (inclusive multi); o da multi (e o de saúde mental) só o autor vê."""

    def setUp(self):
        self.paciente = Paciente.objects.create(nome_completo="Paciente Visibilidade")
        self.medico_a = self._profissional("medico_clinico_geral", "CRM-A")
        self.medico_b = self._profissional("medico_cardiologista", "CRM-B")
        self.psiquiatra = self._profissional("medico_psiquiatra", "CRM-C")
        self.fisio_a = self._profissional("fisioterapeuta", "CREFITO-A")
        self.fisio_b = self._profissional("fisioterapeuta", "CREFITO-B")
        self.do_medico = self._entrada(self.medico_a, "Médico clínico geral")
        self.da_psiquiatria = self._entrada(self.psiquiatra, "Médico psiquiatra")
        self.do_fisio = self._entrada(self.fisio_a, "Fisioterapeuta")
        self.legado_sem_especialidade = EntradaProntuario.objects.create(
            paciente=self.paciente, especialidade="Não informada", origem_legado_id="99", queixa_principal="x",
        )

    def _profissional(self, especialidade, registro):
        profissional = Profissional.objects.create(
            nome=registro, registro_profissional=registro, especialidade=especialidade, percentual_desconto=0,
        )
        profissional.usuario = Usuario.objects.create_user(
            username=registro, password="senha-forte-123", profissional_id=profissional.pk, exigir_troca_senha=False,
        )
        return profissional

    def _entrada(self, profissional, especialidade):
        return EntradaProntuario.objects.create(
            paciente=self.paciente, profissional=profissional, especialidade=especialidade, evolucao="x",
        )

    def _visiveis(self, profissional):
        return set(EntradaProntuario.objects.filter(paciente=self.paciente).visiveis_para(profissional.usuario))

    def test_outro_medico_ve_registro_medico_mas_nao_o_da_multi_nem_psiquiatria(self):
        self.assertEqual(self._visiveis(self.medico_b), {self.do_medico, self.legado_sem_especialidade})

    def test_multi_ve_registros_medicos_e_o_proprio_mas_nao_o_do_colega(self):
        compartilhadas = {self.do_medico, self.legado_sem_especialidade}
        self.assertEqual(self._visiveis(self.fisio_a), compartilhadas | {self.do_fisio})
        self.assertEqual(self._visiveis(self.fisio_b), compartilhadas)

    def test_psiquiatra_ve_o_proprio_e_os_medicos(self):
        self.assertEqual(
            self._visiveis(self.psiquiatra), {self.do_medico, self.da_psiquiatria, self.legado_sem_especialidade}
        )

    def test_tela_do_prontuario_esconde_registro_do_colega_da_multi(self):
        self.client.force_login(self.fisio_b.usuario)
        resposta = self.client.get(reverse("prontuario:prontuario_paciente", args=[self.paciente.pk]))
        self.assertNotContains(resposta, reverse("prontuario:entrada_editar", args=[self.do_fisio.pk]))
        self.assertContains(resposta, "Médico clínico geral")
        self.assertNotContains(resposta, "Fisioterapeuta")


class DataDoAtendimentoTests(TestCase):
    """Pedido da clínica (2026-10-26): a equipe multi evolui dias depois e escolhe a data do
    atendimento ao abrir o prontuário; criado_em continua sendo quando foi digitado."""

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        self.timedelta = timedelta
        self.hoje = timezone.localdate()
        self.paciente = Paciente.objects.create(nome_completo="Paciente Teste")
        self.profissional = Profissional.objects.create(
            nome="Fisio Teste", registro_profissional="CREFITO-1", especialidade="fisioterapeuta",
            percentual_desconto=0
        )
        usuario = Usuario.objects.create_user(
            username="fisio", password="senha-forte-123", profissional_id=self.profissional.pk,
            exigir_troca_senha=False
        )
        self.client.force_login(usuario)
        self.url = reverse("prontuario:entrada_nova_paciente", args=[self.paciente.pk])

    def test_sem_data_informada_usa_hoje(self):
        self.client.post(self.url, {"evolucao": "Sessão ok."})
        self.assertEqual(EntradaProntuario.objects.get().data_atendimento, self.hoje)

    def test_evolucao_registrada_dias_depois_guarda_a_data_escolhida(self):
        tres_dias_atras = self.hoje - self.timedelta(days=3)
        self.client.post(self.url, {"evolucao": "Sessão ok.", "data_atendimento": tres_dias_atras.isoformat()})
        entrada = EntradaProntuario.objects.get()
        self.assertEqual(entrada.data_atendimento, tres_dias_atras)
        self.assertTrue(entrada.registrado_depois)

        resposta = self.client.get(reverse("prontuario:prontuario_paciente", args=[self.paciente.pk]))
        self.assertContains(resposta, tres_dias_atras.strftime("%d/%m/%Y"))
        self.assertContains(resposta, "registrado em")

    def test_nao_aceita_data_no_futuro(self):
        amanha = self.hoje + self.timedelta(days=1)
        resposta = self.client.post(self.url, {"evolucao": "Sessão ok.", "data_atendimento": amanha.isoformat()})
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(EntradaProntuario.objects.exists())

    def test_prontuario_ordena_pela_data_do_atendimento(self):
        self.client.post(self.url, {"evolucao": "Mais recente."})
        self.client.post(
            self.url, {"evolucao": "Atrasada.", "data_atendimento": (self.hoje - self.timedelta(days=5)).isoformat()}
        )
        self.assertEqual(
            list(EntradaProntuario.objects.filter(paciente=self.paciente).values_list("evolucao", flat=True)),
            ["Mais recente.", "Atrasada."],
        )


class ApagarRegistroTests(TestCase):
    """Pedido da clínica (26/10/2026): o autor pode apagar um registro feito errado."""

    def setUp(self):
        self.paciente = Paciente.objects.create(nome_completo="Paciente Apagar")
        self.autor = Profissional.objects.create(
            nome="Fisio Autora", registro_profissional="CREFITO-1", especialidade="fisioterapeuta", percentual_desconto=0
        )
        self.outro = Profissional.objects.create(
            nome="Fisio Outro", registro_profissional="CREFITO-2", especialidade="fisioterapeuta", percentual_desconto=0
        )
        self.usuario_autor = Usuario.objects.create_user(
            username="autora", password="x", profissional_id=self.autor.pk, exigir_troca_senha=False
        )
        self.usuario_outro = Usuario.objects.create_user(
            username="outro", password="x", profissional_id=self.outro.pk, exigir_troca_senha=False
        )
        self.entrada = EntradaProntuario.objects.create(
            paciente=self.paciente, profissional=self.autor, especialidade="Fisioterapeuta", evolucao="Sessão 1"
        )
        self.url = reverse("prontuario:entrada_excluir", args=[self.entrada.pk])

    def test_autor_confirma_e_apaga_com_log(self):
        from accounts.models import LogAuditoria

        self.client.force_login(self.usuario_autor)
        self.assertContains(self.client.get(self.url), "Apagar registro")
        resposta = self.client.post(self.url)
        self.assertRedirects(
            resposta, reverse("prontuario:prontuario_paciente", args=[self.paciente.pk]), fetch_redirect_response=False
        )
        self.assertFalse(EntradaProntuario.objects.filter(pk=self.entrada.pk).exists())
        log = LogAuditoria.objects.latest("pk")
        self.assertIn("Apagou registro do prontuário de 'Paciente Apagar'", log.acao)
        self.assertNotIn("Sessão 1", log.acao)

    def test_outro_profissional_nao_apaga(self):
        self.client.force_login(self.usuario_outro)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertTrue(EntradaProntuario.objects.filter(pk=self.entrada.pk).exists())

    def test_registro_do_sistema_antigo_nao_apaga(self):
        EntradaProntuario.objects.filter(pk=self.entrada.pk).update(origem_legado_id="99")
        self.client.force_login(self.usuario_autor)
        self.client.post(self.url)
        self.assertTrue(EntradaProntuario.objects.filter(pk=self.entrada.pk).exists())


class EvolucaoDeAtendimentoAnteriorTests(TestCase):
    """Pedido da clínica (26/10/2026): a Minha agenda lista os atendimentos dos últimos dias
    ainda sem registro, com link pra evoluir já com a data do atendimento."""

    def setUp(self):
        from datetime import timedelta

        from django.contrib.auth.models import Permission
        from django.utils import timezone

        from agenda.models import Agendamento

        self.paciente = Paciente.objects.create(nome_completo="Paciente Evolução Atrasada")
        self.profissional = Profissional.objects.create(
            nome="Fono Teste", registro_profissional="CRFA-1", especialidade="fonoaudiologo", percentual_desconto=0
        )
        usuario = Usuario.objects.create_user(
            username="fono", password="x", profissional_id=self.profissional.pk, exigir_troca_senha=False
        )
        usuario.user_permissions.add(
            *Permission.objects.filter(codename__in=["agenda_ver_propria", "atendimento_realizar"])
        )
        self.client.force_login(usuario)
        inicio = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0) - timedelta(days=3)
        self.agendamento = Agendamento.objects.create(
            paciente=self.paciente, profissional=self.profissional, data_hora_inicio=inicio,
            data_hora_fim=inicio + timedelta(minutes=30), status=Agendamento.Status.AUTORIZADO,
        )
        self.data_atendimento = timezone.localdate(inicio)

    def test_lista_atendimento_sem_registro_e_some_depois_de_evoluir(self):
        url_evoluir = reverse("prontuario:entrada_nova", args=[self.agendamento.pk])
        agenda = self.client.get(reverse("agenda:agenda_propria"))
        self.assertContains(agenda, "Atendimentos anteriores sem registro")
        self.assertContains(agenda, url_evoluir)

        formulario = self.client.get(url_evoluir)
        self.assertContains(formulario, f'value="{self.data_atendimento.isoformat()}"')
        self.client.post(url_evoluir, {"evolucao": "Evoluído depois", "data_atendimento": self.data_atendimento.isoformat()})
        entrada = EntradaProntuario.objects.get(agendamento=self.agendamento)
        self.assertEqual(entrada.data_atendimento, self.data_atendimento)

        self.assertNotContains(self.client.get(reverse("agenda:agenda_propria")), url_evoluir)

    def test_evolucao_pela_busca_do_prontuario_tira_da_lista_e_vincula(self):
        url_evoluir = reverse("prontuario:entrada_nova", args=[self.agendamento.pk])
        self.client.post(
            reverse("prontuario:entrada_nova_paciente", args=[self.paciente.pk]),
            {"evolucao": "Evoluído pela busca", "data_atendimento": self.data_atendimento.isoformat()},
        )
        self.assertEqual(EntradaProntuario.objects.get(paciente=self.paciente).agendamento, self.agendamento)
        self.assertNotContains(self.client.get(reverse("agenda:agenda_propria")), url_evoluir)

    def test_registro_antigo_sem_vinculo_na_mesma_data_tambem_conta(self):
        EntradaProntuario.objects.create(
            paciente=self.paciente, profissional=self.profissional, especialidade="Fonoaudiologia",
            evolucao="Feito antes do ajuste", data_atendimento=self.data_atendimento,
        )
        url_evoluir = reverse("prontuario:entrada_nova", args=[self.agendamento.pk])
        self.assertNotContains(self.client.get(reverse("agenda:agenda_propria")), url_evoluir)

    def test_registro_em_outra_data_nao_conta(self):
        from datetime import timedelta

        EntradaProntuario.objects.create(
            paciente=self.paciente, profissional=self.profissional, especialidade="Fonoaudiologia",
            evolucao="Outro dia", data_atendimento=self.data_atendimento - timedelta(days=1),
        )
        url_evoluir = reverse("prontuario:entrada_nova", args=[self.agendamento.pk])
        self.assertContains(self.client.get(reverse("agenda:agenda_propria")), url_evoluir)

    def test_medico_nao_tem_lista_de_pendentes(self):
        self.profissional.especialidade = "medico_clinico_geral"
        self.profissional.save()
        self.agendamento.especialidade = ""
        self.agendamento.save()
        self.assertNotContains(self.client.get(reverse("agenda:agenda_propria")), "Atendimentos anteriores sem registro")

    def test_so_equipe_multi_tem_lista_de_pendentes(self):
        """Clínica, 06/11/2026: a lista aparecia pra quem não é equipe multi (enfermeiro,
        "Outra" digitada etc.) e ficou gigante."""
        from agenda.models import Agendamento

        casos = [
            ("enfermeiro", "", False),
            ("cirurgiao_dentista", "", False),
            ("biomedico", "", False),
            ("outra", "Ultrassonografia", False),
            ("outra", "Fisioterapia pélvica", True),
            ("psicologo", "", True),
            ("nutricionista", "", True),
        ]
        for especialidade, outra, aparece in casos:
            with self.subTest(especialidade=especialidade, outra=outra):
                Agendamento.objects.filter(pk=self.agendamento.pk).update(
                    especialidade=especialidade, especialidade_outra=outra
                )
                resposta = self.client.get(reverse("agenda:agenda_propria"))
                if aparece:
                    self.assertContains(resposta, "Atendimentos anteriores sem registro")
                else:
                    self.assertNotContains(resposta, "Atendimentos anteriores sem registro")

    def test_medico_que_tambem_e_acupunturista_nao_tem_lista(self):
        """Caso da clínica (06/11/2026): médico com segunda especialidade "Acupuntura" (Outra)."""
        from agenda.models import Agendamento
        from cadastros.models import ProfissionalEspecialidade

        Profissional.objects.filter(pk=self.profissional.pk).update(especialidade="medico_clinico_geral")
        ProfissionalEspecialidade.objects.create(
            profissional=self.profissional, especialidade="outra", especialidade_outra="Acupuntura"
        )
        Agendamento.objects.filter(pk=self.agendamento.pk).update(especialidade="outra", especialidade_outra="Acupuntura")
        self.assertNotContains(self.client.get(reverse("agenda:agenda_propria")), "Atendimentos anteriores sem registro")

    def test_atendimento_sem_especialidade_usa_a_do_profissional(self):
        from agenda.models import Agendamento

        Agendamento.objects.filter(pk=self.agendamento.pk).update(especialidade="", especialidade_outra="")
        self.assertContains(self.client.get(reverse("agenda:agenda_propria")), "Atendimentos anteriores sem registro")
        Profissional.objects.filter(pk=self.profissional.pk).update(especialidade="enfermeiro")
        self.assertNotContains(self.client.get(reverse("agenda:agenda_propria")), "Atendimentos anteriores sem registro")


class EquipeMultiTests(TestCase):
    def test_palavras(self):
        from .models import eh_equipe_multi

        for texto in ("Fisioterapeuta", "Fonoaudiólogo", "Psicólogo clínico", "Neuropsicóloga", "Nutricionista",
                      "Terapeuta ocupacional", "Educador físico", "Psicopedagoga"):
            self.assertTrue(eh_equipe_multi(texto), texto)
        for texto in ("Médico psiquiatra", "Médico nutrólogo", "Enfermeiro", "Biomédico", "Farmacêutico",
                      "Cirurgião-dentista", "Assistente social", "", "Não informada", "Clínica médica"):
            self.assertFalse(eh_equipe_multi(texto), texto)


class BuscaSemAcentoNoProntuarioTests(TestCase):
    """Clínica, 28/10/2026: profissional não achava no Prontuário o paciente da lista de
    "Evoluir" — a busca agora ignora acento, aceita palavras fora de ordem e CPF sem pontos."""

    def setUp(self):
        profissional = Profissional.objects.create(
            nome="Fisio Busca", registro_profissional="CREFITO-1", especialidade="fisioterapeuta", percentual_desconto=0
        )
        usuario = Usuario.objects.create_user(
            username="fisio_busca", password="x", profissional_id=profissional.pk, exigir_troca_senha=False
        )
        self.client.force_login(usuario)
        self.paciente = Paciente.objects.create(nome_completo="JOÃO DA CONCEIÇÃO", cpf="123.456.789-09")
        Paciente.objects.create(nome_completo="Maria Souza")

    def _achados(self, termo):
        resposta = self.client.get(reverse("prontuario:prontuario_lista"), {"q": termo})
        return [p.pk for p in resposta.context["pagina"]]

    def test_ignora_acento_e_maiuscula(self):
        self.assertEqual(self._achados("joao conceicao"), [self.paciente.pk])

    def test_palavras_fora_de_ordem(self):
        self.assertEqual(self._achados("conceição joão"), [self.paciente.pk])

    def test_cpf_sem_pontuacao(self):
        self.assertEqual(self._achados("12345678909"), [self.paciente.pk])

    def test_nome_com_espaco_duplo_no_cadastro(self):
        # Causa real do relato: 369 nomes do sistema antigo têm espaço duplo; a tela mostra um
        # espaço só, então o nome copiado da agenda não batia no icontains antigo.
        paciente = Paciente.objects.create(nome_completo="Adma  Brena Pereira")
        self.assertEqual(self._achados("Adma Brena Pereira"), [paciente.pk])
