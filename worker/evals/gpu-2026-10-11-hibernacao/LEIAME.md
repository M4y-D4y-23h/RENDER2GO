# Pods hibernados e filtro de imagem em GPU (11/10/2026)

O dono pediu a subida dos pods de 4–5 min para ~1 min (ADR 0011) e o filtro de nudez nos quadros dos vídeos (ADR 0005). Pods reais no RunPod, nuvem secure, com as travas pedidas pelo dono: desligamento automático no agente em cada subida (≤ 25 min e o que o orçamento do teste ainda pagava), orçamento do teste abaixo do saldo da conta menos a reserva, e o pod encerrado no fim (o disco de volume vai junto).

- **Script:** `teste_hibernacao.py` (roda daqui: `uv run python evals/gpu-2026-10-11-hibernacao/teste_hibernacao.py --orcamento-usd 0.6`) e `video_e_filtro.py` (roda no pod: 1 vídeo de 3 s em 832×480 da foto `fixtures/sfw/produto-cafe.jpg`, a conferência e o filtro de imagem, com os tempos).
- **Modelos:** do espelho R2 (os 2 arquivos do filtro foram enviados ao espelho antes, do sandbox, conferidos pelo tamanho; o SHA-256 é conferido no pod).
- **Saldo do RunPod:** US$ 2,32 antes dos testes.

## Teste 1: subida do zero instalando direto no disco de volume, hibernar e voltar

L40S secure (US$ 1,09/h), US-NC-1. Tempos em segundos desde o pedido do pod (fase A) e desde o pedido de volta (fase B).

| Etapa | Fase A: do zero | Fase B: volta da hibernação |
|---|---|---|
| Agente respondeu (prazo real mandado) | 28 | 24 |
| Serviços no ar | 288 | **64** |
| PRONTO (modelos conferidos) | 581 | **64,5** |
| 1º vídeo pronto (render + conferência + filtro) | 668 | **115** |
| Render do vídeo de 3 s (com a carga dos modelos) | 59,7 | 33,7 |
| Filtro de imagem, 1ª chamada (importa PyTorch e carrega o modelo) / seguintes | 17,0 / 0,31 | 11,1 / 0,30 |
| SHA-256 de todos os modelos (segundo plano) | na instalação | conferido em 116 s |

- **Nota do filtro** nos 13 quadros do vídeo SFW: 0,020 (limiar 0,35); "low" (sugestivo) 0,07. Nada barrado.
- **Disco de volume usado:** venv 13 GB, ComfyUI com os modelos 35 GB, Ollama 6,5 GB, cache do apt 0,17 GB: ~55 GB dos 70.
- **Achado:** o `/workspace` desse host é **MooseFS** (`mfs#us-nc-1.runpod.net`, de rede), não um disco local como a documentação do RunPod sugere. Gravar e conferir os 37 GB direto nele levou o PRONTO da fase A a 581 s (modelos baixados em 220 s; SHA-256 até 526 s), contra 94–221 s no disco do contêiner em 08/10. Na volta, ler do MooseFS foi rápido o bastante (serviços em 64 s, render com a carga dos modelos em 34 s, SHA-256 de tudo em ~50 s, ~700 MB/s).
- **Custo:** ~US$ 0,24 (782 s ligados).

## Correções depois do teste 1

- **1ª subida igual à de sempre** (disco do contêiner) e a cópia para o volume em segundo plano depois do PRONTO (`guardar_local` no bootstrap); o marcador `$L/instalado` só no fim, e o orquestrador só hiberna depois do GUARDADO (senão, encerra).
- **Pré-carga do filtro** no PRONTO (a 1ª chamada levava 11–17 s).

## Teste 2: 1ª tentativa (falhou na subida, ~4 min, ~US$ 0,08)

- O disco do contêiner do pod que hiberna tinha ficado com 40 GB (quando a instalação ia direto para o volume): com a 1ª subida de volta ao disco do contêiner, ele **encheu** (37 GB de modelos + ambiente) e a instalação falhou. Agora são 120 GB, como antes.
- O teste de rede do espelho passou a ler o `config.json` de 989 bytes do filtro (o 1º modelo em ordem alfabética) e dava 0 MB/s: o espelho era reprovado e os modelos vinham também do Hugging Face. Agora o teste usa o maior modelo do espelho.
- A falha da instalação passou a mostrar o disco e o fim do `ambiente.log` (antes, o fim dos logs veio vazio).

## Teste 2: 2ª tentativa (a 1ª subida corrigida; a volta falhou por falta de GPU no host)

L40S secure (US$ 1,09/h), outro host: o `/workspace` era **local** (`/dev/md0`, RAID), não MooseFS.

| Etapa | Tempo |
|---|---|
| Teste de rede no espelho (agora no maior modelo) | 226,6 MB/s: só o espelho |
| Modelos baixados / conferidos (bootstrap) | 85 s / 124 s |
| **Pod pronto (serviços e PRONTO), desde o pedido** | **175 s** (era 581 s no teste 1, instalando direto no MooseFS) |
| Cópia para o disco de volume (~50 GB) depois do PRONTO | 42 s (bootstrap 124 → 166 s) |
| Hibernado | 225 s |
| **Volta, 30 s depois** | **falhou:** `start pod: There are not enough free GPUs on the host machine to start this pod.` |

- **O risco da hibernação apareceu na prática:** ao parar, a GPU volta para o mercado na hora, e a L40S desse host foi alugada por outra pessoa em menos de 30 s. No serviço, o orquestrador tenta o próximo hibernado e, sem nenhum, sobe um pod do zero (~3 min até pronto); a falha custa ~1 s e não cobra GPU. Depois de 3 falhas seguidas no mesmo host, o pod sai do conjunto.
- **Custo:** ~US$ 0,07 (224 s ligados). O pod foi encerrado na falha (o disco de volume foi junto).

## Teste 3: o ciclo inteiro com a cópia depois do PRONTO (volta 5 s depois da parada)

L40S secure (US$ 1,09/h), `/workspace` local (`/dev/md0`). `teste_hibernacao.py --orcamento-usd 0.5 --espera-parado-s 5 --sem-video-a`: o caminho que o teste 2 não chegou a cobrir (a volta de um volume gravado pela cópia depois do PRONTO).

| Etapa | Tempo |
|---|---|
| Pod pronto (serviços e PRONTO), desde o pedido | 208 s (modelos do espelho em 83 s; PRONTO aos 129 s do bootstrap) |
| Cópia para o disco de volume depois do PRONTO | 36 s (bootstrap 129 → 165 s) |
| **Volta: pod pronto, desde o pedido de volta** | **66,5 s** (links em 0 s, apt do cache do volume em 13 s) |
| **1º vídeo pronto depois da volta** | **108 s** (render do vídeo de 3 s com a carga dos modelos: 31 s) |
| SHA-256 de todos os modelos (segundo plano) | conferido aos 23 s do bootstrap da volta (110 s desde o pedido de volta) |

- **Nota do filtro** nos 13 quadros: 0,021 (limiar 0,35). Nada barrado. A conferência da continuidade aprovou (deriva 19,2, salto 1,6).
- **Custo:** ~US$ 0,10 (335 s ligados). O pod foi encerrado no fim (o disco de volume foi junto).

## O que ficou

- **Quando o host tem a GPU livre, a volta leva ~1 min até o pod pronto** (64 s no teste 1 e 66,5 s no teste 3, já com a cópia depois do PRONTO) e menos de 2 min até o 1º vídeo (115 s e 108 s), contra 3–5 min de uma subida do zero.
- **Quando não tem (1 das 3 voltas tentadas: a de 30 s depois da parada), vale a subida do zero**, que continua igual à de antes (175 e 208 s até pronto nos testes 2 e 3). Com 2 ou 3 pods hibernados, em hosts diferentes, a chance de algum voltar é maior; a taxa real aparece no painel (seção Pods: voltas e falhas de cada hibernado).
- **Disco de volume:** pode ser local (`md0`) ou de rede (MooseFS), conforme o host. Por isso a 1ª subida instala no disco do contêiner e só copia para o volume depois; na volta, o SHA-256 de tudo é refeito em segundo plano.
- **Filtro de imagem na GPU:** 0,3 s por vídeo de 3 s (13 quadros) depois de carregado; a 1ª chamada levava 11–17 s e agora é feita no PRONTO, em segundo plano.
- **Custo total dos testes:** ~US$ 0,46 (saldo do RunPod de US$ 2,32 para US$ 1,82). Nenhum pod ficou ligado ou parado.
