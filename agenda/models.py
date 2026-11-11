"""
Modelos da Etapa 4 — Agendamento.

Decisões confirmadas com o cliente:
- Agenda é por PROFISSIONAL (não por sala/recurso).
- Duração é flexível por atendimento (não é fixa por procedimento) — e só existe
  para SESSÃO; consulta/exame não têm hora marcada nem duração: entram na lista do
  turno do profissional, na ordem de agendamento (ver Agendamento.eh_sessao).
- Recepção cria/edita agendamentos; o profissional só vê a própria agenda e
  registra a realização do atendimento (permissões já existentes desde a
  Etapa 2: agenda_criar, agenda_editar, agenda_ver_propria, agenda_ver_dia,
  atendimento_realizar).
- Sistema IMPEDE marcar duas SESSÕES que se sobreponham para o mesmo
  profissional (ver Agendamento.verificar_conflito_horario()).
- Atendimento em grupo (turmas fixas, vários profissionais) fica para depois
  — por ora, cada linha de Agendamento é sempre de UM paciente.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Exists, OuterRef, Q, Sum
from django.utils import timezone

from cadastros.models import ESPECIALIDADES_SAUDE, Convenio, HorarioAtendimento, especialidade_rotulo
from fila.models import Senha
from accounts.moeda import formatar_moeda

# Consulta/exame não têm hora marcada (pedido da clínica, 13/10/2026): o paciente é agendado
# num TURNO do profissional e atendido na ordem em que foi agendado. Rótulos curtos dos
# turnos de cadastros.models.HorarioAtendimento.Turno, usados no formulário e nas listas.
TURNO_CHOICES = [
    (HorarioAtendimento.Turno.MANHA, "Manhã"),
    (HorarioAtendimento.Turno.TARDE, "Tarde"),
    (HorarioAtendimento.Turno.NOITE, "Noite"),
]


def turno_da_hora(hora):
    """Turno em que cai um horário — mesma divisão dos horários padrão de
    HorarioAtendimento.HORARIO_PADRAO_POR_TURNO (manhã até 12h, tarde até 18h)."""
    if hora.hour < 12:
        return HorarioAtendimento.Turno.MANHA
    if hora.hour < 18:
        return HorarioAtendimento.Turno.TARDE
    return HorarioAtendimento.Turno.NOITE


class Agendamento(models.Model):
    class Status(models.TextChoices):
        AGENDADO = "AGENDADO", "Agendado"
        # Recepção ligou 1-2 dias antes pra confirmar se o paciente vem.
        PRE_CONFIRMADO = "PRE_CONFIRMADO", "Pré-confirmado"
        # Paciente confirmou por telefone, ou chegou no dia do atendimento.
        CONFIRMADO = "CONFIRMADO", "Confirmado"
        # A partir daqui o caminho depende da forma de atendimento (ver
        # agenda.views.agendamento_mudar_status): atendimento PARTICULAR, ou
        # CONVÊNIO com Convenio.permite_pagamento_na_hora quando a recepção
        # escolhe "pagar na hora", só pode ir pra PRE_AUTORIZADO — recepção
        # encaminhou pro financeiro, que registra o pagamento (ou cortesia, ver
        # PagamentoParticular) na tela agendamento_confirmar_pagamento, o que
        # avança o status pra AUTORIZADO automaticamente. Convênio "normal" (ou
        # convênio-com-pagamento-na-hora quando a recepção escolhe faturar pelo convênio)
        # pula direto de CONFIRMADO pra AUTORIZADO, sem passar pelo financeiro.
        PRE_AUTORIZADO = "PRE_AUTORIZADO", "Pré-autorizado"
        # Exigido antes do profissional poder marcar "atendido" (Realizado),
        # pra qualquer forma de atendimento.
        AUTORIZADO = "AUTORIZADO", "Autorizado"
        REALIZADO = "REALIZADO", "Realizado"
        CANCELADO = "CANCELADO", "Cancelado"
        FALTOU = "FALTOU", "Faltou"

    # Situações que contam como PRODUÇÃO (financeiro, repasses, lotes, desempenho,
    # direito a retorno grátis). Decisão do cliente (2026-09-24): Autorizado já conta,
    # sem esperar o profissional marcar "atendido" — nem todo profissional marca, e a
    # produção dele ficava de fora. Realizado continua existindo só como registro de
    # que o profissional clicou em "atendido".
    STATUS_PRODUCAO = (Status.AUTORIZADO, Status.REALIZADO)
    # Cancelar em dois critérios (pedido da clínica, 23/10/2026): antes da autorização é
    # cancelamento de AGENDA (agenda.views.agendamento_cancelar, permissão agenda_editar);
    # depois de autorizado, só pela tela Autorizações e só pra quem tem
    # agenda_cancelar_autorizado (agenda.views.agendamento_cancelar_autorizado).
    STATUS_JA_AUTORIZADO = STATUS_PRODUCAO

    @staticmethod
    def filtro_pago_como_particular(prefixo=""):
        """Q() de atendimento cujo pagamento na clínica segue a regra do particular (repasse
        semanal ao profissional, caixa do proprietário): particular, ou convênio marcado
        "funciona como particular" (cadastros.models.Convenio.funciona_como_particular — pedido
        da clínica, 23/10/2026; outros convênios também recebem na hora, mas não repassam
        assim). Usado nas consultas de PagamentoParticular com prefixo="agendamento__"."""
        return models.Q(**{f"{prefixo}forma_atendimento": Agendamento.FormaAtendimento.PARTICULAR}) | models.Q(
            **{
                f"{prefixo}forma_atendimento": Agendamento.FormaAtendimento.CONVENIO,
                f"{prefixo}convenio__funciona_como_particular": True,
            }
        )

    @staticmethod
    def filtro_convenio_a_faturar(prefixo=""):
        """Q() do atendimento por CONVÊNIO que a clínica fatura ao plano — exclui o de convênio
        "funciona como particular" que foi pago na clínica (tem PagamentoParticular): o plano não
        paga nada por ele, o dinheiro entrou aqui e foi pro repasse semanal (ver
        filtro_pago_como_particular). Sai de Contas a receber, lotes e repasse de convênio, senão
        contaria em dobro. `prefixo` como em filtro_retorno (ex.: "agendamento__"). Negar a
        relação reversa no filter() vira subconsulta (NOT IN), sem duplicar linhas."""
        return models.Q(**{f"{prefixo}forma_atendimento": Agendamento.FormaAtendimento.CONVENIO}) & (
            ~models.Q(**{f"{prefixo}convenio__funciona_como_particular": True})
            | ~models.Q(**{f"{prefixo}pagamentos__isnull": False})
        )

    @property
    def convenio_pago_como_particular(self):
        """Convênio "funciona como particular" pago na clínica (ver filtro_convenio_a_faturar)."""
        return (
            self.forma_atendimento == self.FormaAtendimento.CONVENIO
            and self.convenio_id is not None
            and self.convenio.funciona_como_particular
            and self.pagamentos.exists()
        )

    class FormaAtendimento(models.TextChoices):
        CONVENIO = "CONVENIO", "Convênio"
        PARTICULAR = "PARTICULAR", "Particular"

    class FormaPagamento(models.TextChoices):
        DINHEIRO = "DINHEIRO", "Dinheiro"
        CARTAO_DEBITO = "CARTAO_DEBITO", "Cartão débito"
        CARTAO_CREDITO = "CARTAO_CREDITO", "Cartão crédito"
        PIX = "PIX", "Pix"
        # Profissional aceitou atender sem cobrar (paciente amigo, etc.) — não gera
        # cobrança nem obrigação de repasse (ver PagamentoParticular abaixo e os filtros
        # de agenda.views.relatorio_repasses_semanal / financeiro.views.painel_financeiro,
        # que excluem CORTESIA das somas de repasse pendente).
        CORTESIA = "CORTESIA", "Cortesia (sem cobrança)"

    paciente = models.ForeignKey(
        "cadastros.Paciente", verbose_name="paciente", on_delete=models.PROTECT, related_name="agendamentos"
    )
    profissional = models.ForeignKey(
        "cadastros.Profissional", verbose_name="profissional", on_delete=models.PROTECT, related_name="agendamentos"
    )
    # Qual das especialidades do profissional vale para ESTE atendimento — só é
    # ambíguo quando ele tem 2+ (ver cadastros.models.Profissional.especialidades_pares).
    # Preenchido sozinho em save() quando não há ambiguidade; AgendamentoForm.clean()
    # (agenda/forms.py) exige a escolha explícita quando o profissional tem 2+. É esta
    # especialidade (não mais "a" especialidade do profissional) que vale para o retorno
    # grátis (AgendamentoItem.buscar_item_para_retorno) e para os relatórios financeiros
    # agrupados por especialidade — precisa disso porque um mesmo profissional
    # multiespecialidade pode atender ora "como" uma, ora "como" outra.
    especialidade = models.CharField(
        "especialidade do atendimento", max_length=50, choices=ESPECIALIDADES_SAUDE, blank=True,
    )
    especialidade_outra = models.CharField("qual especialidade (se 'Outra')", max_length=100, blank=True)
    # LEGADO — substituído por AgendamentoItem (abaixo), que permite mais de um
    # procedimento no mesmo agendamento. Mantido só durante a transição: o backfill
    # de dados lê este campo pra popular AgendamentoItem, mas nenhum código novo lê
    # ou escreve aqui. Sai do schema numa migração de contração separada, depois de
    # validar AgendamentoItem em produção por um ciclo.
    procedimento = models.ForeignKey(
        "cadastros.Procedimento",
        verbose_name="procedimento (legado)",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agendamentos",
    )

    forma_atendimento = models.CharField(
        "forma de atendimento",
        max_length=10,
        choices=FormaAtendimento.choices,
        default=FormaAtendimento.PARTICULAR,
        help_text="O paciente pode ter convênio cadastrado e ainda assim atender particular numa consulta específica.",
    )
    convenio = models.ForeignKey(
        "cadastros.Convenio",
        verbose_name="convênio usado neste atendimento",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agendamentos",
    )

    data_hora_inicio = models.DateTimeField("data/hora de início")
    data_hora_fim = models.DateTimeField("data/hora de término")

    status = models.CharField("situação", max_length=14, choices=Status.choices, default=Status.AGENDADO)
    observacoes = models.TextField("observações", blank=True)

    # Item confirmado com o cliente: "encaixe" é uma exceção deliberada de
    # agenda — permite marcar um atendimento mesmo sobrepondo o horário de
    # outro já existente para o mesmo profissional (ver clean() abaixo, que
    # pula a checagem de conflito quando encaixe=True). Sempre exige uma
    # observação explicando o motivo do encaixe (ver AgendamentoForm.clean()
    # em agenda/forms.py).
    encaixe = models.BooleanField(
        "encaixe (exceção de horário)",
        default=False,
        help_text="Marque para agendar um paciente extra fora do horário normal, mesmo que sobreponha outro atendimento já marcado.",
    )

    # O pagamento particular em si (valor + forma, podendo ser até 2 formas por
    # atendimento) mora no model PagamentoParticular, abaixo — cada linha tem o
    # próprio controle de repasse (necessário porque dinheiro é repassado ao
    # profissional no mesmo dia, as demais formas "no decorrer da semana" — ver
    # agenda.views.relatorio_repasses_semanal — e cortesia não gera repasse
    # nenhum: uma parte do pagamento pode já estar repassada enquanto outra
    # parte do MESMO atendimento ainda está pendente). Convênio não usa esse
    # model (o repasse de convênio segue o prazo configurado em Convenio, à
    # parte) — ver `pagamentos` (related_name) e a property `valor_cobrado` logo abaixo.

    # Contas a receber de CONVÊNIO (módulo Financeiro — ver financeiro.views).
    # Preenchido automaticamente assim que o agendamento é Autorizado (STATUS_PRODUCAO), quando há um preço
    # configurado para o convênio (ver cadastros.models.Convenio.valor_para e
    # agenda.views.agendamento_mudar_status) — quando não há preço configurado (ex.: convênio
    # que calcula por desconto sobre a tabela particular, que ainda não existe no sistema),
    # fica em branco e é lançado manualmente na tela Financeiro → Contas a receber.
    valor_convenio = models.DecimalField(
        "valor a receber do convênio", max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="Só se aplica a atendimento por convênio — calculado assim que o atendimento é Autorizado, quando há preço configurado.",
    )
    convenio_recebido = models.BooleanField("pagamento do convênio recebido", default=False)
    convenio_recebido_em = models.DateTimeField("convênio recebido em", null=True, blank=True)

    # Repasse ao PROFISSIONAL da parte de convênio (financeiro.views.repasse_convenio_lista) —
    # diferente de convenio_recebido acima (que é convênio → clínica). Decisão do cliente: aqui
    # não existe "aguardando convênio" (a tela sempre trata o convênio como já recebido), só
    # "a repassar" (False) x "repassado" (True) — e a ação de marcar como repassado sempre paga
    # de uma vez o valor completo pendente do profissional no período, nunca convênio por convênio.
    convenio_repasse_pago = models.BooleanField("repasse ao profissional pago (convênio)", default=False)
    convenio_repasse_pago_em = models.DateTimeField("repasse ao profissional pago em (convênio)", null=True, blank=True)

    # Preenchido automaticamente só quando o agendamento é criado com
    # "repetir semanalmente" (ver agenda.views.agendamento_novo/
    # _criar_serie_semanal) — todas as sessões nascidas da mesma repetição
    # compartilham o mesmo identificador aqui. É o que permite, ao cancelar
    # uma sessão, oferecer a opção de cancelar "só esta" ou "esta e todas as
    # próximas da mesma repetição" (ver agendamento_cancelar). Fica de fora
    # do formulário — é gerado sozinho, nunca escolhido pelo usuário.
    serie_recorrencia = models.UUIDField(
        "identificador da série de repetição", null=True, blank=True, editable=False, db_index=True,
    )

    # LEGADO — o retorno grátis agora é calculado por ITEM (ver
    # AgendamentoItem.retorno_de/eh_retorno/buscar_item_para_retorno), não mais por
    # agendamento inteiro: um agendamento com Consulta + Eletrocardiograma só pode ter
    # a Consulta elegível a retorno, nunca o ECG junto. Mantido só durante a
    # transição — o backfill de dados religa este vínculo em AgendamentoItem.retorno_de
    # (2ª passada da migração de dados), mas nenhum código novo lê ou escreve aqui.
    # Sai do schema na mesma migração de contração que remove 'procedimento' acima.
    retorno_de = models.ForeignKey(
        "self",
        verbose_name="retorno grátis da consulta (legado)",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="retornos",
    )

    criado_por = models.ForeignKey(
        "accounts.Usuario",
        verbose_name="criado por",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agendamentos_criados",
    )
    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    # Chamada do paciente pelo profissional (dispensa a recepção nessa etapa —
    # ver agenda.views.agendamento_chamar, permissão 'fila_chamar_senha').
    # 'autorizado_em' existe só pra isso: Meta.ordering é por 'data_hora_inicio'
    # (hora agendada), que não é a mesma coisa que a ordem em que cada paciente
    # foi de fato liberado pelo financeiro/recepção — sem este campo não dá pra
    # respeitar a ordem de autorização real. Gravado nos dois pontos que levam a
    # AUTORIZADO (agendamento_mudar_status e agendamento_confirmar_pagamento),
    # nunca no caminho de "Desmarcar" (REALIZADO -> AUTORIZADO de novo).
    autorizado_em = models.DateTimeField("autorizado em", null=True, blank=True)
    # Quando a recepção encaminhou pro financeiro (Confirmado -> Pré-autorizado). Pedido da
    # clínica (23/10/2026): quem vai pagar não pode perder a vez pra um convênio autorizado
    # enquanto espera no caixa — a ordem de chamada usa este horário (ver momento_na_fila).
    # Gravado só na 1ª vez: reencaminhar não joga o paciente pro fim.
    encaminhado_pagamento_em = models.DateTimeField("encaminhado para pagamento em", null=True, blank=True)
    # Pedido da clínica (29/10/2026): na fila do profissional, prioritário passa na frente de
    # normal já autorizado, alternando 1 prioritário e 1 normal (ver agenda.views.
    # _ordena_fila_de_chamada). A senha do totem não fica ligada ao agendamento, então a
    # recepção marca o motivo ao autorizar/encaminhar — mesmas opções da tela de prioridade do
    # totem (fila.models.Senha.ORDEM_PRIORIDADE). Em branco = normal.
    motivo_prioridade = models.CharField(
        "prioridade", max_length=30, blank=True, default="",
        choices=[(motivo.value, motivo.label) for motivo in Senha.ORDEM_PRIORIDADE],
    )
    # Registro da autorização (pedido da clínica, 15/10/2026 — igual à tela "Autorizações" do
    # sistema antigo): quem autorizou, uma observação livre (ex.: "AUT. NO CRM DENNIS") e, pra
    # consulta/exame por convênio, o número da guia. Sessão por convênio usa a guia de sessões
    # do plano (AgendamentoItem.guia_convenio / GuiaConvenio), com controle de saldo.
    autorizado_por = models.ForeignKey(
        "accounts.Usuario",
        verbose_name="autorizado por",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agendamentos_autorizados",
    )
    observacao_autorizacao = models.TextField("observação da autorização", blank=True)
    numero_guia = models.CharField("número da guia", max_length=40, blank=True)
    # Desconto dado no registro do pagamento (pedido da clínica, 15/10/2026 — o profissional
    # às vezes pede para dar desconto). O valor cobrado de cada item já sai com o desconto
    # repartido (AgendamentoItem.valor_cobrado), e o repasse segue o valor pago; para quem
    # cobra valor fixo, o repasse acordado é informado na hora (ver
    # agenda.views.agendamento_confirmar_pagamento).
    desconto = models.DecimalField("desconto (R$)", max_digits=8, decimal_places=2, null=True, blank=True)
    motivo_desconto = models.CharField("motivo do desconto", max_length=200, blank=True)
    # SET_NULL (não CASCADE) e related_name diferente de fila.Senha.chamado_por
    # (related_name 'senhas_chamadas') pra não colidir o acessor reverso em Usuario —
    # mesmo padrão/motivo já usado lá.
    chamado_por = models.ForeignKey(
        "accounts.Usuario",
        verbose_name="chamado por",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agendamentos_chamados",
    )
    chamado_em = models.DateTimeField("chamado em", null=True, blank=True)
    # Congela a sala no momento da chamada (via Profissional.sala_em) — o
    # profissional pode trocar de sala no meio do dia (AtendimentoSala permite
    # mais de uma escolha por dia, vale a mais recente), então uma referência
    # "ao vivo" reescreveria retroativamente qual sala chamou cada paciente no
    # histórico/painel público. PROTECT: mesmo motivo de AtendimentoSala.sala e
    # fila.Senha.guiche — não apagar o registro do atendimento se a sala for
    # excluída do cadastro depois.
    sala_chamada = models.ForeignKey(
        "cadastros.Sala",
        verbose_name="consultório da chamada",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="agendamentos_chamados",
    )

    # Chamada em lote pra "recepção própria" da sala (cadastros.models.Sala.
    # lugares_recepcao_propria, decisão da clínica de 2026-09-19): o profissional chama até N
    # pacientes de uma vez pra Recepção 2 — o de menor `ordem_no_lote` (entre os que ainda
    # estão Autorizados) entra no consultório e os demais esperam sentados. Todos os pacientes
    # da mesma leva compartilham `lote_recepcao`, o que permite ao painel de espera mostrar a
    # leva como UMA chamada só (ver fila.views.painel_espera) em vez de N chamadas soltas. Uma
    # nova chamada enquanto ainda há gente na recepção entra no MESMO lote (continua a
    # numeração), pra a ordem de entrada no consultório nunca se perder. Só a chamada em lote
    # preenche estes campos — a chamada individual (agendamento_chamar) não os toca.
    lote_recepcao = models.UUIDField("lote de chamada da recepção própria", null=True, blank=True, editable=False)
    ordem_no_lote = models.PositiveSmallIntegerField("ordem no lote", null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "Agendamento"
        verbose_name_plural = "Agendamentos"
        # "pk" desempata: consulta/exame do mesmo turno têm o mesmo data_hora_inicio (o
        # início do turno) e são atendidos na ordem em que foram agendados.
        ordering = ["data_hora_inicio", "pk"]

    @property
    def guias_pendentes(self):
        """Guias de sessão (2ª em diante) ainda não autorizadas neste atendimento — ver
        AgendamentoItem.controla_guias. Usa self.itens.all() (aproveita prefetch)."""
        return sum(item.guias_pendentes for item in self.itens.all())

    def __str__(self):
        return f"{self.paciente.nome_completo} — {self.profissional.nome} ({self.data_hora_inicio:%d/%m/%Y %H:%M})"

    # Só SESSÃO (procedimento com permite_repeticao_semanal) tem horário marcado e duração
    # (pedido da clínica, 13/10/2026). Consulta/exame não têm hora marcada: o profissional
    # atende 20/25 pacientes num turno, na ordem de agendamento — gravam data_hora_inicio =
    # início do turno e data_hora_fim = data_hora_inicio, e não entram na checagem de conflito.
    # Não é campo do banco: quem monta o agendamento já sabendo os itens marca aqui
    # (AgendamentoItemFormSet.clean, agenda.views._criar_serie_semanal); None = descobre
    # pelos itens já gravados (ver eh_sessao_resolvido).
    eh_sessao = None
    # Ligado por AgendamentoForm: os itens ainda não foram validados quando o ModelForm
    # chama full_clean(), então a checagem de conflito fica pro formset (que chama
    # verificar_conflito_horario() já sabendo se é sessão).
    adiar_checagem_conflito = False

    @property
    def eh_sessao_resolvido(self):
        if self.eh_sessao is not None:
            return self.eh_sessao
        if self.pk:
            return self.itens.filter(procedimento__permite_repeticao_semanal=True).exists()
        return False

    @property
    def tem_horario_fim(self):
        """False para consulta/exame (sem hora marcada) — ver sem_hora_marcada."""
        return bool(self.data_hora_fim and self.data_hora_inicio and self.data_hora_fim > self.data_hora_inicio)

    @property
    def sem_hora_marcada(self):
        """Consulta/exame: entra na lista do turno, na ordem de agendamento (as telas mostram
        o turno e a posição na lista em vez de um horário)."""
        return not self.tem_horario_fim

    @property
    def turno(self):
        return turno_da_hora(timezone.localtime(self.data_hora_inicio))

    @property
    def turno_rotulo(self):
        return dict(TURNO_CHOICES)[self.turno]

    @property
    def horario_exibicao(self):
        """Texto do horário nas telas: 'Manhã · 3º' (consulta/exame — turno e posição na
        lista, quando numerar_ordem_lista já rodou) ou '08:00 – 08:30' (sessão)."""
        if self.sem_hora_marcada:
            ordem = getattr(self, "ordem_lista", None)
            return f"{self.turno_rotulo} · {ordem}º" if ordem else self.turno_rotulo
        return f"{timezone.localtime(self.data_hora_inicio):%H:%M} – {timezone.localtime(self.data_hora_fim):%H:%M}"

    @staticmethod
    def numerar_ordem_lista(agendamentos):
        """Grava 'ordem_lista' (1, 2, 3...) em cada agendamento SEM hora marcada da lista
        recebida, por profissional e turno, na ordem de agendamento (pk). Cancelados não
        recebem número nem ocupam posição. Trabalha nos próprios objetos (sem consulta
        extra) — a lista precisa trazer o dia inteiro do profissional pra numeração bater."""
        contagem = {}
        for agendamento in sorted(agendamentos, key=lambda a: a.pk):
            agendamento.ordem_lista = None
            if not agendamento.sem_hora_marcada or agendamento.status == Agendamento.Status.CANCELADO:
                continue
            chave = (agendamento.profissional_id, agendamento.data_hora_inicio)
            contagem[chave] = contagem.get(chave, 0) + 1
            agendamento.ordem_lista = contagem[chave]
        return agendamentos

    def verificar_conflito_horario(self):
        """Barra sobreposição entre SESSÕES do mesmo profissional (consulta/exame não têm
        duração e não contam — ver eh_sessao). Respeita encaixe e o limite de atendimentos
        simultâneos do profissional."""
        # "Encaixe" é uma exceção deliberada de agenda (confirmado com o cliente): pula
        # completamente a checagem de conflito de horário abaixo para ESTE agendamento —
        # ele pode sobrepor outro atendimento já marcado do mesmo profissional de propósito.
        if self.encaixe or not (self.profissional_id and self.data_hora_inicio and self.data_hora_fim):
            return
        if not self.eh_sessao_resolvido:
            return
        conflitos = (
            Agendamento.objects.filter(
                profissional_id=self.profissional_id,
                data_hora_inicio__lt=self.data_hora_fim,
                data_hora_fim__gt=self.data_hora_inicio,
                itens__procedimento__permite_repeticao_semanal=True,
            )
            .exclude(status=self.Status.CANCELADO)
            .distinct()
            .order_by("data_hora_inicio")
        )
        if self.pk:
            conflitos = conflitos.exclude(pk=self.pk)
        sobrepostos = list(conflitos)
        # Alguns profissionais atendem mais de um paciente ao mesmo tempo — ex: fisioterapeuta
        # com várias pessoas na sala de ginástica/aparelhos, ou acupuntura com um paciente
        # "descansando" com as agulhas aplicadas enquanto outro já é atendido numa consulta
        # (ver Profissional.limite_atendimento_simultaneo, configurável por profissional —
        # 1 é o padrão/caso normal, sem sobreposição).
        limite_simultaneo = self.profissional.limite_atendimento_simultaneo
        if len(sobrepostos) >= limite_simultaneo:
            outro = sobrepostos[0]
            raise ValidationError(
                f"Conflito de agenda: {self.profissional.nome} já está no limite de sessões "
                f"simultâneas nesse horário (conflita com a sessão de "
                f"{timezone.localtime(outro.data_hora_inicio):%d/%m/%Y %H:%M} às "
                f"{timezone.localtime(outro.data_hora_fim):%H:%M})."
            )

    def clean(self):
        if self.data_hora_inicio and self.data_hora_fim and self.data_hora_fim < self.data_hora_inicio:
            raise ValidationError("O horário de término deve ser depois do horário de início.")

        # A clínica funciona apenas de segunda a sexta (weekday(): Segunda=0 ... Domingo=6).
        if self.data_hora_inicio and self.data_hora_inicio.weekday() >= 5:
            raise ValidationError(
                "A clínica não funciona aos sábados e domingos — escolha um dia de segunda a sexta."
            )

        if not self.adiar_checagem_conflito:
            self.verificar_conflito_horario()

        # Não existe mais um bloqueio de "particular sem pagamento" aqui no
        # model: o controle de pagamento virou uma etapa própria e explícita
        # do fluxo (PRE_AUTORIZADO → registra pagamento → AUTORIZADO, ver
        # Status acima e agenda.views.agendamento_mudar_status/
        # agendamento_confirmar_pagamento) — só existe UM caminho de código
        # que sai de PRE_AUTORIZADO, e ele sempre registra o pagamento (ou
        # cortesia) antes de avançar, então não precisa reforçar aqui.

    def save(self, *args, **kwargs):
        # Preenche a especialidade do atendimento sozinho quando não há ambiguidade
        # (profissional com 0 ou 1 especialidade) — mesmo espírito do auto-preenchimento
        # de cbo_codigo em cadastros.models.Profissional.save(). Cobre qualquer caminho de
        # criação (form, admin, ORM direto, testes), não só AgendamentoForm. Quando o
        # profissional tem 2+ especialidades e nada foi informado, fica em branco aqui —
        # exigir a escolha nesse caso é responsabilidade do form, não do model.
        if not self.especialidade and self.profissional_id:
            pares = self.profissional.especialidades_pares
            if len(pares) == 1:
                self.especialidade, self.especialidade_outra = pares[0]
        # Decisão do cliente (2026-10-16): no dia do atendimento, todo "Agendado" vira
        # "Pré-confirmado" sozinho — inclusive encaixe marcado no próprio dia, que não
        # pega a varredura diária (atualizar_status_do_dia, abaixo).
        if (
            self.status == self.Status.AGENDADO
            and self.data_hora_inicio
            and timezone.localtime(self.data_hora_inicio).date() == timezone.localdate()
        ):
            self.status = self.Status.PRE_CONFIRMADO
            if kwargs.get("update_fields") is not None and "status" not in kwargs["update_fields"]:
                kwargs["update_fields"] = list(kwargs["update_fields"]) + ["status"]
        super().save(*args, **kwargs)

    @property
    def momento_na_fila(self):
        """Quando o paciente entrou na fila de atendimento: o encaminhamento pro pagamento
        (particular guarda a vez desde então), senão a autorização, senão a hora marcada."""
        return self.encaminhado_pagamento_em or self.autorizado_em or self.data_hora_inicio

    @property
    def prioritario(self):
        return bool(self.motivo_prioridade)

    @property
    def ja_autorizado(self):
        return self.status in self.STATUS_JA_AUTORIZADO

    @property
    def plano_exibicao(self):
        """Plano com que ESTE atendimento foi marcado/autorizado (pode diferir do convênio do
        cadastro do paciente — ver forma_atendimento). Mostrado no prontuário."""
        if self.forma_atendimento == self.FormaAtendimento.CONVENIO:
            return f"Convênio — {self.convenio.nome}" if self.convenio_id else "Convênio (não informado)"
        return "Particular"

    @property
    def especialidade_exibicao(self):
        """Nome da especialidade deste atendimento pronto para mostrar na tela (considera
        a opção 'Outra') — ver o campo 'especialidade' acima."""
        return especialidade_rotulo(self.especialidade, self.especialidade_outra)

    @property
    def valor_cobrado(self):
        """Total cobrado no atendimento particular — soma das formas de pagamento já
        registradas (ver PagamentoParticular), EXCLUINDO as que foram estornadas (o
        valor foi devolvido ao paciente, não conta mais como cobrado). None se ainda
        não há nenhuma registrada (cortesia soma 0, então deixa de ser None assim que
        registrada); também pode ser None se a única linha registrada foi estornada —
        ver valor_estornado para saber se foi esse o caso."""
        if not self.pk:
            return None
        return self.pagamentos.exclude(estornado=True).aggregate(total=models.Sum("valor"))["total"]

    @property
    def valor_estornado(self):
        """Total já devolvido ao paciente neste atendimento (soma das linhas de
        PagamentoParticular com estornado=True). Decimal("0.00") quando não há nenhuma
        (diferente de valor_cobrado, que usa None de propósito — aqui não há a mesma
        ambiguidade a preservar)."""
        if not self.pk:
            return Decimal("0.00")
        return self.pagamentos.filter(estornado=True).aggregate(total=models.Sum("valor"))["total"] or Decimal("0.00")

    @property
    def valor_a_cobrar(self):
        """
        Quanto deve ser cobrado neste atendimento — soma de AgendamentoItem.valor_a_cobrar
        de cada item (cada um calculado automaticamente a partir do catálogo, nunca
        digitado à mão — pedido do cliente: evita cobrar um valor errado por digitação).
        None quando não há nenhum item, ou quando QUALQUER item não tem preço calculável
        (cadastro incompleto) — mesmo comportamento de bloqueio de hoje (a tela de
        pagamento não avança sem um valor certo), só que agora um único item incompleto
        já invalida o total inteiro, não só aquele item.
        """
        itens = list(self.itens.all())
        if not itens:
            return None
        total = Decimal("0.00")
        for item in itens:
            valor_item = item.valor_a_cobrar
            if valor_item is None:
                return None
            total += valor_item
        # Pacote vendido NESTE atendimento (ver PacoteVendido): o preço fechado do pacote
        # inteiro entra aqui, e os itens cobertos por ele já saem R$ 0,00 acima.
        if self.pk:
            total += sum((venda.valor for venda in self.pacotes_vendidos.all()), Decimal("0.00"))
        return total

    @property
    def repasse_fixo_padrao(self):
        """Repasse fixo total deste atendimento pelo cadastro (itens + pacote vendido aqui) —
        None quando o profissional não cobra valor fixo (repasse por percentual) ou falta valor
        na tabela dele. É a sugestão mostrada quando há desconto no pagamento."""
        itens = list(self.itens.all())
        if not itens:
            return None
        repasses = [item.valor_repasse_fixo_a_calcular for item in itens]
        if self.pk:
            repasses += [venda.valor_repasse_fixo for venda in self.pacotes_vendidos.all()]
        if any(valor is None for valor in repasses):
            return None
        return sum(repasses, Decimal("0.00"))

    @property
    def tem_item_retorno(self):
        """True se PELO MENOS UM item deste agendamento é retorno grátis (ver
        AgendamentoItem.eh_retorno) — uso: selos/avisos informativos (ex: badge de
        'Retorno' em agenda_dia.html). Ver tem_item_cobravel para a pergunta
        complementar: com múltiplos itens, 'todo mundo é retorno' e 'ninguém é retorno'
        deixaram de ser as únicas duas respostas possíveis."""
        return self.itens.filter(AgendamentoItem.filtro_retorno()).exists()

    @property
    def tem_item_cobravel(self):
        """True se PELO MENOS UM item deste agendamento NÃO é retorno grátis — ou seja,
        ainda existe algo a cobrar neste atendimento (mesmo que outro item do mesmo
        agendamento seja grátis). Usado por ConfirmarPagamentoForm e
        relatorio_detalhado.html para decidir se mostram os campos/valores de
        pagamento, no lugar do antigo 'eh_retorno' de agendamento inteiro. Item coberto por
        pacote (ver PacoteVendido) também não é cobrável — mas a VENDA de um pacote neste
        atendimento é."""
        if self.pk and self.pacotes_vendidos.exists():
            return True
        return self.itens.filter(AgendamentoItem.filtro_nao_retorno(), pacote_vendido__isnull=True).exists()

    @property
    def tem_item_pacote(self):
        """True se pelo menos um item deste atendimento é coberto por um pacote (ver
        PacoteVendido) — selo informativo nas listagens."""
        return any(item.pacote_vendido_id for item in self.itens.all())

    @property
    def so_retorno_gratis(self):
        """Nada a cobrar e pelo menos um item é retorno grátis — automático ou do sistema
        antigo (AgendamentoItem.filtro_retorno). Pedido da clínica (23/10/2026): retorno não
        passa pelo financeiro, é autorizado direto ao encaminhar (ver agendamento_mudar_status)."""
        return not self.tem_item_cobravel and self.itens.filter(AgendamentoItem.filtro_retorno()).exists()

    @property
    def eh_acupuntura(self):
        """Algum procedimento deste atendimento é acupuntura (pelo nome). Pedido da clínica
        (06/11/2026): na Minha agenda do profissional com
        Profissional.acupuntura_primeiro_na_fila, acupuntura vai pro começo da fila — ele
        aplica as agulhas e, enquanto o paciente fica na sessão, atende consultas/ultrassons
        (ver agenda.views._ordena_fila_de_chamada). Usa itens.all() pra aproveitar o
        prefetch_related das telas."""
        return any("acupunt" in (item.procedimento.nome or "").lower() for item in self.itens.all())

    @property
    def coberto_por_pacote(self):
        """Nada a cobrar porque os itens já foram pagos num pacote (os demais, se houver, são
        retorno grátis) — este atendimento não passa pelo financeiro nem gera repasse de novo
        (ver agenda.views.agendamento_mudar_status)."""
        return self.tem_item_pacote and not self.tem_item_cobravel

    @property
    def usa_pacote_a_pagar(self):
        """Algum item usa um pacote do sistema antigo que o paciente ainda não pagou (ver
        PacoteVendido.pagamento_pendente) — o atendimento passa pelo financeiro mesmo coberto,
        pra ele poder cobrar o pacote (ou só confirmar)."""
        return any(item.pacote_vendido_id and item.pacote_vendido.pagamento_pendente for item in self.itens.all())

    @property
    def tem_exame_com_preparo(self):
        """True se PELO MENOS UM item deste agendamento é um exame com preparo (ver
        cadastros.models.Procedimento.exame_com_preparo) — só pro selo "Exame com
        preparo" na Minha agenda (desde 2026-10-26 não muda mais a ordem da fila). Itera `itens.all()` (não `.filter()`) pra aproveitar o
        prefetch_related de agenda_propria em vez de uma consulta por linha."""
        return any(item.procedimento.exame_com_preparo for item in self.itens.all())

    @property
    def itens_resumo(self):
        """String pronta pra exibir em listagens (ex: 'Consulta, Eletrocardiograma', ou
        'Sessão x3') — centraliza aqui a formatação usada em agenda_dia.html/
        agenda_propria.html em vez de repetir a junção em cada template."""
        nomes = [str(item) for item in self.itens.all()]
        return ", ".join(nomes) if nomes else "—"

    @property
    def retorno_referencia(self):
        """Agendamento de origem do retorno grátis deste agendamento (o atendimento cuja
        consulta paga deu direito a este retorno), ou None — usado só pra exibir a data
        de referência em tooltips (ex: agenda_dia.html), já que o vínculo em si agora
        mora em AgendamentoItem.retorno_de, não mais direto neste model. Quando há mais
        de um item de retorno no mesmo agendamento (incomum, mas possível), mostra o
        primeiro — é só um auxílio visual, não afeta o cálculo de cobrança."""
        item_retorno = self.itens.filter(retorno_de__isnull=False).select_related("retorno_de__agendamento").first()
        # Retorno marcado à mão (consulta do sistema antigo) não tem agendamento de origem aqui.
        return item_retorno.retorno_de.agendamento if item_retorno else None

    @property
    def carteirinha_vencida_no_atendimento(self):
        """
        True se este atendimento é pelo convênio e a validade da carteirinha do
        paciente já passou na data marcada — vira um alerta (não bloqueia),
        seguindo o campo 'validade_carteirinha' criado no cadastro de Paciente.
        """
        if self.forma_atendimento != self.FormaAtendimento.CONVENIO or not self.paciente.validade_carteirinha:
            return False
        return self.paciente.validade_carteirinha < self.data_hora_inicio.date()

    @classmethod
    def exames_avulsos_agendados_em(cls, data):
        """
        Exames marcados como Procedimento.exame_sem_profissional com pelo menos um
        agendamento de verdade nesta data (não CANCELADO nem FALTOU) — usado pelo totem
        de senha (fila.views.totem_especialidade) e por EspecialidadesDoDiaForm
        (agenda.forms) pra oferecer esses exames mesmo quando Profissional.
        especialidades_ativas_em não inclui a especialidade (o profissional não tem
        horário fixo/exceção cobrindo o dia, mas a equipe ainda realiza o exame — ex:
        colocação de MAPA, sem precisar do cardiologista presente).

        Devolve uma lista de dicts {"procedimento", "especialidade", "especialidade_outra"}
        — um por procedimento distinto agendado hoje (dois pacientes com o mesmo exame no
        mesmo dia geram uma entrada só). 'especialidade'/'especialidade_outra' vêm do
        PRÓPRIO agendamento, não do profissional direto — mesmo motivo de
        AgendamentoItem.buscar_item_para_retorno (um profissional multiespecialidade pode
        atender ora como uma, ora como outra).
        """
        itens = (
            AgendamentoItem.objects.filter(
                procedimento__exame_sem_profissional=True,
                agendamento__data_hora_inicio__date=data,
            )
            .exclude(agendamento__status__in=[cls.Status.CANCELADO, cls.Status.FALTOU])
            .select_related("procedimento", "agendamento")
            .order_by("procedimento__nome")
        )
        vistos = {}
        for item in itens:
            chave = (item.procedimento_id, item.agendamento.especialidade, item.agendamento.especialidade_outra)
            vistos.setdefault(
                chave,
                {
                    "procedimento": item.procedimento,
                    "especialidade": item.agendamento.especialidade,
                    "especialidade_outra": item.agendamento.especialidade_outra,
                },
            )
        return list(vistos.values())

    @classmethod
    def especialidades_com_exame_com_preparo_em(cls, data):
        """
        Chaves "especialidade|especialidade_outra" (mesmo formato do totem, ver
        cadastros.models.especialidade_a_partir_da_chave) das especialidades com pelo menos
        um agendamento nesta data (não CANCELADO nem FALTOU) contendo exame com preparo
        (Procedimento.exame_com_preparo). É o que faz o totem oferecer a senha "Exame com
        preparo" (pedido da clínica, 2026-10-26; ver fila.views.totem_tipo) — só onde há de
        fato um exame desses marcado hoje. Agendamento sem especialidade gravada conta em
        todas as especialidades do profissional.
        """
        agendamentos = (
            cls.objects.filter(
                data_hora_inicio__date=data,
                itens__procedimento__exame_com_preparo=True,
            )
            .exclude(status__in=[cls.Status.CANCELADO, cls.Status.FALTOU])
            .select_related("profissional")
            .prefetch_related("profissional__especialidades_adicionais")
            .distinct()
        )
        chaves = set()
        for agendamento in agendamentos:
            if agendamento.especialidade:
                pares = [(agendamento.especialidade, agendamento.especialidade_outra)]
            else:
                pares = agendamento.profissional.especialidades_pares
            chaves.update(f"{especialidade}|{outra}" for especialidade, outra in pares)
        return chaves


# Unidade em que os convênios pagam uma sessão (fono, fisioterapia, psicologia...): uma
# sessão de 1h é cobrada como 2 — ver AgendamentoItem.quantidade_guias_sessao.
MINUTOS_POR_GUIA_SESSAO = 30


class AgendamentoItem(models.Model):
    """
    Um procedimento dentro de um Agendamento — substitui o antigo FK único
    Agendamento.procedimento (ver comentário 'LEGADO' lá), permitindo mais de um
    procedimento no mesmo horário/atendimento (ex: Consulta + Eletrocardiograma).

    'quantidade' multiplica o valor do item (2x Sessão = 2 × valor unitário) e também
    conta em dobro contra LimiteDiarioProcedimento (ver agenda.views._verificar_limite_diario).

    on_delete=PROTECT em 'procedimento' (diferente do SET_NULL legado em
    Agendamento.procedimento): com vários itens por agendamento, deixar um Procedimento
    excluído orfanar silenciosamente item(ns) de histórico financeiro é um risco maior
    do que era com um FK só por agendamento. Mesmo padrão já usado em
    Agendamento.paciente/profissional — Procedimento tem o mesmo campo 'ativo' de
    soft-delete que Paciente/Profissional já usam; ver cadastros.views.procedimento_excluir,
    que trata ProtectedError e orienta inativar em vez de excluir (mesmo padrão de
    cadastros.views.paciente_excluir).
    """

    agendamento = models.ForeignKey(
        Agendamento, verbose_name="agendamento", on_delete=models.CASCADE, related_name="itens"
    )
    procedimento = models.ForeignKey(
        "cadastros.Procedimento",
        verbose_name="procedimento",
        on_delete=models.PROTECT,
        related_name="itens_agendamento",
    )
    quantidade = models.PositiveIntegerField("quantidade", default=1, validators=[MinValueValidator(1)])
    # Controle de GUIAS de sessão por convênio (pedido da clínica, 13/10/2026): o plano paga
    # a sessão em unidades de MINUTOS_POR_GUIA_SESSAO — uma sessão de 1h é cobrada como 2
    # sessões, cada uma com a sua guia (ver controla_guias abaixo, e
    # quantidade_guias_sessao, que fixa 'quantidade' pela duração). No dia do atendimento o
    # plano só autoriza a 1ª guia — ela vale junto com o próprio atendimento (status
    # Autorizado). As demais são autorizadas depois, no decorrer da semana (ou até antes do
    # atendimento), na tela "Guias pendentes" (agenda.views.guias_pendentes): este campo
    # conta quantas dessas guias EXTRAS já foram autorizadas. A produção (quantidade e valor
    # do convênio) só conta as guias autorizadas — ver quantidade_produzida.
    guias_extras_autorizadas = models.PositiveIntegerField("guias extras autorizadas", default=0)
    # Pedido da clínica (2026-10-16): às vezes a autorização é feita sem o paciente vir assinar
    # a guia. Marca que a 1ª guia (a que vale junto com o atendimento — ou a única, numa
    # consulta/avaliação) foi autorizada SEM ASSINATURA; as guias extras guardam o mesmo em
    # GuiaExtraAutorizada.sem_assinatura. Aparece em "Guias pendentes" até alguém marcar
    # "Assinou". Não muda produção nem valor — é só controle do que falta assinar.
    sem_assinatura = models.BooleanField("1ª guia sem assinatura", default=False)
    # Registro da assinatura que estava pendente (pedido da clínica, 2026-10-16): em que dia o
    # paciente assinou e quem marcou "Assinou". Em branco = foi assinada na hora da autorização
    # (ou ainda está pendente — ver sem_assinatura).
    assinada_em = models.DateField("1ª guia assinada em", null=True, blank=True)
    assinatura_registrada_por = models.ForeignKey(
        "accounts.Usuario", verbose_name="assinatura registrada por", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+",
    )
    # Pedido da clínica (2026-10-16, provisório): consulta autorizada no SISTEMA ANTIGO e o
    # retorno feito agora no novo — o retorno automático (retorno_de, abaixo) não acha a
    # consulta de origem, então a recepção marca à mão. Vale como retorno grátis em tudo
    # (ver eh_retorno e filtro_retorno).
    retorno_manual = models.BooleanField("retorno de consulta do sistema antigo", default=False)

    # Contas a receber de CONVÊNIO, por item — mesmo padrão/motivo de
    # Agendamento.valor_convenio hoje (coluna real, congelada ao Autorizar, nunca
    # recalculada depois — ver agenda.views.agendamento_mudar_status). Agendamento.valor_convenio
    # continua existindo, mas agora como a SOMA destes valores por item (precisa continuar
    # sendo uma coluna de verdade: financeiro.models.LoteFaturamento.valor_produzido faz
    # Sum("valor_convenio") via ORM sobre Agendamento, não dá pra virar property).
    valor_convenio = models.DecimalField(
        "valor a receber do convênio (item)", max_digits=8, decimal_places=2, null=True, blank=True,
    )
    # Congelado em agenda.views.agendamento_confirmar_pagamento no momento em que o
    # pagamento particular é registrado — existe pra financeiro.views poder somar por
    # procedimento (relatórios) o que foi DE FATO cobrado, sem depender de recalcular pela
    # tabela de preços atual (que pode ter mudado desde então). Nunca editável pelo usuário
    # (mesmo espírito de PagamentoParticular.valor, que também é uma coluna congelada).
    valor_cobrado = models.DecimalField(
        "valor cobrado (particular, item)", max_digits=8, decimal_places=2, null=True, blank=True,
    )

    # Retorno grátis por ITEM — ver buscar_item_para_retorno abaixo. Mesmo espírito do
    # antigo Agendamento.retorno_de (ver comentário 'LEGADO' lá): nunca escolhido pelo
    # usuário, recalculado do zero a cada vez que o item é salvo, exceto quando o
    # agendamento já está Realizado.
    retorno_de = models.ForeignKey(
        "self",
        verbose_name="retorno grátis do item",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="retornos",
    )
    # Item já pago dentro de um pacote (ver PacoteVendido) — vinculado sozinho pelo sistema
    # (vincular_pacotes, abaixo), nunca escolhido pela recepção, igual ao retorno grátis.
    # Item vinculado custa R$ 0,00 aqui (o valor foi cobrado na venda do pacote) e consome o
    # saldo do pacote enquanto o atendimento não for cancelado nem falta.
    pacote_vendido = models.ForeignKey(
        "PacoteVendido",
        verbose_name="pacote usado",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="itens_agendamento",
    )
    # Guia de sessões do plano usada por esta sessão (ver GuiaConvenio) — escolhida na tela de
    # autorização (agenda.views.agendamento_autorizar). Consome 'quantidade' sessões da guia
    # (1h = 2) enquanto o atendimento estiver Autorizado/Realizado.
    guia_convenio = models.ForeignKey(
        "GuiaConvenio",
        verbose_name="guia do convênio",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="itens",
    )
    criado_em = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Item do agendamento"
        verbose_name_plural = "Itens do agendamento"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["agendamento", "procedimento"], name="unico_procedimento_por_agendamento"
            ),
        ]

    def __str__(self):
        return self.procedimento.nome + (f" x{self.quantidade}" if self.quantidade != 1 else "")

    @staticmethod
    def quantidade_guias_sessao(procedimento, forma_atendimento, duracao_minutos):
        """Quantas guias (= 'quantidade') uma SESSÃO por CONVÊNIO ocupa, pela duração do
        atendimento: 30 min = 1, 1h = 2, 1h30 = 3... (arredonda pra baixo — o plano só paga
        unidades completas; 45 min = 1). None quando a regra não se aplica (não é sessão,
        ou é particular) — aí a quantidade continua a digitada."""
        if (
            forma_atendimento != Agendamento.FormaAtendimento.CONVENIO
            or not procedimento.permite_repeticao_semanal
            or not duracao_minutos
        ):
            return None
        return max(1, int(duracao_minutos) // MINUTOS_POR_GUIA_SESSAO)

    @classmethod
    def com_guia_pendente(cls):
        """Itens com guia de sessão ainda não autorizada — o mesmo critério de
        controla_guias/guias_pendentes, só que no banco (pra listar/contar sem carregar
        tudo). Atendimento cancelado ou falta não tem guia a autorizar."""
        return cls.objects.filter(
            agendamento__forma_atendimento=Agendamento.FormaAtendimento.CONVENIO,
            procedimento__permite_repeticao_semanal=True,
            quantidade__gt=models.F("guias_extras_autorizadas") + 1,
        ).exclude(agendamento__status__in=[Agendamento.Status.CANCELADO, Agendamento.Status.FALTOU])

    @classmethod
    def com_assinatura_pendente(cls):
        """Itens de atendimento já autorizado com alguma guia autorizada sem assinatura (a 1ª,
        ver sem_assinatura, ou uma extra, ver GuiaExtraAutorizada.sem_assinatura)."""
        return (
            cls.objects.filter(agendamento__status__in=Agendamento.STATUS_PRODUCAO)
            .filter(models.Q(sem_assinatura=True) | models.Q(guias_extras__sem_assinatura=True))
            .distinct()
        )

    @property
    def assinaturas_pendentes(self):
        """Números das guias autorizadas que ainda faltam assinar (1 = a 1ª guia)."""
        numeros = [1] if self.sem_assinatura else []
        numeros += sorted(
            extra.numero
            for extra in self.guias_extras.all()
            if extra.sem_assinatura and extra.numero <= self.guias_autorizadas
        )
        return numeros

    @property
    def assinaturas_registradas(self):
        """[(nº da guia, assinada_em, quem registrou)] das guias que estavam sem assinatura e
        já foram assinadas — pra mostrar o registro em Guias pendentes."""
        registros = []
        if self.assinada_em:
            registros.append((1, self.assinada_em, self.assinatura_registrada_por))
        registros += sorted(
            ((e.numero, e.assinada_em, e.assinatura_registrada_por) for e in self.guias_extras.all() if e.assinada_em),
            key=lambda r: r[0],
        )
        return registros

    @staticmethod
    def filtro_retorno(prefixo=""):
        """Q() de item que é retorno grátis — automático (retorno_de) OU marcado à mão
        (retorno_manual). `prefixo` pra usar a partir de Agendamento (ex.: "itens__")."""
        return models.Q(**{f"{prefixo}retorno_de__isnull": False}) | models.Q(**{f"{prefixo}retorno_manual": True})

    @staticmethod
    def filtro_nao_retorno(prefixo=""):
        """Q() de item que NÃO é retorno grátis (o contrário de filtro_retorno)."""
        return models.Q(**{f"{prefixo}retorno_de__isnull": True, f"{prefixo}retorno_manual": False})

    @property
    def controla_guias(self):
        """Sessão por convênio com mais de uma guia — só a 1ª é autorizada no dia."""
        return (
            self.quantidade > 1
            and self.agendamento.forma_atendimento == Agendamento.FormaAtendimento.CONVENIO
            and self.procedimento.permite_repeticao_semanal
        )

    @property
    def precisa_guia_convenio(self):
        """Sessão por convênio cobrada do plano: tem que usar uma guia de sessões (GuiaConvenio)
        ao autorizar. Item de pacote ou retorno grátis não é cobrado do plano."""
        return (
            self.agendamento.forma_atendimento == Agendamento.FormaAtendimento.CONVENIO
            and self.procedimento.permite_repeticao_semanal
            and not self.pacote_vendido_id
            and not self.eh_retorno
        )

    @property
    def guias_autorizadas(self):
        """1ª guia (vale junto com o atendimento) + as extras já marcadas."""
        if not self.controla_guias:
            return self.quantidade
        return 1 + min(self.guias_extras_autorizadas, self.quantidade - 1)

    @property
    def guias_pendentes(self):
        return self.quantidade - self.guias_autorizadas

    @property
    def quantidade_produzida(self):
        """O que conta na PRODUÇÃO (relatórios do Financeiro, valor do convênio): em sessão
        com controle de guias, só as guias já autorizadas; nos demais itens, a quantidade."""
        return self.guias_autorizadas

    @classmethod
    def buscar_item_para_retorno(cls, paciente, especialidade, especialidade_outra, data_hora_referencia, excluir_pk=None):
        """
        Mesma regra de negócio de Agendamento.buscar_consulta_para_retorno de antes (30
        dias corridos, mesma especialidade, uso único, prioriza a candidata mais antiga
        entre empates) — ver o histórico dessa regra no model antigo — agora procurando
        um ITEM candidato em vez de um agendamento inteiro: necessário porque um
        agendamento pode ter, por exemplo, Consulta + Eletrocardiograma no mesmo horário,
        e só o item de Consulta pode ser elegível a dar origem a um retorno, nunca o ECG.

        'especialidade'/'especialidade_outra' são as do ATENDIMENTO (Agendamento.especialidade,
        já resolvido por AgendamentoForm.clean()) — não mais "a" especialidade do profissional:
        desde que um profissional pode ter mais de uma (ver
        cadastros.models.Profissional.especialidades_pares), só o atendimento em si sabe qual
        das duas vale aqui. O método já comparava por especialidade, nunca por profissional
        (retorno vale pra qualquer profissional da mesma especialidade), então esta troca só
        deixa isso explícito.

        'excluir_pk' é o pk do PRÓPRIO item sendo salvo (None em uma criação nova) — mesmo
        motivo de antes: reabrir e resalvar um item que já é um retorno não pode se
        autobloquear como "vaga já ocupada por ele mesmo".
        """
        if not (paciente and especialidade and data_hora_referencia):
            return None

        # Prazo por convênio (pedido do cliente, 2026-09-28 — ver
        # Convenio.prazo_retorno_dias): vale o convênio da consulta ORIGINAL; particular
        # usa o padrão de 30. O filtro no banco usa o maior prazo possível e cada
        # candidata é conferida contra o seu próprio prazo no laço abaixo.
        prazo_padrao = Convenio.PRAZO_RETORNO_PADRAO_DIAS
        maior_prazo = max(
            prazo_padrao,
            Convenio.objects.aggregate(maior=models.Max("prazo_retorno_dias"))["maior"] or 0,
        )
        janela_inicio = data_hora_referencia - timedelta(days=maior_prazo)
        candidatas = cls.objects.select_related("agendamento__convenio").filter(
            agendamento__paciente=paciente,
            agendamento__status__in=Agendamento.STATUS_PRODUCAO,
            procedimento__eh_consulta=True,
            retorno_de__isnull=True,
            retorno_manual=False,
            agendamento__especialidade=especialidade,
            agendamento__data_hora_inicio__gte=janela_inicio,
            agendamento__data_hora_inicio__lt=data_hora_referencia,
        )
        if especialidade == "outra":
            candidatas = candidatas.filter(agendamento__especialidade_outra=especialidade_outra)
        if excluir_pk:
            candidatas = candidatas.exclude(pk=excluir_pk)

        for candidata in candidatas.order_by("agendamento__data_hora_inicio"):
            convenio_original = candidata.agendamento.convenio
            prazo = convenio_original.prazo_retorno_dias if convenio_original else prazo_padrao
            if candidata.agendamento.data_hora_inicio < data_hora_referencia - timedelta(days=prazo):
                continue
            ja_usada = candidata.retornos.exclude(
                agendamento__status__in=[Agendamento.Status.CANCELADO, Agendamento.Status.FALTOU]
            )
            if excluir_pk:
                ja_usada = ja_usada.exclude(pk=excluir_pk)
            if not ja_usada.exists():
                return candidata
        return None

    @property
    def eh_retorno(self):
        """True quando ESTE item é um retorno grátis — detectado automaticamente (ver
        retorno_de/buscar_item_para_retorno acima) ou marcado à mão (retorno_manual)."""
        return self.retorno_de_id is not None or self.retorno_manual

    @property
    def valor_a_cobrar(self):
        """
        Quanto este item deve custar — calculado automaticamente a partir do catálogo,
        nunca digitado à mão (mesma regra de sempre). Particular usa
        Procedimento.valor_particular; convênio usa Convenio.valor_para — os dois
        multiplicados por 'quantidade'. None quando não dá pra calcular (cadastro de
        preço incompleto).

        Retorno grátis (ver 'eh_retorno'): sempre R$ 0,00, sem exceção — mesma regra de
        negócio confirmada com o cliente para o antigo Agendamento.eh_retorno (hard lock,
        não é uma opção do operador), agora só aplicada a este item específico em vez de
        ao agendamento inteiro.
        """
        if self.eh_retorno or self.pacote_vendido_id:
            return Decimal("0.00")
        agendamento = self.agendamento
        if agendamento.forma_atendimento == Agendamento.FormaAtendimento.PARTICULAR:
            if agendamento.profissional.cobra_valor_fixo_particular:
                # Profissional com valor próprio (ver cadastros.models.ValorFixoProfissional):
                # sem linha na tabela dele = sem preço, nunca cai no valor_particular padrão.
                valor_fixo = agendamento.profissional.valor_fixo_para(self.procedimento)
                valor_unitario = valor_fixo.valor_cobrado if valor_fixo else None
            else:
                valor_unitario = self.procedimento.valor_particular
        elif agendamento.forma_atendimento == Agendamento.FormaAtendimento.CONVENIO and agendamento.convenio_id:
            valor_unitario = agendamento.convenio.valor_para(self.procedimento, agendamento.profissional)
        else:
            return None
        if valor_unitario is None:
            return None
        return (valor_unitario * self.quantidade_produzida).quantize(Decimal("0.01"))

    @property
    def valor_repasse_fixo_a_calcular(self):
        """Repasse fixo deste item (ValorFixoProfissional.valor_repasse × quantidade) quando o
        profissional cobra valor fixo no particular; None quando o repasse dele segue o
        percentual. Retorno grátis repassa R$ 0,00 (nada foi cobrado). Só lido no momento do
        pagamento — o valor fica congelado em PagamentoParticular.valor_repasse_fixo."""
        agendamento = self.agendamento
        if (
            agendamento.forma_atendimento != Agendamento.FormaAtendimento.PARTICULAR
            or not agendamento.profissional.cobra_valor_fixo_particular
        ):
            return None
        if self.eh_retorno or self.pacote_vendido_id:
            # Pacote: o repasse sai inteiro na venda (PacoteVendido.valor_repasse_fixo).
            return Decimal("0.00")
        valor_fixo = agendamento.profissional.valor_fixo_para(self.procedimento)
        if valor_fixo is None:
            return None
        return (valor_fixo.valor_repasse * self.quantidade).quantize(Decimal("0.01"))


class PagamentoParticular(models.Model):
    """
    Uma forma de pagamento de um atendimento PARTICULAR — um mesmo
    Agendamento pode ter até 2 linhas (pedido do cliente: aceitar pagamento
    dividido, ex. metade dinheiro/metade cartão — o limite de 2 é aplicado no
    formulário, ver agenda.forms.ConfirmarPagamentoForm, não aqui no modelo).

    Cada linha tem o PRÓPRIO controle de repasse (um agendamento pode ter uma
    forma já repassada e a outra pendente; cortesia não gera repasse nenhum).
    Dinheiro: o registrado até 25/10/2026 era entregue ao profissional na hora,
    sem registro; a partir de REPASSE_DINHEIRO_REGISTRADO_DESDE (pedido da clínica,
    26/10/2026) fica no caixa e entra no repasse pendente como cartão/Pix, até o
    financeiro registrar o pagamento (financeiro.models.RepassePagamento). As linhas
    em dinheiro antigas foram marcadas como repassadas pela migração agenda.0033.

    Cortesia (Agendamento.FormaPagamento.CORTESIA) também vira uma linha
    aqui, com valor=0 — é assim que o atendimento deixa de ficar "pendente de
    pagamento" (ver Agendamento.pagamento_pendente) sem cobrar nada nem gerar
    obrigação de repasse.

    valor_recebido/parcelas (só cartão débito/crédito): a maquininha desconta uma taxa que
    varia por débito/crédito e, no crédito, por quantidade de parcelas — sem tabela de taxas
    no sistema (decisão do cliente), o financeiro digita à mão o valor realmente recebido no
    momento do pagamento (ver ConfirmarPagamentoForm). 'valor' continua sendo o valor
    cadastrado/cobrado do particular, e é ele (nunca valor_recebido) que alimenta todo o
    cálculo de repasse ao profissional (financeiro.views._producao_por_profissional,
    agenda.views.relatorio_repasses_semanal) — a taxa da maquininha é um custo que a clínica
    absorve sozinha. valor_recebido só entra nos relatórios do PROPRIETÁRIO (ver
    valor_recebido_efetivo abaixo e financeiro.views._visao_proprietario/painel_financeiro).
    """

    agendamento = models.ForeignKey(
        Agendamento, verbose_name="agendamento", on_delete=models.CASCADE, related_name="pagamentos"
    )
    forma_pagamento = models.CharField(
        "forma de pagamento", max_length=20, choices=Agendamento.FormaPagamento.choices
    )
    valor = models.DecimalField("valor", max_digits=8, decimal_places=2)

    # Só cartão (débito/crédito) — ver ConfirmarPagamentoForm/agendamento_confirmar_pagamento.
    # Sem tabela de taxas no sistema (decisão do cliente): valor_recebido é digitado à mão pelo
    # financeiro no momento do recebimento, e fica congelado aqui como valor_recebido/parcelas —
    # nunca recalculado depois. Usado pelos relatórios do PROPRIETÁRIO (financeiro.views) pra
    # mostrar o valor real recebido, já com a taxa da maquininha descontada; o repasse do
    # profissional continua sempre em cima de 'valor' (valor cadastrado/cobrado), nunca deste
    # campo — a taxa é um custo que a clínica absorve sozinha.
    valor_recebido = models.DecimalField(
        "valor recebido", max_digits=8, decimal_places=2, null=True, blank=True,
        help_text=(
            "Valor que efetivamente entrou na conta da clínica, já descontada a taxa da "
            "maquininha (só cartão débito/crédito) — digitado à mão pelo financeiro no momento "
            "do recebimento (não existe tabela de taxas no sistema, decisão do cliente). "
            "Congelado como PagamentoParticular.valor: nunca recalculado depois."
        ),
    )
    parcelas = models.PositiveSmallIntegerField(
        "parcelas", null=True, blank=True, validators=[MinValueValidator(1)],
        help_text=(
            "Quantidade de parcelas (só cartão de crédito) — só anotação/referência, nenhum "
            "cálculo do sistema depende deste valor."
        ),
    )

    # repasse_pago só vira True quando o repasse LÍQUIDO desta linha foi quitado por inteiro.
    # Pagamento parcial (caixa sem dinheiro suficiente pra repassar tudo no dia — pedido do
    # cliente, 2026-09-28) é abatido aos poucos em repasse_valor_pago, via
    # financeiro.models.RepassePagamento (FIFO por profissional, ver lá) — a linha continua
    # pendente, com o saldo (saldo_repasse) aparecendo em Agenda → Repasses semanais.
    repasse_pago = models.BooleanField("repasse pago ao profissional", default=False)
    repasse_pago_em = models.DateTimeField("repasse pago em", null=True, blank=True)
    repasse_valor_pago = models.DecimalField(
        "valor do repasse já pago", max_digits=8, decimal_places=2, default=Decimal("0.00")
    )
    # Profissional que cobra valor fixo no particular (cadastros.models.ValorFixoProfissional):
    # quanto DESTA linha é repasse, congelado no momento do pagamento (agenda.views.
    # agendamento_confirmar_pagamento reparte o repasse fixo do atendimento entre as linhas,
    # proporcional ao valor de cada uma). NULL = repasse pelo percentual do profissional (caso
    # normal, e todo pagamento registrado antes deste recurso existir).
    valor_repasse_fixo = models.DecimalField(
        "repasse fixo desta linha", max_digits=8, decimal_places=2, null=True, blank=True
    )

    # Estorno — devolve ao paciente o valor desta linha (ex: imprevisto após o
    # pagamento). Se repasse_pago já estava True no momento do estorno, a view
    # (financeiro.views.pagamento_estornar) desfaz o repasse automaticamente
    # (repasse_pago volta a False) — ver decisão registrada no plano da feature.
    estornado = models.BooleanField("estornado", default=False)
    estornado_em = models.DateTimeField("estornado em", null=True, blank=True)
    estornado_por = models.ForeignKey(
        "accounts.Usuario",
        verbose_name="estornado por",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pagamentos_estornados",
    )
    motivo_estorno = models.TextField("motivo do estorno", blank=True)

    criado_em = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Pagamento particular"
        verbose_name_plural = "Pagamentos particulares"
        ordering = ["id"]

    def __str__(self):
        return f"{self.agendamento} — {self.get_forma_pagamento_display()}: R$ {formatar_moeda(self.valor)}"

    # Dinheiro REGISTRADO antes desta data foi entregue ao profissional na hora (regra antiga, sem
    # RepassePagamento) — usado pela posição de caixa físico (financeiro.views) e pela agenda.0033.
    REPASSE_DINHEIRO_REGISTRADO_DESDE = date(2026, 10, 26)

    @property
    def dinheiro_entregue_na_hora(self):
        """Linha em dinheiro da regra antiga: a parte do profissional já saiu do caixa na hora."""
        return (
            self.forma_pagamento == Agendamento.FormaPagamento.DINHEIRO
            and self.criado_em is not None
            and timezone.localtime(self.criado_em).date() < self.REPASSE_DINHEIRO_REGISTRADO_DESDE
        )

    # Janela em que linhas iguais contam como envio duplo (ver copias_em_dobro).
    JANELA_ENVIO_DUPLO = timedelta(minutes=5)

    def assinatura(self):
        return (self.forma_pagamento, self.valor, self.valor_recebido, self.parcelas, self.valor_repasse_fixo)

    @classmethod
    def copias_em_dobro(cls, agendamento, pagamentos):
        """
        Pagamento gravado em DOBRO por envio duplo da tela de registrar pagamento (clínica,
        26/10/2026 — ver agenda.views.agendamento_confirmar_pagamento, que agora trava o
        atendimento). Devolve as cópias ([[linha, ...], [linha, ...], ...], a 1ª é a original)
        quando `pagamentos` (por pk, não estornados) é um mesmo conjunto de 1 ou 2 linhas
        repetido 2+ vezes, gravado em até JANELA_ENVIO_DUPLO, e UM conjunto soma o valor cobrado
        (procedimentos + pacote vendido). Senão None. Usado por financeiro.views.pagamento_corrigir
        e pelo comando remover_pagamentos_duplicados.
        """
        if len(pagamentos) < 2 or pagamentos[-1].criado_em - pagamentos[0].criado_em > cls.JANELA_ENVIO_DUPLO:
            return None
        esperado = sum(
            (item.valor_cobrado or Decimal("0.00") for item in agendamento.itens.all()), Decimal("0.00")
        ) + sum(
            (venda.valor for venda in agendamento.pacotes_vendidos.filter(pago_em__isnull=False)), Decimal("0.00")
        )
        for tamanho in (1, 2):
            if len(pagamentos) % tamanho or len(pagamentos) // tamanho < 2:
                continue
            blocos = [pagamentos[i:i + tamanho] for i in range(0, len(pagamentos), tamanho)]
            modelo = [p.assinatura() for p in blocos[0]]
            if any([p.assinatura() for p in bloco] != modelo for bloco in blocos[1:]):
                continue
            if sum((p.valor for p in blocos[0]), Decimal("0.00")) == esperado:
                return blocos
        return None

    @property
    def valor_recebido_efetivo(self):
        """valor_recebido quando preenchido (cartão, a partir desta feature), senão o próprio
        valor (dinheiro/Pix/cortesia, ou cartão registrado antes desta feature existir) — usado
        pelos relatórios do proprietário que precisam do valor líquido de taxa de cartão sem
        quebrar em dados antigos (todos ficam com valor_recebido=NULL, cai pro valor cheio)."""
        return self.valor_recebido if self.valor_recebido is not None else self.valor

    @property
    def valor_repasse(self):
        """Repasse LÍQUIDO desta linha — valor cobrado menos o percentual de retenção do
        profissional (proprietário não sofre desconto). Mesma conta, linha a linha, usada em
        agenda.views.relatorio_repasses_semanal desde antes; centralizada aqui porque o
        pagamento parcial (financeiro.models.RepassePagamento) abate contra este valor.
        Linha com valor_repasse_fixo (profissional que cobra valor fixo) usa esse valor
        congelado, sem percentual nenhum."""
        profissional = self.agendamento.profissional
        if profissional.eh_proprietario:
            return self.valor
        if self.valor_repasse_fixo is not None:
            return self.valor_repasse_fixo
        percentual = profissional.percentual_desconto
        return (self.valor * (Decimal("100") - percentual) / Decimal("100")).quantize(Decimal("0.01"))

    @property
    def saldo_repasse(self):
        """Quanto do repasse líquido desta linha ainda falta pagar ao profissional."""
        if self.repasse_pago:
            return Decimal("0.00")
        return max(self.valor_repasse - self.repasse_valor_pago, Decimal("0.00"))


class PacoteVendido(models.Model):
    """
    Pacote (cadastros.models.Pacote) vendido a um paciente — pedido do cliente, 2026-10-13.

    A venda acontece na tela de registrar pagamento de um atendimento PARTICULAR do
    profissional (agenda.views.agendamento_confirmar_pagamento): o preço fechado do pacote
    entra no valor daquele atendimento e é pago pelas mesmas linhas de PagamentoParticular de
    sempre. Por isso caixa, fechamentos, repasse (o profissional recebe tudo de uma vez, pelo
    percentual dele ou pelo repasse fixo congelado aqui) e estorno continuam funcionando sem
    nenhum caminho novo. 'agendamento_venda' é esse atendimento.

    Os atendimentos seguintes do mesmo paciente com o MESMO profissional usam o saldo sozinhos
    (ver vincular_pacotes): o item fica R$ 0,00 e o atendimento pula o financeiro. Falta e
    cancelamento devolvem a sessão ao saldo (o paciente remarca); sem validade; sem devolução.

    Itens, nome e valor são congelados na venda (PacoteVendidoItem) — mudar o cadastro do
    pacote depois nunca mexe no que já foi vendido.

    Pacote do sistema antigo (cadastro_manual — clínica, 27/10/2026): cadastrado direto no
    paciente, sem atendimento de venda, e já vale desde o cadastro. Se ficou "a pagar"
    (pagamento_pendente), o financeiro cobra depois na tela de pagamento de qualquer atendimento
    particular do paciente com o profissional — aí 'agendamento_venda' passa a ser esse
    atendimento e a cobrança entra no caixa daquele dia como uma venda normal. Estornar essa
    cobrança volta o pacote para "a pagar" (não desativa). "Já pago no sistema antigo" fica com
    pago_em e sem atendimento: nunca entra no caixa.
    """

    pacote = models.ForeignKey(
        "cadastros.Pacote", verbose_name="pacote", on_delete=models.PROTECT, related_name="vendas"
    )
    nome = models.CharField("nome do pacote", max_length=150)
    paciente = models.ForeignKey(
        "cadastros.Paciente", verbose_name="paciente", on_delete=models.PROTECT, related_name="pacotes_comprados"
    )
    profissional = models.ForeignKey(
        "cadastros.Profissional", verbose_name="profissional", on_delete=models.PROTECT, related_name="pacotes_vendidos"
    )
    agendamento_venda = models.ForeignKey(
        Agendamento,
        verbose_name="atendimento da venda",
        on_delete=models.CASCADE,
        related_name="pacotes_vendidos",
        null=True,
        blank=True,
    )
    cadastro_manual = models.BooleanField("cadastrado direto no paciente (sistema antigo)", default=False)
    valor = models.DecimalField("valor pago", max_digits=8, decimal_places=2)
    # Congelado na venda: Pacote.valor_repasse quando o profissional cobra valor fixo no
    # particular; NULL = repasse pelo percentual do profissional (ver
    # agenda.views._repartir_repasse_fixo).
    valor_repasse_fixo = models.DecimalField("repasse fixo", max_digits=8, decimal_places=2, null=True, blank=True)
    # NULL enquanto o pacote só foi incluído na tela de pagamento e o pagamento ainda não foi
    # registrado — nesse estado só o próprio atendimento da venda pode usar o saldo.
    pago_em = models.DateTimeField("pago em", null=True, blank=True)
    criado_por = models.ForeignKey(
        "accounts.Usuario",
        verbose_name="vendido por",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pacotes_vendidos_registrados",
    )
    criado_em = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Pacote vendido"
        verbose_name_plural = "Pacotes vendidos"
        ordering = ["-criado_em", "-pk"]

    def __str__(self):
        return f"{self.nome} — {self.paciente.nome_completo} ({self.profissional.nome})"

    @classmethod
    def pagos(cls):
        """Pagos num atendimento e com pagamento ainda valendo — é o que entra no caixa (ver
        financeiro.views._vendas_pacote_particular)."""
        pagamento_valido = PagamentoParticular.objects.filter(
            agendamento=OuterRef("agendamento_venda"), estornado=False
        ).exclude(forma_pagamento=Agendamento.FormaPagamento.CORTESIA)
        return cls.objects.filter(pago_em__isnull=False).filter(Exists(pagamento_valido))

    @classmethod
    def ativos(cls):
        """Pacotes que o paciente pode usar: os pagos (estornar o pagamento da venda desativa o
        pacote — não existe devolução; estorno aqui é só pra corrigir engano) e os do sistema
        antigo, que valem desde o cadastro, mesmo com o pagamento ainda pendente."""
        return cls.objects.filter(Q(pk__in=cls.pagos().values("pk")) | Q(cadastro_manual=True))

    @property
    def pagamento_valido(self):
        return bool(self.pago_em and self.agendamento_venda_id) and self.agendamento_venda.pagamentos.filter(
            estornado=False
        ).exclude(forma_pagamento=Agendamento.FormaPagamento.CORTESIA).exists()

    @property
    def esta_ativo(self):
        return self.cadastro_manual or self.pagamento_valido

    @property
    def pagamento_pendente(self):
        """Pacote do sistema antigo que o paciente ainda vai pagar."""
        return self.cadastro_manual and not self.pago_em

    @classmethod
    def desfazer_nao_pagos(cls, agendamentos):
        """Atendimento cancelado (ou pacote retirado da tela de pagamento) antes de pagar: a
        venda não aconteceu e é apagada; o pacote do sistema antigo que ia ser cobrado ali só
        volta a ficar "a pagar"."""
        nao_pagos = cls.objects.filter(agendamento_venda__in=agendamentos, pago_em__isnull=True)
        nao_pagos.filter(cadastro_manual=True).update(agendamento_venda=None)
        nao_pagos.filter(cadastro_manual=False).delete()

    def motivo_nao_cancela(self):
        """Por que este pacote do sistema antigo NÃO pode ser cancelado (texto pra tela), ou ""
        se pode (pedido da clínica, 10/11/2026 — cadastro feito por engano). Bloqueia o que já
        mexeu em dinheiro ou em atendimento de verdade: cobrança num atendimento (paga ou em
        andamento na tela de pagamento — estornar volta o pacote pra "a pagar") e uso num
        atendimento que já conta na produção ou tem pagamento. Uso em atendimento futuro não
        bloqueia: ao cancelar, esses itens voltam a ser cobrados normalmente."""
        if not self.cadastro_manual:
            return "Só o pacote cadastrado direto no paciente (sistema antigo) pode ser cancelado aqui."
        if self.agendamento_venda_id:
            return (
                "Este pacote está sendo cobrado num atendimento. Estorne o pagamento (ou retire o pacote "
                "da tela de pagamento) antes de cancelar."
            )
        usado = self.itens_agendamento.filter(
            Q(agendamento__status__in=Agendamento.STATUS_PRODUCAO) | Q(agendamento__pagamentos__isnull=False)
        ).exclude(agendamento__status__in=[Agendamento.Status.CANCELADO, Agendamento.Status.FALTOU])
        if usado.exists():
            return "Este pacote já foi usado em atendimento autorizado/realizado — não pode ser cancelado."
        return ""

    def _usados(self, excluir_item_pk=None):
        usados = self.itens_agendamento.exclude(
            agendamento__status__in=[Agendamento.Status.CANCELADO, Agendamento.Status.FALTOU]
        )
        if excluir_item_pk:
            usados = usados.exclude(pk=excluir_item_pk)
        return usados

    def saldo_de(self, procedimento_id, excluir_item_pk=None):
        contratado = sum(
            item.quantidade - item.usadas_antes for item in self.itens.all() if item.procedimento_id == procedimento_id
        )
        usado = (
            self._usados(excluir_item_pk).filter(procedimento_id=procedimento_id).aggregate(total=Sum("quantidade"))["total"]
            or 0
        )
        return contratado - usado

    @property
    def saldo(self):
        """Por procedimento: contratado, usado e restante (pras telas)."""
        usados = {
            linha["procedimento_id"]: linha["total"]
            for linha in self._usados().values("procedimento_id").annotate(total=Sum("quantidade"))
        }
        linhas = []
        for item in self.itens.select_related("procedimento"):
            usado = usados.get(item.procedimento_id, 0) + item.usadas_antes
            linhas.append({
                "procedimento": item.procedimento,
                "contratado": item.quantidade,
                "usado": usado,
                "restante": max(item.quantidade - usado, 0),
            })
        return linhas

    @property
    def total_restante(self):
        return sum(linha["restante"] for linha in self.saldo)

    @property
    def saldo_resumo(self):
        """Ex.: 'Sessão: restam 7 de 10' — texto curto pras telas."""
        return ", ".join(
            f"{linha['procedimento'].nome}: restam {linha['restante']} de {linha['contratado']}" for linha in self.saldo
        )

    @classmethod
    def para_item(cls, paciente_id, profissional_id, procedimento_id, quantidade, agendamento_id=None, excluir_item_pk=None):
        """Pacote do paciente com este profissional que ainda cobre 'quantidade' deste
        procedimento — o mais antigo primeiro. Além dos ativos, aceita o pacote que está
        sendo vendido no próprio atendimento (ainda sem pagamento registrado)."""
        if not (paciente_id and profissional_id and procedimento_id):
            return None
        filtro = Q(pk__in=cls.ativos().values("pk"))
        if agendamento_id:
            filtro |= Q(agendamento_venda_id=agendamento_id, pago_em__isnull=True)
        candidatos = (
            cls.objects.filter(paciente_id=paciente_id, profissional_id=profissional_id, itens__procedimento_id=procedimento_id)
            .filter(filtro)
            .distinct()
            .order_by("criado_em", "pk")
        )
        for candidato in candidatos:
            if candidato.saldo_de(procedimento_id, excluir_item_pk) >= quantidade:
                return candidato
        return None


class PacoteVendidoItem(models.Model):
    """Composição do pacote congelada na venda (cópia de cadastros.models.PacoteItem)."""

    pacote_vendido = models.ForeignKey(
        PacoteVendido, verbose_name="pacote vendido", on_delete=models.CASCADE, related_name="itens"
    )
    procedimento = models.ForeignKey(
        "cadastros.Procedimento", verbose_name="procedimento", on_delete=models.PROTECT, related_name="itens_pacote_vendido"
    )
    quantidade = models.PositiveIntegerField("quantidade", validators=[MinValueValidator(1)])
    # Pacote do sistema antigo (PacoteVendido.cadastro_manual): sessões já feitas lá.
    usadas_antes = models.PositiveIntegerField("já usadas no sistema antigo", default=0)

    class Meta:
        verbose_name = "Item do pacote vendido"
        verbose_name_plural = "Itens do pacote vendido"
        ordering = ["id"]

    def __str__(self):
        return f"{self.quantidade}x {self.procedimento.nome}"


class GuiaExtraAutorizada(models.Model):
    """Registro de cada guia EXTRA de uma sessão (2ª, 3ª... — ver
    AgendamentoItem.guias_extras_autorizadas) marcada em "Guias pendentes": em que dia o plano
    autorizou (pode ser antes do atendimento — pedido da clínica, 15/10/2026) e quem marcou.
    Só registro: a produção continua contando no dia do atendimento, pelo contador do item."""

    item = models.ForeignKey(
        AgendamentoItem, verbose_name="item", on_delete=models.CASCADE, related_name="guias_extras"
    )
    numero = models.PositiveSmallIntegerField("nº da guia na sessão")
    autorizada_em = models.DateField("autorizada pelo plano em")
    registrada_por = models.ForeignKey(
        "accounts.Usuario", verbose_name="registrada por", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="guias_extras_registradas",
    )
    registrada_em = models.DateTimeField("registrada em", auto_now_add=True)
    # Autorizada sem o paciente assinar (pedido da clínica, 2026-10-16) — ver
    # AgendamentoItem.sem_assinatura, o mesmo para a 1ª guia.
    sem_assinatura = models.BooleanField("sem assinatura", default=False)
    assinada_em = models.DateField("assinada em", null=True, blank=True)
    assinatura_registrada_por = models.ForeignKey(
        "accounts.Usuario", verbose_name="assinatura registrada por", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+",
    )

    class Meta:
        verbose_name = "Guia extra autorizada"
        verbose_name_plural = "Guias extras autorizadas"
        ordering = ["item", "numero"]
        constraints = [models.UniqueConstraint(fields=["item", "numero"], name="unica_guia_extra_por_numero")]

    def __str__(self):
        return f"{self.numero}ª guia — {self.autorizada_em:%d/%m/%Y}"


class GuiaConvenio(models.Model):
    """
    Guia de SESSÕES liberada pelo plano (pedido da clínica, 15/10/2026): ex.: guia 2013000387
    do Bradesco, 10 sessões de Fono para a paciente X. Cada sessão autorizada por convênio
    escolhe a guia na tela de autorização (agenda.views.agendamento_autorizar) e consome dela
    a 'quantidade' do item (sessão de 1h = 2 — decisão do cliente). A contagem ("sessões 3–4
    de 10") é sempre calculada, nunca digitada; atendimento cancelado ou falta devolve as
    sessões. Guia esgotada ou vencida não pode ser usada (bloqueia — decisão do cliente):
    a recepção cadastra a guia nova liberada pelo plano.

    Não confundir com as "guias pendentes" de AgendamentoItem.guias_extras_autorizadas (as
    unidades de 30 min de uma mesma sessão que o plano autoriza ao longo da semana).
    """

    paciente = models.ForeignKey(
        "cadastros.Paciente", verbose_name="paciente", on_delete=models.PROTECT, related_name="guias_convenio"
    )
    convenio = models.ForeignKey(Convenio, verbose_name="convênio", on_delete=models.PROTECT, related_name="guias")
    procedimento = models.ForeignKey(
        "cadastros.Procedimento", verbose_name="procedimento", on_delete=models.PROTECT, related_name="guias_convenio"
    )
    numero = models.CharField("número da guia", max_length=40)
    quantidade_sessoes = models.PositiveIntegerField(
        "sessões liberadas", validators=[MinValueValidator(1)],
        help_text="Quantas sessões o plano liberou nesta guia (sessão de 1h conta 2).",
    )
    validade = models.DateField(
        "válida até", null=True, blank=True, help_text="Deixe em branco se o plano não definir validade."
    )
    observacoes = models.TextField("observações", blank=True)
    criada_por = models.ForeignKey(
        "accounts.Usuario", verbose_name="cadastrada por", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="guias_convenio_criadas",
    )
    criada_em = models.DateTimeField("cadastrada em", auto_now_add=True)

    class Meta:
        verbose_name = "Guia do convênio (sessões)"
        verbose_name_plural = "Guias do convênio (sessões)"
        ordering = ["-criada_em"]

    def __str__(self):
        return f"Guia {self.numero} — {self.procedimento.nome} ({self.convenio.nome})"

    def itens_consumindo(self):
        """Itens que estão gastando sessões desta guia (atendimento Autorizado/Realizado)."""
        return self.itens.filter(agendamento__status__in=Agendamento.STATUS_PRODUCAO)

    @property
    def sessoes_usadas(self):
        if not hasattr(self, "_sessoes_usadas"):
            self._sessoes_usadas = self.itens_consumindo().aggregate(total=Sum("quantidade"))["total"] or 0
        return self._sessoes_usadas

    @property
    def saldo(self):
        return max(self.quantidade_sessoes - self.sessoes_usadas, 0)

    def vencida_em(self, data):
        return bool(self.validade and data > self.validade)

    def situacao_em(self, data):
        if self.vencida_em(data):
            return "Vencida"
        if not self.saldo:
            return "Esgotada"
        return "Aberta"

    @classmethod
    def faixas_dos_itens(cls, itens):
        """{item.pk: (primeira, última, total)} — posição das sessões de cada item na guia dele
        ("sessões 3–4 de 10"), pela ordem de autorização. Só itens que consomem a guia."""
        guia_ids = {item.guia_convenio_id for item in itens if item.guia_convenio_id}
        if not guia_ids:
            return {}
        faixas = {}
        consumindo = (
            AgendamentoItem.objects.filter(
                guia_convenio_id__in=guia_ids, agendamento__status__in=Agendamento.STATUS_PRODUCAO
            )
            .select_related("guia_convenio", "agendamento")
            .order_by("guia_convenio_id", "agendamento__autorizado_em", "agendamento__data_hora_inicio", "pk")
        )
        usadas = {}
        for item in consumindo:
            inicio = usadas.get(item.guia_convenio_id, 0) + 1
            fim = inicio + item.quantidade - 1
            usadas[item.guia_convenio_id] = fim
            faixas[item.pk] = (inicio, fim, item.guia_convenio.quantidade_sessoes)
        return faixas


def vincular_pacotes(agendamento):
    """
    Liga (ou desliga) cada item do atendimento ao pacote do paciente que o cobre — sempre
    calculado pelo sistema, como o retorno grátis. Só mexe em atendimento que ainda não conta
    na produção, não foi cancelado/falta e não tem pagamento registrado (o que já foi cobrado,
    ou coberto, não é re-rotulado). Só particular; retorno grátis não consome pacote. Um
    vínculo existente é mantido enquanto continuar válido; senão procura outro pacote com
    saldo (ou fica sem nenhum, e o item volta a ser cobrado normalmente). Chamado depois de
    salvar os itens e de novo ao encaminhar ao financeiro (agenda.views.agendamento_mudar_status),
    quando o saldo pode ter mudado (falta, cancelamento, pacote comprado depois do agendamento).
    """
    if not agendamento.pk:
        return
    if agendamento.status in Agendamento.STATUS_PRODUCAO or agendamento.status in (
        Agendamento.Status.CANCELADO,
        Agendamento.Status.FALTOU,
    ):
        return
    if agendamento.pagamentos.exists():
        return
    eh_particular = agendamento.forma_atendimento == Agendamento.FormaAtendimento.PARTICULAR
    alterados = []
    for item in agendamento.itens.select_related("pacote_vendido"):
        novo = None
        if eh_particular and not item.eh_retorno:
            atual = item.pacote_vendido
            if atual is not None and (
                atual.paciente_id == agendamento.paciente_id
                and atual.profissional_id == agendamento.profissional_id
                and (atual.esta_ativo or (atual.agendamento_venda_id == agendamento.pk and not atual.pago_em))
                and atual.saldo_de(item.procedimento_id, excluir_item_pk=item.pk) >= item.quantidade
            ):
                novo = atual
            else:
                novo = PacoteVendido.para_item(
                    agendamento.paciente_id,
                    agendamento.profissional_id,
                    item.procedimento_id,
                    item.quantidade,
                    agendamento_id=agendamento.pk,
                    excluir_item_pk=item.pk,
                )
        if (novo.pk if novo else None) != item.pacote_vendido_id:
            item.pacote_vendido = novo
            alterados.append(item)
    if alterados:
        AgendamentoItem.objects.bulk_update(alterados, ["pacote_vendido"])


def vincular_pacotes_futuros(paciente, profissional):
    """Logo depois de um pacote ser pago: os atendimentos que o paciente JÁ tinha marcados
    com o profissional (ex.: as 10 sessões agendadas antes da venda na 1ª sessão) passam a
    usar o saldo na hora, em ordem de data — assim a agenda já mostra o selo de pacote e o
    saldo exibido bate com o que está marcado. Devolve quantos atendimentos ficaram cobertos."""
    futuros = (
        Agendamento.objects.filter(
            paciente=paciente,
            profissional=profissional,
            forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
            status__in=[
                Agendamento.Status.AGENDADO,
                Agendamento.Status.PRE_CONFIRMADO,
                Agendamento.Status.CONFIRMADO,
                Agendamento.Status.PRE_AUTORIZADO,
            ],
            pagamentos__isnull=True,
        )
        .distinct()
        .order_by("data_hora_inicio", "pk")
    )
    cobertos = 0
    for agendamento in futuros:
        vincular_pacotes(agendamento)
        if agendamento.itens.filter(pacote_vendido__isnull=False).exists():
            cobertos += 1
    return cobertos


# Data (localdate) da última varredura feita por este processo — só uma vez por dia por
# worker, mesmo esquema de mensagens.models.apagar_mensagens_antigas (não há cron/agendador).
_ultima_atualizacao_status = None


def atualizar_status_do_dia(forcar=False):
    """
    Decisão do cliente (2026-10-16), roda sozinha no primeiro acesso de cada dia (ver
    agenda.middleware.AtualizarStatusDoDiaMiddleware):

    - Atendimentos de HOJE ainda "Agendado" viram "Pré-confirmado". Os marcados para hoje
      depois disso (encaixe) já nascem pré-confirmados em Agendamento.save().
    - Atendimentos de dias ANTERIORES que ficaram em "Agendado" ou "Pré-confirmado" (o
      paciente não foi confirmado nem atendido) viram "Faltou" — o "fim do dia" acontece na
      virada, no primeiro acesso do dia seguinte. Confirmado em diante não é tocado.

    Mesmo efeito do botão "Faltou" (que só grava o status): pacote e guia de convênio já
    ignoram atendimento com falta no saldo. Devolve (pré-confirmados, faltas).
    """
    global _ultima_atualizacao_status
    hoje = timezone.localdate()
    if not forcar and _ultima_atualizacao_status == hoje:
        return 0, 0
    pre_confirmados = Agendamento.objects.filter(
        status=Agendamento.Status.AGENDADO, data_hora_inicio__date=hoje
    ).update(status=Agendamento.Status.PRE_CONFIRMADO)
    faltas = Agendamento.objects.filter(
        status__in=[Agendamento.Status.AGENDADO, Agendamento.Status.PRE_CONFIRMADO],
        data_hora_inicio__date__lt=hoje,
    ).update(status=Agendamento.Status.FALTOU)
    _ultima_atualizacao_status = hoje
    return pre_confirmados, faltas
