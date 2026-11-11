"""
Configuração WSGI — usada pelo servidor de aplicação em produção.

No servidor Ubuntu da clínica, este é o módulo apontado pelo Gunicorn
(rodando como serviço systemd — ver LEIA-ME-INSTALACAO-CLINICA.md):
    gunicorn config.wsgi:application --bind 127.0.0.1:8001 --workers 3
"""
import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()
