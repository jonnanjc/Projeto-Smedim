"""
Chat interno — conversas privadas 1-para-1 entre usuários do sistema (ex.:
profissional falando com a recepção), para facilitar a comunicação sem
precisar de telefone/WhatsApp.

Decisões confirmadas com o cliente:
- Conversa é sempre entre DUAS pessoas específicas, não é um chat em grupo
  nem um mural aberto para todo mundo.
- Não precisa ser instantâneo — a tela busca mensagens novas por polling
  (a cada alguns segundos), não usa WebSocket.
- Para não acumular arquivo/registro no servidor, as mensagens são apagadas
  automaticamente no fim de cada dia: `apagar_mensagens_antigas()` roda
  sozinha no primeiro acesso de cada dia (ver context_processors.py), sem
  depender de cron. O management command `limpar_mensagens` continua
  existindo para rodar manualmente ou via cron à meia-noite, se quiser.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

# Data (localdate) da última limpeza feita por este processo, para não rodar
# um DELETE a cada requisição — só uma vez por dia por worker.
_ultima_limpeza = None


def apagar_mensagens_antigas(forcar=False):
    """Apaga todas as mensagens que não são de hoje. Devolve quantas apagou."""
    global _ultima_limpeza
    hoje = timezone.localdate()
    if not forcar and _ultima_limpeza == hoje:
        return 0
    apagadas, _ = Mensagem.objects.exclude(criado_em__date=hoje).delete()
    _ultima_limpeza = hoje
    return apagadas


class Mensagem(models.Model):
    remetente = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="remetente",
        on_delete=models.CASCADE,
        related_name="mensagens_enviadas",
    )
    destinatario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="destinatário",
        on_delete=models.CASCADE,
        related_name="mensagens_recebidas",
    )
    texto = models.TextField("mensagem")
    criado_em = models.DateTimeField("enviada em", auto_now_add=True)
    lida = models.BooleanField("lida", default=False)

    class Meta:
        verbose_name = "Mensagem"
        verbose_name_plural = "Mensagens"
        ordering = ["criado_em"]

    def __str__(self):
        return f"{self.remetente} → {self.destinatario}: {self.texto[:30]}"
