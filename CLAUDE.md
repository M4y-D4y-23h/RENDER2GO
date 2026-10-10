# CLAUDE.md

Projeto Render2Go (antes GPU2GO): serviço de vídeos curtos com IA para pequenos negócios (ver `README.md`; mudanças em `CHANGELOG.md`).
**Status:** Etapas 1 e 2 concluídas em `worker/` e medidas numa RTX 4090 (`worker/evals/gpu-2026-10-05/`). T2V de produção: A14B fp8 + Lightning (ADR 0001, aceito). Etapa 3: código pronto (handler serverless, endpoints como código, bench); imagem montada pelo RunPod a partir do GitHub (ADR 0002, aceito); medições pendentes da primeira build lá. **Caminho escolhido (docs/05): A, serviço de marketing com IA para pequenos negócios**: foco em **lotes de 50+ fotos com um pedido só**, renderizados em vários pods (docs/05, seção 5.1; `lote.py`, `acabamento.py`, presets e receitas v3); fase 2 (`lote_nuvem.py`) validada em GPU; **fase 3 implantada** em `servico/` (API, fila, orquestrador, páginas; ADR 0004 proposto) em https://app.render2go.com (Hetzner, docs/09; 1º lote real em 08/10); aviso de pessoas, histórico, auditoria da autorização, teste gratuito (um pedido, 3 s, 480p, até 5 fotos; por dispositivo e por IP, em grupo), plano interno, **pré-pago em vigor desde 09/10** (saldo em reais, R$ 0,25/s em 480p e R$ 0,50/s em 720p no `.env`, cobrança no envio, estorno da recusa e de cada vídeo que falha, extrato que sobrevive à limpeza; vídeos de 4 a 7 s fora do teste; `precos.py`, tabela `movimentos`, docs/11 seção 4), **conferência do vídeo inteiro desde 09/10** (cor do centro: deriva ≤ 40 e salto ≤ 12; o vídeo que troca de produto é refeito com outra seed; `conferencia.py`, docs/09 seção 5.1.9, calibração em `worker/evals/conferencia-2026-10-09/`), **tradutor I2V v6 e presets/receitas v3** (cena única: o texto vale para cada foto, pedido leigo vira movimento suave; termos de continuidade anexados pelo código), aviso de foto pequena na página, **tela de finalidades antes do pedido** ("O que você quer criar?": 6 blocos + "Ver todas" com 12; só "Foto vira vídeo" disponível, as outras "Em breve" com "Tenho interesse" contado no painel, clique a clique; catálogo em `modelos.py`), **relatório de GPUs no painel** (pod, GPU, tempo ligado, vídeos, custo estimado; docs/09 seção 5.1.8), previsão para o cliente com o estado real dos pods (tabela `geradores`), fila intercalada por cliente e em ordem de chegada no mesmo cliente, subida com modelos só do espelho R2 (quando passa no teste de rede) e serviços e tradução durante o download (4,2–5,1 min até o 1º vídeo, `worker/evals/gpu-2026-10-08-subida/`), L40S como 1ª GPU (`RENDER2GO_GPUS`), `apagar-historico`, `/suporte` com e-mail, aviso de cookies e estilo livre; custos e preços em docs/11; filtro de imagem reservado (ADR 0005 proposto); identidade visual e front-end das páginas (docs/10, ADR 0006 aceito: build com esbuild, cache por versão, CSP com Trusted Types, WCAG 2.2 AA; revisão de 10/10: topo conforme a sessão, rodapé único, índice nas páginas de texto, janelas de baixo no celular e CLS zero, também com a API lenta); **preparação para o lançamento desde 09/10, no servidor desde 10/10 (ADR 0007 e 0008, aceitos; docs/13; e-mail pelo Amazon SES em Ohio, no sandbox até a aprovação)**: cadastro e entrada sem senha, por código de 8 dígitos no e-mail, só de provedores confiáveis, com um teste gratuito por caixa (apelidos normalizados), "não sou um robô" (Turnstile isolado em `/verificacao`) e limites no banco por rede, e-mail e aparelho; sessão em cookie HttpOnly; painel do dono em endereço secreto (`RENDER2GO_ADMIN_CAMINHO`) com token + TOTP, bloqueio por tentativas, IPs opcionais e auditoria, com pods (desligar, parada de emergência) e cada clique em "Tenho interesse"; token de worker por pod; recarga por Pix no Mercado Pago (aviso assinado, crédito só com a confirmação da API, uma vez só); `/termos`, `/privacidade` e `/cookies`; sem `robots.txt` (decisão do dono); contêineres só de leitura e sem privilégios, Caddy endurecido; teste de invasão (`servico/tests/teste_invasao.py`, 73/73) e medição de carga (`servico/tests/medir_carga.py`: ~475–810 pedidos/s com 2–3 processos em 4 vCPU). **Em andamento desde 10/10: área do cliente com menu lateral (perfil, extratos, logs, modelos de IA, configurações com MFA) e acessos do painel por pessoa**: no servidor, já na main, textos por foto (pedido em sequência), extrato por rodada e logs de geração; o pedido do dono, as decisões tomadas e os próximos passos estão em `docs/continuacao-2026-10-10.md` (ler antes de continuar). Etapas 4–6 em pausa. Decisões D1–D5 e D7 da Etapa 0 pendentes (docs/04); D1 (termos do RunPod) segue bloqueando qualquer uso adulto.

## Fontes de verdade

- `referencia/especificacao-pipeline-manual.md`: pipeline manual testado. Não editar.
- `referencia/wan-i2v-com-ollama.api.json`: workflow exportado. Não editar; os templates derivados ficam em `worker/workflows/`.
- `docs/01` a `docs/04`: verificações, arquitetura, segurança e plano. Mudanças de arquitetura viram ADR em `docs/adr/` (0001: modelo T2V, aceito; 0002: hospedagem da imagem, aceito; 0003: arquitetura em escala, aceito; fase 2 = `lote_nuvem.py`; 0004: C1 e revisão humana, proposto; 0005: filtro de imagem nas fotos e quadros, proposto; 0006: front-end, build, entrega e segurança das páginas, aceito; 0007: contas por e-mail, sessão, painel secreto com dois fatores e token por pod, aceito; 0008: recarga por Pix no Mercado Pago, aceito). Qualidade, velocidade e custo dos lotes: `docs/06`; guia técnico consolidado (GPUs, pods, proteções, custos): `docs/07`. Vários clientes ao mesmo tempo, escala e isolamento entre clientes: `docs/08`. Implantação do serviço: `docs/09`. Custos mensais e preços do pré-pago: `docs/11`. Modelos de IA e licenças comerciais por finalidade (e o risco do Wan "Uncensored" em produção): `docs/12`. Identidade visual, regras de layout, componentes e front-end: `docs/10`. Segurança antes do lançamento, teste de invasão, capacidade medida, GPUs com 30+ pods e a lista do dono: `docs/13`.

## Regras técnicas (mudar só com ADR)

- ComfyUI fixado em **v0.38.0**. Template de workflow e versão do ComfyUI mudam juntos.
- Wan2.2 I2V R1 v2: **sem LoRA Lightning**; turbo ligado; 4 passos, CFG 1, troca de expert no passo 2 (8 passos e CFG 1,5–2 testados na Etapa 2: até 3,3× o tempo sem ganho).
- Largura e altura múltiplas de 16; duração inteira de 3 a 7 s a 16 fps (16·d + 1 frames).
- Nunca carregar modelos direto de volume de rede ou de objetos: embutir na imagem ou copiar para o disco local e conferir bytes e SHA-256.
- Em volumes que não são POSIX (Global Volume): sem `rename` nem `chmod`; `rsync --inplace`; conferir bytes depois de copiar.
- Ollama em processo separado do ComfyUI; compilar antes de renderizar; reservar VRAM (`--reserve-vram`).
- PyTorch `+cu128` (driver ≥ 570); não usar as wheels CUDA 13 do PyPI.
- Nos workers, ComfyUI e Ollama só em `127.0.0.1`; nenhuma porta exposta.
- Payload de job sem binário: imagens e vídeos trafegam por URL assinada, só https e só hosts de `RENDER2GO_ALLOWED_URL_HOSTS`.
- Worker sem filtros C1–C2 não processa jobs (`RENDER2GO_SAFETY`); `disabled-sfw-tests` só em endpoints com "teste" no nome.
- Termos dos presets são anexados ao prompt pelo código, nunca reescritos pelo LLM. Preset novo = arquivo novo (`presets.v<N>.json`) que mantém todos os termos da versão anterior. Prompt do compilador novo = arquivo novo (`compiler-<perfil>.v<N>.txt`) + rodada de fidelidade.
- Templates candidatos ficam em `worker/workflows/experimental/` (gerados por `tools/build_t2v_templates.py`); só vão para `worker/workflows/` com ADR aceito.
- Páginas (ADR 0006): editar só `servico/web/src/`; `servico/web/publico/` é gerado por `npm run build` e versionado (nunca editar à mão). Nada de `innerHTML`, `style=` ou `<script>` embutido (CSP com Trusted Types). Cor só por token em `src/css/tokens.css`, nos dois temas, com o par no teste de contraste (`servico/tests/test_web.py`). Logo, ícones, `og.jpg` e fonte dos títulos só pelo `tools/marca.py`. Script de terceiros só na página `/verificacao` (Turnstile, CSP própria). O que chega da API tem o lugar reservado no HTML/CSS (esqueleto, altura mínima): nada é inserido acima do que já está na tela (teste com a API lenta em `test_paginas.py`). Página nova: partes `<!--@sessao-->` (antes de `<!--@cabeca-->`), `<!--@topo-->` e `<!--@rodape-->`, e `iniciarTopo()` no script.
- Painel (ADR 0007): só no endereço secreto; nenhuma página, script, sitemap ou 404 público cita o endereço ou o `painel.js` (fica em `publico/privado/`). Sem `robots.txt` (decisão do dono).

## Regras de segurança (inegociáveis)

- O compilador é um modelo abliterado: nunca é camada de segurança. A regra `REFUSED` continua nos system prompts.
- Toda verificação obrigatória bloqueia na dúvida (fail-closed).
- Sem upload livre de imagem no modo adulto (política da v1, doc 03, seção 3).
- Repositório, testes, fixtures e CI só com conteúdo SFW. Nunca gerar, buscar ou guardar material explícito ou ilícito para teste.
- Não copiar código do `worker-comfyui` (AGPL-3.0); usar só como referência de protocolo.
- Contas e pagamentos (ADR 0007 e 0008): credenciais e assinaturas comparadas em tempo constante e em bytes (`contas.iguais`); crédito de saldo só com a confirmação da API do Mercado Pago, nunca só pelo aviso; respostas que revelariam se uma conta existe só depois da verificação anti-robô e dos limites.

## Comandos

- Testes e lint: `cd worker && uv sync --python 3.12 && uv run pytest -q && uv run ruff check . ../tools && uv run ruff format --check . ../tools`
- Serviço (fase 3): `cd servico && uv sync --python 3.12 && uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`; Postgres de teste por `RENDER2GO_TESTE_PG`; páginas: `cd servico/web && npm ci && npm run build` (gera `publico/`, versionado; o CI confere) e teste no navegador com `uv run --with playwright==1.56.0 pytest -q tests/test_paginas.py` (inclui axe-core, que vem do `npm ci`; `RENDER2GO_CAPTURAS=<pasta>` grava capturas)
- Template em dia com o export: `python tools/derive_i2v_template.py --check`
- Candidatos T2V em dia: `python tools/build_t2v_templates.py --check`
- Dockerfiles do RunPod em dia: `python tools/build_runpod_dockerfiles.py --check` (gerados; nunca editar `infra/runpod/Dockerfile.*` à mão)
- Modo serviço (lote, lote na nuvem e acabamento): ver `worker/README.md`, seção "Modo serviço". Receitas medidas em GPU em 06/10 (`worker/evals/gpu-2026-10-06/`): 6 de 8 aproveitáveis (perfume mudou de cor, fachada parada); compilador acrescentava "Static camera" (com preset de câmera, o código remove essas frases).
- Endpoint de teste e bench: ver `infra/runpod/LEIAME.md`
- Teste de invasão e medição de carga (fora do pytest; apagam o banco de teste indicado): `cd servico && uv run python tests/teste_invasao.py --caddy <binário do Caddy 2.10> --db <postgres de teste>` e `uv run python tests/medir_carga.py --processos 2 --conexoes 3 --db <postgres de teste>` (`docs/13`, seções 3 e 4)
- Fidelidade do compilador (precisa de Ollama): `python tools/eval_compiler.py --out saida/fid` (ver `worker/README.md`)

## Convenções

- Nome: Render2Go. Pacotes `render2go_worker`/`render2go_servico`, variáveis `RENDER2GO_*` (as antigas `GPU2GO_*` ainda valem, por compatibilidade, em `render2go_worker/__init__.py`). Ficam com o nome antigo, de propósito: bucket R2 `gpu2go`, banco/usuário Postgres `gpu2go`, volumes `gpu2go_*` do compose, endereço do repositório e os registros em `worker/evals/gpu-*`.
- Toda mudança relevante entra no `CHANGELOG.md`.

- Python 3.12 + `uv` + `ruff` + `pytest`; TypeScript no frontend.
- Documentação e textos para o usuário em pt-BR.
- Dependências com versão fixada (tags, commits, hashes).

## Ambiente de nuvem

- O acesso à internet foi liberado em 05/10/2026, mas o tráfego passa por um proxy que reassina TLS. Em builds Docker, passe a CA: `docker build --secret id=extra_ca,src=/root/.ccr/ca-bundle.crt ...` (ver `worker/README.md`).
- O daemon Docker não sobe sozinho: `nohup dockerd > /tmp/dockerd.log 2>&1 &`. O disco é limitado (a imagem `slim` tem ~18 GB): rode `docker builder prune -af` depois das builds. Não há GPU; o alvo `full` e os testes de tempo rodam fora daqui.
- Testes em GPU: pod no RunPod criado pela API com chave fornecida pelo usuário para a sessão (credencial Bearer nas configurações do ambiente, para `*.runpod.io` e `*.runpod.ai`, que o proxy inclui nas chamadas; ou a variável `RUNPOD_API_KEY`), sempre com desligamento automático e encerrado no fim (ver `worker/evals/gpu-2026-10-05/LEIAME.md`). Não guardar chaves no repositório.
- Para a rodada de fidelidade sem GPU: Ollama v0.35.1 em CPU (release conferido por SHA-256, precisa de `zstd`), ~5–15 s por caso no 7B com cache de prefixo.
