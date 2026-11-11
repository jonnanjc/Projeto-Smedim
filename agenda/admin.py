from django.contrib import admin

from .models import Agendamento, AgendamentoItem


class AgendamentoItemInline(admin.TabularInline):
    model = AgendamentoItem
    extra = 0
    autocomplete_fields = ["procedimento"]
    readonly_fields = ["retorno_de"]


@admin.register(Agendamento)
class AgendamentoAdmin(admin.ModelAdmin):
    list_display = ("paciente", "profissional", "data_hora_inicio", "data_hora_fim", "forma_atendimento", "status")
    list_filter = ("status", "forma_atendimento", "profissional")
    search_fields = ("paciente__nome_completo", "profissional__nome")
    date_hierarchy = "data_hora_inicio"
    # 'procedimento' saiu daqui — é legado (ver comentário em agenda/models.py),
    # substituído pelo inline de AgendamentoItem abaixo.
    autocomplete_fields = ["paciente", "profissional", "convenio"]
    inlines = [AgendamentoItemInline]
