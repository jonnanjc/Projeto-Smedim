from django.contrib import admin

from .models import Senha


@admin.register(Senha)
class SenhaAdmin(admin.ModelAdmin):
    """
    Só leitura — a única forma válida de criar uma Senha é pelo totem
    (`Senha.emitir`, que calcula número/código/data corretamente). Serve para
    a clínica acompanhar/conferir as senhas emitidas no dia.
    """

    list_display = ("codigo", "tipo", "motivo_prioridade", "especialidade", "procedimento_exame", "status", "data", "criado_em")
    list_filter = ("tipo", "status", "data")
    ordering = ("-criado_em",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
