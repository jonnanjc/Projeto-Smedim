"""
Cria os Perfis (Groups) iniciais e associa as Permissões padrão de cada um,
conforme a tabela da seção 3 do documento "Etapa 2 — Login e Perfis de Acesso".

Uso:
    python manage.py seed_perfis
"""
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from accounts.permissoes import todas_as_permissoes_codenames

# Lista única de permissões agora vive em accounts/permissoes.py (organizada
# por departamento, usada também nas telas de Perfis e Usuários) — evita
# manter a mesma lista duplicada em mais de um lugar.
TODAS_AS_PERMISSOES_CUSTOMIZADAS = todas_as_permissoes_codenames()

PERFIS = {
    "Administrador": TODAS_AS_PERMISSOES_CUSTOMIZADAS,
    "Médico / Profissional de saúde": [
        "agenda_ver_propria",
        "atendimento_realizar",
        "financeiro_ver_proprio",
        "mensagem_usar",
        # Reaproveita a mesma permissão do painel de senha da recepção — dá ao
        # profissional o botão "Chamar paciente" em Minha agenda (ver
        # agenda.views.agendamento_chamar), decisão explícita do cliente de que
        # todo médico já sai habilitado a chamar, sem passo manual.
        "fila_chamar_senha",
        # Prontuário não é uma permissão configurável — acesso é automático para
        # quem está vinculado a um cadastro de Profissional (ver accounts/permissoes.py).
    ],
    "Recepção / Atendimento": [
        "paciente_cadastrar",
        "paciente_editar",
        "agenda_criar",
        "agenda_editar",
        "central_atendimento_usar",
        "fila_autorizar_recepcao",
        "fila_especialidade_configurar",
        "mensagem_usar",
    ],
    # Perfil enxuto para o lançamento inicial "só fila de senha" (totem +
    # monitor + atendente chamando), antes de ativar Agenda/Financeiro/
    # Prontuário/Mensagens para a equipe — não inclui paciente_cadastrar,
    # agenda_criar/editar nem central_atendimento_usar de propósito. Reúne num
    # perfil só a mesma combinação de 4 permissões que já vinha sendo
    # concedida individualmente por usuário (ver joici/teste2/teste4 no banco
    # de dev) pra chamar senha funcionar: agenda_ver_dia (acessar a tela onde
    # ficam os formulários abaixo), fila_autorizar_recepcao ("estou no guichê"),
    # fila_especialidade_configurar ("especialidades de hoje") e
    # fila_chamar_senha (o botão chamar/concluir do painel).
    "Fila / Totem": [
        "agenda_ver_dia",
        "fila_autorizar_recepcao",
        "fila_especialidade_configurar",
        "fila_chamar_senha",
    ],
    "Financeiro": [
        "financeiro_ver_painel",
        "financeiro_ver_movimentacoes_avulsas",
        "financeiro_ver_contas_receber",
        "financeiro_ver_repasses_semanais",
        "financeiro_ver_fechamento_diario",
        "financeiro_ver_fechamento_mensal",
        "financeiro_ver_relatorio_detalhado",
        "financeiro_ver_repasse_convenio",
        "financeiro_ver_producao_convenio",
        "financeiro_lancar",
        "financeiro_conciliar",
        "financeiro_estornar",
        "financeiro_estornar_pagamento",
        "fila_autorizar_financeiro",
        "convenio_gerenciar",
        "faturamento_lote_lancar",
        "faturamento_lote_visualizar",
        "faturamento_comparativo_visualizar",
        "faturamento_retorno_lancar",
        "mensagem_usar",
    ],
    "Apoio clínico": [
        "agenda_ver_dia",
        "fila_chamar_senha",
        "mensagem_usar",
    ],
}


class Command(BaseCommand):
    help = "Cria/atualiza os perfis (grupos) iniciais e suas permissões padrão, conforme o documento da Etapa 2."

    def handle(self, *args, **options):
        for nome_perfil, codigos in PERFIS.items():
            grupo, criado = Group.objects.get_or_create(name=nome_perfil)
            permissoes = Permission.objects.filter(codename__in=codigos)

            encontrados = set(permissoes.values_list("codename", flat=True))
            faltando = set(codigos) - encontrados
            if faltando:
                self.stdout.write(
                    self.style.WARNING(
                        f"Atenção: as permissões {sorted(faltando)} não foram encontradas para o perfil "
                        f"'{nome_perfil}'. Rode 'python manage.py migrate' antes deste comando."
                    )
                )

            grupo.permissions.set(permissoes)
            situacao = "criado" if criado else "atualizado"
            self.stdout.write(self.style.SUCCESS(f"Perfil '{nome_perfil}' {situacao} com {permissoes.count()} permissão(ões)."))

        self.stdout.write(self.style.SUCCESS("Perfis iniciais configurados com sucesso."))
