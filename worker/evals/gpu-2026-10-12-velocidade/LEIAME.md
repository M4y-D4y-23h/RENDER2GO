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

## Resultado

(preenchido depois da medição)
