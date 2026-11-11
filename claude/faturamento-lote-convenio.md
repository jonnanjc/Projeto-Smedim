# Faturamento — Lote de convênio (produção x envio x retorno), especificado, ainda não implementado

Anotado em 21/07/2026, a partir de `claude/mapa-geral-projeto.md` e
`claude/detalhe-agenda-financeiro.md` (levantamento do estado atual de `agenda`
e `financeiro`). Este documento descreve um módulo novo, decidido com o
cliente, que **ainda não existe no código**.

## Problema que o módulo resolve

Hoje o sistema sabe quanto a clínica **produziu** por convênio
(`financeiro.producao_por_convenio`, somando `Agendamento.valor_convenio` de
atendimentos `Realizado`), mas não tem nenhuma noção de **lote/remessa/guia
enviada ao convênio** — confirmado por busca no código inteiro (`agenda`,
`financeiro`, `cadastros`): não existe `data_envio_convenio`, `numero_lote`,
`numero_guia` nem equivalente. Cada `Agendamento` de convênio é tratado como
unidade isolada.

O cliente quer poder registrar, por exemplo:
- Produzido: R$ 20.000
- Enviado ao plano (lote): R$ 25.000

E depois comparar produção x envio x o que efetivamente voltou pago/glosado do
convênio.

## Modelo de dados novo (app `financeiro` — primeiro model próprio do app)

`financeiro` hoje não tem nenhum model (reaproveita campos de
`agenda.Agendamento` e `agenda.PagamentoParticular`). Este é o primeiro model
próprio do app.

### `LoteFaturamento`

| Campo | Tipo | Observação |
|---|---|---|
| `convenio` | FK → `cadastros.Convenio` | Obrigatório. Lote é **sempre** vinculado a um convênio específico (decisão do cliente — não existe lote "geral"). |
| `data_envio` | `DateField` | Data em que o lote foi mandado ao plano. |
| `numero_lote` | `CharField`, blank | Identificação que o convênio usa, se exigir. |
| `valor_total_enviado` | `DecimalField(10,2)` | O valor que foi de fato enviado (pode ser diferente da soma dos agendamentos, se a clínica arredondar/ajustar — a definir se deve ser sempre igual à soma ou editável). |
| `status` | choices: `ENVIADO` / `RETORNO_PARCIAL` / `FECHADO` | Ciclo de vida do lote. |
| `agendamentos` | M2M → `agenda.Agendamento` | Os atendimentos incluídos neste lote. **Ver regra de trava abaixo.** |
| `criado_por` | FK → `accounts.Usuario`, `SET_NULL` | Quem lançou o lote. |
| `criado_em` | `DateTimeField(auto_now_add)` | — |

**Regra de trava (obrigatória, decisão do cliente):** um `Agendamento` não pode
entrar em dois lotes ao mesmo tempo. Validar no momento de montar/editar o
lote: se o agendamento já está em outro `LoteFaturamento` sem retorno
lançado, bloquear a inclusão.

**Filtro de elegibilidade:** a tela de "montar lote" só deve listar
agendamentos que:
- `status = REALIZADO`
- `forma_atendimento = CONVENIO`
- `retorno_de__isnull=True` (retorno grátis nunca entra em lote — mesmo filtro
  que já existe hoje em `contas_receber_lista`; retorno sempre tem
  `valor_convenio = 0.00` e nada a cobrar do convênio)
- não estar em nenhum outro lote ainda sem retorno

### `RetornoLoteItem`

Por atendimento dentro do lote, não só um valor total — porque glosa é
normalmente parcial (alguns itens pagos, outros negados).

| Campo | Tipo | Observação |
|---|---|---|
| `lote` | FK → `LoteFaturamento` | — |
| `agendamento` | FK → `agenda.Agendamento` | Precisa estar em `lote.agendamentos`. |
| `valor_pago` | `DecimalField(8,2)` | Quanto o convênio pagou deste item específico. |
| `valor_glosado` | `DecimalField(8,2)` | Quanto foi negado/glosado deste item. |
| `motivo_glosa` | `TextField`, blank | Motivo informado pelo convênio, se houver. |
| `data_retorno` | `DateField` | Quando o retorno chegou. |

## Integração com o fluxo já existente de Contas a receber

Decisões do cliente, para não duplicar trabalho nem permitir baixa dupla:

1. **Ao salvar um `RetornoLoteItem` com `valor_pago > 0`**, o sistema marca
   automaticamente no `Agendamento` correspondente:
   ```python
   agendamento.convenio_recebido = True
   agendamento.convenio_recebido_em = timezone.now()
   agendamento.save(update_fields=["convenio_recebido", "convenio_recebido_em"])
   ```
   Mesmo efeito que hoje só acontece manualmente em
   `financeiro:contas_receber_marcar_recebido` — só que disparado pelo
   lançamento do retorno do lote, e não por um clique separado do financeiro.
   Se vier só glosado (`valor_pago = 0`), **não** marca como recebido — fica
   pendente (o que fazer com valor glosado — reenviar, cobrar do paciente,
   etc. — fica de fora do escopo deste módulo por ora).

2. **Trava contra baixa duplicada pelo caminho antigo:** enquanto um
   `Agendamento` está dentro de um `LoteFaturamento` ainda sem retorno
   (`status` do lote = `ENVIADO`), ele deve:
   - sumir (ou aparecer desabilitado, com aviso "Está no lote nº X, enviado em
     Y") da aba "A receber" de `contas_receber_lista`;
   - ter o botão "Marcar como recebido" indisponível para ele nessa tela.

   Isso evita que alguém dê baixa manual em Contas a receber enquanto o mesmo
   atendimento está esperando retorno de um lote.

## Comparação produção x envio x retorno

Reaproveitar a agregação que já existe em `financeiro.producao_por_convenio`
(soma de `valor_convenio` de `Agendamento` `Realizado`, por convênio) como o
número de "produzido". Não recalcular produção do zero — só comparar contra:

- **Enviado**: soma de `LoteFaturamento.valor_total_enviado` no período/convênio
- **Pago**: soma de `RetornoLoteItem.valor_pago` no período/convênio
- **Glosado**: soma de `RetornoLoteItem.valor_glosado` no período/convênio
- **Pendente de lote**: produzido − soma dos agendamentos já incluídos em
  algum lote (ainda não foi pra lote nenhum)

## Permissões novas (`accounts/permissoes.py`)

Seguindo o padrão do projeto — toda função nova precisa ter opção de
ativar/desativar por perfil, como as demais permissões do sistema:

- `faturamento_lote_lancar` — criar lote e incluir agendamentos nele
- `faturamento_lote_visualizar` — ver lotes de faturamento e seus detalhes
- `faturamento_comparativo_visualizar` — ver comparativo de produção x envio x retorno
  (dividida de `faturamento_lote_visualizar` em 28/07 — pedido do cliente pra poder
  liberar cada página do financeiro separadamente, ver `claude/detalhe-agenda-financeiro.md`)
- `faturamento_retorno_lancar` — lançar retorno/glosa por item do lote

Perfis padrão sugeridos (a confirmar com o cliente, seguindo o padrão de
`financeiro_lancar`/`financeiro_conciliar`): Financeiro e Administrador.

## Pontos em aberto (não bloqueiam a implementação, mas vale registrar)

- `valor_total_enviado` do lote: deve ser sempre igual à soma dos
  `valor_convenio` dos agendamentos incluídos (calculado, não editável), ou a
  clínica pode digitar um valor diferente (ex.: arredondamento)? Este
  documento assume campo editável por ora.
- O que fazer operacionalmente com o valor glosado (reenviar em novo lote?
  registrar como perda? cobrar do paciente?) — fora do escopo deste módulo,
  fica só registrado (`valor_glosado` + `motivo_glosa`), sem fluxo automático
  disparado a partir disso.

## Referência

Ver também `claude/detalhe-agenda-financeiro.md` para os campos completos de
`Agendamento`/`PagamentoParticular` e o funcionamento atual de Contas a
receber/repasse, que este módulo se conecta.
