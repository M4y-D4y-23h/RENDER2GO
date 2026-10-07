# ADR 0003 — Arquitetura para lotes em escala (pastas grandes, muitos clientes)

- **Status:** **aceito em 07/10/2026** pelo dono do projeto. Fase 2 implementada (`lote_nuvem.py`, seção "Fase 2 implementada"). Fases 3 e 4 por fazer.
- **Contexto:** caminho A (docs/05, seção 5.1), com lotes de 50+ fotos e um pedido só. O dono pediu que o sistema já nasça preparado para escalas muito grandes: pastas enormes, muitos pedidos, download e envio de arquivos, armazenamento, sobrecarga e segurança.
- **Relacionados:** doc 02 (protocolo de jobs por URL assinada, já implementado em `jobs.py` e `media.py`), ADR 0002 (imagem do worker), doc 06 (GPU e custo).

## Princípios

1. **Os bytes nunca passam pelo servidor da aplicação.**
   - O navegador do cliente envia as fotos direto para o armazenamento de objetos, com URL pré-assinada e envio em partes retomável.
   - O worker baixa e devolve por URL assinada (protocolo existente).
   - A API só cria registros e assina URLs, então aguenta muitos clientes numa máquina pequena.
2. **Fila no meio de tudo.** Cada foto vira um job. A fila absorve picos: 10 pastas de 1.000 fotos esperam na fila e não derrubam nada. A velocidade vem de quantas GPUs consomem a fila.
3. **Os workers puxam trabalho; ninguém empurra.**
   - Nenhuma porta de entrada nos workers (regra atual: ComfyUI e Ollama só em 127.0.0.1).
   - Um worker que cai devolve o job para a fila (timeout de visibilidade) e outro pega.
4. **Tudo idempotente pelo `job_id`** (já existe em `jobs.py`). Repetir um job não gera cobrança dupla nem vídeo duplicado.
5. **Fail-closed** como no resto do projeto: na dúvida sobre arquivo, tamanho, tipo ou permissão, o job não roda.

## Componentes

```
Cliente (navegador/celular)
  │ 1. escolhe pasta/fotos (input webkitdirectory) e escreve o pedido
  │ 2. API cria o lote e devolve URLs pré-assinadas (uma por foto, multipart)
  │ 3. envio direto ao armazenamento, em paralelo, retomável
  ▼
Armazenamento de objetos (Cloudflare R2: sem custo de saída)
  clientes/<cliente>/<lote>/entrada/*.jpg     → apagadas em 7 dias
  clientes/<cliente>/<lote>/saida/*.mp4|.zip  → apagadas em 30 dias
  ▲                                   │
  │ 6. vídeos (URL assinada)          │ 5. fotos (URL assinada)
  │                                   ▼
Workers GPU (pods RunPod; depois, serverless para picos)
  puxam jobs da fila → preparo → compilador (1×/lote) → render → folha → envio
  ▲
  │ 4. jobs (1 por foto) + prompt compilado do lote
Fila + banco (Postgres; fila com SELECT ... FOR UPDATE SKIP LOCKED)
  ▲
API (FastAPI, sem estado) ── Orquestrador (sobe e derruba pods pela fila)
  │
  └ 7. aviso ao cliente (e-mail/WhatsApp) com link para a pasta de saída ou o .zip
```

### Armazenamento: Cloudflare R2

Preços conferidos em 06/10/2026 (semaphore.io, resumo do preço público do R2):
- US$ 0,015 por GB/mês;
- operações de escrita US$ 4,50 por milhão; leitura US$ 0,36 por milhão;
- **saída de dados gratuita.**

Para vídeo entregue a cliente, a saída grátis é o que decide. No S3, o download pago dominaria a conta.

Lote de 50 fotos:
- ~150 MB de fotos (3 MB cada) e ~100 MB de vídeos (2 MB cada);
- armazenamento por 30 dias: < US$ 0,01;
- ~300 operações: < US$ 0,01.

**O armazenamento custa praticamente zero perto da GPU.**

Regras do armazenamento:
- **Ciclo de vida automático:** entradas apagadas em 7 dias, saídas em 30. O espaço não cresce sem limite, o que também atende à LGPD.
- **Prefixo por cliente.** A URL assinada vale só para um objeto, por 15 min (envio) ou 24 h (download).
- **Pastas enormes:** envio multipart (partes de 8 a 16 MB), retomável no navegador. Limite por lote (ex.: 1.000 fotos e 50 MB por foto) e por cliente (cota), checados na API antes de assinar.
- **Download do resultado:** link da pasta (lista de URLs assinadas) ou um .zip montado por um worker de CPU, em streaming, sem carregar tudo em memória.

### Fila e banco: Postgres

- Uma tabela `jobs` com status, tentativas, `lease_until` e erro. Os workers pegam jobs com `FOR UPDATE SKIP LOCKED`.
- Aguenta milhares de jobs por minuto sem peça nova. Redis ou SQS só se medirmos gargalo.
- Tabelas: `clientes`, `lotes` (pedido, prompt compilado, presets, contagem), `jobs` (foto, formato, seed, status, GPU, tempo, custo), `eventos` (auditoria).
- **Retentativas:** 3 por job, com espera crescente. Depois, o job vai para "falhou" e aparece no relatório do lote, como no `relatorio.csv` de hoje.

### Workers e orquestrador

O worker de lote é o `lote.py` de hoje com outra origem e outro destino: lê da fila em vez de uma pasta e escreve no R2 em vez do disco. O preparo, o compilador uma vez por lote, o render e a folha de conferência continuam iguais.

**Orquestrador**, um processo pequeno que roda a cada 30 s:
- `GPUs necessárias = min(teto, ceil(jobs na fila × 80 s / prazo do lote))`;
- sobe pods (conferindo o estoque na hora, community ou secure, com teste de CUDA, como `runpod_pods.py subir` e `iniciar.sh`) e derruba pods ociosos depois de N minutos;
- todo pod tem desligamento automático (`AUTO_TERMINATE_HOURS`) caso o orquestrador caia.

Modos de operação:

| Fase | Volume | Modo |
|---|---|---|
| Começo | poucos lotes por dia | Pod sob demanda por lote: sobe, processa, encerra. ~5 a 10 min de subida |
| Crescendo | lotes o dia todo | 1 ou 2 pods fixos no horário comercial (agenda) e pods extras pela fila |
| Grande | picos imprevisíveis | Pods fixos para a carga base e serverless (`handler_runpod.py`, já existe) para os picos; uma imagem com modelos por região |

Escala aproximada: ~45 vídeos por hora por GPU 4090 (80 s cada). 1.000 vídeos por hora pedem ~23 GPUs. O custo de GPU fica igual por vídeo, só aumenta o paralelismo.

### Segurança nos processos

- **Envio:**
  - só tipos de imagem (conferidos pelo conteúdo, não pela extensão);
  - limite de tamanho e de pixels (50 MP, já no `preparo.py`: protege contra "bomba de descompressão");
  - a foto é decodificada e regravada como PNG limpo, o que elimina EXIF, GPS e conteúdo escondido.
- **URLs:** assinadas, curtas e por objeto. Só https, e só hosts permitidos no worker (`GPU2GO_ALLOWED_URL_HOSTS`, já existe).
- **Acesso:**
  - conta por cliente;
  - chaves de API do RunPod e do R2 só no orquestrador (cofre de segredos), nunca no worker. O worker recebe só URLs assinadas;
  - registro de auditoria de cada lote.
- **Conteúdo:**
  - SFW com filtros C1–C2 obrigatórios no worker (`GPU2GO_SAFETY`, já existe; fail-closed);
  - receitas com pessoa exigem consentimento registrado;
  - a folha de conferência fica guardada para revisão humana amostral.
- **Abuso e sobrecarga:**
  - limite de requisições por cliente na API;
  - cota de fotos por dia;
  - lotes acima de um tamanho entram numa fila de prioridade mais baixa;
  - teto de GPUs no orquestrador (proteção de gasto) e alerta de gasto por hora.
- **LGPD:**
  - ciclo de vida apagando dados;
  - exportar e apagar sob pedido;
  - contrato de processamento com o cliente (fotos de terceiros).

## Custos mensais estimados (sem GPU)

| Peça | Começo | Escala grande |
|---|---|---|
| R2 (armazenamento + operações) | < US$ 1 | dezenas de US$ |
| Postgres gerenciado | US$ 0 a 25 | US$ 50 a 200 |
| API + orquestrador (1 VM pequena ou serviço gerenciado) | US$ 5 a 20 | US$ 50 a 150 (2+ instâncias) |
| Monitoramento (Sentry/Grafana, plano básico) | US$ 0 | US$ 30 a 100 |

A GPU continua sendo o custo principal, ~US$ 0,016 por vídeo na 4090 secure.

## Fases de implementação

1. **Fase 1 (feita):** lote local (`lote.py`), preparo, acabamento, pods com desligamento automático.
2. **Fase 2, "lote na nuvem com um comando":** script local que sobe N pods, envia a pasta, roda `lote --gpus`, baixa os vídeos e encerra os pods. Não precisa de R2 nem de banco. Atende os primeiros clientes. Estimativa: 1 a 2 dias.
3. **Fase 3:** R2 + Postgres + API + orquestrador + página de envio. O cliente envia sozinho e recebe o link. Estimativa: 2 a 3 semanas.
4. **Fase 4:** pool fixo + serverless para picos, imagem por região, painel de custos, cotas e cobrança.

## Fase 2 implementada (07/10/2026)

- **Comando:** `tools/lote_nuvem.py` (`gpu2go-lote-nuvem`). Uso em `worker/README.md`, seção "Lote na nuvem".
- **Canal de controle dos pods efêmeros:** `pod_agente.py`. É um agente HTTP na porta 8888, atrás do proxy HTTPS do RunPod, com token aleatório de 256 bits por pod e comparação em tempo constante; sem token, a resposta é 403.
  - É a única porta do pod. ComfyUI e Ollama continuam em 127.0.0.1.
  - Exceção registrada à regra "nenhuma porta exposta", válida só para estes pods de lote, que vivem minutos e se encerram sozinhos.
  - SSH foi descartado porque exige IP público e porta TCP por host, o que nem todo pod tem e que o ambiente de desenvolvimento não alcança.
  - Na fase 3 o canal some: os workers passam a puxar jobs da fila, sem porta de entrada.
- **Desligamento automático** dentro do agente (`DEADMAN_S`), contado do primeiro segundo do pod: 2× a estimativa + 20 min, no máximo 6 h. Vale mesmo que o orquestrador caia.
- **Pod com defeito** é descartado na hora:
  - agente sem resposta em 10 min;
  - `cuInit` diferente de 0;
  - instalação ou serviços com erro.

  O trabalho fica com os outros pods.
- **Instalação:** `scripts/bootstrap_pod.sh` lê versões e hashes do `worker/Dockerfile` e confere os modelos por SHA-256. Leva ~6–10 min por pod, até existir a imagem com modelos (ADR 0002).
- **Sem armazenamento de objetos ainda:** as fotos saem da máquina de quem roda o comando, preparadas e pequenas, e os vídeos voltam para ela.

## Decisão pendente

Fase 3 (R2 + Postgres + API): começa depois de validar a fase 2 com os primeiros clientes, com confirmação do dono do projeto. Alternativas consideradas:
- **S3 da AWS:** saída paga, cara para entregar vídeo.
- **Volume de rede do RunPod como armazenamento do cliente:** prende a um datacenter e não tem URL pública assinada.
- **Fila Redis/SQS desde o início:** peça a mais sem necessidade medida.
