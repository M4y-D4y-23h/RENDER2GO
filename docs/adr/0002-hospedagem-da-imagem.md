# ADR 0002 — Onde fica a imagem do worker (e os modelos)

- **Status:** **aceito em 06/10/2026: integração GitHub do RunPod** (opção 1), escolhida pelo dono do projeto. Falta a primeira build no RunPod confirmar que o `docker build` cabe nos 30 min. Se não couber, a próxima opção é o Docker Hub (opção 2).
- **Data:** 06/10/2026
- **Etapa:** 3 ([plano](../04-plano-de-implementacao.md))

## Contexto

O endpoint serverless precisa de uma imagem que o RunPod consiga baixar. As imagens têm os modelos embutidos, como na v1 do doc 02 (seção 5.5):

| Imagem | Tamanho aproximado | Maior arquivo |
|---|---|---|
| `full` (I2V) | ~55 GB | 14,8 GB (R1 v2, uma camada por arquivo) |
| `full-t2v` (T2V, ADR 0001) | ~50 GB | 14,3 GB (A14B fp8) |

Cada modelo grande vira uma camada única de ~15 GB. É isso que decide o registry.

## O que foi conferido (06/10/2026)

| Opção | Limite relevante | Fonte | Situação |
|---|---|---|---|
| **Cached models do RunPod** | 1 modelo (repositório do Hugging Face) por endpoint, e o repositório inteiro é baixado | docs.runpod.io, "model caching" | **Descartada:** o I2V usa 3 repositórios (R1 v2, NSFW-UMT5, VAE), e o T2V fp8 fica no repositório Comfy-Org, que tem centenas de GB |
| **GHCR** (GitHub) | 10 GB por camada; 10 min por upload | docs.github.com | **Descartada:** as camadas de 14,8 GB não passam |
| **Amazon ECR** | 52.000 MiB por camada | docs.aws.amazon.com | Cabe. O token de login do ECR expira em 12 h, e o suporte do RunPod a ECR privado não está documentado |
| **Docker Hub** (privado) | Limite por camada não documentado | docs.docker.com | Caminho que a documentação do RunPod usa. É preciso testar o envio de uma camada de 15 GB |
| **Integração GitHub do RunPod** (o RunPod faz a build a partir do repositório) | Imagem ≤ 80 GB; build total ≤ 160 min; `docker build` ≤ 30 min; repositório privado aceito; contexto = raiz do repositório | docs.runpod.io, "GitHub integration" | Cabe no tamanho. Exige adaptar o Dockerfile e caber nos 30 min |

Também conta onde a imagem é montada. Este ambiente de desenvolvimento tem ~30 GB de disco. Os runners padrão do GitHub Actions têm ~30 GB livres depois da limpeza. Pods do RunPod não rodam `docker build`. Para as opções Docker Hub e ECR, a build precisa de uma máquina com ~150 GB de disco.

## Opções

1. **Integração GitHub do RunPod (recomendada para testar primeiro):** não precisa de registry nem de máquina de build, e a imagem é montada perto das GPUs.
   - Adaptações necessárias:
     - o Dockerfile precisa funcionar com o contexto na raiz do repositório;
     - o estágio final precisa ser o do endpoint, um Dockerfile por endpoint (I2V e T2V), já que a integração não documenta `--target`;
     - o secret da CA é opcional e fica ausente lá.
   - Risco: o `docker build` estourar os 30 min. São ~37 GB de modelos, mais o PyTorch e o Qwen. Com a rede de ~500 MB/s que medimos no RunPod, os modelos levam ~2 min.
2. **Docker Hub privado:** build numa máquina com disco suficiente (sua máquina, ou uma VM temporária com ~150 GB) e `docker push`. Testar primeiro se uma camada de 15 GB passa. No RunPod, a credencial é cadastrada uma vez (`containerRegistryAuthId`).
3. **ECR:** cabe com folga, mas a credencial expira em 12 h e precisaria ser renovada no RunPod. Só se 1 e 2 falharem.
4. **Plano B, sem camadas gigantes:** imagem `slim` (~18 GB, cabe em qualquer registry) com os modelos baixados do Hugging Face na subida do worker, conferidos por bytes e SHA-256, o que já funciona hoje (`MODELS_FETCH=1`). A ~500 MB/s isso dá ~1,5 min a mais por cold start, **cobrados**. Serve para medir enquanto 1 ou 2 não se resolvem.

## Decisão

**Opção 1.** Implementação:

- `tools/build_runpod_dockerfiles.py` gera `infra/runpod/Dockerfile.i2v` e `Dockerfile.t2v` a partir do `worker/Dockerfile`:
  - estágio do endpoint por último;
  - COPY a partir da raiz;
  - formato clássico (sem `RUN --mount` e sem heredoc; o download do Qwen virou `scripts/pull_compiler_model.sh`);
  - `MODE=serverless`.
- `.dockerignore` na raiz, com contexto de ~0,6 MB.
- `runpod_endpoint.py apply --github` ajusta o endpoint criado no console.
- Conferido aqui:
  - o builder clássico aceita os arquivos;
  - a build com BuildKit chega ao estágio `slim`, com os testes de fumaça.

Plano original, mantido como registro:

1. Testar a opção 1 com o endpoint de teste de I2V.
2. Se a build passar de 30 min ou falhar, seguir pela opção 2 com uma VM de build temporária.
3. Medir cold start e custo com o `tools/bench.py` (aceite da Etapa 3).

Enquanto isso, a opção 4 permite rodar o bench já, com o custo de subida contado à parte.
