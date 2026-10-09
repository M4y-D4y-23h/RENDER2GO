# 10 — Identidade visual e front-end

Como as páginas do serviço são desenhadas, montadas e entregues: `/` (entrada pelo e-mail e painel do cliente), `/suporte`, `/termos`, `/privacidade`, `/cookies`, `/verificacao` (o "não sou um robô", dentro de um quadro), o painel do dono (no endereço secreto, só o dono) e a página de erro 404. As decisões de arquitetura (build, cache, cabeçalhos de segurança e CDN) estão no [ADR 0006](adr/0006-front-end-build-entrega-e-seguranca.md); contas, sessão em cookie, "não sou um robô" e painel, no [ADR 0007](adr/0007-contas-sessoes-e-painel.md).

- **Código:** `servico/web/src/` (TypeScript, CSS, HTML e ícones). O build (`npm run build`) gera `servico/web/publico/`, que a API entrega. As duas pastas são versionadas e o CI confere se `publico/` está em dia.
- **Marca:** `tools/marca.py` gera a fonte dos títulos, o logo, os ícones e a imagem de compartilhamento (seção 1).
- **Testes:** `servico/tests/test_web.py` (cabeçalhos, cache, CSRF, indexação e contraste) e `servico/tests/test_paginas.py` (navegador: fluxo completo, acessibilidade com axe-core, celular, teclado e sessão).

## 1. Identidade visual

### 1.1 Conceito

Dois pigmentos naturais do Brasil:
- **Anil** (índigo): ação e confiança. Botões principais, links, foco do teclado, passos.
- **Urucum** (vermelho-alaranjado): movimento, o "to go". O "2Go" do logo, o fim da barra de progresso, o selo da apresentação e o exemplo de texto.

Os neutros são **tinta** (texto) e **papel** (fundo), levemente azulados para conversar com o anil. O resultado é claro, confiável e com um ponto de calor, sem o visual genérico de "IA neon".

### 1.2 Logo

- **Símbolo:** quadrado arredondado anil, com um play branco e dois traços de movimento em urucum (a foto que vira vídeo e "vai").
- **Palavra:** "Render2Go" em Bricolage Grotesque 800, com "Render" na cor do texto e "2Go" em urucum. Está em contornos dentro do SVG: aparece igual em qualquer aparelho, sem depender da fonte.
- **Arquivos** (em `servico/web/publico/`, gerados pelo `tools/marca.py`; não editar à mão):

| Arquivo | Uso |
|---|---|
| `estatico/logo.svg` e `logo-escuro.svg` | Cabeçalho (o navegador escolhe pelo tema) |
| `estatico/icone.svg` | Favicon e cabeçalho em telas de até 360 px |
| `favicon.ico` (16, 32 e 48 px) | Navegadores antigos |
| `estatico/icone-180.png` | Tela inicial do iPhone |
| `estatico/icone-192.png`, `icone-512.png`, `icone-mascaravel-512.png` | Manifesto (tela inicial do Android) |
| `estatico/og.jpg` (1200 × 630, 64 KB) | Prévia do link no WhatsApp e nas redes |

- **Regras de uso:** respiro mínimo de metade da altura do símbolo; não distorcer, não trocar as cores, não pôr sobre foto sem uma área lisa atrás.

### 1.3 Cores

Os tokens ficam em `servico/web/src/css/tokens.css`. O modo escuro segue o aparelho (`prefers-color-scheme`).

| Token | Claro | Escuro | Uso |
|---|---|---|---|
| `--cor-fundo` (papel) | `#f7f7fb` | `#0f0e17` | Fundo da página |
| `--cor-superficie` | `#ffffff` | `#181722` | Cartões e campos |
| `--cor-superficie-2` | `#f1f0f7` | `#21202d` | Áreas recuadas (soltar fotos, autorização) |
| `--cor-borda` | `#e2e0ec` | `#2e2c3d` | Divisórias e contorno de cartões |
| `--cor-borda-campo` | `#8e8aa6` | `#6f6b88` | Contorno de campos e botões |
| `--cor-texto` (tinta) | `#1a1830` | `#eeedf6` | Texto principal |
| `--cor-texto-2` | `#45425c` | `#cbc8db` | Texto de apoio |
| `--cor-texto-3` | `#5e5b73` | `#a6a3ba` | Dicas e metadados |
| `--cor-marca` (anil) | `#4338ca` | `#a49eff` | Ações, links e foco |
| `--cor-marca-suave` | `#eeedff` | `#26234d` | Fundo de destaques e avisos informativos |
| `--cor-acento` (urucum) | `#e5532e` | `#ff7d57` | Acento decorativo (logo, barra de progresso) |
| `--cor-acento-texto` | `#b5361a` | `#ff9a7c` | Texto em urucum |
| `--cor-ok` / `--cor-ok-fundo` | `#13693f` / `#e4f5ec` | `#72d8a2` / `#10291d` | Pronto, sucesso, aprovar |
| `--cor-erro` / `--cor-erro-fundo` | `#b42318` / `#fdebe9` | `#ff9f94` / `#3a1614` | Recusado, erro, ações destrutivas |
| `--cor-alerta` / `--cor-alerta-fundo` | `#855000` / `#fff3d1` | `#f7c65a` / `#33270c` | Em análise, pessoas nas fotos |

- **Contraste (WCAG 2.2, nível AA):** todo texto sobre o seu fundo tem pelo menos 4,5:1; bordas de campos e o anel de foco, pelo menos 3:1. O `test_web.py` confere 28 pares nos dois temas. Cor nova entra como token, nos dois temas, e ganha um par no teste.
- **Urucum puro (`--cor-acento`) não é cor de texto pequeno:** para texto, use `--cor-acento-texto`.

### 1.4 Tipografia

- **Títulos:** Bricolage Grotesque 700 (eixo óptico 32), licença SIL OFL 1.1 (o texto da licença acompanha o arquivo). O arquivo tem 13 KB porque leva só os caracteres do português (letras com acento, ç, aspas curvas, travessões); outro caractere cai na fonte do sistema.
- **Texto:** a fonte do próprio aparelho (SF Pro no iPhone e no Mac, Roboto no Android, Segoe UI no Windows). Nada a baixar.
- **Carregamento:** `font-display: optional`, sem pré-carregamento. A fonte nunca atrasa a primeira pintura nem troca depois de desenhada (sem deslocamento). Na primeira visita, os títulos podem sair na fonte do sistema; daí em diante, vêm do cache.
- **Escala** (fluida, com `clamp()`): título 1 de 28 a 42 px, título 2 de 20 a 24 px, título 3 de 17 a 19 px, texto 16 px, apoio 14 px, selos 13 px. Entrelinha 1,55 no texto e 1,15 nos títulos. Contagens e tabelas usam números tabulares.

### 1.5 Ícones

33 ícones de linha (24 × 24, traço 2, pontas arredondadas), desenhados para o projeto em `servico/web/src/icones.json`. O HTML usa `<!--@icone nome-->` (expandido no build) e o TypeScript, `icone(nome)`. São sempre decorativos (`aria-hidden`): o nome vem do texto ao lado ou do `aria-label` do botão.

### 1.6 Forma, profundidade e movimento

- **Raios:** 8 px (campos pequenos), 12 px (botões e campos), 16 px (cartões), 24 px (diálogos e aviso de cookies) e pílula (selos).
- **Sombras:** três níveis suaves. No escuro, a separação vem mais das bordas.
- **Movimento:** 120 a 200 ms, curva suave; pulso na previsão de tempo, brilho no carregamento, entrada de cartões e diálogos. Com "reduzir movimento" ligado no aparelho, as animações param.

### 1.7 Voz e texto

- Português do Brasil, tratamento por "você", frases curtas.
- Botões dizem o que fazem: "Enviar pedido com 3 fotos", "Usar este exemplo", "Baixar".
- Plural correto ("1 foto", "2 fotos"), sem "foto(s)".
- Erro diz o que fazer ("Escreva o motivo da recusa (o cliente vê)"), e o foco vai para o campo.
- O cliente vê tempo e estado, nunca as etapas internas (regra da previsão, docs/09, seção 5.1.3).

## 2. Regras de layout

1. **Celular primeiro.** As regras sem media query valem para telas pequenas. Telas maiores só acrescentam, com `min-width` em `em` (que acompanha o zoom do usuário): 40em (640 px) e 64em (1024 px).
2. **Recipiente fluido:** `min(100% − 2 × margem, 72rem)`, com margem lateral de 16 a 32 px (`clamp`). Páginas de formulário (suporte, 404) usam 46rem.
3. **A ordem na tela é a ordem do HTML.** Teclado e leitor de tela seguem a mesma sequência que os olhos. Colunas lado a lado só a partir de 64em:
   - entrada: apresentação e "Como funciona" | cartão do código;
   - painel do cliente: novo pedido (7 partes) | seus pedidos (5 partes);
   - revisão: menu de seções (13rem) | conteúdo.
4. **Espaço em múltiplos de 4 px** (tokens `--esp-1` a `--esp-8`): 16 px entre campos, 24 a 32 px entre blocos, 48 px entre seções da revisão.
5. **Alvos de toque com pelo menos 44 px** (botões, campos, navegação). Em telas de toque, até os botões pequenos crescem para 44 px. Campos têm 48 px de altura. O botão de remover uma foto da prévia tem 32 px, acima dos 24 px do WCAG 2.2 AA, para não cobrir a miniatura.
6. **Nada de rolagem lateral, nem em 320 px, nem com fonte larga.** Toda grade de uma coluna usa `minmax(0, 1fr)`: conteúdo comprido (a opção mais longa de um `<select>`, um endereço) não empurra a página para o lado. Tabelas largas rolam dentro da própria caixa, que recebe foco pelo teclado. O teste de celular confere também com a DejaVu Sans, mais larga, que é a fonte padrão de muitos Linux.
7. **Cabeçalho fixo de 60 px:** logo (leva ao início) e navegação. Abaixo de 480 px, a navegação fica só com ícones (o rótulo continua para o leitor de tela); até 360 px, o logo vira só o símbolo.
8. **Texto corrido com até 68 caracteres por linha.**
9. **Pedido em passos numerados:** Fotos → Estilo e descrição → Ajustes → Autorização → Enviar. Cada passo é um `<fieldset>` com legenda.
10. **Imagens com proporção e tamanho declarados:** nada desloca o layout ao carregar. Quem já tem o código guardado vê o painel desde o primeiro quadro, sem piscar o formulário de entrada.

## 3. Componentes

Todos em `servico/web/src/css/componentes.css`, usados pelas três páginas:

| Componente | Classes | Observação |
|---|---|---|
| Cabeçalho e rodapé | `.topo`, `.navegacao`, `.rodape` | Página atual com `aria-current="page"` |
| Botão | `.botao` + `primario`, `sucesso`, `perigo`, `fantasma`, `pequeno`, `largo` | Ocupado: `aria-busy="true"` (anel girando, desativado) |
| Campo | `.campo`, `.dica`, `.contador`, `.senha` | Erro: `aria-invalid="true"`; dica ligada por `aria-describedby` |
| Caixa de marcar grande | `.marcacao` (+ `.exigida`) | A área toda é clicável |
| Cartão e passo | `.cartao` (+ `.elevado`), `.passo` | Passos numerados por contador CSS |
| Aviso | `.aviso` + `info`, `ok`, `erro`, `alerta` | Sempre com ícone e texto |
| Selo de estado | `.selo.estado-<status>`, `.selo.pessoas` | Cores dos estados (seção 1.3) |
| Previsão e progresso | `.previsao`, `.pulso`, `progress`, `.progresso-texto` | Barra anil → urucum |
| Fotos | `.soltar`, `.previa`, `.miniatura` (+ `.pequena`, `.baixa`) | Remover por foto; ✓ quando a foto termina de subir; selo "baixa resolução" e aviso `#aviso-fotos` quando a foto vai ser ampliada 1,5× ou mais |
| Sugestão e exemplos | `.sugestao`, `.exemplos-pedido` | Exemplo do estilo escolhido; exemplos simples e detalhados num `<details>` |
| Totais do relatório | `.resumo-gpus` (`<dl>`) | Números do período na seção "GPUs" do painel |
| Finalidades | `.finalidades`, `.blocos`, `.bloco` (+ `.em-breve`, `.ver-todas`), `.escolhida`, `.modelo-uso`, `.interesse-ok` | Bloco disponível é um `<button>`; o "Em breve" não é clicável e tem o botão "Tenho interesse" (com `aria-label` citando a opção), que vira a confirmação e recebe o foco |
| Lista completa | `dialog.todas-finalidades`, `.grupo-opcoes`, `.opcao` | Agrupada pelo que o cliente envia; "Usar" fecha a janela e escolhe; o foco volta a quem abriu |
| Vídeos e player | `.videos`, `.video`, `<dialog>` | Assistir na página, abrir em outra aba, baixar |
| Confirmação | `dialog.confirmacao` | No lugar do `confirm()` do navegador |
| Tabela | `.tabela` | Rola dentro da caixa; `caption` e `scope` |
| Filtros | `.filtros` | Botões com `aria-pressed` |
| Aviso de cookies | `.cookies` | Primeiro no teclado; Esc fecha quando aberto pelo rodapé |
| Vazio e carregando | `.vazio`, `.esqueleto` | O que fazer quando não há nada; esqueleto em vez de tela em branco |

## 4. Os atributos pedidos

| Atributo | O que foi feito | Como é conferido |
|---|---|---|
| **Core Web Vitals** | LCP: só o CSS e um script de 0,7 KB bloqueiam a pintura; quem tem código vê o painel no 1º quadro; o script do `<head>` já pede à API os dados do painel, em paralelo com o resto. CLS: tamanhos declarados, fonte sem troca, sessão marcada antes de desenhar. INP: a lista se atualiza trocando só o cartão que mudou; miniaturas desenhadas uma por vez, fora da thread principal | Medição da seção 5 |
| **Minificação de recursos** | esbuild 0.28.2 (versão fixada) minifica JS e CSS; um script por página (o código da revisão não vai mais para o cliente); HTML sem recuo nem comentários; o Caddy comprime com zstd ou gzip | CI: `npm run build` e `publico/` em dia |
| **Otimização de mídias** | Logo e ícones em SVG; `og.jpg` de 64 KB; fonte woff2 de 13 KB; miniaturas das fotos reduzidas no próprio navegador (até 60 na tela); no painel, a mesma foto não é baixada de novo a cada atualização, com `loading="lazy"` e `decoding="async"`; o vídeo só carrega quando o cliente abre o player | `test_paginas.py` (prévia) |
| **Carregamento de scripts** | Scripts da página com `defer`; o único bloqueante (`inicio.js`, 0,7 KB, em cache por 1 ano) marca a sessão e antecipa as chamadas da API; nenhum script de terceiros | `test_paginas.py` (uma chamada só por recurso) |
| **Uso de CDN** | Não ligado agora (seção 7): o IP real do cliente sustenta o teste gratuito e a auditoria, e a CDN da Cloudflare trocaria esse IP. Fotos e vídeos já vão e vêm do R2, que é rede da Cloudflare. Os arquivos estão prontos para CDN: versão no endereço, cache de 1 ano e `immutable` | `test_web.py` (cache) |
| **Responsividade (celular primeiro)** | CSS base para celular, `min-width` em em, colunas só em telas largas | Testes em 320 e 390 px |
| **Layout fluido** | `clamp()` em títulos e margens, `min()` nos recipientes, grades `auto-fill`/`minmax`, medidas em rem/em | Capturas em 320, 390 e 1280 px |
| **Toque e interação** | Alvos de 44 px; área de soltar toda clicável; escolher pasta escondido no celular (não existe lá); arrastar e soltar fotos e pastas no computador; nada depende de passar o mouse | `test_paginas.py` (altura dos alvos) |
| **Semântica HTML** | `header`, `nav`, `main`, `footer` e `section` com título; um `h1` por tela; `fieldset`/`legend` nos passos; listas; `<time>`, `<progress>`, `<dialog>`; tabelas com `caption` e `scope`; botão para ação e link para navegação | axe-core sem violações |
| **Leitores de tela** | Regiões vivas com anúncios curtos (envio, progresso a cada 25 %, mudança de estado, erros); a atualização automática não repete nada; `aria-describedby`, `aria-invalid`, `aria-busy`, `aria-pressed` e `aria-current`; nome em todo botão só de ícone | axe-core (claro e escuro) |
| **Contraste e legibilidade** | Paleta AA nos dois temas; texto de 16 px (mínimo de 13 px nos selos); entrelinha 1,55; até 68 caracteres por linha | `test_web.py` (28 pares) e axe-core |
| **Navegação por teclado** | "Pular para o conteúdo"; foco visível em tudo (anel anil de 3 px); Esc fecha diálogos; o foco volta para quem abriu; a atualização da lista não tira o foco de quem está nela; tabelas roláveis focáveis | `test_paginas.py` (teclado) |
| **URLs amigáveis** | `/`, `/suporte`, `/termos`, `/privacidade` e `/cookies` sem extensão; o painel no endereço secreto (com a barra no fim); `/suporte/` redireciona para `/suporte`; arquivos em `/estatico/`; endereço inexistente abre uma página 404 com caminhos de volta (a API continua em JSON) | `test_web.py` (404) |
| **Tags meta** | Título e descrição por página; canonical; Open Graph e cartão do Twitter (com a imagem 1200 × 630 e texto alternativo); `theme-color` claro e escuro; `color-scheme`; manifesto; ícones; `noindex` no painel, na verificação e no 404; JSON-LD (Organization e WebSite) | `test_web.py` (meta) |
| **Velocidade de indexação** | Sem `robots.txt`, por decisão do dono (não listar caminhos para quem ataca); páginas privadas com `noindex` no cabeçalho; `sitemap.xml` (`/`, `/suporte`, `/termos`, `/privacidade` e `/cookies`), canonical, página inicial legível sem JavaScript (título, descrição e "Como funciona"), páginas servidas da memória com ETag | `test_web.py` (indexação) |
| **Navegação intuitiva** | Cabeçalho igual em todas as páginas (logo, Pedidos, Suporte, Sair) com a página atual marcada; passos numerados; exemplo de texto por estilo; na revisão, menu de seções com o número de pendentes, também no título da aba; 404 com saídas | Capturas |
| **Feedback visual** | Estados de passar o mouse, toque, foco, desativado e ocupado; prévia das fotos com ✓ por foto enviada; progresso em bytes e %; aviso ao tentar sair durante o envio; contador de caracteres; selos de estado; esqueleto ao carregar; erros no próprio campo | `test_paginas.py` |
| **Consistência visual** | Um conjunto de tokens (cores, espaço, raios, sombras, tipos), componentes e ícones compartilhados, uma folha de estilo, partes de HTML comuns (cabeça, topo, rodapé) montadas no build | Revisão das capturas |
| **Proteção contra XSS e CSRF** | XSS: CSP sem `unsafe-inline` nem `unsafe-eval`, `object-src 'none'`, `base-uri 'none'` e Trusted Types obrigatório; nenhum `innerHTML` (todo texto por `textContent`); URL vinda da API só vira link ou vídeo se for https; SRI no CSS e nos scripts. CSRF: a sessão vai num cookie HttpOnly e SameSite, todo pedido que muda algo exige o cabeçalho `X-Render2Go` (outro site não consegue mandá-lo sem CORS, que a API não libera), e todo POST na API vindo de outro site é recusado (Fetch Metadata: `Sec-Fetch-Site` ou `Origin`). O "não sou um robô" da Cloudflare roda só na página `/verificacao`, num quadro, com CSP própria; as outras páginas não carregam script de terceiros. Clickjacking: `frame-ancestors 'none'` e `X-Frame-Options` | `test_web.py` e navegador sem erro de CSP |
| **Integração segura** | URLs assinadas do R2 conferidas antes do uso; a CSP só libera a própria API e o R2; `Referrer-Policy: no-referrer` (as URLs assinadas não vazam); COOP e CORP; `Permissions-Policy` desliga câmera, microfone, localização, pagamento, USB e sensores; respostas da API com `no-store`; HSTS no Caddy; nenhuma fonte ou script de terceiros; links externos com `noopener noreferrer` | `test_web.py` |

## 5. Medições (antes × depois)

- **Como foi medido (08/10/2026):**
  - as duas versões (commit `3228c7e` e a nova), cada uma atrás do Caddy 2.10 com HTTPS e HTTP/2, como em produção;
  - Chromium 141 (Playwright), celular de 390 × 844 px;
  - rede de 4G lenta (1,6 Mbps, 150 ms de ida e volta) e CPU 4× mais lenta;
  - mediana de 5 medições.
- **"Pronto"** é o momento em que o formulário do pedido fica utilizável (estilos carregados).

| Tela | Visita | FCP | LCP | CLS | Pronto | Transferido |
|---|---|---|---|---|---|---|
| Entrada (sem código) | 1ª | 0,44 → 0,52 s | 0,44 → 0,52 s | 0 → 0 | — | 15 → 43 KB |
| Entrada (sem código) | de volta | 0,25 → 0,26 s | 0,25 → 0,26 s | 0 → 0 | — | 2 → 5 KB |
| Painel (com código) | 1ª | 0,42 → 0,53 s | **0,71 → 0,54 s** | 0 → 0 | 0,67 → 0,69 s | 17 → 45 KB |
| Painel (com código) | de volta | 0,23 → 0,30 s | **0,41 → 0,30 s** | **0,028 → 0** | 0,39 → 0,39 s | 4 → 7 KB |

- **Leitura:**
  - o painel, onde o cliente passa o tempo, ficou mais rápido no que importa (LCP) e sem deslocamento;
  - a primeira visita sem código ficou 0,08 s mais lenta nessa rede lenta: o CSS, o HTML e a fonte são maiores;
  - nas visitas de volta, o HTML é conferido no servidor a cada vez (ETag). É de propósito: ninguém fica com página velha depois de uma atualização, e as páginas usam SRI, que exige isso. O resto vem do cache;
  - tudo dentro da faixa "bom" das Core Web Vitals (LCP até 2,5 s, CLS até 0,1).
- **O que foi descartado no caminho**, porque piorava as medidas:
  - pré-carregar a fonte (no Chrome, fonte `optional` pré-carregada passa a bloquear a primeira pintura);
  - o desfoque do cabeçalho (`backdrop-filter`), que pesa na rolagem em celulares simples;
  - `text-wrap: pretty` em todo parágrafo.

## 6. Como mexer nas páginas

- **Editar só `servico/web/src/`** e rodar `npm ci && npm run build` em `servico/web`. Versionar `src/` e `publico/` juntos; o CI recusa `publico/` desatualizado. Nunca editar `publico/` à mão.
- **Página nova:**
  1. `src/paginas/<nome>.html`, com as partes `<!--@cabeca-->`, `<!--@topo-->`, `<!--@rodape-->` e `<!--@anuncios-->`;
  2. script em `src/ts/<nome>.ts`, acrescentado em `PAGINAS_JS` no `construir.mjs`;
  3. rota na API (`servir`, em `render2go_servico/api.py`) e, se for pública, no `sitemap.xml` (`construir.mjs`).
- **Painel do dono:** `src/paginas/painel.html` e `src/ts/painel.ts`. O build põe o script em `publico/privado/painel.js`, que a API só entrega no endereço secreto (`RENDER2GO_ADMIN_CAMINHO`); nenhuma página pública cita esse endereço (o teste de invasão confere).
- **Páginas legais:** `termos.html`, `privacidade.html` e `cookies.html`. Os dados da empresa entram pelo `.env` (`[[EMPRESA_NOME]]` e os outros marcadores viram o valor de `RENDER2GO_EMPRESA_*` quando a API carrega as páginas). Mudança de texto que muda direitos ou dados tratados = versão nova (`contas.DOCUMENTOS`), aceita de novo na próxima entrada.
- **Ícone novo:** em `src/icones.json`, no mesmo desenho (24 × 24, traço 2).
- **Cor nova:** token em `src/css/tokens.css`, nos dois temas, e o par em `PARES` no `test_web.py`.
- **Marca (logo, ícones, `og.jpg`, fonte):** `tools/marca.py` (precisa de rede e do Chromium; comando no cabeçalho do arquivo). `--check` confere a fonte e os SVGs versionados.
- **Endereço público** (canonical, Open Graph, sitemap): `https://app.render2go.com`. Outro domínio: `RENDER2GO_URL_PUBLICA=https://... npm run build`.
- **Navegadores:** Chrome, Edge e Firefox 100 ou mais novos e Safari 15.4 ou mais novo (iPhone com iOS 15.4+), que é o alvo do esbuild em `construir.mjs`. Recursos mais novos entram só como melhoria: sem `:has()`, a caixa de autorização marcada não muda de cor; sem `createImageBitmap` com redução, a prévia usa a própria foto.
- **Regras que a CSP impõe:** nada de `innerHTML`, `style=` no HTML, `<script>` embutido ou `eval`. Textos novos por `textContent`; elementos por `criar()` (`src/ts/comum.ts`).
- **Testes:**
  - `cd servico && uv run pytest -q`: cabeçalhos, cache, CSRF, indexação, contraste;
  - `uv run --with playwright==1.56.0 pytest -q tests/test_paginas.py`: fluxo completo, axe-core em claro e escuro, celular em 320 e 390 px (também com fonte larga), teclado e sessão (precisa de `npm ci` em `servico/web`, por causa do axe-core);
  - `RENDER2GO_CAPTURAS=<pasta>` grava capturas das telas.

## 7. Fora deste trabalho e próximos passos

- **CDN (proxy da Cloudflare, "nuvem laranja"):** não foi ligada porque muda o IP que a API enxerga, e o teste gratuito (teto por IP) e o registro de auditoria dependem dele. Para ligar com segurança:
  1. no Caddy, confiar só nas faixas de IP da Cloudflare (`trusted_proxies`) e ler o `CF-Connecting-IP` (`client_ip_headers`);
  2. o `Caddyfile` já repassa à API o IP resolvido (`header_up X-Render2Go-IP {client_ip}`): basta descomentar as duas linhas do bloco `servers` e fechar o firewall para aceitar só a Cloudflare (`docs/13`, seção 6.4);
  3. na Cloudflare: SSL "Full (strict)", sem Rocket Loader e sem nada que reescreva HTML, CSS ou JS (o SRI bloquearia);
  4. testar o teto do teste gratuito com dois IPs diferentes antes de abrir para clientes.

  Ganho esperado: a conexão TLS fecha em São Paulo em vez de Nuremberg (centenas de ms na primeira visita de quem está no Brasil).
- **Miniaturas no servidor para o painel:** hoje a revisão baixa a foto original (com `lazy` e sem repetir). Uma versão reduzida, gerada na análise de pessoas, pouparia banda do revisor. Mexe no armazenamento e no apagamento das fotos, por isso ficou de fora.
- **Política de privacidade e termos de uso (LGPD):** publicadas em 09/10/2026 (`/termos`, `/privacidade`, `/cookies`), com links no rodapé. Falta a revisão de um advogado (`docs/13`, seção 6.7).
- **Escolha do formato** (vertical, quadrado, horizontal): a API aceita, a página não mostra. É decisão de produto.
- **Baixar todos os vídeos de um pedido (.zip):** depende do servidor (docs/09, seção 8).
- **Search Console:** cadastrar `app.render2go.com` e enviar o `sitemap.xml`.
- **Compatibilidade:** `/app.js` (recarrega a página antiga uma vez) e `/estilo.css` atendem navegadores com a página anterior em cache. Podem sair em algumas semanas.
