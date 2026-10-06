#!/usr/bin/env bash
# Regenera worker/requirements.lock (com hashes) a partir de worker/requirements.in.
#
# As versões já fixadas no lock atual entram como restrição, para que acrescentar uma
# dependência não atualize sem querer o que já foi testado em GPU. Para atualizar tudo de
# propósito: UPGRADE=1 scripts/lock-requirements.sh
set -euo pipefail
cd "$(dirname "$0")/.."
constraints=()
if [[ "${UPGRADE:-0}" != "1" && -f requirements.lock ]]; then
  tmp=lock-constraints.txt  # nome fixo: aparece nos comentários do lock
  trap 'rm -f "$tmp"' EXIT
  grep -E '^[A-Za-z0-9_.-]+==' requirements.lock | sed 's/ \\$//' > "$tmp"
  constraints=(-c "$tmp")
fi
uv pip compile requirements.in "${constraints[@]}" \
  --python-version 3.12 \
  --python-platform x86_64-manylinux_2_28 \
  --index-url https://pypi.org/simple \
  --extra-index-url https://download.pytorch.org/whl/cu128 \
  --index-strategy unsafe-best-match \
  --generate-hashes \
  --no-header \
  -o requirements.lock
