# Sistema de Gestão — Clínica SMEDIM
## Etapa 2 — Login e Perfis de Acesso

Este é o primeiro código real do projeto: cadastro de usuários, perfis
(grupos) e permissões, tela de login com bloqueio por tentativas inválidas,
troca de senha obrigatória e log de auditoria — tudo conforme o documento
"Etapa 2 — Modelagem de Login e Perfis de Acesso" que você aprovou.

Para o **primeiro teste no seu PC**, o projeto já vem configurado para usar
SQLite (um banco de dados em um único arquivo, sem precisar instalar nada
de banco separado). Mais abaixo tem a seção de como migrar para PostgreSQL
quando for para o servidor da clínica.

---

## 1. O que baixar/instalar no seu PC para testar

Só duas coisas:

1. **Python 3.11 ou mais novo** — baixe em https://www.python.org/downloads/
   - No instalador do Windows, marque a caixinha **"Add python.exe to PATH"** antes de clicar em instalar.
2. **Este projeto** (a pasta `backend` que você recebeu).

Não precisa instalar PostgreSQL, nem Django "à parte" — o Django é instalado
via `pip` (gerenciador de pacotes do Python) em um ambiente isolado, no passo
3 abaixo.

## 2. Confirmar que o Python foi instalado

Abra o **Prompt de Comando** (cmd) ou **PowerShell** e digite:

```powershell
python --version
```

Deve aparecer algo como `Python 3.12.x`. Se der erro de "comando não
encontrado", reinstale o Python marcando a opção do PATH mencionada acima.

## 3. Criar o ambiente virtual e instalar as dependências

Dentro da pasta do projeto (`backend`), rode, um comando de cada vez:

```powershell
cd caminho\para\backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Você vai ver `(venv)` aparecer no início da linha do terminal — isso indica
que o ambiente virtual está ativo. Sempre que for rodar o projeto, ative o
ambiente virtual primeiro com `venv\Scripts\activate`.

## 4. Configurar o arquivo `.env`

Copie o arquivo `.env.example` e renomeie a cópia para `.env` (mesma pasta
do `manage.py`). Para o teste local, os valores padrão já funcionam sem
alterar nada (`DB_ENGINE=sqlite`).

## 5. Criar o banco de dados (tabelas)

```powershell
python manage.py migrate
```

Isso cria o arquivo `db.sqlite3` já com todas as tabelas de usuários,
perfis, permissões e log de auditoria.

## 6. Criar os perfis iniciais (Administrador, Médico, Recepção, Financeiro, Apoio clínico)

```powershell
python manage.py seed_perfis
```

## 7. Criar o primeiro usuário administrador

```powershell
python manage.py createsuperuser
```

Preencha usuário, e-mail (pode deixar em branco) e senha. Esse será o seu
usuário para entrar no Django Admin e cadastrar os demais usuários da clínica.

## 8. Rodar o sistema

```powershell
python manage.py runserver
```

Deixe este terminal aberto (é o "servidor" rodando). Agora abra o navegador em:

- **http://127.0.0.1:8000/** → tela de login do sistema
- **http://127.0.0.1:8000/admin/** → painel administrativo do Django, onde
  dá para cadastrar usuários, associar perfis (grupos) e ver o log de
  auditoria

## 9. Testando o fluxo

1. Entre em `/admin/` com o usuário administrador criado no passo 7.
2. Em **Usuários**, crie um novo usuário de teste (ex: recepção), marque
   o perfil "Recepção / Atendimento" na lista de grupos, e deixe marcada
   a opção "exigir troca de senha no próximo acesso".
3. Saia do admin e acesse `/` (tela de login) com o usuário de teste.
4. Ele deve ser redirecionado automaticamente para a tela de troca de senha
   antes de conseguir acessar qualquer outra página.
5. Para testar o bloqueio: digite a senha errada 5 vezes seguidas em um
   usuário — na 5ª tentativa a mensagem deve avisar que a conta foi
   bloqueada. Para desbloquear, volte no admin, selecione o usuário na
   lista, e use a ação "Desbloquear usuário(s) selecionado(s)".

---

## Migrando para PostgreSQL (quando for para o servidor da clínica)

**Importante:** o servidor da clínica roda **Linux**, não Windows — só o seu
PC de teste é Windows. O projeto funciona igual nos dois sistemas (Python e
Django são multiplataforma); a única parte que muda é a instalação do
PostgreSQL e a forma de manter o sistema rodando em segundo plano, descritas
a seguir.

1. No servidor Linux, instale o PostgreSQL (em distribuições baseadas em
   Debian/Ubuntu): `sudo apt update && sudo apt install postgresql`.
2. Crie o banco e o usuário do sistema:
   ```bash
   sudo -u postgres psql -c "CREATE DATABASE clinica_smedim;"
   sudo -u postgres psql -c "CREATE USER clinica_smedim WITH PASSWORD 'defina-uma-senha-forte';"
   sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE clinica_smedim TO clinica_smedim;"
   ```
3. No arquivo `.env` do servidor, altere:
   ```
   DB_ENGINE=postgres
   DB_NAME=clinica_smedim
   DB_USER=clinica_smedim
   DB_PASSWORD=<a senha que você definiu>
   DB_HOST=localhost
   DB_PORT=5432
   ```
4. Rode novamente `python manage.py migrate` e `python manage.py seed_perfis`
   (agora apontando para o PostgreSQL).

## Rodando em produção no servidor Linux da clínica

Isso é para quando o sistema for para o servidor de verdade, não para o
teste no seu PC Windows. O passo a passo completo, com os comandos exatos
(PostgreSQL, Gunicorn, systemd, Nginx, firewall e backup automático), está
em **[LEIA-ME-INSTALACAO-CLINICA.md](LEIA-ME-INSTALACAO-CLINICA.md)**.

---

## O que já existe nesta etapa

- Tela de login (`/`) com bloqueio automático após tentativas inválidas.
- Troca de senha obrigatória (`/trocar-senha/`) quando o administrador marca
  "exigir troca de senha".
- Perfis iniciais (Administrador, Médico/Profissional de saúde,
  Recepção/Atendimento, Financeiro, Apoio clínico) com permissões padrão,
  criados pelo comando `seed_perfis`.
- Log de auditoria (`/admin/accounts/logauditoria/`) registrando logins,
  logouts e trocas de senha.
- Painel administrativo (`/admin/`) para cadastrar usuários e gerenciar
  perfis/permissões, sem precisar de tela própria nesta fase.

## O que ainda não existe (próximas etapas)

- Cadastro de pacientes, profissionais, convênios e procedimentos (Etapa 3).
- Agendamento (Etapa 4), fila/senha com totem (Etapa 5) e financeiro (Etapa 6).
