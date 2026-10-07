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

## 6. Subida do pod, volume e venv (medido em 07/10, `worker/evals/gpu-2026-10-07-volume/`)

**Download em partes (sempre ligado):** o aria2 baixa cada arquivo com 8 conexões, 4 arquivos por vez, e o SHA-256 é conferido em paralelo. A instalação caiu de **~5,5 min para ~1,5 min**, a ~750 MB/s em vez de ~140 MB/s. Do comando ao pod pronto: ~3 min, contra ~6,5 min antes.

**Cache no volume de rede (`--volume <id>`, opcional):**
- o pod sobe no datacenter do volume;
- o 1º pod baixa tudo e, depois do PRONTO, grava em `/workspace/gpu2go-cache`: modelos, compilador e o **ambiente inteiro** (venv + ComfyUI + Ollama) num `tar.zst` chaveado pelo `requirements.lock` e pelo Dockerfile;
- os pods seguintes **copiam para o disco local** e conferem o SHA-256 (regra do projeto: nunca ler direto do volume). Cópia ruim é apagada e refeita da origem.

Assim o "venv no volume" foi feito sem quebrar a regra do volume não POSIX: o volume guarda um arquivo único, extraído no disco local.

**Ganho medido:** só ~14 s a mais (77 s contra 91 s), porque o volume leu a ~580 MB/s, mais devagar que a origem nesse host. O valor do cache é a **reserva**: a subida não depende de Hugging Face, GitHub e PyPI, nem da rede do host.
- Custo: ~US$ 4,20 por mês (60 GB).
- Prende o lote a um datacenter, com menos vagas de GPU.
- Datacenters com volume (em 07/10): AP-JP-1, CA-MTL-1/3/4, EU-FR-1, EU-RO-1, EUR-IS-1/3, EUR-NO-1/2, US-CA-2, US-CO-1, US-IL-1, US-MO-2, **US-NC-2** (usado), US-NE-1, US-TX-3. O US-NC-1 não aceita volume.

**Decisão (07/10):** sem volume do RunPod. O Network Volume de teste foi apagado. A reserva fica no **R2** (espelho dos modelos, `worker/README.md`, "Lote na nuvem"):
- não prende datacenter;
- custa ~US$ 0,75/mês;
- o aria2 baixa da origem e do espelho ao mesmo tempo;
- é o mesmo armazenamento que vai entregar os vídeos aos clientes (ADR 0003).

Testado sem GPU, com S3 local: Hugging Face fora do ar com o espelho bom baixou do espelho; espelho corrompido com a origem boa foi detectado pelo SHA-256 e baixado de novo.

**Global Volume (verificado em 07/10, não medido):** só pode ser anexado a pods pelo painel do RunPod, em "Configure Pod with volume" ([docs](https://docs.runpod.io/storage/globalvolume/globalvolume-pods)). A API de pods, v1 e v2, não tem esse campo (só `globalNetworking`), e a API compatível com S3 é só de network volumes ([docs](https://docs.runpod.io/storage/s3-api)). Por isso o `lote_nuvem`, que cria os pods pela API, não consegue usá-lo. A documentação agora aceita `globalVolumes` em endpoints serverless, o que corrige o `docs/01`, item 12.

**Pod reservado ligado (não feito):**
- pod parado perde o disco do container, e ao religar a GPU pode não estar livre no mesmo host;
- uma RTX PRO 6000 ligada direto custa ~US$ 50/dia, enquanto uma subida nova custa ~US$ 0,10 (~3 min).

Pod fixo só vale com fila constante de clientes (ADR 0003, fases 3 e 4).

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
