"""
Importa as entradas de prontuário do banco PostgreSQL do sistema ANTIGO da
clínica para cá (prontuario.EntradaProntuario) — ver conexão "legado" em
config/settings.py.

SCHEMA DE ORIGEM (mapeado em 2026-07-30 a partir do backup de produção):
    item_prontuario     — uma entrada por atendimento: queixa_principal,
                           historico_atual, exame_clinico, impressao_diagnostica,
                           conduta_terapeutica, evolucao, atendimento (data real
                           do atendimento), prontuario_id, profissional_id
                           (aponta pra papel.id, não participante.id).
    prontuario          — cabeçalho por paciente: cliente_id (aponta pra
                           papel.id — mesmo ID usado como
                           Paciente.origem_legado_id, ver
                           importar_pacientes_legado.py), antecedentes_familiares,
                           antecedentes_pessoais.
    papel/participante  — profissional é resolvido por papel.registro_conselho
                           (é o CRM) e, se não achar, por participante.nome.

Os campos temáticos batem quase 1 a 1 com EntradaProntuario:
    queixa_principal       <- queixa_principal
    historico               <- historico_atual
    avaliacao                <- exame_clinico
    hipotese_diagnostica     <- impressao_diagnostica
    conduta                  <- conduta_terapeutica
    observacoes_adicionais   <- evolucao

Decisão do cliente (2026-07-30): antecedentes_familiares/antecedentes_pessoais
(por paciente, sem equivalente por visita aqui) viram UMA entrada extra por
paciente, com esse texto no campo "historico". Como esse registro sintético
não tem um profissional "dono" de verdade na origem, usa o profissional da
entrada mais antiga do mesmo prontuário (ver profissional_por_prontuario
abaixo), ou o proprietário da clínica (Profissional.eh_proprietario) se
aquele prontuário não tiver nenhuma entrada resolvível.

Decisão do cliente (2026-09-24): o prontuário antigo entra SOMENTE PARA CONSULTA —
ninguém edita (ver EntradaProntuario.somente_leitura), e não é preciso que o
profissional do sistema antigo esteja cadastrado aqui. Se ele existir (por registro
ou nome), a entrada é ligada a ele; senão fica sem vínculo, com o nome antigo em
profissional_legado_nome. O sistema antigo não guarda especialidade por profissional;
ela é deduzida do conselho (CRM -> Medicina...) e, quando há, do CBOS — ver
especialidade_legado. Sem nenhum dos dois, fica "Não informada".

PRÉ-REQUISITO: rode antes `python manage.py importar_pacientes_legado
--confirmar` — cada entrada só é importada se o paciente dela já existir
aqui. Quem não achar o paciente é contado à parte no resumo, não interrompe
o restante da importação.

ANTES DE USAR:
    1. Tenha DB_LEGADO_* configurado no .env e importar_pacientes_legado já
       rodado com --confirmar.
    2. Rode SEM --confirmar primeiro (modo simulação, é o padrão) e confira
       o resumo. Só use --confirmar quando o resumo estiver correto.

Sobre a data: cada entrada importada preserva a data ORIGINAL do atendimento
em "criado_em" (em vez da data de hoje) — sem isso, todo o histórico clínico
migrado apareceria como se tivesse acontecido no dia da importação.

Reexecutável: cada entrada é casada pelo ID de origem (ver
EntradaProntuario.origem_legado_id), então rodar de novo não duplica ninguém.

Uso:
    python manage.py importar_prontuario_legado               # simulação (não grava nada)
    python manage.py importar_prontuario_legado --confirmar    # grava de verdade
"""
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone

from cadastros.models import Paciente, Profissional
from prontuario.models import EntradaProntuario

LIMITE_DETALHE = 20

ESPECIALIDADE_POR_CONSELHO = {
    "CRM": "Medicina",
    "CREFITO": "Fisioterapia",
    "CRFA": "Fonoaudiologia",
    "CRN": "Nutrição",
    "CRP": "Psicologia",
    "CRO": "Odontologia",
    "COREN": "Enfermagem",
    "CRF": "Farmácia",
    "CRAS": "Assistência Social",
}
ESPECIALIDADE_POR_CBOS = {
    "225125": "Clínica médica",
    "225270": "Otorrinolaringologia",
}


def especialidade_legado(conselho_sigla, cbos):
    cbos = (cbos or "").strip()
    if cbos in ESPECIALIDADE_POR_CBOS:
        return ESPECIALIDADE_POR_CBOS[cbos]
    return ESPECIALIDADE_POR_CONSELHO.get((conselho_sigla or "").strip().upper(), "Não informada")


class Command(BaseCommand):
    help = "Importa entradas de prontuário do banco PostgreSQL do sistema antigo (ver DB_LEGADO_* no .env)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirmar",
            action="store_true",
            help="Grava de verdade. Sem esta flag, só simula e mostra o resumo (padrão, seguro).",
        )

    def handle(self, *args, **options):
        conexao = self._validar_conexao()
        confirmar = options["confirmar"]

        if not Paciente.objects.filter(origem_legado_id__isnull=False).exists():
            raise CommandError(
                "Nenhum paciente com origem_legado_id encontrado — rode "
                "`python manage.py importar_pacientes_legado --confirmar` antes deste comando."
            )

        contadores = {"criados": 0, "atualizados": 0, "sem_profissional_local": 0}
        pacientes_nao_encontrados = []
        erros = []
        # Profissional resolvido pra cada prontuario_id, preenchido durante o
        # processamento dos atendimentos (_ler_atendimentos já traz ordenado
        # por data, então o primeiro valor gravado por prontuario_id é
        # sempre o do atendimento mais antigo) — reaproveitado depois pra
        # decidir o "dono" da entrada sintética de antecedentes do mesmo
        # prontuário.
        profissional_por_prontuario = {}

        atendimentos = self._ler_atendimentos(conexao)
        for registro in atendimentos:
            self._processar_atendimento(
                registro, confirmar, contadores, pacientes_nao_encontrados, erros, profissional_por_prontuario,
            )

        antecedentes = self._ler_antecedentes(conexao)
        for registro in antecedentes:
            self._processar_antecedentes(
                registro, confirmar, contadores, pacientes_nao_encontrados, erros, profissional_por_prontuario,
            )

        self._mostrar_resumo(
            len(atendimentos) + len(antecedentes), contadores, pacientes_nao_encontrados, erros, confirmar,
        )

    def _validar_conexao(self):
        if "legado" not in connections.databases:
            raise CommandError(
                "Conexão 'legado' não configurada — preencha DB_LEGADO_NAME (e DB_LEGADO_HOST/"
                "USER/PASSWORD/PORT se precisar) no .env antes de rodar este comando."
            )
        return connections["legado"]

    def _ler_atendimentos(self, conexao):
        sql = """
            SELECT
                item_prontuario.id AS origem_id,
                prontuario.id AS prontuario_origem_id,
                prontuario.cliente_id AS paciente_origem_id,
                prof_papel.registro_conselho AS profissional_registro,
                prof_participante.nome AS profissional_nome,
                prof_papel.cbos AS profissional_cbos,
                conselho.sigla AS profissional_conselho,
                item_prontuario.atendimento AS data_atendimento,
                item_prontuario.queixa_principal AS queixa_principal,
                item_prontuario.historico_atual AS historico,
                item_prontuario.exame_clinico AS avaliacao,
                item_prontuario.impressao_diagnostica AS hipotese_diagnostica,
                item_prontuario.conduta_terapeutica AS conduta,
                item_prontuario.evolucao AS observacoes_adicionais
            FROM item_prontuario
            JOIN prontuario ON prontuario.id = item_prontuario.prontuario_id
            LEFT JOIN papel AS prof_papel ON prof_papel.id = item_prontuario.profissional_id
            LEFT JOIN participante AS prof_participante ON prof_participante.id = prof_papel.participante_id
            LEFT JOIN conselho_profissional AS conselho ON conselho.id = prof_papel.conselho_id
            ORDER BY item_prontuario.atendimento ASC
        """
        nomes_campos = [
            "origem_id", "prontuario_origem_id", "paciente_origem_id",
            "profissional_registro", "profissional_nome", "profissional_cbos", "profissional_conselho",
            "data_atendimento",
            "queixa_principal", "historico", "avaliacao", "hipotese_diagnostica",
            "conduta", "observacoes_adicionais",
        ]
        with conexao.cursor() as cursor:
            cursor.execute(sql)
            linhas = cursor.fetchall()
        return [dict(zip(nomes_campos, linha)) for linha in linhas]

    def _ler_antecedentes(self, conexao):
        sql = """
            SELECT
                prontuario.id AS prontuario_origem_id,
                prontuario.cliente_id AS paciente_origem_id,
                prontuario.antecedentes_familiares AS antecedentes_familiares,
                prontuario.antecedentes_pessoais AS antecedentes_pessoais,
                prontuario.date_created AS data_criacao
            FROM prontuario
            WHERE coalesce(prontuario.antecedentes_familiares, '') <> ''
               OR coalesce(prontuario.antecedentes_pessoais, '') <> ''
        """
        nomes_campos = [
            "prontuario_origem_id", "paciente_origem_id", "antecedentes_familiares",
            "antecedentes_pessoais", "data_criacao",
        ]
        with conexao.cursor() as cursor:
            cursor.execute(sql)
            linhas = cursor.fetchall()
        return [dict(zip(nomes_campos, linha)) for linha in linhas]

    def _resolver_profissional(self, registro_conselho, nome):
        registro_conselho = (registro_conselho or "").strip()
        if registro_conselho:
            encontrado = Profissional.objects.filter(registro_profissional__iexact=registro_conselho).first()
            if encontrado:
                return encontrado
            # Formato pode divergir entre os dois sistemas (ex.: "CRM 12345-SP" aqui x
            # "12345-SP" lá) — tenta como substring antes de desistir e cair pro nome.
            encontrado = Profissional.objects.filter(registro_profissional__icontains=registro_conselho).first()
            if encontrado:
                return encontrado
        nome = (nome or "").strip()
        if nome:
            return Profissional.objects.filter(nome__iexact=nome).first()
        return None

    def _resolver_entrada(self, id_origem, paciente):
        entrada = EntradaProntuario.objects.filter(origem_legado_id=id_origem).first()
        if entrada:
            return entrada, "atualizado"
        return EntradaProntuario(paciente=paciente), "novo"

    def _preservar_data_original(self, entrada, data_original):
        """criado_em/atualizado_em são auto_now_add/auto_now — Django sempre grava a
        data de HOJE nelas na hora do .save(), ignorando qualquer valor atribuído antes.
        Por isso a data original só pode ser aplicada DEPOIS, com um .update() direto
        (que não passa pelo pre_save() responsável por auto_now_add/auto_now) — sem
        isso, todo o histórico clínico migrado apareceria como se tivesse acontecido
        hoje, quebrando a ordem cronológica do prontuário do paciente."""
        if not data_original:
            return
        if hasattr(data_original, "tzinfo") and timezone.is_naive(data_original):
            data_original = timezone.make_aware(data_original)
        EntradaProntuario.objects.filter(pk=entrada.pk).update(
            criado_em=data_original,
            atualizado_em=data_original,
            data_atendimento=timezone.localdate(data_original) if hasattr(data_original, "tzinfo") else data_original,
        )

    def _processar_atendimento(self, registro, confirmar, contadores, pacientes_nao_encontrados,
                                erros, profissional_por_prontuario):
        id_origem = str(registro["origem_id"])
        prontuario_origem_id = str(registro["prontuario_origem_id"])
        paciente_origem_id = str(registro["paciente_origem_id"])

        paciente = Paciente.objects.filter(origem_legado_id=paciente_origem_id).first()
        if not paciente:
            pacientes_nao_encontrados.append(id_origem)
            return
        if paciente.unificado_em_id:
            # Cadastro duplicado já unificado (ver cadastros unificar_pacientes_duplicados):
            # o registro vai pra ficha principal, senão o histórico voltaria a se espalhar.
            paciente = paciente.unificado_em

        profissional = self._resolver_profissional(registro.get("profissional_registro"), registro.get("profissional_nome"))
        nome_legado = (registro.get("profissional_nome") or "").strip()
        if profissional:
            especialidade = profissional.especialidade_exibicao
        else:
            especialidade = especialidade_legado(registro.get("profissional_conselho"), registro.get("profissional_cbos"))
            contadores["sem_profissional_local"] += 1

        if profissional or nome_legado:
            profissional_por_prontuario.setdefault(prontuario_origem_id, (profissional, nome_legado, especialidade))

        try:
            entrada, situacao = self._resolver_entrada(id_origem, paciente)
            entrada.paciente = paciente
            entrada.profissional = profissional
            entrada.profissional_legado_nome = nome_legado
            entrada.especialidade = especialidade
            entrada.queixa_principal = (registro.get("queixa_principal") or "").strip()
            entrada.historico = (registro.get("historico") or "").strip()
            entrada.avaliacao = (registro.get("avaliacao") or "").strip()
            entrada.hipotese_diagnostica = (registro.get("hipotese_diagnostica") or "").strip()
            entrada.conduta = (registro.get("conduta") or "").strip()
            entrada.observacoes_adicionais = (registro.get("observacoes_adicionais") or "").strip()
            entrada.origem_legado_id = id_origem
            entrada.importado_do_legado_em = timezone.now()

            entrada.full_clean()
            if confirmar:
                entrada.save()
                self._preservar_data_original(entrada, registro.get("data_atendimento"))
        except ValidationError as exc:
            erros.append((id_origem, exc))
            return

        contadores["criados" if situacao == "novo" else "atualizados"] += 1

    def _processar_antecedentes(self, registro, confirmar, contadores, pacientes_nao_encontrados,
                                 erros, profissional_por_prontuario):
        prontuario_origem_id = str(registro["prontuario_origem_id"])
        id_origem = f"antecedentes-{prontuario_origem_id}"
        paciente_origem_id = str(registro["paciente_origem_id"])

        paciente = Paciente.objects.filter(origem_legado_id=paciente_origem_id).first()
        if not paciente:
            pacientes_nao_encontrados.append(id_origem)
            return
        if paciente.unificado_em_id:
            # Cadastro duplicado já unificado (ver cadastros unificar_pacientes_duplicados):
            # o registro vai pra ficha principal, senão o histórico voltaria a se espalhar.
            paciente = paciente.unificado_em

        profissional, nome_legado, especialidade = profissional_por_prontuario.get(
            prontuario_origem_id, (None, "", "Não informada")
        )

        pessoais = (registro.get("antecedentes_pessoais") or "").strip()
        familiares = (registro.get("antecedentes_familiares") or "").strip()
        texto = "\n\n".join(filter(None, [
            f"Antecedentes pessoais: {pessoais}" if pessoais else "",
            f"Antecedentes familiares: {familiares}" if familiares else "",
        ]))

        try:
            entrada, situacao = self._resolver_entrada(id_origem, paciente)
            entrada.paciente = paciente
            entrada.profissional = profissional
            entrada.profissional_legado_nome = nome_legado
            entrada.especialidade = especialidade
            entrada.historico = texto
            entrada.origem_legado_id = id_origem
            entrada.importado_do_legado_em = timezone.now()

            entrada.full_clean()
            if confirmar:
                entrada.save()
                self._preservar_data_original(entrada, registro.get("data_criacao"))
        except ValidationError as exc:
            erros.append((id_origem, exc))
            return

        contadores["criados" if situacao == "novo" else "atualizados"] += 1

    def _mostrar_resumo(self, total, contadores, pacientes_nao_encontrados, erros, confirmar):
        modo = "GRAVANDO" if confirmar else "SIMULAÇÃO (nada foi gravado)"
        self.stdout.write(self.style.WARNING(f"--- {modo} ---"))
        self.stdout.write(f"Total lido do sistema antigo (atendimentos + antecedentes): {total}")
        self.stdout.write(f"Novos: {contadores['criados']}")
        self.stdout.write(f"Já importados antes (atualizados): {contadores['atualizados']}")

        if pacientes_nao_encontrados:
            self.stdout.write(
                self.style.ERROR(f"Paciente não encontrado aqui (rode importar_pacientes_legado antes): "
                                  f"{len(pacientes_nao_encontrados)}")
            )
            self._detalhar_ids(pacientes_nao_encontrados)

        self.stdout.write(
            f"Entradas cujo profissional não está cadastrado aqui (entram só com o nome antigo): "
            f"{contadores['sem_profissional_local']}"
        )

        if erros:
            self.stdout.write(self.style.ERROR(f"Erros de validação: {len(erros)}"))
            for id_origem, exc in erros[:LIMITE_DETALHE]:
                self.stdout.write(self.style.ERROR(f"  id={id_origem}: {exc}"))
            if len(erros) > LIMITE_DETALHE:
                self.stdout.write(self.style.ERROR(f"  ... e mais {len(erros) - LIMITE_DETALHE}"))

        if not confirmar:
            self.stdout.write(self.style.WARNING("Rode novamente com --confirmar para gravar de verdade."))

    def _detalhar_ids(self, ids):
        for id_origem in ids[:LIMITE_DETALHE]:
            self.stdout.write(f"  id={id_origem}")
        if len(ids) > LIMITE_DETALHE:
            self.stdout.write(f"  ... e mais {len(ids) - LIMITE_DETALHE}")
