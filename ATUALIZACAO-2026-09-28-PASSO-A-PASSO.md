# Atualização de 28/09/2026 — passo a passo

Este manual serve para levar a versão de **28/09/2026** para o servidor da
clínica. Ele parte do pacote anterior, de **25/09/2026**. Se o servidor ainda
estiver numa versão mais antiga, não tem problema: o Passo 8 aplica todas as
mudanças que faltarem.

- **Arquivo do pacote:** `smedim-update.zip` (na pasta `PROJETO SMEDIM`, no seu PC)
- **Tempo com o sistema fora do ar:** cerca de 10 a 15 minutos (do Passo 4 ao Passo 11)
- **Quando fazer:** fora do horário de atendimento
- **Tem migração nova?** **Sim, 9.** Duas delas alteram dados, então o
  **backup do Passo 2 é obrigatório**.
- **Tem biblioteca nova para instalar?** Não (pode pular o Passo 7).

---

## Parte 1 — O que mudou (em palavras simples)

### 1. Repasse pago em partes (particular em cartão/Pix)
Quando o caixa não tem dinheiro para repassar tudo, ou o profissional quer
levar só uma parte, o financeiro agora registra **quanto pagou** e o resto
fica como **saldo pendente**.
- Onde: **Agenda → Repasses semanais → Registrar pagamento**, ou a tela nova
  **Financeiro → Saldos e pagamentos de repasse**.
- Escolhe-se como pagou: *Dinheiro do caixa* (lança sozinho a saída de caixa)
  ou *Pix/transferência (proprietário)*.
- O valor é abatido dos atendimentos mais antigos, já com o desconto da clínica.
- Lançou errado? Dá para excluir o pagamento, e o saldo volta.
- O botão "Marcar como pago" em cada linha saiu. Para pagar tudo, basta
  confirmar o valor que já vem preenchido.

### 2. Saiu a opção "sempre recebe o repasse no mesmo dia"
Ela saiu do cadastro do profissional. O que já tinha sido acertado com esses
profissionais fica marcado como **pago** automaticamente e não vira pendência.

### 3. Fechamentos e Painel financeiro
- Nos fechamentos diário e mensal e em "Meu fechamento", "no dia / pendente"
  virou **"já pago / pendente"** e passa a contar os pagamentos parciais.
- No Painel financeiro, o card **"Repasse pendente"** agora mostra o valor
  **líquido** (com o desconto da clínica e sem o que já foi pago). Antes ele
  mostrava o valor bruto, por isso o número vai **diminuir**. Isso é esperado.

### 4. Convênio: saiu o "desconto (%) sobre a tabela particular"
Nem todo procedimento desses convênios tinha desconto. Agora **todo convênio
tem os valores na Tabela de Preços**, um por procedimento.
- A atualização **copia sozinha** para a Tabela de Preços os valores que o
  sistema já calculava (com o desconto aplicado).
- Esses convênios ficam com a nova opção **"Permite pagamento na hora"**
  marcada. É ela que mantém o botão "Encaminhar para pagamento (na hora)" na
  agenda.

### 5. Profissional com valor fixo no particular (novo)
Para profissionais que definem o próprio preço e recebem um valor fixo, sem
percentual.
- Onde: **Cadastros → Profissionais → Editar** → marcar **"cobra valor fixo no
  particular (sem percentual)"** e preencher a tabela **"Valores fixos no
  particular"**: procedimento, valor cobrado do paciente e valor repassado.
- Procedimento que não estiver nessa tabela **não pode ser agendado como
  particular** para esse profissional (o sistema avisa).
- Atendimento por convênio continua pelo percentual.
- Na lista de profissionais aparece "(particular: valor fixo)" ao lado deles.

### 6. Prazo de retorno grátis por convênio (novo)
Cada convênio tem o campo **"prazo de retorno grátis (dias)"**.
- Todos começam com **30** (igual a antes, então nada muda sozinho).
- Informe **15** para convênios que só cobrem retorno em 15 dias, ou **0**
  para convênios sem retorno grátis.
- Vale o convênio da consulta original. Particular continua com 30 dias.

### 7. Datas específicas no mês e remanejamento (novo)
A antiga tela "Exceções de agenda" virou **"Datas específicas / exceções"**
(Cadastros → Profissionais → **Datas / exceções**).
- Serve para o **profissional sem dia fixo**, que vem só em datas marcadas
  (ex.: "vem todo dia 15"). Deixe o horário fixo semanal em branco e cadastre
  as datas aqui.
- **Repetir nos próximos meses:** ao salvar uma data, dá para repetir por até
  12 meses (mesmo dia do mês, mesma semana e dia da semana, ou último dia da
  semana do mês). Datas em fim de semana ou já cadastradas são puladas, e o
  sistema avisa quais.
- Cada data mostra **quantos pacientes** já estão marcados e tem os botões
  **Editar** e **Remanejar para outra data**.
- **Remanejar:** quando o profissional muda o dia, leva todos os pacientes
  para a nova data **no mesmo horário** e mostra a lista com **telefones**
  para a recepção avisar cada um. Se algum paciente não couber, nada é
  alterado e a tela explica o motivo.
- Na **Central de Atendimento**, aparecem atalhos com as **próximas datas**
  desse profissional, e um aviso quando ele não atende na semana mostrada.

### 8. Agendamento
Se o profissional escolhido não tiver nenhum procedimento vinculado, a lista
de procedimentos agora mostra um aviso em vez de ficar vazia.

### 9. Chat interno (Mensagens)
- A lista de pessoas foi separada em **Online** (em cima) e **Offline**
  (embaixo, fechada; é só clicar para abrir). Se alguém offline deixou
  mensagem não lida, a lista de offline abre sozinha.
- As mensagens de dias anteriores agora são **apagadas sozinhas** no primeiro
  acesso de cada dia. Não precisa mais configurar nada no servidor (cron).

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
unzip -l /tmp/smedim-update.zip | grep 0026_convenio_remove
```

Tem que aparecer uma linha com `0026_convenio_remove_desconto_percentual_particular.py`.
**Se não aparecer nada, é o zip velho. Não siga.**

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

(O aviso sobre "backslashes" é normal.)

```bash
sudo cp -a /tmp/smedim-update-extraido/. /opt/smedim/backend/
```

Confira que chegou:

```bash
ls /opt/smedim/backend/mensagens/templates/mensagens/_contato.html
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

Devem aparecer linhas `Applying ... OK`, entre elas:
`agenda.0019` a `agenda.0022`, `cadastros.0023` a `cadastros.0026` e
`financeiro.0003`. Se alguma linha mostrar **erro**, pare e vá para o
Passo 12.

### Passo 9 — Arquivos estáticos

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
- [ ] **Mensagens:** aparecem as listas **Online** e **Offline**.
- [ ] **Financeiro → Saldos e pagamentos de repasse** abre.
- [ ] **Agenda → Repasses semanais** mostra o botão **Registrar pagamento**.
- [ ] **Cadastros → Convênios → (um convênio)**: aparecem o campo "prazo de
      retorno grátis (dias)" e, nos que usavam desconto, a Tabela de Preços
      preenchida.
- [ ] **Cadastros → Profissionais → Datas / exceções** abre com a opção
      "Repetir nos próximos meses".

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

## Parte 3 — O que a clínica precisa fazer depois

1. **Conferir a Tabela de Preços dos convênios que usavam desconto %.** Os
   valores foram copiados com o desconto aplicado em **todos** os
   procedimentos. Corrija os procedimentos que **não** tinham desconto.
   *(Cadastros → Convênios → abrir o convênio → Tabela de Preços)*
2. **Prazos de retorno:** nos convênios que só cobrem retorno em 15 dias,
   mude o "prazo de retorno grátis (dias)" para 15. *(Cadastros → Convênios →
   Editar)*
3. **Profissionais com valor fixo:** marque a opção e preencha a tabela de
   valores de cada um. Até isso ser feito, eles continuam pelo percentual.
   *(Cadastros → Profissionais → Editar)*
4. **Profissionais sem dia fixo:** cadastre as datas deles em **Datas /
   exceções**, usando "Repetir nos próximos meses".
5. **Avisar o financeiro** que o card "Repasse pendente" do Painel agora mostra
   o valor líquido. O valor menor é esperado.
6. **Cron do chat:** não precisa configurar mais nada. A limpeza das mensagens
   agora é automática.
