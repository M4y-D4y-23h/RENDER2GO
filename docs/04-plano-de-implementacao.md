# 04 — Plano de implementação em etapas

## Princípios

- Cada etapa termina com um entregável verificável e critérios de aceite.
- **Desenvolvimento e testes só com conteúdo SFW** (pessoas adultas vestidas, cenas comuns). Nada explícito ou ilícito no repositório, nos testes ou no CI. O pipeline técnico é o mesmo, e isso também evita conflito com os termos de fornecedores durante o desenvolvimento.
- Três portões que não se pulam:
  - **G0:** provedor de GPU resolvido (Etapa 0) antes de qualquer uso adulto em produção.
  - **G1:** ninguém de fora usa nada antes das Etapas 4 e 5 e do parecer jurídico.
  - **G2:** beta privado só com o checklist do doc 03 (seção 13) completo.

```
Etapa 0 (decisões) ──────────────────────────────┐ G0
Etapa 1 (imagem + script) ─► Etapa 2 (templates, T2V) ─► Etapa 3 (serverless + medições)
                                                              │
               Etapa 4 (segurança + backend) ◄────────────────┘
                     │
               Etapa 5 (contas, idade, pagamentos) ── G1
                     │
               Etapa 6 (PWA) ─► Etapa 7 (operação e conformidade) ── G2 ─► beta privado
                                                              Etapa 8 (opcional)
```

## Etapa 0 — Decisões (sem código; depende de você)

| # | Decisão | Recomendação |
|---|---|---|
| D1 | Provedor de GPU | Pedir resposta por escrito ao RunPod (rascunho no fim deste documento). Se negar ou não responder, escolher um provedor que aceite por escrito; o worker é agnóstico |
| D2 | Imagens no I2V | v1 só com ativos gerados na plataforma (doc 03, seção 3) |
| D3 | Mercado inicial | Só Brasil |
| D4 | Fornecedores de verificação de idade, moderação e pagamentos | Pedir propostas confirmando que atendem o setor adulto |
| D5 | Advogado(a) de direito digital | Contratar antes da Etapa 5 |
| D6 | Encoder de texto | **Resolvida:** o NSFW-UMT5 é Apache-2.0 (doc 01, item 3) |
| D7 | Arquitetura | Opção B (serverless) com worker agnóstico (doc 02) |

As Etapas 1 e 2 podem andar em paralelo à Etapa 0, porque só usam conteúdo SFW.

## Etapa 1 — Imagem do worker e script de envio (ponto de partida pedido)

> **Status (05/10/2026): implementada**, ver [`worker/README.md`](../worker/README.md). Verificado sem GPU: build do alvo `slim` com teste de fumaça; patch UMT5 carregando; template validado contra o `/object_info` do ComfyUI 0.38.0 real; entrypoint subindo Ollama e ComfyUI; `submit_workflow.py` contra o ComfyUI real (upload aceito, única recusa = os 4 modelos ausentes) e com o compilador real (doc 01, item 20); 42 testes. **Falta, porque exige GPU:** build do alvo `full` (~37 GB de download) e os critérios de tempo (≤ ~40 s numa RTX 4090, compilação < 3 s). Passos para isso no README do worker.

**Objetivo:** reproduzir o `start.sh` numa imagem Docker sem nenhuma instalação em runtime, e ter um script que envia o workflow em formato API ao ComfyUI e devolve o MP4.

**Entregáveis**

1. `worker/Dockerfile` em vários estágios:
   - `base`: CUDA 12.8 runtime, Python 3.12, `uv`, torch 2.8.0+cu128, ComfyUI **v0.38.0**, dependências, patch UMT5 (hash conferido), Ollama em versão fixada, `ffmpeg`.
   - `slim`: `base` + entrypoint + código do worker + modelo do compilador (Qwen 7B), **sem** modelos de vídeo.
   - `full`: `slim` + modelos de vídeo do `models.lock.json`, uma camada por arquivo.
   - Teste de fumaça na build: `python main.py --quick-test-for-ci --cpu` e a linha `[ComfyUI-UMT5-Extended] Loaded` no log.
2. `worker/models.lock.json` e `tools/fetch_models.py`: baixa com retomada, confere os bytes da seção 2 da especificação e o SHA-256 (registrado no primeiro download); serve para a build e para o modo `pod` (cópia `--inplace` a partir do volume, como na seção 4.4).
3. `worker/entrypoint.sh` com os modos `pod` e `serverless` (o `agent` fica para a Etapa 8): checagem de GPU, Ollama supervisionado, ComfyUI com `--reserve-vram`, espera por `/system_stats`.
4. `worker/workflows/i2v_r1v2.v1.api.json` (export sem os nós `165`/`166`), `i2v_r1v2.v1.contract.json` (doc 02, seção 6) e `gpu2go_worker/template.py`, que preenche e valida.
5. `tools/submit_workflow.py`:

   ```bash
   python tools/submit_workflow.py \
     --comfy http://127.0.0.1:8188 \
     --template worker/workflows/i2v_r1v2.v1.api.json \
     --image fixtures/sfw/pessoa-caminhando.png \
     --text-pt "ela caminha em direção à câmera, câmera parada" \
     --ollama http://127.0.0.1:11434 --profile i2v \
     --width 640 --height 640 --duration 5 --seed 42 \
     --out saida/teste.mp4
   ```

   Aceita `--prompt-en "..."` para pular o compilador. Sequência: `POST /upload/image` → preenche o template pelo contrato → `POST /prompt` com `client_id` → acompanha `/ws` → `GET /history/{id}` → acha a saída marcada `animated` → `GET /view` → grava o MP4. Imprime prompt compilado, seed e tempos; usa códigos de saída diferentes para recusa, erro de validação e erro de execução.
6. Testes sem GPU: preenchimento e validação do template (dimensões múltiplas de 16, duração → 4k+1, nós e entradas existentes) e leitura de uma resposta real do `/history` gravada como fixture.
7. CI no GitHub Actions: `ruff`, testes e build do alvo `slim` sem GPU.

**Critérios de aceite**

- `docker build --target slim` passa no CI, com o teste de fumaça do ComfyUI e a linha do patch UMT5.
- Num pod com GPU, a imagem `full` sobe em menos de 2 min sem baixar nada; o log mostra `[ComfyUI-UMT5-Extended] Loaded` e, ao gerar, `vocab_size 257067`.
- `submit_workflow.py` gera um MP4 de 5 s em 640×640 a partir de uma imagem SFW; numa RTX 4090 quente, em até ~40 s (referência: 37,7 s).
- A compilação no Ollama leva menos de 3 s, com todas as camadas na GPU.
- `fetch_models.py` falha se os bytes divergirem da especificação.

## Etapa 2 — Templates, compilador e T2V

- **Presets como dados** (`worker/presets/`): ritmo, câmera, enquadramento, luz e estilo, a partir da seção 9.3.
- **Prompts versionados** (`worker/prompts/`): `compiler-i2v.v3` (o atual) e `compiler-t2v.v2`, com a regra de conteúdo adulto e o glossário de câmera (pendência da seção 12).
- **Testes de fidelidade do compilador:** entrada em pt → propriedades esperadas (termo de câmera correto, nada inventado, ordem das ações, texto entre aspas preservado). Comparar 7B × 14B.
- **Bake-off do T2V:** R1 v2 com imagem em branco × Wan 2.2 T2V A14B (GGUF Q6_K ou fp8) com as LoRAs Lightning T2V v1.1. Critérios: aderência ao prompt, qualidade do movimento, tempo, VRAM e GB a mais na imagem. Decisão registrada em `docs/adr/`.
- **"Continuar vídeo":** extração do último quadro no worker e encadeamento sem recodificar, com limite de 3 a 4 elos (seção 9.2).
- **Qualidade do turbo:** testar 8/1/4 e CFG 1,5–2 (pendência da seção 12).
- Opcional: texto → imagem com o Wan T2V de 1 quadro, para o futuro "animar imagem gerada".

**Aceite:** o template T2V escolhido gera 5 s; a taxa mínima de acerto nos testes de fidelidade está definida e atingida; ADR do T2V registrado.

## Etapa 3 — Serverless em um tier e medições

- **Handler próprio** (`handler_runpod.py`), com o protocolo do doc 02 (seção 4): URLs assinadas, quadros de amostra, erros tipados, idempotência por `job_id`.
- **Endpoint como código** (`infra/runpod/`), criado pela API v2: `allowedCudaVersions` a partir de 12.8, execution timeout, idle timeout, FlashBoot.
- **`tools/bench.py`:** cold start, tempo quente, p50/p95 por resolução e duração, custo por vídeo.
- Comparar modelos embutidos × cached models e escolher o registry.
- Tabela de custo por tier, que alimenta a precificação.

**Aceite:** 50 jobs SFW seguidos sem falha; números de cold start e custo por vídeo publicados no repositório. **G0** precisa estar resolvido antes de qualquer uso adulto em produção.

## Etapa 4 — Segurança e backend mínimo

- **`packages/safety`:** C1 (normalização, listas pt/en/es, expressões de idade) e a interface de C2, com casos de teste sintéticos de bloqueio e de não bloqueio.
- **Backend (FastAPI):** contas (sem pagamento ainda), API de jobs, adaptadores `runpod_serverless` e `fake` (para testar sem GPU), webhook assinado, URLs assinadas, integração C2/C4/C5/C8, quarentena, trilha de auditoria, limites de requisição, livro de créditos.
- **Banco:** `users`, `age_verifications`, `assets` (origem, dono, hashes), `jobs` (parâmetros, prompt compilado, decisões, tempos, custo), `moderation_events`, `reports`, `credit_ledger`, `audit_log`.

**Aceite:** job ponta a ponta com o adaptador `fake` e com o endpoint real (SFW); cada camada com testes; fornecedor de moderação fora do ar → job retido, não entregue.

## Etapa 5 — Contas, idade, créditos e pagamentos

- Verificação de idade por fornecedor; toda tela de geração e galeria bloqueada para quem não verificou.
- Processador de pagamentos com créditos, webhooks e reembolsos.
- Termos de uso, política de privacidade e consentimentos da LGPD, aprovados pelo advogado. **G1.**

## Etapa 6 — Frontend PWA

- Telas: entrada e verificação de idade; novo vídeo (modo, ação, presets em botões, duração, resolução, tier com preço e tempo estimados); acompanhamento (fila e progresso); galeria privada (assistir, baixar, apagar, continuar, variar a seed); créditos; denúncia; privacidade (exportar e apagar dados).
- Mobile-first, pt-BR, robusta em conexão lenta (jobs assíncronos e aviso ao concluir).

## Etapa 7 — Operação e conformidade → beta privado

- Formulário de denúncia e fila de revisão humana; roteiros de CSAM e de imagem íntima não consentida, com ensaio; rotinas de retenção e exclusão; métricas e alertas (gasto, queda do `max workers`, webhooks); marcação de IA (C2PA + marca d'água); teste de invasão.
- Checklist do doc 03 (seção 13) completo. **G2** → beta privado com convidados verificados.

## Etapa 8 (opcional) — Sessões dedicadas e outros provedores

- Agente pull (`MODE=agent`) para GPUs fora do RunPod.
- Sessões dedicadas em pod (Opção A) para usuários avançados, com autenticação e desligamento automático.

## Estrutura do repositório

```
GPU2GO/
├── README.md
├── CLAUDE.md                        # regras para sessões do Claude Code
├── docs/                            # esta proposta; docs/adr/ para decisões registradas
├── referencia/                      # especificação original e workflow exportado (não editar)
├── worker/
│   ├── Dockerfile                   # alvos base / slim / full
│   ├── entrypoint.sh                # MODE=pod | serverless | agent
│   ├── models.lock.json             # url, pasta, bytes e sha256 de cada modelo
│   ├── comfyui.lock                 # tag do ComfyUI, commits dos custom nodes, hash do patch
│   ├── workflows/                   # templates API + contratos de parâmetros
│   ├── prompts/                     # system prompts do compilador, versionados
│   ├── presets/                     # ritmo, câmera, enquadramento, luz, estilo
│   ├── src/gpu2go_worker/
│   │   ├── pipeline.py              # compilar → filtrar → renderizar → enviar
│   │   ├── comfy_client.py          # /upload/image, /prompt, /ws, /history, /view
│   │   ├── ollama_client.py
│   │   ├── template.py              # preenche e valida pelo contrato
│   │   ├── handler_runpod.py        # entrada serverless
│   │   └── agent.py                 # entrada pull (Etapa 8)
│   └── tests/
├── packages/safety/                 # C1 + interface de C2; usado pelo worker e pelo backend
├── backend/
│   ├── src/gpu2go_api/              # routes, jobs, providers, moderation, billing, storage, auth
│   ├── migrations/
│   └── tests/
├── frontend/                        # PWA (Next.js)
├── tools/
│   ├── submit_workflow.py           # envia o workflow API ao ComfyUI e baixa o MP4
│   ├── fetch_models.py              # baixa e confere bytes e sha256
│   └── bench.py                     # cold start, tempo e custo por tier
├── fixtures/sfw/                    # só conteúdo SFW
├── infra/
│   ├── docker-compose.yml           # dev: backend + postgres + storage local (+ worker com GPU)
│   └── runpod/                      # definições dos endpoints
└── .github/workflows/               # lint, testes, validação de template, build do alvo slim
```

## Convenções

- Python 3.12 com `uv`, `ruff` e `pytest`; TypeScript no frontend.
- Toda dependência com versão fixada: tag do ComfyUI, commits dos custom nodes, lock do Python com hashes, SHA-256 dos modelos.
- Decisões relevantes em `docs/adr/NNNN-titulo.md`.
- Segredos nunca no repositório (só `.env.example`).
- Fixtures só SFW.

## Rascunho da consulta ao RunPod (D1)

Enviar pelo suporte ou pelo time comercial e guardar a resposta.

> **Subject:** Acceptable use question: AI video generation platform with adult (18+) content
>
> Hello RunPod team,
>
> We are building a web platform, based in Brazil, that generates short AI videos with ComfyUI and Wan 2.2 on RunPod Serverless. Part of what our users generate is sexually explicit content depicting fictional adult characters only. Your Terms of Service list "pornography or graphic adult content" among unauthorized content, so before building on RunPod we would like to confirm whether this use case is allowed.
>
> Safeguards planned from day one:
> - Reliable age verification for every user, with no self-declaration, as required by Brazilian law (Lei 15.211/2025).
> - No uploads of photos of real people for adult content: image-to-video only starts from images generated on our platform.
> - Layered moderation of prompts, input images and every output video, including CSAM hash matching and classifiers, age estimation and celebrity detection. Suspected CSAM is blocked, preserved and reported to the authorities.
> - No public galleries: outputs are private to each user and delivered through short-lived signed URLs.
> - No public ports on workers; outputs go straight to our own storage.
>
> Questions:
> 1. Is this use case permitted on RunPod (Serverless and Pods)? If so, under which conditions or agreement?
> 2. May we offer generation to our own customers as part of our product (resale of compute)?
> 3. Are there extra requirements, such as an enterprise agreement, Secure Cloud only, or specific regions?
>
> We would appreciate a written answer that we can keep on file.
>
> Best regards,
> [nome / empresa]
