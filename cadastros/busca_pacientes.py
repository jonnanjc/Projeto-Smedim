"""
Busca de paciente por nome/CPF sem diferenciar acento nem maiúscula — usada pela caixa
"Paciente" do agendamento (agenda.views.buscar_pacientes) e pela busca do Prontuário
(prontuario.views.prontuario_lista).

O banco não compara sem acento (o SQLite nem ignora maiúscula fora do ASCII; o PostgreSQL
ignora maiúscula mas não acento), então "joao" não acharia "JOÃO" num simples icontains —
foi o que fez o profissional não achar no Prontuário paciente que estava na lista dele de
"Evoluir" (clínica, 28/10/2026). Normalizar ~37 mil nomes a cada busca seria lento; o
índice em memória (por processo) é refeito só quando o cadastro muda (a "assinatura" é uma
consulta agregada barata) ou a cada 10 minutos — um índice por público (`chave`), já que a
recepção e o profissional enxergam conjuntos diferentes de pacientes.
"""
import time
import unicodedata

from django.db.models import Count, Max, Q

from .models import Paciente

_INDICES = {}
_VALIDADE_SEGUNDOS = 600


def sem_acento(texto):
    return unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii").lower()


def _indice(chave, filtro):
    assinatura = tuple(
        Paciente.objects.aggregate(
            total=Count("pk"), ativos=Count("pk", filter=Q(ativo=True)), ultimo_id=Max("pk"), alterado=Max("atualizado_em")
        ).values()
    )
    indice = _INDICES.get(chave)
    if indice is None or indice["assinatura"] != assinatura or time.monotonic() - indice["montado_em"] > _VALIDADE_SEGUNDOS:
        itens = [
            (pk, sem_acento(nome), "".join(c for c in (cpf or "") if c.isdigit()))
            for pk, nome, cpf in Paciente.objects.filter(filtro).values_list("pk", "nome_completo", "cpf")
        ]
        itens.sort(key=lambda item: item[1])
        indice = _INDICES[chave] = {"assinatura": assinatura, "montado_em": time.monotonic(), "itens": itens}
    return indice["itens"]


def buscar_ids_pacientes(termo, filtro, chave, limite=None):
    """IDs dos pacientes de `filtro` que batem com `termo`: todas as palavras digitadas
    precisam aparecer no nome, em qualquer ordem; nomes que começam com o texto vêm
    primeiro. Só números (3+ dígitos) busca no CPF, com ou sem pontuação."""
    termo = (termo or "").strip()
    termo_normalizado = sem_acento(termo)
    palavras = termo_normalizado.split()
    digitos = "".join(c for c in termo if c.isdigit())
    so_numeros = bool(digitos) and not any(c.isalpha() for c in termo)
    if not palavras or (so_numeros and len(digitos) < 3):
        return []

    comecam, contem = [], []
    for pk, nome, cpf in _indice(chave, filtro):
        if so_numeros:
            if digitos in cpf:
                contem.append(pk)
        elif all(palavra in nome for palavra in palavras):
            (comecam if nome.startswith(termo_normalizado) else contem).append(pk)
        if limite and len(comecam) >= limite:
            break
    ids = comecam + contem
    return ids[:limite] if limite else ids
