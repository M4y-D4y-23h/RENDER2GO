# syntax=docker/dockerfile:1.7
# Imagem do serviço (API + páginas + orquestrador), ADR 0003 fase 3. Sem GPU.
# Contexto = raiz do repositório:
#   docker build -f servico/Dockerfile -t gpu2go-servico .
#   (atrás de proxy que reassina TLS: --secret id=extra_ca,src=/caminho/ca.pem)
# Mesma imagem para os dois processos:
#   gpu2go-servico api          (padrão; porta 8000)
#   gpu2go-servico orquestrar   (sobe e encerra pods conforme a fila)
ARG PYTHON_IMAGE=python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.17

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=uv /uv /usr/local/bin/uv
COPY worker/scripts/ca-env.sh /usr/local/lib/gpu2go/ca-env.sh
WORKDIR /app
# o orquestrador empacota worker/ e tools/ para os pods (sem .git aqui)
COPY worker/ worker/
COPY tools/ tools/
COPY servico/ servico/
RUN --mount=type=secret,id=extra_ca --mount=type=tmpfs,target=/run/gpu2go-ca . /usr/local/lib/gpu2go/ca-env.sh \
    && cd servico && uv sync --frozen --no-dev --no-cache \
    && test -s web/app.js
RUN useradd --system --uid 10001 --create-home gpu2go
USER gpu2go
ENV PATH=/opt/venv/bin:$PATH GPU2GO_CACHE=/home/gpu2go/.cache/gpu2go
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/saude',timeout=4)"
CMD ["gpu2go-servico", "api"]
