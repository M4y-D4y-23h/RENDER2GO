# syntax=docker/dockerfile:1.7
# Imagem do serviço (API + páginas + orquestrador), ADR 0003 fase 3. Sem GPU.
# Contexto = raiz do repositório:
#   docker build -f servico/Dockerfile -t render2go-servico .
#   (atrás de proxy que reassina TLS: --secret id=extra_ca,src=/caminho/ca.pem)
# Mesma imagem para os dois processos:
#   render2go-servico api          (padrão; porta 8000)
#   render2go-servico orquestrar   (sobe e encerra pods conforme a fila)
ARG PYTHON_IMAGE=python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.17

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=uv /uv /usr/local/bin/uv
COPY worker/scripts/ca-env.sh /usr/local/lib/render2go/ca-env.sh
WORKDIR /app
# o orquestrador empacota worker/ e tools/ para os pods (sem .git aqui)
COPY worker/ worker/
COPY tools/ tools/
COPY servico/ servico/
# chmod: o checkout no servidor pode ter vindo com umask 077, e o usuário render2go precisa ler o código
# modelos do aviso de pessoas no /aprovar (pessoas.py): baixados aqui, conferidos por bytes e SHA-256
ENV RENDER2GO_MODELOS_SERVICO=/opt/render2go/modelos-servico
RUN --mount=type=secret,id=extra_ca --mount=type=tmpfs,target=/run/render2go-ca . /usr/local/lib/render2go/ca-env.sh \
    && chmod -R a+rX /app \
    && cd servico && uv sync --frozen --no-dev --no-cache \
    && test -s web/app.js \
    && /opt/venv/bin/python -m render2go_servico.pessoas baixar "$RENDER2GO_MODELOS_SERVICO"
# a pasta de estado já existe com o dono certo: o volume nomeado do compose herda dono e permissões
RUN useradd --system --uid 10001 --create-home render2go \
    && install -d -o render2go -g render2go -m 700 /home/render2go/.cache /home/render2go/.cache/render2go
USER render2go
ENV PATH=/opt/venv/bin:$PATH RENDER2GO_CACHE=/home/render2go/.cache/render2go
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/saude',timeout=4)"
CMD ["render2go-servico", "api"]
