#!/usr/bin/env bash
# Monta o worker num pod efêmero a partir de python:3.12-slim (lote_nuvem, ADR 0003 fase 2):
# mesmas versões e hashes do worker/Dockerfile (lidos de lá), modelos do models.lock.json
# baixados em partes (aria2: 16 conexões por arquivo, 4 arquivos por vez) e conferidos por bytes e
# SHA-256 em paralelo. Medido em 07/10/2026 (US-NC-2): 91 s sem cache (era 5–6 min com uma conexão
# por arquivo) e 77 s com o cache do volume.
# Desde 12/10/2026: cada modelo é conferido assim que o download dele termina (antes, todos esperavam
# o último), e o SHA-256 sai do OpenSSL do Python: na imagem python:3.12-slim-bookworm, o sha256sum
# do coreutils 9.1 não usa a instrução SHA da CPU e conferiu 2 GB em 9,2 s, contra 2,5 s no Python
# (medido em 12/10/2026). Os 10 s depois do início do download viram o teste de rede ($R/rede): o
# orquestrador descarta o host lento e lembra a velocidade de cada datacenter.
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
# /root/render2go/so_espelho (o espelho tem todos os modelos), só do espelho: em 08/10/2026 ele
# baixou os 37 GB em 40 s, com o Hugging Face a 25–39 MB/s no mesmo host. O curl de cada arquivo
# continua com a origem de reserva.
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
#
# Pod que hiberna (ADR 0011): com RENDER2GO_LOCAL (ex.: /workspace/r2g, o disco de volume do próprio
# pod, que fica no datacenter quando o pod é parado; em 11/10/2026, no US-NC-1, era MooseFS, de rede):
# - 1ª subida: tudo como sem volume (disco do contêiner, rápido); depois do PRONTO, em segundo plano,
#   venv, ComfyUI (com os modelos), Ollama e o modelo do compilador são copiados para $L, e
#   $L/instalado recebe a versão ($R/versao_hib, mandada pelo orquestrador). Só então GUARDADO: o
#   orquestrador espera isso para hibernar. Gravar e conferir os 37 GB direto no MooseFS levou ~10 min
#   em 11/10 (contra ~3 min no disco do contêiner).
# - volta, com a mesma versão: os bytes dos modelos e o ambiente são conferidos no próprio volume
#   antes de mexer em /opt (faltou algo: subida do zero, como a 1ª, sem link nenhum para o volume);
#   então /opt aponta para $L, as pastas de dados do ComfyUI (fotos e vídeos dos clientes) ficam no
#   contêiner, que a parada apaga; apt do cache do volume (sem rede) e PRONTO. Uma falha depois dos
#   links é FALHOU (até 12/10/2026, virava uma subida do zero por cima dos links, gravando no volume).
#   O SHA-256 de todos os modelos é refeito em segundo plano (e traz os arquivos para a memória antes
#   do 1º vídeo). Arquivo que não confere: FALHOU, o marcador sai e o pod é encerrado.
#
# Com BOOTSTRAP_SO_FUNCOES=1, o script só define as funções (os testes carregam com `source`).
set -uo pipefail
LAYOUT_HIB=2  # mude ao mudar onde as coisas ficam no disco de volume: os pods hibernados ficam velhos
REPO_TGZ=${1:-} MODEL_SET=${2:-i2v_r1v2}
R=${RENDER2GO_RAIZ:-/root/render2go}
EST=$R/estado
T0=$(date +%s)
marca() { echo "$1 $(( $(date +%s) - T0 ))" >> $R/tempos; }
falha() { echo "FALHOU $*" > $EST; echo "FALHOU: $*"; echo GUARDADO > $R/guardado; exit 1; }
sha256() {  # SHA-256 de um arquivo pelo OpenSSL do Python (3,7× o sha256sum desta imagem)
  python3 -c 'import hashlib, sys
with open(sys.argv[1], "rb") as f:
    print(hashlib.file_digest(f, "sha256").hexdigest())' "$1"
}
export DEBIAN_FRONTEND=noninteractive
W=/opt/render2go/worker COMFY_DIR=/opt/comfyui OLLAMA_MODELS=/opt/ollama/models
export OLLAMA_MODELS R
L=${RENDER2GO_LOCAL:-}
VERSAO=$(cat $R/versao_hib 2>/dev/null || true)
# sem fsync no dpkg: o disco do contêiner é descartável. Na volta, o apt do ffmpeg (~200 pacotes, ~20 s)
# roda em paralelo com a subida dos serviços (o AMBIENTE sai antes dele)
APT=(apt-get -o Dpkg::Options::=--force-unsafe-io)
if [[ -n "$L" ]]; then
  APT+=(-o "Dir::Cache::archives=$L/apt/archives" -o "Dir::State::lists=$L/apt/lists"
        -o APT::Keep-Downloaded-Packages=true)
fi

# /opt e /usr/local apontam para o disco de volume
links() {
  mkdir -p $L/venv $L/comfyui/models $L/ollama/models $L/ollama/usr/bin $L/ollama/usr/lib \
    $L/apt/archives/partial $L/apt/lists/partial /opt || return 1
  ln -sfn $L/venv /opt/venv && ln -sfn $L/comfyui /opt/comfyui && ln -sfn $L/ollama /opt/ollama \
    && ln -sfn $L/ollama/usr/bin/ollama /usr/local/bin/ollama \
    && ln -sfn $L/ollama/usr/lib/ollama /usr/local/lib/ollama
}
# as pastas de dados do ComfyUI (fotos e vídeos dos clientes) ficam no contêiner: a parada as apaga
links_dados() {
  local d
  for d in input output temp user; do
    mkdir -p /root/comfy-dados/$d || return 1
    [[ -L $L/comfyui/$d ]] || rm -rf "${L:?}/comfyui/$d"
    ln -sfn /root/comfy-dados/$d $L/comfyui/$d || return 1
  done
}

lista_modelos() {  # <url> <destino> <bytes> <sha256> de cada modelo do conjunto
  python3 - "$MODEL_SET" <<'PY'
import json, sys
lock = json.load(open("/opt/render2go/worker/models.lock.json"))
nomes = sorted({m for s in sys.argv[1].split(",") for m in lock["sets"][s]})
for n in nomes:
    m = lock["models"][n]
    url = f"https://huggingface.co/{m['repo']}/resolve/{m['revision']}/{m['path']}"
    print(url, f"/opt/comfyui/models/{m['dest']}", m["bytes"], m["sha256"], sep="\t")
PY
}

# SHA-256 de cada modelo e de cada blob do Ollama, em paralelo e com prioridade baixa: confere o disco
# de volume e deixa os arquivos na memória para o 1º vídeo. Arquivo que não confere: FALHOU.
conferir_volume() {
  local ruins
  ruins=$(nice -n 10 python3 - $R/modelos.tsv "$OLLAMA_MODELS/blobs" <<'PY'
import hashlib, os, sys
from concurrent.futures import ThreadPoolExecutor
alvos = []
blobs = sys.argv[2]
if os.path.isdir(blobs):  # o compilador primeiro (o pedido é traduzido antes do 1º vídeo)
    alvos += [(os.path.join(blobs, b), b[7:]) for b in sorted(os.listdir(blobs)) if b.startswith("sha256-")]
for linha in open(sys.argv[1]):
    _, dest, _, sha = linha.rstrip("\n").split("\t")
    alvos.append((dest, sha))
def sha(caminho):
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        while b := f.read(16 << 20):
            h.update(b)
    return h.hexdigest()
with ThreadPoolExecutor(4) as ex:
    for (caminho, esperado), obtido in zip(alvos, ex.map(lambda a: sha(a[0]), alvos)):
        if obtido != esperado:
            print(caminho)
PY
) || ruins="erro na conferência"
  if [[ -n "$ruins" ]]; then
    rm -f $L/instalado
    echo "FALHOU disco de volume com arquivo corrompido: $ruins" > $EST
    echo "SHA-256 não confere: $ruins"
    return 1
  fi
  echo "SHA-256 do disco de volume conferido"
}

# volta da hibernação: nada baixado se o marcador é desta versão e o volume tem tudo. Tudo é conferido
# no próprio volume ANTES dos links: o que falta aqui (return 1) vira a subida do zero, com /opt intacto.
# Depois dos links, uma falha é FALHOU: o orquestrador para o pod de novo, ou o encerra se o problema for
# o disco de volume ("disco de volume" no motivo)
retomada() {
  [[ -n "$L" && -n "$VERSAO" && -f $L/instalado ]] || return 1
  local v
  read -r v _ < $L/instalado || return 1
  [[ "$v" == "$VERSAO" ]] || { echo "disco de volume de outra versão ($v)"; return 1; }
  mkdir -p /opt/render2go && tar -xzf "$REPO_TGZ" -C /opt/render2go || return 1
  lista_modelos > $R/modelos.tsv || return 1
  local url dest bytes sha
  while IFS=$'\t' read -r url dest bytes sha; do
    [ "$(stat -c %s "$L/comfyui/models/${dest#/opt/comfyui/models/}" 2>/dev/null)" = "$bytes" ] \
      || { echo "faltando no volume: $dest"; return 1; }
  done < $R/modelos.tsv
  [[ -x $L/venv/bin/python && -x $L/ollama/usr/bin/ollama && -d $L/ollama/models/blobs ]] \
    || { echo "ambiente incompleto no volume"; return 1; }
  links && links_dados || falha "disco de volume: os links para $L não foram criados"
  marca retomada_links
  echo AMBIENTE > $EST  # os serviços já podem subir (não dependem do apt)
  "${APT[@]}" install -y -qq --no-install-recommends --no-download curl ffmpeg procps >/dev/null 2>&1 \
    || { "${APT[@]}" update -qq && "${APT[@]}" install -y -qq --no-install-recommends curl ffmpeg procps >/dev/null; } \
    || falha "apt na volta da hibernação"
  marca retomada_apt
  echo PRONTO > $EST
  echo "PRONTO (volta da hibernação)"
  conferir_volume > $R/conferir.log 2>&1
  marca retomada_conferida
  return 0
}
arg() { sed -n "s/^ARG $1=//p" $W/Dockerfile | head -1; }
CACHE="" GRAVA=0 CHAVE="" AMB=/nao-ha
export CACHE

# confere bytes e SHA-256; marca em $R/ok. Uso: confere <url> <destino> <bytes> <sha>
confere() {
  [ "$(stat -c %s "$2" 2>/dev/null)" = "$3" ] && [ "$(sha256 "$2")" = "$4" ] \
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
um() {  # reserva: curl numa conexão só, com retomada; da origem e, se falhar, do espelho. Uso:
  # um <url> <destino> [origem]: com o espelho em uso, ele vem antes, menos com "origem" (o arquivo
  # que veio dele não conferiu: até 12/10/2026, a 2ª tentativa ia ao mesmo espelho ruim e desistia)
  mkdir -p "$(dirname "$2")"
  local u m; m=$(espelho "$2")
  local ordem=("$1" "$1" "$1" ${m:+"$m" "$m"})
  [[ -n "$m" && -f $R/so_espelho && "${3:-}" != origem ]] && ordem=("$m" "$m" "$1" "$1")
  for u in "${ordem[@]}"; do
    curl -fsSL -C - --retry 5 -o "$2" "$u" && return 0; sleep 5
  done
  return 1
}
# depois do download em partes: se bytes ou SHA-256 não conferem (ex.: espelho ruim), apaga e
# baixa de novo numa conexão só, da origem primeiro, antes de desistir
reconfere() {
  confere "$@" && return 0
  echo "bytes ou SHA-256 errados: baixando de novo da origem: $(basename "$2")"
  rm -f "$2" && um "$1" "$2" origem && confere "$@" || { echo "bytes ou SHA-256 errados: $1"; return 1; }
}
rotulo() { basename "$1" | cut -c1-24; }
# um modelo do começo ao fim, sem esperar os outros (12/10/2026; antes, todos esperavam o último download
# para começar a conferir): aria2 em 16 partes (espelho e origem ao mesmo tempo, ou só o espelho), curl de
# reserva e o SHA-256 logo que ele termina. Uso: baixar_um <url> <destino> <bytes> <sha>
baixar_um() {
  local url=$1 dest=$2 bytes=$3 m fontes
  mkdir -p "$(dirname "$dest")"
  m=$(espelho "$dest")
  if [[ -n "$m" && -f $R/so_espelho ]]; then fontes=("$m"); else fontes=("$url" ${m:+"$m"}); fi
  aria2c ${SSL_CERT_FILE:+--ca-certificate=$SSL_CERT_FILE} -x 16 -s 16 -k 64M --file-allocation=none \
    --continue=true --allow-overwrite=true --auto-file-renaming=false --max-tries=10 --retry-wait=5 \
    --console-log-level=warn --summary-interval=60 -d "$(dirname "$dest")" -o "$(basename "$dest")" \
    "${fontes[@]}" || echo "aria2 falhou: $(basename "$dest"); completando com curl"
  [ "$(stat -c %s "$dest" 2>/dev/null)" = "$bytes" ] || um "$url" "$dest" || return 1
  marca "baixado_$(rotulo "$dest")"
  reconfere "$@" || return 1
  marca "conferido_$(rotulo "$dest")"
}
export -f sha256 marca confere copia espelho um reconfere rotulo baixar_um
export T0

modelos() {
  lista_modelos > $R/modelos.tsv || return 1
  [[ -n "$CACHE" ]] && xargs -r -P 4 -L 1 bash -c 'copia "$@"' _ < $R/modelos.tsv
  while IFS=$'\t' read -r url dest bytes sha; do
    [[ -f "$R/ok/$(basename "$dest")" ]] || printf '%s\t%s\t%s\t%s\n' "$url" "$dest" "$bytes" "$sha"
  done < $R/modelos.tsv > $R/falta.tsv
  marca modelos_volume
  if [[ ! -s $R/falta.tsv ]]; then
    echo "rede -" > $R/rede  # nada a baixar: o orquestrador não tem o que medir
    return 0
  fi
  # em partes: os 2 arquivos de ~15 GB dominam; numa conexão só, a média caía para ~140 MB/s
  xargs -r -P 4 -L 1 bash -c 'baixar_um "$@" || exit 255' _ < $R/falta.tsv &
  local baixando=$! total
  marca modelos_inicio
  # o teste de rede é o próprio download (antes, 15 s de teste com 4 conexões, antes de começar): o
  # orquestrador lê $R/rede, descarta o host lento e lembra a velocidade do datacenter
  total=$(awk -F'\t' '{s += $3} END {print int(s / 1048576)}' $R/falta.tsv)  # MiB a baixar
  if (( total < 2048 )); then
    echo "rede -" > $R/rede  # pouco a baixar (ex.: um modelo novo): não dá para julgar a rede
  else
    medir_rede $baixando &
  fi
  wait $baixando || return 1
  marca modelos_conferidos
}

# MB/s de ~10 s do download, em $R/rede, sem atrasar o fim dele: começa no 1º MB (até 15 s de espera:
# DNS e TLS das conexões) e para antes se o download acabar. Arquivo apagado no meio (SHA-256 errado,
# baixado de novo) deixa a conta sem sentido: "rede -", e o orquestrador não julga. Uso: medir_rede <pid>
medir_rede() {
  local i=0 k0=0 k1 t0 t1
  while (( i++ < 15 )); do
    k0=$(du -sk "$COMFY_DIR/models" 2>/dev/null | cut -f1)
    (( ${k0:-0} > 1024 )) && break
    sleep 1
  done
  t0=$EPOCHREALTIME
  for (( i = 0; i < 10; i++ )); do
    kill -0 "$1" 2>/dev/null || break
    sleep 1
  done
  k1=$(du -sk "$COMFY_DIR/models" 2>/dev/null | cut -f1)
  t1=$EPOCHREALTIME
  awk -v a="${k0:-0}" -v b="${k1:-0}" -v t0="$t0" -v t1="$t1" 'BEGIN { d = t1 - t0
    if (b < a || d < 1) print "rede -"; else printf "rede %d\n", (b - a) / 1024 / d }' > $R/rede
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
  [ "$(sha256 /tmp/umt5.zip)" = "$(arg UMT5_PATCH_SHA256)" ] || return 1
  unzip -qo /tmp/umt5.zip -d $COMFY_DIR/custom_nodes/
  local v; v=$(arg OLLAMA_VERSION)
  curl -fsSL -o /tmp/ollama.tar.zst "https://github.com/ollama/ollama/releases/download/$v/ollama-linux-amd64.tar.zst" || return 1
  [ "$(sha256 /tmp/ollama.tar.zst)" = "$(arg OLLAMA_SHA256)" ] || return 1
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
  [ "$(sha256 $R/ambiente.tar.zst)" = "$sha" ] \
    || { echo "ambiente do volume não confere"; rm -f $R/ambiente.tar.zst; return 1; }
  tar --zstd -xf $R/ambiente.tar.zst -C / || return 1
  rm -f $R/ambiente.tar.zst
  touch $R/vol/ambiente && echo "ambiente do volume ($CHAVE)"
}

ambiente() {
  export VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH UV_HTTP_TIMEOUT=300
  if ! ambiente_do_volume; then
    instalar_ambiente || return 1
    marca ambiente_instalado
    if [[ $GRAVA == 1 ]]; then  # empacota agora, antes de qualquer serviço escrever no ComfyUI
      tar -C / -cf - --exclude='opt/comfyui/models/*/*' --exclude='opt/comfyui/output' \
        --exclude='opt/comfyui/input' --exclude='opt/comfyui/temp' --exclude='opt/comfyui/user' \
        opt/venv opt/comfyui usr/local/bin/ollama usr/local/lib/ollama \
        | zstd -q -f -3 -T0 -o $R/ambiente.tar.zst \
        && echo "$(sha256 $R/ambiente.tar.zst) $(stat -c %s $R/ambiente.tar.zst)" \
          > $R/ambiente.tar.zst.sha256 \
        || { echo "AVISO: não empacotou o ambiente"; rm -f $R/ambiente.tar.zst*; }
      marca ambiente_empacotado
    fi
  else
    marca ambiente_volume
  fi
  mkdir -p $OLLAMA_MODELS
  if [[ -n "$CACHE" ]]; then  # blobs do volume, conferidos pelo SHA-256 do nome; ruim é apagado
    rsync -r --inplace --size-only $CACHE/ollama/ $OLLAMA_MODELS/
    find $OLLAMA_MODELS/blobs -name 'sha256-*' 2>/dev/null | xargs -r -P 4 -I{} bash -c \
      '[ "$(sha256 "$1")" = "$(basename "$1" | cut -c8-)" ] || { echo "blob ruim: $1"; rm -f "$1"; }' _ {}
  fi
  bash $W/scripts/pull_compiler_model.sh "$(arg COMPILER_MODEL)" "$(arg COMPILER_MODEL_DIGEST)" || return 1
  marca compilador
  [[ "${SKIP_GPU_CHECK:-0}" == "1" ]] || /opt/venv/bin/python $W/scripts/gpu_check.py  # 1: só testes sem GPU
}

# pod que hiberna, depois do PRONTO: tudo o que a volta usa, copiado do disco do contêiner (já
# conferido) para $L; o marcador só no fim, com os bytes de cada modelo conferidos no volume
guardar_local() {
  rm -f $L/instalado
  mkdir -p $L/venv $L/comfyui $L/ollama/usr/bin $L/ollama/usr/lib/ollama $L/ollama/models || return 1
  nice -n 10 rsync -a --delete /opt/venv/ $L/venv/ || return 1
  # as pastas de dados (fotos e vídeos dos clientes) nunca vão para o volume
  nice -n 10 rsync -a --delete --exclude=/input/ --exclude=/output/ --exclude=/temp/ --exclude=/user/ \
    /opt/comfyui/ $L/comfyui/ || return 1
  nice -n 10 rsync -a /usr/local/bin/ollama $L/ollama/usr/bin/ollama || return 1
  nice -n 10 rsync -a --delete /usr/local/lib/ollama/ $L/ollama/usr/lib/ollama/ || return 1
  nice -n 10 rsync -a --delete $OLLAMA_MODELS/ $L/ollama/models/ || return 1
  local dest bytes
  while IFS=$'\t' read -r _ dest bytes _; do
    [ "$(stat -c %s "$L/comfyui/models/${dest#/opt/comfyui/models/}" 2>/dev/null)" = "$bytes" ] \
      || { echo "bytes errados no volume: $dest"; return 1; }
  done < $R/modelos.tsv
  echo "$VERSAO $(date +%s)" > $L/instalado
  echo "disco de volume gravado ($VERSAO)"
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

principal() {
  mkdir -p $R/ok $R/vol && echo INSTALANDO > $EST && : > $R/tempos && rm -f $R/guardado
  if [[ -n "$L" ]] && retomada; then
    echo GUARDADO > $R/guardado
    exit 0
  fi
  if [[ -n "$L" ]]; then  # 1ª subida (ou volume velho/incompleto): o volume só é gravado no fim
    rm -f $L/instalado
    mkdir -p $L/apt/archives/partial $L/apt/lists/partial || falha "disco de volume"
  fi

  "${APT[@]}" update -qq && "${APT[@]}" install -y -qq --no-install-recommends \
    curl ca-certificates rsync aria2 >/dev/null || falha "apt"
  marca apt_download
  if [[ "${RENDER2GO_VOLUME:-0}" == "1" && -d /workspace ]]; then
    CACHE=/workspace/render2go-cache
    mkdir -p $CACHE/modelos $CACHE/ollama $CACHE/ambiente
    [[ "${RENDER2GO_VOLUME_GRAVA:-0}" == "1" ]] && GRAVA=1
  fi
  export CACHE
  mkdir -p /opt/render2go && tar -xzf "$REPO_TGZ" -C /opt/render2go || falha "repo"
  CHAVE=$(cat $W/requirements.lock $W/Dockerfile | sha256sum | cut -c1-16)
  AMB=${CACHE:-/nao-ha}/ambiente/ambiente-$CHAVE.tar.zst

  modelos > $R/modelos.log 2>&1 &
  MPID=$!
  "${APT[@]}" install -y -qq --no-install-recommends git ffmpeg zstd unzip procps >/dev/null \
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
  if [[ -n "$L" && -n "$VERSAO" ]]; then  # pod que hiberna: copia para o disco de volume (com o pod no ar)
    guardar_local > $R/guardar_local.log 2>&1 || echo "AVISO: disco de volume incompleto" >> $R/guardar_local.log
    marca volume_gravado
  fi
  marca guardado
  echo GUARDADO > $R/guardado
}

[[ "${BOOTSTRAP_SO_FUNCOES:-0}" == "1" ]] || principal
