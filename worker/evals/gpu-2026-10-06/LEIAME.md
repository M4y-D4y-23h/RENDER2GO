# Sessão em GPU de 06/10/2026: receitas e lote

RTX 4090 secure no RunPod (Noruega), driver ≥ 570, CUDA 12.8. Ambiente igual ao dos alvos `runtime`/`slim` do `worker/Dockerfile`, montado sobre `python:3.12-slim`. Modelos nas revisões do `models.lock.json`, conferidos por bytes e SHA-256.

Custo: ~US$ 0,80 no total.
- ~US$ 0,60 no pod de teste (~48 min).
- ~US$ 0,20 em pods que não serviram:
  - duas 4090 community com CUDA quebrado (`cuInit` = 999, driver 580);
  - uma A40 cujo container não subiu em 13 min.

Só conteúdo SFW. As fotos de entrada são **sintéticas**: o T2V de produção gerou um vídeo de 3 s por foto, e foi usado o quadro 24 (`fotos-json/`). Não há foto real de cliente nem pessoa real. Os vídeos não estão no repositório.

## Tempos (qualidade rápida, 5 s, I2V R1 v2)

| Teste | Vídeos | Tempo total | Por vídeo |
|---|---|---|---|
| Receitas, `pedidos.csv`, fila 2 (`receitas/`) | 8 | 10,7 min | ~80 s |
| Um pedido só, fila 1 (`fila1/`) | 3 | 4,0 min | ~80 s |
| O mesmo, fila 2 (`fila2/`) | 3 | 4,0 min | ~80 s |
| Só modelos I2V carregados, fila 1 | 3 | 4,2 min | ~85 s |
| **Qualidade alta** (720×1280 e 1280×720, `alta/`) | 2 | 7,1 min | **~213 s** |

- **Fila 2 não ganha nada:** envio e download são rápidos e o render é o gargalo. Para ir mais rápido, só com mais GPUs.
- **~80 s por vídeo, contra 56 s na rodada de 05/10** (outro host 4090 secure, mesma configuração). Ter os modelos T2V carregados não explica a diferença, porque só com I2V deu ~85 s. Fica a variação entre hosts. **Para preço, usar ~80 s.**
- **Qualidade alta:** 2,7× o tempo da rápida. A VRAM coube, com pico de 21,4 GB contra 20,9 GB na rápida.
- **Compilação uma vez por lote:** os 8 pedidos diferentes foram compilados em sequência antes dos renders. Ver `lote.json`.

## Qualidade das receitas (avaliação por quadros 0, 40 e 80; humana pendente)

| Receita | Resultado | Movimento (YDIF) |
|---|---|---|
| `produto-giro` (caneca) | A câmera orbita: a alça some atrás da caneca, e o vapor se mexe. **Bom** | 2,9 |
| `produto-close` (tênis) | Gira e aproxima. **Bom** | 5,0 |
| `produto-close` (perfume) | **Defeito:** a cor do líquido muda de amarelo para laranja e o rótulo se deforma | 3,7 |
| `imovel-sala` | Avanço e panorâmica suaves. **Bom** | 4,1 |
| `imovel-fachada` | **Quase parado:** a subida de câmera não aparece | 3,6 |
| `comida-prato` | Aproximação sutil. **Ok** | 3,5 |
| `comida-bebida` | Gelo e limão se mexem. **Ok** | 3,0 |
| `pessoa-apresentacao` (pessoa sintética) | Sorri e acena; o rosto se mantém. **Bom** | 6,6 |

## Achados

1. **O compilador acrescenta "Static camera."** mesmo quando o preset pede movimento (`lote.json`: "…dolly-in… Static camera. …"). É uma contradição no prompt. Corrigir num `compiler-i2v.v6.txt` com rodada de fidelidade (regra do projeto).
2. **Produtos com líquido ou rótulo** podem mudar de cor ou de forma. Antes de entregar, conferir cor e rótulo do produto contra a foto original.
3. **Fachada:** trocar a câmera da receita (`panoramica-lenta` ou `aproximacao-plano-medio`) e testar de novo.
4. **4090 community:** os dois hosts sorteados hoje (driver 580) falharam no `cuInit`, como na rodada de 05/10. O teste de `cuInit` logo na subida encerrou cada um em ~3 min. Para lote pago, usar secure, ou community só com essa checagem (o `entrypoint.sh` já faz isso com `TERMINATE_ON_BAD_GPU=1`).
