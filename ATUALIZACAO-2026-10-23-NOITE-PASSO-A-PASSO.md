# Atualização 2026-10-23, noite — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).

**Novidade (sem migração):** **Financeiro → Corrigir pagamento** deixa de ser só de hoje e ontem.
A lista tem filtro **Registrado de / até** (abre nos últimos 7 dias, com atalhos Hoje, Ontem,
Últimos 7 dias e Este mês) e, buscando pelo **nome do paciente**, acha o pagamento em qualquer
data. Dá para corrigir lançamento de qualquer dia — continua bloqueado só quando o repasse já foi
pago ao profissional ou o pagamento foi estornado.

## 1. Backup do banco

```bash
sudo docker exec smedim-postgres pg_dump -U clinica_smedim clinica_smedim > /opt/smedim/backups/backup_pre-update_$(date +%Y%m%d-%H%M).sql
ls -lh /opt/smedim/backups/
```

## 2. Levar o zip e conferir

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```

Copie o `smedim-update.zip` para `/tmp/smedim-update.zip` e confira:

```bash
unzip -p /tmp/smedim-update.zip LEIA-ME-ATUALIZACAO-CLINICA.md | grep -m1 "Resumo desta leva"
```

Tem que aparecer **"versão 2026-10-23, noite"**.

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
sudo systemctl status smedim
```

O `migrate` deve dizer **"No migrations to apply"**, e o status `active (running)`.

## 4. Testar

Financeiro → **Corrigir pagamento** → clicar em **Este mês** → aparecem os pagamentos do mês, com
**Corrigir** nos que ainda podem ser corrigidos. Digitar o nome de um paciente atendido semanas
atrás → o pagamento dele aparece.

## 5. Limpar

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```