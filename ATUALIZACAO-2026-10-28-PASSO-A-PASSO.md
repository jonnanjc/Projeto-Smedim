# Atualização 2026-10-28 — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).
Detalhes de cada passo no [LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

**Se a versão 2026-10-27 (tarde) ainda NÃO foi aplicada**, ela vem junto neste zip — siga também os passos 4
e 5 de [ATUALIZACAO-2026-10-27-TARDE-PASSO-A-PASSO.md](ATUALIZACAO-2026-10-27-TARDE-PASSO-A-PASSO.md)
(marcar a permissão nova e testar).

**Novidades desta versão (sem migração nova):**

- **Busca do Prontuário:** ignora acento e maiúscula ("joao" acha "João"), palavras em qualquer ordem e CPF
  com ou sem pontos. Também acha nome com **espaço duplo** no cadastro (369 do sistema antigo), que a tela
  mostra como um espaço só — por isso o nome copiado da agenda não era encontrado.
- **"Atendimentos anteriores sem registro" (Minha agenda):** só aparece para a equipe multi. O atendimento
  sai da lista também quando o profissional evolui pela busca do Prontuário com a data do atendimento —
  inclusive as evoluções que já foram feitas assim.
- **Meu fechamento (diário e mensal):** no menu Financeiro de todo profissional, sem marcar permissão. No topo,
  **"Quanto eu levo"**: particular + convênio **já com o desconto da clínica**. Convênio agora mostra valor
  (antes só a quantidade).

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
unzip -l /tmp/smedim-update.zip | grep busca_pacientes
```

Tem que aparecer **"versão 2026-10-28"** e o arquivo `cadastros/busca_pacientes.py`. Se não, é o zip velho:
**não siga**.

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

O `migrate` desta versão não tem nada novo (`No migrations to apply`, se a de 27/10 tarde já estava
aplicada). Status tem que ser `active (running)`.

## 4. Testar (recarregue com Ctrl+F5)

1. **Prontuários:** copie o nome de um paciente da lista "Atendimentos anteriores sem registro" e busque →
   ele aparece. Busque também digitando **sem acento** (ex.: "joao").
2. **Minha agenda de um profissional da equipe multi:** o paciente que já foi evoluído pela busca do
   Prontuário, com a data do atendimento, **não** aparece mais em "Atendimentos anteriores sem registro".
3. **Minha agenda de um médico:** a lista "Atendimentos anteriores sem registro" não aparece.
4. **Entrando como profissional:** menu **Financeiro → Meu fechamento** abre o fechamento do dia, com o link
   para o mensal. No topo, **"Quanto eu levo hoje"** mostra Particular, Convênios e Total já com o desconto —
   confira com o **Fechamento diário** filtrado por esse profissional (a "Produção total" tem que bater).

## 5. Limpar

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```
