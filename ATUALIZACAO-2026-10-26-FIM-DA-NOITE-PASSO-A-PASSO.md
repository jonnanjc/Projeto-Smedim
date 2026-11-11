# Atualização 2026-10-26, fim da noite — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).
Detalhes de cada passo no [LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

**Se as versões de 2026-10-26 (tarde e noite) ainda NÃO foram aplicadas**, elas vêm junto neste zip. Nesse
caso, depois de aplicar, faça também o passo 4 de
[ATUALIZACAO-2026-10-26-NOITE-PASSO-A-PASSO.md](ATUALIZACAO-2026-10-26-NOITE-PASSO-A-PASSO.md)
(limpar pagamentos em dobro).

**Novidades desta versão (1 migração nova, só de dados):**

- **Dinheiro fica no caixa e entra no repasse:** a parte do profissional no pagamento em dinheiro aparece
  como **pendente** em Repasses (igual cartão/Pix) até alguém registrar o pagamento a ele. Vale para o
  dinheiro registrado **a partir de 26/10**; o de antes conta como já entregue (a migração marca).
  Se algum profissional levou dinheiro na mão entre 26/10 e hoje, registre esse repasse em dinheiro.
- **Visão do proprietário:** "Em dinheiro" é o dinheiro físico no caixa (inclui convênio pago na clínica e a
  parte dos profissionais ainda não repassada). Repasse pago em dinheiro do caixa não desconta mais duas
  vezes do resultado. O proprietário não aparece mais como repasse pendente.
- **Fechamento filtrado por um profissional:** convênio já com o desconto da clínica.
- **Painel de chamada:** lista "Senhas chamadas hoje" com **Chamar de novo**. Totem não emite duas senhas
  com toque duplo.
- **Prontuário:** link **Apagar** (só quem escreveu). **Minha agenda:** "Atendimentos anteriores sem registro
  no prontuário" com **Evoluir**.
- **Fechamentos contam procedimentos:** atendimento com 2 procedimentos conta 2 no total do Particular
  (por procedimento e por forma de pagamento). **Início do profissional:** cartão "Procedimentos realizados".

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
unzip -l /tmp/smedim-update.zip | grep 0033_repasse_dinheiro_registrado
```

Tem que aparecer **"versão 2026-10-26, fim da noite"** e o arquivo `0033_repasse_dinheiro_registrado.py`.

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

O `migrate` tem que mostrar `Applying agenda.0033_repasse_dinheiro_registrado... OK` (e as da tarde, se
ainda não estavam aplicadas). Status tem que ser `active (running)`.

## 4. Corrigir o pagamento do Douglas (26/10)

Em **Financeiro → Corrigir pagamento**, abra o do **Douglas Willian Medeiros Ferreira** (Dra. Laura, 26/10) e
troque de R$ 260 para **R$ 130** em dinheiro (foi o que entrou).

## 5. Testar

1. **Fechamento diário de 26/10** → Visão do proprietário: **Em dinheiro (no caixa) = R$ 244,00**, com a linha
   "já descontado: repasse pago em dinheiro do caixa − R$ 460,00".
2. **Agenda → Repasses semanais:** o Dr. Dennis tem a parte dele da Regina (dinheiro) como pendente; o
   Dr. Iranildo **não** aparece.
3. **Fechamento diário filtrado pela Dra. Laura ou Dr. Dennis:** convênio "já com o desconto".
4. **Painel de chamada:** lista "Senhas chamadas hoje" → **Chamar de novo** numa senha → a TV anuncia de novo.
5. **Minha agenda** de um profissional da equipe multi: seção "Atendimentos anteriores sem registro" → **Evoluir**.
6. **Fechamento diário:** "Total particular · N procedimentos" igual à soma da lista por procedimento.
   **Início** de um profissional: cartão "Procedimentos realizados".

## 6. Limpar

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```
