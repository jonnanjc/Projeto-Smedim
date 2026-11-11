# Atualização 2026-11-06 (fim da noite) — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).
Detalhes de cada passo no [LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

**Sem migração, sem passo manual.** Só muda o texto da tela **Cadastros → Pacientes → Possíveis
duplicados**: o exemplo agora é **"Fatima" e "Fátima" contam como iguais**.

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
unzip -p /tmp/smedim-update.zip cadastros/templates/cadastros/paciente_duplicados.html | grep -c "Fátima"
```

Tem que aparecer **"versão 2026-11-06-fim-da-noite"** e o número **1**. Se não, é o zip velho: **não siga**.

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

**Cadastros → Pacientes → Possíveis duplicados:** o texto do topo diz **"Fatima" e "Fátima" contam como
iguais**.

## 5. Limpar

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```

Se ainda não apagou as cópias do banco tiradas para o teste dos duplicados:

```bash
rm -f ~/copia_clinica_*.sql.gz ~/Downloads/copia_clinica_*.sql.gz
```
