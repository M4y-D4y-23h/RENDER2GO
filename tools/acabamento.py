#!/usr/bin/env python3
"""Acabamento: junta clipes, logo, cartão final e música. Ver --help."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker" / "src"))

from render2go_worker.acabamento import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
