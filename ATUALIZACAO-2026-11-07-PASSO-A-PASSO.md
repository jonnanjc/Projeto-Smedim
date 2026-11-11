# Atualização 2026-11-07 — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).
Detalhes de cada passo no [LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

**Sem migração, sem passo manual.** Só muda a **impressão do receituário e do atestado**:

- texto todo em **preto** (nada mais em cinza/verde) e **letras maiores**;
- título ("RECEITUÁRIO" / "ATESTADO MÉDICO") em negrito, centralizado;
- **logo da clínica maior**, com "Clínica SMEDIM" e o endereço ao lado — bairro agora **Centro**;
- o aviso **"Receituário emitido" / "Atestado emitido" não sai mais no papel** (continua aparecendo na tela);
- **data e assinatura no pé da folha** (data à esquerda, assinatura à direita).

O sistema fica parado só alguns segundos (passo 3).

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
unzip -p /tmp/smedim-update.zip accounts/templates/accounts/base.html | grep -c "Data e assinatura no pé da folha"
```

Tem que aparecer **"versão 2026-11-07"** e o número **1**. Se não, é o zip velho: **não siga**.

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

## 4. Testar (recarregue com Ctrl+F5)

Num prontuário, emita (ou reabra) um **receituário** e clique em **Imprimir**. Na prévia de impressão:

- texto preto e maior, logo grande no canto superior esquerdo, endereço com **Centro**;
- **sem** a faixa "Receituário emitido" no topo da folha;
- data e assinatura **lá embaixo** na folha.

Faça o mesmo com um **atestado**.

Se aparecer data/endereço do site no topo ou no rodapé da folha, desmarque **"Cabeçalhos e rodapés"** na
tela de impressão do navegador (só precisa uma vez).

## 5. Limpar

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```
