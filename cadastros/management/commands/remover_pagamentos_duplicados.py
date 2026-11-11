"""
Pagamentos particulares gravados em DOBRO pelo envio duplo da tela "Registrar e autorizar"
(caso relatado pela clínica em 26/10/2026: Pix 90 + Dinheiro 110 aparecendo duas vezes, total
R$ 400 numa consulta de R$ 200). A tela foi corrigida (agenda.views.agendamento_confirmar_pagamento
trava o atendimento); este comando limpa o que já ficou gravado.

Uso (no servidor):
    python manage.py remover_pagamentos_duplicados              # só LISTA (não altera nada)
    python manage.py remover_pagamentos_duplicados --confirmar  # apaga as cópias

Considera duplicado o atendimento cujas linhas de pagamento (não estornadas) são o MESMO conjunto
repetido (mesma forma, valor, valor recebido, parcelas e repasse fixo), gravado em até 5 minutos,
e em que UM conjunto bate com o valor cobrado (soma dos procedimentos + pacote vendido). Fica a
primeira cópia de cada linha; se o repasse de uma cópia já foi pago (total ou parcial), fica essa
no lugar. Atendimento em que mais de uma cópia teve repasse só aparece na lista, pra acerto manual.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from agenda.models import Agendamento, PagamentoParticular


def _teve_repasse(pagamento):
    return bool(pagamento.repasse_pago or pagamento.repasse_valor_pago or pagamento.alocacoes_repasse.exists())


class Command(BaseCommand):
    help = "Lista (e, com --confirmar, apaga) pagamentos particulares gravados em dobro por envio duplo."

    def add_arguments(self, parser):
        parser.add_argument("--confirmar", action="store_true", help="Apaga as cópias (sem isto, só lista).")

    def handle(self, *args, confirmar=False, **options):
        candidatos = (
            Agendamento.objects.filter(pagamentos__estornado=False)
            .annotate(linhas=Count("pagamentos"))
            .filter(linhas__gte=2)
            .select_related("paciente", "profissional")
            .order_by("data_hora_inicio", "pk")
        )
        corrigidos = manuais = apagadas = 0
        with transaction.atomic():
            for agendamento in candidatos:
                pagamentos = list(agendamento.pagamentos.filter(estornado=False).order_by("pk"))
                blocos = PagamentoParticular.copias_em_dobro(agendamento, pagamentos)
                if not blocos:
                    continue

                cabecalho = (
                    f"{agendamento.data_hora_inicio:%d/%m/%Y} — {agendamento.profissional.nome} — "
                    f"{agendamento.paciente.nome_completo} (#{agendamento.pk})"
                )
                resumo = " + ".join(f"{p.get_forma_pagamento_display()} R$ {p.valor}" for p in blocos[0])
                total = sum((p.valor for p in pagamentos), Decimal("0.00"))

                apagar = []
                problema = None
                for posicao in range(len(blocos[0])):
                    copias = [bloco[posicao] for bloco in blocos]
                    com_repasse = [p for p in copias if _teve_repasse(p)]
                    if len(com_repasse) > 1:
                        problema = "mais de uma cópia já teve repasse pago"
                        break
                    manter = com_repasse[0] if com_repasse else copias[0]
                    apagar += [p for p in copias if p.pk != manter.pk]
                if problema:
                    self.stdout.write(f"MANUAL {cabecalho}: {resumo} gravado {len(blocos)}x (R$ {total}) — {problema}.")
                    manuais += 1
                    continue

                self.stdout.write(
                    f"{'CORRIGIDO' if confirmar else 'DUPLICADO'} {cabecalho}: {resumo} gravado {len(blocos)}x "
                    f"(R$ {total} → R$ {sum((p.valor for p in blocos[0]), Decimal("0.00"))}); {len(apagar)} linha(s) a mais."
                )
                if confirmar:
                    PagamentoParticular.objects.filter(pk__in=[p.pk for p in apagar]).delete()
                apagadas += len(apagar)
                corrigidos += 1

        acao = "apagada(s)" if confirmar else "a apagar (rode com --confirmar)"
        self.stdout.write(
            self.style.SUCCESS(
                f"{corrigidos} atendimento(s) com pagamento em dobro — {apagadas} linha(s) {acao}; "
                f"{manuais} para conferir à mão."
            )
        )
