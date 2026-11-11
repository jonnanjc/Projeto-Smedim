from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.models import Group

Usuario = get_user_model()

# Senha inicial aplicada a todo usuário criado pela tela de "Novo usuário".
# Como "exigir_troca_senha" já vem marcado por padrão, a pessoa é obrigada a
# trocar essa senha no primeiro login — então não tem problema ela ser fixa.
SENHA_PADRAO_NOVO_USUARIO = "smedim20"


class EstiloCampoMixin:
    """Aplica a classe CSS 'campo' (usada em accounts/base.html) a todo campo de texto/número/data/seleção."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            if isinstance(campo.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                continue
            classes = campo.widget.attrs.get("class", "")
            campo.widget.attrs["class"] = (classes + " campo").strip()


class LoginForm(forms.Form):
    username = forms.CharField(
        label="Usuário",
        widget=forms.TextInput(attrs={"autofocus": True, "class": "campo", "placeholder": "seu.usuario"}),
    )
    password = forms.CharField(
        label="Senha",
        widget=forms.PasswordInput(attrs={"class": "campo", "placeholder": "••••••••"}),
    )


class MeuPerfilForm(forms.ModelForm):
    """
    Autoatendimento — qualquer usuário logado pode trocar a própria foto por
    aqui, sem precisar de 'accounts.usuario_gerenciar'. Os demais dados
    (nome, usuário, perfis) continuam exclusivos da tela de Usuários.
    """

    class Meta:
        model = Usuario
        fields = ["foto"]
        labels = {"foto": "Foto de perfil"}


class TrocarSenhaForm(SetPasswordForm):
    """Reaproveita a validação de senha padrão do Django (força mínima etc.)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_password1"].widget.attrs.update({"class": "campo"})
        self.fields["new_password2"].widget.attrs.update({"class": "campo"})


class EspecialidadesPadraoMixin:
    """
    Campo "especialidades que costuma cobrir" (recepção), compartilhado por
    UsuarioCriarForm e UsuarioEditarForm — baseline raramente alterado que só
    serve pra pré-marcar o formulário de "especialidades de hoje" (ver
    agenda.forms.EspecialidadesDoDiaForm / cadastros.models.
    AtendimentoEspecialidade), sem precisar selecionar tudo de novo todo dia.
    Não afeta permissões: quem pode usar isso é controlado por perfil
    ('fila_especialidade_configurar'), não por ter algo marcado aqui.

    As opções vêm de Profissional.especialidades_cadastradas() — mesma fonte
    do cadastro de profissional, nunca uma lista digitada à mão.
    """

    def _preparar_campo_especialidades_padrao(self):
        from cadastros.models import EspecialidadePadraoUsuario, Profissional

        self.fields["especialidades_padrao"] = forms.MultipleChoiceField(
            label="Especialidades que costuma cobrir (recepção)",
            required=False,
            widget=forms.CheckboxSelectMultiple,
            choices=Profissional.especialidades_cadastradas(),
            help_text="Só pré-marca o formulário de 'especialidades de hoje' — não concede nenhuma permissão por si só.",
        )
        if self.instance.pk:
            self.fields["especialidades_padrao"].initial = [
                f"{especialidade}|{especialidade_outra}"
                for especialidade, especialidade_outra in EspecialidadePadraoUsuario.especialidades_de(self.instance)
            ]

    def _salvar_especialidades_padrao(self, usuario):
        from cadastros.models import EspecialidadePadraoUsuario, especialidade_a_partir_da_chave

        pares = [especialidade_a_partir_da_chave(chave) for chave in self.cleaned_data["especialidades_padrao"]]
        EspecialidadePadraoUsuario.definir(usuario, pares)


class UsuarioCriarForm(EspecialidadesPadraoMixin, EstiloCampoMixin, forms.ModelForm):
    """
    Tela própria de 'Novo usuário' (fora do /admin/), para o sub-administrador
    cadastrar acessos. Não pede senha: todo usuário novo já entra com a senha
    padrão da clínica (ver SENHA_PADRAO_NOVO_USUARIO) e, como "exigir troca de
    senha" vem marcado, a pessoa troca por uma senha própria no primeiro login.
    """

    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Perfis de acesso",
        help_text="Selecione um ou mais perfis — eles definem o que este usuário pode fazer no sistema.",
    )

    class Meta:
        model = Usuario
        fields = ["username", "first_name", "last_name", "email", "status", "exigir_troca_senha", "groups"]
        labels = {
            "username": "Nome de usuário (login)",
            "first_name": "Nome",
            "last_name": "Sobrenome",
            "exigir_troca_senha": "Exigir troca de senha no primeiro acesso",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["exigir_troca_senha"].initial = True
        self._preparar_campo_especialidades_padrao()

    def save(self, commit=True):
        usuario = super().save(commit=False)
        usuario.set_password(SENHA_PADRAO_NOVO_USUARIO)
        if commit:
            usuario.save()
            usuario.groups.set(self.cleaned_data["groups"])
            self._salvar_especialidades_padrao(usuario)
        return usuario


class UsuarioEditarForm(EspecialidadesPadraoMixin, EstiloCampoMixin, forms.ModelForm):
    """Edição de um usuário já existente — sem mexer na senha (isso tem uma tela própria)."""

    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Perfis de acesso",
    )

    class Meta:
        model = Usuario
        fields = ["username", "first_name", "last_name", "email", "status", "exigir_troca_senha", "groups"]
        labels = {
            "username": "Nome de usuário (login)",
            "first_name": "Nome",
            "last_name": "Sobrenome",
            "exigir_troca_senha": "Exigir troca de senha no próximo acesso",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["groups"].initial = self.instance.groups.all()
        self._preparar_campo_especialidades_padrao()

    def save(self, commit=True):
        usuario = super().save(commit=commit)
        if commit:
            usuario.groups.set(self.cleaned_data["groups"])
            self._salvar_especialidades_padrao(usuario)
        return usuario


class RedefinirSenhaUsuarioForm(EstiloCampoMixin, SetPasswordForm):
    """Um administrador define uma nova senha PARA outro usuário (ex: esqueceu a senha)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_password1"].widget.attrs.update({"class": "campo"})
        self.fields["new_password2"].widget.attrs.update({"class": "campo"})


class PerfilForm(EstiloCampoMixin, forms.Form):
    """
    Só cuida do nome do perfil — as permissões (agrupadas por departamento)
    são renderizadas à parte, direto no template (ver
    accounts/templates/accounts/_permissoes_por_departamento.html), e lidas
    na view via request.POST.getlist('permissoes'), pra ter controle total
    do agrupamento visual sem depender da renderização padrão de um campo
    de formulário do Django.
    """

    nome = forms.CharField(label="Nome do perfil", max_length=150)
