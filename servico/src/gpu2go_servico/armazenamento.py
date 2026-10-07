"""Armazenamento do serviço (Cloudflare R2). O cliente S3 fica em `gpu2go_worker.armazenamento`,
compartilhado com o `lote_nuvem` (espelho dos modelos).

Chaves dos objetos:
    clientes/<cliente>/<lote>/entrada/<foto>.<ext>    (apagadas em 7 dias, regra no bucket)
    clientes/<cliente>/<lote>/saida/<video>.mp4       (apagadas em 30 dias)
    modelos/<sha256>                                  (espelho dos modelos; ver lote_nuvem)
"""

from __future__ import annotations

from gpu2go_worker.armazenamento import Armazenamento, ArmazenamentoError

__all__ = ["Armazenamento", "ArmazenamentoError", "chave_entrada", "chave_saida"]


def chave_entrada(cliente: str, lote: str, nome: str) -> str:
    return f"clientes/{cliente}/{lote}/entrada/{nome}"


def chave_saida(cliente: str, lote: str, nome: str) -> str:
    return f"clientes/{cliente}/{lote}/saida/{nome}"
