# Atualização 2026-10-26, noite — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).
Detalhes de cada passo no [LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

**Se a atualização de 2026-10-26 (tarde) ainda NÃO foi aplicada**, ela vem junto neste zip (3 migrações:
exame com preparo no totem e data do atendimento no prontuário). Nesse caso o `migrate` do passo 3
mostra essas 3 migrações — veja os testes da tarde em
[ATUALIZACAO-2026-10-26-TARDE-PASSO-A-PASSO.md](ATUALIZACAO-2026-10-26-TARDE-PASSO-A-PASSO.md).

**Novidades desta versão (sem migração nova):**

- **Pagamento em dobro corrigido:** clicar duas vezes (ou o navegador reenviar) em **"Registrar e
  autorizar"** gravava o pagamento duas vezes (ex.: Pix 90 + Dinheiro 110 aparecendo 2x, total R$ 400
  numa consulta de R$ 200). Agora o segundo envio não grava nada e mostra "pagamento já está registrado".
  Em todas as telas, depois de clicar em salvar o formulário fica apagadinho e ignora novos cliques.
- **Corrigir pagamento:** abrindo um pagamento gravado em dobro, aparece o aviso amarelo **"gravado em
  dobro"** e o total já vem certo (R$ 200). É só conferir e salvar: as linhas repetidas são apagadas e o
  valor da consulta nos relatórios não muda.
- **Comando `remover_pagamentos_duplicados`** para limpar todos de uma vez (passo 4).

## 1. Backup do banco (obrigatório)

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
unzip -l /tmp/smedim-update.zip | grep remover_pagamentos_duplicados
```

Tem que aparecer **"versão 2026-10-26, noite"** e o arquivo `remover_pagamentos_duplicados.py`.

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

Status tem que ser `active (running)`. Se a versão da tarde já estava aplicada, o `migrate` diz
`No migrations to apply`.

## 4. Limpar os pagamentos que já ficaram em dobro

Primeiro só LISTA (não altera nada):

```bash
cd /opt/smedim/backend
sudo -u www-data venv/bin/python manage.py remover_pagamentos_duplicados
```

Cada linha `DUPLICADO` é um atendimento com pagamento em dobro (ex.: Ildo Lima Martins, 26/10). Se a
lista estiver certa, aplique:

```bash
sudo -u www-data venv/bin/python manage.py remover_pagamentos_duplicados --confirmar
```

Linhas `MANUAL` (o repasse de mais de uma cópia já foi pago ao profissional) não são mexidas — acerte
com o profissional e corrija à mão.

Se preferir, em vez do comando dá para corrigir um por um em **Financeiro → Corrigir pagamento**
(aviso amarelo "gravado em dobro" → Salvar).

## 5. Testar

1. **Financeiro → Corrigir pagamento** → abra o pagamento do Ildo Lima Martins (26/10): deve mostrar só
   Pix 90 + Dinheiro 110, total R$ 200 (se o passo 4 já limpou), ou o aviso "gravado em dobro".
2. Registre um pagamento novo clicando **duas vezes seguidas** em "Registrar e autorizar" → em Corrigir
   pagamento ele aparece **uma vez só**.

## 6. Limpar

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```
