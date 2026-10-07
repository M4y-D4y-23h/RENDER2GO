#!/usr/bin/env bash
# Entrada da imagem do worker. Substitui o start.sh do pipeline manual: nada é instalado
# aqui; só confere a GPU e os modelos, sobe o Ollama e o ComfyUI e os supervisiona.
#
# Variáveis:
#   MODE              pod (padrão) | serverless (handler do RunPod)
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
#   COMFY_GPUS        modo pod: um ComfyUI por GPU, nas portas COMFY_PORT, +1, +2...
#   COMFY_POR_GPU     modo pod: ComfyUIs por GPU (padrão 1). Com 2, a GPU de 96 GB roda dois
#                     vídeos ao mesmo tempo; cada um ganha metade da VRAM (--reserve-vram)
#                     (número ou "auto" = todas as GPUs; padrão 1; serverless usa sempre 1)
#   AUTO_TERMINATE_HOURS  modo pod no RunPod: o pod se encerra depois de N horas
#                     (padrão e máximo: 2; regra do projeto, nenhum pod sem limite)
#   TERMINATE_ON_BAD_GPU  modo pod no RunPod: encerra o pod se a GPU falhar (padrão: 1)
#   GPU2GO_SAFETY, GPU2GO_ALLOWED_URL_HOSTS  obrigatórias no serverless (ver jobs.py)
set -euo pipefail

MODE="${MODE:-pod}"
MODEL_SET="${MODEL_SET:-i2v_r1v2}"
COMFY_DIR="${COMFY_DIR:-/opt/comfyui}"
COMFY_LISTEN="${COMFY_LISTEN:-127.0.0.1}"
COMFY_PORT="${COMFY_PORT:-8188}"
RESERVE_VRAM_GB="${RESERVE_VRAM_GB:-6}"
COMFY_GPUS="${COMFY_GPUS:-1}"
COMFY_POR_GPU="${COMFY_POR_GPU:-1}"
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
  pod|serverless) ;;
  *) die "MODE desconhecido: $MODE" ;;
esac
if [[ "$COMFY_GPUS" == "auto" ]]; then
  COMFY_GPUS=$(nvidia-smi -L 2>/dev/null | grep -c "^GPU" || true)
  [[ "$COMFY_GPUS" -ge 1 ]] || COMFY_GPUS=1
fi
[[ "$COMFY_GPUS" =~ ^[1-9][0-9]*$ ]] || die "COMFY_GPUS inválido: $COMFY_GPUS"
[[ "$COMFY_POR_GPU" =~ ^[1-4]$ ]] || die "COMFY_POR_GPU inválido: $COMFY_POR_GPU (1 a 4)"
[[ "$MODE" == "serverless" ]] && COMFY_GPUS=1 && COMFY_POR_GPU=1  # o handler fala com um só

# 0. Desligamento automático no RunPod (o RunPod injeta RUNPOD_POD_ID e RUNPOD_API_KEY) ---
DEADMAN_PID=""
MAX_POD_HOURS=2
AUTO_TERMINATE_HOURS="${AUTO_TERMINATE_HOURS:-$MAX_POD_HOURS}"
[[ "$AUTO_TERMINATE_HOURS" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "AUTO_TERMINATE_HOURS inválido: $AUTO_TERMINATE_HOURS"
if python -c "import sys; h = float(sys.argv[1]); sys.exit(0 if 0 < h <= $MAX_POD_HOURS else 1)" "$AUTO_TERMINATE_HOURS"; then :; else
  log "AUTO_TERMINATE_HOURS=$AUTO_TERMINATE_HOURS fora de (0, $MAX_POD_HOURS]: usando $MAX_POD_HOURS h"
  AUTO_TERMINATE_HOURS=$MAX_POD_HOURS
fi
if [[ "$MODE" == "pod" && -n "${RUNPOD_POD_ID:-}" ]]; then
  secs=$(python -c "print(int(float('$AUTO_TERMINATE_HOURS') * 3600))")
  log "desligamento automático em $AUTO_TERMINATE_HOURS h"
  (sleep "$secs" && python -m gpu2go_worker.runpod_pods autoencerrar) &
  DEADMAN_PID=$!
fi

# 1. GPU ---------------------------------------------------------------------------
if [[ "${SKIP_GPU_CHECK:-0}" != "1" ]]; then
  log "checando a GPU..."
  if ! python "$WORKER_DIR/scripts/gpu_check.py"; then
    # máquina com defeito (visto num host community: nvidia-smi ok, cuInit falhando)
    # (no serverless, basta sair: o RunPod troca o worker)
    if [[ "$MODE" == "pod" && -n "${RUNPOD_POD_ID:-}" && "${TERMINATE_ON_BAD_GPU:-1}" == "1" ]]; then
      log "encerrando o pod para não cobrar por uma GPU que não roda CUDA"
      python -m gpu2go_worker.runpod_pods autoencerrar || true
    fi
    die "GPU indisponível (veja a mensagem acima)"
  fi
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

# 5. ComfyUI: COMFY_POR_GPU processos por GPU (CUDA_VISIBLE_DEVICES), portas consecutivas ----
# Com mais de um por GPU, cada processo reserva a VRAM dos outros (--reserve-vram), para os
# dois não disputarem a memória inteira.
COMFY_PIDS=()
N_COMFY=$((COMFY_GPUS * COMFY_POR_GPU))
reserva=$RESERVE_VRAM_GB
if ((COMFY_POR_GPU > 1)); then
  total_gb=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits -i 0 | awk '{print int($1/1024)}')
  reserva=$((total_gb * (COMFY_POR_GPU - 1) / COMFY_POR_GPU + RESERVE_VRAM_GB))
  log "$COMFY_POR_GPU ComfyUI por GPU de ${total_gb} GB: cada um usa até $((total_gb - reserva)) GB"
fi
cd "$COMFY_DIR"
for ((i = 0; i < N_COMFY; i++)); do
  g=$((i / COMFY_POR_GPU))
  port=$((COMFY_PORT + i))
  log "subindo o ComfyUI $i na GPU $g em $COMFY_LISTEN:$port (--reserve-vram $reserva)"
  gpu_env=()  # com uma GPU, respeita o CUDA_VISIBLE_DEVICES do host
  ((COMFY_GPUS > 1)) && gpu_env=("CUDA_VISIBLE_DEVICES=$g")
  env "${gpu_env[@]}" setsid python main.py \
    --listen "$COMFY_LISTEN" --port "$port" \
    --reserve-vram "$reserva" \
    --disable-metadata --disable-auto-launch \
    ${COMFY_EXTRA_ARGS:-} \
    > >(sed -u "s/^/[comfyui:$i] /") 2>&1 &
  COMFY_PIDS+=($!)
done
for ((i = 0; i < N_COMFY; i++)); do
  wait_http "http://127.0.0.1:$((COMFY_PORT + i))/system_stats" 300 "ComfyUI $i"
done
log "pronto"

HANDLER_PID=""
if [[ "$MODE" == "serverless" ]]; then
  log "subindo o handler do RunPod"
  setsid python -m gpu2go_worker.handler_runpod > >(sed -u 's/^/[handler] /') 2>&1 &
  HANDLER_PID=$!
fi

# 6. Supervisão: se um serviço cair, derruba os outros e sai com erro   ----------------------
trap 'kill -TERM "$OLLAMA_PID" "${COMFY_PIDS[@]}" $HANDLER_PID $DEADMAN_PID 2>/dev/null || true' TERM INT
set +e
wait -n "$OLLAMA_PID" "${COMFY_PIDS[@]}" $HANDLER_PID
status=$?
kill -TERM "$OLLAMA_PID" "${COMFY_PIDS[@]}" $HANDLER_PID $DEADMAN_PID 2>/dev/null
wait
die "um dos serviços terminou (código $status)"
