#!/usr/bin/env python
"""Utilitário de linha de comando do Django para o Sistema de Gestão da Clínica SMEDIM."""
import os
import sys


def main():
    try:
        from dotenv import load_dotenv

        load_dotenv()  # carrega variáveis do arquivo .env, se existir
    except ImportError:
        pass

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Não foi possível importar o Django. Verifique se ele está instalado e "
            "disponível na variável de ambiente PYTHONPATH, e se o ambiente virtual "
            "está ativado."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
