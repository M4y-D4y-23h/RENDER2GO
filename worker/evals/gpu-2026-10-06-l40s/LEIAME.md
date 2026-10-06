# Sessão em GPU de 06/10/2026 (2): L40S, `--fast` e qualidade alta

- **Máquina:** 1× L40S secure no RunPod (EUA). 48 GB de VRAM, driver 570.195.03, 128 vCPUs, 1,5 TB de RAM no host.
- **Execução autônoma:** um script no pod instalou, baixou os modelos, testou e empacotou os resultados. O desligamento automático em Python estava armado para 75 min, e o vigia daqui encerrou o pod ao terminar.
- **Entradas:** as mesmas 8 fotos sintéticas SFW da rodada anterior. Os vídeos não estão no repositório.
- **Código:** commit `c8d13d3`, com o filtro de "Static camera", o preparo Lanczos, `COMFY_GPUS=auto` e as folhas de conferência.

Custo: ~US$ 1,10 no total.
- ~US$ 0,71 no pod de teste (~39 min).
- ~US$ 0,36 num pod 2× L40S cujo container não subiu em 10 min (encerrado pelo script).

O teste de 2 GPUs em paralelo não rodou: só havia L40S avulsa. O caminho multi-GPU foi exercitado com N = 1.

## Resultados (I2V R1 v2, 5 s)

| Teste | Vídeos | Por vídeo | Pico de VRAM |
|---|---|---|---|
| B. Rápida, 8 receitas | 8 | **~55 s** (54–64) | 40,2 GB |
| D. Alta (720×1280, 960×960, 1280×720) | 4 | **~180 s** | 43,6 GB |
| E. Rápida com `--fast fp8_matrix_mult` | 8 | ~55 s (53–73) | 40,2 GB |

- **L40S vs 4090 secure de hoje (~80 s):** ~30% mais rápida. Os dois experts ficam juntos na VRAM (40 GB), sem troca de modelo a cada vídeo.
- **Custo por vídeo (rápida):**
  - L40S secure: 55 s × US$ 1,09/h ≈ **US$ 0,017**;
  - L40S community (US$ 0,79/h): ≈ **US$ 0,012**;
  - 4090 secure: ≈ US$ 0,016.

  O custo fica no mesmo nível e a entrega é mais rápida.
- **Qualidade alta:** ~180 s na L40S contra ~213 s na 4090. Custa ~US$ 0,055 por vídeo na L40S secure.
- **`--fast fp8_matrix_mult`:** **sem ganho** (~55 s). Não entra como padrão.
- **RAM:** ~35 GB por ComfyUI (82 → 116 GB com os modelos carregados). Pod com N GPUs precisa de ~40 GB × N de RAM.
- **"Static camera":** o filtro funcionou. A única ocorrência que sobrou vem do preset `parada-close` da bebida, que é o pedido.
- **Qualidade:**
  - a caneca orbita bem;
  - a fachada continua com pouco movimento (YDIF 3,6). A solução é `acabamento --zoom`;
  - o perfume ainda deforma o rótulo e a cor no fim. É limitação do modelo, não da GPU.

  As folhas de conferência (`conferencia/`) mostram isso sem abrir os vídeos.

## Achado de operação

Containers que não sobem em GPUs de datacenter: a A40 e a 2× L40S não responderam em 10 min, enquanto a L40S avulsa respondeu em 40 s. O script de início (agente → `cuInit` → instalação) encerrou cada pod com defeito sozinho. Fica como regra para o orquestrador (ADR 0003): **corte em 10 min sem resposta e teste de CUDA antes de instalar qualquer coisa.**
