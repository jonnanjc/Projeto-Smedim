from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import ConfiguracaoTema, LogAuditoria, Usuario


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    """
    Gestão de usuários e perfis (Groups) direto pelo Django Admin.
    Isso já cobre, de forma imediata, a tela de "cadastro de usuários e perfis"
    descrita no documento — sem precisarmos escrever uma tela própria nesta etapa.
    """

    list_display = (
        "username",
        "first_name",
        "last_name",
        "status",
        "is_active",
        "exigir_troca_senha",
        "last_login",
    )
    list_filter = ("status", "is_active", "groups")
    search_fields = ("username", "first_name", "last_name", "email")

    fieldsets = UserAdmin.fieldsets + (
        (
            "Controle de acesso (Etapa 2)",
            {
                "fields": (
                    "status",
                    "tentativas_login_invalidas",
                    "exigir_troca_senha",
                    "profissional_id",
                    "tema",
                )
            },
        ),
    )

    readonly_fields = UserAdmin.readonly_fields + ("tentativas_login_invalidas",)

    actions = ["desbloquear_usuarios"]

    @admin.action(description="Desbloquear usuário(s) selecionado(s) (zera tentativas e reativa)")
    def desbloquear_usuarios(self, request, queryset):
        atualizados = 0
        for usuario in queryset:
            usuario.tentativas_login_invalidas = 0
            usuario.status = Usuario.Status.ATIVO
            usuario.is_active = True
            usuario.save(update_fields=["tentativas_login_invalidas", "status", "is_active"])
            atualizados += 1
        self.message_user(request, f"{atualizados} usuário(s) desbloqueado(s) com sucesso.")


@admin.register(LogAuditoria)
class LogAuditoriaAdmin(admin.ModelAdmin):
    list_display = ("data_hora", "usuario", "acao", "ip")
    list_filter = ("usuario",)
    search_fields = ("acao", "usuario__username")
    date_hierarchy = "data_hora"

    def has_add_permission(self, request):
        # Log é gerado automaticamente pelo sistema, não deve ser criado manualmente.
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ConfiguracaoTema)
class ConfiguracaoTemaAdmin(admin.ModelAdmin):
    """
    Painel do Administrador para ligar/desligar a faixa de destaque de
    campanha de conscientização do mês (Setembro Amarelo, Outubro Rosa etc.).
    Qual campanha aparece é automático, conforme o mês atual do servidor
    (accounts.models.CAMPANHA_DO_MES) — aqui só se decide se a faixa aparece
    ou não. É um "singleton": sempre existe só uma linha de configuração.
    """

    list_display = ("ativo",)
    fields = ("ativo",)

    def has_add_permission(self, request):
        # Só pode existir uma configuração (linha única) — se já existe, não deixa criar outra.
        return not ConfiguracaoTema.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # Atalho: manda direto para a tela de edição da configuração única,
        # em vez de mostrar uma listagem de 1 item só.
        config = ConfiguracaoTema.carregar()
        from django.shortcuts import redirect

        return redirect("admin:accounts_configuracaotema_change", config.pk)
