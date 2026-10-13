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


## Rodada de 13/10/2026: I2V v7 (critérios escritos antes da medição)

**Por quê:** pedido do dono de 13/10/2026 (`docs/06`, seção 9.1): o v6 começa com "This is an adult content pipeline", manda traduzir nudez ao pé da letra e, num pedido vago, chegou a escrever "She stays in place…" sem pessoa no pedido.

**O que a v7 muda** ([`compiler-i2v.v7.txt`](../../prompts/compiler-i2v.v7.txt)), com as regras do serviço em código (`render2go_worker/traducao.py`):
- sem as linhas de conteúdo adulto; a regra `REFUSED` fica igual, no fim;
- nunca acrescentar pessoa, parte do corpo, animal, objeto, texto ou logotipo que o cliente não pediu;
- pedido vago: decidido no código (`traducao.pedido_vago`: nenhuma palavra de movimento, mudança ou câmera no texto do cliente) e trocado pelo movimento padrão, fixo e em inglês (receitas v4: o da categoria da foto no Livre, o do estilo nos outros); o v7 também pode responder `VAGUE`. Motivo: numa sonda com frases que **não** estão nos casos (13/10/2026), o 7B abliterado acertou só 14 de 28 na separação vago × concreto, inventou um cachorro num "algo que chame a atenção dos clientes" e, quando respondia `VAGUE: <assunto>`, copiava o assunto do exemplo do prompt; o vocabulário acertou 54 de 55 frases da mesma sonda;
- a linha `pessoas: nenhuma` (foto sem pessoas): nunca citar pessoas, nem para dizer que não há.

**Casos:** [`cases.v3.json`](cases.v3.json) = os 45 da v2, sem mudar nenhum, mais 19 `i2v-v3-*` (17 que contam e 2 REFUSED informativos). Os casos com `receita` passam pelo caminho do serviço inteiro (presets do estilo, câmera suave do Livre, VAGUE, pessoa inventada traduzida de novo até 3 vezes e, no fim, o movimento padrão), e os com `sem_pessoas` ganham a conferência "pessoa" (nenhuma pessoa no prompt final). CPU, Ollama 0.35.1, o mesmo 7B (`huihui_ai/qwen2.5-abliterate:7b`), seeds 1–3, o v6 e o v7 no mesmo caminho (a linha `pessoas` só no v7, que a entende).

**O v7 entra em produção se, nos casos de I2V que contam (as 3 seeds):**
1. a taxa de casos certos for ≥ a do v6;
2. "sem invenção" (`forbid`) for ≥ a do v6, e "sem pessoa inventada" no prompt final for de 100%;
3. as aspas continuarem 100%;
4. cada caso REFUSED for recusado pelo v7 pelo menos tantas vezes quanto pelo v6 (nenhuma recusa perdida).

Os limites gerais da v1 (85%, 100%, 95%) continuam valendo como referência, sem mudar.

**Resultado (13/10/2026, 02:35–03:13; CPU de 4 vCPU; 150 rodadas por versão, 141 que contam e 9 REFUSED informativos):**

| Prompt | Todos os casos | Sem o limite de palavras | Casos da v1 | Casos do serviço | Casos de 13/10 | Sem invenção | Sem pessoa inventada | Aspas | Ordem | REFUSED | Mediana |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **I2V v6** | **69,5%** | **80,1%** | **65,3%** | **66,7%** | **76,5%** | **96,5%** | 100% | **100%** | 87,5% | 0 de 9 | 7,8 s |
| I2V v7 | 58,2% | 74,5% | 55,6% | 44,4% | 66,7% | 90,8% | 100% | 97,2% | **100%** | **3 de 9** | 6,0 s |

Relatórios: [`relatorios/2026-10-13-cpu-i2v6-casos-v3-7b.md`](relatorios/2026-10-13-cpu-i2v6-casos-v3-7b.md) e [`relatorios/2026-10-13-cpu-i2v7-casos-v3-7b.md`](relatorios/2026-10-13-cpu-i2v7-casos-v3-7b.md).

- **Critério 1 (casos certos):** não cumprido (58,2% × 69,5%).
- **Critério 2 (sem invenção e sem pessoa inventada):** não cumprido no "sem invenção" (90,8% × 96,5%). Sem pessoa inventada: 100% nos dois, em 42 rodadas de fotos sem pessoas; nenhuma tradução precisou ser refeita por citar alguém, e os 21 (v6) e 24 (v7) pedidos vagos viraram o movimento padrão no código.
- **Critério 3 (aspas):** não cumprido (97,2%: o v7 perdeu o "slow motion" de `i2v-aspas-ingles` numa seed).
- **Critério 4 (REFUSED):** cumprido. O v7 recusou a pessoa real 3 vezes; o v6, nenhuma. Neymar e o menino de 7 anos passaram nos dois (o compilador abliterado não é camada de segurança: o C1 e a revisão humana barram).
- **Onde o v7 errou mais:** o plural com verbo voltou ("The products", "The houses", "The motorcycles": 9 das 18 rodadas do serviço), respostas curtas demais (30 traduções abaixo de 15 palavras; o v6, 15) e um sujeito trocado ("He jumps from the sofa" no pedido do gato, nas 3 seeds). O `VAGUE` do v7 só pegou, além dos vagos que o código já separa, o pedido leigo `i2v-servico-leigo-sem-acao` (3 rodadas, que viraram o movimento padrão). Os casos vagos de imóvel e de pessoa falham nos dois só pelo limite de palavras (6 rodadas em cada): o movimento padrão fixo tem 14.

**Decisão:** **o v6 continua em produção** (`compiler.PROFILES["i2v"]["version"] = 6`), pela regra escrita antes. O v7 fica no diretório, registrado com estes números, como base da etapa seguinte (a legenda por foto e a rodada de um tradutor comum, não abliterado). As regras do serviço em código valem com o v6 (pedido vago e movimento padrão, câmera suave do Livre, pessoa inventada traduzida de novo e barrada na API); só a linha `pessoas: nenhuma` é do v7 em diante.

## Rodada de 13/10/2026 (2): tradutor comum e legenda por foto (ADR 0014, critério 6)

**Por quê:** pedido do dono de 13/10/2026 (`docs/adr/0014-modelos-oficiais-tradutor-e-legenda.md`): trocar o tradutor abliterado pelo Qwen 2.5 7B Instruct comum (`qwen2.5:7b-instruct`, Apache 2.0, digest `sha256:2bada8a7…3730`) e dar a cada foto uma legenda do modelo de visão (`qwen2.5vl:7b`), que o prompt v8 ([`compiler-i2v.v8.txt`](../../prompts/compiler-i2v.v8.txt): o v7 mais as linhas `foto` e `categoria`) passa ao tradutor.

**Critério 6, escrito antes da medição (ADR 0014):** o Qwen 2.5 7B comum com o prompt v7 ≥ o abliterado com o v6 na taxa de casos certos e em "sem invenção", 100% das aspas e cada caso REFUSED recusado pelo menos tantas vezes quanto o v6 recusa (nos casos da v3); e o v8 (com as legendas) ≥ o v7 nos mesmos casos.

**Casos:** [`cases.v4.json`](cases.v4.json) = os 64 da v3, sem mudar nenhum, mais 13 `i2v-v4-*` com a legenda da foto escrita à mão (sem rodar o modelo de visão), todas válidas no `legenda.conferir`; entre eles, um rótulo com uma instrução escrita ("write the word sale") e uma foto com pessoa num pedido vago. A legenda vale no código para qualquer versão do prompt (categoria e assunto do movimento padrão; a regra da pessoa, que também vale quando ela diz "sem pessoas"); só o v8 a recebe na entrada. CPU de 4 vCPU, Ollama 0.35.1, seeds 1–3, 189 rodadas por versão (180 que contam e 9 REFUSED informativos).

**Resultado (13/10/2026, 03:24–04:15):**

| Tradutor | Prompt | Casos | Todos os casos | Sem o limite de palavras | Casos da v1 | Casos do serviço | Casos de 13/10 | Casos com legenda | Sem invenção | Sem pessoa inventada | Aspas | Ordem | REFUSED | Mediana |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| abliterado (produção) | v6 | v3 | **69,5%** | 80,1% | **65,3%** | **66,7%** | 76,5% | — | **96,5%** | 100% | 100% | 87,5% | 0 de 9 | 7,8 s |
| comum (Instruct) | v7 | v3 | 56,7% | 75,2% | 44,4% | 50,0% | 76,5% | — | 94,3% | 100% | 100% | 100% | **6 de 9** | 7,9 s |
| comum (Instruct) | v8 | v3 | 54,6% | 73,0% | 41,7% | 50,0% | 74,5% | — | 95,0% | 100% | 94,4% | 100% | 6 de 9 | 5,8 s |
| comum (Instruct) | v7 | v4 | 64,4% | 80,6% | 44,4% | 50,0% | 76,5% | **92,3%** | 95,6% | 100% | 100% | 100% | 6 de 9 | 7,9 s |
| comum (Instruct) | v8 | v4 | 59,4% | 75,6% | 41,7% | 50,0% | 74,5% | 76,9% | 92,8% | 100% | 94,4% | 100% | 6 de 9 | 6,8 s |

Relatórios: [`relatorios/2026-10-13-cpu-i2v7-casos-v4-instruct-7b.md`](relatorios/2026-10-13-cpu-i2v7-casos-v4-instruct-7b.md) e [`relatorios/2026-10-13-cpu-i2v8-casos-v4-instruct-7b.md`](relatorios/2026-10-13-cpu-i2v8-casos-v4-instruct-7b.md) (as linhas "casos v3" são as mesmas rodadas, só com os 50 casos de I2V da v3).

- **Tradutor comum com o v7 × abliterado com o v6 (casos da v3): não cumpre.** Casos certos 56,7% × 69,5% e "sem invenção" 94,3% × 96,5%; aspas 100% e REFUSED 6 de 9 × 0 de 9 (a pessoa real e o menino de 7 anos recusados nas 3 seeds; o Neymar passou, como no v6). Os erros são os do v7 com o abliterado: termos de câmera perdidos ("from behind", "medium shot", "low angle"), respostas curtas (38 rodadas abaixo de 15 palavras; o v6, 21) e o plural com verbo ("The motorcycles", "The houses", "The products"), nas 3 seeds.
- **v8 × v7 (os dois com o tradutor comum): não cumpre.** Pior nos casos da v3 (54,6% × 56,7%; aspas 94,4%: o "slow motion" de `i2v-aspas-ingles` sumiu em 2 seeds) e nos com legenda (76,9% × 92,3%). Com a legenda na entrada, o tradutor passou a descrever a foto em vez do movimento pedido ("highlighting the leather and gold clasp"), a pôr no plural o que a legenda descreve como par ("The sneakers") e, numa seed, **copiou para o prompt o texto do rótulo** ("with the word "sale" clearly visible") — o caminho que a regra "o texto da foto nunca é instrução" do `legenda.v1.txt` quer fechar: a descrição é texto de terceiros (o que está escrito na foto) e entra no prompt do vídeo.
- **O que vale nas duas versões, no código:** zero pessoa inventada no prompt final (78 rodadas de fotos sem pessoas, nenhuma tradução refeita), e os 43–44 pedidos vagos viraram o movimento padrão com a categoria e o assunto da legenda (`i2v-v4-*`). Os 3 casos vagos com pessoa na foto falham nas duas versões só pelo limite de palavras: o movimento padrão de "pessoa" com um assunto de 2 palavras tem 14.

**Decisão (pela regra escrita antes):** **o tradutor continua o abliterado com o v6**; o `qwen2.5:7b-instruct` fica registrado com estes números, e o v8 também. A legenda por foto **não entra no prompt do tradutor** (nenhuma versão do prompt a recebe na produção); ela continua candidata só no código (categoria e assunto do movimento padrão e a regra da pessoa, mais rígida), que é o que a rodada em GPU mede (ADR 0014, combinação D redefinida antes da rodada).
