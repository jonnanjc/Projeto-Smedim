# Puxar só os pacientes do sistema antigo da clínica (sem prontuário)

Este guia é só para trazer o **cadastro de pacientes** (nome, CPF, telefone,
data de nascimento etc.) do sistema antigo da clínica para dentro do SMEDIM —
**sem** trazer o histórico de prontuário. O sistema já tem os dois processos
prontos e **separados**: você só precisa rodar o de paciente e não rodar o de
prontuário.

> O prontuário é um comando à parte (`importar_prontuario_legado`), tratado só
> na Parte C, e entra **somente para consulta** (ninguém edita).

## Como este guia está organizado

- **Parte A — Teste no seu PC (Windows):** serve para validar o mapeamento e
  ver os pacientes na tela local. Já foi feita em 2026-09-23 (37.752
  pacientes importados no banco de teste). Não afeta a clínica.
- **Parte B — Importação de verdade no servidor da clínica:** é a que coloca
  os pacientes no sistema que a clínica usa. **Só faça depois** de aplicar a
  atualização de código ([LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md))
  e confirmar que o sistema está no ar.

## O que já existe pronto no projeto

- `cadastros/management/commands/inspecionar_legado.py` — lista tabelas/colunas
  do banco antigo, para conferir a conexão antes de importar.
- `cadastros/management/commands/importar_pacientes_legado.py` — lê só as
  tabelas `participante` + `papel` (filtrando `papel.class = 'com.mkmikael.smedim.Cliente'`) do
  banco antigo e grava em `cadastros.Paciente` aqui. **Nunca toca em tabela
  de prontuário** — é fisicamente incapaz de trazer prontuário, mesmo rodando
  com `--confirmar`.
- Conexão chamada `"legado"` em `config/settings.py`, configurada por
  `DB_LEGADO_NAME/USER/PASSWORD/HOST/PORT` no `.env` (ver `.env.example`).

# PARTE A — Teste no seu PC (Windows)

## Passo 1 — Ter uma cópia do banco antigo acessível

O sistema antigo roda em PostgreSQL. Você precisa de um Postgres que essa
importação consiga acessar — normalmente **não** é o banco de produção do
sistema antigo direto (evita mexer no sistema que a clínica ainda usa no
dia a dia), e sim uma cópia restaurada à parte. Duas formas:

**a) Já tem um backup/dump do sistema antigo (`.sql` ou `.dump`)**

```powershell
# No seu PC, com um PostgreSQL instalado (ou num Postgres da rede):
createdb -U postgres sistema_antigo_smedim
psql -U postgres -d sistema_antigo_smedim -f caminho\para\o\backup.sql
```

(se o arquivo for `.dump` no formato custom do `pg_dump -Fc`, use
`pg_restore -U postgres -d sistema_antigo_smedim caminho\para\o\backup.dump`)

**b) Não tem backup ainda — precisa tirar um do servidor do sistema antigo**

No servidor onde o sistema antigo roda hoje (peça acesso a quem administra
esse sistema, se não for você):

```bash
pg_dump -h <host> -U <usuario> -d <nome_do_banco_antigo> -F c -f backup_sistema_antigo.dump
```

Isso copia o banco **inteiro** do sistema antigo (inclusive prontuário) —
tudo bem, o filtro de "sem prontuário" acontece depois, na hora de importar
pra cá (Passo 4), não na hora do dump. Traga esse arquivo para o seu PC e
restaure como no item (a).

## Passo 2 — Apontar o `.env` para essa cópia

No `.env` deste projeto (nunca no `.env.example`, que é só modelo):

```
DB_LEGADO_NAME=sistema_antigo_smedim
DB_LEGADO_USER=postgres
DB_LEGADO_PASSWORD=<senha do seu Postgres local>
DB_LEGADO_HOST=localhost
DB_LEGADO_PORT=5432
```

Reinicie o servidor Django (`python manage.py runserver`) se estiver rodando,
para carregar o `.env` de novo.

## Passo 3 — Conferir a conexão e o nome da classe "paciente"

```powershell
python manage.py inspecionar_legado
python manage.py inspecionar_legado --tabela papel
python manage.py inspecionar_legado --tabela participante
```

Confirme que as tabelas `papel` e `participante` aparecem com as colunas
esperadas (`papel.class`, `papel.participante_id`, `participante.nome`,
`participante.documento`, etc.). Se quiser conferir uma amostra real para
bater o formato de algum campo:

```powershell
python manage.py inspecionar_legado --tabela papel --amostra 3
```

⚠️ Isso imprime dado real de paciente no terminal (CPF, nome...). Não deixe
a saída salva em log ou print compartilhado — é dado protegido por LGPD.

No banco do sistema antigo o valor de `papel.class` para paciente é
`com.mkmikael.smedim.Cliente` (já configurado na constante `CLASSE_CLIENTE`
no topo do arquivo; confirmado em 2026-09-23 com 37.768 pacientes). Se a
simulação do Passo 4 trouxer 0 registros, esse é o primeiro lugar a ajustar —
para listar os valores existentes:
`SELECT class, count(*) FROM papel GROUP BY class;`

## Passo 4 — Simular a importação (não grava nada ainda)

```powershell
python manage.py importar_pacientes_legado
```

Sem `--confirmar`, o comando só **mostra um resumo** (quantos pacientes
seriam criados, quantos já existem por CPF, erros de validação) e não grava
nada no banco do SMEDIM. Confira se o total bate com o que você espera do
sistema antigo antes de seguir.

## Passo 5 — Importar de verdade

Com o resumo do Passo 4 conferido:

```powershell
python manage.py importar_pacientes_legado --confirmar
```

Isso grava os pacientes em `cadastros.Paciente` aqui. É seguro rodar de novo
no futuro (ex.: sistema antigo ganhou pacientes novos) — cada paciente é
casado pelo ID de origem (`origem_legado_id`), então não duplica ninguém.

**Fora do escopo desta importação (por decisão já tomada no projeto, não
precisa mexer):** convênio/carteirinha e endereço (bairro/cidade) não vêm
junto — ficam em branco aqui, o dado original continua intacto no backup.
Prontuário **não é tocado em nenhum momento** deste fluxo.

## Depois de importar

- Confira alguns pacientes na tela **Cadastros → Pacientes** do SMEDIM para
  bater com o sistema antigo.
- Se restaurou o banco antigo num Postgres local só para isso, pode apagar
  esse banco (`dropdb sistema_antigo_smedim`) e limpar `DB_LEGADO_*` do
  `.env` depois de confirmar que a importação está correta — não precisa
  manter essa conexão ligada o tempo todo.
- Guarde o arquivo de backup do sistema antigo (`.dump`/`.sql`) em lugar
  seguro por enquanto, caso precise reimportar ou complementar depois.

---

# PARTE B — Importação de verdade no servidor da clínica

Vale o mesmo raciocínio da atualização: o banco de produção (`clinica_smedim`)
tem dado real, então o cuidado central é **não perder nem misturar nada**.
O banco do sistema antigo é restaurado num banco **separado**
(`sistema_antigo_smedim`) do mesmo PostgreSQL e só é lido — o
`importar_pacientes_legado` nunca escreve nele.

**Antes de começar, confirme:**

- [ ] A atualização de código já foi aplicada e o sistema abre normalmente
      (`sudo systemctl status smedim` → `active (running)`).
- [ ] Você tem o arquivo de backup do sistema antigo. O ideal é um **novo**,
      tirado no dia (o sistema antigo continua recebendo pacientes). O de
      2026-07-29 (`smedim_production.2026-07-29.backup`) serve para ensaio.
- [ ] Tem a senha do usuário `clinica_smedim` do PostgreSQL (a mesma
      `DB_PASSWORD` do `.env` do servidor, criada na Parte 5 da instalação).

## B1 — Levar o backup do sistema antigo para o servidor

No seu PC (PowerShell), ajuste o IP e o nome do arquivo:

```powershell
scp "C:\Users\jonna\Downloads\smedim_production.2026-07-29.backup" smedim@192.168.18.X:/tmp/
```

(ou AnyDesk/pendrive, como nas outras vezes — o destino é `/tmp/` do servidor).
No servidor, confira que chegou inteiro:

```bash
ls -lh /tmp/smedim_production*.backup
```

O tamanho tem que ser igual ao do arquivo no seu PC.

## B2 — Backup do banco de produção (não pule)

Primeiro garanta que a pasta de backups existe (se o backup automático da
instalação ainda não foi configurado, ela não existe e o comando dá
"Arquivo ou diretório inexistente"):

```bash
sudo mkdir -p /opt/smedim/backups
sudo chown smedim:smedim /opt/smedim/backups
```

Depois gere o backup e confira:

```bash
sudo docker exec smedim-postgres pg_dump -U clinica_smedim clinica_smedim > /opt/smedim/backups/backup_pre-importacao_$(date +%Y%m%d-%H%M).sql
ls -lh /opt/smedim/backups/backup_pre-importacao_*
```

(roda dentro do container do banco, `smedim-postgres` — só pede a senha do `sudo`.)
O arquivo não pode estar vazio. Se algo der errado na importação, é a
restauração desse arquivo que desfaz tudo (Passo 12 do guia de atualização).

## B3 — Criar o banco separado e restaurar o backup

Criar o banco, com o usuário `clinica_smedim` como dono (assim ele consegue
ler o que for restaurado, sem precisar de senha do superusuário `postgres`):

```bash
sudo -u postgres createdb -O clinica_smedim sistema_antigo_smedim
```

Restaurar (pede a senha do `clinica_smedim`; pode levar alguns minutos, sem
barra de progresso — espere o prompt voltar):

```bash
pg_restore -h 127.0.0.1 -U clinica_smedim -d sistema_antigo_smedim --no-owner --no-privileges /tmp/smedim_production.2026-07-29.backup
```

Podem aparecer avisos ("role does not exist", "already exists"). Se o
comando terminar e o teste abaixo funcionar, pode ignorá-los. Confira:

```bash
psql -h 127.0.0.1 -U clinica_smedim -d sistema_antigo_smedim -c "SELECT class, count(*) FROM papel GROUP BY class;"
```

Deve listar `com.mkmikael.smedim.Cliente` com cerca de 37 mil registros (o
número exato depende da data do backup). Se aparecer "relation papel does
not exist", a restauração falhou — pare e guarde a mensagem de erro do
`pg_restore` antes de seguir.

## B4 — Ligar o sistema ao banco antigo (`.env` do servidor)

O `.env` fica em `/opt/smedim/backend/.env` e pertence ao `www-data`, então
edite com `sudo`:

```bash
sudo nano /opt/smedim/backend/.env
```

**1. Anote a senha atual.** Enquanto o arquivo está aberto, ache a linha
`DB_PASSWORD=...` (role com as setas) e leia o que vem depois do `=`. É essa
mesma senha que vai nas linhas novas.

**2. Vá para o final do arquivo:** aperte `Alt` + `/` (ou role com a seta
para baixo até acabar o texto). Se a última linha não estiver vazia,
aperte `Enter` para começar numa linha nova, e mais uma vez para deixar uma
linha em branco de separação.

**3. Digite (ou cole) estas cinco linhas** — uma por linha, sem espaço em
volta do `=`, sem aspas. **Não altere nenhuma linha que já existia**,
principalmente `DB_ENGINE`, `DB_NAME`, `DB_USER` e `DB_PASSWORD`: elas são
o banco de produção do sistema.

```
DB_LEGADO_NAME=sistema_antigo_smedim
DB_LEGADO_USER=clinica_smedim
DB_LEGADO_PASSWORD=<a mesma senha que está em DB_PASSWORD>
DB_LEGADO_HOST=127.0.0.1
DB_LEGADO_PORT=5432
```

(no `nano` do terminal, colar é com `Ctrl`+`Shift`+`V`; se a senha tiver
símbolos, digitar à mão pode ser mais seguro que colar.)

**4. Salvar e sair:** `Ctrl`+`O`, depois `Enter` (confirma o nome do
arquivo), depois `Ctrl`+`X`.

**5. Conferir que gravou** (mostra só as linhas novas):

```bash
sudo grep DB_LEGADO /opt/smedim/backend/.env
```

Deve mostrar as cinco linhas. **Não precisa reiniciar o serviço** — os
comandos de importação leem o `.env` na hora em que rodam.

**6. Testar a conexão** (deve listar as tabelas do sistema antigo, entre
elas `papel` e `participante`):

```bash
cd /opt/smedim/backend
sudo -u www-data venv/bin/python manage.py inspecionar_legado
```

Se der "Conexão 'legado' não configurada", o `DB_LEGADO_NAME` não foi
gravado (volte ao passo 3). Se der erro de autenticação, a senha está
diferente da do `DB_PASSWORD` — confira caractere a caractere. Se der
"database does not exist", o B3 não foi concluído.

## B5 — Simular a importação (não grava nada)

```bash
sudo -u www-data venv/bin/python manage.py importar_pacientes_legado
```

Confira o resumo. Com o backup de 2026-07-29 o esperado é: **lidos 37768,
novos ~37752, erros de validação 16** (8 CPF inválido + 8 e-mail inválido —
dado ruim na origem, esses ficam de fora). Com um backup novo os números
serão um pouco maiores. Se "Total lido" vier **0**, pare: o valor de
`papel.class` não bate (ver Passo 3 da Parte A).

Se o servidor já tiver pacientes cadastrados à mão, os que tiverem o mesmo
CPF aparecem em "Casados com paciente já existente por CPF" — são
atualizados, não duplicados.

## B6 — Importar de verdade

Só com o resumo do B5 conferido e o backup do B2 feito:

```bash
sudo -u www-data venv/bin/python manage.py importar_pacientes_legado --confirmar
```

Deve levar alguns minutos. No final repete o resumo (agora `GRAVANDO`).

## B7 — Conferir

1. Abra o sistema (`http://192.168.18.X/`) → Cadastros → Pacientes. A lista
   vem paginada (50 por página) e mostra o total no rodapé.
2. Busque 2 ou 3 pacientes conhecidos por nome ou CPF e confira os dados.
3. Rode a importação de novo só para ter certeza de que não duplica: o
   resumo deve mostrar `Novos: 0` e todos em "atualizados".

## B8 — Limpeza (LGPD)

O arquivo de backup contém dado real de pacientes **e prontuários**. Não
deixe solto em `/tmp`:

```bash
sudo mv /tmp/smedim_production.2026-07-29.backup /opt/smedim/backups/
sudo chmod 600 /opt/smedim/backups/smedim_production.2026-07-29.backup
```

O banco auxiliar `sistema_antigo_smedim` também guarda os prontuários do
sistema antigo. Se não for reimportar nos próximos dias, apague-o e retire
as cinco linhas `DB_LEGADO_*` do `.env` (o sistema funciona igual sem elas):

```bash
sudo -u postgres dropdb sistema_antigo_smedim
```

Se preferir mantê-lo para reimportar quando o sistema antigo ganhar
pacientes novos, basta restaurar um backup novo por cima e repetir B5/B6.

## Se algo der errado

- **Importação gravou algo errado:** restaure o backup do B2 (mesmos
  comandos do "rollback do banco" do Passo 12 do guia de atualização, com o
  arquivo `backup_pre-importacao_...sql`).
- **Erro de permissão:** rode com `sudo -u www-data` (para `manage.py`) ou
  `sudo` (para arquivos do `/opt/smedim`).
- **Prontuário:** as Partes A e B não o importam. Ele é a Parte C, à parte.

---

# PARTE C — Prontuário do sistema antigo (só consulta)

**Só depois da Parte B concluída** (pacientes já importados) **e da versão
2026-09-24 aplicada no servidor** (o `migrate` cria o que a importação usa).

O que você vai ver depois: no prontuário de cada paciente, as entradas do
sistema antigo aparecem misturadas às novas, em ordem de data, com a
especialidade, o nome do profissional de lá e a marca "Sistema antigo ·
somente consulta". **Não há botão Editar** e ninguém consegue alterá-las.

## C1 — Backup do banco de produção

Repita o B2 (o prontuário grava dezenas de milhares de registros):

```bash
sudo docker exec smedim-postgres pg_dump -U clinica_smedim clinica_smedim > /opt/smedim/backups/backup_pre-prontuario_$(date +%Y%m%d-%H%M).sql
ls -lh /opt/smedim/backups/
```

## C2 — Simular

O banco auxiliar `sistema_antigo_smedim` e o `.env` (`DB_LEGADO_*`) já devem
estar prontos, do B3 e B4.

```bash
cd /opt/smedim/backend
sudo -u www-data venv/bin/python manage.py importar_prontuario_legado
```

Com o backup de 2026-07-29 o esperado é: **lidas 64152, novas ~64142**;
10 "paciente não encontrado" (são os pacientes que ficaram de fora por CPF
ou e-mail inválido, ver B5); e um número alto em "profissional não
cadastrado aqui" (~43 mil) — isso é normal e **não** impede a importação.
Se "Novos" vier bem menor que o total, pare e me avise.

## C3 — Gravar

```bash
sudo -u www-data venv/bin/python manage.py importar_prontuario_legado --confirmar
```

Leva vários minutos e não mostra progresso — espere o prompt voltar. É
reexecutável (não duplica). Cada entrada mantém a **data original** do
atendimento.

## C4 — Conferir

Entre com um usuário profissional → Prontuários → busque um paciente antigo
→ abra. Confira que as entradas antigas têm a marca "somente consulta" e
nenhum botão Editar, e que um registro novo que você criar continua
editável por você.

**Sigilo:** a regra de saúde mental continua valendo. Entrada com
especialidade Psicologia/Psiquiatria/Terapia só aparece para o profissional
que a escreveu. Como o sistema antigo quase só tem médicos (CRM), isso
praticamente não se aplica; se algum dia aparecerem entradas de Psicologia
sem profissional cadastrado aqui, elas ficam ocultas para todos.

## Depois de importar: unificar os pacientes duplicados

O sistema antigo tem a mesma pessoa cadastrada várias vezes (mesmo nome e
nascimento). Depois de importar pacientes (e prontuário), rode o comando
`unificar_pacientes_duplicados` — primeiro em simulação, conferindo o
relatório CSV, e só então com `--confirmar`. Passo a passo completo na Parte 3
do [ATUALIZACAO-2026-10-13-PASSO-A-PASSO.md](ATUALIZACAO-2026-10-13-PASSO-A-PASSO.md).

Rodar as importações de novo depois disso é seguro: as fichas já unificadas
são reconhecidas e os registros de prontuário vão direto para a ficha
principal — a duplicata não volta.
