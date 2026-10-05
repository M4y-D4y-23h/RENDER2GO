# Worker

Imagem Docker que substitui o `start.sh` do pipeline manual, e o script que envia o workflow ao ComfyUI e devolve o MP4 (Etapa 1 do [plano](../docs/04-plano-de-implementacao.md)).

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

Fica de fora da produção: ComfyUI-Manager, comfyui-ollama, VideoHelperSuite, GGUF e Frame-Interpolation (ver `docs/02-arquitetura.md`, seção 5.2).

## Build

Contexto de build: a pasta `worker/`.

```bash
# sem modelos de vídeo (~18 GB sem o Qwen; para pods com volume ou MODELS_FETCH=1)
docker build -f worker/Dockerfile --target slim -t gpu2go-worker:slim worker

# com os modelos de vídeo embutidos (+~37 GB, uma camada por arquivo)
docker build -f worker/Dockerfile --target full -t gpu2go-worker:full worker
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
| `MODE` | `pod` | `serverless` chega na Etapa 3 |
| `MODEL_SET` | `i2v_r1v2` | Conjunto do `models.lock.json` |
| `MODELS_FROM_DIR` | — | Copia os modelos que faltarem deste diretório (ex.: `/workspace/models` do volume), por leitura sequencial, para o disco local |
| `MODELS_FETCH` | `0` | `1` baixa do Hugging Face os modelos que faltarem |
| `VERIFY_SHA` | `0` | `1` confere SHA-256 na subida (mais lento; os bytes sempre são conferidos) |
| `RESERVE_VRAM_GB` | `6` | VRAM livre para o Ollama; use 4 em GPUs de 24 GB |
| `COMFY_LISTEN` | `127.0.0.1` | Não exponha o ComfyUI: ele não tem login |
| `PUBLIC_KEY` | — | Liga o `sshd` para o túnel |

Com a imagem `slim` num pod com o Global Volume montado: `-e MODELS_FROM_DIR=/workspace/models` (~3 min para 37 GB, como no `start.sh`).

O entrypoint confere a GPU com um kernel real, confere ou copia os modelos, sobe o Ollama e o ComfyUI como processos separados e encerra o container se qualquer um dos dois cair.

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
- Parâmetros fora do contrato (`workflows/*.contract.json`) falham antes de qualquer trabalho: dimensões múltiplas de 16, área até 921.600 px, duração inteira de 3 a 7 s (16·d + 1 frames), seed sorteada se omitida.
- Imprime um JSON com o prompt compilado, os parâmetros efetivos, os tempos e o SHA-256 do MP4.
- Códigos de saída: `0` ok, `2` parâmetro inválido, `3` recusado pelo compilador, `4` erro do ComfyUI, `5` serviço inacessível.

**Atenção:** o compilador **não** é camada de segurança. No teste real, a regra `REFUSED` não disparou para um pedido com uma pessoa real identificável. Os filtros entram na Etapa 4 (`docs/03`, seção 5). Use só conteúdo SFW nos testes.

## Desenvolvimento

```bash
cd worker
uv sync --python 3.12
uv run pytest -q                       # sem GPU: ComfyUI e Ollama falsos
uv run ruff check . ../tools && uv run ruff format --check . ../tools
python ../tools/derive_i2v_template.py --check   # template em dia com o export
bash scripts/lock-requirements.sh      # regenera requirements.lock
```

O template de produção é **derivado** de `referencia/wan-i2v-com-ollama.api.json` por `tools/derive_i2v_template.py` (remove os nós do Ollama e troca os valores de teste pelos padrões). Não edite o `.api.json` à mão: mude o script ou o contrato.
