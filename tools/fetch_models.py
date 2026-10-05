#!/usr/bin/env python3
"""Baixa ou copia os modelos do worker/models.lock.json conferindo bytes e SHA-256."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker" / "src"))

from gpu2go_worker.models import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
