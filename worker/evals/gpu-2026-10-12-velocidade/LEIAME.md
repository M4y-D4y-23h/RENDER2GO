# Velocidade em GPU: worker novo, aquecimento, SageAttention 2.2 e RIFE (12/10/2026)

Pedido do dono (12/10/2026): avaliar, com uso real em pod, as três ideias que valiam da proposta
FastAPI/asyncio: **a GPU não espera** (preparar o próximo vídeo e enviar o anterior enquanto ela
gera), **SageAttention 2.2 e interpolação** e **aquecer no boot**. O `torch.compile` fica de fora
por enquanto (decisão do dono).

## O que roda

- `medir_velocidade.py` (na sessão): sobe um pod L40S secure como o orquestrador, com
  desligamento automático pelo preço real da GPU e pelo teto do teste (`--orcamento-usd`, abaixo do
  saldo menos a reserva, e nunca mais de 100 min), acompanha, baixa os resultados para `saida/` e
  encerra o pod (try/finally).
- `preparar_extras.sh` (no pod, a partir do estado AMBIENTE): pesos do RIFE v4.25 (MIT;
  `Comfy-Org/frame_interpolation`, revisão `219da3c9`, conferidos por tamanho e SHA-256) e o
  SageAttention 2.2.0 (Apache-2.0) compilado no pod, do commit `eb615cf6` (tag v2.2.0), só sm89,
  com o nvcc 12.8 do repositório da NVIDIA para Debian 12 (o CUDA do PyTorch 2.8.0+cu128).
- `no_pod.py` (no pod): as fases abaixo, com fotos SFW de `fixtures/sfw` e seeds fixas. Cada fase
  sobe o seu ComfyUI com os argumentos do entrypoint (`--reserve-vram 6`). A API e o
  armazenamento são de mentira, no mesmo processo, com latência de rede: a da API medida no pod
  (página inicial do serviço), e 0,5 s para baixar a foto, 0,3 s para enviar a folha e 0,8 s para
  enviar o vídeo (estimativas). Testado antes sem GPU (`--simular`).

| Fase | O quê |
|---|---|
| 1 | ComfyUI novo → 1 vídeo 480p de 5 s (frio) → o mesmo de novo (quente); ComfyUI novo → aquecer (256×256, 3 s) → o mesmo vídeo |
| 2 | Os mesmos 6 vídeos 480p de 5 s com o worker de antes (um de cada vez) e o novo; 2 vídeos 720p de 5 s |
| 3 | O template com RIFE ×2 (16 → 32 fps) em 2 dos vídeos 480p |
| 4 | SageAttention: a atenção sozinha (40 cabeças, 32.760 tokens, como o Wan em 480p de 5 s) e os mesmos 6 + 2 vídeos, comparados quadro a quadro com os da fase 2 |

## Regras de decisão (escritas antes da medição)

1. **Worker novo**: fica ligado por padrão se, nos 6 vídeos 480p, (a) os vídeos saírem iguais aos
   do worker de antes (mesma seed, mesmo SHA-256) e todos certos; (b) o tempo de GPU parada entre
   um vídeo e o próximo cair pelo menos 80%; (c) o tempo por vídeo cair. Senão, `--sem-adiantar`
   vira o padrão.
2. **Aquecer**: fica ligado (só age com a fila vazia) se o vídeo depois do aquecimento sair a menos
   de 10% do tempo do vídeo quente, isto é, se o aquecimento tirar do 1º pedido a carga dos
   modelos. Senão, sai do padrão.
3. **SageAttention 2.2**: vira proposta de ADR se (a) o tempo do ComfyUI cair pelo menos 15% em
   480p e 20% em 720p; (b) nenhum vídeo reprovar na conferência ou no filtro de imagem; (c) o SSIM
   médio contra o vídeo atual (mesma seed) for de pelo menos 0,90 e as folhas lado a lado não
   mostrarem defeito visível (deformação, ruído, cor); (d) o log do ComfyUI não tiver
   "Error running sage attention" (o ComfyUI volta sozinho para a atenção normal e a medida não
   valeria). Ganho só de velocidade, com diferença visível: não adotar.
4. **RIFE ×2**: vira proposta de ADR (como opção, com decisão do dono: muda o vídeo entregue, 32 fps)
   se custar até 5 s de GPU por vídeo 480p de 5 s e os quadros do meio não tiverem fantasma visível
   nas folhas.

## Resultado (12/10/2026, 13:06–13:54)

- **Máquina:** 1× L40S secure (EUA), driver 580.126.09, 48 GB de VRAM (45 GiB para uso), US$ 1,09/h. Subida até o
  PRONTO em 3 min (modelos do espelho R2 em 114 s, SHA-256 conferido aos 150 s). Latência da API
  medida no pod: 0,04 s com a conexão aberta.
- **Custo:** US$ 0,86 (saldo do RunPod de US$ 11,24 para US$ 10,38; 47 min de pod). Pod encerrado
  e conferido na API (`pod: None`, nenhum pod na conta, gasto de US$ 0/h).
- Arquivos: `resultado.json` (fases 1 e 4), `complemento.json` (o que a fase 2 perdeu, ver
  "Incidente"), `medidas.log` (o log do roteiro e do worker, com as etapas de cada vídeo),
  `estado_extras` (RIFE e compilação) e `retomada.json`.

### 1. Frio e aquecimento (480p, 5 s, a mesma seed)

| Vídeo | Tempo no ComfyUI |
|---|---|
| 1º num ComfyUI novo (frio) | 56,2 s |
| O mesmo de novo (quente) | 50,7 s |
| ComfyUI novo → aquecer (16,6 s) → o vídeo | **50,7 s** |

O aquecimento tira toda a carga do 1º vídeo, que aqui é pequena: **5,5 s** num pod novo (os
modelos acabaram de ser baixados e estão na memória). Na volta da hibernação de 11/10, com o disco
de volume, a diferença foi de ~26 s. Custo: 16,6 s de GPU, só com a fila vazia.

### 2. Worker de antes × novo (6 vídeos 480p de 5 s, as mesmas seeds)

| | Antes (um de cada vez) | Novo (a GPU não espera) |
|---|---|---|
| Tempo total | 325,7 s | 310,0 s |
| Por vídeo | 54,3 s | **51,7 s (−4,8%)** |
| GPU parada entre um vídeo e o próximo | 2,03 s (1,1–3,1) | **0,04 s (−98%)** |
| Vídeos certos | 4 de 6 | 4 de 6 (os mesmos) |

- Etapas que o worker novo esconde atrás da GPU (por vídeo): preparo da foto 0,6 s, conferência
  0,4–0,8 s, filtro de imagem 0,4–0,5 s, envio da folha e do vídeo 1,4 s (com a rede simulada:
  0,5 s para baixar a foto, 0,3 s e 0,8 s para enviar). Com a rede de verdade (fotos grandes do
  cliente, R2), o ganho é o tempo real dessas etapas.
- Os 2 vídeos que não saíram são os mesmos nos dois: `cafe1` e `cafe5` reprovam na deriva de cor
  (55 e 59; limite 40); a aproximação da xícara de café muda a cor do centro. As xícaras de
  porcelana passam sempre.
- **Mesma saída:** o ComfyUI não repete o vídeo bit a bit nem no mesmo processo (os 3 vídeos da
  fase 1, da mesma seed, têm SHA-256 diferentes), então a regra 1(a) foi conferida pelo resultado
  (os mesmos vídeos certos, a mesma deriva: 55,0 × 55,1 e 59,2 × 59,3) e pelo SSIM antes × novo:
  **0,990–0,997** (PSNR 44–49 dB), o ruído de uma geração para outra.
- 720p (2 vídeos): 172,5 s por vídeo (render de 170,3 s na xícara; `cafe1` reprova como em 480p).

### 3. RIFE ×2 (16 → 32 fps), na mesma GPU quente, o mesmo vídeo sem e com

| Vídeo 480p de 5 s | 16 fps | 32 fps (RIFE) | Diferença |
|---|---|---|---|
| Café | 50,75 s, 81 quadros, 486 KB | 55,78 s, 161 quadros, 531 KB | **+5,0 s**, +9% no arquivo |
| Xícara | 50,71 s, 81 quadros, 212 KB | 55,08 s, 161 quadros, 233 KB | **+4,4 s**, +10% no arquivo |

A interpolação em si leva ~2 s (80 pares a ~40 por segundo); o resto é codificar o dobro de
quadros. VRAM igual (~40 GB). Nas folhas (quadros 40 a 44 a 32 fps), os quadros do meio não têm
fantasma nem borda dupla, nos dois movimentos lentos testados; movimento rápido e texto não foram
testados.

### 4. SageAttention 2.2

- **Compilação:** 7 min no pod (sm89), wheel `sageattention-2.2.0-cp312-cp312-linux_x86_64.whl`,
  SHA-256 `964ee18cf8c8a6421bb3009111ecaf5f57da9f41d440f3aca78c2caa7a50fa42`. O ComfyUI disse
  "Using sage attention" e não houve nenhum "Error running sage attention".
- **A atenção sozinha** (40 cabeças × 32.760 tokens × 128): 60,0 ms contra 108,4 ms (**1,8×**),
  cosseno 0,9992 com a atenção atual.

| | Atual | SageAttention | Diferença |
|---|---|---|---|
| 480p, tempo no ComfyUI (12 × 6 vídeos) | 50,7 s (50,2–51,0) | 42,9 s (42,8–43,2) | **−15,3%** |
| 480p, por vídeo no worker novo | 51,7 s | 43,9 s | −15% |
| 720p, tempo no ComfyUI (2 × 2 vídeos) | 170,2 s | 125,4 s | **−26,3%** |
| Pico de VRAM (a GPU toda, com o filtro de imagem e o ComfyUI parado do entrypoint) | 39,7 GiB (480p) | 40,3 GiB (480p), 43,2 GiB (720p) | +0,6 GiB |
| 1º vídeo num ComfyUI novo | 54,7 s | 48,7 s (com a compilação do Triton) | |
| Vídeos certos (480p) | 4 de 6 | **3 de 6** | `cafe3` reprova (40,4) |

- **Semelhança com a atenção atual (mesma seed):** SSIM 0,987–0,992 em 480p e 0,982 em 720p
  (PSNR 37,5–40,9 e 34,4 dB), um pouco mais longe que o ruído de uma geração para outra
  (0,990–0,997). Nas folhas lado a lado (quadros 0, 40 e 80), as xícaras são iguais a olho.
- **A deriva de cor subiu nos vídeos do café:** 59,9 × 55,1 (`cafe1`), 60,7 × 59,3 (`cafe5`) e
  **40,4 em `cafe3`, que passa com a atenção atual** nas duas rodadas. É um vídeo na beira do
  limite (40), numa amostra pequena, mas é o que a regra 3(b) proíbe.

### Incidente: a fase 2 caiu no meio, e o pod foi retomado

Depois do 1º vídeo com RIFE, a fase 2 parou num `StopIteration`: o roteiro procurava o tempo do
mesmo vídeo entre os vídeos certos do worker novo, e esse vídeo (`cafe1`) tinha reprovado. O
detalhe da fase 2 (etapas e comparação) não foi gravado e o 2º vídeo com RIFE não rodou. Como os
vídeos estavam no disco do pod, o `medir_velocidade.py` foi parado de propósito antes de encerrar o
pod (o desligamento automático do agente seguiu armado, para 14:46), e o `retomar.py` pegou o
token do agente pela API do RunPod (sem imprimir), esperou o fim do roteiro, rodou o
`complemento.py` (inventário, antes × novo, SageAttention × atual, RIFE limpo), baixou tudo e
encerrou o pod às 13:53:57. O `no_pod.py` já está corrigido: o vídeo que não saiu não derruba mais
a fase e a fase que falha guarda o parcial. As etapas da fase 2 vieram do log do worker
(`medidas.log`).

## Decisão (pelas regras escritas antes)

1. **Worker novo: fica ligado por padrão.** A GPU parada entre vídeos caiu 98% (2,03 → 0,04 s), o
   tempo por vídeo caiu 4,8% e o resultado é o mesmo. A regra 1(a) pedia o mesmo SHA-256, o que o
   ComfyUI não dá nem repetindo o mesmo vídeo; ficou valendo o mesmo resultado e o SSIM no nível do
   ruído.
2. **Aquecer: fica ligado** (só age com a fila vazia): o vídeo depois do aquecimento saiu no tempo
   do quente (50,7 s).
3. **SageAttention 2.2: não entra agora.** É 15% mais rápido em 480p (no limite da regra 3(a)) e 26% em 720p, e a olho os
   vídeos são iguais, mas um vídeo a mais reprovou na conferência (regra 3(b)). Uma reprovação a
   mais em cada 6 vídeos custaria mais (o vídeo é refeito) do que os 15% economizam. Próximo passo,
   se o dono quiser: um teste maior, as duas atenções com as mesmas seeds em ~24 vídeos de 6 a 8
   fotos (~40 min de GPU, ~US$ 0,75), para ver se a taxa de reprovação muda de verdade. Se não
   mudar, vira ADR. Para produção, ainda faltaria: o wheel no espelho R2 com SHA-256 (sm89, e sm120
   para a RTX PRO 6000), `gcc` no pod (a quantização do SageAttention usa o Triton, que compila na
   1ª chamada) e o worker tratar "Error running sage attention" como falha (o ComfyUI volta sozinho
   para a atenção normal).
4. **RIFE ×2: vira proposta**, como opção e com decisão do dono ([ADR 0013](../../../docs/adr/0013-interpolacao-rife.md)):
   +4,7 s em média por vídeo 480p de 5 s (+9% de GPU, no limite da regra), sem fantasma visível
   nos movimentos lentos testados.
