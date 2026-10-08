#!/usr/bin/env python3
"""Envia um workflow (formato API) ao ComfyUI e baixa o MP4. Ver --help."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker" / "src"))

from render2go_worker.cli_submit import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
