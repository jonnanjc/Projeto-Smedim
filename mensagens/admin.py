from django.contrib import admin

from .models import Mensagem


@admin.register(Mensagem)
class MensagemAdmin(admin.ModelAdmin):
    list_display = ("remetente", "destinatario", "texto", "criado_em", "lida")
    list_filter = ("lida", "criado_em")
    search_fields = ("remetente__username", "destinatario__username", "texto")
    date_hierarchy = "criado_em"
