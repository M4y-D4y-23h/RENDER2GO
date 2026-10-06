#!/usr/bin/env python3
"""Geração de vídeos em lote a partir de pedidos.csv (modo serviço). Ver --help."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker" / "src"))

from gpu2go_worker.lote import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
