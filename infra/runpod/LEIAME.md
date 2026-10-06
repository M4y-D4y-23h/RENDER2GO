# Endpoints serverless do RunPod

Cada arquivo `*.json` descreve um template e um endpoint. O `tools/runpod_endpoint.py` cria ou atualiza os dois pelo nome, então rodar de novo não duplica nada.

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
