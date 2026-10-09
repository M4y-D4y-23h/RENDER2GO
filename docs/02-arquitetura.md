# 02 — Arquitetura proposta

Pré-requisitos de leitura: [`01-verificacoes.md`](01-verificacoes.md) (fatos conferidos) e [`03-seguranca-e-conformidade.md`](03-seguranca-e-conformidade.md) (camadas de segurança citadas aqui como C1–C9).

## 1. Requisitos e consequências

| Requisito (seção 10.1 da especificação) | Consequência na arquitetura |
|---|---|
| Poucos cliques, sem terminal nem ComfyUI | Imagem Docker pronta (nada é instalado em runtime); ComfyUI vira motor interno, sem porta exposta |
| Cliente escolhe GPU e custo | Tiers de GPU, cada um ligado a um endpoint; estimativa de custo e tempo antes de gerar |
| Qualquer dispositivo | PWA responsiva; jobs assíncronos com notificação, sem conexão longa presa ao celular |
| I2V e T2V a partir de português + presets | Compilador no worker; presets como dados no backend; o usuário digita só a ação |
| Salvar e retomar | PostgreSQL + storage S3 privado; galeria e histórico por usuário |
| Nada de GPU ligada sem uso | Serverless com escala a zero, idle timeout curto e teto de gasto |
| Segurança obrigatória (seção 11) | Moderação em camadas, que bloqueia na dúvida, desde a v1 |

## 2. Opção A (pods sob demanda) × Opção B (serverless)

### Premissas dos números

Estimativas para comparar ordens de grandeza; a Etapa 3 do plano mede os valores reais.

- Geração quente, 640×640, 81 frames, turbo, RTX 4090: **~35 s** (a especificação mediu 37,7 s no 1º vídeo, já incluindo a carga dos modelos).
- Cold start serverless com modelos na imagem: **60–120 s**.
- Boot de pod num host sem a imagem em cache: **5–15 min** (pull de ~50 GB), supondo cobrança desde a criação.
- Preços: serverless 24 GB PRO flex US$ 0,00031/s; pod RTX 4090 secure US$ 0,74/h (US$ 0,000206/s).

### Comparação

| Critério | A — Pods sob demanda | B — Serverless |
|---|---|---|
| Custo de 1 vídeo isolado | **~US$ 0,25** (10 min de boot + 35 s + 10 min até o desligamento por inatividade) | **~US$ 0,05** (90 s de cold start + 35 s + 30 s de idle) |
| Custo de 20 vídeos seguidos | ~US$ 0,39 (US$ 0,020/vídeo) | ~US$ 0,25 (US$ 0,013/vídeo) |
| GPU ociosa cobrando | Até o desligamento automático (minutos) | Só o idle timeout (segundos) |
| Latência do 1º vídeo | Minutos | ~1–2 min |
| Latência com GPU quente | ~35 s | ~35 s + fila |
| Escolha de GPU | Qualquer GPU do catálogo | Por tier (pool de GPUs por endpoint) |
| Vários clientes | Um pod por sessão: isolamento forte, custo alto | Fila compartilhada com escala automática |
| Complexidade | Alta: ciclo de vida, health check, pods órfãos, falta de estoque, detecção de ociosidade, expor e proteger o ComfyUI | Média: handler + configuração do endpoint; fila e escala ficam com o provedor |
| Superfície de ataque | ComfyUI acessível por proxy público e sem autenticação nativa | Nenhuma porta exposta; só a API de jobs, autenticada |
| Modos de falha | Pod volta com "Zero GPU", GPU esgotada | Fila longa, cotas de workers, cold starts |
| Melhor uso | Sessões longas e dedicadas de usuários avançados | Geração por pedido para muitos clientes |

### Recomendação

**Opção B (serverless) como caminho principal**, com três ajustes:

1. **Worker agnóstico de provedor.** O pipeline (compilar → filtrar → renderizar → enviar) é uma biblioteca; o handler do RunPod é só uma porta de entrada. Uma segunda entrada, um **agente "pull"** que busca jobs no backend, roda em qualquer GPU (servidor dedicado, outro provedor). Isso é o plano B se o item 1 do doc 01 (Termos do RunPod) não se resolver.
2. **Pods só para depuração interna** (o modo `pod` da mesma imagem substitui o `start.sh` atual) e, no futuro, para um modo opcional de sessão dedicada (Etapa 8 do plano).
3. **Escolha de GPU vira escolha de tier**, com preço e tempo estimados na tela.

## 3. Visão geral

```
  Celular / computador (PWA)
            │ HTTPS: sessão autenticada, idade verificada
            ▼
 ┌─────────────────────── Backend (API) ───────────────────────┐
 │ contas e idade · jobs e créditos · filtros de texto (C1–C2) │
 │ presets · galeria · denúncias e revisão · adaptador de GPU  │
 └──┬────────────┬──────────────┬──────────────┬───────────────┘
    │            │              │              │ /run + webhook
    ▼            ▼              ▼              ▼
 Postgres   Storage S3     Moderação      Provedor de GPU
 (contas,   privado        externa        ├─ RunPod Serverless
  jobs,     (mídia e       (CSAM, idade,  │  (1 endpoint por tier)
  auditoria) quarentena)   celebridade)   └─ ou agente "pull"
                ▲                              │
                │ URLs assinadas               ▼
                └──────────────────── Worker (imagem única)
                                      handler → Ollama → filtros
                                      → ComfyUI 0.38 → MP4 + quadros
```

| Componente | Papel | Tecnologia proposta |
|---|---|---|
| Frontend | Pedido, presets, tier com estimativa, acompanhamento, galeria, denúncia | PWA em Next.js (TypeScript), mobile-first, pt-BR |
| Backend | Contas, idade, jobs, créditos, filtros C1–C2, moderação C3–C5/C8, webhooks, auditoria | Python 3.12 + FastAPI (mesma linguagem do worker e da biblioteca de segurança) |
| Banco | Usuários, verificações, ativos, jobs, eventos de moderação, denúncias, livro de créditos, auditoria | PostgreSQL |
| Storage | Entradas, saídas, quadros de amostra; bucket de quarentena separado | S3 compatível, privado, criptografado |
| Moderação e idade | CSAM (hash + classificador), estimativa de idade, celebridades, verificação de idade do cliente | Fornecedores externos (doc 03) |
| Adaptador de GPU | `submit`, `status`, `cancel`, recebimento de webhook | Interface com implementações `runpod_serverless` e `agent` |
| Worker | Compilar, filtrar, renderizar, enviar | Imagem Docker única (seção 5) |

A hospedagem de cada peça (backend, storage, CDN) depende de fornecedores que aceitem o caso de uso (doc 03, seção 1).

> **Front-end implementado (08/10/2026):** no serviço de marketing (fase 3, `servico/`), as páginas são TypeScript sem framework, com build próprio (esbuild), em vez do PWA em Next.js previsto acima. Identidade visual, layout e os cuidados de desempenho, acessibilidade e segurança estão no [docs/10](10-identidade-visual-e-front-end.md); a decisão, no [ADR 0006](adr/0006-front-end-build-entrega-e-seguranca.md).

## 4. Fluxo de um job

1. O cliente envia `POST /jobs` com modo (`t2v`, `continuar` ou `i2v` a partir de um ativo), ação em português, presets, duração, resolução e tier.
2. O backend confere sessão, idade verificada, limites de uso e saldo; reserva créditos; aplica C1–C2 ao texto; no I2V, confere se o ativo pertence ao usuário e tem origem permitida (doc 03, seção 3); monta a entrada estruturada do compilador (ação + presets, como na seção 9.3 da especificação).
3. O backend gera URLs assinadas (GET da imagem de entrada, PUT do MP4 e dos quadros), sorteia a seed e chama `POST /run` no endpoint do tier, com `webhook` assinado.
4. O worker baixa a imagem, compila com o Ollama, trata `REFUSED`, aplica C1–C2 ao prompt em inglês, preenche o template, envia ao ComfyUI, acompanha pelo WebSocket, envia MP4 e quadros por PUT e devolve só metadados.
5. No webhook, o backend valida assinatura e job, registra tempos e custo, e aplica C8 aos quadros (CSAM, idade, celebridade) e o hash do vídeo.
6. Se aprovado: job "pronto", créditos debitados, cliente notificado. Se reprovado: nada é entregue; quarentena e fluxo do doc 03.
7. O cliente assiste ou baixa por URL assinada de curta duração.

Exemplo de entrada do job (nada de binário no payload; o limite do `/run` é 10 MB). Implementado em `worker/src/render2go_worker/jobs.py` (Etapa 3):

```json
{
  "input": {
    "job_id": "job_01J9...",
    "mode": "i2v",
    "template": "i2v_r1v2@1",
    "compiler": {
      "action_pt": "ela caminha devagar pela praia",
      "presets": {"camera": "parada-corpo-inteiro-costas", "luz": "por-do-sol"},
      "version": 5,
      "keep_alive": "0"
    },
    "params": {"width": 640, "height": 640, "duration_s": 5, "seed": 731942055},
    "input_image_url": "https://storage/.../entrada.png?assinatura-GET",
    "output_upload_url": "https://storage/.../saida.mp4?assinatura-PUT",
    "frame_upload_urls": ["https://storage/.../q00.jpg?assinatura-PUT"]
  },
  "webhook": "https://api.exemplo/internal/gpu/webhook?sig=..."
}
```

- O worker monta a entrada rotulada a partir da ação e dos presets e anexa os termos dos presets ao prompt (`presets.py`). O backend manda só os ids dos presets.
- `mode`: `i2v` (com `input_image_url`), `continuar` (com `input_video_url`; o worker usa o último quadro) ou `t2v`.
- `compiler.prompt_en` no lugar de `action_pt` pula o compilador. Os filtros C1–C2 valem nos dois casos.
- As URLs precisam ser https e de um host listado em `RENDER2GO_ALLOWED_URL_HOSTS`. Sem essa lista, nenhuma URL é aceita.

Saída do job:

```json
{
  "status": "ok",
  "job_id": "job_01J9...",
  "compiled_prompt": "...",
  "seed": 731942055,
  "timings": {"input_s": 0.4, "compile_s": 3.3, "render_s": 56.2, "upload_s": 0.9, "total_s": 61.0},
  "versions": {"image": "2026.10.1", "comfyui": "0.38.0", "template": "i2v_r1v2@1", "compiler": "i2v@6", "presets": 3},
  "output": {"bytes": 2345678, "sha256": "...", "uploaded": true},
  "frames": 7,
  "worker": {"uptime_s": 312.5, "job_index": 4, "safety": "..."}
}
```

**Erros tipados** (`error.code`, `error.retryable`): `INVALID_INPUT`, `UNSAFE_URL`, `INPUT_INVALID_MEDIA`, `INPUT_DOWNLOAD_FAILED`*, `COMPILER_UNAVAILABLE`*, `COMPILER_OUTPUT`*, `COMFY_UNAVAILABLE`*, `COMFY_VALIDATION`, `COMFY_EXECUTION`*, `TIMEOUT`*, `UPLOAD_FAILED`*, `SAFETY_NOT_CONFIGURED`, `DUPLICATE_IN_PROGRESS`*, `INTERNAL`* (* = pode repetir com o mesmo `job_id`).

- Em erro, o SDK do RunPod marca o job como FAILED e tira `error` da saída. Por isso `error_code` e `retryable` também ficam na saída. Esse comportamento foi conferido com o SDK 1.12.0.
- `COMFY_UNAVAILABLE` pede um worker novo ao RunPod (`refresh_worker`).
- Um `job_id` já concluído no mesmo worker devolve o resultado guardado, com `"replayed": true`.

`status` pode ser `ok`, `refused` (compilador ou filtro bloqueou; o motivo vai em `refusal`) ou `error`.

## 5. Worker

### 5.1 Uma imagem, três modos

| `MODE` | Entrada | Uso |
|---|---|---|
| `serverless` | Handler do RunPod (push) | Produção |
| `agent` | Busca jobs no backend (pull) | Qualquer GPU fora do RunPod (Etapa 8) |
| `pod` | Interface do ComfyUI para depuração | Uso interno; substitui o `start.sh`; nunca exposta a clientes |

### 5.2 Conteúdo da imagem

- Base `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04`, Python 3.12, `uv`.
- PyTorch `+cu128` fixado (2.8.0 testado; 2.11.0 por teste A/B), pelo motivo do item 15 do doc 01.
- ComfyUI **v0.38.0** fixado (item 9 do doc 01).
- Só o custom node necessário: **ComfyUI-UMT5-Extended**, conferido por hash. Ficam fora da imagem de produção: ComfyUI-Manager (instala código em runtime), comfyui-ollama (o handler chama o Ollama direto), VideoHelperSuite (o workflow usa `CreateVideo`/`SaveVideo` nativos), GGUF e Frame-Interpolation (entram só se o T2V GGUF ou o RIFE forem adotados).
- Ollama em versão fixada, com `huihui_ai/qwen2.5-abliterate:7b` baixado na build.
- Modelos de vídeo na imagem (alvo `full`) ou ausentes (alvo `slim`), listados num `models.lock.json` com URL, pasta, bytes e SHA-256.

### 5.3 Subida

1. Checa a GPU com um kernel real (como o `worker-comfyui` faz) e falha cedo se a build do torch não tiver kernels para a arquitetura.
2. Sobe o Ollama como processo separado e supervisionado (o papel do `setsid` de hoje) e espera ficar pronto.
3. Sobe o ComfyUI em `127.0.0.1` com `--reserve-vram ${RESERVE_VRAM_GB}` e `--disable-metadata` (o ComfyUI embute workflow e prompt no MP4; a marcação de proveniência é feita de forma controlada, doc 03 seção 10).
4. Espera `GET /system_stats` responder e só então aceita jobs.

### 5.4 Estratégia de VRAM

- O job compila **antes** de renderizar, então Ollama e Wan não disputam a GPU ao mesmo tempo.
- Tier de 24 GB: `keep_alive=0` (o Qwen descarrega logo após compilar) e `--reserve-vram` de 4 a 5.
- Tiers de 32 GB ou mais: `keep_alive` de 10 min e `--reserve-vram 6` (configuração testada).
- Critério de aceite: o log do Ollama mostra todas as camadas na GPU (`29/29`) e a compilação leva menos de 3 s.
- Se a VRAM de 24 GB não comportar, o compilador vai para um endpoint separado e barato (16 GB). Fica como evolução, não como v1.

### 5.5 Onde ficam os modelos

| Estratégia | Cold start | Imagem | Trocar modelo | Uso |
|---|---|---|---|---|
| Embutidos na imagem | Rápido depois do pull; 1º pull lento em cada host | ~50 GB | Rebuild | **v1** |
| Cached models do RunPod | Rápido; download não cobrado | ~12 GB | Trocar a referência | **Descartado** na Etapa 3: um repositório do HF por endpoint, baixado inteiro (ADR 0002) |
| Volume + cópia para o disco local na subida | +~3 min **cobrados** a cada cold start | ~12 GB | Copiar para o volume | Só modo `pod` |
| Leitura direta do volume | ~14 min por vídeo (medido) | — | — | **Nunca** |

Arquivos de 14,8 GB viram camadas enormes: escolher um registry que aceite camadas desse tamanho e medir o tempo de pull. O GHCR não aceita (10 GB por camada); as opções estão no ADR 0002.

### 5.6 Falhas

- Se o ComfyUI ou o Ollama morrer, o worker falha o job e encerra; o provedor sobe outro worker.
- O backend repete o job com o mesmo `job_id` (idempotência) até um limite, e reembolsa se não der.
- Execution timeout por tier (ex.: 300 s) para não pagar por job travado.

## 6. Workflow como template versionado

- `worker/workflows/i2v_r1v2.v1.api.json`: derivado do export, **sem os nós `165` e `166`** (Ollama); a entrada `text` de `129:93` passa a ser texto literal.
- Um arquivo de contrato mapeia cada parâmetro para `[nó, entrada]` e define limites. O código nunca edita o JSON "na mão".

| Parâmetro | Nó | Entrada | Regra |
|---|---|---|---|
| `image` | `97` (LoadImage) | `image` | Nome do arquivo enviado por `POST /upload/image` |
| `prompt_en` | `129:93` (CLIPTextEncode) | `text` | Saída do compilador, após C6–C7 |
| `negative` | `129:89` | `text` | Vazio (sem efeito com CFG 1) |
| `width`, `height` | `129:98` (WanImageToVideo) | `width`, `height` | Múltiplos de 16 (exigência do nó); área ≤ 921.600 px (720p) |
| `duration_s` | `129:161` | `value` | Inteiro de 3 a 7 → 49, 65, 81, 97 ou 113 frames (16·d + 1, sempre 4k + 1) |
| `fps` | `129:162` | `value` | Fixo em 16 |
| `seed` | `129:86` (KSampler high) | `noise_seed` | Sorteada por job e registrada |
| `turbo` | `129:131` | `value` | Fixo `true` para o R1 v2 |
| passos / CFG / troca | `129:118` / `129:122` / `129:124` | `value` | 4 / 1 / 2 (variante de qualidade 8 / 1 / 4 em teste) |
| modelos | `129:95`, `129:96`, `129:84`, `129:90` | nomes de arquivo | Fixos, conferidos com o `models.lock.json` |
| saída | `108` (SaveVideo) | `filename_prefix` | `video/{job_id}` |

- O CI valida o template contra o `/object_info` de um ComfyUI 0.38.0 rodando em CPU (os nós e as entradas precisam existir).
- **T2V:** decidido na Etapa 2, comparando (a) R1 v2 com imagem em branco (zero GB extra) e (b) Wan 2.2 T2V A14B (GGUF Q6_K ou fp8) com as LoRAs Lightning T2V v1.1 (+24 a 28 GB na imagem).

## 7. Compilador de prompts

- Os system prompts saem dos Modelfiles e viram **arquivos de texto versionados** (`worker/prompts/compiler-<perfil>.v<N>.txt`; em produção I2V v6, desde 09/10/2026, e T2V v3, ver `worker/evals/compiler/RESULTADOS.md`), enviados a cada chamada pelo campo `system` da API do Ollama, com `temperature 0.3` e `num_ctx 4096`. Não é mais preciso `ollama create`.
- O job indica `profile` e `version`, e a saída registra qual versão foi usada.
- O backend monta a entrada estruturada da seção 9.3 a partir da ação digitada e dos presets escolhidos. Os termos em inglês dos presets são **anexados ao prompt pelo código**, palavra por palavra: no teste, o LLM parafraseava ou contradizia termos de câmera ("de baixo" virou "high angle").
- Saída: normalizar espaços, exigir um parágrafo dentro da faixa de palavras; `REFUSED` encerra o job como recusado.
- **O compilador é um modelo abliterado: nunca é camada de segurança.** A regra `Rules: ... REFUSED` continua no prompt (seção 6.3 da especificação), mas quem decide são C1–C2 e C7–C8.
- Etapa 2 (feita): regra de conteúdo adulto e glossário de câmera no perfil T2V; comparação 7B × 14B (o 14B não compensa: ~9 GB de VRAM sem ganho consistente). Saída fora do inglês é refeita uma vez com outra seed.

## 8. Tiers, estimativa e cobrança

Proposta inicial; os benchmarks da Etapa 3 confirmam ou mudam.

| Tier | Pool | Resoluções | Duração |
|---|---|---|---|
| Econômico | 24 GB PRO (RTX 4090) | 640×640, 832×480, 480×832 | 3–5 s |
| Padrão | 32 GB PRO (RTX 5090 / PRO 4500) | + 1280×720, 720×1280 | 3–7 s |
| Máximo | 48 GB PRO ou 80 GB | idem | 3–7 s |

- **Estimativa mostrada ao cliente** = tempo p50 medido (tier × resolução × duração) × preço por segundo do tier (catálogo) × (1 + margem) + custos fixos (moderação, armazenamento), convertida em créditos.
- **Cobrança por tabela, não por segundos brutos:** o cliente sabe o preço antes; a variação fica na margem.
- Créditos reservados no envio, debitados na entrega; falha do sistema devolve.
- **Tetos de gasto:** limite diário por usuário; limite global diário (o backend recusa novos jobs ao atingir); `max workers` por endpoint.

## 9. Armazenamento e entrega

- Bucket de mídia privado: `entradas/`, `saidas/`, `quadros/`, separados por usuário. Bucket de **quarentena** separado, criptografado, com acesso restrito (doc 03).
- O worker não tem credenciais de storage: só recebe URLs assinadas válidas para aquele job.
- Entrega por URL assinada de poucos minutos; nada público, nenhum link permanente na v1.
- Cada saída guarda SHA-256 e hash perceptual (para remoção de cópias e denúncias).

## 10. Operação

- Métricas: tempo de fila, cold start, tempo de render por tier, taxa de falha, taxa de recusa, taxa de bloqueio na moderação, custo por job, gasto diário.
- Alertas: tetos de gasto; queda automática do `max workers` após 3 ou 7 dias sem uso (item 13 do doc 01); falha de webhook; indisponibilidade de fornecedor de moderação (que segura os jobs, doc 03).
- Prompts e conteúdo ficam no banco, criptografados e com acesso controlado, **não** nos logs da aplicação.

## 11. Do fluxo manual para a imagem

| Hoje (`start.sh` / `save.sh`) | Na plataforma |
|---|---|
| `apt-get` e `pip` a cada sessão (15–20 min) | Na build da imagem |
| `git clone` do ComfyUI e de 5 extensões | ComfyUI v0.38.0 + só o patch UMT5, versões fixadas |
| `unzip` do patch | Na build, com hash conferido |
| `extra_model_paths.yaml` | Desnecessário com modelos na imagem; mantido no modo `pod` para ler do volume |
| `rsync` dos modelos com `--ignore-existing` | Modelos na imagem, ou cópia `--inplace` com conferência de bytes no modo `pod` |
| `curl .../install.sh` do Ollama | Binário fixado na build, modelo baixado na build |
| `ollama serve` com `setsid` | Processo supervisionado pelo entrypoint, independente do ComfyUI |
| `ollama create compilador-*` | Desnecessário: system prompt enviado por chamada |
| Cópia dos workflows | Templates versionados no repositório |
| `--listen 0.0.0.0` | `127.0.0.1`; nada exposto |
| `save.sh` | Upload por URL assinada ao fim de cada job |

## 12. Riscos técnicos

| Risco | Mitigação |
|---|---|
| Termos do provedor (item 1 do doc 01) | Worker agnóstico + agente pull; decisão na Etapa 0 |
| Imagem de ~50 GB (pull lento, limite de camada) | Testar registry na Etapa 1; avaliar cached models na Etapa 3 |
| Cold start alto | FlashBoot; worker ativo em horário de pico (desconto de até ~30%); cached models |
| Ollama e Wan disputando 24 GB | Compilar antes; `keep_alive=0`; plano de endpoint separado |
| Mudança de versão do ComfyUI quebrar o template | Versões fixadas; template validado no CI |
| Licença do encoder NSFW (item 3 do doc 01) | Encoder oficial como alternativa medida |
