# Vídeos quase instantâneos: o que é possível e o que custa

Pedido do dono (12/10/2026): "avaliar como podemos gerar vídeos praticamente instantâneos em qualquer resolução". Esta é a avaliação, com os números já medidos no projeto (`docs/06`, `docs/07`, `worker/evals/`). Nada aqui foi implementado além do que está marcado como **feito**.

## 1. Resposta curta

- **Instantâneo, em qualquer resolução, não dá com o modelo de hoje** (Wan 2.2 I2V 14B, 4 passos). Na GPU mais rápida já medida (RTX PRO 6000), um vídeo de 5 s leva **28 s em 480p e 93 s em 720p** só de geração; na L40S, 55 s e 180 s. A conta cresce com a resolução (720p ≈ 3,3× 480p) e com a duração (tempo ∝ duração^1,6).
- **O que dá para ter:** o **1º vídeo em ~1–1,5 min** depois do pedido (sem esperar pod) e, a partir daí, **um vídeo a cada ~30–55 s por GPU em 480p**, com várias GPUs em paralelo nos pedidos grandes. Para chegar lá, o que mais pesa hoje não é a geração: é a **subida do pod** (~4 min) e as esperas do processo (revisão, agrupamento do teste).
- **Para "instantâneo" de verdade (segundos)**, só com outro tipo de modelo (destilado, de poucos passos, ou de "tempo real"), com outra qualidade e outras licenças: é um projeto à parte, com ADR e teste em GPU (seção 5).

## 2. Onde está o tempo hoje (pedido de 1 vídeo de 5 s, 480p, L40S)

| Etapa | Tempo | O que reduz |
|---|---|---|
| Revisão humana (planos teste e pago) | minutos a horas | aprovar logo; aprovação automática para clientes de confiança (ADR 0004 diz como) |
| Agrupamento do teste gratuito (sem pod ligado) | até 10 min | `RENDER2GO_AGRUPAR_TESTE_MIN` menor (ex.: 2) |
| Filtro das fotos (desde 12/10) | ~2–4 s por foto, no servidor | a 1ª foto sai em segundos; o pod sobe junto |
| **Subida do pod** (sem pod ligado) | **~4–5 min** do zero; **~1 min** na volta da hibernação | seção 3 |
| Tradução do pedido | segundos, durante a subida | já corre em paralelo (estado `AMBIENTE`, desde 08/10) |
| **Geração** | **55 s** (L40S) / **28 s** (RTX PRO 6000) | seção 4 |
| Conferência, filtro e envio | ~1–3 s (desde 12/10, durante a geração do próximo) | **feito** (a GPU não espera) |

## 3. Tirar a subida do caminho

| Opção | 1º vídeo | Custo | Situação |
|---|---|---|---|
| Subida do zero (hoje) | ~4–5 min (medido em 08/10: 4,2 min na RTX PRO 6000 e 5,1 min na L40S, vídeo de 3 s) | só o tempo ligado | — |
| **Pods hibernados** (ADR 0011) | **~2 min** (pronto em ~1 min + geração) quando o host tem a GPU livre | ~US$ 14/mês por pod parado | **feito**; corrigido em 12/10 para não perder o volume (ADR 0011, atualização). Ligar com `RENDER2GO_PODS_HIBERNADOS=2` ou 3 |
| Pod quente mais longo | ~1 min enquanto ele espera | ~US$ 0,018 por minuto a mais na L40S | `RENDER2GO_POD_OCIOSO_MIN` (padrão 3) |
| Pod ligado no horário comercial | ~1 min | ~US$ 288/mês (L40S, 9h–21h, dias úteis; `docs/11`, seção 3.4) | opção do dono |
| Volume de rede com tudo instalado (⚡ de alta performance) | ~1,5–2 min para **qualquer** pod novo daquele datacenter | ~US$ 4/mês (padrão, 60 GB) ou mais (alta performance); prende a um datacenter | **a medir** (`docs/07`, seção 12) |
| Serverless com FlashBoot | segundos com um worker quente; minutos no frio | ~2× o preço por segundo; imagem com os modelos (~50 GB); muda a fila (ADR 0003) | ADR novo, para quando houver fila constante |

**Recomendação:** ligar a hibernação (2–3 pods) agora que ela não perde o volume; medir o volume de rede de alta performance numa subida; serverless só com volume de pedidos que pague o preço maior.

## 4. Gerar mais rápido

| Opção | Ganho | Custo e risco | Situação |
|---|---|---|---|
| **GPU mais rápida:** RTX PRO 6000 (96 GB) | **~2×** (28 × 55 s em 480p; 93 × 180 s em 720p) | o mesmo custo por vídeo (~US$ 0,02), mas a subida e o pod quente custam mais por hora (US$ 2,49 × 1,09) | é a 3ª da lista (`RENDER2GO_GPUS`); pô-la em 1º troca custo fixo por velocidade |
| H100/H200/B200 | estimativa ~25–35 s em 480p | US$ 3,5+/h, custo por vídeo maior | não medido |
| **Uma GPU por vídeo** (vários pods) | o lote termina N× mais rápido; cada vídeo, igual | custo por vídeo igual | **feito** (o orquestrador sobe até `RENDER2GO_MAX_PODS`) |
| Várias GPUs num vídeo só (paralelismo de sequência, ex.: xDiT/USP) | o ganho cresce com o número de GPUs, mas abaixo do linear (comunicação entre elas); não medido aqui | custo por vídeo maior; não existe no ComfyUI v0.38.0: exige outro runtime | pesquisa; só para "um vídeo urgente" |
| Dois vídeos na mesma GPU | **8% pior** (medido) | — | descartado |
| **SageAttention 2.2** | **−15% em 480p e −26% em 720p** (medido em 12/10, L40S) | um vídeo a mais reprovou na conferência (deriva 40,4, limite 40) | não entra até o teste maior (~24 vídeos, ~US$ 0,75) |
| Menos quadros + RIFE ×2 | gerar metade dos quadros custa ~1/3 do tempo (tempo ∝ quadros^1,6); RIFE soma ~5 s | o modelo foi treinado a 16 fps: a metade dos quadros vira câmera lenta, ou exige outra cadência; risco de "fantasmas" | **a medir**; o RIFE ×2 de 16 → 32 fps já foi medido (+9% de tempo, ADR 0013 proposto) |
| Gerar em 480p e ampliar para 720p (ou mais) | a "alta" ficaria ~3× mais rápida | perde o que a 720p nativa corrige (rótulo e cor do produto estáveis, `docs/07`, seção 3) | possível como opção "720p rápida", com o `acabamento` (ampliação) já existente |
| Menos passos | já são 4 (o mínimo prático do ajuste atual) | menos que isso degrada | — |
| Cache entre passos (TeaCache, MagCache) | feitos para 20–50 passos; com 4, pouco ou nada | — | não vale |
| `torch.compile` | alguns % | recompila a cada tamanho; pode mudar o resultado | fora, por decisão do dono (12/10) |
| `--enable-triton-backend` | estimativa de 2–5% | — | não medido |
| A GPU não espera + aquecimento | GPU parada entre vídeos de 2,03 s para 0,04 s; 1º vídeo de um pod novo sem os ~5 s de carga | — | **feito** (12/10) |

## 5. "Quase instantâneo" de verdade: outro modelo

Modelos de poucos passos ou destilados geram em segundos em GPUs grandes, mas são outra decisão:

- **Wan 2.2 TI2V 5B** (Apache 2.0): menor e mais rápido que o 14B, com qualidade menor; serviria para uma "prévia" de baixa resolução enquanto o vídeo final é gerado.
- **LTX-Video / LTX-2** (Lightricks): feitos para velocidade; licença própria, grátis abaixo de US$ 10 mi de faturamento/ano e que já mudou duas vezes em 2026 (`docs/12`, seção 3.3).
- Qualquer troca é um ADR com teste em GPU, com as mesmas receitas, comparando qualidade, tempo e taxa de reprovação na conferência e no filtro.

**Prévia em segundos (percepção de velocidade):** mostrar ao cliente, logo que o pedido começa, a foto preparada no formato do vídeo e o andamento; e, se valer a pena, um rascunho de baixa resolução (ex.: TI2V 5B a 320p, 2–3 s de vídeo) antes do vídeo final. Custa GPU a mais por pedido; faz sentido com pod quente.

## 6. Plano sugerido (do mais barato ao mais caro)

1. **Agora, sem mudar a qualidade:** hibernação com 2–3 pods; `RENDER2GO_AGRUPAR_TESTE_MIN` menor; revisar pedidos assim que chegam (o painel mostra a fila de revisão).
2. **Medir (uma tarde de GPU, < US$ 5):** volume de rede de alta performance com tudo instalado; SageAttention no teste maior; RTX PRO 6000 em 1º para pedidos pequenos.
3. **Decidir com números:** pod no horário comercial ou serverless, quando houver fila constante; a troca para os modelos oficiais do Wan (`docs/12`), que também reduz vídeos barrados pelo filtro.
4. **Projeto à parte:** prévia de baixa resolução e modelos de poucos passos.
