from django.conf import settings
from django.db import models, transaction
from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone

from cadastros.models import ESPECIALIDADES_SAUDE, Guiche, especialidade_rotulo


class Senha(models.Model):
    """
    Senha de atendimento tirada pelo paciente no totem de autoatendimento da
    recepção (sem identificação/login). Numeração sequencial reiniciada todo
    dia (campo `data`, no fuso local — ver `emitir`), com contadores
    independentes para Normal e Prioritário: por isso o mesmo número (ex: 7)
    pode existir uma vez como "N007" e outra como "P007" no mesmo dia (ver
    `Meta.constraints`).

    `motivo_prioridade` fica em branco para senha Normal. Para Prioritário,
    guarda qual categoria o paciente tocou na segunda tela — usado só para
    estatística/auditoria interna (a Lei 10.048/2000 e a Lei 13.146/2015
    definem quem tem direito a atendimento prioritário); a senha impressa
    mostra apenas "PRIORITÁRIO", nunca o motivo específico, por privacidade
    no papel que o paciente carrega pela sala de espera.

    `nivel_especial` (abaixo) marca os casos que a clínica decidiu chamar
    antes até dos demais prioritários (idoso 80+, gestante em emergência/
    trabalho de parto) — é só uma leitura de `motivo_prioridade`, não uma
    coluna própria, para não guardar em duas colunas uma informação que já
    está em uma só. Usado pelo futuro painel de chamada.

    `especialidade`/`especialidade_outra` guardam a especialidade que o
    paciente escolheu no totem (etapa obrigatória — decisão do cliente,
    20/07/2026: o totem não deixa emitir sem escolher, então não existe senha
    "sem especialidade" a tratar à parte). Mesmas colunas e mesma lista de
    opções (ESPECIALIDADES_SAUDE) do cadastro de Profissional — só oferece,
    na hora de emitir, quem tem alguém atendendo naquele dia (ver
    cadastros.models.Profissional.especialidades_ativas_em e fila.views.
    totem_especialidade) — nunca uma lista digitada à parte.

    `procedimento_exame` fica em branco pra senha normal de especialidade — só é
    preenchido quando a senha é de um EXAME AVULSO (decisão da clínica, 2026-07-30: ex.
    colocação de MAPA, feita pela equipe sem o cardiologista atender no dia). Continua
    preenchendo `especialidade`/`especialidade_outra` normalmente (a fila e o padrão de
    chamada abaixo não mudam) — este campo é só a marcação A MAIS que diferencia, pro
    painel de chamada e pro painel de espera, "Exame: MAPA" de uma consulta normal de
    Cardiologia. Ver agenda.models.Agendamento.exames_avulsos_agendados_em (só entra na
    lista quando há de fato um agendamento hoje pra aquele exame) e fila.views.
    totem_especialidade/totem_emitir.

    `status` transiciona aguardando → chamada → atendida pelo painel de
    chamada (`fila.views.painel_chamada`, permissão `fila_chamar_senha`):
    `chamar_proxima` usa a mesma ordem de `proxima_para_especialidades` pra
    escolher e grava `guiche`/`chamado_por`/`chamado_em`; `concluir_atendimento`
    fecha com `atendido_em`. Os quatro campos ficam em branco até a senha ser
    chamada (nunca preenchidos na emissão, só depois). `guiche`/`chamado_por`
    não usam CASCADE: a senha (registro de atendimento ao paciente) deve
    sobreviver mesmo se o guichê for excluído (por isso PROTECT, como em
    `cadastros.AtendimentoGuiche.guiche`) ou o usuário removido depois (por
    isso SET_NULL, como em `accounts.models.LogAuditoria.usuario`).

    A ORDEM de chamada segue o padrão fixo P;P;N;P;P;N (decisão da clínica,
    2026-07-30: 2 chamadas de Prioritário pra cada 1 de Normal, nessa sequência —
    nunca esvazia todo o prioritário antes de chamar um normal) — ver
    `PADRAO_CHAMADA`/`_proximo_tipo_do_padrao`. Nível especial (`nivel_especial`
    acima) continua furando a vez dos DEMAIS prioritários dentro de um turno de
    Prioritário, mas não fura a sequência em si: se o turno é de Normal, nível
    especial espera o próximo turno de Prioritário como qualquer outro motivo —
    a ordem P;P;N se mantém sempre.
    """

    class Tipo(models.TextChoices):
        NORMAL = "N", "Normal"
        PRIORITARIO = "P", "Prioritário"
        # Pedido da clínica (2026-10-26): exame com preparo (cadastros.models.Procedimento.
        # exame_com_preparo) tem senha própria no totem e é chamado ANTES de P e N, por ordem
        # de chegada — ver _escolher_proxima. Fica fora do padrão P;P;N (_proximo_tipo_do_padrao).
        EXAME_PREPARO = "E", "Exame com preparo"

    class Motivo(models.TextChoices):
        IDOSO_60 = "idoso_60", "Idoso (60 anos ou mais)"
        IDOSO_80 = "idoso_80", "Idoso + 80"
        GESTANTE = "gestante", "Gestantes"
        GESTANTE_EMERGENCIA = "gestante_emergencia", "Gestante em emergência ou trabalho de parto"
        LACTANTE = "lactante", "Lactantes"
        DEFICIENCIA = "deficiencia", "Pessoa com deficiência ou cadeirantes"
        CRIANCA_COLO = "crianca_colo", "Acompanhada de criança de colo"
        OBESIDADE = "obesidade", "Pessoa obesa ou Pessoa com mobilidade reduzida"
        AUTISMO = "autismo", "Pessoas com transtorno do espectro autista"

    class Status(models.TextChoices):
        AGUARDANDO = "aguardando", "Aguardando"
        CHAMADA = "chamada", "Chamada"
        ATENDIDA = "atendida", "Atendida"

    # Motivos que a clínica definiu (2026-07-20) como "nível especial" —
    # chamados antes até dos demais prioritários pelo painel de chamada (chamar_proxima).
    # GESTANTE_EMERGENCIA não aparece mais como cartão no totem (removido no
    # mesmo dia, a pedido da clínica) — segue aqui só pelas senhas antigas que
    # já têm esse motivo e por essa regra de nível especial em si.
    MOTIVOS_NIVEL_ESPECIAL = {Motivo.IDOSO_80, Motivo.GESTANTE_EMERGENCIA}

    # Ordem de prioridade de chamada dentro do grupo "prioritário", definida
    # pela clínica (2026-07-20) com base na Lei nº 10.048/2000 — quem está na
    # frente da lista é chamado antes de quem está atrás, mesmo que tenha
    # chegado depois (ver chamar_proxima/_prioritario_ordenado). Mesma ordem
    # mostrada nos cartões do totem (fila.views.MOTIVOS_PRIORIDADE, que
    # itera sobre esta mesma lista pra nunca desalinhar tela x chamada real).
    # GESTANTE_EMERGENCIA não entra aqui de propósito: já é nível especial
    # (chamada antes de chegar nesta etapa, ver MOTIVOS_NIVEL_ESPECIAL acima).
    ORDEM_PRIORIDADE = [
        Motivo.IDOSO_80,
        Motivo.DEFICIENCIA,
        Motivo.AUTISMO,
        Motivo.IDOSO_60,
        Motivo.GESTANTE,
        Motivo.LACTANTE,
        Motivo.CRIANCA_COLO,
        Motivo.OBESIDADE,
    ]

    # Padrão fixo de chamada, definido pela clínica (2026-07-30): 2 chamadas de
    # Prioritário pra cada 1 de Normal, sempre nesta sequência — nunca esvazia todo o
    # prioritário antes de chamar um normal (o que podia deixar quem tirou senha Normal
    # esperando o dia inteiro em dias de muito prioritário). A posição no ciclo é
    # recalculada a cada chamada a partir de quantas senhas já foram chamadas hoje (ver
    # _proximo_tipo_do_padrao) — não guarda um contador à parte, pelo mesmo motivo de
    # `nivel_especial` acima não ser uma coluna própria: evita o contador desalinhar do
    # que de fato aconteceu. Nível especial (idoso 80+/gestante emergência) continua
    # furando a vez dos DEMAIS prioritários dentro de um turno de Prioritário, mas não
    # fura a sequência em si — ver _proximo_prioritario/_proximo_tipo_do_padrao.
    PADRAO_CHAMADA = [Tipo.PRIORITARIO, Tipo.PRIORITARIO, Tipo.NORMAL]

    tipo = models.CharField("tipo", max_length=1, choices=Tipo.choices)
    motivo_prioridade = models.CharField(
        "motivo da prioridade", max_length=30, choices=Motivo.choices, blank=True
    )
    # default="" explícito (em vez de confiar só em blank=True) porque a tabela já
    # tem senhas emitidas antes dessas colunas existirem — sem isso, o Django pediria
    # (de forma interativa) um valor padrão pontual pras linhas já existentes.
    especialidade = models.CharField(
        "especialidade", max_length=50, choices=ESPECIALIDADES_SAUDE, blank=True, default=""
    )
    especialidade_outra = models.CharField(
        "qual especialidade (se 'Outra')", max_length=100, blank=True, default=""
    )
    # Preenchido só quando esta senha é de um exame avulso (ver docstring da classe
    # acima) — PROTECT (não SET_NULL) porque a senha é registro de atendimento e não
    # pode ficar orfanada silenciosamente se o procedimento for excluído; mesmo motivo de
    # agenda.models.AgendamentoItem.procedimento (Procedimento tem 'ativo' pra
    # soft-delete, ver cadastros.views.procedimento_excluir).
    procedimento_exame = models.ForeignKey(
        "cadastros.Procedimento",
        verbose_name="exame avulso (quando não é uma especialidade normal)",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="senhas_exame",
    )
    # Pedido da clínica (2026-10-16): numa especialidade com 2+ profissionais atendendo no
    # dia, o paciente escolhe no totem com qual veio — a recepção se divide por profissional
    # (ver cadastros.models.AtendimentoProfissional). Em branco = especialidade com um só
    # profissional, exame avulso, ou "Não sei / qualquer um" (qualquer atendente chama).
    # PROTECT pelo mesmo motivo de procedimento_exame (registro de atendimento).
    profissional = models.ForeignKey(
        "cadastros.Profissional",
        verbose_name="profissional escolhido no totem",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="senhas",
    )
    numero = models.PositiveIntegerField("número")
    codigo = models.CharField("código", max_length=10, editable=False)
    data = models.DateField("data de referência")
    status = models.CharField(
        "status", max_length=12, choices=Status.choices, default=Status.AGUARDANDO
    )
    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    # Preenchidos só a partir da chamada (ver chamar_proxima/concluir_atendimento,
    # painel de chamada) — em branco pra toda senha ainda AGUARDANDO.
    guiche = models.ForeignKey(
        Guiche, verbose_name="guichê", on_delete=models.PROTECT, null=True, blank=True, related_name="senhas_chamadas"
    )
    # SET_NULL (não CASCADE) pra manter a senha mesmo se o usuário que chamou for
    # removido depois — mesmo padrão de accounts.models.LogAuditoria.usuario.
    chamado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="chamado por",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="senhas_chamadas",
    )
    chamado_em = models.DateTimeField("chamado em", null=True, blank=True)
    atendido_em = models.DateTimeField("atendido em", null=True, blank=True)

    class Meta:
        verbose_name = "Senha"
        verbose_name_plural = "Senhas"
        ordering = ["-criado_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["data", "tipo", "numero"], name="fila_senha_unica_por_dia_tipo_numero"
            ),
        ]

    def __str__(self):
        return self.codigo

    @property
    def nivel_especial(self):
        return self.motivo_prioridade in self.MOTIVOS_NIVEL_ESPECIAL

    @property
    def especialidade_exibicao(self):
        return especialidade_rotulo(self.especialidade, self.especialidade_outra)

    @property
    def rotulo_atendimento(self):
        """
        Do que se trata esta senha, pronto pra mostrar na tela — o exame avulso
        ("Exame: MAPA") quando `procedimento_exame` estiver preenchido, senão a
        especialidade normal (`especialidade_exibicao`). Centraliza aqui em vez de
        repetir o mesmo if/else em totem_senha.html, painel_chamada.html e
        painel_espera.html.
        """
        if self.procedimento_exame_id:
            return f"Exame: {self.procedimento_exame.nome}"
        if self.profissional_id:
            return f"{self.especialidade_exibicao} — {self.profissional.nome}"
        return self.especialidade_exibicao

    @classmethod
    def emitir(
        cls, tipo, motivo_prioridade="", especialidade="", especialidade_outra="", procedimento_exame=None, profissional=None
    ):
        """
        Cria a próxima senha do tipo informado para o dia de hoje (fuso
        local, America/Sao_Paulo). `select_for_update` dentro de uma
        transação evita que dois toques quase simultâneos no totem gerem o
        mesmo número (o totem normalmente é usado por uma pessoa de cada
        vez, mas nada impede um duplo toque na tela).
        """
        if tipo != cls.Tipo.PRIORITARIO:
            motivo_prioridade = ""
        if especialidade != "outra":
            especialidade_outra = ""

        hoje = timezone.localdate()
        with transaction.atomic():
            ultima = (
                cls.objects.select_for_update()
                .filter(data=hoje, tipo=tipo)
                .order_by("-numero")
                .first()
            )
            numero = (ultima.numero + 1) if ultima else 1
            return cls.objects.create(
                tipo=tipo,
                motivo_prioridade=motivo_prioridade,
                especialidade=especialidade,
                especialidade_outra=especialidade_outra,
                procedimento_exame=procedimento_exame,
                profissional=profissional,
                numero=numero,
                codigo=f"{tipo}{numero:03d}",
                data=hoje,
            )

    @classmethod
    def _filtro_especialidades(cls, especialidades):
        """Q() que casa qualquer uma das (especialidade, especialidade_outra) informadas
        — compartilhado por _fila_aguardando e _proximo_tipo_do_padrao, pra não duplicar
        essa montagem de filtro em mais de um lugar.

        Cada item pode trazer um 3º valor (ver cadastros.models.AtendimentoProfissional.
        cobertura_em): conjunto de ids de profissionais — aí a especialidade só casa com as
        senhas desses profissionais e as sem profissional ("Não sei"). None/ausente = todas."""
        filtro = models.Q()
        for especialidade, especialidade_outra, *resto in especialidades:
            condicao = models.Q(especialidade=especialidade, especialidade_outra=especialidade_outra)
            profissionais_ids = resto[0] if resto else None
            if profissionais_ids:
                condicao &= models.Q(profissional__isnull=True) | models.Q(profissional_id__in=profissionais_ids)
            filtro |= condicao
        return filtro

    @classmethod
    def _fila_aguardando(cls, especialidades, data):
        """Queryset base (AGUARDANDO, na data, dentro das especialidades informadas)
        compartilhado por proxima_para_especialidades e chamar_proxima — pra não
        duplicar o mesmo filtro em dois lugares."""
        return cls.objects.filter(data=data, status=cls.Status.AGUARDANDO).filter(
            cls._filtro_especialidades(especialidades)
        )

    @classmethod
    def _prioritario_ordenado(cls, fila):
        """Fila (tipo=PRIORITARIO) ordenada pela posição em ORDEM_PRIORIDADE —
        Lei nº 10.048/2000 — e, dentro da mesma posição, por ordem de chegada
        (`criado_em`). Motivo fora de ORDEM_PRIORIDADE (não deveria acontecer,
        já que GESTANTE_EMERGENCIA é nível especial e nunca chega aqui) cai no
        fim, nunca quebra a consulta."""
        posicoes = {motivo: posicao for posicao, motivo in enumerate(cls.ORDEM_PRIORIDADE)}
        ordem_motivo = Case(
            *(When(motivo_prioridade=motivo, then=Value(posicao)) for motivo, posicao in posicoes.items()),
            default=Value(len(posicoes)),
            output_field=IntegerField(),
        )
        return (
            fila.filter(tipo=cls.Tipo.PRIORITARIO)
            .annotate(_ordem_motivo=ordem_motivo)
            .order_by("_ordem_motivo", "criado_em")
        )

    @classmethod
    def _proximo_prioritario(cls, fila):
        """Próxima senha do grupo Prioritário dentro de `fila`: nível especial primeiro
        (ver MOTIVOS_NIVEL_ESPECIAL — furam a vez dos DEMAIS prioritários, mas não a
        sequência do padrão de chamada, ver _proximo_tipo_do_padrao), senão o restante do
        prioritário por ORDEM_PRIORIDADE (ver _prioritario_ordenado). None se não houver
        nenhuma senha prioritária aguardando."""
        nivel_especial = fila.filter(motivo_prioridade__in=cls.MOTIVOS_NIVEL_ESPECIAL).order_by("criado_em").first()
        return nivel_especial or cls._prioritario_ordenado(fila).first()

    @classmethod
    def _proximo_normal(cls, fila):
        """Próxima senha Normal dentro de `fila`, por ordem de chegada (`criado_em`, não
        `numero`, que só é sequencial DENTRO do tipo Normal, não entre Normal e
        Prioritário). None se não houver nenhuma aguardando."""
        return fila.filter(tipo=cls.Tipo.NORMAL).order_by("criado_em").first()

    @classmethod
    def _proximo_tipo_do_padrao(cls, especialidades, data):
        """
        Decide se a PRÓXIMA chamada deve tentar o grupo Prioritário ou o Normal primeiro,
        seguindo o padrão fixo PADRAO_CHAMADA (P;P;N;P;P;N — 2 prioritário pra cada 1
        normal, decisão da clínica de 2026-07-30). A posição no ciclo é derivada de
        quantas senhas já foram CHAMADAS hoje dentro destas mesmas especialidades (não
        guarda um contador à parte — mesmo espírito de `nivel_especial` não ser uma
        coluna própria: contar de novo a cada chamada garante que o padrão nunca
        desalinha, mesmo que uma chamada seja investigada/corrigida manualmente depois).

        Correção (pedido da clínica, 23/10/2026 — saíam 3 P pra 1 N): antes a posição no
        ciclo era o NÚMERO de chamadas do dia, e quando a vez era de Normal mas não havia
        nenhum Normal esperando, o Prioritário chamado no lugar "gastava" a vez do Normal —
        o ciclo desalinhava e saíam 3+ P seguidos. Agora olha o TIPO das últimas senhas de
        fato chamadas: com 2 Prioritários seguidos desde o último Normal, a vez é do Normal.
        """
        prioritarios_por_normal = cls.PADRAO_CHAMADA.count(cls.Tipo.PRIORITARIO)
        ultimos_tipos = (
            cls.objects.filter(data=data, chamado_em__isnull=False)
            .filter(cls._filtro_especialidades(especialidades))
            .exclude(tipo=cls.Tipo.EXAME_PREPARO)
            .order_by("-chamado_em", "-pk")
            .values_list("tipo", flat=True)[:prioritarios_por_normal]
        )
        prioritarios_seguidos = 0
        for tipo in ultimos_tipos:
            if tipo != cls.Tipo.PRIORITARIO:
                break
            prioritarios_seguidos += 1
        if prioritarios_seguidos >= prioritarios_por_normal:
            return cls.Tipo.NORMAL
        return cls.Tipo.PRIORITARIO

    @classmethod
    def _escolher_proxima(cls, fila, tipo_da_vez):
        """Senha a chamar dada a vez do padrão. Na vez de Normal, a Normal só passa se chegou
        ANTES de alguma Prioritária que ainda espera (pedido da clínica, 23/10/2026: quem já
        estava na clínica com senha P é chamado antes de um Normal que chegou depois) — o
        2 P pra 1 N vale entre quem chegou depois da Normal, pra ela não ficar esquecida.

        Senha de exame com preparo (pedido da clínica, 2026-10-26) passa na frente de tudo,
        por ordem de chegada — o paciente precisa começar o preparo logo."""
        exame_preparo = fila.filter(tipo=cls.Tipo.EXAME_PREPARO).order_by("criado_em").first()
        if exame_preparo:
            return exame_preparo
        prioritario = cls._proximo_prioritario(fila)
        normal = cls._proximo_normal(fila)
        if tipo_da_vez == cls.Tipo.PRIORITARIO:
            return prioritario or normal
        if normal is None:
            return prioritario
        # Na vez do N, o P que passa é o MAIS ANTIGO, não o primeiro da ordem da lei (correção
        # de 09/11/2026): senão uma gestante das 08:12 era sempre ultrapassada por idosos 60+
        # que chegavam depois e, como ela "chegou antes" de todos os N, nenhum N saía mais.
        prioritario_mais_antigo = fila.filter(tipo=cls.Tipo.PRIORITARIO).order_by("criado_em").first()
        if prioritario_mais_antigo and prioritario_mais_antigo.criado_em < normal.criado_em:
            return prioritario_mais_antigo
        return normal

    @classmethod
    def _escolher_entre_especialidades(cls, fila, especialidades, data):
        """Ciclo P;P;N SEPARADO por especialidade (pedido da clínica, 09/11/2026): a atendente
        cobrindo Cardiologia + Fisioterapia só chamava P de Cardio, porque a vez do N era uma
        só pro guichê e ia pro N mais antigo — que era sempre da Fisio. Agora cada especialidade
        coberta escolhe a sua candidata com o próprio ciclo (_proximo_tipo_do_padrao só com ela)
        e, entre as candidatas, sai a de nível especial primeiro, senão a que chegou antes.

        Exame com preparo continua na frente de tudo, de qualquer especialidade coberta."""
        exame_preparo = fila.filter(tipo=cls.Tipo.EXAME_PREPARO).order_by("criado_em").first()
        if exame_preparo:
            return exame_preparo
        candidatas = []
        for item in especialidades:
            fila_da_especialidade = fila.filter(cls._filtro_especialidades([item]))
            senha = cls._escolher_proxima(fila_da_especialidade, cls._proximo_tipo_do_padrao([item], data))
            if senha:
                candidatas.append(senha)
        if not candidatas:
            return None
        return min(
            candidatas,
            key=lambda s: (s.motivo_prioridade not in cls.MOTIVOS_NIVEL_ESPECIAL, s.criado_em, s.pk),
        )

    @classmethod
    def proxima_para_especialidades(cls, especialidades, data=None):
        """
        Próxima senha a chamar, considerando só as especialidades cobertas pela
        atendente no momento (`especialidades`: lista de (especialidade,
        especialidade_outra), tipicamente vinda de cadastros.models.
        AtendimentoEspecialidade.especialidades_em) — só olha senhas AGUARDANDO, e segue
        o padrão de chamada P;P;N;P;P;N (ver _proximo_tipo_do_padrao/PADRAO_CHAMADA): na
        vez de Prioritário, nível especial primeiro, depois o restante por
        ORDEM_PRIORIDADE (ver _proximo_prioritario); na vez de Normal, por ordem de
        chegada (ver _proximo_normal). Quando o grupo da vez está vazio, cai pro outro
        grupo — o padrão nunca trava a fila esperando um tipo que não tem ninguém.

        Devolve None se a atendente não estiver cobrindo nenhuma especialidade
        (lista vazia) ou se não houver nenhuma senha aguardando dentro do que
        ela cobre. Só a consulta (não muda status) — quem realmente chama e
        grava é `chamar_proxima`, abaixo, usada pelo painel de chamada.
        """
        if not especialidades:
            return None

        data = data or timezone.localdate()
        fila = cls._fila_aguardando(especialidades, data)
        return cls._escolher_entre_especialidades(fila, especialidades, data)

    @classmethod
    def chamar_proxima(cls, usuario, guiche, especialidades, data=None):
        """
        Efetivamente chama a próxima senha: mesma ordem de proxima_para_especialidades
        (padrão P;P;N;P;P;N, ver _proximo_tipo_do_padrao), mas dentro de uma transação
        com select_for_update() na fila — mesmo cuidado de concorrência já usado em
        `emitir` (evita duas atendentes de guichês diferentes chamarem a mesma senha se
        apertarem "Chamar próxima" ao mesmo tempo).

        Devolve a Senha chamada (já com status=CHAMADA, guiche/chamado_por/
        chamado_em gravados) ou None se a fila estiver vazia.
        """
        if not especialidades:
            return None

        data = data or timezone.localdate()
        with transaction.atomic():
            fila = cls._fila_aguardando(especialidades, data).select_for_update()
            senha = cls._escolher_entre_especialidades(fila, especialidades, data)
            if not senha:
                return None

            senha.status = cls.Status.CHAMADA
            senha.guiche = guiche
            senha.chamado_por = usuario
            senha.chamado_em = timezone.now()
            senha.save(update_fields=["status", "guiche", "chamado_por", "chamado_em"])
            return senha

    @classmethod
    def chamar_especifica(cls, usuario, guiche, senha_id, data=None):
        """
        Chamada MANUAL (pedido da clínica, 10/11/2026; permissão `fila_chamar_manual`): chama
        a senha escolhida na lista "Fila de espera", fora da ordem automática de chamar_proxima.
        Mesmo select_for_update de chamar_proxima, pra duas atendentes não chamarem a mesma
        senha; só vale pra senha AGUARDANDO de hoje. Entra no histórico como qualquer chamada
        (conta no ciclo P;P;N de _proximo_tipo_do_padrao). Devolve a Senha ou None.
        """
        data = data or timezone.localdate()
        with transaction.atomic():
            senha = (
                cls.objects.select_for_update()
                .filter(pk=senha_id, data=data, status=cls.Status.AGUARDANDO)
                .first()
            )
            if not senha:
                return None
            senha.status = cls.Status.CHAMADA
            senha.guiche = guiche
            senha.chamado_por = usuario
            senha.chamado_em = timezone.now()
            senha.save(update_fields=["status", "guiche", "chamado_por", "chamado_em"])
            return senha

    def concluir_atendimento(self):
        """Fecha o atendimento desta senha (chamada → atendida)."""
        self.status = self.Status.ATENDIDA
        self.atendido_em = timezone.now()
        self.save(update_fields=["status", "atendido_em"])

    def chamar_novamente(self):
        """Atualiza chamado_em pra agora (mesmo guichê/status) — não muda nada
        além do horário, só serve pra senha voltar a aparecer em destaque no
        painel público de espera (painel_espera) pra quem não ouviu a primeira chamada."""
        self.chamado_em = timezone.now()
        self.save(update_fields=["chamado_em"])
