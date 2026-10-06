# Custo por vídeo (alimenta a precificação, doc 02, seção 8)

**Situação (06/10/2026): estimativa.** Os tempos vêm do pod RTX 4090 de 05/10/2026 (`gpu-2026-10-05/`), não ainda de um endpoint serverless. O `tools/bench.py` substitui esta tabela pelos números medidos (aceite da Etapa 3). Os preços são da página de preços do RunPod (Serverless, Flex, cobrança por segundo), conferidos em 06/10/2026.

| Tier | GPU | US$/h (Flex) | US$/s |
|---|---|---|---|
| Econômico | RTX 4090 (24 GB PRO) | 1,10 | 0,000306 |
| Padrão | RTX 5090 (32 GB PRO) | 1,58 | 0,000439 |

## Fórmulas (as mesmas do `bench.py`)

- **Custo quente** = tempo de execução do job × US$/s. É o caso com o worker já ligado e jobs em fila.
- **Custo isolado** = (subida do worker + execução + idle timeout) × US$/s. É um vídeo sozinho, com o worker ligando e desligando só para ele: o pior caso.
- Preço ao cliente = custo × (1 + margem) + moderação + armazenamento, em créditos (doc 02, seção 8).

## Estimativa no tier econômico (RTX 4090)

| Modo | Resolução | Duração | Execução quente | Custo quente | Custo isolado* |
|---|---|---|---|---|---|
| I2V (R1 v2) | 640×640 | 5 s | ~60 s (3,3 s de compilação + 56,2 s de render) | **~US$ 0,018** | ~US$ 0,028 + subida |
| T2V (A14B fp8) | 832×480 | 5 s | ~60 s (≈3 s + 56,5 s) | **~US$ 0,018** | ~US$ 0,028 + subida |

\* Com idle timeout de 30 s. A subida (pull da imagem de ~55 GB na primeira vez em cada host, mais o boot) ainda não foi medida: depende da hospedagem da imagem (ADR 0002). Com o FlashBoot, as subidas seguintes no mesmo host devem ser bem menores.

Ainda não medido: 3 s e 7 s, 832×480 no I2V, 720p no tier padrão, e p95. Fica para o `bench-i2v-economico.v1.json` (52 jobs) e o `bench-t2v-economico.v1.json`.
