#!/usr/bin/env bash
# Roda no 1º pod da rodada de qualidade (13/10/2026), em segundo plano, a partir do estado AMBIENTE:
# o SageAttention 2.2.0 compilado neste pod a partir do commit da tag v2.2.0 (Apache-2.0), só para
# sm89 (L40S), com o nvcc 12.8 do repositório da NVIDIA para Debian 12 (o CUDA do PyTorch
# 2.8.0+cu128 do worker). Gera o wheel e não instala (a fase S do no_pod.py instala). É a parte do
# SageAttention do worker/evals/gpu-2026-10-12-velocidade/preparar_extras.sh, sem o RIFE.
# Escreve SAGE_OK <wheel> <sha256> (ou SAGE_FALHOU <motivo>) em $SAIDA/estado_sage.
# Compila com nice 19 e metade dos núcleos da cota, para atrapalhar o mínimo as medidas.
set -uo pipefail
SAIDA=${1:-/root/render2go/qualidade/saida}
mkdir -p "$SAIDA"
EST=$SAIDA/estado_sage
: > "$EST"
marca() { echo "$(date +%s) $*" | tee -a "$EST"; }
falha() { marca "SAGE_FALHOU $*"; exit 1; }
SAGE_COMMIT=eb615cf6cf4d221338033340ee2de1c37fbdba4a  # tag v2.2.0

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
# as CPUs da cota do contêiner (no pod, nproc e os.cpu_count() dão as do host)
CPUS=$(PYTHONPATH=/opt/render2go/worker/src /opt/venv/bin/python -c \
  'from render2go_worker import legenda; print(legenda.cpus_do_conteiner())' 2>/dev/null || echo 4)
MEM_GB=$(awk '/MemTotal/ {print int($2/1048576)}' /proc/meminfo)
JOBS=$((CPUS / 2)); ((JOBS < 2)) && JOBS=2; ((JOBS > MEM_GB / 4)) && JOBS=$((MEM_GB / 4))
marca "compilando: $CPUS CPUs na cota, ${MEM_GB} GB, $JOBS processos"
cd /root/sage || falha "pasta"
TORCH_CUDA_ARCH_LIST=8.9 EXT_PARALLEL=3 MAX_JOBS=$JOBS NVCC_APPEND_FLAGS="--threads 2" \
  nice -n 19 /opt/venv/bin/python setup.py -q bdist_wheel > "$SAIDA/sage_build.log" 2>&1 \
  || falha "build (ver sage_build.log)"
WHL=$(ls /root/sage/dist/sageattention-2.2.0-*.whl 2>/dev/null | head -1)
[ -n "$WHL" ] || falha "wheel ausente"
cp "$WHL" "$SAIDA/" && marca "SAGE_OK $(basename "$WHL") $(sha256sum < "$WHL" | cut -c1-64)"
