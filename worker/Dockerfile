# syntax=docker/dockerfile:1.7
#
# Imagem do worker GPU2GO. Reproduz o start.sh do pipeline manual sem nada instalado em
# runtime. Contexto de build: a pasta worker/.
#
#   docker build -f worker/Dockerfile --target slim -t gpu2go-worker:slim worker
#   docker build -f worker/Dockerfile --target full -t gpu2go-worker:full worker
#   docker build -f worker/Dockerfile --target full-t2v -t gpu2go-worker:full-t2v worker
#   docker build -f worker/Dockerfile --target bakeoff -t gpu2go-worker:bakeoff worker
#
# Alvos:
#   runtime  sistema + Python + PyTorch cu128 + ComfyUI v0.38.0 + patch UMT5 + Ollama
#   slim     runtime + código do worker + modelo do compilador; sem modelos de vídeo
#            (para pods com volume ou MODELS_FETCH=1)
#   full     slim + modelos de vídeo do models.lock.json, uma camada por arquivo (~37 GB)
#
# O PyTorch e o Ollama trazem as próprias bibliotecas CUDA; a base é Ubuntu puro e o
# NVIDIA container toolkit injeta o driver (driver >= 570 para cu128).

ARG UBUNTU_IMAGE=ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.17

FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------------------------------
FROM ${UBUNTU_IMAGE} AS runtime

ARG COMFYUI_REF=v0.38.0
ARG COMFYUI_COMMIT=6b747c0428c343e1417219641db93a4fb7cb69ae
ARG UMT5_PATCH_URL=https://huggingface.co/rzgar/NSFW-Wan-UMT5-XXL-V2/resolve/f89480ab2f3ed3da9561ca886a0f2cc25f90bd94/ComfyUI-UMT5-Extended.zip
ARG UMT5_PATCH_SHA256=717ca288e990523442d97fd8f4849e8a40348447708abdb290dd8fff7d4157cb
ARG OLLAMA_VERSION=v0.35.1
ARG OLLAMA_SHA256=9fcd79ac4575b2bd31b992eee18b1000c8ad126b451627c8f8cd091714cfbb10

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    UV_LINK_MODE=copy \
    COMFY_DIR=/opt/comfyui \
    OLLAMA_MODELS=/opt/ollama/models \
    GPU2GO_WORKER_DIR=/opt/gpu2go/worker \
    PYTHONPATH=/opt/gpu2go/worker/src

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv ca-certificates curl git ffmpeg zstd unzip tini \
        libgl1 libglib2.0-0 openssh-server \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv
COPY scripts/ca-env.sh /usr/local/lib/gpu2go/ca-env.sh

# Dependências Python com versões e hashes fixados (worker/requirements.lock)
COPY requirements.lock /tmp/requirements.lock
RUN --mount=type=secret,id=extra_ca --mount=type=tmpfs,target=/run/gpu2go-ca . /usr/local/lib/gpu2go/ca-env.sh \
    && export UV_HTTP_TIMEOUT=300 UV_CONCURRENT_DOWNLOADS=4 \
    && uv venv /opt/venv --python /usr/bin/python3.12 \
    && uv pip install --no-cache --require-hashes -r /tmp/requirements.lock \
        --index-url https://pypi.org/simple \
        --extra-index-url https://download.pytorch.org/whl/cu128 \
        --index-strategy unsafe-best-match \
    && rm /tmp/requirements.lock

# ComfyUI na tag fixada, conferindo o commit
RUN --mount=type=secret,id=extra_ca --mount=type=tmpfs,target=/run/gpu2go-ca . /usr/local/lib/gpu2go/ca-env.sh \
    && git clone --depth 1 --branch "${COMFYUI_REF}" https://github.com/comfyanonymous/ComfyUI.git "${COMFY_DIR}" \
    && test "$(git -C "${COMFY_DIR}" rev-parse HEAD)" = "${COMFYUI_COMMIT}" \
    && rm -rf "${COMFY_DIR}/.git"

# Patch obrigatório do encoder NSFW-UMT5 (só monkeypatch; revisado: sem rede nem I/O)
RUN --mount=type=secret,id=extra_ca --mount=type=tmpfs,target=/run/gpu2go-ca . /usr/local/lib/gpu2go/ca-env.sh \
    && curl -fsSL -o /tmp/umt5.zip "${UMT5_PATCH_URL}" \
    && echo "${UMT5_PATCH_SHA256}  /tmp/umt5.zip" | sha256sum -c - \
    && unzip -q /tmp/umt5.zip -d "${COMFY_DIR}/custom_nodes/" \
    && rm /tmp/umt5.zip \
    && test -f "${COMFY_DIR}/custom_nodes/ComfyUI-UMT5-Extended/__init__.py"

# Ollama na versão fixada, conferindo o checksum publicado no release
RUN --mount=type=secret,id=extra_ca --mount=type=tmpfs,target=/run/gpu2go-ca . /usr/local/lib/gpu2go/ca-env.sh \
    && curl -fsSL -o /tmp/ollama.tar.zst \
        "https://github.com/ollama/ollama/releases/download/${OLLAMA_VERSION}/ollama-linux-amd64.tar.zst" \
    && echo "${OLLAMA_SHA256}  /tmp/ollama.tar.zst" | sha256sum -c - \
    && tar --zstd -xf /tmp/ollama.tar.zst -C /usr/local \
    && rm /tmp/ollama.tar.zst \
    && ollama --version 2>&1 | grep -q "${OLLAMA_VERSION#v}"

# ---------------------------------------------------------------------------------------
FROM runtime AS slim

ARG WITH_COMPILER_MODEL=1
ARG COMPILER_MODEL=huihui_ai/qwen2.5-abliterate:7b
ARG COMPILER_MODEL_DIGEST=sha256:212345411ab817a8d7669ed63f24a30f2e2a2cd297455a6aad1de50c82f7691c

COPY pyproject.toml models.lock.json entrypoint.sh /opt/gpu2go/worker/
COPY src /opt/gpu2go/worker/src
COPY scripts /opt/gpu2go/worker/scripts
COPY workflows /opt/gpu2go/worker/workflows
COPY prompts /opt/gpu2go/worker/prompts
COPY presets /opt/gpu2go/worker/presets
RUN chmod +x /opt/gpu2go/worker/entrypoint.sh \
    && python -c "import runpod, gpu2go_worker.handler_runpod, gpu2go_worker.jobs"
# A versão da imagem (GPU2GO_IMAGE_VERSION) vem do endpoint em runtime, não de um ARG:
# um ARG aqui invalidaria o cache das camadas de modelos do alvo full.

# Teste de fumaça (CPU, sem GPU): o ComfyUI importa todos os nós, o patch UMT5 carrega,
# e o template de produção é validado contra o /object_info desta versão do ComfyUI.
RUN cd "${COMFY_DIR}" \
    && python main.py --quick-test-for-ci --cpu > /tmp/quick.log 2>&1 \
    || (cat /tmp/quick.log; exit 1); \
    grep -q "\[ComfyUI-UMT5-Extended\] Loaded" /tmp/quick.log \
    || (cat /tmp/quick.log; echo "patch UMT5 não carregou"; exit 1)
RUN cd "${COMFY_DIR}" \
    && (python main.py --cpu --listen 127.0.0.1 --port 8188 > /tmp/comfy.log 2>&1 &) \
    && for i in $(seq 1 120); do curl -fsS -o /dev/null http://127.0.0.1:8188/system_stats && break; sleep 1; done \
    && python -m gpu2go_worker.template --comfy http://127.0.0.1:8188 \
        /opt/gpu2go/worker/workflows/*.api.json \
    || (tail -50 /tmp/comfy.log; exit 1)

# Modelo do compilador, conferindo o digest da camada de pesos
RUN --mount=type=secret,id=extra_ca --mount=type=tmpfs,target=/run/gpu2go-ca . /usr/local/lib/gpu2go/ca-env.sh \
    && /opt/gpu2go/worker/scripts/pull_compiler_model.sh "$COMPILER_MODEL" "$COMPILER_MODEL_DIGEST"

ENV MODE=pod
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/gpu2go/worker/entrypoint.sh"]

# ---------------------------------------------------------------------------------------
FROM slim AS full

# Uma camada por arquivo; bytes e SHA-256 conferidos contra o models.lock.json
RUN --mount=type=secret,id=extra_ca --mount=type=tmpfs,target=/run/gpu2go-ca . /usr/local/lib/gpu2go/ca-env.sh \
    && python -m gpu2go_worker.models wan21_vae --models-dir "${COMFY_DIR}/models"
RUN --mount=type=secret,id=extra_ca --mount=type=tmpfs,target=/run/gpu2go-ca . /usr/local/lib/gpu2go/ca-env.sh \
    && python -m gpu2go_worker.models nsfw_umt5_xxl_v2_fp8 --models-dir "${COMFY_DIR}/models"
RUN --mount=type=secret,id=extra_ca --mount=type=tmpfs,target=/run/gpu2go-ca . /usr/local/lib/gpu2go/ca-env.sh \
    && python -m gpu2go_worker.models wan22_i2v_r1v2_high --models-dir "${COMFY_DIR}/models"
RUN --mount=type=secret,id=extra_ca --mount=type=tmpfs,target=/run/gpu2go-ca . /usr/local/lib/gpu2go/ca-env.sh \
    && python -m gpu2go_worker.models wan22_i2v_r1v2_low --models-dir "${COMFY_DIR}/models"

# ---------------------------------------------------------------------------------------
# T2V de produção (ADR 0001): Wan 2.2 T2V A14B fp8 + Lightning T2V v1.1, imagem separada do
# I2V para não pesar o cold start dele. Uma camada por arquivo, bytes e SHA-256 conferidos.
FROM slim AS full-t2v

RUN --mount=type=secret,id=extra_ca --mount=type=tmpfs,target=/run/gpu2go-ca . /usr/local/lib/gpu2go/ca-env.sh \
    && python -m gpu2go_worker.models wan21_vae --models-dir "${COMFY_DIR}/models"
RUN --mount=type=secret,id=extra_ca --mount=type=tmpfs,target=/run/gpu2go-ca . /usr/local/lib/gpu2go/ca-env.sh \
    && python -m gpu2go_worker.models nsfw_umt5_xxl_v2_fp8 --models-dir "${COMFY_DIR}/models"
RUN --mount=type=secret,id=extra_ca --mount=type=tmpfs,target=/run/gpu2go-ca . /usr/local/lib/gpu2go/ca-env.sh \
    && python -m gpu2go_worker.models lightning_t2v_v11_high lightning_t2v_v11_low --models-dir "${COMFY_DIR}/models"
RUN --mount=type=secret,id=extra_ca --mount=type=tmpfs,target=/run/gpu2go-ca . /usr/local/lib/gpu2go/ca-env.sh \
    && python -m gpu2go_worker.models wan22_t2v_a14b_high_fp8 --models-dir "${COMFY_DIR}/models"
RUN --mount=type=secret,id=extra_ca --mount=type=tmpfs,target=/run/gpu2go-ca . /usr/local/lib/gpu2go/ca-env.sh \
    && python -m gpu2go_worker.models wan22_t2v_a14b_low_fp8 --models-dir "${COMFY_DIR}/models"

ENV MODEL_SET=t2v_a14b_fp8

# ---------------------------------------------------------------------------------------
# Só para o bake-off do T2V (docs/adr/0001): slim + ComfyUI-GGUF. Os modelos entram em
# runtime (MODEL_SET=i2v_r1v2,t2v_a14b_q6k,t2v_a14b_fp8 com MODELS_FROM_DIR ou
# MODELS_FETCH=1), para não gerar uma imagem de ~70 GB para um experimento.
FROM slim AS bakeoff

ARG COMFYUI_GGUF_COMMIT=6ea2651e7df66d7585f6ffee804b20e92fb38b8a

COPY requirements-bakeoff.lock /tmp/requirements-bakeoff.lock
RUN --mount=type=secret,id=extra_ca --mount=type=tmpfs,target=/run/gpu2go-ca . /usr/local/lib/gpu2go/ca-env.sh \
    && uv pip install --no-cache --no-deps --require-hashes -r /tmp/requirements-bakeoff.lock \
        --index-url https://pypi.org/simple \
    && rm /tmp/requirements-bakeoff.lock \
    && git clone https://github.com/city96/ComfyUI-GGUF.git "${COMFY_DIR}/custom_nodes/ComfyUI-GGUF" \
    && git -C "${COMFY_DIR}/custom_nodes/ComfyUI-GGUF" checkout -q "${COMFYUI_GGUF_COMMIT}" \
    && rm -rf "${COMFY_DIR}/custom_nodes/ComfyUI-GGUF/.git"

RUN cd "${COMFY_DIR}" \
    && (python main.py --cpu --listen 127.0.0.1 --port 8188 > /tmp/comfy.log 2>&1 &) \
    && for i in $(seq 1 120); do curl -fsS -o /dev/null http://127.0.0.1:8188/system_stats && break; sleep 1; done \
    && python -m gpu2go_worker.template --comfy http://127.0.0.1:8188 \
        /opt/gpu2go/worker/workflows/*.api.json /opt/gpu2go/worker/workflows/experimental/*.api.json \
    || (tail -50 /tmp/comfy.log; exit 1)

ENV MODEL_SET=i2v_r1v2,t2v_a14b_q6k
