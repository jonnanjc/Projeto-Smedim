"""
Configurações do projeto — Sistema de Gestão da Clínica SMEDIM.

Etapa 2: Login e Perfis de Acesso.

As configurações sensíveis (senha de banco, chave secreta etc.) são lidas de
variáveis de ambiente / arquivo .env, para não ficarem expostas no código-fonte.
Veja o arquivo .env.example na raiz do projeto.
"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(nome, padrao=None):
    return os.environ.get(nome, padrao)


def env_bool(nome, padrao=False):
    valor = env(nome)
    if valor is None:
        return padrao
    return valor.strip().lower() in ("1", "true", "sim", "yes")


def env_int(nome, padrao):
    valor = env(nome)
    try:
        return int(valor) if valor is not None else padrao
    except ValueError:
        return padrao


# --- Segurança básica -------------------------------------------------------

SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    "chave-de-desenvolvimento-NAO-use-em-producao-troque-no-.env",
)

DEBUG = env_bool("DJANGO_DEBUG", True)

ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()]


# --- Apps --------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "cadastros",
    "fila",
    "agenda",
    "mensagens",
    "prontuario",
    "financeiro",
    "documentos",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Middleware próprio: força a troca de senha quando o usuário está
    # marcado com "exigir_troca_senha" (ex.: senha provisória do administrador).
    "accounts.middleware.ForcarTrocaSenhaMiddleware",
    # Atualiza "última atividade" do usuário logado, usado pelo chat interno
    # (mensagens) para mostrar quem está online.
    "accounts.middleware.AtualizarUltimoAcessoMiddleware",
    # Impede que o navegador guarde páginas em cache (ex.: reexibir tela
    # antiga ao clicar em "voltar") — dados da clínica mudam o tempo todo.
    "accounts.middleware.DesabilitarCacheMiddleware",
    # Uma vez por dia: agendados de hoje viram Pré-confirmado e os não confirmados de
    # dias anteriores viram Faltou (agenda.models.atualizar_status_do_dia).
    "agenda.middleware.AtualizarStatusDoDiaMiddleware",
]

# Liga/desliga o middleware acima. Desligado em "manage.py test": a varredura roda uma vez
# por processo e mudaria o status dos agendamentos de teste conforme a ordem dos testes —
# os testes da regra chamam agenda.models.atualizar_status_do_dia(forcar=True) direto.
AGENDA_STATUS_AUTOMATICO = env_bool("AGENDA_STATUS_AUTOMATICO", True) and sys.argv[1:2] != ["test"]

# Quantos retornos grátis cada profissional atende por dia (pedido da clínica, 23/10/2026).
# Retorno além disso é marcado como encaixe sozinho, pra ser justificado depois (ver
# agenda.forms.AgendamentoItemFormSet.clean). Ajustável no .env sem mexer no código.
AGENDA_RETORNOS_POR_DIA = env_int("AGENDA_RETORNOS_POR_DIA", 5)

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.tema_do_mes",
                "accounts.context_processors.favoritos_resolvidos",
                "mensagens.context_processors.mensagens_nao_lidas",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


# --- Banco de dados ------------------------------------------------------
#
# Por padrão usa SQLite (zero configuração), ótimo para desenvolvimento e
# para os primeiros testes. Para produção no servidor Windows da clínica,
# defina DB_ENGINE=postgres no .env (veja README.md).

if env("DB_ENGINE", "sqlite") == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME", "clinica_smedim"),
            "USER": env("DB_USER", "clinica_smedim"),
            "PASSWORD": env("DB_PASSWORD", ""),
            "HOST": env("DB_HOST", "localhost"),
            "PORT": env("DB_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# --- Banco de dados do sistema ANTIGO da clínica (migração) ------------------
#
# Conexão só-leitura usada pelos comandos de importação (ver
# cadastros/management/commands/inspecionar_legado.py e importar_pacientes_legado.py,
# prontuario/management/commands/importar_prontuario_legado.py) para trazer
# pacientes e prontuário do sistema PostgreSQL atual da clínica para cá. Os
# dois sistemas devem conviver por um tempo, então a importação é pensada
# para rodar mais de uma vez sem duplicar dados — ver origem_legado_id em
# cadastros.models.Paciente e prontuario.models.EntradaProntuario.
#
# Só é registrada se DB_LEGADO_NAME estiver preenchido no .env — não afeta em
# nada a conexão "default" acima nem o funcionamento normal do sistema.
if env("DB_LEGADO_NAME"):
    DATABASES["legado"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_LEGADO_NAME"),
        "USER": env("DB_LEGADO_USER", "postgres"),
        "PASSWORD": env("DB_LEGADO_PASSWORD", ""),
        "HOST": env("DB_LEGADO_HOST", "localhost"),
        "PORT": env("DB_LEGADO_PORT", "5432"),
    }


# --- Modelo de usuário customizado -------------------------------------------

AUTH_USER_MODEL = "accounts.Usuario"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# --- Login / sessão (regras definidas no documento da Etapa 2) --------------

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

# Quantidade de tentativas inválidas seguidas antes de bloquear o usuário.
MAX_TENTATIVAS_LOGIN = env_int("MAX_TENTATIVAS_LOGIN", 5)

# Tempo de inatividade (em minutos) até a sessão expirar automaticamente.
SESSAO_TIMEOUT_MINUTOS = env_int("SESSAO_TIMEOUT_MINUTOS", 60)
SESSION_COOKIE_AGE = SESSAO_TIMEOUT_MINUTOS * 60
# Renova o prazo da sessão a cada requisição — efetivamente vira um timeout
# "por inatividade" e não um prazo fixo desde o login.
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = False


# --- Internacionalização ------------------------------------------------

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True


# --- Fila / Senha (totem de autoatendimento da recepção) -------------------
#
# O tamanho do papel do ticket não é configurado aqui: vem do papel padrão do
# driver da impressora no PC do totem (ELGIN i8 em "80(72.1) x 3276 mm") —
# ver o comentário do @page em fila/totem_senha.html.

# Quantos segundos a tela de "sua senha é..." fica visível antes de voltar
# sozinha para a tela inicial do totem.
FILA_TOTEM_REDIRECT_SEGUNDOS = env_int("FILA_TOTEM_REDIRECT_SEGUNDOS", 8)

# Modelo de voz (Piper) do painel de chamada — ver fila/voz.py. Fica fora do pacote de
# atualização (63 MB): baixado uma vez no servidor com
#   venv/bin/python -m piper.download_voices pt_BR-faber-medium --download-dir voz
# Sem ele (ou sem o pacote piper-tts), o painel usa a voz do navegador.
PAINEL_VOZ_MODELO = env("PAINEL_VOZ_MODELO", str(BASE_DIR / "voz" / "pt_BR-faber-medium.onnx"))
# Lentidão da fala (Piper length_scale): 1.0 = normal; maior = mais devagar. A clínica
# achou 1.1 rápido demais na chamada do paciente (2026-10-16).
try:
    PAINEL_VOZ_VELOCIDADE = float(env("PAINEL_VOZ_VELOCIDADE", "1.4").replace(",", "."))
except ValueError:
    PAINEL_VOZ_VELOCIDADE = 1.4
if sys.argv[1:2] == ["test"]:
    PAINEL_VOZ_MODELO = ""  # testes nunca carregam o modelo de 63 MB (fila/tests.py usa mock)


# --- Arquivos estáticos ---------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- Arquivos enviados por usuários (ex.: foto de perfil) -----------------
#
# Diferente de STATIC_ROOT (arquivos do próprio código, versionados), MEDIA_ROOT
# guarda o que as pessoas enviam pelo sistema — por isso não faz parte do pacote
# de atualização (ver config/urls.py, que serve isto direto pelo Django, sem
# depender de configuração nova no Nginx).
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
