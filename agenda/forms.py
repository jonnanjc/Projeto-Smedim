from datetime import datetime, time, timedelta
from decimal import Decimal

from django import forms
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from accounts.moeda import formatar_moeda, preparar_campos_moeda
from cadastros.models import (
    ESPECIALIDADES_SAUDE,
    Convenio,
    HorarioAtendimento,
    Pacote,
    Paciente,
    Procedimento,
    Profissional,
    especialidade_rotulo,
)

from .models import TURNO_CHOICES, Agendamento, AgendamentoItem, GuiaConvenio, PacoteVendido, turno_da_hora

DIAS_SEMANA_CHOICES = [
    ("0", "Segunda-feira"),
    ("1", "Terça-feira"),
    ("2", "Quarta-feira"),
    ("3", "Quinta-feira"),
    ("4", "Sexta-feira"),
    ("5", "Sábado"),
    ("6", "Domingo"),
]


class EstiloCampoMixin:
    """Aplica a classe CSS 'campo' (usada em accounts/base.html) a todo campo de texto/número/data/seleção."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            if isinstance(campo.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                continue
            classes = campo.widget.attrs.get("class", "")
            campo.widget.attrs["class"] = (classes + " campo").strip()
        preparar_campos_moeda(self)  # dinheiro digitado/mostrado como 150.000,00


class SelectComConvenioPaciente(forms.Select):
    """
    <select> de paciente com o nome (e o id) do convênio, se houver, anexados
    em cada <option> como atributos 'data-convenio-nome'/'data-convenio-id' —
    usado por agendamento_form.html tanto para rotular a opção "Convênio" do
    campo 'forma_atendimento' como "Convênio (nome do convênio do paciente)"
    quanto para saber qual convênio consultar ao filtrar os procedimentos com
    preço cadastrado (ver AgendamentoForm.clean() e agenda.views.
    procedimentos_do_profissional) — tudo sem precisar de outra chamada ao
    servidor só pra descobrir o convênio do paciente escolhido.
    """

    def __init__(self, *args, pacientes_convenio=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._pacientes_convenio = pacientes_convenio or {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        try:
            # 'value' vem como ModelChoiceIteratorValue (Django >= 3.1) para campos
            # ModelChoiceField, não como int puro — por isso passa por str() antes
            # de converter, senão int(value) sempre falha silenciosamente e nenhuma
            # <option> nunca ganharia os atributos 'data-convenio-*'.
            chave = int(str(value))
        except (TypeError, ValueError):
            return option
        convenio = self._pacientes_convenio.get(chave)
        if convenio:
            option["attrs"]["data-convenio-nome"] = convenio["nome"]
            option["attrs"]["data-convenio-id"] = convenio["id"]
        return option


# Duração fixa em opções pré-definidas (pedido do cliente: substitui o campo
# livre de duração/horário de término por um <select>). Quando um agendamento
# já existente tiver uma duração fora dessas opções (dado antigo), essa
# duração "extra" é inserida dinamicamente na lista de opções do formulário
# (ver __init__ abaixo) — assim nenhum agendamento antigo quebra ao ser
# editado, mesmo que sua duração não seja uma das 4 opções padrão.
DURACAO_CHOICES = [
    (15, "15 minutos"),
    (30, "30 minutos"),
    (45, "45 minutos"),
    (60, "1 hora"),
]


class AgendamentoForm(EstiloCampoMixin, forms.ModelForm):
    """
    Em vez de pedir 'data/hora de início' e 'data/hora de término' direto (o
    modelo guarda assim), a tela pede Data + Horário de início + Duração
    (select com opções fixas: 15 min, 30 min, 45 min, 1 hora) — mais próximo
    de como a recepção pensa na hora de marcar — e o fim é calculado
    automaticamente no clean().

    'forma_atendimento' virou o único campo que a recepção escolhe para
    definir se o atendimento é particular ou por convênio — "Convênio" só fica
    disponível quando o profissional escolhido atende algum convênio (ver
    JavaScript em agendamento_form.html).
    Desde 2026-10-14 (pedido do cliente), com forma_atendimento=CONVENIO a
    recepção escolhe QUAL convênio no campo 'convenio' — só os que o
    profissional atende (ver clean() abaixo); o do cadastro do paciente vem
    pré-selecionado.
    """

    # format="%Y-%m-%d" força o valor inicial a sempre sair em ISO (independente do locale
    # pt-br, que por padrão usaria "%d/%m/%Y") — <input type="date"> só reconhece ISO;
    # sem isso, o navegador rejeita o valor pré-preenchido ao editar um agendamento
    # existente (o campo aparenta estar vazio e barra o envio, mesmo com a data certa
    # gravada no banco — bug encontrado ao testar a edição de agendamentos com itens).
    data = forms.DateField(
        label="Data", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"), input_formats=["%Y-%m-%d"]
    )
    # Só SESSÃO tem hora marcada e duração (pedido da clínica, 13/10/2026 — ver
    # Agendamento.eh_sessao). Consulta/exame são agendados por TURNO e atendidos na ordem de
    # agendamento. Por isso nenhum dos três é obrigatório aqui: AgendamentoItemFormSet.clean()
    # exige horário+duração quando há sessão e turno quando não há, e o JavaScript de
    # agendamento_form.html mostra só os campos do caso.
    turno = forms.ChoiceField(label="Turno", choices=[("", "---------")] + TURNO_CHOICES, required=False)
    hora_inicio = forms.TimeField(
        label="Horário da sessão", widget=forms.TimeInput(attrs={"type": "time"}), required=False
    )
    duracao_minutos = forms.TypedChoiceField(
        label="Duração da sessão", choices=DURACAO_CHOICES, coerce=int, initial=30, required=False,
        empty_value=None,
    )

    # Fora de Meta.fields de propósito (mesmo motivo de 'convenio' abaixo): o valor
    # definitivo é resolvido em clean() a partir do profissional escolhido (automático
    # quando ele só tem 1 especialidade, escolha obrigatória quando tem 2+ — ver
    # cadastros.models.Profissional.especialidades_pares) e atribuído direto à
    # instance lá, não aqui — colocar em Meta.fields faria o _post_clean() do
    # ModelForm sobrescrever essa resolução com o valor bruto enviado.
    especialidade = forms.ChoiceField(
        label="Especialidade deste atendimento", choices=ESPECIALIDADES_SAUDE, required=False,
        help_text="Só aparece quando o profissional escolhido tem mais de uma especialidade cadastrada.",
    )
    especialidade_outra = forms.CharField(label="Qual especialidade (se 'Outra')", required=False)

    # Fora de Meta.fields pelo mesmo motivo de 'especialidade' acima: resolvido em clean().
    # Pedido do cliente (2026-10-14): o convênio é escolhido na hora de agendar, entre os que
    # o PROFISSIONAL atende (cadastros.models.Profissional.convenios_para_agendar) — não é
    # mais só o do cadastro do paciente (que continua vindo pré-selecionado quando dá).
    convenio = forms.ModelChoiceField(
        label="Convênio", queryset=Convenio.objects.none(), required=False,
        help_text="Só aparecem os convênios que o profissional atende e que têm preço para algum procedimento dele.",
    )

    # Pedido da clínica (2026-10-16, provisório): consulta autorizada no sistema antigo e o
    # retorno agora no novo. Não é campo do Agendamento: vira AgendamentoItem.retorno_manual
    # no item de consulta (ver _AgendamentoItemFormSetBase.clean()).
    retorno_sistema_antigo = forms.BooleanField(
        label="Retorno de consulta feita no sistema antigo (não cobra a consulta)", required=False,
        help_text="Use só quando a consulta original foi autorizada no sistema antigo — o retorno de "
        "consultas feitas neste sistema é reconhecido sozinho.",
    )

    # Não são campos do modelo — usados só na criação, para gerar várias
    # sessões futuras de uma vez (ex.: sessão semanal de fono/fisioterapia).
    repetir_semanalmente = forms.BooleanField(
        label="Repetir semanalmente (mesmo dia da semana e horário desta sessão)", required=False
    )
    quantidade_sessoes = forms.IntegerField(
        label="Quantidade de sessões (incluindo esta)",
        required=False,
        min_value=2,
        max_value=52,
        help_text="Cria automaticamente as próximas sessões, uma por semana, no mesmo horário.",
    )

    # Opcional: paciente que vem mais de uma vez por semana, em outro dia com
    # outro horário (ex.: terça E sexta, com horários diferentes). Gera uma
    # segunda série de sessões, na mesma quantidade de semanas escolhida acima.
    tem_segundo_dia = forms.BooleanField(
        label="Este paciente também vem em outro dia da semana (horário diferente)?", required=False
    )
    segundo_dia_semana = forms.ChoiceField(
        label="Segundo dia da semana", choices=DIAS_SEMANA_CHOICES, required=False
    )
    segundo_horario = forms.TimeField(
        label="Horário do segundo dia", widget=forms.TimeInput(attrs={"type": "time"}), required=False
    )
    segundo_duracao_minutos = forms.IntegerField(
        label="Duração do segundo dia (minutos)", required=False, min_value=5
    )

    class Meta:
        model = Agendamento
        # 'status' fica de fora do formulário de propósito: todo agendamento
        # novo sempre nasce como "Agendado"; mudanças de situação (Pré-
        # confirmado, Confirmado, Realizado, Faltou, Cancelado) só acontecem
        # pelos botões da tela de Agenda do dia / Minha agenda, nunca por
        # aqui. 'valor_cobrado'/'forma_pagamento' também ficam de fora — só
        # são preenchidos na tela de "registrar pagamento" (ver
        # ConfirmarPagamentoForm abaixo), pra não dar pra confirmar o
        # pagamento "por acidente" editando outra coisa do agendamento.
        # 'convenio' também ficou de fora — é preenchido sozinho no clean()
        # abaixo, a partir do convênio cadastrado do paciente (ver decisão
        # no topo desta classe).
        # 'procedimento' NÃO está mais aqui — um agendamento pode ter mais de um
        # procedimento agora (ver AgendamentoItem/AgendamentoItemFormSet abaixo,
        # tratado à parte na view junto com este form).
        fields = ["paciente", "profissional", "forma_atendimento", "encaixe", "observacoes"]
        widgets = {"observacoes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, data_travada=None, **kwargs):
        super().__init__(*args, **kwargs)
        pacientes_qs = Paciente.objects.filter(ativo=True).select_related("convenio").order_by("nome_completo")
        # O <select> NÃO lista mais todos os pacientes: depois da importação do sistema
        # antigo são dezenas de milhares (37 mil em 10/2026), e renderizar um <option> pra
        # cada um deixava a tela de agendamento com mais de 2 MB e travava o navegador
        # (lentidão relatada ao agendar pela Central de Atendimento em 13/10/2026). Agora
        # só vai pro <select> o paciente já escolhido (POST com erro, edição, ou vindo do
        # pop-up de cadastro); os demais são buscados por nome/CPF via agenda.views.
        # buscar_pacientes. A validação continua contra o queryset completo de ativos.
        paciente_atual = None
        valor_atual = (
            self.data.get(self.add_prefix("paciente")) if self.is_bound else self.initial.get("paciente")
        )
        if valor_atual:
            try:
                paciente_atual = pacientes_qs.filter(pk=int(str(valor_atual))).first()
            except (TypeError, ValueError):
                paciente_atual = None
        # Mapa paciente_id -> {nome, id} do convênio, usado pelo SelectComConvenioPaciente
        # para anexar 'data-convenio-nome'/'data-convenio-id' na <option> (ver JS em
        # agendamento_form.html, que troca o rótulo da opção "Convênio" do campo
        # forma_atendimento e filtra os procedimentos com preço assim que o paciente é escolhido).
        pacientes_convenio = {}
        if paciente_atual and paciente_atual.convenio_id:
            pacientes_convenio[paciente_atual.pk] = {
                "nome": paciente_atual.convenio.nome,
                "id": paciente_atual.convenio_id,
            }
        # IMPORTANTE: troca o widget ANTES de atribuir o queryset. O setter de
        # 'queryset' do ModelChoiceField sincroniza as opções escrevendo em
        # 'self.widget.choices' — se isso rodar antes da troca do widget, as
        # opções acabam gravadas no <select> padrão (que é descartado logo
        # em seguida), e o widget novo (SelectComConvenioPaciente) fica com
        # 'choices=()' vazio pra sempre, fazendo o campo de paciente aparecer
        # sem nenhuma opção na tela (bug encontrado e corrigido em 13/07/2026).
        self.fields["paciente"].widget = SelectComConvenioPaciente(pacientes_convenio=pacientes_convenio)
        self.fields["paciente"].queryset = pacientes_qs
        # Sobrescreve as opções que o setter de 'queryset' acabou de copiar pro widget
        # (iteraria todos os pacientes ao renderizar) — ver comentário no início do __init__.
        opcoes_paciente = [("", "---------")]
        if paciente_atual:
            opcoes_paciente.append((paciente_atual.pk, str(paciente_atual)))
        self.fields["paciente"].widget.choices = opcoes_paciente
        self.fields["profissional"].queryset = Profissional.objects.filter(ativo=True).order_by("nome")
        # A lista completa fica disponível no <select> (a filtragem por
        # profissional é feita visualmente por JavaScript, ver
        # agendamento_form.html); a validação de "profissional habilitado"
        # é reforçada por item em AgendamentoItemFormSet.clean() abaixo, sem
        # travar quem ainda não configurou os procedimentos habilitados de
        # cada profissional.
        self.fields["encaixe"].required = False

        instance = kwargs.get("instance")
        self._convenio_original_id = instance.convenio_id if instance and instance.pk else None
        self._profissional_original_id = instance.profissional_id if instance and instance.pk else None
        # Edição de um agendamento DO DIA (pedido da clínica, 06/11/2026): corrigir algo
        # errado com o paciente já na clínica não pode travar o atendimento pedindo a
        # justificativa do encaixe — ver clean() abaixo e
        # _AgendamentoItemFormSetBase._limitar_retornos_por_dia. Vale a data ORIGINAL do
        # agendamento (antes desta edição).
        self._encaixe_original = bool(instance and instance.pk and instance.encaixe)
        self.edicao_no_dia = bool(
            instance and instance.pk and timezone.localtime(instance.data_hora_inicio).date() == timezone.localdate()
        )
        # Convênio desativado de um agendamento antigo continua válido na edição.
        self.fields["convenio"].queryset = Convenio.objects.filter(
            Q(ativo=True) | Q(pk=self._convenio_original_id)
        ).order_by("nome")
        if instance and instance.pk and instance.convenio_id:
            self.fields["convenio"].initial = instance.convenio_id
        elif paciente_atual and paciente_atual.convenio_id:
            self.fields["convenio"].initial = paciente_atual.convenio_id
        if instance and instance.pk:
            self.fields["data"].initial = timezone.localtime(instance.data_hora_inicio).date()
            self.fields["hora_inicio"].initial = timezone.localtime(instance.data_hora_inicio).time()
            self.fields["turno"].initial = instance.turno
            duracao = int((instance.data_hora_fim - instance.data_hora_inicio).total_seconds() // 60) or 30
            # Se a duração deste agendamento (possivelmente antigo) não bate com
            # nenhuma das 4 opções fixas, adiciona ela mesma como opção extra no
            # topo da lista — pra não quebrar a edição de agendamentos antigos.
            duracoes_padrao = dict(DURACAO_CHOICES)
            if duracao not in duracoes_padrao:
                self.fields["duracao_minutos"].choices = [(duracao, f"{duracao} minutos (atual)")] + DURACAO_CHOICES
            self.fields["duracao_minutos"].initial = duracao
            self.fields["especialidade"].initial = instance.especialidade
            self.fields["especialidade_outra"].initial = instance.especialidade_outra
            self.fields["retorno_sistema_antigo"].initial = instance.itens.filter(retorno_manual=True).exists()
            # Repetição só faz sentido ao criar um agendamento novo.
            del self.fields["repetir_semanalmente"]
            del self.fields["quantidade_sessoes"]
            del self.fields["tem_segundo_dia"]
            del self.fields["segundo_dia_semana"]
            del self.fields["segundo_horario"]
            del self.fields["segundo_duracao_minutos"]
        elif not self.is_bound and self.initial.get("hora_inicio") and not self.initial.get("turno"):
            # Veio de um horário clicado na Central/Agenda do dia: já sugere o turno dele.
            self.fields["turno"].initial = turno_da_hora(self.initial["hora_inicio"])
        if not (instance and instance.pk) and data_travada:
            # Veio da Agenda do dia / calendário com um dia já escolhido —
            # não faz sentido perguntar a data de novo (ela fica fixa, mas
            # ainda é enviada normalmente no POST, só que num campo oculto).
            self.fields["data"].initial = data_travada
            self.fields["data"].widget = forms.HiddenInput()

    def clean(self):
        dados = super().clean()
        data = dados.get("data")
        hora_inicio = dados.get("hora_inicio")
        duracao = dados.get("duracao_minutos")
        forma = dados.get("forma_atendimento")
        paciente = dados.get("paciente")
        profissional = dados.get("profissional")

        # Data/hora de início calculada aqui, mais cedo do que antes (não só no final
        # deste método) — AgendamentoItemFormSet.clean() (validado pela view logo depois
        # deste form) precisa do horário calculado pra detectar retorno grátis por item. O
        # valor final só é gravado na instance no fim deste método, igual sempre foi (ver
        # comentário lá) — aqui só antecipamos o CÁLCULO, não a atribuição, pra não
        # duplicar a conta duas vezes.
        inicio = fim = None
        if data and not hora_inicio and dados.get("turno"):
            # Provisório (início padrão do turno): AgendamentoItemFormSet.clean() troca pelo
            # início do turno do próprio profissional quando não é sessão.
            padrao = HorarioAtendimento.HORARIO_PADRAO_POR_TURNO[HorarioAtendimento.Turno(dados["turno"])]
            hora_inicio = time.fromisoformat(padrao[0])
        if data and hora_inicio:
            inicio_ingenuo = datetime.combine(data, hora_inicio)
            inicio = timezone.make_aware(inicio_ingenuo) if timezone.is_naive(inicio_ingenuo) else inicio_ingenuo
            # Provisório: AgendamentoItemFormSet.clean() volta o fim pro próprio início quando
            # nenhum item é sessão (consulta/exame não têm duração).
            fim = inicio + timedelta(minutes=duracao or 0)

        # Convênio escolhido no agendamento (pedido do cliente, 2026-10-14); sem escolha, vale o
        # do cadastro do paciente. Tem que ser um convênio que o profissional atende — conferido
        # só quando o agendamento é novo ou trocou convênio/profissional, pra não travar a
        # edição de agendamentos antigos de quem deixou de atender aquele convênio.
        if forma == Agendamento.FormaAtendimento.CONVENIO:
            convenio = dados.get("convenio") or (paciente.convenio if paciente and paciente.convenio_id else None)
            mudou = (
                self._convenio_original_id is None
                or (convenio and convenio.pk != self._convenio_original_id)
                or (profissional and profissional.pk != self._profissional_original_id)
            )
            if not convenio:
                self.add_error("convenio", "Escolha o convênio deste atendimento (ou selecione 'Particular').")
            elif profissional and mudou and not profissional.convenios_atendidos.filter(pk=convenio.pk).exists():
                self.add_error(
                    "convenio",
                    f"{profissional.nome} não atende o convênio '{convenio.nome}' (confira em Cadastros → "
                    "Profissionais → 'convênios que atende').",
                )
            else:
                self.instance.convenio = convenio
        else:
            self.instance.convenio = None

        # Especialidade do ATENDIMENTO: só ambíguo quando o profissional tem 2+ (ver
        # cadastros.models.Profissional.especialidades_pares) — automático no caso comum
        # (mesmo valor que Agendamento.save() preencheria sozinho; adiantado aqui pra já
        # estar disponível quando AgendamentoItemFormSet.clean() calcular o retorno
        # grátis, ver agenda/views.py), escolha obrigatória só quando ele tem 2+.
        if profissional:
            pares = profissional.especialidades_pares
            if len(pares) <= 1:
                self.instance.especialidade, self.instance.especialidade_outra = pares[0] if pares else ("", "")
            else:
                especialidade_escolhida = dados.get("especialidade")
                outra_escolhida = dados.get("especialidade_outra") if especialidade_escolhida == "outra" else ""
                par_escolhido = (especialidade_escolhida, outra_escolhida)
                if not especialidade_escolhida:
                    self.add_error("especialidade", "Escolha qual especialidade vale para este atendimento.")
                elif especialidade_escolhida == "outra" and not outra_escolhida:
                    self.add_error("especialidade_outra", "Informe qual é a especialidade.")
                elif par_escolhido not in pares:
                    self.add_error("especialidade", "Escolha uma das especialidades cadastradas para este profissional.")
                else:
                    self.instance.especialidade, self.instance.especialidade_outra = par_escolhido

        # Validação por procedimento (preço cadastrado, profissional habilitado,
        # procedimentos permitidos no bloco de horário, retorno grátis) mudou pra
        # AgendamentoItemFormSet.clean() — um agendamento pode ter vários procedimentos
        # agora, cada um com sua própria checagem (ver agenda/views.py, que valida este
        # form e o formset de itens juntos).

        # Encaixe que JÁ existia (ex.: retorno além do limite) não trava a edição no dia do
        # atendimento — continua aparecendo "justificar" na Agenda do dia. Encaixe marcado
        # agora, à mão, sempre pede o motivo.
        encaixe_ja_existente_no_dia = self.edicao_no_dia and self._encaixe_original
        if dados.get("encaixe") and not (dados.get("observacoes") or "").strip() and not encaixe_ja_existente_no_dia:
            self.add_error(
                "observacoes",
                "Encaixe precisa de uma observação explicando o motivo da exceção de horário.",
            )

        if dados.get("repetir_semanalmente"):
            if not dados.get("quantidade_sessoes"):
                self.add_error("quantidade_sessoes", "Informe quantas sessões deseja agendar.")
            # A checagem de que os procedimentos escolhidos permitem repetição semanal
            # (Procedimento.permite_repeticao_semanal) acontece na view, depois de validar
            # o formset de itens — todos os itens do agendamento precisam permitir, já que
            # a série semanal copia o conjunto inteiro pra cada sessão gerada.

        if dados.get("tem_segundo_dia"):
            if not dados.get("repetir_semanalmente"):
                self.add_error(
                    "tem_segundo_dia", "Marque também 'Repetir semanalmente' para usar um segundo dia da semana."
                )
            if dados.get("segundo_dia_semana") in (None, ""):
                self.add_error("segundo_dia_semana", "Escolha o segundo dia da semana.")
            if not dados.get("segundo_horario"):
                self.add_error("segundo_horario", "Informe o horário do segundo dia.")
            if not dados.get("segundo_duracao_minutos"):
                dados["segundo_duracao_minutos"] = duracao

        if inicio and fim:
            # Guarda direto na instance: o _post_clean() do ModelForm chama
            # instance.full_clean() logo depois deste clean(), e é lá que
            # Agendamento.clean() confere conflito de horário — precisa que
            # esses dois campos já estejam preenchidos na instance nesse momento.
            self.instance.data_hora_inicio = inicio
            self.instance.data_hora_fim = fim
        # Ainda não se sabe se é sessão (os itens são validados depois, pelo formset) — a
        # checagem de conflito de horário fica pra AgendamentoItemFormSet.clean().
        self.instance.adiar_checagem_conflito = True

        return dados


class AgendamentoItemForm(EstiloCampoMixin, forms.ModelForm):
    """Uma linha (procedimento + quantidade) do formset abaixo — a validação de
    verdade (preço cadastrado, profissional habilitado, procedimentos permitidos no
    bloco de horário, retorno grátis) mora em AgendamentoItemFormSet.clean(), não
    aqui, porque depende de dados do AgendamentoForm (paciente/profissional/forma_
    atendimento/horário) que este form sozinho não tem acesso."""

    class Meta:
        model = AgendamentoItem
        fields = ["procedimento", "quantidade"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["procedimento"].queryset = Procedimento.objects.filter(ativo=True).order_by("nome")

    def has_changed(self):
        # Item já salvo é sempre regravado: o formset do Django só salva os itens
        # existentes cujo form "mudou", mas parte do que vai no item é calculada em
        # _AgendamentoItemFormSetBase.clean() a partir do AgendamentoForm (retorno_manual
        # do "retorno do sistema antigo", retorno_de, quantidade de guias). Sem isto,
        # editar só o checkbox de retorno (sem mexer na linha do procedimento) não
        # salvava nada — relatado pela clínica em 06/11/2026.
        return bool(self.instance.pk) or super().has_changed()


class _AgendamentoItemFormSetBase(forms.BaseInlineFormSet):
    """
    Base do formset de AgendamentoItem (ver AgendamentoItemFormSet, produzido a
    partir desta classe logo abaixo via inlineformset_factory) — substitui o antigo
    campo único 'procedimento' de AgendamentoForm. A view instancia isto passando
    `agendamento_form=<o AgendamentoForm já validado>`, nessa ordem (form primeiro,
    formset depois), porque o clean() abaixo lê `agendamento_form.cleaned_data`/
    `.instance` (paciente, profissional, forma_atendimento, convenio,
    data_hora_inicio já resolvidos lá).

    Duplicar o mesmo procedimento em duas linhas do mesmo agendamento já é barrado
    automaticamente pela validação de unicidade do Django sobre
    AgendamentoItem.Meta.constraints (unico_procedimento_por_agendamento) — não
    precisa reforçar aqui.

    Depois de validar com sucesso, expõe `self.algum_item_nao_repete` (bool) — usado
    pela view pra decidir se "repetir semanalmente" pode seguir em frente (todos os
    procedimentos escolhidos precisam permitir repetição semanal, já que a série
    semanal copia o conjunto inteiro de itens pra cada sessão gerada).
    """

    def __init__(self, *args, agendamento_form=None, **kwargs):
        self.agendamento_form = agendamento_form
        self.algum_item_nao_repete = False
        self._opcoes_procedimento = None
        super().__init__(*args, **kwargs)

    def _procedimentos_do_profissional_escolhido(self):
        """Opções do <select> de procedimento já filtradas pelos habilitados do
        profissional escolhido no AgendamentoForm (POST, initial vindo da Central/
        Agenda do dia, ou o do agendamento em edição) — ou nenhuma, se ainda não há
        profissional. Antes a página chegava com TODOS os procedimentos ativos e só o
        JavaScript (popularSelectProcedimento em agendamento_form.html) filtrava
        depois, então dava pra ver/escolher procedimento de outro profissional
        enquanto a lista não era trocada. O JavaScript continua refinando por preço."""
        if self._opcoes_procedimento is None:
            form = self.agendamento_form
            valor = None
            if form is not None:
                valor = form.data.get(form.add_prefix("profissional")) if form.is_bound else form.initial.get("profissional")
            try:
                profissional_id = int(str(valor)) if valor else None
            except (TypeError, ValueError):
                profissional_id = None
            habilitados = (
                Procedimento.objects.filter(ativo=True, profissionais_habilitados__pk=profissional_id).order_by("nome")
                if profissional_id
                else Procedimento.objects.none()
            )
            self._opcoes_procedimento = [(p.pk, str(p)) for p in habilitados]
        return self._opcoes_procedimento

    def add_fields(self, form, index):
        super().add_fields(form, index)
        if "procedimento" not in form.fields:
            return
        opcoes = list(self._procedimentos_do_profissional_escolhido())
        # Item já salvo (edição) com procedimento que deixou de ser vinculado: continua
        # aparecendo selecionado, pra pessoa ver o que era — a validação acima barra o
        # salvamento até trocar.
        atual = form.instance.procedimento if form.instance.pk and form.instance.procedimento_id else None
        if atual and atual.pk not in {pk for pk, _ in opcoes}:
            opcoes.append((atual.pk, str(atual)))
        form.fields["procedimento"].widget.choices = [("", "---------")] + opcoes

    def clean(self):
        super().clean()
        if any(self.errors) or self.agendamento_form is None:
            return

        af = self.agendamento_form
        dados_ag = af.cleaned_data
        paciente = dados_ag.get("paciente")
        profissional = dados_ag.get("profissional")
        forma = dados_ag.get("forma_atendimento")
        data = dados_ag.get("data")
        hora_inicio = dados_ag.get("hora_inicio")
        encaixe = dados_ag.get("encaixe")
        convenio = af.instance.convenio
        inicio = af.instance.data_hora_inicio
        # Já resolvidos por AgendamentoForm.clean() (automático quando o profissional só
        # tem 1 especialidade, escolhido pela recepção quando ele tem 2+) — é isto, não
        # mais "a" especialidade do profissional, que vale pra achar o retorno grátis.
        especialidade = af.instance.especialidade
        especialidade_outra = af.instance.especialidade_outra

        # Mesma regra de sempre: o que já está Realizado não é re-rotulado (o que foi
        # cobrado, ou não, já aconteceu de verdade) — ver Agendamento.clean() e o
        # comentário equivalente que existia em AgendamentoForm.clean().
        ja_realizado = bool(af.instance.pk) and af.instance.status == Agendamento.Status.REALIZADO

        procedimentos = [
            form.cleaned_data["procedimento"]
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE") and form.cleaned_data.get("procedimento")
        ]
        if not procedimentos:
            raise forms.ValidationError("Adicione pelo menos um procedimento a este agendamento.")
        if dados_ag.get("retorno_sistema_antigo") and not any(p.eh_consulta for p in procedimentos):
            raise forms.ValidationError(
                "\"Retorno de consulta do sistema antigo\" só vale para consulta — adicione o procedimento de "
                "consulta ou desmarque essa opção."
            )

        # Só sessão tem hora marcada, duração e conflito de horário (ver Agendamento.eh_sessao).
        # Consulta/exame entram na lista do TURNO do profissional, na ordem de agendamento:
        # gravam o início do turno (o do bloco dele nesse dia, ou o padrão do turno quando ele
        # não tem horário cadastrado) com fim = início, e nunca bloqueiam a agenda.
        tem_sessao = any(p.permite_repeticao_semanal for p in procedimentos)
        ag = af.instance
        ag.eh_sessao = tem_sessao
        if data:
            if tem_sessao:
                if not hora_inicio:
                    raise forms.ValidationError("Informe o horário da sessão.")
                if not dados_ag.get("duracao_minutos"):
                    raise forms.ValidationError("Informe a duração da sessão.")
            else:
                # Sem turno escolhido mas com horário (ex: veio de um horário clicado na
                # Central de Atendimento): vale o turno desse horário.
                turno = dados_ag.get("turno") or (turno_da_hora(hora_inicio) if hora_inicio else "")
                if not turno:
                    raise forms.ValidationError("Escolha o turno do atendimento (manhã, tarde ou noite).")
                bloco, atende_no_dia = profissional.bloco_do_turno_em(data, turno) if profissional else (None, False)
                if bloco is None and atende_no_dia and not encaixe:
                    raise forms.ValidationError(
                        f"{profissional.nome} não atende no turno da {dict(TURNO_CHOICES)[turno].lower()} "
                        "nesse dia — escolha outro turno ou outro dia (ou marque encaixe)."
                    )
                if bloco:
                    hora_inicio = bloco.horario_inicio
                else:
                    padrao = HorarioAtendimento.HORARIO_PADRAO_POR_TURNO[HorarioAtendimento.Turno(turno)]
                    hora_inicio = time.fromisoformat(padrao[0])
                inicio_ingenuo = datetime.combine(data, hora_inicio)
                inicio = timezone.make_aware(inicio_ingenuo) if timezone.is_naive(inicio_ingenuo) else inicio_ingenuo
                ag.data_hora_inicio = ag.data_hora_fim = inicio

        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            procedimento = form.cleaned_data.get("procedimento")
            if not procedimento:
                continue

            # Sessão por convênio: a quantidade (= nº de guias) sai da duração — 1h = 2
            # sessões/guias (ver AgendamentoItem.quantidade_guias_sessao). Não mexe em
            # atendimento que já conta na produção (valor do convênio já congelado).
            if not (af.instance.pk and af.instance.status in Agendamento.STATUS_PRODUCAO):
                guias = AgendamentoItem.quantidade_guias_sessao(procedimento, forma, dados_ag.get("duracao_minutos"))
                if guias is not None:
                    form.instance.quantidade = guias

            # --- Retorno grátis por item (30 dias corridos ou o prazo do convênio da
            # consulta original, mesma especialidade) — ver
            # AgendamentoItem.retorno_de/buscar_item_para_retorno/eh_retorno. 100%
            # calculado pelo sistema, nunca uma escolha da recepção. Recalculado do
            # ZERO a cada clean() (nunca "fixado" da primeira vez), exceto quando já
            # Realizado.
            if not ja_realizado:
                form.instance.retorno_de = None
                if procedimento.eh_consulta and especialidade and paciente and inicio:
                    form.instance.retorno_de = AgendamentoItem.buscar_item_para_retorno(
                        paciente, especialidade, especialidade_outra, inicio, excluir_pk=form.instance.pk
                    )
                # Retorno de consulta do SISTEMA ANTIGO (pedido da clínica, 2026-10-16 —
                # provisório): marcado à mão no agendamento, só vale pra item de consulta e só
                # quando o automático acima não achou nada.
                form.instance.retorno_manual = bool(
                    dados_ag.get("retorno_sistema_antigo") and procedimento.eh_consulta and not form.instance.retorno_de_id
                )

            # Só é possível agendar um procedimento que tenha valor cadastrado pra forma
            # de atendimento escolhida — mesma regra/motivo de sempre (ver histórico em
            # AgendamentoForm.clean()). "Encaixe" NÃO pula essa checagem (é exceção de
            # horário, não de preço); retorno grátis detectado acima TAMBÉM pula (não
            # faz sentido exigir preço pra um item que o sistema já sabe que é grátis).
            # Item que o pacote do paciente já cobre (ver agenda.models.PacoteVendido) também
            # não precisa de preço cadastrado — ele não vai ser cobrado.
            coberto_por_pacote = (
                forma == Agendamento.FormaAtendimento.PARTICULAR
                and not form.instance.eh_retorno
                and paciente
                and profissional
                and PacoteVendido.para_item(
                    paciente.pk,
                    profissional.pk,
                    procedimento.pk,
                    form.cleaned_data.get("quantidade") or 1,
                    agendamento_id=af.instance.pk,
                    excluir_item_pk=form.instance.pk,
                )
                is not None
            )
            if not form.instance.eh_retorno and not coberto_por_pacote:
                if (
                    forma == Agendamento.FormaAtendimento.PARTICULAR
                    and profissional
                    and profissional.cobra_valor_fixo_particular
                ):
                    # Profissional com valor fixo: só o que está na tabela dele (decisão do
                    # cliente — não cai no valor_particular padrão do procedimento).
                    if profissional.valor_fixo_para(procedimento) is None:
                        form.add_error(
                            "procedimento",
                            f"{profissional.nome} cobra valor fixo no particular e '{procedimento.nome}' "
                            "ainda não está na tabela dele (Cadastros → Profissionais → editar → 'Valores "
                            "fixos no particular') — não é possível agendar como particular.",
                        )
                elif forma == Agendamento.FormaAtendimento.PARTICULAR and procedimento.valor_particular is None:
                    form.add_error(
                        "procedimento",
                        f"'{procedimento.nome}' ainda não tem valor particular cadastrado (Cadastros → "
                        "Procedimentos → 'Valor particular') — não é possível agendar como particular.",
                    )
                elif (
                    forma == Agendamento.FormaAtendimento.CONVENIO
                    and convenio
                    and convenio.valor_para(procedimento, profissional) is None
                ):
                    form.add_error(
                        "procedimento",
                        f"'{procedimento.nome}' ainda não tem valor cadastrado para o convênio "
                        f"'{convenio.nome}' (Cadastros → Convênios → Tabela de preços) — não é possível "
                        "agendar por esse convênio.",
                    )

            # Sem exceção pra profissional que ainda não tem nenhum procedimento vinculado
            # (antes, nesse caso, qualquer procedimento passava — ver
            # agenda.views.procedimentos_do_profissional).
            if profissional and not profissional.procedimentos_habilitados.filter(
                ativo=True, pk=procedimento.pk
            ).exists():
                form.add_error(
                    "procedimento",
                    f"{profissional.nome} não está habilitado para '{procedimento.nome}' "
                    "(confira em Cadastros → Procedimentos → 'profissionais habilitados').",
                )

            # Mesma exceção de "encaixe" de sempre — pula a checagem de procedimentos
            # permitidos no bloco de horário (ver histórico em AgendamentoForm.clean()).
            if profissional and data and hora_inicio and not encaixe:
                bloco = profissional.bloco_atendimento_em(data, hora_inicio)
                if bloco is not None:
                    permitidos = bloco.procedimentos_permitidos.all()
                    if permitidos.exists() and procedimento not in permitidos:
                        nomes = ", ".join(p.nome for p in permitidos)
                        form.add_error(
                            "procedimento",
                            f"Nesse horário, {profissional.nome} só atende: {nomes} (confira em "
                            "Cadastros → Profissionais → 'Horários'/'Exceções').",
                        )

            if not procedimento.permite_repeticao_semanal:
                self.algum_item_nao_repete = True

        self._limitar_retornos_por_dia(ag, profissional, encaixe, ja_realizado)

        if tem_sessao and ag.data_hora_inicio:
            ag.verificar_conflito_horario()

    def _limitar_retornos_por_dia(self, ag, profissional, encaixe, ja_realizado):
        """Pedido da clínica (23/10/2026): cada profissional atende até
        settings.AGENDA_RETORNOS_POR_DIA retornos grátis por dia (padrão 5). Retorno além disso
        vira ENCAIXE sozinho e, desde 06/11/2026 (pedido da clínica: "tá passando direto"),
        só salva com o motivo escrito nas observações. Exceção: edição de agendamento do
        próprio dia (AgendamentoForm.edicao_no_dia) — corrigir algo com o paciente na clínica
        não trava; fica "encaixe sem justificativa" (destacado na Agenda do dia) pra justificar
        depois. `self.retorno_alem_do_limite` guarda quantos já havia, pra view avisar."""
        self.retorno_alem_do_limite = None
        self.retorno_encaixe_justificado = None
        if encaixe or ja_realizado or not (profissional and ag.data_hora_inicio):
            return
        if not any(
            form.instance.eh_retorno
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE") and form.cleaned_data.get("procedimento")
        ):
            return
        dia = timezone.localtime(ag.data_hora_inicio).date()
        outros = (
            Agendamento.objects.filter(
                AgendamentoItem.filtro_retorno("itens__"),
                profissional=profissional,
                data_hora_inicio__date=dia,
            )
            .exclude(status__in=[Agendamento.Status.CANCELADO, Agendamento.Status.FALTOU])
            .distinct()
        )
        if ag.pk:
            outros = outros.exclude(pk=ag.pk)
        ja_marcados = outros.count()
        if ja_marcados >= settings.AGENDA_RETORNOS_POR_DIA:
            af = self.agendamento_form
            sem_motivo = not (af.cleaned_data.get("observacoes") or "").strip()
            if sem_motivo and not af.edicao_no_dia:
                raise forms.ValidationError(
                    f"{profissional.nome} já tem {ja_marcados} retorno(s) em {dia:%d/%m/%Y} (limite de "
                    f"{settings.AGENDA_RETORNOS_POR_DIA} por dia) — este retorno será um ENCAIXE. Escreva "
                    "nas observações o motivo do encaixe para salvar."
                )
            ag.encaixe = True
            self.retorno_alem_do_limite = ja_marcados if sem_motivo else None
            self.retorno_encaixe_justificado = ja_marcados if not sem_motivo else None


# Formset de verdade usado pela view (agenda.views.agendamento_novo/agendamento_editar) —
# combina AgendamentoItemForm (campos por linha) com _AgendamentoItemFormSetBase (clean()
# entre linhas). extra=0 + min_num=1: agendamento novo abre com 1 linha em branco (o Django
# SOMA extra ao mínimo — com extra=1 abriam 2 linhas, relatado pela clínica em 15/10/2026); min_num=1 +
# validate_min=True: exige pelo menos um procedimento (a 1ª linha não é "opcional" como as
# extras além do mínimo); can_delete=True: permite remover itens ao editar um agendamento
# existente.
AgendamentoItemFormSet = forms.inlineformset_factory(
    Agendamento,
    AgendamentoItem,
    form=AgendamentoItemForm,
    formset=_AgendamentoItemFormSetBase,
    extra=0,
    min_num=1,
    validate_min=True,
    can_delete=True,
)


class SalaDoDiaForm(EstiloCampoMixin, forms.Form):
    """
    Formulário simples usado em "Minha agenda" para o profissional escolher
    "estou atendendo na sala: [X]" no dia — grava um novo AtendimentoSala
    (ver cadastros.models). Preparação para o futuro sistema de fila/senha.
    """

    sala = forms.ModelChoiceField(label="Sala de hoje", queryset=None)

    def __init__(self, *args, **kwargs):
        from cadastros.models import Sala

        super().__init__(*args, **kwargs)
        self.fields["sala"].queryset = Sala.objects.filter(ativa=True).order_by("nome")


class GuicheDoDiaForm(EstiloCampoMixin, forms.Form):
    """
    Formulário simples usado na Agenda do dia para o usuário da recepção
    escolher "estou no guichê: [X]" no dia — grava um novo AtendimentoGuiche
    (ver cadastros.models). Mesmo padrão de SalaDoDiaForm, mas para a
    recepção em vez do profissional. Preparação para o futuro sistema de
    fila/senha.
    """

    guiche = forms.ModelChoiceField(label="Guichê de hoje", queryset=None)

    def __init__(self, *args, **kwargs):
        from cadastros.models import Guiche

        super().__init__(*args, **kwargs)
        self.fields["guiche"].queryset = Guiche.objects.filter(ativo=True).order_by("nome")


class EspecialidadesDoDiaForm(EstiloCampoMixin, forms.Form):
    """
    Formulário usado na Agenda do dia para o usuário da recepção marcar quais
    especialidades está cobrindo HOJE (pode ser mais de uma) — grava um novo
    conjunto de AtendimentoEspecialidade (ver cadastros.models), substituindo
    o que valia antes nesse mesmo dia. Mesmo espírito de GuicheDoDiaForm, mas
    permitindo múltipla escolha.

    As opções vêm de Profissional.especialidades_ativas_em (mesma fonte do
    cadastro de profissional, só quem tem alguém atendendo hoje) — nunca uma
    lista digitada à mão — MAIS as especialidades que só têm um exame avulso
    agendado hoje (ver Agendamento.exames_avulsos_agendados_em/
    cadastros.models.Procedimento.exame_sem_profissional, ex: MAPA), marcadas
    com um rótulo à parte ("— exame agendado hoje") pra recepção não confundir
    com o profissional realmente atendendo — decisão da clínica, 2026-07-30:
    sem isso, a recepção não conseguia marcar que estava cobrindo a
    especialidade do exame, e a senha ficava sem ninguém pra chamar.
    """

    especialidades = forms.MultipleChoiceField(
        label="Especialidades de hoje",
        widget=forms.CheckboxSelectMultiple,
        choices=[],
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        hoje = timezone.localdate()
        ativas = Profissional.especialidades_ativas_em(hoje)
        chaves_ativas = {chave for chave, _rotulo in ativas}

        exames_por_chave = {}
        for exame in Agendamento.exames_avulsos_agendados_em(hoje):
            chave = f"{exame['especialidade']}|{exame['especialidade_outra']}"
            if chave in chaves_ativas or chave in exames_por_chave:
                continue
            rotulo = especialidade_rotulo(exame["especialidade"], exame["especialidade_outra"])
            exames_por_chave[chave] = f"{rotulo} — exame agendado hoje"

        self.fields["especialidades"].choices = ativas + list(exames_por_chave.items())

        # Profissionais de hoje (ver cadastros.models.AtendimentoProfissional): só nas
        # especialidades com 2+ profissionais atendendo, onde a recepção se divide.
        # `profissionais_por_chave` é o que o template usa pra mostrar cada profissional
        # embaixo da sua especialidade.
        self.profissionais_por_chave = {
            chave: profissionais
            for chave, profissionais in Profissional.presentes_por_especialidade(hoje).items()
            if len(profissionais) > 1
        }
        vistos = {}
        for profissionais in self.profissionais_por_chave.values():
            for profissional in profissionais:
                vistos[str(profissional.pk)] = profissional.nome
        self.fields["profissionais"].choices = list(vistos.items())

    profissionais = forms.MultipleChoiceField(
        label="Profissionais de hoje",
        widget=forms.CheckboxSelectMultiple,
        choices=[],
        required=False,
    )

    def grupos(self):
        """[(checkbox da especialidade, [checkboxes dos profissionais dela])] — profissionais
        só aparecem nas especialidades com 2+ atendendo hoje."""
        caixas_profissional = {str(caixa.data["value"]): caixa for caixa in self["profissionais"]}
        resultado = []
        for caixa in self["especialidades"]:
            profissionais = self.profissionais_por_chave.get(str(caixa.data["value"]), [])
            resultado.append((caixa, [caixas_profissional[str(p.pk)] for p in profissionais if str(p.pk) in caixas_profissional]))
        return resultado


class ConfirmarPagamentoForm(EstiloCampoMixin, forms.Form):
    """
    Tela de "registrar pagamento" — usada antes de deixar o agendamento
    avançar de 'Pré-autorizado' pra 'Autorizado' (ver
    agenda.views.agendamento_confirmar_pagamento).

    O valor cobrado é sempre calculado automaticamente a partir do cadastro
    (ver Agendamento.valor_a_cobrar) — NUNCA digitado à mão, pra não correr o
    risco de cobrar um valor errado por engano (pedido do cliente).
    'valor_1'/'valor_2' só entram em jogo quando o pagamento é dividido em 2
    formas (ex.: metade dinheiro, metade cartão) — nesse caso sim precisam
    ser preenchidos, e a soma dos dois tem que bater exatamente com o valor
    esperado; com uma forma só, o campo de valor nem aparece na tela, só a
    forma de pagamento. Cada forma vira um agenda.models.PagamentoParticular
    à parte (ver `linhas_pagamento()` abaixo, usada pela view depois de
    validar). 'cortesia' é uma opção separada: quando marcada, o profissional
    aceitou atender sem cobrar — ignora as formas de pagamento e fecha o
    atendimento sem gerar cobrança nem repasse.

    'valor_recebido_1'/'2' e 'parcelas_1'/'2' são o oposto de 'valor_1'/'2': só
    existem pra cartão (débito ou crédito) e são SEMPRE digitados à mão, nunca
    calculados pelo sistema — não há tabela de taxas de maquininha aqui
    (decisão do cliente); quem registra o pagamento já sabe/vê quanto caiu na
    conta e digita direto. 'valor_recebido' é exigido em qualquer forma de
    cartão; 'parcelas' só é exigido em crédito (débito é sempre à vista).
    Nenhum dos dois é usado no cálculo de repasse ao profissional — isso
    continua inteiramente sobre 'valor' (ver agenda.models.PagamentoParticular).
    """

    cortesia = forms.BooleanField(
        required=False,
        label="Atendimento cortesia (sem cobrança)",
        help_text="O profissional aceitou atender sem cobrar (ex.: paciente amigo) — não gera cobrança nem repasse.",
    )
    forma_pagamento_1 = forms.ChoiceField(label="1ª forma de pagamento", choices=[], required=False)
    valor_1 = forms.DecimalField(
        label="Valor da 1ª forma", required=False, min_value=Decimal("0.01"), max_digits=8, decimal_places=2
    )
    # Só cartão (débito/crédito) — sem tabela de taxas no sistema (decisão do cliente): o
    # financeiro digita o valor realmente recebido no momento do pagamento (ver clean() abaixo,
    # que exige/limpa estes campos conforme a forma de pagamento escolhida em cada linha).
    valor_recebido_1 = forms.DecimalField(
        label="Valor recebido — 1ª forma (líquido da maquininha)", required=False,
        min_value=Decimal("0.01"), max_digits=8, decimal_places=2,
    )
    parcelas_1 = forms.TypedChoiceField(label="Parcelas — 1ª forma", required=False, choices=[], coerce=int, empty_value=None)
    forma_pagamento_2 = forms.ChoiceField(
        label="2ª forma de pagamento (opcional, se dividir o valor)", choices=[], required=False
    )
    valor_2 = forms.DecimalField(
        label="Valor da 2ª forma", required=False, min_value=Decimal("0.01"), max_digits=8, decimal_places=2
    )
    valor_recebido_2 = forms.DecimalField(
        label="Valor recebido — 2ª forma (líquido da maquininha)", required=False,
        min_value=Decimal("0.01"), max_digits=8, decimal_places=2,
    )
    parcelas_2 = forms.TypedChoiceField(
        label="Parcelas — 2ª forma", required=False, choices=[], coerce=int, empty_value=None
    )
    # Desconto combinado com o profissional (pedido da clínica, 15/10/2026). O valor cobrado
    # passa a ser o do cadastro MENOS o desconto; o motivo é obrigatório (fica no histórico).
    # 'repasse_acordado' só existe para quem cobra valor fixo no particular (o repasse dele
    # não é percentual, então precisa ser combinado de novo com o desconto) — ver __init__.
    desconto = forms.DecimalField(
        label="Desconto (R$)", required=False, min_value=Decimal("0.01"), max_digits=8, decimal_places=2,
        help_text="Só se o profissional autorizou dar desconto. Deixe em branco para cobrar o valor normal.",
    )
    motivo_desconto = forms.CharField(
        label="Motivo do desconto", required=False, max_length=200,
        help_text="Ex.: \"Dr. Fulano autorizou\".",
    )
    repasse_acordado = forms.DecimalField(
        label="Repasse acordado com o profissional (R$)", required=False, min_value=Decimal("0"),
        max_digits=8, decimal_places=2,
        help_text="Quanto o profissional vai receber deste atendimento com o desconto.",
    )

    def __init__(self, *args, agendamento=None, **kwargs):
        self.agendamento = agendamento
        super().__init__(*args, **kwargs)
        self.repasse_fixo_padrao = agendamento.repasse_fixo_padrao if agendamento else None
        if self.repasse_fixo_padrao is None:
            del self.fields["repasse_acordado"]
        # As 4 formas "reais" — cortesia é decidida pelo checkbox dedicado acima, não
        # aparece nestes <select>s (ver docstring da classe).
        formas_reais = [
            (codigo, rotulo)
            for codigo, rotulo in Agendamento.FormaPagamento.choices
            if codigo != Agendamento.FormaPagamento.CORTESIA
        ]
        self.fields["forma_pagamento_1"].choices = [("", "---------")] + formas_reais
        self.fields["forma_pagamento_2"].choices = [("", "---------")] + formas_reais
        # Teto do dropdown é só uma conveniência de UI, sem regra de negócio por trás (o campo
        # no modelo aceita qualquer valor >= 1) — ajustar aqui se a clínica usar mais parcelas.
        opcoes_parcelas = [("", "---------")] + [(n, f"{n}x") for n in range(1, 13)]
        self.fields["parcelas_1"].choices = opcoes_parcelas
        self.fields["parcelas_2"].choices = opcoes_parcelas

    def clean(self):
        dados = super().clean()

        # Retorno grátis (ver Agendamento.tem_item_cobravel): cortesia aqui é
        # OBRIGATÓRIA e NUNCA escolhida pelo operador — trava sem exceção (diferente da
        # cortesia "normal" logo abaixo, que É uma escolha dele). Só entra neste ramo
        # quando TODOS os itens do agendamento são retorno grátis — um agendamento misto
        # (ex: consulta-retorno + exame pago no mesmo horário) tem 'tem_item_cobravel'
        # True e cai no fluxo normal abaixo, cobrando só pelos itens não-retorno. A tela
        # nem mostra os campos de forma de pagamento quando não há nada cobrável (ver
        # agendamento_confirmar_pagamento.html), então só chegaria algo neles por
        # adulteração do POST — por isso limpa qualquer erro que esses campos já tenham
        # acumulado em _clean_fields() (ex: um valor não-numérico digitado à força) e
        # ignora qualquer valor submetido: a decisão é sempre do sistema, nunca do que
        # foi postado.
        campos_cartao = ("valor_recebido_1", "parcelas_1", "valor_recebido_2", "parcelas_2")

        if self.agendamento and not self.agendamento.tem_item_cobravel:
            for campo in ("forma_pagamento_1", "valor_1", "forma_pagamento_2", "valor_2") + campos_cartao:
                self.errors.pop(campo, None)
            dados["cortesia"] = True
            dados["forma_pagamento_1"] = ""
            dados["valor_1"] = None
            dados["forma_pagamento_2"] = ""
            dados["valor_2"] = None
            for campo in campos_cartao:
                dados[campo] = None
            for campo in ("desconto", "motivo_desconto", "repasse_acordado"):
                self.errors.pop(campo, None)
                dados[campo] = None
            return dados

        if dados.get("cortesia") or not dados.get("desconto"):
            dados["desconto"] = None
            dados["motivo_desconto"] = ""
            dados["repasse_acordado"] = None
            for campo in ("desconto", "motivo_desconto", "repasse_acordado"):
                self.errors.pop(campo, None)

        if dados.get("cortesia"):
            if self.agendamento and self.agendamento.pacotes_vendidos.exists():
                self.add_error(None, "Pacote não pode ser vendido como cortesia — retire o pacote ou desmarque a cortesia.")
            if dados.get("forma_pagamento_2") or dados.get("valor_2"):
                self.add_error(None, "Cortesia não pode ser combinada com uma segunda forma de pagamento.")
            for campo in campos_cartao:
                self.errors.pop(campo, None)
                dados[campo] = None
            return dados

        if not dados.get("forma_pagamento_1"):
            self.add_error("forma_pagamento_1", "Selecione a forma de pagamento (ou marque como cortesia).")
            return dados

        valor_esperado = self.agendamento.valor_a_cobrar if self.agendamento else None
        desconto = dados.get("desconto")
        if desconto and valor_esperado is not None:
            if self.agendamento.pacotes_vendidos.exists():
                self.add_error("desconto", "Desconto não vale junto com a venda de pacote — ajuste o valor do pacote.")
            elif desconto >= valor_esperado:
                self.add_error(
                    "desconto",
                    f"O desconto precisa ser menor que o valor do atendimento (R$ {formatar_moeda(valor_esperado)}) — "
                    "para não cobrar nada, marque cortesia.",
                )
            else:
                valor_esperado -= desconto
                if not (dados.get("motivo_desconto") or "").strip():
                    self.add_error("motivo_desconto", "Informe o motivo do desconto (ex.: quem autorizou).")
                if "repasse_acordado" in self.fields:
                    repasse = dados.get("repasse_acordado")
                    if repasse is None:
                        self.add_error(
                            "repasse_acordado",
                            "Este profissional cobra valor fixo — informe quanto ficou acordado de repasse com o desconto.",
                        )
                    elif repasse > valor_esperado:
                        self.add_error(
                            "repasse_acordado",
                            f"O repasse não pode ser maior que o valor cobrado com desconto (R$ {formatar_moeda(valor_esperado)}).",
                        )
        self.valor_final = valor_esperado

        if dados.get("forma_pagamento_2"):
            # Dividido em 2 formas — aí sim precisa digitar os valores, e eles têm que
            # somar exatamente o valor esperado (evita cobrar errado por digitação).
            if not dados.get("valor_1") or not dados.get("valor_2"):
                self.add_error(None, "Informe o valor de cada forma de pagamento ao dividir em duas.")
            elif valor_esperado is not None and dados["valor_1"] + dados["valor_2"] != valor_esperado:
                self.add_error(
                    None,
                    f"A soma das duas formas de pagamento precisa ser R$ {formatar_moeda(valor_esperado)} "
                    "(valor deste atendimento) — confira os valores.",
                )
        else:
            # Uma forma só — o valor é sempre o automático, ninguém digita nada aqui.
            if valor_esperado is None:
                self.add_error(
                    None,
                    "Não foi possível calcular o valor deste atendimento automaticamente — cadastre o valor "
                    "particular do procedimento (Cadastros → Procedimentos) ou o preço no convênio "
                    "(Cadastros → Convênios) antes de registrar o pagamento.",
                )
            else:
                dados["valor_1"] = valor_esperado

        # Valor recebido/parcelas — só cartão (débito ou crédito), uma linha de cada vez. Sem
        # tabela de taxas no sistema (decisão do cliente): quem registra o pagamento digita à
        # mão o valor que de fato caiu na conta, já descontada a taxa da maquininha. Parcelas só
        # faz sentido em crédito (débito é sempre à vista) — por isso só é exigido ali.
        for sufixo in ("1", "2"):
            forma = dados.get(f"forma_pagamento_{sufixo}")
            eh_debito = forma == Agendamento.FormaPagamento.CARTAO_DEBITO
            eh_credito = forma == Agendamento.FormaPagamento.CARTAO_CREDITO
            campo_valor_recebido, campo_parcelas = f"valor_recebido_{sufixo}", f"parcelas_{sufixo}"

            if not (eh_debito or eh_credito):
                self.errors.pop(campo_valor_recebido, None)
                self.errors.pop(campo_parcelas, None)
                dados[campo_valor_recebido] = None
                dados[campo_parcelas] = None
                continue

            valor_recebido = dados.get(campo_valor_recebido)
            valor_desta_forma = dados.get(f"valor_{sufixo}")
            if valor_recebido is None:
                self.add_error(campo_valor_recebido, "Informe o valor recebido (já descontada a taxa da maquininha).")
            elif valor_desta_forma is not None and valor_recebido > valor_desta_forma:
                self.add_error(
                    campo_valor_recebido,
                    f"O valor recebido não pode ser maior que o valor cobrado nesta forma (R$ {formatar_moeda(valor_desta_forma)}).",
                )

            if eh_credito:
                if not dados.get(campo_parcelas):
                    self.add_error(campo_parcelas, "Informe a quantidade de parcelas.")
            else:
                dados[campo_parcelas] = None

        return dados

    def linhas_pagamento(self):
        """Lista de dicts (forma_pagamento/valor/valor_recebido/parcelas) a criar como
        PagamentoParticular — só chamar depois de is_valid(). Cortesia devolve uma única linha
        com valor 0 e valor_recebido/parcelas None (cortesia nunca é cartão)."""
        if self.cleaned_data.get("cortesia"):
            return [{
                "forma_pagamento": Agendamento.FormaPagamento.CORTESIA,
                "valor": Decimal("0"),
                "valor_recebido": None,
                "parcelas": None,
            }]
        linhas = [{
            "forma_pagamento": self.cleaned_data["forma_pagamento_1"],
            "valor": self.cleaned_data["valor_1"],
            "valor_recebido": self.cleaned_data.get("valor_recebido_1"),
            "parcelas": self.cleaned_data.get("parcelas_1"),
        }]
        if self.cleaned_data.get("forma_pagamento_2"):
            linhas.append({
                "forma_pagamento": self.cleaned_data["forma_pagamento_2"],
                "valor": self.cleaned_data["valor_2"],
                "valor_recebido": self.cleaned_data.get("valor_recebido_2"),
                "parcelas": self.cleaned_data.get("parcelas_2"),
            })
        return linhas


class AutorizacaoConvenioForm(EstiloCampoMixin, forms.Form):
    """Tela de autorização de atendimento por convênio (agenda.views.agendamento_autorizar) —
    pedido da clínica, 15/10/2026. Quem autorizou é gravado sozinho; aqui só a observação e o
    número da guia de consulta/exame (opcional — decisão do cliente)."""

    numero_guia = forms.CharField(label="Número da guia (consulta/exame)", max_length=40, required=False)
    observacao = forms.CharField(
        label="Observação", required=False, widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Ex.: \"AUT. NO CRM DENNIS\", senha do plano, quem liberou...",
    )
    # Pedido da clínica (2026-10-16): autorização feita sem o paciente vir assinar a guia —
    # fica constando em "Guias pendentes" até marcar "Assinou" (AgendamentoItem.sem_assinatura).
    sem_assinatura = forms.BooleanField(
        label="Sem assinatura (o paciente ainda não assinou a guia)", required=False,
    )
    # Pedido da clínica (29/10/2026) — ver Agendamento.motivo_prioridade.
    motivo_prioridade = forms.ChoiceField(
        label="Prioridade", required=False,
        choices=[("", "Normal (sem prioridade)")] + Agendamento._meta.get_field("motivo_prioridade").choices,
        help_text="Paciente com senha P no totem: marque o motivo — na fila do profissional ele passa "
        "na frente, alternando 1 prioritário e 1 normal.",
    )


class GuiaSessaoForm(EstiloCampoMixin, forms.Form):
    """Escolha da guia de sessões do plano (GuiaConvenio) para UMA sessão do atendimento — uma
    guia já aberta do paciente, ou cadastrar a nova que o plano liberou. Saldo e validade são
    conferidos de novo, com a guia travada, na view (agenda.views.agendamento_autorizar)."""

    NOVA = "nova"

    guia = forms.ChoiceField(
        label="Guia do plano",
        error_messages={
            "invalid_choice": "Esta guia não pode mais ser usada nesta sessão (esgotada, vencida ou sem saldo "
            "para a sessão inteira) — escolha outra ou cadastre a guia nova liberada pelo plano.",
        },
    )
    novo_numero = forms.CharField(label="Número da guia nova", max_length=40, required=False)
    nova_quantidade = forms.IntegerField(label="Sessões liberadas", min_value=1, required=False)
    nova_validade = forms.DateField(
        label="Válida até (opcional)", required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"), input_formats=["%Y-%m-%d"],
    )

    def __init__(self, *args, item, guias_abertas, **kwargs):
        super().__init__(*args, **kwargs)
        self.item = item
        opcoes = [
            (str(guia.pk), f"Guia {guia.numero} — usadas {guia.sessoes_usadas} de {guia.quantidade_sessoes}"
             + (f" (válida até {guia.validade:%d/%m/%Y})" if guia.validade else ""))
            for guia in guias_abertas
        ]
        self.fields["guia"].choices = opcoes + [(self.NOVA, "+ Cadastrar guia nova")]
        if not self.is_bound:
            self.initial["guia"] = opcoes[0][0] if opcoes else self.NOVA

    def clean(self):
        dados = super().clean()
        if dados.get("guia") == self.NOVA:
            if not dados.get("novo_numero"):
                self.add_error("novo_numero", "Informe o número da guia nova.")
            quantidade = dados.get("nova_quantidade")
            if not quantidade:
                self.add_error("nova_quantidade", "Informe quantas sessões o plano liberou.")
            elif quantidade < self.item.quantidade:
                self.add_error(
                    "nova_quantidade",
                    f"Esta sessão usa {self.item.quantidade} sessões da guia — a guia precisa liberar pelo menos isso.",
                )
        return dados


class PacoteManualForm(EstiloCampoMixin, forms.Form):
    """Pacote do sistema antigo cadastrado direto no paciente, sem atendimento de venda (ver
    PacoteVendido.cadastro_manual) — agenda.views.pacote_cadastrar_manual. As sessões já usadas
    de cada procedimento vêm à parte no POST (usadas_<PacoteItem.pk>), conforme o pacote."""

    A_PAGAR = "a_pagar"
    PAGO = "pago"

    paciente = forms.ModelChoiceField(
        queryset=Paciente.objects.filter(ativo=True),
        widget=forms.HiddenInput,
        error_messages={"required": "Escolha o paciente."},
    )
    profissional = forms.ModelChoiceField(queryset=Profissional.objects.filter(ativo=True).order_by("nome"))
    pacote = forms.ModelChoiceField(queryset=Pacote.objects.filter(ativo=True).order_by("nome"))
    valor = forms.DecimalField(label="Valor do pacote (R$)", max_digits=8, decimal_places=2, min_value=0)
    situacao = forms.ChoiceField(
        label="Pagamento",
        choices=[
            (A_PAGAR, "A pagar — o paciente paga depois (o financeiro cobra num próximo atendimento)"),
            (PAGO, "Já pago no sistema antigo — não entra no caixa"),
        ],
        initial=A_PAGAR,
        widget=forms.RadioSelect,
    )

    def clean(self):
        dados = super().clean()
        pacote, profissional = dados.get("pacote"), dados.get("profissional")
        if pacote and profissional and pacote.profissional_id and pacote.profissional_id != profissional.pk:
            self.add_error("pacote", f"Este pacote é só de {pacote.profissional.nome}.")
        return dados


class GuiaConvenioForm(EstiloCampoMixin, forms.ModelForm):
    """Correção de uma guia de sessões já cadastrada (número digitado errado, plano liberou mais
    sessões, validade) — agenda.views.guia_convenio_editar."""

    class Meta:
        model = GuiaConvenio
        fields = ["numero", "quantidade_sessoes", "validade", "observacoes"]
        widgets = {
            "validade": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "observacoes": forms.Textarea(attrs={"rows": 2}),
        }

    def clean_quantidade_sessoes(self):
        quantidade = self.cleaned_data["quantidade_sessoes"]
        usadas = self.instance.sessoes_usadas if self.instance.pk else 0
        if quantidade < usadas:
            raise forms.ValidationError(f"Esta guia já tem {usadas} sessões usadas — não dá para liberar menos que isso.")
        return quantidade
