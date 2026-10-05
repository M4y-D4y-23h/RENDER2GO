#!/usr/bin/env bash
# Entrada da imagem do worker. Substitui o start.sh do pipeline manual: nada é instalado
# aqui; só confere a GPU e os modelos, sobe o Ollama e o ComfyUI e os supervisiona.
#
# Variáveis:
#   MODE              pod (padrão) | serverless (Etapa 3)
#   MODEL_SET         conjuntos do models.lock.json, separados por vírgula (padrão: i2v_r1v2)
#   MODELS_FROM_DIR   copiar modelos deste diretório (ex.: /workspace/models) se faltarem
#   MODELS_FETCH=1    baixar do Hugging Face os modelos que faltarem
#   VERIFY_SHA=1      conferir SHA-256 na cópia/download (padrão: só bytes)
#   REQUIRE_MODELS=0  subir mesmo sem os modelos (só para depuração)
#   RESERVE_VRAM_GB   VRAM deixada livre pelo ComfyUI para o Ollama (padrão: 6; 24 GB → 4)
#   COMFY_LISTEN      endereço do ComfyUI (padrão: 127.0.0.1; acesse por túnel SSH)
#   PUBLIC_KEY        chave SSH pública: liga o sshd no modo pod
#   SKIP_GPU_CHECK=1  pular a checagem de GPU (só testes sem GPU)
#   COMFY_EXTRA_ARGS  argumentos extras do ComfyUI (ex.: --cpu em testes sem GPU)
set -euo pipefail

MODE="${MODE:-pod}"
MODEL_SET="${MODEL_SET:-i2v_r1v2}"
COMFY_DIR="${COMFY_DIR:-/opt/comfyui}"
COMFY_LISTEN="${COMFY_LISTEN:-127.0.0.1}"
COMFY_PORT="${COMFY_PORT:-8188}"
RESERVE_VRAM_GB="${RESERVE_VRAM_GB:-6}"
WORKER_DIR="${GPU2GO_WORKER_DIR:-/opt/gpu2go/worker}"
export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-0}"

log() { echo "[gpu2go] $*"; }
die() { echo "[gpu2go] ERRO: $*" >&2; exit 1; }

wait_http() {  # url, segundos, nome
  local i
  for ((i = 0; i < $2; i++)); do
    curl -fsS -o /dev/null "$1" 2>/dev/null && return 0
    sleep 1
  done
  die "$3 não respondeu em $2 s ($1)"
}

case "$MODE" in
  pod) ;;
  serverless) die "MODE=serverless chega na Etapa 3 (handler próprio); use MODE=pod" ;;
  *) die "MODE desconhecido: $MODE" ;;
esac

# 1. GPU ---------------------------------------------------------------------------
if [[ "${SKIP_GPU_CHECK:-0}" != "1" ]]; then
  log "checando a GPU..."
  python "$WORKER_DIR/scripts/gpu_check.py" || die "GPU indisponível (veja a mensagem acima)"
fi

# 2. Modelos: sempre no disco local, nunca lidos direto do volume ---------------------
IFS=, read -ra model_sets <<< "$MODEL_SET"
fetch_args=("${model_sets[@]}" --models-dir "$COMFY_DIR/models")
[[ "${VERIFY_SHA:-0}" == "1" ]] || fetch_args+=(--no-sha)
if ! python -m gpu2go_worker.models "${fetch_args[@]}" --check-only >/dev/null 2>&1; then
  if [[ -n "${MODELS_FROM_DIR:-}" ]]; then
    log "copiando modelos de $MODELS_FROM_DIR (leitura sequencial; ~3 min para 37 GB)"
    python -m gpu2go_worker.models "${fetch_args[@]}" --from-dir "$MODELS_FROM_DIR" \
      || die "falha ao copiar os modelos"
  elif [[ "${MODELS_FETCH:-0}" == "1" ]]; then
    log "baixando modelos do Hugging Face"
    python -m gpu2go_worker.models "${fetch_args[@]}" || die "falha ao baixar os modelos"
  elif [[ "${REQUIRE_MODELS:-1}" == "1" ]]; then
    python -m gpu2go_worker.models "${fetch_args[@]}" --check-only || true
    die "modelos ausentes: use a imagem full, MODELS_FROM_DIR ou MODELS_FETCH=1"
  else
    log "AVISO: subindo sem os modelos (REQUIRE_MODELS=0)"
  fi
fi

# 3. SSH (só modo pod, para túnel até o ComfyUI) ---------------------------------------
if [[ "$MODE" == "pod" && -n "${PUBLIC_KEY:-}" ]]; then
  mkdir -p /root/.ssh /run/sshd && chmod 700 /root/.ssh
  echo "$PUBLIC_KEY" > /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys
  ssh-keygen -A >/dev/null
  /usr/sbin/sshd && log "sshd ligado (ssh -L 8188:127.0.0.1:8188 ...)"
fi

# 4. Ollama, desacoplado do ComfyUI (o papel do setsid do start.sh) ----------------------
log "subindo o Ollama em $OLLAMA_HOST"
setsid ollama serve > >(sed -u 's/^/[ollama] /') 2>&1 &
OLLAMA_PID=$!
wait_http "http://$OLLAMA_HOST/api/version" 60 "Ollama"

# 5. ComfyUI -----------------------------------------------------------------------------
log "subindo o ComfyUI em $COMFY_LISTEN:$COMFY_PORT (--reserve-vram $RESERVE_VRAM_GB)"
cd "$COMFY_DIR"
setsid python main.py \
  --listen "$COMFY_LISTEN" --port "$COMFY_PORT" \
  --reserve-vram "$RESERVE_VRAM_GB" \
  --disable-metadata --disable-auto-launch \
  ${COMFY_EXTRA_ARGS:-} \
  > >(sed -u 's/^/[comfyui] /') 2>&1 &
COMFY_PID=$!
wait_http "http://127.0.0.1:$COMFY_PORT/system_stats" 300 "ComfyUI"
log "pronto"

# 6. Supervisão: se um dos dois cair, derruba o outro e sai com erro ----------------------
trap 'kill -TERM "$OLLAMA_PID" "$COMFY_PID" 2>/dev/null || true' TERM INT
set +e
wait -n "$OLLAMA_PID" "$COMFY_PID"
status=$?
kill -TERM "$OLLAMA_PID" "$COMFY_PID" 2>/dev/null
wait
die "um dos serviços terminou (código $status)"
