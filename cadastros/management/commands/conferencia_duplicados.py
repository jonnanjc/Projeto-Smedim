"""
Leva a conferência de pacientes duplicados feita NUMA CÓPIA do banco para o banco da clínica —
pedido da clínica (06/11/2026): conferir com calma os ~500 grupos "só nome igual" numa cópia,
fora do horário, e aplicar tudo na clínica de uma vez.

    exportar  (na cópia):   grava um arquivo .json com TODAS as unificações da cópia (as do
                            comando em lote e as feitas na tela) e os grupos marcados como
                            "não são a mesma pessoa".
    importar  (na clínica): repete essas decisões. Por padrão só SIMULA; --confirmar grava.

O arquivo não tem dado pessoal: só os números (IDs) das fichas e uma impressão digital
(hash) do nome de cada uma. Os IDs são os mesmos na cópia e na clínica, porque a cópia é um
pg_dump da clínica. A impressão digital do nome garante que o ID ainda é a mesma pessoa: se
alguém trocou o nome da ficha na clínica depois da cópia, a decisão é pulada.

Cada decisão é pulada (e aparece no relatório) se, na clínica: alguma ficha não existe mais,
mudou de nome, já foi unificada em outra ficha, ou os CPFs conflitam. Decisão que já está feita
(ex.: importar duas vezes) conta como "já estava feito". Fichas novas cadastradas na clínica
depois da cópia não são tocadas — depois do importar, rode o unificar_pacientes_duplicados de
novo pra pegar os casos de certeza novos, e o resto aparece na tela Possíveis duplicados.

Uso:
    python manage.py conferencia_duplicados exportar /caminho/conferencia.json
    python manage.py conferencia_duplicados importar /caminho/conferencia.json             # simulação
    python manage.py conferencia_duplicados importar /caminho/conferencia.json --confirmar
"""
import hashlib
import json
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from cadastros.models import DuplicidadeIgnorada, Paciente
from cadastros.unificacao import UnificacaoInvalida, nome_normalizado, unificar


def impressao_nome(nome):
    return hashlib.sha256(nome_normalizado(nome).encode("utf-8")).hexdigest()[:16]


class _Simulacao(Exception):
    pass


class Command(BaseCommand):
    help = "Exporta (da cópia) ou importa (na clínica) a conferência de pacientes duplicados."

    def add_arguments(self, parser):
        parser.add_argument("acao", choices=["exportar", "importar"])
        parser.add_argument("arquivo")
        parser.add_argument("--confirmar", action="store_true", help="importar: grava de verdade (sem isso, só simula).")

    def handle(self, *args, **options):
        if options["acao"] == "exportar":
            self._exportar(options["arquivo"])
        else:
            self._importar(options["arquivo"], options["confirmar"])

    # ------------------------------------------------------------------ exportar
    def _exportar(self, caminho):
        nomes = dict(Paciente.objects.values_list("pk", "nome_completo"))
        grupos = defaultdict(list)
        for pk, principal_id in Paciente.objects.filter(unificado_em__isnull=False).values_list("pk", "unificado_em_id"):
            grupos[principal_id].append(pk)
        unificacoes = [
            {"principal": principal_id, "copias": sorted(copias)} for principal_id, copias in sorted(grupos.items())
        ]
        ignorados = []
        for chave in DuplicidadeIgnorada.objects.values_list("ids", flat=True):
            ids = [int(pk) for pk in chave.split(",") if pk.isdigit()]
            ignorados.append({"ids": ids})
        usados = {u["principal"] for u in unificacoes} | {pk for u in unificacoes for pk in u["copias"]}
        usados |= {pk for item in ignorados for pk in item["ids"]}
        dados = {
            "versao": 1,
            "exportado_em": timezone.localtime().isoformat(),
            "nomes": {str(pk): impressao_nome(nomes[pk]) for pk in sorted(usados) if pk in nomes},
            "unificacoes": unificacoes,
            "ignorados": ignorados,
        }
        with open(caminho, "w", encoding="utf-8") as arquivo:
            json.dump(dados, arquivo, indent=1)
        self.stdout.write(self.style.SUCCESS(f"Exportado: {caminho}"))
        self.stdout.write(
            f"{len(unificacoes)} ficha(s) principal(is) com {sum(len(u['copias']) for u in unificacoes)} ficha(s) "
            f"juntada(s); {len(ignorados)} grupo(s) marcado(s) como pessoas diferentes."
        )

    # ------------------------------------------------------------------ importar
    def _importar(self, caminho, confirmar):
        try:
            with open(caminho, encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
        except (OSError, ValueError) as erro:
            raise CommandError(f"Não consegui ler {caminho}: {erro}")
        if dados.get("versao") != 1:
            raise CommandError("Arquivo de conferência em formato desconhecido.")

        impressoes = {int(pk): valor for pk, valor in dados["nomes"].items()}
        fichas = Paciente.objects.in_bulk(list(impressoes))
        totais = defaultdict(int)
        problemas = []

        def conferir_ids(ids):
            """Mensagem do problema, ou None se todas as fichas existem com o mesmo nome."""
            for pk in ids:
                ficha = fichas.get(pk)
                if ficha is None:
                    return f"ficha #{pk} não existe mais"
                if impressao_nome(ficha.nome_completo) != impressoes.get(pk):
                    return f"ficha #{pk} mudou de nome desde a cópia (agora '{ficha.nome_completo}')"
            return None

        try:
            with transaction.atomic():
                for item in dados["unificacoes"]:
                    principal_id, copias_ids = item["principal"], item["copias"]
                    problema = conferir_ids([principal_id] + copias_ids)
                    if not problema:
                        principal = Paciente.objects.get(pk=principal_id)
                        if principal.unificado_em_id:
                            problema = f"ficha principal #{principal_id} já foi unificada em #{principal.unificado_em_id}"
                    if problema:
                        problemas.append(f"Unificação em #{principal_id}: {problema}")
                        totais["puladas"] += 1
                        continue
                    copias = list(Paciente.objects.filter(pk__in=copias_ids))
                    ja_feitas = [c for c in copias if c.unificado_em_id == principal_id]
                    outras = [c for c in copias if c.unificado_em_id and c.unificado_em_id != principal_id]
                    pendentes = [c for c in copias if not c.unificado_em_id]
                    totais["ja_feitas"] += len(ja_feitas)
                    for copia in outras:
                        problemas.append(f"Ficha #{copia.pk}: já foi unificada em #{copia.unificado_em_id} (e não em #{principal_id})")
                        totais["puladas"] += 1
                    if not pendentes:
                        continue
                    try:
                        movidos = unificar(principal, pendentes, origem="conferência importada da cópia")
                    except UnificacaoInvalida as erro:
                        problemas.append(f"Unificação em #{principal_id}: {erro}")
                        totais["puladas"] += 1
                    else:
                        totais["fichas_juntadas"] += len(pendentes)
                        totais["movidos"] += movidos

                for item in dados["ignorados"]:
                    ids = item["ids"]
                    problema = conferir_ids(ids)
                    if problema:
                        problemas.append(f"Pessoas diferentes {DuplicidadeIgnorada.chave(ids)}: {problema}")
                        totais["puladas"] += 1
                        continue
                    _obj, criado = DuplicidadeIgnorada.objects.get_or_create(
                        ids=DuplicidadeIgnorada.chave(ids), defaults={"nome": fichas[ids[0]].nome_completo[:150]}
                    )
                    totais["ignorados" if criado else "ignorados_ja"] += 1
                if not confirmar:
                    raise _Simulacao
        except _Simulacao:
            pass

        modo = "GRAVADO" if confirmar else "SIMULAÇÃO (nada foi gravado — use --confirmar para gravar)"
        self.stdout.write(self.style.MIGRATE_HEADING(f"\nConferência de duplicados importada — {modo}"))
        self.stdout.write(f"Fichas {'juntadas' if confirmar else 'a juntar'}: {totais['fichas_juntadas']} "
                          f"({totais['movidos']} registro(s) de histórico)")
        self.stdout.write(f"Já estavam juntadas: {totais['ja_feitas']}")
        self.stdout.write(f"Grupos marcados como pessoas diferentes: {totais['ignorados']} (já estavam: {totais['ignorados_ja']})")
        if problemas:
            self.stdout.write(self.style.WARNING(f"Decisões puladas (conferir na tela Possíveis duplicados): {totais['puladas']}"))
            for linha in problemas:
                self.stdout.write(f"  - {linha}")
        else:
            self.stdout.write("Nenhuma decisão pulada.")
