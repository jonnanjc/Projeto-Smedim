# Unificar pacientes duplicados — teste com a cópia do banco da clínica

Objetivo: testar a unificação **numa cópia** do banco da clínica, aqui no seu PC,
antes de mexer no servidor de verdade. Na clínica nada muda até a Parte C.

O que a unificação faz (resumo):

- **Casos 1, 2 e 3** (nome igual sem ligar pra acento/maiúscula — "Raissa" = "Raíssa" —
  **e** mesma data de nascimento, com ou sem telefone diferente): um comando junta
  todos de uma vez.
- **Casos 4 e 5** (só o nome igual): podem ser homônimos, então **não** são juntados
  sozinhos. Aparecem na tela nova **Cadastros → Pacientes → Possíveis duplicados**,
  onde alguém confere lado a lado (nascimento, CPF, telefone, quantos atendimentos)
  e clica em "Unificar" ou "Não são a mesma pessoa".
- Ao juntar, **todo** o histórico vai pra ficha que fica: agendamentos (com a
  produção, pagamentos e repasses deles), prontuário, documentos, pacotes e guias.
  Nada é apagado; a ficha repetida só sai da lista e da busca.
- O profissional não vê nada de "mesclado": o prontuário simplesmente fica completo.
- **Daqui pra frente:** ao cadastrar um paciente novo com um nome que já existe, o
  sistema mostra a ficha existente e só deixa salvar se marcar "é outra pessoa".

---

## Parte A — Na clínica: tirar a cópia do banco (só leitura, não muda nada)

No terminal do servidor:

```bash
sudo docker exec smedim-postgres pg_dump -U clinica_smedim clinica_smedim | gzip > ~/copia_clinica_$(date +%Y%m%d).sql.gz
```

```bash
ls -lh ~/copia_clinica_*.sql.gz
```

O arquivo precisa ter tamanho (alguns MB). Traga esse `.sql.gz` para o seu PC
(pendrive, AnyDesk, o jeito que você costuma usar) e coloque em:

    C:\copia-clinica\

(fora do OneDrive de propósito: a cópia tem dados de todos os pacientes e não
pode subir para a nuvem)

> O arquivo tem dados de pacientes. Depois do teste, apague a cópia do PC e do
> `~` do servidor.

## Parte B — Aqui no PC: conferir na cópia (feito em 06/11/2026)

A cópia já está num PostgreSQL de teste em `C:\copia-clinica\pgdata`, separado da
clínica e do banco de desenvolvimento, com a atualização nova e o lote (casos 1–3)
já aplicados. A produção conferiu idêntica antes e depois.

**Para abrir o sistema na cópia** (pode fechar e voltar outro dia, nada se perde):
dê dois cliques em `C:\copia-clinica\rodar_sistema_na_copia.cmd`, deixe a janela
preta aberta e entre em **http://localhost:8012** no navegador, com o seu login da
clínica. Para parar, feche a janela preta.

Confira em **Cadastros → Pacientes → Possíveis duplicados**: em cada grupo, junte
o que for a mesma pessoa ou clique em "Não são a mesma pessoa". Os botões pedem um
segundo clique (ficam vermelhos) para confirmar.

**Quando terminar**, me avise: eu gero o arquivo `conferencia.json` com as suas
decisões (só números das fichas, sem nome nem dado pessoal):

    python manage.py conferencia_duplicados exportar C:\copia-clinica\conferencia.json

Ensaio feito em 06/11: aplicar esse arquivo num banco igual ao da clínica deixou
fichas, agendamentos e prontuário **idênticos** aos da cópia conferida.

## Parte C — Na clínica, de uma vez

Vai junto com o pacote de atualização (`smedim-update.zip` e o passo a passo do dia,
como sempre). Leve também o `conferencia.json` e copie para o servidor em
`/tmp/conferencia.json` (se cair em Downloads: `cp ~/Downloads/conferencia.json /tmp/`).
Depois de atualizar:

1. **Backup** (obrigatório antes):

   ```bash
   sudo docker exec smedim-postgres pg_dump -U clinica_smedim clinica_smedim > ~/backup_antes_unificar_$(date +%Y%m%d-%H%M).sql
   ```

2. **Simular** as suas decisões (não grava nada):

   ```bash
   cd /opt/smedim/backend && sudo -u www-data venv/bin/python manage.py conferencia_duplicados importar /tmp/conferencia.json
   ```

   "Fichas a juntar" deve bater com o que foi conferido. "Decisões puladas" lista
   o que mudou na clínica desde a cópia (ficha renomeada, juntada por alguém etc.):
   esses ficam para conferir na tela, lá mesmo.

3. **Gravar** as suas decisões:

   ```bash
   cd /opt/smedim/backend && sudo -u www-data venv/bin/python manage.py conferencia_duplicados importar /tmp/conferencia.json --confirmar
   ```

4. **Lote dos casos novos** (pacientes cadastrados depois da cópia com nome +
   nascimento iguais). Tem que ser **depois** do passo 3:

   ```bash
   cd /opt/smedim/backend && sudo -u www-data venv/bin/python manage.py unificar_pacientes_duplicados --confirmar --relatorio /tmp/unificacao_feita.csv
   ```

5. O que sobrar aparece na tela **Cadastros → Pacientes → Possíveis duplicados**.
   Quem vê a tela: quem tem a permissão **"Excluir cadastro de pacientes"** (o
   Administrador já tem).

**Se der errado:** volte o backup do passo 1 (me chame antes: para voltar o backup
em cima do banco existente é preciso esvaziar o banco primeiro).

**Depois:** apague `~/copia_clinica_*.sql.gz` e `~/Downloads/copia_clinica_*` do
servidor, e a pasta `C:\copia-clinica` do PC.
