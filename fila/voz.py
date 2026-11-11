"""
Voz do painel de chamada gerada no SERVIDOR (Piper, offline) — a TV só toca um .wav.

Por quê: a voz do próprio navegador (speechSynthesis) não funciona na TV box da clínica
(TV Bro/WebView não tem; o Firefox de lá também ficou mudo, mesmo com voz instalada no
Android). Áudio comum toca em qualquer navegador. Se o Piper não estiver instalado no
servidor (pacote opcional, ver requirements-voz.txt) ou o modelo de voz não existir, tudo
aqui devolve None e o painel volta a usar a voz do navegador — nada quebra.

O texto é montado AQUI a partir da chamada (nunca vem pronto da URL): o endpoint é
público (o painel não tem login), e assim ninguém consegue fazê-lo falar outra coisa.
"""
import io
import threading
import wave
from collections import OrderedDict

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from agenda.models import Agendamento
from cadastros.models import Sala

from .models import Senha

_trava = threading.Lock()
_voz = None
_voz_indisponivel = False
_cache = OrderedDict()  # texto -> bytes do .wav (últimos poucos, por processo)
_TAMANHO_CACHE = 32

TEXTO_TESTE = "Som ativado."


def _primeiro_nome(paciente):
    return (paciente.nome_completo or "").split(" ")[0].strip().capitalize() if paciente else ""


def texto_da_chamada(tipo, chave):
    """Frase falada pra uma chamada de HOJE, ou None se não existir. Mesmo texto que o
    painel usava na voz do navegador: senha soletrada + guichê; no consultório, só o
    primeiro nome (painel público) + sala."""
    hoje = timezone.localdate()
    if tipo == "teste":
        return TEXTO_TESTE
    if tipo == "senha":
        senha = Senha.objects.filter(pk=chave, data=hoje, chamado_em__isnull=False).select_related("guiche").first()
        if senha is None:
            return None
        # "P007" -> "P 0 0 7": lê "pê zero zero sete", igual está no papel.
        texto = "Senha " + " ".join(senha.codigo) + "."
        if senha.guiche_id:
            texto += f" {senha.guiche.nome}."
        return texto
    if tipo == "recepcao_propria":
        return _texto_da_leva(chave, hoje)
    if tipo == "agendamento":
        try:
            agendamento = (
                Agendamento.objects.filter(pk=chave, chamado_em__date=hoje).select_related("paciente", "sala_chamada").first()
            )
        except (ValueError, ValidationError):  # chave mal formada
            return None
        if agendamento is None:
            return None
        nome = _primeiro_nome(agendamento.paciente)
        if not agendamento.sala_chamada_id:
            return f"{nome}."
        # Ponto (não vírgula) entre nome e sala: a voz faz uma pausa de verdade entre os dois.
        return f"{nome}. {agendamento.sala_chamada.nome}."
    return None


def _texto_da_leva(lote, hoje):
    """Leva da Recepção 2 (mesmos membros que fila.views._chamadas_recepcao_propria mostra
    na tela: só quem ainda está Autorizado): o primeiro entra no consultório e TODOS os
    demais são chamados pelo nome pra aguardar na Recepção 2. Antes só o primeiro era
    falado — e, sem filtrar o status, continuava sendo o paciente já atendido."""
    try:
        membros = list(
            Agendamento.objects.filter(
                lote_recepcao=lote, chamado_em__date=hoje, status=Agendamento.Status.AUTORIZADO
            )
            .select_related("paciente", "sala_chamada")
            .order_by("ordem_no_lote", "pk")
        )
    except (ValueError, ValidationError):  # chave mal formada (ex.: UUID inválido)
        return None
    if not membros:
        return None
    primeiro, demais = membros[0], membros[1:]
    nome = _primeiro_nome(primeiro.paciente)
    texto = f"{nome}. Entrar no {primeiro.sala_chamada.nome}." if primeiro.sala_chamada_id else f"{nome}."
    if demais:
        nomes = ", ".join(_primeiro_nome(m.paciente) for m in demais)
        texto += f" {nomes}. Aguardar na {Sala.NOME_RECEPCAO_PROPRIA}."
    return texto


def _carregar_voz():
    """Carrega o modelo uma vez por processo (demora uns segundos) e já "aquece" — a 1ª
    frase sintetizada é bem mais lenta que as seguintes."""
    global _voz, _voz_indisponivel
    if _voz is not None or _voz_indisponivel:
        return _voz
    with _trava:
        if _voz is not None or _voz_indisponivel:
            return _voz
        if not settings.PAINEL_VOZ_MODELO:
            _voz_indisponivel = True
            return None
        try:
            from piper import PiperVoice

            voz = PiperVoice.load(str(settings.PAINEL_VOZ_MODELO))
            with wave.open(io.BytesIO(), "wb") as descarte:
                voz.synthesize_wav(TEXTO_TESTE, descarte)
            _voz = voz
        except Exception:
            # Piper não instalado, modelo ausente/corrompido: painel usa a voz do navegador.
            _voz_indisponivel = True
    return _voz


def aquecer_em_segundo_plano():
    """Chamado ao abrir o painel: carrega a voz numa thread, pra 1ª chamada do dia não
    esperar o carregamento."""
    if _voz is None and not _voz_indisponivel:
        threading.Thread(target=_carregar_voz, daemon=True).start()


def gerar_wav(texto):
    """Bytes de um .wav com o texto falado, ou None se a voz do servidor não está disponível."""
    if texto in _cache:
        _cache.move_to_end(texto)
        return _cache[texto]
    voz = _carregar_voz()
    if voz is None:
        return None
    from piper import SynthesisConfig

    buffer = io.BytesIO()
    with _trava:
        with wave.open(buffer, "wb") as arquivo:
            # length_scale > 1 = fala mais devagar (sala de espera, TV longe) — ajustável no
            # .env (PAINEL_VOZ_VELOCIDADE), ver config/settings.py.
            voz.synthesize_wav(
                texto, arquivo, syn_config=SynthesisConfig(length_scale=settings.PAINEL_VOZ_VELOCIDADE)
            )
    dados = buffer.getvalue()
    _cache[texto] = dados
    while len(_cache) > _TAMANHO_CACHE:
        _cache.popitem(last=False)
    return dados
