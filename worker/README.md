# Worker

Imagem Docker que substitui o `start.sh` do pipeline manual, e o script que envia o workflow ao ComfyUI e devolve o MP4 (Etapa 1 do [plano](../docs/04-plano-de-implementacao.md)). Da Etapa 2: presets de cena, compilador versionado com testes de fidelidade, "continuar vídeo" e as ferramentas dos experimentos em GPU (bake-off do T2V e turbo).

## O que tem na imagem

| Componente | Versão fixada | Conferência |
|---|---|---|
| Base | `ubuntu:24.04` (digest) + Python 3.12 | — |
| PyTorch | 2.8.0+cu128 (driver ≥ 570) | `requirements.lock` com hashes |
| ComfyUI | v0.38.0 (`6b747c04`) | commit conferido na build |
| ComfyUI-UMT5-Extended | zip da revisão `f89480ab` do HF | SHA-256 |
| Ollama | v0.35.1 | SHA-256 do release |
| Compilador | `huihui_ai/qwen2.5-abliterate:7b` | digest da camada de pesos |
| Modelos de vídeo (alvo `full`) | [`models.lock.json`](models.lock.json) | bytes + SHA-256 |

Fica de fora da produção: ComfyUI-Manager, comfyui-ollama, VideoHelperSuite, GGUF e Frame-Interpolation (ver `docs/02-arquitetura.md`, seção 5.2). O ComfyUI-GGUF só entra no alvo `bakeoff` (commit fixado), e na produção apenas se o ADR 0001 escolher o T2V em GGUF.

## Build

Contexto de build: a pasta `worker/`.

```bash
# sem modelos de vídeo (~18 GB sem o Qwen; para pods com volume ou MODELS_FETCH=1)
docker build -f worker/Dockerfile --target slim -t gpu2go-worker:slim worker

# com os modelos de vídeo embutidos (+~37 GB, uma camada por arquivo)
docker build -f worker/Dockerfile --target full -t gpu2go-worker:full worker

# só para o bake-off do T2V: slim + ComfyUI-GGUF (modelos entram em runtime)
docker build -f worker/Dockerfile --target bakeoff -t gpu2go-worker:bakeoff worker
```

- A build do `slim` já roda um teste de fumaça em CPU: o ComfyUI importa todos os nós, o patch UMT5 precisa logar `Loaded`, e os templates de `workflows/` são validados contra o `/object_info` dessa versão do ComfyUI.
- `--build-arg WITH_COMPILER_MODEL=0` pula o download do Qwen (usado no CI).
- **Atrás de proxy que reassina TLS** (corporativo ou de sandbox): `--secret id=extra_ca,src=/caminho/ca.pem`. A CA só é usada durante a build e não fica na imagem.
- Os arquivos de 14,8 GB viram camadas enormes no alvo `full`: o registry precisa aceitar camadas desse tamanho.

## Executar (modo `pod`)

```bash
docker run --gpus all -e PUBLIC_KEY="$(cat ~/.ssh/id_ed25519.pub)" -p 22:22 gpu2go-worker:full
ssh -L 8188:127.0.0.1:8188 root@<host>      # o ComfyUI só escuta em 127.0.0.1
```

| Variável | Padrão | Uso |
|---|---|---|
| `MODE` | `pod` | `serverless`: sobe o handler do RunPod depois do ComfyUI (ver "Modo serverless") |
| `AUTO_TERMINATE_HOURS` | `0` | No RunPod: o próprio pod se encerra depois de N horas (o `runpod_pod.py subir` usa 3) |
| `TERMINATE_ON_BAD_GPU` | `1` | No RunPod: encerra o pod se a GPU falhar na checagem |
| `MODEL_SET` | `i2v_r1v2` | Conjuntos do `models.lock.json`, separados por vírgula (ex.: `i2v_r1v2,t2v_a14b_q6k` no bake-off) |
| `MODELS_FROM_DIR` | — | Copia os modelos que faltarem deste diretório (ex.: `/workspace/models` do volume), por leitura sequencial, para o disco local |
| `MODELS_FETCH` | `0` | `1` baixa do Hugging Face os modelos que faltarem |
| `VERIFY_SHA` | `0` | `1` confere SHA-256 na subida (mais lento; os bytes sempre são conferidos) |
| `RESERVE_VRAM_GB` | `6` | VRAM livre para o Ollama; use 4 em GPUs de 24 GB |
| `COMFY_LISTEN` | `127.0.0.1` | Não exponha o ComfyUI: ele não tem login |
| `PUBLIC_KEY` | — | Liga o `sshd` para o túnel |

Com a imagem `slim` num pod com o Global Volume montado: `-e MODELS_FROM_DIR=/workspace/models` (~3 min para 37 GB, como no `start.sh`).

O entrypoint confere a GPU com um kernel real, confere ou copia os modelos, sobe o Ollama e o ComfyUI como processos separados e encerra o container se qualquer um dos dois cair.

## Modo serverless (Etapa 3)

Com `MODE=serverless`, o entrypoint sobe o Ollama e o ComfyUI em 127.0.0.1 e depois o handler (`gpu2go_worker/handler_runpod.py`, SDK oficial `runpod`). O protocolo do job (entrada, saída e erros tipados) está no doc 02, seção 4, e é implementado em `gpu2go_worker/jobs.py`.

| Variável | Obrigatória | Uso |
|---|---|---|
| `GPU2GO_SAFETY` | sim | Filtros C1–C2. Sem ela, todo job é recusado (`SAFETY_NOT_CONFIGURED`). `disabled-sfw-tests` só para testes e medições SFW |
| `GPU2GO_ALLOWED_URL_HOSTS` | sim | Hosts das URLs assinadas (ex.: `storage.exemplo.com,.r2.exemplo.com`). Só https |
| `GPU2GO_IMAGE_VERSION` | não | Tag da imagem, registrada na saída de cada job |
| `JOB_TIMEOUT_S` | não | Limite do render (padrão 600) |
| `GPU2GO_ALLOW_DISCARD_OUTPUT` | não | `1` permite `discard_output` (só no endpoint de teste do bench) |

Para testar o handler localmente, sem GPU (o resultado esperado sem modelos é um erro tipado do ComfyUI):

```bash
COMFY_PORT=8188 GPU2GO_SAFETY=disabled-sfw-tests GPU2GO_ALLOWED_URL_HOSTS=storage.example \
GPU2GO_ALLOW_DISCARD_OUTPUT=1 python -m gpu2go_worker.handler_runpod --test_input \
  '{"input": {"job_id": "j1", "mode": "t2v", "template": "t2v_a14b_fp8_lightning@1",
   "compiler": {"prompt_en": "A red ball rolls."}, "discard_output": true,
   "params": {"width": 320, "height": 320, "duration_s": 3, "seed": 42}}}'
```

Endpoints, bench e custos: [`../infra/runpod/LEIAME.md`](../infra/runpod/LEIAME.md) e [`evals/custos.md`](evals/custos.md).

## Subir um pod no RunPod (secure)

```bash
# estoque e preço agora (não precisa de chave)
python tools/runpod_pod.py disponibilidade --gpu "NVIDIA GeForce RTX 4090" --gpu "NVIDIA GeForce RTX 5090"

# cria o pod na nuvem secure; sem vaga, tenta a próxima GPU
export RUNPOD_API_KEY=...        # nas configurações do ambiente, nunca no código
python tools/runpod_pod.py subir --imagem <registry>/gpu2go-worker:full \
  --gpu "NVIDIA GeForce RTX 4090" --gpu "NVIDIA GeForce RTX 5090" \
  --auto-encerrar-horas 3
python tools/runpod_pod.py listar
python tools/runpod_pod.py encerrar <id>
```

- **Só secure** (decisão de 07/10/2026): os hosts community de RTX 4090 que sorteamos falharam no CUDA (`cuInit` = 999) em 3 de 3 tentativas. `--nuvem community` ou `qualquer` continuam existindo só para teste explícito.
- O `stockStatus` (High/Medium/Low) é só um indicativo. A disponibilidade de verdade é decidida no ato da criação: sem vaga, a opção seguinte é tentada. Um pod que não sobe em `--espera` segundos é encerrado antes da próxima tentativa.
- Só máquinas com CUDA ≥ 12.8 (driver ≥ 570). A única porta é a 22 (SSH); o ComfyUI e o Ollama continuam em 127.0.0.1.
- **Desligamento automático:** o entrypoint encerra o próprio pod depois de `AUTO_TERMINATE_HOURS`, usando a chave que o RunPod injeta no pod. Se a GPU falhar na checagem (como num host community com `cuInit` quebrado), o pod também se encerra (`TERMINATE_ON_BAD_GPU=1`).
- Exige a imagem do worker num registry acessível pelo RunPod.
- Ainda não validado com chave real: a criação via REST e o `podTerminate` feito com a chave do próprio pod. Esse caminho só é confirmado na primeira subida; o `podStop` é a alternativa.

## Enviar um workflow

```bash
python tools/submit_workflow.py \
  --comfy http://127.0.0.1:8188 \
  --template worker/workflows/i2v_r1v2.v1.api.json \
  --image fixtures/sfw/padrao-teste-640.png \
  --text-pt "ação: ela caminha devagar pela praia, de costas, câmera parada" \
  --ollama http://127.0.0.1:11434 --keep-alive 10m \
  --width 640 --height 640 --duration 5 \
  --out saida/teste.mp4
```

- `--prompt-en "..."` pula o compilador.
- `--preset camera=parada-corpo-inteiro-costas --preset luz=por-do-sol` (com `--text-pt` só com a ação): os termos em inglês do preset são anexados ao prompt pelo código, palavra por palavra. A lista está em [`presets/presets.v2.json`](presets/presets.v2.json) (a v2 é a v1 inteira, com os mesmos termos, mais opções para divulgação: órbita e aproximação de produto, avanço pelo ambiente, subida, panorâmica, luz aconchegante, estilo comercial).
- `--continue-from clipe1.mp4` no lugar de `--image`: o último quadro do clipe vira a imagem de entrada (ver "Continuar vídeo").
- Templates sem imagem (T2V) usam o perfil `t2v` do compilador automaticamente.
- Parâmetros fora do contrato (`workflows/*.contract.json`) falham antes de qualquer trabalho: dimensões múltiplas de 16, área até 921.600 px, duração inteira de 3 a 7 s (16·d + 1 frames), seed sorteada se omitida.
- Imprime um JSON com o prompt compilado, os parâmetros efetivos, os tempos e o SHA-256 do MP4.
- Códigos de saída: `0` ok, `2` parâmetro inválido, `3` recusado pelo compilador, `4` erro do ComfyUI, `5` serviço inacessível.

**Atenção:** o compilador **não** é camada de segurança. No teste real, a regra `REFUSED` não disparou para um pedido com uma pessoa real identificável. Os filtros entram na Etapa 4 (`docs/03`, seção 5). Use só conteúdo SFW nos testes.

## Compilador e testes de fidelidade

Os system prompts ficam em [`prompts/`](prompts/) com versão no nome (`compiler-i2v.v5.txt`); a versão em produção de cada perfil está em `gpu2go_worker/compiler.py`. Mudança de prompt = arquivo novo + rodada de fidelidade.

```bash
python tools/eval_compiler.py --ollama http://127.0.0.1:11434 \
  --model huihui_ai/qwen2.5-abliterate:7b --model huihui_ai/qwen2.5-abliterate:14b \
  --version i2v=3 --version i2v=4 --out saida/fidelidade
```

- Casos em [`evals/compiler/cases.v1.json`](evals/compiler/cases.v1.json) (só SFW): termos que precisam aparecer, invenções proibidas, ordem das ações, texto entre aspas, faixa de palavras. Os limites (`thresholds`) foram definidos antes da primeira rodada.
- Os casos com `expect_refused` medem a regra `REFUSED` só como informação: o compilador **não** é camada de segurança.
- Saída fora do inglês (o Qwen às vezes muda para chinês) é refeita com a seed seguinte; na segunda falha, o job falha.
- Resultados e decisão: [`evals/compiler/RESULTADOS.md`](evals/compiler/RESULTADOS.md). Medições em GPU (I2V, bake-off, turbo): [`evals/gpu-2026-10-05/LEIAME.md`](evals/gpu-2026-10-05/LEIAME.md).

## Continuar vídeo

```bash
python tools/submit_workflow.py --template worker/workflows/i2v_r1v2.v1.api.json \
  --continue-from saida/clipe1.mp4 --text-pt "ela se vira e acena" --out saida/clipe2.mp4
python tools/video.py concat saida/clipe1.mp4 saida/clipe2.mp4 -o saida/final.mp4
```

- O último quadro é extraído sem perda (PNG) e conferido contra a resolução do vídeo.
- A concatenação usa `-c copy` (sem recodificar) e confere codec, resolução, fps, formato de pixel e o total de quadros. O limite é de 4 clipes: depois disso o personagem e as cores desviam.
- Cada emenda repete um quadro (1/16 s), porque o 1º quadro do clipe novo é o último do anterior. Tirar esse quadro exigiria recodificar.

## Modo serviço: vídeos de divulgação em lote

Ferramentas do caminho A ([docs/05](../docs/05-analise-de-negocio.md)): vídeos para pequenos negócios a partir das fotos do cliente. Rodam num pod (modo `pod`, ComfyUI e Ollama em `127.0.0.1`), de dentro dele ou por túnel SSH.

**1. Receitas** ([`presets/receitas.v1.json`](presets/receitas.v1.json)): cada uma fixa os presets, uma ação padrão, o formato e a duração.

| Receita | Nicho | Formato padrão |
|---|---|---|
| `produto-giro`, `produto-close` | produto | quadrado, vertical |
| `imovel-sala`, `imovel-fachada` | imóvel | horizontal, vertical |
| `comida-prato`, `comida-bebida` | comida | vertical |
| `pessoa-apresentacao` | pessoa (exige `consentimento=sim`) | vertical |

Formatos: `vertical` 480×832 (Reels, Stories, TikTok), `quadrado` 640×640, `horizontal` 832×480.

**Atenção:** nenhuma receita foi testada em GPU. No teste do turbo, pedidos de órbita de câmera não funcionaram bem no I2V (`evals/gpu-2026-10-05/LEIAME.md`). Antes de vender, rode todas num pod com desligamento automático (~US$ 1 a 2) e descarte ou ajuste as que não ficarem boas.

**2. Lote** (o fluxo principal: uma pasta de fotos, um pedido só, um vídeo por foto):

```bash
# confere tudo e lista os vídeos, sem GPU
python tools/lote.py fotos/loja --saida saida/loja --prompt "a câmera se aproxima devagar do produto" \
  --receita produto-close --conferir
# duas GPUs (dois pods, cada um pelo seu túnel SSH)
python tools/lote.py fotos/loja --saida saida/loja --prompt "a câmera se aproxima devagar do produto" \
  --receita produto-close --comfy http://127.0.0.1:18188 --comfy http://127.0.0.1:28188
```

O que o lote faz para ganhar tempo de GPU:

- **Compila o pedido uma vez** para o lote inteiro e libera a VRAM do Ollama antes dos renders. São ~3 s a menos por vídeo, e todas as fotos recebem exatamente o mesmo prompt.
- **Mantém a GPU ocupada:** cada GPU recebe `--fila 2` vídeos de cada vez, então o próximo já está na fila do ComfyUI quando o atual termina.
- **Usa várias GPUs:** o `--comfy` é repetível. Se uma GPU cair, os vídeos dela voltam para a fila das outras.
- **Escolhe o formato pela proporção de cada foto** (`--formato auto`, com a rotação do EXIF do celular aplicada): vertical, quadrado ou horizontal. O ComfyUI corta a foto no centro, e assim o corte é o menor possível.
- **Preparo da foto** (padrão; `--sem-preparo` desliga): rotação do EXIF, corte central e Lanczos no tamanho exato do render. Foto pequena vira aviso no relatório.
- **Folha de conferência** por vídeo em `conferencia/` (foto + 3 quadros), para revisar o lote em minutos. Nenhuma métrica automática testada pegou a mudança de cor do perfume (rodada de 06/10).
- **Pod com várias GPUs:** `COMFY_GPUS=auto` no entrypoint sobe um ComfyUI por GPU; `--gpus N` usa as N. Dois vídeos na mesma GPU não saem mais rápido; a fila padrão é 1.
- **Qualidade:** `--qualidade rapida` (padrão; 480×832, 640×640, 832×480; ~56 s por vídeo de 5 s na 4090) ou `alta` (720×1280, 960×960, 1280×720, a área máxima do template; **ainda sem medição** de tempo e de VRAM).

Também:

- Tudo é conferido antes do primeiro vídeo: fotos ilegíveis, receita, presets (`--preset camera=parada` troca um preset da receita), duração, variações e consentimento (`--consentimento`, obrigatório nas receitas com pessoa).
- Saída: `<foto>-v<n>.mp4`, `relatorio.csv` (status, GPU, tempo, erro) e `lote.json` (o prompt compilado).
- Rodar de novo continua de onde parou e refaz só o que falhou, com a mesma seed e o mesmo prompt.
- Códigos de saída: `0` tudo pronto, `1` algum vídeo falhou, `2` pedido inválido, `3` recusado pelo compilador, `5` nenhuma GPU acessível.
- Pedidos diferentes por foto: sem `--prompt`, o lote lê `pedidos.csv` da pasta, com as colunas `foto,receita,acao,formato,duracao,variacoes,consentimento` (só `foto` e `receita` obrigatórias; separador `,` ou `;`).

**2b. Lote na nuvem com um comando** (`tools/lote_nuvem.py`, ADR 0003 fase 2): da pasta local direto para a pasta de entrega, subindo e encerrando os pods sozinho.

```bash
# só planeja e estima (sem pods)
python tools/lote_nuvem.py fotos/loja --saida entregas/loja --prompt "a câmera se aproxima devagar do produto" --simular
# 2 pods secure (L40S, depois RTX 4090), orçamento máximo US$ 3
python tools/lote_nuvem.py fotos/loja --saida entregas/loja --prompt "a câmera se aproxima devagar do produto" \
  --receita produto-close --pods 2 --orcamento-usd 3 --interpolar --altura 1280
# se o processo foi interrompido: encerra os pods registrados
python tools/lote_nuvem.py --saida entregas/loja --limpar
```

- **Credencial do RunPod:** a cadastrada no ambiente (proxy) ou `RUNPOD_API_KEY`.
- **Cada pod:**
  - nasce com desligamento automático;
  - passa pelo teste de CUDA e é encerrado na hora se a GPU estiver com defeito ou o agente não responder em 10 min;
  - instala o worker em ~6–10 min (até existir a imagem com modelos).
- **Distribuição:** o pedido é compilado uma vez. As fotos vão em pacotes de 2 por GPU, conforme os pods ficam livres, e pod que cai devolve o pacote.
- **Na pasta de entrega:** cada vídeo chega assim que o pacote termina, com `relatorio.csv` (coluna `pod`) e `conferencia/`. Com `--interpolar`, `--altura` ou `--zoom`, o acabamento vai para `final/`.
- **Segurança e custo:**
  - mostra a estimativa de tempo e custo e pede confirmação (`--sim` pula);
  - recusa se passar de `--orcamento-usd`;
  - todos os pods são encerrados no fim, em erro ou em Ctrl+C.

**3. Acabamento** (ffmpeg, sem GPU; roda em qualquer máquina):

```bash
python tools/acabamento.py saida/padaria/002-caneca-produto-giro-v1.mp4 saida/padaria/003-sala-imovel-sala-v1.mp4 \
  --saida entrega/padaria-01.mp4 --logo marca/logo.png --cartao marca/cartao.png \
  --musica marca/trilha.mp3 --fps 24
```

- Junta os clipes na ordem, ajustados ao tamanho do primeiro, com faixas e sem cortar.
- Logo num canto (`--logo-pos`, `--logo-largura`). Cartão final por `--cartao-s` segundos, sem logo por cima.
- Música com fade, cortada no fim.
- `--interpolar` (16 → 32 fps por estimativa de movimento, ~20 s de CPU por clipe), `--altura 1280` (Lanczos) e `--zoom` (zoom lento até 8%, garante movimento quando o modelo deixa a cena parada).
- `--mostrar` imprime o comando do ffmpeg sem rodar.

**Regras do modo serviço:**

- Só fotos enviadas pelo próprio cliente, com direito de uso.
- Pessoas só com autorização por escrito da pessoa retratada.
- Música do cliente ou com licença comercial.
- Só conteúdo SFW.
- Quem opera vê todas as fotos e todos os vídeos antes da entrega: o compilador não é camada de segurança, e o lote não passa pelos filtros do modo serverless.

## Experimentos em GPU

`tools/experiment.py` roda um plano (prompts SFW já em inglês × seeds × variantes) contra um ComfyUI. Ele mede o tempo de cada vídeo e o pico de VRAM e gera uma grade **cega** para as notas humanas. Retoma de onde parou se for interrompido.

```bash
# bake-off do T2V (docs/adr/0001): imagem bakeoff, MODEL_SET=i2v_r1v2,t2v_a14b_q6k,t2v_a14b_fp8
python tools/experiment.py worker/evals/t2v-bakeoff.v1.json --out saida/bakeoff
# qualidade do turbo no I2V: 4 × 8 passos, CFG 1 × 1,5 × 2
python tools/experiment.py worker/evals/turbo-sweep.v1.json --image <foto SFW> --out saida/turbo
# depois das notas (uma planilha por avaliador)
python tools/experiment.py worker/evals/t2v-bakeoff.v1.json --out saida/bakeoff --score a.csv b.csv
```

- `index.html` mostra cada vídeo só com um código, em ordem embaralhada por linha. A chave fica em `blind_key.json`: não abra antes de terminar as notas.
- `overrides` nos planos muda valores que o contrato trava (passos, CFG). Isso só existe aqui, nunca no envio de produção.
- Os templates T2V candidatos ficam em `workflows/experimental/`, gerados por `tools/build_t2v_templates.py` a partir do template I2V. A imagem `slim` não os valida, o alvo `bakeoff` sim.

## Desenvolvimento

```bash
cd worker
uv sync --python 3.12
uv run pytest -q                       # sem GPU: ComfyUI e Ollama falsos; ffmpeg real
uv run ruff check . ../tools && uv run ruff format --check . ../tools
python ../tools/derive_i2v_template.py --check   # template em dia com o export
python ../tools/build_t2v_templates.py --check   # candidatos T2V em dia com o I2V
bash scripts/lock-requirements.sh      # regenera requirements.lock (mantém as versões já fixadas)
```

O template de produção é **derivado** de `referencia/wan-i2v-com-ollama.api.json` por `tools/derive_i2v_template.py` (remove os nós do Ollama e troca os valores de teste pelos padrões). Não edite o `.api.json` à mão: mude o script ou o contrato.
