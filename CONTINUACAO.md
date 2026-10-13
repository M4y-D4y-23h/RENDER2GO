# Continuação do pedido do dono de 13/10/2026 ("tirar o máximo do Wan 2.2 I2V")

Arquivo temporário, para a próxima sessão continuar de onde esta parou (a sessão foi interrompida a pedido do dono, por falta de cota). **Apagar no último commit do pedido.**

## 0. Como retomar

- Branch `claude/magical-lamport-4rrms9`, sobre `be8dfe8` (a `claude/festive-mccarthy-sc784n` já está incorporada, avanço direto).
- **2ª sessão de 13/10:** o WIP foi separado. O commit da **Fase 1 está feito** (seção 2). Os commits seguintes são da Fase 2 em diante; um commit "WIP Fase N", se houver, é trabalho em andamento dessa fase, para não perder nada numa interrupção: no fim da fase, ele vira o commit único da fase (`git reset --soft` até o commit da fase anterior e um commit só; `git push --force-with-lease`, que reescreve só o que não está na `main`).
- **Não publicar no servidor nada depois do commit da Fase 1** antes do docs/09 da fase dizer como: o conjunto `i2v_oficial` no `models.lock.json` (Fase 2) muda a versão da hibernação (`lote_nuvem.versao_hibernacao`), e os pods hibernados seriam encerrados e refeitos.
- Sem PR: o dono pede quando quiser.
- Contêiner novo começa do zero:
  - Postgres de teste: como no CLAUDE.md, "Ambiente de nuvem". No contêiner de 13/10, `/var/tmp/pg16/data` já existia, com os 4 bancos. Nesse caso, sem `initdb`, só ligar: `su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D /var/tmp/pg16/data -o '-p 55432 -k /var/tmp -c listen_addresses=' -l /var/tmp/pg16/log start"`.
  - Disco: ~9,3 GB livres no contêiner de 13/10. Cabe um modelo do Ollama por vez.
  - Ollama v0.35.1 em CPU: release conferido por SHA-256; precisa de `zstd`.
  - Modelos: `huihui_ai/qwen2.5-abliterate:7b`; na Fase 2, também `qwen2.5:7b-instruct` e `qwen2.5vl:7b` (digests na seção 3). O disco é limitado: tire um modelo antes de baixar outro, se faltar espaço.

## 1. O pedido do dono, em resumo (ele não quer perguntas sobre o que já decidiu)

**Objetivo:** vídeos fiéis a cada foto, mais qualidade e uma primeira forma de edição. Aplicar tudo com cuidado, testar de verdade e não quebrar o que funciona. Se algo conflitar com uma regra de segurança do CLAUDE.md, a regra vence e o motivo vai no relatório.

**Decisões já tomadas:**
- Arquivos "Uncensored" trocados pelos oficiais do Wan 2.2, com o LoRA oficial de 4 passos.
- Tradutor abliterado trocado pelo Qwen 2.5 7B Instruct comum.
- Legenda de cada foto pelo Qwen2.5-VL-7B-Instruct (Apache 2.0; o 3B não serve) e prompt por foto.
- NAG com CFG 1, se o ComfyUI v0.38.0 permitir sem trocar de versão. **Não permite** (ADR 0014).
- Duração máxima de 5 s nos pedidos novos: 4 e 5 s no pago e no interno; o teste continua com 3 s; os pedidos antigos ficam como estão.
- ADRs aceitos de antemão **só se** a rodada em GPU cumprir os critérios. Senão, a produção não muda e o ADR fica "proposto", com os números.
- Montagem (Fase 5): desligada para os clientes até o preço ser definido; ligada no plano interno.

**GPU:** um pod no RunPod, criado pela API.
- **Sem orçamento nem saldo mínimo** (decisão do dono de 13/10, depois da pausa): o teto de US$ 10 e a reserva de US$ 15 do pedido original não valem mais. A Fase 3 roda com o saldo que houver; conferir o saldo antes e depois serve só para registrar o custo.
- Saldo em 13/10: US$ 9,52. O único pod da conta era o hibernado de produção `render2go-fila-dca936-2` (o volume custa US$ 0,019/h), que nunca deve ser tocado.
- Estimativa da rodada da seção 4: cerca de 3 h de L40S secure (~US$ 1,10/h), uns US$ 3 a 4. Acompanhar o saldo durante a rodada, para ela não ser cortada no meio por falta de saldo.

**Regras (seção 3 do pedido):**
- O CLAUDE.md à risca.
- Os filtros (C1, filtro das fotos, filtro dos quadros, conferência) nunca afrouxam.
- Antes de cada commit:
  - testes e lint do worker e do serviço, com Postgres;
  - build conferido e testes no navegador;
  - os geradores `--check`;
  - reler o próprio diff.
- Um commit por fase, com push na branch; sem PR.

**Critérios (seção 4 do pedido; B, C e D contra A; já transcritos no ADR 0014):**
1. Vídeos aproveitáveis ≥ A.
2. Reprovações na conferência ≤ A.
3. Nenhum vídeo SFW barrado, e a média das notas do filtro ≤ a de A.
4. Zero pessoa inventada nas fotos sem pessoas.
5. Tempo por vídeo ≤ A + 10%, em 480p e em 720p, sem falta de memória em 720p com o Ollama no pod.
6. Fidelidade do tradutor novo ≥ a do v6, com todos os REFUSED mantidos.

**Entrega final (seção 5 do pedido), um relatório em pt-BR com:**
- o resumo de cada fase;
- a tabela A × B × C × D da rodada;
- o que entrou, o que ficou de fora e por quê;
- o custo de GPU;
- o que fazer no servidor (entrada nova no `docs/09`) e no `.env`, inclusive o preço da montagem.

## 2. Fase 1 (sem GPU e sem ADR): **fechada na 2ª sessão de 13/10** (commit da Fase 1)

**Resultado da rodada de fidelidade (13/10, 02:35–03:13):** o v7 não passou (58,2% × 69,5% dos casos certos, "sem invenção" 90,8% × 96,5%, aspas 97,2%; só as recusas melhoraram, 3 de 9 × 0 de 9). **O v6 continua** (`PROFILES["i2v"]["version"] = 6`), com as regras de `traducao.py`. Tabela e decisão em `worker/evals/compiler/RESULTADOS.md`; relatórios em `worker/evals/compiler/relatorios/2026-10-13-cpu-i2v{6,7}-casos-v3-7b.*`.

**Também na 2ª sessão:** as 12 falhas de `test_fila.py` (o compilador falso agora devolve um `compiler.CompileResult`), as 2 linhas longas, e o botão da página passou a "Usar o texto pronto do estilo" (copia o `acao` genérico do estilo; o exemplo concreto, como "o bolo", só é mostrado: copiá-lo pediria ao vídeo um produto que as fotos podem não ter). Docs: `docs/06` seção 9.4, CHANGELOG 13/10/2026, CLAUDE.md, `docs/09` entrada 13/10/2026, `docs/10` (o nome do botão).

O que estava escrito antes, para referência:

### Feito (no commit WIP)

1. **Tradutor v7** (`worker/prompts/compiler-i2v.v7.txt`):
   - sem as linhas de conteúdo adulto; a regra REFUSED fica igual;
   - nunca inventa pessoa, objeto ou texto;
   - responde `VAGUE` ao pedido vago e entende a linha `pessoas: nenhuma`.

   No `compiler.py`: `VAGUE` vira `VAGO_TEXTO`; `fill_subject`; faixa de 15 a 60 palavras no v7; `linha_pessoas: 7`. **A produção continua no v6** (`PROFILES["i2v"]["version"] = 6`) até a rodada de fidelidade.
2. **Presets e receitas v4** (`worker/presets/presets.v4.json`, `receitas.v4.json`):
   - todos os termos da v3; câmeras novas (suave, deslocamento lateral, órbita do veículo); continuidade com rosto;
   - **Livre com câmera suave** (`camera_padrao`), só quando o texto do cliente não fala da câmera;
   - categorias e **movimento padrão por categoria**:
     - comida e bebida: aproximação lenta, vapor e brilho;
     - produto e cosmético: aproximação com a luz passando;
     - fachada e interior: deslocamento lateral lento;
     - roupa: tecido em movimento leve e órbita lenta;
     - carro: órbita lenta;
     - pessoa: movimento sutil, sem mexer no rosto;
   - um "exemplo" concreto por estilo.

   `presets.py`, `lote.py` e `fila.py` usam a v4; `traducao.validar_receitas` confere as receitas.
3. **`worker/src/render2go_worker/traducao.py`** (novo):
   - **Pedido vago:** decidido no código, por um vocabulário. Numa sonda, o 7B abliterado acertou só 14 de 28.
   - **Movimento padrão:** texto fixo em inglês.
   - **Pessoa inventada:** vale quando a foto não tem pessoas (ou não tem a análise) e o texto não cita ninguém. O prompt que cita uma pessoa é traduzido de novo, até 3 vezes, com seed + 100. Depois disso, vale o movimento padrão, sem pessoa.
   - Usado por `fila.py` (job compilar, com as `regras` vindas da API), por `lote.py` e pela fidelidade.
4. **API** (`servico/src/render2go_servico/api.py`, `db.py`):
   - `regras_traducao` vai no payload do job compilar.
   - `sem_pessoa_inventada` age em dois lugares:
     - na rota `prompt`: troca o prompt pelo padrão, passa o C1 no padrão e grava o evento `prompt_pessoa_inventada`, que só o painel vê;
     - no `reivindicar` do render: a última barreira antes do pod.
   - `PromptIn` ganhou `vago`, `padrao`, `tentativas` e `pessoas_recusadas`.
   - `db.py`: `fotos_da_traducao`, `registrar_evento` e o detalhe nos eventos `prompt` e `prompt_foto`.
5. **Teto de 5 s:**
   - `precos.DURACAO_MAX = 5` e `api.duracoes`; `/api/receitas` limita o padrão do estilo;
   - textos: `modelos.py` ("3 a 5 s"), `docs/11` e a página;
   - o worker continua aceitando até 7 s;
   - termos e privacidade conferidos: não citam a duração, então não há versão nova.
6. **Página do pedido** (`servico/web/src/paginas/index.html`, `ts/pedidos.ts`, `ts/catalogo.ts`, `ts/painel-clientes.ts`; `publico/` gerado):
   - o exemplo concreto do estilo, como "a câmera se aproxima devagar do bolo e o vapor sobe da xícara";
   - a dica "produto com rótulo ou texto pequeno: escolha Alta (720p)".
7. **Fidelidade:**
   - `worker/evals/compiler/cases.v3.json`: os 45 casos da v2, sem mudança, mais 19 `i2v-v3-*`, com vagos como "deixa bonito" e "deixe bonito, com um movimento suave";
   - `fidelity.py` e `cli_eval.py`: o caminho do serviço nos casos com `receita` e a conferência "pessoa";
   - **critérios do v7 escritos antes da medição** em `worker/evals/compiler/RESULTADOS.md`, seção "Rodada de 13/10/2026".
8. **Testes:**
   - novos: `worker/tests/test_traducao.py` e `servico/tests/test_pessoa_inventada.py`;
   - atualizados: `test_presets`, `test_lote`, `test_lote_nuvem`, `test_cli_submit`, `test_api` e `test_paginas`.

**Arquivos da Fase 1:**
- `docs/11-custos-e-planos.md`
- `servico/src/render2go_servico/{api,db,modelos,precos}.py`
- `servico/tests/{test_api,test_paginas,test_pessoa_inventada}.py`
- `servico/web/src/paginas/index.html`, `servico/web/src/ts/{catalogo,painel-clientes,pedidos}.ts` e `servico/web/publico/` (gerado)
- `worker/evals/compiler/{RESULTADOS.md,cases.v3.json}`
- `worker/presets/{presets,receitas}.v4.json`
- `worker/prompts/compiler-i2v.v7.txt`
- `worker/src/render2go_worker/{cli_eval,compiler,fidelity,fila,lote,presets,traducao}.py`
- `worker/tests/{test_cli_submit,test_lote,test_lote_nuvem,test_presets,test_traducao}.py`

### Estado dos testes no commit WIP

- **worker:** 301 passaram e 2 foram pulados; ruff e formato ok; os 4 geradores `--check` ok.
- **serviço:** 417 passaram e **12 falharam** (lista abaixo).
  - Ruff: 2 linhas longas (`src/render2go_servico/db.py:2990` e `tests/test_pessoa_inventada.py:35`), e falta o `ruff format` em `tests/test_pessoa_inventada.py`.
  - **Causa da 1ª falha** (`test_fila.py`): o compilador falso dos testes devolve um `SimpleNamespace` sem `model`, e `traducao.traduzir` lê `comp.model` (`AttributeError`).
  - Correção: usar `getattr(comp, "model", model)`, e o mesmo para `compiled` e `word_count`, ou completar o falso.
  - Conferir também:
    - se as outras falhas têm a mesma causa;
    - se o Ollama falso do serviço responde com uma pessoa ("She walks…"), o que agora gera traduções a mais.
- **Navegador e build:** os testes no navegador e o `npm ci && npm run build` (conferir que `publico/` não muda) não rodaram depois das últimas mudanças.

Falhas no serviço, todas em `servico/tests/test_fila.py` (provavelmente a mesma causa):

```
test_queue_worker_renders_checks_and_delivers
test_a_video_of_the_wrong_photo_never_reaches_the_client
test_a_video_that_swaps_the_product_midway_is_redone_with_another_seed
test_worker_translates_while_models_download_and_renders_after_ready
test_a_video_blocked_by_the_frame_filter_never_leaves_the_pod
test_without_the_frame_filter_nothing_is_delivered_and_the_worker_leaves
test_the_gpu_does_not_wait_for_the_upload_or_the_next_photo[True]
test_the_gpu_does_not_wait_for_the_upload_or_the_next_photo[False]
test_a_video_picked_ahead_is_not_rendered_when_the_client_stops_the_order
test_when_the_gpu_fails_the_video_picked_ahead_goes_back_without_an_attempt
test_near_the_pod_time_limit_the_next_video_is_not_picked_ahead
test_with_an_order_waiting_the_gpu_does_not_warm_up
```

### Falta para fechar a Fase 1

1. Corrigir as 12 falhas e o lint do serviço.
2. **Rodada de fidelidade v6 × v7.** A desta sessão foi interrompida com a v6 em 149 de 192 casos; refazer as duas (~50 min cada em 4 vCPU), da pasta `worker`:
   ```
   uv run python ../tools/eval_compiler.py --ollama http://127.0.0.1:11434 --profile i2v --version i2v=6 --out <pasta>/2026-10-13-cpu-i2v6-casos-v3-7b
   uv run python ../tools/eval_compiler.py --ollama http://127.0.0.1:11434 --profile i2v --version i2v=7 --out <pasta>/2026-10-13-cpu-i2v7-casos-v3-7b
   ```
   - As falhas da v6 vistas até ali são as mesmas de 09/10 (câmera de costas, close, de cima, câmera que segue).
   - Aplicar os 4 critérios de `RESULTADOS.md`.
   - **Se o v7 passar:** `PROFILES["i2v"]["version"] = 7`, os relatórios em `worker/evals/compiler/relatorios/` e a tabela em `RESULTADOS.md`.
   - **Se o v7 não passar:** o v6 fica. As regras de `traducao.py` valem também com ele (só a linha `pessoas` é do v7), e o v7 fica registrado com os números.
3. Documentação:
   - `docs/06`, seção 9: o que entrou em 13/10;
   - `CHANGELOG.md`: entrada de 13/10/2026;
   - `CLAUDE.md`, status: receitas e presets v4, tradutor v7 ou v6, vídeos de 4 e 5 s, pessoa inventada;
   - `docs/09`: entrada de publicação, sem variável nova.
4. Todos os testes (worker, serviço com Postgres e navegador), build conferido e geradores; reler o diff; commit da Fase 1 e push.

## 3. Fase 2 (ADR 0014, proposto): começada

### Feito (no commit WIP)

- **ADR** `docs/adr/0014-modelos-oficiais-tradutor-e-legenda.md`, proposto: a decisão, os critérios 1 a 6 escritos antes da medição, o NAG fora com o motivo, o que fazer no aceite e as consequências.
- **`worker/models.lock.json`:** conjunto novo `i2v_oficial`; o `i2v_r1v2` fica.
  - Os arquivos e as origens:
    - `wan22_i2v_high_fp8` e `wan22_i2v_low_fp8` (Comfy-Org/Wan_2.2_ComfyUI_Repackaged);
    - `umt5_xxl_fp8` (Comfy-Org/Wan_2.1_ComfyUI_repackaged);
    - `lightning_i2v_v1_high` e `lightning_i2v_v1_low` (LoRA `wan2.2_i2v_lightx2v_4steps_lora_v1`);
    - mais o VAE e o filtro.
  - Cada um com revisão, bytes e SHA-256.
  - **Os 5 arquivos novos foram baixados inteiros e conferidos em 13/10: bytes e SHA-256 batem.**

    | Arquivo | Bytes | SHA-256 |
    |---|---|---|
    | LoRAs (cada um) | 1.226.977.424 | — |
    | umt5 | 6.735.906.897 | — |
    | high | 14.294.742.832 | `6122e79d…6a42` |
    | low | 14.294.742.832 | `5471a457…c21e` |
- **Licenças conferidas (Apache 2.0):** Comfy-Org, Wan-AI/Wan2.2-I2V-A14B, lightx2v/Wan2.2-Lightning, Qwen2.5-7B-Instruct e Qwen2.5-VL-7B-Instruct.
- **Template candidato:** `tools/build_i2v_templates.py` (com `--check`) gera `worker/workflows/experimental/i2v_oficial_lightx2v.v1.{api,contract}.json`.
  - Mesmo grafo e mesma amostragem da produção; o LoRA, com força 1,0, entra entre o UNETLoader e a chave do turbo.
  - `PRODUCTION` fica vazio até o aceite.
  - Vêm junto `worker/tests/test_i2v_templates.py` e o `worker/tests/conftest.py` (`T2V_TEMPLATES` só com `t2v_*`).
- **NAG: fora.**
  - O nó nativo `NAGuidance` usa o `attn1_output_patch`, que o Wan da v0.38.0 não chama, e dobra o custo.
  - O ComfyUI-NAG (MIT, commit `ef8a641be08983cf5f06669f70719b6eecce3c7f`) troca o forward do Wan por uma cópia de outra versão do ComfyUI.
  - Por isso, a combinação C não roda.
- **Digests no Ollama** (camada de pesos):
  - `qwen2.5:7b-instruct`: `sha256:2bada8a7450677000f678be90653b85d364de7db25eb5ea54136ada5f3933730` (4.683.073.952 bytes);
  - `qwen2.5vl:7b`: `sha256:a99b7f834d754b88f122d865f32758ba9f0994a83f8363df2c1e71c17605a025` (5.969.233.408 bytes).
- **Desenho da legenda** (no ADR): um 2º Ollama, só em CPU (`CUDA_VISIBLE_DEVICES` vazio, `127.0.0.1:11435`), roda em paralelo com a geração. Usa zero VRAM, então não há risco de faltar memória em 720p.

**Arquivos da Fase 2 até aqui:**
- `docs/adr/0014-modelos-oficiais-tradutor-e-legenda.md`
- `worker/models.lock.json`
- `tools/build_i2v_templates.py`
- `worker/workflows/experimental/i2v_oficial_lightx2v.v1.*`
- `worker/tests/test_i2v_templates.py` e `worker/tests/conftest.py`

### Falta

1. **Tradutor comum:**
   - `worker/Dockerfile` e `worker/scripts/pull_compiler_model.sh` aceitando o modelo candidato pelo digest, sem trocar o padrão antes do aceite.
   - Rodada de fidelidade do `qwen2.5:7b-instruct` com o v7 nos casos de `cases.v3.json`. Critério 6, contra o abliterado com o v6:
     - casos certos e "sem invenção" ≥ os do abliterado;
     - 100% das aspas;
     - cada REFUSED recusado pelo menos tantas vezes quanto pelo v6.
2. **Legenda por foto, no worker:**
   - `render2go_worker/legenda.py`: usa o Ollama de CPU, com `format` JSON e esquema fixo:
     - descrição curta e objetiva;
     - categoria das receitas v4 (`comida`, `produto`, `imovel`, `roupa`, `carro`, `pessoa`, `geral`);
     - assunto em inglês;
     - se tem pessoa;
     - se tem rótulo ou texto.
   - O código confere o esquema. Se a legenda falhar, a foto segue sem ela; nada bloqueia o pedido.
   - `worker/prompts/legenda.v1.txt`.
   - `compiler-i2v.v8.txt`: o v7 mais as linhas da legenda e da categoria. O tradutor recebe o texto do cliente, a legenda e a categoria.
   - Casos de fidelidade com legendas escritas à mão (sem rodar o modelo de visão); a rodada do v8 tem de ficar ≥ v7.
   - Medir aqui o tempo da legenda em CPU com o `qwen2.5vl:7b`, nas fotos SFW do repositório.
3. **Legenda por foto, no servidor:**
   - Chave `RENDER2GO_LEGENDA_POR_FOTO`, padrão 0.
   - Colunas `fotos.legenda` e `fotos.categoria`, com a migração nos dois bancos.
   - Com a chave ligada:
     - um job compilar por foto do pedido aprovado, reaproveitando `fotos.prompt_en`, `definir_prompt_foto` e o caminho do pedido em sequência;
     - o render da foto espera o prompt dela no `reivindicar`;
     - o payload do compilar leva a URL da foto;
     - a rota do prompt grava a legenda e a categoria, e o C1 continua conferindo cada prompt.
   - A legenda aparece no painel (histórico e "vídeos que não saíram") e na auditoria, **nunca para o cliente**.
   - A regra da pessoa inventada vale se a análise **ou** a legenda disser que não há pessoa (fica mais rígida).
   - A fila mudou: rodar o teste dos 10 pods com saldo para 3 no Postgres.
   - Política de privacidade v6: a descrição automática fica guardada com o pedido e sai com a limpeza do histórico.
4. **Legenda por foto, no pod:**
   - O orquestrador passa a chave ao `bootstrap_pod.sh`, ao `entrypoint.sh` e ao `fila.py`, que sobem o Ollama de CPU na porta 11435 e puxam o `qwen2.5vl:7b` pelo digest.
   - Ajustar a versão da hibernação.
   - A legenda de cada foto roda durante a geração do vídeo anterior; o 1º vídeo pode levar no máximo ~30 s a mais.
5. **Espelho R2** dos 5 arquivos (`worker/scripts/espelhar.py`) antes de qualquer produção. É um passo do servidor e vai no `docs/09`.
6. CLAUDE.md (comando `python tools/build_i2v_templates.py --check`), testes e commit da Fase 2.

## 4. Fase 3: rodada em GPU (sem limite de orçamento)

**Orçamento:** sem teto e sem saldo mínimo (decisão do dono de 13/10; seção 1). Registrar o saldo do RunPod antes e depois da rodada, para o custo do relatório. A credencial vai pelo proxy (`*.runpod.io`).

**Pasta da rodada:** `worker/evals/gpu-2026-10-13-qualidade/`, com um LEIAME que segue o protocolo do `worker/evals/gpu-2026-10-05/LEIAME.md`:
- um L40S secure;
- desligamento automático armado antes de tudo;
- nome diferente dos pods de produção;
- nunca mexer nos `render2go-fila-*` nem nos hibernados;
- pod encerrado no fim e conferido na API;
- custo registrado (saldo antes e depois), sem teto.

O LEIAME também traz os critérios do ADR 0014 e a lista de "aproveitável", escrita antes de ver os vídeos.

**Fotos:**
- as de `fixtures/sfw` e mais 6 a 8 de domínio público ou CC0: comida, bebida, fachada, interior, roupa, carro e cosmético com rótulo;
- reduzidas a 640 px, com fonte e licença em `fixtures/sfw/FONTES.md`;
- fotos reais de clientes só fora do repositório; nada explícito, nunca.

**Combinações:**
- **A:** a produção depois da Fase 1.
- **B:** modelos oficiais com o LoRA, com os mesmos prompts de A.
- **C:** fica de fora (NAG).
- **D:** B com o tradutor comum e a legenda e o prompt por foto (v8).

**Roteiro:**
- Cada foto com 1 pedido concreto e 1 vago, seeds fixas e vídeos de 5 s.
- Todas as combinações em 480p; as duas melhores também em 720p, com o Ollama no pod e sem falta de memória.
- SageAttention 2.2 na vencedora, com ~24 vídeos; entra só se não houver reprovação a mais.
- Registro por vídeo:
  - tempo;
  - pico de VRAM;
  - conferência;
  - notas do filtro;
  - prompt;
  - pessoa ou objeto inventado;
  - aproveitável ou não.
- Folhas lado a lado, de A a D, por foto.
- Simular o roteiro sem GPU antes de gastar.

## 5. Fase 4: decidir e pôr em produção

**Com a rodada:**
- Decidir cada parte pelos critérios. O ADR 0014 passa a "aceito", com os números, ou fica "proposto".
- No aceite:
  - `PRODUCTION` no gerador;
  - `lote.I2V_TEMPLATE` e o conjunto do bootstrap passam a `i2v_oficial`;
  - `Dockerfile` (`COMPILER_MODEL` e o digest), `compiler.DEFAULT_MODEL` e a versão do prompt;
  - `RENDER2GO_LEGENDA_POR_FOTO=1`;
  - CLAUDE.md: a regra "sem LoRA Lightning" e a frase do "modelo abliterado";
  - `docs/06`, `docs/07` e `docs/12`;
  - `docs/09`, com o passo a passo: espelho R2, o aviso de que os hibernados são encerrados e refeitos, e o `.env`;
  - `CHANGELOG.md`.
- O que falhar fica de fora, com o motivo.

**Sem a rodada:** o ADR fica proposto e a produção recebe só a Fase 1.

## 6. Fase 5: Montagem (ADR próprio, o 0015, coerente com o ADR 0010 "Mix e Projetos")

- **Processamento:** a v1 usa só ffmpeg em CPU, num processo isolado, com limite de memória e de tempo e fora da API (o servidor tem 4 GB de RAM).
- **Vídeos:** junta de 2 a 10 vídeos prontos da conta, na ordem escolhida, com corte seco ou transição curta (fade).
- **Texto por cima** (título, preço, chamada):
  - em posições fixas, passando pelo C1 e com limite de tamanho;
  - **nunca interpolar o texto do cliente na linha de filtros do ffmpeg**: usar um arquivo de texto (`textfile=`) ou outra forma segura;
  - fonte OFL fixada por SHA-256.
- **Logo opcional:**
  - só JPEG, PNG ou WebP;
  - aberto por `pessoas.abrir_foto` e passado pelo filtro das fotos;
  - regravado em PNG pelo servidor antes de ir ao ffmpeg.
- **Trilha opcional:**
  - só de uma biblioteca com a licença de cada faixa conferida e documentada (CC0 ou comprada);
  - sem envio de áudio pelo cliente na v1.
- **Saída:** 9:16, 1:1 ou 16:9, em 720p, H.264 + AAC, em `saida/`, com a mesma guarda dos vídeos.
- **Telas:**
  - área do cliente e painel pelos passos do `docs/10`, seção 6 (`TELAS`, `TELAS_CONTA`, `PERMISSOES_PAINEL` e o teste no navegador);
  - com extrato e registro de acesso.
- **Preço e chave:**
  - preço por montagem no `.env` (por exemplo, `RENDER2GO_PRECO_MONTAGEM`), proposto no `docs/11`;
  - `RENDER2GO_MONTAGEM` desligada para os clientes e ligada no plano interno.
- **Testes:** SFW, inclusive texto malicioso para o ffmpeg e logo inválido.
- Commit da Fase 5.

## 7. Fase 6 (opcional, só com as anteriores fechadas)

- Opções de 32 fps (RIFE ×2, ADR 0013) e de upscale para Full HD.
- As duas desligadas por padrão, com tempo e custo medidos (precisa de GPU).

## 8. Relatório final

O da seção 5 do pedido (seção 1 deste arquivo):
- o resumo de cada fase;
- a tabela A × B × C × D;
- o que entrou e o que ficou de fora, com os motivos;
- o custo de GPU;
- os passos no servidor (`docs/09`) e no `.env`, inclusive o preço da montagem.

## 9. Texto para colar na nova sessão

> Continue o meu pedido de 13/10/2026 na branch `claude/magical-lamport-4rrms9`. Leia primeiro o `CONTINUACAO.md` na raiz (estado, o que falta por fase, critérios e decisões já tomadas) e o CLAUDE.md, e não me pergunte o que esse arquivo já decide. Comece pela Fase 1: corrija as falhas e o lint do serviço, refaça a rodada de fidelidade v6 × v7 em CPU, aplique os critérios, documente e separe o commit WIP em um commit por fase. Depois siga as Fases 2 a 5 na ordem do arquivo. A Fase 3 roda sem limite de orçamento nem saldo mínimo (decisão minha de 13/10). Siga o protocolo de segurança do pod da seção 4: desligamento automático armado antes de tudo, nome próprio, nunca mexer nos pods de produção, encerrar no fim e conferir na API. No fim, me entregue o relatório da seção 8 e apague o `CONTINUACAO.md`.
