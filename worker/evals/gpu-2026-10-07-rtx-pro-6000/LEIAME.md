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
