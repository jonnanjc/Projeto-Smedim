"""
Prontuário unificado do paciente — registro clínico (consultas, exames,
procedimentos) preenchido opcionalmente pelo profissional quando marca um
atendimento como "Realizado" (ver agenda.views e prontuario.views).

Regra de visibilidade (pedido da clínica, 26/10/2026 — substitui a regra anterior,
em que só saúde mental era sigilosa e o resto ficava visível a todos):
  * registro de MÉDICO (CRM) é compartilhado — qualquer profissional vê, médico
    ou da equipe multi;
  * registro da EQUIPE MULTI (fisio, psicologia, fono, nutrição etc.) só aparece
    para quem o criou — nem colega da multi nem médico vê;
  * registro de saúde mental (inclusive Psiquiatria, que é médica) continua só
    do autor.
Quem não pode ver um registro não vê nem que ele EXISTE (some da linha do tempo),
não só o conteúdo — ver EntradaProntuarioQuerySet.visiveis_para e
EntradaProntuario.visivel_para.
"""
from django.db import models
from django.utils import timezone


# Especialidades tratadas como "saúde mental" para a regra de sigilo acima —
# comparação sempre case-insensitive contra o texto da especialidade do
# profissional (ver EntradaProntuario.especialidade, que é preenchido a
# partir de Profissional.especialidade_exibicao no momento da criação do
# registro). Cobrimos tanto as chaves usadas em
# cadastros.models.ESPECIALIDADES_SAUDE quanto variações de texto livre
# (campo "especialidade_outra"), por isso a checagem usa substring, não
# igualdade exata (ver `eh_especialidade_saude_mental` abaixo).
ESPECIALIDADES_SAUDE_MENTAL = (
    "psicologia",
    "psicólogo",
    "psicologo",
    "psiquiatria",
    "psiquiatra",
    "terapia",
    "psicoterapia",
)


def eh_especialidade_saude_mental(texto_especialidade):
    """True se o texto da especialidade (livre, vindo de Profissional.especialidade_exibicao
    ou digitado à mão) corresponder a uma especialidade de saúde mental — comparação
    case-insensitive e por substring, pra cobrir variações como 'Psicólogo clínico',
    'Médico psiquiatra', 'Psicoterapia individual' etc."""
    texto = (texto_especialidade or "").strip().lower()
    return any(chave in texto for chave in ESPECIALIDADES_SAUDE_MENTAL)


# Especialidade do sistema antigo (ver importar_prontuario_legado.ESPECIALIDADE_POR_CBOS) que é
# médica mas não tem "médic" no nome.
ESPECIALIDADES_MEDICAS_LEGADO = ("otorrinolaringologia",)


def eh_especialidade_medica(texto_especialidade):
    """True para médico: todas as chaves 'medico_*' de cadastros.ESPECIALIDADES_SAUDE começam
    com 'Médico', 'Outra' digitada como 'Médico ...' também, e no legado vem 'Medicina' /
    'Clínica médica' (ver ESPECIALIDADES_MEDICAS_LEGADO pras exceções)."""
    texto = (texto_especialidade or "").strip().lower()
    return "médic" in texto or "medic" in texto or texto in ESPECIALIDADES_MEDICAS_LEGADO


# Equipe multiprofissional DE FATO (pedido da clínica, 06/11/2026): a lista de evoluções
# pendentes da Minha agenda usava "tudo que não é médico" e acabava mostrando enfermeiro,
# dentista, farmacêutico, "Outra" digitada (ex.: ultrassonografia) e atendimento sem
# especialidade — virou uma lista gigante. Por substring, sem acento, pra cobrir também o
# texto livre de "Outra" (ex.: "Fisioterapia pélvica", "Neuropsicóloga").
PALAVRAS_EQUIPE_MULTI = (
    "fisio",
    "fono",
    "psicolog",
    "psicopedag",
    "nutricion",
    "ocupacional",
    "educador fisico",
    "educacao fisica",
)


def eh_equipe_multi(texto_especialidade):
    """True só pra especialidade da equipe multi (ver PALAVRAS_EQUIPE_MULTI) — médico nunca."""
    import unicodedata

    texto = unicodedata.normalize("NFKD", (texto_especialidade or "").lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    if eh_especialidade_medica(texto):
        return False
    return any(palavra in texto for palavra in PALAVRAS_EQUIPE_MULTI)


def usa_prontuario_simplificado(texto_especialidade):
    """Pedido da clínica (26/10/2026): os 6 temas do prontuário são demais pra equipe
    multiprofissional (fisioterapia, psicologia, fono, nutrição etc.) — quem não é médico
    registra num campo único de evolução (ver EntradaProntuario.evolucao). Médico
    continua com o formulário completo."""
    return not eh_especialidade_medica(texto_especialidade)


class EntradaProntuarioQuerySet(models.QuerySet):
    def visiveis_para(self, usuario):
        """
        Filtra as entradas que o `usuario` logado pode ver (regra no topo do arquivo):
        as que ele mesmo criou, mais as compartilhadas (registros médicos). Usada em toda tela que lista o prontuário do paciente (ver
        prontuario.views.prontuario_paciente) — nunca filtrar na template, sempre
        aqui, pra a entrada nem chegar a existir no contexto de quem não pode vê-la.
        """
        profissional_id = getattr(usuario, "profissional_id", None)
        visiveis_ids = [entrada.pk for entrada in self.all() if entrada._visivel(profissional_id)]
        return self.filter(pk__in=visiveis_ids).order_by("-criado_em")


class EntradaProntuario(models.Model):
    """Um registro clínico do paciente — uma consulta, exame ou procedimento realizado."""

    # Ordem de exibição dos temas — única fonte da ordem (usada tanto pelo formulário,
    # via Meta.fields, quanto por `secoes_preenchidas` abaixo); o rótulo de cada um vem
    # direto do verbose_name do campo, pra não duplicar o texto em dois lugares.
    CAMPOS_TEMATICOS = (
        "queixa_principal", "historico", "avaliacao",
        "hipotese_diagnostica", "conduta", "observacoes_adicionais",
    )
    # Formulário simplificado da equipe multi (ver usa_prontuario_simplificado) — campo
    # único, mostrado antes dos temas na tela do prontuário.
    CAMPO_SIMPLIFICADO = "evolucao"
    CAMPOS_EXIBICAO = (CAMPO_SIMPLIFICADO,) + CAMPOS_TEMATICOS

    paciente = models.ForeignKey(
        "cadastros.Paciente", verbose_name="paciente", on_delete=models.PROTECT, related_name="entradas_prontuario"
    )
    # Nulo só em entrada importada do sistema antigo cujo profissional não existe aqui
    # (ver profissional_legado_nome). Registro novo sempre tem profissional.
    profissional = models.ForeignKey(
        "cadastros.Profissional",
        verbose_name="profissional",
        on_delete=models.PROTECT,
        related_name="entradas_prontuario",
        null=True,
        blank=True,
    )
    profissional_legado_nome = models.CharField(
        "profissional (sistema antigo)",
        max_length=150,
        blank=True,
        editable=False,
        help_text="Nome do profissional como estava no sistema antigo — só para exibição, sem vínculo com cadastro daqui.",
    )
    especialidade = models.CharField(
        "especialidade",
        max_length=100,
        help_text="Preenchida automaticamente com a especialidade do profissional no momento do registro.",
    )
    agendamento = models.ForeignKey(
        "agenda.Agendamento",
        verbose_name="agendamento relacionado",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="entradas_prontuario",
        help_text="Vínculo opcional com o atendimento específico que originou este registro.",
    )
    # Registro clínico dividido em temas pré-definidos (pedido do cliente: prontuário
    # "mais completo") em vez de um único campo de texto livre — cobre tanto consulta
    # médica quanto sessão de psicologia/fisioterapia/etc. TODOS os campos são
    # opcionais (blank=True): cada profissional preenche só o que fizer sentido pra
    # especialidade/atendimento dele, nenhum é obrigatório.
    evolucao = models.TextField(
        "evolução / registro do atendimento", blank=True,
    )
    queixa_principal = models.TextField(
        "queixa principal / motivo do atendimento", blank=True,
    )
    historico = models.TextField(
        "histórico (anamnese)", blank=True,
    )
    avaliacao = models.TextField(
        "avaliação / exame", blank=True,
    )
    hipotese_diagnostica = models.TextField(
        "hipótese diagnóstica", blank=True,
        help_text="Inclua o CID aqui também, se aplicável — não há campo separado para o código.",
    )
    conduta = models.TextField(
        "conduta / plano terapêutico", blank=True,
    )
    observacoes_adicionais = models.TextField(
        "observações adicionais", blank=True,
    )

    # Pedido da clínica (2026-10-26): a equipe multi às vezes evolui o atendimento dias
    # depois — a data do ATENDIMENTO é escolhida no formulário (padrão: dia do agendamento,
    # ou hoje); `criado_em` continua sendo quando o registro foi digitado (auditoria).
    # Nulo só em registro antigo sem data informada — ver data_exibicao.
    data_atendimento = models.DateField("data do atendimento", null=True, blank=True)
    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    # Rastreio da migração do sistema antigo da clínica (PostgreSQL) — ver
    # prontuario/management/commands/importar_prontuario_legado.py. Mesmo
    # motivo/formato de cadastros.models.Paciente.origem_legado_id: guarda o
    # ID do registro NO SISTEMA ANTIGO, para casar entre execuções (os dois
    # sistemas convivem por um tempo, a importação roda mais de uma vez e não
    # pode duplicar entrada de prontuário).
    origem_legado_id = models.CharField(
        "ID no sistema antigo",
        max_length=64,
        null=True,
        blank=True,
        unique=True,
        editable=False,
        help_text="Preenchido automaticamente pela importação do sistema antigo — não editável na tela.",
    )
    importado_do_legado_em = models.DateTimeField(
        "importado do sistema antigo em",
        null=True,
        blank=True,
        editable=False,
    )

    objects = EntradaProntuarioQuerySet.as_manager()

    class Meta:
        verbose_name = "Entrada de Prontuário"
        verbose_name_plural = "Entradas de Prontuário"
        ordering = ["-data_atendimento", "-criado_em"]

    def __str__(self):
        return f"{self.paciente.nome_completo} — {self.especialidade} ({self.data_exibicao:%d/%m/%Y})"

    @property
    def data_exibicao(self):
        """Data do atendimento; em registro antigo sem ela, o dia em que foi digitado."""
        return self.data_atendimento or timezone.localdate(self.criado_em)

    @property
    def registrado_depois(self):
        """True quando o registro foi digitado em outro dia que não o do atendimento — a
        tela do prontuário mostra "registrado em ..." pra deixar isso claro."""
        return bool(self.data_atendimento) and timezone.localdate(self.criado_em) != self.data_atendimento

    @property
    def somente_leitura(self):
        """Entrada trazida do sistema antigo: fica só para consulta, ninguém edita (nem o autor)."""
        return bool(self.origem_legado_id)

    @property
    def profissional_exibicao(self):
        if self.profissional_id:
            return self.profissional.nome
        return self.profissional_legado_nome or "Não identificado"

    @property
    def sigiloso(self):
        """True se esta entrada é de uma especialidade de saúde mental (ver regra de sigilo no topo do arquivo)."""
        return eh_especialidade_saude_mental(self.especialidade)

    @property
    def secoes_preenchidas(self):
        """Lista de (rótulo, texto) só dos temas que o profissional realmente preencheu
        (todos são opcionais) — usada na tela de prontuário do paciente pra não desenhar
        uma linha vazia pra cada tema em toda entrada."""
        return [
            (self._meta.get_field(nome).verbose_name, valor)
            for nome in self.CAMPOS_EXIBICAO
            if (valor := getattr(self, nome))
        ]

    @property
    def compartilhada(self):
        """Registro de médico (fora saúde mental) — visível a todo profissional. O resto só
        o autor vê. Registro antigo com especialidade "Não informada" (sem conselho/CBOS no
        sistema antigo, ver importar_prontuario_legado) continua compartilhado como antes:
        sem dono aqui, ninguém mais conseguiria consultá-lo."""
        if self.sigiloso:
            return False
        return eh_especialidade_medica(self.especialidade) or (
            self.somente_leitura and self.especialidade == "Não informada"
        )

    def _visivel(self, profissional_id):
        if profissional_id and self.profissional_id == profissional_id:
            return True
        return self.compartilhada

    def visivel_para(self, usuario):
        """Confere se `usuario` pode ver ESTA entrada específica — usado como segunda
        camada de proteção (ex: acesso direto por URL), além do filtro em `visiveis_para`."""
        return self._visivel(getattr(usuario, "profissional_id", None))
