# Histórico de mudanças

Tudo o que mudou no projeto, do mais recente para o mais antigo. Detalhes técnicos estão nos documentos e ADRs citados, e cada item corresponde a commits no repositório.

## 08/10/2026 (noite): fila, previsão, subida mais rápida, GPU, custos e limpeza

### Fila e previsão
- **Pedidos da mesma conta em ordem de chegada:**
  - a vez na fila passou a ser por cliente: vai primeiro quem tem menos vídeos começados;
  - no mesmo cliente, o pedido mais antigo sai antes;
  - no mesmo pedido, os vídeos saem na ordem da lista.
  - Antes a vez era por pedido, e o pedido novo de uma conta passava na frente do antigo da mesma conta ([`docs/09`](docs/09-implantacao.md), seção 5.1.5).
- **Previsão fiel com o pod desligado:**
  - o orquestrador registra cada pod (tabela `geradores`: subindo, pronto, encerrado);
  - a previsão usa esse estado e a subida medida (mediana das últimas 10, da decisão de ligar até o 1º vídeo);
  - os vídeos à frente são contados na mesma ordem da fila.
  - Antes, um pod desligado havia menos de 10 min contava como ligado ("começa em instantes"), e um pod no meio da subida não descontava o tempo já passado.

### Subida do pod mais rápida ([`docs/07`](docs/07-guia-tecnico-gpus-e-pods.md), seção 9)
- **Durante o download dos modelos:** ComfyUI e Ollama sobem, e o worker já traduz o pedido. O render só começa com os modelos conferidos (estado `PRONTO`).
- **Orquestrador:** olha a fila a cada 10 s (eram 30 s).
- **Consultas ao pod:** a cada 3 s (eram 10 s).
- **apt:** instala primeiro só o que o download usa.
- **Ganho esperado:** ~1 a 1,5 min por subida, a confirmar no próximo pedido real.

### GPU
- **A RTX PRO 6000 subiu de US$ 2,09 para US$ 2,49/h.** A nova ordem é L40S (US$ 1,09/h, medida) → RTX 6000 Ada (US$ 0,99/h) → RTX PRO 6000 → RTX 4090.
  - Subida e pod quente custam menos da metade por pedido (~US$ 0,11 contra ~US$ 0,26), e o vídeo ~14% menos.
  - Em troca, cada vídeo leva ~2× mais; nos lotes grandes, o orquestrador sobe mais pods.
- **Configurável no `.env`, sem mudar o código:**
  - `RENDER2GO_GPUS`: ordem das GPUs;
  - `RENDER2GO_CICLO_S`: intervalo do ciclo;
  - `RENDER2GO_POD_OCIOSO_MIN`: tempo de pod quente.
- **Orçamento e desligamento automático** passam a contar o preço da opção mais cara com vaga.

### Custos e planos ([`docs/11`](docs/11-custos-e-planos.md))
- **Custos mensais:**
  - parado: ~R$ 43;
  - escala pequena (300 vídeos/mês): ~R$ 136;
  - demanda alta (30 mil vídeos/mês): ~R$ 7.400, ~96% de GPU.
- **Proposta de planos por créditos (não aplicada):** Avulso R$ 19,90, Essencial R$ 49,90, Profissional R$ 129,90 e Agência R$ 349,90. O custo de referência é o medido pelo dono, ~US$ 0,03 por vídeo.

### Limpeza do histórico
- **`render2go-servico apagar-historico`:**
  - apaga pedidos, prompts, trabalhos, autorizações, eventos, suporte e registro dos pods;
  - apaga os arquivos dos clientes no R2 (`entrada/` e `saida/`; nunca o espelho dos modelos);
  - reescreve as tabelas (`VACUUM FULL`).
  - Os clientes e os códigos de acesso ficam. Sem `--confirmar`, só mostra o que seria apagado.
- **Servidor:** os passos estão no [`docs/09`](docs/09-implantacao.md), seção 0.

## 08/10/2026: identidade visual e front-end das páginas

### Identidade visual ([`docs/10`](docs/10-identidade-visual-e-front-end.md))
- **Conceito:** anil (ações e confiança) e urucum (acento de movimento), dois pigmentos brasileiros, com tinta e papel como neutros. Modo claro e escuro, com contraste AA conferido em teste (28 pares).
- **Logo:** símbolo (play com traços de movimento) e a palavra "Render2Go" em contornos. Favicon, ícones de tela inicial, manifesto e imagem de compartilhamento (1200 × 630) saem do `tools/marca.py`.
- **Tipografia:** títulos em Bricolage Grotesque (OFL), só com os caracteres do português (13 KB); texto na fonte do aparelho.
- **Regras de layout e componentes documentados:** celular primeiro, alvos de toque de 44 px, pedido em passos numerados, 25 ícones próprios.

### Páginas
- **Cliente (`/`):**
  - quem chega vê a apresentação e "Como funciona" ao lado do código de acesso;
  - pedido em passos, com prévia das fotos (dá para tirar uma por uma), arrastar e soltar (inclusive pastas), exemplo de texto para o estilo escolhido e contador de caracteres;
  - autorização destacada quando o estilo exige; erros no próprio campo, com o foco nele;
  - envio com progresso em bytes e porcentagem, ✓ em cada foto enviada e confirmação antes de fechar a página no meio;
  - vídeos assistidos na própria página, com "Abrir em outra aba" e "Baixar";
  - código inválido ou desativado volta ao formulário com a explicação (antes, a mensagem ficava escondida);
  - quem já tem o código vê o painel desde o primeiro quadro.
- **Revisão (`/aprovar`):** menu de seções com o número de pendentes, também no título da aba; aprovar em verde e recusar em vermelho; as fotos não são baixadas de novo a cada atualização; confirmação própria para desativar um código; código novo com botão "Copiar".
- **Suporte e 404:** validação no campo e contador; endereço inexistente abre uma página 404 com caminhos de volta.

### Desempenho, acessibilidade e segurança ([ADR 0006](docs/adr/0006-front-end-build-entrega-e-seguranca.md))
- **Build com esbuild** (versão fixada): JS e CSS minificados, um script por página (o código da revisão não vai mais para o cliente), HTML compacto, versão por hash e SRI. Arquivos com cache de 1 ano; páginas revalidadas a cada visita (ETag). Caddy com zstd.
- **Medido em 4G lenta, celular:** no painel, LCP de 0,71 s para 0,54 s na 1ª visita e de 0,41 s para 0,30 s na volta; CLS de 0,028 para 0. A 1ª visita sem código ficou 0,08 s mais lenta (CSS, HTML e fonte maiores).
- **Acessibilidade:** WCAG 2.2 AA conferido pelo axe-core no CI, em claro e escuro; "Pular para o conteúdo", foco visível, teclado completo e anúncios curtos para leitores de tela (a atualização automática não repete nada).
- **Segurança:** CSP com Trusted Types, COOP, CORP e Permissions-Policy; `no-store` nas respostas da API; pedido que muda algo na API vindo de outro site é recusado (CSRF por Fetch Metadata).
- **Indexação:** descrição, canonical, Open Graph, `robots.txt`, `sitemap.xml`, manifesto e JSON-LD.
- **Testes:** `test_web.py` (cabeçalhos, cache, SRI, 404, CSRF, indexação, contraste) e, no navegador, acessibilidade, celular (320 e 390 px, também com fonte larga), teclado, sessão e antecipação das chamadas da API.
- **Atualizar o servidor:** `git pull && docker compose up -d --build && docker compose restart caddy` (docs/09).

## 08/10/2026: Render2Go no ar, segurança e experiência do cliente

### Implantação
- **Serviço em produção** em https://app.render2go.com:
  - servidor Hetzner CX23 em Nuremberg;
  - Docker Compose com API, orquestrador, Postgres e Caddy (HTTPS automático);
  - guia passo a passo no [`docs/09`](docs/09-implantacao.md).
- **1º lote real** (9 fotos de teste): pod pronto em 2 min 40 s, 9 vídeos de 3 s conferidos, pod encerrado 3,5 min depois do último vídeo, US$ 0,24 no total.
- **Correções achadas na implantação:**
  - a consulta de preço das GPUs leva a chave do RunPod (no servidor não há o proxy que a incluía);
  - a imagem não copia mais o `.env`;
  - o orquestrador funciona com código baixado sob `umask 077`;
  - o orquestrador tem um healthcheck próprio;
  - com a fila vazia, não há consulta de preço a cada 30 s.

### Nome do projeto: GPU2GO → Render2Go
- **Trocou:**
  - pacotes `render2go_worker` e `render2go_servico`;
  - comandos `render2go-*` e imagens;
  - variáveis `RENDER2GO_*`;
  - caminhos `/opt/render2go` e `~/.cache/render2go`;
  - páginas e documentação.
- **Compatibilidade:** variáveis com o prefixo antigo `GPU2GO_*` continuam valendo, sem sobrescrever as novas. O navegador migra o código de acesso e o identificador do dispositivo.
- **Mantidos de propósito:**
  - o bucket R2 `gpu2go`, porque renomear exigiria copiar o espelho de 36,6 GB;
  - o banco e o usuário Postgres `gpu2go` e os volumes da 1ª implantação, onde estão os dados;
  - o endereço do repositório no GitHub;
  - os registros históricos dos testes em GPU (`worker/evals/gpu-*`).
- Os passos para migrar o servidor estão no [`docs/09`](docs/09-implantacao.md), seção 0.

### Segurança e auditoria
- **Filtro de texto C1 v2** (`worker/filtros/c1.v2.json`): bloqueia também despir, roupa íntima e movimentos sexualizados ("tire a roupa", "sem roupa", "remove her bikini", "rebolando"…), inclusive com as letras separadas. Palavras comuns em marketing, como gostoso, sedução e decote, ficaram de fora.
- **Aviso "contém pessoas" no `/aprovar`:**
  - o detector roda no próprio servidor: YOLOX-tiny para pessoas, inclusive de costas, e YuNet para rostos;
  - a página mostra um selo por foto e um resumo do lote, em vermelho quando falta a autorização.
- **Registro de auditoria da autorização das pessoas das fotos:**
  - texto exibido e sua versão, com o SHA-256;
  - conta do cliente, IP, dispositivo, navegador, fuso e hora do clique;
  - SHA-256 de cada foto, que fica no banco mesmo depois que a foto é apagada;
  - exportação em JSON por pedido;
  - a caixa não pede nome.
- **ADR 0005 (proposto):** reserva um filtro de imagem nas fotos e nos quadros dos vídeos.
- **Fotos de entrada apagadas** do R2 assim que o pedido fecha.

### Teste gratuito e custo
- **Regras:**
  - um pedido só, com até 10 fotos, vídeos de 3 s em 480p;
  - até 10 fotos por dispositivo e 50 por rede (IP), para que várias pessoas de uma empresa consigam testar.
- **Agrupamento:** os testes esperam juntar 20 vídeos ou 10 minutos antes de subir um pod. Dez testes de 1 foto pagam uma subida em vez de dez.
- **Sinais no `/aprovar`:** tabela de dispositivos (troca de IP, vários códigos, mesmo navegador com outro identificador) e uso por IP.
- **Plano interno** (só o dono): sem limites e com aprovação automática; os filtros continuam valendo.

### Página do cliente
- **Previsão de tempo** sem expor as etapas internas ("começa em cerca de N min", "termina em cerca de N min"), com um indicador de atividade.
- **Vídeos:** o nome abre numa aba nova; "baixar" salva o arquivo.
- **Modelo de geração visível** (hoje o Wan 2.2), pronto para mais modelos; o gerador recusa modelo que não tem.
- **Estilo "Livre (descreva o que quiser)"** (`receitas.v2.json`), para pedidos que não se encaixam nos outros estilos.
- **Suporte** em https://app.render2go.com/suporte:
  - funciona com ou sem código de acesso;
  - tem limite contra spam;
  - envia por e-mail quando configurado (SMTP) e guarda a mensagem no `/aprovar`.
- **Aviso de cookies** em navegador novo: aceitar todos, só os necessários ou gerenciar. A escolha fica registrada no servidor.

### `/aprovar` (revisão)
- **Histórico** de pedidos feitos, aprovados e recusados, com cliente, IP, dispositivo, modelo, autorização e andamento.
- **Desativar o código** de um cliente.
- **Mensagens de suporte.**

## 07/10/2026: escala, subida rápida e fase 3

- **GPUs medidas:**
  - RTX PRO 6000: ~28 s por vídeo de 5 s em 480p, 1,9× a L40S pelo mesmo custo; virou a 1ª opção;
  - 720p: ~93 s;
  - 7 s de vídeo: ~160 s.
- **Pods:**
  - só nuvem secure, limite de 2 h e desligamento automático pelo menor entre tempo, orçamento e saldo;
  - teste de rede na subida, que descarta servidor lento;
  - troca automática de pod que falha ou fica lento.
- **Subida mais rápida:** download em partes (aria2) e SHA-256 em paralelo. A instalação caiu de ~5,5 min para 91 s.
- **Espelho dos modelos no R2** (36,6 GB) como reserva do Hugging Face; o Network Volume de teste foi apagado.
- **Pods automáticos pelo tamanho do lote** (`--pods auto`) e prazo de entrega (`--prazo`).
- **Fase 2 validada em pods reais:** 8 vídeos, 16,9 min, ~US$ 0,40.
- **Fase 3:**
  - API, banco e fila (pagos antes de testes, clientes intercalados);
  - conferência do 1º quadro, para que um vídeo trocado nunca chegue ao cliente;
  - worker da fila, orquestrador de pods e páginas do cliente e do aprovador;
  - ADR 0004 (filtro C1 e revisão humana), CI e guia de implantação.
- **Documentos:** [`docs/07`](docs/07-guia-tecnico-gpus-e-pods.md) (guia técnico) e [`docs/08`](docs/08-varios-clientes-e-demanda-alta.md) (vários clientes e isolamento).

## 06/10/2026: caminho A (marketing para pequenos negócios)

- **Etapa 2 concluída:**
  - medições numa RTX 4090;
  - compilador I2V v5 e T2V v3;
  - ADR 0001 aceito (T2V A14B fp8 + Lightning).
- **Etapa 3:** handler serverless, endpoints como código, bench e custos; imagem montada pelo RunPod a partir do GitHub (ADR 0002 aceito).
- **Caminho A escolhido** ([`docs/05`](docs/05-analise-de-negocio.md)):
  - presets v2, receitas e lote (pasta de fotos + um pedido);
  - preparo da foto, folha de conferência e acabamento (interpolação, altura, zoom);
  - várias GPUs por pod.
- **Rodada L40S:** ~55 s por vídeo. Análise de qualidade, velocidade e custo em [`docs/06`](docs/06-qualidade-velocidade-custo.md); ADR 0003 (arquitetura em escala).

## 05/10/2026: proposta e primeiras etapas

- **Proposta:** verificações, arquitetura, segurança e conformidade, e plano ([`docs/01`](docs/01-verificacoes.md) a [`docs/04`](docs/04-plano-de-implementacao.md)).
- **Etapa 1:** imagem Docker do worker (ComfyUI 0.38.0) e script de envio do workflow.
- **Etapa 2 (início):** presets, compilador versionado, candidatos T2V e "continuar vídeo".
