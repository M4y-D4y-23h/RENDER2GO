"""Perfil e preferências da conta do cliente (área do cliente, 10/10/2026).

Nome e sobrenome: opcionais, de 1 a 60 caracteres (letras de qualquer alfabeto, espaço, apóstrofo, hífen e
ponto). Tema: claro, escuro ou automático (o do aparelho).

Foto: não há como pegar a foto do Gmail ou do Outlook sem "Entrar com Google/Microsoft"; o caminho viável é o
Gravatar, **por escolha da pessoa** (desligado por padrão). Quem baixa é o servidor, não o navegador: host
fixo (gravatar.com), SHA-256 do e-mail em minúsculas (o e-mail não sai daqui), sem seguir redirecionamento,
5 s, até 200 KB, só PNG ou JPEG (conferidos pelos primeiros bytes). A página recebe a imagem do próprio
site (`GET /api/conta/foto`), e a CSP não muda. Sem foto, a página mostra a inicial do e-mail.
"""

from __future__ import annotations

import hashlib
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from collections import OrderedDict
from collections.abc import Callable

TEMAS = ("claro", "escuro", "auto")
NOME_MAX = 60
SINAIS_NOME = " '’-."
GRAVATAR_URL = "https://gravatar.com/avatar/{}?d=404&s=160"
GRAVATAR_MAX_BYTES = 200 * 1024
GRAVATAR_TEMPO_S = 5.0
# quanto a resposta do Gravatar vale no cache de cada processo da API: foto, sem foto e falha de rede
VALIDADE_FOTO_S = 6 * 3600
VALIDADE_SEM_FOTO_S = 3600
VALIDADE_FALHA_S = 300


def nome_pessoa(texto: str | None) -> str | None:
    """O nome como fica guardado (espaços juntados, NFC), None se vazio; ValueError se não é um nome."""
    t = " ".join(unicodedata.normalize("NFC", texto or "").split())
    if not t:
        return None
    if len(t) > NOME_MAX:
        raise ValueError(f"use até {NOME_MAX} caracteres")
    if not any(ch.isalpha() for ch in t) or not all(ch.isalpha() or ch in SINAIS_NOME for ch in t):
        raise ValueError("use só letras, espaço, apóstrofo, hífen e ponto")
    return t


def hash_gravatar(email: str) -> str:
    """O identificador que o Gravatar usa: SHA-256 do e-mail sem espaços e em minúsculas."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


class _SemRedirecionar(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # redirecionamento vira erro: a foto só vem do endereço fixo


def baixar_gravatar(email: str, tempo_s: float = GRAVATAR_TEMPO_S) -> tuple[bytes, str] | None:
    """(bytes, tipo) da foto do Gravatar; None se a pessoa não tem foto lá ou a resposta não serve.
    OSError (rede, tempo esgotado) sobe: quem chama guarda a falha por pouco tempo."""
    req = urllib.request.Request(GRAVATAR_URL.format(hash_gravatar(email)), headers={"User-Agent": "render2go"})
    try:
        with urllib.request.build_opener(_SemRedirecionar).open(req, timeout=tempo_s) as r:
            if r.status != 200:
                return None
            dados = r.read(GRAVATAR_MAX_BYTES + 1)
    except urllib.error.HTTPError:  # 404 (sem foto), redirecionamento ou erro do Gravatar
        return None
    if len(dados) > GRAVATAR_MAX_BYTES:
        return None
    if dados.startswith(b"\x89PNG\r\n\x1a\n"):
        return dados, "image/png"
    if dados.startswith(b"\xff\xd8\xff"):
        return dados, "image/jpeg"
    return None


class CacheFotos:
    """Fotos do Gravatar na memória do processo: com milhares de páginas abertas, o Gravatar é consultado
    uma vez por pessoa a cada algumas horas. Teto em bytes (as mais antigas saem primeiro)."""

    def __init__(self, baixar: Callable[[str], tuple[bytes, str] | None] = baixar_gravatar,
                 max_bytes: int = 16 * 1024**2) -> None:  # fmt: skip
        self.baixar, self.max_bytes = baixar, max_bytes
        self._itens: OrderedDict[str, tuple[float, tuple[bytes, str] | None]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def foto(self, email: str) -> tuple[bytes, str] | None:
        chave, agora = hash_gravatar(email), time.monotonic()
        with self._lock:
            item = self._itens.get(chave)
            if item and item[0] > agora:
                self._itens.move_to_end(chave)
                return item[1]
        try:
            valor, validade = self.baixar(email), None
        except (OSError, ValueError):  # rede fora: sem foto agora, tenta de novo daqui a pouco
            valor, validade = None, VALIDADE_FALHA_S
        if validade is None:
            validade = VALIDADE_FOTO_S if valor else VALIDADE_SEM_FOTO_S
        with self._lock:
            self._guardar(chave, (agora + validade, valor))
        return valor

    def _guardar(self, chave: str, item: tuple[float, tuple[bytes, str] | None]) -> None:
        antigo = self._itens.pop(chave, None)
        if antigo and antigo[1]:
            self._bytes -= len(antigo[1][0])
        self._itens[chave] = item
        if item[1]:
            self._bytes += len(item[1][0])
        while self._bytes > self.max_bytes or len(self._itens) > 20_000:
            _, (_, velho) = self._itens.popitem(last=False)
            if velho:
                self._bytes -= len(velho[0])
