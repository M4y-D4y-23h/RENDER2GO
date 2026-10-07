# 07 — Guia técnico: GPUs, pods, custos e qualidade (o que foi medido)

Consolida as medições de 05 a 07/10/2026. Detalhes e relatórios em `worker/evals/gpu-2026-*`. Comando de produção: `tools/lote_nuvem.py` (ADR 0003, fase 2).

## 1. GPUs (vídeo I2V de 5 s; RunPod secure)

| GPU | VRAM | US$/h | Rápida (480p) | Alta (720p) | Alta 7 s | Custo/vídeo rápida | Custo/vídeo alta |
|---|---|---|---|---|---|---|---|
| **RTX PRO 6000** (Blackwell) | 96 GB | 2,09 | **28 s** | **93 s** | **160 s** | US$ 0,017 | US$ 0,054 |
| L40S | 48 GB | 1,09 | 55 s | 180 s | — | US$ 0,017 | US$ 0,055 |
| RTX 4090 | 24 GB | 0,74 | 56–80 s | 213 s | — | US$ 0,016 | US$ 0,044 |

- **Ordem padrão:** RTX PRO 6000 → L40S → L40 → RTX 6000 Ada → RTX 4090, só secure. A RTX PRO 6000 tem o mesmo custo por vídeo da L40S em metade do tempo.
- **Community:** fora de uso. Os 3 hosts RTX 4090 que sorteamos tinham CUDA quebrado.
- **A40:** não serve (Ampere, sem fp8; o container também não subiu).
- **Pior caso para estimar:** GPU sem medição usa 80 s (rápida) e 213 s (alta).

## 2. O que não acelera (medido)

- **2 vídeos ao mesmo tempo na mesma GPU:** 8% **pior** (100 s de GPU por vídeo contra 93 s), mesmo com VRAM sobrando. O render já ocupa todo o cálculo. Paralelismo de verdade é **uma GPU por vídeo**: `--pods N` ou `--gpus-por-pod N`. A opção `--comfy-por-gpu` existe só para teste.
- **Fila 2 num ComfyUI:** igual à fila 1.
- **`--fast fp8_matrix_mult`:** sem ganho na L40S.
- **8 passos ou CFG 1,5–2:** até 3,3× o tempo sem ganho visível.
- **VRAM extra:** um vídeo usa ~40 GB (2 experts) e 720p chega a ~44 GB. A VRAM só precisa caber.
- **Ollama com VRAM reservada:** no lote ele compila o pedido **uma vez** (~5 s), então manter o modelo carregado não muda nada. No uso interativo, sim.

## 3. Duração, resolução e texto

- **Tempo ∝ duração^1,6:** 7 s custam 1,72× os 5 s, porque a atenção cresce com o número de quadros.
- **720p custa ~3,3× a rápida.** O teto do modelo e do template é 921.600 px (1280×720). Acima disso, `acabamento --altura 1920` amplia na CPU.
- **O texto do pedido não pesa no tempo:** um pedido complexo (luz, reflexos, poeira) levou o mesmo que um simples.
- **720p corrigiu defeitos:** o rótulo e a cor do perfume, que deformavam em 480p, ficaram estáveis em 720p, mesmo em 7 s.

## 4. Subida de pod: o que dá errado e as proteções

| Problema visto | Proteção no `lote_nuvem` |
|---|---|
| Host com CUDA quebrado (`cuInit` 999) | Teste de `cuInit` e de driver ≥ 12.8 no 1º minuto; pod descartado |
| `allowedCudaVersions` excluía GPUs novas ("sem vaga" falso) | O pedido não restringe; o pod confere o driver |
| Rede de 2–3 MB/s (37 GB = 3,5 h) | Teste de rede com 4 conexões por 15 s, mínimo 40 MB/s; descarte em ~30 s (~US$ 0,02) |
| Teste curto aprovou host que entregava 12 MB/s | Teste sustentado; prazo de instalação de 18 min |
| Datacenter ruim repetido | Memória de datacenters lentos por 7 dias (`~/.cache/gpu2go/datacentros.json`); só pede IDs que a API REST aceita |
| Pod que renderiza devagar | Vazão medida a cada pacote; se for > 1,6× o medido, o pod é trocado (até 2 trocas; os vídeos já feitos ficam) |
| Sem vaga | Espera 60 s antes de gastar outra troca |
| Proxy do RunPod com 404 transitório | Repetição só de respostas que não vêm do agente |
| Comando morto (kill/timeout) | SIGTERM tratado como Ctrl+C; os pods são encerrados |

Datacenters medidos para RTX PRO 6000: **bons** US-NC-1 (267–329 MB/s) e US-NC-2 (285 MB/s); **lentos** EUR-IS-2 (22 MB/s), US-KS-2 (12 MB/s no download real) e 5 hosts sem datacenter registrado (2–3 MB/s).

## 5. Desligamento automático e custo

Cada pod nasce com desligamento automático no agente, contado do 1º segundo. O prazo é **o menor** entre:
1. 2 h (regra do projeto);
2. 2× a estimativa do lote + 20 min;
3. o que `--orcamento-usd` paga, com 20% de folga, no preço por hora dos pods;
4. o que o **saldo da conta** paga, menos a reserva (US$ 1 ou 10% do saldo).

Por isso pod caro para mais cedo que pod barato.

- Se o saldo não paga a estimativa, ou se o prazo dá menos de 15 min, **nada é criado**.
- Durante o lote, se o gasto passa de 1,5× o orçamento, o lote para e encerra os pods.
- O lote deixa de mandar pacotes 10 min antes do prazo, para baixar tudo; o que sobrar é retomado na próxima execução.

## 6. Pod reservado, volume e venv (pedido de 07/10)

**Feito: cache no volume de rede.** Com `--volume <id>`:
- o pod sobe no datacenter do volume;
- o 1º pod baixa os modelos (37 GB) e o modelo do compilador e os guarda em `/workspace/gpu2go-cache`;
- os pods seguintes **copiam do volume para o disco local** (regra do projeto: nunca ler modelo direto do volume) e conferem o SHA-256.

Isso tira o download do Hugging Face e a dependência da rede do host.
- Custo: ~US$ 0,07/GB/mês, ~US$ 3–4 por mês para ~50 GB.
- Escolha um datacenter **bom**, como o US-NC-1.

**Não feito, e por quê:**
- **venv dentro do volume:** o volume global não é POSIX (sem `rename`/`chmod`). O `uv`/`pip` usam `rename` e bits de execução, e importar Python por rede deixa cada subida lenta. O ambiente segue sendo instalado no disco local, ~3–4 min, em paralelo com a cópia dos modelos.
- **Pod reservado ligado (parado e religado):**
  - pod parado perde o disco do container, e ao religar a GPU pode não estar livre no mesmo host;
  - uma RTX PRO 6000 ligada direto custa ~US$ 50/dia.

  Com o volume, o mesmo ganho vem de **subir um pod novo que copia do volume** (~2–3 min de subida), sem pagar GPU ociosa. Pod fixo só vale com fila constante de clientes (ADR 0003, fases 3 e 4).

## 7. Qualidade "rápida" × "alta" como padrão

| | Rápida (480p) | Alta (720p) |
|---|---|---|
| Custo por vídeo de 5 s | ~US$ 0,017 | ~US$ 0,054 (3,3×) |
| Tempo (RTX PRO 6000, 1 GPU) | 28 s; 50 fotos ≈ 24 min | 93 s; 50 fotos ≈ 78 min (≈ 20 min com 4 pods) |
| Qualidade | Boa em celular; produto com rótulo ou líquido pode deformar | Rótulo e cor estáveis; nitidez para telas grandes |
| Defeitos vistos | Perfume mudou de cor | Nenhum nos testes |

**Recomendação:** **alta como padrão dos pacotes pagos** e rápida para prévias e amostras de venda. O custo de GPU continua abaixo de 3% de um preço de R$ 2–5 por vídeo. O tempo extra se resolve com mais pods (`--pods 4`) sem aumentar o custo por vídeo.

## 8. Comandos de referência

```bash
# estimar sem gastar
python tools/lote_nuvem.py FOTOS --saida ENTREGA --prompt "..." --qualidade alta --simular
# lote real: 4 pods, orçamento máximo, acabamento local
python tools/lote_nuvem.py FOTOS --saida ENTREGA --prompt "..." --qualidade alta --pods 4 \
  --orcamento-usd 10 --interpolar --altura 1280
# com cache no volume de rede (datacenter bom)
python tools/lote_nuvem.py FOTOS --saida ENTREGA --prompt "..." --volume <id-do-volume>
# encerrar pods de uma execução interrompida
python tools/lote_nuvem.py --saida ENTREGA --limpar
```
