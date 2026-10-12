#!/usr/bin/env bash
# Roda no pod de medição (12/10/2026), em segundo plano, a partir do estado AMBIENTE:
#   1. pesos do RIFE v4.25 (MIT) do Comfy-Org/frame_interpolation, revisão fixada, conferidos
#      por tamanho e SHA-256, no disco local (models/frame_interpolation);
#   2. SageAttention 2.2.0 compilado neste pod a partir do commit da tag v2.2.0 (Apache-2.0),
#      só para sm89 (L40S), com o nvcc 12.8 do repositório da NVIDIA para Debian 12: o mesmo
#      CUDA do PyTorch 2.8.0+cu128 do worker. Gera o wheel e não instala (a fase 4 instala).
# Escreve RIFE_OK / SAGE_OK (ou *_FALHOU <motivo>) em $SAIDA/estado_extras.
# Compila com nice 19 e metade dos núcleos, para atrapalhar o mínimo as medidas da fase 1.
set -uo pipefail
SAIDA=${1:-/root/render2go/medidas}
mkdir -p "$SAIDA"
EST=$SAIDA/estado_extras
: > "$EST"
marca() { echo "$(date +%s) $*" | tee -a "$EST"; }

RIFE_URL=https://huggingface.co/Comfy-Org/frame_interpolation/resolve/219da3c9d8c357ceaf457fc1d5932c6e861b8dee/frame_interpolation/rife_v4.25.safetensors
RIFE_SHA=1505884b9bdae956795430d2a70f7e2317b2abd8f130f8cfdb35a5759f909481
RIFE_BYTES=22674688
SAGE_COMMIT=eb615cf6cf4d221338033340ee2de1c37fbdba4a  # tag v2.2.0
RIFE=/opt/comfyui/models/frame_interpolation/rife_v4.25.safetensors

# 1. RIFE -------------------------------------------------------------------------------------
mkdir -p "$(dirname $RIFE)"
if curl -fsSL --retry 3 -o "$RIFE.part" "$RIFE_URL" \
  && [ "$(stat -c %s "$RIFE.part")" = "$RIFE_BYTES" ] \
  && echo "$RIFE_SHA  $RIFE.part" | sha256sum -c --quiet -; then
  mv "$RIFE.part" "$RIFE" && marca RIFE_OK
else
  rm -f "$RIFE.part"; marca "RIFE_FALHOU download ou SHA-256"
fi

# 2. SageAttention ----------------------------------------------------------------------------
falha() { marca "SAGE_FALHOU $*"; exit 1; }
# o apt do bootstrap (ffmpeg) roda junto com o download dos modelos: espera ele terminar
for _ in $(seq 600); do
  grep -qlx -e apt-get -e dpkg -e apt /proc/[0-9]*/comm 2>/dev/null || break
  sleep 2
done
marca apt_livre
export DEBIAN_FRONTEND=noninteractive
apt-get -qq update >/dev/null && apt-get -qq install -y --no-install-recommends \
  gcc g++ libc6-dev make git >/dev/null || falha "apt (compilador)"
curl -fsSL -o /tmp/cuda-keyring.deb \
  https://developer.download.nvidia.com/compute/cuda/repos/debian12/x86_64/cuda-keyring_1.1-1_all.deb \
  || falha "cuda-keyring"
marca "cuda-keyring sha256 $(sha256sum < /tmp/cuda-keyring.deb | cut -c1-64)"
dpkg -i /tmp/cuda-keyring.deb >/dev/null || falha "dpkg cuda-keyring"
apt-get -qq update >/dev/null && apt-get -qq install -y --no-install-recommends \
  cuda-nvcc-12-8 cuda-cudart-dev-12-8 cuda-driver-dev-12-8 cuda-nvrtc-dev-12-8 \
  cuda-nvtx-12-8 cuda-profiler-api-12-8 libcublas-dev-12-8 libcusparse-dev-12-8 \
  libcusolver-dev-12-8 >/dev/null || falha "apt (CUDA 12.8)"
export CUDA_HOME=/usr/local/cuda-12.8 PATH=/usr/local/cuda-12.8/bin:$PATH
marca "nvcc $(nvcc --version | grep -o 'release [0-9.]*')"
rm -rf /root/sage
git clone -q --depth 1 --branch v2.2.0 https://github.com/thu-ml/SageAttention /root/sage \
  || falha "git clone"
[ "$(git -C /root/sage rev-parse HEAD)" = "$SAGE_COMMIT" ] || falha "commit diferente da tag"
UV=$(command -v uv) || falha "uv ausente"
"$UV" pip install -q --python /opt/venv/bin/python "setuptools>=70" wheel ninja packaging \
  || falha "ferramentas de build"
marca "ferramentas $("$UV" pip freeze --python /opt/venv/bin/python | grep -iE '^(setuptools|wheel|ninja|packaging)==' | tr '\n' ' ')"
NPROC=$(nproc)
MEM_GB=$(awk '/MemTotal/ {print int($2/1048576)}' /proc/meminfo)
JOBS=$((NPROC / 2)); ((JOBS < 2)) && JOBS=2; ((JOBS > MEM_GB / 4)) && JOBS=$((MEM_GB / 4))
marca "compilando: $NPROC núcleos, ${MEM_GB} GB, $JOBS processos"
cd /root/sage || falha "pasta"
TORCH_CUDA_ARCH_LIST=8.9 EXT_PARALLEL=3 MAX_JOBS=$JOBS NVCC_APPEND_FLAGS="--threads 2" \
  nice -n 19 /opt/venv/bin/python setup.py -q bdist_wheel > "$SAIDA/sage_build.log" 2>&1 \
  || falha "build (ver sage_build.log)"
WHL=$(ls /root/sage/dist/sageattention-2.2.0-*.whl 2>/dev/null | head -1)
[ -n "$WHL" ] || falha "wheel ausente"
cp "$WHL" "$SAIDA/" && marca "SAGE_OK $(basename "$WHL") $(sha256sum < "$WHL" | cut -c1-64)"
