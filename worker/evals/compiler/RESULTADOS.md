# Fidelidade do compilador: resultados (05/10/2026; I2V v6 em 09/10/2026)

Casos: [`cases.v1.json`](cases.v1.json), todos SFW. São 24 casos de I2V e 12 de T2V que contam para a nota, mais 3 informativos da regra `REFUSED`, com 3 seeds por caso. Limites fixados **antes** da primeira rodada: 85% dos casos passando em todos os critérios, 100% do texto entre aspas preservado e 95% sem invenção.

Rodadas:

- RTX 4090 (RunPod, Ollama 0.35.1, 49/49 camadas na GPU): I2V v3 e v4 e T2V v2, no 7B e no 14B. Relatório em [`relatorios/2026-10-05-gpu-4090-…`](relatorios/).
- CPU (Ollama 0.35.1, 7B): I2V v5 e T2V v3, as versões escritas depois da rodada na GPU. O resultado não depende do hardware, só o tempo muda.

## Resultado

O "caminho do produto" são os casos em que o texto livre não traz termos de câmera. No produto, a câmera vem dos presets, cujos termos o código anexa sem passar pelo LLM.

| Modelo | Prompt | Todos os casos | Sem o limite de palavras | Caminho do produto | Produto, sem o limite de palavras | Mediana de palavras |
|---|---|---|---|---|---|---|
| 7B | I2V v3 | 4% | 81% | 7% | 79% | 19 |
| 7B | I2V v4 | 42% | 51% | 60% | 71% | 55 |
| **7B** | **I2V v5** | **50%** | 68% | **60%** | **88%** | 18 |
| 14B | I2V v3 | 19% | 76% | 29% | 90% | 33 |
| 14B | I2V v4 | 38% | 75% | 45% | 81% | 41 |
| 7B | T2V v2 | 6% | 56% | 17% | 67% | 59 |
| **7B** | **T2V v3** | **36%** | 58% | **50%** | 75% | 54 |
| 14B | T2V v2 | 14% | 69% | 25% | 100% | 68 |

Tempo por compilação na RTX 4090: 0,3–0,9 s no 7B e 0,7–1,4 s no 14B, com o modelo já carregado. No envio real com `keep_alive 0`, 3,3 s incluindo a carga.

**Nenhuma configuração atinge o limite de 85% de todos os casos.** O limite continua valendo: não foi trocado depois de ver os números.

## O que os números mostram

1. **Texto longo e fidelidade puxam em sentidos opostos.** O v3, conciso, é fiel (81% sem contar o tamanho) mas fica abaixo do mínimo de 40 palavras que veio do Modelfile antigo. O v4 pede 40–70 palavras e passa a inventar ("medium shot", "slight stretch") e a perder termos (51%). A faixa de 40–90 palavras era uma suposição; nenhum teste mostrou que o Wan precisa dela.
2. **Termos de câmera em texto livre não são confiáveis em nenhum modelo.** "de baixo" virou "high angle" (o oposto), "câmera parada" virou "the camera remains still", e "à noite" virou "evening". Por isso a câmera vai pelos presets, que o código anexa palavra por palavra (`presets.py`). Isso fez o critério "aspas preservadas" ir a 100% nos casos com preset.
3. **O 14B não resolve.** Ganha em alguns recortes e perde em outros, custa ~9 GB de VRAM em vez de ~5 GB e disputa memória com o Wan numa GPU de 24 GB. Fica o 7B.
4. **A regra `REFUSED` não é confiável:** recusou 0 de 3 pedidos com pessoa real no I2V e 3 de 6 no T2V. Confirma o que o doc 03 já exige: as camadas C1–C2 bloqueiam, e o compilador nunca é camada de segurança.
5. **Saída em chinês:** o Qwen trocou de idioma no meio do texto em alguns casos. O compilador refaz com a seed seguinte e falha o job se acontecer de novo.

## Decisão

- **Em produção:** I2V **v5** e T2V **v3** no 7B (`compiler.PROFILES`). São as melhores versões medidas: no caminho do produto, 88% e 75% de fidelidade sem contar o tamanho do texto, contra 79% e 67% das versões anteriores.
- **Critério de aceite da Etapa 2 ("taxa mínima definida e atingida"): não atingido pelo critério original.** Em 06/10/2026 o dono do projeto decidiu aceitar o compilador atual como opção inicial ("não precisa ficar perfeito"); modelos mais especializados devem entrar depois. As opções que estavam na mesa, mantidas como registro:
  - **(a)** Aceitar um critério restrito ao caminho do produto e à fidelidade, sem o limite de palavras. Nele, o I2V v5 passa (88%) e o T2V v3 não (75%). Antes, vale um teste curto em GPU comparando vídeos com prompt curto e longo, para saber se o tamanho do texto importa para o Wan.
  - **(b)** Manter o critério e testar outro compilador, como uma versão abliterada mais nova do Qwen ou um modelo maior fora da GPU do worker.
- Os casos e os limites não mudam; versões novas de prompt entram como arquivos novos e passam pela mesma rodada. A opção (a) segue recomendada como próximo passo quando o compilador voltar a ser prioridade.

## Como reproduzir

```bash
python tools/eval_compiler.py --ollama http://127.0.0.1:11434 \
  --version i2v=3 --version i2v=4 --version i2v=5 \
  --model huihui_ai/qwen2.5-abliterate:7b --out saida/fidelidade
```

## Rodada de 09/10/2026: I2V v6 (serviço)

**Por quê:** no teste gratuito de 08/10, o pedido "crie um vídeo de apresentação desses carros pra cada uma dessas fotos" foi traduzido ao pé da letra, no plural, e 2 dos 5 vídeos trocaram de carro no meio. O texto do cliente vale para cada foto, uma de cada vez, e o v5 não sabia disso.

**O que a v6 acrescenta à v5** ([`compiler-i2v.v6.txt`](../../prompts/compiler-i2v.v6.txt)):
- uma foto, uma cena: ordens ao serviço ("crie um vídeo", "pra cada foto", "para o Instagram") saem, e as palavras video, photo, presentation, cut e transition nunca aparecem;
- o sujeito principal vai no singular ("desses carros" = "the car");
- pedido sem ação ("deixe bonito") vira movimento suave de luz sobre o produto, com a câmera parada;
- tudo em inglês; a linha `continuidade` (presets v3) entra na lista das linhas fixas.

**Casos:** [`cases.v2.json`](cases.v2.json) = os 39 da v1, sem mudar nenhum, mais 6 `i2v-servico-*` (ordem ao serviço, pedido leigo, plural, pedido detalhado, "em cada foto", plural com ação), com os presets do estilo Livre. Nenhum caso repete as palavras dos exemplos do prompt. Limites iguais. CPU, Ollama 0.35.1, 7B, seeds 1–3.

| Prompt | Todos os casos | Sem o limite de palavras | Casos da v1 | Casos do serviço | Sem invenção | Aspas | Mediana |
|---|---|---|---|---|---|---|---|
| I2V v5 | 42% | 59% | 49% | 17% | 83% | 100% | 6,8 s |
| **I2V v6** | **66%** | **74%** | **65%** | **67%** | **94%** | 100% | 5,2 s |

Relatórios: [`relatorios/2026-10-09-cpu-i2v5-casos-v2-7b.md`](relatorios/2026-10-09-cpu-i2v5-casos-v2-7b.md) e [`relatorios/2026-10-09-cpu-i2v6-7b.md`](relatorios/2026-10-09-cpu-i2v6-7b.md).

**Como chegou na v6 final (três rodadas, todas registradas aqui):**
1. O 1º rascunho mandava "The camera slowly pushes in." no pedido sem ação. O 7B passou a escrever isso em quase todo pedido, inclusive com ação, e às vezes junto com "Static camera.": casos que a v5 acertava (câmera parada, de frente, corpo inteiro) caíram. A aproximação saiu do prompt.
2. O código agora remove "Static camera." quando o próprio LLM descreve a câmera se movendo (`compiler.drop_contradictory_static_camera`; só quando a câmera é o sujeito da frase). A v5 de referência rodou antes dessa regra.
3. Os exemplos das regras usam palavras que não estão nos casos (sapatos, bolsas, lojas, vitrine).

**O que ainda falha na v6:**
- "os produtos giram devagar" continua "The products" (0 de 3): o plural com verbo resiste à regra;
- "faróis" sai como "Faros" (0 de 3 no caso detalhado; a v5 traduzia);
- 1 das 18 saídas do serviço escreveu "She" num pedido leigo, sem pessoa no pedido;
- ainda abaixo do limite de 85% de todos os casos, como todas as versões medidas.

**Decisão:** **I2V v6 em produção** (`compiler.PROFILES`), por ganhar em todos os recortes medidos. A conferência do vídeo inteiro (`conferencia.py`) continua sendo a rede de segurança para vídeo que troca de produto, independente do texto.

