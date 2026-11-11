# Atualização 2026-10-23 — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).
Ele já traz **tudo** que ainda não foi aplicado no servidor (inclusive a versão de 16/10, se ela
ainda não foi). Os detalhes de cada passo estão no [LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

**Novidades desta leva (2 migrações novas, só acrescentam — não alteram dado existente):**
- **Prontuário:** idade do paciente (sem data de nascimento aparece **(pendente cadastro)**),
  **plano do cadastro** e **plano autorizado no atendimento**.
- **Recepção 2:** a voz do painel agora chama **todos** da leva ("Ana. Entrar no Consultório 9.
  Bruno, Carla. Aguardar na Recepção 2.").
- **Cancelamento em dois critérios:** na Agenda só se cancela — e só se marca **Faltou** — o que
  **ainda não foi autorizado**. O já autorizado se cancela em **Agenda → Autorizações**, com motivo,
  só para quem tiver a permissão nova (**passo 10**).
- **Fila de chamada:** quem é encaminhado para pagamento **guarda a vez** — não perde mais o lugar
  para um convênio autorizado enquanto está no caixa.
- **Registrar pagamento:** botão para **cancelar quando o paciente desiste** na hora de pagar.
- **Fechamento diário e mensal:** convênios agora aparecem **com valor** (por convênio e
  procedimento) e há a seção nova **Produção** (particular + convênio = produção total).
- **Profissional:** tela nova **Agenda → Listagem de atendidos**, com o que ele já atendeu.

Faça fora do horário de atendimento: o sistema fica fora do ar do passo 4 ao passo 8.

## 0. A versão de 16/10 já foi aplicada? (só consulta)

```bash
cd /opt/smedim/backend
sudo -u www-data venv/bin/python manage.py showmigrations agenda | grep 0031
```

- Aparece `[X] 0031_registro_da_assinatura` → a de 16/10 **já está** no servidor: pule os passos 6b e 6c.
- Aparece `[ ] 0031_registro_da_assinatura` → a de 16/10 **vem junto agora**: faça também os passos 6b e 6c.

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

Tem que aparecer **"versão 2026-10-23"**. Se aparecer outra, é o zip velho: não siga.

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

Tem que aparecer, entre outras, `accounts.0018_permissao_cancelar_autorizado... OK` e
`agenda.0032_agendamento_encaminhado_pagamento_em... OK` (se a de 16/10 vier junto, aparecem
mais) — deixe rodar até o fim.

### 6b. Só se a de 16/10 veio junto (passo 0): contar quantos viram "Faltou"

Siga o **passo 6b** do [ATUALIZACAO-2026-10-16-PASSO-A-PASSO.md](ATUALIZACAO-2026-10-16-PASSO-A-PASSO.md)
(só conta, não altera; lá explica como desligar a regra se o número for alto demais).

### 6c. Só se a de 16/10 veio junto (passo 0): voz do painel no servidor

Siga o **passo 6c** do [ATUALIZACAO-2026-10-16-PASSO-A-PASSO.md](ATUALIZACAO-2026-10-16-PASSO-A-PASSO.md)
(instalação única, precisa de internet no servidor; se der erro, siga a atualização mesmo assim).

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

1. **Prontuário:** entrar como profissional → Prontuários → abrir um paciente → no topo aparecem
   **Idade**, **Plano no cadastro** e **Plano autorizado no atendimento**. Num paciente sem data de
   nascimento, a idade aparece **(pendente cadastro)**.
2. **Agenda do dia:** num atendimento **Autorizado**, os links **Cancelar** e **Faltou** não
   aparecem mais; num **Confirmado**, continuam.
3. **Autorizações** (com um usuário que já tenha a permissão do passo 10): aparece a coluna
   **Cancelar**; ao cancelar, o motivo é obrigatório. (Teste num atendimento de teste.)
4. **Registrar pagamento:** abrir um paciente na Fila de pagamento → no fim da tela, abrir
   **"Paciente desistiu? Cancelar este atendimento"** e conferir que aparece (não precisa cancelar).
5. **Vez guardada:** encaminhar um particular para pagamento, autorizar um convênio do mesmo
   profissional e só depois registrar o pagamento do particular → na Minha agenda do profissional,
   o particular aparece **na frente** do convênio.
6. **Fechamento diário:** Financeiro → Fechamento diário de um dia com atendimento por convênio →
   a seção **Convênios** mostra o valor de cada procedimento e o total; logo abaixo de
   "Particular", a seção **Produção** soma particular + convênio. Se aparecer "sem preço" em
   vermelho, falta o valor daquele procedimento na Tabela de Preços do convênio.
7. **Listagem de atendidos:** entrar como profissional → menu Agenda → **Listagem de atendidos**
   → aparecem os atendimentos dele de hoje já autorizados (em verde os marcados como atendido);
   clicar em **Este mês** para ver o mês todo.
8. **Recepção 2:** o profissional da sala da Recepção 2 chama uma leva → o painel fala o 1º
   ("entrar no Consultório…") e os demais ("…aguardar na Recepção 2").

Se algo der errado, siga o **Passo 12 (rollback)** do
[LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

## 10. Dar a permissão de cancelar atendimento autorizado (obrigatório)

Depois de atualizar, **ninguém** consegue cancelar atendimento já autorizado até marcar a
permissão — nem o Administrador.

1. Entrar com um usuário que gerencia perfis → **Administração → Perfis**.
2. Editar o perfil de quem pode cancelar (ex.: **Administrador**, supervisão da recepção).
3. No grupo **Agenda**, marcar **"Cancelar atendimento já autorizado (tela Autorizações)"** → salvar.
4. Se só uma pessoa específica puder, dá para marcar direto no usuário (Administração →
   Usuários → editar → permissões extras).

**Não** rode o comando `seed_perfis` para isso: ele regrava as permissões dos perfis padrão e
desfaz o que foi ajustado à mão.

## 11. Limpar os temporários

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```
