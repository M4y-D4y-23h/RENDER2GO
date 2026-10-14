# ADR 0011: pods hibernados (subida em ~1 min) e prazo pelo preço real da GPU

- **Status:** **aceito pelo dono em 11/10/2026**, depois da medição em GPU (seção "Medição"). Implementado atrás de `RENDER2GO_PODS_HIBERNADOS` (padrão 0 = desligado). Plano do dono: 0 durante os testes (os pods são encerrados como antes) e 2 ou 3 a partir da divulgação; voltar para 0 (ou diminuir) encerra os hibernados a mais no ciclo seguinte do orquestrador.
- **Contexto:** o dono pediu a subida dos pods de 4–5 min para ~1 min. Medido em 08/10 (`worker/evals/gpu-2026-10-08-subida/`): agente, CUDA e rede ~60 s; ambiente (venv, ComfyUI, Ollama) 60–190 s; modelos (37 GB do espelho R2) 44–175 s; serviços e tradução ~1 min. Baixar 37 GB não cabe em 1 min em host nenhum, e o cache de modelos do RunPod é só para Serverless, com um modelo por endpoint (nós usamos quatro arquivos e o Ollama). Também em 11/10: um pod do dono saiu aos ~40 min, depois do último vídeo, porque o saldo do RunPod estava baixo (US$ 2,32 na conferência) e o desligamento automático era calculado pelo preço da GPU mais cara da lista (US$ 2,49/h da RTX PRO 6000), mesmo com o pod numa L40S.

## Atualização (12/10/2026): o "volume zerado" e as correções

**O que o dono viu:** com `RENDER2GO_PODS_HIBERNADOS=1`, o pod hibernou; no pedido seguinte, o pod que atendeu subiu do zero (disco e volume vazios, ~4 min), e não em ~1 min.

**O que aconteceu (conferido na API do RunPod):** o pod que atendeu era **outro pod**, criado do zero. O hibernado não voltou porque a GPU do host dele tinha sido alugada por outra pessoa enquanto ele estava parado ("not enough free GPUs on the host machine", o caso já visto no 2º teste da medição). O código de antes tentava a volta 3 vezes **no mesmo pedido**, em segundos; as 3 falhas seguidas tiravam o pod do conjunto e o **encerravam com o disco de volume**. O pod novo, sem volume, fazia a subida inteira e hibernava no lugar (no momento da conferência, o único hibernado era esse, `-2`, parado às 15:17 e nunca religado). Quem estava no volume nunca foi lido: ele foi apagado junto com o pod.

**Outros caminhos que apagavam ou estragavam um hibernado**, achados na revisão do código e corrigidos junto:

| Onde | O que fazia | Agora |
|---|---|---|
| Volta sem GPU no host (`_retomar_algum`) | 3 tentativas no mesmo pedido e o pod era encerrado | **uma tentativa por pedido**; só sai do conjunto depois de 3 pedidos seguidos sem GPU livre (`FALHAS_RETOMADA`), com o motivo e a hora no painel ("Última volta que não deu certo"). No lugar, sobe um pod do zero, como antes. **14/10/2026 (ADR 0016):** o código contava por chamada, e os ciclos seguintes do mesmo pedido chamavam de novo; agora conta por episódio de demanda (`demanda.json`), com 180 s antes de tentar o mesmo pod (`nao_antes`, que sobrevive ao reinício) |
| Conjunto cheio | o pod que acabou de rodar era encerrado, e o hibernado cujo host estava cheio ficava | o pod que acabou de rodar (com o volume conferido) **toma a vaga** de um hibernado que já falhou na volta (o que mais falhou); os que nunca falharam ficam |
| Erro depois da volta (apt, links) | o pod era encerrado, mesmo com o volume bom | só sai se o erro mostra o volume estragado ("disco de volume", "não existe mais", GPU com defeito, driver antigo); senão, é parado de novo e conta uma falha |
| Prazo do agente (`pod_agente.desligar`, modo parar) | `podStop` e, 60 s depois, `podTerminate` se a API ainda não mostrasse o pod parado | até 10 `podStop` (2 min entre as tentativas); `podTerminate` só no fim, como último recurso |
| `entrypoint.sh` | o desligamento de 2 h do contêiner (`podTerminate` primeiro) corria junto com o prazo do agente | com `DEADMAN_S` (prazo do agente), o contêiner não tem desligamento próprio |
| Pod parado pelo próprio prazo | entrava no conjunto sem conferir o volume | entra só com o marcador `GUARDADO` e o `instalado` da versão atual, lidos do pod |
| Pod que voltou | hibernava de novo sem conferir o marcador | confere `guardado` e `instalado` antes de hibernar; sem eles, é encerrado |
| Volta com volume incompleto (`retomada` no bootstrap) | criava os links de `/opt` para o volume antes de conferir; a instalação do zero, depois, escrevia no volume pelos links | confere marcador, versão, cada modelo (bytes), venv, Ollama e blobs **antes** de criar qualquer link; faltou algo, instala do zero no disco do contêiner, sem links |
| Orquestrador parando com um pod fechando | o mesmo pod podia ser fechado duas vezes (hibernar e encerrar) | um dono só por pod (`PodFila.pegar_vez`, com trava) |

**Testado sem GPU:** o bootstrap em contêiner com a imagem dos pods (`worker/evals/bootstrap-2026-10-12/`): volta certa, volume incompleto (sem links, instala do zero) e arquivo corrompido (FALHOU e o marcador sai); e os testes do orquestrador (`servico/tests/test_orquestrador.py`): host cheio guarda o hibernado e tenta uma vez por pedido; pod novo toma a vaga do que falhou; prazo do agente; volta sem marcador; erros depois da volta; fechamento único.

**O que a hibernação não resolve:** se a GPU do host foi alugada, o pod parado não volta, e não há como reservar a GPU parada no RunPod (a política do RunPod: o pod parado só volta no mesmo host, com GPU livre; senão, migrar = pod novo). Com 2–3 hibernados em hosts diferentes, a chance de algum voltar é maior. A outra saída medível é o volume de rede (seção final e `docs/07`, seção 12).

## Decisão

1. **Disco de volume do próprio pod.** Com a hibernação ligada, todo pod do serviço nasce com `volumeInGb` (`RENDER2GO_POD_VOLUME_GB`, padrão 70) em `/workspace`, além do disco do contêiner de 120 GB de sempre.
   - **1ª subida igual à de sempre**, no disco do contêiner. Depois do PRONTO, com o pod já trabalhando, o `bootstrap_pod.sh` copia venv, ComfyUI (com os modelos), Ollama, o modelo do compilador e o cache do apt para `/workspace/r2g` (`RENDER2GO_LOCAL`, função `guardar_local`) e só no fim grava o marcador `$L/instalado` com a versão. O orquestrador só hiberna o pod depois do GUARDADO e com o marcador desta versão; senão, encerra.
   - **Na volta**, `/opt/venv`, `/opt/comfyui`, `/opt/ollama` e `/usr/local/{bin,lib}/ollama` viram links para o volume.
   - **O volume não é local em todo host:** em 11/10/2026, no US-NC-1, o `/workspace` era **MooseFS** (`mfs#us-nc-1.runpod.net`), um sistema de arquivos de rede (POSIX) do datacenter. Instalar direto nele levou o PRONTO da 1ª subida a 581 s; por isso a 1ª subida voltou para o disco do contêiner. Na volta, ler dele foi rápido (ver "Medição").
2. **Hibernar em vez de encerrar.** Quando o worker sai sem pedidos (pod quente vencido) ou pelo limite de tempo, até `RENDER2GO_PODS_HIBERNADOS` pods são **parados** (`POST /pods/{id}/stop`): a GPU para de cobrar e o disco de volume fica, a US$ 0,20/GB/mês (~US$ 14/mês com 70 GB). Os demais são encerrados, como antes.
3. **Ligar os hibernados primeiro.** Quando a fila pede pods, o orquestrador tenta os hibernados na ordem de `RENDER2GO_GPUS` (`POST /pods/{id}/start`). Se o host não tem GPU livre (a API recusa, ou o pod volta com 0 GPU e é parado de novo), tenta o próximo e, no fim, cria um pod novo, como antes. Três vezes seguidas sem GPU no mesmo host: o pod sai do conjunto (encerrado com o disco), e um pod novo ocupa a vaga quando hibernar.
4. **Volta sem download.** Na volta, o orquestrador pula o teste de rede; o bootstrap confere o marcador `$L/instalado` (versão), refaz os links, confere os bytes de cada modelo, instala `curl ffmpeg procps` do cache do apt no próprio volume (sem rede) e escreve PRONTO. Os serviços sobem já no estado AMBIENTE (o `entrypoint.sh` não depende mais do curl). O teste de GPU do entrypoint é pulado (mesma GPU de antes; o cuInit do orquestrador continua).
5. **Integridade e a regra "nunca carregar modelo direto de volume de rede".** Os modelos são conferidos (bytes e SHA-256) no disco do contêiner, na 1ª subida, e copiados para o volume. Na volta, os bytes são conferidos antes do PRONTO e o **SHA-256 de todos os modelos e dos blobs do Ollama é refeito em segundo plano**, logo depois do PRONTO (isso também traz os arquivos para a memória antes do 1º vídeo). Arquivo que não confere: o estado vira FALHOU, o marcador sai, o worker para de pegar jobs (ele relê o estado a cada job) e o pod é encerrado, não hibernado.
   - **Isto muda uma regra técnica do projeto** (CLAUDE.md: "nunca carregar modelos direto de volume de rede ou de objetos"): na volta, o ComfyUI lê os modelos do volume do pod, que pode ser de rede (MooseFS). A regra existia por dois motivos: velocidade (o volume de rede de 07/10 lia a ~580 MB/s e o "Global Volume" não era POSIX) e arquivo corrompido. Aqui, a velocidade foi medida (render com a carga dos modelos em 34 s; SHA-256 de tudo em ~50 s), o MooseFS é POSIX e o SHA-256 é refeito a cada volta, fail-closed. Por isso esta ADR depende do aceite do dono; a alternativa que cumpre a regra ao pé da letra é copiar os 37 GB para o disco do contêiner a cada volta (~1 min a mais: a volta passa de ~1 para ~2 min).
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

- **Subida medida:** 64 s até o pod pronto e 115 s até o 1º vídeo, quando o host tem a GPU livre (seção "Medição").
- **Custo fixo:** ~US$ 14/mês por pod hibernado (70 GB a US$ 0,20/GB/mês). Com 2 hibernados, ~US$ 28/mês; com 3, ~US$ 42/mês. Em troca, cada volta deixa de pagar ~2–3 min de GPU (~US$ 0,05 na L40S) e o cliente espera ~2–3 min a menos. A 1ª subida de cada pod do conjunto espera a cópia para o volume antes de hibernar (~45 s no host local; mais no MooseFS).
- **Disponibilidade:** o pod parado só volta no mesmo host, e a GPU pode ser alugada por outra pessoa em segundos (visto no 2º teste). Aí vale a subida do zero, sem piora.
- **Atualizações:** mudar dependências, modelos, agente ou o layout do bootstrap encerra os hibernados; os primeiros pedidos depois disso sobem do zero.

## Alternativas consideradas

- **Imagem Docker com tudo dentro (~50 GB):** o host que não tem a imagem baixa 50 GB; a subida pioraria fora dos hosts com cache.
- **Volume de rede com os modelos:** a regra do projeto proíbe carregar modelo direto de volume de rede; copiar 37 GB para o disco local levou 77 s em 07/10 (`worker/evals/gpu-2026-10-07-volume/`), e o volume prende o pod a um datacenter.
- **Serverless com FlashBoot e cache de modelos:** cobra ~2× o pod por segundo e o cache aceita um modelo por endpoint; muda a arquitetura da fila (ADR 0003). Fica como opção se a hibernação não der o resultado medido.
- **Pods quentes o dia todo:** ~US$ 620/mês por pod L40S.

## Medição (11/10/2026, `worker/evals/gpu-2026-10-11-hibernacao/`)

L40S secure (US$ 1,09/h), US-NC-1, com o vídeo de 3 s em 832×480 da foto `fixtures/sfw/produto-cafe.jpg`:

| | Do zero (instalando no volume, 1º teste) | **Volta da hibernação** |
|---|---|---|
| Pod pronto (serviços e PRONTO) | 581 s | **64 s** |
| 1º vídeo pronto | 668 s | **115 s** |
| SHA-256 de todos os modelos | na instalação | 116 s, em segundo plano |

- Volta: ~24 s até o agente responder (o RunPod liga o contêiner no mesmo host), ~40 s até os serviços (ComfyUI e Ollama lendo do volume); o apt do cache do volume (~16 s) corre junto.
- **2º teste, com a 1ª subida no disco do contêiner:** pronto em **175 s** desde o pedido (era 581 s instalando no MooseFS) e a cópia para o volume em 42 s depois do PRONTO (nesse host o volume era local, `md0`). **A volta, 30 s depois, falhou:** "There are not enough free GPUs on the host machine": a L40S do host foi alugada por outra pessoa logo depois da parada.
- **3º teste, o ciclo inteiro com a cópia depois do PRONTO** (volta 5 s depois da parada, host com `md0`): pronto em 208 s do zero, cópia para o volume em 36 s, **volta em 66,5 s até o pod pronto e 108 s até o 1º vídeo** (render de 31 s), SHA-256 de tudo conferido em segundo plano; ~US$ 0,10.
- **Leitura:** quando o host tem a GPU livre, ~1 min até pronto (64 e 66,5 s); quando não tem (1 das 3 voltas tentadas), a subida do zero de sempre. A falha custa ~1 s e nada de GPU. Com 2–3 hibernados em hosts diferentes, a chance de algum voltar é maior; a taxa real fica no painel (voltas e falhas). Três tentativas não dão uma taxa confiável.
- **Custo dos testes:** ~US$ 0,46 no total (saldo de US$ 2,32 para US$ 1,82); nenhum pod ficou ligado ou parado.

## Alternativa a medir se a taxa de volta for baixa: volume de rede do RunPod com tudo instalado

Um volume de rede (US$ 0,07/GB/mês, ~US$ 4/mês para 60 GB) num datacenter com estoque de L40S, com ambiente e modelos prontos, montado por **qualquer** pod novo daquele datacenter (não só pelo mesmo host): subida esperada de ~1–1,5 min para todos os pods, inclusive os da escala, sem depender de uma GPU específica livre. Contra: prende os pods a um datacenter, e ler os modelos direto dele cai na mesma exceção da regra (seção 5). O `lote_nuvem` já tem o modo `--volume` (de 07/10), que hoje copia tudo para o disco local (77 s medidos). Fica para a conversa sobre a capacidade.
