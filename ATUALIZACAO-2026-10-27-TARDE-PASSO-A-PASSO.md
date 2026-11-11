# Atualização 2026-10-27, tarde — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).
Detalhes de cada passo no [LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

**Se a versão 2026-10-27 (manhã) ainda NÃO foi aplicada**, ela vem junto neste zip — siga também o que falta em
[ATUALIZACAO-2026-10-27-PASSO-A-PASSO.md](ATUALIZACAO-2026-10-27-PASSO-A-PASSO.md) (o aviso do topo e o teste
do passo 4).

**Novidades desta versão (2 migrações novas, sem mexer em dado existente):**

- **Venda de pacote no fechamento:** vender o pacote na sessão de hoje contava 2 procedimentos; agora conta 1
  (em "por procedimento" e em "por forma de pagamento").
- **Pacote do sistema antigo:** em **Pacotes dos pacientes → "+ Cadastrar pacote do sistema antigo"** dá para
  colocar o pacote direto no paciente, sem agendamento: sessões já usadas, valor e se está **a pagar** ou
  **já pago no sistema antigo**. A pagar: as sessões passam pelo financeiro, que pode **"Cobrar pacote agora"**
  ou só confirmar sem cobrar.
- **Permissão nova:** "Cadastrar pacote no paciente sem agendamento" (Perfis/Usuários → Agenda). O perfil
  Administrador já vem com ela; os demais precisam ser marcados.

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
unzip -l /tmp/smedim-update.zip | grep -E "0034_pacote_do_sistema_antigo|0019_permissao_cadastrar_pacote_paciente"
```

Tem que aparecer **"versão 2026-10-27, tarde"** e os **2 arquivos** de migração. Se não, é o zip velho: **não siga**.

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

O `migrate` tem que mostrar `Applying accounts.0019_permissao_cadastrar_pacote_paciente... OK` e
`Applying agenda.0034_pacote_do_sistema_antigo... OK`. Status tem que ser `active (running)`.

## 4. Marcar a permissão

Em **Perfis** (ou **Usuários**), grupo **Agenda**, marque **"Cadastrar pacote no paciente sem agendamento"**
para quem vai cadastrar os pacotes do sistema antigo (o Administrador já tem).

## 5. Testar (recarregue com Ctrl+F5)

1. **Fechamento diário de hoje:** a venda de pacote feita hoje aparece como **1 procedimento** (sem a linha
   "(pelo pacote)" da mesma sessão), e o total do Particular bate nas duas tabelas.
2. **Pacotes dos pacientes → "+ Cadastrar pacote do sistema antigo":** cadastre um pacote de um paciente que
   veio do sistema antigo como **a pagar** → na lista aparece **"A pagar"** e o saldo certo ("restam X de Y").
3. Quando esse paciente for encaminhado ao financeiro, a tela de pagamento mostra **"Cobrar pacote agora"**.
   Se ele não for pagar hoje, é só **Confirmar e autorizar**.

## 6. Limpar

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```
