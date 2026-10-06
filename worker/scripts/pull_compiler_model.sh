#!/usr/bin/env bash
# Baixa o modelo do compilador na build e confere o digest da camada de pesos.
# Uso: pull_compiler_model.sh <modelo> <digest sha256:...>   (WITH_COMPILER_MODEL=0 pula)
set -euo pipefail
model=$1 digest=$2
[[ "${WITH_COMPILER_MODEL:-1}" == "1" ]] || { echo "compilador fora da imagem (WITH_COMPILER_MODEL=0)"; exit 0; }
ollama serve > /tmp/ollama.log 2>&1 &
opid=$!
for _ in $(seq 1 60); do curl -fsS -o /dev/null http://127.0.0.1:11434/api/version && break; sleep 1; done
ollama pull "$model"
test -f "$OLLAMA_MODELS/blobs/$(echo "$digest" | tr : -)" || { echo "digest não confere: $digest"; exit 1; }
kill "$opid"
wait "$opid" || true
rm -f /tmp/ollama.log
