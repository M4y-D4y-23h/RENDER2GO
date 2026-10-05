# 01 — Verificação dos itens [NÃO VERIFICADO]

Data da conferência: **05/10/2026**. Referência: [`referencia/especificacao-pipeline-manual.md`](../referencia/especificacao-pipeline-manual.md) (as seções citadas abaixo são as dela).

## Método e nível de confiança

| Fonte | Como foi lida | Confiança |
|---|---|---|
| `runpod-workers/worker-comfyui` (commit `b7574c8`, 21/09/2026, versão 5.10.0) | Código-fonte, via `git clone` | Alta |
| `comfyanonymous/ComfyUI` (tags `v0.34.0` e `v0.38.0`) | Código-fonte, via `git clone` | Alta |
| Workflow anexado (`referencia/wan-i2v-com-ollama.api.json`) | Analisado por script (grafo, valores efetivos) | Alta |
| `runpod.io`, `docs.runpod.io`, `huggingface.co`, `ollama.com`, `comfy.org` | **Bloqueados pela política de rede do ambiente onde esta conferência rodou.** Usei busca web, que devolve trechos das páginas | Média: confirmar abrindo as páginas |
| Legislação, pagamentos, moderação | Busca web (sites oficiais, escritórios de advocacia, fornecedores) | Média; **não é parecer jurídico** |

Legenda: **[CONFIRMADO]** · **[PARCIAL]** (parte confirmada, parte em aberto) · **[NÃO CONFIRMADO]** · **[CONFLITO]** (contradiz uma premissa do projeto).

## Resumo

| # | Item (seção da especificação) | Status | Impacto |
|---|---|---|---|
| 1 | Termos do RunPod para conteúdo adulto (11.7) | **[CONFLITO]** | **Bloqueador do produto como descrito** |
| 2 | Revenda de computação no RunPod (11.7) | [NÃO CONFIRMADO] | Perguntar junto com o item 1 |
| 3 | Licença do NSFW-UMT5 e do patch (2.2, 11.9) | [NÃO CONFIRMADO] | Bloqueia uso comercial do encoder até confirmar |
| 4 | Licenças de Wan 2.2, R1 v2, Qwen2.5 e derivados (11.9) | [PARCIAL] | Baixo, exceto NSFW-UMT5 |
| 5 | Tag `huihui_ai/qwen2.5-abliterate:14b` (2.7) | [CONFIRMADO] | — |
| 6 | Versões VL abliteradas (2.7) | [PARCIAL] | Só para etapa futura |
| 7 | Nomes das LoRAs Lightning (2.6) | [CONFIRMADO]/[PARCIAL] | Só se usar modelos oficiais |
| 8 | Export de subgraph em formato API (7.5) | [CONFIRMADO] | Não precisa achatar à mão |
| 9 | Versão do ComfyUI exigida pelo workflow (novo) | [CONFIRMADO] | Fixar ComfyUI 0.38.0 |
| 10 | `worker-comfyui` atual (10.2-B) | [CONFIRMADO] | Base de referência para o worker |
| 11 | API do RunPod: pods, endpoints, catálogo (10.2-A, 12) | [PARCIAL] | Global Volume via API em aberto |
| 12 | Global Volume (3.3) | [PARCIAL] | Preço e regra dos 15 dias não reconfirmados |
| 13 | Serverless: cache de modelos, limites, webhooks, timeouts (10.2-B) | [CONFIRMADO] | Define o desenho do worker |
| 14 | Preços atuais (3.5) | [PARCIAL] | Consultar dinamicamente via API |
| 15 | PyTorch cu130 (2.9) | [CONFIRMADO] | Manter cu128 |
| 16 | Pagamentos (11.8) | [CONFIRMADO] | Exige processador especializado |
| 17 | Legislação aplicável (11) | [PARCIAL] | Requisitos novos para a v1 |
| 18 | Ferramentas de moderação e CSAM (11.3–11.4) | [PARCIAL] | Elegibilidade e preço em aberto |
| 19 | Valores do workflow anexado (7) | Divergências | Corrigir no template |

---

## 1. Termos do RunPod para conteúdo adulto — [CONFLITO]

- **Achado:** duas buscas independentes no domínio `runpod.io` devolvem o trecho dos Termos de Serviço que lista, entre os conteúdos não autorizados, *"intoxicants, illegal drugs, alcoholic beverages, games of chance, and pornography or graphic adult content, images, or other adult products"*, e o aviso *"Postings of any unauthorized products or content may result in immediate termination of your account and a lifetime ban from use of the Site"*. A redação acima é a dos resultados de busca; conferir a exata na página.
- Um blog de terceiros afirma que os termos "não têm uma frase simples sobre NSFW" e que o RunPod "vende computação"; o texto dos Termos é o que vale.
- Fonte: <https://www.runpod.io/legal/terms-of-service> (página bloqueada neste ambiente; texto obtido por busca).
- **Impacto:**
  1. A plataforma, como descrita, provavelmente viola os Termos do RunPod.
  2. O uso manual atual (geração de vídeo adulto com os vídeos guardados no Global Volume) também pode estar em conflito.
  3. Risco de encerramento da conta, banimento e perda do Global Volume.
- **Contexto de mercado:** nenhum grande provedor de GPU pesquisado permite conteúdo adulto de forma explícita. O Vast.ai proíbe conteúdo "obscene" (termo jurídico ambíguo) ([termos](https://vast.ai/terms)); a Atlas Cloud proíbe "adult content" ([AUP](https://www.atlascloud.ai/acceptable-use)).
- **Ação:** ver Etapa 0 em [`04-plano-de-implementacao.md`](04-plano-de-implementacao.md): ler a cláusula inteira na página oficial, pedir resposta **por escrito** ao RunPod (há um rascunho de mensagem lá) e, enquanto isso, desenvolver e testar só com conteúdo SFW. A arquitetura proposta é agnóstica de provedor justamente por causa deste item.

## 2. Revenda de computação no RunPod — [NÃO CONFIRMADO]

- Não encontrei proibição explícita de revenda. Os Termos mencionam *"RunPod reseller account"* (no trecho sobre CSAM: a conta revendedora é suspensa e orientada a encerrar a conta responsável), o que sugere que revenda é uma modalidade prevista, com obrigações próprias.
- **Ação:** perguntar na mesma mensagem do item 1.

## 3. Licença do NSFW-UMT5 e do patch `ComfyUI-UMT5-Extended` — [NÃO CONFIRMADO]

- `huggingface.co` está bloqueado aqui e as buscas não exibem o campo de licença de `rzgar/NSFW-Wan-UMT5-XXL-V2`.
- O UMT5-XXL original do Google é Apache-2.0 (conhecimento prévio, não reverificado). Um finetune pode declarar outra licença; **sem licença declarada, não há permissão explícita** para uso comercial nem para redistribuir os pesos ou o patch dentro de uma imagem Docker.
- **Ação:** abrir o card (aceitando o aviso de conteúdo) e anotar o campo `license` e o texto do README. Se estiver ausente ou restritivo, perguntar ao autor ou adotar o encoder oficial `umt5_xxl_fp8_e4m3fn_scaled.safetensors` (seção 2.5), medindo a perda de fidelidade.

## 4. Licenças dos modelos e do software — [PARCIAL]

| Componente | Licença | Status | Observação |
|---|---|---|---|
| Wan 2.2 I2V/T2V A14B (base) | Apache-2.0 | [CONFIRMADO] | Derivados no HF declaram herdar a Apache-2.0 de `Wan-AI/Wan2.2-I2V-A14B` ([ex.](https://huggingface.co/QuantStack/Wan2.2-I2V-A14B-GGUF)) |
| Wan2.2 I2V R1 v2 (`rzgar`) | Apache-2.0 segundo o card | Não reverificado (HF bloqueado) | Anotado na seção 2.1 |
| Qwen2.5-7B-Instruct e 14B-Instruct | Apache-2.0 | [CONFIRMADO] | Por busca; canônico: cards `Qwen/Qwen2.5-7B-Instruct` e `Qwen/Qwen2.5-14B-Instruct` |
| `huihui_ai/qwen2.5-abliterate` | Herda do Qwen2.5 | [PARCIAL] | Conferir o card do huihui-ai |
| NSFW-Wan-UMT5-XXL-V2 + patch | ? | [NÃO CONFIRMADO] | Item 3 |
| VAE `wan_2.1_vae` (repack Comfy-Org) | Apache-2.0 (Wan) | [PARCIAL] | — |
| LoRAs lightx2v | ? | [NÃO CONFIRMADO] | Só se forem usadas |
| ComfyUI | GPL-3.0 | [CONFIRMADO] | Usar como serviço não exige publicar código |
| `worker-comfyui` | **AGPL-3.0** | [CONFIRMADO] (arquivo `LICENSE`) | Copiar e modificar o handler traria obrigações da AGPL; a proposta usa handler próprio |
| Custom nodes | variadas | Auditar | A imagem de produção leva só o necessário (doc 02) |

## 5. Tag `huihui_ai/qwen2.5-abliterate:14b` — [CONFIRMADO]

- O registro do Ollama lista as tags `0.5b`, `1.5b`, `3b`, `7b`, `14b`, `32b` e `72b` ([registro](https://registry.ollama.ai/huihui_ai/qwen2.5-abliterate)).
- Com ~9 GB (estimativa da seção 2.7), a VRAM reservada para o LLM precisa subir para ~10 GB; isso só cabe com folga em GPUs de 48 GB, ou rodando o compilador antes de carregar o Wan.

## 6. Versões VL abliteradas — [PARCIAL]

- No HF, o huihui-ai publica versões abliteradas do **Qwen3-VL** (de 2B a 235B; ex.: `huihui-ai/Huihui-Qwen3-VL-8B-Instruct-abliterated`, [coleção](https://huggingface.co/collections/huihui-ai/qwen3-vl-abliterated)) e do **Qwen2.5-VL** (3B e 32B).
- Empacotamento oficial no registro do Ollama: não confirmado.
- Um compilador que enxerga a imagem pode melhorar a fidelidade, mas **não serve como filtro de segurança** (ver doc 03).

## 7. Nomes das LoRAs Lightning — [CONFIRMADO]/[PARCIAL]

Repositório `Comfy-Org/Wan_2.2_ComfyUI_Repackaged`, pasta `split_files/loras/`:

| Uso | Arquivo | Status |
|---|---|---|
| I2V oficial, high noise | `wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors` | [CONFIRMADO] |
| I2V oficial, low noise | `wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors` | [CONFIRMADO] (já citada na seção 2.5) |
| T2V oficial, high noise | `wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors` (renomeada de `Wan2.2_T2V_A14B_4steps_lora_rank64_Seko_high_noise_V1.1.safetensors`) | [CONFIRMADO] |
| T2V oficial, low noise | `wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors` | [PARCIAL]: inferido pelo padrão; confirmar |

Fontes: [comfy.org — T2V v1.1 high](https://comfy.org/p/supported-models/wan2-2-t2v-lightx2v-4steps-lora-v1-1-high-noise.md), [comfy.org — I2V v1 high](https://comfy.org/p/supported-models/wan2-2-i2v-lightx2v-4steps-lora-v1-high-noise.md). Continuam **proibidas com o R1 v2**.

## 8. Export de subgraph em formato API — [CONFIRMADO]

O próprio arquivo anexado responde à pergunta da seção 7.5 (frontend 1.53.10):

- O subgraph **é achatado automaticamente** no export. Os nós internos recebem IDs no formato `<ID do nó externo>:<ID interno>` (`129:98`, `129:86`, ...).
- Os dois `Load LoRA` em bypass **não aparecem** no JSON; os `Switch (Model)` apontam direto para os `UNETLoader` nos dois ramos.
- Os widgets do nó externo viram valores nos nós internos (ex.: duração em `129:161`, FPS em `129:162`).
- **Consequência:** não é preciso achatar à mão. Mas os IDs `129:*` dependem do ID do nó externo; o template de produção deve ser congelado e versionado no repositório, e não reexportado a cada ajuste.

## 9. Versão do ComfyUI exigida pelo workflow — [CONFIRMADO] (achado novo)

- O export usa a entrada `codec` do nó `CreateVideo`, que existe na **0.38.0** e não existe na **0.34.0** (a 0.34 só tem `bit_depth` e `color_space`). O `SaveVideo` também mudou a estrutura de `format`. Já `ComfyMathExpression`, `ComfySwitchNode` e os `Primitive*` existem nas duas.
- O `worker-comfyui` 5.10.0 fixa o ComfyUI em **0.34.0**. O próprio changelog avisa que workflows de versões novas falham com "missing node" em imagens antigas.
- **Regra:** a versão do ComfyUI e o template de workflow são versionados juntos. A imagem deve ser construída com o ComfyUI **0.38.0**.

## 10. `worker-comfyui` atual — [CONFIRMADO]

- **Versão 5.10.0** (21/09/2026). Base `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04`, Python 3.12, `uv`, `comfy-cli` 1.13.0, **torch 2.11.0+cu128**, `allowedCudaVersions` de 12.8 a 13.2 (driver ≥ 570).
- **Nós customizados:** Dockerfile próprio `FROM runpod/worker-comfyui:<versão>-base` com `RUN comfy-node-install <nome-ou-url>`. **Modelos:** `RUN comfy model download --url ... --relative-path models/<pasta> --filename ...`, ou network volume montado em `/runpod-volume/models/...`.
- **Entrada:** `{"input": {"workflow": {...}, "images": [{"name": "x.png", "image": "<base64>"}]}}`.
- **Saída:** cada arquivo de `outputs[nó].images` volta como base64 ou, com `BUCKET_ENDPOINT_URL`/`BUCKET_ACCESS_KEY_ID`/`BUCKET_SECRET_ACCESS_KEY`, como URL do S3.
- **Vídeo funciona:** o `SaveVideo` do ComfyUI 0.38 reporta o MP4 na chave `images` com `animated: [true]`; o handler oficial entrega o arquivo e só registra um aviso sobre a chave `animated`.
- Extras úteis: pré-validação dos modelos citados no workflow, checagem de GPU com um kernel real na subida, ComfyUI-Manager forçado para modo offline.
- **O que ele não faz:** não chama LLM, não aplica filtros de segurança e devolve arquivos inteiros no corpo da resposta (ver limites no item 13). Por isso a proposta usa **handler próprio**, com o oficial como referência de protocolo.

## 11. API do RunPod — [PARCIAL]

- **REST v1** (base `https://rest.runpod.io/v1`): `POST /pods` aceita `gpuTypeIds`, `containerDiskInGb`, `networkVolumeId`, `allowedCudaVersions`, `ports` (ex.: `"8888/http"`, `"22/tcp"`), `env` e `cloudType` (`SECURE`/`COMMUNITY`) ([referência](https://docs.runpod.io/api-reference/pods/POST/pods)). `POST /endpoints` aceita `networkVolumeId` e `networkVolumeIds` ([referência](https://docs.runpod.io/api-reference/endpoints/POST/endpoints)).
- **API v2** (nova, base `https://api.runpod.io/v2`): pods (listar, criar, obter, atualizar, apagar, logs) com transição de estado única `POST /v2/pods/{id}/action` e corpo `{"action": "start|stop|restart|terminate"}`; network volumes; endpoints serverless; **catálogo** `GET /v2/catalog/gpus`, `/cpus` e `/datacenters` para consultar oferta sem provisionar; há guia de migração da v1 ([visão geral](https://docs.runpod.io/api-reference-v2/overview)).
- **Jobs serverless** usam outro domínio: `https://api.runpod.ai/v2/{endpoint_id}/run`, `/runsync`, `/status/{job_id}`, `/cancel/{job_id}`.
- **Global Volume via API:** não encontrei o campo; a documentação descreve anexar pela interface. [NÃO CONFIRMADO]

## 12. Global Volume — [PARCIAL]

- **Confirmado:** beta; elástico (cresce sozinho); independente de região; anexável a **Pods GPU e endpoints Serverless GPU** (não a CPU); persiste após parar ou apagar o pod; feito para "escreve pouco, lê muito" (inferência), não para escrita frequente; em serverless monta em `/runpod-volume` ([visão geral](https://docs.runpod.io/storage/globalvolume/overview), [anúncio](https://www.runpod.io/blog/global-volumes-beta)).
- **Não reconfirmado** (página bloqueada): preço de US$ 0,09/GB/mês + IOPS e exclusão após 15 dias de saldo zerado.
- Consistente com as medições da seção 3.4: a regra "nunca carregar modelos direto do volume" continua valendo, inclusive em serverless.

## 13. Serverless: cache de modelos, limites, webhooks e timeouts — [CONFIRMADO]

- **Cached models** ([docs](https://docs.runpod.io/serverless/endpoints/model-caching)): o RunPod pré-carrega um modelo do Hugging Face no host; o worker o encontra em `/runpod-volume/huggingface-cache/hub/models--{org}--{nome}/snapshots/{hash}/`; **não há cobrança pelo tempo de download**; o agendador prefere hosts que já têm o modelo; funciona com imagem própria (o código resolve o caminho). **Em aberto:** mais de um repositório por endpoint? baixa o repositório inteiro ou só alguns arquivos? funciona com repositório marcado como NFAA?
- **Limites de payload:** `/run` 10 MB e `/runsync` 20 MB, valendo para entrada e saída ([referência](https://docs.runpod.io/serverless/endpoints/operation-reference)). Imagens e vídeos devem trafegar por URL assinada, nunca em base64.
- **Webhook:** campo `webhook` no corpo do `/run`; o RunPod faz POST ao concluir, espera HTTP 200 e tenta mais 2 vezes com 10 s de intervalo ([docs](https://docs.runpod.io/serverless/endpoints/send-requests)).
- **Timeouts:** idle timeout padrão de 5 s (1–3600 s); execution timeout padrão de 600 s (até 7 dias); FlashBoot ligado por padrão ([docs](https://docs.runpod.io/serverless/endpoints/endpoint-configurations)).
- **Armadilha operacional:** após 3 dias sem requisições, o máximo de workers do endpoint cai para 2 (com aviso por e-mail); após 7 dias, cai para 0.
- **Cobrança:** do início do worker até ele parar, arredondada para cima ao segundo; inclui cold start e idle timeout.

## 14. Preços atuais — [PARCIAL]

Valores coletados por busca em 05/10/2026, só como referência. **O backend deve consultar o catálogo (`/v2/catalog/gpus`) e uma tabela de configuração, nunca valores fixos no código.**

Serverless, worker flex:

| Tier | GPUs (conhecimento prévio; confirmar no console) | US$/s | US$/h |
|---|---|---|---|
| 16 GB | A4000, A4500, RTX 4000 | 0,00016 | 0,58 |
| 24 GB | L4, A5000, 3090 | 0,00019 | 0,69 |
| 24 GB PRO | RTX 4090 | 0,00031 | 1,10 |
| 32 GB PRO | RTX 5090, RTX PRO 4500 | ~0,00044 | 1,58 |
| 48 GB | A6000, A40 | 0,00034 | 1,22 |
| 48 GB PRO | L40, L40S, 6000 Ada | 0,00053 | 1,90 |
| 80 GB | A100 | 0,00076 | 2,72 |
| 80 GB PRO | H100 | 0,00116 | 5,59 (inconsistente com o valor por segundo; confirmar) |

Workers ativos (sempre ligados) têm até ~30% de desconto ([preços serverless](https://docs.runpod.io/serverless/pricing)).

Pods (US$/h, community / secure): RTX 4090 0,34 / 0,74; RTX 5090 0,53–0,69 / 0,99; RTX A6000 0,33 / 0,53; L40S 0,79 / 1,09 ([preços](https://www.runpod.io/pricing)). Cobrança por segundo/milissegundo.

## 15. PyTorch cu130 — [CONFIRMADO]

- A partir do torch 2.11, as wheels do PyPI dependem de CUDA 13 (`nvidia-*-cu13`) e exigem **driver ≥ 580**; as wheels `+cu128` rodam com **driver ≥ 570**, que é o que a maior parte dos hosts anuncia (CUDA 12.8/12.9). É por isso que o `worker-comfyui` fixa `torch==2.11.0` do índice `cu128`.
- **Decisão:** manter cu128. Começar com o 2.8.0+cu128 testado na seção 2.9, avaliar o 2.11.0+cu128 por teste A/B, e declarar `allowedCudaVersions` no endpoint.

## 16. Pagamentos — [CONFIRMADO]

- Stripe e PayPal não aceitam negócios de conteúdo adulto gerado por IA ([guia](https://signaturepayments.com/does-stripe-allow-adult-content/), [guia](https://mobiuspay.com/blog/will-stripe-and-paypal-accept-nsfw-ai)).
- Processadores especializados citados: CCBill, Segpay, Paxum; Verotel e Epoch por conhecimento prévio. Opções com Pix e adquirentes brasileiros: não pesquisado.
- **Regras da Mastercard para conteúdo adulto** (AN 5196, de 2021): verificação documentada de idade e identidade de todas as pessoas retratadas e de quem envia conteúdo; consentimento documentado; controle e revisão de conteúdo; processo de denúncia e remoção; o banco adquirente certifica esses controles ([Mastercard](https://www.mastercard.com/global/en/news-and-trends/perspectives/2021/protecting-our-network-protecting-you-preventing-illegal-adult-content-on-our-network.html), [CCBill](https://ccbill.com/blog/new-mastercard-rules)). Fontes do mercado dizem que as regras de 2026 abrangem explicitamente conteúdo **gerado ou sintético** e exigem consentimento quando a semelhança de uma pessoa real é usada ([guia](https://mobiuspay.com/blog/mastercard-adult-content-rules)); confirmar com o adquirente.

## 17. Legislação aplicável — [PARCIAL]

> Levantamento técnico para orientar o desenho, **não é parecer jurídico**. Contratar advogado(a) de direito digital antes de abrir ao público.

**Brasil**

- **ECA Digital (Lei 15.211/2025), em vigor desde 17/03/2026.** Para conteúdo pornográfico, exige verificação de idade confiável e auditável e **veda a autodeclaração** ("clique se tem mais de 18" não vale mais). A ANPD é a reguladora (Decreto 12.622/2025), publicou orientações preliminares em 20/03/2026, e a regulamentação dos mecanismos aceitos está em andamento. Fornecedores devem **remover e comunicar** às autoridades nacionais e internacionais conteúdo aparente de exploração ou abuso sexual infantil, sequestro e aliciamento, e **reter os dados** associados; no Brasil a autoridade é a Polícia Federal, com um Centro Nacional de Triagem de Notificações ([ANPD](https://www.gov.br/anpd/pt-br/assuntos/eca-digital), [MJSP](https://www.gov.br/mj/pt-br/assuntos/noticias/sancionado-projeto-que-fortalece-a-anpd-e-fixa-inicio-da-vigencia-do-eca-digital), [Mattos Filho](https://www.mattosfilho.com.br/unico/eca-digital-sancionada-lei/)).
- **Código Penal, art. 216-B, parágrafo único (Lei 13.772/2018):** é crime *"realiza[r] montagem em fotografia, vídeo, áudio ou qualquer outro registro com o fim de incluir pessoa em cena de nudez ou ato sexual ou libidinoso de caráter íntimo"* ([Lei 13.772](https://planalto.gov.br/ccivil_03/_Ato2015-2018/2018/Lei/L13772.htm)). É a base direta para não aceitar fotos de terceiros no I2V adulto.
- **Lei 15.123/2025:** aumenta em metade a pena da violência psicológica contra a mulher (art. 147-B do CP) quando praticada com IA ou recurso que altere imagem ou som da vítima ([Lei 15.123](http://www.planalto.gov.br/ccivil_03/_ato2023-2026/2025/lei/l15123.htm)).
- Por conhecimento prévio, não reverificados nesta sessão: ECA arts. 240 a 241-E (o 241-C trata de simulação por montagem); Marco Civil da Internet, art. 15 (guarda de registros de acesso por 6 meses) e art. 21 (remoção após notificação de conteúdo íntimo não consentido); LGPD (dados sobre vida sexual e biométricos são sensíveis). A seção 11 da especificação também cita a Lei 14.811/2024; não reverifiquei o alcance dela para este caso.

**Estados Unidos** (relevante se houver usuários lá)

- **TAKE IT DOWN Act**, conformidade exigida desde **19/05/2026**: plataformas cobertas devem remover imagens íntimas não consentidas, **inclusive falsificações geradas por IA**, em até **48 h** após pedido válido, publicar um aviso claro do processo e fazer esforço razoável para remover cópias idênticas. Fiscalização da FTC; multa civil de até US$ 53.088 por violação ([WilmerHale](https://wilmerhale.com/en/insights/client-alerts/20260615-the-take-it-down-act-goes-live), [Reed Smith](https://www.reedsmith.com/our-insights/blogs/viewpoints/102ms5e/covered-platforms-are-you-ready-to-take-it-down/)).

**União Europeia** (relevante se houver usuários lá)

- **AI Act, art. 50**, aplicável desde **02/08/2026**: deepfakes devem ser identificados como tal; saídas de IA generativa devem ter marcação legível por máquina, com transição até **02/12/2026** para sistemas já no mercado; multas de até € 15 milhões ou 3% do faturamento ([Baker McKenzie](https://connectontech.bakermckenzie.com/new-eu-guidance-on-ai-transparency-what-should-companies-be-doing-from-2-august-2026)).

## 18. Ferramentas de moderação e CSAM — [PARCIAL]

- **Thorn Safer / Safer Predict:** classificador de CSAM inédito em **imagem e vídeo**, inclusive entradas e saídas de sistemas de IA, além de bancos de hashes (SaferHash para imagens, SSVH para vídeo) ([Thorn](https://www.thorn.org/blog/introducing-safer-predict-using-the-power-of-ai-to-detect-child-sexual-abuse-and-exploitation-online)).
- **Hive:** API de detecção de CSAM (hash + classificador da Thorn) e classificação demográfica com as classes `teenager` (13–17) e `pre_teen` (5–12) ([CSAM API](https://docs.thehive.ai/docs/csam-detection-api), [demografia](https://docs.thehive.ai/docs/demographic-attributes)).
- Por conhecimento prévio, a confirmar elegibilidade e termos para clientes do setor adulto: Microsoft PhotoDNA, Google Content Safety API, AWS Rekognition (faixa de idade estimada, reconhecimento de celebridades, comparação de rostos), Yoti (estimativa facial de idade).
- Preços e aceitação de clientes do setor adulto: **não confirmados**.

## 19. Valores do workflow anexado — divergências

Análise do arquivo `referencia/wan-i2v-com-ollama.api.json`: 32 nós, grafo acíclico, todos alcançáveis a partir do `SaveVideo`; turbo efetivo de **4 passos, CFG 1, troca de expert no passo 2**; high e low apontando para os arquivos R1 v2 corretos.

| Parâmetro | No export | Na especificação | Ação no template |
|---|---|---|---|
| Duração (`129:161`) | **10 s → 161 frames** | 5 s (81 frames), máx. ~7 s | Limitar a 3–7 s inteiros |
| Resolução (`129:98`) | **360 × 360** | 640 × 640, 832 × 480, 720p | Definir por tier |
| Imagem (`97`) | vazia | — | Preenchida por job |
| Pedido do Ollama (`165`) | vazio | — | Nós `165`/`166` removidos; prompt compilado vira texto literal em `129:93` |
| Seed (`129:86`) | fixa (`559594226693707`) | aleatória | Sorteada por job e registrada |
| Prompt negativo (`129:89`) | vazio | sem efeito com CFG 1 | Manter vazio |
