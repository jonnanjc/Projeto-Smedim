"""
Apaga as mensagens do chat interno que não são de hoje, para o servidor
nunca acumular histórico de conversa (decisão do cliente: "zerado no fim
do dia").

Obs.: o próprio sistema já faz essa limpeza sozinho no primeiro acesso de
cada dia (mensagens.models.apagar_mensagens_antigas), então o cron abaixo é
opcional — serve só para apagar logo à meia-noite mesmo sem ninguém acessar.

Uso manual:
    python manage.py limpar_mensagens

Uso em produção (Linux): agendar para rodar todo dia à meia-noite via cron.
Exemplo de linha de crontab (rodando o Python do virtualenv do projeto):

    0 0 * * * cd /caminho/para/clinica-sistema/backend && /caminho/para/venv/bin/python manage.py limpar_mensagens >> /var/log/smedim_limpar_mensagens.log 2>&1

Para editar o crontab do usuário que roda a aplicação: `crontab -e`.
"""
from django.core.management.base import BaseCommand

from mensagens.models import apagar_mensagens_antigas


class Command(BaseCommand):
    help = "Apaga mensagens do chat interno que não são de hoje (roda diariamente, de preferência à meia-noite)."

    def handle(self, *args, **options):
        apagadas = apagar_mensagens_antigas(forcar=True)
        self.stdout.write(self.style.SUCCESS(f"{apagadas} mensagem(ns) antiga(s) apagada(s)."))
