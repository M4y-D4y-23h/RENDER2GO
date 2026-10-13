# ADR 0014: modelos oficiais do Wan 2.2, tradutor comum e legenda por foto

- **Status:** proposto (13/10/2026). O dono aceita de antemão **se** a rodada em GPU cumprir os critérios abaixo, escritos antes da medição (pedido de 13/10/2026). Se algum critério falhar, a produção continua com a configuração de hoje e este ADR fica proposto, com os números.
- **Já medido sem GPU (13/10/2026):** o tradutor comum e o prompt v8 **não cumpriram o critério 6** (seção "Medições sem GPU"): o tradutor continua o abliterado com o v6, e a legenda não entra no prompt do tradutor. A combinação D da rodada em GPU foi redefinida antes da rodada (seção "Critérios").
- **Contexto:** `docs/06`, seção 9.1: vídeos fugindo do contexto da foto, pessoas e objetos que não estavam lá, vídeos barrados pelo filtro de imagem. Duas causas são de modelo, não de código: (1) o Wan em produção é um ajuste da comunidade marcado "Uncensored" (`Wan2.2_I2V_{High,Low}_R1_n54w_v2`) com o codificador de texto `nsfw_wan_umt5_xxl_v2` (767 palavras de vocabulário sexual a mais; `docs/12`, seção 1); (2) o tradutor é um Qwen "abliterado" (sem recusas) que vê só o texto, nunca a foto, e escreve o mesmo prompt para todas as fotos do pedido. A Fase 1 de 13/10 (sem GPU e sem ADR) já tirou do tradutor as linhas de conteúdo adulto (v7), decidiu o pedido vago no código e barra a pessoa inventada (`render2go_worker/traducao.py`); o que falta precisa de GPU para medir.

## Decisão proposta

1. **Modelos oficiais (combinação B da rodada):** os arquivos do Wan 2.2 I2V A14B da Comfy-Org em fp8 com escala (`wan2.2_i2v_{high,low}_noise_14B_fp8_scaled`), o codificador `umt5_xxl_fp8_e4m3fn_scaled` original (Comfy-Org/Wan_2.1_ComfyUI_repackaged) e o LoRA lightx2v de 4 passos v1 (`wan2.2_i2v_lightx2v_4steps_lora_v1_{high,low}_noise`, o mesmo SHA-256 do lightx2v/Wan2.2-Lightning, Seko-V1), força 1,0 em cada expert.
   - **Licenças conferidas em 13/10/2026:** Apache 2.0 nos cartões da Comfy-Org (as duas revisões), do Wan-AI/Wan2.2-I2V-A14B e do lightx2v/Wan2.2-Lightning.
   - **Lock:** conjunto novo `i2v_oficial` no `models.lock.json` (o `i2v_r1v2` fica), com revisão, bytes e SHA-256 (OID do LFS) de cada arquivo, e os cinco arquivos novos conferidos baixando cada um inteiro e refazendo bytes e SHA-256 (13/10/2026).
   - **Template candidato** `worker/workflows/experimental/i2v_oficial_lightx2v.v1` (gerado por `tools/build_i2v_templates.py`, com `--check`): o mesmo grafo do de produção, no mesmo ComfyUI v0.38.0, com 4 passos, CFG 1, troca de expert no passo 2, shift 5 e euler/simple (os valores do workflow de referência do lightx2v para o I2V), e o `LoraLoaderModelOnly` entre o `UNETLoader` e a chave do turbo. Só vai para `worker/workflows/` com este ADR aceito (`PRODUCTION` no gerador).
   - **Muda uma regra técnica do CLAUDE.md** ("Wan2.2 I2V R1 v2: sem LoRA Lightning"): com o aceite, a regra passa a ser "Wan 2.2 I2V A14B oficial em fp8 com escala + LoRA lightx2v de 4 passos v1 (força 1,0); 4 passos, CFG 1, troca de expert no passo 2".
   - O remendo `ComfyUI-UMT5-Extended` (do NSFW-UMT5) só age quando o vocabulário do codificador é diferente do padrão (256.384); com o codificador oficial, não faz nada. Sai da imagem quando o conjunto antigo sair.
2. **Tradutor comum:** `qwen2.5:7b-instruct` no Ollama (Apache 2.0, conferido no cartão do Qwen/Qwen2.5-7B-Instruct e no arquivo de licença do próprio registro do Ollama), a mesma quantização do de hoje (Q4_K_M), fixado pelo digest da camada de pesos `sha256:2bada8a7450677000f678be90653b85d364de7db25eb5ea54136ada5f3933730` (4.683.073.952 bytes), como o atual (`worker/Dockerfile`, `pull_compiler_model.sh`). Ele também não é camada de segurança: a regra `REFUSED` e o C1 continuam, e a frase do CLAUDE.md sobre o "modelo abliterado" muda quando ele entrar.
   - **Medido em 13/10/2026: não entra** (critério 6; seção "Medições sem GPU"). Para medir de novo: `--build-arg COMPILER_MODEL=qwen2.5:7b-instruct --build-arg COMPILER_MODEL_DIGEST=<o digest acima>` e a rodada de fidelidade com `--model`.
3. **Legenda e prompt por foto (combinação D):**
   - **Modelo:** `qwen2.5vl:7b` no Ollama do pod (Qwen2.5-VL-7B-Instruct, Apache 2.0; o 3B não serve, licença não comercial, `docs/12`), fixado pelo digest `sha256:a99b7f834d754b88f122d865f32758ba9f0994a83f8363df2c1e71c17605a025` (5.969.233.408 bytes; no Ollama, visão e texto vêm no mesmo arquivo).
   - **Legenda:** cada foto ganha uma descrição curta e objetiva (o que é, material, cor, cenário, se tem pessoa, se tem rótulo ou texto), uma categoria (as das receitas v4: comida, produto, imóvel, roupa, carro, pessoa, geral) e o assunto principal em inglês, numa resposta JSON com esquema fixo (`format` do Ollama), conferida pelo código (`render2go_worker/legenda.py`, prompt versionado `worker/prompts/legenda.v1.txt`). Falhou ou veio fora do esquema: a foto segue sem legenda (a categoria do estilo, como hoje); nada bloqueia o pedido por isso.
   - **Tradução:** o tradutor recebe o texto do cliente, a legenda e a categoria e escreve o prompt daquela foto (`compiler-i2v.v8.txt`, rodada de fidelidade com as legendas escritas nos casos, sem rodar o modelo de visão). O pedido vago vira o movimento padrão da categoria da legenda, com o assunto da legenda.
     - **Medido em 13/10/2026: o v8 não entra** (critério 6): pior que o v7 nos mesmos casos, perdeu aspas e copiou o texto de um rótulo para o prompt. A legenda fica só no código: a categoria e o assunto do movimento padrão (pedido vago) e a regra da pessoa inventada, com qualquer versão do prompt (o v6 de produção inclusive); o texto do tradutor não a vê.
   - **Pessoa inventada:** a regra fica mais rígida, nunca mais frouxa: vale se a análise do servidor diz que a foto não tem pessoas **ou** se a legenda diz que não tem.
   - **Banco e fila:** o caminho do prompt por foto que já existe (pedido em sequência): com `RENDER2GO_LEGENDA_POR_FOTO=1`, cada foto do pedido aprovado ganha a sua tradução (`compilar` com a foto), o vídeo dela espera o prompt dela, e a legenda e a categoria ficam na foto (`fotos.legenda`, `fotos.categoria`), no painel (histórico, "vídeos que não saíram") e na auditoria, nunca para o cliente. O C1 continua conferindo cada prompt na API. Padrão: desligado (0); os pedidos aprovados antes continuam como foram.
   - **VRAM:** a legenda roda num segundo Ollama, só em CPU (`CUDA_VISIBLE_DEVICES` vazio, `127.0.0.1:11435`), em paralelo com a geração: zero VRAM por construção, então nunca falta memória em 720p (onde o Wan chega a ~44 GB dos 48 da L40S e o tradutor já divide o resto). Medido nesta máquina (4 vCPU, a pior das CPUs): ver "Medições sem GPU". Na GPU, a rodada mede o tempo na CPU do pod, a espera do 1º vídeo (limite: ~30 s a mais) e o pico de VRAM.
   - **CPU do pod:** a legenda usa metade das CPUs **do contêiner** (no mínimo 2; `legenda.threads_da_legenda`), pela cota do cgroup (`cpu.max`) e pela afinidade do processo, nunca por `os.cpu_count()`: no pod L40S de 12/10, ele deu 128 (as CPUs do host), e o Ollama abriria dezenas de threads numa cota bem menor, disputando a CPU com o ComfyUI e a conferência. A outra metade fica com eles.
   - **Download no pod:** o modelo de visão (~6 GB, do registro do Ollama, conferido pelo digest) baixa em segundo plano depois do estado AMBIENTE, junto com os modelos do vídeo, num Ollama só para o download (porta 11436): nada espera por ele (sem ele, as fotos seguem sem legenda), e o volume (cache e hibernação) só é gravado depois dele. A versão da hibernação muda com a chave (`lote_nuvem.versao_hibernacao`): o volume com o modelo de visão é outro.
   - **Erros:** qualquer erro na legenda (rede, Ollama de CPU fora do ar, foto ilegível, resposta fora do esquema ou um erro inesperado) deixa a foto sem legenda e nunca derruba o laço da GPU (`fila.WorkerFila._legendar`).
   - **Privacidade:** a política ganha a versão 6, dizendo que a descrição automática de cada foto fica guardada com o pedido (para a revisão e a auditoria) e sai com a limpeza do histórico.
4. **NAG (combinação C): fica de fora**, porque exigiria outra versão do ComfyUI:
   - o ComfyUI v0.38.0 tem o nó nativo `NAGuidance` (`comfy_extras/nodes_nag.py`), mas ele age pelo gancho `attn1_output_patch`, que só os modelos de SD, Flux e Krea chamam; a atenção do Wan nessa versão (`comfy/ldm/wan/model.py`) só expõe `attn1_patch` e `attn2_patch`, e o `attn2_patch` recebe o resultado já somado da atenção cruzada, sem as duas saídas (prompt e negativo) que o NAG precisa. O nó ainda desliga o atalho do CFG 1 (`disable_model_cfg1_optimization`): no Wan, o passe negativo seria calculado em todo passo, dobrando o custo, sem efeito nenhum no vídeo;
   - o nó da comunidade ChenDarYen/ComfyUI-NAG (licença MIT, último commit `ef8a641be08983cf5f06669f70719b6eecce3c7f`, de 03/11/2025) suporta o Wan trocando o `WanModel.forward` e a atenção cruzada por cópias escritas para o ComfyUI de novembro de 2025: na v0.38.0 (29/09/2026), o Wan calcula o RoPE por `rope_encode`, passa `transformer_options` a cada bloco e usa `AttentionTensorContainer`; a cópia não faz nada disso. Usá-lo seria rodar outro Wan, de outra versão do ComfyUI, ou manter um remendo nosso no código do modelo;
   - sem NAG, a combinação D da rodada é B + legenda e prompt por foto.

## Critérios de aceite (escritos antes da medição; valem para B e D contra A)

A rodada (`worker/evals/gpu-2026-10-13-qualidade/`) usa as mesmas fotos e as mesmas seeds em todas as combinações, vídeos de 5 s, um pedido concreto e um vago por foto. A = a produção depois da Fase 1 (R1 v2, codificador NSFW-UMT5, tradutor abliterado com o prompt v6, que a Fase 1 manteve, e as regras de `traducao.py`). B = modelos oficiais + LoRA, com os mesmos prompts de A (só o modelo de vídeo muda). D = B + tradutor comum + legenda e prompt por foto (v8).

**D redefinida antes da rodada em GPU (13/10/2026, depois da medição do critério 6 e antes de qualquer vídeo):** o tradutor comum e o v8 já ficaram de fora pelo critério 6, então a D escrita acima não poderia ser aceita, e a rodada não a gera. **D = B + legenda por foto só no código, com o tradutor de produção** (abliterado, v6): cada foto ganha a legenda no Ollama de CPU do pod, e a tradução daquela foto usa a categoria e o assunto da legenda no movimento padrão (pedido vago) e a regra da pessoa inventada com a legenda; o v6 não vê a legenda. Num pedido concreto, o texto do tradutor é o mesmo de B: a rodada gera em D só os vídeos cujo prompt muda (os pedidos vagos e uma pessoa barrada pela legenda) e, nos outros, conta o vídeo de B (o mesmo prompt, a mesma seed e o mesmo modelo). Os critérios 1 a 5 valem para essa D, como estão.

1. **Vídeos aproveitáveis** (produto reconhecível, mesma cor e forma, sem pessoa ou objeto novo, movimento coerente com o pedido; avaliados nas folhas lado a lado pela lista escrita no LEIAME da rodada antes de ver os vídeos): B e D ≥ A, nas mesmas fotos e seeds.
2. **Reprovações na conferência automática** (1º quadro e continuidade, `conferencia.py`): B e D ≤ A.
3. **Filtro de imagem:** nenhum vídeo barrado nas fotos SFW, e a média das notas (janela de 2 quadros) de B e D ≤ a de A.
4. **Pessoa inventada:** zero vídeo com pessoa nas fotos sem pessoa (pelo prompt e pelas folhas).
5. **Tempo por vídeo** (o do ComfyUI, mesmo pod, GPU quente): B e D ≤ A + 10%, em 480p e em 720p, e nenhuma falta de memória em 720p com o Ollama no pod (o tradutor na GPU e, em D, a legenda no Ollama de CPU); a espera do 1º vídeo de um pedido com legenda (pod quente) ≤ ~30 s a mais que sem.
6. **Tradutor novo:** na rodada de fidelidade (`cases.v3.json`, CPU), o Qwen 2.5 7B comum com o prompt v7 ≥ o abliterado com o v6 na taxa de casos certos e em "sem invenção", 100% das aspas, e cada caso REFUSED recusado pelo menos tantas vezes quanto o v6 recusa; e o v8 (com as legendas) ≥ o v7 nos mesmos casos.

Cada parte entra só com os critérios dela cumpridos: os modelos oficiais (1–5 em B), o tradutor comum (6, e 1–5 em D), a legenda por foto (1–5 em D). O que falhar fica fora, com o motivo e os números aqui.

## Medições sem GPU (13/10/2026)

**Critério 6 (fidelidade em CPU; tabela e relatórios em `worker/evals/compiler/RESULTADOS.md`, "Rodada de 13/10/2026 (2)"):**

| Tradutor e prompt (casos da v3) | Casos certos | Sem invenção | Aspas | REFUSED |
|---|---|---|---|---|
| abliterado, v6 (produção) | **69,5%** | **96,5%** | 100% | 0 de 9 |
| comum, v7 | 56,7% | 94,3% | 100% | **6 de 9** |
| comum, v8 (com as legendas) | 54,6% | 95,0% | 94,4% | 6 de 9 |

Nos 13 casos com legenda, o v8 acertou 76,9% contra 92,3% do v7. **O critério 6 não foi cumprido nas duas partes**: o tradutor comum perde 13 pontos de casos certos (termos de câmera, respostas curtas, plural com verbo) e 2,2 de "sem invenção", embora recuse mais (6 de 9 × 0 de 9); o v8 é pior que o v7 e, numa seed, copiou para o prompt o texto escrito num rótulo. As recusas a mais não pesam a favor: o tradutor nunca é camada de segurança (o C1, o filtro das fotos e a revisão humana é que barram).

**Legenda em CPU** (`qwen2.5vl:7b`, Ollama 0.35.1, 4 vCPU Intel Xeon a 2,1 GHz, 4 threads; foto preparada a 832×480 e reduzida a 512 px; `worker/evals/legenda/`):

| Foto (`fixtures/sfw`) | Tempo | Legenda |
|---|---|---|
| xícara (1ª, com a carga do modelo de um disco lento) | 503 s | produto, "the ceramic cup and saucer", sem pessoa, sem texto |
| café | 77 s | comida, "the cup of coffee", sem pessoa |
| padaria (foto antiga, com pessoas) | 79 s | geral, "the men in the tent", com pessoas |

- As três legendas são certas e passam no `legenda.conferir`. O tempo é alto porque o Ollama 0.35.1 amplia toda imagem do Qwen2.5-VL para no mínimo 802.816 pixels (`image_min_pixels`, ~1.000 tokens de imagem), então reduzir a foto abaixo disso não ajuda.
- **No pod**, com metade das CPUs da cota (8 de 16, a estimar), a legenda deve ficar perto da geração de um vídeo 480p de 5 s na L40S (~51 s): cabe durante o vídeo anterior, menos a do 1º vídeo de um pedido, que espera a legenda dele. A rodada em GPU mede as duas coisas (critério 5: no máximo ~30 s a mais no 1º vídeo).

## Para pôr em produção (com o aceite)

- `tools/build_i2v_templates.py`: o candidato em `PRODUCTION`; `lote.I2V_TEMPLATE` e o conjunto do bootstrap (`lote_nuvem.Remoto.instalar`) passam a `i2v_oficial`. A versão da hibernação (`lote_nuvem.versao_hibernacao`, que inclui o `models.lock.json` e o `worker/Dockerfile`) muda já com o conjunto novo no lock: na 1ª atualização do servidor depois disso, os pods hibernados são encerrados no ciclo seguinte do orquestrador e o conjunto se refaz com pods novos (ADR 0011, decisão 6), com o download inteiro na 1ª subida de cada um.
- `worker/Dockerfile`: `COMPILER_MODEL` e `COMPILER_MODEL_DIGEST` do tradutor comum; `compiler.DEFAULT_MODEL` e o perfil `i2v` com a versão do prompt aceita.
- Espelho R2 dos cinco arquivos novos (`worker/scripts/espelhar.py`) antes da 1ª subida.
- `RENDER2GO_LEGENDA_POR_FOTO=1` no `.env` e a política de privacidade v6 publicada.
- CLAUDE.md (regras técnicas e a frase do modelo abliterado), `docs/06`, `docs/07`, `docs/12` e a entrada do `docs/09` com o passo a passo.

## Consequências

- **Download:** o conjunto novo tem 38,2 GB (o atual, 36,8 GB); com a legenda, mais ~6 GB do modelo de visão, vindos do registro do Ollama.
- **Tempo:** o LoRA soma uma passada de pesos na carga (os pesos do LoRA são aplicados ao modelo) e nada por passo depois de carregado; a legenda corre na CPU, fora da GPU.
- **Pedido com legenda:** uma tradução por foto (1–3 s de GPU cada) em vez de uma por pedido; o tempo de CPU de cada legenda corre durante a geração do vídeo anterior.
- **Sem NAG**, o prompt negativo continua sem efeito com CFG 1, como hoje.

## Alternativas consideradas

- **NAG com outro ComfyUI:** fora da regra "ComfyUI fixado em v0.38.0; template e versão mudam juntos" sem uma medição só disso. Quando houver uma versão do ComfyUI com NAG para o Wan, ela entra com um ADR próprio.
- **Legenda na GPU antes de carregar o Wan:** zero disputa no pod novo (os modelos ainda baixam), mas num pod quente o Wan já está na VRAM e a legenda teria de esperar uma janela entre vídeos ou descarregar o Wan; a CPU nunca disputa a VRAM.
- **Legenda no servidor:** o modelo de visão de 7B precisa de ~6 GB de memória; o servidor tem 4 GB.
- **Qwen2.5-VL-3B:** licença só para fins não comerciais (`docs/12`, seção 2.5).
