# ADR 0013: vídeo a 32 fps com interpolação RIFE ×2

- **Status:** proposto (12/10/2026), pela regra escrita antes da medição em GPU que o dono pediu ("SageAttention 2.2 e interpolação"). Depende da decisão do dono: muda o vídeo entregue.
- **Contexto:** o Wan 2.2 gera a 16 fps (16·d + 1 quadros). Movimento de câmera a 16 fps pode parecer travado, e as redes sociais tocam a 30 fps. O ComfyUI v0.38.0, a versão fixada, já tem os nós `FrameInterpolationModelLoader` e `FrameInterpolate` (RIFE e FILM). Os pesos RIFE v4.25 (Practical-RIFE, licença MIT, 22,7 MB) estão em `Comfy-Org/frame_interpolation`, revisão `219da3c9d8c357ceaf457fc1d5932c6e861b8dee`, SHA-256 `1505884b9bdae956795430d2a70f7e2317b2abd8f130f8cfdb35a5759f909481`. O GIMM-VFI ficou de fora (licença não comercial).
- **Medição** (`worker/evals/gpu-2026-10-12-velocidade/`, L40S, 480p de 5 s, o mesmo vídeo sem e com, na mesma GPU quente): **+4,4 s e +5,0 s** por vídeo (+9% de GPU; a interpolação em si leva ~2 s e o resto é codificar 161 quadros em vez de 81), arquivo 9–10% maior, VRAM igual. Nas folhas, os quadros do meio não têm fantasma nos dois movimentos lentos testados (aproximação e giro).

## Decisão proposta

1. **Template novo** `i2v_r1v2.v2` (ComfyUI v0.38.0, o mesmo), com o RIFE ×2 entre o VAE e o vídeo e **duas saídas**: o vídeo de 16 fps, onde o worker faz a conferência do 1º quadro, a da continuidade e o filtro de imagem (os limites foram calibrados a 16 fps; a 32 fps, o salto de um corte se divide em dois e passaria abaixo do limite), e o de 32 fps, que é o entregue e também passa pelo filtro de imagem.
2. **Pesos** no `models.lock.json` e no espelho R2, com bytes e SHA-256, baixados para o disco local do pod como os outros modelos.
3. **Para quem:** o dono escolhe entre (a) todos os vídeos a 32 fps, no preço atual (+~US$ 0,0014 por vídeo de 480p na L40S); (b) uma opção no pedido ("vídeo mais fluido"), com preço próprio; ou (c) não adotar.
4. **Antes de aceitar:** medir em 720p e com movimento rápido, texto e rótulos (onde a interpolação mais erra), e conferir os vídeos de 32 fps com o filtro de imagem.

## Consequências

- ~9% a mais de GPU e ~10% a mais de armazenamento por vídeo em 480p (em 720p, a medir).
- A conferência continua valendo como hoje, no vídeo de 16 fps.
- O acabamento na CPU (`acabamento --interpolar`, docs/06) continua para os lotes do modo serviço; este ADR é só a fila do site.
