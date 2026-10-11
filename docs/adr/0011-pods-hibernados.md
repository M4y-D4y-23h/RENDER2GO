# ADR 0011: pods hibernados (subida em ~1 min) e prazo pelo preço real da GPU

- **Status:** proposto (11/10/2026), implementado atrás de `RENDER2GO_PODS_HIBERNADOS` (padrão 0 = desligado). Aceitar é decisão do dono do projeto, depois da medição em GPU (seção "Medição").
- **Contexto:** o dono pediu a subida dos pods de 4–5 min para ~1 min. Medido em 08/10 (`worker/evals/gpu-2026-10-08-subida/`): agente, CUDA e rede ~60 s; ambiente (venv, ComfyUI, Ollama) 60–190 s; modelos (37 GB do espelho R2) 44–175 s; serviços e tradução ~1 min. Baixar 37 GB não cabe em 1 min em host nenhum, e o cache de modelos do RunPod é só para Serverless, com um modelo por endpoint (nós usamos quatro arquivos e o Ollama). Também em 11/10: um pod do dono saiu aos ~40 min, depois do último vídeo, porque o saldo do RunPod estava baixo (US$ 2,32 na conferência) e o desligamento automático era calculado pelo preço da GPU mais cara da lista (US$ 2,49/h da RTX PRO 6000), mesmo com o pod numa L40S.

## Decisão

1. **Disco de volume do próprio pod.** Com a hibernação ligada, todo pod do serviço nasce com `volumeInGb` (`RENDER2GO_POD_VOLUME_GB`, padrão 70) em `/workspace`, e o `bootstrap_pod.sh` instala venv, ComfyUI, Ollama, modelos e o cache do apt em `/workspace/r2g` (`RENDER2GO_LOCAL`); `/opt/venv`, `/opt/comfyui`, `/opt/ollama` e `/usr/local/{bin,lib}/ollama` são links. O disco de volume é **local** (no host, não de rede), POSIX, e fica no host quando o pod é parado.
2. **Hibernar em vez de encerrar.** Quando o worker sai sem pedidos (pod quente vencido) ou pelo limite de tempo, até `RENDER2GO_PODS_HIBERNADOS` pods são **parados** (`POST /pods/{id}/stop`): a GPU para de cobrar e o disco de volume fica, a US$ 0,20/GB/mês (~US$ 14/mês com 70 GB). Os demais são encerrados, como antes.
3. **Ligar os hibernados primeiro.** Quando a fila pede pods, o orquestrador tenta os hibernados na ordem de `RENDER2GO_GPUS` (`POST /pods/{id}/start`). Se o host não tem GPU livre (a API recusa, ou o pod volta com 0 GPU e é parado de novo), tenta o próximo e, no fim, cria um pod novo, como antes. Três vezes seguidas sem GPU no mesmo host: o pod sai do conjunto (encerrado com o disco), e um pod novo ocupa a vaga quando hibernar.
4. **Volta sem download.** Na volta, o orquestrador pula o teste de rede; o bootstrap confere o marcador `$L/instalado` (versão), refaz os links, confere os bytes de cada modelo, instala `curl ffmpeg procps` do cache do apt no próprio volume (sem rede) e escreve PRONTO. Os serviços sobem já no estado AMBIENTE (o `entrypoint.sh` não depende mais do curl). O teste de GPU do entrypoint é pulado (mesma GPU de antes; o cuInit do orquestrador continua).
5. **Integridade.** Os modelos foram conferidos (bytes e SHA-256) quando gravados no disco local do volume, como manda a regra do projeto. Na volta, os bytes são conferidos antes do PRONTO e o **SHA-256 de todos os modelos e dos blobs do Ollama é refeito em segundo plano**, logo depois do PRONTO (isso também traz os arquivos para a memória antes do 1º vídeo). Arquivo que não confere: o estado vira FALHOU, o marcador sai, o worker para de pegar jobs (ele relê o estado a cada job) e o pod é encerrado, não hibernado.
6. **Versão.** `versao_hibernacao()` = SHA-256 de `requirements.lock`, `worker/Dockerfile`, `models.lock.json`, `pod_agente.py` e da linha `LAYOUT_HIB` do bootstrap. Mudou (ex.: dependência nova, modelo novo): os hibernados velhos são encerrados no ciclo seguinte e o conjunto se refaz com os pods novos.
7. **Desligamento automático.**
   - O agente (`pod_agente.py`) ganhou `POST /deadman {"s"}`: o orquestrador manda o prazo **com o preço real da GPU** assim que o agente responde (pods novos e hibernados). O prazo conta o que os outros pods ainda podem gastar até o prazo deles (antes: 2 h de cada pod ligado, e nada dos que estavam subindo).
   - O pod que hiberna nasce com 20 min no ambiente (`PISO_HIB_S`): se o orquestrador cair antes de mandar o prazo real, o pod para sozinho em 20 min.
   - Na hora do prazo, o pod que hiberna é **parado** (podStop) e, se o stop falhar, encerrado (podTerminate).
   - **As 2 h valem por subida:** cada volta recalcula o prazo pelo saldo daquele momento. A regra do dono (nenhum pod ligado mais de 2 h) continua valendo para cada período ligado; um pod que bate o limite com fila pendente hiberna e pode voltar no ciclo seguinte, em ~1 min.
8. **Motivo da saída.** O worker escreve por que saiu (`--motivo`: sem pedidos, limite de tempo do pod, modelos, GPU) e o orquestrador grava no relatório de GPUs do painel ("hibernado (fila vazia)", "limite de tempo do pod"...).
9. **Painel.** A seção Pods mostra o saldo do RunPod (consultado a cada 5 min), o prazo que um pod novo teria por ele (selo "Saldo baixo" abaixo de 1 h e "Sem saldo" abaixo de 15 min) e os hibernados.
10. **Segurança e dados.** O disco de volume só guarda software e modelos públicos. As pastas de dados do ComfyUI (`input`, `output`, `temp`, `user`: fotos e vídeos dos clientes) são links para o disco do contêiner, que a parada apaga; o token do worker fica na raiz do contêiner e é revogado no fim de cada subida. O token de cada agente (sem ele, o pod parado não serve) fica em `hibernados.json`, no volume do orquestrador, com permissão 0600.
11. **Limpeza.** Parada de emergência: encerra também os hibernados. "Desligar" no painel: encerra de vez. Orquestrador parando (atualização): os pods ativos que cabem no conjunto hibernam. Na partida: sai do conjunto o pod que o RunPod não tem mais, e é encerrado o pod parado deste serviço que não está no conjunto. O nome dos pods leva o hash da `RENDER2GO_API_URL` (`render2go-fila-<6 hex>-N`), para dois ambientes na mesma conta do RunPod não mexerem nos pods um do outro.

## Consequências

- **Subida esperada:** start do pod no mesmo host (imagem já em cache) + agente + cuInit + links e apt do cache + serviços ≈ 1 min até o pod pronto; o 1º vídeo ainda carrega os modelos na GPU. Medir antes de aceitar.
- **Custo fixo:** ~US$ 14/mês por pod hibernado (70 GB a US$ 0,20/GB/mês). Com 2 hibernados, ~US$ 28/mês. Em troca, cada subida deixa de pagar ~4 min de GPU (~US$ 0,07 na L40S) e o cliente espera ~3 min a menos.
- **Disponibilidade:** o pod parado só volta no mesmo host. Em horário de pico, o host pode estar cheio; aí vale a subida antiga (4–5 min), sem piora.
- **Atualizações:** mudar dependências, modelos, agente ou o layout do bootstrap encerra os hibernados; os primeiros pedidos depois disso sobem do zero.

## Alternativas consideradas

- **Imagem Docker com tudo dentro (~50 GB):** o host que não tem a imagem baixa 50 GB; a subida pioraria fora dos hosts com cache.
- **Volume de rede com os modelos:** a regra do projeto proíbe carregar modelo direto de volume de rede; copiar 37 GB para o disco local levou 77 s em 07/10 (`worker/evals/gpu-2026-10-07-volume/`), e o volume prende o pod a um datacenter.
- **Serverless com FlashBoot e cache de modelos:** cobra ~2× o pod por segundo e o cache aceita um modelo por endpoint; muda a arquitetura da fila (ADR 0003). Fica como opção se a hibernação não der o resultado medido.
- **Pods quentes o dia todo:** ~US$ 620/mês por pod L40S.

## Medição

Pendente: um pod L40S secure com desligamento automático e orçamento abaixo do saldo: subida do zero no modo volume, hibernação, volta (tempos de cada etapa), 1º vídeo depois da volta e o resultado da conferência do SHA-256. Registro em `worker/evals/`.
