# Endpoints serverless do RunPod

Cada arquivo `*.json` descreve um template e um endpoint. O `tools/runpod_endpoint.py` cria ou atualiza os dois pelo nome, então rodar de novo não duplica nada.

## Integração GitHub do RunPod (ADR 0002, opção escolhida)

O RunPod monta a imagem direto deste repositório, sem registry próprio. Os Dockerfiles `infra/runpod/Dockerfile.i2v` e `Dockerfile.t2v` são **gerados** a partir de `worker/Dockerfile` por `tools/build_runpod_dockerfiles.py`. Cada um tem o estágio do endpoint por último, os caminhos a partir da raiz do repositório e o formato clássico, sem `RUN --mount` nem heredoc, porque a documentação não diz se a build usa BuildKit. O `.dockerignore` da raiz deixa o contexto em ~0,6 MB. Não edite os gerados: mude o `worker/Dockerfile` e rode o script (o CI confere).

**No console do RunPod (uma vez):**

1. *Settings → Connections → GitHub → Connect*. Autorize **só** o repositório `GPU2GO`.
2. *Serverless → New Endpoint → Import Git Repository* e escolha o repositório.
3. Preencha:
   - **Branch**: a que tem esta versão. Hoje é `claude/festive-mccarthy-sc784n`; depois do merge, `main`.
   - **Dockerfile Path**: `infra/runpod/Dockerfile.i2v` (ou `Dockerfile.t2v`).
   - **Endpoint Name**: `gpu2go-i2v-economico-teste`.
   - **Endpoint Type**: Queue.
   - **GPU**: 24 GB PRO (RTX 4090).
   - **Workers**: 0 ativos, 2 no máximo.
   - **Execution timeout**: 600 s.
   - **Idle timeout**: 30 s.
   - **Variáveis de ambiente** (endpoint de teste, só conteúdo SFW):
     - `GPU2GO_SAFETY=disabled-sfw-tests`
     - `GPU2GO_ALLOWED_URL_HOSTS=<host do seu storage>`
     - `GPU2GO_ALLOW_DISCARD_OUTPUT=1`
     - `MODEL_SET=i2v_r1v2` (`t2v_a14b_fp8` no T2V)
     - `RESERVE_VRAM_GB=4`
     - `JOB_TIMEOUT_S=300`
4. *Deploy Endpoint*. A primeira build baixa ~37 GB de modelos e o PyTorch. O limite é de 30 min para o `docker build` e 160 min no total, e o log da build aparece no console.
5. Para atualizar depois, crie um **release** no GitHub: o RunPod só reconstrói com um release novo, não a cada push.

**Depois, por aqui** (com `RUNPOD_API_KEY` no ambiente):

```bash
# normaliza a configuração do endpoint criado no console (não cria nada nem troca a imagem)
python tools/runpod_endpoint.py apply infra/runpod/i2v-economico.json --github \
  --versao <tag do release> --hosts <host do storage> --sfw-tests --sufixo teste
python tools/bench.py worker/evals/bench-i2v-economico.v1.json --endpoint <id> --out saida/bench-i2v --frios 3
```

## Com imagem num registry (alternativa)

| Arquivo | Modo | GPU | Imagem |
|---|---|---|---|
| `i2v-economico.json` | I2V (R1 v2) | RTX 4090 | alvo `full` |
| `t2v-economico.json` | T2V (A14B fp8 + Lightning, ADR 0001) | RTX 4090 | alvo `full-t2v` |

```bash
export RUNPOD_API_KEY=...   # nas configurações do ambiente, nunca no código

# endpoint de teste e medição (filtros desligados; só conteúdo SFW)
python tools/runpod_endpoint.py plan  infra/runpod/i2v-economico.json \
  --imagem <registry>/gpu2go-worker:2026.10.1 --hosts storage.exemplo.com --sfw-tests --sufixo teste
python tools/runpod_endpoint.py apply infra/runpod/i2v-economico.json \
  --imagem <registry>/gpu2go-worker:2026.10.1 --hosts storage.exemplo.com --sfw-tests --sufixo teste

# medições (cold start, p50/p95, custo); a saída vai para worker/evals/
python tools/bench.py worker/evals/bench-i2v-economico.v1.json --endpoint <id> --out saida/bench-i2v --frios 3

python tools/runpod_endpoint.py delete gpu2go-i2v-economico-teste
```

Regras que o `apply` confere antes de chamar a API:

- `MODE=serverless`, CUDA a partir de 12.8 e nenhuma porta exposta.
- **Fail-closed:**
  - Sem filtros C1–C2 (`GPU2GO_SAFETY`), o endpoint não é criado.
  - `--sfw-tests` liga o modo de testes, que só é aceito com "teste" no nome e libera `discard_output` para o bench.
  - Sem `--hosts` (os hosts das URLs assinadas), o endpoint também não é criado.
- `GPU2GO_IMAGE_VERSION` recebe a tag da imagem e aparece na saída de cada job.

Onde hospedar a imagem: [ADR 0002](../../docs/adr/0002-hospedagem-da-imagem.md). Ainda sem medição real: falta a imagem acessível pelo RunPod.
