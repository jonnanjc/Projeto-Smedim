"""
Unificação de pacientes duplicados (pedido da clínica, 06/11/2026) — ver cadastros/unificacao.py.
Casos da clínica: (1) nome + nascimento iguais e telefone diferente; (2) nome + nascimento
iguais; (3) "Raissa" x "Raíssa" + nascimento igual — esses o comando em lote unifica sozinho;
(4) só o nome igual; (5) "Raissa" x "Raíssa" sem nascimento igual — esses só pela tela, com
alguém conferindo.
"""
import os
import tempfile
from datetime import date, datetime, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import LogAuditoria
from agenda.models import Agendamento, GuiaConvenio
from prontuario.models import EntradaProntuario

from .models import Convenio, DuplicidadeIgnorada, Paciente, Procedimento, Profissional
from .unificacao import UnificacaoInvalida, grupos_por_nome, nome_normalizado, relacoes_do_paciente, unificar


def _comando(confirmar=True):
    caminho = os.path.join(tempfile.mkdtemp(), "relatorio.csv")
    args = ["--relatorio", caminho] + (["--confirmar"] if confirmar else [])
    call_command("unificar_pacientes_duplicados", *args, stdout=open(os.devnull, "w"))
    with open(caminho, encoding="utf-8-sig") as arquivo:
        return arquivo.read()


class _Base(TestCase):
    def setUp(self):
        self.nasc = date(1990, 3, 15)
        self.profissional = Profissional.objects.create(
            nome="Dra. Teste", registro_profissional="CRM-UNI", percentual_desconto=Decimal("30.00"),
            especialidade="medico_clinico_geral",
        )

    def _agendamento(self, paciente, dia=1):
        inicio = timezone.make_aware(datetime.combine(date(2026, 10, dia), time(9, 0)))
        return Agendamento.objects.create(
            paciente=paciente, profissional=self.profissional, data_hora_inicio=inicio,
            data_hora_fim=inicio + timezone.timedelta(minutes=30),
        )


class RegraTests(_Base):
    def test_nome_normalizado(self):
        self.assertEqual(nome_normalizado("Raíssa  da SILVA."), nome_normalizado("raissa da silva"))
        self.assertNotEqual(nome_normalizado("Raissa Silva"), nome_normalizado("Rayssa Silva"))

    def test_relacoes_cobrem_todo_historico(self):
        labels = {model._meta.label for model, _campo in relacoes_do_paciente()}
        for esperado in (
            "agenda.Agendamento", "agenda.PacoteVendido", "agenda.GuiaConvenio",
            "prontuario.EntradaProntuario", "documentos.DocumentoEmitido",
        ):
            self.assertIn(esperado, labels)

    def test_move_tudo_e_completa_dados_sem_sobrescrever(self):
        principal = Paciente.objects.create(nome_completo="Raissa Lima", data_nascimento=self.nasc, telefone="(91) 1111-1111")
        copia = Paciente.objects.create(
            nome_completo="Raíssa Lima", data_nascimento=self.nasc, telefone="(91) 2222-2222", cpf="52998224725", rg="123"
        )
        ag = self._agendamento(copia)
        EntradaProntuario.objects.create(paciente=copia, especialidade="medico_clinico_geral")
        convenio = Convenio.objects.create(nome="Plano X")
        guia = GuiaConvenio.objects.create(
            paciente=copia, convenio=convenio, procedimento=Procedimento.objects.create(nome="Sessão"),
            numero="1", quantidade_sessoes=10, validade=date(2027, 1, 1),
        )

        movidos = unificar(principal, [copia])

        self.assertEqual(movidos, 3)
        ag.refresh_from_db(), guia.refresh_from_db(), principal.refresh_from_db(), copia.refresh_from_db()
        self.assertEqual(ag.paciente, principal)
        self.assertEqual(guia.paciente, principal)
        self.assertEqual(EntradaProntuario.objects.filter(paciente=principal).count(), 1)
        self.assertEqual(principal.telefone, "(91) 1111-1111")  # já preenchido: não troca
        self.assertEqual((principal.cpf, principal.rg), ("52998224725", "123"))  # vazios: completa
        self.assertEqual(copia.unificado_em, principal)
        self.assertIsNone(copia.cpf)
        self.assertTrue(LogAuditoria.objects.filter(acao__contains=f"#{copia.pk}").exists())

    def test_cpfs_diferentes_nao_unifica(self):
        a = Paciente.objects.create(nome_completo="Ana Souza", data_nascimento=self.nasc, cpf="52998224725")
        b = Paciente.objects.create(nome_completo="Ana Souza", data_nascimento=self.nasc, cpf="11144477735")
        with self.assertRaises(UnificacaoInvalida):
            unificar(a, [b])
        b.refresh_from_db()
        self.assertIsNone(b.unificado_em_id)

    def test_corrente_de_unificacao_aponta_direto_pra_principal(self):
        a = Paciente.objects.create(nome_completo="Joao")
        b = Paciente.objects.create(nome_completo="Joao")
        c = Paciente.objects.create(nome_completo="Joao")
        unificar(b, [c])
        unificar(a, [b])
        c.refresh_from_db()
        self.assertEqual(c.unificado_em, a)

    def test_tipos_de_grupo(self):
        Paciente.objects.create(nome_completo="Mesma Data", data_nascimento=self.nasc)
        Paciente.objects.create(nome_completo="Mesmá Data", data_nascimento=self.nasc)
        Paciente.objects.create(nome_completo="Sem Data")
        Paciente.objects.create(nome_completo="Sem Data", data_nascimento=self.nasc)
        Paciente.objects.create(nome_completo="Outra Data", data_nascimento=self.nasc)
        Paciente.objects.create(nome_completo="Outra Data", data_nascimento=date(2000, 1, 1))
        Paciente.objects.create(nome_completo="Sozinho")
        tipos = {g["chave"]: g["tipo"] for g in grupos_por_nome()}
        self.assertEqual(tipos, {"mesma data": "certo", "sem data": "sem_nascimento", "outra data": "nascimento_diferente"})


class ComandoLoteTests(_Base):
    def test_casos_1_2_3_unificam_e_4_5_nao(self):
        caso1a = Paciente.objects.create(nome_completo="Maria Alves", data_nascimento=self.nasc, telefone="(91) 1111-1111")
        caso1b = Paciente.objects.create(nome_completo="Maria Alves", data_nascimento=self.nasc, telefone="(91) 9999-9999")
        caso3a = Paciente.objects.create(nome_completo="Raissa Costa", data_nascimento=self.nasc)
        caso3b = Paciente.objects.create(nome_completo="RAÍSSA  COSTA", data_nascimento=self.nasc)
        caso4a = Paciente.objects.create(nome_completo="Jose Lima", data_nascimento=self.nasc)
        caso4b = Paciente.objects.create(nome_completo="Jose Lima")
        caso5a = Paciente.objects.create(nome_completo="Raissa Melo", data_nascimento=self.nasc)
        caso5b = Paciente.objects.create(nome_completo="Raíssa Melo", data_nascimento=date(2001, 1, 1))
        self._agendamento(caso1b)

        relatorio = _comando()

        unificados = set(Paciente.objects.filter(unificado_em__isnull=False).values_list("pk", flat=True))
        self.assertEqual(len(unificados), 2)
        self.assertTrue({caso1a.pk, caso1b.pk} & unificados and {caso3a.pk, caso3b.pk} & unificados)
        self.assertFalse(unificados & {caso4a.pk, caso4b.pk, caso5a.pk, caso5b.pk})
        # Principal do caso 1 = a com histórico; o agendamento continua nela.
        self.assertIn(caso1a.pk, unificados)
        self.assertEqual(Agendamento.objects.get().paciente_id, caso1b.pk)
        self.assertIn("unificado", relatorio)

    def test_simulacao_nao_grava_nada(self):
        a = Paciente.objects.create(nome_completo="Maria Alves", data_nascimento=self.nasc)
        b = Paciente.objects.create(nome_completo="Maria Alves", data_nascimento=self.nasc)
        self._agendamento(b)
        _comando(confirmar=False)
        self.assertFalse(Paciente.objects.filter(unificado_em__isnull=False).exists())
        self.assertEqual(Agendamento.objects.get().paciente_id, b.pk)
        self.assertFalse(LogAuditoria.objects.filter(acao__contains="Unificou").exists())
        self.assertTrue(a.pk)

    def test_cpfs_diferentes_ficam_no_relatorio(self):
        Paciente.objects.create(nome_completo="Ana Souza", data_nascimento=self.nasc, cpf="52998224725")
        Paciente.objects.create(nome_completo="Ana Souza", data_nascimento=self.nasc, cpf="11144477735")
        relatorio = _comando()
        self.assertFalse(Paciente.objects.filter(unificado_em__isnull=False).exists())
        self.assertIn("CPFs diferentes", relatorio)

    def test_prefere_cadastro_feito_aqui_como_principal(self):
        legado = Paciente.objects.create(nome_completo="Paulo Reis", data_nascimento=self.nasc, origem_legado_id="77", telefone="antigo")
        novo = Paciente.objects.create(nome_completo="Paulo Reis", data_nascimento=self.nasc, telefone="(91) 98888-0000")
        EntradaProntuario.objects.create(paciente=legado, especialidade="medico_clinico_geral")
        _comando()
        legado.refresh_from_db()
        self.assertEqual(legado.unificado_em, novo)
        self.assertEqual(EntradaProntuario.objects.get().paciente, novo)


class TelaDuplicadosTests(_Base):
    def setUp(self):
        super().setUp()
        self.a = Paciente.objects.create(nome_completo="Jose Lima", data_nascimento=self.nasc)
        self.b = Paciente.objects.create(nome_completo="José Lima")
        self.url = reverse("cadastros:paciente_duplicados")

    def _login(self, *codenames):
        usuario = get_user_model().objects.create_user(username="recep", password="senha-teste-123", exigir_troca_senha=False)
        for codename in codenames:
            usuario.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(usuario)
        return usuario

    def test_exige_permissao(self):
        self._login("paciente_cadastrar", "paciente_editar")
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_lista_e_unifica(self):
        self._login("paciente_excluir")
        resposta = self.client.get(self.url)
        self.assertContains(resposta, "jose lima")
        self._agendamento(self.b)
        self.client.post(self.url, {"acao": "unificar", "principal": self.a.pk, "juntar": [self.b.pk], "voltar": self.url})
        self.b.refresh_from_db()
        self.assertEqual(self.b.unificado_em, self.a)
        self.assertEqual(Agendamento.objects.get().paciente, self.a)
        self.assertNotContains(self.client.get(self.url), "jose lima")

    def test_nao_sao_a_mesma_pessoa(self):
        self._login("paciente_excluir")
        self.client.post(self.url, {"acao": "ignorar", "ids": f"{self.a.pk},{self.b.pk}", "voltar": self.url})
        self.assertTrue(DuplicidadeIgnorada.objects.exists())
        self.assertNotContains(self.client.get(self.url), "jose lima")
        # Uma terceira ficha com o mesmo nome: o grupo volta pra conferir.
        Paciente.objects.create(nome_completo="JOSE LIMA")
        self.assertContains(self.client.get(self.url), "jose lima")

    def test_profissional_abre_copia_e_cai_na_principal(self):
        unificar(self.a, [self.b])
        usuario = self._login()
        usuario.profissional_id = self.profissional.pk
        usuario.save()
        resposta = self.client.get(reverse("prontuario:prontuario_paciente", args=[self.b.pk]))
        self.assertRedirects(resposta, reverse("prontuario:prontuario_paciente", args=[self.a.pk]), fetch_redirect_response=False)


class CadastroNovoAvisaDuplicadoTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(username="recep", password="senha-teste-123", exigir_troca_senha=False)
        usuario.user_permissions.add(Permission.objects.get(codename="paciente_cadastrar"))
        self.client.force_login(usuario)
        self.existente = Paciente.objects.create(nome_completo="Raíssa Lima", data_nascimento=date(1990, 3, 15), telefone="(91) 1111-1111")
        self.url = reverse("cadastros:paciente_novo")
        self.dados = {"nome_completo": "raissa lima", "data_nascimento": "1990-03-15", "telefone": "(91) 2222-2222", "ativo": "on"}

    def test_avisa_e_nao_salva(self):
        resposta = self.client.post(self.url, self.dados)
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "já existe paciente com este nome")
        self.assertContains(resposta, "(91) 1111-1111")
        self.assertEqual(Paciente.objects.count(), 1)

    def test_salva_ao_confirmar_que_e_outra_pessoa(self):
        self.client.post(self.url, {**self.dados, "confirmar_outra_pessoa": "on"})
        self.assertEqual(Paciente.objects.count(), 2)

    def test_nome_novo_salva_direto(self):
        self.client.post(self.url, {**self.dados, "nome_completo": "Outra Pessoa"})
        self.assertEqual(Paciente.objects.count(), 2)

    def test_popup_usar_existente(self):
        resposta = self.client.get(self.url, {"popup": "1", "usar": self.existente.pk})
        self.assertContains(resposta, "paciente_criado")
        self.assertContains(resposta, f"id: {self.existente.pk}")


class ConferenciaCopiaParaClinicaTests(_Base):
    """conferencia_duplicados: decisões tomadas numa cópia do banco repetidas na clínica."""

    def _rodar(self, *args):
        import io

        saida = io.StringIO()
        call_command("conferencia_duplicados", *args, stdout=saida)
        return saida.getvalue()

    def setUp(self):
        super().setUp()
        self.arquivo = os.path.join(tempfile.mkdtemp(), "conferencia.json")
        self.a = Paciente.objects.create(nome_completo="Jose Lima", data_nascimento=self.nasc)
        self.b = Paciente.objects.create(nome_completo="José Lima")
        self.c = Paciente.objects.create(nome_completo="Ana Reis", data_nascimento=self.nasc)
        self.d = Paciente.objects.create(nome_completo="Ana Reis", data_nascimento=date(2001, 1, 1))
        self.ag = self._agendamento(self.b)

    def _decidir_na_copia_e_desfazer(self):
        """Simula a cópia: toma as decisões, exporta e volta o banco ao estado "clínica"."""
        from django.db import transaction

        with transaction.atomic():
            unificar(self.a, [self.b])
            DuplicidadeIgnorada.objects.create(ids=DuplicidadeIgnorada.chave([self.c.pk, self.d.pk]), nome="Ana Reis")
            saida = self._rodar("exportar", self.arquivo)
            transaction.set_rollback(True)
        return saida

    def test_exporta_sem_nome_e_importa(self):
        self._decidir_na_copia_e_desfazer()
        with open(self.arquivo, encoding="utf-8") as arquivo:
            conteudo = arquivo.read()
        self.assertNotIn("Lima", conteudo)  # sem dado pessoal, só IDs e hash
        self.assertIsNone(Paciente.objects.get(pk=self.b.pk).unificado_em_id)

        saida = self._rodar("importar", self.arquivo)  # simulação
        self.assertIn("a juntar: 1", saida)
        self.assertIsNone(Paciente.objects.get(pk=self.b.pk).unificado_em_id)

        self._rodar("importar", self.arquivo, "--confirmar")
        self.assertEqual(Paciente.objects.get(pk=self.b.pk).unificado_em_id, self.a.pk)
        self.ag.refresh_from_db()
        self.assertEqual(self.ag.paciente_id, self.a.pk)
        self.assertTrue(DuplicidadeIgnorada.objects.filter(ids=DuplicidadeIgnorada.chave([self.c.pk, self.d.pk])).exists())

        saida = self._rodar("importar", self.arquivo, "--confirmar")  # de novo: nada a fazer
        self.assertIn("Já estavam juntadas: 1", saida)
        self.assertIn("Nenhuma decisão pulada", saida)

    def test_pula_ficha_que_mudou_de_nome_na_clinica(self):
        self._decidir_na_copia_e_desfazer()
        Paciente.objects.filter(pk=self.b.pk).update(nome_completo="Outra Pessoa")
        saida = self._rodar("importar", self.arquivo, "--confirmar")
        self.assertIn("mudou de nome", saida)
        self.assertIsNone(Paciente.objects.get(pk=self.b.pk).unificado_em_id)


class TelefoneLixoTests(TestCase):
    def test_telefone_invalido_da_principal_e_trocado(self):
        principal = Paciente.objects.create(nome_completo="Alex Santos", telefone="9", cpf="52998224725")
        copia = Paciente.objects.create(nome_completo="Alex Santos", telefone="(91)99207-8467")
        unificar(principal, [copia])
        principal.refresh_from_db()
        self.assertEqual(principal.telefone, "(91)99207-8467")

    def test_telefone_valido_da_principal_fica(self):
        principal = Paciente.objects.create(nome_completo="Alex Santos", telefone="(91)98888-0000")
        copia = Paciente.objects.create(nome_completo="Alex Santos", telefone="(91)99207-8467")
        unificar(principal, [copia])
        principal.refresh_from_db()
        self.assertEqual(principal.telefone, "(91)98888-0000")


class CpfComESemPontuacaoTests(TestCase):
    def test_mesmo_cpf_com_e_sem_pontuacao_unifica(self):
        a = Paciente.objects.create(nome_completo="Ana Reis", cpf="529.982.247-25")
        b = Paciente.objects.create(nome_completo="Ana Reis", cpf="52998224725")
        unificar(a, [b])
        b.refresh_from_db()
        self.assertEqual(b.unificado_em, a)
