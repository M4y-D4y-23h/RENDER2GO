# CLAUDE.md

Projeto GPU2GO: plataforma web de geração de vídeo com IA (ver `README.md`).
**Status:** Etapa 1 (worker) implementada em `worker/`; decisões D1–D5 e D7 da Etapa 0 pendentes (docs/04).

## Fontes de verdade

- `referencia/especificacao-pipeline-manual.md`: pipeline manual testado. Não editar.
- `referencia/wan-i2v-com-ollama.api.json`: workflow exportado. Não editar; os templates derivados ficam em `worker/workflows/`.
- `docs/01` a `docs/04`: verificações, arquitetura, segurança e plano. Mudanças de arquitetura viram ADR em `docs/adr/`.

## Regras técnicas (mudar só com ADR)

- ComfyUI fixado em **v0.38.0**. Template de workflow e versão do ComfyUI mudam juntos.
- Wan2.2 I2V R1 v2: **sem LoRA Lightning**; turbo ligado; 4 passos, CFG 1, troca de expert no passo 2.
- Largura e altura múltiplas de 16; duração inteira de 3 a 7 s a 16 fps (16·d + 1 frames).
- Nunca carregar modelos direto de volume de rede ou de objetos: embutir na imagem ou copiar para o disco local e conferir bytes e SHA-256.
- Em volumes que não são POSIX (Global Volume): sem `rename` nem `chmod`; `rsync --inplace`; conferir bytes depois de copiar.
- Ollama em processo separado do ComfyUI; compilar antes de renderizar; reservar VRAM (`--reserve-vram`).
- PyTorch `+cu128` (driver ≥ 570); não usar as wheels CUDA 13 do PyPI.
- Nos workers, ComfyUI e Ollama só em `127.0.0.1`; nenhuma porta exposta.
- Payload de job sem binário: imagens e vídeos trafegam por URL assinada.

## Regras de segurança (inegociáveis)

- O compilador é um modelo abliterado: nunca é camada de segurança. A regra `REFUSED` continua nos system prompts.
- Toda verificação obrigatória bloqueia na dúvida (fail-closed).
- Sem upload livre de imagem no modo adulto (política da v1, doc 03, seção 3).
- Repositório, testes, fixtures e CI só com conteúdo SFW. Nunca gerar, buscar ou guardar material explícito ou ilícito para teste.
- Não copiar código do `worker-comfyui` (AGPL-3.0); usar só como referência de protocolo.

## Comandos

- Testes e lint: `cd worker && uv sync --python 3.12 && uv run pytest -q && uv run ruff check . ../tools`
- Template em dia com o export: `python tools/derive_i2v_template.py --check`

## Convenções

- Python 3.12 + `uv` + `ruff` + `pytest`; TypeScript no frontend.
- Documentação e textos para o usuário em pt-BR.
- Dependências com versão fixada (tags, commits, hashes).

## Ambiente de nuvem

- O acesso à internet foi liberado em 05/10/2026, mas o tráfego passa por um proxy que reassina TLS. Em builds Docker, passe a CA: `docker build --secret id=extra_ca,src=/root/.ccr/ca-bundle.crt ...` (ver `worker/README.md`).
- O daemon Docker não sobe sozinho: `nohup dockerd > /tmp/dockerd.log 2>&1 &`. O disco é limitado (a imagem `slim` tem ~18 GB): rode `docker builder prune -af` depois das builds. Não há GPU; o alvo `full` e os testes de tempo rodam fora daqui.
