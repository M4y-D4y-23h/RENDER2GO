# Sessão em GPU de 07/10/2026: RTX PRO 6000 (Blackwell, 96 GB)

- **Como rodou:** `tools/lote_nuvem.py` (fase 2), com as mesmas 8 fotos sintéticas SFW das rodadas de 06/10 e o pedido "a câmera se aproxima devagar do produto".
- **Máquina:** 1× RTX PRO 6000 Blackwell Server Edition secure, datacenter US-NC-1, US$ 2,09/h.
- **Resultado:** pod pronto em 6,5 min; 8 de 8 vídeos; 11,8 min do comando ao fim; ~US$ 0,41.

| Por vídeo (rápida, 5 s) | RTX PRO 6000 | L40S (06/10) | RTX 4090 (06/10) |
|---|---|---|---|
| Tempo | **~28 s** (27,6–31,9; o 1º carrega os modelos) | ~55 s | ~80 s |
| US$/h (secure) | 2,09 | 1,09 | 0,74 |
| **Custo por vídeo** | **~US$ 0,017** | ~US$ 0,017 | ~US$ 0,016 |

**Conclusão:** a RTX PRO 6000 é ~1,9× mais rápida que a L40S pelo mesmo custo por vídeo. Um lote de 50 fotos numa GPU sai em ~24 min, em vez de ~46. A VRAM extra (96 GB) não é o motivo: o vídeo usa ~40 GB. O ganho vem do cálculo e da memória mais rápidos (Blackwell). PyTorch 2.8 +cu128 reconheceu a GPU (sm_120) sem ajuste.

## Achados de operação

- **Restrição de versão de CUDA:** pedir o pod com `allowedCudaVersions` (a API aceita até "13.0") dava "sem vaga", porque os hosts dessa GPU têm driver mais novo. O pedido agora não restringe, e o pod confere `cuInit` e driver ≥ 12.8 ao ligar.
- **Rede por datacenter:**
  - 5 hosts seguidos baixavam do Hugging Face a 2–3 MB/s, o que daria 3,5 h só de modelos;
  - US-KS-2 passou num teste curto (73,8 MB/s numa conexão), mas entregou ~12 MB/s no download real;
  - o teste de rede agora usa 4 conexões por 15 s e exige ≥ 40 MB/s; host lento é descartado em ~30 s por ~US$ 0,02;
  - US-NC-1 deu 329 MB/s no teste e ~140 MB/s no download, e baixou os modelos em ~4 min.
- **Custo dos descartes até achar o host bom:** ~US$ 0,30.

## Rodada 2 (07/10, tarde): 720p, 7 s e 2 vídeos ao mesmo tempo na mesma GPU

Três pods em paralelo, todos escolhidos pelo `lote_nuvem` com as proteções novas: teste de rede, troca de pod e memória de datacenter. O EUR-IS-2 (22 MB/s) foi descartado e anotado; os testes rodaram em US-NC-1 e US-NC-2.

| Teste | Vídeos | Por vídeo | Custo por vídeo (US$ 2,09/h) |
|---|---|---|---|
| A. 720p (alta), 5 s, 1 ComfyUI (`teste-A/`) | 8 | **~93 s** (92,8–95,6) | ~US$ 0,054 |
| B. 720p, 5 s, **2 ComfyUI na mesma GPU** (`--comfy-por-gpu 2`, `teste-B/`) | 8 | ~200 s cada, **~100 s de GPU por vídeo** | ~US$ 0,058 |
| C. 720p, **7 s**, pedido complexo (`teste-C/`) | 3 | **~160 s** | ~US$ 0,093 |

### Conclusões

- **Dois vídeos ao mesmo tempo na mesma GPU não compensam:** a vazão ficou 8% pior que um de cada vez. O render já ocupa a GPU inteira, então a VRAM sobrando (96 − 40 GB) não vira velocidade. Paralelismo que funciona é **uma GPU por vídeo** (mais pods ou pods com várias GPUs). `COMFY_POR_GPU` fica em 1; a opção existe só para teste.
- **720p custa 3,3× a qualidade rápida** por 2,25× mais pixels.
- **7 s custam 1,72× os 5 s**: o tempo cresce com a duração^1,6. As estimativas do `lote_nuvem` usam isso.
- **O texto não pesa:** o pedido complexo (luz, reflexos, poeira) não mudou o tempo, que depende só de resolução e duração.
- **720p corrigiu o perfume:** em 480p a cor e o rótulo mudavam no fim; em 720p ficaram estáveis do primeiro ao último quadro, mesmo em 7 s.
- **Resolução máxima:** 720p é o teto do template (área de 921.600 px) e a resolução de treino do Wan 2.2. Acima disso, `acabamento --altura 1920` amplia na CPU.

**Custo da rodada:** ~US$ 1,95 (A 0,68 + B 0,70 + C 0,52 + descartes ~0,05).
