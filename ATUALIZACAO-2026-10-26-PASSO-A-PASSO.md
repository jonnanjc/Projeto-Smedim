# Atualização 2026-10-26 — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).

**Novidades (1 migração nova, só campo novo — `prontuario.0005_evolucao_equipe_multi`):**

- **Prontuário da equipe multi:** quem não é médico (fisioterapia, psicologia, fono, nutrição,
  enfermagem, TO...) registra num **campo único**, "Evolução / registro do atendimento". Se
  precisar, o link **"Usar formulário completo"** abre os 6 temas. Médico continua com o
  formulário completo.
- **Quem vê o quê no prontuário:**
  - registro de **médico** → todos os profissionais veem (médicos e equipe multi);
  - registro da **equipe multi** → só quem escreveu vê; o colega da multi (e o médico) não vê nem
    que ele existe;
  - registro de **saúde mental** (Psicologia, Psiquiatria, Terapia) → só quem escreveu, como já era.
- Registro antigo da multi com texto repetido em dois campos: o autor abre em **Editar**, apaga o
  campo repetido e salva.

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
unzip -l /tmp/smedim-update.zip | grep 0005_evolucao_equipe_multi
```

Tem que aparecer **"versão 2026-10-26"** e a linha do arquivo `0005_evolucao_equipe_multi.py`.

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

O `migrate` tem que mostrar **`Applying prontuario.0005_evolucao_equipe_multi... OK`** e o status
`active (running)`.

## 4. Testar

1. Entre com um profissional **da equipe multi** (ex.: fisioterapia) → **Prontuário** → abra um
   paciente → **+ Novo registro** → aparece só o campo **"Evolução / registro do atendimento"**.
   Escreva algo e salve.
2. Entre com **outro profissional da multi** e abra o mesmo paciente → o registro do passo 1
   **não aparece**. Os registros dos médicos aparecem.
3. Entre com um **médico** → **+ Novo registro** → aparece o formulário completo (queixa,
   histórico, avaliação...). O registro da multi do passo 1 **não aparece** para ele.

## 5. Limpar

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```
