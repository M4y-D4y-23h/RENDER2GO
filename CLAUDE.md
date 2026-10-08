# CLAUDE.md

Projeto Render2Go (antes GPU2GO): serviço de vídeos curtos com IA para pequenos negócios (ver `README.md`; mudanças em `CHANGELOG.md`).
**Status:** Etapas 1 e 2 concluídas em `worker/` e medidas numa RTX 4090 (`worker/evals/gpu-2026-10-05/`). T2V de produção: A14B fp8 + Lightning (ADR 0001, aceito). Etapa 3: código pronto (handler serverless, endpoints como código, bench); imagem montada pelo RunPod a partir do GitHub (ADR 0002, aceito); medições pendentes da primeira build lá. **Caminho escolhido (docs/05): A, serviço de marketing com IA para pequenos negócios**: foco em **lotes de 50+ fotos com um pedido só**, renderizados em vários pods (docs/05, seção 5.1; `lote.py`, `acabamento.py`, presets v2, receitas); fase 2 (`lote_nuvem.py`) validada em GPU; **fase 3 implantada** em `servico/` (API, fila, orquestrador, páginas; ADR 0004 proposto) em https://app.render2go.com (Hetzner, docs/09; 1º lote real em 08/10); aviso de pessoas, histórico, auditoria da autorização, teste gratuito (por dispositivo e por IP, em grupo), plano interno, previsão para o cliente, `/suporte` com e-mail, aviso de cookies e estilo livre; filtro de imagem reservado (ADR 0005 proposto); Etapas 4–6 em pausa. Decisões D1–D5 e D7 da Etapa 0 pendentes (docs/04); D1 (termos do RunPod) segue bloqueando qualquer uso adulto.

## Fontes de verdade

- `referencia/especificacao-pipeline-manual.md`: pipeline manual testado. Não editar.
- `referencia/wan-i2v-com-ollama.api.json`: workflow exportado. Não editar; os templates derivados ficam em `worker/workflows/`.
- `docs/01` a `docs/04`: verificações, arquitetura, segurança e plano. Mudanças de arquitetura viram ADR em `docs/adr/` (0001: modelo T2V, aceito; 0002: hospedagem da imagem, aceito; 0003: arquitetura em escala, aceito; fase 2 = `lote_nuvem.py`; 0004: C1 e revisão humana, proposto; 0005: filtro de imagem nas fotos e quadros, proposto). Qualidade, velocidade e custo dos lotes: `docs/06`; guia técnico consolidado (GPUs, pods, proteções, custos): `docs/07`. Vários clientes ao mesmo tempo, escala e isolamento entre clientes: `docs/08`. Implantação do serviço: `docs/09`.

## Regras técnicas (mudar só com ADR)

- ComfyUI fixado em **v0.38.0**. Template de workflow e versão do ComfyUI mudam juntos.
- Wan2.2 I2V R1 v2: **sem LoRA Lightning**; turbo ligado; 4 passos, CFG 1, troca de expert no passo 2 (8 passos e CFG 1,5–2 testados na Etapa 2: até 3,3× o tempo sem ganho).
- Largura e altura múltiplas de 16; duração inteira de 3 a 7 s a 16 fps (16·d + 1 frames).
- Nunca carregar modelos direto de volume de rede ou de objetos: embutir na imagem ou copiar para o disco local e conferir bytes e SHA-256.
- Em volumes que não são POSIX (Global Volume): sem `rename` nem `chmod`; `rsync --inplace`; conferir bytes depois de copiar.
- Ollama em processo separado do ComfyUI; compilar antes de renderizar; reservar VRAM (`--reserve-vram`).
- PyTorch `+cu128` (driver ≥ 570); não usar as wheels CUDA 13 do PyPI.
- Nos workers, ComfyUI e Ollama só em `127.0.0.1`; nenhuma porta exposta.
- Payload de job sem binário: imagens e vídeos trafegam por URL assinada, só https e só hosts de `RENDER2GO_ALLOWED_URL_HOSTS`.
- Worker sem filtros C1–C2 não processa jobs (`RENDER2GO_SAFETY`); `disabled-sfw-tests` só em endpoints com "teste" no nome.
- Termos dos presets são anexados ao prompt pelo código, nunca reescritos pelo LLM. Preset novo = arquivo novo (`presets.v<N>.json`) que mantém todos os termos da versão anterior. Prompt do compilador novo = arquivo novo (`compiler-<perfil>.v<N>.txt`) + rodada de fidelidade.
- Templates candidatos ficam em `worker/workflows/experimental/` (gerados por `tools/build_t2v_templates.py`); só vão para `worker/workflows/` com ADR aceito.

## Regras de segurança (inegociáveis)

- O compilador é um modelo abliterado: nunca é camada de segurança. A regra `REFUSED` continua nos system prompts.
- Toda verificação obrigatória bloqueia na dúvida (fail-closed).
- Sem upload livre de imagem no modo adulto (política da v1, doc 03, seção 3).
- Repositório, testes, fixtures e CI só com conteúdo SFW. Nunca gerar, buscar ou guardar material explícito ou ilícito para teste.
- Não copiar código do `worker-comfyui` (AGPL-3.0); usar só como referência de protocolo.

## Comandos

- Testes e lint: `cd worker && uv sync --python 3.12 && uv run pytest -q && uv run ruff check . ../tools && uv run ruff format --check . ../tools`
- Serviço (fase 3): `cd servico && uv sync --python 3.12 && uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`; Postgres de teste por `RENDER2GO_TESTE_PG`; páginas: `cd servico/web && npm ci && npm run build` (o `app.js` é versionado; o CI confere) e teste no navegador com `uv run --with playwright==1.56.0 pytest -q tests/test_paginas.py`
- Template em dia com o export: `python tools/derive_i2v_template.py --check`
- Candidatos T2V em dia: `python tools/build_t2v_templates.py --check`
- Dockerfiles do RunPod em dia: `python tools/build_runpod_dockerfiles.py --check` (gerados; nunca editar `infra/runpod/Dockerfile.*` à mão)
- Modo serviço (lote, lote na nuvem e acabamento): ver `worker/README.md`, seção "Modo serviço". Receitas medidas em GPU em 06/10 (`worker/evals/gpu-2026-10-06/`): 6 de 8 aproveitáveis (perfume mudou de cor, fachada parada); compilador acrescenta "Static camera" (corrigir no v6).
- Endpoint de teste e bench: ver `infra/runpod/LEIAME.md`
- Fidelidade do compilador (precisa de Ollama): `python tools/eval_compiler.py --out saida/fid` (ver `worker/README.md`)

## Convenções

- Nome: Render2Go. Pacotes `render2go_worker`/`render2go_servico`, variáveis `RENDER2GO_*` (as antigas `GPU2GO_*` ainda valem, por compatibilidade, em `render2go_worker/__init__.py`). Ficam com o nome antigo, de propósito: bucket R2 `gpu2go`, banco/usuário Postgres `gpu2go`, volumes `gpu2go_*` do compose, endereço do repositório e os registros em `worker/evals/gpu-*`.
- Toda mudança relevante entra no `CHANGELOG.md`.

- Python 3.12 + `uv` + `ruff` + `pytest`; TypeScript no frontend.
- Documentação e textos para o usuário em pt-BR.
- Dependências com versão fixada (tags, commits, hashes).

## Ambiente de nuvem

- O acesso à internet foi liberado em 05/10/2026, mas o tráfego passa por um proxy que reassina TLS. Em builds Docker, passe a CA: `docker build --secret id=extra_ca,src=/root/.ccr/ca-bundle.crt ...` (ver `worker/README.md`).
- O daemon Docker não sobe sozinho: `nohup dockerd > /tmp/dockerd.log 2>&1 &`. O disco é limitado (a imagem `slim` tem ~18 GB): rode `docker builder prune -af` depois das builds. Não há GPU; o alvo `full` e os testes de tempo rodam fora daqui.
- Testes em GPU: pod no RunPod criado pela API com chave fornecida pelo usuário para a sessão (credencial Bearer nas configurações do ambiente, para `*.runpod.io` e `*.runpod.ai`, que o proxy inclui nas chamadas; ou a variável `RUNPOD_API_KEY`), sempre com desligamento automático e encerrado no fim (ver `worker/evals/gpu-2026-10-05/LEIAME.md`). Não guardar chaves no repositório.
- Para a rodada de fidelidade sem GPU: Ollama v0.35.1 em CPU (release conferido por SHA-256, precisa de `zstd`), ~5–15 s por caso no 7B com cache de prefixo.
