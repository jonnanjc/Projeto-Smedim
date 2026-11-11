# Atualização 2026-10-27 — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).
Detalhes de cada passo no [LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

**A versão 2026-10-26 (fim da noite) ainda não foi aplicada**, então ela vem junto neste zip, com a
migração `agenda.0033_repasse_dinheiro_registrado`. Depois de aplicar, faça também os passos 4 e 5 de
[ATUALIZACAO-2026-10-26-FIM-DA-NOITE-PASSO-A-PASSO.md](ATUALIZACAO-2026-10-26-FIM-DA-NOITE-PASSO-A-PASSO.md)
(corrigir o pagamento do Douglas e testar). Se as versões de 26/10 tarde e noite também não foram
aplicadas, faça ainda o passo 4 de
[ATUALIZACAO-2026-10-26-NOITE-PASSO-A-PASSO.md](ATUALIZACAO-2026-10-26-NOITE-PASSO-A-PASSO.md)
(limpar pagamentos em dobro).

**Novidades desta versão (sem migração nova):**

- **Produção do fechamento filtrado por um profissional:** o Particular também sai já com o desconto da
  clínica (o mesmo valor do "Repasse líquido" da tabela), não mais o valor cobrado.
- **Cadastros → Procedimentos:** campo de busca por nome ou código.

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
unzip -p /tmp/smedim-update.zip cadastros/templates/cadastros/procedimento_lista.html | grep -c "Buscar por nome ou código"
```

Tem que aparecer **"versão 2026-10-27"** e o número **1**. Se não, é o zip velho: **não siga**.

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

Esta versão não tem migração própria. O `migrate` mostra `Applying agenda.0033_repasse_dinheiro_registrado... OK`
se a versão de 26/10 (fim da noite) ainda não estava aplicada; se já estava, mostra `No migrations to apply`.
Status tem que ser `active (running)`.

## 4. Testar (recarregue com Ctrl+F5)

1. **Fechamento diário filtrado por um profissional comum** (ex.: Dr. Armindo José): em "Produção", o
   Particular aparece "(já com desconto de 30,00%)" e o valor é igual ao **Repasse líquido** da tabela de baixo.
2. **Fechamento diário sem filtro (Geral):** Particular continua "(valor cobrado)".
3. **Cadastros → Procedimentos:** digite parte de um nome (ex.: "ultra") → **Buscar** → só aparecem os
   que batem; **Limpar** volta à lista completa.

## 5. Limpar

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```
