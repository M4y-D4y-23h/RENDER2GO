#!/usr/bin/env python3
"""Lote na nuvem com um comando: sobe os pods, gera, traz os vídeos e encerra. Ver --help."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker" / "src"))

from gpu2go_worker.lote_nuvem import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
