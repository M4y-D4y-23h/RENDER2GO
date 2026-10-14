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
