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
DURACAO_MIN = 4  # pago e interno: 4 e 5 s; 3 s só no teste gratuito
# teto dos pedidos novos (pedido do dono, 13/10/2026): acima de 5 s, o modelo repete o movimento e deforma
# (docs/06, seção 9.1). Os pedidos de antes seguem como foram feitos: o pod continua aceitando até 7 s
DURACAO_MAX = 5
VALOR = re.compile(r"^-?[0-9]{1,7}([.,][0-9]{1,2})?$")
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


def preco_video(qualidade: str, duracao: int) -> int:
    return PRECOS[qualidade] * duracao


# montagem de vídeos (ADR 0015, 13/10/2026): quem usa e quanto custa, pelo .env
#   RENDER2GO_MONTAGEM        desligada | interno (padrão: só o plano interno, sem custo) | todos (também o pago)
#   RENDER2GO_PRECO_MONTAGEM  reais por montagem pronta (docs/11); sem ele, o plano pago não usa nem com "todos"
MODOS_MONTAGEM = ("desligada", "interno", "todos")


def montagem_do_ambiente() -> tuple[str, int | None]:
    """(modo, preço em centavos ou None). ValueError fora do formato (a API não sobe com o .env errado)."""
    modo = os.environ.get("RENDER2GO_MONTAGEM", "").strip().lower() or "interno"
    if modo not in MODOS_MONTAGEM:
        raise ValueError(f"RENDER2GO_MONTAGEM: {', '.join(MODOS_MONTAGEM)}")
    bruto = os.environ.get("RENDER2GO_PRECO_MONTAGEM", "").strip()
    preco = centavos(bruto) if bruto else None
    if preco is not None and preco <= 0:
        raise ValueError("RENDER2GO_PRECO_MONTAGEM precisa ser maior que zero")
    return modo, preco


def acesso_montagem(modo: str, preco: int | None, plano: str) -> tuple[bool, int | None]:
    """(a conta pode montar, o preço que paga por montagem pronta; None = sem custo). O plano interno monta sem
    custo (com o modo "interno" ou "todos"); o pago, só com "todos" e o preço definido; o teste gratuito, nunca."""
    if modo == "desligada":
        return False, None
    if plano == "interno":
        return True, None
    if plano in PLANOS_COBRADOS and modo == "todos" and preco:
        return True, preco
    return False, None
