# Atualização 2026-11-06 — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).
Detalhes de cada passo no [LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

**Se a versão 2026-11-03 ainda NÃO foi aplicada**, ela vem junto neste zip (sem migração) — faça também o
passo 4 (testar) de [ATUALIZACAO-2026-11-03-PASSO-A-PASSO.md](ATUALIZACAO-2026-11-03-PASSO-A-PASSO.md).

**Esta versão tem 1 migração nova:** `cadastros.0034_profissional_acupuntura_primeiro_na_fila` (só um campo
novo, desmarcado para todos).

**Novidades:**

1. **Dinheiro com ponto de milhar** — painéis, fechamentos, relatórios e mensagens mostram **150.000,00**.
   Nos campos de valor dá para digitar `150000`, `150000,00` ou `150.000,00`: ao sair do campo ele fica
   `150.000,00`.
2. **Editar agendamento** — marcar só "Retorno de consulta do sistema antigo" agora salva.
3. **Cadastro de paciente** — editar não apaga mais a data de nascimento nem a validade da carteirinha.
4. **Agenda do dia** — no resumo do profissional, retornos aparecem à parte (em roxo).
5. **Retorno acima do limite diário** — só salva com o motivo do encaixe nas observações. Editar um
   agendamento **do dia** não trava.
6. **Botão "Autorizar (pacote já pago)"** no lugar de "Encaminhar para pagamento" quando o pacote cobre.
7. **Evoluções pendentes** (Minha agenda) — só para a equipe multi; médico (inclusive médico acupunturista)
   não vê mais a lista gigante.
8. **Acupuntura no começo da fila** — opção nova no cadastro do profissional (passo 5 abaixo).

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
unzip -l /tmp/smedim-update.zip | grep 0034_profissional_acupuntura
unzip -p /tmp/smedim-update.zip accounts/moeda.py | grep -c formatar_moeda
```

Tem que aparecer **"versão 2026-11-06"**, a linha do arquivo `0034_profissional_acupuntura_primeiro_na_fila.py`
e um número **maior que 0**. Se não, é o zip velho: **não siga**.

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

O `migrate` **tem que mostrar** `Applying cadastros.0034_profissional_acupuntura_primeiro_na_fila... OK`
(se a de 29/10 ainda não estava aplicada, aparece também `Applying agenda.0035_agendamento_motivo_prioridade... OK`).
Status tem que ser `active (running)`.

Sem `collectstatic` e sem `pip install` nesta versão.

## 4. Testar (recarregue com Ctrl+F5)

1. **Financeiro → Painel financeiro / Fechamento mensal:** valores acima de mil aparecem com ponto
   (ex.: **R$ 12.345,67**).
2. **Financeiro → Movimentações avulsas → Nova:** digite `150000` no Valor e clique fora: vira
   **150.000,00**. Clique em **Cancelar** (não precisa salvar).
3. **Cadastros → Pacientes →** edite um paciente que tem data de nascimento: a data **aparece preenchida**.
4. **Editar um agendamento** de consulta de amanhã, marcar só "Retorno de consulta do sistema antigo" e
   salvar: ao abrir de novo, continua marcado.
5. **Agenda do dia** filtrando um profissional com retornos: o resumo mostra "… – retorno ×N" em roxo.
6. **Minha agenda** de um médico: não aparece mais "Atendimentos anteriores sem registro no prontuário".

## 5. Depois de atualizar: marcar a acupuntura do profissional

**Cadastros → Profissionais →** editar o profissional da acupuntura → marcar **"Acupuntura no começo da
fila"** → **Salvar**. Só ele. Os outros continuam na ordem normal.

## 6. Avisar a equipe

- **Recepção:** retorno acima do limite do dia agora pede o motivo do encaixe nas observações (menos ao
  editar um agendamento do próprio dia). Pacote já pago aparece como "Autorizar (pacote já pago)".
- **Recepção:** quem já tentou marcar "retorno do sistema antigo" editando e não salvou precisa marcar de
  novo. Datas de nascimento que sumiram em edições antigas **não voltam sozinhas** — corrigir no cadastro
  quando o paciente vier.
- **Financeiro:** os valores agora aparecem com ponto de milhar; pode digitar com ou sem os pontos.

## 7. Limpar

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```
