"""
Lista as tabelas e colunas do banco PostgreSQL do sistema ANTIGO da clínica
(conexão "legado", ver DB_LEGADO_* no .env e config/settings.py) — primeiro
passo antes de preencher o mapeamento em importar_pacientes_legado.py e
importar_prontuario_legado.py, já que ainda não conhecemos o schema real
desse banco.

Uso:
    python manage.py inspecionar_legado
        Lista todas as tabelas do schema "public".

    python manage.py inspecionar_legado --tabela paciente
        Lista as colunas (nome, tipo, se aceita nulo) da tabela informada.

    python manage.py inspecionar_legado --tabela paciente --amostra 3
        Mostra também até 3 linhas de exemplo dessa tabela.
        CUIDADO: isso imprime dado real de paciente no terminal — o dado é
        real da clínica (LGPD). Use só quando precisar mesmo conferir o
        formato de uma coluna, e não deixe a saída em log/arquivo compartilhado.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connections


class Command(BaseCommand):
    help = "Lista tabelas/colunas do banco do sistema antigo (conexão 'legado') para mapear os campos da importação."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tabela",
            help="Mostra só as colunas desta tabela (padrão: lista os nomes de todas as tabelas do schema 'public').",
        )
        parser.add_argument(
            "--amostra",
            type=int,
            default=0,
            help="Mostra N linhas de exemplo da tabela (exige --tabela). Contém dado real de paciente — use com cuidado.",
        )

    def handle(self, *args, **options):
        if "legado" not in connections.databases:
            raise CommandError(
                "Conexão 'legado' não configurada — preencha DB_LEGADO_NAME no .env "
                "(e DB_LEGADO_HOST/USER/PASSWORD/PORT, se precisar) antes de rodar este comando."
            )

        tabela = options.get("tabela")
        amostra = options.get("amostra") or 0
        conexao = connections["legado"]

        with conexao.cursor() as cursor:
            if not tabela:
                self._listar_tabelas(cursor)
                return
            self._descrever_tabela(cursor, conexao, tabela, amostra)

    def _listar_tabelas(self, cursor):
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )
        tabelas = [linha[0] for linha in cursor.fetchall()]
        if not tabelas:
            self.stdout.write(self.style.WARNING("Nenhuma tabela encontrada no schema 'public' — confira a conexão."))
            return
        self.stdout.write(f"{len(tabelas)} tabela(s) encontrada(s) no schema 'public':")
        for nome in tabelas:
            self.stdout.write(f"  - {nome}")
        self.stdout.write("\nRode com --tabela <nome> para ver as colunas de uma tabela específica.")

    def _descrever_tabela(self, cursor, conexao, tabela, amostra):
        cursor.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
            [tabela],
        )
        colunas = cursor.fetchall()
        if not colunas:
            raise CommandError(f"Tabela '{tabela}' não encontrada no schema 'public' (confira o nome exato).")

        self.stdout.write(f"Colunas de '{tabela}':")
        for nome, tipo, aceita_nulo in colunas:
            sufixo = "" if aceita_nulo == "YES" else " (obrigatório)"
            self.stdout.write(f"  - {nome}: {tipo}{sufixo}")

        if amostra > 0:
            tabela_quotada = conexao.ops.quote_name(tabela)
            cursor.execute(f"SELECT * FROM {tabela_quotada} LIMIT %s", [amostra])
            nomes_colunas = [descricao[0] for descricao in cursor.description]
            self.stdout.write(
                self.style.WARNING(f"\n{amostra} linha(s) de exemplo — CONTÉM DADO REAL DE PACIENTE:")
            )
            for linha in cursor.fetchall():
                self.stdout.write("  " + str(dict(zip(nomes_colunas, linha))))
