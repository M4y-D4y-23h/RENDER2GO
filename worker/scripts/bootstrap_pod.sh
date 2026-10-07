#!/usr/bin/env bash
# Monta o worker num pod efêmero a partir de python:3.12-slim (lote_nuvem, ADR 0003 fase 2):
# mesmas versões e hashes do worker/Dockerfile (lidos de lá), modelos do models.lock.json
# baixados em paralelo e conferidos por bytes e SHA-256. Enquanto a imagem com os modelos
# não existe (ADR 0002), isto leva ~6–10 min por pod.
#
# Uso: bootstrap_pod.sh <repo.tgz> <conjuntos de modelos, ex.: i2v_r1v2>
# Estado em /root/gpu2go/estado: INSTALANDO → PRONTO | FALHOU <motivo>
#
# Com GPU2GO_VOLUME=1 (volume de rede do RunPod em /workspace), os modelos e o modelo do
# compilador ficam em /workspace/gpu2go-cache: o 1º pod baixa e guarda; os seguintes copiam do
# volume para o disco local (regra: nunca ler modelo direto do volume) e conferem o SHA-256.
# Volume não POSIX: só rsync --inplace, sem rename nem chmod.
set -uo pipefail
REPO_TGZ=$1 MODEL_SET=${2:-i2v_r1v2}
EST=/root/gpu2go/estado
mkdir -p /root/gpu2go && echo INSTALANDO > $EST
falha() { echo "FALHOU $*" > $EST; echo "FALHOU: $*"; exit 1; }
export DEBIAN_FRONTEND=noninteractive
W=/opt/gpu2go/worker COMFY_DIR=/opt/comfyui OLLAMA_MODELS=/opt/ollama/models
export OLLAMA_MODELS

apt-get update -qq && apt-get install -y -qq --no-install-recommends \
  git curl ffmpeg zstd unzip ca-certificates procps rsync >/dev/null || falha "apt"
CACHE=""
[[ "${GPU2GO_VOLUME:-0}" == "1" && -d /workspace ]] && CACHE=/workspace/gpu2go-cache && mkdir -p $CACHE/modelos $CACHE/ollama
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
  if [[ -n "$CACHE" ]]; then  # do volume para o disco local, quando o tamanho confere
    while IFS=$'\t' read -r _ dest bytes _; do
      c="$CACHE/modelos/$(basename "$dest")"
      if [[ -f "$c" && "$(stat -c %s "$c")" == "$bytes" ]]; then
        mkdir -p "$(dirname "$dest")" && rsync --inplace "$c" "$dest" && echo "do volume: $(basename "$dest")"
      fi
    done < /root/gpu2go/modelos.tsv
  fi
  # baixa só o que faltar ou estiver incompleto
  while IFS=$'\t' read -r url dest bytes _; do
    [[ -f "$dest" && "$(stat -c %s "$dest")" == "$bytes" ]] || printf '%s\t%s\n' "$url" "$dest"
  done < /root/gpu2go/modelos.tsv | xargs -P 4 -L 1 bash -c 'um "$0" "$1"' || return 1
  while IFS=$'\t' read -r _ dest bytes sha; do
    [ "$(stat -c %s "$dest")" = "$bytes" ] || { echo "bytes errados: $dest"; return 1; }
    echo "$sha  $dest" | sha256sum -c --quiet - || return 1
    if [[ -n "$CACHE" ]]; then  # guarda no volume para os próximos pods (bytes conferidos)
      c="$CACHE/modelos/$(basename "$dest")"
      [[ -f "$c" && "$(stat -c %s "$c")" == "$bytes" ]] || rsync --inplace "$dest" "$c"
      [ "$(stat -c %s "$c")" = "$bytes" ] || echo "AVISO: cópia no volume incompleta: $c"
    fi
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
  [[ -n "$CACHE" ]] && rsync -r --inplace $CACHE/ollama/ $OLLAMA_MODELS/  # o pull só confere
  bash $W/scripts/pull_compiler_model.sh "$(arg COMPILER_MODEL)" "$(arg COMPILER_MODEL_DIGEST)" || return 1
  [[ -n "$CACHE" ]] && rsync -r --inplace $OLLAMA_MODELS/ $CACHE/ollama/
  /opt/venv/bin/python $W/scripts/gpu_check.py
}

modelos > /root/gpu2go/modelos.log 2>&1 &
MPID=$!
ambiente > /root/gpu2go/ambiente.log 2>&1 || { kill $MPID 2>/dev/null; falha "ambiente (ver ambiente.log)"; }
wait $MPID || falha "modelos (ver modelos.log)"
echo PRONTO > $EST
echo PRONTO
