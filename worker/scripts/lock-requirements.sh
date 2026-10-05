#!/usr/bin/env bash
# Regenera worker/requirements.lock (com hashes) a partir de worker/requirements.in.
set -euo pipefail
cd "$(dirname "$0")/.."
uv pip compile requirements.in \
  --python-version 3.12 \
  --python-platform x86_64-manylinux_2_28 \
  --index-url https://pypi.org/simple \
  --extra-index-url https://download.pytorch.org/whl/cu128 \
  --index-strategy unsafe-best-match \
  --generate-hashes \
  --no-header \
  -o requirements.lock
