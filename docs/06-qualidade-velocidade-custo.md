# 06 — Qualidade, velocidade e custo em lotes de 50+

- **Data:** 06/10/2026
- **Pedido:** avaliar os pontos levantados pelo dono do projeto para lotes de 50+ fotos: GPU mais potente com paralelismo, upscale e limpeza, upsampling de prompt, motion buckets e flow match, bf16 e INT8, cache em volume, escolha de GPU.
- **Base:** medições de 05/10 e 06/10 (`worker/evals/gpu-2026-*`). Os números marcados *estimativa* ainda não foram medidos.
- Arquitetura para escala: [ADR 0003](adr/0003-arquitetura-em-escala.md).

## 1. Resumo

| Ponto | Vale para o lote? | Situação |
|---|---|---|
| GPU mais potente | Sim, se o custo por vídeo cair | GPUs de 48 GB ainda sem medição (seção 2) |
| Vários vídeos ao mesmo tempo **na mesma GPU** | **Não** | Medido: fila 1 e fila 2 deram ~80 s por vídeo |
| Paralelismo com **uma GPU por vídeo** | **Sim**, é o que acelera | Feito: `COMFY_GPUS=auto` e `lote --gpus N` |
| Limpeza e ajuste da foto antes do render | Sim | Feito: `preparo.py` (EXIF, corte, Lanczos) |
| Upscale da foto de entrada com IA | Raramente | Foto de celular já é maior que o vídeo; só foto pequena se beneficia |
| Upscale e interpolação do vídeo de saída | **Sim**, com bom custo | Feito na CPU: `acabamento --altura --interpolar`. Upscale com IA fica para medir |
| Upsampling do prompt com LLM local | Já existe | O compilador (Qwen 7B) faz isso. Próximo passo: descrever cada foto com um modelo de visão |
| Motion buckets | Não se aplica ao Wan | É conceito do Stable Video Diffusion |
| Flow match | O Wan 2.2 já é flow matching | Movimento garantido: presets, `acabamento --zoom` e, depois, modelo de câmera (seção 5) |
| BF16 puro | **Não** | Dobra a VRAM sem ganho visível. Hoje: pesos fp8 com cálculo em bf16 |
| INT8 / GGUF | Só em GPU sem fp8 | Ampere (A40) |
| Aceleração `--fast` fp8 | **Não** | Medido na L40S: ~55 s com e sem |
| SageAttention | Talvez | A medir (pacote novo, ADR) |
| Cache em volume de rede | Hoje, não | Download do Hugging Face: ~2 min e grátis. Melhor: imagem com os modelos (ADR 0002) |

## 2. GPU mais potente e paralelismo

**Por que 4 ou 8 vídeos ao mesmo tempo na mesma GPU não aceleram.** Um vídeo do Wan já ocupa a GPU inteira durante o render: a GPU fica limitada pelo cálculo, não pela espera. Se rodar dois ao mesmo tempo, cada um leva o dobro. A rodada de 06/10 confirmou: fila 1 e fila 2 deram ~80 s por vídeo. Além disso, cada modelo de 14B ocupa ~15 GB. Numa GPU de 24 GB não cabe um segundo vídeo; numa de 48 GB caberiam os dois "experts" de um vídeo, não dois vídeos.

**O que acelera de verdade:**

1. **Uma GPU por vídeo.**
   - Um pod com 2, 4 ou 8 GPUs, com um ComfyUI por GPU (`COMFY_GPUS=auto` no `entrypoint.sh`) e o lote distribuindo os vídeos (`lote --gpus N`). Ou vários pods (`lote --comfy URL1 --comfy URL2 ...`).
   - O custo por vídeo não muda e o tempo do lote cai na proporção.
   - **Cuidado com a RAM:** cada ComfyUI guarda ~37 GB de modelos em RAM. Num pod de 4 GPUs, são ~150 GB, então é preciso conferir a RAM do pod antes.
2. **GPU de 48 GB** (L40S, RTX 6000 Ada, A40).
   - Os dois experts do I2V cabem juntos, o que elimina a troca de modelos a cada vídeo. Na 4090 foram ~11 s por fase (`gpu-2026-10-05`).
3. **GPU mais rápida** (H100): menos tempo por vídeo, mas preço por hora bem maior.

Para custo baixo, o que importa é o **custo por vídeo** (preço por hora × tempo por vídeo). Para entregar rápido ao cliente, o que importa é **quantas GPUs trabalham em paralelo**.

| GPU (RunPod, 06/10) | VRAM | fp8 nativo | US$/h | Por vídeo de 5 s (rápida) | Custo por vídeo |
|---|---|---|---|---|---|
| RTX 4090 secure | 24 GB | sim | 0,74 | **~80 s (medido)** | **~US$ 0,016** |
| RTX 4090 community | 24 GB | sim | 0,34 | ~80 s | ~US$ 0,008, mas 3 de 3 hosts com CUDA quebrado |
| RTX 5090 | 32 GB | sim | 0,69 a 0,99 | *~50–60 s (estimativa)* | *~US$ 0,010–0,016* |
| **L40S** | 48 GB | sim | 0,79 a 1,09 | **~55 s (medido)**; alta ~180 s | **~US$ 0,012–0,017** |
| A40 | 48 GB | **não** (Ampere) | 0,49 | *~120–160 s (estimativa: metade do cálculo de uma 4090)* | *~US$ 0,016–0,022* |
| H100 SXM | 80 GB | sim | 3,49 | *~25–35 s (estimativa)* | *~US$ 0,024–0,034* |

- **A40 não serve para "velocidade extrema".** É uma geração anterior (Ampere), sem fp8 nativo. Deve ser mais lenta por vídeo que a 4090. Pode compensar pelo preço, mas só medindo.
- **A L40S foi medida** (`worker/evals/gpu-2026-10-06-l40s/`): ~55 s por vídeo, 30% mais rápida que a 4090 secure de hoje, com custo por vídeo igual ou menor. É a **recomendação para lotes**: community quando houver vaga (com o teste de CUDA), secure como reserva.

## 3. Upscale e limpeza da imagem antes do render

- **Feito: `preparo.py`, ligado por padrão no lote.**
  - Aplica a rotação do EXIF, converte para RGB e põe fundo branco onde houver transparência.
  - Corta no centro para a proporção e redimensiona com Lanczos para o tamanho exato do render.
  - O ComfyUI faria esse ajuste com redimensionamento bilinear, que serrilha ao reduzir foto de celular de 4000 px.
  - Bônus: o envio cai de MB para KB, e os metadados (EXIF e GPS) da foto do cliente não vão para a GPU.
- **Upscale da entrada com IA:** só ajuda quando a foto é menor que o vídeo. O lote avisa ("foto pequena, ampliada 2,1x"). Para esses casos, um ESRGAN no pod é a próxima opção, mas precisa de modelo, de licença conferida e de medição.
- **Limpeza com IA** (ruído, compressão): sem caso real ainda. As fotos de produto e imóvel costumam ser boas.

## 4. Upsampling de prompt

O compilador já faz isso: ele transforma "a câmera se aproxima devagar do produto" em um parágrafo em inglês de 15 a 60 palavras. O lote compila **uma vez** por pedido.

- **Corrigido nesta rodada:** o compilador escrevia "Static camera." mesmo com preset de movimento. Com preset de câmera, essas frases agora saem pelo código (`compiler.drop_static_camera`).
- **Próximo passo de qualidade:** um modelo de visão pequeno (ex.: Qwen2.5-VL 3B) descreve **cada foto**, e o prompt cita o que existe nela ("vapor sobe da caneca branca").
  - Custo estimado: 1 a 3 s por foto, e a troca de modelo na VRAM.
  - Exige novo prompt do compilador, rodada de fidelidade e ADR.
  - Vale testar quando houver fotos reais de clientes.

## 5. Motion buckets e flow match

- **Motion bucket** é um parâmetro do Stable Video Diffusion (quanto de movimento gerar). O Wan 2.2 não tem esse controle.
- **Flow matching** é o tipo de modelo que o Wan 2.2 já é, e o `shift` do sampler já está no template.

Como garantir "slow pan" e "cinematic zoom":

1. **Presets de câmera** (já existem) e o conserto do "Static camera".
2. **`acabamento --zoom`:** zoom lento no centro, garantido e sem GPU. Resolveu a fachada parada da rodada de 06/10 (`saida/videos-2026-10-06/fachada-acabada.mp4`).
3. **Depois:** modelo de controle de câmera do Wan 2.2 (família "Fun Camera", com trajetória explícita). Movimento de câmera exato, mas com modelo novo, template novo, VRAM e licença a conferir. Pede ADR.

## 6. BF16, INT8 e cache em volume

### BF16 e INT8

- **Hoje:** pesos em fp8 (e4m3fn com escala), com o cálculo feito em bf16. É o que o pedido descreve como "gerar em BF16", e é o padrão dos modelos oficiais do ComfyUI.
- **BF16 puro nos pesos:**
  - dobra a VRAM: ~29 GB por expert, ~57 GB os dois, o que exige GPU de 80 GB;
  - deixa o render mais lento por mover o dobro de dados;
  - a diferença de qualidade para fp8 com escala é pequena.

  Não recomendo.
- **INT8 / GGUF Q8:** útil em GPU sem fp8 (A40) ou com pouca VRAM. Na 4090 e na L40S, o fp8 é melhor.
- **Onde há ganho real de velocidade (a medir):**
  - `--fast fp8_matrix_mult` do ComfyUI (multiplicação em fp8 nas GPUs Ada, Hopper e Blackwell);
  - SageAttention (atenção quantizada; pacote novo, precisa de versão fixada).

  São candidatos ao teste em GPU. Se ganharem, entram como opção do `COMFY_EXTRA_ARGS`.

### Cache em volume de rede

- **Custo:** US$ 0,07/GB/mês, ~US$ 5 por mês para os ~70 GB de modelos.
- **Desvantagens:**
  - prende os pods a um datacenter, justo quando a disponibilidade de GPU mais pesa;
  - a regra do projeto proíbe ler modelos direto do volume: é preciso copiar e conferir, o que leva ~3 min;
  - o download do Hugging Face já leva ~2 min e é grátis.
- **Melhor caminho:** a imagem com os modelos embutidos (ADR 0002), que o RunPod mantém em cache no host. O volume só passa a valer com pods fixos num datacenter.

## 7. Teste em GPU de 48 GB (feito em 06/10) e o que falta

- **Feito** numa L40S avulsa: ~55 s por vídeo na rápida, ~180 s na alta e nenhum ganho com `--fast fp8_matrix_mult` (`worker/evals/gpu-2026-10-06-l40s/`). Custo: ~US$ 1,10.
- **Falta:** 2 vídeos ao mesmo tempo em 2 GPUs. O pod 2× L40S não subiu, e o código está pronto (`COMFY_GPUS=auto`, `lote --gpus 2`).

## 8. O fluxo desejado, passo a passo

| Passo | Situação |
|---|---|
| 1. O cliente envia uma pasta | Falta a tela e o armazenamento (ADR 0003). Hoje a pasta é local |
| 2. O script sobe o pod, executa, para e encerra | Partes prontas (`runpod_pod.py`, desligamento automático, teste de CUDA). Falta juntar num comando (ADR 0003, fase 2) |
| 3. Preparo → prompt → render | **Pronto:** `preparo` → compilador uma vez → render fp8 com cálculo em bf16 → folha de conferência |
| 3b. Acabamento (32 fps, 720p/1080p, zoom) | Pronto na CPU (`acabamento.py`); falta rodar no lote |
| 4. O vídeo vai para a pasta do cliente | Falta o armazenamento (ADR 0003) |
