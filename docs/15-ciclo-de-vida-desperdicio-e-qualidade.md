# 15. Ciclo de vida dos pods, desperdício, capacidade e qualidade (revisão de 14/10/2026)

Pedido do dono de 14/10/2026: melhorar a velocidade de geração e entrega, a subida e o encerramento dos pods ociosos e a qualidade comercial, **preservando o modelo atual** (Wan I2V R1 v2, 4 passos, CFG 1, receitas v4, tradutor v6). Base: `main` em `0a8149b` (revalidada no código antes de cada mudança). Branch: `claude/magical-lamport-4rrms9`. Nada publicado, nenhum pod criado, nenhuma configuração com custo ligada.

O objetivo de cada mudança é **tempo e custo por vídeo aproveitável** e o tempo até o 1º vídeo entregue; nenhum ganho é afirmado sem medição. Os testes desta revisão são locais (provedores falsos, relógio simulado, SQLite e Postgres): **não são medições em GPU**. O que depende de GPU está na seção 6, com o protocolo e o custo previsto.

## 1. Ciclo de vida dos pods e orçamento (Etapa 1; ADR 0016)

### 1.1 O que estava errado (reproduzido)

`worker/evals/ciclo-de-vida-2026-10-14/` tem o script e as saídas antes (no `0a8149b`) e depois:

| Achado | Antes | Depois |
|---|---|---|
| `lote_nuvem._encerrar` marcava `vivo=False` e `fim` antes do `DELETE`; com o RunPod falhando, o pod saía do `pods.json` | 2 chamadas locais → **1 chamada ao provedor**, lista vazia gravada | 2 chamadas ao provedor; o pod fica registrado (`encerramento: falhou`) e a reconciliação insiste |
| O teto por hora somava só ativos e os que subiam | gravador terminando o cache (até 15 min) e fechamento que falhou fora do teto | `faturaveis_usd_h`: tudo o que pode cobrar, mais as subidas reservadas |
| `_retomar_algum` contava falhas por chamada | 3 chamadas no mesmo estado: falhas 1, 2 e **o descarte do pod com o disco** | 1 falha por episódio de demanda; o mesmo pod só de novo depois de 180 s |

### 1.2 O que mudou

- **Estados** (`lote_nuvem.Pod.estado`): `ativo`, `drenando`, `encerrando`/`parando`, `encerrado`/`parado`; o fechamento (`pedido`, `falhou`, `aceito`, `confirmado`) e o que confirmou (`404`, `TERMINATED`, `EXITED`, `aceite`).
- **Contrato do RunPod** (documentação conferida em 14/10/2026): `DELETE /pods/{id}` responde 204; `GET /pods/{id}` dá o `desiredStatus` (o estado esperado) e 404 se o pod não existe; **não há o instante do fim da cobrança**. O custo de um pod fechando é estimado do aceite (limite de baixo, `gasto(minimo=True)`) até a observação (limite de cima, `gasto()`).
- **Reconciliação** idempotente, com intervalos de 5 a 300 s; o `DELETE` aceito que continua `RUNNING` depois de 120 s vai de novo; resposta inconclusiva não muda nada; depois de um reinício, o `pods.json` vira inventário (`carregar_pendentes`). Só os pods deste ambiente; o desligamento automático do agente continua como estava.
- **Relatório de GPUs:** `geradores.fechamento`, `fechamento_em` e `confirmacao`; `desligado_em` é o fim observado; o fechamento pendente conta como ligado (estimado) e o total `fechando` mostra quantos.
- **Teto por hora:** `faturaveis_usd_h` (ligados, drenando, fechamento não observado e subidas reservadas sem pod). A reserva vira o pod quando o RunPod o cria; nunca contam os dois. Um pod drenando não conta como capacidade.
- **Hibernados:** episódio de demanda (`demanda.json`), intervalo `nao_antes` (no `hibernados.json`), reserva com a trava; disco que não serve sai na hora; sem hibernado que volte, a subida do zero.
- **Cache opcional com orçamento:** `RENDER2GO_CACHE_MAX_USD` (padrão US$ 0,30) limita a espera do gravador pelo cache, além dos 15 min e do desligamento automático; o que não terminou não é usado.

### 1.3 Configurações novas e rollback

| Variável | Padrão | Efeito | Rollback |
|---|---|---|---|
| `RENDER2GO_CACHE_MAX_USD` | 0,30 | a espera do cache não passa do que esse valor paga na GPU do pod (L40S: os 15 min de sempre) | `100` volta aos 15 min em qualquer GPU |

Constantes (no código): `ENC_BACKOFF_S` (5, 15, 30, 60, 120, 300 s), `ENC_REENVIO_S` (120 s), `ENC_FIM_LOTE_S` (120 s), `ESPERA_RETOMADA_S` (180 s). Banco: três colunas novas em `geradores` (criadas sozinhas na partida, SQLite e Postgres; vazias no código de antes).

### 1.4 Testes

`worker/tests/test_ciclo_de_vida.py` (12) e `servico/tests/test_orquestrador_ciclo.py` (11 × SQLite e Postgres): falha antes e depois do aceite (resposta perdida), 404, `TERMINATED`, respostas inconclusivas, reinício, fechamento concorrente, `--limpar` com pendência, custo até a observação, orçamento com pod drenando e com fechamento não observado, reservas sem contagem dupla, episódios de demanda, intervalo depois de reinício, retomadas simultâneas, corrupção do volume, descarte de hibernado que falha e o orçamento do cache.

## 2. Geração desperdiçada (Etapa 2; ADR 0017)

### 2.1 O que estava errado (revalidado no código)

| Onde | Antes | Depois |
|---|---|---|
| `fila._conferir_e_enviar` | a falha final do `media.upload` (3 tentativas em ~3 s) concluía com `repetir=True` e apagava a pasta: o vídeo aprovado voltava para a GPU, com outra seed | o mesmo vídeo (SHA-256 conferido) é enviado de novo por até 10 min, com URLs novas se a assinatura vencer; só então volta, como falha de transporte, com a mesma seed |
| conclusão | resposta perdida → a repetição levava 409 (sem cobrança dupla, mas sem saber que deu certo) | a mesma tentativa recebe a mesma resposta (`repetida`) |
| dono do job | só o nome do worker; o mesmo pod podia concluir a tentativa nova com o resultado da antiga; o vídeo ia sempre para o mesmo objeto | identificador da tentativa em todas as rotas; objeto por tentativa, publicado pela API |
| prazos | lease de 900 s renovado só no `iniciar`; render de 1800 s; o polling recomeçava a espera quando o WebSocket caía | prazo absoluto de 1 h por tentativa; renovação só com progresso; um prazo só no ComfyUI |

### 2.2 Testes

- `servico/tests/test_entrega.py` (SQLite e Postgres na API): tentativa antiga sem conclusão nem sobrescrita depois do prazo vencido; resposta perdida respondida de novo com uma cobrança só; publicação que falha (503) sem refazer o vídeo; falha de transporte com a mesma seed; renovação só com a tentativa e progresso, até o prazo absoluto; URLs novas só para a tentativa atual; no worker (API falsa): falha intermitente de envio, URL vencida, confirmação perdida, entrega esgotada, tentativa perdida, artefato corrompido, disco cheio, reinício do worker com a entrega pendente e o batimento que perde a tentativa; **de ponta a ponta** (worker e API de verdade, SQLite e Postgres): duas falhas de envio e a resposta da conclusão perdida → uma geração, uma cobrança, o objeto da tentativa publicado e apagado.
- `worker/tests/test_comfy_prazo.py` (relógio simulado): o WebSocket que cai aos 95 s de 100 não recomeça a espera (antes: ~195 s); a última olhada no `/history` vem antes do erro; o polling respeita o prazo.
- `servico/tests/test_armazenamento.py`: o `CopyObject` assinado (com o `x-amz-copy-source` na assinatura) e o erro no corpo de uma resposta 200.

### 2.3 Configurações e rollback

Sem variável nova. Constantes: `fila.ENTREGA_MAX_S` (600 s), `ENTREGA_ALEM_ATE_S` (300 s), `ENTREGAS_MAX` (8), `ENTREGAS_MAX_BYTES` (2 GB), `BATIMENTO_S` (60 s); `db.LEASE_S` (900 s, como antes), `PRAZO_TENTATIVA_S` (3600 s), `SEM_PROGRESSO_S` (600 s). Banco: nove colunas novas em `jobs`, criadas na partida. Rollback: voltar o commit (ADR 0017, Consequências).

## 3. Métricas, capacidade e ociosidade (Etapa 3)

### 3.1 Métricas (o que faltava; o que já existia continua)

Já existiam: no pod, as etapas de cada vídeo no log do worker (`fila.Preparado.etapas`) e os tempos da instalação no log do orquestrador; no banco, os horários de cada pod (`geradores`: criação, PRONTO, 1º vídeo, fim da fila, desligamento, GPU, datacenter, preço) e o tempo de pod de cada vídeo (`jobs.segundos`). Entraram:

- **por vídeo** (`jobs.etapas`, JSON só com números: preparo, espera da GPU, GPU, render do ComfyUI, conferência, filtro), a fila (`tentativa_em − criado`), a tentativa, o SHA-256, as tentativas a mais e as falhas de envio (Etapa 2);
- **por pod** (`geradores`): o tipo de subida (`fria` ou `retomada`), quando o agente respondeu, a versão do ambiente e dos modelos (`versao_hibernacao`), o fechamento pedido e o observado (Etapa 1);
- **no painel** (rota `GET <painel>/api/metricas?dias=14`, só o dono; sem tela nova): subidas por GPU, datacenter e tipo; etapas de cada vídeo por GPU, resolução e duração, com p50 e, com n ≥ 20, p95; tentativas a mais, falhas de envio e custo por vídeo entregue. Nada do pedido, da foto, do cliente ou de URLs.

### 3.2 Capacidade da fila (`render2go_servico.capacidade`)

| Antes (revalidado no código) | Depois |
|---|---|
| `Banco.trabalho` somava a tradução do pedido nos vídeos pendentes | vídeos (`pendentes`, `em_andamento`) e traduções (`traducoes`) separados; a tradução conta 60 s; um pedido que só espera a tradução ainda pede um pod |
| cada vídeo em andamento contava como um vídeo inteiro | conta o que falta (`andamento_s`, desde que foi para a GPU: `jobs.gpu_em`), com piso de 25% |
| o tempo por vídeo era sempre o da 1ª GPU da tabela | o histórico de cada GPU, qualidade e duração (mediana com ≥ 5 amostras, dos últimos 14 dias), só dentro de 0,5–2× a tabela; sem amostras, a tabela ou o pior caso |
| cada pod ligado contava 1, qualquer GPU | pela velocidade da GPU dele (uma RTX PRO 6000 vale ~2 L40S); nunca mais que `RENDER2GO_MAX_PODS` pods |

A ordem da fila, a justiça entre clientes, o saldo, o filtro das fotos e a aprovação não mudam: só o número de pods pedidos.

### 3.3 Memória dos datacenters

- **Gravação com trava** (threads e `flock` entre processos) **e atômica** (tmp + rename): antes, duas subidas ao mesmo tempo podiam perder uma anotação ou deixar o arquivo cortado (teste com 24 threads).
- **Recuperação controlada:** uma medida ruim tira o datacenter só por 6 h (antes: 7 dias); duas ruins na semana, sem uma boa depois, por 7 dias; uma medida boa depois devolve.

### 3.4 Ociosidade e drenagem (`capacidade.PoliticaOciosidade`)

- **Padrão preservado:** fixa em 180 s (`RENDER2GO_POD_OCIOSO_MIN`, mínimo de 60 s pelo ambiente, como antes).
- **Histerese opcional, desligada:** `RENDER2GO_POD_OCIOSO_LONGO_MIN` (ex.: com `RENDER2GO_POD_OCIOSO_MIN=1` e `..._LONGO_MIN=3`): se a demanda volta até 10 min depois de um pod sair por falta de pedido, os próximos 3 episódios de demanda usam o tempo longo.
- **Drenagem:** o pod só fecha depois de o worker terminar as entregas pendentes (Etapa 2) e, se for o gravador, o cache com o orçamento (Etapa 1). A hibernação mantém o disco cobrado e não reserva GPU: o padrão continua 0 hibernados.

### 3.5 Simulação (não é medição)

`render2go_servico.simulacao`, 30 sementes por cenário; parâmetros e tabela completa em `worker/evals/simulacao-2026-10-14/`. O p95 só com n ≥ 20.

| Cenário | Comparação | 1º vídeo p50 / p95 (s) | Pedido p50 / p95 (s) | GPU ociosa | Custo | Pods subidos |
|---|---|---|---|---|---|---|
| chegada intermitente (24 pedidos de 3 a 10, ~15 min; n = 720) | 180 s → 60 s | 276 / 480 → 285 / 492 | 585 / 1187 → 641 / 1287 | −28% | **−9,3%** | +13% |
| idem | 180 s → histerese 60/180 | 276 / 480 → 281 / 470 | 585 / 1187 → 644 / 1121 | −4% | −1,3% | +4% |
| rajada (10 × 20 vídeos em 2 min; n = 300) | 180 s → 60 s | igual (671 / 1427) | igual (1066 / 1561) | −33% | −6,7% | igual |
| fila pequena (1 × 5; n = 30) | 180 s → 60 s | igual (299 / 320) | igual (517 / 544) | −33% | −17% | igual |
| envio instável (20% de falha; n = 120) | entrega antiga → nova (180 s) | 449 / 623 → 406 / 544 | 814 / 990 → 661 / 793 | −3% | **−13%**; render desperdiçado 16.753 s → 0 | igual |
| tradução pendente (4 × 4 em 720p; n = 120) | capacidade antiga → nova | 411 / 444 → 414 / 456 | 770 / 812 → 804 / 956 | −18% | −7,8% | −17% |
| cancelamento durante a subida (n = 30) | — | os 2 pods do pedido cancelado sobem do mesmo jeito e ficam ociosos até sair | | | | |

**Leitura:**
- **60 × 180 s:** nas premissas, 60 s custa de 7 a 17% menos em todos os cenários; com pedidos espaçados (~15 min), o 1º vídeo piora pouco no p50 (+3%) e o pedido inteiro piora ~10%, com 13% mais subidas. A histerese fica perto dos 180 s em custo e tem o melhor p95 do 1º vídeo nesse cenário. **O padrão continua 180 s:** a decisão depende do intervalo real entre pedidos e do peso que o dono dá à espera do cliente; medir com o `/api/metricas` (subida, fila e ociosidade reais) antes de mudar.
- **Entrega nova:** com 20% de envios falhando, nenhum render é refeito (antes, 16.753 s de GPU em 30 sementes), o pedido termina ~19% antes no p50 e custa 13% menos. A taxa real de falha de envio em produção não foi medida; com envio estável, a diferença é zero.
- **Capacidade nova:** mais barata (menos pods para traduções), mas no cenário de 4 pedidos de 720p chegando juntos o pedido termina ~4% depois no p50 (18% no p95): a regra antiga pedia um pod a mais por contar cada tradução como um vídeo de 720p. É uma troca, não um ganho puro; o prazo (`RENDER2GO_PRAZO_MIN`) continua sendo o jeito de pedir mais pods.
- **Cancelamento durante a subida:** o orquestrador não cancela subidas quando a demanda some antes de o pod ficar pronto; os pods sobem e saem pela ociosidade. Ideia (não implementada): encerrar a subida que ainda não pegou trabalho quando a fila zera.

### 3.6 Testes

`servico/tests/test_capacidade.py` (SQLite e Postgres onde há banco): a tradução conta o tempo dela, o vídeo em andamento conta o que falta (com piso), o histórico só dentro dos limites, a GPU mais rápida vale mais, o formato de antes ainda funciona, o `Banco.trabalho` separado, a política fixa por padrão e a histerese só pelo ambiente, as métricas sem dados do cliente, e a simulação (determinística, regras de saldo e foto bloqueada, entrega nova sem render desperdiçado, pedido cancelado que não gera, 60 s nunca mais caro numa rajada). `worker/tests/test_ciclo_de_vida.py`: a memória dos datacenters (recuperação e 24 threads).

## 4. Subida dos pods: experimento da imagem com o ambiente pronto (Etapa 4; preparado, não executado)

O fluxo atual já tem downloads em paralelo, o espelho (R2), o cache do volume, a tradução durante o download e o vídeo seguinte preparado durante o atual: nada disso foi duplicado. O que ficou pronto para medir, desligado por padrão:

- **Imagem dos pods por digest** (alvo `pod` do `worker/Dockerfile`; `RENDER2GO_POD_IMAGEM`, só aceita `@sha256:`): o ambiente (venv, ComfyUI com o patch, Ollama) já instalado; **os pesos ficam fora** (o espelho entrega os 37 GB em 40–44 s; com eles, a imagem passaria de ~55 GB). O bootstrap só usa o ambiente da imagem com a chave do código igual e as peças no lugar; senão instala como sempre (fallback testado em `test_lote_nuvem.py`).
- **Protocolo, critérios escritos antes e custo previsto** (~US$ 1,65; teto pedido de US$ 3): `worker/evals/bootstrap-imagem-2026-10-14/LEIAME.md`, com o coletor `coletar.py` (testado nos logs de 08/10). Hipótese honesta: os serviços sobem 60–190 s antes, mas o 1º vídeo pode não melhorar (o PRONTO espera os modelos), e o pull num host frio pode deixar a subida **mais lenta**. Tamanho da imagem não medido aqui (sem disco para a build nesta sessão).
- **Manifesto dos hibernados** (`manifesto_hibernacao`, `RENDER2GO_MANIFESTO_HIB=2`, desligado): o v1 encerra os hibernados a cada mudança em qualquer parte do `Dockerfile` ou do `models.lock.json` (inclusive alvos e modelos experimentais que o volume não usa). O v2 cobre só o que o disco usa: o `requirements.lock`, **cada ARG que o bootstrap lê** (achados no próprio bootstrap: um ARG novo entra sozinho; o teste falha se algum ficar de fora), os modelos do conjunto do pod, o agente, o script do compilador, o `LAYOUT_HIB`, a legenda e a imagem. O v1 ganhou a imagem do pod quando ela não é a de sempre (o venv do volume usa o Python dela). Trocar para o v2 encerra os hibernados uma vez (padrão: 0 hibernados). Volume compartilhado entre pods não foi proposto (o serviço passa `volume=None`; seria outro experimento, com custo e fallback próprios).
- **GPUs** (preços consultados na API em 14/10/2026: L40S US$ 1,09/h; RTX PRO 6000 US$ 2,49/h; RTX 6000 Ada e 4090 sem estoque): a RTX PRO 6000 gera cada vídeo em ~metade do tempo, mas custa 16–18% mais por vídeo e ~2,3× mais por minuto de subida e ociosidade; aproveitamento não medido nela. A L40S continua a 1ª; nada troca sozinho.
