"""Armazenamento do serviço (Cloudflare R2). O cliente S3 fica em `gpu2go_worker.armazenamento`,
compartilhado com o `lote_nuvem` (espelho dos modelos).

Chaves dos objetos (o prefixo decide a retenção: as regras de ciclo de vida do R2 filtram só
pelo começo da chave):
    entrada/<cliente>/<lote>/<foto>.<ext>             (apagadas em 7 dias, regra no bucket)
    saida/<cliente>/<lote>/<video>.mp4 e conferencia/ (apagadas em 30 dias)
    modelos/<sha256>                                  (espelho dos modelos; nunca apagar)
"""

from __future__ import annotations

from gpu2go_worker.armazenamento import Armazenamento, ArmazenamentoError

__all__ = ["Armazenamento", "ArmazenamentoError", "chave_entrada", "chave_saida"]


def chave_entrada(cliente: str, lote: str, nome: str) -> str:
    return f"entrada/{cliente}/{lote}/{nome}"


def chave_saida(cliente: str, lote: str, nome: str) -> str:
    return f"saida/{cliente}/{lote}/{nome}"
