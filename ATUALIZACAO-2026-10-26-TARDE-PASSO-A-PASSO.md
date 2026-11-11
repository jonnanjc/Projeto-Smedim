# Atualização 2026-10-26, tarde — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).
Detalhes de cada passo no [LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).
Se a atualização de **2026-10-26 (manhã)** ainda não foi aplicada, ela vem junto neste zip.

**Novidades (3 migrações novas — o backup do passo 1 é obrigatório):**

- **Exame com preparo na Minha agenda:** não passa mais na frente de quem foi autorizado antes —
  vale a ordem de autorização. O selo "🧪 Exame com preparo" continua aparecendo.
- **Totem — senha "Exame com preparo":** na especialidade que tem exame com preparo agendado no
  dia, a tela "Qual o tipo da sua senha?" ganha o botão laranja **EXAME COM PREPARO**. A senha sai
  como **E001, E002…** e a recepção que cobre aquela especialidade/profissional chama essas senhas
  **antes** de Prioritário e Normal, por ordem de chegada. O padrão 2 P para 1 N continua igual.
  O botão só aparece se o procedimento estiver marcado como **"exame com preparo"** em
  Cadastros → Procedimentos.
- **Prontuário — data do atendimento:** o formulário de registro tem o campo **"Data do
  atendimento"** (vem com o dia do agendamento, ou hoje). Quem faz a evolução dias depois muda para
  o dia certo. O prontuário mostra essa data e, quando foi digitado em outro dia, "registrado em
  dd/mm/aaaa hh:mm". Não aceita data no futuro. Registros antigos recebem a data automaticamente.

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
unzip -l /tmp/smedim-update.zip | grep -E "0008_senha_tipo_exame_preparo|0006_entrada_data_atendimento"
```

Tem que aparecer **"versão 2026-10-26, tarde"** e as duas migrações.

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

O `migrate` tem que mostrar `Applying cadastros.0033_procedimento_preparo_senha_totem... OK`,
`Applying fila.0008_senha_tipo_exame_preparo... OK` e `Applying prontuario.0006_entrada_data_atendimento... OK`
(e a `prontuario.0005` se a versão da manhã vier junto), e o status `active (running)`.

## 4. Testar

1. **Cadastros → Procedimentos:** confira que a Endoscopia (e os outros exames com preparo) estão
   com **"exame com preparo"** marcado.
2. **Totem:** escolha a especialidade que tem endoscopia hoje → aparece **EXAME COM PREPARO**.
   Tire uma senha (sai **E001**). Em outra especialidade, o botão não aparece.
3. **Painel de chamada** da recepção que cobre essa especialidade: com senhas N/P esperando,
   **Chamar próxima** chama a **E001** primeiro.
4. **Minha agenda** do gastro: um exame com preparo autorizado depois de uma consulta fica
   **abaixo** dela (ordem de autorização).
5. **Prontuário** (com um profissional da multi): **+ Novo registro** → campo **"Data do
   atendimento"**; ponha uma data de 3 dias atrás, salve → o registro aparece com essa data e
   "registrado em …" embaixo.

## 5. Limpar

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```
