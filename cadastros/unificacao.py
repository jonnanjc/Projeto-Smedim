"""
Unificação de cadastros de paciente duplicados — pedido da clínica (13/10/2026, ampliado em
06/11/2026). Usada pelo comando `unificar_pacientes_duplicados` (lote, só os casos de certeza)
e pela tela Cadastros → Pacientes → Possíveis duplicados (casos que alguém precisa conferir).

Unificar = juntar várias fichas da MESMA pessoa numa ficha principal:
  1. TODO o histórico das cópias passa pra principal — agendamentos (e com eles a produção, os
     pagamentos e os repasses, que pertencem ao agendamento), prontuário, documentos, pacotes
     e guias de convênio. A lista de tabelas é lida do próprio Django (toda chave estrangeira
     que aponta pra Paciente), então uma tabela nova que apontar pra paciente no futuro entra
     sozinha, sem ninguém lembrar de mexer aqui.
  2. Dados vazios na principal são completados com os da cópia (CPF, RG, nascimento, telefone,
     convênio...). O que já está preenchido na principal nunca é sobrescrito.
  3. A cópia NÃO é apagada: fica com `unificado_em` = principal, escondida da recepção e do
     prontuário (link antigo pro prontuário dela redireciona pra principal). Assim a
     reimportação do sistema antigo reconhece o ID de origem dela e não recria a duplicata.

Pro profissional não aparece nada de "mesclado": o prontuário da principal simplesmente passa
a ter o histórico todo, como se sempre tivesse sido uma ficha só.
"""
import re
import unicodedata
from collections import defaultdict

from django.db import transaction
from django.db.models import Count, Max
from django.utils import timezone

from .models import Paciente

# Completados na principal quando estão vazios nela e preenchidos numa cópia. 'cpf' é tratado
# à parte (único no banco: precisa sair da cópia antes de entrar na principal).
CAMPOS_COMPLETAR = [
    "data_nascimento",
    "rg",
    "telefone",
    "email",
    "bairro",
    "cidade",
    "convenio_id",
    "numero_carteirinha",
    "validade_carteirinha",
    "responsavel_nome",
    "responsavel_cpf",
]

# Tipos de grupo (ver grupos_por_nome). "certo" = nome e nascimento iguais, e é o único que o
# comando em lote unifica sozinho.
TIPO_CERTO = "certo"
TIPO_SEM_NASCIMENTO = "sem_nascimento"
TIPO_NASCIMENTO_DIFERENTE = "nascimento_diferente"
TIPOS = {
    TIPO_CERTO: "Nome e nascimento iguais",
    TIPO_SEM_NASCIMENTO: "Nome igual, alguma ficha sem nascimento",
    TIPO_NASCIMENTO_DIFERENTE: "Nome igual, nascimento diferente",
}


class UnificacaoInvalida(Exception):
    """Pedido de unificação que não pode ser feito (mensagem pronta pra mostrar na tela)."""


def nome_normalizado(nome):
    """'Raíssa  da SILVA' e 'raissa da silva.' viram a mesma chave: sem acento, minúsculo,
    sem pontuação e sem espaço sobrando."""
    sem_acento = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sem_acento).split())


def telefone_valido(telefone):
    """Pelo menos 8 dígitos — o sistema antigo tem telefones como "9" ou "0"."""
    return sum(c.isdigit() for c in telefone or "") >= 8


def relacoes_do_paciente():
    """(model, nome_do_campo) de toda chave estrangeira que aponta pra Paciente, menos a
    própria ligação de unificação (tratada à parte)."""
    relacoes = []
    for relacao in Paciente._meta.related_objects:
        if relacao.related_model is Paciente or relacao.many_to_many or relacao.one_to_one:
            continue
        relacoes.append((relacao.related_model, relacao.field.name))
    return relacoes


def contar_historico(ids):
    """{paciente_id: {"total": n, "agendamentos": n, "prontuario": n, "ultimo_atendimento": data}}
    de cada ficha em `ids` — uma consulta agregada por tabela, não uma por ficha."""
    ids = list(ids)
    resultado = {pk: {"total": 0, "agendamentos": 0, "prontuario": 0, "ultimo_atendimento": None} for pk in ids}
    if not ids:
        return resultado
    for model, campo in relacoes_do_paciente():
        linhas = model._base_manager.filter(**{f"{campo}__in": ids}).values(campo).annotate(qtd=Count("pk"))
        for linha in linhas:
            resultado[linha[campo]]["total"] += linha["qtd"]
            if model._meta.label == "agenda.Agendamento":
                resultado[linha[campo]]["agendamentos"] += linha["qtd"]
            elif model._meta.label == "prontuario.EntradaProntuario":
                resultado[linha[campo]]["prontuario"] += linha["qtd"]
    from agenda.models import Agendamento

    for linha in Agendamento.objects.filter(paciente_id__in=ids).values("paciente_id").annotate(ultimo=Max("data_hora_inicio")):
        resultado[linha["paciente_id"]]["ultimo_atendimento"] = linha["ultimo"]
    return resultado


def sugerir_principal(fichas, historico):
    """A ficha que fica: a ativa; depois a cadastrada aqui pela recepção (dados mais atuais
    que os vindos do sistema antigo); depois a com mais histórico; com CPF; com mais dados
    preenchidos; a mais antiga."""

    def prioridade(ficha):
        preenchidos = sum(1 for campo in CAMPOS_COMPLETAR if getattr(ficha, campo))
        return (
            ficha.ativo,
            ficha.origem_legado_id is None,
            historico.get(ficha.pk, {}).get("total", 0),
            bool(ficha.cpf),
            preenchidos,
            -ficha.pk,
        )

    return max(fichas, key=prioridade)


def cpfs_conflitantes(fichas):
    """CPFs diferentes entre as fichas = provavelmente pessoas diferentes (ou CPF digitado
    errado numa delas) — não unifica até alguém corrigir. Compara só os números: o mesmo CPF
    com e sem pontuação ("015.203.742-08" x "01520374208") é o mesmo (visto no ensaio de
    06/11/2026)."""
    por_digitos = {}
    for ficha in fichas:
        digitos = "".join(c for c in ficha.cpf or "" if c.isdigit())
        if digitos:
            por_digitos.setdefault(digitos, ficha.cpf)
    return sorted(por_digitos.values())


@transaction.atomic
def unificar(principal, copias, usuario=None, request=None, origem="tela"):
    """Junta `copias` em `principal` (ver topo do arquivo). Devolve quantos registros de
    histórico foram movidos. Levanta UnificacaoInvalida se o pedido não fizer sentido."""
    copias = [c for c in copias if c.pk != principal.pk]
    if not copias:
        raise UnificacaoInvalida("Escolha pelo menos uma ficha para juntar na ficha principal.")
    # Relê do banco com trava: dois cliques ao mesmo tempo (ou a tela e o comando) não podem
    # unificar a mesma ficha duas vezes. of=("self",) — ver nota sobre FOR UPDATE + FK anulável.
    ids = [principal.pk] + [c.pk for c in copias]
    fichas = {p.pk: p for p in Paciente.objects.select_for_update(of=("self",)).filter(pk__in=ids)}
    if len(fichas) != len(ids):
        raise UnificacaoInvalida("Alguma das fichas não existe mais. Atualize a página.")
    principal = fichas[principal.pk]
    copias = [fichas[c.pk] for c in copias]
    if principal.unificado_em_id or any(c.unificado_em_id for c in copias):
        raise UnificacaoInvalida("Alguma das fichas já foi unificada em outra. Atualize a página.")
    cpfs = cpfs_conflitantes([principal] + copias)
    if len(cpfs) > 1:
        raise UnificacaoInvalida(
            "As fichas têm CPFs diferentes (" + ", ".join(cpfs) + "). Se for a mesma pessoa, corrija o CPF "
            "errado no cadastro (ou apague-o) e tente de novo."
        )

    ids_copias = [c.pk for c in copias]
    movidos = 0
    for model, campo in relacoes_do_paciente():
        movidos += model._base_manager.filter(**{f"{campo}__in": ids_copias}).update(**{campo: principal})
    # Cópias que já tinham sido unificadas numa das cópias de agora passam a apontar direto
    # pra principal (sem corrente cópia → cópia → principal).
    Paciente.objects.filter(unificado_em_id__in=ids_copias).update(unificado_em=principal)

    campos_alterados = set()
    # A cópia mais recente primeiro: se duas cópias têm telefone e a principal não, fica o
    # telefone do cadastro mais novo.
    for copia in sorted(copias, key=lambda c: c.atualizado_em or timezone.now(), reverse=True):
        for campo in CAMPOS_COMPLETAR:
            if campo == "telefone":
                # Telefone "lixo" do sistema antigo (ex.: "9") conta como vazio: fica o da
                # cópia, se o dela for um número de verdade (visto na conferência, 06/11/2026).
                if not telefone_valido(principal.telefone) and telefone_valido(copia.telefone):
                    principal.telefone = copia.telefone
                    campos_alterados.add(campo)
                continue
            if not getattr(principal, campo) and getattr(copia, campo):
                setattr(principal, campo, getattr(copia, campo))
                campos_alterados.add(campo)
        if not principal.cpf and copia.cpf:
            cpf = copia.cpf
            Paciente.objects.filter(pk=copia.pk).update(cpf=None)  # único no banco: libera antes
            principal.cpf = cpf
            campos_alterados.add("cpf")
        if not principal.ativo and copia.ativo:
            principal.ativo = True
            campos_alterados.add("ativo")
    if campos_alterados:
        principal.save(update_fields=sorted(campos_alterados) + ["atualizado_em"])

    # update() direto (sem full_clean/save): a cópia pode ter dado inválido herdado do sistema
    # antigo, e aqui só interessa ligá-la à principal. atualizado_em à mão porque update() não
    # aciona auto_now (a busca de pacientes usa esse campo pra saber que o cadastro mudou).
    Paciente.objects.filter(pk__in=ids_copias).update(unificado_em=principal, atualizado_em=timezone.now())

    from accounts.models import LogAuditoria

    acao = (
        f"Unificou paciente(s) {', '.join(f'#{c.pk}' for c in copias)} na ficha #{principal.pk} "
        f"({principal.nome_completo}) — {movidos} registro(s) de histórico movido(s) [{origem}]"
    )[:255]
    if request is not None:
        LogAuditoria.registrar(request, usuario, acao)
    else:
        LogAuditoria.objects.create(usuario=usuario, acao=acao)
    return movidos


def grupos_por_nome(busca=""):
    """Grupos de fichas ainda não unificadas com o mesmo nome normalizado (2+ fichas), cada um
    {"chave", "tipo", "fichas"} — o tipo diz o grau de certeza (TIPOS). Lê só as colunas
    necessárias: roda sobre o cadastro inteiro (~38 mil fichas) em fração de segundo."""
    por_nome = defaultdict(list)
    busca = nome_normalizado(busca)
    for pk, nome, nascimento in Paciente.objects.filter(unificado_em__isnull=True).values_list(
        "pk", "nome_completo", "data_nascimento"
    ):
        chave = nome_normalizado(nome)
        if chave and (not busca or all(palavra in chave for palavra in busca.split())):
            por_nome[chave].append((pk, nascimento))
    grupos = []
    for chave, itens in sorted(por_nome.items()):
        if len(itens) < 2:
            continue
        nascimentos = {nascimento for _pk, nascimento in itens}
        if None in nascimentos:
            tipo = TIPO_SEM_NASCIMENTO
        elif len(nascimentos) == 1:
            tipo = TIPO_CERTO
        else:
            tipo = TIPO_NASCIMENTO_DIFERENTE
        grupos.append({"chave": chave, "tipo": tipo, "ids": sorted(pk for pk, _n in itens)})
    return grupos


def grupos_certos():
    """Subgrupos de certeza pro comando em lote: mesmo nome normalizado E mesma data de
    nascimento (sem nascimento não entra). Dentro de um nome com nascimentos diferentes, cada
    nascimento repetido é um subgrupo — ex.: 3 "Maria Souza", 2 nascidas em 01/02/1980 e 1 em
    1995: as 2 de 1980 são unificadas, a de 1995 fica."""
    por_chave = defaultdict(list)
    for pk, nome, nascimento in Paciente.objects.filter(
        unificado_em__isnull=True, data_nascimento__isnull=False
    ).values_list("pk", "nome_completo", "data_nascimento"):
        por_chave[(nome_normalizado(nome), nascimento)].append(pk)
    return [sorted(ids) for _chave, ids in sorted(por_chave.items()) if len(ids) > 1]
