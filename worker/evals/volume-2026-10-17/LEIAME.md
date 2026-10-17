# "Volume vazio depois da retomada" (17/10/2026): diagnóstico

Pedido do dono (17/10/2026): no painel, às 14:38 (horário de Brasília), o pod `pod-ltuxxfgujgnpsm`
(NVIDIA L40S, OC-AU-1, US$ 1,09/h) levou 5 min 37 s até o 1º vídeo, ficou 13 min 16 s ligado, fez
5 vídeos sem falha e saiu como "hibernado (fila vazia)". Ele viu o disco de volume vazio depois da
retomada. Pedido: não assumir a causa e não tocar no pod de produção.

Tudo aqui saiu de consultas **só de leitura** à API do RunPod (metadados do pod e faturamento), do
código e do histórico do git. Nenhum pod foi ligado, parado ou encerrado. Os arquivos não têm token,
variável de ambiente, IP nem o id da conta.

## Evidências

1. **O pod de 17:38 é novo** (`GET /v1/pods/ltuxxfgujgnpsm`, campos sem segredo): `createdAt`
   17:38:27.632 UTC e `lastStartedAt` 17:38:27.627 UTC (o pod foi ligado uma vez só: nunca voltou da
   hibernação); `desiredStatus` EXITED, "Exited by user" às 17:51:42 UTC (13 min 15 s, como no
   painel); `volumeInGb` 70 em `/workspace`; `containerDiskInGb` 120; `machineId` l1nylhod9xpo;
   `costPerHr` 1,09; nome `render2go-fila-dca936-1`.
2. **O hibernado anterior foi encerrado às ~17:25 UTC** (`GET /v1/billing/pods?bucketSize=hour`,
   [`faturamento-por-hora.csv`](faturamento-por-hora.csv)): `hf4dbmglblw7sh`
   (`render2go-fila-dca936-2`), hibernado desde 12/10, foi cobrado só pelo disco de volume,
   US$ 0,019444/h (= 70 GB × US$ 0,20/GB/mês ÷ 720 h), de 12/10 15:00 até o balde de 17/10 17:00,
   que tem US$ 0,008102 (25 min). Depois disso, nada. Ele teve 0,163 h de GPU, todas em 12/10:
   **em 5 dias parado, nunca voltou**, e custou US$ 2,56 de disco
   ([`faturamento-por-pod.csv`](faturamento-por-pod.csv)).
3. **A atualização do servidor mudou a versão da hibernação.** O servidor passou de `bdec7f0` para
   `a19529b` às ~17:20 UTC. `lote_nuvem.versao_hibernacao()` dá `83ade25e23f2a735` em `bdec7f0` e
   `1ada2ec01194e930` em `a19529b`: mudaram `worker/Dockerfile`, `worker/models.lock.json` e
   `pod_agente.py` (o `LAYOUT_HIB` continuou 2). Na partida, `Orquestrador._estado_runpod` encerra
   todo hibernado de outra versão ("versão anterior do ambiente ou dos modelos"), e o encerramento
   apaga o disco de volume (contrato do RunPod, abaixo).
4. **O 1º pedido depois disso subiu um pod do zero.** Às 17:38:27 não havia hibernado compatível: o
   orquestrador criou `ltuxxfgujgnpsm` com um disco de volume novo, vazio como todo disco novo. Os
   5 min 37 s até o 1º vídeo estão na faixa da subida do zero (4,2–5,1 min até o 1º vídeo em 08/10,
   `worker/evals/gpu-2026-10-08-subida/`), não na da volta (64–67 s em 11/10,
   `worker/evals/gpu-2026-10-11-hibernacao/`).
5. **O pod novo hibernou com o cache gravado (a confirmar).** O orquestrador só hiberna depois do
   GUARDADO e do marcador desta versão (`_volume_gravado`). Só a próxima volta confirma o disco, e
   ela não foi feita (não tocar no pod de produção).
6. **O descarte era necessário desta vez.** O agente de `bdec7f0` ainda tinha o defeito corrigido
   em 12/10: no desligamento automático de um pod que hiberna, ele mandava `podTerminate` 60 s
   depois de um `podStop` aceito, se o contêiner ainda estivesse vivo (o disco ia junto). Um pod
   parado guarda o agente com que foi criado; voltar com ele traria o defeito de volta.

O faturamento também mediu o disco do pod ligado: o balde de 17:00 de `ltuxxfgujgnpsm` (ainda
incompleto na consulta, 285 s de GPU) deu US$ 1,118 por hora de GPU, contra 1,09 de tabela.
1,09 + 190 GB × US$ 0,10/GB/mês ÷ 720 h = 1,116. Nas três L40S de 13–14/10, foi de US$ 1,104 a
1,112 por hora. O relatório de GPUs contava só os 1,09.

## Conclusão

**Pod substituto.** O "volume vazio" é o disco novo de outro pod. A causa é a invalidação por versão
na atualização: o hibernado `hf4dbmglblw7sh` foi encerrado, e o disco dele foi junto. Não foi um
volume que esvaziou na parada, nem uma montagem ausente, um cache incompatível no mesmo pod ou um
arquivo corrompido.

Grau: forte (os metadados do pod, o faturamento e o código concordam). Falta a confirmação nos logs
do orquestrador e na tabela `geradores` (comandos abaixo, só leitura).

## Hipóteses

| # | Hipótese | Evidência | Situação |
|---|---|---|---|
| H1 | o mesmo pod voltou com o disco vazio | `createdAt` = `lastStartedAt`; o id é outro | descartada |
| H2 | parada e volta × encerramento e recriação | `hf4…` cobrado até 17:25 e `ltux…` criado às 17:38 | recriação, confirmada |
| H3 | a montagem não existia (o cache ia para o disco do contêiner) | `volumeMountPath` /workspace e `volumeInGb` 70 | sem evidência; agora conferida a cada subida |
| H4 | a sentinela e o marcador não persistem na parada | não testado (pede um ciclo de gravar, parar e ligar num pod descartável) | aberta |
| H5 | a cópia não terminou antes da parada (disco cheio, agente caído, SHA-256) | o orquestrador espera o GUARDADO e o marcador; para `ltux…`, só a volta dirá | aberta, sem efeito em 17/10 |
| H6 | o painel ou o console mostraram o uso do disco errado ou velho | o painel do serviço não mostra uso do disco; o console do RunPod não mede o disco de um pod parado | aberta: em que tela o dono viu o volume vazio? |

## O que mudou no código (17/10/2026)

- `render2go_worker/volume.py` e o bootstrap: `$R/volume.json` com a montagem, o sistema de
  arquivos, se ela é persistente, o espaço, a sentinela (`$L/sentinela.json`: o pod que gravou), o
  marcador, as entradas do volume e o motivo de o cache não servir ("primeira", "vazio",
  "sem_montagem", "outra_versao", "incompleto", "corrompido", "outro_pod", "sem_espaco",
  "copia_falhou"...). O cache só é gravado numa montagem persistente e que comporta tudo; senão,
  nada de marcador, e o orquestrador encerra o pod em vez de hibernar um disco que voltaria vazio. O
  SHA-256 continua sendo conferido em toda volta.
- O orquestrador guarda o diagnóstico de cada sessão (`geradores.volume`), os tempos da subida
  (`geradores.etapas`), os discos e a máquina, e o fim do disco de cada hibernado encerrado
  (`volume_fim_em`, `volume_fim_motivo`, com as duas versões no motivo).
- `render2go-servico hibernados [--runpod]`: o que a versão do código em mãos faria com cada
  hibernado, antes de subir uma atualização (`docs/09`).
- A versão da hibernação desta branch continua `1ada2ec01194e930`: subir esta branch não encerra o
  hibernado de agora.

## Comandos para confirmar (no servidor, só leitura)

```bash
cd ~/GPU2GO/servico/implantacao
docker compose logs --since 2026-10-17T17:00:00Z orquestrador \
  | grep -E "hibernado|versão anterior|do zero|hf4dbmglblw7sh|ltuxxfgujgnpsm"
docker compose exec -T postgres psql -U gpu2go -d gpu2go -c "SELECT id, worker, estado, subida_tipo, versao,
  to_timestamp(pod_criado_em) AS inicio, to_timestamp(primeiro_video_em) AS video,
  to_timestamp(desligado_em) AS fim, motivo FROM geradores ORDER BY criado DESC LIMIT 6"
```

Esperado, se a conclusão está certa: uma linha `pod hibernado hf4dbmglblw7sh encerrado (versão
anterior do ambiente ou dos modelos)` pouco depois das 17:20 UTC, e a linha de
`pod-ltuxxfgujgnpsm` com `subida_tipo` = `fria`.

## Contrato do RunPod (consultado em 17/10/2026)

- <https://docs.runpod.io/pods/storage/types>: o disco do contêiner "is cleared when the Pod stops";
  no disco de volume, "Data stored in the /workspace directory survives Pod stops and restarts, but
  is deleted when the Pod is terminated"; o volume de rede "exists independently from any Pod" e
  "must be attached during Pod creation". Preços: disco de volume US$ 0,10/GB/mês ligado e
  US$ 0,20/GB/mês parado; volume de rede US$ 0,07/GB/mês.
- <https://docs.runpod.io/pods/manage-pods>: parar "releases the GPU and preserves data in
  /workspace"; encerrar "permanently deletes all data not stored in a network volume"; ao ligar de
  novo, "You may be allocated zero GPUs if capacity has changed".
