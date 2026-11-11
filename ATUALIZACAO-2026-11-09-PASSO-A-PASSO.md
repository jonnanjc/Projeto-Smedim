# Atualização 2026-11-09 — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).
Detalhes de cada passo no [LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

**Sem migração, sem passo manual.** Só muda a **ordem de chamada das senhas na recepção**:

- o ciclo **P → P → N** agora é **separado por especialidade**;
- guichê que cobre duas especialidades (ex.: **Cardiologia + Fisioterapia**) não chama mais só P de
  uma delas — a Cardiologia tem a vez do N dela, a Fisioterapia a dela;
- entre as especialidades, sai primeiro **idoso 80+ / gestante em emergência**, senão **quem chegou antes**;
- senha **E** (exame com preparo) continua na frente de tudo;
- **fila travada só em P resolvida:** na vez do N, se tem P esperando desde antes do N, sai o **P mais
  antigo** (antes uma gestante esperando desde cedo era sempre passada para trás pelos idosos 60+ e,
  por causa dela, nenhum N saía).

Se a atualização de **07/11** (impressão do receituário/atestado) ainda não foi aplicada, ela vai junto
neste zip — nada a mais para fazer.

O sistema fica parado só alguns segundos (passo 3). **Melhor aplicar fora do horário de chamada**
(ou num momento de recepção vazia).

## 1. Backup do banco (obrigatório)

```bash
sudo docker exec smedim-postgres pg_dump -U clinica_smedim clinica_smedim > /opt/smedim/backups/backup_pre-update_$(date +%Y%m%d-%H%M).sql
```

```bash
ls -lh /opt/smedim/backups/
```

O backup novo tem que ter uns **25 MB ou mais**.

## 2. Levar o zip e conferir

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```

Copie o `smedim-update.zip` para `/tmp/smedim-update.zip`.

**Se o zip chegou na pasta Downloads do próprio servidor** (baixado pelo
navegador, AnyDesk etc.), ele ainda **não está no `/tmp`** — copie:

```bash
ls -lh ~/Downloads/smedim-update*.zip
cp ~/Downloads/smedim-update.zip /tmp/smedim-update.zip
ls -lh /tmp/smedim-update.zip
```

Depois confira:

```bash
unzip -p /tmp/smedim-update.zip LEIA-ME-ATUALIZACAO-CLINICA.md | grep -m1 "Resumo desta leva"
unzip -p /tmp/smedim-update.zip fila/models.py | grep -c "_escolher_entre_especialidades"
unzip -p /tmp/smedim-update.zip fila/models.py | grep -c "return prioritario_mais_antigo"
```

Tem que aparecer **"versão 2026-11-09"**, o número **3** e o número **1**. Se não, é o zip velho: **não siga**.

## 3. Aplicar

```bash
sudo systemctl stop smedim
sudo cp -a /opt/smedim/backend /opt/smedim/backend.bak-$(date +%Y%m%d-%H%M)
cd /tmp
unzip -o smedim-update.zip -d smedim-update-extraido
sudo cp -a /tmp/smedim-update-extraido/. /opt/smedim/backend/
sudo find /opt/smedim/backend -type d -exec chmod 755 {} \;
sudo find /opt/smedim/backend -type f -exec chmod u+rw,go+r {} \;
sudo chown -R www-data:www-data /opt/smedim/backend
cd /opt/smedim/backend
sudo -u www-data venv/bin/python manage.py migrate
sudo systemctl start smedim
sudo systemctl status smedim --no-pager | head -3
```

O `migrate` tem que dizer **"No migrations to apply."** Status tem que ser `active (running)`.

Sem `collectstatic` e sem `pip install` nesta versão.

## 4. Testar

Na recepção, com um guichê cobrindo **Cardiologia e Fisioterapia** (Agenda do dia → especialidades que
atendo), clique em **Chamar próxima** algumas vezes ao longo da manhã e olhe **Senhas chamadas hoje**:

- a Cardiologia tem que ter **N** chamado também (a cada 2 P de Cardiologia, 1 N de Cardiologia,
  quando tem N esperando);
- a Fisioterapia continua sendo chamada normalmente;
- se tinha uma P antiga esperando (ex.: gestante desde cedo), ela sai na próxima vez de N, e logo
  depois sai o N mais antigo.

Se der algum problema, volte o código pela cópia `backend.bak-...` do passo 3 (ver LEIA-ME, "Se algo der errado").

## 5. Limpar

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```
