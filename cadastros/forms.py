from django import forms
from django.db.models import Q

from accounts.moeda import preparar_campos_moeda

from .models import (
    Convenio,
    Guiche,
    LimiteDiarioProcedimento,
    Pacote,
    PacoteItem,
    Paciente,
    Procedimento,
    Profissional,
    ProfissionalEspecialidade,
    Sala,
    TabelaPrecoConvenio,
    ValorFixoProfissional,
)


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


class PacienteForm(EstiloCampoMixin, forms.ModelForm):
    class Meta:
        model = Paciente
        # Por pedido do cliente: e-mail, bairro e dados do responsável saíram da
        # tela de cadastro (continuam existindo na tabela, só não aparecem aqui).
        fields = [
            "nome_completo",
            "cpf",
            "rg",
            "data_nascimento",
            "telefone",
            "cidade",
            "convenio",
            "numero_carteirinha",
            "validade_carteirinha",
            "ativo",
        ]
        # format="%Y-%m-%d" é obrigatório: o <input type="date"> só aceita data ISO. Sem
        # ele o Django escrevia a data no formato do idioma (dd/mm/aaaa), o navegador
        # mostrava o campo vazio e, ao salvar a edição por outro motivo (ex.: incluir o
        # convênio), a data de nascimento era apagada — relatado em 06/11/2026.
        widgets = {
            "data_nascimento": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "validade_carteirinha": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }
        labels = {
            "validade_carteirinha": "Validade da carteirinha (opcional)",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Fichas já existentes com o mesmo nome (ver clean) — mostradas no aviso da tela.
        self.duplicados = []
        if self.is_bound and not self.instance.pk:
            self.fields["confirmar_outra_pessoa"] = forms.BooleanField(
                label="Conferi: é outra pessoa, cadastrar mesmo assim",
                required=False,
            )

    def clean(self):
        """Evita duplicado novo (pedido da clínica, 06/11/2026): no cadastro de paciente NOVO,
        se já existe ficha com o mesmo nome (sem diferenciar acento/maiúscula), avisa e só
        salva depois de marcar "é outra pessoa". Na edição não pergunta."""
        dados = super().clean()
        if "confirmar_outra_pessoa" not in self.fields:
            return dados
        from .busca_pacientes import buscar_ids_pacientes
        from .unificacao import nome_normalizado

        chave = nome_normalizado(dados.get("nome_completo"))
        if chave:
            # Mesma busca sem acento da caixa de paciente (índice em memória, não varre o
            # banco); a comparação final é pelo nome normalizado inteiro.
            ids = buscar_ids_pacientes(chave, Q(unificado_em__isnull=True), "cadastro_duplicados")
            candidatos = Paciente.objects.filter(pk__in=ids[:200]).select_related("convenio").order_by("pk")
            self.duplicados = [p for p in candidatos if nome_normalizado(p.nome_completo) == chave][:10]
        if self.duplicados and not dados.get("confirmar_outra_pessoa"):
            nascimento = dados.get("data_nascimento")
            mesmo_nascimento = nascimento and any(p.data_nascimento == nascimento for p in self.duplicados)
            self.add_error(
                None,
                "Já existe paciente com este nome"
                + (" e esta mesma data de nascimento" if mesmo_nascimento else "")
                + " (veja abaixo). Se for a mesma pessoa, use o cadastro que já existe (e atualize o telefone "
                "nele, se mudou). Se for outra pessoa, marque \"é outra pessoa\" e salve de novo.",
            )
        elif not self.duplicados:
            self.fields.pop("confirmar_outra_pessoa", None)
        return dados


class ProfissionalForm(EstiloCampoMixin, forms.ModelForm):
    class Meta:
        model = Profissional
        fields = [
            "nome",
            "registro_profissional",
            "especialidade",
            "especialidade_outra",
            # "cbo_codigo" fica de fora do formulário de propósito — é
            # preenchido sozinho no save() do modelo, conforme a especialidade.
            "percentual_desconto",
            "cobra_valor_fixo_particular",
            "limite_atendimento_simultaneo",
            "acupuntura_primeiro_na_fila",
            "eh_proprietario",
            "convenios_atendidos",
            "ativo",
        ]
        widgets = {"convenios_atendidos": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Convênio desativado continua aparecendo se já estiver marcado (pra não sumir calado).
        filtro = Q(ativo=True)
        if self.instance.pk:
            filtro |= Q(pk__in=self.instance.convenios_atendidos.values("pk"))
        self.fields["convenios_atendidos"].queryset = Convenio.objects.filter(filtro).order_by("nome")

    def clean(self):
        dados = super().clean()
        if dados.get("especialidade") == "outra" and not dados.get("especialidade_outra"):
            self.add_error("especialidade_outra", "Informe qual é a especialidade.")
        return dados


class ProfissionalEspecialidadeForm(EstiloCampoMixin, forms.ModelForm):
    """Uma linha (especialidade adicional) do formset abaixo — mesmo espírito de
    AgendamentoItemForm (agenda/forms.py)."""

    class Meta:
        model = ProfissionalEspecialidade
        fields = ["especialidade", "especialidade_outra"]

    def clean(self):
        dados = super().clean()
        if dados.get("especialidade") == "outra" and not dados.get("especialidade_outra"):
            self.add_error("especialidade_outra", "Informe qual é a especialidade.")
        return dados


class _ProfissionalEspecialidadeFormSetBase(forms.BaseInlineFormSet):
    """
    Duplicar a mesma especialidade entre duas linhas ADICIONAIS já é barrado pela
    validação automática de UniqueConstraint em formsets (mesmo raciocínio de
    _AgendamentoItemFormSetBase, em agenda/forms.py — não precisa reforçar aqui).
    O que a constraint do banco não enxerga é uma linha adicional repetir a
    especialidade PRINCIPAL (campos do Profissional, tabela diferente) — só esse
    caso precisa de checagem manual, contra o ProfissionalForm já validado.
    """

    def __init__(self, *args, profissional_form=None, **kwargs):
        self.profissional_form = profissional_form
        super().__init__(*args, **kwargs)

    def clean(self):
        super().clean()
        if any(self.errors) or self.profissional_form is None:
            return
        dados_prof = self.profissional_form.cleaned_data
        especialidade_principal = dados_prof.get("especialidade")
        outra_principal = dados_prof.get("especialidade_outra") if especialidade_principal == "outra" else ""

        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            especialidade = form.cleaned_data.get("especialidade")
            outra = form.cleaned_data.get("especialidade_outra") if especialidade == "outra" else ""
            if especialidade and especialidade_principal and especialidade == especialidade_principal and outra == outra_principal:
                form.add_error(
                    "especialidade",
                    "Esta já é a especialidade principal do profissional — escolha outra, ou remova esta linha.",
                )


# extra=0 e sem min_num (diferente de AgendamentoItemFormSet): estas linhas são
# puramente opcionais — o profissional já tem a especialidade principal nos campos
# de cima, "adicionar especialidade" é sempre por escolha de quem cadastra.
ProfissionalEspecialidadeFormSet = forms.inlineformset_factory(
    Profissional,
    ProfissionalEspecialidade,
    form=ProfissionalEspecialidadeForm,
    formset=_ProfissionalEspecialidadeFormSetBase,
    extra=0,
    can_delete=True,
)


class ValorFixoProfissionalForm(EstiloCampoMixin, forms.ModelForm):
    """Uma linha da tabela 'Valores fixos no particular' do profissional (ver
    cadastros.models.ValorFixoProfissional)."""

    class Meta:
        model = ValorFixoProfissional
        fields = ["procedimento", "valor_cobrado", "valor_repasse"]

    def __init__(self, *args, profissional=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Só os procedimentos habilitados para ESTE profissional (Cadastros → Procedimentos —
        # pedido da clínica, 15/10/2026: antes vinha a tabela inteira da clínica). Profissional
        # ainda não salvo não tem nenhum. Linha já salva com procedimento que deixou de ser
        # habilitado continua aparecendo, pra não sumir calada.
        filtro = Q(ativo=True, profissionais_habilitados=profissional) if profissional and profissional.pk else Q(pk__in=[])
        if self.instance.pk and self.instance.procedimento_id:
            filtro |= Q(pk=self.instance.procedimento_id)
        self.fields["procedimento"].queryset = Procedimento.objects.filter(filtro).distinct().order_by("nome")


class _ValorFixoProfissionalFormSetBase(forms.BaseInlineFormSet):
    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["profissional"] = self.instance
        return kwargs


# Mesmo formato do formset de especialidades adicionais: linhas opcionais, adicionadas
# por JS na tela. Procedimento repetido é barrado pela UniqueConstraint do model.
ValorFixoProfissionalFormSet = forms.inlineformset_factory(
    Profissional,
    ValorFixoProfissional,
    form=ValorFixoProfissionalForm,
    formset=_ValorFixoProfissionalFormSetBase,
    extra=0,
    can_delete=True,
)


class ProcedimentoForm(EstiloCampoMixin, forms.ModelForm):
    class Meta:
        model = Procedimento
        fields = [
            "nome",
            "codigo",
            "profissionais_habilitados",
            "recursos_necessarios",
            "permite_repeticao_semanal",
            "eh_ultrassom",
            "eh_consulta",
            "exame_sem_profissional",
            "exame_com_preparo",
            "valor_particular",
            "ativo",
        ]
        widgets = {
            "profissionais_habilitados": forms.CheckboxSelectMultiple,
        }


class ConvenioForm(EstiloCampoMixin, forms.ModelForm):
    class Meta:
        model = Convenio
        fields = [
            "nome",
            "codigo_ans",
            "prazo_pagamento_dias",
            "prazo_repasse_dias",
            "prazo_retorno_dias",
            "permite_pagamento_na_hora",
            "funciona_como_particular",
            "ativo",
        ]


class TabelaPrecoForm(EstiloCampoMixin, forms.ModelForm):
    class Meta:
        model = TabelaPrecoConvenio
        fields = ["procedimento", "profissional", "valor"]
        labels = {"profissional": "Profissional (opcional — só se o valor for diferente para ele)"}

    def __init__(self, *args, convenio=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["profissional"].required = False
        self._convenio = convenio

    def clean(self):
        dados = super().clean()
        convenio = self._convenio or getattr(self.instance, "convenio", None)
        procedimento = dados.get("procedimento")
        profissional = dados.get("profissional")
        # Preço específico de um profissional só para procedimento vinculado a
        # ele ("profissionais habilitados" em Cadastros → Procedimentos) — mesmo
        # critério da lista filtrada no form_generico.html. Linha antiga já salva
        # com a mesma combinação continua podendo ser editada (ex.: só o valor).
        if profissional and procedimento:
            combinacao_ja_salva = (
                self.instance.pk
                and self.instance.procedimento_id == procedimento.pk
                and self.instance.profissional_id == profissional.pk
            )
            vinculado = profissional.procedimentos_habilitados.filter(pk=procedimento.pk).exists()
            if not vinculado and not combinacao_ja_salva:
                self.add_error(
                    "procedimento",
                    f"{procedimento} não está vinculado a {profissional} "
                    "(Cadastros → Procedimentos → profissionais habilitados).",
                )
        if convenio and procedimento:
            existentes = TabelaPrecoConvenio.objects.filter(convenio=convenio, procedimento=procedimento, profissional=profissional)
            if self.instance.pk:
                existentes = existentes.exclude(pk=self.instance.pk)
            if existentes.exists():
                if profissional:
                    self.add_error("profissional", f"Já existe um preço para este procedimento com {profissional} neste convênio.")
                else:
                    self.add_error("procedimento", "Já existe um preço geral para este procedimento neste convênio.")
        return dados


class ImportarTabelaPrecoForm(EstiloCampoMixin, forms.Form):
    arquivo = forms.FileField(
        label="Planilha (.xlsx ou .csv)",
        help_text="Colunas esperadas: Procedimento, Código (opcional), Profissional (opcional) e Valor.",
    )


class ImportarProcedimentosForm(EstiloCampoMixin, forms.Form):
    arquivo = forms.FileField(
        label="Planilha (.xlsx ou .csv)",
        help_text="Colunas esperadas: Procedimento (ou Nome), Código (opcional) e Valor particular (opcional).",
    )


class SalaForm(EstiloCampoMixin, forms.ModelForm):
    class Meta:
        model = Sala
        fields = ["nome", "lugares_recepcao_propria", "ativa"]


class GuicheForm(EstiloCampoMixin, forms.ModelForm):
    class Meta:
        model = Guiche
        fields = ["nome", "ativo"]


class LimiteDiarioProcedimentoForm(EstiloCampoMixin, forms.ModelForm):
    class Meta:
        model = LimiteDiarioProcedimento
        fields = ["profissional", "procedimento", "todos_ultrassons", "convenio", "limite_diario"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["profissional"].queryset = Profissional.objects.filter(ativo=True).order_by("nome")
        # Só os procedimentos vinculados ao profissional escolhido (campo
        # "profissionais habilitados" em Cadastros → Procedimentos) — sem
        # fallback pro cadastro inteiro, mesmo critério das telas de Horários
        # (ver cadastros.views._procedimentos_do_profissional_para_bloco).
        # Sem profissional escolhido ainda, a lista começa vazia e o JavaScript
        # do form_generico.html a preenche ao escolher o profissional.
        self.fields["procedimento"].queryset = self._procedimentos_do_profissional()
        self.fields["procedimento"].required = False
        self.fields["convenio"].queryset = Convenio.objects.filter(ativo=True).order_by("nome")
        self.fields["convenio"].required = False

    def _procedimentos_do_profissional(self):
        if self.is_bound:
            profissional_id = self.data.get(self.add_prefix("profissional"))
        elif self.instance.pk:
            profissional_id = self.instance.profissional_id
        else:
            profissional_id = self.initial.get("profissional")
        try:
            profissional_id = int(profissional_id)
        except (TypeError, ValueError):
            return Procedimento.objects.none()
        filtro = Q(ativo=True, profissionais_habilitados__pk=profissional_id)
        if self.instance.pk and self.instance.procedimento_id and self.instance.profissional_id == profissional_id:
            # Editando um limite antigo cujo vínculo mudou depois: não deixa o
            # procedimento já salvo sumir do select.
            filtro |= Q(pk=self.instance.procedimento_id)
        return Procedimento.objects.filter(filtro).distinct().order_by("nome")

    def clean(self):
        dados = super().clean()
        profissional = dados.get("profissional")
        if dados.get("todos_ultrassons") and dados.get("procedimento"):
            self.add_error(
                "procedimento",
                "Com \"todos os ultrassons\" marcado, deixe o procedimento em branco — o limite vale para todos os USG.",
            )
        if profissional:
            # 'procedimento' e 'convenio' agora são opcionais e combináveis (ver
            # cadastros.models.LimiteDiarioProcedimento) — a checagem de duplicidade
            # precisa ser pela combinação exata, não só profissional+procedimento.
            existentes = LimiteDiarioProcedimento.objects.filter(
                profissional=profissional,
                procedimento=dados.get("procedimento"),
                todos_ultrassons=bool(dados.get("todos_ultrassons")),
                convenio=dados.get("convenio"),
            )
            if self.instance.pk:
                existentes = existentes.exclude(pk=self.instance.pk)
            if existentes.exists():
                self.add_error(None, "Já existe um limite cadastrado para esta combinação de profissional, procedimento e convênio.")
        return dados


class PacoteForm(EstiloCampoMixin, forms.ModelForm):
    """Cadastro de pacote/combo particular (ver cadastros.models.Pacote) — os itens vêm no
    PacoteItemFormSet logo abaixo."""

    class Meta:
        model = Pacote
        fields = ["nome", "profissional", "valor", "valor_repasse", "ativo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["profissional"].queryset = Profissional.objects.filter(ativo=True).order_by("nome")
        self.fields["profissional"].empty_label = "Qualquer profissional"


class PacoteItemForm(EstiloCampoMixin, forms.ModelForm):
    class Meta:
        model = PacoteItem
        fields = ["procedimento", "quantidade"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["procedimento"].queryset = Procedimento.objects.filter(ativo=True).order_by("nome")


class _PacoteItemFormSetBase(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        procedimentos = [
            form.cleaned_data["procedimento"]
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE") and form.cleaned_data.get("procedimento")
        ]
        if not procedimentos:
            raise forms.ValidationError("Adicione pelo menos um procedimento ao pacote.")
        # Pacote preso a um profissional: tudo nele precisa estar habilitado para ele em
        # Cadastros → Procedimentos (pedido do cliente, 2026-10-14). Pacote de "qualquer
        # profissional" é conferido na venda (agenda.views._pacotes_a_venda).
        profissional = self.instance.profissional if self.instance.profissional_id else None
        if profissional:
            habilitados = set(profissional.procedimentos_habilitados.values_list("pk", flat=True))
            fora = [p.nome for p in procedimentos if p.pk not in habilitados]
            if fora:
                raise forms.ValidationError(
                    f"{profissional.nome} não está habilitado para: {', '.join(fora)} (confira em Cadastros → "
                    "Procedimentos → 'profissionais habilitados'). O pacote só pode ter procedimentos que ele realiza."
                )


# Procedimento repetido no mesmo pacote é barrado pela UniqueConstraint do model.
PacoteItemFormSet = forms.inlineformset_factory(
    Pacote,
    PacoteItem,
    form=PacoteItemForm,
    formset=_PacoteItemFormSetBase,
    extra=0,
    can_delete=True,
)
