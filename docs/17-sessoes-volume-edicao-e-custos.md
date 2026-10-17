# 17. Sessões dos pods, disco de volume, edição de imagem, naturalidade e custos (17/10/2026)

Pedido do dono (17/10/2026), a partir da `main` em `a19529b`, na branch `claude/festive-mccarthy-sc784n`: revalidar
tudo no HEAD; diagnosticar o disco de volume na hibernação e reduzir o tempo até a 1ª entrega; preparar a edição de
imagem que troca o cenário e preserva o produto, dentro das versões e da aprovação (ADR 0018); avaliar movimentos mais
naturais sem esconder divergências do pedido; rodar vídeos nativos de 7 s em 480p e 720p; medir o custo real e
revisar os preços. **Sem merge nem implantação automática; nada aberto ao público.**

Cada seção diz o que foi **implementado** (código e testes), **medido**, **simulado** ou ficou **pendente**.

## 1. Métricas e diagnóstico (Etapa 1)

### 1.1 O relatório de GPUs contava os vídeos de todas as sessões do pod (reproduzido e corrigido)

Cada subida é uma linha de `geradores`: a do zero e cada volta da hibernação (ADR 0011). O pod que volta tem o mesmo
nome (`pod-<id>`), e o relatório (`Banco.relatorio_geradores`) ligava os jobs pelo nome. Assim, cada sessão do pod
contava os vídeos, as traduções e os segundos de todas as outras.

- **Reproduzido no código de `a19529b`:** um pod com 2 vídeos na 1ª sessão e 3 na volta aparecia com 5 vídeos em
  cada sessão (10 no total). O teste é `servico/tests/test_sessoes_pod.py::test_two_sessions_of_the_same_pod_do_not_count_each_others_videos`.
- **A mesma causa em outros dois lugares:**
  - `primeiro_video_em` era gravado em todas as linhas do pod ainda sem ele, então uma sessão sem vídeo ganhava o 1º
    vídeo da seguinte;
  - na volta de um hibernado que não ficava pronta, a mesma subida ligava outro pod e **sobrescrevia** a linha, e o
    tempo de GPU da volta que falhou sumia do relatório.
- **Corrigido:**
  - o job guarda a sessão da tentativa (`jobs.gerador_id`, a linha mais recente do pod na reivindicação: um pod nunca
    tem duas sessões no ar);
  - cada reivindicação vira uma linha de `tentativas` (sessão, começo, ida à GPU, fim e resultado: `ok`, `erro`,
    `repetir`, `transporte`, `prazo`, `cancelado`, `devolvido`; só números e identificadores);
  - `primeiro_video_em` só na sessão atual;
  - a volta que falhou fica numa sessão própria, encerrada (`gerador_criado`).
- **Dados antigos:** os jobs de antes não têm a sessão. Com uma sessão só do pod, a atribuição continua a de sempre.
  Com mais de uma, ela é pelo horário da tentativa, e a sessão sai marcada em `incertezas`
  (`atribuicao_por_horario`, `sem_tentativas`, `disco_desconhecido`, `fim_estimado`). Nada do histórico é apagado nem
  reescrito.

### 1.2 O que o painel mostrava em 17/10, conferido

| Número do painel | O que é de verdade |
|---|---|
| "5 min 37 s até o 1º vídeo" | da criação do pod até **começar** o 1º vídeo (a reivindicação). Inclui a subida, o download, a tradução e a espera dos modelos; não é o bootstrap sozinho e não é a entrega. O relatório agora traz também `agente_s`, `pronto_s` e `primeira_entrega_s`, e os tempos do bootstrap por etapa (`geradores.etapas`). |
| "Uso da GPU 32%" | `soma(jobs.segundos) ÷ tempo ligado`, dos vídeos certos, sem NVML. Virou "Ocupação": a soma dos tempos de todas as tentativas e das traduções sobre o tempo ligado, com a explicação na página. |
| "US$ 0,241" | 796 s × US$ 1,09/h ÷ 3600 = US$ 0,2410, só a GPU. O RunPod cobra o disco à parte: com 120 GB de contêiner e 70 GB de volume ligados, mais US$ 0,0058 (US$ 0,2468). |
| "US$ 0,0482 por vídeo" | 0,241 ÷ 5: só a GPU da sessão, dividida pelos vídeos dela. Não é o custo unitário: faltam os discos (parado e ligado), o servidor, o R2, as taxas e a revisão (`docs/11`). |

O relatório (`relatorio_geradores`) agora separa, por sessão e no total:

- tentativas, tentativas que falharam e jobs finais certos (entregues) ou com falha;
- vídeos que o cliente disse que pode usar como estão (`aproveitaveis`) e os que precisam mudar;
- o tempo ocioso;
- o custo de GPU (`custo_usd`, como antes) e o custo com os discos, ligado e parado
  (`disco_ligado_usd`, `disco_parado_usd`, `custo_total_usd`);
- o custo por vídeo entregue e por vídeo aproveitável.

O preço do disco vem da tabela do RunPod (US$ 0,10/GB/mês ligado, 0,20 parado, mês de 720 h:
`RENDER2GO_RUNPOD_DISCO_USD_GB_MES` e `RENDER2GO_RUNPOD_VOLUME_PARADO_USD_GB_MES`). Ele confere com o faturamento: 70 GB
parados deram US$ 0,019444/h em 12–17/10; uma L40S ligada de US$ 1,09/h deu US$ 1,104–1,118 por hora de GPU, e a
diferença é o disco. Ficam de fora, e a página diz: o servidor, o R2, as taxas de pagamento e a revisão pela equipe.

### 1.3 O disco de volume "vazio" depois da retomada

O diagnóstico completo, com as evidências (metadados do pod e faturamento por hora, só leitura), está em
[`worker/evals/volume-2026-10-17/`](../worker/evals/volume-2026-10-17/LEIAME.md).

**Conclusão: pod substituto.** A atualização do servidor (`bdec7f0` → `a19529b`, ~17:20 UTC) mudou a versão da
hibernação (`83ade25e…` → `1ada2ec0…`: `Dockerfile`, `models.lock.json` e `pod_agente.py`). Na partida, o orquestrador
encerrou o hibernado de 12/10 (`hf4dbmglblw7sh`, cobrado só pelo disco até ~17:25 UTC; nunca voltou em 5 dias:
US$ 2,56 de disco), e o disco dele foi junto. O pedido das 17:38 subiu um pod do zero (`ltuxxfgujgnpsm`:
`createdAt` = `lastStartedAt`, nunca voltou), com um disco novo, vazio como todo disco novo. Os 5 min 37 s estão na
faixa da subida do zero, não da volta. Desta vez o descarte era necessário: o agente de `bdec7f0` ainda podia mandar
`podTerminate` no desligamento automático de um pod que hiberna.

Grau: forte (metadados, faturamento e código concordam). Falta a confirmação nos logs do orquestrador e na tabela
`geradores` (comandos só de leitura no `LEIAME.md`). Ficam abertas: a sentinela e o marcador sobreviverem a uma
parada (pede um ciclo num pod descartável), a cópia completa do pod novo (só a próxima volta confirma) e em que tela o
dono viu o volume vazio.

### 1.4 Verificação da montagem e diagnóstico estruturado (implementado)

- `render2go_worker/volume.py` (só a biblioteca padrão, rodado no pod com `python3 -I`) e o bootstrap gravam
  `$R/volume.json`:
  - a montagem que contém o cache (`/proc/self/mountinfo`), o sistema de arquivos e se ela é **persistente**: uma
    montagem própria, em outro dispositivo que não a raiz, e que não é overlay nem tmpfs;
  - o espaço total e livre;
  - a sentinela (`$L/sentinela.json`, gravada na 1ª subida: o pod que gravou e quando);
  - o marcador (a versão e quando a cópia terminou) e as entradas do volume;
  - o motivo, com um código fixo: `primeira` (pod novo, esperado), `vazio` (volta com o volume vazio), `sem_montagem`,
    `sem_marcador` (cópia anterior incompleta ou invalidada), `outra_versao`, `incompleto`, `ambiente_incompleto`,
    `corrompido`, `outro_pod`, `sem_espaco`, `copia_falhou`, `ok`. Ele é gravado na subida (`inicio`) e no fim da 1ª
    subida (`gravacao`).
- **Fail-closed:** o cache só é gravado numa montagem persistente e que comporta tudo (`du` do que vai × o tamanho da
  montagem, com 2% de folga). Sem isso, não há marcador, e o orquestrador encerra o pod em vez de hibernar um disco
  que voltaria vazio. O SHA-256 continua sendo refeito em toda volta.
- O orquestrador lê o diagnóstico com `volume.ler_diagnostico` (só os campos conhecidos, com limites) na subida e no
  fim do worker, na mesma consulta do fim da fila. Ele guarda em `geradores.volume`, junto com os tempos do bootstrap,
  os discos e a máquina (`machineId`). O painel mostra a coluna "Cache" ("usado", "1ª subida", "volume vazio", "sem
  montagem"...) e, na situação, o fim do disco de cada hibernado encerrado, com as duas versões no motivo.
- **Sem segredos:** nada do ambiente do pod além do id dele; nada dos pedidos.
- A versão da hibernação desta branch continua `1ada2ec01194e930`: subir esta branch não encerra o hibernado de agora
  (com o `.env` sem `RENDER2GO_MANIFESTO_HIB=2`, `RENDER2GO_LEGENDA_POR_FOTO=1` ou `RENDER2GO_POD_IMAGEM`).

### 1.5 Testes

- `servico/tests/test_sessoes_pod.py` (SQLite e Postgres): duas sessões do mesmo pod; jobs antigos pelo horário; a
  volta que falhou com a sessão e o custo dela; o disco parado entre as sessões e até o fim; o exemplo do painel
  (US$ 0,241 e 0,2468); tentativas com prazo vencido, devolução e cancelamento; o retorno do cliente; e o inventário
  dos hibernados.
- `worker/tests/test_volume.py`: o ponto de montagem e os sistemas de arquivos efêmeros; a classificação de cada falta
  de cache; a etapa da gravação; a leitura só dos campos conhecidos (nada de token nem marcação); e, no bootstrap
  carregado com `source`, o motivo de cada volta e a recusa de gravar fora de uma montagem persistente.
- O teste dos 10 pods com saldo para 3 (`test_db.py` e `test_versoes.py`) passa no Postgres com a fila nova
  (`reivindicar` grava a sessão e a tentativa).

## 2. Arquitetura da subida (Etapa 2)

### 2.1 Princípio: a base se reconstrói; a hibernação é uma otimização

A base é o ambiente fixado por versão e os pesos versionados no espelho R2 (`models.lock.json`, bytes e SHA-256),
copiados para um cache local conferido. Hoje é o bootstrap; com o experimento da imagem por digest, o ambiente da
imagem; e o fallback é sempre o bootstrap. A hibernação (ADR 0011) só economiza o download e a instalação quando o pod
volta. Ela pode faltar a qualquer momento: o host sem GPU livre, uma atualização que muda a versão, o encerramento
pelo painel. Nada pode depender dela.

### 2.2 Onde está o tempo (medido antes; agora registrado por sessão)

| Etapa | Subida do zero (08/10, L40S) | Volta da hibernação (11/10) |
|---|---|---|
| ambiente pronto (venv, ComfyUI, Ollama) | 104–189 s | links e apt: ~20–30 s |
| modelos baixados e conferidos (37 GB, espelho R2) | 123–216 s | 0 (SHA-256 refeito em 2º plano) |
| PRONTO | 160–221 s | — |
| 1º vídeo começando | 307–407 s | 64–67 s |

Desde esta mudança, cada sessão guarda os tempos do bootstrap (`geradores.etapas`), a hora em que o agente respondeu,
a hora em que o worker começou, o 1º vídeo começando e a 1ª entrega. As métricas do painel resumem as etapas por GPU,
datacenter e tipo de subida (`bootstrap_<etapa>_s`, n e p50; p95 só com n ≥ 20). A carga dos modelos na VRAM fica
dentro do 1º vídeo (o ComfyUI carrega no 1º prompt): a diferença entre o 1º vídeo de uma sessão e os seguintes
(`jobs.etapas`) mostra esse custo. Uma amostra só não vira p95.

**Datacenter:** a escolha continua pela velocidade medida dos downloads (`velocidades`). Com `primeira_entrega_s` por
sessão e por datacenter, a escolha pode passar a ser pelo tempo até a 1ª entrega, quando houver amostras. Os 5 min 37 s
de 17/10 em OC-AU-1 foram uma subida do zero, dentro da faixa medida em outros datacenters: não há evidência contra o
OC-AU-1.

### 2.3 Imagem do ambiente por digest (experimento de 14/10): bloqueado aqui

O protocolo, os critérios escritos antes e o coletor estão em `worker/evals/bootstrap-imagem-2026-10-14/`. Para medir o
pull frio e quente, a instalação, o download, o hash, a carga na VRAM, a tradução, o render, o filtro, o envio e a 1ª
entrega, falta:

1. um registro de imagens que o RunPod alcance, com credencial de envio. Esta sessão não tem nenhuma: o GitHub daqui
   não publica imagens no GHCR;
2. espaço para a build: a imagem tem ~12–14 GB, e este ambiente tem ~12 GB livres depois da limpeza de 17/10;
3. o orçamento dos pods (US$ 1,65 previsto, teto de US$ 3; seção 6).

Sem a imagem, a produção continua no bootstrap de sempre (`RENDER2GO_POD_IMAGEM` vazia).

### 2.4 Só os modelos de que o pod precisa

- O pod de vídeo baixa só o conjunto `i2v_r1v2` (37 GB). Os modelos de edição de imagem (seção 3) ficam num conjunto
  próprio, que **nenhum pod de vídeo baixa**.
- Os pods são separados por capacidade: o worker diz quais modelos roda, e a fila só entrega o job desses modelos
  (`modelos` em `Banco.reivindicar`, que já existe).
- **Workers especializados, não carregamento sequencial na mesma GPU:**
  - o Qwen-Image-Edit-2511 em BF16 tem ~20 B de parâmetros (~41 GB só o transformer, mais o codificador de texto
    Qwen2.5-VL de 7 B);
  - os dois experts do Wan 2.2 em fp8 têm ~14 GB cada;
  - numa L40S de 48 GB, os dois não cabem juntos, e trocar um pelo outro a cada pedido custa a carga inteira (minutos
    de disco para a VRAM);
  - manter os dois na VRAM ao mesmo tempo só com uma medição que prove que cabe e que não piora o vídeo. Não há essa
    medição.

### 2.5 Volume de rede e Global Volume: experimentos separados

A avaliação continua a do `docs/07`, seção 12. Volume de rede (US$ 0,07/GB/mês, independente do pod, preso a um
datacenter) e Global Volume são outra arquitetura de cache, com regras próprias (sem `rename`/`chmod`, `rsync
--inplace`, bytes conferidos depois de copiar, e nunca carregar o modelo direto do volume). Não entram nesta rodada.

### 2.6 `RENDER2GO_PODS_HIBERNADOS`: inventário e plano (nada mudado)

**Efeito de reduzir o número:** na volta seguinte do ciclo, `_estado_runpod` encerra os hibernados acima do número (os
parados há mais tempo primeiro), e **o disco de volume de cada um vai junto**. Com 1 → 0, o `ltuxxfgujgnpsm` seria
encerrado. Uma atualização que muda a versão da hibernação faz o mesmo (17/10). Na partida, o orquestrador também
encerra os pods parados deste serviço que não estão no conjunto (`_conferir_hibernados`).

**Inventário, antes de mudar o número ou de subir uma atualização** (só leitura, sem tokens):

```bash
cd ~/GPU2GO/servico/implantacao && docker compose build api   # a imagem render2go-servico, que o orquestrador usa
docker compose run --rm --no-deps orquestrador render2go-servico hibernados --runpod
```

O comando lista cada hibernado (GPU, datacenter, desde quando está parado, voltas, falhas, versão e ~US$/dia
parado) e diz qual **seria encerrado** pelo código da imagem nova e por quê, além dos parados fora do conjunto.

| Opção | Custo fixo | Ganho | Risco |
|---|---|---|---|
| manter 1 (hoje) | 70 GB parados: US$ 0,47/dia ≈ US$ 14/mês | o 1º pedido de um episódio começa em ~1 min, não ~5 | a volta falha sem GPU livre no host; cada atualização que muda a versão descarta o disco; de 12 a 17/10 o hibernado nunca foi usado (US$ 2,56 por nada) |
| 0 | nenhum | — | todo 1º pedido espera a subida do zero (~5 min) |
| aquecer antes de uma sessão marcada do piloto | L40S ligada: US$ 1,116/h com os discos (3 min = US$ 0,056; 24 h = US$ 26,8) | começa na hora | caro se ninguém pedir; precisa de uma janela combinada |

Recomendação (decisão do dono, configuração no `.env`): manter `RENDER2GO_PODS_HIBERNADOS=1` durante as semanas do
piloto, conferir o inventário antes de cada atualização e voltar a 0 fora delas. Nenhuma mudança foi feita.

### 2.7 Ligar uma GPU só para traduzir (modo "revisar", ADR 0018)

No modo "revisar", a tradução roda num pod, e o pedido espera o cliente aprovar a versão vista. O bloqueio real do ADR
0018 continua: nada é liberado sem a aprovação.

- **Custo de hoje, por pedido que espera o cliente:**
  - subida até o AMBIENTE com a tradução (~2–3 min do zero, ~1 min na volta) mais os 3 min de pod ocioso
    (`RENDER2GO_POD_OCIOSO_MIN`): ≈ US$ 0,08–0,11 de GPU a US$ 1,116/h;
  - se o cliente aprova depois que o pod saiu, os vídeos pagam outra subida.
- **Tradução em CPU, medida:** o compilador de produção (`huihui_ai/qwen2.5-abliterate:7b`, digest conferido) no
  Ollama v0.35.1 em CPU, neste ambiente de 4 vCPU: p50 7,8 s, p90 13,8 s, máx. 36 s em 150 traduções (13/10,
  `worker/evals/compiler/relatorios/2026-10-13-cpu-i2v6-casos-v3-7b.json`). O modelo precisa de ~5 GB de RAM: **não
  cabe no servidor de 4 GB** e nunca roda no processo da API.
- **Proposta (não implementada; pede um ADR):**
  - um worker de tradução só em CPU, numa máquina separada, que pega só os jobs `compilar` (a fila já filtra por tipo,
    `tipos=["compilar"]`);
  - o mesmo modelo, o mesmo digest, o mesmo prompt, a regra `REFUSED`, o filtro C1 e a guarda da pessoa inventada;
  - os pods de vídeo continuam traduzindo quando o worker de CPU não está.
  - Antes, medir na máquina escolhida (o preço dela e o p95 com n ≥ 20).
