# Rodada de qualidade em GPU (13/10/2026, ADR 0014)

A Fase 3 do pedido do dono de 13/10/2026 ("tirar o máximo do Wan 2.2 I2V"): medir, com as mesmas fotos e seeds, se os modelos oficiais do Wan 2.2 com o LoRA de 4 passos (B) e a legenda por foto (D) fazem vídeos melhores que a produção (A), pelos critérios do [ADR 0014](../../../docs/adr/0014-modelos-oficiais-tradutor-e-legenda.md), escritos antes da medição. **Este arquivo, até a seção "Resultado", foi escrito antes de qualquer vídeo da rodada.**

## Combinações

- **A** (produção depois da Fase 1): o template `i2v_r1v2` (Wan 2.2 I2V "R1 v2" e o codificador NSFW-UMT5) e os prompts do tradutor de produção (abliterado, prompt v6) pelo caminho do serviço (`render2go_worker.traducao`), com a seed fixa por pedido do worker (`fila.semente_do_pedido`).
- **B**: o template candidato `experimental/i2v_oficial_lightx2v.v1` (Wan 2.2 I2V A14B oficial em fp8 com escala, o umt5 original e o LoRA lightx2v de 4 passos v1), com os mesmos prompts de A.
- **C** (NAG): fora (ADR 0014, ponto 4).
- **D** (redefinida antes da rodada, porque o tradutor comum e o v8 já ficaram de fora pelo critério 6): B + a legenda de cada foto só no código (`qwen2.5vl:7b` no Ollama de CPU do pod; categoria e assunto no movimento padrão do pedido vago e a regra da pessoa); o v6 não vê a legenda. A rodada gera D nos pedidos cujo prompt muda e em mais 4 cujo prompt não muda, com a legenda das fotos rodando ao mesmo tempo no Ollama de CPU: nesses 4, o vídeo é o de B de novo, e a diferença de tempo mede a interferência da legenda; **para os critérios, D é o vídeo gerado onde o prompt muda e o de B onde não muda** (ADR 0014).

## Fotos e pedidos

As fotos de `fixtures/sfw` e as CC0 ou de domínio público do Wikimedia Commons listadas em [`fixtures/sfw/FONTES.md`](../../../fixtures/sfw/FONTES.md) (reduzidas a 640 px no lado maior). Cada foto tem dois pedidos, como um cliente escreveria (`plano.json`): um **concreto**, no estilo da foto, e um **vago** ("deixa bonito", no Livre). A seed do vídeo é fixa por pedido e a mesma em todas as combinações; a da tradução é a do worker (fixa por pedido). Vídeos de 5 s; 480p com o formato da foto (`auto`); 720p só nos pedidos concretos. `pessoas` no plano é a análise que o servidor daria (`render2go_servico.pessoas`): só a foto da padaria tem pessoas.

## Três pods (um pod vive no máximo 2 h)

| Pod | Fases | O quê |
|---|---|---|
| 1 | P | a tradução de cada pedido no Ollama da GPU e a legenda de cada foto no Ollama de CPU, com a GPU parada |
| 1 | 4 | 480p: A, B e D (cada grupo depois de um aquecimento de 256×256 e 3 s, como o do worker) |
| 2 | 7 | 720p: A e B nos pedidos concretos, com os prompts do pod 1; durante B, a legenda no Ollama de CPU e uma tradução no Ollama da GPU entre dois vídeos (o pior caso de memória com a legenda ligada) |
| 3 | S | SageAttention 2.2 (compilado no pod, `preparar_sage.sh`): os vídeos 480p da vencedora sem e com ele, com a compilação já terminada |

Cada comparação fica toda num pod, na mesma GPU. Cada vídeo registra: o tempo do ComfyUI ("Prompt executed in"), o pico de VRAM (a GPU toda), a conferência e o filtro de imagem de produção (`conferencia`, `filtro_imagem`), o prompt e se ele cita pessoa, e a tira (a foto preparada e os quadros 0, 20, 40, 60 e 80).

## Protocolo do pod (o de `worker/evals/gpu-2026-10-05/LEIAME.md`)

- Um L40S secure por vez, criado pela API como o orquestrador cria, com o nome `render2go-teste-qualidade-<n>`; nunca toca nos pods de produção (`render2go-fila-*`) nem nos hibernados.
- **Desligamento automático armado na criação** (o agente do pod encerra o pod sozinho no prazo, até 1 h 55 min, ajustado pelo preço real da GPU), antes de qualquer instalação.
- O pod é encerrado no fim (try/finally), e a API é consultada depois para conferir que ele não existe mais e que os pods de produção estão como estavam.
- O saldo do RunPod é registrado antes e depois de cada pod (o custo do relatório). Sem teto de orçamento (decisão do dono de 13/10/2026); a trava é o prazo do pod.
- O pod de teste nunca grava no espelho R2 de produção: se o espelho estiver incompleto, baixa tudo da origem.

## Critérios (ADR 0014; B e D contra A)

1. Vídeos aproveitáveis (lista abaixo) ≥ A, nas mesmas fotos e seeds.
2. Reprovações na conferência automática ≤ A.
3. Nenhum vídeo SFW barrado pelo filtro de imagem, e a média das notas (janela de 2 quadros) ≤ a de A.
4. Zero pessoa inventada nas fotos sem pessoa (pelo prompt e pelas tiras).
5. Tempo por vídeo ≤ A + 10% em 480p e em 720p (a mediana do tempo do ComfyUI no grupo, GPU quente), nenhuma falta de memória em 720p com a legenda rodando e o tradutor na GPU entre os vídeos, e a espera do 1º vídeo de um pedido com legenda ≤ ~30 s a mais (o tempo da legenda de uma foto com o modelo carregado, que o 1º vídeo espera).

Cada parte entra só com os critérios dela cumpridos: os modelos oficiais (1–5 em B) e a legenda por foto (1–5 em D). O que falhar fica fora, com os números.

## "Aproveitável" (escrito antes de ver os vídeos)

Um vídeo é aproveitável se, na tira (a foto preparada e os quadros 0, 20, 40, 60 e 80):

1. o produto ou assunto da foto é reconhecível do 1º ao último quadro, com a mesma forma, cor e textura; rótulo e texto que eram legíveis continuam legíveis e iguais;
2. nenhuma pessoa, parte do corpo, animal ou objeto aparece que não estava na foto, nem em parte;
3. o movimento é coerente com o pedido: no concreto, o que foi pedido (ou perto disso, sem contrariar); no vago, um movimento suave que não muda a cena;
4. nenhum defeito grave: deformação ou derretimento, troca de cena ou corte, piscada, cor estourada ou lavada, texto ou logotipo inventado, desfoque forte que a foto não tinha;
5. passou na conferência automática e no filtro de imagem (o que eles barram não chega ao cliente).

**Às cegas:** `julgar.py preparar` copia as tiras com um código aleatório no lugar da combinação, com só o pedido do cliente (em português) ao lado; o julgamento de cada tira (sim ou não e o motivo) é escrito inteiro antes de abrir a chave (`julgar.py tabela` recusa um julgamento incompleto). Dúvida conta como não aproveitável. Pessoa inventada (critério 4): o prompt cita uma pessoa numa foto sem pessoa, ou a tira mostra pessoa ou parte do corpo que a foto não tinha.

## SageAttention (regra escrita antes)

- **Vencedora:** B, se em 480p ela não teve mais reprovações automáticas (conferência e filtro) que A; senão, A.
- **Vira proposta de ADR** se, nos 22 vídeos 480p da vencedora (os mesmos pedidos e seeds, sem e com ele, no mesmo pod): (a) nenhuma reprovação automática a mais que sem ele; (b) SSIM médio ≥ 0,90 contra o mesmo vídeo sem ele; (c) tempo do ComfyUI pelo menos 15% menor; (d) nenhum "Error running sage attention" no log. Ganho só de velocidade, com diferença visível nas tiras: não entra.

## Como rodar

```bash
cd worker
# simulação sem GPU (gerador, tradutor, legenda e filtro falsos), antes de gastar:
RENDER2GO_RAIZ=.. uv run python evals/gpu-2026-10-13-qualidade/no_pod.py /tmp/sim1 --fases P4 --simular
RENDER2GO_RAIZ=.. uv run python evals/gpu-2026-10-13-qualidade/no_pod.py /tmp/sim2 --fases 7 \
    --prompts /tmp/sim1/resultado.json --simular
RENDER2GO_RAIZ=.. uv run python evals/gpu-2026-10-13-qualidade/no_pod.py /tmp/sim3 --fases S \
    --prompts /tmp/sim1/resultado.json --simular
# a rodada (credencial do RunPod no ambiente; R2 opcional, só leitura):
uv run python evals/gpu-2026-10-13-qualidade/medir_qualidade.py --pod 1 --saida <pasta1>
uv run python evals/gpu-2026-10-13-qualidade/medir_qualidade.py --pod 2 --saida <pasta2> \
    --prompts <pasta1>/resultado.json
uv run python evals/gpu-2026-10-13-qualidade/medir_qualidade.py --pod 3 --saida <pasta4> \
    --prompts <pasta1>/resultado.json
# o julgamento às cegas e a tabela:
uv run python evals/gpu-2026-10-13-qualidade/julgar.py preparar <pasta1> <pasta2> <pasta3>
uv run python evals/gpu-2026-10-13-qualidade/julgar.py tabela <pasta1> <pasta2> <pasta3>
```

Os vídeos ficam fora do repositório; vão para cá o resultado de cada pod, os logs, o julgamento, a chave, a tabela e as folhas lado a lado.

## Resultado

Rodada da noite de 13/10/2026 à madrugada de 14/10/2026, em três pods L40S secure (US-TX-3 e US-TX-4), todos com o desligamento automático armado na criação, encerrados no fim e conferidos na API (`pod1/`, `pod2/` e `pod3/execucao.json`). O julgamento às cegas (`julgamento.json`, 76 tiras) foi escrito inteiro antes de abrir a chave (`chave.json`); a tabela e as folhas lado a lado (`folhas/`, reduzidas) saíram do `julgar.py tabela`.

### A × B × D (critérios do ADR 0014)

| Combinação | Resolução | Vídeos | Aproveitáveis | Reprovados na conferência | Barrados pelo filtro | Nota média do filtro | Pessoa inventada (fotos sem pessoa) | Tempo mediano do ComfyUI (s) | Pico de VRAM (MiB) | Falta de memória |
|---|---|---|---|---|---|---|---|---|---|---|
| A | 480p | 22 | **11** | **0** | 0 | **0,0128** | **0** | 52,0 | 41.413 | 0 |
| B | 480p | 22 | 5 | 3 | 0 | 0,0189 | 3 | 52,2 | 41.421 | 0 |
| D | 480p | 22 | 3 | 1 | 0 | 0,0137 | 3 | 52,1 | 41.533 | 0 |
| A | 720p | 11 | **6** | 0 | 0 | 0,0140 | 0 | 172,7 | 45.445 | 0 |
| B | 720p | 11 | 3 | 0 | 0 | 0,0110 | 0 | 177,5 | 44.301 | 0 |

(C, o NAG, fica de fora: ADR 0014, ponto 4. D conta o vídeo gerado onde o prompt muda, os 10 pedidos vagos com a legenda, e o de B nos outros.)

**Critérios:**

| Critério | B | D |
|---|---|---|
| 1. Aproveitáveis ≥ A | **não** (5 × 11 em 480p; 3 × 6 em 720p) | **não** (3 × 11) |
| 2. Reprovações na conferência ≤ A | **não** (3 × 0 em 480p: deriva de cor 57,2, 64,8 e 42,7, todos pedidos vagos) | **não** (1 × 0: xícara, vago, deriva 48,2) |
| 3. Nenhum barrado e nota média ≤ A | **não** em 480p (0,0189 × 0,0128; nenhum barrado, todas as notas abaixo de 0,14 com o limiar de 0,5); sim em 720p | **não** (0,0137 × 0,0128) |
| 4. Zero pessoa inventada | **não** (3: duas atendentes atrás do balcão da loja e um homem com uma criança no salão vazio) | **não** (3) |
| 5. Tempo ≤ A + 10%, sem falta de memória, 1º vídeo com legenda ≤ ~30 s a mais | sim (+0,4% em 480p; +2,8% em 720p, com a legenda e o tradutor rodando; pico de 44,3 GB) | **não**: a legenda de uma foto nova leva 49–54 s com a GPU parada e 53–78 s (mediana 60 s) com a GPU gerando 720p, e o 1º vídeo de um pedido a espera |

**Decisão pela regra escrita antes: nem B nem D entram.** O ADR 0014 continua proposto, com estes números, e a produção continua com A.

**O que os vídeos mostram (as tiras, por pedido):**
- **O modelo oficial com o LoRA inventa muito mais:** em 480p, B despejou líquido em 8 dos 22 vídeos (café, xícara, frasco, garrafa, bolo) e grãos de café em outro, pôs pessoas na loja e no salão vazios, derreteu o tênis e embaralhou o rótulo em 720p ("CRANGE"). A, nos mesmos prompts e seeds, inventou líquido em 4, todos em pedidos vagos ou no café.
- **Os erros de A** (11 de 22 em 480p): líquido despejado nos pedidos vagos de café, frasco e garrafa e no concreto do café ("o vapor sobe…"); a padaria (foto antiga com pessoas) muda os rostos quando eles se mexem; o salão troca os quadros da parede por cartazes; a fachada ganha galhos de árvore quando a câmera sobe. É o que a Fase 1 ainda não resolveu e fica para a próxima etapa (`docs/06`, seção 9.5).
- **A legenda (D) piorou os pedidos vagos:** com a categoria da legenda, o movimento padrão da comida e do produto ("o vapor sobe", "a luz passa pelo vidro") virou líquido despejado ou escorrendo em 5 dos 10 vagos (e uma película sobre o tênis); com a categoria do estilo (A), a luz passando inventou menos.
- **Pessoa inventada pelo prompt:** zero nas três combinações (a regra da Fase 1 funcionou); as três de B e D vieram do modelo de vídeo, com prompts sem pessoa.

**Medidas que sobram:**
- **Legenda:** 11 de 11 fotos com legenda certa em 720p (em 480p, 10 de 11: o salão foi recusado pelo formato do assunto, "the café interior", com acento; a foto seguiu sem legenda, como deve). O "6 s com a GPU ocupada" do pod 1 era o cache de prompts do llama-server (a mesma foto já legendada na fase P); a medida certa, com fotos novas, é a do pod 2.
- **Interferência:** a legenda rodando em CPU não mudou o tempo da GPU (±2% em 480p; B em 720p com a legenda: +2,8% sobre A, no limite do ruído) nem o vídeo (SSIM 1,0 nos 4 pedidos repetidos).
- **Memória:** sem falta em 720p com a legenda e o tradutor carregando na GPU entre dois vídeos (a 1ª tradução levou 57,5 s, carregando o modelo; as seguintes, ~4,5 s).
- **Subida:** 195–221 s até o pod pronto com os dois conjuntos de modelos (74 GB) e o modelo de visão.

### SageAttention 2.2

**não entra.** Com a vencedora pela regra escrita antes (A, porque B teve mais reprovações), os 22 vídeos 480p sem e com ele no mesmo pod: tempo mediano do ComfyUI 53,1 → 45,1 s (−15,1%, a condição (c) passa), nenhum "Error running sage attention" (d), mas 1 reprovação a mais na conferência (o café, pedido vago, deriva de cor 41,0 com o limite de 40; a condição (a) falha) e SSIM médio de 0,871 contra o mesmo vídeo sem ele (mínimo 0,703; a condição (b) pede 0,90). Igual ao de 12/10: mais rápido, mas o vídeo muda o bastante para a conferência ver.

### Custo

| Pod | Ligado | Custo (tempo × preço) |
|---|---|---|
| Tentativas de 13/10 que não subiram (2) | — | US$ 0,08 |
| 1 (P e 4: prompts, legendas e 480p) | 71 min | US$ 1,30 |
| 2 (7: 720p) | 73 min | US$ 1,32 |
| 3 (S: SageAttention, com a compilação de ~12 min) | 56 min | US$ 1,02 |
| **Total** | | **US$ 3,72** |

O saldo do RunPod caiu mais que isso entre os pods porque outro pod da conta, que não é desta rodada, estava ligado (`omnis-piloto-diag-codec-…`, nunca tocado); o custo de cada pod é o tempo dele × o preço da GPU. O processo local do pod 2 caiu com o reinício do contêiner da sessão; o pod seguiu sozinho, foi retomado pela API (o token do agente fica no ambiente do pod) e encerrado do mesmo jeito.
