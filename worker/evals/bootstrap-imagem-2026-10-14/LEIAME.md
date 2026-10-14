# Experimento: imagem dos pods com o ambiente pronto × bootstrap de sempre (preparado em 14/10/2026; **não executado**)

Nada aqui foi medido em GPU. Esta pasta tem o protocolo, os critérios (escritos antes de qualquer medida), o custo previsto e o coletor dos tempos (`coletar.py`). A execução precisa de: (1) a imagem construída e publicada num registro que o RunPod alcance, (2) orçamento do dono para os pods.

## O que muda (só com `RENDER2GO_POD_IMAGEM`; sem ela, nada muda)

- **Alvo `pod` do `worker/Dockerfile`:** o `runtime` (Ubuntu 24.04 fixado por digest, Python 3.12, venv do `requirements.lock` com hashes, PyTorch cu128, ComfyUI v0.38.0 no commit conferido, patch do UMT5 pelo SHA-256, Ollama v0.35.1 pelo SHA-256) + `aria2`, `rsync`, `procps` e a chave do ambiente (`requirements.lock` + `Dockerfile`).
- **Pesos fora da imagem (decisão):** os 37 GB de modelos de vídeo e os ~4,7 GB do compilador continuam vindo do espelho (R2) e da origem, conferidos por bytes e SHA-256. Motivos: o espelho entregou os 37 GB em 40–44 s (08/10); com os pesos, a imagem passaria de ~55 GB e o pull num host frio dominaria a subida; e a imagem mudaria a cada modelo novo (que hoje só muda o `models.lock.json`).
- **Bootstrap:** usa o ambiente da imagem só se a chave gravada nela for a do código enviado ao pod e as peças estiverem no lugar (venv, ComfyUI, patch, versão do Ollama); senão, **instala como sempre (fallback)**. O apt só instala o que falta.
- **Imagem só por digest:** `RENDER2GO_POD_IMAGEM=<registro>/<imagem>@sha256:<64 hex>`; sem o digest, o `lote_nuvem` avisa e usa a `python:3.12-slim-bookworm` de sempre. A imagem entra na versão dos hibernados (o venv do disco de volume usa o Python dela).

## Hipóteses (do que já foi medido)

| Etapa (08/10, `gpu-2026-10-08-subida/`) | L40S (teste 1) | RTX PRO 6000 (A) | L40S (B) |
|---|---|---|---|
| ambiente instalado (venv, ComfyUI, Ollama), s desde o pedido | 189 | 60 | 104 |
| modelos baixados e conferidos | 175–216 | 44–71 | 123 |
| PRONTO | 221 | 94 | 160 |
| 1º vídeo | 407 | 249 | 307 |

- **Ganho esperado:** os serviços e a tradução sobem ~60–190 s antes; o PRONTO só melhora quando o ambiente era a etapa mais lenta (teste 1). Com o espelho rápido, o ganho no 1º vídeo pode ser **zero**.
- **Custo esperado:** o pull da imagem (estimada em ~12–14 GB sem pesos; o alvo `slim`, que tem o modelo do compilador, tem ~18 GB) num host que nunca a baixou. Se o pull for mais lento que a instalação (60–190 s), a imagem piora a subida. **Uma imagem grande pode ser mais lenta que o bootstrap.**
- **Tamanho:** não medido nesta sessão (o disco do ambiente tinha ~7 GB livres; a build não cabe). Medir com `docker image inspect --format '{{.Size}}'` na máquina que construir.

## Protocolo

1. **Construir e publicar** (fora daqui; o dono decide o registro, ex.: GHCR público, sem credencial nos pods):
   ```
   docker build -f worker/Dockerfile --target pod -t ghcr.io/<conta>/render2go-pod:<commit> worker
   docker push ghcr.io/<conta>/render2go-pod:<commit>
   docker image inspect --format '{{.Size}} {{index .RepoDigests 0}}' ghcr.io/<conta>/render2go-pod:<commit>
   ```
   Anotar o tamanho e o digest; o mesmo commit do código vai para os pods (a chave confere).
2. **Rodar**, sempre L40S secure e o mesmo datacenter (`--datacentro`, o mais rápido da memória), 1 foto, 1 vídeo de 5 s em 480p, alternando as variantes (bootstrap, imagem, bootstrap, imagem...) para o mesmo período do dia:
   ```
   mkdir -p exp/foto && cp fixtures/sfw/produto-cafe.jpg exp/foto/
   python tools/lote_nuvem.py exp/foto --saida saida/exp-bootstrap-N --prompt "a câmera se aproxima devagar do produto" \
       --pods 1 --gpu "NVIDIA L40S" --datacentro <DC> --sim 2> logs/bootstrap-N.log
   RENDER2GO_POD_IMAGEM=ghcr.io/<conta>/render2go-pod@sha256:<digest> python tools/lote_nuvem.py ... 2> logs/imagem-N.log
   ```
   O desligamento automático vem armado na criação e o pod é encerrado no fim (e confirmado: ADR 0016).
3. **Frio × quente:** o RunPod guarda imagens no host, mas não deixa escolher o host. Registrar o `machineId` de cada pod (GraphQL `pod { machineId }`) e separar "1ª vez neste host" (frio) de "o host já tinha a imagem" (quente). Sem o mesmo host duas vezes, só o frio é medido (e é o que importa para a subida típica).
4. **Coletar:** `python worker/evals/bootstrap-imagem-2026-10-14/coletar.py --variante bootstrap logs/bootstrap-*.log --variante imagem logs/imagem-*.log > tempos.csv`.
5. **Fallback:** uma subida com a imagem de um commit anterior (a chave não confere) precisa instalar como sempre e chegar ao PRONTO.

## Critérios (escritos antes; a imagem só vira padrão com todos)

1. Pelo menos **4 subidas frias por variante** no mesmo tipo de GPU e datacenter.
2. **1º vídeo:** mediana da imagem ≥ 30 s menor que a do bootstrap, e nenhuma subida da imagem mais de 60 s pior que a pior do bootstrap.
3. **Zero** falhas novas (FALHOU, agente que não responde, CUDA), e o fallback do passo 5 chega ao PRONTO.
4. O custo por subida (tempo até o PRONTO × US$/h) não sobe.
5. Sem os critérios, a produção continua no bootstrap de sempre e a variável fica vazia.

## Custo previsto

L40S secure a **US$ 1,09/h** (preço consultado na API do RunPod em 14/10/2026; estoque "Low"). Cada rodada: subida (~4–7 min) + 1 vídeo (~1 min) + encerramento ≈ 8 min ≈ **US$ 0,15**. Com 4 + 4 frias, 2 de reserva (sem vaga, host lento) e o fallback: ~11 pods ≈ **US$ 1,65**; pedir **US$ 3** de teto (margem para repetir um host lento). A build e o registro não custam GPU.

## Comparação de GPUs (sem trocar a L40S automaticamente)

Preços consultados na API do RunPod em 14/10/2026 (nuvem secure): **L40S US$ 1,09/h** (estoque baixo); **RTX PRO 6000 US$ 2,49/h** (estoque médio); RTX 6000 Ada e RTX 4090 **sem estoque**. Tempo do vídeo de 5 s pela tabela medida (`lote_nuvem.MEDIDO_S`; L40S também em 13/10: 52,0 e 172,7 s).

| GPU | 480p: s / US$ por vídeo | 720p: s / US$ por vídeo | Subida + 3 min de pod quente (~7 min) |
|---|---|---|---|
| L40S | 55 / 0,0167 | 180 / 0,0545 | US$ 0,13 |
| RTX PRO 6000 | 28 / 0,0194 (+16%) | 93 / 0,0643 (+18%) | US$ 0,29 |

- Por vídeo aproveitável, a taxa de aproveitamento medida (11 de 22 em 480p e 6 de 11 em 720p, só na L40S, configuração A) dobra os valores; **não há medida de aproveitamento na RTX PRO 6000** com a configuração A (supor igual é uma hipótese: o modelo e os passos são os mesmos).
- A RTX PRO 6000 entrega cada vídeo em ~metade do tempo, mas custa 16–18% mais por vídeo e ~2,3× mais por minuto de subida e de ociosidade. Ela só compensa quando a espera do cliente pesa mais que o custo, e com estoque; a L40S continua a 1ª da lista (`RENDER2GO_GPUS`). A simulação (`docs/15`, seção 3.5, "GPUs heterogêneas") mostra o efeito de ter as duas.
