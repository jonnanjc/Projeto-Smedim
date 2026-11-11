from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Usuários e Perfis de Acesso"

    def ready(self):
        # Dinheiro (Decimal) com ponto de milhar nas telas: 150.000,00 — ver accounts/moeda.py.
        from .moeda import instalar_milhar_em_decimais

        instalar_milhar_em_decimais()
