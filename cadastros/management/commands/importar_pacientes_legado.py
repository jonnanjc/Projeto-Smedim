"""
Importa os pacientes do banco PostgreSQL do sistema ANTIGO da clínica para
cá (cadastros.Paciente) — ver conexão "legado" em config/settings.py.

SCHEMA DE ORIGEM (mapeado em 2026-07-30 a partir do backup de produção):
sistema Grails/Hibernate com modelo "participante + papel" — não existe uma
tabela "paciente" ou "cliente" isolada. `participante` guarda os dados da
pessoa (nome, documento/CPF, contato); `papel` é o "papel" que ela exerce no
sistema (paciente, profissional, convênio...), diferenciado pela coluna
`class`. Todo o resto do sistema antigo referencia `papel.id`, nunca
`participante.id` direto — por isso esta importação usa `papel.id` como
Paciente.origem_legado_id (é o mesmo ID que prontuario.cliente_id,
agenda_cliente.cliente_id etc. usam pra apontar pro paciente).

ANTES DE USAR:
    1. Restaure o dump/backup do sistema antigo num Postgres acessível e
       preencha DB_LEGADO_* no .env (ver .env.example).
    2. Confira CLASSE_CLIENTE abaixo — é um palpite ("Cliente", convenção
       padrão do Grails pro nome simples da classe do domínio), ainda não
       confirmado contra o dado real. Se a simulação (ver abaixo) trouxer 0
       pacientes lidos, é o primeiro lugar a ajustar.
    3. Rode SEM --confirmar primeiro (modo simulação, é o padrão) e confira
       o resumo. Só use --confirmar quando o resumo estiver correto.

Reexecutável: os dois sistemas devem conviver por um tempo, então cada
paciente é casado pelo ID de origem (papel.id, ver Paciente.origem_legado_id)
— rodar de novo não duplica ninguém. Se o paciente já existir aqui por CPF
(cadastrado manualmente antes de existir essa importação), casa por CPF na
primeira vez em vez de duplicar.

FORA DO ESCOPO (decisão do cliente, 2026-07-29): convênio/carteirinha
(tabela cliente_convenio) não são migrados — Paciente.convenio fica em
branco para todo paciente importado. Endereço: a origem só tem um campo
único "endereco" (texto livre), sem separação bairro/cidade — não dá pra
separar com confiança automaticamente, então os dois campos também ficam em
branco aqui (dado não é perdido — continua no backup original).

Uso:
    python manage.py importar_pacientes_legado               # simulação (não grava nada)
    python manage.py importar_pacientes_legado --confirmar    # grava de verdade
"""
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone

from cadastros.models import Paciente

# Valor da coluna papel.class que identifica um paciente — ver docstring
# acima sobre a incerteza deste valor.
CLASSE_CLIENTE = "com.mkmikael.smedim.Cliente"

LIMITE_DETALHE = 20


class Command(BaseCommand):
    help = "Importa pacientes do banco PostgreSQL do sistema antigo (ver DB_LEGADO_* no .env)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirmar",
            action="store_true",
            help="Grava de verdade. Sem esta flag, só simula e mostra o resumo (padrão, seguro).",
        )

    def handle(self, *args, **options):
        conexao = self._validar_conexao()
        confirmar = options["confirmar"]

        registros = self._ler_origem(conexao)

        criados = atualizados = casados_por_cpf = ignorados_unificados = 0
        erros = []

        for registro in registros:
            id_origem = str(registro["origem_id"])
            dados = self._normalizar_dados(registro)

            try:
                paciente, situacao = self._resolver_paciente(id_origem, dados)
                if paciente.unificado_em_id:
                    # Cópia de um duplicado já unificado (ver unificar_pacientes_duplicados):
                    # não atualiza (o CPF dela pode ter ido pra ficha principal, e reescrever
                    # daria conflito) — ela só existe pra não ser recriada aqui.
                    ignorados_unificados += 1
                    continue
                for campo, valor in dados.items():
                    setattr(paciente, campo, valor)
                paciente.origem_legado_id = id_origem
                paciente.importado_do_legado_em = timezone.now()

                paciente.full_clean()
                if confirmar:
                    paciente.save()
            except ValidationError as exc:
                erros.append((id_origem, exc))
                continue

            if situacao == "novo":
                criados += 1
            elif situacao == "cpf":
                casados_por_cpf += 1
            else:
                atualizados += 1

        self._mostrar_resumo(len(registros), criados, atualizados, casados_por_cpf, erros, confirmar)
        if ignorados_unificados:
            self.stdout.write(f"Ignorados por já estarem unificados em outra ficha: {ignorados_unificados}")

    def _validar_conexao(self):
        if "legado" not in connections.databases:
            raise CommandError(
                "Conexão 'legado' não configurada — preencha DB_LEGADO_NAME (e DB_LEGADO_HOST/"
                "USER/PASSWORD/PORT se precisar) no .env antes de rodar este comando."
            )
        return connections["legado"]

    def _ler_origem(self, conexao):
        # papel.id (não participante.id!) é o identificador que o resto do
        # sistema antigo usa pra referenciar o paciente — ver docstring no
        # topo do arquivo. Vira Paciente.origem_legado_id, e é o mesmo valor
        # que importar_prontuario_legado.py procura via prontuario.cliente_id.
        sql = """
            SELECT
                papel.id AS origem_id,
                participante.nome AS nome_completo,
                participante.documento AS cpf,
                participante.email AS email,
                participante.fone1 AS fone1,
                participante.fone2 AS fone2,
                participante.nascimento AS data_nascimento
            FROM papel
            JOIN participante ON participante.id = papel.participante_id
            WHERE papel.class = %s
        """
        nomes_campos = ["origem_id", "nome_completo", "cpf", "email", "fone1", "fone2", "data_nascimento"]
        with conexao.cursor() as cursor:
            cursor.execute(sql, [CLASSE_CLIENTE])
            linhas = cursor.fetchall()
        return [dict(zip(nomes_campos, linha)) for linha in linhas]

    def _normalizar_dados(self, registro):
        """Converte os valores lidos da origem para o formato esperado pelos campos do
        Paciente. CPF é o único campo que precisa virar None (não "") quando vazio: o
        campo é unique=True, e duas strings vazias ("") colidem nessa constraint tanto
        no Postgres quanto no SQLite (NULL, ao contrário, nunca colide com outro NULL)."""
        cpf = (registro.get("cpf") or "").strip() or None
        telefone = (registro.get("fone1") or "").strip() or (registro.get("fone2") or "").strip()
        nascimento = registro.get("data_nascimento")
        return {
            "nome_completo": (registro.get("nome_completo") or "").strip(),
            "cpf": cpf,
            "email": (registro.get("email") or "").strip(),
            "telefone": telefone,
            "data_nascimento": nascimento.date() if hasattr(nascimento, "date") else nascimento,
        }

    def _resolver_paciente(self, id_origem, dados):
        """Acha o Paciente correspondente a este registro de origem, ou prepara um novo.
        Devolve (paciente, situacao) — situacao é "novo", "atualizado" ou "cpf" (já
        existia aqui por CPF, cadastrado antes desta importação existir)."""
        paciente = Paciente.objects.filter(origem_legado_id=id_origem).first()
        if paciente:
            return paciente, "atualizado"

        cpf = dados.get("cpf")
        if cpf:
            candidato = Paciente.objects.filter(cpf=cpf).first()
            if candidato:
                return candidato, "cpf"

        return Paciente(), "novo"

    def _mostrar_resumo(self, total, criados, atualizados, casados_por_cpf, erros, confirmar):
        modo = "GRAVANDO" if confirmar else "SIMULAÇÃO (nada foi gravado)"
        self.stdout.write(self.style.WARNING(f"--- {modo} ---"))
        self.stdout.write(f"Total lido do sistema antigo (papel.class = '{CLASSE_CLIENTE}'): {total}")
        if total == 0:
            self.stdout.write(self.style.ERROR(
                "0 registros encontrados — CLASSE_CLIENTE provavelmente está errado. Confira o "
                "valor real da coluna papel.class (ajuste a constante no topo deste arquivo)."
            ))
        self.stdout.write(f"Novos: {criados}")
        self.stdout.write(f"Já importados antes (atualizados): {atualizados}")
        self.stdout.write(f"Casados com paciente já existente por CPF: {casados_por_cpf}")

        if erros:
            self.stdout.write(self.style.ERROR(f"Erros de validação: {len(erros)}"))
            for id_origem, exc in erros[:LIMITE_DETALHE]:
                self.stdout.write(self.style.ERROR(f"  id={id_origem}: {exc}"))
            if len(erros) > LIMITE_DETALHE:
                self.stdout.write(self.style.ERROR(f"  ... e mais {len(erros) - LIMITE_DETALHE}"))

        if not confirmar:
            self.stdout.write(self.style.WARNING("Rode novamente com --confirmar para gravar de verdade."))
