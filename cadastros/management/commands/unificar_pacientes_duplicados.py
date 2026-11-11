"""
Unifica em LOTE os cadastros de paciente duplicados de CERTEZA — pedido da clínica
(13/10/2026, ampliado em 06/11/2026). A regra de unificação em si (o que move, o que completa,
por que a cópia não é apagada) está em cadastros/unificacao.py.

CRITÉRIO DO LOTE: mesmo nome (ignorando acento, maiúscula/minúscula, pontuação e espaços — ex.:
"Raissa" e "Raíssa") E mesma data de nascimento. Telefone diferente não impede (é comum o
paciente trocar de número). CPFs diferentes no mesmo grupo impedem: o grupo fica de fora e vai
pro relatório pra alguém conferir.

Nome igual sem nascimento igual NÃO é unificado aqui (pode ser homônimo): esses aparecem na tela
Cadastros → Pacientes → Possíveis duplicados, onde alguém confere e unifica com um clique.

FICHA PRINCIPAL de cada grupo: a ativa; depois a cadastrada aqui pela recepção (dados mais
atuais); depois a com mais histórico; com CPF; com mais dados preenchidos; a mais antiga.

ANTES DE USAR: faça o backup do banco. Rode SEM --confirmar primeiro (simulação, o padrão):
mostra o resumo e grava um relatório CSV com cada grupo pra conferência. Só use --confirmar
quando o relatório estiver certo. Reexecutável: cópias já unificadas ficam de fora.

Uso:
    python manage.py unificar_pacientes_duplicados                 # simulação (não grava nada)
    python manage.py unificar_pacientes_duplicados --confirmar     # grava de verdade
"""
import csv

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from cadastros.models import Paciente
from cadastros.unificacao import (
    UnificacaoInvalida,
    contar_historico,
    cpfs_conflitantes,
    grupos_certos,
    sugerir_principal,
    unificar,
)


class _Simulacao(Exception):
    """Desfaz tudo no fim da simulação (roda a unificação de verdade dentro da transação e
    joga fora — assim a simulação conta exatamente o que o --confirmar faria)."""


class Command(BaseCommand):
    help = "Unifica pacientes duplicados (mesmo nome + mesma data de nascimento) numa ficha só."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirmar",
            action="store_true",
            help="Grava de verdade. Sem esta flag, só simula e gera o relatório (padrão, seguro).",
        )
        parser.add_argument(
            "--relatorio",
            default=None,
            help="Caminho do relatório CSV (padrão: relatorio_unificacao_pacientes_<data-hora>.csv na pasta atual).",
        )

    def handle(self, *args, **options):
        confirmar = options["confirmar"]
        caminho = options["relatorio"] or f"relatorio_unificacao_pacientes_{timezone.localtime():%Y%m%d-%H%M%S}.csv"

        grupos = grupos_certos()
        todos_ids = [pk for ids in grupos for pk in ids]
        fichas = Paciente.objects.in_bulk(todos_ids)
        historico = contar_historico(todos_ids)

        linhas = []
        totais = {"grupos": 0, "copias": 0, "movidos": 0, "pulados": 0}
        try:
            with transaction.atomic():
                for numero, ids in enumerate(grupos, start=1):
                    grupo = [fichas[pk] for pk in ids]
                    principal = sugerir_principal(grupo, historico)
                    copias = [f for f in grupo if f.pk != principal.pk]
                    situacao, movidos = "unificado", 0
                    if len(cpfs_conflitantes(grupo)) > 1:
                        situacao = "NÃO unificado: CPFs diferentes — conferir na tela Possíveis duplicados"
                        totais["pulados"] += 1
                    else:
                        try:
                            movidos = unificar(principal, copias, origem="comando unificar_pacientes_duplicados")
                        except UnificacaoInvalida as erro:
                            situacao = f"NÃO unificado: {erro}"
                            totais["pulados"] += 1
                        else:
                            totais["grupos"] += 1
                            totais["copias"] += len(copias)
                            totais["movidos"] += movidos
                    linhas.append(self._linha(numero, principal, copias, historico, movidos, situacao))
                if not confirmar:
                    raise _Simulacao
        except _Simulacao:
            pass

        self._gravar_relatorio(caminho, linhas)
        self._mostrar_resumo(totais, caminho, confirmar)

    @staticmethod
    def _linha(numero, principal, copias, historico, movidos, situacao):
        def descrever(ficha):
            h = historico.get(ficha.pk, {})
            return (
                f"#{ficha.pk} {ficha.nome_completo} (tel {ficha.telefone or '—'}, CPF {ficha.cpf or '—'}, "
                f"{h.get('agendamentos', 0)} atend., {h.get('prontuario', 0)} pront.)"
            )

        return {
            "grupo": numero,
            "nascimento": f"{principal.data_nascimento:%d/%m/%Y}",
            "ficha_principal": descrever(principal),
            "copias_unificadas": " | ".join(descrever(c) for c in copias),
            "registros_movidos": movidos,
            "situacao": situacao,
        }

    @staticmethod
    def _gravar_relatorio(caminho, linhas):
        campos = ["grupo", "nascimento", "ficha_principal", "copias_unificadas", "registros_movidos", "situacao"]
        # utf-8-sig + ';' → abre direto no Excel em português com acentos certos.
        with open(caminho, "w", newline="", encoding="utf-8-sig") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=campos, delimiter=";")
            escritor.writeheader()
            escritor.writerows(linhas)

    def _mostrar_resumo(self, totais, caminho, confirmar):
        modo = "GRAVADO" if confirmar else "SIMULAÇÃO (nada foi gravado — use --confirmar para gravar)"
        self.stdout.write(self.style.MIGRATE_HEADING(f"\nUnificação de pacientes duplicados — {modo}"))
        self.stdout.write(f"Grupos {'unificados' if confirmar else 'a unificar'} (mesmo nome + nascimento): {totais['grupos']}")
        self.stdout.write(f"Fichas duplicadas {'juntadas' if confirmar else 'a juntar'} na principal: {totais['copias']}")
        self.stdout.write(
            f"Registros de histórico {'movidos' if confirmar else 'a mover'} (agendamentos, prontuário, documentos, "
            f"pacotes, guias): {totais['movidos']}"
        )
        if totais["pulados"]:
            self.stdout.write(self.style.WARNING(f"Grupos deixados de fora pra conferir (ver relatório): {totais['pulados']}"))
        self.stdout.write(
            "Nome igual com nascimento diferente ou faltando: conferir em Cadastros > Pacientes > Possíveis duplicados."
        )
        self.stdout.write(f"Relatório detalhado: {caminho}")
