# Atualização 2026-10-23, tarde — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).
Detalhes de cada passo no [LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

**Novidades (2 migrações novas, só campos novos — o backup do passo 1 continua obrigatório):**

- **Totem:** a especialidade some quando o horário do profissional acaba (ex.: atende das 07h
  às 11h → às 11:01 não aparece mais).
- **Limite diário:** opção **"todos os ultrassons (USG)"** — um limite só para todos os USG.
- **Agendamento "que não salvava":** quando alguma regra impede salvar (ex.: procedimento que o
  profissional não atende naquele horário), agora aparece no **topo** o quadro vermelho
  **"Não foi possível salvar"** com o motivo. Antes o aviso ficava escondido ou fora da tela.
- **Repasse com valor fixo gravado errado:** comando novo `corrigir_repasse_fixo` — corrige o
  caso da Dra. Elaine em 20/10 (**passo 10**).
- **Até 5 retornos por profissional por dia:** o 6º em diante vira **encaixe** sozinho e fica
  com o aviso **"justificar"** na Agenda do dia.

- **Convênio "funciona como particular"** (opção nova no cadastro do convênio): quando o paciente
  de um convênio **marcado** paga no financeiro, o valor entra no **repasse semanal** do
  profissional e no **caixa do proprietário**, e sai de Contas a receber, lotes e repasse de
  convênio. Convênios não marcados continuam como antes. **Marcar a opção — passo 9.**
- **Corrigir pagamento** (Financeiro → Corrigir pagamento): corrige a forma, a divisão entre 2
  formas, o valor recebido do cartão, as parcelas e o **valor total** (com motivo) de um pagamento
  registrado **hoje ou ontem**.
- **Senhas do totem:** Prioritária que já estava esperando é chamada antes da Normal que chegou
  depois; se a Normal já esperava, no máximo **2 Prioritárias para 1 Normal** (estava saindo 3 P para 1 N).
- O **retorno grátis** — o automático (consulta feita no sistema
novo) e o do **sistema antigo** — não vai mais para a fila do financeiro. Na Agenda do dia, para
esses pacientes, o botão aparece como **"Autorizar (retorno, sem cobrança)"** e já deixa o
paciente **Autorizado**.

Atualização rápida: o sistema fica fora do ar só do passo 4 ao passo 7.

## 1. Backup do banco (obrigatório)

```bash
sudo docker exec smedim-postgres pg_dump -U clinica_smedim clinica_smedim > /opt/smedim/backups/backup_pre-update_$(date +%Y%m%d-%H%M).sql
ls -lh /opt/smedim/backups/
```

O `.sql` novo tem que ter alguns MB (não `0`). Se vier vazio, **pare aqui**.

## 2. Levar o zip para o servidor

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```

Copie o `smedim-update.zip` para `/tmp/smedim-update.zip`
(se caiu em Downloads: `cp ~/Downloads/smedim-update.zip /tmp/smedim-update.zip`).

## 3. Conferir que é o zip certo

```bash
unzip -p /tmp/smedim-update.zip LEIA-ME-ATUALIZACAO-CLINICA.md | grep -m1 "Resumo desta leva"
```

Tem que aparecer **"versão 2026-10-23, tarde"**. Se aparecer outra, é o zip velho: não siga.

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

## 6. Migrações (por garantia)

```bash
cd /opt/smedim/backend
sudo -u www-data venv/bin/python manage.py migrate
```

Tem que aparecer `cadastros.0031_limite_diario_todos_ultrassons... OK` e
`cadastros.0032_convenio_funciona_como_particular... OK`.

## 7. Subir o sistema

```bash
sudo systemctl start smedim
sudo systemctl status smedim
```

Tem que mostrar `active (running)`.

## 8. Testar

1. Na **Agenda do dia**, num paciente **Confirmado** que é retorno (marcado como "Retorno de
   consulta feita no sistema antigo", ou retorno automático de consulta feita no sistema novo),
   o botão aparece como **"Autorizar (retorno, sem cobrança)"**. Clicar → o paciente fica **Autorizado** na hora e **não** aparece na Fila
   de pagamento do financeiro.
2. Num paciente particular comum, o botão continua **"Encaminhar para pagamento"**.
3. **Senhas:** tirar 1 senha Normal e depois 4 Prioritárias, e chamar no painel → P, P, N, P, P.
   Tirar 3 Prioritárias e depois 1 Normal → as 3 P saem antes da N.
4. **Totem:** abrir o totem depois do fim do horário de um profissional que é o único da
   especialidade dele no dia → a especialidade não aparece mais.
5. **Limite USG:** Cadastros → Limites diários → Novo → marcar **"todos os ultrassons (USG)"**,
   deixar o procedimento em branco, salvar → a lista mostra "Todos os ultrassons (USG)".
6. **Retornos:** num dia em que um profissional já tem 5 retornos, agendar mais um → aparece o
   aviso de encaixe, e na Agenda do dia a linha mostra "🔶 Encaixe **justificar**".
7. **Agendamento:** editar um agendamento, escolher um procedimento que o profissional não atende
   naquele horário e salvar → aparece no topo **"Não foi possível salvar"** com o motivo.
8. **Convênio "funciona como particular"** (depois do passo 9): registrar o pagamento de um
   convênio marcado (cartão ou Pix) → ele aparece em **Agenda → Repasses semanais** como
   pendente do profissional e **não** aparece em Financeiro → Contas a receber. Num convênio
   **não** marcado, nada muda.
9. **Corrigir pagamento:** Financeiro → **Corrigir pagamento** → aparecem os pagamentos de hoje e
   ontem → abrir um, trocar a forma (ex.: Pix → Cartão débito) e salvar → a lista mostra a forma
   nova. Mudar o total sem motivo → o sistema pede o motivo; com motivo, salva e o fechamento do
   dia passa a mostrar o valor novo.

**Quem já estava na Fila de pagamento** antes da atualização continua lá: o financeiro abre
cada um e clica em **"Confirmar retorno e autorizar"**.

## 9. Marcar os convênios que funcionam como particular (obrigatório)

Cadastros → **Convênios** → abrir o convênio → **Editar** → marcar **"Funciona como particular"**
→ salvar. Faça só nos convênios em que o pagamento feito na clínica é repassado ao profissional
como no particular. Os atendimentos antigos desse convênio que **já foram pagos** ao profissional
pelo repasse de convênio ficam como já repassados — não voltam como pendentes.

Se algo der errado, siga o **Passo 12 (rollback)** do
[LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

## 10. Corrigir o repasse da Dra. Elaine do dia 20/10

O pagamento no **cartão débito** do Geraldo foi registrado antes de marcarem "Cobra valor fixo"
para ela, e ficou com o repasse de 30% (R$ 161) em vez do valor fixo (R$ 150). Por isso o
fechamento de 20/10 mostrava retenção de R$ 149 em vez de R$ 160.

**10.1 — Ver o que vai ser corrigido (não altera nada):**

```bash
cd /opt/smedim/backend
sudo -u www-data venv/bin/python manage.py corrigir_repasse_fixo --profissional 9
```

Tem que aparecer uma linha **"A CORRIGIR"** do Geraldo: *Cartão débito R$ 230.00: repasse
gravado R$ 161.00, pelo valor fixo seria R$ 150.00*. Vitor (cortesia) e Natalia (convênio) **não**
aparecem — estão certos. Se aparecer algo diferente disso, **pare** e me mande o que saiu.

**10.2 — Aplicar:**

```bash
sudo -u www-data venv/bin/python manage.py corrigir_repasse_fixo --profissional 9 --confirmar
```

Tem que aparecer **"CORRIGIDO"** na linha do Geraldo.

**10.3 — Conferir:** Financeiro → Fechamento diário → 20/10/2026 → profissional Dra. Elaine →
retenção **R$ 160,00** e repasse **R$ 300,00** (já pago R$ 150,00 · pendente R$ 150,00).
(Se no passo 9 o convênio da Natalia — o Pix de R$ 185 — for marcado como "funciona como
particular", esse valor também passa a aparecer no fechamento dela, somado a estes.)

Se aparecer alguma linha **"ACERTO MANUAL"** (pagamento em dinheiro ou já repassado), o comando
não mexe nela: a diferença mostrada é o que precisa ser acertado com a profissional fora do sistema.

## 11. Limpar os temporários

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```
