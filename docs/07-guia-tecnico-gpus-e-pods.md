# 07 — Guia técnico: GPUs, pods, custos e qualidade (o que foi medido)

Consolida as medições de 05 a 07/10/2026. Detalhes e relatórios em `worker/evals/gpu-2026-*`. Comando de produção: `tools/lote_nuvem.py` (ADR 0003, fase 2).

## 1. GPUs (vídeo I2V de 5 s; RunPod secure)

Preços da nuvem secure em **08/10/2026** (consulta à API do RunPod). Os tempos foram medidos em 06 e 07/10.

| GPU | VRAM | US$/h | Rápida (480p) | Alta (720p) | Alta 7 s | Custo/vídeo rápida | Custo/vídeo alta |
|---|---|---|---|---|---|---|---|
| **L40S** (Ada) | 48 GB | **1,09** | 55 s | 180 s | — | **US$ 0,017** | US$ 0,055 |
| RTX 6000 Ada | 48 GB | 0,99 | (sem medição; mesmo chip da L40S) | — | — | ~US$ 0,015–0,018 | — |
| RTX PRO 6000 (Blackwell) | 96 GB | 2,49 (era 2,09 até 07/10) | **28 s** | **93 s** | **160 s** | US$ 0,019 | US$ 0,064 |
| RTX 4090 | 24 GB | 0,89 | 56–80 s | 213 s | — | US$ 0,014–0,020 | US$ 0,053 |

- **Ordem padrão (desde 08/10):** L40S → RTX 6000 Ada → RTX PRO 6000 → RTX 4090, só secure. Para trocar no serviço: `RENDER2GO_GPUS` no `.env` (`docs/09`).
- **Por que a L40S passou a ser a 1ª:** com a alta, a RTX PRO 6000 ficou ~14% mais cara por vídeo. Pior: cada pedido paga por hora a subida (~3 min) e o pod quente (3 min). Na L40S, isso custa ~US$ 0,11 por vez, contra ~US$ 0,26 na RTX PRO 6000. Em pedidos pequenos, como os testes grátis, essa parte pesa mais que os vídeos.
- **O que se perde:** cada vídeo leva ~2× o tempo (55 s contra 28 s em 480p, 5 s). Em lotes grandes, o orquestrador compensa subindo mais pods (cada um com ≥ 10 min de trabalho), quase com o mesmo custo por vídeo. Nos pedidos de 3 a 10 vídeos, o total muda pouco, porque a subida domina, e ela ficou mais curta (seção 9).
- **RTX 6000 Ada:** é o mesmo chip AD102 da L40S, com o mesmo cálculo de IA no papel, e entra como 2ª opção quando a L40S não tem vaga. Ainda não foi medida, então as estimativas usam o pior caso (80 s). A L40 (sem "S") ficou de fora: tem metade do cálculo de IA.
- **Orçamento e desligamento automático** contam o preço da opção mais cara com vaga, porque o pod pode cair em qualquer uma da lista.
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
| Rede de 2–3 MB/s (37 GB = 3,5 h) | Velocidade medida no próprio download dos modelos (desde 12/10/2026; antes, um teste de 15 s antes dele), mínimo 40 MB/s; descarte em ~30–45 s (~US$ 0,02) |
| Teste curto aprovou host que entregava 12 MB/s | Teste sustentado; prazo de instalação de 18 min |
| Datacenter ruim repetido | Memória da velocidade medida por datacenter (`~/.cache/render2go/datacentros.json`): os lentos ficam de fora por 7 dias, e os rápidos vêm primeiro (seção 11); só pede IDs que a API REST aceita |
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
- o 1º pod baixa tudo e, depois do PRONTO, grava em `/workspace/render2go-cache`: modelos, compilador e o **ambiente inteiro** (venv + ComfyUI + Ollama) num `tar.zst` chaveado pelo `requirements.lock` e pelo Dockerfile;
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

**Recomendação:** **alta como padrão dos pacotes pagos** e rápida para prévias e amostras de venda. O custo de GPU continua abaixo de 3% de um preço de R$ 2–5 por vídeo. O tempo extra se resolve com mais pods (`--pods auto` já usa até 7 em 50 fotos; `--prazo` para entregar mais rápido), quase sem aumentar o custo por vídeo.

## 8. Comandos de referência

```bash
# estimar sem gastar
python tools/lote_nuvem.py FOTOS --saida ENTREGA --prompt "..." --qualidade alta --simular
# lote real: pods automáticos (cada um com ≥ 10 min de vídeos), orçamento máximo, acabamento local
python tools/lote_nuvem.py FOTOS --saida ENTREGA --prompt "..." --qualidade alta \
  --orcamento-usd 10 --interpolar --altura 1280
# com prazo: o mínimo de pods para entregar em 20 min
python tools/lote_nuvem.py FOTOS --saida ENTREGA --prompt "..." --qualidade alta --prazo 20
# com cache no volume de rede (datacenter bom)
python tools/lote_nuvem.py FOTOS --saida ENTREGA --prompt "..." --volume <id-do-volume>
# encerrar pods de uma execução interrompida
python tools/lote_nuvem.py --saida ENTREGA --limpar
```

## 9. Subida mais rápida no serviço (08/10/2026, medida em 3 pods reais)

No primeiro uso real, um pedido com o pod desligado chegou a levar ~6 min até 3 vídeos prontos. As mudanças e os testes estão em `worker/evals/gpu-2026-10-08-subida/`.

| Etapa | Antes | Agora |
|---|---|---|
| Pedido aprovado até o orquestrador ver | até 30 s (ciclo) | até 10 s (`RENDER2GO_CICLO_S`) |
| Teste de rede | Hugging Face primeiro; o espelho (R2) só se ele fosse reprovado | o espelho primeiro; aprovado, a origem nem é testada |
| Modelos (37 GB) | das duas origens ao mesmo tempo: 175 s num host com o Hugging Face a 25 MB/s | só do espelho (`so_espelho`): **44 s** (~925 MB/s) num host com o Hugging Face a 39 MB/s; 123 s num host pior |
| apt | 14–23 s antes de qualquer download | o que o download usa fica pronto em 3 s; git e ffmpeg durante o download |
| Cada espera do orquestrador pelo pod | consulta a cada 10 s | a cada 3 s (`CONSULTA_S`) |
| ComfyUI e Ollama no ar e tradução do pedido | só depois de os modelos baixarem | assim que o ambiente fica pronto (estado `AMBIENTE`), durante o download |
| 1º vídeo | depois de tudo | assim que os modelos são conferidos (estado `PRONTO`) |

- **Medido (do pedido do pod ao 1º vídeo de 3 s pronto):**
  - **6,8 min** antes, num host lento (L40S);
  - **4,2 min** (RTX PRO 6000) e **5,1 min** (L40S) depois.
- **O que mais pesa agora:**
  - a instalação do ambiente pelo PyPI e pelo GitHub: 60 a 189 s, conforme o host;
  - o 1º vídeo, que carrega os 37 GB na GPU: ~27 s na RTX PRO 6000 e ~53 s na L40S.
- **Tentado e retirado:** guardar o ambiente pronto no R2. O envio feito pelo pod não terminou em 15 min. Se voltar, deve ser montado e enviado de uma máquina com boa saída de rede, como o CI.
- **Fail-closed continua igual:**
  - o worker da fila só pede jobs de tradução até o estado `PRONTO` (`--modelos-prontos`);
  - nenhum vídeo é renderizado com modelo baixado pela metade ou sem conferir o SHA-256;
  - se a instalação falha ou passa do prazo de 18 min, o worker sai e o pod é encerrado.
- **O que mais encurtaria, com custo:**
  - um pod ligado no horário comercial (`docs/11`, seção 3.4);
  - um pod quente mais longo (`RENDER2GO_POD_OCIOSO_MIN`);
  - endpoints serverless com FlashBoot (ADR 0002, Etapa 3), cuja subida pode cair para segundos quando a máquina já tem a imagem. Exigem a imagem com os modelos; ficam para quando houver fila constante.

## 10. Pods hibernados e prazo pelo preço real (11/10/2026, ADR 0011)

- **Por que o pod saiu aos ~40 min (11/10):** o desligamento automático era calculado na criação, pelo preço da GPU mais cara da lista (US$ 2,49/h da RTX PRO 6000), e o saldo do RunPod estava baixo (US$ 2,32 na conferência). O worker para de pegar pedidos 10 min antes do prazo; com ~US$ 3 de saldo, o prazo ficou em ~50 min e o pod saiu depois do último vídeo, sem os 3 min de pod quente.
- **O que mudou:**
  - o agente aceita `POST /deadman`, e o orquestrador manda o prazo com o **preço real** da GPU assim que o agente responde (L40S a ~US$ 0,86–1,09/h: com o mesmo saldo, o prazo mais que dobra);
  - o saldo é dividido pelo que cada pod ligado ainda pode gastar até o prazo dele (antes: 2 h de cada pod ligado, e nada dos que estavam subindo);
  - o painel (seção Pods) mostra o saldo do RunPod e o prazo que um pod novo teria; o relatório de GPUs, por que cada pod saiu.
- **Hibernação (`RENDER2GO_PODS_HIBERNADOS`):** o pod sobe do zero como sempre (disco do contêiner), copia tudo para o disco de volume próprio (`/workspace/r2g`) depois do PRONTO e, sem pedidos, é **parado** em vez de encerrado. Na volta (mesmo host), nada é baixado: links, apt do cache do volume, bytes dos modelos e PRONTO; o SHA-256 é refeito em segundo plano. O host sem GPU livre faz o orquestrador tentar o próximo hibernado e, no fim, subir um pod do zero. Detalhes, custos e riscos no ADR 0011.
- **Medido em 11/10 (`worker/evals/gpu-2026-10-11-hibernacao/`, L40S secure):** volta em **64–66,5 s até pronto e 108–115 s até o 1º vídeo**; subida do zero em 175–208 s até pronto. Uma das 3 voltas tentadas falhou porque a GPU do host foi alugada logo depois da parada. O `/workspace` pode ser local (`md0`) ou de rede (MooseFS), conforme o host: instalar direto no MooseFS levou 581 s, por isso a 1ª subida é no disco do contêiner.

## 11. Por que a subida ainda leva ~4 min, e o que mudou em 12/10/2026

Pergunta do dono: há algo no código que prende a subida nesse tempo, ou que atrasa a carga dos modelos? **Não há espera artificial nem limite de tempo segurando a subida**: os ~4 min são a soma de etapas reais (seção 9: agente, CUDA e rede ~55–60 s; ambiente 60–190 s; 37 GB de modelos 44–175 s; serviços e tradução ~1 min; 1º vídeo carregando os modelos na GPU ~30–55 s). O que somava tempo sem necessidade, e saiu:

| O que atrasava | Antes | Agora |
|---|---|---|
| Teste de rede antes do download | ~15 s com 4 conexões, só para decidir a origem | **saiu**: o próprio download é o teste. O bootstrap mede a velocidade real dos primeiros ~10 s (`$R/rede`); abaixo de 40 MB/s, o pod é descartado como antes; a medida vai para a memória do datacenter |
| SHA-256 dos modelos | `sha256sum` do Debian bookworm (coreutils 9.1, sem libcrypto): **9,2 s por 2 GB**, todos no fim | `hashlib` do Python (OpenSSL com SHA-NI): **2,45 s por 2 GB (3,7×)**; cada arquivo conferido logo depois do próprio download, enquanto os outros baixam |
| Download | um `aria2c` para todos (`-j 4 -x 8`), depois o `curl` do que faltou e a conferência de tudo | um `aria2c` por arquivo (`-x 16 -s 16`), 4 arquivos ao mesmo tempo, cada um conferido e marcado; arquivo errado do espelho é baixado de novo **da origem** (antes, do mesmo espelho) |
| Escolha do host | qualquer datacenter com a GPU, evitando só os lentos medidos | camadas (abaixo), com os filtros de velocidade do próprio RunPod e a velocidade medida de cada datacenter |

**Escolha do host (`lote_nuvem.RunPod.criar`):** a API REST do RunPod aceita, na criação do pod, `minDownloadMbps`, `minUploadMbps`, `minDiskBandwidthMBps`, `countryCodes` e `dataCenterPriority` (conferido em 12/10/2026 com um pedido inválido de propósito, sem criar pod). Cada subida tenta, em ordem:

1. **máquina rápida:** download ≥ 2000 Mbit/s (`RENDER2GO_REDE_MIN_MBPS`, ~250 MB/s: 37 GB em ~2,5 min) e disco ≥ 1000 MB/s (`RENDER2GO_DISCO_MIN_MBS`), nos datacenters com a GPU disponível, na ordem da velocidade medida (`dataCenterPriority: custom`): primeiro os medidos ≥ 200 MB/s, depois os não medidos (América do Norte, depois Europa), por último os medidos mais lentos;
2. os mesmos datacenters, sem os filtros;
3. qualquer datacenter aceito, menos os lentos.

Sem vaga na 1ª camada, ela é pulada por 10 min. **Memória por datacenter** (`datacentros.json`, no volume do orquestrador): a velocidade medida em cada subida entra numa média móvel (metade o valor novo, metade o histórico); os lentos (< 40 MB/s) ficam de fora por 7 dias. Os filtros do RunPod usam a velocidade anunciada da máquina, não garantida: por isso a medida real continua valendo para descartar.

**Esperas que não são da subida (por desenho, não por erro):**

- **teste gratuito agrupado:** com nenhum pod ligado, os pedidos de teste esperam juntar 20 vídeos ou 10 min antes de ligar um pod (`RENDER2GO_AGRUPAR_TESTE_VIDEOS` e `RENDER2GO_AGRUPAR_TESTE_MIN`). Para o teste começar mais rápido, diminua `RENDER2GO_AGRUPAR_TESTE_MIN` (ex.: 2), pagando mais subidas;
- **revisão humana:** nos planos teste e pago, o pedido espera a aprovação no painel; só o interno é aprovado sozinho;
- **filtro das fotos (desde 12/10):** ~2–4 s por foto no servidor; a 1ª foto é liberada em segundos e o pod sobe enquanto as outras são avaliadas.

**O que encurtaria de verdade:** pods hibernados que voltam (~1 min, seção 10, quando o host tem a GPU livre), um pod ligado no horário comercial (`docs/11`, seção 3.4), um pod quente mais longo, ou o volume de rede com tudo instalado (seção 12). A avaliação completa, com o que daria vídeo "quase instantâneo", está em `docs/14`.

## 12. Volume de rede de alta performance (o ícone roxo do RunPod): avaliação

A recomendação recebida pelo dono fala em "diamante roxo"; no painel do RunPod, é um **raio roxo (⚡)** ao lado dos datacenters com **high-performance network storage**, e uma aba "High-performance" filtra por eles ([docs do RunPod](https://docs.runpod.io/storage/high-performance-storage)).

- **O que é:** um tipo de **volume de rede** (o disco que fica no datacenter e é montado em `/workspace` por pods desse datacenter), com "até 3× a vazão e 4× as IOPS" do volume padrão em operações paralelas, "a um preço acima do padrão", que "varia por datacenter". O padrão custa US$ 0,07/GB/mês (até 1 TB) e transfere 200–400 MB/s ([volumes de rede](https://docs.runpod.io/storage/network-volumes)). Disponível em alguns datacenters dos EUA e da Europa; vale para pods e Serverless; volume padrão não vira de alta performance (é preciso criar outro e copiar).
- **Ajuda a internet do servidor? Não.** Ele acelera a leitura e a escrita **do próprio volume**. Hoje os pods não usam volume de rede: baixam os 37 GB do espelho R2 para o disco do contêiner, e o que limita é a internet e o disco do host, que os filtros da seção 11 tratam. Escolher datacenter pelo ⚡ não deixa o download mais rápido.
- **Quando ajudaria:** numa arquitetura diferente, com o ambiente e os modelos **já instalados num volume de rede** e montados por qualquer pod novo daquele datacenter (a "alternativa a medir" do ADR 0011). Aí nada é baixado, e o que pesa é ler 37 GB do volume: a 200–400 MB/s, ~1,5–3 min; a ~3× isso, menos de 1 min. Contra: prende os pods a um datacenter (no dia 12/10, a L40S estava com estoque "Low" nos 4 datacenters que a tinham); custa todo mês; e carregar modelo direto de volume de rede é a mesma exceção à regra do projeto que o ADR 0011 abriu (com SHA-256 refeito a cada subida).
- **Decisão:** o ⚡ **não** entrou como critério de escolha de host (não muda o download). Entraram os filtros de velocidade do próprio RunPod e a medida real por datacenter (seção 11). Se o dono quiser a subida de ~1 min para todos os pods, e não só para os hibernados que acham a GPU livre, o próximo passo é **medir** um volume de alta performance de ~60 GB num datacenter com estoque de L40S (uma subida, alguns dólares), antes de qualquer ADR.
