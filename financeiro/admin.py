from django.contrib import admin

from .models import LoteFaturamento, MovimentacaoCaixa, RetornoLoteItem


class RetornoLoteItemInline(admin.TabularInline):
    model = RetornoLoteItem
    extra = 0
    autocomplete_fields = ["agendamento"]


@admin.register(LoteFaturamento)
class LoteFaturamentoAdmin(admin.ModelAdmin):
    list_display = ("identificacao", "convenio", "data_envio", "valor_total_enviado", "status")
    list_filter = ("status", "convenio")
    search_fields = ("numero_lote", "convenio__nome")
    autocomplete_fields = ["convenio", "agendamentos", "criado_por"]
    date_hierarchy = "data_envio"
    inlines = [RetornoLoteItemInline]

    @admin.display(description="Lote")
    def identificacao(self, obj):
        return obj.identificacao


@admin.register(RetornoLoteItem)
class RetornoLoteItemAdmin(admin.ModelAdmin):
    list_display = ("lote", "agendamento", "valor_pago", "valor_glosado", "data_retorno")
    list_filter = ("data_retorno",)
    autocomplete_fields = ["lote", "agendamento"]


@admin.register(MovimentacaoCaixa)
class MovimentacaoCaixaAdmin(admin.ModelAdmin):
    list_display = ("data", "tipo", "valor", "descricao", "profissional", "criado_por")
    list_filter = ("tipo", "data")
    search_fields = ("descricao",)
    autocomplete_fields = ["profissional", "criado_por"]
    date_hierarchy = "data"
