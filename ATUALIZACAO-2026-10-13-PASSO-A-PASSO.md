# Atualização de 13/10/2026 — passo a passo

Este manual serve para levar a versão de **13/10/2026** para o servidor da
clínica. *(Pacote gerado de novo no fim do dia 13/10: além do que já estava,
traz o agendamento de consulta/exame por turno e os pacotes/combos — itens 6
e 7 da Parte 1. Use só o `smedim-update.zip` novo.)* Ele parte do pacote anterior, de **09/10/2026**. Se o servidor ainda
estiver numa versão mais antiga, não tem problema: o Passo 8 aplica todas as
mudanças que faltarem (mas veja o aviso sobre o totem no fim da Parte 3).

- **Arquivo do pacote:** `smedim-update.zip` (na pasta `PROJETO SMEDIM`, no seu PC)
- **Tempo com o sistema fora do ar:** cerca de 10 a 15 minutos (do Passo 4 ao Passo 11)
- **Quando fazer:** fora do horário de atendimento
- **Tem migração nova?** **Sim, 6.** Uma delas altera dados (as sessões de 1h
  já agendadas), então o **backup do Passo 2 é obrigatório**.
- **Tem biblioteca nova para instalar?** Não (pode pular o Passo 7).
- **Tem passo manual depois?** **Sim:** unificar os pacientes duplicados
  (Parte 3). Pode ser feito no mesmo dia ou depois.

---

## Parte 1 — O que mudou (em palavras simples)

### 1. Agendamento mais rápido
A tela de novo agendamento (o clique em "Vago" na Central de Atendimento)
demorava porque carregava os 37 mil pacientes de uma vez.
- Agora o paciente é **buscado enquanto se digita**: nome (sem precisar de
  acento ou maiúscula, em qualquer ordem) ou CPF.
- As sugestões mostram também a **data de nascimento**, para diferenciar
  pacientes com o mesmo nome.

### 2. Só aparecem os procedimentos do profissional
- Na hora de agendar, a lista mostra **só os procedimentos vinculados** ao
  profissional escolhido. Antes, profissional sem nenhum vínculo mostrava
  todos os procedimentos da clínica.
- **Profissional sem nenhum procedimento vinculado não pode ser agendado**
  até vincular: a tela mostra um aviso em vermelho. *(Cadastros →
  Procedimentos → "profissionais habilitados")*

### 3. Resumo do profissional na Agenda do dia
Ao escolher um profissional no filtro da **Agenda do dia** (visão Dia),
aparece uma faixa com:
- total de agendados e as situações (confirmados, a confirmar, atendidos,
  faltas; cancelados à parte);
- o **próximo paciente** (só quando o dia mostrado é hoje);
- a lista de **procedimentos** do dia com a quantidade (ex.: Consulta ×6).

### 4. Guias de sessão por convênio (sessão de 1h = 2 sessões)
O convênio paga a sessão em unidades de 30 minutos.
- Ao agendar uma **sessão por convênio**, a quantidade sai da duração:
  **1h = 2 guias**, 1h30 = 3 (45 min = 1).
- No dia, ao **Autorizar** o atendimento, conta na produção **só a 1ª guia**.
- As demais ficam em **Agenda → Guias pendentes** (também no menu
  Financeiro → "Guias de sessão pendentes"). O botão **"Marcar 2ª guia
  autorizada"** soma a sessão na produção. Dá para marcar **antes do
  atendimento** também.
- A guia conta na produção **do dia do atendimento**, não do dia em que foi
  marcada (sessão de 30/10 com a 2ª guia marcada em 01/11 entra em outubro).
- Tem **Desfazer** para engano. Depois que o **repasse ao profissional** daquele
  atendimento for pago, a guia não muda mais (aparece "Bloqueado").
- Avisos: o **Painel financeiro** mostra quantas guias faltam autorizar;
  **Contas a receber** e a **montagem de lote** marcam os atendimentos com guia
  pendente.
- As sessões de 1h **já agendadas** (futuras, ainda não autorizadas) são
  convertidas sozinhas no Passo 8.

### 5. Pacientes importados do sistema antigo
- Quem veio **só com o nome** (sem CPF, nascimento nem telefone) **sai da
  busca do agendamento e da lista de Pacientes**. Não é apagado: a lista de
  Pacientes tem o link **"ver esses cadastros"**, e ao completar o cadastro o
  paciente volta a aparecer.
- O **profissional continua vendo** o prontuário de todos normalmente.
- Cadastros feitos na própria clínica sempre aparecem.
- A lista de Pacientes ganhou a coluna **Nascimento**.
- Os **duplicados** são unificados com um comando à parte (Parte 3).

### 6. Consulta e exame sem hora marcada (por turno)
- Só a **sessão** (fisioterapia, fono, psicologia...) tem **horário e duração**.
- **Consulta e exame** são agendados num **turno** (manhã, tarde ou noite) e o
  paciente é atendido **na ordem em que foi agendado**. As telas mostram o
  turno e a posição na lista (ex.: "Manhã · 3º") em vez de um horário.
- Consulta/exame não bloqueiam mais a agenda por conflito de horário; só
  sessão continua com essa trava.

### 7. Pacotes / combos (particular)
Preço fechado pago de uma vez — ex.: **"Consulta + Eletrocardiograma"** ou
**"10 sessões"**.
- **Cadastro:** *Cadastros → Pacotes / combos*: nome, procedimentos com a
  quantidade, preço e (só para profissional de valor fixo) o repasse fixo.
- **Venda:** na tela **Registrar pagamento** de um atendimento particular,
  "Vender pacote neste atendimento". O paciente paga o pacote inteiro ali e o
  **profissional recebe o repasse do pacote todo nesse pagamento**.
- **Uso:** os atendimentos seguintes do paciente **com o mesmo profissional**
  usam o saldo sozinhos (selo **📦 Pacote** na agenda) e, em "Encaminhar para
  pagamento", são **autorizados direto**, sem passar pelo financeiro.
- **Falta ou cancelamento** devolvem a sessão ao saldo. Sem validade e sem
  devolução. Acabou o saldo, volta a cobrar normalmente.
- **Saldo de cada paciente:** *Agenda → Pacotes dos pacientes*.
- Só **particular** (convênio nunca usa pacote).

---

## Parte 2 — Instalando no servidor

> Cole **um bloco de comandos por vez** e espere terminar antes do próximo.
> Quando o comando tem `sudo`, ele pede a senha do usuário `smedim`. Ao
> digitar, nada aparece na tela, e isso é normal.

### Passo 1 — Pacote (já está pronto)
O arquivo `smedim-update.zip` já foi gerado no seu PC, na pasta
`PROJETO SMEDIM`. Não precisa fazer nada aqui.

### Passo 2 — Backup do banco (OBRIGATÓRIO)
No terminal do servidor:

```bash
sudo mkdir -p /opt/smedim/backups
sudo chown smedim:smedim /opt/smedim/backups
```

```bash
sudo docker exec smedim-postgres pg_dump -U clinica_smedim clinica_smedim > /opt/smedim/backups/backup_pre-update_$(date +%Y%m%d-%H%M).sql
```

Roda dentro do container do banco (`smedim-postgres`) — só pede a senha do `sudo`. Depois confira:

```bash
ls -lh /opt/smedim/backups/
```

O arquivo `.sql` tem que ter alguns MB (não pode ser `0`). **Se deu erro ou
veio vazio, pare aqui.**

### Passo 3 — Levar o zip para o servidor
Apague os restos da atualização anterior:

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```

Leve o `smedim-update.zip` (por AnyDesk, pendrive ou `scp`). Se ele cair na
pasta Downloads do servidor, copie para o `/tmp`:

```bash
cp ~/Downloads/smedim-update.zip /tmp/smedim-update.zip
```

Confira que é o zip desta versão:

```bash
unzip -l /tmp/smedim-update.zip | grep unificar_pacientes
```

Tem que aparecer uma linha com `unificar_pacientes_duplicados.py`.
**Se não aparecer nada, é o zip velho. Não siga.** Confira também que é o
zip gerado no fim do dia (com os pacotes):

```bash
unzip -l /tmp/smedim-update.zip | grep 0028_pacote
```

Tem que aparecer `cadastros/migrations/0028_pacote.py`. **Se não aparecer,
é o zip da manhã de 13/10 — use o novo.**

### Passo 4 — Parar o sistema

```bash
sudo systemctl stop smedim
sudo systemctl status smedim
```

Tem que mostrar `inactive (dead)`. A partir daqui o sistema fica fora do ar.

### Passo 5 — Cópia de segurança do código atual

```bash
sudo cp -a /opt/smedim/backend /opt/smedim/backend.bak-$(date +%Y%m%d-%H%M)
```

### Passo 6 — Colocar o código novo

```bash
cd /tmp
unzip -o smedim-update.zip -d smedim-update-extraido
```

```bash
sudo cp -a /tmp/smedim-update-extraido/. /opt/smedim/backend/
```

Confira que chegou:

```bash
ls /opt/smedim/backend/agenda/templates/agenda/guias_pendentes.html /opt/smedim/backend/cadastros/templates/cadastros/pacote_lista.html
```

Arrume as permissões:

```bash
sudo find /opt/smedim/backend -type d -exec chmod 755 {} \;
sudo find /opt/smedim/backend -type f -exec chmod u+rw,go+r {} \;
sudo chown -R www-data:www-data /opt/smedim/backend
```

### Passo 7 — Dependências
**Pode pular.** Não tem biblioteca nova nesta versão.

### Passo 8 — Migrações (atualiza o banco)

```bash
cd /opt/smedim/backend
sudo -u www-data venv/bin/python manage.py migrate
```

Devem aparecer linhas `Applying ... OK` para `agenda.0023`, `agenda.0024`,
`agenda.0025`, `agenda.0026`, `cadastros.0027` e `cadastros.0028`. A `agenda.0024` pode mostrar quantas sessões futuras foram
convertidas em guias (se não houver nenhuma, não mostra nada — normal).
Se alguma linha mostrar **erro**, pare e vá para o Passo 12.

### Passo 9 — Arquivos estáticos
Esta versão não precisa, mas rode por garantia (a de 09/10 precisa, caso
ainda não tenha sido aplicada):

```bash
sudo -u www-data venv/bin/python manage.py collectstatic --noinput
```

### Passo 10 — Permissões de novo (garantia)

```bash
sudo find /opt/smedim/backend -type d -exec chmod 755 {} \;
sudo chown -R www-data:www-data /opt/smedim/backend
```

### Passo 11 — Ligar o sistema e testar

```bash
sudo systemctl start smedim
sudo systemctl status smedim
```

Tem que mostrar `active (running)`. Depois, de outro computador da clínica:

- [ ] Fazer login normalmente.
- [ ] **Central de Atendimento →** escolher um profissional **→ clicar num
      "Vago"**: a tela abre rápido e, ao digitar parte do nome no campo
      Paciente, aparecem sugestões com a data de nascimento.
- [ ] Na mesma tela, a lista de procedimentos mostra **só os do profissional**.
- [ ] **Agenda do dia →** escolher um profissional: aparece a faixa de resumo.
- [ ] **Agenda → Guias pendentes** abre.
- [ ] **Cadastros → Pacientes** mostra a coluna Nascimento e o link "ver esses
      cadastros".
- [ ] **Novo agendamento de consulta:** pede o **turno**, não o horário; numa
      **sessão** continua pedindo horário e duração.
- [ ] **Cadastros → Pacotes / combos** abre (vazio no começo).
- [ ] **Agenda → Pacotes dos pacientes** abre.

Por fim, apague os arquivos temporários:

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```

A cópia `backend.bak-...` do Passo 5 pode ser apagada depois de alguns dias,
se estiver tudo certo.

### Passo 12 — Se algo der errado (voltar à versão anterior)
Troque `AAAAMMDD-HHMM` pela data e hora da cópia feita no Passo 5
(`ls /opt/smedim/` mostra o nome):

```bash
sudo systemctl stop smedim
sudo rm -rf /opt/smedim/backend
sudo mv /opt/smedim/backend.bak-AAAAMMDD-HHMM /opt/smedim/backend
sudo systemctl start smedim
```

Como esta versão **tem migrações**, se o Passo 8 já tinha rodado, também é
preciso restaurar o banco a partir do backup do Passo 2. Siga o Passo 12 do
[LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

---

## Parte 3 — Unificar os pacientes duplicados (passo manual)

O sistema antigo tem a mesma pessoa cadastrada mais de uma vez (no banco do
PC de testes foram **1.710 grupos**, 1.835 fichas a mais). O comando junta
cada grupo numa ficha só, com **todo o prontuário, agendamentos e
documentos**, e completa nela os dados que faltam (telefone, CPF...). As
cópias não são apagadas: ficam ligadas à ficha principal e escondidas.

**Critério:** mesmo nome (sem diferenciar acento, maiúscula e espaços) **e**
mesma data de nascimento. Mesmo nome com nascimento diferente não é unificado.

Pode ser feito no mesmo dia da atualização ou depois, com o sistema no ar,
de preferência fora do horário de atendimento.

### 1. Simular (não grava nada)

```bash
cd /opt/smedim/backend
sudo -u www-data venv/bin/python manage.py unificar_pacientes_duplicados --relatorio /tmp/unificacao_simulacao.csv
```

Aparece um resumo: quantos grupos, quantas cópias, quanto prontuário vai ser
movido. Termina com `SIMULAÇÃO (nada foi gravado...)`.

### 2. Conferir o relatório
O relatório tem uma linha por grupo: a ficha que fica (principal) e as que
serão unificadas nela. Para abrir no seu PC, copie para a pasta do usuário e
leve pelo AnyDesk/pendrive:

```bash
sudo cp /tmp/unificacao_simulacao.csv ~/
sudo chown smedim:smedim ~/unificacao_simulacao.csv
```

Abra no Excel e confira alguns grupos por amostragem (principalmente os com
número na coluna `prontuario_movido`). **Se algum grupo não for a mesma
pessoa, não siga** e me avise antes.

### 3. Gravar de verdade
Faça um **backup novo** antes (o mesmo comando do Passo 2):

```bash
sudo docker exec smedim-postgres pg_dump -U clinica_smedim clinica_smedim > /opt/smedim/backups/backup_pre-unificacao_$(date +%Y%m%d-%H%M).sql
```

```bash
ls -lh /opt/smedim/backups/
```

Depois:

```bash
cd /opt/smedim/backend
sudo -u www-data venv/bin/python manage.py unificar_pacientes_duplicados --confirmar --relatorio /tmp/unificacao_gravada.csv
```

O resumo agora termina com `GRAVADO`. Rodar o mesmo comando de novo (sem
`--confirmar`) deve mostrar **0 grupos**.

### 4. Conferir no sistema
- **Cadastros → Pacientes:** busque um nome que estava repetido; agora deve
  aparecer uma vez só.
- Com um usuário **profissional → Prontuários:** abra esse paciente e
  confira que o histórico está completo.

### 5. Apagar os relatórios
Os CSV têm nome e telefone de pacientes — não deixe espalhados:

```bash
rm -f /tmp/unificacao_simulacao.csv /tmp/unificacao_gravada.csv ~/unificacao_simulacao.csv
```

(apague também a cópia que levou para o seu PC, depois de conferir.)

---

## Parte 4 — O que a clínica precisa fazer depois

1. **Vincular os procedimentos de todos os profissionais** que atendem.
   Quem estiver sem nenhum procedimento vinculado não pode mais ser agendado.
   *(Cadastros → Procedimentos → abrir o procedimento → "profissionais
   habilitados")*
2. **Avisar a recepção** sobre a tela **Guias pendentes**: a 2ª guia das
   sessões de 1h precisa ser marcada lá quando o convênio autorizar.
   Idealmente antes de fechar o mês, para a produção do profissional sair
   completa.
3. **Avisar o financeiro:** o Painel mostra quantas guias de sessão faltam
   autorizar; conferir antes de montar o lote e fechar o mês.
4. **Cadastrar os pacotes** que a clínica vende (*Cadastros → Pacotes /
   combos*) e **avisar recepção e financeiro**: a venda é feita na tela de
   Registrar pagamento; depois disso as sessões do pacote são autorizadas
   direto, sem passar pelo financeiro.
5. **Avisar a recepção** que consulta/exame agora são agendados por turno e
   atendidos na ordem de agendamento.
6. **Totem (só se a versão de 09/10 ainda não tinha sido aplicada):** fazer
   o "Ajuste no PC do totem" descrito na seção da versão 2026-10-09 do
   [LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).
