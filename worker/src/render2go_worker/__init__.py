"""Worker do Render2Go."""

import os
from pathlib import Path

# Compatibilidade com o nome antigo do projeto: variáveis GPU2GO_* (num .env antigo ou nas
# configurações do ambiente) continuam valendo como RENDER2GO_*, sem sobrescrever as novas.
for _nome, _valor in list(os.environ.items()):
    if _nome.startswith("GPU2GO_"):
        os.environ.setdefault("RENDER2GO_" + _nome[len("GPU2GO_") :], _valor)

WORKER_DIR = Path(os.environ.get("RENDER2GO_WORKER_DIR", Path(__file__).resolve().parents[2]))
"""Diretório `worker/` (no repositório) ou `/opt/render2go/worker` (na imagem)."""
