# Especificação técnica: pipeline de vídeo IA (Wan 2.2 + Ollama) no RunPod

**Documento de handoff para o Claude Code.**
Montado a partir de uma sessão real de configuração e testes feita entre 3 e 4 de outubro de 2026. Tudo que está marcado como **[TESTADO]** funcionou na prática; o que está marcado como **[NÃO VERIFICADO]** precisa ser conferido antes de entrar em produção.

> Cópia fiel do documento entregue em 05/10/2026, guardada como referência; só esta nota foi acrescentada. O resultado da conferência dos itens **[NÃO VERIFICADO]** está em [`docs/01-verificacoes.md`](../docs/01-verificacoes.md).

---

## 0. Como usar este documento

Este arquivo descreve, em detalhe, um pipeline que hoje funciona **manualmente** (terminal Jupyter + interface gráfica do ComfyUI) e o objetivo de transformá-lo numa **plataforma automatizada** para clientes finais.

O Claude Code deve:

1. Ler as seções 1 a 9 para entender exatamente o que existe hoje, com nomes de arquivos, tamanhos, comandos e armadilhas já descobertas.
2. Ler a seção 10 (objetivo do produto) e a seção 11 (requisitos de segurança e conformidade, **obrigatórios**).
3. Conferir na documentação atual do RunPod, do ComfyUI e do Ollama tudo o que estiver marcado como **[NÃO VERIFICADO]**, porque APIs e recursos mudam (o Global Volume do RunPod, por exemplo, está em beta).
4. Propor a arquitetura antes de escrever código, usando as opções da seção 10 como ponto de partida.

---

## 1. Visão geral do pipeline atual

```
Usuário escreve a ideia em PORTUGUÊS
        │
        ▼
Ollama  ──  modelo "compilador-i2v" ou "compilador-t2v"
(Qwen2.5-7B-Instruct abliterated + system prompt próprio)
        │  devolve 1 parágrafo em INGLÊS, no formato que o Wan entende
        ▼
ComfyUI  ──  template "Wan 2.2 14B Image to Video" (subgraph)
   ├─ Load Image (imagem de entrada, define a aparência)
   ├─ Load Diffusion Model x2 → Wan2.2 I2V R1 v2 (high noise + low noise)
   ├─ Load CLIP → NSFW-Wan-UMT5-XXL-V2 (tipo "wan")
   ├─ Load VAE → wan_2.1_vae
   ├─ 2x KSamplerAdvanced (high noise → low noise), modo turbo
   └─ Save Video → MP4
        │
        ▼
/root/ComfyUI/output/video/*.mp4  ──(save.sh)──▶  Global Volume /workspace/output/
```

Os dois "casos de uso" do diagrama original do usuário:

- **I2V** (imagem + texto → vídeo): é o fluxo principal, já funcionando.
- **T2V** (texto → vídeo): pode ser feito de duas formas. (a) Com o Wan 2.2 T2V A14B oficial em GGUF, já baixado no volume mas ainda sem workflow montado. (b) Com o próprio R1 v2 usando uma imagem em branco como entrada, que o autor do modelo diz funcionar muito bem. Ainda não foi testado qual dá melhor resultado.

---

## 2. Modelos e arquivos (especificação completa)

### 2.1 Modelo de vídeo principal (I2V): Wan 2.2 I2V R1 v2 [TESTADO]

| Campo | Valor |
|---|---|
| Repositório | https://huggingface.co/rzgar/Wan2.2_I2V_R1_base_v2 |
| Arquivo high noise | `Wan2.2_I2V_High_R1_n54w_v2.safetensors` |
| Arquivo low noise | `Wan2.2_I2V_Low_R1_n54w_v2.safetensors` |
| Tamanho exato (cada) | **14.803.879.958 bytes** (~14,8 GB) |
| Formato | safetensors, fp8 (refeito sobre a base "KJ FP8") |
| Pasta no ComfyUI | `models/diffusion_models/` |
| Nó do ComfyUI | `Load Diffusion Model`, `weight_dtype = default` |
| Licença (segundo o card) | Apache-2.0 |

Informações do model card que afetam a configuração:

- **A aceleração lightx2v já vem embutida nos pesos.** Por isso, **não** se aplica a LoRA Lightning por cima, roda-se com poucos passos e CFG 1.0 (modo turbo do template).
- O autor afirma que o modelo também funciona em estilo T2V usando uma imagem em branco (ou o `canvas.png` da pasta demo do repositório) como entrada.
- Força recomendada para LoRAs adicionais: **0,5 a 0,65**.
- O card lista LoRAs opcionais de aparência e VAEs alternativos (não usados até agora).
- O autor recomenda usar o NSFW-Wan-UMT5-XXL-V2 como encoder.

Comando de download que funcionou:

```bash
hf download rzgar/Wan2.2_I2V_R1_base_v2 \
  Wan2.2_I2V_High_R1_n54w_v2.safetensors \
  Wan2.2_I2V_Low_R1_n54w_v2.safetensors \
  --local-dir /root/tmp
```

### 2.2 Text encoder: NSFW-Wan-UMT5-XXL-V2 [TESTADO]

| Campo | Valor |
|---|---|
| Repositório | https://huggingface.co/rzgar/NSFW-Wan-UMT5-XXL-V2 (marcado como NFAA/sensível no HF; o download por CLI funciona sem token) |
| Arquivo usado | `encoders/nsfw_wan_umt5_xxl_v2_fp8_e4m3fn_scaled.safetensors` |
| Tamanho exato | **6.741.537.445 bytes** (~6,7 GB) |
| Pasta no ComfyUI | `models/text_encoders/` |
| Nó do ComfyUI | `CLIPLoader` padrão, `type = wan` |
| Vocabulário | **257.067** tokens (o UMT5 original tem 256.384) |
| Licença | **[NÃO VERIFICADO]** a página está atrás de aviso de conteúdo; conferir antes de uso comercial |

Lista completa de arquivos do repositório (obtida com `huggingface_hub.list_repo_files`):

```
.gitattributes
ComfyUI-UMT5-Extended.zip
ComfyUI-UMT5-Extended/__init__.py
README.md
config.json
encoders/nsfw_wan_umt5_xxl_v2_bf16.safetensors
encoders/nsfw_wan_umt5_xxl_v2_fp16.safetensors
encoders/nsfw_wan_umt5_xxl_v2_fp32.safetensors
encoders/nsfw_wan_umt5_xxl_v2_fp8_e4m3fn_scaled.safetensors   ← usado
encoders/nsfw_wan_umt5_xxl_v2_int4_convrot.safetensors
encoders/nsfw_wan_umt5_xxl_v2_int8-convrot.safetensors
encoders/nsfw_wan_umt5_xxl_v2_int8.safetensors
load_example.py
new_tokens_v4.json
tokenizer/special_tokens_map.json
tokenizer/spiece.model
tokenizer/tokenizer_config.json
```

**Patch obrigatório: `ComfyUI-UMT5-Extended`** (zip de 1.101 bytes no mesmo repositório).

- Não cria nós visíveis. Faz um monkeypatch em `comfy.sd.load_text_encoder_state_dicts` que detecta o tamanho do vocabulário pelo shape de `shared.weight` e injeta `vocab_size` em `model_options["umt5xxl_model_config"]`.
- Sem ele, o carregamento falha por incompatibilidade de tamanho da camada de embedding.
- O código foi revisado: não acessa a rede nem lê outros arquivos.
- O comentário do autor sugere a subpasta `models/text_encoders/UMT5_extended/`, mas a detecção é pelo shape, não pela pasta, então a pasta padrão funciona.
- Instalação: descompactar em `ComfyUI/custom_nodes/` (o zip contém a pasta `ComfyUI-UMT5-Extended/`).
- Linhas de log que confirmam funcionamento:
  - `[ComfyUI-UMT5-Extended] Loaded — extended Wan UMT5 encoders supported` (na inicialização)
  - `[ComfyUI-UMT5-Extended] Extended UMT5 detected — vocab_size 257067 (stock 256384)` (na geração)

Download que funcionou:

```bash
hf download rzgar/NSFW-Wan-UMT5-XXL-V2 \
  encoders/nsfw_wan_umt5_xxl_v2_fp8_e4m3fn_scaled.safetensors \
  README.md ComfyUI-UMT5-Extended.zip \
  --local-dir /root/tmp
```

### 2.3 VAE [TESTADO]

| Campo | Valor |
|---|---|
| URL direta | https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors |
| Tamanho exato | **253.815.318 bytes** |
| Pasta | `models/vae/` |
| Observação | O Wan 2.2 **A14B** usa o VAE do 2.1. O `wan2.2_vae` é só para o modelo TI2V-5B. |

### 2.4 Modelo T2V oficial em GGUF (opcional) [BAIXADO, WORKFLOW NÃO MONTADO]

| Campo | Valor |
|---|---|
| Repositório | https://huggingface.co/QuantStack/Wan2.2-T2V-A14B-GGUF |
| Arquivos | `HighNoise/Wan2.2-T2V-A14B-HighNoise-Q6_K.gguf` e `LowNoise/Wan2.2-T2V-A14B-LowNoise-Q6_K.gguf` |
| Tamanho exato (cada) | **12.002.013.696 bytes** |
| Pasta | `models/unet/` |
| Nó | `Unet Loader (GGUF)` (extensão ComfyUI-GGUF) |
| Observação | Para rodar em modo turbo, precisaria das LoRAs Lightning T2V (seção 2.6). Pode ser descartado se o R1 v2 resolver o T2V, economizando 24 GB no volume. |

Download:

```bash
hf download QuantStack/Wan2.2-T2V-A14B-GGUF --include "*Q6_K*" --local-dir /root/tmp
```

### 2.5 Modelos oficiais de fallback (não baixados)

Usados pelo template padrão do ComfyUI; servem de referência ou alternativa caso os finetunes deem problema:

- `https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors` (~13,3 GB)
- `.../wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors` (~13,3 GB)
- `.../wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors` e `..._low_noise_...`
- Encoder oficial: `https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors` (~6,3 GB)
- Versões GGUF do I2V oficial: `QuantStack/Wan2.2-I2V-A14B-GGUF`
- LoRAs do template: `wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors` / `..._low_noise...` (~1,14 GB cada)

### 2.6 LoRAs Lightning (só para modelos oficiais) [NÃO VERIFICADO]

Necessárias apenas se for usar o T2V/I2V **oficial** em modo turbo. **Não** usar com o R1 v2. Fontes indicadas: repositório `lightx2v/Wan2.2-Lightning` ou a pasta Wan 2.2 de `Kijai/WanVideo_comfy`, ambos no Hugging Face. Os nomes exatos dos arquivos não foram conferidos.

### 2.7 LLM compilador de prompts [TESTADO]

| Campo | Valor |
|---|---|
| Runtime | Ollama (instalado via `curl -fsSL https://ollama.com/install.sh \| sh`; precisa de `zstd`) |
| Modelo base | `huihui_ai/qwen2.5-abliterate:7b` (tag confirmada; repare na grafia "abliterate", sem "d") |
| Tamanho | 4,7 GB (quantização padrão da tag) |
| Modelos derivados | `compilador-i2v` e `compilador-t2v`, criados com `ollama create` a partir de Modelfiles (seção 6) |
| Desempenho medido | RTX PRO 4500 (32 GB): 29/29 camadas na GPU, **61 tokens/s**, ~2 s por prompt com carga do modelo, ~0,2 s com o modelo já carregado |

Observações de comportamento:

- Mesmo abliterado, o 7B às vezes **suaviza** termos explícitos (omite, troca por eufemismo), **erra termos de câmera** ("de costas" virou "in profile") e **inventa detalhes**. As correções aplicadas estão na seção 9.
- Alternativa se o 7B não for fiel o suficiente: `huihui_ai/qwen2.5-abliterate:14b` (~9 GB) **[NÃO VERIFICADO: tag]**.
- Alternativa futura para I2V: uma versão **VL** (visão) abliterada do Qwen, que enxergaria a imagem de entrada.

### 2.8 Extensões (custom nodes) do ComfyUI

| Extensão | Repositório | Uso |
|---|---|---|
| ComfyUI-Manager | https://github.com/ltdrdata/ComfyUI-Manager | Gerenciador de nós |
| ComfyUI-GGUF | https://github.com/city96/ComfyUI-GGUF | Carregar modelos GGUF (T2V) |
| comfyui-ollama | https://github.com/stavsap/comfyui-ollama | Nós `Ollama Generate` + `Ollama Connectivity` |
| ComfyUI-VideoHelperSuite | https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite | Utilitários de vídeo |
| ComfyUI-Frame-Interpolation | https://github.com/Fannovel16/ComfyUI-Frame-Interpolation | RIFE (ainda não usado) |
| ComfyUI-UMT5-Extended | zip no repo do encoder NSFW | Patch do encoder (obrigatório) |

### 2.9 Versões de software observadas no log [TESTADO]

- ComfyUI 0.38.0 (commit `v0.38.0-33-gf1072eb0`), frontend 1.53.10, templates 0.11.76
- comfy-aimdo 0.5.5 ("DynamicVRAM" ativo), comfy-kitchen 0.2.37
- PyTorch **2.8.0+cu128**, Python 3.12.3
- Aviso não fatal: o ComfyUI pede PyTorch **cu130** para operações CUDA otimizadas. Funciona sem; atualizar pode quebrar dependências.
- ComfyUI-Manager V3.42

---

## 3. Infraestrutura RunPod

### 3.1 GPUs testadas

| GPU | VRAM | RAM do pod | Resultado |
|---|---|---|---|
| RTX 4090 | 24 GB | 59 GB | Funciona. 640×640, 81 frames, 4 passos: **37,7 s** por vídeo (modelos no container disk) |
| RTX PRO 4500 (Blackwell) | 32 GB | 90 GB, 16 vCPU | Funciona, geração rápida. Ollama 100% GPU |

GPUs de 48 GB (RTX A6000, A40, L40S) estavam esgotadas no momento dos testes. Recomendação por faixa:

- **24 GB:** funciona em 640–832 px; com o Ollama mantido carregado, a memória pode apertar.
- **32 GB:** bom equilíbrio; permite 720p.
- **48 GB+:** os dois experts cabem juntos; 720p com 7 s tranquilo.
- **Blackwell** (5090, PRO 4500/6000, B200): exige PyTorch com CUDA 12.8+. O template com torch 2.8.0+cu128 funcionou.

### 3.2 Configuração do pod

| Item | Valor |
|---|---|
| Template | RunPod PyTorch (o que trouxe torch 2.8.0+cu128) |
| Container disk | **60 GB** (ComfyUI, pacotes, Ollama e as cópias dos modelos ficam aqui) |
| Volume | **Global Volume** montado em `/workspace` |
| HTTP ports | `8188` (ComfyUI), `8888` (Jupyter) |
| TCP ports | `22` |
| Acesso | `https://<POD_ID>-8188.proxy.runpod.net` (o hostname do container é diferente do POD_ID) |

### 3.3 Global Volume: características e armadilhas [TESTADO]

O Global Volume (beta) é armazenamento elástico, independente de região, que qualquer pod pode montar, e persiste quando o pod é parado ou deletado. Preço observado na documentação: **US$ 0,09/GB/mês + IOPS**. **Se o saldo da conta zerar, ele é apagado permanentemente após 15 dias.**

Ele é baseado em armazenamento de objetos e **não é POSIX completo**. Consequências descobertas na prática:

| Operação | Comportamento | Solução adotada |
|---|---|---|
| `chmod` | `Operation not permitted` | Rodar scripts com `bash script.sh` |
| `rsync` padrão | Falha com `mkstemp ... Operation not permitted` (cria temporário oculto e renomeia) | `rsync -h --inplace --progress` |
| `cp` de arquivo grande | Uma vez **truncou silenciosamente** (4,7 GB de 12 GB) | Baixar para o container disk, copiar com `rsync --inplace`, conferir o tamanho em bytes |
| `hf download` direto no volume | Usa renomeação; arriscado | Baixar em `/root/tmp` e depois copiar |
| `sed -i` | Usa arquivo temporário + renomeação | `sed ... arquivo > /root/tmp.txt && cat /root/tmp.txt > arquivo` |
| `ls` logo após copiar | Pode mostrar dados desatualizados (cache de listagem de ~1 min) | Esperar e listar de novo |
| Escrita com `cat > arquivo` / `wget` | Funciona | — |
| Leitura sequencial | **85 a 234 MB/s** medidos | — |
| Leitura aleatória (mmap do ComfyUI) | **~14 MB/s** medidos | Copiar os modelos para o container disk (seção 4) |

### 3.4 Impacto medido da leitura do volume

| Cenário | Tempo total do 1º vídeo |
|---|---|
| ComfyUI lendo os modelos direto do Global Volume | **14 min 45 s** (sendo ~36 s de cálculo real) |
| Modelos copiados para o container disk | **37,67 s** |

O próprio ComfyUI loga `Model storage policy: fast_disk=False` nos dois casos.

### 3.5 Custos de referência (documentação RunPod, out/2026)

| Armazenamento | Preço |
|---|---|
| Container disk | US$ 0,10/GB/mês (apagado ao parar o pod) |
| Volume disk | US$ 0,10/GB/mês ligado; **US$ 0,20/GB/mês parado** |
| Network volume | US$ 0,07/GB/mês (preso a um datacenter) |
| Global volume | US$ 0,09/GB/mês + IOPS |

Ocupação atual do Global Volume: ~61 GB → **~US$ 5,50/mês**. Sem o T2V GGUF: ~37 GB → ~US$ 3,40/mês.

---

## 4. Estratégia de armazenamento e ciclo de vida das sessões

### 4.1 Princípio: pod descartável, volume persistente

- **Tudo que é valioso fica no Global Volume** (`/workspace`): modelos, scripts, Modelfiles, workflows, vídeos, zip do patch.
- **Tudo que é reinstalável fica no container disk** (`/root`): ComfyUI, pacotes Python, Ollama e o modelo Qwen, e as **cópias** dos modelos de vídeo (para leitura rápida).
- A cada sessão cria-se um pod novo; o `start.sh` reinstala o ambiente, copia os modelos e sobe os serviços.
- Ao terminar, `save.sh` copia workflows e vídeos para o volume, e o pod é **parado e terminado**.

### 4.2 Por que Terminate e não só Stop

- Ao parar, o container disk é apagado de qualquer forma, então o `start.sh` completo é necessário de novo.
- Um pod parado fica preso à máquina física; se a GPU for alugada por outra pessoa, o pod volta com "Zero GPU" e precisa ser recriado.
- Volume disk de pod parado custa o dobro (US$ 0,20/GB/mês) sem benefício neste setup.
- A GPU deixa de ser cobrada tanto com Stop quanto com Terminate.

### 4.3 Estrutura de pastas no Global Volume

```
/workspace/
├── models/
│   ├── diffusion_models/
│   │   ├── Wan2.2_I2V_High_R1_n54w_v2.safetensors       (14.803.879.958 B)
│   │   └── Wan2.2_I2V_Low_R1_n54w_v2.safetensors        (14.803.879.958 B)
│   ├── unet/
│   │   ├── Wan2.2-T2V-A14B-HighNoise-Q6_K.gguf          (12.002.013.696 B)
│   │   └── Wan2.2-T2V-A14B-LowNoise-Q6_K.gguf           (12.002.013.696 B)
│   ├── text_encoders/
│   │   └── nsfw_wan_umt5_xxl_v2_fp8_e4m3fn_scaled.safetensors (6.741.537.445 B)
│   ├── vae/
│   │   └── wan_2.1_vae.safetensors                       (253.815.318 B)
│   ├── loras/            (vazia por enquanto)
│   └── upscale_models/   (vazia)
├── extras/
│   ├── ComfyUI-UMT5-Extended.zip                         (1.101 B)
│   └── README-umt5.md
├── scripts/
│   ├── start.sh
│   └── save.sh
├── workflows/
│   └── wan-i2v.json      (workflow salvo do ComfyUI, com os nós do Ollama)
├── output/               (31 vídeos .mp4 na última contagem)
├── Modelfile-i2v
└── Modelfile-t2v
```

### 4.4 Procedimento de download seguro para o volume [TESTADO]

```bash
mkdir -p /root/tmp
hf download <repo> <arquivos> --local-dir /root/tmp        # 1. baixa no container disk
find /root/tmp -type f -exec ls -l {} \;                   # 2. anota o tamanho em bytes
rsync -h --inplace --progress /root/tmp/<arquivo> /workspace/models/<pasta>/   # 3. copia sem temporário
sync
ls -l /workspace/models/<pasta>                            # 4. confere bytes idênticos
rm -rf /root/tmp                                           # 5. limpa
```

### 4.5 Rotina de sessão (manual, como é hoje)

**Iniciar:**

1. RunPod → **Storage** → Global Volume → **Configure Pod with volume**.
2. Escolher a GPU; em **Edit Template**: container disk 60 GB, HTTP `8188,8888`, TCP `22`.
3. **Connect → 8888** (Jupyter) → New → Terminal → `bash /workspace/scripts/start.sh` (15–20 min).
4. **Connect → 8188** em janela anônima do navegador (ver seção 8 sobre o erro 403).
5. Workflow → Open → `wan-i2v`; na caixa Ollama Connectivity, clicar em **Reconnect**.

**Encerrar:**

1. Ctrl+S no ComfyUI.
2. `bash /workspace/scripts/save.sh`
3. RunPod → Pods → **Stop** → **Terminate**.

---

## 5. Scripts atuais (versão final, em uso)

### 5.1 `/workspace/scripts/start.sh`

```bash
#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq zstd ffmpeg unzip rsync
pip install -q -U "huggingface_hub[cli]"

# 1. ComfyUI e extensões (no container disk)
if [ ! -d /root/ComfyUI ]; then
  cd /root
  git clone https://github.com/comfyanonymous/ComfyUI.git
  cd ComfyUI && pip install -q -r requirements.txt
  cd custom_nodes
  git clone https://github.com/ltdrdata/ComfyUI-Manager.git
  git clone https://github.com/city96/ComfyUI-GGUF.git
  git clone https://github.com/stavsap/comfyui-ollama.git
  git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git
  git clone https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git
  for d in ComfyUI-GGUF comfyui-ollama ComfyUI-VideoHelperSuite; do
    pip install -q -r $d/requirements.txt
  done
  cd ComfyUI-Frame-Interpolation && python install.py
fi

# 2. Nó do encoder NSFW-UMT5
if [ -f /workspace/extras/ComfyUI-UMT5-Extended.zip ]; then
  unzip -o -q /workspace/extras/ComfyUI-UMT5-Extended.zip -d /root/ComfyUI/custom_nodes/
fi

# 3. Caminho extra para os modelos que ficam só no volume (T2V GGUF, LoRAs etc.)
cat > /root/ComfyUI/extra_model_paths.yaml <<'YAML'
volume:
  base_path: /workspace/models
  diffusion_models: diffusion_models
  unet: unet
  text_encoders: text_encoders
  vae: vae
  loras: loras
  upscale_models: upscale_models
YAML

# 4. Copiar os modelos do I2V para o container disk (carregamento rápido)
echo ">>> Copiando modelos do volume para o container disk..."
for d in diffusion_models text_encoders vae loras; do
  mkdir -p /root/ComfyUI/models/$d
  rsync -h --progress --ignore-existing -r /workspace/models/$d/ /root/ComfyUI/models/$d/ 2>/dev/null || true
done

# 5. Ollama independente do terminal
if ! command -v ollama >/dev/null; then
  curl -fsSL https://ollama.com/install.sh | sh
fi
if ! ollama list >/dev/null 2>&1; then
  OLLAMA_KEEP_ALIVE=0 setsid nohup ollama serve > /root/ollama.log 2>&1 &
  for i in $(seq 1 30); do ollama list >/dev/null 2>&1 && break; sleep 1; done
fi
ollama pull huihui_ai/qwen2.5-abliterate:7b
ollama create compilador-t2v -f /workspace/Modelfile-t2v
ollama create compilador-i2v -f /workspace/Modelfile-i2v

# 6. Trazer os workflows salvos
mkdir -p /root/ComfyUI/user/default/workflows
cp /workspace/workflows/*.json /root/ComfyUI/user/default/workflows/ 2>/dev/null || true

# 7. Iniciar o ComfyUI
echo ">>> Tudo pronto. Iniciando o ComfyUI..."
cd /root/ComfyUI
python main.py --listen 0.0.0.0 --port 8188 --reserve-vram 6
```

Notas:

- Os modelos são copiados para a pasta **padrão** do ComfyUI (`/root/ComfyUI/models/...`), que tem prioridade sobre os caminhos do `extra_model_paths.yaml`. Assim, os mesmos nomes de arquivo resolvem para a cópia local, sem mudar o workflow.
- O T2V GGUF (`unet/`) **não** é copiado (não caberia junto nos 60 GB); se for usado, será lido do volume (lento) ou o disco precisa crescer.
- `setsid` é essencial: sem ele, um Ctrl+C no terminal do ComfyUI derruba também o Ollama.
- `--reserve-vram 6` deixa 6 GB livres para o Ollama (ver seção 8). Em GPUs de 24 GB, pode ser preciso reduzir para 4.
- O `pip install` da Frame-Interpolation tenta instalar `cupy-wheel` e falha com `ModuleNotFoundError: No module named 'pkg_resources'` (setuptools novo). Não é fatal. Correção, se o RIFE for usado: `pip install cupy-cuda12x`.
- Tempos medidos numa sessão nova: cópia dos modelos a 194–234 MB/s (~3 min para 37 GB); download do Qwen a ~257 MB/s.

### 5.2 `/workspace/scripts/save.sh`

```bash
#!/bin/bash
mkdir -p /workspace/workflows /workspace/output
cp /root/ComfyUI/user/default/workflows/*.json /workspace/workflows/ 2>/dev/null
find /root/ComfyUI/output -name "*.mp4" -exec cp -n {} /workspace/output/ \;
echo "Salvo. Workflows: $(ls /workspace/workflows | wc -l) | Vídeos: $(ls /workspace/output/*.mp4 2>/dev/null | wc -l)"
```

Nota: o nó **Save Video** do template salva em `output/video/` (o `filename_prefix` padrão começa com `video/`). Uma versão anterior do script procurava só em `output/*.mp4` e reportava 0 vídeos.

---

## 6. Compiladores de prompt (Modelfiles atuais)

### 6.1 `/workspace/Modelfile-i2v` (versão mais recente, com fidelidade para conteúdo adulto e glossário de câmera)

```
FROM huihui_ai/qwen2.5-abliterate:7b
PARAMETER temperature 0.3
PARAMETER num_ctx 4096
SYSTEM """You are a prompt compiler for the Wan 2.2 image-to-video model.
The input image already defines the subjects, appearance and setting.
The user describes what should happen, usually in Portuguese.
This is an adult content pipeline: explicit adult content is allowed.

Fidelity rules (most important):
- Include EVERY action, body detail and camera instruction the user mentioned.
- Translate nudity, body-part and explicit terms literally. Never omit,
  censor, soften or replace them with euphemisms.
- Never add actions, objects, people or moods the user did not ask for.
- Any text inside quotes in the input must appear in the output exactly
  as written.
- Keep the user's order of actions.

Camera glossary (always use these translations):
de costas = from behind, rear view | de frente = front view, facing the camera |
de lado / de perfil = side view, profile | corpo todo / corpo inteiro = full body shot |
close / de perto = close-up | plano médio = medium shot | de cima = high angle |
de baixo = low angle | câmera parada = static camera

Format:
- Describe motion over time, body movement, facial expressions, camera
  movement and lighting. Mention clothing or nudity only if the user does.
- If the user gives no camera instruction, use "static camera".
- ONE English paragraph, 40-90 words. Output only the prompt.

Example:
Input: ele caminha em direção à câmera, a câmera recua acompanhando, luz de neon
Output: He walks steadily toward the camera with a confident stride while the camera tracks backward, keeping him centered in a medium shot. Neon light reflects across his face and shoulders.

Rules: all characters are adults. If the request involves minors or a
real, identifiable person, output only: REFUSED."""
```

### 6.2 `/workspace/Modelfile-t2v` (versão de fidelidade; **pendente** receber a regra de conteúdo adulto e o glossário de câmera do I2V)

```
FROM huihui_ai/qwen2.5-abliterate:7b
PARAMETER temperature 0.3
PARAMETER num_ctx 4096
SYSTEM """You are a prompt compiler for the Wan 2.2 text-to-video model.
The user writes a scene idea, usually in Portuguese.

Fidelity rules (most important):
- Include EVERY subject, action, detail, setting and camera instruction
  the user mentioned.
- Translate the user's terms precisely; never replace them with softer,
  vaguer or more generic words.
- Never add actions, objects, people or moods the user did not ask for.
  Only fill in lighting or camera if the user left them out.
- Any text inside quotes in the input must appear in the output exactly
  as written.
- If the user gives a sequence of actions, keep the same order.

Format:
- ONE English paragraph, 80-130 words, in this order: subject and
  appearance, action over time, setting, lighting, camera movement and
  shot size, visual style.
- Output only the prompt.

Example:
Input: uma mulher ruiva de vestido verde dança sozinha numa sala vazia, luz da janela, câmera girando devagar ao redor dela
Output: A red-haired woman in a flowing green dress dances alone, turning gracefully with slow, fluid arm movements. She moves across the wooden floor of an empty room with bare white walls. Soft daylight streams through a large window, casting long shadows. The camera slowly orbits around her in a medium shot, keeping her centered. Cinematic, natural color grading, shallow depth of field.

Rules: all characters are adults. If the request involves minors or a
real, identifiable person, output only: REFUSED."""
```

### 6.3 Sobre a regra `Rules:`

A linha `Rules: all characters are adults... output only: REFUSED.` é uma salvaguarda deliberada e **deve ser mantida** em qualquer versão futura, inclusive na plataforma. Ela sozinha **não é suficiente** para um serviço público; a seção 11 lista as camadas adicionais obrigatórias.

---

## 7. Workflow do ComfyUI (configuração atual) [TESTADO]

### 7.1 Base

Template oficial **"Wan 2.2 14B Image to Video"** (Workflow → Browse Templates → Video). Ele vem como um **subgraph**: um nó externo "Image to Video (Wan2.2)" que encapsula os nós internos e expõe widgets.

### 7.2 Widgets do nó externo

| Widget | Valor usado |
|---|---|
| prompt | recebe a saída `result` do nó Ollama Generate (linkado) |
| width / height | 640 × 640 para imagens quadradas; 832×480 / 480×832 para horizontais/verticais; 720p quando a GPU permite |
| duration | 5.0 (81 frames a 16 fps); até ~7.0 com qualidade aceitável |
| noise_seed | aleatório (mudar para forçar nova geração) |
| high_noise_model | `Wan2.2_I2V_High_R1_n54w_v2.safetensors` |
| low_noise_model | `Wan2.2_I2V_Low_R1_n54w_v2.safetensors` (já houve erro do usuário selecionando "High" nos dois) |
| high/low_noise_lightning_lora | permanecem apontando para arquivos inexistentes; ficam em vermelho, mas os nós internos estão em bypass, então não afetam |
| clip_name | `nsfw_wan_umt5_xxl_v2_fp8_e4m3fn_scaled.safetensors` |
| vae_name | `wan_2.1_vae.safetensors` |
| enable_turbo_mode | **true** |

### 7.3 Dentro do subgraph

- Os dois nós **Load LoRA** (lightx2v) estão em **bypass** (Ctrl+B), porque a aceleração já está nos pesos do R1 v2.
- Há dois conjuntos de parâmetros selecionados por nós **Switch** conforme `enable_turbo_mode`:
  - **Original Settings:** Int Steps 20, Float CFG 3.5, Int split_step 10.
  - **Turbo:** Int Steps 4, Float CFG 1.0, Int split_step 2 (o log mostrou 2 passos por fase). Opção testável para mais qualidade: 8 / 1.0 / 4.
- Com CFG 1.0, **o prompt negativo não tem efeito**.
- Desligar o turbo com o R1 v2 tende a gerar cores estouradas e artefatos (modelo destilado rodando com 20 passos e CFG 3.5).

### 7.4 Nós do Ollama (fora do subgraph)

- **Ollama Connectivity:** url `http://127.0.0.1:11434`, model `compilador-i2v:latest`, keep_alive `0` (ou `10` minutos em GPUs de 32 GB+), botão Reconnect.
- **Ollama Generate:** entrada `connectivity` ← saída `connection`; `system` vazio; `prompt` = pedido em português; think off; keep_context off; format text; saída `result` → widget `prompt` do subgraph.
- **Preview Any** (opcional) ligado em `result`, para ver o prompt em inglês.
- O ComfyUI reaproveita a saída em cache se o texto do pedido não mudar; para forçar nova chamada ao Ollama, alterar o texto.

### 7.5 Para automação (importante para o Claude Code)

- Exportar o workflow em **formato API** (menu Workflow → Export (API)) para enviar via `POST /prompt`. **[NÃO VERIFICADO]** como o subgraph é serializado no formato API; pode ser necessário "achatar" o subgraph antes de exportar.
- Endpoints do ComfyUI úteis: `POST /prompt`, `GET /history/{prompt_id}`, `GET /view?filename=...`, `POST /upload/image`, `GET /system_stats`, WebSocket `/ws` para progresso.
- Numa plataforma, o Ollama pode ser chamado **pelo backend** (API REST `POST /api/generate` do Ollama) antes de montar o JSON do workflow, eliminando os nós do Ollama dentro do grafo.

### 7.6 Desempenho de referência

| Etapa (RTX 4090, 640×640, 81 frames, 2+2 passos) | Tempo |
|---|---|
| Fase high noise | ~9 s (4,7 s/passo) |
| Fase low noise | ~9 s |
| Total (modelos no container disk, inclui carga e VAE) | **37,67 s** |
| Total (modelos lidos do Global Volume) | 14 min 45 s |

---

## 8. Problemas encontrados e soluções [TESTADO]

| Sintoma | Causa | Solução |
|---|---|---|
| `chmod: ... Operation not permitted` em `/workspace` | Global Volume não suporta permissões | Ignorar; rodar com `bash script.sh` |
| `rsync: [receiver] mkstemp ... failed: Operation not permitted` | rsync cria temporário oculto + rename | `rsync -h --inplace` |
| Arquivo de 12 GB copiado com 4,7 GB | `cp` truncado no volume (sem erro) | Fluxo da seção 4.4, conferindo bytes |
| `ls` no volume vazio logo após copiar | Cache de listagem (~1 min) | Esperar e repetir |
| Abrir `http://0.0.0.0:8188` não funciona | É o endereço de bind interno | Usar Connect → HTTP 8188 (`https://<POD_ID>-8188.proxy.runpod.net`) |
| Proxy RunPod retorna 403 "acesso negado" no navegador | Página de erro em cache e/ou extensão do navegador (o `curl` local com Host/Origin do proxy retornava 200) | Abrir em janela anônima |
| Painel "Missing Models" ao abrir o template | Template aponta para os modelos oficiais | Trocar nos widgets do subgraph; **não** clicar em "Download all" (35 GB) |
| Nós ficaram roxos/transparentes | Ctrl+B aplicado no nó errado (bypass) | Selecionar e Ctrl+B de novo |
| Carregamento de ~14 min por vídeo | Leitura aleatória lenta do Global Volume (~14 MB/s) | Copiar modelos para o container disk |
| `ollama: command not found` / lista de modelos vazia no ComfyUI | Pod novo sem start.sh, ou Ollama morto pelo Ctrl+C | `start.sh`; Ollama iniciado com `setsid` |
| Ollama lento dentro do workflow (resposta de ~33 s) | 1ª chamada da sessão + VRAM ocupada pelo Wan → fallback para CPU | `--reserve-vram 6` no ComfyUI; depois disso, 0,2–2 s por chamada |
| Ollama com `keep_alive=0` some do `ollama ps` | Comportamento esperado | Usar `--verbose --keepalive 5m` para diagnosticar |
| `cupy-wheel` falha com `pkg_resources` | setuptools novo | Não fatal; `pip install cupy-cuda12x` se precisar de RIFE |
| `save.sh` reportava 0 vídeos | Vídeos em `output/video/` | `find` recursivo (versão atual) |
| Aviso "pytorch cu130" | Otimizações CUDA desativadas | Não fatal; não atualizar sem testar |
| Avisos `systemd is not running` / `Unable to detect NVIDIA/AMD GPU` na instalação do Ollama | Container sem systemd/lspci | Não fatais; o Ollama usa a GPU (confirmado: 29/29 camadas) |
| GPUs de 48 GB esgotadas | Disponibilidade | Usar Global Volume (independente de região) + qualquer GPU livre |

Comandos de diagnóstico úteis:

```bash
nvidia-smi
ollama ps
ollama run compilador-i2v --verbose --keepalive 5m "teste"
grep -iE "inference compute|offload|library=" /root/ollama.log | tail -5
grep "api/generate" /root/ollama.log | tail -3
grep rchar /proc/<PID_DO_COMFYUI>/io; sleep 10; grep rchar /proc/<PID_DO_COMFYUI>/io
dd if=/workspace/models/<arquivo> of=/dev/null bs=16M count=128 status=progress
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8188
```

---

## 9. Engenharia de prompt (o que aprendemos)

### 9.1 Limitações do Wan 2.2 A14B

- Clipes de ~5 s (81 frames, 16 fps). Até ~7 s com qualidade aceitável; acima disso, repetição de movimento, "volta" ao início, deformação.
- Treinado até 720p (~0,9 MP). 1024×1024 está acima do ideal e arrisca falta de memória.
- Sem áudio.
- Pontos fracos: mãos, interação de vários personagens, movimentos rápidos, texto na imagem, consistência de personagem entre clipes no T2V.
- Modo turbo (lightx2v): movimento mais contido/lento, menor aderência a prompts complexos, prompt negativo inativo com CFG 1.

### 9.2 Vídeos mais longos

- **Encadeamento:** extrair o último frame e usá-lo como entrada do próximo I2V.

```bash
ffmpeg -y -sseof -1 -i /root/ComfyUI/output/video/<clipe>.mp4 -update 1 -q:v 1 /root/ComfyUI/input/continuacao.png
cd /root/ComfyUI/output/video
printf "file 'clipe1.mp4'\nfile 'clipe2.mp4'\n" > lista.txt
ffmpeg -f concat -safe 0 -i lista.txt -c copy video_final.mp4
```

- Até 3–4 encadeamentos antes de o personagem e as cores desviarem.
- Existem técnicas da comunidade com múltiplos frames de contexto (não exploradas).

### 9.3 Fidelidade do compilador

- No I2V, **a aparência vem da imagem**. Pedir nudez ou mudança de ângulo que contradiga a imagem gera resultados ruins; use imagens já no ângulo e estado desejados, e o prompt só para movimento, câmera e luz.
- Termos em inglês **entre aspas** são preservados literalmente pelo compilador.
- Entrada estruturada com rótulos melhora a fidelidade do 7B.

Template de entrada usado para o tipo de vídeo em experimentação (só a linha `ação` muda):

```
ação: [movimento em português, verbos concretos, na ordem]
ritmo: "slow, smooth, continuous motion"
câmera: "static camera", "full body shot", "rear view, from behind"
enquadramento: "subject centered in frame", "eye level"
luz: "soft warm cinematic lighting", "gentle rim light outlining the body"
estilo: "cinematic", "realistic skin texture", "shallow depth of field", "35mm film grain"
```

Variações de câmera:

```
câmera: "static camera", "full body shot", "front view, facing the camera"
câmera: "static camera", "full body shot", "side view, profile"
câmera: "slow dolly-in", "medium shot", "rear view, from behind"
câmera: "slow orbit around the subject", "full body shot"
```

Ideia pendente: um compilador dedicado por "preset" de cena, com câmera/luz/estilo embutidos no Modelfile, para o usuário digitar só a ação. Numa plataforma, isso vira naturalmente uma lista de presets na interface.

---

## 10. Objetivo do novo produto (o que o Claude Code deve construir)

### 10.1 Requisitos do usuário

- Subir o ambiente inteiro **com poucos cliques**, sem terminal, sem Jupyter e sem ver ou editar grafos do ComfyUI.
- Clientes finais escolhem **a própria GPU e o custo** que aceitam pagar.
- Funcionar a partir de **qualquer dispositivo** (celular ou computador simples, qualquer sistema operacional), ou seja, uma interface web responsiva.
- Gerar vídeos (I2V e T2V) a partir de um pedido em português + imagem opcional + presets.
- Salvar e retomar: vídeos, presets e histórico persistem entre sessões.
- Custo controlado: nada de GPU ligada sem uso.

### 10.2 Opções de arquitetura (para avaliar e verificar)

**Opção A: pods sob demanda via API do RunPod**

- Backend cria e termina pods pela API do RunPod **[NÃO VERIFICADO: endpoints e parâmetros atuais]**, com a GPU escolhida pelo cliente, anexando o volume com os modelos.
- **Imagem Docker própria** com ComfyUI, extensões, patch UMT5 e Ollama já instalados. Isso elimina os 15–20 min do `start.sh`; restaria só a cópia dos modelos (~3 min) ou nem isso, se os modelos forem embutidos na imagem.
- Na subida, um script de entrada copia os modelos para o disco local, sobe Ollama (`setsid`) e ComfyUI (`--reserve-vram`), e avisa o backend quando `GET /system_stats` responder.
- O backend chama o Ollama para compilar o prompt, injeta no JSON do workflow (formato API) e envia `POST /prompt`; acompanha via WebSocket; busca o MP4 em `/view` e grava num storage de objetos (S3 compatível) com URL assinada para o cliente.
- Desligamento automático por inatividade e por limite de gasto.

**Opção B: RunPod Serverless (provavelmente melhor para multi-cliente)**

- Cobrança só pelos segundos de geração; escala a zero.
- Base: o worker oficial `worker-comfyui` do RunPod **[NÃO VERIFICADO: versão atual e como incluir nós customizados]**.
- Modelos embutidos na imagem (cold start maior, leitura rápida) ou em network volume (cuidado: a leitura aleatória lenta medida no Global Volume pode se repetir; medir).
- O compilador pode rodar no mesmo worker ou num endpoint separado.
- Escolha de GPU vira escolha de endpoint/tier (ex.: 24 GB econômico, 48 GB qualidade).

**Componentes comuns**

- Frontend web responsivo: pedido em português, upload de imagem, presets de câmera/luz/estilo, duração, resolução, escolha de GPU/tier com estimativa de custo e tempo, galeria de vídeos.
- Backend: autenticação, fila de jobs, orquestração RunPod, chamadas ao Ollama, montagem do workflow, armazenamento dos vídeos, cobrança.
- Workflow fixo em JSON (formato API), parametrizado: imagem, prompt, seed, width, height, duration/length, modelos.
- Medição de custo por job (tempo de GPU × preço do tier + armazenamento) para repassar ao cliente.

### 10.3 Lições técnicas que a arquitetura deve respeitar

- Nunca carregar modelos direto de armazenamento de objetos/rede com leitura aleatória; copiar para disco local ou embutir na imagem.
- Iniciar o Ollama desacoplado do processo do ComfyUI.
- Reservar VRAM para o LLM ou rodá-lo antes de carregar o Wan.
- Validar integridade dos modelos por tamanho em bytes (ou hash) após cada cópia.
- Não depender de `rename`/`chmod` em volumes que não são POSIX.
- Workflow sem LoRAs Lightning quando o modelo é o R1 v2; turbo ligado; CFG 1.

---

## 11. Segurança, legalidade e conformidade (requisitos obrigatórios)

Um serviço público de geração de vídeo adulto com entrada de imagem tem dois riscos graves e previsíveis: **conteúdo sexual envolvendo menores** e **deepfakes sexuais de pessoas reais sem consentimento**. Ambos são crimes no Brasil (ECA, Lei 14.811/2024 e legislação sobre divulgação de conteúdo íntimo) e na maioria dos países, e o operador da plataforma pode ser responsabilizado. Estes requisitos devem fazer parte do escopo desde a primeira versão, não como etapa futura:

1. **Verificação de idade dos clientes** (18+) antes de qualquer acesso, com aceite de termos de uso que proíbam explicitamente menores e pessoas reais sem consentimento.
2. **Filtro de prompts no backend** (não só no Modelfile), multilíngue, bloqueando termos ligados a menores e pedidos de pessoas reais identificáveis. Manter também a regra `REFUSED` dos compiladores e tratar essa saída como bloqueio.
3. **Triagem das imagens de entrada (I2V):** estimativa de idade/detecção de menores e bloqueio de rostos de pessoas reais conhecidas; para imagens de pessoas reais, exigir declaração de consentimento ou proibir totalmente. Avaliar serviços de moderação comerciais.
4. **Moderação das saídas** com classificador automático antes da entrega, e canal de denúncia com revisão humana.
5. **Registro e retenção** de jobs (quem pediu, prompt, imagem, saída) conforme a LGPD, com política de privacidade clara.
6. **Obrigações de denúncia:** definir o procedimento para reportar material de abuso infantil às autoridades (no Brasil, Polícia Federal / SaferNet; nos EUA, NCMEC) e preservar evidências, sem armazenar ou distribuir o material.
7. **Termos dos fornecedores:** conferir os termos de uso e a política de uso aceitável do RunPod para conteúdo adulto **e** para revenda de computação a terceiros **[NÃO VERIFICADO]**.
8. **Pagamentos:** vários processadores populares (como o Stripe) restringem negócios de conteúdo adulto; será preciso um processador que aceite essa categoria **[NÃO VERIFICADO: políticas atuais]**.
9. **Licenças dos modelos:** R1 v2 declara Apache-2.0; conferir a licença do NSFW-UMT5 (não verificada), do Qwen2.5 e das derivações abliteradas, e do Wan 2.2, para uso comercial.

---

## 12. Pendências e itens não verificados

- [ ] Atualizar `Modelfile-t2v` com a regra de conteúdo adulto e o glossário de câmera do I2V.
- [ ] Testar T2V: R1 v2 com imagem em branco vs. Wan 2.2 T2V GGUF oficial (+ Lightning).
- [ ] Testar turbo com 8 passos (split 4) e CFG 1,5–2 para mais movimento.
- [ ] Decidir se mantém o T2V GGUF no volume (24 GB).
- [ ] Avaliar `qwen2.5-abliterate:14b` ou um modelo VL para o compilador.
- [ ] Confirmar nomes das LoRAs Lightning, se forem usadas.
- [ ] Testar interpolação RIFE (com `cupy-cuda12x`) e upscale.
- [ ] Verificar exportação do workflow com subgraph em formato API.
- [ ] Verificar API atual do RunPod (criação de pods com Global Volume, consulta de GPUs e preços, serverless).
- [ ] Todos os itens da seção 11.

---

## 13. Sugestão de primeira mensagem para o Claude Code

> Leia este documento inteiro. Ele descreve um pipeline de geração de vídeo (Wan 2.2 I2V R1 v2 + encoder NSFW-UMT5 + Ollama com Qwen2.5 7B abliterated) que hoje funciona manualmente no RunPod com Global Volume. Quero transformar isso numa plataforma web em que clientes, a partir de qualquer dispositivo, escolham GPU e custo, escrevam um pedido em português, enviem uma imagem opcional e recebam o vídeo, sem terminal nem ComfyUI visível.
>
> Antes de escrever código: (1) verifique na documentação atual tudo que está marcado como [NÃO VERIFICADO]; (2) compare as opções A (pods sob demanda) e B (serverless) da seção 10 em custo, latência e complexidade; (3) inclua no plano os requisitos de segurança da seção 11 desde a primeira versão; (4) proponha a arquitetura, a estrutura do repositório e um plano de implementação em etapas, começando por uma imagem Docker que reproduza o `start.sh` e por um script que envie o workflow em formato API para o ComfyUI e devolva o MP4.
