"""
Pagamentos particulares de profissional que COBRA VALOR FIXO mas que ficaram gravados com o
repasse pelo PERCENTUAL (sem PagamentoParticular.valor_repasse_fixo) — acontece quando o
pagamento foi registrado antes de marcar "Cobra valor fixo no particular" no cadastro do
profissional (ou antes de o procedimento entrar na tabela "Valores fixos" dele): o repasse fica
congelado no momento do pagamento, de propósito, e não muda sozinho depois. Caso relatado pela
clínica em 23/10/2026 (Dra. Elaine, 20/10: um pagamento com valor fixo, outro com 30%).

Uso (no servidor):
    python manage.py corrigir_repasse_fixo                 # só LISTA (não altera nada)
    python manage.py corrigir_repasse_fixo --profissional 9
    python manage.py corrigir_repasse_fixo --confirmar     # corrige os ainda não repassados

Só corrige linha que ainda não teve repasse nenhum (cartão/Pix pendente). Dinheiro (entregue na
hora) e linha já repassada, total ou parcialmente, só aparecem na lista com a diferença, pra
acerto manual. Atendimento com desconto (repasse acordado na hora) fica de fora.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from agenda.models import Agendamento, PagamentoParticular
from agenda.views import _repartir_repasse_fixo


class Command(BaseCommand):
    help = "Lista (e, com --confirmar, corrige) pagamentos de profissional com valor fixo gravados pelo percentual."

    def add_arguments(self, parser):
        parser.add_argument("--profissional", type=int, help="Só este profissional (id).")
        parser.add_argument("--confirmar", action="store_true", help="Aplica a correção (sem isto, só lista).")

    def handle(self, *args, profissional=None, confirmar=False, **options):
        linhas = (
            PagamentoParticular.objects.filter(
                agendamento__forma_atendimento=Agendamento.FormaAtendimento.PARTICULAR,
                agendamento__profissional__cobra_valor_fixo_particular=True,
                agendamento__profissional__eh_proprietario=False,
                agendamento__status__in=Agendamento.STATUS_PRODUCAO,
                valor_repasse_fixo__isnull=True,
                estornado=False,
            )
            .exclude(forma_pagamento=Agendamento.FormaPagamento.CORTESIA)
            .select_related("agendamento__paciente", "agendamento__profissional")
            .order_by("agendamento__data_hora_inicio", "pk")
        )
        if profissional:
            linhas = linhas.filter(agendamento__profissional_id=profissional)

        agendamentos = {}
        for linha in linhas:
            agendamentos.setdefault(linha.agendamento_id, linha.agendamento)

        corrigidas = manuais = puladas = 0
        with transaction.atomic():
            for agendamento in agendamentos.values():
                cabecalho = (
                    f"{agendamento.data_hora_inicio:%d/%m/%Y} — {agendamento.profissional.nome} — "
                    f"{agendamento.paciente.nome_completo} (#{agendamento.pk})"
                )
                if agendamento.desconto:
                    self.stdout.write(f"PULADO {cabecalho}: teve desconto (repasse acordado na hora) — conferir à mão.")
                    puladas += 1
                    continue
                pagamentos = list(
                    agendamento.pagamentos.filter(estornado=False).exclude(
                        forma_pagamento=Agendamento.FormaPagamento.CORTESIA
                    ).order_by("pk")
                )
                if any(p.valor_repasse_fixo is not None for p in pagamentos):
                    self.stdout.write(f"PULADO {cabecalho}: parte do pagamento já tem valor fixo — conferir à mão.")
                    puladas += 1
                    continue
                itens = list(agendamento.itens.select_related("procedimento"))
                vendas = list(agendamento.pacotes_vendidos.filter(pago_em__isnull=False))
                partes = _repartir_repasse_fixo(itens, [{"valor": p.valor} for p in pagamentos], False, vendas, None)
                if any(parte is None for parte in partes):
                    sem = [i.procedimento.nome for i in itens if i.valor_repasse_fixo_a_calcular is None]
                    self.stdout.write(
                        f"PULADO {cabecalho}: sem valor fixo na tabela do profissional para {', '.join(sem)}."
                    )
                    puladas += 1
                    continue
                for pagamento, parte in zip(pagamentos, partes):
                    atual = pagamento.valor_repasse
                    texto = (
                        f"{cabecalho} — {pagamento.get_forma_pagamento_display()} R$ {pagamento.valor}: "
                        f"repasse gravado R$ {atual}, pelo valor fixo seria R$ {parte} (diferença R$ {atual - parte})"
                    )
                    pode_corrigir = (
                        not pagamento.dinheiro_entregue_na_hora
                        and not pagamento.repasse_pago
                        and pagamento.repasse_valor_pago == Decimal("0.00")
                    )
                    if pode_corrigir:
                        if confirmar:
                            pagamento.valor_repasse_fixo = parte
                            pagamento.save(update_fields=["valor_repasse_fixo"])
                            self.stdout.write(self.style.SUCCESS(f"CORRIGIDO {texto}"))
                        else:
                            self.stdout.write(f"A CORRIGIR {texto}")
                        corrigidas += 1
                    else:
                        motivo = "dinheiro, entregue na hora" if pagamento.dinheiro_entregue_na_hora else "já repassado"
                        self.stdout.write(self.style.WARNING(f"ACERTO MANUAL ({motivo}) {texto}"))
                        manuais += 1

        if not agendamentos:
            self.stdout.write(self.style.SUCCESS("Nenhum pagamento nessa situação."))
            return
        acao = "corrigido(s)" if confirmar else "a corrigir (rode de novo com --confirmar)"
        self.stdout.write(
            f"\nResumo: {corrigidas} {acao}; {manuais} para acerto manual; {puladas} atendimento(s) pulado(s)."
        )
