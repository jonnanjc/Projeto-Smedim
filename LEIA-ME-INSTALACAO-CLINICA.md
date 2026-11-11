# Instalação em produção — servidor Ubuntu da clínica

Este guia é para o dia da instalação de verdade, no computador Ubuntu da
clínica — sem mim (Claude) presente para rodar comandos junto com você. Siga
os passos **na ordem**, um de cada vez, e leia o quadro "⚠️ Atenção" antes de
cada parte que mexe em algo compartilhado do servidor (porta, firewall,
nginx).

## O que este guia faz

- Instala o sistema **do zero, com banco de dados novo e vazio** — nenhum
  paciente, agendamento, prontuário ou usuário de teste que exista aqui no
  ambiente de desenvolvimento vai junto. O arquivo `db.sqlite3` daqui **não
  é copiado** para a clínica em nenhum passo abaixo; o servidor novo cria as
  tabelas na hora, em branco, com `migrate`.
- Deixa o sistema rodando permanentemente como serviço (reinicia sozinho se
  o servidor reiniciar ou o processo cair).
- Não mexe, para, reinicia ou reconfigura nada do que já roda hoje no
  endereço `192.168.18.3:8080` (o sistema atual da clínica, que está sendo
  substituído aos poucos). Esse sistema é um software diferente — o guia
  usa uma porta própria para não encostar nele.

## ⚠️ Antes de tocar no servidor: confirme isto

1. **Esse Ubuntu é a mesma máquina que responde em `192.168.18.3:8080`, ou é
   um computador diferente?** Se não tiver certeza, rode no próprio Ubuntu:
   ```bash
   hostname -I
   ```
   Se um dos IPs mostrados for `192.168.18.3`, é a **mesma máquina** — redobre
   a atenção nas checagens de porta abaixo. Se for outro IP, o risco de
   conflito é bem menor, mas siga as checagens do mesmo jeito (custa só
   um comando a mais).
2. **Nunca rode `stop`, `restart`, `disable` ou desinstale nada relacionado
   ao serviço que já existe** (mesmo que você descubra o nome dele). Decidir
   quando (ou se) o sistema antigo sai do ar é uma decisão separada, feita
   com calma depois que o SMEDIM novo estiver testado — não faz parte deste
   guia.
3. Antes de instalar qualquer coisa que "escuta" numa porta (o próprio
   sistema, o Nginx, etc.), confira o que já está em uso:
   ```bash
   sudo ss -tulpn | grep -E ':80 |:8080 |:5432 |:8001 '
   ```
   Se aparecer algo na porta que este guia planeja usar (80 ou 8001), troque
   por outra porta livre em todos os passos correspondentes (fica marcado
   com 🔧 onde a porta aparece).

---

## Checklist rápido (marque conforme for avançando)

- [ ] Parte 1 — Preparar o pacote do projeto no seu PC (sem venv/.env/banco)
- [ ] Parte 2 — Levar o pacote para o servidor Ubuntu
- [ ] Parte 3 — Instalar os programas necessários no Ubuntu
- [ ] Parte 4 — Organizar a pasta do projeto no servidor
- [ ] Parte 5 — Criar o banco de dados PostgreSQL
- [ ] Parte 6 — Ambiente virtual Python e dependências
- [ ] Parte 7 — Configurar o `.env` de produção
- [ ] Parte 8 — Criar as tabelas (zerado), perfis e o administrador
- [ ] Parte 9 — Teste rápido antes de produtizar
- [ ] Parte 10 — Gunicorn como serviço permanente (systemd)
- [ ] Parte 11 — Nginx na frente do sistema
- [ ] Parte 12 — Firewall (opcional, com cuidado)
- [ ] Parte 13 — Testar de outro computador da clínica
- [ ] Parte 14 — Backup automático diário
- [ ] Parte 15 — Problemas comuns
- [ ] (Opcional, depois) Importar pacientes/prontuário do sistema antigo — ver última seção do guia

---

## Parte 1 — Preparar o pacote do projeto no seu PC (Windows)

No seu PC, **não** vamos enviar a pasta `backend` inteira como está — ela tem
o ambiente virtual (`venv`), o banco de dados local com dado real
(`db.sqlite3` e os `.bak-*`) e o `.env` de desenvolvimento, que não devem ir
para a clínica. Faça uma cópia limpa primeiro.

Abra o PowerShell **na pasta "PROJETO SMEDIM"** (uma acima de `backend`) e
rode, uma linha de cada vez:

```powershell
Copy-Item -Recurse -Path "backend" -Destination "smedim-deploy"
Remove-Item -Recurse -Force "smedim-deploy\venv"
Remove-Item -Force "smedim-deploy\db.sqlite3" -ErrorAction SilentlyContinue
Remove-Item -Force "smedim-deploy\db.sqlite3.bak-*" -ErrorAction SilentlyContinue
Remove-Item -Force "smedim-deploy\.env" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "smedim-deploy\.claude" -ErrorAction SilentlyContinue
Get-ChildItem -Path "smedim-deploy" -Include "__pycache__" -Recurse | Remove-Item -Recurse -Force
Compress-Archive -Path "smedim-deploy\*" -DestinationPath "smedim-deploy.zip"
```

Isso cria `smedim-deploy.zip` (dentro de "PROJETO SMEDIM") já sem nada
sensível ou desnecessário. Confira rapidamente que `db.sqlite3` e `.env` não
aparecem se você abrir o zip.

## Parte 2 — Levar o pacote para o servidor Ubuntu

Três opções, use a que for mais fácil no seu caso:

- **AnyDesk** (se já tiver acesso remoto a este servidor por ele): use a
  transferência de arquivo do próprio AnyDesk para enviar `smedim-deploy.zip`.
  Ele costuma cair na pasta pessoal do usuário (`/home/SEU_USUARIO/`), não
  direto em `/tmp/` — depois de transferir, confirme onde caiu e mova:
  ```bash
  find / -iname "smedim-deploy.zip" 2>/dev/null
  mv ~/smedim-deploy.zip /tmp/
  ```
  (ajuste o caminho de origem no `mv` conforme o que o `find` mostrar, se
  for diferente de `~/`).
- **Pendrive**: copie `smedim-deploy.zip` para um pendrive, leve fisicamente
  até o computador Ubuntu da clínica, conecte o pendrive nele e copie o
  arquivo para `/tmp/`. O importante é terminar com o arquivo em
  `/tmp/smedim-deploy.zip`, que é onde o próximo passo espera encontrá-lo.
  Pelo terminal (Ubuntu Server, sem interface gráfica — se o seu Ubuntu tiver
  área de trabalho, pode simplesmente abrir o pendrive no gerenciador de
  arquivos e arrastar o arquivo para `/tmp/` em vez destes comandos):

  ```bash
  # 1. Veja os discos já conhecidos ANTES de conectar o pendrive
  lsblk

  # 2. Conecte o pendrive, espere uns 5 segundos, rode de novo
  lsblk
  ```

  Compare as duas listas: vai aparecer uma linha nova (ex: `sdb` com uma
  partição `sdb1`) que não existia antes — é o seu pendrive. Se a coluna
  `MOUNTPOINT` dessa partição já mostrar algo como `/media/seu-usuario/...`,
  o sistema já montou sozinho — pule direto para o passo 5. Se estiver em
  branco, monte manualmente (troque `sdb1` pelo nome real que apareceu para
  você):

  ```bash
  # 3. Crie uma pasta para montar o pendrive e monte nela
  sudo mkdir -p /mnt/pendrive
  sudo mount /dev/sdb1 /mnt/pendrive
  ```

  Se der erro de tipo de sistema de arquivos desconhecido (comum em pendrive
  formatado como exFAT pelo Windows, o padrão para pendrives grandes):

  ```bash
  sudo apt install -y exfatprogs
  sudo mount /dev/sdb1 /mnt/pendrive
  ```

  Com o pendrive montado (em `/mnt/pendrive`, ou em `/media/...` se montou
  sozinho no passo 2):

  ```bash
  # 4. Confirme que o zip está lá (ajuste o caminho se foi automontado em /media/...)
  ls /mnt/pendrive

  # 5. Copie para onde a Parte 3 espera encontrar
  cp /mnt/pendrive/smedim-deploy.zip /tmp/

  # 6. Confirme que copiou certo (deve mostrar o tamanho do arquivo)
  ls -lh /tmp/smedim-deploy.zip

  # 7. Só então desmonte com segurança, antes de tirar o pendrive da porta
  sudo umount /mnt/pendrive
  ```
- **SCP/SSH** (se já tiver acesso remoto ao servidor configurado): do
  PowerShell,
  ```powershell
  scp smedim-deploy.zip usuario@192.168.18.X:/tmp/
  ```
  trocando `usuario` e `192.168.18.X` pelos dados reais do servidor.

## Parte 3 — Instalar os programas necessários no Ubuntu

Já no terminal do Ubuntu (local ou via SSH), com um usuário que tenha
`sudo`:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip unzip nginx
```

(o PostgreSQL fica de fora desta lista de propósito — a versão que o Ubuntu
oferece aqui costuma ser antiga demais para o Django; ele é instalado à
parte na Parte 5, de um jeito que garante uma versão atual.)

Confirme a versão do Python antes de continuar:

```bash
python3 --version
```

Este projeto foi testado com **Django 5.2**, compatível com Python 3.10 a
3.13. Se aparecer `3.10`, `3.11`, `3.12` ou `3.13`, siga em frente. Se
aparecer uma versão muito mais nova (3.14+) ou muito mais antiga (3.9-), veja
a nota de troubleshooting na Parte 15 antes de continuar.

## Parte 4 — Organizar a pasta do projeto no servidor

```bash
sudo mkdir -p /opt/smedim
sudo chown $USER:$USER /opt/smedim
unzip /tmp/smedim-deploy.zip -d /opt/smedim/backend
find /opt/smedim/backend -type d -exec chmod 755 {} \;
cd /opt/smedim/backend
ls
```

O `find`/`chmod` acima corrige um problema que o `.zip` traz do Windows: como
o PowerShell não guarda os bits de execução de pasta do jeito que o Linux
espera, algumas pastas saem do `unzip` sem permissão de "entrar" nelas (dá
erro `PermissionError` mais à frente, ex: na Parte 8, `collectstatic`,
mesmo com o dono certo) — esse comando garante que todas as pastas do
projeto ficam com a permissão `755` (padrão, sem afetar os arquivos).

Você deve ver `manage.py`, `config/`, `accounts/`, `requirements.txt`,
`.env.example`, etc.

## Parte 5 — Instalar o PostgreSQL e criar o banco de dados

O sistema já vem pronto para usar PostgreSQL em produção (é o mesmo banco
usado nos testes de migração) — mas ele precisa ser **PostgreSQL 14 ou mais
novo** (exigência do Django). Em versões mais antigas do Ubuntu (20.04, por
exemplo) o pacote `postgresql` da própria distribuição instala a versão 12,
antiga demais — o app sobe, mas cai assim que tenta usar o banco, com o erro
`django.db.utils.NotSupportedError: PostgreSQL 14 or later is required`. Por
isso instalamos direto do repositório oficial do PostgreSQL (PGDG), que
sempre oferece uma versão atual, independente do Ubuntu.

Confira o "codinome" da sua versão do Ubuntu:
```bash
lsb_release -cs
```
Anote o que apareceu (ex: `focal`, `jammy`, `noble`). **Se esta instalação já
te deu sinais de mistura de versão do Ubuntu** (ver Parte 15), **não confie
neste comando** — use o nome da versão que os pacotes já instalados
realmente usam (aparece nas linhas do `apt update`, ex: `focal`), não o que
`lsb_release` disser.

```bash
sudo apt install -y curl ca-certificates
sudo install -d /usr/share/postgresql-common/pgdg
sudo curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc --fail https://www.postgresql.org/media/keys/ACCC4CF8.asc
```

Troque `NOME_DA_VERSAO` abaixo pelo nome anotado acima:
```bash
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt NOME_DA_VERSAO-pgdg main" | sudo tee /etc/apt/sources.list.d/pgdg.list
sudo apt update
sudo apt install -y postgresql-16
```

Confirme que instalou e ficou na porta padrão:
```bash
sudo pg_lsclusters
```
Deve aparecer uma linha como `16  main  5432  online  postgres  ...`.

Agora crie o banco e o usuário, com uma senha forte e **sua**, escolhida
agora (anote em lugar seguro — vai ser usada no `.env` daqui a pouco):

```bash
sudo -u postgres psql -c "CREATE DATABASE clinica_smedim;"
sudo -u postgres psql -c "CREATE USER clinica_smedim WITH PASSWORD 'TROQUE-POR-UMA-SENHA-FORTE';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE clinica_smedim TO clinica_smedim;"
sudo -u postgres psql -d clinica_smedim -c "GRANT ALL ON SCHEMA public TO clinica_smedim;"
```

Não precisa mexer em nenhum outro arquivo de configuração do PostgreSQL — a
conexão é local (do próprio servidor para ele mesmo), o que já é liberado por
padrão na instalação do Ubuntu.

**Se o `apt update` acima der 404 em `NOME_DA_VERSAO-pgdg Release`**: o
repositório oficial do Postgres não tem mais pacotes para uma versão tão
antiga/fora de suporte do Ubuntu (mesmo padrão de outros repositórios já
vistos neste guia). Não insista — se este servidor já tiver Docker instalado
(comum se ele também roda outros sistemas em container), é mais simples
rodar o PostgreSQL dentro de um container, sem depender de nenhum
repositório do sistema operacional:
```bash
sudo docker run -d --name smedim-postgres --restart unless-stopped -p 5432:5432 -e POSTGRES_DB=clinica_smedim -e POSTGRES_USER=clinica_smedim -e POSTGRES_PASSWORD='TROQUE-POR-UMA-SENHA-FORTE' postgres:16
```
Isso já cria o banco, o usuário e a senha sozinho (a imagem oficial faz
isso na primeira vez que sobe) — **não rode os comandos de CREATE
DATABASE/CREATE USER acima neste caso**, e o `.env` continua exatamente
igual (`DB_HOST=localhost`, `DB_PORT=5432`), já que o Docker mapeia a porta
do container para a mesma porta do host. Confirme com:
```bash
sudo docker ps
sudo docker logs smedim-postgres --tail 20
```
(deve aparecer `database system is ready to accept connections`). Na Parte
14 (backup), troque o `pg_dump` direto por `docker exec smedim-postgres
pg_dump -U clinica_smedim clinica_smedim` (sem `-t`, que estraga o arquivo), já que as ferramentas de cliente
do Postgres ficam dentro do container, não no host.

Se em algum momento futuro você precisar trocar de versão de novo (ex: já
tem um `postgresql-12` instalado e precisa migrar para o 16, como aconteceu
durante o desenvolvimento deste guia), o caminho é: `sudo pg_lsclusters` (ver
o que existe), `sudo pg_dropcluster VERSAO main --stop` (remover o cluster
antigo — só faça isso se ele estiver vazio/de teste, nunca com dado real),
`sudo apt purge -y postgresql-VERSAO postgresql-client-VERSAO postgresql`,
depois instalar a versão nova como acima.

## Parte 6 — Ambiente virtual Python e dependências

```bash
cd /opt/smedim/backend
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Você deve ver `(venv)` no início da linha do terminal. Mantenha esse mesmo
terminal aberto (com o venv ativo) para os próximos passos.

## Parte 7 — Configurar o `.env` de produção

```bash
cp .env.example .env
nano .env
```

Ajuste estas linhas (as outras podem ficar como estão):

```
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=192.168.18.X
DB_ENGINE=postgres
DB_NAME=clinica_smedim
DB_USER=clinica_smedim
DB_PASSWORD=<a mesma senha forte que você criou na Parte 5>
DB_HOST=localhost
DB_PORT=5432
```

Troque `192.168.18.X` pelo IP real deste servidor na rede da clínica
(o mesmo que apareceu no `hostname -I` lá na checagem inicial).

Para a `DJANGO_SECRET_KEY`, gere uma chave de verdade (não deixe o valor de
exemplo do `.env.example`):

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Copie o resultado e cole como valor de `DJANGO_SECRET_KEY` no `.env`
(dentro do `nano`). Deixe `DB_LEGADO_*` em branco por enquanto — isso é só
para quando o dump do sistema antigo chegar, é um passo futuro separado.

Depois de salvar (`Ctrl+O`, `Enter`, `Ctrl+X` no nano), proteja o arquivo:

```bash
chmod 600 .env
```

## Parte 8 — Criar as tabelas (banco zerado), perfis e o administrador

Com o venv ainda ativo:

```bash
python manage.py migrate
python manage.py seed_perfis
python manage.py createsuperuser
python manage.py collectstatic --noinput
```

- `migrate` cria todas as tabelas vazias no PostgreSQL que você criou na
  Parte 5 — nenhum paciente, agendamento ou usuário antigo entra aqui.
- `seed_perfis` cria os perfis padrão (Administrador, Médico/Profissional de
  saúde, Recepção/Atendimento, Financeiro, Apoio clínico) com as permissões
  já definidas no sistema.
- `createsuperuser` pede um usuário e senha — esse será o **primeiro** login
  da clínica, use para depois cadastrar os demais usuários reais pelo
  `/admin/` ou pelas telas de Usuários do próprio sistema.
- `collectstatic` junta os arquivos de estilo/imagem num lugar só
  (`staticfiles/`), que o Nginx vai servir na Parte 11.

## Parte 9 — Teste rápido antes de produtizar

Ainda com o venv ativo, suba o servidor de teste do Django por um instante:

```bash
python manage.py runserver 0.0.0.0:8001
```

De outro computador na rede da clínica, abra o navegador em
`http://192.168.18.X:8001/` (o IP deste servidor) e confirme que a tela de
login aparece e que dá para entrar com o usuário criado na Parte 8. Depois
de confirmar, volte ao terminal e aperte `Ctrl+C` para parar — este comando
é só um teste manual, o serviço de verdade vem na Parte 10.

## Parte 10 — Gunicorn como serviço permanente (systemd)

Primeiro, entregue a pasta do projeto para o usuário que vai rodar o
serviço (o mesmo `www-data` que o Nginx já usa por padrão no Ubuntu):

```bash
deactivate
sudo chown -R www-data:www-data /opt/smedim
```

Crie o arquivo de serviço:

```bash
sudo nano /etc/systemd/system/smedim.service
```

Cole exatamente isto (🔧 troque `8001` se essa porta já estiver em uso,
conforme a checagem do início do guia):

```ini
[Unit]
Description=SMEDIM - servidor de aplicacao (Gunicorn)
After=network.target postgresql.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/smedim/backend
ExecStart=/opt/smedim/backend/venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8001 --workers 3 --access-logfile - --error-logfile -
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Ative e inicie:

```bash
sudo systemctl daemon-reload
sudo systemctl enable smedim
sudo systemctl start smedim
sudo systemctl status smedim
```

`status` deve mostrar `active (running)` em verde. Se não mostrar, veja a
Parte 15.

> Nota: `127.0.0.1:8001` aqui é só a porta **interna**, entre o Gunicorn e o
> Nginx — quem acessa pelo navegador não digita `:8001`, isso é resolvido no
> próximo passo pelo Nginx.

## Parte 11 — Nginx na frente do sistema

```bash
sudo nano /etc/nginx/sites-available/smedim
```

Cole (🔧 troque `192.168.18.X` pelo IP real do servidor, e `listen 80` pela
porta livre que você confirmou no início, se a 80 já estiver ocupada):

```nginx
server {
    listen 80;
    server_name 192.168.18.X;

    client_max_body_size 20M;

    location /static/ {
        alias /opt/smedim/backend/staticfiles/;
    }

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Ative **só este site novo** (não mexa em outros arquivos que já existirem em
`sites-enabled/`):

```bash
sudo ln -s /etc/nginx/sites-available/smedim /etc/nginx/sites-enabled/smedim
sudo nginx -t
```

`nginx -t` deve dizer `syntax is ok` e `test is successful`. Só então:

```bash
sudo systemctl reload nginx
```

(`reload`, não `restart` — evita derrubar outros sites que porventura já
estejam configurados nesse mesmo Nginx.)

## Parte 12 — Firewall (opcional, com cuidado)

Só faça isto se a clínica realmente quer um firewall ativo neste servidor.
Numa rede interna já protegida, pode pular esta parte sem problema.

```bash
sudo ufw status
```

- Se aparecer **inactive**: se decidir ativar, **garanta a porta 22 (SSH)
  liberada antes de ativar**, senão você pode perder o acesso remoto ao
  servidor:
  ```bash
  sudo ufw allow 22/tcp
  sudo ufw allow 80/tcp
  sudo ufw enable
  ```
- Se já aparecer **active**: só adicione a porta nova, sem tocar em mais
  nada:
  ```bash
  sudo ufw allow 80/tcp
  ```

## Parte 13 — Testar de outro computador da clínica

De outro PC na mesma rede, abra o navegador em `http://192.168.18.X/`
(sem `:8001` — o Nginx já cuida disso na porta 80). Confirme:

1. A tela de login aparece.
2. Login com o usuário criado na Parte 8 funciona.
3. Em `/admin/`, crie um usuário de teste real (ex: recepção), marque o
   perfil correspondente e "exigir troca de senha no próximo acesso".
4. Saia e entre com esse usuário — deve cair direto na troca de senha.
5. Confirme também que `192.168.18.3:8080` (o sistema antigo) continua
   respondendo normalmente nesse meio tempo.

## Parte 14 — Backup automático diário e limpeza do chat interno

```bash
sudo mkdir -p /opt/smedim/backups
```

Depois grave as tarefas agendadas do root de uma vez (cole o bloco inteiro; sem
editor — **substitui** o crontab do root, que numa instalação nova está vazio;
confira antes com `sudo crontab -l`):

```bash
printf '%s\n' \
'0 8 * * * docker exec smedim-postgres pg_dump -U clinica_smedim clinica_smedim > /opt/smedim/backups/backup_$(date +\%Y\%m\%d).sql 2>> /var/log/smedim_backup.log' \
'0 3 * * * find /opt/smedim/backups -name "backup_2*.sql" -mtime +30 -delete' \
'0 0 * * * cd /opt/smedim/backend && /opt/smedim/backend/venv/bin/python manage.py limpar_mensagens >> /var/log/smedim_limpar_mensagens.log 2>&1' \
| sudo crontab -
sudo crontab -l
```

Teste o backup uma vez, do mesmo jeito que o cron roda:

```bash
sudo sh -c 'docker exec smedim-postgres pg_dump -U clinica_smedim clinica_smedim > /opt/smedim/backups/backup_$(date +%Y%m%d).sql' && ls -lh /opt/smedim/backups/
```

A limpeza das 3h só apaga os backups **diários** (`backup_2...sql`) com mais de
30 dias — os `backup_pre-update_...` das atualizações ficam.

Isso guarda um backup por dia às 8h da manhã (horário pedido pela clínica — o servidor precisa estar ligado) e apaga sozinho os com mais de
30 dias. O banco roda no container Docker `smedim-postgres` (Parte 4), por isso o
backup usa o `pg_dump` de dentro dele — sem senha e sem `-t`. (Até 15/10/2026
esta linha usava o `pg_dump` do Ubuntu, que é o 12 e **falha** contra o banco 16
com `server version mismatch`: quem instalou com a linha antiga precisa trocá-la
no `sudo crontab -e`.) A terceira linha é a que zera o chat interno à meia-noite (decisão
do cliente: mensagens não ficam acumuladas no servidor — ver
`mensagens/management/commands/limpar_mensagens.py`); sem ela as mensagens
antigas nunca são apagadas. Teste rodando os comandos manualmente uma vez
(sem o `sudo crontab -e`, direto no terminal) para confirmar que o backup
gera o arquivo em `/opt/smedim/backups/` sem erro e que
`manage.py limpar_mensagens` roda sem erro e imprime quantas mensagens
apagou.

## Parte 15 — Problemas comuns

- **`nginx -t` reclama de "address already in use" ou porta ocupada**: outra
  coisa já está usando a porta escolhida (por exemplo, se for a mesma
  máquina do `192.168.18.3:8080` e você tiver reusado essa porta por
  engano). Troque a porta no `listen` do Nginx e repita a Parte 11.
- **`systemctl status smedim` mostra `failed`**: rode
  `sudo journalctl -u smedim -n 50 --no-pager` para ver o erro exato. As
  causas mais comuns são `.env` com dado errado (confira `DB_PASSWORD`) ou
  caminho do `venv` errado no `ExecStart`.
- **Erro ao instalar `psycopg2-binary` (falha compilando)**: geralmente
  acontece só em versões muito novas do Python sem pacote pronto ainda.
  Rode `sudo apt install -y libpq-dev python3-dev` e tente
  `pip install -r requirements.txt` de novo. Se persistir, considere usar
  Ubuntu 22.04 ou 24.04 LTS com o Python que já vem instalado por padrão
  (evite instalar manualmente uma versão muito recente do Python só para
  este projeto).
- **`apt update` mostra erros em outros repositórios** (ex: `mega.nz`,
  `cli.github.com`, `download.docker.com`, chave GPG expirada, "does not
  have a Release file"): normal se este servidor já tinha outros programas
  instalados antes do SMEDIM — são repositórios de terceiros não
  relacionados a este guia, e o erro neles não impede instalar os pacotes
  que vêm do repositório oficial do Ubuntu (`archive.ubuntu.com`, que
  aparece como `Atingido`/`Hit`). Pode seguir em frente com a Parte 3
  normalmente; não precisa corrigir nem remover esses outros repositórios
  para instalar o SMEDIM.
- **`apt install` reclama de dependências quebradas em pacotes `python3-*`**
  (ex: `python3-venv : Depende: python3 (= 3.8.2-...) mas 3.10.x está para
  ser instalado`, ou `E: Impossível corrigir problemas, você manteve (hold)
  pacotes quebrados`): sinal de que este servidor já passou por uma troca de
  versão do Ubuntu incompleta — `lsb_release -a` mostra uma versão (ex:
  22.10) diferente da que está em `/etc/apt/sources.list` (ex: `focal`,
  20.04), deixando pacotes de duas versões misturados. **Antes de corrigir
  qualquer coisa no sistema operacional, confirme que este servidor não é o
  mesmo que já roda o sistema antigo da clínica**:
  ```bash
  hostname -I
  ```
  ```bash
  sudo ss -tulpn | grep -E ':80 |:8080 |:5432 |:8001 '
  ```
  Se algum IP mostrado bater com `192.168.18.3`, ou a porta 8080 já
  aparecer em uso (repare também **quem** está escutando nela — se for
  `docker-proxy`, é bem provável que o sistema antigo rode dentro de um
  container Docker nesta própria máquina), **não** faça upgrade de release
  nem reinstale o Ubuntu nesta máquina — o risco é derrubar o sistema que a
  clínica já usa. Nesse caso (ou enquanto não tiver certeza), corrija só o
  Python, sem mexer em mais nada do sistema operacional.

  Tente primeiro o PPA deadsnakes (mais rápido quando funciona):
  ```bash
  sudo apt install -y software-properties-common
  sudo add-apt-repository -y ppa:deadsnakes/ppa
  sudo apt update
  sudo apt install -y python3.12 python3.12-venv python3.12-dev
  ```
  Se o `apt update` reclamar de `kinetic`, o deadsnakes usou o nome de
  versão errado — force para `focal`, que é a que este servidor realmente
  usa, e tente de novo:
  ```bash
  sudo sed -i 's/kinetic/focal/' /etc/apt/sources.list.d/deadsnakes*.list
  sudo apt update
  ```
  Em servidores bem antigos (testado num Ubuntu 20.04/focal com histórico de
  troca de versão incompleta), o deadsnakes pode simplesmente **não ter
  nenhuma versão nova o suficiente publicada** para essa base — `apt
  install` reclama de "pacote não encontrado" mesmo depois do passo acima, e
  `apt-cache search python3.1` só mostra a versão antiga (3.10), sem
  3.11/3.12/3.13.

  Nesse caso, o instinto seria compilar o Python direto do site oficial —
  mas numa máquina com esse histórico de mistura de versão, isso **também
  falha**: os pacotes `-dev` necessários pra compilar (`zlib1g-dev`,
  `libbz2-dev`, `libsqlite3-dev` etc.) têm o mesmo problema de dependência
  quebrada que o `python3-venv` (pedem a versão exata antiga da biblioteca,
  mas a versão instalada é mais nova) — é o sistema inteiro misturado, não
  só o Python. Se `sudo apt install -y build-essential zlib1g-dev
  libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev
  libffi-dev libsqlite3-dev libbz2-dev liblzma-dev wget` reclamar de
  "dependências desencontradas" parecido com o erro do `python3-venv` (ou,
  se você conseguir compilar mesmo assim, `python3.11 -m ensurepip` falhar
  com `ModuleNotFoundError: No module named 'zlib'`), não insista em
  compilar — pule direto para a solução abaixo.

  **Solução que funciona independente do estado do sistema operacional**: um
  Python já compilado, que não precisa de nenhuma biblioteca do sistema.
  Instale a ferramenta `uv` (script oficial, não depende de repositório do
  Ubuntu) e peça a ela um Python pronto:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
  (sem `curl`, use `wget -qO- https://astral.sh/uv/install.sh | sh`)
  ```bash
  source $HOME/.local/bin/env
  uv python install 3.11
  uv python find 3.11
  ```
  O último comando mostra o caminho completo do Python instalado (algo como
  `/home/SEU_USUARIO/.local/share/uv/python/cpython-3.11.../bin/python3.11`).
  Anote esse caminho.

  Se foi pelo `uv`, **não crie o venv direto do caminho em `/home/SEU_USUARIO/...`** —
  o usuário `www-data` (que vai rodar o serviço a partir da Parte 10) não
  tem permissão de entrar na home de outro usuário, e além disso esse Python
  "standalone" precisa manter a estrutura de pastas completa de quando foi
  baixado para conseguir se localizar sozinho (achar sua própria biblioteca
  padrão) — copiar só o executável para dentro do venv (`--copies`) quebra
  isso e dá erro `Fatal Python error: init_fs_encoding` /
  `ModuleNotFoundError: No module named 'encodings'` (só aparece mais tarde,
  ao rodar como serviço systemd — no teste manual da Parte 9 não dá pra
  perceber). Em vez disso, copie a instalação **inteira** para um lugar
  acessível a todos os usuários antes de criar o venv:
  ```bash
  sudo mkdir -p /opt/python3.11
  sudo cp -a /home/SEU_USUARIO/.local/share/uv/python/cpython-3.11-linux-x86_64-gnu/. /opt/python3.11/
  sudo chmod -R a+rX /opt/python3.11
  ```
  (troque `SEU_USUARIO` e o nome exato da pasta `cpython-3.11-...` pelo que
  apareceu no `uv python find 3.11`.)

  Qualquer que seja o caminho que funcionou (deadsnakes, compilado, ou
  `uv`), o resultado final é o mesmo: em **todo comando do guia que crie o
  ambiente virtual** (Parte 6: `python3 -m venv venv`), use o caminho do
  Python que você conseguiu no lugar de `python3` — se foi pelo `uv`, use o
  caminho da cópia em `/opt` (sem `--copies` — agora não precisa mais, já
  que a cópia inteira em `/opt/python3.11` resolve os dois problemas de
  uma vez):
  ```bash
  /opt/python3.11/bin/python3.11 -m venv venv
  ```
  Dali em diante o resto do guia continua igual — dentro do venv ativado,
  `python` já aponta para a versão certa sozinho. Isso resolve o suficiente
  para instalar o SMEDIM, mas o servidor continua numa base sem atualização
  de segurança (release antiga/fora de suporte); vale planejar, com calma e
  em outro momento, reinstalar este Ubuntu com uma versão LTS suportada
  (22.04 ou 24.04).

  Só se confirmar que esta máquina é **dedicada** (nenhum IP bate, porta
  8080 livre, nada relevante nela via `docker ps`) considere pular direto
  para uma reinstalação limpa do Ubuntu (22.04 ou 24.04 LTS) em vez de
  qualquer um dos desvios acima — mais trabalhoso agora, mas evita carregar
  essa mistura de pacotes para dentro de um sistema que vai guardar dado
  real de paciente.
- **Página abre mas sem estilo (sem CSS)**: o `collectstatic` (Parte 8) não
  rodou, ou o caminho no `alias` do Nginx (Parte 11) está diferente de
  `/opt/smedim/backend/staticfiles/`. Rode `python manage.py collectstatic
  --noinput` de novo (dentro do venv) e confirme o caminho.
- **Preciso recomeçar do zero durante o período de testes** (antes de a
  clínica realmente começar a usar pra valer): isso apaga TODOS os dados já
  lançados no PostgreSQL de produção — não use depois que a clínica já
  estiver com pacientes reais cadastrados.
  ```bash
  cd /opt/smedim/backend
  source venv/bin/activate
  python manage.py flush
  python manage.py seed_perfis
  python manage.py createsuperuser
  ```
- **Preciso criar outro superusuário depois, sem o venv ativo como
  `www-data`**:
  ```bash
  sudo -u www-data /opt/smedim/backend/venv/bin/python /opt/smedim/backend/manage.py createsuperuser
  ```

---

## Importar pacientes e prontuário do sistema antigo (opcional, sem pressa)

O cliente já enviou o backup do banco PostgreSQL do sistema antigo:
`smedim_production.2026-07-29.backup` (formato `custom` do `pg_dump`,
~21MB). Isso só faz sentido depois que o SMEDIM novo já estiver rodando
(Partes 1 a 14 concluídas) — é um processo separado, sem pressa, pode ser
feito dias depois da instalação. **Sempre rode cada comando de importação
sem `--confirmar` primeiro** e confira se o resumo bate com o esperado
antes de rodar de novo com `--confirmar` (grava de verdade).

### Leve o backup para o servidor

Do mesmo jeito que o pacote do sistema (Parte 2) — pendrive, ou:

```powershell
scp smedim_production.2026-07-29.backup usuario@192.168.18.X:/tmp/
```

### Restaure num banco separado (não é o banco do sistema!)

Isso é só uma cópia de LEITURA do sistema antigo, para a importação
consultar — não é o banco que o SMEDIM usa (`clinica_smedim`, criado na
Parte 5). Crie um banco à parte, com seu próprio usuário (mesmo padrão da
Parte 5), e restaure o backup nele:

```bash
sudo -u postgres createdb legado_smedim
sudo -u postgres psql -c "CREATE USER legado_smedim WITH PASSWORD 'TROQUE-POR-UMA-SENHA-FORTE';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE legado_smedim TO legado_smedim;"
sudo -u postgres psql -d legado_smedim -c "GRANT ALL ON SCHEMA public TO legado_smedim;"
sudo -u postgres pg_restore -d legado_smedim /tmp/smedim_production.2026-07-29.backup
```

(`pg_restore` já vem com o pacote `postgresql` instalado na Parte 3 — nada
a mais para instalar. Se aparecerem avisos durante o restore, geralmente não
é problema; só preste atenção em erros fatais que impeçam de terminar.)

### Aponte o `.env` para esse banco

```bash
cd /opt/smedim/backend
nano .env
```

Preencha as linhas `DB_LEGADO_*` (deixadas em branco lá na Parte 7):

```
DB_LEGADO_NAME=legado_smedim
DB_LEGADO_USER=legado_smedim
DB_LEGADO_PASSWORD=<a mesma senha que você criou acima>
DB_LEGADO_HOST=localhost
DB_LEGADO_PORT=5432
```

### Rode a importação — sempre em simulação primeiro

```bash
source venv/bin/activate
python manage.py importar_pacientes_legado
```

Confira o resumo: "Novos" deve bater com o número de pacientes esperado da
clínica. Se vier **0 registros lidos**, o valor de `CLASSE_CLIENTE` no topo
de `cadastros/management/commands/importar_pacientes_legado.py` está
provavelmente errado — foi um palpite educado (nunca confirmado contra o
dado real, ver comentário no próprio arquivo), ajuste e rode a simulação de
novo até a contagem fazer sentido. Só então:

```bash
python manage.py importar_pacientes_legado --confirmar
```

Depois, o prontuário (precisa dos pacientes já importados no passo
anterior):

```bash
python manage.py importar_prontuario_legado
```

Confira o resumo (pacientes não encontrados / profissionais não resolvidos
apontam o que ajustar) e só então:

```bash
python manage.py importar_prontuario_legado --confirmar
```

### Depois de confirmar que migrou certo

O banco `legado_smedim` guarda uma cópia integral do sistema antigo
(inclusive prontuário) só para a importação consultar. Depois de conferir
que os dados migraram corretamente no SMEDIM, apague-o e limpe o `.env` —
não precisa manter essa cópia extra rodando no servidor:

```bash
sudo -u postgres dropdb legado_smedim
sudo -u postgres psql -c "DROP USER legado_smedim;"
```

E deixe as linhas `DB_LEGADO_*` em branco de novo no `.env`.
