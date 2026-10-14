"""Armazenamento do serviço (Cloudflare R2). O cliente S3 fica em `render2go_worker.armazenamento`,
compartilhado com o `lote_nuvem` (espelho dos modelos).

Chaves dos objetos (o prefixo decide a retenção: as regras de ciclo de vida do R2 filtram só
pelo começo da chave):
    entrada/<cliente>/<lote>/<foto>.<ext>             (apagadas em 7 dias, regra no bucket)
    saida/<cliente>/<lote>/<video>.mp4 e conferencia/ (apagadas em 30 dias)
    modelos/<sha256>                                  (espelho dos modelos; nunca apagar)
"""

from __future__ import annotations

from render2go_worker.armazenamento import Armazenamento, ArmazenamentoError

__all__ = ["Armazenamento", "ArmazenamentoError", "chave_entrada", "chave_saida", "chave_tentativa"]


def chave_entrada(cliente: str, lote: str, nome: str) -> str:
    return f"entrada/{cliente}/{lote}/{nome}"


def chave_saida(cliente: str, lote: str, nome: str) -> str:
    return f"saida/{cliente}/{lote}/{nome}"


def chave_tentativa(cliente: str, lote: str, job: str, tentativa: str, extensao: str) -> str:
    """Onde o pod envia o vídeo (e a folha) de uma tentativa (14/10/2026): um objeto por tentativa, que só a API
    publica no endereço do cliente (`chave_saida`) depois de conferir a conclusão. Um pod antigo, com o prazo vencido,
    envia para o objeto da tentativa dele, que nunca é publicado: o vídeo entregue não é sobrescrito. Fica sob
    `saida/` (a mesma regra de 30 dias do bucket); a publicação apaga o objeto da tentativa."""
    return f"saida/{cliente}/{lote}/tentativas/{job}-{tentativa}{extensao}"


def chave_quarentena(cliente: str, lote: str, nome: str) -> str:
    """Vídeo barrado pelo filtro de imagem e a folha dele: só o dono vê, pelo painel (ADR 0005), por 7 dias."""
    return f"quarentena/{cliente}/{lote}/{nome}"
