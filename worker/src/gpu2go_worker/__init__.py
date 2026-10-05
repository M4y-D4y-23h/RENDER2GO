"""Worker do GPU2GO."""

import os
from pathlib import Path

WORKER_DIR = Path(os.environ.get("GPU2GO_WORKER_DIR", Path(__file__).resolve().parents[2]))
"""Diretório `worker/` (no repositório) ou `/opt/gpu2go/worker` (na imagem)."""
