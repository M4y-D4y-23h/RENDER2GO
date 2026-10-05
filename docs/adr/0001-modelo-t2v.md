# ADR 0001 — Modelo de texto → vídeo (T2V)

- **Status:** proposto. A decisão depende do bake-off em GPU (não há GPU no ambiente de desenvolvimento). A regra de decisão abaixo foi fixada **antes** das medições, para que o resultado decida e não a preferência de quem olha os vídeos.
- **Data:** 05/10/2026
- **Etapa:** 2 ([plano](../04-plano-de-implementacao.md))

## Contexto

O I2V de produção usa o finetune Wan 2.2 I2V R1 v2 (sem LoRA, turbo de 4 passos). Para o modo texto → vídeo, a especificação (seções 2.4 e 12) deixou duas opções em aberto: o próprio R1 v2 sem imagem, ou o Wan 2.2 T2V A14B oficial com as LoRAs Lightning. O GGUF Q6_K do T2V já está no volume do pipeline manual (24 GB).

O que pesa na escolha:

- **Qualidade:** aderência ao prompt e movimento. O R1 v2 foi treinado para animar uma imagem; sem ela, a qualidade é desconhecida.
- **Operação:** cada GB a mais na imagem do worker aumenta o cold start e o custo de registry (doc 02). Um conjunto de modelos a mais também aumenta o que precisa ser conferido, copiado e testado.
- **Dependências:** o GGUF exige o nó `ComfyUI-GGUF` (Apache-2.0), que a imagem de produção não tem hoje.

## Candidatos

Gerados por `tools/build_t2v_templates.py` em `worker/workflows/experimental/`. Os três usam o mesmo encoder NSFW-UMT5, VAE, duração, fps e saída do I2V.

| Variante | Template | Modelos extras | Nós extras |
|---|---|---|---|
| `r1v2-sem-imagem` | `t2v_r1v2_noimg.v1` | nenhum | nenhum |
| `a14b-q6k-lightning` | `t2v_a14b_q6k_lightning.v1` | +26,5 GB (2 × 12,0 GB + LoRAs) | ComfyUI-GGUF `6ea2651e` |
| `a14b-fp8-lightning` | `t2v_a14b_fp8_lightning.v1` | +31,0 GB (2 × 14,3 GB + LoRAs) | nenhum |

**R1 v2 sem imagem:** sem `start_image`, o `WanImageToVideo` não envia imagem de condicionamento e o modelo recebe máscara 1 em todos os quadros (`comfy/model_base.py`, `concat_cond`), ou seja, gera tudo do zero. Uma imagem "em branco" de verdade fixaria o primeiro quadro nela, por isso não entrou.

**A14B + Lightning:** LoRAs T2V v1.1 com força 1,0, 4 passos, troca no passo 2, CFG 1, shift 5, euler/simple. São os valores do workflow de referência do lightx2v. Os arquivos da Comfy-Org têm o mesmo SHA-256 dos originais (doc 01, item 7).

**Já verificado sem GPU (05/10/2026):** os três templates passam pela validação completa do `/prompt` do ComfyUI v0.38.0 com o ComfyUI-GGUF e o patch UMT5 (classes, entradas e valores). Só falharam ao carregar arquivos de modelo vazios, como esperado. Todos os modelos estão no `models.lock.json` com bytes e SHA-256.

## Procedimento

Num pod com GPU de 24 GB (RTX 4090, o tier mais barato) e o Global Volume:

```bash
docker build -f worker/Dockerfile --target bakeoff -t gpu2go-worker:bakeoff worker
docker run --gpus all -e MODEL_SET=i2v_r1v2,t2v_a14b_q6k,t2v_a14b_fp8 \
  -e MODELS_FROM_DIR=/workspace/models -e RESERVE_VRAM_GB=4 ... gpu2go-worker:bakeoff
python tools/experiment.py worker/evals/t2v-bakeoff.v1.json --out saida/bakeoff
```

- O plano `worker/evals/t2v-bakeoff.v1.json` tem 6 prompts SFW (chuva à noite, cavalo na praia, mãos com argila, xadrez no parque, letreiro de neon com texto, floresta aérea), 2 seeds e 832×480, 5 s. São 36 vídeos.
- Os prompts já estão em inglês, para que o compilador não entre na comparação.
- A grade `index.html` é cega: cada vídeo aparece só com um código, em ordem embaralhada por linha.
- Duas pessoas, sem combinar, dão notas de 1 a 5 em `ratings.csv` para três critérios: aderência ao prompt, qualidade do movimento e ausência de artefatos.
- `python tools/experiment.py worker/evals/t2v-bakeoff.v1.json --out saida/bakeoff --score notas-a.csv notas-b.csv` junta as notas à chave e grava `scores.json`.

## Regra de decisão (fixada antes das medições)

1. **Eliminatório:** cai a variante que falhar em qualquer vídeo, que passar de 20 GB de pico de VRAM na RTX 4090 (é preciso deixar 4 GB para o Ollama) ou cuja mediana quente passar de 60 s a 832×480, 5 s.
2. **Preferência pelo mais simples:** se a média geral do `r1v2-sem-imagem` ficar a até **0,3 ponto** da melhor variante A14B, escolher o R1 v2. Não traz modelos nem nós novos, e o tier T2V usa a mesma imagem do I2V.
3. **Entre os A14B:** se o fp8 ficar a até **0,2 ponto** do Q6_K, escolher o fp8 (nós nativos, sem ComfyUI-GGUF). Caso contrário, o Q6_K.
4. Empate dentro das margens: vence a menor mediana quente.

## Consequências

- **Se vencer o R1 v2:** o template sai de `experimental/` para `workflows/` como `t2v_r1v2.v1`, com o perfil `t2v` do compilador. Os conjuntos `t2v_a14b_*` saem do lock e os 24 GB do GGUF podem sair do volume (decisão pendente da seção 12 da especificação).
- **Se vencer um A14B:** o T2V ganha uma imagem própria (`full-t2v`) para não pesar o cold start do I2V. Os endpoints de I2V e T2V ficam separados por tier (doc 02, seção 7). Se for o Q6_K, o ComfyUI-GGUF entra na imagem de produção com o commit fixado e revisão do código.
- Nos dois casos, o critério de aceite da Etapa 2 ("o template T2V escolhido gera 5 s") é conferido pelo próprio bake-off. Este ADR passa a "aceito" com a tabela de resultados preenchida.

## Resultados

_Pendente: preencher com `summary.json` e `scores.json` do bake-off._

| Variante | Média geral | Aderência | Movimento | Artefatos | Mediana quente (s) | Pico VRAM (GB) | GB extras |
|---|---|---|---|---|---|---|---|
| r1v2-sem-imagem | | | | | | | 0 |
| a14b-q6k-lightning | | | | | | | 26,5 |
| a14b-fp8-lightning | | | | | | | 31,0 |
