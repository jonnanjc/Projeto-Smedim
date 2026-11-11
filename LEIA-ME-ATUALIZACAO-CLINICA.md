# Atualização do sistema já instalado — servidor Ubuntu da clínica

Este guia é para depois que a instalação inicial ([LEIA-ME-INSTALACAO-CLINICA.md](LEIA-ME-INSTALACAO-CLINICA.md))
já estiver rodando na clínica (Gunicorn + Nginx + systemd + PostgreSQL no ar,
serviço `smedim` ativo). Use-o **sempre que houver uma leva de mudanças no
código prontas para ir para produção** — não repita o guia de instalação,
que cria banco vazio e reconfigura tudo do zero.

Diferente da instalação, aqui o banco **tem dado real** — o cuidado central
deste guia é nunca perder dado no processo.

> **Resumo desta leva (versão 2026-11-10):**
> - **1 migração (accounts.0020), sem collectstatic.** Duas permissões novas, ativadas por usuário
>   (Usuários → permissões extras, ou Perfis), grupo "Fila / Senha":
>   - **Ver quem está aguardando na fila de senhas** — lista com senha, hora de chegada e tempo de
>     espera, no Painel de chamada e na tela nova **AGENDA → Fila de espera** (atualiza a cada 30 s);
>   - **Chamar uma senha específica, fora da ordem** — botão "Chamar esta" na lista (precisa também de
>     "Chamar a próxima senha" e do guichê do dia); fica no log de auditoria.
> - O perfil Administrador já sai com as duas. Os demais usuários: marcar à mão (**nunca** rodar `seed_perfis`).
> - Tela nova **AGENDA → Fila dos profissionais**: para cada profissional, quem já passou pela recepção
>   (autorizado) e aguarda ser chamado, na mesma ordem da Minha agenda, com **quantos estão na frente**
>   e busca pelo nome. Aparece para quem autoriza na recepção ou tem "Ver quem está aguardando".
> - **Cancelar pacote** cadastrado direto no paciente (sistema antigo), em AGENDA → Pacotes dos pacientes,
>   para quem pode cadastrar esse pacote. Atendimentos marcados com ele voltam a ser cobrados; bloqueado
>   se o pacote está sendo cobrado ou já foi usado em atendimento autorizado/realizado. Fica no log.
> - Se a versão 2026-11-09 (abaixo) ainda não foi aplicada, ela vai junto neste zip.
>
> **Versão 2026-11-09:**
> - **Sem migração, sem collectstatic.** Só muda `fila/models.py`: o ciclo de chamada **P-P-N agora é
>   separado por especialidade**. Guichê cobrindo duas especialidades (ex.: Cardiologia + Fisioterapia)
>   não chama mais só P de uma delas — cada especialidade tem a sua vez de N. Entre as especialidades,
>   sai primeiro o nível especial (idoso 80+/gestante emergência), senão quem chegou antes. Exame com
>   preparo (senha E) continua na frente de tudo.
> - **Fila travada só em P:** na vez do N, quando um P chegou antes do N, agora sai o **P mais antigo**
>   (antes saía o primeiro da ordem da lei — uma gestante das 08:12 era sempre ultrapassada por idosos 60+
>   e, como chegou antes de todos os N, nenhum N de Cardiologia saía).
> - Se a versão 2026-11-07 (abaixo) ainda não foi aplicada, ela vai junto neste zip.
>
> **Versão 2026-11-07:**
> - **Sem migração, sem collectstatic.** Impressão do **receituário e do atestado**: texto todo em preto,
>   letras maiores, título em negrito centralizado, logo da clínica maior (72px) com nome e endereço ao lado.
>   O aviso "Receituário emitido"/"Atestado emitido" não sai mais no papel (continua na tela) — vale para
>   qualquer aviso desse tipo em qualquer impressão. Data e assinatura ficam no pé da folha. Endereço no
>   cabeçalho: bairro agora é **Centro** (antes "Bairro Novo").
>
> **Versão 2026-11-06-fim-da-noite (já aplicada na clínica):**
> - **Sem migração.** Só o texto da tela Possíveis duplicados: o exemplo passou a ser "Fatima" e "Fátima".
>
> **Versão 2026-11-06-noite (já aplicada na clínica):**
> - **1 migração nova** (`cadastros.0035_duplicidade_ignorada`, tabela nova vazia).
> - **Unificação de pacientes duplicados:** tela nova Cadastros → Pacientes → **Possíveis duplicados**
>   (permissão "Excluir cadastro de pacientes"); comando `conferencia_duplicados` para aplicar a conferência
>   feita na cópia do banco; `unificar_pacientes_duplicados` agora move também pacotes e guias de convênio.
>   Cadastro de paciente novo com nome já existente avisa e pede "é outra pessoa". Passo manual depois da
>   atualização: ver ATUALIZACAO-2026-11-06-NOITE-PASSO-A-PASSO.md.
>
> **Versão 2026-11-06 (já aplicada na clínica):**
> - **1 migração nova** (`cadastros.0034_profissional_acupuntura_primeiro_na_fila`, só um campo novo,
>   desmarcado para todos os profissionais).
> - **Dinheiro com ponto de milhar:** telas, painéis, fechamentos e mensagens mostram **150.000,00** (antes
>   150000,00, e algumas mensagens até 150000.00). Os campos de valor aceitam digitar com ou sem pontos e
>   formatam ao sair do campo. Contagens e códigos (ex.: ano, nº do paciente) não ganham ponto.
> - **Editar agendamento:** marcar só "Retorno de consulta do sistema antigo" (sem mexer no procedimento)
>   não salvava — agora salva (vale também para desmarcar).
> - **Cadastro de paciente:** ao editar (ex.: só incluir o convênio), a **data de nascimento** e a validade da
>   carteirinha sumiam — agora vêm preenchidas e não se perdem. Datas já apagadas não voltam sozinhas.
> - **Agenda do dia (resumo do profissional):** retornos contados à parte, em roxo (ex.: "Consulta ×5" e
>   "Consulta – retorno ×3").
> - **Retorno acima do limite diário:** só salva com o **motivo do encaixe** nas observações. Exceção: editar
>   um agendamento **do próprio dia** não trava (fica "justificar" na Agenda do dia) — e encaixe que já
>   existia também não trava a edição no dia.
> - **Botão da Agenda do dia (particular):** pacote já pago mostra **"Autorizar (pacote já pago)"** em vez de
>   "Encaminhar para pagamento" (retorno já mostrava "Autorizar (retorno, sem cobrança)").
> - **Evoluções pendentes (Minha agenda):** só para a equipe multi de fato (fisio, fono, psicologia,
>   nutrição, TO, educação física); profissional com qualquer especialidade médica não vê mais a lista.
> - **Acupuntura no começo da fila:** opção nova em Cadastros → Profissionais. Marcada, a acupuntura passa
>   na frente de quem aguarda na Minha agenda daquele profissional (só fica atrás de quem já está em
>   atendimento). Ninguém vem marcado.
>   Se a versão 2026-11-03 ainda não foi aplicada, ela vem junto (itens abaixo).
>
> **Versão 2026-11-03:**
> - **Sem migração nova.** Só o **Relatório detalhado** (Financeiro) mudou:
>   - **Filtros** iguais aos do sistema antigo: Data **até** data, **Convênio** (ou Particular), **Nome do
>     paciente** e **Profissional**, com atalhos **Hoje** / **Este mês**.
>   - Coluna nova **Guia**: mostra a sessão na guia do convênio (ex.: **2/10 · KNZEM94**), calculada pelo
>     sistema — o que antes a recepção escrevia à mão na observação. A última sessão da guia fica em laranja.
>   - **Mais rápido**: o mês inteiro carregava devagar (milhares de consultas ao banco); agora são poucas.
> - **Tabela de preços do convênio** (Cadastros → Convênios → abrir): **50 por página** e busca por
>   procedimento/código/profissional — convênio com 648 procedimentos vinha numa página só, e lento.
>   Se a versão 2026-10-29 ainda não foi aplicada, ela vem junto (itens abaixo).
>
> **Versão 2026-10-29:**
> - **1 migração nova** (`agenda.0035_agendamento_motivo_prioridade`, só um campo novo — todos os atendimentos
>   já existentes ficam como "Normal").
> - **Prioridade na fila do profissional (Minha agenda):** paciente prioritário passa na frente dos normais já
>   autorizados, **alternando 1 prioritário e 1 normal**. A recepção marca o motivo no campo **Prioridade** ao
>   "Encaminhar para pagamento" (particular) ou na tela de **Autorizar** (convênio); dá para corrigir com
>   **"Salvar prioridade"** na Agenda do dia até o profissional chamar. Na Minha agenda aparece o selo
>   laranja "P · motivo". A senha do totem (ciclo P, P, N e senha E) não mudou.
> - **Tema escuro:** a lista aberta dos campos de escolha (ex.: o de Prioridade) ficava com texto claro em
>   fundo branco, ilegível — agora abre escura.
>   Se a versão 2026-10-28 ainda não foi aplicada, ela vem junto (itens abaixo).
>
> **Versão 2026-10-28:**
> - **Sem migração nova.**
> - **Busca do Prontuário:** ignora acento e maiúscula ("joao" acha "João"), aceita as palavras em qualquer
>   ordem e CPF com ou sem pontos — antes o profissional não achava o paciente que estava na lista de "Evoluir"
>   (369 nomes do sistema antigo têm espaço duplo, que a tela mostra como um só: o nome copiado não batia).
> - **Lista "Atendimentos anteriores sem registro" (Minha agenda):** só para a equipe multi (médico não vê
>   mais); e o atendimento sai da lista também quando o profissional evolui pela busca do Prontuário, com a
>   data do atendimento (o registro fica vinculado ao atendimento daquele dia). Evoluções já feitas assim
>   antes desta versão também passam a contar.
> - **Meu fechamento (diário e mensal):** aparece no menu Financeiro para **todo usuário vinculado a um
>   profissional**, sem precisar marcar permissão no perfil. No topo, **"Quanto eu levo"**: particular +
>   convênio, **já com o desconto da clínica**; os convênios passam a mostrar valor (antes só a quantidade).
>   Se a versão 2026-10-27 (tarde) ainda não foi aplicada, ela vem junto (itens abaixo).
>
> **Versão 2026-10-27, tarde:**
> - **2 migrações novas** (`agenda.0034_pacote_do_sistema_antigo`, só campos novos, e
>   `accounts.0019_permissao_cadastrar_pacote_paciente`, permissão nova já marcada no perfil Administrador).
> - **Venda de pacote no fechamento:** vender o pacote na sessão de hoje contava 2 procedimentos (a venda e a
>   sessão "pelo pacote"); agora conta 1, em "por procedimento" e em "por forma de pagamento".
> - **Pacote do sistema antigo:** em **Pacotes dos pacientes → "+ Cadastrar pacote do sistema antigo"** dá para
>   colocar o pacote direto no paciente (sem agendamento), informando quantas sessões já foram usadas e se
>   está **a pagar** ou **já pago no sistema antigo**. A pagar: as sessões passam pelo financeiro, que pode
>   **"Cobrar pacote agora"** em qualquer atendimento particular do paciente com o profissional (entra no caixa
>   daquele dia) ou só confirmar sem cobrar. Só faz isso quem tiver a permissão nova **"Cadastrar pacote no
>   paciente sem agendamento"** (Perfis/Usuários → Agenda) — depois de atualizar, marque para quem vai usar.
>   Se a versão 2026-10-27 (manhã) ainda não foi aplicada, ela vem junto (itens abaixo).
>
> **Versão 2026-10-27, manhã:**
> - **Sem migração nova.** **Produção do fechamento filtrado por um profissional:** o Particular também
>   sai já com o desconto da clínica (igual ao repasse líquido), não mais o valor cobrado.
> - **Cadastros → Procedimentos:** campo de **busca** por nome ou código.
>   Se a versão 2026-10-26 (fim da noite) ainda não foi aplicada, ela vem junto (itens abaixo).
>
> **Versão 2026-10-26, fim da noite:**
> - **1 migração nova**, só de dados (`agenda.0033_repasse_dinheiro_registrado`): marca como já repassado
>   o dinheiro registrado até 25/10 (regra antiga, o profissional levava na hora).
> - **Dinheiro fica no caixa e entra no repasse:** desde 26/10 a parte do profissional no pagamento em
>   dinheiro aparece como pendente em Repasses (igual cartão/Pix) até ser registrado o pagamento a ele.
> - **Visão do proprietário:** "Em dinheiro" passa a ser o dinheiro físico no caixa (inclui convênio pago
>   na clínica em dinheiro e a parte dos profissionais ainda não repassada); repasse pago em dinheiro
>   do caixa não desconta mais duas vezes do resultado. **Proprietário** não aparece mais como repasse pendente.
> - **Fechamento de um profissional** (filtrado): convênio já com o desconto da clínica.
> - **Painel de chamada:** lista de senhas chamadas hoje com **"Chamar de novo"** (mesmo guichê de quem chamou).
>   **Totem** ignora toque duplo (não emite duas senhas).
> - **Prontuário:** o autor pode **apagar** um registro feito errado; **Minha agenda** lista os atendimentos
>   dos últimos 30 dias sem registro, com **"Evoluir"** (já com a data do atendimento).
> - **Fechamentos contam procedimentos:** atendimento com 2 procedimentos conta 2 no total do Particular
>   (por procedimento e por forma de pagamento) e no Convênio. **Início do profissional:** cartão novo
>   "Procedimentos realizados" ao lado de "Já atendidos".
>   Se a versão 2026-10-26 (noite) ainda não foi aplicada, ela vem junto (itens abaixo).
>
> **Versão 2026-10-26, noite:**
> - **Sem migração nova.** **Pagamento gravado em dobro** (clique duplo / envio repetido em "Registrar e
>   autorizar"): a tela agora trava o atendimento — o segundo envio só mostra "pagamento já está
>   registrado". Todas as telas do sistema ignoram o segundo clique num botão de salvar.
> - **Corrigir pagamento** reconhece o pagamento em dobro (aviso amarelo, total já certo) e, ao salvar,
>   apaga as linhas repetidas sem mexer no valor da consulta. Comando `remover_pagamentos_duplicados`
>   limpa todos de uma vez (passo 4 do passo a passo).
>   Se a versão 2026-10-26 (tarde) ainda não foi aplicada, ela vem junto (itens abaixo).
>
> **Versão 2026-10-26, tarde:**
> - **3 migrações novas** (`fila.0008_senha_tipo_exame_preparo`, `cadastros.0033_procedimento_preparo_senha_totem`
>   e `prontuario.0006_entrada_data_atendimento` — esta preenche a data dos registros que já existem).
> - **Exame com preparo:** não fura mais a fila na Minha agenda do profissional (lá vale a ordem de
>   autorização). No lugar, o **totem** oferece a senha **"Exame com preparo"** (código **E001**…) na
>   especialidade que tem exame com preparo marcado no dia, e a recepção chama essas senhas **antes** de
>   Prioritário e Normal, por ordem de chegada (o padrão 2 P para 1 N continua igual).
> - **Prontuário — data do atendimento:** o formulário tem o campo **"Data do atendimento"** (padrão: dia
>   do agendamento, ou hoje). Quem evolui dias depois escolhe a data certa; o prontuário mostra essa data
>   e, se foi digitado em outro dia, "registrado em …". Não aceita data no futuro.
>   Se a versão 2026-10-26 (manhã) ainda não foi aplicada, ela vem junto (itens abaixo).
>
> **Versão 2026-10-26, manhã:**
> - **1 migração nova**, só campo novo (`prontuario.0005_evolucao_equipe_multi`). **Prontuário da
>   equipe multi** (quem não é médico): formulário com um campo só, "Evolução / registro do
>   atendimento", com link para o formulário completo se precisar. Médico continua com o completo.
> - **Quem vê o quê no prontuário:** registro de médico fica visível a todos os profissionais;
>   registro da equipe multi e de saúde mental (inclusive Psiquiatria) só quem escreveu vê — o
>   colega da multi não vê nem que ele existe.
>   Se a versão 2026-10-23 (fim da noite) ainda não foi aplicada, ela vem junto (itens abaixo).
>
> **Versão 2026-10-23, fim da noite:**
> - **Sem migração nova.** **Central de Atendimento:** botão **"Repetir último agendamento"** — abre a
>   agenda do mesmo profissional e, ao clicar num horário vago, o agendamento já vem com profissional,
>   forma de atendimento, convênio e procedimentos; falta só escolher o paciente. O botão também aparece
>   no Novo agendamento. **Busca de profissional** (digitar parte do nome) em todos os campos de
>   profissional; na Central dá para buscar o profissional direto, sem escolher a especialidade.
>   **Agenda do dia** não desmarca mais o profissional ao trocar a data.
>   Se a versão 2026-10-23 (noite) ainda não foi aplicada, ela vem junto (itens abaixo).
>
> **Versão 2026-10-23, noite:**
> - **Sem migração nova.** **Corrigir pagamento** sem limite de data: a lista tem filtro de período
>   (padrão últimos 7 dias; atalhos Hoje / Ontem / Últimos 7 dias / Este mês) e, buscando pelo nome
>   do paciente, acha em qualquer data. Continua bloqueado se o repasse já foi pago ou houve estorno.
>   Se a versão 2026-10-23 (tarde) ainda não foi aplicada, ela vem junto (itens abaixo).
>
> **Versão 2026-10-23, tarde:**
> - **2 migrações novas**, só campos novos (`cadastros.0031_limite_diario_todos_ultrassons` e
>   `cadastros.0032_convenio_funciona_como_particular`). **Convênio "funciona como particular"**
>   (opção nova no cadastro do convênio): o que o paciente paga na clínica entra no repasse
>   semanal e no caixa do proprietário, e sai de Contas a receber, lotes e repasse de convênio.
>   **Depois de atualizar, marque a opção nos convênios que funcionam assim.**
> - **Corrigir pagamento** (Financeiro → Corrigir pagamento): corrige forma, divisão entre 2 formas,
>   valor recebido do cartão, parcelas e **valor total** (com motivo) de pagamento registrado **hoje ou ontem**.
> - **Agendamento que "não salvava":** o motivo agora aparece no topo ("Não foi possível salvar").
>   Comando novo `corrigir_repasse_fixo` para pagamento de profissional com valor fixo que ficou
>   gravado pelo percentual (caso da Dra. Elaine, 20/10 — passo 10 do passo a passo da tarde).
> - **Totem** esconde a especialidade quando o horário do profissional acaba. **Limite diário**
>   com opção "todos os ultrassons (USG)". **Até 5 retornos por profissional por dia** — o 6º em
>   diante vira encaixe, para justificar depois.
> - **Senhas do totem:** Prioritária que já estava esperando é chamada antes da Normal que chegou
>   depois; com a Normal esperando desde antes, no máximo **2 Prioritárias pra 1 Normal**.
> - **Retorno grátis** (automático ou do sistema antigo, sem cobrança)
>   não vai mais para a fila do financeiro: na Agenda do dia o botão vira **"Autorizar (retorno,
>   sem cobrança)"** e autoriza direto. Se a versão 2026-10-23 (manhã) ainda não foi aplicada, ela vem
>   junto (itens abaixo).
>
> **Versão 2026-10-23 (manhã):**
> - **2 migrações novas** em 23/10 (`accounts.0018_permissao_cancelar_autorizado` e
>   `agenda.0032_agendamento_encaminhado_pagamento_em`) — só permissão e campo novos,
>   não alteram dado existente.
> - **Cancelamento em dois critérios:** na Agenda só se cancela (e marca Faltou) o que
>   **ainda não foi autorizado**; o já autorizado se cancela em **Agenda → Autorizações**,
>   com motivo, só para quem tiver a permissão nova **"Cancelar atendimento já autorizado"**.
>   **Depois de atualizar, marque essa permissão nos perfis certos** (Administração → Perfis) —
>   até isso, ninguém cancela atendimento autorizado.
> - **Registrar pagamento:** botão para cancelar quando o paciente desiste no caixa; e quem foi
>   encaminhado para pagamento **guarda a vez** na fila de chamada.
> - **Prontuário:** idade do paciente (ou "(pendente cadastro)"), plano do cadastro e plano
>   autorizado no atendimento. **Recepção 2:** a voz agora chama todos da leva.
> - **Fechamento diário/mensal:** convênios agora **com valor** (por convênio e procedimento) e
>   nova seção **Produção** (particular + convênio = produção total).
> - **Profissional:** tela nova Agenda → **Listagem de atendidos** (o que ele já atendeu).
> - Se a versão **2026-10-16** ainda não foi aplicada, ela vem junto (itens abaixo — tem o
>   passo 6b, contagem do status automático, e o 6c, voz no servidor).
> - **4 migrações novas** em 16/10 (`cadastros.0030_atendimento_profissional`,
>   `fila.0007_senha_profissional`, `agenda.0030_assinatura_e_retorno_manual` e `agenda.0031_registro_da_assinatura`) —
>   só tabela/campos novos, não alteram dado existente.
> - **Autorizar "sem assinatura"** (1ª guia e guias extras) — fica em Guias
>   pendentes até marcar "Assinou"; a profissional vê as dela em "Minhas guias
>   pendentes". **Retorno de consulta do sistema antigo** marcado à mão no agendamento. **Senha por profissional:** numa especialidade com 2+ profissionais
>   no dia, o totem pergunta "Com qual profissional?" e cada atendente marca, em
>   "Especialidades de hoje", quais profissionais atende.
> - Também em 16/10: telefone do paciente na lista da Agenda do
>   dia, painel de chamada com guichê maior e **voz** (senha + guichê; primeiro
>   nome + consultório), e "Ver agenda do dia" do Início do profissional sem
>   erro 403. Ver a seção da versão 2026-10-16 (tem ajuste no PC da TV do painel).
> - **Status automático (16/10):** no dia do atendimento, todo **Agendado** vira
>   **Pré-confirmado** sozinho; na virada do dia, quem ficou em Agendado ou
>   Pré-confirmado vira **Faltou**. **No primeiro acesso depois de atualizar,
>   isso vale para todo o histórico** — o passo a passo mostra como contar antes.
> - Se a versão **2026-10-15, fim da tarde** ainda não foi aplicada, ela vem
>   junto (itens abaixo).
> - **Sem migração nova** na versão 2026-10-15, fim da tarde: só a tela de registrar pagamento
>   (valor da 1ª e da 2ª forma, 2ª calculada sozinha com o desconto). Ver a
>   seção da versão "2026-10-15, fim da tarde". Tudo abaixo vem junto se ainda
>   não foi aplicado.
> - **2 migrações novas** (`agenda.0028_guia_extra_data` e
>   `agenda.0029_desconto_pagamento`) — tabela e campos novos, não alteram dado
>   existente. Data da autorização da 2ª guia em **Guias pendentes**; **desconto**
>   no registro do pagamento (com repasse acordado para quem cobra valor fixo);
>   valores fixos do profissional só com os procedimentos dele. Se a versão da manhã de 15/10 ainda
>   não foi aplicada, ela vem junto (item abaixo).
> - Versão da manhã de 15/10: **1 migração** (`agenda.0027_autorizacao_e_guias_convenio`) — só
>   campos e tabela novos, não altera dado existente.
> - **Autorizar convênio agora abre uma tela** (observação, nº da guia, guia de
>   sessões do plano com saldo) e há as telas novas **Agenda → Autorizações** e
>   **Agenda → Guias do convênio**. Ver a seção da versão 2026-10-15.
> - **Traz junto a correção do erro 500 ao marcar a 2ª guia** (versão
>   2026-10-14, tarde) — se ela ainda não foi aplicada, não precisa de nada a
>   mais.
> - Se o pacote da **manhã de 14/10** ainda não foi aplicado, vem junto mais 1
>   migração (`cadastros.0029_profissional_convenios_atendidos`).
>   **Depois de atualizar, todo profissional fica sem convênio marcado** (só
>   particular) até marcar em Cadastros → Profissionais → "Convênios que
>   atende" — ver a seção da versão 2026-10-14.
> - Se a versão **2026-10-13** ainda não tinha sido aplicada no servidor, ela
>   vem junto neste pacote (mais 6 migrações, `agenda.0023` a `agenda.0026`,
>   `cadastros.0027`, `cadastros.0028`). A `agenda.0024` **altera dados**
>   (converte as sessões de 1h por convênio já agendadas em 2 guias) — **o
>   backup do Passo 2 é obrigatório**.
> - Sem dependência nova. **Rode o Passo 9 (`collectstatic`) por garantia** —
>   esta versão não precisa, mas a 2026-10-09 precisa (ver último item).
> - **Depois de atualizar, tem um passo manual:** unificar os pacientes
>   duplicados (comando `unificar_pacientes_duplicados`, simulação primeiro).
>   Ver a seção da versão 2026-10-13 e o
>   [ATUALIZACAO-2026-10-13-PASSO-A-PASSO.md](ATUALIZACAO-2026-10-13-PASSO-A-PASSO.md).
> - Se a versão **2026-10-09** ainda não tinha sido aplicada no servidor, ela
>   vem junto neste pacote — faça também o "Ajuste no PC do totem" da seção
>   dela.

## Antes de começar: confirme que a base está saudável

```bash
sudo systemctl status smedim
sudo systemctl is-enabled smedim
```

Se `is-enabled` não disser `enabled`, resolva isso primeiro (fora do escopo
desta atualização, é pendência da instalação):

```bash
sudo systemctl enable smedim
```

## Antes de colar comandos no terminal (leia uma vez)

- **Um bloco por vez.** Cole, aperte Enter, espere o prompt voltar, e só então
  cole o próximo.
- **Comando longo pode ser cortado ao colar.** Se aparecer uma tela enorme de
  ajuda (`Usage: ...`, `--help`) ou "erro de sintaxe/uso", o comando foi
  cortado no meio e **nada foi executado** — cole de novo, ou digite à mão.
- **Comandos com `sudo` pedem a senha do usuário `smedim`.** Ao digitar,
  nada aparece na tela; é normal.
- **Permissão negada** em algo dentro de `/opt/smedim` = faltou `sudo` (ou
  `sudo -u www-data`, para `manage.py` e `pip`). Nada foi alterado, é só
  repetir com `sudo`.

## Checklist rápido

- [ ] Passo 1 — Gerar o pacote novo no seu PC
- [ ] Passo 2 — Backup do banco de produção (antes de qualquer coisa)
- [ ] Passo 3 — Levar o pacote para o servidor
- [ ] Passo 4 — Parar o serviço
- [ ] Passo 5 — Guardar uma cópia da versão atual (para rollback rápido)
- [ ] Passo 6 — Substituir o código (mantendo `.env`, `venv` e `staticfiles`)
- [ ] Passo 7 — Atualizar dependências (se `requirements.txt` mudou)
- [ ] Passo 8 — Rodar migrações novas
- [ ] Passo 9 — `collectstatic` (se mudou algo em CSS/JS/imagens)
- [ ] Passo 10 — Corrigir permissões
- [ ] Passo 11 — Subir o serviço de novo e testar
- [ ] Passo 12 — Se algo quebrar: rollback

---

## Passo 1 — Gerar o pacote novo no seu PC (Windows)

Mesmo processo da Parte 1 da instalação — pasta limpa, sem `venv`/`db.sqlite3`/`.env`:

```powershell
Copy-Item -Recurse -Path "backend" -Destination "smedim-update"
Remove-Item -Recurse -Force "smedim-update\venv"
Remove-Item -Force "smedim-update\db.sqlite3" -ErrorAction SilentlyContinue
Remove-Item -Force "smedim-update\db.sqlite3.bak-*" -ErrorAction SilentlyContinue
Remove-Item -Force "smedim-update\.env" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "smedim-update\.claude" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "smedim-update\media" -ErrorAction SilentlyContinue
# Modelo da voz do painel (63 MB): instalado uma vez só no servidor, não vai no zip.
Remove-Item -Force "smedim-update\voz\*.onnx*" -ErrorAction SilentlyContinue
Get-ChildItem -Path "smedim-update" -Include "__pycache__" -Recurse | Remove-Item -Recurse -Force
Compress-Archive -Path "smedim-update\*" -DestinationPath "smedim-update.zip" -Force
```

Antes de levar para o servidor, **anote aqui o que mudou nesta versão**
(preencha manualmente ou peça pro Claude preencher no fim da sessão de
mudanças) — ajuda a saber, no Passo 8, se dá pra esperar migração nova e, no
Passo 9, se mexeu em estático:

```
## Versão: <data de hoje>
- [ ] Tem migração nova? (sim/não)
- [ ] Mexeu em static/templates? (sim/não)
- [ ] Mexeu em requirements.txt? (sim/não)
- O que mudou: ...
```

## Versão: 2026-11-06-fim-da-noite (preenchido pelo Claude ao fim da sessão)

- [ ] Tem migração nova? Não.
- [ ] Mudou `requirements.txt`? Não.
- [ ] Mudou CSS/JS/imagens estáticas? Não (só o template `cadastros/paciente_duplicados.html`).
- [ ] Mudou `.env`? Não.
- [ ] Passo manual depois? Não.

## Versão: 2026-11-06-noite (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Sim, 1:** `cadastros.0035_duplicidade_ignorada` (tabela nova, começa vazia).
- [ ] Mudou `requirements.txt`? Não.
- [ ] Mudou CSS/JS/imagens estáticas? Não (só templates) — sem `collectstatic`.
- [ ] Mudou `.env`? Não.
- [x] Passo manual depois: aplicar a conferência de duplicados (`conferencia_duplicados importar` e depois
  `unificar_pacientes_duplicados --confirmar`), com backup antes e fora do horário — ver
  ATUALIZACAO-2026-11-06-NOITE-PASSO-A-PASSO.md. Ensaio feito em 06/11 23:03 numa cópia: 2.361 fichas
  juntadas, produção idêntica.

## Versão: 2026-11-06 (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Sim, 1:** `cadastros.0034_profissional_acupuntura_primeiro_na_fila` (campo
  booleano novo em Profissional, padrão desmarcado).
- [x] Mexeu em static/templates? **Só templates** (JavaScript embutido em `accounts/base.html`,
  `agenda/agendamento_confirmar_pagamento.html`, `agenda/pacote_cadastrar_manual.html`,
  `agenda/agenda_dia.html`, `financeiro/lote_dar_baixa.html`, `financeiro/retorno_lancar.html`); o Passo 9 não
  é necessário. Recarregar com **Ctrl+F5**.
- [x] Mexeu em requirements.txt? **Não**.
- Testado: 463 testes (suíte inteira) + conferência no navegador (movimentação avulsa de 150.000,00 no
  banco de desenvolvimento, painel financeiro).
- Ponto de milhar: `accounts/moeda.py`. Nas telas, só **Decimal** ganha ponto (envolve
  `django.template.base.localize` em `AccountsConfig.ready`); inteiros (ids, ano, contagens) não — por isso
  NÃO se usa `USE_THOUSAND_SEPARATOR`. Campos DecimalField de dinheiro viram `CampoMoeda` (texto,
  aceita "150.000,00") via `preparar_campos_moeda` nos `EstiloCampoMixin`; `window.Moeda` no `<head>`.
- Passo a passo curto: `ATUALIZACAO-2026-11-06-PASSO-A-PASSO.md`. Depois de atualizar, marcar "Acupuntura no
  começo da fila" no profissional da acupuntura.
- O que mudou: ponto de milhar; edição de agendamento salvando retorno do sistema antigo (formset regrava
  itens existentes); data de nascimento na edição de paciente (`format="%Y-%m-%d"`); retornos à parte no
  resumo da Agenda do dia; retorno acima do limite exige motivo (exceto edição no dia); botão "Autorizar
  (pacote já pago)"; evoluções pendentes só equipe multi (`prontuario.models.eh_equipe_multi`); acupuntura
  no começo da fila por profissional.

## Versão: 2026-11-03 (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Não**.
- [x] Mexeu em static/templates? **Só templates** (`financeiro/relatorio_detalhado.html`,
  `cadastros/convenio_detalhe.html`); o Passo 9 não é necessário. Recarregar com **Ctrl+F5**.
- [x] Mexeu em requirements.txt? **Não**.
- Testado: 344 testes (agenda + financeiro) + 25 (cadastros).
- Tabela de preços do convênio (`cadastros.views.convenio_detalhe`): paginada em 50 (`paginar`), busca `q`,
  `select_related("profissional")` (antes 1 consulta por linha).
- Passo a passo curto: `ATUALIZACAO-2026-11-03-PASSO-A-PASSO.md`.
- O que mudou: Relatório detalhado com filtros (período de/até, convênio ou particular, paciente,
  profissional), coluna "Guia" com a sessão X/Y da guia do convênio (`GuiaConvenio.faixas_dos_itens`) e sem
  consultas por linha. Os links antigos (`?data=` do Fechamento diário e `?modo=mes`) continuam valendo.

## Versão: 2026-10-27, tarde (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Sim, 2**: `agenda.0034_pacote_do_sistema_antigo` (campos novos no pacote vendido —
  `cadastro_manual`, `usadas_antes` — e "atendimento da venda" passa a aceitar vazio) e
  `accounts.0019_permissao_cadastrar_pacote_paciente` (permissão nova `pacote_paciente_cadastrar`, já dada ao
  perfil Administrador). Não alteram dado existente.
- [x] Mexeu em static/templates? **Só templates** (`pacotes_vendidos.html`, `agendamento_confirmar_pagamento.html`
  e o novo `pacote_cadastrar_manual.html`, menu em `base.html`); o Passo 9 não é necessário. Recarregar com **Ctrl+F5**.
- [x] Mexeu em requirements.txt? **Não**.
- Testado: 411 testes (todos).
- Passo a passo curto: `ATUALIZACAO-2026-10-27-TARDE-PASSO-A-PASSO.md`.
- O que mudou:
  - **Fechamento — venda de pacote:** item que usa o pacote no próprio atendimento da venda não conta de
    novo (`financeiro.views._itens_particular`).
  - **Pacote do sistema antigo** (`PacoteVendido.cadastro_manual`): tela `agenda/pacotes/cadastrar/`
    (permissão própria "Cadastrar pacote no paciente sem agendamento", marcada em Perfis/Usuários). Vale desde o cadastro; "a pagar" faz as sessões cobertas
    passarem pelo financeiro, que cobra com "Cobrar pacote agora"; estorno dessa cobrança volta o pacote para
    "a pagar"; cancelar/retirar não apaga o pacote.

## Versão: 2026-10-27, manhã (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Não.**
- [x] Mexeu em static/templates? **Só templates** (`_fechamento_secoes.html`, `procedimento_lista.html`);
  o Passo 9 não é necessário. Recarregar com **Ctrl+F5**.
- [x] Mexeu em requirements.txt? **Não**.
- Testado: 401 testes (todos).
- Passo a passo curto: `ATUALIZACAO-2026-10-27-PASSO-A-PASSO.md`.
- O que mudou:
  - **Produção (fechamento diário/mensal filtrado por profissional comum):** Particular = repasse líquido
    do profissional (já sem a retenção da clínica, inclusive valor fixo); rótulo "já com desconto de N%".
    Visão geral e proprietário continuam com o valor cobrado.
  - **Cadastros → Procedimentos:** busca por nome ou código (`?q=`), mantida na paginação.

## Versão: 2026-10-26, fim da noite (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Sim, 1, só de dados** (`agenda.0033_repasse_dinheiro_registrado`).
- [x] Mexeu em static/templates? **Só templates** (fechamentos, painel financeiro, corrigir pagamento,
  painel de chamada, totem, Minha agenda, prontuário); o Passo 9 não é necessário. Recarregar com **Ctrl+F5**.
- [x] Mexeu em requirements.txt? **Não**.
- Testado: 398 testes (todos).
- Passo a passo curto: `ATUALIZACAO-2026-10-26-FIM-DA-NOITE-PASSO-A-PASSO.md`.
- Zip regerado na mesma noite (sem migração a mais) para incluir a contagem por procedimentos.
- O que mudou:
  - **Repasse do dinheiro registrado:** `PagamentoParticular.REPASSE_DINHEIRO_REGISTRADO_DESDE` (26/10/2026,
    pela data em que o pagamento foi registrado). Dinheiro entra em Repasses semanais / saldos / registrar
    pagamento como as demais formas. A migração marca como pago o dinheiro registrado antes disso — por isso
    **Corrigir pagamento** de dinheiro anterior a 26/10 fica bloqueado ("repasse já pago").
  - **Visão do proprietário / Painel:** "Em dinheiro" = caixa físico (`_dinheiro_no_caixa`); saída gerada por
    repasse pago em dinheiro não reduz o resultado (mostrada à parte). Dinheiro + banco não somam mais o resultado.
  - **Proprietário** fora dos repasses pendentes (semanal, saldo, já pago/pendente).
  - **Fechamento filtrado por profissional comum:** convênio com o percentual de retenção já descontado.
  - **Painel de chamada:** "Senhas chamadas hoje" + "Chamar de novo". **Totem:** trava de toque duplo.
  - **Prontuário:** "Apagar" (só o autor, não legado, com confirmação e log de auditoria). **Minha agenda:**
    "Atendimentos anteriores sem registro no prontuário" (30 dias).
  - **Fechamentos (diário, mensal, Meu fechamento):** total do Particular e "por forma de pagamento" contam
    procedimentos (soma de `quantidade` dos itens + venda de pacote), não atendimentos; atendimento pago em
    2 formas conta na de maior valor. Convênio só trocou o rótulo para "procedimentos". A tabela de repasse
    continua em atendimentos. **Início do profissional:** cartão "Procedimentos realizados".

## Versão: 2026-10-23, fim da noite (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Não**.
- [x] Mexeu em static/templates? **Só templates** (base, Agenda do dia, Central de Atendimento,
  formulário de agendamento); o Passo 9 não é necessário. Na recepção, recarregar com **Ctrl+F5**.
- [x] Mexeu em requirements.txt? **Não**.
- Testado: 364 testes (todos).
- Passo a passo curto: `ATUALIZACAO-2026-10-23-FIM-DA-NOITE-PASSO-A-PASSO.md`.
- O que mudou:
  - **Repetir último agendamento** (Central de Atendimento e Novo agendamento): puxa profissional,
    especialidade, forma de atendimento, convênio, procedimentos e turno/duração do último
    agendamento (não cancelado) que o usuário marcou — paciente, data e horário ficam em aberto.
  - **Busca de profissional** em todo `<select name="profissional">` do sistema (caixa de texto
    por cima da lista, sem acento/maiúscula). Na Central, busca direta do profissional.
  - **Agenda do dia** guarda o profissional do filtro na sessão do usuário: trocar a data, voltar
    depois de salvar ou abrir pelo menu não desmarca mais. "Todos" limpa.

## Versão: 2026-10-23, tarde (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Sim, 2**: `cadastros.0031_limite_diario_todos_ultrassons` (campo novo no limite diário, desmarcado nos existentes) e `cadastros.0032_convenio_funciona_como_particular` (opção nova no convênio, desmarcada em todos). Não alteram dado existente.
- [x] Mexeu em static/templates? **Só templates** (Agenda do dia, fechamento); o Passo 9 não é necessário.
- [x] Mexeu em requirements.txt? **Não**. Configuração nova **opcional** no `.env`: `AGENDA_RETORNOS_POR_DIA` (padrão 5 — sem a linha, vale 5).
- Testado: 356 testes (todos).
- **Passo manual depois de atualizar:** Cadastros → Convênios → editar cada convênio em que o
  pagamento na clínica deve ir para o repasse semanal → marcar **"Funciona como particular"** → salvar.
- O que mudou:
  - **Corrigir pagamento** (menu Financeiro, para quem tem a permissão de autorizar na fila —
    etapa financeiro): lista os pagamentos registrados **hoje e ontem** (pela data do registro).
    Em cada um dá para corrigir a **forma**, **dividir em 2 formas ou juntar**, o **valor
    recebido do cartão**, as **parcelas** e o **valor total** (a soma das formas; mudando o total,
    o **motivo** é obrigatório e o valor cobrado dos procedimentos do atendimento é ajustado na
    mesma proporção, para o fechamento continuar batendo). Com **venda de pacote** no pagamento o
    total não muda (estorno). Profissional com valor fixo: o repasse fixo continua o mesmo e é
    repartido de novo entre as formas (o novo total não pode ficar abaixo dele). **Não deixa corrigir** pagamento
    estornado, cortesia/retorno, com repasse já pago (total ou parcial) ao profissional, ou
    registrado antes de ontem. Antes/depois fica no log de auditoria.
  - **Retorno grátis direto, sem financeiro:** atendimento em que tudo é retorno grátis —
    o **automático** (consulta feita no sistema novo, dentro do prazo) ou o marcado como
    "Retorno de consulta feita no sistema antigo" — é **autorizado direto** quando a recepção
    clica no botão, que nesses casos aparece como **"Autorizar (retorno, sem cobrança)"** no
    lugar de "Encaminhar para pagamento". Grava a mesma
    cortesia de R$ 0,00 que o financeiro gravaria (relatórios iguais) e registra no log de
    auditoria. Atendimento com algum item a cobrar continua indo para o financeiro.
  - **Convênio "funciona como particular"** — opção nova no cadastro do convênio, só para os
    convênios marcados (outros convênios também recebem na hora, mas não repassam assim e
    continuam como antes). Quando o paciente desse convênio paga no financeiro, o pagamento entra no **repasse semanal**
    (Agenda → Repasses semanais e Financeiro → Saldos e pagamentos de repasse), com o
    percentual do profissional; no **Fechamento** e no **Painel**, entra no particular e na
    **visão do proprietário** (dinheiro em caixa / banco). E **sai** de Contas a receber, dos
    lotes de faturamento, do Repasse aos profissionais (convênio) e do comparativo de
    faturamento — o plano não paga nada por ele, então não conta em dobro. No fechamento, a
    seção Convênios ainda lista o atendimento, marcado "(pago na clínica)", com o valor no Particular.
    Ao marcar a opção num convênio, os atendimentos antigos dele que **já tinham sido pagos** ao
    profissional pelo repasse de convênio ficam como já repassados (não voltam como pendentes).
    O card "repasse pendente" do Painel deixou de contar pagamento na hora de convênio não marcado.
  - **Totem esconde quem já encerrou:** a especialidade só aparece no totem enquanto algum
    profissional dela ainda não terminou o horário do dia (horário fixo ou exceção da data).
    Ex.: atende das 07h às 11h → às 11:01 some (se for o único da especialidade). Antes do
    início continua aparecendo. A recepção (Especialidades de hoje) continua vendo o dia todo.
  - **Limite diário — "todos os ultrassons (USG)":** em Cadastros → Limites diários, nova
    caixa **"todos os ultrassons (USG)"** (deixe o procedimento em branco): um limite só, que
    soma todos os procedimentos marcados como USG, em vez de uma regra por exame. Um limite de
    um procedimento específico continua valendo mais que o de todos os USG.
  - **Até 5 retornos por profissional por dia:** vale para todos os profissionais, sem
    cadastro. O 6º retorno do dia em diante **não é bloqueado**: é salvo como **encaixe** sem
    justificativa, com aviso na hora. Na Agenda do dia aparece "🔶 Encaixe **justificar**" —
    clicar abre o agendamento, e ao salvar é obrigatório escrever o motivo nas observações.
    Cancelados e faltas não contam. Para mudar o número: `AGENDA_RETORNOS_POR_DIA=6` (por
    exemplo) no `.env` e reiniciar.
  - **Senhas (2 P pra 1 N):** saíam 3 (ou mais) Prioritárias antes de uma Normal. Causa: o
    sistema seguia o ciclo P, P, N pela **quantidade** de senhas chamadas no dia, e quando
    era a vez da Normal sem nenhuma Normal esperando, a Prioritária chamada no lugar "gastava"
    a vez — o ciclo desalinhava. Agora a vez é decidida pelas **últimas senhas realmente
    chamadas**: depois de 2 Prioritárias seguidas, é a vez da Normal. E, a pedido da clínica,
    a Normal só passa na frente de Prioritária que chegou **depois** dela: se as Prioritárias
    que estão esperando chegaram antes da Normal, elas são chamadas primeiro.
  - **Quem já estava na fila de pagamento** antes da atualização continua lá: o financeiro
    libera com "Confirmar retorno e autorizar" (um clique cada).
- **Teste no Passo 11:** ver o `ATUALIZACAO-2026-10-23-TARDE-PASSO-A-PASSO.md`.

## Versão: 2026-10-23 (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Sim, 2**: `accounts.0018_permissao_cancelar_autorizado` (permissão nova "Cancelar atendimento já autorizado"; só muda o texto da "Editar/cancelar agendamentos") e `agenda.0032_agendamento_encaminhado_pagamento_em` (campo novo, em branco nos atendimentos antigos). Não alteram dado existente.
- [x] Mexeu em static/templates? **Só templates** (prontuário, Agenda do dia, Autorizações, Registrar pagamento, painel da TV, menu, fechamentos); o Passo 9 não é necessário.
- [x] Mexeu em requirements.txt? **Não**.
- Testado: 330 testes (todos).
- **Passo manual depois de atualizar:** Administração → **Perfis** → editar o(s) perfil(is) que podem
  cancelar atendimento já autorizado (ex.: Administrador, supervisão) → marcar, em Agenda,
  **"Cancelar atendimento já autorizado (tela Autorizações)"** → salvar. **Não** rode o
  `seed_perfis` para isso: ele regrava as permissões dos perfis padrão e desfaz ajustes feitos à mão.
- O que mudou:
  - **Prontuário (profissional):** no topo do prontuário e do formulário de registro aparecem a
    **idade** (pela data de nascimento; sem data, "(pendente cadastro)" em vermelho; bebê em
    meses), o **plano do cadastro** do paciente e o **plano autorizado no atendimento** (o de
    hoje com o profissional logado, senão o último autorizado; no registro feito a partir do
    atendimento, o plano daquele atendimento).
  - **Recepção 2 (voz do painel):** a leva chamada só falava o 1º paciente. Agora fala todos —
    "Ana. Entrar no Consultório 9. Bruno, Carla. Aguardar na Recepção 2." — e, depois que o 1º é
    atendido, chama quem está de fato entrando (antes repetia o nome de quem já foi atendido).
  - **Cancelamento em dois critérios:**
    - **Agenda do dia:** "Cancelar" e "Faltou" só aparecem até a autorização (Agendado,
      Pré-confirmado, Confirmado, Pré-autorizado). Em Autorizado/Realizado não aparecem, e o
      sistema recusa mesmo pela URL. "Cancelar este e os próximos" de uma série pula as
      sessões já autorizadas.
    - **Agenda → Autorizações:** coluna **Cancelar** para quem tem a permissão nova. Pede
      **motivo** (vai para as observações do atendimento e para o log de auditoria). Bloqueia
      se o atendimento já está num **lote de faturamento**, com a **baixa do convênio**
      recebida, com **repasse pago** ou com **pagamento particular não estornado**. A guia de
      sessões volta a ter saldo sozinha.
  - **Fila de chamada guarda a vez de quem vai pagar:** o particular (ou convênio pago na hora)
    passa a contar a vez a partir do **"Encaminhar para pagamento"**, não do pagamento — não
    perde mais o lugar para convênio autorizado enquanto está no caixa. Quem já estiver no
    caixa na hora da atualização segue a regra antiga.
  - **Registrar pagamento:** no fim da tela, **"Paciente desistiu? Cancelar este atendimento"**
    (motivo opcional, pede confirmação; sai da fila de pagamento e da agenda). Os antigos
    links "Cancelar" dessa tela, que só voltavam, viraram **"Voltar"**.
  - **Fechamento diário e mensal com valor de convênio:** a seção Convênios, que era só
    quantidade, agora mostra o **valor** por convênio e por procedimento (valor da Tabela de
    Preços congelado na autorização — o que se tem a receber do plano; retorno aparece como
    "grátis"). Procedimento sem preço na tabela não soma e aparece em vermelho ("sem preço").
    Seção nova **Produção**: particular (valor cobrado) + convênios = **produção total** do
    dia/mês. "Meu fechamento" do profissional não mudou.
  - **Listagem de atendidos (profissional):** menu Agenda → **Listagem de atendidos** (mesma
    permissão da Minha agenda). Mostra os atendimentos **dele** já autorizados no período
    (padrão: hoje; atalhos Hoje / Ontem / Este mês), com paciente, procedimento, plano e
    situação — em verde os que ele marcou como atendidos, e "Autorizado (não marcado)" os que
    não marcou. Filtros por paciente e situação; link para o prontuário. Sem valores (dinheiro
    continua em "Meu fechamento").
- **Teste no Passo 11:** ver o passo 9 do `ATUALIZACAO-2026-10-23-PASSO-A-PASSO.md`.

## Versão: 2026-10-16 (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Sim, 4**: `cadastros.0030_atendimento_profissional` (tabela nova), `fila.0007_senha_profissional` (campo novo, em branco nas senhas antigas), `agenda.0030_assinatura_e_retorno_manual` (3 campos novos, desmarcados nos registros antigos) e `agenda.0031_registro_da_assinatura` (data/quem registrou a assinatura, em branco). Não alteram dado existente.
- [x] Mexeu em static/templates? **Só templates** (painel de chamada, Início, Agenda do dia, totem); o Passo 9 não é necessário.
- [x] Mexeu em requirements.txt? **Não**. Novo `requirements-voz.txt` (opcional, voz do painel — passo 6c). Mexeu em `config/settings.py` (middleware novo) — vem no zip, o `.env` não muda.
- Testado: 301 testes (todos).
- O que mudou:
  - **Sem assinatura:** na tela **Autorizar** (convênio) há a caixa "Sem
    assinatura (o paciente ainda não assinou a guia)"; em **Guias pendentes**, ao
    marcar a 2ª guia (sessão de 1h) também há "sem assinatura". Essas guias
    aparecem na coluna **Assinatura** ("✍ 1ª guia sem assinatura") com o botão
    **Assinou** — que pede o **dia em que o paciente assinou** (padrão hoje) e
    grava quem registrou. O registro aparece como "✔ 1ª guia assinada em
    dd/mm/aaaa (registrado por …)" em Guias pendentes (opção "Pendentes e já
    autorizadas/assinadas") e em Autorizações. Novo filtro "Mostrar": tudo
    pendente / só falta autorizar / só falta assinatura. Não muda produção nem valor.
  - **Repasse particular e o proprietário:** o login do profissional marcado como
    **proprietário** (Cadastros → Profissionais) vê **Financeiro → Saldos e
    pagamentos de repasse** e pode **Registrar pagamento** (e excluir um lançado
    errado), mesmo sem as permissões do financeiro. Ele mesmo **não aparece** nos
    saldos (não há repasse a pagar a ele) — também saiu do "repasse pendente" do
    Painel financeiro.
  - **Profissional:** menu Agenda → **Minhas guias pendentes** — só os
    atendimentos dela, só consulta (sem botões).
  - **Retorno do sistema antigo (provisório):** no agendamento, caixa "Retorno de
    consulta feita no sistema antigo (não cobra a consulta)". Vale só para o item
    de consulta e conta como retorno grátis em tudo (valor R$ 0,00, relatórios,
    faturamento). O retorno de consultas feitas no sistema novo continua automático.
  - **Senha por profissional** (especialidade com 2+ profissionais atendendo no dia):
    - **Totem:** depois da especialidade aparece "Com qual profissional?" com os
      nomes e "Não sei / qualquer um". O papel e o painel mostram
      "Especialidade — Profissional". Com 1 profissional só, nada muda.
    - **Recepção (Agenda do dia → Especialidades de hoje):** embaixo dessas
      especialidades aparecem os profissionais; marque **só os que você atende**
      (nenhum marcado = todos). "Chamar senha" passa a trazer só as senhas
      desses profissionais **mais** as de "Não sei". A grade da agenda também
      mostra só eles.
  - **Status automático do dia** (`agenda.models.atualizar_status_do_dia`, roda
    sozinha no primeiro acesso de cada dia, sem cron):
    - atendimentos de **hoje** em "Agendado" viram **"Pré-confirmado"**; encaixe
      marcado para hoje já nasce pré-confirmado;
    - atendimentos de **dias anteriores** que ficaram em "Agendado" ou
      "Pré-confirmado" viram **"Faltou"**. Confirmado, Pré-autorizado,
      Autorizado, Realizado e Cancelado não são tocados.
    - **Atenção:** no primeiro acesso depois da atualização, a regra pega todo
      o histórico (não só ontem). Conte antes — ver o passo a passo de 16/10.
    - Para desligar sem mexer no código: `AGENDA_STATUS_AUTOMATICO=0` no `.env`
      e reiniciar o serviço.
  - **Agenda do dia → Lista do dia:** coluna nova **Telefone** (do cadastro do
    paciente), para a recepção ligar e confirmar. Sem telefone cadastrado aparece "—".
  - **Painel de chamada (TV):** "Guichê X" bem maior (quase do tamanho da senha);
    na chamada pelo nome, o consultório também aumentou.
  - **Voz no painel:** bipe + voz, falada 2 vezes — "Senha P zero zero sete.
    Guichê 1." e, na chamada do profissional, só o **primeiro nome** +
    consultório ("Maria, Consultório 3."). O painel não recarrega mais a página
    inteira a cada 5 s (só o conteúdo) — recarregar fazia o Chrome bloquear o som.
  - **Minha agenda (profissional):** a lista "Autorizados de hoje / ações" mostra
    **só os Autorizados** (agendados, faltas e já atendidos saem da lista). A grade
    da semana, em cima, continua mostrando tudo.
  - **Quem agendou:** na lista da Agenda do dia, embaixo do paciente, aparece
    pequeno "agendado por <usuário>" (passando o mouse mostra data/hora); na tela
    de editar o agendamento também. Agendamento antigo sem esse registro não mostra nada.
  - **Painel:** especialidade da senha em negrito e maior.
  - **Voz do painel gerada no servidor** (`fila/voz.py`, Piper offline, voz
    pt_BR-faber-medium): a TV só toca um `.wav` (`/fila/painel/voz/<tipo>/<id>/`),
    funciona em TV box (TV Bro). **Instalação única no servidor** (pacote opcional
    `requirements-voz.txt` + modelo de 63 MB na pasta `voz/`, que não vai no zip) —
    ver passo 6c do `ATUALIZACAO-2026-10-16-PASSO-A-PASSO.md`. Sem isso, o painel
    usa a voz do navegador como antes. Velocidade da fala ajustável no `.env`
    (`PAINEL_VOZ_VELOCIDADE`, padrão 1.4; maior = mais devagar) + restart; pausa
    entre o nome do paciente e o consultório.
  - **Agenda do dia:** o filtro **Profissional** virou uma busca (digita parte do
    nome, sem precisar de acento). E depois de **Pré-confirmar / Confirmar /
    Faltou** a tela volta para a **mesma linha** (destacada de leve), não pro topo.
  - **Início do profissional:** o botão "Ver agenda do dia" da faixa "Agora"
    abria a Agenda do dia da recepção e dava **erro 403**; agora abre a
    **Minha agenda**.

### Ajuste no PC da TV do painel (uma vez só)

Depois de ligar o PC, o Chrome só deixa o painel falar depois de **um clique** no
botão do som ("Toque para ativar o som"). Para não precisar desse clique, ponha
esta opção no atalho do Chrome do painel (botão direito no atalho →
Propriedades → campo **Destino**, depois do `chrome.exe"`):

```
--autoplay-policy=no-user-gesture-required
```

Feche todos os Chrome antes de abrir pelo atalho (com outro Chrome aberto, a
opção é ignorada). A voz é a do Windows/Chrome em português: a "Google
português do Brasil" precisa de internet; sem internet, usa a da Microsoft.

- **Teste no Passo 11:** chamar uma senha na recepção e um paciente pela
  "Minha agenda" e ouvir o painel; abrir a Agenda do dia e ver a coluna
  Telefone; entrar como profissional e clicar em "Ver agenda do dia".

## Versão: 2026-10-15, fim da tarde (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Não**.
- [x] Mexeu em static/templates? **Só template** (tela de registrar pagamento); o Passo 9 não é necessário.
- [x] Mexeu em requirements.txt? **Não**.
- Testado: 164 testes da agenda.
- O que mudou:
  - **Registrar pagamento dividido em 2 formas:** cada forma agora tem o seu
    valor logo abaixo ("Valor da 1ª forma" / "Valor da 2ª forma"). Ao digitar o
    valor da 1ª, a 2ª é preenchida sozinha com o que falta para fechar o
    **valor já com desconto**, e embaixo aparece "Soma das formas: R$ X de R$ Y"
    (vermelho se faltar ou passar). Antes os campos de valor ficavam soltos no
    fim da tela e a conta não considerava o desconto.
  - **Teste no Passo 11:** registrar um pagamento com desconto de R$ 50 em
    Dinheiro + Cartão débito, digitar só o valor da 1ª forma e conferir que a
    2ª fecha o total com desconto.

## Versão: 2026-10-15, tarde (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Sim, 2**: `agenda.0028_guia_extra_data` — tabela nova
      com a data de cada guia extra; `agenda.0029_desconto_pagamento` — campos de
      desconto no atendimento. Nenhuma altera dado existente.
- [x] Mexeu em static/templates? **Só templates**; o Passo 9 não é necessário.
- [x] Mexeu em requirements.txt? **Não**.
- Testado também no PostgreSQL (274 testes).
- O que mudou:
  - **Guias pendentes:** ao marcar a 2ª (3ª...) guia autorizada, aparece o
    campo **"autorizada em"** (vem com a data de hoje; dá para pôr uma data
    anterior, nunca futura). A data aparece embaixo, na coluna Guias
    ("2ª guia autorizada em 13/10/2026"). Guias marcadas antes desta versão
    ficam sem data. A produção continua contando no dia do atendimento.
  - **Backup:** o Passo 2 e o Passo 12 agora usam `docker exec smedim-postgres`
    (o banco roda no Docker); ver o Passo 2.
  - **Desconto no registro do pagamento:** campo "Desconto (R$)" com motivo
    obrigatório (ex.: "Dr. Fulano autorizou"); o valor cobrado já sai com o
    desconto. Profissional por percentual: o repasse é calculado sobre o valor
    com desconto. Profissional que **cobra valor fixo**: aparece o campo
    **"Repasse acordado"** (mostra o repasse normal como referência) e é
    obrigatório quando há desconto. Desconto não vale junto com venda de pacote;
    desconto do valor inteiro = cortesia.
  - **Cadastro do profissional → Valores fixos no particular:** a lista de
    procedimentos mostra só os habilitados para ele (antes vinha a tabela
    inteira da clínica).
  - **Novo agendamento:** "Forma de atendimento" e "Convênio" agora vêm antes
    dos procedimentos (a lista de procedimentos depende deles), e a tela abre
    com 1 linha de procedimento em branco (antes abria com 2).
  - **Teste no Passo 11:** Agenda → Guias pendentes → marcar uma guia com uma
    data anterior e conferir a data embaixo; abrir um novo agendamento e
    conferir a ordem dos campos; registrar um pagamento com desconto.

## Versão: 2026-10-15 (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Sim, 1**: `agenda.0027_autorizacao_e_guias_convenio`
      — campos novos no atendimento (quem autorizou, observação, nº da guia),
      tabela nova de guias de sessões e o vínculo da sessão com a guia. Não
      altera nenhum dado existente.
- [x] Mexeu em static/templates? **Só templates** (nenhum arquivo em `static/`);
      o Passo 9 não é necessário.
- [x] Mexeu em requirements.txt? **Não**.
- Testado também num PostgreSQL de verdade (250 testes), não só no SQLite.
- O que mudou:
  - **Autorizar convênio abre uma tela** (antes era um clique direto): grava
    sozinho **quem autorizou** e a hora; campo de **observação** livre (ex.:
    "AUT. NO CRM DENNIS"); **número da guia** opcional para consulta/exame.
  - **Sessão por convênio usa uma guia de sessões do plano**: escolhe uma guia
    já aberta do paciente (mostra "usadas 3 de 10") ou cadastra a nova (número,
    sessões liberadas, validade opcional). Sessão de 1h gasta 2 da guia. Guia
    **esgotada ou vencida bloqueia** — cadastre a guia nova liberada pelo
    plano. Atendimento cancelado ou falta devolve as sessões. Aviso quando a
    guia está acabando (2 ou menos).
  - **Agenda → Autorizações**: igual à tela do sistema antigo — filtros por
    período, convênio, paciente e profissional; mostra a guia ("sessão 3–4 de
    10"), quem autorizou e a observação. Atendidos em verde.
  - **Agenda → Guias do convênio**: guias de cada paciente com o saldo; dá para
    corrigir número, quantidade e validade (não deixa liberar menos que o já
    usado).
  - Pagamento particular registrado no financeiro também grava quem autorizou.
  - **Teste no Passo 11:** autorizar uma sessão por convênio cadastrando a
    guia; conferir em Agenda → Autorizações e em Agenda → Guias do convênio.

## Versão: 2026-10-14, tarde (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Não**.
- [x] Mexeu em static/templates? **Não**.
- [x] Mexeu em requirements.txt? **Não**.
- O que mudou:
  - **Erro 500 em Guias pendentes → "marcar a 2ª guia autorizada"**: a tela
    travava a linha no banco junto com o convênio do atendimento, e o
    PostgreSQL recusa isso. Corrigido.
  - Tempo de inatividade padrão passou para 60 min **só para instalações
    novas** — no servidor já instalado vale o `SESSAO_TIMEOUT_MINUTOS` do
    `.env` (a atualização não mexe no `.env`).
  - **Teste no Passo 11:** Agenda → Guias pendentes → marcar uma guia
    autorizada (e "desfazer", se quiser voltar).

## Versão: 2026-10-14 (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Sim, 1**:
      - `cadastros.0029_profissional_convenios_atendidos` — campo novo
        "Convênios que atende" no profissional. **Começa vazio** para todos
        (decisão da clínica, ainda em implantação) — não altera nenhum dado
        existente.
- [x] Mexeu em static/templates? **Só templates** (nenhum arquivo em `static/`);
      o Passo 9 não é necessário.
- [x] Mexeu em requirements.txt? **Não**.
- O que mudou:
  - **Convênios que o profissional atende:** campo novo no cadastro do
    profissional. Na hora de agendar, só aparecem os convênios marcados ali
    **e** que têm preço para algum procedimento dele. Profissional sem nenhum
    marcado = só particular (a opção "Convênio" fica desativada).
  - **Escolha do convênio no agendamento:** ao escolher "Convênio", aparece a
    lista de convênios do profissional (o do cadastro do paciente vem
    pré-selecionado quando está na lista). Dá para agendar por convênio mesmo
    com paciente sem convênio no cadastro.
  - **Procedimento só aparece no convênio em que tem preço** (geral ou
    específico daquele profissional — preço específico de outro profissional
    não conta mais).
  - **Pacotes:** pacote com profissional escolhido só aceita procedimentos
    habilitados para ele (a lista já filtra; o sistema recusa salvar se tiver
    procedimento fora).
  - **Passo manual depois de atualizar:** em Cadastros → Profissionais, editar
    cada profissional e marcar os convênios que ele atende. Até isso, ninguém
    consegue ser agendado por convênio.
  - **Teste no Passo 11:** marcar um convênio num profissional; em novo
    agendamento com ele, escolher "Convênio" e conferir que só aparece esse
    convênio e só os procedimentos com preço nele.

## Versão: 2026-10-13 (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Sim, 6**:
      - `agenda.0023_guias_extras_sessao` — campo novo (guias extras autorizadas).
      - `agenda.0024_converter_sessoes_futuras_em_guias` — **altera dados**: as
        sessões por convênio **futuras e ainda não autorizadas** passam a ter a
        quantidade de guias pela duração (1h = 2, 1h30 = 3). Mostra no terminal
        quantas converteu. Passadas, autorizadas, canceladas e faltas não mudam.
      - `cadastros.0027_paciente_unificado_em` — campo novo (ficha unificada).
      - `agenda.0025_ordem_por_agendamento` — só ordenação (sem mudança de dados).
      - `agenda.0026_pacote_vendido` e `cadastros.0028_pacote` — tabelas novas
        de pacotes/combos (sem mudança em dados existentes).
- [x] Mexeu em static/templates? **Só templates** (nenhum arquivo em `static/`);
      o Passo 9 não é necessário.
- [x] Mexeu em requirements.txt? **Não**.
- O que mudou:
  - **Agendamento mais rápido.** A tela de novo agendamento (clique em "Vago"
    na Central de Atendimento) carregava os 37 mil pacientes de uma vez; agora
    o paciente é buscado por nome ou CPF enquanto se digita.
  - **Só aparecem os procedimentos vinculados ao profissional.** Profissional
    sem nenhum procedimento vinculado não pode mais ser agendado até vincular
    (Cadastros → Procedimentos → "profissionais habilitados"); a tela avisa.
  - **Agenda do dia:** ao filtrar um profissional (visão Dia), aparece uma faixa
    de resumo: agendados por situação, próximo paciente e procedimentos do dia.
  - **Guias de sessão por convênio:** sessão de 1h = 2 guias de 30 min. Só a
    1ª conta na produção ao autorizar o atendimento; as demais são marcadas
    em **Agenda → Guias pendentes** (também no menu Financeiro) e contam na
    produção **do dia do atendimento**. Avisos no Painel financeiro, em
    Contas a receber e na montagem de lote. Trava só depois do repasse ao
    profissional pago.
  - **Pacientes importados:** quem veio do sistema antigo **só com o nome**
    (sem CPF, nascimento ou telefone) sai da busca do agendamento e da lista
    de Pacientes (fica em "ver esses cadastros"); o profissional continua
    vendo o prontuário. Novo comando para **unificar duplicados** (mesmo
    nome + nascimento) — roda à mão, ver o passo a passo da versão.
  - **Consulta/exame sem hora marcada:** só sessão tem horário e duração;
    consulta/exame são agendados por turno e atendidos na ordem de agendamento.
  - **Pacotes/combos particulares** (preço fechado pago de uma vez): cadastro
    em Cadastros → Pacotes / combos; venda na tela Registrar pagamento (repasse
    do pacote inteiro sai nesse pagamento); atendimentos seguintes com o mesmo
    profissional usam o saldo e são autorizados direto; saldo em Agenda →
    Pacotes dos pacientes.
  - **Teste no Passo 11:** novo agendamento pela Central (a busca de
    paciente responde rápido); Agenda → Guias pendentes abre; Agenda do dia
    com um profissional filtrado mostra o resumo.

## Versão: 2026-10-09 (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Não**.
- [x] Mexeu em static/templates? **Sim** — `fila/totem_senha.html` e imagem
      nova `fila/static/fila/img/logo_smedim_ticket.svg`. **O Passo 9
      (`collectstatic`) é obrigatório**, senão a logo não aparece no ticket.
- [x] Mexeu em requirements.txt? **Não**.
- O que mudou:
  - **Ticket da senha continuava comprido.** Causa: o driver da ELGIN i8 vem
    com o papel padrão "80(72.1) x 297 mm" e o Chrome usa esse papel. A página
    não força mais tamanho nenhum; quem define é o driver, que agora precisa
    ficar no papel de **rolo** (corta logo depois do conteúdo). Testado com a
    ELGIN i8 de verdade: senha normal e prioritária saindo curtas.
  - **Logo da clínica de volta no ticket**, numa versão só preto e branco
    (a colorida saía cinza pontilhado). Ela fica no topo, no lugar do texto
    "Clínica SMEDIM".
  - O espaço em branco de ~1,5 cm no começo do ticket é da própria impressora
    (distância entre a cabeça de impressão e a guilhotina) e não dá para
    tirar pelo sistema.
  - As configurações `FILA_TOTEM_LARGURA_PAPEL_MM` e
    `FILA_TOTEM_ALTURA_PAPEL_MM` deixaram de existir. Se estiverem no `.env`
    do servidor, não atrapalham (são ignoradas); não precisa apagar.

### Ajuste no PC do totem (obrigatório nesta versão, uma vez só)

Feito no **Windows do PC do totem**, não no servidor:

1. Feche o Chrome do totem.
2. Menu Iniciar → digite **Painel de Controle** → **Exibir impressoras e
   dispositivos** (ou "Dispositivos e Impressoras").
3. Clique com o botão direito na **ELGIN i8** → **Propriedades da
   impressora** → aba **Avançado** → botão **Padrões de impressão...**
4. Botão **Avançado...** → **Tamanho do papel** → escolha
   **80(72.1) x 3276 mm** → OK → Aplicar → OK.
5. Confira que a ELGIN i8 continua como **impressora padrão** (com o
   ✓ verde).
6. Abra o Chrome do totem de novo (pelo atalho de sempre, com
   `--kiosk-printing`) e tire uma senha normal e uma prioritária: o papel
   deve cortar logo depois da data, com a logo no topo.

Se ainda sair comprido: reinicie o PC do totem (o Chrome às vezes guarda o
papel antigo até fechar de vez) e teste de novo.

## Versão: 2026-10-08 (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Não**.
- [x] Mexeu em static/templates? **Sim** — só `fila/totem_senha.html` (CSS de
      impressão dentro do próprio template; o Passo 9 não é necessário, mas
      não faz mal rodar).
- [x] Mexeu em requirements.txt? **Não**.
- O que mudou:
  - **Ticket da senha do totem:** saía comprido, com o logo e o texto "Toque
    na tela para tirar sua senha" impressos em cinza (apagados). Agora sai só
    Clínica SMEDIM, a senha, a especialidade e a data, em preto e negrito,
    num ticket de 60 mm de comprimento.
  - Nova configuração opcional `FILA_TOTEM_ALTURA_PAPEL_MM` no `.env`
    (removida na versão 2026-10-09).
  - **Imprimir sem abrir a tela "Imprimir" do Chrome não depende do servidor**:
    é o atalho do totem com `--kiosk-printing` e a ELGIN i8 como impressora
    padrão, configurado no próprio PC do totem.
  - **Teste no Passo 11:** tirar uma senha normal e uma prioritária no totem
    e conferir o papel.

## Versão: 2026-09-28 (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Sim** — `agenda.0019`, `agenda.0020` (ajusta dados:
      marca como pago o cartão/Pix de quem tinha "sempre recebe no dia"),
      `agenda.0021` (repasse fixo por linha de pagamento), `agenda.0022` (só
      texto de ajuda), `cadastros.0023` (remove "sempre recebe no dia"),
      `cadastros.0024` (valor fixo no particular), `cadastros.0025` (prazo de
      retorno por convênio, padrão 30), `cadastros.0026` (remove o desconto %
      do convênio, copiando os valores para a Tabela de Preços) e
      `financeiro.0003`. **Backup antes (Passo 2) é obrigatório.**
- [x] Mexeu em static/templates? **Sim** — templates de Repasses semanais,
      fechamentos, menu, cadastro de profissional/datas, Central de
      Atendimento, chat e 5 telas novas (sem CSS/JS novo, o Passo 9 cobre).
- [x] Mexeu em requirements.txt? **Não**.
- O que mudou:
  - **Pagamento parcial de repasse (particular em cartão/Pix):** quando o caixa
    não tem dinheiro para repassar tudo, ou o profissional quer levar só uma
    parte, o financeiro usa **Registrar pagamento** (em Repasses semanais ou
    na tela nova **Financeiro → Saldos e pagamentos de repasse**) e informa
    quanto pagou e como: *Dinheiro do caixa* ou *Pix/transferência
    (proprietário)*. O valor é abatido dos atendimentos mais antigos, já com o
    desconto da clínica, e o restante fica como **saldo pendente** até ser
    pago. Pagamento em dinheiro lança sozinho a saída de caixa. Um lançamento
    errado pode ser excluído, e o saldo volta junto com a saída de caixa.
  - O botão "Marcar como pago" por linha, em Repasses semanais, saiu. Agora
    tudo passa por "Registrar pagamento" (para pagar tudo, basta confirmar o
    valor que já vem preenchido).
  - A opção **"sempre recebe o repasse no mesmo dia"** saiu do cadastro do
    profissional. O que já tinha sido acertado para esses profissionais fica
    marcado como pago, sem virar pendência.
  - **Fechamento diário/mensal e Meu fechamento:** "no dia / pendente" virou
    **"já pago / pendente"** e passa a considerar os pagamentos parciais.
  - **Painel financeiro:** o card "Repasse pendente" agora mostra o saldo
    **líquido** (com desconto e sem o que já foi pago). Antes mostrava o valor
    bruto.
  - **Convênio: saiu o "desconto (%) sobre a tabela particular"** (migração
    `cadastros.0026`). Nem todo procedimento desses convênios tinha desconto,
    então agora todo convênio tem os valores na Tabela de Preços. A migração
    copia para a Tabela de Preços, com o desconto já aplicado, os valores que o
    sistema calculava até hoje. **Depois de atualizar, a clínica precisa
    conferir a tabela desses convênios** e corrigir os procedimentos que não
    têm desconto. Esses convênios também ficam com o novo campo **"Permite
    pagamento na hora"** marcado. É ele que mantém na agenda o botão
    "Encaminhar para pagamento (na hora)".
  - **Profissional com valor fixo no particular:** no cadastro do profissional,
    a nova opção **"cobra valor fixo no particular (sem percentual)"** abre a
    tabela **"Valores fixos no particular"**: para cada procedimento, o valor
    cobrado do paciente e o valor repassado ao profissional. Procedimento que
    não estiver nessa tabela **não pode ser agendado como particular** para
    ele. Convênio continua pelo percentual. O repasse fica congelado no
    momento do pagamento.
  - **Prazo de retorno grátis por convênio:** novo campo no convênio
    (padrão 30 dias; ex.: 15 para convênios que só cobrem retorno em 15 dias;
    0 = sem retorno grátis). Vale o convênio da consulta original.
    Particular continua com 30 dias.
  - **Datas específicas / exceções** (antes "Exceções de agenda"): agora
    serve também para profissional **sem dia fixo**, que vem só em datas
    marcadas no mês. Dá para **repetir** a data nos próximos meses (mesmo dia
    do mês, mesma semana/dia da semana, ou último dia da semana do mês),
    **editar** uma data, ver quantos pacientes já estão marcados nela e
    **Remanejar para outra data** (leva os pacientes no mesmo horário e
    mostra a lista com telefones para avisar cada um).
  - **Central de Atendimento:** mostra as **próximas datas específicas** do
    profissional como atalhos e avisa quando ele não atende na semana
    exibida.
  - Agendamento: se o profissional não tiver nenhum procedimento vinculado, a
    lista de procedimentos agora avisa isso em vez de ficar vazia.
  - **Chat interno:** lista separada em **Online** e **Offline** (offline
    recolhido; abre sozinho se houver mensagem não lida de alguém offline).
    As mensagens de dias anteriores agora são **apagadas sozinhas** no
    primeiro acesso do dia — o cron do `limpar_mensagens` virou opcional.

## Versão: 2026-09-25 (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Não**.
- [x] Mexeu em static/templates? **Sim** — só `prontuario/entrada_form.html`
      (um campo oculto; sem CSS/JS novo, o Passo 9 cobre).
- [x] Mexeu em requirements.txt? **Não**.
- O que mudou:
  - **Financeiro → Fila de pagamento → Registrar pagamento:** o pagamento já
    era gravado, mas logo depois aparecia uma página "403 Forbidden", porque o
    sistema voltava para a Agenda do dia, que o perfil Financeiro não acessa.
    Agora quem não tem acesso à Agenda do dia volta para a **Fila de
    pagamento** (com a mensagem verde); quem tem acesso continua voltando para
    a agenda, como antes.
  - **Registro no prontuário (a partir do atendimento):** depois de salvar, a
    tela mostrava o formulário vazio de novo (parecia que não tinha gravado).
    Agora volta para a tela de onde o profissional veio (normalmente Minha
    agenda), ou para o prontuário do paciente.
  - Pacote gerado **sem a pasta `media/`** (fotos de perfil do PC de
    desenvolvimento não vão mais para o servidor; as fotos já enviadas na
    clínica continuam intactas).
  - Guia de importação: passo B2 do `LEIA-ME-IMPORTAR-PACIENTES-LEGADO.md`
    agora cria a pasta de backups antes do `pg_dump`.

## Versão: 2026-09-24 (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Sim** — `prontuario.0004_entrada_legado_somente_leitura`
      (torna o profissional da entrada opcional e cria o campo do nome antigo;
      não apaga nem altera nenhum registro existente) e
      `agenda.0018_backfill_valor_convenio_autorizados` (ver abaixo). As duas
      rodam no Passo 8.
- [x] Mexeu em static/templates? **Sim** — `prontuario/prontuario_paciente.html`
      e os templates do financeiro/desempenho listados abaixo.
- [x] Mexeu em requirements.txt? **Não**.
- O que mudou:
  - **Prontuário do sistema antigo entra só para consulta.** Entradas trazidas
    pelo `importar_prontuario_legado` aparecem na linha do tempo do paciente com
    a especialidade e o nome do profissional de lá, marcadas como "Sistema
    antigo · somente consulta", **sem botão Editar para ninguém** (nem para o
    profissional que as escreveu; a tela também recusa o acesso direto por
    URL). Registros criados neste sistema continuam editáveis pelo autor.
  - A importação de prontuário deixou de exigir que o profissional do sistema
    antigo esteja cadastrado aqui (antes, ~43 mil entradas ficavam de fora). O
    sistema antigo não guarda especialidade: ela é deduzida do conselho do
    profissional (CRM = Medicina etc.) e do CBOS; sem nenhum dos dois fica
    "Não informada".
  - Sem impacto até rodar a importação de prontuário (ver Parte C do
    [LEIA-ME-IMPORTAR-PACIENTES-LEGADO.md](LEIA-ME-IMPORTAR-PACIENTES-LEGADO.md)).
  - **Produção passa a contar no "Autorizado"**, sem esperar o profissional
    marcar "atendido". Vale para Painel financeiro, Fechamentos, Relatório
    detalhado, Contas a receber, Repasses (particular e convênio), Produção por
    convênio, Lotes de faturamento, "Meu desempenho" e o direito a retorno
    grátis. Consequência: paciente autorizado que vai embora sem ser atendido
    **também conta** — nesse caso a recepção precisa mudar a situação dele
    (Faltou/Cancelado) para tirá-lo da produção.
  - O valor do convênio agora é preenchido já na autorização. **Migração nova**
    `agenda.0018_backfill_valor_convenio_autorizados`: preenche esse valor nos
    atendimentos de convênio que já estavam em "Autorizado" (só preenche o que
    está vazio, não altera valores já lançados; roda no Passo 8).
  - Templates alterados: `financeiro/painel.html`, `contas_receber_lista.html`,
    `faturamento_comparativo.html`, `lote_form.html`, `relatorio_detalhado.html`
    e `agenda/_desempenho_secao.html` (só textos/rótulos).

## Versão: 2026-09-23 (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Não**.
- [x] Mexeu em static/templates? **Sim** — `cadastros/paciente_lista.html` e
      `accounts/base.html`, `accounts/_paginacao.html` (novo) e as listas de
      Prontuários, Profissionais, Procedimentos, Convênios, Usuários, Fila de
      pagamento, Lotes e Movimentações avulsas (só HTML, sem CSS/JS novo; o
      Passo 9 cobre).
- [x] Mexeu em requirements.txt? **Não**.
- O que mudou:
  - Cadastros → Pacientes agora carrega **50 pacientes por página**, com
    links "Anterior/Próxima" e o total no rodapé. A busca por nome/CPF
    continua igual e mantém o filtro ao trocar de página. Mesma paginação
    (50 por página, filtros preservados) na busca de **Prontuários** do
    profissional e nas listas de Profissionais, Procedimentos, Convênios,
    Usuários, Fila de pagamento, Lotes de faturamento e Movimentações
    avulsas. Contas a receber e Repasse aos profissionais não foram
    paginados de propósito (são agrupados, com totais por grupo).
  - Menu lateral: ao abrir **Financeiro → Painel financeiro**, o grupo
    **Agenda** também abria sozinho (a checagem casava `painel` dentro de
    `painel_chamada`). Agora cada grupo só abre nas suas próprias páginas.
  - Importação do sistema antigo: corrigido o valor de `CLASSE_CLIENTE` em
    `importar_pacientes_legado.py` para `com.mkmikael.smedim.Cliente` (o
    valor antigo, `Cliente`, não achava ninguém). Só afeta quem rodar essa
    importação — ver [LEIA-ME-IMPORTAR-PACIENTES-LEGADO.md](LEIA-ME-IMPORTAR-PACIENTES-LEGADO.md).

## Versão: 2026-09-19 (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Sim** — `cadastros.0021_procedimento_exame_com_preparo`,
      `cadastros.0022_sala_lugares_recepcao_propria` e
      `agenda.0017_agendamento_lote_recepcao` (só adicionam colunas com padrão
      vazio/zero; rodam normalmente no Passo 8).
- [x] Mexeu em static/templates? **Sim** — `agenda_propria.html`,
      `procedimento_lista.html`, `sala_lista.html` e `painel_espera.html` (sem
      CSS/JS novo; o Passo 9 cobre).
- [x] Mexeu em requirements.txt? **Não**.
- O que mudou:
  - Novo marcador **"Exame com preparo"** no cadastro de Procedimentos. Depois de
    autorizado, o agendamento que tiver esse exame passa na frente dos demais
    autorizados que ainda não foram chamados, na fila de chamada do profissional
    (Minha agenda). Quem já foi chamado não é ultrapassado. Emissão de senha e
    padrão P;P;N não mudam.
    *(Substituído em 2026-10-26: agora o exame com preparo tem senha própria no
    totem, chamada antes das demais, e na Minha agenda vale a ordem de autorização.)*
  - **Depois de atualizar:** ir em Cadastros → Procedimentos e marcar quais
    exames têm preparo — por padrão nenhum vem marcado, então nada muda até isso.
  - **Recepção 2:** salas ganharam o campo "lugares na recepção própria" (0 =
    sem). Na sala da segunda recepção, o profissional que a escolher em "Minha
    agenda" ganha o botão "Chamar N pacientes para a Recepção 2": chama de uma
    vez quem couber nos lugares livres; o primeiro entra no consultório e os
    demais aguardam sentados. Como não há tela na Recepção 2, o painel de espera
    da recepção 1 anuncia a leva inteira. **Depois de atualizar:** editar a sala
    da Recepção 2 em Cadastros → Salas e preencher os lugares (5) — enquanto
    isso não for feito, o botão não aparece pra ninguém.

## Versão: 2026-08-21 (preenchido pelo Claude ao fim da sessão)

- [x] Tem migração nova? **Sim** — `accounts.0016_usuario_paginas_favoritas` e
      `accounts.0017_usuario_foto` (rodam normalmente no Passo 8, sem passo extra).
- [x] Mexeu em static/templates? **Sim** — login, cabeçalho, menu lateral e
      Início redesenhados; um template novo (`accounts/meu_perfil.html`).
- [x] Mexeu em requirements.txt? **Sim** — adicionado `Pillow` (necessário
      para o upload de foto de perfil; entra sozinho no Passo 7).
- O que mudou:
  - Tela de login com novo layout (banner + cartão).
  - Cabeçalho e menu lateral do sistema redesenhados: avatar do usuário,
    busca rápida de paciente, favoritos fixáveis no menu, e uma faixa "Agora"
    no Início mostrando o próximo atendimento do profissional.
  - Foto de perfil: cada usuário troca a própria em "Meu perfil" (clique no
    avatar, no canto superior direito). Guardada em `media/usuarios/fotos/`
    — pasta nova, criada sozinha no primeiro upload; **não precisa criar nem
    configurar nada no Nginx**, o próprio Django serve esses arquivos (ver
    `config/urls.py`). Como não é excluída pelo rsync do Passo 6, uma foto já
    enviada em produção sobrevive normalmente às próximas atualizações.

## Passo 2 — Backup do banco de produção (não pule isto)

No servidor, **antes de tocar em qualquer arquivo**. Primeiro garanta que a
pasta de backups existe (se o backup automático da Parte 14 da instalação
ainda não foi configurado, ela **não existe**):

```bash
sudo mkdir -p /opt/smedim/backups
sudo chown smedim:smedim /opt/smedim/backups
```

Depois o backup em si — uma linha só:

```bash
sudo docker exec smedim-postgres pg_dump -U clinica_smedim clinica_smedim > /opt/smedim/backups/backup_pre-update_$(date +%Y%m%d-%H%M).sql
```

O banco da clínica roda **dentro do Docker**, no container `smedim-postgres`
(ver Parte 4 da instalação), então o backup é feito com o `pg_dump` de dentro
do container — **não pede a senha do banco**, só a do `sudo` (a mesma do login
do usuário `smedim`). Pode demorar um pouco. **Não** acrescente `-t` ao
`docker exec` (ele estraga o arquivo de backup).

Histórico (15/10/2026) — o que **não** funciona neste servidor: o
`pg_dump -h 127.0.0.1 -U clinica_smedim ...` direto (pede a senha do banco e,
mesmo com ela, dá `server version mismatch`: o `pg_dump` do Ubuntu é o 12 e o
banco é o 16) e o `sudo -u postgres pg_dump` (não há Postgres fora do Docker).

Confirme que o arquivo **não está vazio**:

```bash
ls -lh /opt/smedim/backups/
```

Deve aparecer o `.sql` com tamanho de alguns MB (não `0`). Se der erro de
autenticação ou o arquivo vier vazio, **não siga** para os próximos passos.

(O backup automático diário da Parte 14 da instalação usava o `pg_dump` do
Ubuntu e por isso **também falhava** neste servidor — ver a Parte 14 corrigida
no guia de instalação.)

## Passo 3 — Levar o pacote para o servidor

**3.1 — Limpe restos de atualização anterior** (senão dá para usar o zip velho
por engano). Faça **antes** de copiar o zip novo. O `sudo` é necessário porque
a pasta extraída tem subpastas sem permissão de escrita:

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```

**3.2 — Leve o zip novo.** Mesmas três opções da Parte 2 da instalação
(AnyDesk, pendrive ou SCP) — o destino final é sempre `/tmp/smedim-update.zip`
**no servidor**:

```powershell
scp smedim-update.zip usuario@192.168.18.X:/tmp/
```

**Se o zip chegou na pasta Downloads do próprio servidor** (baixado pelo
navegador, AnyDesk etc.), ele ainda **não está no `/tmp`** — copie:

```bash
ls -lh ~/Downloads/smedim-update*.zip
cp ~/Downloads/smedim-update.zip /tmp/smedim-update.zip
ls -lh /tmp/smedim-update.zip
```

Se o arquivo tiver outro nome (por exemplo `smedim-update (1).zip`), use o
nome exato que o primeiro `ls` mostrou, entre aspas, no `cp`. O tamanho deve
ser cerca de 1 MB.

**3.3 — Confira que é o zip desta versão.** O tamanho e a data do arquivo não
provam nada (a data é a da cópia). Veja o conteúdo:

```bash
unzip -p /tmp/smedim-update.zip LEIA-ME-ATUALIZACAO-CLINICA.md | grep -m1 -A1 "Resumo desta leva"
```

Deve citar a versão mais recente listada neste guia (a primeira da seção
"Versão", mais abaixo). Para conferir uma migração específica:
`unzip -l /tmp/smedim-update.zip | grep NOME_DA_MIGRACAO`. Se não bater, é o
zip velho: **não siga**.

## Passo 4 — Parar o serviço

```bash
sudo systemctl stop smedim
sudo systemctl status smedim
```

`status` deve mostrar `inactive (dead)`. O site vai ficar fora do ar a partir
daqui até o Passo 11 — se possível, avise a clínica e faça fora do horário de
atendimento.

## Passo 5 — Guardar uma cópia da versão atual (rollback rápido)

```bash
sudo cp -a /opt/smedim/backend /opt/smedim/backend.bak-$(date +%Y%m%d-%H%M)
```

Isso copia a pasta inteira (código, `venv`, `.env`, `staticfiles`) para um
lugar seguro. Se algo der errado nos próximos passos, é só apagar
`/opt/smedim/backend` e renomear esta cópia de volta para `backend`.

## Passo 6 — Substituir o código

**Só faça depois de concluir os Passos 2 (backup), 4 (serviço parado) e 5
(cópia do código atual).** Não apague `venv/`, `.env` nem `staticfiles/` —
só o código-fonte muda. O zip não traz essas pastas, então copiar o
conteúdo dele por cima não as toca.

```bash
cd /tmp
unzip -o smedim-update.zip -d smedim-update-extraido
```

O aviso `appears to use backslashes as path separators` é **normal** (zip
feito no Windows) e inofensivo — o `unzip` converte sozinho. Nesse ponto
nada do sistema foi alterado ainda; os arquivos só foram extraídos em `/tmp`.

Agora copie por cima do sistema (comando curto de propósito — o `.` depois
da barra é necessário):

```bash
sudo cp -a /tmp/smedim-update-extraido/. /opt/smedim/backend/
```

Se terminar sem mensagem, funcionou. Confira que o código novo chegou
(troque pelo arquivo novo da versão, se quiser):

```bash
ls /opt/smedim/backend/agenda/templates/agenda/guias_pendentes.html
```

**O `sudo` é obrigatório**: a pasta `/opt/smedim/backend` pertence ao
`www-data`; sem `sudo` aparecem vários `Permission denied` e nada é copiado
(o código antigo fica intacto — é só repetir com `sudo`).

*Alternativa com `rsync`* (mantém o que não veio no pacote e ignora
`venv`/`.env`/`staticfiles`; comando longo, que costuma ser cortado ao
colar — use só se preferir):

```bash
sudo rsync -a --exclude venv --exclude .env --exclude staticfiles --exclude 'db.sqlite3*' /tmp/smedim-update-extraido/ /opt/smedim/backend/
```

Se o servidor não tiver `rsync` ou `unzip`: `sudo apt install -y rsync unzip`.

### Permissões (ainda no Passo 6)

```bash
sudo find /opt/smedim/backend -type d -exec chmod 755 {} \;
sudo find /opt/smedim/backend -type f -exec chmod u+rw,go+r {} \;
sudo chown -R www-data:www-data /opt/smedim/backend
```

**Faça isto logo aqui, antes dos Passos 7, 8 e 9** — o zip vindo do Windows
traz as pastas novas sem permissão de acesso, e sem isso o `collectstatic`
falha com `Permission denied` em `.../static/...`.

## Passo 7 — Atualizar dependências (só se `requirements.txt` mudou)

```bash
cd /opt/smedim/backend
sudo -u www-data venv/bin/pip install -r requirements.txt
```

(`sudo -u www-data` é obrigatório: o `venv` pertence ao `www-data`; sem isso
o pip falha com `Permissão negada`. Vale para os Passos 8 e 9 também.)

Se não mudou nada em `requirements.txt` nesta versão, pode pular — não faz
mal rodar de novo mesmo assim, só demora um pouco.

## Passo 8 — Rodar migrações novas

```bash
sudo -u www-data venv/bin/python manage.py migrate
```

 Se não houver migração nova, o comando só confirma
"No migrations to apply" e não faz nada — sempre seguro rodar.

## Passo 9 — `collectstatic` (só se mexeu em CSS/JS/imagens/templates)

```bash
sudo -u www-data venv/bin/python manage.py collectstatic --noinput
```

## Passo 10 — Corrigir permissões

```bash
sudo find /opt/smedim/backend -type d -exec chmod 755 {} \;
sudo chown -R www-data:www-data /opt/smedim/backend
```

(já feito no fim do Passo 6 — repetir aqui é só garantia, pois o
`collectstatic` e o `migrate` podem ter criado arquivos novos.)

## Passo 11 — Subir o serviço de novo e testar

```bash
sudo systemctl start smedim
sudo systemctl status smedim
```

Deve voltar para `active (running)`. Depois:

1. Abra `http://192.168.18.X/` de outro computador da clínica e confirme
   login normal.
2. Teste especificamente a área que mudou nesta versão.
3. Se tudo certo, pode apagar a cópia de segurança do Passo 5 depois de
   alguns dias (não precisa apagar na hora):
   ```bash
   sudo rm -rf /opt/smedim/backend.bak-AAAAMMDD-HHMM
   ```
4. Limpe os arquivos temporários da atualização (contêm o código todo; não
   deixe no `/tmp`):
   ```bash
   rm -f /tmp/smedim-update.zip
   sudo rm -rf /tmp/smedim-update-extraido
   ```

## Passo 12 — Se algo quebrar: rollback

```bash
sudo systemctl stop smedim
sudo rm -rf /opt/smedim/backend
sudo mv /opt/smedim/backend.bak-AAAAMMDD-HHMM /opt/smedim/backend
sudo systemctl start smedim
sudo systemctl status smedim
```

Isso volta o código para a versão anterior. Se a migração do Passo 8 já
tiver alterado o banco e isso for incompatível com o código antigo, restaure
também o banco a partir do backup do Passo 2:

```bash
sudo systemctl stop smedim
sudo docker exec smedim-postgres psql -U clinica_smedim -d clinica_smedim -c "DROP OWNED BY clinica_smedim;"
sudo docker exec -i smedim-postgres psql -U clinica_smedim -d clinica_smedim < /opt/smedim/backups/backup_pre-update_AAAAMMDD-HHMM.sql
sudo systemctl start smedim
```

(troque o nome do arquivo pelo backup gerado no Passo 2 desta atualização.
`DROP OWNED BY` apaga todas as tabelas do sistema (inclusive o que a migração criou) antes de
restaurar — só faça isso junto com a restauração do backup, nunca sozinho.)
</content>
