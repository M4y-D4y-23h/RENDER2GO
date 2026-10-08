#!/usr/bin/env bash
# Monta o worker num pod efêmero a partir de python:3.12-slim (lote_nuvem, ADR 0003 fase 2):
# mesmas versões e hashes do worker/Dockerfile (lidos de lá), modelos do models.lock.json
# baixados em partes (aria2: 8 conexões por arquivo, 4 arquivos por vez) e conferidos por bytes e
# SHA-256 em paralelo. Medido em 07/10/2026 (US-NC-2): 91 s sem cache (era 5–6 min com uma conexão
# por arquivo) e 77 s com o cache do volume.
#
# Uso: bootstrap_pod.sh <repo.tgz> <conjuntos de modelos, ex.: i2v_r1v2>
# Estado em /root/render2go/estado: INSTALANDO → AMBIENTE → PRONTO | FALHOU <motivo>
#   AMBIENTE: venv, ComfyUI, Ollama e o modelo do compilador prontos; os modelos de vídeo ainda
#   baixam. Os serviços já podem subir e o pedido já pode ser traduzido; renderizar, só no PRONTO.
# Tempos por etapa (s desde o início) em /root/render2go/tempos.
# Os pacotes que o download usa (aria2, curl, rsync) vêm primeiro; os outros (git, ffmpeg...)
# instalam enquanto os modelos baixam (08/10/2026: o apt inteiro levava 14–23 s antes do download).
# Com /root/render2go/espelho.tsv (<arquivo>\t<URL assinada>, do lote_nuvem), cada modelo também vem
# do espelho no R2, ao mesmo tempo que da origem (reserva se o Hugging Face cair). Com
# /root/render2go/so_espelho (o teste de rede achou a origem lenta), só do espelho.
# Com /root/render2go/ambiente.tsv (<URL assinada>\t<sha256>\t<bytes>), o ambiente (venv, ComfyUI
# e Ollama) vem pronto do espelho, num tar.zst conferido pelo SHA-256; se falhar, é instalado da
# origem. Com RENDER2GO_AMBIENTE_GRAVA=1, o ambiente instalado é empacotado para o espelho
# (ambiente.tar.zst e .sha256 em /root/render2go; o lote_nuvem envia depois do PRONTO).
#
# Com RENDER2GO_VOLUME=1 (volume de rede do RunPod em /workspace), o cache fica em
# /workspace/render2go-cache:
#   modelos/   arquivos do models.lock (SHA-256 do lock)
#   ollama/    modelo do compilador (blobs com o SHA-256 no nome)
#   ambiente/  venv + ComfyUI + Ollama num tar.zst, chaveado pelo requirements.lock e Dockerfile,
#              com um .sha256 escrito por último (sem ele, o arquivo é ignorado)
# Tudo é copiado para o disco local e conferido antes do uso (regra: nunca ler modelo direto do
# volume); cópia ruim é apagada e refeita da origem. Só o pod com RENDER2GO_VOLUME_GRAVA=1 grava no
# volume, e só depois do PRONTO (não atrasa o pod); ao terminar, escreve /root/render2go/guardado.
# Volume não POSIX: só rsync --inplace e cp, sem rename nem chmod.
set -uo pipefail
REPO_TGZ=$1 MODEL_SET=${2:-i2v_r1v2}
R=/root/render2go EST=/root/render2go/estado
mkdir -p $R/ok $R/vol && echo INSTALANDO > $EST && : > $R/tempos && rm -f $R/guardado
T0=$(date +%s)
marca() { echo "$1 $(( $(date +%s) - T0 ))" >> $R/tempos; }
falha() { echo "FALHOU $*" > $EST; echo "FALHOU: $*"; echo GUARDADO > $R/guardado; exit 1; }
export DEBIAN_FRONTEND=noninteractive
W=/opt/render2go/worker COMFY_DIR=/opt/comfyui OLLAMA_MODELS=/opt/ollama/models
export OLLAMA_MODELS R

apt-get update -qq && apt-get install -y -qq --no-install-recommends \
  curl ca-certificates rsync aria2 >/dev/null || falha "apt"
marca apt_download
CACHE="" GRAVA=0
if [[ "${RENDER2GO_VOLUME:-0}" == "1" && -d /workspace ]]; then
  CACHE=/workspace/render2go-cache
  mkdir -p $CACHE/modelos $CACHE/ollama $CACHE/ambiente
  [[ "${RENDER2GO_VOLUME_GRAVA:-0}" == "1" ]] && GRAVA=1
fi
export CACHE
mkdir -p /opt/render2go && tar -xzf "$REPO_TGZ" -C /opt/render2go || falha "repo"
arg() { sed -n "s/^ARG $1=//p" $W/Dockerfile | head -1; }
CHAVE=$(cat $W/requirements.lock $W/Dockerfile | sha256sum | cut -c1-16)
AMB=${CACHE:-/nao-ha}/ambiente/ambiente-$CHAVE.tar.zst

# confere bytes e SHA-256; marca em $R/ok. Uso: confere <url> <destino> <bytes> <sha>
confere() {
  [ "$(stat -c %s "$2" 2>/dev/null)" = "$3" ] && echo "$4  $2" | sha256sum -c --quiet - \
    && touch "$R/ok/$(basename "$2")"
}
# do volume para o disco local; cópia que não confere é apagada (será baixada)
copia() {
  local c="$CACHE/modelos/$(basename "$2")"
  [[ -f "$c" && "$(stat -c %s "$c")" == "$3" ]] || return 0
  mkdir -p "$(dirname "$2")"
  if rsync --inplace "$c" "$2" && confere "$@"; then
    touch "$R/vol/$(basename "$2")" && echo "do volume: $(basename "$2")"
  else
    echo "cópia do volume não confere, baixando de novo: $(basename "$2")" && rm -f "$2"
  fi
}
espelho() {  # URL assinada do espelho (R2) para o arquivo, se o lote_nuvem mandou uma
  [[ -f $R/espelho.tsv ]] && awk -F'\t' -v n="$(basename "$1")" '$1 == n {print $2}' $R/espelho.tsv
}
um() {  # reserva: curl numa conexão só, com retomada; da origem e, se falhar, do espelho
  mkdir -p "$(dirname "$2")"
  local u m; m=$(espelho "$2")
  local ordem=("$1" "$1" "$1" ${m:+"$m" "$m"})
  [[ -n "$m" && -f $R/so_espelho ]] && ordem=("$m" "$m" "$1" "$1")  # origem lenta: espelho antes
  for u in "${ordem[@]}"; do
    curl -fsSL -C - --retry 5 -o "$2" "$u" && return 0; sleep 5
  done
  return 1
}
# depois do download em partes: se bytes ou SHA-256 não conferem (ex.: espelho ruim), apaga e
# baixa de novo numa conexão só, antes de desistir
reconfere() {
  confere "$@" && return 0
  echo "bytes ou SHA-256 errados: $(basename "$2"); baixando de novo"
  rm -f "$2" && um "$1" "$2" && confere "$@" || { echo "bytes ou SHA-256 errados: $1"; return 1; }
}
export -f confere copia espelho um reconfere

modelos() {
  python3 - "$MODEL_SET" > $R/modelos.tsv <<'PY' || return 1
import json, sys
lock = json.load(open("/opt/render2go/worker/models.lock.json"))
nomes = sorted({m for s in sys.argv[1].split(",") for m in lock["sets"][s]})
for n in nomes:
    m = lock["models"][n]
    url = f"https://huggingface.co/{m['repo']}/resolve/{m['revision']}/{m['path']}"
    print(url, f"/opt/comfyui/models/{m['dest']}", m["bytes"], m["sha256"], sep="\t")
PY
  [[ -n "$CACHE" ]] && xargs -r -P 4 -L 1 bash -c 'copia "$@"' _ < $R/modelos.tsv
  while IFS=$'\t' read -r url dest bytes sha; do
    [[ -f "$R/ok/$(basename "$dest")" ]] || printf '%s\t%s\t%s\t%s\n' "$url" "$dest" "$bytes" "$sha"
  done < $R/modelos.tsv > $R/falta.tsv
  marca modelos_volume
  [[ -s $R/falta.tsv ]] || return 0
  # em partes: os 2 arquivos de ~15 GB dominam; numa conexão só, a média caía para ~140 MB/s
  while IFS=$'\t' read -r url dest _ _; do
    mkdir -p "$(dirname "$dest")"
    m=$(espelho "$dest")  # com espelho, o aria2 baixa das duas origens ao mesmo tempo
    if [[ -n "$m" && -f $R/so_espelho ]]; then  # origem lenta neste host (teste de rede): só o espelho
      printf '%s\n  dir=%s\n  out=%s\n' "$m" "$(dirname "$dest")" "$(basename "$dest")"
    else
      printf '%s%s\n  dir=%s\n  out=%s\n' "$url" "${m:+$'\t'$m}" "$(dirname "$dest")" "$(basename "$dest")"
    fi
  done < $R/falta.tsv > $R/aria2.txt
  aria2c ${SSL_CERT_FILE:+--ca-certificate=$SSL_CERT_FILE} -i $R/aria2.txt -j 4 -x 8 -s 8 -k 64M --file-allocation=none --continue=true \
    --allow-overwrite=true --auto-file-renaming=false --max-tries=10 --retry-wait=5 \
    --console-log-level=warn --summary-interval=30 \
    || echo "aria2 falhou; completando com curl"
  while IFS=$'\t' read -r url dest bytes _; do
    [ "$(stat -c %s "$dest" 2>/dev/null)" = "$bytes" ] || printf '%s\t%s\n' "$url" "$dest"
  done < $R/falta.tsv | xargs -r -P 4 -L 1 bash -c 'um "$0" "$1"' || return 1
  marca modelos_download
  xargs -r -P 4 -L 1 bash -c 'reconfere "$@" || exit 255' _ < $R/falta.tsv || return 1
  marca modelos_conferidos
}

instalar_ambiente() {
  rm -rf /opt/venv
  pip install -q "uv==$(arg UV_IMAGE | sed 's/.*://')" || return 1
  uv venv -q /opt/venv --python python3.12 || return 1
  uv pip install -q --require-hashes -r $W/requirements.lock \
    --index-url https://pypi.org/simple --extra-index-url https://download.pytorch.org/whl/cu128 \
    --index-strategy unsafe-best-match || return 1
  rm -rf /opt/comfy-src
  git clone -q --depth 1 --branch "$(arg COMFYUI_REF)" https://github.com/comfyanonymous/ComfyUI.git /opt/comfy-src || return 1
  [ "$(git -C /opt/comfy-src rev-parse HEAD)" = "$(arg COMFYUI_COMMIT)" ] || { echo "commit do ComfyUI"; return 1; }
  mkdir -p $COMFY_DIR && cp -a /opt/comfy-src/. $COMFY_DIR/ && rm -rf /opt/comfy-src
  curl -fsSL -o /tmp/umt5.zip "$(arg UMT5_PATCH_URL)" || return 1
  echo "$(arg UMT5_PATCH_SHA256)  /tmp/umt5.zip" | sha256sum -c --quiet - || return 1
  unzip -qo /tmp/umt5.zip -d $COMFY_DIR/custom_nodes/
  local v; v=$(arg OLLAMA_VERSION)
  curl -fsSL -o /tmp/ollama.tar.zst "https://github.com/ollama/ollama/releases/download/$v/ollama-linux-amd64.tar.zst" || return 1
  echo "$(arg OLLAMA_SHA256)  /tmp/ollama.tar.zst" | sha256sum -c --quiet - || return 1
  tar --zstd -xf /tmp/ollama.tar.zst -C /usr/local || return 1
  rm -f /tmp/ollama.tar.zst /tmp/umt5.zip
}

# ambiente do volume: copia, confere o SHA-256 do .sha256 (escrito por último) e extrai
ambiente_do_volume() {
  [[ -n "$CACHE" && -f "$AMB" && -f "$AMB.sha256" ]] || return 1
  local sha bytes
  read -r sha bytes < "$AMB.sha256" || return 1
  [ "$(stat -c %s "$AMB")" = "$bytes" ] || return 1
  rsync --inplace "$AMB" $R/ambiente.tar.zst || return 1
  echo "$sha  $R/ambiente.tar.zst" | sha256sum -c --quiet - \
    || { echo "ambiente do volume não confere"; rm -f $R/ambiente.tar.zst; return 1; }
  tar --zstd -xf $R/ambiente.tar.zst -C / || return 1
  rm -f $R/ambiente.tar.zst
  touch $R/vol/ambiente && echo "ambiente do volume ($CHAVE)"
}

# ambiente do espelho (R2): $R/ambiente.tsv tem <URL assinada>\t<sha256>\t<bytes> (lote_nuvem).
# Baixa em partes, confere bytes e SHA-256, extrai e confere que o venv e o Ollama funcionam.
# Qualquer falha apaga o arquivo e o venv, e o ambiente é instalado da origem (como antes).
ambiente_do_espelho() {
  [[ -s $R/ambiente.tsv ]] || return 1
  local url sha bytes
  IFS=$'\t' read -r url sha bytes < $R/ambiente.tsv || return 1
  [[ -n "$url" && ${#sha} == 64 && "$bytes" =~ ^[0-9]+$ ]] || return 1
  rm -f $R/ambiente.tar.zst
  if aria2c ${SSL_CERT_FILE:+--ca-certificate=$SSL_CERT_FILE} -x 8 -s 8 -k 64M --file-allocation=none \
      --max-tries=5 --retry-wait=3 --console-log-level=warn --summary-interval=0 \
      -d $R -o ambiente.tar.zst "$url" \
    && [ "$(stat -c %s $R/ambiente.tar.zst 2>/dev/null)" = "$bytes" ] \
    && echo "$sha  $R/ambiente.tar.zst" | sha256sum -c --quiet - \
    && tar --zstd -xf $R/ambiente.tar.zst -C / \
    && /opt/venv/bin/python -c "import torch" \
    && [[ -f $COMFY_DIR/main.py && -x /usr/local/bin/ollama ]]; then
    rm -f $R/ambiente.tar.zst
    touch $R/vol/ambiente && echo "ambiente do espelho ($CHAVE)"
    return 0
  fi
  echo "ambiente do espelho não serviu; instalando da origem"
  rm -rf $R/ambiente.tar.zst /opt/venv
  return 1
}

ambiente() {
  export VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH UV_HTTP_TIMEOUT=300
  if ambiente_do_volume; then
    marca ambiente_volume
  elif ambiente_do_espelho; then
    marca ambiente_espelho
  else
    instalar_ambiente || return 1
    marca ambiente_instalado
    # empacota agora, antes de qualquer serviço escrever no ComfyUI: para o volume ou para o espelho
    if [[ $GRAVA == 1 || "${RENDER2GO_AMBIENTE_GRAVA:-0}" == "1" ]]; then
      tar -C / -cf - --exclude='opt/comfyui/models/*/*' --exclude='opt/comfyui/output' \
        --exclude='opt/comfyui/input' --exclude='opt/comfyui/temp' --exclude='opt/comfyui/user' \
        opt/venv opt/comfyui usr/local/bin/ollama usr/local/lib/ollama \
        | zstd -q -f -3 -T0 -o $R/ambiente.tar.zst \
        && echo "$(sha256sum < $R/ambiente.tar.zst | cut -d' ' -f1) $(stat -c %s $R/ambiente.tar.zst)" \
          > $R/ambiente.tar.zst.sha256 \
        || { echo "AVISO: não empacotou o ambiente"; rm -f $R/ambiente.tar.zst*; }
      marca ambiente_empacotado
    fi
  fi
  mkdir -p $OLLAMA_MODELS
  if [[ -n "$CACHE" ]]; then  # blobs do volume, conferidos pelo SHA-256 do nome; ruim é apagado
    rsync -r --inplace --size-only $CACHE/ollama/ $OLLAMA_MODELS/
    find $OLLAMA_MODELS/blobs -name 'sha256-*' 2>/dev/null | xargs -r -P 4 -I{} bash -c \
      '[ "$(sha256sum < "$1" | cut -d" " -f1)" = "$(basename "$1" | cut -c8-)" ] || { echo "blob ruim: $1"; rm -f "$1"; }' _ {}
  fi
  bash $W/scripts/pull_compiler_model.sh "$(arg COMPILER_MODEL)" "$(arg COMPILER_MODEL_DIGEST)" || return 1
  marca compilador
  /opt/venv/bin/python $W/scripts/gpu_check.py
}

# depois do PRONTO, no pod gravador: o que falta no volume (tudo já conferido no disco local)
guardar() {
  [[ -n "$CACHE" && $GRAVA == 1 ]] || return 0
  while IFS=$'\t' read -r _ dest bytes _; do
    [[ -f "$R/vol/$(basename "$dest")" ]] || printf '%s\t%s\n' "$dest" "$bytes"
  done < $R/modelos.tsv | xargs -r -P 4 -L 1 bash -c \
    'c="$CACHE/modelos/$(basename "$0")"; rsync --inplace "$0" "$c" && [ "$(stat -c %s "$c")" = "$1" ] && echo "guardado: $c" || echo "AVISO: cópia no volume incompleta: $c"'
  rsync -r --inplace --size-only $OLLAMA_MODELS/ $CACHE/ollama/
  if [[ -f $R/ambiente.tar.zst.sha256 ]]; then
    rm -f "$AMB.sha256"
    read -r _ bytes < $R/ambiente.tar.zst.sha256
    if rsync --inplace $R/ambiente.tar.zst "$AMB" && [ "$(stat -c %s "$AMB")" = "$bytes" ]; then
      cp $R/ambiente.tar.zst.sha256 "$AMB.sha256" && echo "guardado: $AMB"
      find $CACHE/ambiente -maxdepth 1 -name 'ambiente-*' ! -name "ambiente-$CHAVE*" -delete
    else
      echo "AVISO: ambiente no volume incompleto"
    fi
  fi
}

modelos > $R/modelos.log 2>&1 &
MPID=$!
apt-get install -y -qq --no-install-recommends git ffmpeg zstd unzip procps >/dev/null \
  || { kill $MPID 2>/dev/null; falha "apt"; }
marca apt
ambiente > $R/ambiente.log 2>&1 || { kill $MPID 2>/dev/null; falha "ambiente (ver ambiente.log)"; }
marca ambiente_pronto
echo AMBIENTE > $EST
wait $MPID || falha "modelos (ver modelos.log)"
marca pronto
echo PRONTO > $EST
echo PRONTO
guardar > $R/guardar.log 2>&1
marca guardado
echo GUARDADO > $R/guardado
