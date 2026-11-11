# Atualização 2026-11-06 (noite) — unificação de pacientes duplicados — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).
O arquivo com a sua conferência (`conferencia_duplicados_20261106.json`) **já vai dentro do zip**.
Detalhes de cada passo no [LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

**Faça fora do horário de atendimento.** O sistema fica parado do passo 3 ao fim do passo 4
(uns 10 minutos), e a unificação roda com ele parado, para ninguém estar mexendo num cadastro na hora.

**Esta versão tem 1 migração nova:** `cadastros.0035_duplicidade_ignorada` (tabela nova, começa vazia).

**Novidades:**

1. **Pacientes duplicados unificados:** 2.361 fichas repetidas são juntadas na ficha certa, com todo o
   histórico (agendamentos, produção, pagamentos, prontuário, documentos, pacotes, guias). Nada é apagado.
   O profissional só vê o prontuário completo, sem nenhum aviso de "mesclado".
2. **Tela nova Cadastros → Pacientes → Possíveis duplicados** (quem tem "Excluir cadastro de pacientes",
   o Administrador já tem): para os casos que aparecerem daqui pra frente.
3. **Cadastro de paciente novo** com um nome que já existe: o sistema mostra a ficha que já está lá e só
   salva marcando "é outra pessoa". No agendamento, dá para clicar em "Usar este".

**Ensaio feito em 06/11 às 23:03** com uma cópia do banco da clínica: 2.361 fichas juntadas, nenhuma
decisão pulada, produção, pagamentos, agendamentos e prontuários **idênticos** antes e depois.

## 1. Backup do banco (obrigatório)

```bash
sudo docker exec smedim-postgres pg_dump -U clinica_smedim clinica_smedim > /opt/smedim/backups/backup_pre-update_$(date +%Y%m%d-%H%M).sql
```

```bash
ls -lh /opt/smedim/backups/
```

O backup novo tem que ter **uns 25 MB ou mais** (sem compactar). Se aparecer com 0 ou poucos bytes, **pare aqui**.

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
unzip -l /tmp/smedim-update.zip | grep -E "0035_duplicidade_ignorada|conferencia_duplicados"
```

Tem que aparecer **"versão 2026-11-06-noite"** e **3 linhas**: `0035_duplicidade_ignorada.py`,
`conferencia_duplicados.py` e `conferencia_duplicados_20261106.json`. Se não, é o zip velho: **não siga**.

## 3. Aplicar (o sistema para aqui)

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
```

O `migrate` **tem que mostrar** `Applying cadastros.0035_duplicidade_ignorada... OK`.

**Ainda não ligue o sistema.** Sem `collectstatic` e sem `pip install` nesta versão.

## 4. Unificar os duplicados (com o sistema ainda parado)

**4a. Simular** (não grava nada):

```bash
cd /opt/smedim/backend && sudo -u www-data venv/bin/python manage.py conferencia_duplicados importar conferencia_duplicados_20261106.json
```

Tem que mostrar **"Fichas a juntar: 2361"** (ou bem perto) e **"Nenhuma decisão pulada"**. Se aparecer
alguma decisão pulada, tudo bem: é ficha que mudou na clínica desde a cópia, e ela fica para conferir na
tela depois. Se aparecer **erro** (texto em inglês, "Traceback"), **pare** e me mande a mensagem.

**4b. Gravar:**

```bash
cd /opt/smedim/backend && sudo -u www-data venv/bin/python manage.py conferencia_duplicados importar conferencia_duplicados_20261106.json --confirmar
```

Tem que mostrar **"GRAVADO"** e os mesmos números da simulação.

**4c. Lote dos casos novos** (pacientes repetidos cadastrados depois da cópia; no ensaio deu 0):

```bash
cd /opt/smedim/backend && sudo -u www-data venv/bin/python manage.py unificar_pacientes_duplicados --confirmar --relatorio /tmp/unificacao_lote.csv
```

**4d. Ligar o sistema:**

```bash
sudo systemctl start smedim
sudo systemctl status smedim
```

Status tem que ser `active (running)`.

## 5. Testar (recarregue com Ctrl+F5)

1. **Cadastros → Pacientes**, busque **Anderson Jose Brasil Dias**: aparece **uma ficha só**.
2. Com um login de profissional, abra o **Prontuário** dele: aparecem os registros antigos (8).
3. **Cadastros → Pacientes → Possíveis duplicados**: a lista está **vazia** (ou só com os casos pulados no 4a).
4. **Financeiro → Fechamento mensal** de outubro e novembro: os totais iguais aos de antes.
5. **+ Novo paciente** com o nome "Anderson Jose Brasil Dias": aparece o aviso de nome repetido. Clique
   em **Cancelar** (não salve).

## 6. Avisar a equipe

- **Recepção:** os pacientes repetidos sumiram da lista, porque agora cada pessoa tem uma ficha só. Ao
  cadastrar alguém com nome que já existe, o sistema mostra a ficha existente: se for a mesma pessoa,
  use a que já existe e atualize o telefone nela.
- **Profissionais:** nada muda na tela; o prontuário de quem tinha ficha repetida ficou completo.

## 7. Limpar

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```

```bash
rm -f ~/copia_clinica_*.sql.gz ~/Downloads/copia_clinica_*.sql.gz
```

(o último apaga as cópias do banco que você tirou para o teste; o backup do passo 1 continua em
`/opt/smedim/backups/`)

## Se der errado

Volte o backup do passo 1 seguindo o **Passo 12 — rollback** do
[LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md) (código + banco), ou me chame antes.
