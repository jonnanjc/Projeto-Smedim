# Atualização 2026-10-23, fim da noite — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).

**Novidades (sem migração):**

- **Central de Atendimento → "Repetir último agendamento":** no topo da Central aparece o último
  agendamento que você marcou (profissional, procedimento, particular/convênio). O botão abre a
  agenda desse profissional; ao clicar num horário **Vago**, o agendamento já vem com profissional,
  forma de atendimento, convênio e procedimentos preenchidos — falta só escolher o paciente. Serve
  para marcar vários pacientes seguidos com o mesmo profissional. O botão também aparece em
  **Novo agendamento**.
- **Busca de profissional:** todo campo "Profissional" do sistema (novo agendamento, Autorizações,
  Guias pendentes, fechamentos e relatórios do financeiro, pacotes...) agora aceita digitar
  qualquer parte do nome, como já era na Agenda do dia. Na Central, o campo **"Ou busque direto
  pelo profissional"** acha o profissional sem precisar escolher a especialidade antes.
- **Agenda do dia:** o profissional escolhido no filtro **não é mais desmarcado** ao trocar a data
  (nem depois de salvar um agendamento). Para ver todos de novo, escolha **Todos** no filtro.

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

Tem que aparecer **"versão 2026-10-23, fim da noite"**.

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

O `migrate` deve dizer **"No migrations to apply"** (ou aplicar as da versão da tarde, se ela
ainda não tinha sido aplicada), e o status `active (running)`.

Nos computadores da recepção, recarregue a página com **Ctrl+F5** (a busca de profissional é
JavaScript novo; sem isso o navegador pode mostrar a versão antiga).

## 4. Testar

1. **Agenda → Agenda do dia:** escolha um profissional no filtro (digite parte do nome) e clique
   em outro dia do calendário → o profissional continua escolhido.
2. **Central de Atendimento:** faça um agendamento qualquer. Volte à Central → aparece
   **"Seu último agendamento"** com o botão **↻ Repetir último agendamento**. Clique nele e depois
   num horário **Vago** → o formulário vem com profissional e procedimentos preenchidos e o paciente
   em branco.
3. Na Central, digite parte do nome em **"Ou busque direto pelo profissional"** → os horários dele
   aparecem.

## 5. Limpar

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```
