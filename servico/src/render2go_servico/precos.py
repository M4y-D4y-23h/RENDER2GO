"""Preços do pré-pago (09/10/2026, docs/11): o cliente do plano "pago" recarrega o saldo e paga por
segundo de vídeo. O pedido é cobrado inteiro quando as fotos terminam de chegar; o pedido recusado
e cada vídeo que não sair voltam para o saldo. Teste gratuito e plano interno não pagam.

Valores em centavos de real. Os preços por segundo vêm do .env (padrão: US$ 0,05 e US$ 0,10 no
câmbio de R$ 5,00 que o dono do serviço usou):
    RENDER2GO_PRECO_480P_S   reais por segundo de vídeo em 480p (qualidade "rapida"; padrão 0,25)
    RENDER2GO_PRECO_720P_S   reais por segundo de vídeo em 720p (qualidade "alta"; padrão 0,50)
"""

from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation

PLANOS_COBRADOS = ("pago",)
DURACAO_MIN = 4  # pago e interno: de 4 a 7 s; 3 s só no teste gratuito
VALOR = re.compile(r"^-?\d{1,7}([.,]\d{1,2})?$")
MAX_MOVIMENTO = 100_000_00  # R$ 100 mil por recarga ou ajuste (contra erro de digitação)


def centavos(texto: str) -> int:
    """'19,90', '19.90', '50' ou '-5' em reais → centavos. ValueError fora do formato."""
    t = texto.strip().replace(" ", "")
    if not VALOR.match(t):
        raise ValueError(f"valor inválido: {texto!r} (use reais com até 2 casas, ex.: 19,90)")
    try:
        return int(Decimal(t.replace(",", ".")) * 100)
    except InvalidOperation as e:
        raise ValueError(f"valor inválido: {texto!r}") from e


def reais(c: int) -> str:
    """Centavos → 'R$ 1.234,50' (o sinal de menos vem antes do R$)."""
    sinal = "-" if c < 0 else ""
    inteiro, resto = divmod(abs(c), 100)
    return f"{sinal}R$ {inteiro:,}".replace(",", ".") + f",{resto:02d}"


def _preco(nome: str, padrao: str) -> int:
    valor = centavos(os.environ.get(nome, padrao))
    if valor <= 0:
        raise ValueError(f"{nome} precisa ser maior que zero")
    return valor


# centavos por segundo de vídeo, por qualidade (render2go_worker.lote.QUALIDADES)
PRECOS = {
    "rapida": _preco("RENDER2GO_PRECO_480P_S", "0,25"),
    "alta": _preco("RENDER2GO_PRECO_720P_S", "0,50"),
}
RESOLUCAO = {"rapida": "480p", "alta": "720p"}


def preco_video(qualidade: str, duracao: int) -> int:
    return PRECOS[qualidade] * duracao
