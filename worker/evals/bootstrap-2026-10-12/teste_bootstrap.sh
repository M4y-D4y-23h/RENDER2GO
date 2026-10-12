#!/usr/bin/env bash
# Teste do bootstrap_pod.sh em contêiner (sem GPU nem RunPod), na imagem dos pods:
#   bash worker/evals/bootstrap-2026-10-12/teste_bootstrap.sh
# Precisa de Docker. Gera ~2,2 GB de "modelos" aleatórios numa pasta temporária (apagada no fim). Atrás do
# proxy do ambiente de nuvem, o contêiner usa a rede do host e a CA do proxy (/root/.ccr/ca-bundle.crt).
set -euo pipefail
AQUI=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$AQUI/../../.." && pwd)
IMG=${IMG:-public.ecr.aws/docker/library/python:3.12-slim-bookworm}
CA=${CA:-/root/.ccr/ca-bundle.crt}
CTX=$(mktemp -d)
trap 'rm -rf "$CTX"' EXIT
cp "$REPO/worker/scripts/bootstrap_pod.sh" "$AQUI/servidor.py" "$AQUI/no_conteiner.sh" "$CTX/"
tar -czf "$CTX/repo.tgz" -C "$REPO" worker/models.lock.json worker/scripts
mkdir "$CTX/arquivos"
# 2,2 GB: abaixo de 2 GB a baixar, o bootstrap não mede a rede (pouco dado para julgar)
head -c 1000M /dev/urandom > "$CTX/arquivos/m1.safetensors"
head -c 1000M /dev/urandom > "$CTX/arquivos/m2.safetensors"
head -c 200M /dev/urandom > "$CTX/arquivos/m3.safetensors"
head -c 2000 /dev/urandom > "$CTX/arquivos/m4.json"
args=(--rm --network host -e https_proxy -e HTTPS_PROXY -e no_proxy -e NO_PROXY -v "$CTX:/ctx")
[[ -f $CA ]] && args+=(-v "$CA:/etc/ssl/certs/ca-certificates.crt:ro")
rc=0
for parte in ${PARTES:-downloads volta}; do
  echo "== $parte"
  docker run "${args[@]}" "$IMG" bash /ctx/no_conteiner.sh "$parte" || rc=1
done
exit $rc
