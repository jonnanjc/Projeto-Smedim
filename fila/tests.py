"""
Testes do padrão de chamada P;P;N;P;P;N (ver Senha.PADRAO_CHAMADA/chamar_proxima em
fila/models.py — decisão da clínica, 2026-07-30) e do fluxo de exame avulso (ver
cadastros.models.Procedimento.exame_sem_profissional e agenda.models.Agendamento.
exames_avulsos_agendados_em, mesma decisão — ex: colocação de MAPA sem o cardiologista
atender no dia).

Roda numa base de dados de teste isolada (padrão do Django TestCase, em memória para
SQLite) — não toca em db.sqlite3.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from agenda.models import Agendamento, AgendamentoItem
from cadastros.models import Guiche, Paciente, Procedimento, Profissional

from .models import Senha

Usuario = get_user_model()

ESPECIALIDADE_CARDIO = [("medico_cardiologista", "")]


class ChamarProximaPadraoTests(TestCase):
    """Padrão fixo P;P;N;P;P;N (Senha.PADRAO_CHAMADA/_proximo_tipo_do_padrao)."""

    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            username="atendente_teste", password="senha-teste-123", exigir_troca_senha=False
        )
        self.guiche = Guiche.objects.create(nome="Guichê Teste")

    def _emitir(self, tipo, motivo_prioridade=""):
        return Senha.emitir(tipo=tipo, motivo_prioridade=motivo_prioridade, especialidade="medico_cardiologista")

    def _chamar(self):
        senha = Senha.chamar_proxima(self.usuario, self.guiche, ESPECIALIDADE_CARDIO)
        return senha.tipo if senha else None

    def test_segue_padrao_p_p_n_quando_o_normal_ja_esperava(self):
        # Normais chegaram antes: os prioritários que chegam depois passam na frente, mas no
        # máximo 2 por vez — a Normal não fica esquecida.
        for _ in range(2):
            self._emitir(Senha.Tipo.NORMAL)
        for _ in range(4):
            self._emitir(Senha.Tipo.PRIORITARIO, Senha.Motivo.IDOSO_60)

        chamadas = [self._chamar() for _ in range(6)]
        self.assertEqual(chamadas, ["P", "P", "N", "P", "P", "N"])

    def test_prioritarios_que_ja_estavam_vem_antes_do_normal_que_chegou_depois(self):
        """Pedido da clínica (23/10/2026): se os P já estão na clínica e chega um Normal,
        os P são chamados antes dele."""
        for _ in range(4):
            self._emitir(Senha.Tipo.PRIORITARIO, Senha.Motivo.IDOSO_60)
        for _ in range(2):
            self._emitir(Senha.Tipo.NORMAL)

        chamadas = [self._chamar() for _ in range(6)]
        self.assertEqual(chamadas, ["P", "P", "P", "P", "N", "N"])

    def test_cai_pro_outro_tipo_quando_fila_da_vez_esta_vazia(self):
        # só prioritário aguardando: o turno de Normal (3ª chamada) não trava a fila,
        # cai pro prioritário que sobrou.
        for _ in range(3):
            self._emitir(Senha.Tipo.PRIORITARIO, Senha.Motivo.IDOSO_60)

        chamadas = [self._chamar() for _ in range(3)]
        self.assertEqual(chamadas, ["P", "P", "P"])

    def test_normal_esperando_nao_perde_a_vez_para_mais_de_2_prioritarios(self):
        """Pedido da clínica (23/10/2026): saíam 3 P pra 1 N mesmo com a Normal esperando
        desde antes. A vez de Normal sem ninguém esperando era "gasta" por um P e o ciclo
        desalinhava. Agora conta as últimas chamadas de fato."""
        for _ in range(3):
            self._emitir(Senha.Tipo.PRIORITARIO, Senha.Motivo.IDOSO_60)
        self.assertEqual([self._chamar() for _ in range(3)], ["P", "P", "P"])  # só tinha P

        self._emitir(Senha.Tipo.NORMAL)
        for _ in range(3):
            self._emitir(Senha.Tipo.PRIORITARIO, Senha.Motivo.IDOSO_60)
        self._emitir(Senha.Tipo.NORMAL)
        # N1 chegou antes dos 3 P → sai já; depois P, P; a 2ª Normal chegou depois do 3º P,
        # então ele vem antes dela.
        self.assertEqual([self._chamar() for _ in range(5)], ["N", "P", "P", "P", "N"])

    def test_nivel_especial_fura_so_os_demais_prioritarios_nao_a_sequencia(self):
        """
        Confirmado com o cliente (2026-07-30): idoso 80+/gestante em emergência furam a
        vez dos DEMAIS prioritários (continuam na frente dentro de um turno de
        Prioritário), mas não furam a sequência P;P;N em si — um turno de Normal
        continua sendo de Normal mesmo com nível especial aguardando.
        """
        comum = self._emitir(Senha.Tipo.PRIORITARIO, Senha.Motivo.IDOSO_60)
        especial = self._emitir(Senha.Tipo.PRIORITARIO, Senha.Motivo.IDOSO_80)
        normal = self._emitir(Senha.Tipo.NORMAL)

        primeira = Senha.chamar_proxima(self.usuario, self.guiche, ESPECIALIDADE_CARDIO)
        segunda = Senha.chamar_proxima(self.usuario, self.guiche, ESPECIALIDADE_CARDIO)
        terceira = Senha.chamar_proxima(self.usuario, self.guiche, ESPECIALIDADE_CARDIO)

        self.assertEqual(primeira.pk, especial.pk)
        self.assertEqual(segunda.pk, comum.pk)
        self.assertEqual(terceira.pk, normal.pk)

    def test_especialidades_diferentes_nao_compartilham_o_padrao(self):
        """O padrão é contado por especialidade coberta (mesmo escopo de
        _fila_aguardando) — chamadas de Cardiologia não avançam o ciclo de Pediatria."""
        self._emitir(Senha.Tipo.PRIORITARIO, Senha.Motivo.IDOSO_60)
        pediatria_normal = Senha.emitir(tipo=Senha.Tipo.NORMAL, especialidade="medico_pediatra")

        self._chamar()  # consome o turno P de Cardiologia — não deveria afetar Pediatria

        chamada_pediatria = Senha.chamar_proxima(self.usuario, self.guiche, [("medico_pediatra", "")])
        self.assertEqual(chamada_pediatria.pk, pediatria_normal.pk)

    def test_guiche_com_duas_especialidades_tem_um_ciclo_para_cada(self):
        """Pedido da clínica (09/11/2026): guichê cobrindo Cardio + Fisio só chamava P de Cardio —
        a vez do N era uma só e ia sempre pro N da Fisio, que tinha chegado antes. Agora cada
        especialidade tem o seu P;P;N, e o N de Cardio sai na vez de Cardio."""
        cobertura = [("medico_cardiologista", ""), ("fisioterapeuta", "")]
        fisio_1 = Senha.emitir(tipo=Senha.Tipo.NORMAL, especialidade="fisioterapeuta")
        fisio_2 = Senha.emitir(tipo=Senha.Tipo.NORMAL, especialidade="fisioterapeuta")
        cardio_n = self._emitir(Senha.Tipo.NORMAL)
        cardio_p = [self._emitir(Senha.Tipo.PRIORITARIO, Senha.Motivo.IDOSO_60) for _ in range(4)]

        chamadas = [Senha.chamar_proxima(self.usuario, self.guiche, cobertura).pk for _ in range(7)]
        self.assertEqual(
            chamadas,
            [fisio_1.pk, fisio_2.pk, cardio_p[0].pk, cardio_p[1].pk, cardio_n.pk, cardio_p[2].pk, cardio_p[3].pk],
        )

    def test_p_antigo_de_ordem_menor_nao_trava_os_normais(self):
        """Caso real da clínica (09/11/2026): gestante P007 às 08:12 e N017 às 08:15. Os idosos
        60+ que chegavam depois saíam antes dela (ordem da lei) e, na vez do N, ela "tinha chegado
        antes" — então chamava outro idoso 60+ e nenhum N saía. Agora, na vez do N, sai a gestante
        (P mais antigo) e o N sai logo na chamada seguinte (ainda é vez de N: 2 P seguidos)."""
        gestante = self._emitir(Senha.Tipo.PRIORITARIO, Senha.Motivo.GESTANTE)
        normal = self._emitir(Senha.Tipo.NORMAL)
        idosos = [self._emitir(Senha.Tipo.PRIORITARIO, Senha.Motivo.IDOSO_60) for _ in range(2)]
        primeiras = [Senha.chamar_proxima(self.usuario, self.guiche, ESPECIALIDADE_CARDIO).pk for _ in range(2)]
        idosos += [self._emitir(Senha.Tipo.PRIORITARIO, Senha.Motivo.IDOSO_60) for _ in range(2)]
        depois = [Senha.chamar_proxima(self.usuario, self.guiche, ESPECIALIDADE_CARDIO).pk for _ in range(4)]

        self.assertEqual(
            primeiras + depois,
            [idosos[0].pk, idosos[1].pk, gestante.pk, normal.pk, idosos[2].pk, idosos[3].pk],
        )

    def test_nivel_especial_de_outra_especialidade_sai_antes(self):
        cobertura = [("medico_cardiologista", ""), ("fisioterapeuta", "")]
        Senha.emitir(tipo=Senha.Tipo.NORMAL, especialidade="fisioterapeuta")
        especial = self._emitir(Senha.Tipo.PRIORITARIO, Senha.Motivo.IDOSO_80)
        self.assertEqual(Senha.chamar_proxima(self.usuario, self.guiche, cobertura).pk, especial.pk)


class SenhaPorProfissionalTests(TestCase):
    """Pedido da clínica (2026-10-16): especialidade com 2+ profissionais no dia — o paciente
    escolhe o profissional no totem e cada atendente chama só os seus (AtendimentoProfissional)."""

    def setUp(self):
        from datetime import time

        from cadastros.models import HorarioAtendimento

        self.hoje = timezone.localdate()

        def _clinico(nome):
            profissional = Profissional.objects.create(
                nome=nome, registro_profissional="CRM", percentual_desconto=Decimal("30.00"),
                especialidade="medico_clinico_geral",
            )
            HorarioAtendimento.objects.create(
                # Dia inteiro: o totem esconde quem já encerrou o horário (23/10/2026) e o teste
                # não pode depender da hora em que roda.
                profissional=profissional, dia_semana=self.hoje.weekday(), turno="MANHA",
                horario_inicio=time(0), horario_fim=time(23, 59, 59),
            )
            return profissional

        self.ana = _clinico("Dra. Ana")
        self.bruno = _clinico("Dr. Bruno")
        self.guiche = Guiche.objects.create(nome="Guichê 1")
        self.atendente_ana = Usuario.objects.create_user(username="at_ana", password="x", exigir_troca_senha=False)
        self.atendente_bruno = Usuario.objects.create_user(username="at_bruno", password="x", exigir_troca_senha=False)

    def _cobrir(self, usuario, profissionais):
        from cadastros.models import AtendimentoEspecialidade, AtendimentoProfissional

        AtendimentoEspecialidade.definir(usuario, self.hoje, [("medico_clinico_geral", "")])
        AtendimentoProfissional.definir(usuario, self.hoje, [p.pk for p in profissionais])
        return AtendimentoProfissional.cobertura_em(usuario, self.hoje)

    def test_totem_pergunta_o_profissional_e_grava_na_senha(self):
        resposta = self.client.get("/fila/totem/tipo/", {"especialidade": "medico_clinico_geral|"})
        self.assertRedirects(resposta, "/fila/totem/profissional/?especialidade=medico_clinico_geral%7C")

        tela = self.client.get("/fila/totem/profissional/", {"especialidade": "medico_clinico_geral|"})
        self.assertContains(tela, "Dra. Ana")
        self.assertContains(tela, "Dr. Bruno")
        self.assertContains(tela, "Não sei / qualquer um")

        self.client.post(
            "/fila/totem/emitir/",
            {"tipo": "N", "especialidade": "medico_clinico_geral|", "procedimento": "", "profissional": str(self.bruno.pk)},
        )
        senha = Senha.objects.get()
        self.assertEqual(senha.profissional, self.bruno)
        self.assertEqual(senha.rotulo_atendimento, "Médico clínico geral — Dr. Bruno")

    def test_com_um_so_profissional_nao_pergunta(self):
        self.bruno.ativo = False
        self.bruno.save()
        resposta = self.client.get("/fila/totem/tipo/", {"especialidade": "medico_clinico_geral|"})
        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, "Com qual profissional")

    def test_profissional_invalido_vira_senha_sem_profissional(self):
        self.client.post(
            "/fila/totem/emitir/",
            {"tipo": "N", "especialidade": "medico_clinico_geral|", "procedimento": "", "profissional": "999999"},
        )
        self.assertIsNone(Senha.objects.get().profissional)

    def test_cada_atendente_chama_so_os_seus_e_os_sem_profissional(self):
        do_bruno = Senha.emitir(tipo="N", especialidade="medico_clinico_geral", profissional=self.bruno)
        da_ana = Senha.emitir(tipo="N", especialidade="medico_clinico_geral", profissional=self.ana)
        qualquer = Senha.emitir(tipo="N", especialidade="medico_clinico_geral")

        cobertura_ana = self._cobrir(self.atendente_ana, [self.ana])
        primeira = Senha.chamar_proxima(self.atendente_ana, self.guiche, cobertura_ana)
        segunda = Senha.chamar_proxima(self.atendente_ana, self.guiche, cobertura_ana)
        terceira = Senha.chamar_proxima(self.atendente_ana, self.guiche, cobertura_ana)
        self.assertEqual([primeira.pk, segunda.pk], [da_ana.pk, qualquer.pk])
        self.assertIsNone(terceira)

        cobertura_bruno = self._cobrir(self.atendente_bruno, [self.bruno])
        self.assertEqual(Senha.chamar_proxima(self.atendente_bruno, self.guiche, cobertura_bruno).pk, do_bruno.pk)

    def test_agenda_do_dia_grava_profissionais_de_hoje(self):
        from django.contrib.auth.models import Permission

        from cadastros.models import AtendimentoProfissional

        for codename in ("agenda_ver_dia", "fila_especialidade_configurar"):
            self.atendente_ana.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(self.atendente_ana)

        tela = self.client.get("/agenda/dia/")
        self.assertContains(tela, "Só os profissionais (nenhum = todos)")

        self.client.post(
            "/agenda/dia/",
            {"form": "especialidades", "especialidades": ["medico_clinico_geral|"], "profissionais": [str(self.ana.pk)]},
        )
        self.assertEqual(AtendimentoProfissional.profissionais_em(self.atendente_ana, self.hoje), [self.ana.pk])
        self.assertEqual(
            AtendimentoProfissional.cobertura_em(self.atendente_ana, self.hoje),
            [("medico_clinico_geral", "", {self.ana.pk})],
        )

    def test_sem_profissional_marcado_cobre_todos(self):
        Senha.emitir(tipo="N", especialidade="medico_clinico_geral", profissional=self.bruno)
        cobertura = self._cobrir(self.atendente_ana, [])
        self.assertEqual(cobertura, [("medico_clinico_geral", "", None)])
        self.assertIsNotNone(Senha.chamar_proxima(self.atendente_ana, self.guiche, cobertura))


class ExameAvulsoTests(TestCase):
    """Procedimento.exame_sem_profissional + Agendamento.exames_avulsos_agendados_em."""

    def setUp(self):
        self.paciente = Paciente.objects.create(nome_completo="Paciente Teste")
        self.cardiologista = Profissional.objects.create(
            nome="Dr. Cardio Teste",
            registro_profissional="CRM-TESTE",
            percentual_desconto=Decimal("30.00"),
            especialidade="medico_cardiologista",
        )
        self.mapa = Procedimento.objects.create(
            nome="MAPA", exame_sem_profissional=True, valor_particular=Decimal("200.00")
        )
        self.hoje = timezone.localdate()
        inicio = timezone.make_aware(datetime.combine(self.hoje, datetime.min.time().replace(hour=9)))
        self.agendamento = Agendamento.objects.create(
            paciente=self.paciente,
            profissional=self.cardiologista,
            data_hora_inicio=inicio,
            data_hora_fim=inicio + timedelta(minutes=30),
        )
        AgendamentoItem.objects.create(agendamento=self.agendamento, procedimento=self.mapa)

    def test_exame_aparece_quando_ha_agendamento_hoje(self):
        exames = Agendamento.exames_avulsos_agendados_em(self.hoje)
        self.assertEqual([e["procedimento"] for e in exames], [self.mapa])
        self.assertEqual(exames[0]["especialidade"], "medico_cardiologista")

    def test_exame_nao_aparece_em_outro_dia(self):
        ontem = self.hoje - timedelta(days=1)
        self.assertEqual(Agendamento.exames_avulsos_agendados_em(ontem), [])

    def test_exame_some_quando_agendamento_cancelado(self):
        self.agendamento.status = Agendamento.Status.CANCELADO
        self.agendamento.save()
        self.assertEqual(Agendamento.exames_avulsos_agendados_em(self.hoje), [])

    def test_procedimento_sem_a_marcacao_nao_aparece(self):
        consulta_normal = Procedimento.objects.create(
            nome="Consulta Teste", eh_consulta=True, valor_particular=Decimal("150.00")
        )
        AgendamentoItem.objects.create(agendamento=self.agendamento, procedimento=consulta_normal)

        exames = Agendamento.exames_avulsos_agendados_em(self.hoje)
        self.assertEqual([e["procedimento"] for e in exames], [self.mapa])

    def test_senha_com_exame_mostra_rotulo_do_exame(self):
        senha = Senha.emitir(tipo=Senha.Tipo.NORMAL, especialidade="medico_cardiologista", procedimento_exame=self.mapa)
        self.assertEqual(senha.rotulo_atendimento, "Exame: MAPA")

    def test_senha_sem_exame_mostra_especialidade(self):
        senha = Senha.emitir(tipo=Senha.Tipo.NORMAL, especialidade="medico_cardiologista")
        self.assertEqual(senha.rotulo_atendimento, "Médico cardiologista")


class PainelVozTests(TestCase):
    """Voz do painel gerada no servidor (fila/voz.py, fila.views.painel_voz)."""

    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            username="atendente_voz", password="senha-teste-123", exigir_troca_senha=False
        )
        self.guiche = Guiche.objects.create(nome="Guichê 1")

    def _senha_chamada(self):
        Senha.emitir(tipo=Senha.Tipo.PRIORITARIO, motivo_prioridade=Senha.Motivo.IDOSO_60, especialidade="medico_cardiologista")
        return Senha.chamar_proxima(self.usuario, self.guiche, ESPECIALIDADE_CARDIO)

    def test_texto_da_senha_soletra_codigo_e_guiche(self):
        from . import voz

        senha = self._senha_chamada()
        esperado = "Senha " + " ".join(senha.codigo) + ". Guichê 1."
        self.assertEqual(voz.texto_da_chamada("senha", str(senha.pk)), esperado)

    def test_texto_do_consultorio_so_primeiro_nome(self):
        from cadastros.models import Sala

        from . import voz

        profissional = Profissional.objects.create(
            nome="Dra Teste", registro_profissional="CRM-1", percentual_desconto=Decimal("30.00")
        )
        sala = Sala.objects.create(nome="Consultório 3")
        inicio = timezone.now()
        agendamento = Agendamento.objects.create(
            paciente=Paciente.objects.create(nome_completo="MARIA DAS DORES SILVA"),
            profissional=profissional,
            forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
            data_hora_inicio=inicio,
            data_hora_fim=inicio + timedelta(minutes=30),
            status=Agendamento.Status.AUTORIZADO,
            chamado_em=inicio,
            sala_chamada=sala,
        )
        self.assertEqual(voz.texto_da_chamada("agendamento", str(agendamento.pk)), "Maria. Consultório 3.")

    def test_chamada_inexistente_ou_tipo_invalido_da_404(self):
        self.assertEqual(self.client.get(reverse("fila:painel_voz", args=["senha", "999999"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("fila:painel_voz", args=["outro", "1"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("fila:painel_voz", args=["recepcao_propria", "xyz"])).status_code, 404)

    def test_sem_voz_instalada_responde_503(self):
        with mock.patch("fila.voz.gerar_wav", return_value=None):
            resposta = self.client.get(reverse("fila:painel_voz", args=["teste", "0"]))
        self.assertEqual(resposta.status_code, 503)

    def test_devolve_o_audio(self):
        senha = self._senha_chamada()
        with mock.patch("fila.voz.gerar_wav", return_value=b"RIFF-falso") as gerar:
            resposta = self.client.get(reverse("fila:painel_voz", args=["senha", senha.pk]))
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["Content-Type"], "audio/wav")
        self.assertEqual(resposta.content, b"RIFF-falso")
        gerar.assert_called_once()

    def test_painel_aponta_para_a_voz_da_chamada(self):
        senha = self._senha_chamada()
        with mock.patch("fila.voz.aquecer_em_segundo_plano"):
            resposta = self.client.get(reverse("fila:painel_espera"))
        self.assertContains(resposta, f'data-voz-url="{reverse("fila:painel_voz", args=["senha", senha.pk])}"')


class TotemEscondeQuemJaEncerrouTests(TestCase):
    """Pedido da clínica (23/10/2026): atende das 07h às 11h → às 11:01 a especialidade some do totem."""

    def test_especialidade_some_depois_do_horario(self):
        from datetime import date, time

        from cadastros.models import HorarioAtendimento

        profissional = Profissional.objects.create(
            nome="Dr. Manhã", registro_profissional="CRM-M", especialidade="medico_cardiologista", percentual_desconto=0
        )
        segunda = date(2026, 7, 20)
        HorarioAtendimento.objects.create(
            profissional=profissional, dia_semana=segunda.weekday(), turno="MANHA",
            horario_inicio=time(7, 0), horario_fim=time(11, 0),
        )
        chaves = lambda hora: [c for c, _ in Profissional.especialidades_ativas_em(segunda, hora)]
        self.assertIn("medico_cardiologista|", chaves(time(6, 30)))   # antes de começar: aparece
        self.assertIn("medico_cardiologista|", chaves(time(10, 59)))
        self.assertNotIn("medico_cardiologista|", chaves(time(11, 1)))  # terminou: some
        self.assertIn("medico_cardiologista|", chaves(None))  # recepção continua vendo o dia todo

class SenhaExamePreparoTests(TestCase):
    """Senha "Exame com preparo" (pedido da clínica, 2026-10-26): o totem só oferece onde há
    exame com preparo marcado hoje, e a recepção chama essas senhas antes de P e N, por ordem
    de chegada, sem mexer no padrão P;P;N."""

    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            username="atendente_teste", password="senha-teste-123", exigir_troca_senha=False
        )
        self.guiche = Guiche.objects.create(nome="Guichê Teste")
        self.cardiologista = Profissional.objects.create(
            nome="Dr. Cardio Teste",
            registro_profissional="CRM-TESTE",
            percentual_desconto=Decimal("30.00"),
            especialidade="medico_cardiologista",
        )
        self.endoscopia = Procedimento.objects.create(
            nome="Endoscopia", exame_com_preparo=True, valor_particular=Decimal("300.00")
        )
        self.hoje = timezone.localdate()
        inicio = timezone.make_aware(datetime.combine(self.hoje, datetime.min.time().replace(hour=9)))
        self.agendamento = Agendamento.objects.create(
            paciente=Paciente.objects.create(nome_completo="Paciente Teste"),
            profissional=self.cardiologista,
            data_hora_inicio=inicio,
            data_hora_fim=inicio + timedelta(minutes=30),
        )
        AgendamentoItem.objects.create(agendamento=self.agendamento, procedimento=self.endoscopia)

    def _emitir(self, tipo, motivo=""):
        return Senha.emitir(tipo=tipo, motivo_prioridade=motivo, especialidade="medico_cardiologista")

    def _chamar(self):
        senha = Senha.chamar_proxima(self.usuario, self.guiche, ESPECIALIDADE_CARDIO)
        return senha.codigo if senha else None

    def test_exame_preparo_chamado_primeiro_por_ordem_de_chegada(self):
        self._emitir(Senha.Tipo.PRIORITARIO, Senha.Motivo.IDOSO_80)
        self._emitir(Senha.Tipo.NORMAL)
        self._emitir(Senha.Tipo.EXAME_PREPARO)
        self._emitir(Senha.Tipo.EXAME_PREPARO)
        self.assertEqual([self._chamar() for _ in range(4)], ["E001", "E002", "P001", "N001"])

    def test_exame_preparo_nao_conta_no_padrao_p_p_n(self):
        self._emitir(Senha.Tipo.NORMAL)
        for _ in range(3):
            self._emitir(Senha.Tipo.PRIORITARIO, Senha.Motivo.IDOSO_60)
        self.assertEqual([self._chamar() for _ in range(2)], ["P001", "P002"])
        self._emitir(Senha.Tipo.EXAME_PREPARO)
        # E fura a vez, mas depois dele a vez continua sendo do Normal (P;P;N intacto).
        self.assertEqual([self._chamar() for _ in range(3)], ["E001", "N001", "P003"])

    def test_totem_oferece_exame_preparo_so_onde_ha_agendamento(self):
        url = reverse("fila:totem_tipo")
        resposta = self.client.get(url, {"especialidade": "medico_cardiologista|", "profissional": "0"})
        self.assertContains(resposta, "EXAME COM PREPARO")
        resposta = self.client.get(url, {"especialidade": "psicologo|", "profissional": "0"})
        self.assertNotContains(resposta, "EXAME COM PREPARO")

        self.agendamento.status = Agendamento.Status.CANCELADO
        self.agendamento.save()
        resposta = self.client.get(url, {"especialidade": "medico_cardiologista|", "profissional": "0"})
        self.assertNotContains(resposta, "EXAME COM PREPARO")

    def test_emitir_exame_preparo_reconfere_no_servidor(self):
        with mock.patch.object(
            Profissional, "especialidades_ativas_em", return_value=[("medico_cardiologista|", "Cardio")]
        ):
            self.client.post(reverse("fila:totem_emitir"), {"tipo": "E", "especialidade": "medico_cardiologista|"})
            self.assertEqual(Senha.objects.get().codigo, "E001")

            self.agendamento.status = Agendamento.Status.CANCELADO
            self.agendamento.save()
            self.client.post(reverse("fila:totem_emitir"), {"tipo": "E", "especialidade": "medico_cardiologista|"})
            self.assertEqual(Senha.objects.count(), 1)


class SenhasChamadasHojeTests(TestCase):
    """Pedido da clínica (26/10/2026): o painel de chamada lista as senhas chamadas no dia e
    qualquer um com acesso pode chamar de novo — volta à TV com o guichê de quem chamou antes."""

    def setUp(self):
        from django.contrib.auth.models import Permission

        self.guiche = Guiche.objects.create(nome="Guichê 3")
        self.quem_chamou = Usuario.objects.create_user(username="at_1", password="x", exigir_troca_senha=False)
        self.outra = Usuario.objects.create_user(username="at_2", password="x", exigir_troca_senha=False)
        self.outra.user_permissions.add(Permission.objects.get(codename="fila_chamar_senha"))
        self.client.force_login(self.outra)
        Senha.emitir(tipo=Senha.Tipo.NORMAL, especialidade="medico_cardiologista")
        self.senha = Senha.chamar_proxima(self.quem_chamou, self.guiche, ESPECIALIDADE_CARDIO)
        self.senha.concluir_atendimento()

    def test_lista_e_chama_de_novo_com_o_guiche_de_quem_chamou(self):
        tela = self.client.get(reverse("fila:painel_chamada"))
        self.assertContains(tela, self.senha.codigo)
        self.assertContains(tela, "Chamar de novo")

        antes = self.senha.chamado_em
        resposta = self.client.post(reverse("fila:painel_chamada"), {"acao": "rechamar", "senha_id": self.senha.pk})
        self.assertEqual(resposta.status_code, 302)
        self.senha.refresh_from_db()
        self.assertGreater(self.senha.chamado_em, antes)
        self.assertEqual(self.senha.guiche, self.guiche)
        self.assertEqual(self.senha.chamado_por, self.quem_chamou)
        self.assertEqual(self.senha.status, Senha.Status.ATENDIDA)

    def test_senha_de_outro_dia_nao_e_rechamada(self):
        Senha.objects.filter(pk=self.senha.pk).update(data=timezone.localdate() - timedelta(days=1))
        antes = Senha.objects.get(pk=self.senha.pk).chamado_em
        self.client.post(reverse("fila:painel_chamada"), {"acao": "rechamar", "senha_id": self.senha.pk})
        self.assertEqual(Senha.objects.get(pk=self.senha.pk).chamado_em, antes)


class FilaDeEsperaEChamadaManualTests(TestCase):
    """Pedido da clínica (10/11/2026): permissão por usuário pra ver quem aguarda (com a hora de
    chegada) e outra pra chamar uma senha específica, fora da ordem automática."""

    def setUp(self):
        from django.contrib.auth.models import Permission

        from accounts.models import LogAuditoria
        from cadastros.models import AtendimentoGuiche

        self.LogAuditoria = LogAuditoria
        self.Permission = Permission
        self.guiche = Guiche.objects.create(nome="Guichê 1")
        self.usuario = Usuario.objects.create_user(username="recep", password="x", exigir_troca_senha=False)
        self.client.force_login(self.usuario)
        AtendimentoGuiche.objects.create(usuario=self.usuario, guiche=self.guiche, data=timezone.localdate())
        self.primeira = Senha.emitir(tipo=Senha.Tipo.NORMAL, especialidade="medico_cardiologista")
        self.segunda = Senha.emitir(tipo=Senha.Tipo.NORMAL, especialidade="medico_cardiologista")

    def _dar(self, *codenames):
        for codename in codenames:
            self.usuario.user_permissions.add(self.Permission.objects.get(codename=codename))
        self.usuario = Usuario.objects.get(pk=self.usuario.pk)  # limpa cache de permissões
        self.client.force_login(self.usuario)

    def test_sem_permissao_nao_ve_a_fila(self):
        self.assertEqual(self.client.get(reverse("fila:fila_espera")).status_code, 403)
        self._dar("fila_chamar_senha")
        tela = self.client.get(reverse("fila:painel_chamada"))
        self.assertNotContains(tela, "Aguardando para ser chamado")

    def test_com_permissao_ve_quem_aguarda_e_a_hora_de_chegada(self):
        self._dar("fila_ver_aguardando")
        tela = self.client.get(reverse("fila:fila_espera"))
        self.assertContains(tela, "N001")
        self.assertContains(tela, "N002")
        self.assertContains(tela, timezone.localtime(self.primeira.criado_em).strftime("%H:%M"))
        self.assertNotContains(tela, "Chamar esta")

    def test_chamar_manual_chama_a_senha_escolhida_fora_da_ordem(self):
        self._dar("fila_ver_aguardando", "fila_chamar_senha", "fila_chamar_manual")
        self.assertContains(self.client.get(reverse("fila:painel_chamada")), "Chamar esta")
        resposta = self.client.post(reverse("fila:chamar_manual"), {"senha_id": self.segunda.pk})
        self.assertRedirects(resposta, reverse("fila:painel_chamada"))
        self.segunda.refresh_from_db()
        self.primeira.refresh_from_db()
        self.assertEqual(self.segunda.status, Senha.Status.CHAMADA)
        self.assertEqual(self.segunda.guiche, self.guiche)
        self.assertEqual(self.segunda.chamado_por, self.usuario)
        self.assertEqual(self.primeira.status, Senha.Status.AGUARDANDO)
        self.assertTrue(self.LogAuditoria.objects.filter(acao__contains="manualmente a senha N002").exists())

        # Com uma senha em atendimento, não chama outra antes de concluir.
        self.client.post(reverse("fila:chamar_manual"), {"senha_id": self.primeira.pk})
        self.primeira.refresh_from_db()
        self.assertEqual(self.primeira.status, Senha.Status.AGUARDANDO)

    def test_chamar_manual_exige_a_permissao(self):
        self._dar("fila_ver_aguardando", "fila_chamar_senha")
        resposta = self.client.post(reverse("fila:chamar_manual"), {"senha_id": self.segunda.pk})
        self.assertEqual(resposta.status_code, 403)
        self.segunda.refresh_from_db()
        self.assertEqual(self.segunda.status, Senha.Status.AGUARDANDO)

    def test_senha_ja_chamada_nao_e_chamada_de_novo_pelo_manual(self):
        self._dar("fila_ver_aguardando", "fila_chamar_senha", "fila_chamar_manual")
        outro = Usuario.objects.create_user(username="outro", password="x", exigir_troca_senha=False)
        Senha.chamar_especifica(outro, self.guiche, self.segunda.pk)
        self.client.post(reverse("fila:chamar_manual"), {"senha_id": self.segunda.pk})
        self.segunda.refresh_from_db()
        self.assertEqual(self.segunda.chamado_por, outro)
