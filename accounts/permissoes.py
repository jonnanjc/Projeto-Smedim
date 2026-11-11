"""
Lista única de permissões customizadas do sistema, organizada por
departamento — usada em três lugares, pra nunca ficar desalinhada:

1. accounts.models.PermissoesSistema.Meta.permissions (onde as permissões
   são de fato declaradas/registradas no Django);
2. accounts.management.commands.seed_perfis (perfis padrão criados na
   primeira instalação);
3. As telas de "Perfis" e "Usuários" (permissões extras por usuário), pra
   mostrar os checkboxes agrupados por departamento.

Se um novo departamento/permissão surgir no futuro, basta adicionar aqui
(e no Meta.permissions do models.py) — as telas de Perfis/Usuários e o
seed_perfis já pegam automaticamente.
"""

DEPARTAMENTOS_PERMISSOES = [
    ("Administração", [
        ("usuario_gerenciar", "Gerenciar usuários (criar, editar, bloquear)"),
        ("perfil_gerenciar", "Gerenciar perfis e permissões"),
        ("sistema_configurar", "Configurar parâmetros gerais do sistema"),
    ]),
    ("Pacientes", [
        ("paciente_cadastrar", "Cadastrar pacientes"),
        ("paciente_editar", "Editar cadastro de pacientes"),
        ("paciente_excluir", "Excluir cadastro de pacientes"),
    ]),
    ("Profissionais e Procedimentos", [
        ("profissional_gerenciar", "Cadastrar/editar/excluir profissionais"),
        ("procedimento_gerenciar", "Cadastrar/editar/excluir procedimentos"),
    ]),
    ("Convênios", [
        ("convenio_gerenciar", "Gerenciar convênios e tabelas de preço"),
    ]),
    ("Agenda", [
        ("agenda_criar", "Criar agendamentos"),
        ("agenda_editar", "Editar/cancelar agendamentos (só antes da autorização)"),
        ("agenda_cancelar_autorizado", "Cancelar atendimento já autorizado (tela Autorizações)"),
        ("pacote_paciente_cadastrar", "Cadastrar pacote no paciente sem agendamento (pacote do sistema antigo)"),
        ("agenda_ver_propria", "Ver apenas a própria agenda (profissional)"),
        ("agenda_ver_dia", "Ver a agenda do dia (apoio clínico)"),
        ("atendimento_realizar", "Registrar a realização de um atendimento"),
        ("central_atendimento_usar", "Usar a Central de Atendimento (agendar por telefone)"),
        ("limite_diario_gerenciar", "Gerenciar limites diários de procedimento por profissional (Central de Atendimento)"),
    ]),
    ("Salas", [
        ("sala_gerenciar", "Gerenciar cadastro de salas/consultórios"),
    ]),
    ("Guichês", [
        ("guiche_gerenciar", "Gerenciar cadastro de guichês de atendimento (recepção)"),
    ]),
    # Prontuário NÃO é uma permissão configurável por perfil (pedido explícito do
    # cliente): o acesso é exclusivo de quem está vinculado a um cadastro de
    # Profissional (Usuario.profissional_id) — nem o Administrador tem acesso por
    # perfil. Ver prontuario.views (checagem direta de profissional_id, sem
    # permission_required) e os templates que linkam pra lá (checam
    # `user.profissional_id`, não uma permissão).
    ("Fila / Senha", [
        ("fila_autorizar_recepcao", "Autorizar paciente na fila (etapa recepção)"),
        ("fila_autorizar_financeiro", "Autorizar paciente na fila (etapa financeiro)"),
        ("fila_chamar_senha", "Chamar a próxima senha no painel"),
        ("fila_ver_aguardando", "Ver quem está aguardando na fila de senhas (com a hora de chegada)"),
        ("fila_chamar_manual", "Chamar uma senha específica da fila, fora da ordem automática"),
        ("fila_especialidade_configurar", "Definir quais especialidades está cobrindo no dia (recepção)"),
    ]),
    ("Financeiro", [
        # Cada permissão abaixo libera exatamente 1 página do menu FINANCEIRO (ver
        # accounts/templates/accounts/base.html) — antes, "financeiro_ver_completo" liberava
        # estas 9 páginas de uma vez só, sem dar pra escolher uma sem as outras (pedido do
        # cliente, 2026-07-28).
        ("financeiro_ver_painel", "Ver o painel financeiro (resumo da semana)"),
        ("financeiro_ver_movimentacoes_avulsas", "Ver movimentações avulsas de caixa"),
        ("financeiro_ver_contas_receber", "Ver contas a receber de convênio"),
        ("financeiro_ver_repasses_semanais", "Ver repasses semanais (particular)"),
        ("financeiro_ver_fechamento_diario", "Ver fechamento diário"),
        ("financeiro_ver_fechamento_mensal", "Ver fechamento mensal"),
        ("financeiro_ver_relatorio_detalhado", "Ver relatório detalhado"),
        ("financeiro_ver_repasse_convenio", "Ver repasse aos profissionais (convênio)"),
        ("financeiro_ver_producao_convenio", "Ver produção por convênio"),
        ("financeiro_ver_proprio", "Ver apenas o próprio financeiro/produção (profissional)"),
        ("financeiro_lancar", "Lançar valores no financeiro"),
        ("financeiro_conciliar", "Conciliar pagamentos"),
        ("financeiro_estornar", "Estornar baixa de convênio (não mexe em dinheiro)"),
        ("financeiro_estornar_pagamento", "Estornar pagamento particular (devolve dinheiro ao paciente)"),
    ]),
    ("Faturamento em lote (convênio)", [
        ("faturamento_lote_lancar", "Criar lote de faturamento e incluir atendimentos nele"),
        ("faturamento_lote_visualizar", "Ver lotes de faturamento e seus detalhes"),
        ("faturamento_comparativo_visualizar", "Ver comparativo de produção x envio x retorno"),
        ("faturamento_retorno_lancar", "Lançar retorno/glosa por atendimento do lote"),
    ]),
    ("Mensagens", [
        ("mensagem_usar", "Usar o chat interno (mensagens entre usuários)"),
    ]),
    ("Auditoria", [
        ("log_auditoria_ver", "Ver o log de auditoria do sistema (quem fez o quê e quando)"),
    ]),
]


def todas_as_permissoes_codenames():
    """Lista simples de todos os codenames — usado pelo seed_perfis e por validações."""
    return [codigo for _, itens in DEPARTAMENTOS_PERMISSOES for codigo, _ in itens]


# Páginas que o usuário pode fixar em "Favoritos", no topo do menu lateral
# (ver accounts/views.alternar_favorito_view e accounts/context_processors.favoritos_resolvidos).
# Cada usuário guarda só a lista de identificadores (Usuario.paginas_favoritas) — o
# rótulo, a permissão e a URL vêm sempre daqui, nunca do que foi salvo no banco.
PAGINAS_FAVORITAVEIS = {
    "agenda_dia": {"label": "Agenda do dia", "url_name": "agenda:agenda_dia", "permissao": "accounts.agenda_ver_dia"},
    "central_atendimento": {"label": "Central de Atendimento", "url_name": "agenda:central_atendimento", "permissao": "accounts.central_atendimento_usar"},
    "agenda_propria": {"label": "Minha agenda", "url_name": "agenda:agenda_propria", "permissao": "accounts.agenda_ver_propria"},
    "pacientes": {"label": "Pacientes", "url_name": "cadastros:paciente_lista", "permissao": "accounts.paciente_cadastrar"},
    "financeiro_painel": {"label": "Financeiro", "url_name": "financeiro:painel", "permissao": "accounts.financeiro_ver_painel"},
    "mensagens": {"label": "Mensagens", "url_name": "mensagens:lista_conversas", "permissao": "accounts.mensagem_usar"},
}
