# Atualização 2026-10-16 — passo a passo na clínica

**O que levar:** o arquivo `smedim-update.zip` (pasta `PROJETO SMEDIM`, ao lado da pasta `backend`).
Ele já traz **tudo** que ainda não foi aplicado no servidor (versões de 13/10, 14/10, 15/10 e 16/10).
Os detalhes de cada passo estão no [LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

**Novidades desta leva (4 migrações novas, só acrescentam — não alteram dado existente):**
- **Autorizar sem assinatura:** caixa "Sem assinatura" na tela Autorizar (e ao marcar a 2ª guia em
  Guias pendentes). Fica em **Guias pendentes**, coluna Assinatura, até clicar **Assinou** (grava o dia
  em que o paciente assinou e quem registrou). A
  profissional vê as dela em Agenda → **Minhas guias pendentes**.
- **Proprietário e repasse particular:** o proprietário registra os pagamentos em Financeiro →
  **Saldos e pagamentos de repasse** e ele mesmo não aparece mais na lista (não há repasse a ele).
- **Retorno do sistema antigo:** no agendamento, caixa "Retorno de consulta feita no sistema antigo"
  — a consulta sai como retorno grátis.
- **Senha por profissional:** em especialidade com 2 ou mais profissionais no dia, o totem pergunta
  **"Com qual profissional?"** (tem também "Não sei / qualquer um"). Na Agenda do dia, em
  **Especialidades de hoje**, cada atendente marca **quais profissionais atende** (nenhum = todos)
  e só chama as senhas deles (mais as de "Não sei").
- **Agenda do dia → Lista do dia:** coluna **Telefone** do paciente, para confirmar por telefone,
  e embaixo do nome uma obs pequena **"agendado por <usuário>"**.
- **Painel de chamada (TV):** guichê bem maior e **voz** — "Senha P zero zero sete. Guichê 1." e,
  na chamada do profissional, "Maria, Consultório 3." (só o primeiro nome). Fala 2 vezes.
- **Voz do painel pelo servidor:** a voz passa a ser gerada no servidor e funciona na TV box
  (TV Bro). Precisa instalar uma vez no servidor — **passo 6c**.
- **Início do profissional:** "Ver agenda do dia" não dá mais **erro 403** (abre a Minha agenda).
- **Agenda do dia:** busca no filtro de **Profissional** (digite parte do nome) e, ao
  Pré-confirmar/Confirmar, a tela volta para a **mesma linha**, não pro topo.
- **Minha agenda:** a lista de hoje do profissional mostra **só os Autorizados**
  (quem já foi atendido sai da lista).
- **Status automático:** no dia do atendimento, todo **Agendado** vira **Pré-confirmado** sozinho
  (encaixe do dia já nasce pré-confirmado). Na virada do dia, quem ficou em **Agendado** ou
  **Pré-confirmado** (não foi confirmado nem atendido) vira **Faltou**. Roda sozinho no
  primeiro acesso de cada dia — não precisa de nada no servidor.

Faça fora do horário de atendimento: o sistema fica fora do ar do passo 4 ao passo 9.

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

Tem que aparecer **"versão 2026-10-16"**. Se aparecer outra, é o zip velho: não siga.

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

Tem que aparecer, entre outras, `cadastros.0030_atendimento_profissional... OK`,
`fila.0007_senha_profissional... OK`, `agenda.0030_assinatura_e_retorno_manual... OK` e
`agenda.0031_registro_da_assinatura... OK` (as versões anteriores que vêm junto podem ter mais) —
deixe rodar até o fim.

### 6b. Ver quantos atendimentos vão mudar de status (só conta, não altera)

Quando o sistema subir, o status automático roda no primeiro acesso e vale para **todo o
histórico**: tudo de dias anteriores ainda em Agendado/Pré-confirmado vira **Faltou**. Veja
antes quantos são:

```bash
sudo -u www-data venv/bin/python manage.py shell -c "from django.utils import timezone; from agenda.models import Agendamento as A; h = timezone.localdate(); print('Viram Faltou:', A.objects.filter(status__in=['AGENDADO','PRE_CONFIRMADO'], data_hora_inicio__date__lt=h).count()); print('Viram Pre-confirmado (hoje):', A.objects.filter(status='AGENDADO', data_hora_inicio__date=h).count())"
```

Se o número de "Viram Faltou" parecer alto demais (atendimentos antigos que na verdade
aconteceram e só não foram marcados), **pare antes do passo 8** e desligue a regra até
decidir: abra o `.env` (`sudo nano /opt/smedim/backend/.env`), acrescente a linha
`AGENDA_STATUS_AUTOMATICO=0`, salve e siga. Para ligar depois, apague a linha e
reinicie (`sudo systemctl restart smedim`).

### 6c. Voz do painel gerada no servidor (uma vez só — precisa de internet no servidor)

A TV box (TV Bro/Firefox) não fala com a voz do navegador. Com isto, o **servidor** gera a voz
e a TV só toca um áudio comum — funciona em qualquer navegador. São ~100 MB de download.

```bash
cd /opt/smedim/backend
sudo -u www-data venv/bin/pip install -r requirements-voz.txt
sudo -u www-data mkdir -p voz
sudo -u www-data venv/bin/python -m piper.download_voices pt_BR-faber-medium --download-dir voz
ls -lh voz/
```

O `ls` tem que mostrar `pt_BR-faber-medium.onnx` (~61 MB) e `pt_BR-faber-medium.onnx.json`.
Se o `pip install` der erro, **não tem problema**: siga a atualização normalmente (o painel
continua com a voz do navegador) e me mande a mensagem de erro.

Nas próximas atualizações não precisa repetir: a pasta `voz/` e o pacote ficam no servidor.

**Velocidade da fala:** o padrão é `1.4` (mais devagar que o normal, `1.0`). Para ajustar sem
mexer no código, ponha no `.env` a linha `PAINEL_VOZ_VELOCIDADE=1.6` (maior = mais devagar) e
rode `sudo systemctl restart smedim`.

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

1. **Agenda do dia:** na "Lista do dia", conferir a coluna **Telefone**.
2. **Profissional:** entrar com um profissional que tenha paciente hoje → no Início, clicar em
   **Ver agenda do dia** → tem que abrir a **Minha agenda** (sem erro 403).
3. **Painel (TV box, no TV Bro mesmo):** abrir o painel de novo, clicar em **Ativar som** com o
   controle → deve falar "Som ativado" (a 1ª vez depois de subir o sistema pode levar ~10 s).
   Teste direto do servidor, em qualquer navegador: `http://<ip-do-servidor>/fila/painel/voz/teste/0/`
   tem que tocar "Som ativado".
4. Chamar uma senha na recepção → o painel deve tocar o bipe e falar "Senha ... Guichê ...".
5. Chamar um paciente pela Minha agenda → deve falar o primeiro nome e o consultório.
6. **Senha por profissional:** num dia com 2 profissionais da mesma especialidade, tirar uma
   senha no totem escolhendo um deles → o papel sai com "Especialidade — Nome". Na Agenda do dia,
   em Especialidades de hoje, marcar só esse profissional, salvar e chamar a senha.
7. **Sem assinatura:** autorizar um atendimento de convênio marcando "Sem assinatura" → em Guias
   pendentes aparece "✍ 1ª guia sem assinatura"; clicar **Assinou** (com a data em que assinou) → sai da
   pendência e fica o registro "✔ assinada em …" (Mostrar → "Pendentes e já autorizadas/assinadas").
8. **Status automático:** na Agenda do dia de hoje, ninguém deve estar mais em "Agendado"
   (viraram "Pré-confirmado"); num dia anterior, quem não foi confirmado aparece como "Faltou".

Se algo der errado, siga o **Passo 12 (rollback)** do
[LEIA-ME-ATUALIZACAO-CLINICA.md](LEIA-ME-ATUALIZACAO-CLINICA.md).

## 10. PC da TV do painel (opcional, uma vez só)

Para o painel falar sozinho depois de reiniciar o PC, sem precisar clicar em "Ativar som",
ponha no atalho do Chrome do painel (botão direito → Propriedades → **Destino**, depois de `chrome.exe"`):

```
--autoplay-policy=no-user-gesture-required
```

Feche todos os Chrome antes de abrir pelo atalho.

## 11. Limpar os temporários

```bash
rm -f /tmp/smedim-update.zip
sudo rm -rf /tmp/smedim-update-extraido
```

Se ainda não fez a unificação de pacientes duplicados da versão 13/10, veja o
[ATUALIZACAO-2026-10-13-PASSO-A-PASSO.md](ATUALIZACAO-2026-10-13-PASSO-A-PASSO.md).
