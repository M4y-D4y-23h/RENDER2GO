#!/usr/bin/env python3
"""Benchmark de um endpoint serverless (cold start, p50/p95, custo). Ver --help."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker" / "src"))

from gpu2go_worker.bench import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
