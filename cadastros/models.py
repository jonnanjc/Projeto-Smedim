"""
Modelos da Etapa 3 — Cadastros: Pacientes, Profissionais, Convênios e Procedimentos.

Uma diferença em relação ao desenho original do documento da Etapa 1: lá, o
"Convênio" misturava dados do convênio em si com a tabela de preços por
procedimento numa única entidade. Aqui isso foi separado em duas tabelas:

    Convenio              -> dados gerais do convênio (nome, código ANS)
    TabelaPrecoConvenio    -> preço de CADA procedimento, para CADA convênio

Isso evita ficar repetindo o nome do convênio a cada procedimento cadastrado,
e deixa mais fácil ver "todos os convênios" e "tabela de preços de um
convênio" separadamente — sem perder nenhuma informação do desenho original.
"""
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from accounts.moeda import formatar_moeda


def validador_cpf(valor):
    """
    Valida o CPF usando o cálculo padrão dos dois dígitos verificadores
    (módulo 11) — não só o formato. Aceita com ou sem pontuação. Como o
    campo continua opcional, quem chama isso só valida se algo foi digitado.
    """
    digitos = re.sub(r"\D", "", valor or "")
    if not digitos:
        return

    if len(digitos) != 11 or digitos == digitos[0] * 11:
        raise ValidationError("CPF inválido — confira os números digitados.")

    def _digito_verificador(cpf_parcial):
        soma = sum(int(d) * peso for d, peso in zip(cpf_parcial, range(len(cpf_parcial) + 1, 1, -1)))
        resto = (soma * 10) % 11
        return "0" if resto >= 10 else str(resto)

    digito_1 = _digito_verificador(digitos[:9])
    digito_2 = _digito_verificador(digitos[:9] + digito_1)
    if digitos[-2:] != digito_1 + digito_2:
        raise ValidationError("CPF inválido — confira os números digitados.")


# Lista curada de especialidades/ocupações de saúde, organizada com base nos
# grupos da CBO (Classificação Brasileira de Ocupações) — família 2251
# (Médicos) e demais famílias de profissões de saúde (223x/225x). Não
# incluímos o código numérico da CBO aqui porque a tabela oficial tem
# milhares de entradas e pode variar; quem precisar do código exato para
# fins de documentação oficial pode preenchê-lo no campo "Código CBO"
# (abaixo), à parte. Se a especialidade do profissional não estiver nesta
# lista, use a opção "Outra" e descreva no campo de texto.
ESPECIALIDADES_SAUDE = [
    ("Médicos", (
        ("medico_clinico_geral", "Médico clínico geral"),
        ("medico_acupunturista", "Médico acupunturista"),
        ("medico_alergista_imunologista", "Médico alergista e imunologista"),
        ("medico_anestesiologista", "Médico anestesiologista"),
        ("medico_angiologista", "Médico angiologista"),
        ("medico_cardiologista", "Médico cardiologista"),
        ("medico_cirurgiao_cabeca_pescoco", "Médico cirurgião de cabeça e pescoço"),
        ("medico_cirurgiao_geral", "Médico cirurgião geral"),
        ("medico_cirurgiao_pediatrico", "Médico cirurgião pediátrico"),
        ("medico_cirurgiao_plastico", "Médico cirurgião plástico"),
        ("medico_dermatologista", "Médico dermatologista"),
        ("medico_endocrinologista", "Médico endocrinologista e metabologista"),
        ("medico_endoscopista", "Médico endoscopista"),
        ("medico_gastroenterologista", "Médico gastroenterologista"),
        ("medico_geneticista", "Médico geneticista"),
        ("medico_geriatra", "Médico geriatra"),
        ("medico_ginecologista_obstetra", "Médico ginecologista e obstetra"),
        ("medico_hematologista", "Médico hematologista"),
        ("medico_infectologista", "Médico infectologista"),
        ("medico_mastologista", "Médico mastologista"),
        ("medico_nefrologista", "Médico nefrologista"),
        ("medico_neurologista", "Médico neurologista"),
        ("medico_nutrologo", "Médico nutrólogo"),
        ("medico_oftalmologista", "Médico oftalmologista"),
        ("medico_oncologista", "Médico oncologista clínico"),
        ("medico_ortopedista", "Médico ortopedista e traumatologista"),
        ("medico_otorrinolaringologista", "Médico otorrinolaringologista"),
        ("medico_pediatra", "Médico pediatra"),
        ("medico_pneumologista", "Médico pneumologista"),
        ("medico_psiquiatra", "Médico psiquiatra"),
        ("medico_radiologista", "Médico radiologista e diagnóstico por imagem"),
        ("medico_reumatologista", "Médico reumatologista"),
        ("medico_urologista", "Médico urologista"),
    )),
    ("Outras profissões de saúde", (
        ("cirurgiao_dentista", "Cirurgião-dentista"),
        ("enfermeiro", "Enfermeiro"),
        ("fisioterapeuta", "Fisioterapeuta"),
        ("fonoaudiologo", "Fonoaudiólogo"),
        ("nutricionista", "Nutricionista"),
        ("psicologo", "Psicólogo clínico"),
        ("terapeuta_ocupacional", "Terapeuta ocupacional"),
        ("biomedico", "Biomédico"),
        ("farmaceutico", "Farmacêutico"),
        ("assistente_social", "Assistente social"),
        ("educador_fisico", "Educador físico"),
    )),
    ("Outra", (
        ("outra", "Outra (especificar abaixo)"),
    )),
]

# Código oficial da CBO (Classificação Brasileira de Ocupações) para cada
# chave de ESPECIALIDADES_SAUDE acima — conferido nas tabelas públicas de
# operadoras de convênio (padrão TISS/ANS) e no portal ocupacoes.com.br
# (que replica a tabela do Ministério do Trabalho). Usado para preencher o
# campo "Código CBO" do Profissional automaticamente, sem precisar digitar.
# "educador_fisico" e "outra" não têm um código único de família — ficam sem
# preenchimento automático (o campo continua podendo ser ajustado à mão pelo
# Administrador em /admin/, se necessário).
ESPECIALIDADE_CBO = {
    "medico_clinico_geral": "225125",
    "medico_acupunturista": "225105",
    "medico_alergista_imunologista": "225110",
    "medico_anestesiologista": "225151",
    "medico_angiologista": "225115",
    "medico_cardiologista": "225120",
    "medico_cirurgiao_cabeca_pescoco": "225215",
    "medico_cirurgiao_geral": "225225",
    "medico_cirurgiao_pediatrico": "225230",
    "medico_cirurgiao_plastico": "225235",
    "medico_dermatologista": "225135",
    "medico_endocrinologista": "225155",
    "medico_endoscopista": "225310",
    "medico_gastroenterologista": "225165",
    "medico_geneticista": "225175",
    "medico_geriatra": "225180",
    "medico_ginecologista_obstetra": "225250",
    "medico_hematologista": "225185",
    "medico_infectologista": "225103",
    "medico_mastologista": "225255",
    "medico_nefrologista": "225109",
    "medico_neurologista": "225112",
    "medico_nutrologo": "225118",
    "medico_oftalmologista": "225265",
    "medico_oncologista": "225121",
    "medico_ortopedista": "225270",
    "medico_otorrinolaringologista": "225275",
    "medico_pediatra": "225124",
    "medico_pneumologista": "225127",
    "medico_psiquiatra": "225133",
    "medico_radiologista": "225320",
    "medico_reumatologista": "225136",
    "medico_urologista": "225285",
    "cirurgiao_dentista": "223208",
    "enfermeiro": "223505",
    "fisioterapeuta": "223605",
    "fonoaudiologo": "223810",
    "nutricionista": "223710",
    "psicologo": "251510",
    "terapeuta_ocupacional": "223905",
    "biomedico": "221205",
    "farmaceutico": "223405",
    "assistente_social": "251605",
}

# Emoji de apoio visual pra tela "Bem-vindo" do totem (fila.views.
# totem_especialidade) — mesmo espírito do ícone em fila.views.MOTIVOS_
# PRIORIDADE: é só reforço de leitura rápida, o rótulo continua sendo a
# informação principal. Chave sem entrada aqui (ex: "outra", ou uma
# especialidade nova ainda não mapeada) cai no padrão de especialidade_emoji().
ESPECIALIDADE_EMOJI = {
    "medico_clinico_geral": "🩺",
    "medico_acupunturista": "🪡",
    "medico_alergista_imunologista": "🤧",
    "medico_anestesiologista": "💉",
    "medico_angiologista": "🩸",
    "medico_cardiologista": "❤️",
    "medico_cirurgiao_cabeca_pescoco": "🔪",
    "medico_cirurgiao_geral": "🔪",
    "medico_cirurgiao_pediatrico": "🧸",
    "medico_cirurgiao_plastico": "✨",
    "medico_dermatologista": "🧴",
    "medico_endocrinologista": "⚖️",
    "medico_endoscopista": "📷",
    "medico_gastroenterologista": "🍽️",
    "medico_geneticista": "🧬",
    "medico_geriatra": "🧓",
    "medico_ginecologista_obstetra": "🤰",
    "medico_hematologista": "🩸",
    "medico_infectologista": "🦠",
    "medico_mastologista": "🎗️",
    "medico_nefrologista": "💧",
    "medico_neurologista": "🧠",
    "medico_nutrologo": "🥗",
    "medico_oftalmologista": "👁️",
    "medico_oncologista": "🎗️",
    "medico_ortopedista": "🦴",
    "medico_otorrinolaringologista": "👂",
    "medico_pediatra": "🧒",
    "medico_pneumologista": "🫁",
    "medico_psiquiatra": "🧠",
    "medico_radiologista": "🩻",
    "medico_reumatologista": "🦴",
    "medico_urologista": "💧",
    "cirurgiao_dentista": "🦷",
    "enfermeiro": "🩹",
    "fisioterapeuta": "🏃",
    "fonoaudiologo": "🗣️",
    "nutricionista": "🥗",
    "psicologo": "💬",
    "terapeuta_ocupacional": "🧩",
    "biomedico": "🔬",
    "farmaceutico": "💊",
    "assistente_social": "🤝",
    "educador_fisico": "🏋️",
    "outra": "🏥",
}

# Rótulos "achatados" de ESPECIALIDADES_SAUDE (que é agrupado, em formato de
# optgroup) — montado uma vez aqui pra especialidade_rotulo() não precisar
# varrer os grupos a cada chamada.
_ESPECIALIDADES_SAUDE_ROTULOS = {
    chave: rotulo for _grupo, opcoes in ESPECIALIDADES_SAUDE for chave, rotulo in opcoes
}


def especialidade_rotulo(especialidade, especialidade_outra=""):
    """
    Nome de especialidade pronto pra mostrar na tela (considera a opção "Outra"),
    a partir dos valores "soltos" de especialidade/especialidade_outra — não exige
    uma instância de Profissional (que teria get_especialidade_display() de
    graça). Usada por Profissional.especialidade_exibicao e por qualquer outro
    registro que guarda essas duas colunas fora de Profissional (fila.Senha,
    AtendimentoEspecialidade, EspecialidadePadraoUsuario) — mesma fonte, mesmo
    rótulo, em todo lugar que precisar exibir uma especialidade.
    """
    if especialidade == "outra":
        return especialidade_outra or "Outra"
    if not especialidade:
        return "—"
    return _ESPECIALIDADES_SAUDE_ROTULOS.get(especialidade, especialidade)


def especialidade_emoji(especialidade):
    """Emoji de apoio visual pra especialidade (ver ESPECIALIDADE_EMOJI); 🏥 se não mapeada."""
    return ESPECIALIDADE_EMOJI.get(especialidade, "🏥")


def especialidade_a_partir_da_chave(chave):
    """
    Desfaz a "chave" usada nos formulários/telas que listam especialidades
    (ex: Profissional.especialidades_cadastradas) — string "especialidade|
    especialidade_outra" — de volta para as duas colunas separadas, prontas
    pra gravar em Senha/AtendimentoEspecialidade/EspecialidadePadraoUsuario.
    """
    especialidade, _, especialidade_outra = chave.partition("|")
    return especialidade, especialidade_outra


class Profissional(models.Model):
    """Médico ou demais profissionais de saúde da clínica — sempre autônomos (ver decisão da Etapa 3)."""

    nome = models.CharField("nome completo", max_length=150)
    registro_profissional = models.CharField(
        "registro profissional",
        max_length=30,
        help_text="CRM ou registro do conselho de classe equivalente (ex: CRM 12345-SP, COREN 98765).",
    )
    especialidade = models.CharField(
        "especialidade",
        max_length=50,
        choices=ESPECIALIDADES_SAUDE,
        blank=True,
        help_text="Baseado na classificação de ocupações de saúde da CBO. Não encontrou? Escolha 'Outra'.",
    )
    especialidade_outra = models.CharField(
        "qual especialidade (se 'Outra')",
        max_length=100,
        blank=True,
    )
    cbo_codigo = models.CharField(
        "código CBO",
        max_length=10,
        blank=True,
        help_text="Preenchido automaticamente com base na especialidade escolhida acima (padrão oficial da CBO).",
    )

    percentual_desconto = models.DecimalField(
        "percentual de desconto do profissional",
        max_digits=5,
        decimal_places=2,
        help_text=(
            "Percentual que a clínica desconta/retém sobre o valor de CADA procedimento que este profissional "
            "realizar (ex: 30,00 para 30% retido pela clínica — o restante fica com o profissional). "
            "Definido no cadastro e aplicado a todos os procedimentos dele."
        ),
    )

    # Pedido do cliente (2026-09-28): alguns profissionais cobram um valor próprio por
    # procedimento no PARTICULAR e recebem sempre um valor fixo de repasse por ele — nada de
    # percentual sobre a produção. Quando marcado, o preço particular e o repasse de cada
    # procedimento vêm de ValorFixoProfissional (abaixo), e um procedimento sem linha lá NÃO
    # pode ser agendado como particular (decisão do cliente: bloqueia, não cai no padrão).
    # Convênio não muda: preço pela tabela do convênio e repasse por percentual_desconto.
    cobra_valor_fixo_particular = models.BooleanField(
        "cobra valor fixo no particular (sem percentual)",
        default=False,
        help_text=(
            "Marque para profissionais que definem o próprio valor de cada procedimento/consulta "
            "particular e recebem sempre um valor fixo de repasse, em vez do percentual acima. Os "
            "valores ficam na tabela 'Valores fixos no particular' logo abaixo — procedimento que não "
            "estiver nela não pode ser agendado como particular para este profissional. O percentual "
            "acima continua valendo para os atendimentos por convênio."
        ),
    )

    limite_atendimento_simultaneo = models.PositiveSmallIntegerField(
        "quantos pacientes pode atender ao mesmo tempo",
        default=1,
        validators=[MinValueValidator(1)],
        help_text=(
            "Quantos atendimentos deste profissional podem se sobrepor no mesmo horário sem contar como "
            "conflito de agenda. Deixe 1 para o caso normal (um atendimento por vez, o padrão da maioria "
            "dos profissionais). Aumente para profissionais que atendem vários pacientes ao mesmo tempo — "
            "ex: fisioterapeuta com várias pessoas na sala de ginástica/aparelhos, ou acupuntura com um "
            "paciente 'descansando' com as agulhas aplicadas enquanto outro já é atendido."
        ),
    )

    # Pedido da clínica (06/11/2026), só pra um profissional específico: ele aplica as agulhas
    # e atende consultas/ultrassons enquanto o paciente fica na sessão — ver
    # agenda.views._ordena_fila_de_chamada.
    acupuntura_primeiro_na_fila = models.BooleanField(
        "acupuntura no começo da fila",
        default=False,
        help_text=(
            "Na Minha agenda deste profissional, paciente de acupuntura já autorizado passa na frente de "
            "quem aguarda (até de prioridade) — para aplicar as agulhas e atender os outros enquanto isso."
        ),
    )

    eh_proprietario = models.BooleanField(
        "é o proprietário da clínica",
        default=False,
        help_text=(
            "Produção própria não sofre percentual_desconto (fica com 100% do que atende) — a retenção só "
            "se aplica aos demais profissionais. No fechamento diário/mensal, o proprietário também vê a "
            "retenção da clínica sobre todos os outros profissionais. Só pode haver um."
        ),
    )

    # Pedido do cliente (2026-10-14): na hora de agendar, só aparecem os convênios que o
    # profissional atende (e que têm preço para algum procedimento dele) — ver
    # agenda.views.procedimentos_do_profissional e AgendamentoForm.clean(). Nenhum marcado
    # = só particular.
    convenios_atendidos = models.ManyToManyField(
        "Convenio",
        verbose_name="convênios que atende",
        related_name="profissionais_que_atendem",
        blank=True,
        help_text=(
            "Marque os convênios/planos que este profissional atende. Na hora de agendar, só aparecem "
            "estes convênios (e só os procedimentos com preço cadastrado no convênio escolhido). "
            "Nenhum marcado = atende só particular."
        ),
    )

    ativo = models.BooleanField("ativo", default=True)

    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Profissional"
        verbose_name_plural = "Profissionais"
        ordering = ["nome"]

    def __str__(self):
        return f"{self.nome} ({self.registro_profissional})" if self.registro_profissional else self.nome

    def clean(self):
        if self.eh_proprietario:
            outro = Profissional.objects.filter(eh_proprietario=True).exclude(pk=self.pk).first()
            if outro:
                raise ValidationError({
                    "eh_proprietario": f"Já existe um proprietário cadastrado ({outro.nome}) — "
                    "desmarque o atual antes de definir outro.",
                })

    def save(self, *args, **kwargs):
        # Preenche o código CBO sozinho, com base na especialidade escolhida — ver ESPECIALIDADE_CBO
        # acima. Para "outra" (especialidade fora da lista) ou "educador_fisico" (sem código único de
        # família na CBO), não tem preenchimento automático; o campo continua editável manualmente.
        if self.especialidade in ESPECIALIDADE_CBO:
            self.cbo_codigo = ESPECIALIDADE_CBO[self.especialidade]
        super().save(*args, **kwargs)

    @property
    def especialidade_exibicao(self):
        """Nome da especialidade pronto para mostrar na tela (considera a opção 'Outra')."""
        return especialidade_rotulo(self.especialidade, self.especialidade_outra)

    @property
    def especialidades_pares(self):
        """
        Lista deduplicada de (especialidade, especialidade_outra): a principal (campos
        deste model, se preenchida) seguida das adicionais (ProfissionalEspecialidade,
        abaixo) — fonte única usada por especialidades_cadastradas/especialidades_ativas_em/
        ativos_com_especialidades para considerar TODAS as especialidades do profissional,
        não só a principal (ver bug: profissional com 2+ especialidades não aparecia na
        Central de Atendimento nem no totem de senha).
        """
        pares, vistos = [], set()
        if self.especialidade:
            par = (self.especialidade, self.especialidade_outra if self.especialidade == "outra" else "")
            pares.append(par)
            vistos.add(par)
        for adicional in self.especialidades_adicionais.all():
            par = (adicional.especialidade, adicional.especialidade_outra if adicional.especialidade == "outra" else "")
            if par not in vistos:
                pares.append(par)
                vistos.add(par)
        return pares

    @property
    def usuario_vinculado(self):
        """
        Usuário de login criado automaticamente para este profissional (ver
        cadastros.views._criar_login_automatico_profissional), se houver.
        Não é uma ForeignKey de verdade porque accounts.Usuario guarda só o
        ID (accounts.models.Usuario.profissional_id) — ver comentário lá.
        """
        from django.contrib.auth import get_user_model

        return get_user_model().objects.filter(profissional_id=self.pk).first()

    def valor_fixo_para(self, procedimento):
        """Linha de ValorFixoProfissional deste profissional para o procedimento, ou None —
        só faz sentido quando cobra_valor_fixo_particular está marcado."""
        return self.valores_fixos.filter(procedimento=procedimento).first()

    def procedimentos_com_preco_no(self, convenio):
        """Procedimentos ativos habilitados para este profissional que têm preço no convênio —
        preço geral ou específico DELE (preço específico de outro profissional não conta; mesma
        regra de Convenio.valor_para). Vazio se ele não atende o convênio (convenios_atendidos)."""
        if not self.convenios_atendidos.filter(pk=convenio.pk).exists():
            return Procedimento.objects.none()
        return self.procedimentos_habilitados.filter(
            Q(precos_convenios__profissional__isnull=True) | Q(precos_convenios__profissional=self),
            ativo=True,
            precos_convenios__convenio=convenio,
        ).distinct()

    def convenios_para_agendar(self):
        """Convênios que aparecem na hora de agendar com este profissional (pedido do cliente,
        2026-10-14): os que ele atende E que têm preço para pelo menos um procedimento dele."""
        precos = TabelaPrecoConvenio.objects.filter(
            Q(profissional__isnull=True) | Q(profissional=self),
            procedimento__profissionais_habilitados=self,
            procedimento__ativo=True,
        )
        return (
            self.convenios_atendidos.filter(ativo=True, pk__in=precos.values("convenio_id"))
            .order_by("nome")
        )

    def sala_em(self, data):
        """Sala escolhida por este profissional para a data informada (ver AtendimentoSala), ou None."""
        registro = self.atendimentos_sala.filter(data=data).order_by("-criado_em").first()
        return registro.sala if registro else None

    def bloco_atendimento_em(self, data, hora):
        """
        Devolve o bloco de horário (HorarioAtendimento ou ExcecaoAtendimento)
        que cobre esse dia+hora para este profissional, ou None se não achar
        nenhum bloco cadastrado cobrindo esse horário.

        Uma exceção cadastrada para aquela DATA específica (ver
        ExcecaoAtendimento) tem prioridade sobre o horário fixo semanal —
        assim dá pra tratar casos como "esse médico normalmente faz
        ultrassom de manhã, mas no dia 20/07 ele vai fazer à tarde" sem
        mexer no padrão semanal dele.
        """
        excecoes = list(self.excecoes_atendimento.filter(data=data))
        blocos = excecoes if excecoes else list(self.horarios_atendimento.filter(dia_semana=data.weekday()))
        for bloco in blocos:
            if bloco.horario_inicio <= hora < bloco.horario_fim:
                return bloco
        return None

    def bloco_do_turno_em(self, data, turno):
        """
        Bloco (HorarioAtendimento ou ExcecaoAtendimento) deste profissional no turno
        informado, nessa data — mesma prioridade de bloco_atendimento_em (exceção da data
        substitui o horário semanal). Devolve (bloco, atende_no_dia): bloco None com
        atende_no_dia=True = ele atende nesse dia, mas não nesse turno. Usado pelo
        agendamento de consulta/exame, que é por turno, sem hora marcada.
        """
        excecoes = list(self.excecoes_atendimento.filter(data=data))
        blocos = excecoes if excecoes else list(self.horarios_atendimento.filter(dia_semana=data.weekday()))
        return next((b for b in blocos if b.turno == turno), None), bool(blocos)

    def atende_no_dia(self, data):
        """
        True se este profissional tem algum bloco de atendimento (horário fixo ou
        exceção) cobrindo esta data — mesma regra de prioridade de
        bloco_atendimento_em (uma exceção cadastrada pra essa data substitui
        completamente o horário fixo semanal), só que sem exigir uma hora
        específica. Usado por especialidades_ativas_em para descobrir quais
        especialidades têm alguém de plantão num dia, sem duplicar essa regra
        uma terceira vez.
        """
        if self.excecoes_atendimento.filter(data=data).exists():
            return True
        return self.horarios_atendimento.filter(dia_semana=data.weekday()).exists()

    def atende_ainda_em(self, data, hora):
        """
        True se, nesta data, ainda resta algum bloco de atendimento dele que não terminou
        até `hora` — mesma prioridade de atende_no_dia (exceção da data substitui o horário
        semanal). Pedido da clínica (23/10/2026): o totem esconde a especialidade quando o
        horário do profissional acaba (atende das 07h às 11h → às 11:01 some). Antes do
        início continua aparecendo (o paciente chega cedo e espera).
        """
        excecoes = self.excecoes_atendimento.filter(data=data)
        blocos = excecoes if excecoes.exists() else self.horarios_atendimento.filter(dia_semana=data.weekday())
        return blocos.filter(horario_fim__gt=hora).exists()

    def _presente(self, data, hora):
        return self.atende_no_dia(data) if hora is None else self.atende_ainda_em(data, hora)

    @classmethod
    def especialidades_cadastradas(cls, profissionais=None):
        """
        Lista (chave, rótulo) das especialidades distintas entre os profissionais
        informados — por padrão, todos os ativos, sem filtro de data. Considera
        TODAS as especialidades de cada profissional (a principal E as adicionais,
        ver especialidades_pares), não só a principal — um profissional com 2+
        especialidades entra em cada uma delas. `chave` é "especialidade|especialidade_outra"
        (mesmo formato já usado em agenda.views.central_atendimento), pronta pra virar
        `value` de checkbox/opção de formulário e voltar depois com
        especialidade_a_partir_da_chave().

        Fonte única reaproveitada pelo totem de senha (especialidades_ativas_em,
        abaixo), pela atribuição de especialidades do dia da recepção e pelo
        cadastro de especialidades padrão do usuário — nenhum desses lugares
        mantém uma lista de especialidades própria.
        """
        if profissionais is None:
            profissionais = cls.objects.filter(ativo=True).prefetch_related("especialidades_adicionais")
        vistas = {}
        for profissional in profissionais:
            for especialidade, especialidade_outra in profissional.especialidades_pares:
                chave = f"{especialidade}|{especialidade_outra}"
                if chave not in vistas:
                    vistas[chave] = especialidade_rotulo(especialidade, especialidade_outra)
        return sorted(vistas.items(), key=lambda item: item[1])

    @classmethod
    def especialidades_ativas_em(cls, data, hora=None):
        """
        Só as especialidades com pelo menos um profissional ativo atendendo
        NESTA data específica (ver atende_no_dia) — usada no totem de senha e na
        atribuição de especialidades do dia da recepção, pra nunca oferecer uma
        especialidade que ninguém atende hoje. Com `hora` (só o totem), conta só quem
        ainda não terminou o horário (ver atende_ainda_em); a recepção continua vendo o dia todo.
        """
        ativos = cls.objects.filter(ativo=True).prefetch_related("especialidades_adicionais")
        presentes = [profissional for profissional in ativos if profissional._presente(data, hora)]
        return cls.especialidades_cadastradas(presentes)

    @classmethod
    def presentes_por_especialidade(cls, data, hora=None):
        """
        {chave "especialidade|especialidade_outra": [profissionais ativos atendendo NESTA
        data, por nome]} — mesma regra de especialidades_ativas_em. Usado pelo totem (tela
        "Com qual profissional?", só quando a especialidade tem 2+) e pela recepção ao
        escolher quais profissionais cobre (ver AtendimentoProfissional).
        """
        ativos = cls.objects.filter(ativo=True).prefetch_related("especialidades_adicionais").order_by("nome")
        por_especialidade = {}
        for profissional in ativos:
            if not profissional._presente(data, hora):
                continue
            for especialidade, especialidade_outra in profissional.especialidades_pares:
                por_especialidade.setdefault(f"{especialidade}|{especialidade_outra}", []).append(profissional)
        return por_especialidade

    @classmethod
    def ativos_com_especialidades(cls, pares):
        """
        Profissionais ativos com pelo menos uma especialidade (principal OU
        adicional) entre `pares` (lista de (especialidade, especialidade_outra),
        mesmo formato de especialidades_cadastradas/especialidades_ativas_em acima).
        Usada pela Agenda do dia (ver agenda.views.agenda_dia) para restringir a
        grade às especialidades marcadas em "Especialidades de hoje"
        (AtendimentoEspecialidade) — `especialidade_outra` só entra na condição
        quando especialidade == "outra", mesma ressalva de especialidades_cadastradas.

        .distinct() é necessário (não só defensivo): um profissional pode bater em
        mais de uma condição via o join com especialidades_adicionais, o que devolveria
        a mesma linha duplicada sem isso.
        """
        if not pares:
            return cls.objects.none()
        condicoes = models.Q()
        for especialidade, especialidade_outra in pares:
            if especialidade == "outra":
                condicoes |= models.Q(especialidade="outra", especialidade_outra=especialidade_outra)
                condicoes |= models.Q(
                    especialidades_adicionais__especialidade="outra",
                    especialidades_adicionais__especialidade_outra=especialidade_outra,
                )
            else:
                condicoes |= models.Q(especialidade=especialidade)
                condicoes |= models.Q(especialidades_adicionais__especialidade=especialidade)
        return cls.objects.filter(condicoes, ativo=True).distinct()


class ProfissionalEspecialidade(models.Model):
    """
    Especialidade ADICIONAL de um profissional, além da principal (que continua em
    Profissional.especialidade/especialidade_outra, nunca duplicada aqui) — existe
    porque um profissional pode atender por mais de uma especialidade (ex: Clínico
    Geral e Acupunturista). Mesma forma de EspecialidadePadraoUsuario/
    AtendimentoEspecialidade (mais abaixo), só que para Profissional em vez de Usuario.
    Ver Profissional.especialidades_pares.
    """

    profissional = models.ForeignKey(
        Profissional,
        verbose_name="profissional",
        on_delete=models.CASCADE,
        related_name="especialidades_adicionais",
    )
    especialidade = models.CharField("especialidade", max_length=50, choices=ESPECIALIDADES_SAUDE)
    especialidade_outra = models.CharField("qual especialidade (se 'Outra')", max_length=100, blank=True)

    class Meta:
        verbose_name = "Especialidade adicional (profissional)"
        verbose_name_plural = "Especialidades adicionais (profissionais)"
        ordering = ["profissional__nome", "especialidade"]
        constraints = [
            models.UniqueConstraint(
                fields=["profissional", "especialidade", "especialidade_outra"],
                name="unica_especialidade_adicional_por_profissional",
            ),
        ]

    def __str__(self):
        return f"{self.profissional.nome} — {self.especialidade_exibicao}"

    @property
    def especialidade_exibicao(self):
        return especialidade_rotulo(self.especialidade, self.especialidade_outra)


class ValorFixoProfissional(models.Model):
    """
    Valor cobrado e valor repassado FIXOS de um procedimento particular para um profissional
    com Profissional.cobra_valor_fixo_particular marcado (pedido do cliente, 2026-09-28).
    valor_cobrado substitui Procedimento.valor_particular na cobrança (ver
    agenda.models.AgendamentoItem.valor_a_cobrar); valor_repasse substitui o percentual no
    repasse, e é congelado em agenda.models.PagamentoParticular.valor_repasse_fixo no momento
    do pagamento — mudar a tabela aqui depois nunca reescreve repasse já registrado.
    """

    profissional = models.ForeignKey(
        Profissional, verbose_name="profissional", on_delete=models.CASCADE, related_name="valores_fixos"
    )
    procedimento = models.ForeignKey(
        "Procedimento", verbose_name="procedimento", on_delete=models.CASCADE, related_name="valores_fixos"
    )
    valor_cobrado = models.DecimalField(
        "valor cobrado do paciente", max_digits=8, decimal_places=2, validators=[MinValueValidator(0)]
    )
    valor_repasse = models.DecimalField(
        "valor repassado ao profissional", max_digits=8, decimal_places=2, validators=[MinValueValidator(0)]
    )

    class Meta:
        verbose_name = "Valor fixo no particular (profissional)"
        verbose_name_plural = "Valores fixos no particular (profissionais)"
        ordering = ["profissional__nome", "procedimento__nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["profissional", "procedimento"], name="unico_valor_fixo_por_profissional_procedimento"
            ),
        ]

    def __str__(self):
        return f"{self.profissional.nome} — {self.procedimento.nome}: R$ {formatar_moeda(self.valor_cobrado)} (repasse R$ {formatar_moeda(self.valor_repasse)})"

    def clean(self):
        if self.valor_cobrado is not None and self.valor_repasse is not None and self.valor_repasse > self.valor_cobrado:
            raise ValidationError({"valor_repasse": "O repasse não pode ser maior que o valor cobrado do paciente."})


# Dias da semana em que o profissional pode atender — só segunda a sexta,
# porque a clínica não funciona aos fins de semana (mesma regra já aplicada
# em agenda.models.Agendamento.clean()). weekday(): Segunda=0 ... Sexta=4.
DIAS_SEMANA_ATENDIMENTO = [
    (0, "Segunda-feira"),
    (1, "Terça-feira"),
    (2, "Quarta-feira"),
    (3, "Quinta-feira"),
    (4, "Sexta-feira"),
]


class HorarioAtendimento(models.Model):
    """
    Um "bloco" de horário em que um Profissional atende, num dia da semana
    específico (ex: "Dra. Ana atende Segunda de manhã, das 8h às 12h").

    Um mesmo profissional pode ter vários blocos no mesmo dia (ex: manhã E
    tarde) — por isso não é um campo direto no Profissional, é uma tabela à
    parte com vários registros por profissional.

    Isso existe para a Central de Atendimento saber exatamente quando cada
    profissional está disponível, e não oferecer como "vago" um horário em
    que ele nem atende (ver agenda.views._monta_disponibilidade).
    """

    class Turno(models.TextChoices):
        MANHA = "MANHA", "Manhã (8h às 12h)"
        TARDE = "TARDE", "Tarde (14h às 18h)"
        NOITE = "NOITE", "Noite (18h às 20h)"

    # Horário padrão sugerido de cada turno — usado só para pré-preencher o
    # formulário; o horário de início/término de cada bloco continua editável
    # à parte (ex: um profissional pode atender das 8h30 às 11h30).
    HORARIO_PADRAO_POR_TURNO = {
        Turno.MANHA: ("08:00", "12:00"),
        Turno.TARDE: ("14:00", "18:00"),
        Turno.NOITE: ("18:00", "20:00"),
    }

    profissional = models.ForeignKey(
        Profissional, verbose_name="profissional", on_delete=models.CASCADE, related_name="horarios_atendimento"
    )
    dia_semana = models.PositiveSmallIntegerField("dia da semana", choices=DIAS_SEMANA_ATENDIMENTO)
    turno = models.CharField("turno", max_length=10, choices=Turno.choices)
    horario_inicio = models.TimeField("horário de início")
    horario_fim = models.TimeField("horário de término")
    procedimentos_permitidos = models.ManyToManyField(
        "Procedimento",
        verbose_name="procedimentos permitidos neste bloco",
        related_name="horarios_atendimento",
        blank=True,
        help_text=(
            "Deixe em branco para permitir qualquer procedimento neste bloco. Selecione procedimentos "
            "específicos para restringir (ex: só 'Ultrassom' no bloco de segunda de manhã)."
        ),
    )

    class Meta:
        verbose_name = "Horário de Atendimento"
        verbose_name_plural = "Horários de Atendimento"
        ordering = ["profissional__nome", "dia_semana", "horario_inicio"]
        constraints = [
            models.UniqueConstraint(
                fields=["profissional", "dia_semana", "turno"], name="unico_turno_por_profissional_e_dia"
            ),
        ]

    def __str__(self):
        return (
            f"{self.profissional.nome} — {self.get_dia_semana_display()} "
            f"({self.horario_inicio:%H:%M} às {self.horario_fim:%H:%M})"
        )

    def clean(self):
        if self.horario_inicio and self.horario_fim and self.horario_fim <= self.horario_inicio:
            raise ValidationError("O horário de término deve ser depois do horário de início.")


class ExcecaoAtendimento(models.Model):
    """
    Substitui, só para uma DATA específica, o horário fixo semanal de um
    profissional (ver HorarioAtendimento) — para casos pontuais como "esse
    médico normalmente faz ultrassom de manhã, mas no dia 20/07 vai fazer
    à tarde".

    Quando existe pelo menos uma exceção cadastrada para uma data, ela
    substitui COMPLETAMENTE o horário semanal daquele profissional só
    naquele dia (ver Profissional.bloco_atendimento_em) — o horário fixo
    semanal continua intacto para as outras semanas.
    """

    profissional = models.ForeignKey(
        Profissional, verbose_name="profissional", on_delete=models.CASCADE, related_name="excecoes_atendimento"
    )
    data = models.DateField("data")
    turno = models.CharField("turno", max_length=10, choices=HorarioAtendimento.Turno.choices)
    horario_inicio = models.TimeField("horário de início")
    horario_fim = models.TimeField("horário de término")
    procedimentos_permitidos = models.ManyToManyField(
        "Procedimento",
        verbose_name="procedimentos permitidos neste bloco",
        related_name="excecoes_atendimento",
        blank=True,
        help_text="Deixe em branco para permitir qualquer procedimento neste bloco, nesta data.",
    )

    class Meta:
        verbose_name = "Exceção de Atendimento"
        verbose_name_plural = "Exceções de Atendimento"
        ordering = ["data", "horario_inicio"]
        constraints = [
            models.UniqueConstraint(fields=["profissional", "data", "turno"], name="unica_excecao_por_profissional_data_turno"),
        ]

    def __str__(self):
        return f"{self.profissional.nome} — {self.data:%d/%m/%Y} ({self.horario_inicio:%H:%M} às {self.horario_fim:%H:%M})"

    def clean(self):
        if self.horario_inicio and self.horario_fim and self.horario_fim <= self.horario_inicio:
            raise ValidationError("O horário de término deve ser depois do horário de início.")
        if self.data and self.data.weekday() >= 5:
            raise ValidationError("A clínica não funciona aos sábados e domingos — escolha uma data de segunda a sexta.")


class Procedimento(models.Model):
    """Catálogo de consultas e procedimentos oferecidos pela clínica."""

    nome = models.CharField("nome", max_length=150)
    codigo = models.CharField(
        "código",
        max_length=20,
        blank=True,
        help_text="Código do procedimento com base na tabela TUSS ou CBHPM (se aplicável).",
    )
    profissionais_habilitados = models.ManyToManyField(
        Profissional,
        verbose_name="profissionais habilitados",
        related_name="procedimentos_habilitados",
        blank=True,
        help_text="Quem pode realizar este procedimento/consulta.",
    )
    recursos_necessarios = models.CharField(
        "recursos necessários",
        max_length=200,
        blank=True,
        help_text="Sala/equipamento necessário, se houver.",
    )
    permite_repeticao_semanal = models.BooleanField(
        "permite repetição semanal (sessão)",
        default=False,
        help_text=(
            "Marque para procedimentos do tipo SESSÃO (fono, fisioterapia, psicologia, acupuntura...), que se "
            "repetem periodicamente toda semana. Deixe desmarcado para CONSULTA — consulta não se repete "
            "automaticamente (o retorno é agendado à parte, quando o profissional pedir)."
        ),
    )
    eh_ultrassom = models.BooleanField(
        "é um tipo de ultrassom (USG)",
        default=False,
        help_text=(
            "Marque para qualquer exame de ultrassom (ex: 'Ultrassom abdominal', 'Ultrassom obstétrico'...). "
            "Na tela de Horários/Exceções do profissional, todos os procedimentos marcados assim aparecem "
            "agrupados como um único 'USG', em vez de precisar marcar cada tipo de exame separadamente."
        ),
    )
    eh_consulta = models.BooleanField(
        "é uma consulta",
        default=False,
        help_text=(
            "Marque para procedimentos do tipo CONSULTA (não exame, não sessão). É o que identifica, no "
            "Agendamento, quais atendimentos abrem/fecham o direito a retorno grátis (30 dias corridos, "
            "ou o prazo de retorno do convênio) na mesma especialidade (ver agenda.models.Agendamento.buscar_consulta_para_retorno) — exame "
            "(ex: Ultrassom) e sessão (ex: Fisioterapia, Acupuntura) NUNCA contam, mesmo dentro da janela "
            "de retorno. Campo normal e editável — a migração que o introduziu já marcou os procedimentos "
            "existentes automaticamente (ver migração), mas procedimentos novos precisam ser marcados à "
            "mão aqui por quem cadastra."
        ),
    )
    # Decisão da clínica, 2026-07-30: alguns exames são feitos pela equipe sem o
    # profissional precisar estar atendendo naquele dia (ex: colocação do aparelho de
    # MAPA — o cardiologista avalia o resultado depois, sem precisar estar na clínica na
    # hora da colocação). Profissional.especialidades_ativas_em (usada pelo totem de senha
    # e por "especialidades de hoje" da recepção) só olha o horário fixo/exceção do
    # profissional — por isso, sem esta marcação, um paciente vindo só pelo MAPA não
    # conseguia tirar senha nem a recepção conseguia cobrir a especialidade nesse dia.
    # Ver agenda.models.Agendamento.exames_avulsos_agendados_em (só oferece o cartão de
    # Exame no totem quando há de fato um agendamento hoje para este procedimento — nunca
    # "sempre disponível", pra não gerar senha de exame que ninguém da equipe espera).
    exame_sem_profissional = models.BooleanField(
        "exame feito sem o profissional atendendo no dia",
        default=False,
        help_text=(
            "Marque para exames que a equipe realiza sem precisar do profissional atendendo "
            "naquele dia (ex: colocação do aparelho de MAPA). No totem de senha, procedimentos "
            "marcados assim ganham um cartão de 'Exame' à parte — disponível mesmo em dias sem "
            "nenhum profissional da especialidade atendendo, mas só quando há um agendamento de "
            "verdade para este exame no dia."
        ),
    )
    # Exames que exigem preparo do paciente. Pedido da clínica (2026-10-26): a prioridade é
    # na recepção — o totem oferece a senha "Exame com preparo" (fila.models.Senha.Tipo.
    # EXAME_PREPARO), chamada antes das demais por ordem de chegada. Na fila do profissional
    # vale a ordem de autorização (até 2026-10-26 passava na frente lá; ver agenda.views.
    # _ordena_fila_de_chamada). A Minha agenda só mostra o selo "Exame com preparo".
    exame_com_preparo = models.BooleanField(
        "exame com preparo (senha própria no totem)",
        default=False,
        help_text=(
            "Marque para exames que exigem preparo do paciente. Nos dias em que houver um agendamento "
            "com este exame, o totem oferece a senha 'Exame com preparo' na especialidade, e a recepção "
            "chama essas senhas antes das demais, por ordem de chegada."
        ),
    )
    valor_particular = models.DecimalField(
        "valor particular",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Valor cobrado no atendimento particular. Deixe em branco enquanto este procedimento não "
            "puder ser agendado como particular — só é possível agendar (convênio ou particular) o que "
            "tiver um valor cadastrado (aqui, ou na Tabela de Preços do convênio)."
        ),
    )
    ativo = models.BooleanField("ativo", default=True)

    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Procedimento"
        verbose_name_plural = "Procedimentos"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class Convenio(models.Model):
    """Convênio/plano de saúde aceito pela clínica."""

    nome = models.CharField("nome do convênio", max_length=150, unique=True)
    codigo_ans = models.CharField(
        "código de registro (ANS)",
        max_length=20,
        blank=True,
        help_text="Registro do convênio na ANS, se aplicável.",
    )
    prazo_pagamento_dias = models.PositiveIntegerField(
        "prazo de pagamento (dias)",
        null=True,
        blank=True,
        help_text="Prazo médio de pagamento deste convênio, conforme contrato (usado no módulo financeiro).",
    )

    # Pedido do cliente, 2026-09-28: saiu o antigo "desconto (%) sobre a tabela particular"
    # — nem todo procedimento desses convênios tinha o desconto, então todo convênio agora
    # tem os valores cadastrados na Tabela de Preços, um por procedimento (a migração 0026
    # já copiou os valores calculados pra quem usava o desconto). O que continuou foi a
    # opção de o paciente pagar na hora (ver agenda.views.agendamento_mudar_status).
    permite_pagamento_na_hora = models.BooleanField(
        "permite pagamento na hora",
        default=False,
        help_text=(
            "Marque se, para este convênio, a recepção pode escolher na hora entre encaminhar o "
            "paciente para pagar no financeiro ou autorizar faturando pelo convênio."
        ),
    )
    # Pedido da clínica (23/10/2026): alguns convênios pagos na hora "funcionam como particular" —
    # o que o paciente paga no financeiro vai pro repasse semanal do profissional e pro caixa do
    # proprietário, e o atendimento sai do faturamento ao plano (Contas a receber, lotes, repasse
    # de convênio). Outros convênios também recebem na hora mas NÃO repassam assim — por isso é
    # uma opção por convênio. Ver agenda.models.Agendamento.filtro_pago_como_particular.
    funciona_como_particular = models.BooleanField(
        "funciona como particular",
        default=False,
        help_text=(
            "Marque se, quando o paciente deste convênio paga na clínica, o valor deve ser repassado "
            "ao profissional como no particular (repasse semanal) e sair do faturamento ao convênio. "
            "Só vale para atendimento pago no financeiro (\"permite pagamento na hora\")."
        ),
    )
    # --- Repasse ao profissional (configuração individual por convênio) -----------
    # Escolhido 'prazo_repasse_dias' (em vez de 'meses_de_atraso_repasse') porque
    # dias é a unidade que permite calcular a DATA exata em que o repasse é devido
    # a partir do fim do mês de atendimento (data_fim_mes_atendimento + prazo_repasse_dias),
    # sem precisar assumir que todo mês tem o mesmo número de dias — o exemplo do
    # cliente ("atendeu dia 31/07, recebe o repasse referente a julho só em setembro,
    # dois meses depois") já dá pra representar com um valor em dias (ex: 60) e também
    # dá pra exibir de volta em "meses de atraso" quando for útil mostrar na tela
    # (ver Convenio.repasse_atraso_aproximado_meses abaixo).
    prazo_repasse_dias = models.PositiveIntegerField(
        "prazo de repasse ao profissional (dias)",
        null=True,
        blank=True,
        help_text=(
            "Quantos dias após o FIM do mês de atendimento a clínica repassa ao profissional o valor "
            "deste convênio. Ex.: se a clínica atende em 31/07 mas só recebe/repassa o profissional em "
            "setembro (dois meses depois), informe aproximadamente 60 dias. Deixe em branco se não "
            "houver atraso conhecido de repasse para este convênio."
        ),
    )
    # Pedido do cliente, 2026-09-28: alguns convênios só dão retorno grátis em 15 dias, não
    # nos 30 de sempre. Vale o convênio da CONSULTA ORIGINAL (a que abriu o direito ao
    # retorno) — ver agenda.models.AgendamentoItem.buscar_item_para_retorno. Particular
    # continua com PRAZO_RETORNO_PADRAO_DIAS.
    PRAZO_RETORNO_PADRAO_DIAS = 30
    prazo_retorno_dias = models.PositiveIntegerField(
        "prazo de retorno grátis (dias)",
        default=PRAZO_RETORNO_PADRAO_DIAS,
        help_text=(
            "Quantos dias corridos após uma consulta por este convênio o paciente tem direito a retorno "
            "grátis na mesma especialidade (padrão: 30). Ex.: informe 15 para convênios que só cobrem "
            "retorno em 15 dias. Informe 0 se este convênio não dá direito a retorno grátis."
        ),
    )
    ativo = models.BooleanField("ativo", default=True)

    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Convênio"
        verbose_name_plural = "Convênios"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    def save(self, *args, **kwargs):
        ligando = self.funciona_como_particular and (
            self.pk is None
            or not Convenio.objects.filter(pk=self.pk, funciona_como_particular=True).exists()
        )
        super().save(*args, **kwargs)
        if ligando:
            self.marcar_pagos_ja_repassados()

    def marcar_pagos_ja_repassados(self):
        """Ao passar a "funcionar como particular", os atendimentos ANTIGOS deste convênio pagos na
        clínica entram no repasse semanal. Os que já foram pagos ao profissional pelo "Repasse aos
        profissionais (convênio)" não podem reaparecer como pendentes (pagaria em dobro): as
        linhas de pagamento deles ficam marcadas como já repassadas, com a mesma data.
        Devolve quantas marcou."""
        from agenda.models import PagamentoParticular

        linhas = PagamentoParticular.objects.filter(
            agendamento__convenio=self,
            agendamento__forma_atendimento="CONVENIO",
            agendamento__convenio_repasse_pago=True,
            repasse_pago=False,
        ).select_related("agendamento")
        total = 0
        for linha in linhas:
            linha.repasse_pago = True
            linha.repasse_pago_em = linha.agendamento.convenio_repasse_pago_em
            linha.save(update_fields=["repasse_pago", "repasse_pago_em"])
            total += 1
        return total

    @property
    def repasse_atraso_aproximado_meses(self):
        """Só para exibição na tela (ex.: 'aprox. 2 meses depois') — o cálculo de verdade
        de quando o repasse é devido usa prazo_repasse_dias, não este valor arredondado."""
        if not self.prazo_repasse_dias:
            return None
        return round(self.prazo_repasse_dias / 30)

    def valor_para(self, procedimento, profissional=None):
        """
        Quanto este convênio paga por um procedimento — usado pelo módulo Financeiro
        (agenda.views.agendamento_mudar_status) para preencher sozinho o valor a receber
        assim que o atendimento é Autorizado (Agendamento.STATUS_PRODUCAO — "Realizado"
        é só o check do profissional, não é o que conta produção), e também pra filtrar quais procedimentos
        esse convênio libera no agendamento (ver agenda.views.procedimentos_do_profissional
        e AgendamentoForm.clean()).

        Vem da Tabela de Preços (TabelaPrecoConvenio) — um preço específico do
        profissional tem prioridade sobre o preço geral. Devolve None se o procedimento
        não estiver cadastrado para este convênio.
        """
        if not procedimento:
            return None
        if profissional:
            preco = self.tabela_precos.filter(procedimento=procedimento, profissional=profissional).first()
            if preco:
                return preco.valor
        preco_geral = self.tabela_precos.filter(procedimento=procedimento, profissional__isnull=True).first()
        if preco_geral:
            return preco_geral.valor
        return None


class TabelaPrecoConvenio(models.Model):
    """
    Preço de um procedimento específico, para um convênio específico.

    O campo 'profissional' é opcional: deixe em branco para um preço geral
    do procedimento naquele convênio (vale para qualquer profissional que
    realizar), ou selecione um profissional para um preço específico dele —
    útil quando o mesmo procedimento (ex: "Consulta") tem valores diferentes
    conforme quem atende (ex: consulta com fonoaudiólogo x consulta com
    médico), sem precisar criar um Procedimento duplicado para cada caso.
    Quando existir um preço específico do profissional, ele tem prioridade
    sobre o preço geral (ver `TabelaPrecoConvenio.objects` e a lógica de
    busca de preço, quando o módulo Financeiro/Agenda for implementado).
    """

    convenio = models.ForeignKey(Convenio, verbose_name="convênio", on_delete=models.CASCADE, related_name="tabela_precos")
    procedimento = models.ForeignKey(Procedimento, verbose_name="procedimento", on_delete=models.CASCADE, related_name="precos_convenios")
    profissional = models.ForeignKey(
        Profissional,
        verbose_name="profissional (opcional)",
        on_delete=models.CASCADE,
        related_name="precos_especificos",
        null=True,
        blank=True,
        help_text="Deixe em branco para um preço geral do procedimento. Selecione um profissional só se o valor for diferente para ele.",
    )
    valor = models.DecimalField("valor", max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "Preço de Procedimento por Convênio"
        verbose_name_plural = "Tabela de Preços por Convênio"
        constraints = [
            # Observação: bancos de dados tratam NULL como "diferente de si mesmo"
            # em restrições de unicidade, então esta constraint garante um único
            # preço por (convênio, procedimento, profissional) quando um
            # profissional é informado — o caso "preço geral" (profissional em
            # branco) é protegido por validação no formulário/importação, não
            # pelo banco.
            models.UniqueConstraint(fields=["convenio", "procedimento", "profissional"], name="unico_procedimento_profissional_por_convenio"),
        ]
        ordering = ["convenio__nome", "procedimento__nome", "profissional__nome"]

    def __str__(self):
        sufixo = f" ({self.profissional.nome})" if self.profissional_id else ""
        return f"{self.convenio.nome} — {self.procedimento.nome}{sufixo}: R$ {formatar_moeda(self.valor)}"


class Paciente(models.Model):
    """Cadastro de pacientes da clínica."""

    nome_completo = models.CharField("nome completo", max_length=150)
    cpf = models.CharField(
        "CPF",
        max_length=14,
        blank=True,
        null=True,
        unique=True,
        validators=[validador_cpf],
        help_text="Opcional — deixe em branco para pacientes sem CPF próprio (ex: crianças).",
    )
    rg = models.CharField("RG", max_length=20, blank=True)
    data_nascimento = models.DateField("data de nascimento", null=True, blank=True)

    telefone = models.CharField("telefone", max_length=20, blank=True)
    # "email" e "bairro" continuam existindo na tabela (não removidos, pra não perder
    # dado de quem já tinha preenchido), mas saíram da tela de cadastro por pedido do
    # cliente — ver PacienteForm em cadastros/forms.py.
    email = models.EmailField("e-mail", blank=True)
    bairro = models.CharField("bairro", max_length=100, blank=True)
    cidade = models.CharField("cidade", max_length=100, blank=True)

    convenio = models.ForeignKey(
        Convenio,
        verbose_name="convênio",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pacientes",
        help_text="Deixe em branco se o paciente for particular (sem convênio). Na hora do agendamento dá para escolher usar o convênio ou particular mesmo assim.",
    )
    numero_carteirinha = models.CharField("número da carteirinha", max_length=40, blank=True)
    validade_carteirinha = models.DateField(
        "validade da carteirinha",
        null=True,
        blank=True,
        help_text="Se informada, o sistema avisa quando o paciente tiver um atendimento agendado com a carteirinha já vencida.",
    )

    # Dados do responsável — mantidos na tabela por compatibilidade, mas saíram da
    # tela de cadastro (ver PacienteForm).
    responsavel_nome = models.CharField("nome do responsável", max_length=150, blank=True)
    responsavel_cpf = models.CharField(
        "CPF do responsável",
        max_length=14,
        blank=True,
        validators=[validador_cpf],
    )

    ativo = models.BooleanField("ativo", default=True)

    # Rastreio da migração do sistema antigo da clínica (PostgreSQL) — ver
    # cadastros/management/commands/importar_pacientes_legado.py. Guarda o ID
    # do paciente NO SISTEMA ANTIGO, não daqui, para o comando de importação
    # conseguir casar o mesmo paciente entre execuções (os dois sistemas
    # convivem por um tempo, então a importação roda mais de uma vez e não
    # pode duplicar ninguém). CharField (não Integer) porque não sabemos
    # ainda o tipo da chave primária de origem. Fica em branco/NULL para
    # todo paciente cadastrado direto aqui — nunca preenchido pela tela normal.
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

    # Unificação de cadastros duplicados (comando unificar_pacientes_duplicados, pedido da
    # clínica 13/10/2026): a importação do sistema antigo trouxe a mesma pessoa várias
    # vezes (mesmo nome + nascimento). A cópia NÃO é apagada — todo o histórico dela
    # (prontuário, agendamentos, documentos) passa pra ficha principal e ela fica apontando
    # pra principal aqui, escondida da recepção e do prontuário. Mantida (em vez de
    # excluída) pra reimportação do sistema antigo reconhecer o ID de origem dela e não
    # recriar a duplicata.
    unificado_em = models.ForeignKey(
        "self",
        verbose_name="unificado na ficha",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="copias_unificadas",
    )

    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Paciente"
        verbose_name_plural = "Pacientes"
        ordering = ["nome_completo"]

    @staticmethod
    def filtro_visivel_recepcao():
        """Q dos pacientes que aparecem pra recepção (lista de pacientes, busca do
        agendamento) — pedido da clínica (13/10/2026): da importação do sistema antigo só
        ficam os cadastros com dado pra identificar a pessoa além do nome (CPF, nascimento
        ou telefone). Os demais continuam no sistema e o profissional segue vendo o
        prontuário completo deles — só não poluem a recepção. Cadastro feito aqui mesmo
        (sem origem_legado_id) sempre aparece. Cópia unificada (unificado_em) nunca aparece."""
        return Q(ativo=True, unificado_em__isnull=True) & Paciente.filtro_dados_minimos()

    @staticmethod
    def filtro_dados_minimos():
        """Q: cadastro com algo além do nome (CPF, nascimento ou telefone), ou feito aqui
        mesmo (sem origem no sistema antigo) — ver filtro_visivel_recepcao."""
        tem_dado = (
            (Q(cpf__isnull=False) & ~Q(cpf=""))
            | Q(data_nascimento__isnull=False)
            | ~Q(telefone="")
        )
        return Q(origem_legado_id__isnull=True) | tem_dado

    @property
    def oculto_recepcao(self):
        """Mesmo critério de filtro_visivel_recepcao, pra uma ficha já carregada."""
        if self.unificado_em_id:
            return True
        if self.origem_legado_id is None:
            return False
        return not (self.cpf or self.data_nascimento or self.telefone)

    def __str__(self):
        return self.nome_completo

    @property
    def idade(self):
        """Idade em anos completos hoje, ou None sem data de nascimento no cadastro."""
        if not self.data_nascimento:
            return None
        hoje = timezone.localdate()
        nascimento = self.data_nascimento
        return hoje.year - nascimento.year - ((hoje.month, hoje.day) < (nascimento.month, nascimento.day))

    @property
    def idade_exibicao(self):
        """Idade pro prontuário (pedido da clínica, 23/10/2026) — sem nascimento no cadastro
        aparece "(pendente cadastro)" pra recepção completar."""
        idade = self.idade
        if idade is None:
            return "(pendente cadastro)"
        if idade == 0:  # bebê: em meses
            hoje, nascimento = timezone.localdate(), self.data_nascimento
            meses = (hoje.year - nascimento.year) * 12 + hoje.month - nascimento.month - (hoje.day < nascimento.day)
            return f"{meses} {'mês' if meses == 1 else 'meses'}"
        return f"{idade} {'ano' if idade == 1 else 'anos'}"

    @property
    def plano_cadastro_exibicao(self):
        """Convênio do cadastro do paciente, ou "Particular" quando não tem."""
        return self.convenio.nome if self.convenio_id else "Particular (sem convênio no cadastro)"

    @property
    def primeiro_nome(self):
        """Usado nos blocos da grade da agenda, onde o nome completo não cabe."""
        partes = self.nome_completo.split()
        return partes[0] if partes else ""

    @property
    def status_financeiro(self):
        """
        Placeholder até o módulo Financeiro (Etapa 6) existir de verdade.
        Vai passar a calcular pendências reais quando os lançamentos
        financeiros forem implementados.
        """
        return "Não avaliado (módulo financeiro ainda não implementado)"

    @property
    def carteirinha_vencida(self):
        """
        True se a validade da carteirinha já passou. Por enquanto só um
        indicador na tela de Pacientes — quando o Agendamento (Etapa 4) for
        implementado, essa mesma checagem vai gerar o alerta na hora de
        marcar um atendimento futuro para este paciente com o convênio dele.
        """
        if not self.validade_carteirinha:
            return False
        return self.validade_carteirinha < timezone.localdate()

    @property
    def nome_exibicao_publica(self):
        """Primeiro + último nome — usado no painel público de chamada
        (fila.views.painel_espera), que não exige login: nunca mostra o nome
        completo numa tela que qualquer um na sala de espera pode ver."""
        partes = self.nome_completo.split()
        if len(partes) <= 1:
            return self.nome_completo
        return f"{partes[0]} {partes[-1]}"


class DuplicidadeIgnorada(models.Model):
    """Grupo de fichas com o mesmo nome que alguém conferiu na tela Possíveis duplicados e
    marcou como "não são a mesma pessoa" (homônimos) — pedido da clínica, 06/11/2026. O grupo
    some da tela enquanto continuar exatamente com essas fichas; se aparecer uma ficha nova com
    o mesmo nome, o grupo volta pra ser conferido de novo."""

    ids = models.CharField("fichas (IDs)", max_length=1000, unique=True, help_text="IDs das fichas em ordem, separados por vírgula.")
    nome = models.CharField("nome", max_length=150)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="marcado por", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    criado_em = models.DateTimeField("marcado em", auto_now_add=True)

    class Meta:
        verbose_name = "Duplicidade ignorada (homônimos)"
        verbose_name_plural = "Duplicidades ignoradas (homônimos)"
        ordering = ["nome"]

    def __str__(self):
        return f"{self.nome} ({self.ids})"

    @staticmethod
    def chave(ids):
        return ",".join(str(pk) for pk in sorted(ids))


class Sala(models.Model):
    """
    Cadastro simples de salas/consultórios da clínica — preparação para o
    futuro sistema de fila/senha (o profissional escolhe em qual sala está
    atendendo no dia, e depois "chama" o paciente daquela sala). Por
    enquanto são só 5 salas genéricas (ver seed em cadastros/migrations) —
    o cliente vai enviar depois a planta baixa real, e as salas podem ser
    renomeadas/adicionadas aqui livremente, sem precisar mexer em código.
    """

    # Nome com que a segunda recepção aparece pro paciente (painel de espera). Fixo aqui, e
    # não um campo do cadastro, porque a clínica só tem duas recepções e esta é a "2".
    NOME_RECEPCAO_PROPRIA = "Recepção 2"

    nome = models.CharField("nome da sala", max_length=100, unique=True)
    ativa = models.BooleanField("ativa", default=True)
    # Decisão da clínica, 2026-09-19: existe uma segunda recepção, com poucos lugares, que só
    # atende os pacientes de quem usa uma sala específica (não tem tela/painel próprio — quem
    # chama é o painel de espera da recepção 1). 0 = sala sem recepção própria (o normal).
    # Ver agenda.views.agendamento_chamar_recepcao e Agendamento.lote_recepcao.
    lugares_recepcao_propria = models.PositiveSmallIntegerField(
        "lugares na recepção própria",
        default=0,
        help_text=(
            "Só preencha se esta sala tem uma recepção exclusiva (a 'Recepção 2'): quantas pessoas cabem "
            "sentadas nela, contando quem já entrou no consultório. O profissional que escolher esta sala "
            "ganha o botão de chamar vários pacientes de uma vez pra ela. Deixe 0 nas demais salas."
        ),
    )

    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Sala"
        verbose_name_plural = "Salas"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    @property
    def tem_recepcao_propria(self):
        return self.lugares_recepcao_propria > 0


class AtendimentoSala(models.Model):
    """
    Registra em qual Sala um Profissional escolheu atender em um dia
    específico (botão "estou atendendo na sala: [X]" em "Minha agenda").
    Guardado por (profissional, data) — um profissional pode trocar de sala
    no mesmo dia, e a escolha mais recente é a que vale (ver
    Profissional.sala_de_hoje). É a base para o futuro painel de
    fila/senha "chamar o próximo" a partir da sala escolhida — nenhuma fila
    de verdade é implementada ainda, só o registro da escolha da sala.
    """

    profissional = models.ForeignKey(
        Profissional, verbose_name="profissional", on_delete=models.CASCADE, related_name="atendimentos_sala"
    )
    sala = models.ForeignKey(Sala, verbose_name="sala", on_delete=models.PROTECT, related_name="atendimentos")
    data = models.DateField("data")
    criado_em = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Sala do dia (profissional)"
        verbose_name_plural = "Salas do dia (profissionais)"
        ordering = ["-data", "-criado_em"]

    def __str__(self):
        return f"{self.profissional.nome} — {self.sala.nome} ({self.data:%d/%m/%Y})"


class Guiche(models.Model):
    """
    Cadastro simples de guichês de atendimento da recepção — mesmo padrão
    de Sala acima, só que para quem trabalha no balcão da recepção em vez
    de em consultório: preparação para o futuro sistema de fila/senha (o
    usuário da recepção escolhe em qual guichê está no dia, e depois
    "chama" a senha daquele guichê). Os guichês podem ser renomeados/
    adicionados livremente aqui, sem precisar mexer em código.
    """

    nome = models.CharField("nome do guichê", max_length=100, unique=True)
    ativo = models.BooleanField("ativo", default=True)

    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Guichê"
        verbose_name_plural = "Guichês"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class AtendimentoGuiche(models.Model):
    """
    Registra em qual Guichê um usuário da recepção escolheu atender em um
    dia específico (botão "estou no guichê: [X]" na Agenda do dia).
    Guardado por (usuario, data) — mesmo padrão de AtendimentoSala, mas
    ligado ao Usuario (login) em vez de Profissional, já que quem trabalha
    na recepção não tem necessariamente um cadastro de Profissional. É a
    base para o futuro painel de fila/senha "chamar o próximo" a partir do
    guichê escolhido — nenhuma fila de verdade é implementada ainda, só o
    registro da escolha do guichê.
    """

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="usuário",
        on_delete=models.CASCADE,
        related_name="atendimentos_guiche",
    )
    guiche = models.ForeignKey(Guiche, verbose_name="guichê", on_delete=models.PROTECT, related_name="atendimentos")
    data = models.DateField("data")
    criado_em = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Guichê do dia (recepção)"
        verbose_name_plural = "Guichês do dia (recepção)"
        ordering = ["-data", "-criado_em"]

    def __str__(self):
        quem = self.usuario.get_full_name() or self.usuario.username
        return f"{quem} — {self.guiche.nome} ({self.data:%d/%m/%Y})"

    @classmethod
    def guiche_em(cls, usuario, data):
        """Guichê escolhido por este usuário para a data informada, ou None."""
        registro = cls.objects.filter(usuario=usuario, data=data).order_by("-criado_em").first()
        return registro.guiche if registro else None


class EspecialidadePadraoUsuario(models.Model):
    """
    Especialidades que um usuário da recepção cobre HABITUALMENTE — baseline que
    muda raramente, editado no cadastro do usuário (ver
    accounts.forms.UsuarioEditarForm, permissão 'usuario_gerenciar'). Serve só
    pra pré-marcar o formulário de "especialidades de hoje" (ver
    AtendimentoEspecialidade, abaixo) sem precisar selecionar tudo de novo todo
    dia — não filtra a fila sozinho: quem filtra é sempre o registro do dia.

    Mesmo motivo de AtendimentoGuiche pra guardar 'usuario' (Usuario/login) em
    vez de 'profissional': quem trabalha na recepção não tem necessariamente
    um cadastro de Profissional.
    """

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="usuário",
        on_delete=models.CASCADE,
        related_name="especialidades_padrao",
    )
    especialidade = models.CharField("especialidade", max_length=50, choices=ESPECIALIDADES_SAUDE)
    especialidade_outra = models.CharField("qual especialidade (se 'Outra')", max_length=100, blank=True)

    class Meta:
        verbose_name = "Especialidade padrão (recepção)"
        verbose_name_plural = "Especialidades padrão (recepção)"
        ordering = ["usuario__username"]
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "especialidade", "especialidade_outra"],
                name="unica_especialidade_padrao_por_usuario",
            ),
        ]

    def __str__(self):
        quem = self.usuario.get_full_name() or self.usuario.username
        return f"{quem} — {self.especialidade_exibicao}"

    @property
    def especialidade_exibicao(self):
        return especialidade_rotulo(self.especialidade, self.especialidade_outra)

    @classmethod
    def especialidades_de(cls, usuario):
        """Lista de (especialidade, especialidade_outra) que este usuário cobre habitualmente."""
        return list(cls.objects.filter(usuario=usuario).values_list("especialidade", "especialidade_outra"))

    @classmethod
    def definir(cls, usuario, especialidades):
        """Substitui completamente o conjunto padrão deste usuário pelo informado
        (lista de (especialidade, especialidade_outra))."""
        with transaction.atomic():
            cls.objects.filter(usuario=usuario).delete()
            cls.objects.bulk_create(
                cls(usuario=usuario, especialidade=especialidade, especialidade_outra=especialidade_outra)
                for especialidade, especialidade_outra in especialidades
            )


class AtendimentoEspecialidade(models.Model):
    """
    Quais especialidades um usuário da recepção está cobrindo em um dia
    específico (marcado na Agenda do dia — mesma tela/espírito de
    AtendimentoGuiche, mas aqui pode haver VÁRIAS especialidades no mesmo dia,
    por isso é uma linha por (usuario, especialidade, data) em vez de uma FK
    única). Pré-marcado a partir de EspecialidadePadraoUsuario na hora de
    montar o formulário, mas gravado à parte — mudar o padrão não altera dias
    já configurados, e vice-versa.

    Ao contrário de AtendimentoGuiche (grava um valor novo e sempre lê o mais
    recente), aqui cada salvamento REGRAVA o conjunto do dia inteiro (ver
    `definir`) em vez de só acrescentar linhas: o que importa é "quais valem
    AGORA", não um histórico de escolhas dentro do mesmo dia.

    Base de Senha.proxima_para_especialidades — a fila que a recepção vê pra
    chamar o próximo é filtrada por este conjunto.
    """

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="usuário",
        on_delete=models.CASCADE,
        related_name="especialidades_atendimento",
    )
    especialidade = models.CharField("especialidade", max_length=50, choices=ESPECIALIDADES_SAUDE)
    especialidade_outra = models.CharField("qual especialidade (se 'Outra')", max_length=100, blank=True)
    data = models.DateField("data")
    criado_em = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Especialidade do dia (recepção)"
        verbose_name_plural = "Especialidades do dia (recepção)"
        ordering = ["-data", "usuario__username"]
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "data", "especialidade", "especialidade_outra"],
                name="unica_especialidade_por_usuario_e_dia",
            ),
        ]

    def __str__(self):
        quem = self.usuario.get_full_name() or self.usuario.username
        return f"{quem} — {self.especialidade_exibicao} ({self.data:%d/%m/%Y})"

    @property
    def especialidade_exibicao(self):
        return especialidade_rotulo(self.especialidade, self.especialidade_outra)

    @classmethod
    def especialidades_em(cls, usuario, data):
        """Lista de (especialidade, especialidade_outra) que este usuário está cobrindo nesta data."""
        return list(cls.objects.filter(usuario=usuario, data=data).values_list("especialidade", "especialidade_outra"))

    @classmethod
    def definir(cls, usuario, data, especialidades):
        """Substitui completamente o conjunto de especialidades deste usuário
        nesta data pelo informado (lista de (especialidade, especialidade_outra)) —
        não acumula com o que já existia antes nesse mesmo dia."""
        with transaction.atomic():
            cls.objects.filter(usuario=usuario, data=data).delete()
            cls.objects.bulk_create(
                cls(usuario=usuario, data=data, especialidade=especialidade, especialidade_outra=especialidade_outra)
                for especialidade, especialidade_outra in especialidades
            )


class AtendimentoProfissional(models.Model):
    """
    Pedido da clínica (2026-10-16): numa especialidade com mais de um profissional
    atendendo no dia, a recepção se divide — cada atendente cuida dos pacientes de um
    profissional específico. Este registro diz QUAIS profissionais o usuário está
    cobrindo hoje, além das especialidades (AtendimentoEspecialidade, acima): marcado
    na Agenda do dia junto com as especialidades e regravado inteiro a cada salvamento
    (mesmo `definir`).

    Só restringe a especialidade em que a atendente marcou algum profissional — numa
    especialidade sem nenhum profissional marcado ela continua pegando todas as senhas
    (ver cobertura_em e fila.models.Senha._filtro_especialidades). Senha tirada com
    "Não sei / qualquer um" no totem (profissional em branco) vale pra todas as
    atendentes da especialidade.
    """

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="usuário",
        on_delete=models.CASCADE,
        related_name="profissionais_atendimento",
    )
    profissional = models.ForeignKey(
        Profissional, verbose_name="profissional", on_delete=models.CASCADE, related_name="atendentes_do_dia"
    )
    data = models.DateField("data")
    criado_em = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Profissional do dia (recepção)"
        verbose_name_plural = "Profissionais do dia (recepção)"
        ordering = ["-data", "usuario__username"]
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "data", "profissional"], name="unico_profissional_por_usuario_e_dia"
            ),
        ]

    def __str__(self):
        quem = self.usuario.get_full_name() or self.usuario.username
        return f"{quem} — {self.profissional.nome} ({self.data:%d/%m/%Y})"

    @classmethod
    def profissionais_em(cls, usuario, data):
        """Ids dos profissionais que este usuário marcou como seus nesta data."""
        return list(cls.objects.filter(usuario=usuario, data=data).values_list("profissional_id", flat=True))

    @classmethod
    def definir(cls, usuario, data, profissionais_ids):
        """Substitui o conjunto do dia pelo informado — mesmo jeito de AtendimentoEspecialidade.definir."""
        with transaction.atomic():
            cls.objects.filter(usuario=usuario, data=data).delete()
            cls.objects.bulk_create(
                cls(usuario=usuario, data=data, profissional_id=profissional_id)
                for profissional_id in set(profissionais_ids)
            )

    @classmethod
    def cobertura_em(cls, usuario, data):
        """
        O que este usuário cobre na fila de senhas nesta data: lista de
        (especialidade, especialidade_outra, ids_de_profissionais_ou_None). None = todos os
        profissionais da especialidade (nenhum marcado nela); um conjunto = só as senhas
        tiradas para esses profissionais, mais as tiradas sem profissional ("Não sei").
        """
        especialidades = AtendimentoEspecialidade.especialidades_em(usuario, data)
        escolhidos = Profissional.objects.filter(
            pk__in=cls.profissionais_em(usuario, data)
        ).prefetch_related("especialidades_adicionais")
        cobertura = []
        for especialidade, especialidade_outra in especialidades:
            ids = {p.pk for p in escolhidos if (especialidade, especialidade_outra) in p.especialidades_pares}
            cobertura.append((especialidade, especialidade_outra, ids or None))
        return cobertura


class LimiteDiarioProcedimento(models.Model):
    """
    Limite diário opcional de quantos atendimentos um Profissional pode ter
    no mesmo dia — usado como AVISO (não bloqueio) na Central de Atendimento,
    sugerindo à recepção considerar outro profissional/dia quando o limite
    for atingido (ver agenda.views.central_atendimento / _verificar_limite_diario).

    'procedimento' e 'convenio' são opcionais e combináveis (pedido do
    cliente: o que importa às vezes não é o procedimento em si, e sim o
    CONVÊNIO — alguns pagam pouco e a clínica quer limitar quantos
    atendimentos daquele plano um profissional faz por dia, independente de
    qual procedimento seja):

    - só 'procedimento' preenchido: limite geral daquele procedimento,
      igual ao comportamento original (qualquer convênio ou particular).
    - só 'convenio' preenchido: limite de QUALQUER atendimento daquele
      convênio no dia, independente do procedimento.
    - os dois preenchidos: limite bem específico (só aquele procedimento,
      só para aquele convênio).
    - os dois em branco: limite geral de atendimentos do profissional no
      dia, independente de procedimento/convênio.

    Quando mais de um limite combina com um agendamento, o mais específico
    (mais campos preenchidos) prevalece — ver _verificar_limite_diario.
    """

    profissional = models.ForeignKey(
        Profissional, verbose_name="profissional", on_delete=models.CASCADE, related_name="limites_diarios"
    )
    procedimento = models.ForeignKey(
        Procedimento,
        verbose_name="procedimento (opcional)",
        on_delete=models.CASCADE,
        related_name="limites_diarios",
        null=True,
        blank=True,
        help_text="Deixe em branco para o limite valer para qualquer procedimento.",
    )
    # Pedido da clínica (23/10/2026): um limite só pra todos os ultrassons, sem criar uma regra
    # por exame — vale pra todo procedimento com Procedimento.eh_ultrassom marcado.
    todos_ultrassons = models.BooleanField(
        "todos os ultrassons (USG)",
        default=False,
        help_text=(
            "Marque para o limite valer para todos os procedimentos marcados como ultrassom (USG) "
            "somados, em vez de um procedimento só. Deixe o campo procedimento em branco."
        ),
    )
    convenio = models.ForeignKey(
        Convenio,
        verbose_name="convênio (opcional)",
        on_delete=models.CASCADE,
        related_name="limites_diarios",
        null=True,
        blank=True,
        help_text=(
            "Deixe em branco para o limite valer independente do convênio (inclusive particular). "
            "Selecione um convênio para limitar só os atendimentos daquele plano — útil para planos "
            "que pagam pouco, quando o que importa não é o procedimento, e sim o convênio."
        ),
    )
    limite_diario = models.PositiveIntegerField("limite diário")

    class Meta:
        verbose_name = "Limite diário de atendimento"
        verbose_name_plural = "Limites diários de atendimento"
        ordering = ["profissional__nome", "procedimento__nome", "convenio__nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["profissional", "procedimento", "todos_ultrassons", "convenio"],
                name="unico_limite_por_profissional_procedimento_usg_convenio",
            ),
        ]

    @property
    def alvo_exibicao(self):
        if self.procedimento_id:
            return self.procedimento.nome
        if self.todos_ultrassons:
            return "Todos os ultrassons (USG)"
        return "qualquer procedimento"

    def __str__(self):
        alvo = self.alvo_exibicao
        if self.convenio_id:
            alvo += f" — convênio {self.convenio.nome}"
        return f"{self.profissional.nome} — {alvo}: até {self.limite_diario}/dia"


class Pacote(models.Model):
    """
    Pacote/combo PARTICULAR com preço fechado (pedido do cliente, 2026-10-13) — ex.:
    "Consulta + Eletrocardiograma" por um valor só, ou "10 sessões" pagas de uma vez. Este é
    o CATÁLOGO; a venda pra um paciente vira agenda.models.PacoteVendido, que congela itens,
    valor e repasse no momento da venda (mudar o cadastro aqui nunca mexe em pacote já vendido).

    Regras confirmadas com o cliente: só particular; o paciente paga tudo na venda e o
    repasse do profissional sai inteiro nesse pagamento; o pacote vendido fica preso ao
    profissional que vendeu; sem validade; sem devolução; falta não consome sessão (o
    paciente remarca).
    """

    nome = models.CharField("nome", max_length=150)
    profissional = models.ForeignKey(
        Profissional,
        verbose_name="profissional (opcional)",
        on_delete=models.CASCADE,
        related_name="pacotes",
        null=True,
        blank=True,
        help_text="Deixe em branco para o pacote poder ser vendido por qualquer profissional.",
    )
    valor = models.DecimalField(
        "valor do pacote (R$)", max_digits=8, decimal_places=2, validators=[MinValueValidator(0)],
        help_text="Preço fechado, pago pelo paciente de uma vez no dia da venda.",
    )
    valor_repasse = models.DecimalField(
        "repasse fixo ao profissional (R$)", max_digits=8, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text=(
            "Só para profissional que cobra VALOR FIXO no particular (obrigatório nesse caso). "
            "Para os demais, deixe em branco: o repasse segue o percentual do profissional sobre o valor do pacote."
        ),
    )
    ativo = models.BooleanField("ativo", default=True, help_text="Desmarque para parar de oferecer este pacote.")

    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Pacote"
        verbose_name_plural = "Pacotes"
        ordering = ["nome"]

    def __str__(self):
        return f"{self.nome} — R$ {formatar_moeda(self.valor)}"

    def clean(self):
        if self.valor is not None and self.valor_repasse is not None and self.valor_repasse > self.valor:
            raise ValidationError({"valor_repasse": "O repasse não pode ser maior que o valor do pacote."})

    @property
    def itens_resumo(self):
        return ", ".join(str(item) for item in self.itens.all()) or "—"


class PacoteItem(models.Model):
    """Um procedimento (e quantas vezes) dentro de um Pacote."""

    pacote = models.ForeignKey(Pacote, verbose_name="pacote", on_delete=models.CASCADE, related_name="itens")
    procedimento = models.ForeignKey(
        Procedimento, verbose_name="procedimento", on_delete=models.PROTECT, related_name="itens_pacote"
    )
    quantidade = models.PositiveIntegerField("quantidade", default=1, validators=[MinValueValidator(1)])

    class Meta:
        verbose_name = "Item do pacote"
        verbose_name_plural = "Itens do pacote"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["pacote", "procedimento"], name="unico_procedimento_por_pacote"),
        ]

    def __str__(self):
        return f"{self.quantidade}x {self.procedimento.nome}"
