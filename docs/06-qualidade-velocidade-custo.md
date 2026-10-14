# 06 — Qualidade, velocidade e custo em lotes de 50+

- **Data:** 06/10/2026
- **Pedido:** avaliar os pontos levantados pelo dono do projeto para lotes de 50+ fotos: GPU mais potente com paralelismo, upscale e limpeza, upsampling de prompt, motion buckets e flow match, bf16 e INT8, cache em volume, escolha de GPU.
- **Base:** medições de 05/10 e 06/10 (`worker/evals/gpu-2026-*`). Os números marcados *estimativa* ainda não foram medidos.
- Arquitetura para escala: [ADR 0003](adr/0003-arquitetura-em-escala.md).

## 1. Resumo

| Ponto | Vale para o lote? | Situação |
|---|---|---|
| GPU mais potente | Sim, se o custo por vídeo cair | GPUs de 48 GB ainda sem medição (seção 2) |
| Vários vídeos ao mesmo tempo **na mesma GPU** | **Não** | Medido duas vezes: fila 2 num ComfyUI (4090, ~80 s) e 2 ComfyUI na mesma RTX PRO 6000 (~100 s de GPU por vídeo, contra 93 s um de cada vez) |
| Paralelismo com **uma GPU por vídeo** | **Sim**, é o que acelera | Feito: `COMFY_GPUS=auto` e `lote --gpus N` |
| Limpeza e ajuste da foto antes do render | Sim | Feito: `preparo.py` (EXIF, corte, Lanczos) |
| Upscale da foto de entrada com IA | Raramente | Foto de celular já é maior que o vídeo; só foto pequena se beneficia |
| Upscale e interpolação do vídeo de saída | **Sim**, com bom custo | Feito na CPU: `acabamento --altura --interpolar`. Upscale com IA fica para medir |
| Upsampling do prompt com LLM local | Já existe | O compilador (Qwen 7B) faz isso. Próximo passo: descrever cada foto com um modelo de visão |
| Motion buckets | Não se aplica ao Wan | É conceito do Stable Video Diffusion |
| Flow match | O Wan 2.2 já é flow matching | Movimento garantido: presets, `acabamento --zoom` e, depois, modelo de câmera (seção 5) |
| BF16 puro | **Não** | Dobra a VRAM sem ganho visível. Hoje: pesos fp8 com cálculo em bf16 |
| INT8 / GGUF | Só em GPU sem fp8 | Ampere (A40) |
| Aceleração `--fast` fp8 | **Não** | Medido na L40S: ~55 s com e sem |
| SageAttention | Talvez | Medido em 12/10: −15% em 480p e −26% em 720p, mas um vídeo a mais reprovou na conferência; teste maior antes de um ADR (seção 9.2) |
| Cache em volume de rede | Hoje, não | Download do Hugging Face: ~2 min e grátis. Melhor: imagem com os modelos (ADR 0002) |

## 2. GPU mais potente e paralelismo

**Por que 4 ou 8 vídeos ao mesmo tempo na mesma GPU não aceleram.** Um vídeo do Wan já ocupa a GPU inteira durante o render: a GPU fica limitada pelo cálculo, não pela espera. Se rodar dois ao mesmo tempo, cada um leva o dobro. A rodada de 06/10 confirmou: fila 1 e fila 2 deram ~80 s por vídeo. Além disso, cada modelo de 14B ocupa ~15 GB. Numa GPU de 24 GB não cabe um segundo vídeo; numa de 48 GB caberiam os dois "experts" de um vídeo, não dois vídeos.

**O que acelera de verdade:**

1. **Uma GPU por vídeo.**
   - Um pod com 2, 4 ou 8 GPUs, com um ComfyUI por GPU (`COMFY_GPUS=auto` no `entrypoint.sh`) e o lote distribuindo os vídeos (`lote --gpus N`). Ou vários pods (`lote --comfy URL1 --comfy URL2 ...`).
   - O custo por vídeo não muda e o tempo do lote cai na proporção.
   - **Cuidado com a RAM:** cada ComfyUI guarda ~37 GB de modelos em RAM. Num pod de 4 GPUs, são ~150 GB, então é preciso conferir a RAM do pod antes.
2. **GPU de 48 GB** (L40S, RTX 6000 Ada, A40).
   - Os dois experts do I2V cabem juntos, o que elimina a troca de modelos a cada vídeo. Na 4090 foram ~11 s por fase (`gpu-2026-10-05`).
3. **GPU mais rápida** (H100): menos tempo por vídeo, mas preço por hora bem maior.

Para custo baixo, o que importa é o **custo por vídeo** (preço por hora × tempo por vídeo). Para entregar rápido ao cliente, o que importa é **quantas GPUs trabalham em paralelo**.

| GPU (RunPod, 06/10) | VRAM | fp8 nativo | US$/h | Por vídeo de 5 s (rápida) | Custo por vídeo |
|---|---|---|---|---|---|
| RTX 4090 secure | 24 GB | sim | 0,74 | **~80 s (medido)** | **~US$ 0,016** |
| RTX 4090 community | 24 GB | sim | 0,34 | ~80 s | **Fora de uso** (07/10): 3 de 3 hosts com CUDA quebrado |
| RTX 5090 | 32 GB | sim | 0,69 a 0,99 | *~50–60 s (estimativa)* | *~US$ 0,010–0,016* |
| **RTX PRO 6000** (Blackwell) | 96 GB | sim | 2,09 | **~28 s (medido, 07/10)**; 720p ~93 s; 720p 7 s ~160 s | **~US$ 0,017**: mesmo custo da L40S, metade do tempo (`worker/evals/gpu-2026-10-07-rtx-pro-6000/`) |
| **L40S** | 48 GB | sim | 0,79 a 1,09 | **~55 s (medido)**; alta ~180 s | **~US$ 0,012–0,017** |
| A40 | 48 GB | **não** (Ampere) | 0,49 | *~120–160 s (estimativa: metade do cálculo de uma 4090)* | *~US$ 0,016–0,022* |
| H100 SXM | 80 GB | sim | 3,49 | *~25–35 s (estimativa)* | *~US$ 0,024–0,034* |

- **A40 não serve para "velocidade extrema".** É uma geração anterior (Ampere), sem fp8 nativo. Deve ser mais lenta por vídeo que a 4090. Pode compensar pelo preço, mas só medindo.
- **A L40S foi medida** (`worker/evals/gpu-2026-10-06-l40s/`): ~55 s por vídeo, 30% mais rápida que a 4090 secure de hoje, com custo por vídeo igual ou menor. É a **recomendação para lotes**, sempre na nuvem **secure** (decisão de 07/10/2026). Ordem padrão do `lote_nuvem` desde 08/10/2026 (a RTX PRO 6000 subiu para US$ 2,49/h; `docs/07`, seção 1): **L40S → RTX 6000 Ada → RTX PRO 6000 → RTX 4090**. A RTX 6000 Ada (US$ 0,99/h) usa o mesmo chip da L40S, com 48 GB e o mesmo cálculo de IA no papel, e ainda não foi medida. A L40 saiu da lista: tem metade do cálculo de IA da L40S. **Nenhum pod fica ligado mais de 2 h.**

## 3. Upscale e limpeza da imagem antes do render

- **Feito: `preparo.py`, ligado por padrão no lote.**
  - Aplica a rotação do EXIF, converte para RGB e põe fundo branco onde houver transparência.
  - Corta no centro para a proporção e redimensiona com Lanczos para o tamanho exato do render.
  - O ComfyUI faria esse ajuste com redimensionamento bilinear, que serrilha ao reduzir foto de celular de 4000 px.
  - Bônus: o envio cai de MB para KB, e os metadados (EXIF e GPS) da foto do cliente não vão para a GPU.
- **Upscale da entrada com IA:** só ajuda quando a foto é menor que o vídeo. O lote avisa ("foto pequena, ampliada 2,1x"). Para esses casos, um ESRGAN no pod é a próxima opção, mas precisa de modelo, de licença conferida e de medição.
- **Limpeza com IA** (ruído, compressão): sem caso real ainda. As fotos de produto e imóvel costumam ser boas.

## 4. Upsampling de prompt

O compilador já faz isso: ele transforma "a câmera se aproxima devagar do produto" em um parágrafo em inglês de 15 a 60 palavras. O lote compila **uma vez** por pedido.

- **Corrigido nesta rodada:** o compilador escrevia "Static camera." mesmo com preset de movimento. Com preset de câmera, essas frases agora saem pelo código (`compiler.drop_static_camera`).
- **Próximo passo de qualidade:** um modelo de visão pequeno (ex.: Qwen2.5-VL 3B) descreve **cada foto**, e o prompt cita o que existe nela ("vapor sobe da caneca branca").
  - Custo estimado: 1 a 3 s por foto, e a troca de modelo na VRAM.
  - Exige novo prompt do compilador, rodada de fidelidade e ADR.
  - Vale testar quando houver fotos reais de clientes.

## 5. Motion buckets e flow match

- **Motion bucket** é um parâmetro do Stable Video Diffusion (quanto de movimento gerar). O Wan 2.2 não tem esse controle.
- **Flow matching** é o tipo de modelo que o Wan 2.2 já é, e o `shift` do sampler já está no template.

Como garantir "slow pan" e "cinematic zoom":

1. **Presets de câmera** (já existem) e o conserto do "Static camera".
2. **`acabamento --zoom`:** zoom lento no centro, garantido e sem GPU. Resolveu a fachada parada da rodada de 06/10 (`saida/videos-2026-10-06/fachada-acabada.mp4`).
3. **Depois:** modelo de controle de câmera do Wan 2.2 (família "Fun Camera", com trajetória explícita). Movimento de câmera exato, mas com modelo novo, template novo, VRAM e licença a conferir. Pede ADR.

## 6. BF16, INT8 e cache em volume

### BF16 e INT8

- **Hoje:** pesos em fp8 (e4m3fn com escala), com o cálculo feito em bf16. É o que o pedido descreve como "gerar em BF16", e é o padrão dos modelos oficiais do ComfyUI.
- **BF16 puro nos pesos:**
  - dobra a VRAM: ~29 GB por expert, ~57 GB os dois, o que exige GPU de 80 GB;
  - deixa o render mais lento por mover o dobro de dados;
  - a diferença de qualidade para fp8 com escala é pequena.

  Não recomendo.
- **INT8 / GGUF Q8:** útil em GPU sem fp8 (A40) ou com pouca VRAM. Na 4090 e na L40S, o fp8 é melhor.
- **Onde há ganho real de velocidade (a medir):**
  - `--fast fp8_matrix_mult` do ComfyUI (multiplicação em fp8 nas GPUs Ada, Hopper e Blackwell);
  - SageAttention (atenção quantizada; pacote novo, precisa de versão fixada).

  São candidatos ao teste em GPU. Se ganharem, entram como opção do `COMFY_EXTRA_ARGS`.

### Cache em volume de rede

- **Custo:** US$ 0,07/GB/mês, ~US$ 5 por mês para os ~70 GB de modelos.
- **Desvantagens:**
  - prende os pods a um datacenter, justo quando a disponibilidade de GPU mais pesa;
  - a regra do projeto proíbe ler modelos direto do volume: é preciso copiar e conferir, o que leva ~3 min;
  - o download do Hugging Face já leva ~2 min e é grátis.
- **Melhor caminho:** a imagem com os modelos embutidos (ADR 0002), que o RunPod mantém em cache no host. O volume só passa a valer com pods fixos num datacenter.

## 7. Teste em GPU de 48 GB (feito em 06/10) e o que falta

- **Feito** numa L40S avulsa: ~55 s por vídeo na rápida, ~180 s na alta e nenhum ganho com `--fast fp8_matrix_mult` (`worker/evals/gpu-2026-10-06-l40s/`). Custo: ~US$ 1,10.
- **Falta:** 2 vídeos ao mesmo tempo em 2 GPUs. O pod 2× L40S não subiu, e o código está pronto (`COMFY_GPUS=auto`, `lote --gpus 2`).

## 8. O fluxo desejado, passo a passo

| Passo | Situação |
|---|---|
| 1. O cliente envia uma pasta | Falta a tela e o armazenamento (ADR 0003). Hoje a pasta é local |
| 2. O script sobe o pod, executa, para e encerra | Partes prontas (`runpod_pod.py`, desligamento automático, teste de CUDA). Falta juntar num comando (ADR 0003, fase 2) |
| 3. Preparo → prompt → render | **Pronto:** `preparo` → compilador uma vez → render fp8 com cálculo em bf16 → folha de conferência |
| 3b. Acabamento (32 fps, 720p/1080p, zoom) | Pronto na CPU (`acabamento.py`); falta rodar no lote |
| 4. O vídeo vai para a pasta do cliente | Falta o armazenamento (ADR 0003) |

## 9. Diagnóstico e avaliações de 12/10/2026 (pedido do dono)

Depois dos testes do dono no site (pedido "deixe bonito, com um movimento suave", estilo "Livre"): vídeos fugindo do contexto, elementos sem cabimento, movimento repetitivo, objetos parados se mexendo e itens mudando; 4 de 10 barrados pelo filtro de imagem. Análise do código e dos registros, sem GPU; os números de velocidade marcados como estimativa não foram medidos.

### 9.1 Por que os vídeos fogem do contexto (o mais provável primeiro)

1. **Pedido vago vira um prompt sem assunto, o mesmo para todas as fotos.** O tradutor só vê o texto, não a foto (`worker/src/render2go_worker/compiler.py`), roda uma vez por pedido, e cada vídeo usa o mesmo `prompt_en`. O estilo "Livre", o 1º da lista, não tem preset de câmera, só "natural pace, fluid motion" (`worker/presets/receitas.v3.json`, `presets.v3.json`); o v6 escreve frases como "the main subject stays in place while soft light glides across it… Static camera." (`worker/prompts/compiler-i2v.v6.txt`). Em 09/10, um caso vago parecido virou "She stays in place…": uma pessoa inventada (`worker/evals/compiler/cases.v2.json`).
2. **Modelos e tradutor feitos para conteúdo adulto.** O checkpoint "R1" da comunidade e o codificador de texto `nsfw_wan_umt5_xxl_v2` (`worker/workflows/i2v_r1v2.v1.contract.json`; `docs/12`, seção 1), e o prompt do tradutor v6 começa com "This is an adult content pipeline" e manda traduzir nudez ao pé da letra. Explica pessoas e corpos aparecendo e boa parte dos vídeos barrados.
3. **Sem guia negativa:** com CFG 1, o prompt negativo não tem efeito (`referencia/especificacao-pipeline-manual.md`).
4. **Duração e recorte:** acima de ~5 s o modelo repete o movimento e deforma (a mesma especificação); o formato "auto" recorta a foto em três formatos fixos e amplia as pequenas.
5. **As conferências não veem isso:** elas comparam o 1º quadro com a foto e a cor do centro ao longo do vídeo (`conferencia.py`); não veem objeto novo, pessoa nova nem movimento em laço. O filtro de imagem barra com nota ≥ 0,35 (o cartão do modelo usa 0,5), confere também o 1º quadro, que é a própria foto do cliente, e ainda não foi calibrado com fotos reais (ADR 0005, "Antes de aceitar").

**Correções propostas:**
- **Sem ADR:**
  - tradutor `compiler-i2v.v7.txt` sem as linhas de conteúdo adulto, com a rodada de fidelidade incluindo este texto;
  - `receitas.v4.json` com o "Livre" ganhando uma câmera suave;
  - um exemplo de texto mais concreto na página;
  - traduzir de novo quando o prompt inventa uma pessoa que o pedido não citou;
  - teto de 5 s, ou 5 s com interpolação (seção 9.2).
- **Com ADR e teste em GPU:**
  - a troca de modelo que o dono já previu: arquivos oficiais da Comfy-Org (Wan 2.2 I2V fp8_scaled, codificador padrão e o LoRA lightx2v de 4 passos) e um Qwen 2.5 7B Instruct comum no tradutor;
  - legenda de cada foto com o Qwen2.5-VL-7B (Apache-2.0; o 3B não serve, licença não comercial, `docs/12`);
  - guia negativa com CFG 1 (NAG).

### 9.2 Recomendações de velocidade que o dono recebeu

| Recomendação | Como está o projeto | Veredito |
|---|---|---|
| Pesos em FP8 | Já são: os pesos do R1 v2 estão em fp8 e4m3 com escala (`comfy_quant`, `models.lock.json`), e o ComfyUI v0.38.0 já multiplica em fp8 nessas GPUs. Por isso o `--fast fp8_matrix_mult` não ganhou nada no teste de 06/10 (L40S: 55 s em 480p e 180 s em 720p, vídeos de 5 s) | Nada a fazer |
| GGUF | Economiza VRAM, mas é mais lento: desquantiza cada camada antes de multiplicar. Medido no ADR 0001: Q6_K 77,2 s × fp8 56,5 s | Não usar |
| `--fp8_e4m3fn` | Esse nome não existe: o ComfyUI nem inicia (argumento ambíguo). Os reais (`--fp8_e4m3fn-unet`, `--fp8_e4m3fn-text-enc`) só mudam arquivos que não são fp8 | Não usar |
| `--highvram` / `--gpu-only` | Os dois especialistas já ficam na VRAM de 48 GB; essas opções desligam a gerência de memória que protege do Ollama (mesma GPU) e não cabem na 4090 | Não usar; manter `--reserve-vram` |
| FlashAttention-2 | O PyTorch 2.8 (SDPA) já usa os núcleos do FA2 | Não usar |
| SageAttention 2.2 | **Medido em 12/10** (L40S): a atenção 1,8× mais rápida; o vídeo inteiro **−15,3% em 480p** (42,9 × 50,7 s) e **−26,3% em 720p** (125,4 × 170,2 s); SSIM 0,98–0,99 contra a atenção atual, iguais a olho. Mas um vídeo a mais reprovou na conferência (deriva de cor 40,4, limite 40; os do café sobem 1–5 pontos). Precisa de um wheel compilado (fixado por SHA-256 no espelho R2) e de `gcc` no pod | **Não entra.** O teste maior, de 14/10 (22 vídeos 480p na produção, sem e com ele, no mesmo pod; `worker/evals/gpu-2026-10-13-qualidade/`): −15,1% de tempo (53,1 → 45,1 s), mas de novo 1 reprovação a mais na conferência (deriva 41,0) e SSIM médio de 0,871 (a regra pedia 0,90) |
| `--enable-triton-backend` | Com cu128, partes da quantização fp8 rodam em PyTorch puro; **estimativa** de 2–5% | Não medido (fora do pedido de 12/10) |
| Interpolar 16 → 32 fps | Nós de RIFE e FILM já no ComfyUI v0.38.0; pesos RIFE v4.25 (MIT, 22,7 MB, `Comfy-Org/frame_interpolation`). **Medido em 12/10:** +4,4 a +5,0 s por vídeo 480p de 5 s (+9%), arquivo +10%, sem fantasma nos movimentos lentos testados. A conferência e o filtro continuam no vídeo de 16 fps (a 32 fps, o salto de um corte cai para menos do limite) | **Proposta: [ADR 0013](adr/0013-interpolacao-rife.md)**, decisão do dono (muda o vídeo entregue). GIMM-VFI não (licença não comercial) |
| A GPU não espera (da proposta FastAPI/asyncio) | O worker da fila (`fila.py`) fazia um vídeo de cada vez: baixar e preparar a foto, gerar, conferir, filtrar e enviar. Agora pega e prepara o próximo e confere e envia o anterior enquanto a GPU gera. **Medido em 12/10:** GPU parada entre vídeos de 2,03 s para 0,04 s; 54,3 → 51,7 s por vídeo (−4,8%, com a rede simulada; com a rede de verdade, o ganho é o tempo real do envio e do download) | **Feito**, ligado por padrão (`--sem-adiantar` desliga) |
| Carregar o modelo no boot (da mesma proposta) | O 1º vídeo de um ComfyUI novo leva 56,2 s contra 50,7 s quente (num pod novo; na volta da hibernação, ~26 s a mais). Com a fila vazia logo depois do PRONTO, o worker aquece com um vídeo pequeno e descartado (16,6 s de GPU) e o 1º pedido sai em 50,7 s | **Feito**, ligado (só age com a fila vazia; `--sem-aquecer` desliga) |
| `torch.compile` (da mesma proposta) | Compila de novo a cada tamanho de vídeo e pode mudar o resultado | Fora, por decisão do dono (12/10) |

**Teste em GPU feito em 12/10/2026** (`worker/evals/gpu-2026-10-12-velocidade/`, um pod L40S secure, 47 min, US$ 0,86, com desligamento automático e encerrado no fim): o worker de antes e o novo, o aquecimento, o SageAttention 2.2 (compilado no pod, do commit da tag v2.2.0) e o RIFE ×2, com fotos SFW e seeds fixas, e as regras de decisão escritas antes da medição. Resultado na tabela acima; o detalhe, as folhas de quadros e o incidente do roteiro (a fase 2 caiu e o pod foi retomado para refazê-la), no LEIAME da medição.


### 9.3 Vídeos barrados por "conteúdo impróprio" (12/10/2026, pedido do dono)

- **Onde está o medidor:** `worker/src/render2go_worker/filtro_imagem.py` (constantes `LIMIAR`, `LIMIAR_ALTO`, `JANELA` e a função `barrado`), rodando no pod em 1 a cada 4 quadros de cada vídeo; a API confere de novo com as notas que o pod manda (`servico/src/render2go_servico/api.py`, rota `concluir`). Classificador: `Freepik/nsfw_image_detector` (ADR 0005).
- **Por que barrava demais:** um **quadro só** com P(medium) + P(high) ≥ **0,35** barrava o vídeo inteiro, abaixo do limiar de 0,5 do próprio cartão do modelo. Com 13 a 29 quadros por vídeo, um quadro de transição (borrão, mão, tecido, pele de um produto) bastava.
- **Regra nova:** barra com a média de 2 quadros seguidos (0,25 s) ≥ **0,5**, ou com um quadro **explícito** (P(high)) ≥ **0,35**. O nível "medium" (nudez parcial) precisa aparecer por mais de um instante; o explícito barra num quadro só. Os dois limiares mudam no `.env`, só dentro de limites (o filtro nunca é desligado); a consulta para calibrar com os números reais está no `docs/09`, seção 5.1.9.
- **O que também pesa, e não é o filtro:** o modelo de vídeo e o codificador de texto em produção são ajustes da comunidade para conteúdo adulto ("Uncensored", vocabulário explícito; `docs/12`, seção 1). Eles aumentam a chance de o próprio vídeo sair impróprio a partir de uma foto e de um texto inocentes. A troca pelos arquivos oficiais do Wan 2.2 (Apache 2.0) é a correção de raiz: ADR com teste em GPU, já recomendada.
- **Desde 12/10, também nas fotos:** a mesma regra roda no servidor antes de qualquer pod (`servico/src/render2go_servico/filtro_fotos.py`): a foto que não passa nem chega à GPU, e o cliente vê só que ela não pôde ser usada (ADR 0005, atualização de 12/10).
- **Vídeos quase instantâneos:** a avaliação está em `docs/14`.

### 9.4 O que entrou em 13/10/2026 (pedido do dono, sem GPU)

As correções "sem ADR" da seção 9.1, com teste de verdade (fidelidade em CPU e testes do worker, do serviço e no navegador). As "com ADR" (modelos oficiais, tradutor comum e legenda por foto) ficam para a etapa seguinte, que depende de uma rodada em GPU.

- **Pedido vago decidido no código** (`worker/src/render2go_worker/traducao.py`, `pedido_vago`): sem nenhuma palavra de movimento, mudança ou câmera no texto ("deixe bonito", "apresentação da minha loja nova"), o prompt é o **movimento padrão**, um texto fixo em inglês que não passa pelo LLM. Numa sonda com frases fora dos casos de teste, o 7B abliterado separou vago × concreto em só 14 de 28 frases e inventou um cachorro num "algo que chame a atenção dos clientes"; o vocabulário acertou 54 de 55. O texto do cliente continua passando pelo compilador (a regra `REFUSED` vale) e pelo C1.
- **Movimento padrão por categoria de foto** (receitas v4, `worker/presets/receitas.v4.json`): comida e bebida, aproximação lenta com vapor e brilho; produto e cosmético, aproximação com a luz passando; fachada e interior, deslocamento lateral lento; roupa, tecido em movimento leve e órbita lenta; carro, órbita lenta; pessoa, movimento sutil sem mexer no rosto; e o geral, a luz passando com uma aproximação bem lenta. Até a legenda por foto (a etapa seguinte), a categoria é a do estilo (o Livre usa a geral).
- **Livre com câmera suave:** o estilo Livre ganhou a câmera "Suave (aproximação bem lenta)" quando o texto do cliente não fala da câmera. Com a câmera parada, o modelo mexia o que devia ficar parado (seção 9.1). Presets v4: os termos da v3 iguais, mais as câmeras do movimento padrão e a continuidade com o rosto igual.
- **Pessoa inventada:** com a foto sem pessoas (pela análise do servidor; sem a análise, na dúvida, sem pessoas) e o texto do cliente sem ninguém, o prompt que cita uma pessoa é traduzido de novo, até 3 vezes (outra seed). Esgotadas as tentativas, vale o movimento padrão, sem pessoa. A API confere de novo quando o prompt chega e, outra vez, quando cada vídeo vai para o pod (a última barreira, que pega também os pedidos traduzidos antes desta regra), e grava o evento `prompt_pessoa_inventada` no registro do pedido (só o painel vê, no "Baixar registro de auditoria"). Na rodada de fidelidade, nenhum dos dois tradutores precisou de uma 2ª tradução: os pedidos vagos, onde o "She stays in place…" de 09/10 apareceu, agora nem chegam ao texto do LLM.
- **Teto de 5 s:** os pedidos novos do pago e do interno são de 4 ou 5 s (eram de 4 a 7); o teste continua com 3 s. Os pedidos feitos antes seguem com a duração que tinham (o pod aceita até 7 s).
- **Página do pedido:** o exemplo de cada estilo agora é concreto ("a câmera se aproxima devagar do bolo e o vapor sobe da xícara"); o botão "Usar o texto pronto do estilo" copia o texto genérico do estilo, que serve para qualquer foto (o exemplo cita um produto que as fotos do cliente podem não ter); os exemplos de pedido são um por tipo de foto, nenhum vago; e a dica "Produto com rótulo ou texto pequeno: escolha Alta (720p)".
- **Tradutor v7, medido e fora:** sem as linhas de conteúdo adulto, com o `VAGUE` e a linha `pessoas: nenhuma`. Na rodada de fidelidade de 13/10 (`cases.v3.json`, 141 rodadas que contam), acertou 58,2% dos casos contra 69,5% do v6, com menos "sem invenção" (90,8% × 96,5%) e uma aspa perdida; ganhou só nas recusas (3 de 9 × 0 de 9). Pela regra escrita antes, **o v6 continua** (`worker/evals/compiler/RESULTADOS.md`, "Rodada de 13/10/2026"). As linhas de conteúdo adulto do v6 seguem lá até um tradutor que passe nos critérios (o v7 com um Qwen comum, não abliterado, é medido na etapa seguinte).

### 9.5 Rodada em GPU de 13 e 14/10/2026: modelos oficiais e legenda por foto ficam de fora

A rodada do [ADR 0014](adr/0014-modelos-oficiais-tradutor-e-legenda.md) (`worker/evals/gpu-2026-10-13-qualidade/`, três pods L40S, julgamento às cegas): com as mesmas fotos, prompts e seeds, a produção (A) fez 11 vídeos aproveitáveis de 22 em 480p e 6 de 11 em 720p; os modelos oficiais do Wan 2.2 com o LoRA de 4 passos (B), 5 e 3, com 3 reprovações na conferência e 3 pessoas inventadas; B com a legenda por foto (D), 3 de 22. **Nada disso entra; a produção não muda** (ADR 0014, "Resultado da rodada em GPU"). A legenda também espera demais: ~60 s por foto nova na CPU do pod.

**O que sobra nos vídeos de produção** (os 11 não aproveitáveis de A em 480p), para a próxima etapa:
- **Líquido despejado** (4): nos pedidos vagos de café, frasco e garrafa e no concreto do café ("o vapor sobe…"). O modelo associa bebida e vidro a "encher"; um movimento padrão que não fale de vapor nem de líquido nas fotos de bebida, e o termo "nothing is poured, no liquid is added" nos presets, são o próximo teste (preset novo, `presets.v5.json`, com a rodada de fidelidade e uma rodada em GPU com critérios escritos antes).
- **Rostos que mudam** quando as pessoas se mexem (a foto antiga da padaria, nos dois pedidos): o movimento padrão de "pessoa" já pede o rosto parado, mas o pedido concreto ("as pessoas conversam") mexe nelas; para fotos com pessoas, sugerir na página um movimento de câmera em vez de um movimento das pessoas.
- **Quadros e cartazes trocados** no salão e **galhos de árvore** na fachada quando a câmera avança ou sobe: o modelo inventa o que a foto não mostra; o movimento de câmera curto (aproximação lenta) inventa menos que "avançar pelo salão" ou "subir pela fachada".
- **Nos pedidos vagos, ainda:** a asa da xícara que entorta e um objeto branco que desce sobre o tênis com um clarão.

A conferência automática não pega nenhum desses: os 22 de A passaram nela. Ela só vê a cor do centro e o 1º quadro (seção 9.1, item 5).
