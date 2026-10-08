"""Modelos de geração que o serviço oferece (a página mostra; o pedido guarda qual foi usado).

Hoje só o Wan 2.2 (imagem para vídeo). Para acrescentar um modelo:
    1. template e contrato em worker/workflows/ e modelos no models.lock.json (ADR);
    2. o worker passa a aceitar o id em `gpu2go_worker.fila.MODELOS`;
    3. entra aqui com `disponivel: True`.
O worker recusa (sem repetir) pedido de modelo que ele não conhece: fail-closed.
"""

from __future__ import annotations

PADRAO = "wan2.2-i2v"
MODELOS = [
    {
        "id": "wan2.2-i2v",
        "nome": "Wan 2.2",
        "descricao": "imagem para vídeo · 14B",
        "disponivel": True,
    },
]


def disponiveis() -> list[dict]:
    return [m for m in MODELOS if m["disponivel"]]


def nome(modelo_id: str | None) -> str:
    m = next((m for m in MODELOS if m["id"] == (modelo_id or PADRAO)), None)
    return m["nome"] if m else (modelo_id or PADRAO)
