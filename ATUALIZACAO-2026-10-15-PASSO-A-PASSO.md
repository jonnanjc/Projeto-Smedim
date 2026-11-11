# Atualização 2026-10-15 (fim da tarde) — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).
Ele já traz **tudo** que ainda não foi aplicado no servidor (versões de 13/10, 14/10 e 15/10).
Os detalhes de cada passo estão no [LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

**Novidade desta leva:** na tela **Registrar pagamento**, com 2 formas de pagamento, cada forma
tem o seu valor ("Valor da 1ª forma" / "Valor da 2ª forma"). Ao digitar a 1ª, a 2ª é
preenchida sozinha com o que falta para fechar o **valor com desconto**.

Faça fora do horário de atendimento: o sistema fica fora do ar do passo 4 ao passo 9.

## 1. Backup do banco (obrigatório)

```bash
sudo mkdir -p /opt/smedim/backups
sudo chown smedim:smedim /opt/smedim/backups
sudo docker exec smedim-postgres pg_dump -U clinica_smedim clinica_smedim > /opt/smedim/backups/backup_pre-update_$(date +%Y%m%d-%H%M).sql
ls -lh /opt/smedim/backups/
```

O `.sql` novo tem que ter alguns MB (não `0`). Se vier vazio, **pare aqui**.

## 2. Levar o zip para o servidor

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```

Copie o `smedim-update.zip` (pendrive, AnyDesk ou `scp`) para `/tmp/smedim-update.zip`.
Se ele caiu em Downloads: `cp ~/Downloads/smedim-update.zip /tmp/smedim-update.zip`.

## 3. Conferir que é o zip certo

```bash
unzip -p /tmp/smedim-update.zip LEIA-ME-ATUALIZACAO-CLINICA.md | grep -m1 "Resumo desta leva"
```

Tem que aparecer **"versão 2026-10-15, fim da tarde"**. Se aparecer outra, é o zip velho: não siga.

## 4. Parar o sistema e guardar uma cópia

```bash
sudo systemctl stop smedim
sudo cp -a /opt/smedim/backend /opt/smedim/backend.bak-$(date +%Y%m%d-%H%M)
```

## 5. Colocar o código novo

```bash
cd /tmp
unzip -o smedim-update.zip -d smedim-update-extraido
sudo cp -a /tmp/smedim-update-extraido/. /opt/smedim/backend/
sudo find /opt/smedim/backend -type d -exec chmod 755 {} \;
sudo find /opt/smedim/backend -type f -exec chmod u+rw,go+r {} \;
sudo chown -R www-data:www-data /opt/smedim/backend
```

(O aviso `backslashes as path separators` é normal.)

## 6. Migrações

```bash
cd /opt/smedim/backend
sudo -u www-data venv/bin/python manage.py migrate
```

Esta correção não tem migração, mas as versões anteriores que vêm junto podem ter —
deixe rodar até o fim.

## 7. Arquivos estáticos (por garantia)

```bash
sudo -u www-data venv/bin/python manage.py collectstatic --noinput
sudo chown -R www-data:www-data /opt/smedim/backend
```

## 8. Subir o sistema

```bash
sudo systemctl start smedim
sudo systemctl status smedim
```

Tem que mostrar `active (running)`.

## 9. Testar

1. Entrar no sistema de outro computador da clínica.
2. Abrir um atendimento pré-autorizado → **Registrar pagamento**.
3. Desconto de R$ 50 (com motivo) → 1ª forma **Dinheiro**, 2ª forma **Cartão débito**.
4. Digitar só o **Valor da 1ª forma** → a 2ª deve se preencher sozinha e a linha
   "Soma das formas" deve ficar com ✔. (Não precisa salvar se for paciente de verdade — clique em Cancelar.)

Se algo der errado, siga o **Passo 12 (rollback)** do
[LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

## 10. Limpar os temporários

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```

Se ainda não fez a unificação de pacientes duplicados da versão 13/10, veja o
[ATUALIZACAO-2026-10-13-PASSO-A-PASSO.md](ATUALIZACAO-2026-10-13-PASSO-A-PASSO.md).
