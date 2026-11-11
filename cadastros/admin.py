"""
Painéis administrativos dos cadastros (Etapa 3).

Em vez de usar as permissões padrão do Django (que seriam algo como
"cadastros.add_paciente"), estas telas checam as permissões granulares que já
definimos no documento da Etapa 2 e no comando `seed_perfis` (ex:
"accounts.paciente_cadastrar"). Isso mantém tudo consistente com a tabela de
perfis que já foi validada, em vez de criar um segundo sistema de permissões
em paralelo.
"""
from django.contrib import admin

from .models import (
    Convenio,
    ExcecaoAtendimento,
    HorarioAtendimento,
    Paciente,
    Procedimento,
    Profissional,
    ProfissionalEspecialidade,
    TabelaPrecoConvenio,
)


class PermissaoCustomizadaMixin:
    """Troca as checagens padrão do Django Admin pelas nossas permissões nomeadas (ver docstring do módulo)."""

    permissao_ver = None
    permissao_adicionar = None
    permissao_editar = None
    permissao_excluir = None

    def has_view_permission(self, request, obj=None):
        codigo = self.permissao_ver or self.permissao_editar
        return codigo is not None and request.user.has_perm(f"accounts.{codigo}")

    def has_add_permission(self, request):
        return self.permissao_adicionar is not None and request.user.has_perm(f"accounts.{self.permissao_adicionar}")

    def has_change_permission(self, request, obj=None):
        return self.permissao_editar is not None and request.user.has_perm(f"accounts.{self.permissao_editar}")

    def has_delete_permission(self, request, obj=None):
        return self.permissao_excluir is not None and request.user.has_perm(f"accounts.{self.permissao_excluir}")

    def has_module_permission(self, request):
        # Sobrescrevemos o padrão do Django porque nossas permissões customizadas
        # "moram" no app 'accounts' (onde foram declaradas), não no app 'cadastros'
        # onde estes modelos vivem — sem isso, a seção nem apareceria no menu.
        return (
            self.has_view_permission(request)
            or self.has_add_permission(request)
            or self.has_change_permission(request)
            or self.has_delete_permission(request)
        )


@admin.register(Paciente)
class PacienteAdmin(PermissaoCustomizadaMixin, admin.ModelAdmin):
    permissao_ver = "paciente_cadastrar"
    permissao_adicionar = "paciente_cadastrar"
    permissao_editar = "paciente_editar"
    permissao_excluir = "paciente_excluir"

    list_display = ("nome_completo", "cpf", "convenio", "telefone", "ativo")
    list_filter = ("ativo", "convenio")
    search_fields = ("nome_completo", "cpf", "telefone", "email")

    fieldsets = (
        ("Dados pessoais", {"fields": ("nome_completo", "cpf", "rg", "data_nascimento", "telefone", "email")}),
        ("Endereço", {"fields": ("bairro", "cidade")}),
        ("Convênio", {"fields": ("convenio", "numero_carteirinha")}),
        ("Responsável (se paciente menor de idade / dependente)", {"fields": ("responsavel_nome", "responsavel_cpf")}),
        ("Situação", {"fields": ("ativo",)}),
    )


class ProfissionalEspecialidadeInline(admin.TabularInline):
    model = ProfissionalEspecialidade
    extra = 0


@admin.register(Profissional)
class ProfissionalAdmin(PermissaoCustomizadaMixin, admin.ModelAdmin):
    permissao_ver = "profissional_gerenciar"
    permissao_adicionar = "profissional_gerenciar"
    permissao_editar = "profissional_gerenciar"
    permissao_excluir = "profissional_gerenciar"

    list_display = ("nome", "registro_profissional", "especialidade_exibicao", "cbo_codigo", "percentual_desconto", "ativo")
    list_filter = ("ativo", "especialidade")
    search_fields = ("nome", "registro_profissional", "especialidade", "especialidade_outra")
    readonly_fields = ("cbo_codigo",)
    inlines = [ProfissionalEspecialidadeInline]

    @admin.display(description="Especialidade")
    def especialidade_exibicao(self, obj):
        return obj.especialidade_exibicao


@admin.register(HorarioAtendimento)
class HorarioAtendimentoAdmin(PermissaoCustomizadaMixin, admin.ModelAdmin):
    permissao_ver = "profissional_gerenciar"
    permissao_adicionar = "profissional_gerenciar"
    permissao_editar = "profissional_gerenciar"
    permissao_excluir = "profissional_gerenciar"

    list_display = ("profissional", "get_dia_semana_display", "turno", "horario_inicio", "horario_fim")
    list_filter = ("dia_semana", "turno")
    search_fields = ("profissional__nome",)

    @admin.display(description="Dia da semana")
    def get_dia_semana_display(self, obj):
        return obj.get_dia_semana_display()


@admin.register(ExcecaoAtendimento)
class ExcecaoAtendimentoAdmin(PermissaoCustomizadaMixin, admin.ModelAdmin):
    permissao_ver = "profissional_gerenciar"
    permissao_adicionar = "profissional_gerenciar"
    permissao_editar = "profissional_gerenciar"
    permissao_excluir = "profissional_gerenciar"

    list_display = ("profissional", "data", "turno", "horario_inicio", "horario_fim")
    list_filter = ("data", "turno")
    search_fields = ("profissional__nome",)


@admin.register(Procedimento)
class ProcedimentoAdmin(PermissaoCustomizadaMixin, admin.ModelAdmin):
    permissao_ver = "procedimento_gerenciar"
    permissao_adicionar = "procedimento_gerenciar"
    permissao_editar = "procedimento_gerenciar"
    permissao_excluir = "procedimento_gerenciar"

    list_display = (
        "nome", "codigo", "permite_repeticao_semanal", "eh_ultrassom", "eh_consulta",
        "exame_sem_profissional", "exame_com_preparo", "recursos_necessarios", "ativo",
    )
    list_filter = (
        "ativo", "permite_repeticao_semanal", "eh_ultrassom", "eh_consulta", "exame_sem_profissional",
        "exame_com_preparo",
    )
    search_fields = ("nome", "codigo")
    filter_horizontal = ("profissionais_habilitados",)


class TabelaPrecoConvenioInline(admin.TabularInline):
    model = TabelaPrecoConvenio
    extra = 1
    autocomplete_fields = ["procedimento", "profissional"]


@admin.register(Convenio)
class ConvenioAdmin(PermissaoCustomizadaMixin, admin.ModelAdmin):
    permissao_ver = "convenio_gerenciar"
    permissao_adicionar = "convenio_gerenciar"
    permissao_editar = "convenio_gerenciar"
    permissao_excluir = "convenio_gerenciar"

    list_display = ("nome", "codigo_ans", "prazo_pagamento_dias", "ativo")
    list_filter = ("ativo",)
    search_fields = ("nome", "codigo_ans")
    inlines = [TabelaPrecoConvenioInline]
