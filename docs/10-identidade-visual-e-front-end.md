# 10 — Identidade visual e front-end

Como as páginas do serviço são desenhadas, montadas e entregues: `/` (entrada pelo e-mail e painel do cliente), `/suporte`, `/termos`, `/privacidade`, `/cookies`, `/verificacao` (o "não sou um robô", dentro de um quadro), o painel do dono (no endereço secreto, só o dono) e a página de erro 404. As decisões de arquitetura (build, cache, cabeçalhos de segurança e CDN) estão no [ADR 0006](adr/0006-front-end-build-entrega-e-seguranca.md); contas, sessão em cookie, "não sou um robô" e painel, no [ADR 0007](adr/0007-contas-sessoes-e-painel.md).

- **Código:** `servico/web/src/` (TypeScript, CSS, HTML e ícones). O build (`npm run build`) gera `servico/web/publico/`, que a API entrega. As duas pastas são versionadas e o CI confere se `publico/` está em dia.
- **Marca:** `tools/marca.py` gera a fonte dos títulos, o logo, os ícones e a imagem de compartilhamento (seção 1).
- **Testes:** `servico/tests/test_web.py` (cabeçalhos, cache, CSRF, indexação e contraste) e `servico/tests/test_paginas.py` (navegador: fluxo completo, acessibilidade com axe-core, celular, teclado, sessão e estabilidade do layout).
- **Revisões:** 08/10/2026 (primeira versão) e 10/10/2026 (topo conforme a sessão, rodapé único, ilustração e brilho na entrada, índice nas páginas de texto, janelas que sobem de baixo no celular e CLS zero mesmo com a API lenta; seções 4.1 e 5.1).

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
| `estatico/icone.svg` | Favicon, rodapé e cabeçalho em telas de até 360 px |
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
| `--cor-brilho-marca` / `--cor-brilho-acento` | anil a 11% / urucum a 8% | anil claro a 12% / urucum a 7% | Brilho decorativo no fundo da entrada (só para quem não entrou) |

- **Contraste (WCAG 2.2, nível AA):** todo texto sobre o seu fundo tem pelo menos 4,5:1; bordas de campos e o anel de foco, pelo menos 3:1. O `test_web.py` confere 28 pares nos dois temas e também o texto sobre o brilho decorativo, no pior caso (os dois brilhos no ponto mais forte, um sobre o outro: o texto de dicas fica em 4,7:1 no claro). Campos nunca ficam sobre o brilho, só dentro de cartões. Cor nova entra como token, nos dois temas, e ganha um par no teste.
- **Preferências do aparelho:** com "mais contraste" ligado (`prefers-contrast: more`), as dicas passam a usar a cor do texto de apoio e as divisórias ficam como as bordas dos campos. Com cores forçadas (alto contraste do Windows), selos, previsões, sugestões, filtros marcados, a página atual, os botões principais e as barras de progresso ganham borda, o item atual do índice fica sublinhado, o "404" perde o degradê e o foco usa a cor do sistema (`Highlight`). Na impressão, o tema claro vira preto no branco, sem sombras.
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
- **Movimento:** 120 a 200 ms, curva suave; pulso na previsão de tempo, brilho no carregamento, entrada de cartões e diálogos (no celular, as janelas sobem de baixo); ao tocar, botões e blocos afundam 1 px. Com "reduzir movimento" ligado no aparelho, as animações param.
- **Brilho e ilustração da entrada:** para quem ainda não entrou, o fundo da página inicial tem dois brilhos suaves (anil à esquerda, urucum à direita) e, a partir de 640 px, uma ilustração desenhada para o projeto: fotos empilhadas → traços de movimento em urucum → um vídeo com play e a barra anil → urucum (a mesma história do logo). É um SVG dentro do próprio HTML (`src/partes/ilustracao.html`, 0,7 KB comprimido, nada a baixar), decorativo (`aria-hidden`), com as cores dos tokens (troca sozinha no tema escuro). No celular fica de fora, para o cartão de entrada subir.

### 1.7 Voz e texto

- Português do Brasil, tratamento por "você", frases curtas.
- Botões dizem o que fazem: "Enviar pedido com 3 fotos", "Usar este exemplo", "Baixar".
- Plural correto ("1 foto", "2 fotos"), sem "foto(s)".
- Erro diz o que fazer ("Escreva o motivo da recusa (o cliente vê)"), e o foco vai para o campo.
- O cliente vê tempo e estado, nunca as etapas internas (regra da previsão, docs/09, seção 5.1.3).

## 2. Regras de layout

1. **Celular primeiro.** As regras sem media query valem para telas pequenas. Telas maiores só acrescentam, com `min-width` em `em` (que acompanha o zoom do usuário): 40em (640 px) e 64em (1024 px).
2. **Recipiente fluido:** `min(100% − 2 × margem, 72rem)`, com margem lateral de 16 a 32 px (`clamp`), nunca menor que a área segura do aparelho (`env(safe-area-inset-*)`: iPhone com entalhe deitado). Páginas de formulário e de texto (suporte, 404, termos, privacidade, cookies) usam 46rem de texto.
3. **A ordem na tela é a ordem do HTML.** Teclado e leitor de tela seguem a mesma sequência que os olhos. Colunas lado a lado só a partir de 64em:
   - entrada: apresentação e "Como funciona" | cartão do código;
   - painel do cliente: novo pedido (7 partes) | seus pedidos (5 partes);
   - páginas de texto com índice (termos, privacidade): "Nesta página" (15rem, fixo ao rolar) | texto (até 46rem); no celular, o índice vem antes do texto;
   - revisão: menu de seções (13rem) | conteúdo.
4. **Espaço em múltiplos de 4 px** (tokens `--esp-1` a `--esp-8`): 16 px entre campos, 24 a 32 px entre blocos, 48 px entre seções da revisão.
5. **Alvos de toque com pelo menos 44 px** (botões, campos, navegação). Em telas de toque, até os botões pequenos crescem para 44 px. Campos têm 48 px de altura. O botão de remover uma foto da prévia tem 32 px, acima dos 24 px do WCAG 2.2 AA, para não cobrir a miniatura.
6. **Nada de rolagem lateral, nem em 320 px, nem com fonte larga.** Toda grade de uma coluna usa `minmax(0, 1fr)`: conteúdo comprido (a opção mais longa de um `<select>`, um endereço) não empurra a página para o lado. Tabelas largas rolam dentro da própria caixa, que recebe foco pelo teclado. O teste de celular confere também com a DejaVu Sans, mais larga, que é a fonte padrão de muitos Linux.
7. **Cabeçalho fixo de 60 px:** logo (leva ao início) e navegação conforme a sessão. Quem não entrou vê "Entrar" (leva a `/#entrar` e põe o foco no cartão de entrada; o Tab seguinte cai no e-mail) e "Suporte"; quem entrou vê "Pedidos", "Suporte" e "Sair", em todas as páginas, também nas de texto e na 404. O `inicio.js` marca a sessão no `<html>` (`.com-sessao`) antes do primeiro quadro, e o CSS mostra um ou outro (`.so-sem-sessao`, `.so-com-sessao`): nada pisca nem empurra os itens. Abaixo de 480 px, a navegação fica só com ícones (o rótulo continua para o leitor de tela); até 360 px, o logo vira só o símbolo.
8. **Texto corrido com até 68 caracteres por linha.**
9. **Pedido em passos numerados:** Fotos → Estilo e descrição → Ajustes → Autorização → Enviar. Cada passo é um `<fieldset>` com legenda.
10. **Imagens com proporção e tamanho declarados:** nada desloca o layout ao carregar. Quem já tem o código guardado vê o painel desde o primeiro quadro, sem piscar o formulário de entrada.
11. **O que chega da API tem o lugar reservado** (CLS zero, mesmo com a API lenta): a linha "Conectado como…" já tem a altura final (até 2 linhas no celular e 1 no computador, com reticências; o texto inteiro fica no título); o "Novo pedido" mostra um esqueleto de pelo menos uma tela de altura até as receitas e a conta chegarem juntas, e o saldo, o aviso do plano e as opções entram no mesmo quadro; o link de suporte abaixo dos pedidos fica invisível, no lugar, até a lista chegar; o quadro do "não sou um robô" (entrada e suporte) e o aviso "Você está conectado" do suporte já vêm no HTML, e o e-mail da conta entra numa linha de altura fixa. Nunca inserir algo acima do que já está na tela.
12. **Rodapé igual em todas as páginas**, também na 404: símbolo e frase da marca, links (Termos de Uso, Privacidade, Cookies, Suporte e Preferências de cookies) e a identificação da empresa. No computador, a marca fica à esquerda e os links à direita; em telas de toque, os links têm 44 px de altura.
13. **Janelas (diálogos):** no celular sobem de baixo, na largura toda, com os cantos de cima arredondados e o respiro da área segura (perto do polegar); no computador, ficam no centro. O aviso de cookies vai de ponta a ponta no celular e, no computador, fica no canto inferior esquerdo (27rem), sem cobrir o cartão de entrada.
14. **Impressão:** só o conteúdo (sem topo, navegação, botões, índice nem aviso de cookies), em preto e branco; links para fora mostram o endereço.

## 3. Componentes

Em `servico/web/src/css/componentes.css` (os de uma página só, em `paginas.css`):

| Componente | Classes | Observação |
|---|---|---|
| Cabeçalho e rodapé | `.topo`, `.navegacao`, `.rodape` (+ `.rodape-marca`, `.identificacao`) | Página atual com `aria-current="page"`; "Entrar", ou "Pedidos" e "Sair", conforme a sessão (`.so-sem-sessao`, `.so-com-sessao`, que valem desde o 1º quadro) |
| Janela | `<dialog>` (+ `.dialogo-corpo`, `.dialogo-acoes`) | No celular, sobe de baixo na largura toda; Esc fecha; o foco volta a quem abriu |
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
| Finalidades | `.finalidades`, `.blocos`, `.bloco` (+ `.disponivel`, `.em-breve`, `.ver-todas`), `.escolhida`, `.modelo-uso`, `.interesse-ok` | Bloco disponível é um `<button>` na linha inteira, com borda anil, seta e título maior (o caminho principal); o "Em breve" é compacto, não é clicável e tem o selo e o botão "Tenho interesse" (com `aria-label` citando a opção) na mesma linha; o botão vira a confirmação e recebe o foco |
| Lista completa | `dialog.todas-finalidades`, `.grupo-opcoes`, `.opcao` | Agrupada pelo que o cliente envia; "Usar" fecha a janela e escolhe; o foco volta a quem abriu |
| Vídeos e player | `.videos`, `.video`, `<dialog>` | Assistir na página, abrir em outra aba, baixar |
| Confirmação | `dialog.confirmacao` | No lugar do `confirm()` do navegador |
| Tabela | `.tabela` | Rola dentro da caixa; `caption` e `scope` |
| Filtros | `.filtros` | Botões com `aria-pressed` |
| Aviso de cookies | `.cookies` | Primeiro no teclado; Esc fecha quando aberto pelo rodapé; "Aceitar todos" e "Só os necessários" com o mesmo destaque (recusar tão fácil quanto aceitar, como pede o guia de cookies da ANPD) |
| Vazio e carregando | `.vazio`, `.esqueleto`, `.esqueleto-pedido` | O que fazer quando não há nada; esqueleto em vez de tela em branco, com a altura do que vai chegar |
| Linha da conta | `.conta-linha`, `.conta-resumo` | Altura reservada antes de a API responder; texto inteiro no `title` |
| Verificação ("não sou um robô") | `.verificacao`, `.quadro-verificacao` | Lugar de 4,25rem reservado no HTML (no suporte, só para quem não entrou); o quadro carrega a página `/verificacao` |
| Ilustração da entrada | `.ilustracao` (`src/partes/ilustracao.html`) | SVG no HTML, decorativo, cores por token; só a partir de 40em |
| Índice de página de texto | `.indice`, `.texto-legal.com-indice`, `.corpo-legal` | "Nesta página" num `<nav>` com título; no computador, fixo ao lado do texto; a seção que está na tela ganha `aria-current="location"` (`legal.ts`) |

## 4. Os atributos pedidos

| Atributo | O que foi feito | Como é conferido |
|---|---|---|
| **Core Web Vitals** | LCP: só o CSS e um script de 0,7 KB bloqueiam a pintura; quem tem código vê o painel no 1º quadro; o script do `<head>` já pede à API os dados do painel (receitas, pedidos e conta), em paralelo com o resto. CLS: tamanhos declarados, fonte sem troca, sessão marcada antes de desenhar (também no topo das outras páginas), lugar reservado para o que chega da API e esqueleto no "Novo pedido": zero, mesmo com a API mais lenta que a página. INP: a lista se atualiza trocando só o cartão que mudou; miniaturas desenhadas uma por vez, fora da thread principal | Medição da seção 5; `test_paginas.py` (CLS com a API lenta, em 390 e 1280 px) |
| **Minificação de recursos** | esbuild 0.28.2 (versão fixada) minifica JS e CSS; um script por página (o código da revisão não vai mais para o cliente); HTML sem recuo nem comentários; o Caddy comprime com zstd ou gzip | CI: `npm run build` e `publico/` em dia |
| **Otimização de mídias** | Logo e ícones em SVG; ilustração da entrada em SVG dentro do HTML (0,7 KB comprimido, sem pedido a mais); `og.jpg` de 64 KB; fonte woff2 de 13 KB; símbolo do rodapé com `loading="lazy"` (o mesmo arquivo do favicon, em cache); miniaturas das fotos reduzidas no próprio navegador (até 60 na tela); no painel, a mesma foto não é baixada de novo a cada atualização, com `loading="lazy"` e `decoding="async"`; o vídeo só carrega quando o cliente abre o player | `test_paginas.py` (prévia) |
| **Carregamento de scripts** | Scripts da página com `defer`; o único bloqueante (`inicio.js`, 0,7 KB, em cache por 1 ano, antes do CSS) marca a sessão em todas as páginas do cliente (para o topo) e, no painel, antecipa as chamadas da API; nenhum script de terceiros fora da `/verificacao` | `test_paginas.py` (uma chamada só por recurso); `test_web.py` (o script de sessão em cada página) |
| **Uso de CDN** | Não ligado (reavaliado em 10/10; seção 7): o IP real do cliente sustenta o teste gratuito e a auditoria, e a CDN da Cloudflare trocaria esse IP. Fotos e vídeos já vão e vêm do R2, que é rede da Cloudflare. Os arquivos estão prontos para CDN: versão no endereço, cache de 1 ano e `immutable` | `test_web.py` (cache) |
| **Responsividade (celular primeiro)** | CSS base para celular, `min-width` em em, colunas só em telas largas; no celular, janelas que sobem de baixo e o índice das páginas de texto antes do texto; no computador, índice fixo ao lado e aviso de cookies no canto | Testes em 320 e 390 px (janela ancorada embaixo, na largura toda) |
| **Layout fluido** | `clamp()` em títulos e margens, margens que respeitam a área segura (`env(safe-area-inset-*)`), `min()` nos recipientes, grades `auto-fill`/`minmax`, medidas em rem/em | Capturas em 320, 390, 768 e 1280 px |
| **Toque e interação** | Alvos de 44 px (também os links do rodapé em telas de toque); estado de toque (`:active`) em botões, blocos e navegação; janelas perto do polegar; área de soltar toda clicável; escolher pasta escondido no celular (não existe lá); arrastar e soltar fotos e pastas no computador; nada depende de passar o mouse | `test_paginas.py` (altura dos alvos) |
| **Semântica HTML** | `header`, `nav`, `main`, `footer` e `section` com título; um `h1` por tela; índice das páginas de texto num `<nav>` com título; `fieldset`/`legend` nos passos; listas; `<time>`, `<progress>`, `<dialog>`; tabelas com `caption` e `scope`; botão para ação ("Sair") e link para navegação ("Entrar", "Pedidos") | axe-core sem violações |
| **Leitores de tela** | Regiões vivas com anúncios curtos (envio, progresso a cada 25 %, mudança de estado, erros); a atualização automática não repete nada; `aria-describedby`, `aria-invalid`, `aria-busy`, `aria-pressed` e `aria-current` (`page` no topo e no rodapé, `location` no índice); esqueletos e ilustração escondidos (`aria-hidden`); nome em todo botão só de ícone | axe-core (claro e escuro) |
| **Contraste e legibilidade** | Paleta AA nos dois temas, também sobre o brilho decorativo; "mais contraste" e cores forçadas respeitados; impressão em preto e branco; texto de 16 px (mínimo de 13 px nos selos); entrelinha 1,55; até 68 caracteres por linha | `test_web.py` (28 pares e o brilho no pior caso) e axe-core |
| **Navegação por teclado** | "Pular para o conteúdo"; foco visível em tudo (anel anil de 3 px); "Entrar" no topo leva o foco ao cartão de entrada (e o Tab, ao e-mail); Esc fecha diálogos; o foco volta para quem abriu; a atualização da lista não tira o foco de quem está nela; tabelas roláveis focáveis | `test_paginas.py` (teclado e topo) |
| **URLs amigáveis** | `/`, `/suporte`, `/termos`, `/privacidade` e `/cookies` sem extensão; âncoras legíveis para as seções (`/#entrar`, `/privacidade#direitos`, `#seguranca`, `#uso-de-cookies`…); o painel no endereço secreto (com a barra no fim); `/suporte/` redireciona para `/suporte`; arquivos em `/estatico/`; endereço inexistente abre uma página 404 com caminhos de volta (a API continua em JSON) | `test_web.py` (404); `test_paginas.py` (índice) |
| **Tags meta** | Título e descrição por página; canonical; Open Graph e cartão do Twitter (com a imagem 1200 × 630 e texto alternativo); `theme-color` claro e escuro; `color-scheme`; `application-name` e `apple-mobile-web-app-title` (nome curto na tela inicial); manifesto; ícones; `noindex` no painel, na verificação e no 404; JSON-LD (Organization e WebSite) | `test_web.py` (meta) |
| **Velocidade de indexação** | Sem `robots.txt`, por decisão do dono (não listar caminhos para quem ataca); páginas privadas com `noindex` no cabeçalho; `sitemap.xml` (`/`, `/suporte`, `/termos`, `/privacidade` e `/cookies`), canonical, página inicial legível sem JavaScript (título, descrição e "Como funciona"), páginas servidas da memória com ETag | `test_web.py` (indexação) |
| **Navegação intuitiva** | Cabeçalho igual em todas as páginas, conforme a sessão ("Entrar", ou "Pedidos" e "Sair") e com a página atual marcada; rodapé igual também na 404; "Nesta página" nas páginas de texto; a opção disponível em destaque e as "Em breve" compactas; passos numerados; exemplo de texto por estilo; na revisão, menu de seções com o número de pendentes, também no título da aba; 404 com saídas conforme a sessão | Capturas; `test_paginas.py` (topo e "Sair") |
| **Feedback visual** | Estados de passar o mouse, toque (`:active`), foco, desativado e ocupado; prévia das fotos com ✓ por foto enviada; progresso em bytes e %; aviso ao tentar sair durante o envio; contador de caracteres; selos de estado; esqueleto ao carregar (também no "Novo pedido"); seção atual marcada no índice; erros no próprio campo | `test_paginas.py` |
| **Consistência visual** | Um conjunto de tokens (cores, espaço, raios, sombras, tipos), componentes e ícones compartilhados, uma folha de estilo, partes de HTML comuns (sessão, cabeça, topo, rodapé, ilustração) montadas no build; o mesmo topo e o mesmo rodapé em todas as páginas, também na 404 | Revisão das capturas; `test_web.py` (topo e rodapé em cada página) |
| **Proteção contra XSS e CSRF** | XSS: CSP sem `unsafe-inline` nem `unsafe-eval`, `object-src 'none'`, `base-uri 'none'` e Trusted Types obrigatório; nenhum `innerHTML` (todo texto por `textContent`); URL vinda da API só vira link ou vídeo se for https; SRI no CSS e nos scripts. CSRF: a sessão vai num cookie HttpOnly e SameSite, todo pedido que muda algo exige o cabeçalho `X-Render2Go` (outro site não consegue mandá-lo sem CORS, que a API não libera), e todo POST na API vindo de outro site é recusado (Fetch Metadata: `Sec-Fetch-Site` ou `Origin`). O "não sou um robô" da Cloudflare roda só na página `/verificacao`, num quadro, com CSP própria; as outras páginas não carregam script de terceiros. "Sair" é um POST com o cabeçalho, e a marca de conectado do navegador nunca dá acesso a nada (só escolhe o que mostrar; quem decide é o cookie HttpOnly, conferido pela API). Clickjacking: `frame-ancestors 'none'` e `X-Frame-Options` | `test_web.py`, navegador sem erro de CSP e teste de invasão (73 verificações) |
| **Integração segura** | URLs assinadas do R2 conferidas antes do uso; a CSP só libera a própria API e o R2; `Referrer-Policy: no-referrer` (as URLs assinadas não vazam); COOP e CORP; `Permissions-Policy` desliga câmera, microfone, localização, pagamento, USB e sensores; respostas da API com `no-store`; HSTS no Caddy; nenhuma fonte ou script de terceiros; links externos com `noopener noreferrer` | `test_web.py` |

### 4.1 Revisão de 10/10/2026

O dono pediu uma nova passada em todas as telas, com foco na estética, na experiência de uso e nos mesmos atributos. Cada atributo foi conferido de novo, nas capturas (320, 390, 768 e 1280 px, claro e escuro) e com medição antes e depois (seção 5.1). O que mudou:

- **Estética e identidade:**
  - entrada com o brilho anil e urucum no fundo e a ilustração "fotos → vídeo" (seção 1.6);
  - "O que você quer criar?": a opção disponível ocupa a linha inteira, com borda anil, seta e título maior; as "Em breve" ficaram compactas (selo e "Tenho interesse" na mesma linha);
  - rodapé redesenhado (símbolo, frase da marca, links e identificação da empresa) e igual em todas as páginas; a 404 tinha um rodapé próprio, sem "Cookies", sem "Preferências de cookies" e sem a identificação;
  - páginas de texto com ritmo de leitura (espaço entre seções e parágrafos, marcadores anil) e, nos termos e na privacidade, o índice "Nesta página", fixo ao lado no computador, com a seção atual marcada;
  - no pedido, a descrição do modelo encostava no botão "Trocar" no celular.
- **Navegação:**
  - o topo segue a sessão: quem não tinha entrado via "Pedidos" (que só levava à entrada), e quem tinha entrado não via "Sair" fora do painel. Agora o visitante vê "Entrar", que leva ao cartão de entrada, e quem entrou vê "Pedidos" e "Sair" também no suporte, nas páginas de texto e na 404;
  - a 404 e o "Voltar" do suporte dizem "Ir para os pedidos" ou "Ir para o início", conforme a sessão; o suporte mostra o prazo de resposta (5 dias úteis) logo no começo;
  - janelas que sobem de baixo no celular (o CSS das janelas foi reescrito no padrão "celular primeiro": a folha de baixo é a base, e o centro só vale a partir de 40em); aviso de cookies no canto inferior esquerdo no computador.
- **Core Web Vitals:** desde a tela de finalidades (09/10), as opções e o saldo chegam da API depois da 1ª pintura. Na visita de volta, com o CSS e o JavaScript em cache, o painel pintava o cartão vazio e depois empurrava os pedidos e o rodapé: **CLS de 0,59 no celular e 0,24 no computador** (faixa "ruim"; seção 5.1). A medição de 08/10, com a rede lenta para tudo, não pegava esse caso. Na 1ª visita, a linha "Conectado como … · saldo R$ …" também quebrava depois da pintura (0,076). Agora: a linha tem a altura reservada; a conta também é pedida no `<head>` e chega junto com as receitas, e o saldo, o aviso do plano e as opções entram no mesmo quadro; um esqueleto segura o lugar do "Novo pedido"; "Sair" já vem marcado no HTML; o link de suporte abaixo dos pedidos só aparece com a lista; o quadro do "não sou um robô" e o aviso de conta do suporte já têm o lugar no HTML. Resultado: CLS zero em todas as telas, também com a API 1,5 s mais lenta que a página, de 320 a 1440 px. Custo: no painel, o maior texto (a dica das opções) passa a aparecer junto com os dados, e o LCP fica uma ida e volta à API mais tarde (seção 5.1), ainda bem dentro da faixa "bom".
- **Acessibilidade e legibilidade:** "mais contraste", cores forçadas e impressão (seção 1.3); contraste do texto sobre o brilho conferido no teste; `aria-current="location"` no índice; "Entrar" leva o foco ao cartão de entrada.
- **Cookies (LGPD):** "Aceitar todos" deixou de ser o botão principal. Aceitar e recusar ("Só os necessários") têm o mesmo destaque, como recomenda o guia de cookies da ANPD.
- **Tags meta:** `application-name` e `apple-mobile-web-app-title`.
- **Conferidos e mantidos:** minificação (já completa), CDN (continua desligada por causa do IP real; seção 7), URLs (já sem extensão; ganharam âncoras nas seções), indexação (sem `robots.txt`, decisão do dono), proteção contra XSS e CSRF e integração segura (nada novo usa `innerHTML`, `style=` ou script embutido; o teste de invasão segue com 73 de 73).
- **Testes novos:** topo conforme a sessão, "Sair" encerrando a sessão no servidor, marca antiga sem sessão e o que já vem reservado no HTML antes da API ("Sair", aviso de conta do suporte, quadro da verificação) (`test_paginas.py`); índice da privacidade (alvos, marcação e impressão); CLS com a API lenta e o esqueleto saindo quando a API falha; janela ancorada embaixo no celular; os dois botões do aviso de cookies iguais; topo, rodapé e script de sessão em cada página e o contraste sobre o brilho (`test_web.py`).
- **Descartado no caminho:** `scrollbar-gutter: stable`, que deixava uma faixa vazia à direita.

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

### 5.1 Revisão de 10/10/2026

- **Como foi medido:** a versão de antes desta revisão (commit `ff23487`) e a nova, do mesmo jeito da seção 5 (Caddy 2.10 com HTTPS e HTTP/2, Chromium 141, mediana de 5), agora com o "não sou um robô" ligado como em produção (chave pública de teste da Cloudflare: o quadro e a página `/verificacao` carregam; o script da Cloudflare não, porque aqui não há internet).
- **1) Rede lenta para tudo** (celular de 390 × 844 px, 4G lenta de 1,6 Mbps e 150 ms, CPU 4× mais lenta; 1ª visita e visita de volta):

| Tela | Visita | FCP | LCP | CLS | Transferido |
|---|---|---|---|---|---|
| Entrada (sem sessão) | 1ª | 0,51 s → 0,54 s | 0,51 s → 0,54 s | 0 → 0 | 54 KB → 57 KB |
| Entrada (sem sessão) | de volta | 0,34 s → 0,35 s | 0,34 s → 0,35 s | 0 → 0 | 1 KB → 1 KB |
| Painel (com sessão) | 1ª | 0,50 s → 0,51 s | 0,50 s → 0,96 s | 0,076 → 0 | 56 KB → 59 KB |
| Painel (com sessão) | de volta | 0,38 s → 0,38 s | 0,38 s → 0,60 s | 0 → 0 | 4 KB → 4 KB |
| Suporte (sem sessão) | 1ª | 0,53 s → 0,51 s | 0,53 s → 0,51 s | 0 → 0 | 36 KB → 39 KB |
| Suporte (sem sessão) | de volta | 0,32 s → 0,34 s | 0,32 s → 0,35 s | 0 → 0 | 1 KB → 1 KB |
| Termos | 1ª | 0,54 s → 0,52 s | 0,54 s → 0,52 s | 0 → 0 | 36 KB → 39 KB |
| Termos | de volta | 0,33 s → 0,38 s | 0,36 s → 0,38 s | 0 → 0 | 0,3 KB → 0,3 KB |

- **2) Visita de volta com o cache cheio**, o caso de quem já usa o serviço: CSS e JavaScript vêm do cache, o HTML e a API vão até o servidor; ida e volta de 0, 120 e 250 ms (de São Paulo a Nuremberg são ~200 ms), CPU 4× mais lenta. Painel com sessão:

| Ida e volta até o servidor | Largura | LCP | CLS |
|---|---|---|---|
| 0 ms | 390 px | 0,10 s → 0,11 s | 0,589 → 0 |
| 0 ms | 1280 px | 0,14 s → 0,14 s | 0,236 → 0 |
| 120 ms | 390 px | 0,26 s → 0,38 s | 0,589 → 0 |
| 120 ms | 1280 px | 0,25 s → 0,37 s | 0,236 → 0 |
| 250 ms | 390 px | 0,37 s → 0,62 s | 0,589 → 0 |
| 250 ms | 1280 px | 0,38 s → 0,63 s | 0,236 → 0 |

- **3) Entrada e suporte com o "não sou um robô"** (mesmas condições do item 2, 120 ms):

| Tela | Largura | CLS (mediana) | CLS (pior de 3) |
|---|---|---|---|
| Entrada (sem sessão) | 390 px | 0,008 → 0 | 0,008 → 0 |
| Entrada (sem sessão) | 1280 px | 0,017 → 0 | 0,018 → 0 |
| Suporte (sem sessão) | 390 px | 0,005 → 0 | 0,005 → 0 |
| Suporte (sem sessão) | 1280 px | 0,004 → 0 | 0,004 → 0 |
| Suporte (com sessão) | 390 px | 0,061 → 0 | 0,061 → 0 |
| Suporte (com sessão) | 1280 px | 0,017 → 0 | 0,017 → 0 |

- **Com a API 1,5 s mais lenta que a página** (a 1ª pintura sai muito antes dos dados): CLS 0 no painel de 320 a 1440 px. O teste `test_panel_does_not_shift_when_the_api_answers_after_the_first_paint` faz o mesmo com 0,8 s, em 390 e 1280 px, a cada rodada do CI.
- **Leitura:**
  - o problema real estava no item 2: o painel antigo pintava o cartão vazio e depois empurrava pedidos e rodapé, com CLS na faixa "ruim" (acima de 0,25) no celular, em toda visita de quem já é cliente. A medição de 08/10 (item 1, rede lenta para tudo) não mostrava isso, porque ali os dados chegavam antes da 1ª pintura;
  - agora o CLS é zero em todas as telas e condições medidas;
  - o custo é o LCP do painel: o maior texto (a dica das opções) aparece junto com os dados, uma ida e volta à API depois da 1ª pintura. Continua bem dentro da faixa "bom" (até 2,5 s) em todas as condições, e é a mesma hora em que o cliente já pode escolher a opção;
  - entrada, suporte e termos ficaram iguais em velocidade (diferenças de poucos centésimos, dentro do ruído da medição); cada página tem 1 ou 2 pedidos a mais (o script de sessão, de 0,7 KB, nas páginas que não o tinham, e o símbolo do rodapé, que é o mesmo arquivo do favicon e só carrega quando o rodapé se aproxima da tela) e 2 a 3 KB a mais na 1ª visita (CSS e a ilustração); tudo em cache por 1 ano.

## 6. Como mexer nas páginas

- **Editar só `servico/web/src/`** e rodar `npm ci && npm run build` em `servico/web`. Versionar `src/` e `publico/` juntos; o CI recusa `publico/` desatualizado. Nunca editar `publico/` à mão.
- **Página nova:**
  1. `src/paginas/<nome>.html`, com as partes `<!--@sessao-->` (antes de `<!--@cabeca-->`, para o topo saber da sessão antes de desenhar), `<!--@cabeca-->`, `<!--@topo-->`, `<!--@rodape-->` e `<!--@anuncios-->`;
  2. script em `src/ts/<nome>.ts`, acrescentado em `PAGINAS_JS` no `construir.mjs`, chamando `iniciarCookies()` (`cookies.ts`) e `iniciarTopo()` (`topo.ts`, que liga o "Sair"); página de texto pode usar o `legal.js`, que já faz os dois e marca o índice;
  3. rota na API (`servir`, em `render2go_servico/api.py`) e, se for pública, no `sitemap.xml` (`construir.mjs`).
- **Mostrar algo só para quem entrou (ou só para quem não entrou):** classes `so-com-sessao` e `so-sem-sessao`, no CSS, sem JavaScript; valem desde o primeiro quadro. Se a página confirmar com a API que a sessão acabou (401), `semSessao()` (`topo.ts`) tira a marca e volta o topo de visitante.
- **Conteúdo que vem da API:** reservar o lugar antes (altura mínima, esqueleto ou `visibility: hidden` no lugar final) e trocar tudo num mesmo quadro; nunca inserir acima do que já está na tela. O teste `test_panel_does_not_shift_when_the_api_answers_after_the_first_paint` atrasa a API e mede o CLS.
- **Índice em página de texto:** `<nav class="indice" aria-labelledby="...">` com links para as seções (`<section id="..." aria-labelledby="...">`), a classe `com-indice` no `<article>` e as seções dentro de `<div class="corpo-legal">`; o `legal.ts` marca a seção visível.
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
  - `uv run --with playwright==1.56.0 pytest -q tests/test_paginas.py`: fluxo completo, axe-core em claro e escuro, celular em 320 e 390 px (também com fonte larga), teclado, sessão e topo, índice das páginas de texto e CLS com a API lenta (precisa de `npm ci` em `servico/web`, por causa do axe-core);
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
- **Medir no servidor real:** as medições daqui são de laboratório (seção 5). Depois de publicar, conferir no PageSpeed Insights e, com tráfego, nos dados de campo (CrUX / Search Console, "Core Web Vitals").
- **Compatibilidade:** `/app.js` (recarrega a página antiga uma vez) e `/estilo.css` atendem navegadores com a página anterior em cache. Podem sair em algumas semanas.
