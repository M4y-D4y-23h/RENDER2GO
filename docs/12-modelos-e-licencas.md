# 12. Modelos de IA e licenças comerciais

Quais modelos abertos podem ser usados num serviço pago como o Render2Go e com que condições. Serve para escolher o próximo modelo de cada finalidade da tela "O que você quer criar?" (`servico/src/render2go_servico/modelos.py`).

- **Conferido em 09/10/2026**, nos cartões e nos textos das licenças no Hugging Face e no GitHub (links em cada linha). Licenças mudam (a da LTX mudou duas vezes em 2026): releia a licença do modelo na data em que ele for adotado.
- **Não é parecer jurídico.** Antes de cobrar por um modelo com licença própria (seção 3), vale uma leitura do texto inteiro, de preferência com um advogado.
- **A licença que vale é a dos arquivos usados de fato.** Versões quantizadas (fp8, GGUF) e reempacotadas (Comfy-Org, Kijai) herdam a licença do original; versões ajustadas pela comunidade podem trazer outros riscos (seção 1).

## Resumo

| Finalidade | Liberados para uso comercial | Liberados com condições | Não comerciais (fora) |
|---|---|---|---|
| Imagem para vídeo | **Wan 2.2** (em uso) | LTX, HunyuanVideo-I2V, CogVideoX-5B, SkyReels V2 | |
| Texto para vídeo | Wan 2.2, Mochi 1, CogVideoX-2B, Step-Video\* | LTX, HunyuanVideo | |
| Vídeo para vídeo | Wan 2.2 Animate, Wan VACE | | |
| Áudio para vídeo | Wan 2.2 S2V, InfiniteTalk, MultiTalk | HunyuanVideo-Avatar | |
| Texto para imagem | Qwen-Image, FLUX.1 schnell, Chroma | HiDream-I1, SDXL, Stable Diffusion 3.5, HunyuanImage | FLUX.1 dev, FLUX.2 dev |
| Imagem para imagem | Qwen-Image-Edit, Step1X-Edit\* | | FLUX.1 Kontext dev, OmniGen2 |
| Ampliação | SeedVR2, Real-ESRGAN | | |
| Imagem para 3D | TRELLIS | Hunyuan3D | |
| Texto para voz | Chatterbox (fala português), Kokoro | | XTTS-v2, F5-TTS |
| Texto para música | ACE-Step | Stable Audio Open | MusicGen |
| Vídeo para áudio | ThinkSound\* | HunyuanVideo-Foley | MMAudio |
| Áudio para imagem | nenhum modelo aberto consolidado (seção 2.11) | | |
| Tradutor do pedido | Qwen 2.5 7B (em uso) | | |

"Liberados" = Apache 2.0, MIT ou BSD-3: uso comercial sem limite de faturamento nem de território (o que exigem está na seção 3.1).

\* O modelo principal é Apache 2.0 ou MIT, mas usa componentes de terceiros (codificadores de texto ou de imagem) cuja licença o cartão não informa: confira antes de adotar. É o caso que derrubou o OmniGen2: o cartão diz Apache 2.0, mas ele embute o Qwen2.5-VL-3B, de licença só para fins não comerciais (seção 2.5).

## 1. Modelos em uso hoje

| Peça | Arquivos | Licença | Fonte |
|---|---|---|---|
| Imagem para vídeo (produção) | `Wan2.2_I2V_{High,Low}_R1_n54w_v2` | Apache 2.0 | [rzgar/Wan2.2_I2V_R1_base_v2](https://huggingface.co/rzgar/Wan2.2_I2V_R1_base_v2) |
| Codificador de texto (produção) | `nsfw_wan_umt5_xxl_v2_fp8_e4m3fn_scaled` | Apache 2.0 | [rzgar/NSFW-Wan-UMT5-XXL-V2](https://huggingface.co/rzgar/NSFW-Wan-UMT5-XXL-V2) |
| VAE | `wan_2.1_vae` | Apache 2.0 | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged) |
| Texto para vídeo (testado, fora do serviço) | Wan 2.2 T2V A14B fp8 e GGUF, LoRA Lightning | Apache 2.0 | [Comfy-Org](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged), [QuantStack](https://huggingface.co/QuantStack/Wan2.2-T2V-A14B-GGUF) |
| Tradutor do pedido | `huihui_ai/qwen2.5-abliterate:7b` (Ollama) | Apache 2.0 (base Qwen 2.5 7B Instruct) | [Qwen/Qwen2.5-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/blob/main/LICENSE), [huihui-ai](https://huggingface.co/huihui-ai/Qwen2.5-7B-Instruct-abliterated-v2) |
| Filtro de imagem nos quadros (desde 11/10/2026, ADR 0005) | `Freepik/nsfw_image_detector` (EVA02-base 448 px) e a biblioteca `timm` 1.0.30 | MIT (modelo e base `timm/eva02_base_patch14_448`); Apache 2.0 (`timm`) | [Freepik/nsfw_image_detector](https://huggingface.co/Freepik/nsfw_image_detector), [timm](https://github.com/huggingface/pytorch-image-models) |

**Juridicamente, tudo o que roda hoje permite uso comercial.** Mas há um risco que não é de licença: os dois arquivos principais do Wan em produção são ajustes da comunidade feitos para conteúdo adulto, herança do projeto original (antes do caminho A, `docs/05`).
- O modelo de vídeo é marcado "Uncensored" no próprio cartão.
- O codificador de texto ganhou 767 palavras novas de vocabulário sexual explícito.
- Num serviço comercial, só de conteúdo seguro, isso aumenta a chance de um resultado impróprio e pesa contra a marca se for descoberto.
- Também não há garantia de procedência dos dados desses ajustes.

**Recomendação:** trocar pelos arquivos oficiais equivalentes, todos Apache 2.0 e do mesmo repositório de onde já vem o VAE:
- `wan2.2_i2v_{high,low}_noise_14B_fp8_scaled` e a LoRA `wan2.2_i2v_lightx2v_4steps_lora_v1`, em [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged);
- o codificador `umt5_xxl_fp8_e4m3fn_scaled` original, em [Comfy-Org/Wan_2.1_ComfyUI_repackaged](https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged).

Pelas regras do projeto, a troca é um ADR com teste em GPU: as mesmas receitas, comparando qualidade e tempo, porque o ajuste atual já traz o acelerador embutido e a regra "sem LoRA Lightning" muda.

O tradutor (versão "abliterada" do Qwen, sem as recusas) também veio do projeto original. Para o serviço comercial, um Qwen 2.5 7B Instruct comum (Apache 2.0) é a escolha natural, com nova rodada de fidelidade.

## 2. Candidatos por finalidade

### 2.1 Imagem para vídeo e texto para vídeo

| Modelo | Licença | Uso comercial | Condições | Fonte |
|---|---|---|---|---|
| Wan 2.2 I2V A14B, T2V A14B, TI2V 5B | Apache 2.0 | Sim | Seção 3.1 | [I2V](https://huggingface.co/Wan-AI/Wan2.2-I2V-A14B), [T2V](https://huggingface.co/Wan-AI/Wan2.2-T2V-A14B), [TI2V](https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B) |
| Mochi 1 (texto para vídeo) | Apache 2.0 | Sim | Seção 3.1 | [genmo/mochi-1-preview](https://huggingface.co/genmo/mochi-1-preview) |
| CogVideoX-2B | Apache 2.0 | Sim | Seção 3.1 | [zai-org/CogVideoX-2b](https://huggingface.co/zai-org/CogVideoX-2b) |
| Step-Video-T2V | MIT | Sim, conferindo os 2 codificadores de texto (não informados no cartão) | Seção 3.1 | [stepfun-ai/stepvideo-t2v](https://huggingface.co/stepfun-ai/stepvideo-t2v) |
| LTX-Video (0.9.6 em diante) e LTX-2 | Licenças próprias da Lightricks | Sim, abaixo de US$ 10 mi de faturamento anual | Seção 3.3 | [LTX-Video 0.X](https://huggingface.co/Lightricks/LTX-Video/blob/main/LTX-Video-Open-Weights-License-0.X.txt), [LTX-2](https://github.com/Lightricks/LTX-2/blob/main/LICENSE) |
| HunyuanVideo, HunyuanVideo-I2V, HunyuanVideo 1.5 | Tencent Hunyuan Community | Sim, fora da UE, Reino Unido e Coreia do Sul | Seção 3.4 | [1.0](https://huggingface.co/tencent/HunyuanVideo/blob/main/LICENSE), [I2V](https://huggingface.co/tencent/HunyuanVideo-I2V), [1.5](https://github.com/Tencent-Hunyuan/HunyuanVideo-1.5/blob/master/LICENSE) |
| CogVideoX-5B e 1.5-5B | CogVideoX License | Sim, com registro, até 1 mi de visitas/mês | Seção 3.5 | [LICENSE](https://huggingface.co/zai-org/CogVideoX-5b/blob/main/LICENSE) |
| SkyReels V2 | Skywork Community License | Sim, segundo o cartão | Seção 3.6 | [cartão](https://huggingface.co/Skywork/SkyReels-V2-I2V-14B-540P) |

### 2.2 Vídeo para vídeo

| Modelo | Licença | Uso comercial | Fonte |
|---|---|---|---|
| Wan 2.2 Animate 14B (anima um personagem com o movimento de um vídeo) | Apache 2.0 | Sim | [Wan-AI/Wan2.2-Animate-14B](https://huggingface.co/Wan-AI/Wan2.2-Animate-14B) |
| Wan 2.1 VACE 14B (edição e estilo de vídeo) | Apache 2.0 | Sim | [Wan-AI/Wan2.1-VACE-14B](https://huggingface.co/Wan-AI/Wan2.1-VACE-14B) |

### 2.3 Áudio para vídeo (pessoa falando ou cantando)

| Modelo | Licença | Uso comercial | Fonte |
|---|---|---|---|
| Wan 2.2 S2V 14B | Apache 2.0 | Sim | [Wan-AI/Wan2.2-S2V-14B](https://huggingface.co/Wan-AI/Wan2.2-S2V-14B) |
| InfiniteTalk, MultiTalk | Apache 2.0 | Sim | [InfiniteTalk](https://huggingface.co/MeiGen-AI/InfiniteTalk), [MultiTalk](https://huggingface.co/MeiGen-AI/MeiGen-MultiTalk) |
| HunyuanVideo-Avatar | Tencent Hunyuan Community | Com as condições da seção 3.4 | [cartão](https://huggingface.co/tencent/HunyuanVideo-Avatar) |

Qualquer modelo desta finalidade exige a autorização por escrito da pessoa retratada e da dona da voz, como já é exigido nas fotos com pessoas (`docs/03`). Sem isso, vira ferramenta de deepfake.

### 2.4 Texto para imagem

| Modelo | Licença | Uso comercial | Condições | Fonte |
|---|---|---|---|---|
| Qwen-Image | Apache 2.0 (o codificador, Qwen2.5-VL-7B, também) | Sim | Seção 3.1 | [Qwen/Qwen-Image](https://huggingface.co/Qwen/Qwen-Image), [Qwen2.5-VL-7B](https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct) |
| FLUX.1 schnell | Apache 2.0 | Sim | Download pede aceite no Hugging Face | [black-forest-labs/FLUX.1-schnell](https://huggingface.co/black-forest-labs/FLUX.1-schnell) |
| HiDream-I1 | MIT, mas usa o Llama 3.1 8B como codificador de texto | Sim, com as condições do Llama 3.1 | Seção 3.9 | [HiDream-ai/HiDream-I1-Full](https://huggingface.co/HiDream-ai/HiDream-I1-Full) |
| Chroma | Apache 2.0 | Sim | Seção 3.1 | [lodestones/Chroma](https://huggingface.co/lodestones/Chroma) |
| Stable Diffusion XL | CreativeML Open RAIL++-M | Sim | Seção 3.2 | [LICENSE](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/main/LICENSE.md) |
| Stable Diffusion 3.5 | Stability AI Community | Sim, abaixo de US$ 1 mi de faturamento anual | Seção 3.7 | [LICENSE](https://huggingface.co/stabilityai/stable-diffusion-3.5-large/blob/main/LICENSE.md) |
| HunyuanImage 2.1 e 3.0 | Tencent Hunyuan Community | Com as condições da seção 3.4 | | [2.1](https://github.com/Tencent-Hunyuan/HunyuanImage-2.1/blob/master/LICENSE), [3.0](https://huggingface.co/tencent/HunyuanImage-3.0) |
| FLUX.1 dev, FLUX.2 dev | Licenças não comerciais da Black Forest Labs | **Não** (só com licença paga da BFL) | | [FLUX.1 dev](https://huggingface.co/black-forest-labs/FLUX.1-dev/blob/main/LICENSE.md), [FLUX.2 dev](https://huggingface.co/black-forest-labs/FLUX.2-dev) |

### 2.5 Imagem para imagem (edição)

| Modelo | Licença | Uso comercial | Fonte |
|---|---|---|---|
| Qwen-Image-Edit e Qwen-Image-Edit-2509 | Apache 2.0 | Sim | [Qwen-Image-Edit](https://huggingface.co/Qwen/Qwen-Image-Edit), [2509](https://huggingface.co/Qwen/Qwen-Image-Edit-2509) |
| Step1X-Edit | Apache 2.0 | Sim, conferindo o modelo de visão e linguagem que ele usa | [stepfun-ai/Step1X-Edit](https://huggingface.co/stepfun-ai/Step1X-Edit) |
| OmniGen2 | Apache 2.0 no cartão, mas embute o Qwen2.5-VL-3B (`mllm/`, 36 camadas de 2048), de licença Qwen Research | **Não**: o componente é só para fins não comerciais | [OmniGen2/OmniGen2](https://huggingface.co/OmniGen2/OmniGen2), [licença do Qwen2.5-VL-3B](https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct/blob/main/LICENSE) |
| FLUX.1 Kontext dev | Não comercial (BFL) | **Não** | [FLUX.1-Kontext-dev](https://huggingface.co/black-forest-labs/FLUX.1-Kontext-dev) |

### 2.6 Ampliação de imagem e vídeo

| Modelo | Licença | Uso comercial | Fonte |
|---|---|---|---|
| SeedVR2 (vídeo e imagem) | Apache 2.0 | Sim | [ByteDance-Seed/SeedVR2-3B](https://huggingface.co/ByteDance-Seed/SeedVR2-3B) |
| Real-ESRGAN (imagem) | BSD-3-Clause | Sim | [xinntao/Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN/blob/master/LICENSE) |

### 2.7 Imagem para 3D

| Modelo | Licença | Uso comercial | Fonte |
|---|---|---|---|
| TRELLIS | MIT | Sim | [microsoft/TRELLIS-image-large](https://huggingface.co/microsoft/TRELLIS-image-large) |
| Hunyuan3D 2 e 2.1 | Tencent Hunyuan Community | Com as condições da seção 3.4 | [2](https://huggingface.co/tencent/Hunyuan3D-2), [2.1](https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1/blob/main/LICENSE) |

### 2.8 Texto para voz

| Modelo | Licença | Uso comercial | Observação | Fonte |
|---|---|---|---|---|
| Chatterbox e Chatterbox Multilingual | MIT | Sim | Fala português, com uma versão ajustada para o português do Brasil; clona vozes, então exige a autorização da dona da voz | [ResembleAI/chatterbox](https://huggingface.co/ResembleAI/chatterbox) |
| Kokoro 82M | Apache 2.0 | Sim | Leve; conferir a qualidade em português | [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) |
| Dia 1.6B | Apache 2.0 | Sim | Só inglês | [nari-labs/Dia-1.6B](https://huggingface.co/nari-labs/Dia-1.6B) |
| XTTS-v2 | Coqui Public Model License | **Não** | Não comercial | [coqui/XTTS-v2](https://huggingface.co/coqui/XTTS-v2) |
| F5-TTS | CC BY-NC 4.0 (pesos) | **Não** | Não comercial | [SWivid/F5-TTS](https://huggingface.co/SWivid/F5-TTS) |

### 2.9 Texto para música

| Modelo | Licença | Uso comercial | Fonte |
|---|---|---|---|
| ACE-Step v1 3.5B | Apache 2.0 | Sim | [ACE-Step/ACE-Step-v1-3.5B](https://huggingface.co/ACE-Step/ACE-Step-v1-3.5B) |
| Stable Audio Open 1.0 | Stability AI Community (segundo o cartão; o texto exige login) | Sim, abaixo de US$ 1 mi de faturamento anual (seção 3.7) | [stabilityai/stable-audio-open-1.0](https://huggingface.co/stabilityai/stable-audio-open-1.0) |
| MusicGen | CC BY-NC 4.0 | **Não** | [facebook/musicgen-large](https://huggingface.co/facebook/musicgen-large) |

### 2.10 Vídeo para áudio (som e efeitos)

| Modelo | Licença | Uso comercial | Fonte |
|---|---|---|---|
| ThinkSound | Apache 2.0 | Sim, conferindo os codificadores de imagem e texto que ele usa | [FunAudioLLM/ThinkSound](https://huggingface.co/FunAudioLLM/ThinkSound) |
| HunyuanVideo-Foley | Tencent Hunyuan Community | Com as condições da seção 3.4 | [LICENSE](https://huggingface.co/tencent/HunyuanVideo-Foley/blob/main/LICENSE) |
| MMAudio | CC BY-NC 4.0 | **Não** | [hkchengrex/MMAudio](https://huggingface.co/hkchengrex/MMAudio) |

### 2.11 Áudio para imagem

Não há um modelo aberto consolidado que gere imagem direto do som. O caminho usado no mercado é em duas etapas: o áudio vira texto (transcrição ou descrição) e o texto vira imagem (seção 2.4). A opção fica na lista completa da tela, como "Em breve", sem modelo previsto.

## 3. O que cada licença exige

### 3.1 Apache 2.0, MIT e BSD-3

- **Uso comercial liberado**, sem limite de faturamento, de usuários ou de território. As imagens e os vídeos gerados são de quem gerou (as licenças não reivindicam as saídas).
- **Ao redistribuir os arquivos** (não é o caso de um serviço que só roda o modelo): manter a licença e os avisos de direitos autorais; na Apache 2.0, também o arquivo NOTICE, se houver, e marcar os arquivos modificados.
- **Marcas:** a licença não autoriza usar o nome ou o logotipo do autor como se fosse parceiro (ex.: "Wan 2.2" pode ser citado como o modelo usado, não como selo de endosso).

### 3.2 CreativeML Open RAIL++-M (SDXL)

- Uso comercial liberado, mas com **restrições de uso** (Anexo A: nada ilegal, nada que prejudique menores, desinformação, discriminação etc.).
- Essas restrições precisam constar nos **termos de uso do serviço** (os usuários ficam sujeitos a elas).

### 3.3 LTX (LTX-Video 0.9.6 em diante e LTX-2)

- **Grátis para quem fatura menos de US$ 10 milhões por ano**, somando empresas do mesmo grupo. Acima disso, licença paga (`ltxv-licensing@lightricks.com`).
- Restrições de uso no Anexo A; derivados (ajustes, LoRAs) só sob a mesma licença.
- A licença mudou duas vezes em 2026 (LTX-2 em 05/01 e LTX-2.x em 11/08): confira a versão do arquivo baixado.

### 3.4 Tencent Hunyuan Community (HunyuanVideo, HunyuanImage, Hunyuan3D, Avatar, Foley)

- **Território:** a licença não vale na União Europeia, no Reino Unido e na Coreia do Sul. Usar o modelo **ou mostrar o resultado** para clientes desses lugares não é permitido. O Brasil está dentro.
- Mais de 100 milhões de usuários ativos por mês: precisa pedir licença à Tencent.
- Não usar o modelo nem os resultados para melhorar outro modelo de IA.
- Política de uso aceitável (Anexo A) e cópia do contrato para quem recebe o modelo ou o serviço.
- **Na versão 1.5**, o serviço precisa informar com destaque o nome da empresa que o presta e dizer que a Tencent não é afiliada, não patrocina nem endossa o serviço.
- Para o Render2Go: dá para usar no Brasil, mas é preciso bloquear clientes da UE, do Reino Unido e da Coreia do Sul. Por isso fica atrás dos modelos Apache.

### 3.5 CogVideoX License (CogVideoX-5B e 1.5)

- Uso comercial grátis **depois de registro** no formulário da Zhipu (open.bigmodel.cn/mla/form).
- Limite de **1 milhão de visitas por mês**; acima, contrato à parte.
- Lei chinesa e foro em Pequim; restrições de uso (militar, ilegal, segurança nacional da China).

### 3.6 Skywork Community License (SkyReels V2)

- O cartão diz que o uso comercial é permitido sem pedido prévio, desde que se cumpram os termos da licença, que está num PDF. **Leia o PDF antes de adotar**; ele não foi conferido aqui.

### 3.7 Stability AI Community (Stable Diffusion 3.5, Stable Audio Open)

- **Grátis para quem fatura menos de US$ 1 milhão por ano** (somando o grupo), com **registro** em stability.ai/community-license para uso comercial. Ao passar de US$ 1 milhão, a licença acaba e é preciso a licença empresarial.
- Mostrar "Powered by Stability AI" no site ou na documentação; os resultados são de quem gerou.

### 3.8 Não comerciais (fora)

FLUX.1 dev, FLUX.1 Kontext dev e FLUX.2 dev (Black Forest Labs), XTTS-v2 (Coqui), F5-TTS, MusicGen e MMAudio (CC BY-NC 4.0), e o OmniGen2, pelo componente Qwen2.5-VL-3B (Qwen Research License: "non-commercial purposes only"). Um serviço pago é uso comercial do modelo, mesmo que o cliente fique com as imagens. Só com licença paga do autor, quando ele oferece.

### 3.9 Llama 3.1 Community License (codificador do HiDream-I1)

- Uso comercial permitido; acima de 700 milhões de usuários ativos por mês, precisa de licença da Meta.
- Mostrar "Built with Llama" no site ou na documentação do serviço, seguir a política de uso aceitável da Meta e aceitar os termos no Hugging Face (download restrito). Fonte: [meta-llama/Llama-3.1-8B-Instruct](https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct).

## 4. Modelos previstos na tela de finalidades

Na janela "Ver todas", cada opção "Em breve" mostra os modelos previstos. Entram só os liberados (seção 3.1), sem limite de faturamento nem de território (`modelos.py`, campo `previstos`):

| Finalidade | Previsto | Por quê |
|---|---|---|
| Vídeo a partir de um texto | Wan 2.2 (texto para vídeo) | Mesma família do que já roda; o fluxo e o tradutor T2V já existem no projeto (testados em GPU em 05/10) |
| Editar ou melhorar uma foto | Qwen-Image-Edit | Apache 2.0, troca de fundo, luz e estilo com texto |
| Imagem a partir de um texto | Qwen-Image | Apache 2.0, bom com texto escrito na imagem (artes de divulgação) |
| Pessoa falando o seu áudio | Wan 2.2 S2V | Mesma família; exige a autorização da pessoa |
| Mudar o estilo de um vídeo | Wan 2.2 Animate | Mesma família |
| Aumentar a resolução | SeedVR2 | Apache 2.0, imagem e vídeo |
| Objeto 3D a partir de uma foto | TRELLIS | MIT |
| Locução a partir de um texto | Chatterbox Multilingual | MIT, fala português do Brasil |
| Trilha sonora a partir de um texto | ACE-Step | Apache 2.0 |
| Som e efeitos para um vídeo | (nenhum por enquanto) | O ThinkSound é Apache 2.0, mas os componentes dele ainda não foram conferidos |
| Imagem a partir de um áudio | (nenhum) | Seção 2.11 |

## 5. Antes de ativar um modelo

1. Reler a licença na data e registrar aqui a versão conferida.
2. Conferir de onde vêm os arquivos usados (oficial, reempacotado, ajuste da comunidade) e se herdam a licença, **e a licença de cada componente** (codificadores de texto, de imagem e de áudio, VAE): foi assim que o OmniGen2 caiu.
3. Aceitar os termos no Hugging Face quando o download exige (gated), com a conta do projeto.
4. Termos de uso do serviço com as restrições que a licença manda repassar (RAIL, políticas de uso aceitável).
5. Segurança: filtro C1 no texto, revisão humana, autorização de pessoas e vozes, e conferência automática própria da finalidade (`docs/03`, `docs/08`).
6. ADR do modelo (fluxo de geração, contrato, arquivos no `models.lock.json`), perfil próprio do tradutor com rodada de fidelidade, preço (`docs/11`) e o modelo em `modelos.py` com `disponivel: True`.
