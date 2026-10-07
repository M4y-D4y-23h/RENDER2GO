#!/usr/bin/env bash
# Monta o worker num pod efêmero a partir de python:3.12-slim (lote_nuvem, ADR 0003 fase 2):
# mesmas versões e hashes do worker/Dockerfile (lidos de lá), modelos do models.lock.json
# baixados em paralelo e conferidos por bytes e SHA-256. Enquanto a imagem com os modelos
# não existe (ADR 0002), isto leva ~6–10 min por pod.
#
# Uso: bootstrap_pod.sh <repo.tgz> <conjuntos de modelos, ex.: i2v_r1v2>
# Estado em /root/gpu2go/estado: INSTALANDO → PRONTO | FALHOU <motivo>
set -uo pipefail
REPO_TGZ=$1 MODEL_SET=${2:-i2v_r1v2}
EST=/root/gpu2go/estado
mkdir -p /root/gpu2go && echo INSTALANDO > $EST
falha() { echo "FALHOU $*" > $EST; echo "FALHOU: $*"; exit 1; }
export DEBIAN_FRONTEND=noninteractive
W=/opt/gpu2go/worker COMFY_DIR=/opt/comfyui OLLAMA_MODELS=/opt/ollama/models
export OLLAMA_MODELS

apt-get update -qq && apt-get install -y -qq --no-install-recommends \
  git curl ffmpeg zstd unzip ca-certificates procps >/dev/null || falha "apt"
mkdir -p /opt/gpu2go && tar -xzf "$REPO_TGZ" -C /opt/gpu2go || falha "repo"
arg() { sed -n "s/^ARG $1=//p" $W/Dockerfile | head -1; }

# modelos: lista do lock, 4 downloads em paralelo, com retomada; depois bytes + SHA-256
modelos() {
  python3 - "$MODEL_SET" > /root/gpu2go/modelos.tsv <<'PY' || return 1
import json, sys
lock = json.load(open("/opt/gpu2go/worker/models.lock.json"))
nomes = sorted({m for s in sys.argv[1].split(",") for m in lock["sets"][s]})
for n in nomes:
    m = lock["models"][n]
    url = f"https://huggingface.co/{m['repo']}/resolve/{m['revision']}/{m['path']}"
    print(url, f"/opt/comfyui/models/{m['dest']}", m["bytes"], m["sha256"], sep="\t")
PY
  um() {
    mkdir -p "$(dirname "$2")"
    for _ in 1 2 3 4 5; do curl -fsSL -C - --retry 5 -o "$2" "$1" && return 0; sleep 5; done
    return 1
  }
  export -f um
  cut -f1,2 /root/gpu2go/modelos.tsv | xargs -P 4 -L 1 bash -c 'um "$0" "$1"' || return 1
  while IFS=$'\t' read -r _ dest bytes sha; do
    [ "$(stat -c %s "$dest")" = "$bytes" ] || { echo "bytes errados: $dest"; return 1; }
    echo "$sha  $dest" | sha256sum -c --quiet - || return 1
  done < /root/gpu2go/modelos.tsv
}

ambiente() {
  pip install -q "uv==$(arg UV_IMAGE | sed 's/.*://')" || return 1
  export VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH UV_HTTP_TIMEOUT=300
  uv venv -q /opt/venv --python python3.12 || return 1
  uv pip install -q --require-hashes -r $W/requirements.lock \
    --index-url https://pypi.org/simple --extra-index-url https://download.pytorch.org/whl/cu128 \
    --index-strategy unsafe-best-match || return 1
  git clone -q --depth 1 --branch "$(arg COMFYUI_REF)" https://github.com/comfyanonymous/ComfyUI.git /opt/comfy-src || return 1
  [ "$(git -C /opt/comfy-src rev-parse HEAD)" = "$(arg COMFYUI_COMMIT)" ] || { echo "commit do ComfyUI"; return 1; }
  mkdir -p $COMFY_DIR && cp -a /opt/comfy-src/. $COMFY_DIR/ && rm -rf /opt/comfy-src
  curl -fsSL -o /tmp/umt5.zip "$(arg UMT5_PATCH_URL)" || return 1
  echo "$(arg UMT5_PATCH_SHA256)  /tmp/umt5.zip" | sha256sum -c --quiet - || return 1
  unzip -qo /tmp/umt5.zip -d $COMFY_DIR/custom_nodes/
  v=$(arg OLLAMA_VERSION)
  curl -fsSL -o /tmp/ollama.tar.zst "https://github.com/ollama/ollama/releases/download/$v/ollama-linux-amd64.tar.zst" || return 1
  echo "$(arg OLLAMA_SHA256)  /tmp/ollama.tar.zst" | sha256sum -c --quiet - || return 1
  tar --zstd -xf /tmp/ollama.tar.zst -C /usr/local || return 1
  mkdir -p $OLLAMA_MODELS
  bash $W/scripts/pull_compiler_model.sh "$(arg COMPILER_MODEL)" "$(arg COMPILER_MODEL_DIGEST)" || return 1
  /opt/venv/bin/python $W/scripts/gpu_check.py
}

modelos > /root/gpu2go/modelos.log 2>&1 &
MPID=$!
ambiente > /root/gpu2go/ambiente.log 2>&1 || { kill $MPID 2>/dev/null; falha "ambiente (ver ambiente.log)"; }
wait $MPID || falha "modelos (ver modelos.log)"
echo PRONTO > $EST
echo PRONTO
