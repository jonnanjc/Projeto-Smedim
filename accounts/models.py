"""
Modelos do módulo de Login e Perfis de Acesso.

Mapeamento entre o documento da Etapa 2 e as tabelas do Django:

    Documento (Etapa 2)         ->  Implementação
    -----------------------------------------------------------------
    Usuário                     ->  accounts.Usuario (abaixo)
    Perfil                      ->  django.contrib.auth.models.Group
    Usuário-Perfil (associação) ->  Usuario.groups (M2M nativo do Django)
    Permissão                   ->  django.contrib.auth.models.Permission
    Perfil-Permissão            ->  Group.permissions (M2M nativo do Django)
    Log de Auditoria            ->  accounts.LogAuditoria (abaixo)

Optamos por usar Group/Permission nativos do Django em vez de recriar essas
tabelas do zero: eles já fazem exatamente o que o documento descreve (um
usuário pode ter vários perfis; um perfil concede várias permissões; dá para
conceder/revogar uma permissão específica direto no usuário, além do perfil).
Isso também nos dá de graça o Django Admin já pronto para gerenciar tudo.
"""
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.functional import cached_property


class Usuario(AbstractUser):
    """Qualquer pessoa que acessa o sistema (médico, recepção, financeiro, administrador etc.)."""

    class Status(models.TextChoices):
        ATIVO = "ATIVO", "Ativo"
        INATIVO = "INATIVO", "Inativo"
        BLOQUEADO = "BLOQUEADO", "Bloqueado"

    status = models.CharField(
        "status",
        max_length=10,
        choices=Status.choices,
        default=Status.ATIVO,
        help_text="Controle manual do administrador (Inativo) ou automático do sistema (Bloqueado por tentativas).",
    )

    tentativas_login_invalidas = models.PositiveSmallIntegerField(
        "tentativas de login inválidas",
        default=0,
        help_text="Zerado automaticamente a cada login bem-sucedido.",
    )

    exigir_troca_senha = models.BooleanField(
        "exigir troca de senha no próximo acesso",
        default=True,
        help_text="Marque para forçar a troca de senha no próximo login (ex.: ao definir uma senha provisória).",
    )

    profissional_id = models.PositiveIntegerField(
        "profissional vinculado",
        null=True,
        blank=True,
        help_text=(
            "Referência ao cadastro de Profissional (médico/demais), quando este usuário for "
            "um profissional de saúde. Vira uma ForeignKey de verdade quando o cadastro de "
            "Profissionais for implementado na Etapa 3 — por enquanto guarda só o ID."
        ),
    )

    class Tema(models.TextChoices):
        CLARO = "claro", "Claro"
        ESCURO = "escuro", "Escuro"

    tema = models.CharField(
        "modo de exibição",
        max_length=10,
        choices=Tema.choices,
        default=Tema.CLARO,
        help_text="Preferência de modo claro/escuro deste usuário, lembrada entre acessos.",
    )

    ultimo_acesso = models.DateTimeField(
        "última atividade no sistema",
        null=True,
        blank=True,
        help_text="Atualizado automaticamente a cada navegação, usado só para mostrar quem está 'online' no chat interno.",
    )

    paginas_favoritas = models.JSONField(
        "páginas favoritas",
        default=list,
        blank=True,
        help_text="Identificadores das páginas que este usuário fixou no topo do menu lateral (ver accounts.permissoes.PAGINAS_FAVORITAVEIS).",
    )

    foto = models.ImageField(
        "foto de perfil",
        upload_to="usuarios/fotos/",
        null=True,
        blank=True,
        help_text="Mostrada no lugar das iniciais no cabeçalho do sistema.",
    )

    class Meta:
        verbose_name = "Usuário"
        verbose_name_plural = "Usuários"

    def __str__(self):
        return self.get_full_name() or self.username

    def registrar_login_valido(self):
        self.tentativas_login_invalidas = 0
        self.save(update_fields=["tentativas_login_invalidas"])

    def registrar_tentativa_invalida(self, max_tentativas):
        """Incrementa o contador e bloqueia a conta ao atingir o limite. Retorna True se bloqueou agora."""
        self.tentativas_login_invalidas += 1
        bloqueou_agora = False
        if self.tentativas_login_invalidas >= max_tentativas:
            self.status = Usuario.Status.BLOQUEADO
            self.is_active = False
            bloqueou_agora = True
        self.save(update_fields=["tentativas_login_invalidas", "status", "is_active"])
        return bloqueou_agora

    @cached_property
    def eh_proprietario(self):
        """True se este usuário é o login do profissional proprietário da clínica
        (cadastros.models.Profissional.eh_proprietario) — ex.: pode registrar pagamento de
        repasse particular mesmo sem a permissão do financeiro (pedido da clínica, 2026-10-16)."""
        if not self.profissional_id:
            return False
        from cadastros.models import Profissional

        return Profissional.objects.filter(pk=self.profissional_id, eh_proprietario=True).exists()


class PermissoesSistema(Usuario):
    """
    Modelo "proxy" (não cria tabela nova — reaproveita a tabela de Usuario).

    Serve apenas como local para registrar, no Django, as permissões
    granulares por tela/ação descritas na seção 3 do documento da Etapa 2
    (ex.: 'financeiro.ver_completo', 'paciente.editar'). Essas permissões
    depois são associadas aos Perfis (Groups) pelo comando de gestão
    `seed_perfis`.
    """

    class Meta:
        proxy = True
        default_permissions = ()  # não gera add/change/delete/view automáticos para este proxy
        verbose_name = "Permissão do sistema (técnica)"
        permissions = [
            # --- Administração --------------------------------------------------
            ("usuario_gerenciar", "Pode gerenciar usuários (criar, editar, bloquear)"),
            ("perfil_gerenciar", "Pode gerenciar perfis e permissões"),
            ("sistema_configurar", "Pode configurar parâmetros gerais do sistema"),
            # --- Pacientes --------------------------------------------------------
            ("paciente_cadastrar", "Pode cadastrar pacientes"),
            ("paciente_editar", "Pode editar cadastro de pacientes"),
            ("paciente_excluir", "Pode excluir cadastro de pacientes"),
            # --- Profissionais e Procedimentos (catálogo) ------------------------
            ("profissional_gerenciar", "Pode cadastrar/editar/excluir profissionais"),
            ("procedimento_gerenciar", "Pode cadastrar/editar/excluir procedimentos"),
            # --- Agenda -------------------------------------------------------------
            ("agenda_criar", "Pode criar agendamentos"),
            ("agenda_editar", "Pode editar/cancelar agendamentos (só antes da autorização)"),
            ("agenda_cancelar_autorizado", "Pode cancelar atendimento já autorizado (tela Autorizações)"),
            ("pacote_paciente_cadastrar", "Pode cadastrar pacote no paciente sem agendamento (pacote do sistema antigo)"),
            ("agenda_ver_propria", "Pode ver apenas a própria agenda (profissional)"),
            ("agenda_ver_dia", "Pode ver a agenda do dia (apoio clínico)"),
            ("atendimento_realizar", "Pode registrar a realização de um atendimento"),
            ("central_atendimento_usar", "Pode usar a Central de Atendimento (agendar por telefone)"),
            ("limite_diario_gerenciar", "Pode gerenciar limites diários de procedimento por profissional"),
            # --- Salas ------------------------------------------------------------
            ("sala_gerenciar", "Pode gerenciar cadastro de salas/consultórios"),
            # --- Guichês ------------------------------------------------------------
            ("guiche_gerenciar", "Pode gerenciar cadastro de guichês de atendimento (recepção)"),
            # Prontuário não vira permissão configurável — ver accounts/permissoes.py.
            # --- Fila / senha -----------------------------------------------------
            ("fila_autorizar_recepcao", "Pode autorizar paciente na fila (etapa recepção)"),
            ("fila_autorizar_financeiro", "Pode autorizar paciente na fila (etapa financeiro)"),
            ("fila_chamar_senha", "Pode chamar a próxima senha no painel"),
            ("fila_ver_aguardando", "Pode ver quem está aguardando na fila de senhas (com a hora de chegada)"),
            ("fila_chamar_manual", "Pode chamar uma senha específica da fila, fora da ordem automática"),
            ("fila_especialidade_configurar", "Pode definir quais especialidades está cobrindo no dia (recepção)"),
            # --- Financeiro ------------------------------------------------------
            # Uma permissão por página do menu FINANCEIRO — ver o mesmo comentário em
            # accounts/permissoes.py (fonte única, esta lista só espelha ela pro Django).
            ("financeiro_ver_painel", "Pode ver o painel financeiro (resumo da semana)"),
            ("financeiro_ver_movimentacoes_avulsas", "Pode ver movimentações avulsas de caixa"),
            ("financeiro_ver_contas_receber", "Pode ver contas a receber de convênio"),
            ("financeiro_ver_repasses_semanais", "Pode ver repasses semanais (particular)"),
            ("financeiro_ver_fechamento_diario", "Pode ver fechamento diário"),
            ("financeiro_ver_fechamento_mensal", "Pode ver fechamento mensal"),
            ("financeiro_ver_relatorio_detalhado", "Pode ver relatório detalhado"),
            ("financeiro_ver_repasse_convenio", "Pode ver repasse aos profissionais (convênio)"),
            ("financeiro_ver_producao_convenio", "Pode ver produção por convênio"),
            ("financeiro_ver_proprio", "Pode ver apenas o próprio financeiro/produção (profissional)"),
            ("financeiro_lancar", "Pode lançar valores no financeiro"),
            ("financeiro_conciliar", "Pode conciliar pagamentos"),
            ("financeiro_estornar", "Pode estornar baixa de convênio (não mexe em dinheiro)"),
            ("financeiro_estornar_pagamento", "Pode estornar pagamento particular (devolve dinheiro ao paciente)"),
            ("convenio_gerenciar", "Pode gerenciar convênios e tabelas de valores"),
            # --- Faturamento em lote (convênio) ----------------------------------
            ("faturamento_lote_lancar", "Pode criar lote de faturamento e incluir atendimentos nele"),
            ("faturamento_lote_visualizar", "Pode ver lotes de faturamento e seus detalhes"),
            ("faturamento_comparativo_visualizar", "Pode ver comparativo de produção x envio x retorno"),
            ("faturamento_retorno_lancar", "Pode lançar retorno/glosa por atendimento do lote"),
            # --- Mensagens ----------------------------------------------------------
            ("mensagem_usar", "Pode usar o chat interno (mensagens entre usuários)"),
            # --- Auditoria ----------------------------------------------------------
            ("log_auditoria_ver", "Pode ver o log de auditoria do sistema (quem fez o quê e quando)"),
        ]


# Campanhas de conscientização mais conhecidas no Brasil, com uma cor de
# destaque e uma descrição curta. A campanha exibida é escolhida
# automaticamente pelo mês atual do servidor (ver CAMPANHA_DO_MES logo
# abaixo) — o Administrador não escolhe mais manualmente qual campanha
# aparece, apenas pode ligar/desligar a faixa por completo em
# "Configuração de Tema".
CAMPANHAS_CONSCIENTIZACAO = {
    "JANEIRO_BRANCO": {"nome": "Janeiro Branco", "cor": "#F5F5F5", "cor_texto": "#333333", "descricao": "Saúde mental e qualidade de vida."},
    "FEVEREIRO_ROXO": {"nome": "Fevereiro Roxo", "cor": "#8E44AD", "cor_texto": "#FFFFFF", "descricao": "Lúpus, Fibromialgia e Alzheimer."},
    "MARCO_AZUL_MARINHO": {"nome": "Março Azul-marinho", "cor": "#1B3A6B", "cor_texto": "#FFFFFF", "descricao": "Prevenção do câncer colorretal."},
    "ABRIL_AZUL": {"nome": "Abril Azul", "cor": "#2C7FB8", "cor_texto": "#FFFFFF", "descricao": "Conscientização sobre o autismo."},
    "MAIO_AMARELO": {"nome": "Maio Amarelo", "cor": "#FDC500", "cor_texto": "#333333", "descricao": "Segurança no trânsito."},
    "JUNHO_VERMELHO": {"nome": "Junho Vermelho", "cor": "#C0392B", "cor_texto": "#FFFFFF", "descricao": "Doação de sangue."},
    "JULHO_AMARELO": {"nome": "Julho Amarelo", "cor": "#FDC500", "cor_texto": "#333333", "descricao": "Prevenção e combate às hepatites virais."},
    "AGOSTO_DOURADO": {"nome": "Agosto Dourado", "cor": "#D4AF37", "cor_texto": "#333333", "descricao": "Incentivo ao aleitamento materno."},
    "SETEMBRO_AMARELO": {"nome": "Setembro Amarelo", "cor": "#FDC500", "cor_texto": "#333333", "descricao": "Prevenção ao suicídio."},
    "OUTUBRO_ROSA": {"nome": "Outubro Rosa", "cor": "#F06292", "cor_texto": "#FFFFFF", "descricao": "Prevenção do câncer de mama."},
    "NOVEMBRO_AZUL": {"nome": "Novembro Azul", "cor": "#2196F3", "cor_texto": "#FFFFFF", "descricao": "Prevenção do câncer de próstata."},
    "DEZEMBRO_VERMELHO": {"nome": "Dezembro Vermelho", "cor": "#C0392B", "cor_texto": "#FFFFFF", "descricao": "Prevenção do HIV/Aids."},
}

# Qual campanha (chave de CAMPANHAS_CONSCIENTIZACAO) fica em destaque em cada
# mês do calendário, para a seleção automática por data do servidor.
CAMPANHA_DO_MES = {
    1: "JANEIRO_BRANCO",
    2: "FEVEREIRO_ROXO",
    3: "MARCO_AZUL_MARINHO",
    4: "ABRIL_AZUL",
    5: "MAIO_AMARELO",
    6: "JUNHO_VERMELHO",
    7: "JULHO_AMARELO",
    8: "AGOSTO_DOURADO",
    9: "SETEMBRO_AMARELO",
    10: "OUTUBRO_ROSA",
    11: "NOVEMBRO_AZUL",
    12: "DEZEMBRO_VERMELHO",
}


class ConfiguracaoTema(models.Model):
    """
    Configuração única (singleton) controlada pelo Administrador em
    /admin/, apenas para ligar/desligar a faixa de destaque de campanha de
    conscientização do mês — sem trocar a identidade visual da clínica.
    A campanha exibida (qual mês -> qual cor/descrição) é automática,
    conforme a data atual do servidor (CAMPANHA_DO_MES acima); o
    Administrador não escolhe mais manualmente qual campanha aparece.
    """

    ativo = models.BooleanField(
        "exibir faixa de campanha do mês",
        default=True,
        help_text="Desmarque para desativar completamente a faixa de campanha, independente do mês atual.",
    )

    class Meta:
        verbose_name = "Configuração de Tema do Mês"
        verbose_name_plural = "Configuração de Tema do Mês"

    def __str__(self):
        return "Configuração de Tema do Mês"

    def save(self, *args, **kwargs):
        self.pk = 1  # força sempre existir só uma linha (padrão "singleton")
        super().save(*args, **kwargs)

    @classmethod
    def carregar(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def dados_campanha(self):
        if not self.ativo:
            return None
        from django.utils import timezone

        chave = CAMPANHA_DO_MES.get(timezone.localdate().month)
        return CAMPANHAS_CONSCIENTIZACAO.get(chave) if chave else None


class LogAuditoria(models.Model):
    """Registro de ações sensíveis realizadas no sistema."""

    usuario = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="logs_auditoria",
        verbose_name="usuário",
    )
    acao = models.CharField("ação", max_length=255)
    data_hora = models.DateTimeField("data/hora", auto_now_add=True)
    ip = models.GenericIPAddressField("IP / estação", null=True, blank=True)

    class Meta:
        verbose_name = "Log de Auditoria"
        verbose_name_plural = "Logs de Auditoria"
        ordering = ["-data_hora"]

    def __str__(self):
        quem = self.usuario.username if self.usuario else "(usuário removido)"
        return f"{self.data_hora:%d/%m/%Y %H:%M} — {quem} — {self.acao}"

    @classmethod
    def registrar(cls, request, usuario, acao):
        """Atalho para registrar uma ação sensível a partir de qualquer app (agenda,
        financeiro etc.) sem precisar importar accounts.views. Mesma extração de IP
        usada em accounts.views._registrar_log (login, usuários, perfis)."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        ip = x_forwarded_for.split(",")[0].strip() if x_forwarded_for else request.META.get("REMOTE_ADDR")
        return cls.objects.create(usuario=usuario, acao=acao, ip=ip)
