# Atualização 2026-10-29 — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).
Detalhes de cada passo no [LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

**Se a versão 2026-10-28 ainda NÃO foi aplicada**, ela vem junto neste zip — faça também o passo 4 de
[ATUALIZACAO-2026-10-28-PASSO-A-PASSO.md](ATUALIZACAO-2026-10-28-PASSO-A-PASSO.md) (testar). E se nem a
2026-10-27 (tarde) foi, siga os passos 4 e 5 de
[ATUALIZACAO-2026-10-27-TARDE-PASSO-A-PASSO.md](ATUALIZACAO-2026-10-27-TARDE-PASSO-A-PASSO.md).

**Novidade desta versão (1 migração nova: `agenda.0035_agendamento_motivo_prioridade`):**

- **Prioridade na Minha agenda:** o paciente prioritário passa na frente dos normais já autorizados,
  **alternando 1 prioritário e 1 normal** (é o que a recepção já fala: "tem 4 na sua frente, desde que não
  chegue prioridade"). Um normal nunca passa na frente de um prioritário que chegou antes dele.
- **Recepção marca a prioridade:** campo **Prioridade** ao lado do botão "Encaminhar para pagamento"
  (particular) e na tela **Autorizar** (convênio). Senha P → escolha o motivo; senha N ou E → deixe "Normal".
  Esqueceu? **"Salvar prioridade"** na Agenda do dia, até o profissional chamar.
- Atendimentos que já existem ficam todos como **Normal**. Totem e painel de senha não mudaram.
- **Tema escuro:** a lista aberta dos campos de escolha não fica mais com texto claro em fundo branco.

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
unzip -l /tmp/smedim-update.zip | grep 0035_agendamento_motivo_prioridade
```

Tem que aparecer **"versão 2026-10-29"** e o arquivo `agenda/migrations/0035_agendamento_motivo_prioridade.py`.
Se não, é o zip velho: **não siga**.

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

O `migrate` tem que mostrar `Applying agenda.0035_agendamento_motivo_prioridade... OK` (e, se a de 27/10
tarde não estava aplicada, também as duas dela). Status tem que ser `active (running)`.

## 4. Testar (recarregue com Ctrl+F5)

1. **Agenda do dia (recepção):** num atendimento **Confirmado** particular aparece o seletor **Normal / P · …**
   ao lado de "Encaminhar para pagamento". Num convênio, a tela **Autorizar** tem o campo **Prioridade**.
2. Encaminhe/autorize um paciente marcando **P · Idoso 60+**: na **Minha agenda** do profissional ele aparece
   com o selo laranja e passa na frente dos normais, alternando (P, N, P, N…).
3. Num paciente autorizado e ainda não chamado, troque a prioridade e clique **Salvar prioridade** → a ordem
   na Minha agenda muda.

## 5. Limpar

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```

## 6. Avisar a equipe

Mande o esquemático `DOCUMENTOS/Esquema-Prioridades-Fila.png` para a recepção e os profissionais.
