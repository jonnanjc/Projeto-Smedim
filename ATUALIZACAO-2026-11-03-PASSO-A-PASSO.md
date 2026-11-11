# Atualização 2026-11-03 — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).
Detalhes de cada passo no [LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

**Se a versão 2026-10-29 ainda NÃO foi aplicada**, ela vem junto neste zip (com a migração
`agenda.0035_agendamento_motivo_prioridade`) — faça também o passo 4 (testar) de
[ATUALIZACAO-2026-10-29-PASSO-A-PASSO.md](ATUALIZACAO-2026-10-29-PASSO-A-PASSO.md) e o aviso à equipe.

**Novidade desta versão (sem migração nova):** Financeiro → **Relatório detalhado**

- **Filtros** iguais aos do sistema antigo: **Data até data**, **Convênio** (ou Particular), **Nome do
  paciente** e **Profissional**, botão **Procurar** e atalhos **Hoje** / **Este mês**.
- Coluna nova **Guia**: mostra em que sessão da guia do convênio o paciente está (ex.: **2/10 · KNZEM94**),
  contada pelo sistema — não precisa mais anotar na observação. A **última sessão da guia fica em laranja**
  (hora de pedir guia nova).
- **Carrega bem mais rápido**, mesmo puxando o mês inteiro.

E em Cadastros → **Convênios** → abrir um convênio: a **tabela de preços** agora mostra **50 por página**
(antes um convênio com 648 procedimentos vinha tudo numa página só, e demorava), com campo de **busca** por
procedimento, código ou profissional.

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
unzip -p /tmp/smedim-update.zip financeiro/templates/financeiro/relatorio_detalhado.html | grep -c selo-guia
unzip -p /tmp/smedim-update.zip cadastros/templates/cadastros/convenio_detalhe.html | grep -c _paginacao
```

Tem que aparecer **"versão 2026-11-03"** e dois números **maiores que 0**. Se não, é o zip velho: **não siga**.

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

O `migrate` deve mostrar `No migrations to apply.` — ou, se a de 29/10 não estava aplicada,
`Applying agenda.0035_agendamento_motivo_prioridade... OK`. Status tem que ser `active (running)`.

## 4. Testar (recarregue com Ctrl+F5)

1. **Financeiro → Relatório detalhado:** aparecem os campos **Data / até / Convênio / Nome do paciente /
   Profissional** e o botão **Procurar**.
2. Coloque **01/10 até 31/10**, convênio **Bradesco** e clique **Procurar**: a página abre rápido e só
   traz atendimentos do Bradesco.
3. Digite o nome de um paciente que faz sessão com guia: a coluna **Guia** mostra **1/10, 2/10, 3/10…** em
   ordem, e a última da guia em laranja.
4. No **Fechamento diário**, o link "Ver relatório detalhado deste dia" ainda abre só aquele dia.
5. **Cadastros → Convênios →** abra o convênio com muitos procedimentos: abre rápido, com 50 linhas e
   "Página 1 de 13 (648 registros)" no fim. Teste a busca digitando parte do nome de um procedimento.

## 5. Limpar

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```
