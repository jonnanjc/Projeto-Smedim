# Atualização 2026-11-10 — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).
Detalhes de cada passo no [LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

**1 migração (accounts.0020), sem collectstatic, sem pip install.** Duas permissões novas na fila de senhas,
que o administrador **ativa por usuário**:

- **Ver quem está aguardando na fila de senhas (com a hora de chegada)** — lista com a senha, o tipo
  (Normal / Prioritário e motivo / Exame com preparo), o atendimento, a **hora de chegada** e **há quanto
  tempo espera**. Aparece no **Painel de chamada** e numa tela nova: **AGENDA → Fila de espera**
  (atualiza sozinha a cada 30 segundos).
- **Chamar uma senha específica da fila, fora da ordem automática** — botão **"Chamar esta"** em cada
  linha. Precisa também da permissão "Chamar a próxima senha", do guichê do dia escolhido e de não
  estar com outra senha em atendimento. Cada chamada manual fica no **log de auditoria**.

O perfil **Administrador** já recebe as duas automaticamente.

E uma tela nova para a recepção, **AGENDA → Fila dos profissionais**: para cada profissional, quem já
passou pela recepção (autorizado) e aguarda ser chamado, na mesma ordem da Minha agenda, com
**quantos estão na frente** de cada paciente e um campo para procurar pelo nome. Aparece sozinha para
quem já autoriza na recepção (ou tem "Ver quem está aguardando") — nada para marcar.

E o botão **"Cancelar pacote"** em **AGENDA → Pacotes dos pacientes**, para pacote cadastrado direto
no paciente (sistema antigo) por engano. Aparece para quem pode cadastrar esse pacote. Os atendimentos
marcados que usavam o pacote voltam a ser cobrados normalmente. O sistema não deixa cancelar se o
pacote está sendo cobrado num atendimento ou já foi usado em atendimento autorizado/realizado. Fica no
log de auditoria.

Se a atualização de **09/11** (ciclo P-P-N por especialidade) ainda não foi aplicada, ela vai junto
neste zip — nada a mais para fazer.

O sistema fica parado só alguns segundos (passo 3). **Melhor aplicar fora do horário de chamada**
(ou num momento de recepção vazia).

## 1. Backup do banco (obrigatório)

```bash
sudo docker exec smedim-postgres pg_dump -U clinica_smedim clinica_smedim > /opt/smedim/backups/backup_pre-update_$(date +%Y%m%d-%H%M).sql
```

```bash
ls -lh /opt/smedim/backups/
```

O backup novo tem que ter uns **25 MB ou mais**.

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
unzip -l /tmp/smedim-update.zip | grep -c "0020_permissoes_fila_aguardando_e_chamada_manual"
unzip -p /tmp/smedim-update.zip fila/views.py | grep -c "def chamar_manual"
unzip -p /tmp/smedim-update.zip agenda/views.py | grep -c "def fila_profissionais"
unzip -p /tmp/smedim-update.zip agenda/views.py | grep -c "def pacote_cancelar_manual"
```

Tem que aparecer **"versão 2026-11-10"** e o número **1** quatro vezes. Se não, é o zip velho: **não siga**.

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
sudo systemctl status smedim --no-pager | head -3
```

O `migrate` tem que mostrar **`Applying accounts.0020_permissoes_fila_aguardando_e_chamada_manual... OK`**.
Status tem que ser `active (running)`.

Sem `collectstatic` e sem `pip install` nesta versão.

## 4. Ativar para os usuários

No sistema, como administrador: **Usuários → editar o usuário → permissões extras → "Fila / Senha"** e
marque o que essa pessoa deve ter:

- **Ver quem está aguardando na fila de senhas (com a hora de chegada)**;
- **Chamar uma senha específica da fila, fora da ordem automática** (só para quem também chama senha).

Se preferir liberar para um perfil inteiro, marque em **Perfis**. **Não rode `seed_perfis`** — ele
sobrescreve os perfis ajustados à mão.

## 5. Testar

1. Tire 2 senhas no totem.
2. Com um usuário que tem a permissão de ver, abra **AGENDA → Fila de espera**: as duas senhas aparecem
   com a **hora de chegada** e o tempo de espera.
3. Com um usuário da recepção que tem as permissões de chamar (e guichê escolhido na Agenda do dia),
   abra **Chamar senha**, clique **"Chamar esta"** na **segunda** senha e confirme: ela vai para a TV
   com o guichê dessa pessoa, e a primeira continua aguardando.
4. Conclua o atendimento normalmente.
5. Com um usuário da recepção, abra **AGENDA → Fila dos profissionais**: cada profissional com paciente
   autorizado aparece com a lista de quem aguarda e quantos estão na frente — confira com a
   **Minha agenda** do profissional (a ordem tem que ser a mesma).
6. Em **AGENDA → Pacotes dos pacientes**, o pacote cadastrado direto no paciente mostra o botão
   **"Cancelar pacote"** na última coluna. Para cancelar o que foi cadastrado por engano: clique e
   confirme.

Se der algum problema, volte o código pela cópia `backend.bak-...` do passo 3 (ver LEIA-ME, "Se algo der errado").

## 6. Limpar

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```
