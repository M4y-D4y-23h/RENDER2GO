# ADR 0006: front-end com build próprio, entrega com cache por versão e cabeçalhos de segurança

- **Status:** aceito (08/10/2026), a pedido do dono do projeto: definir a identidade visual e as regras de layout e aplicar desempenho, acessibilidade e segurança no front-end.
- **Contexto:** as páginas da fase 3 (ADR 0003) eram um `app.js` único, gerado pelo `tsc` e servido igual para o cliente e para o aprovador, uma folha de estilo simples e três HTMLs servidos com `FileResponse`, sem regra de cache. O doc 02 previa um PWA em Next.js; o serviço seguiu sem framework e não há motivo para mudar: são três páginas, e a imagem do serviço não roda Node.

## Decisão

1. **Build próprio com esbuild** (versão fixada no `package-lock.json`, sem framework):
   - fontes em `servico/web/src/` (TypeScript em módulos, CSS com tokens, HTML com partes comuns e ícones);
   - um script por página (`inicio.js`, `cliente.js`, `suporte.js`, `aprovar.js`), JS e CSS minificados, HTML sem recuo nem comentários;
   - a saída `servico/web/publico/` é versionada (a imagem não roda Node) e o CI confere se está em dia, como já fazia com o `app.js`;
   - a checagem de tipos continua no `tsc`.
2. **Cache por versão:**
   - o build põe `?v=<12 hex do SHA-256>` em toda referência a `/estatico/...` e `integrity` (SRI, sha384) nos scripts e no CSS;
   - a API carrega `publico/` na memória ao subir e responde `/estatico/...` com a versão certa como `public, max-age=31536000, immutable`; sem a versão, ou com uma antiga, entrega com `no-cache`;
   - as páginas saem com `no-cache` e ETag: o navegador confere a cada visita (304 quando nada mudou). Com SRI, uma página velha em cache, se o arquivo antigo tiver saído do cache, quebraria; revalidar a página evita isso.
3. **Cabeçalhos de segurança em toda resposta** (middleware da API):
   - CSP sem `unsafe-inline` nem `unsafe-eval`, com `script-src 'self'`, `object-src 'none'`, `base-uri 'none'`, `frame-ancestors 'none'` e **Trusted Types obrigatório** (`require-trusted-types-for 'script'`, `trusted-types 'none'`); `img-src` aceita `blob:` (miniaturas desenhadas no navegador) e o R2;
   - `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Resource-Policy` (`same-origin`; `cross-origin` só nas imagens da marca, para as prévias de link), `Permissions-Policy` desligando câmera, microfone, localização, pagamento, USB e sensores;
   - `Cache-Control: no-store` e `X-Robots-Tag: noindex` nas respostas da API; `noindex` também no `/aprovar`.
4. **CSRF por Fetch Metadata:** todo POST/PUT/PATCH/DELETE na API com `Sec-Fetch-Site` diferente de `same-origin`/`none` (ou, em navegador antigo, `Origin` de outro endereço) recebe 403. Pedido sem esses cabeçalhos não vem de navegador (pods, curl) e segue exigindo o token. O token continua no cabeçalho `Authorization`, guardado no navegador: o navegador não o envia sozinho, e a CSP com Trusted Types fecha a porta do XSS que poderia lê-lo.
5. **Antecipação no `<head>`:** um script bloqueante de 0,7 KB marca a sessão antes da primeira pintura (o painel aparece sem piscar o formulário de entrada) e, com o código e o identificador do dispositivo guardados, já pede à API os dados do painel; a página usa essas respostas uma vez só.
6. **Página 404 em HTML** para endereço inexistente aberto no navegador; a API continua respondendo JSON.
7. **Fonte dos títulos própria** (Bricolage Grotesque, OFL), recortada para o pt-BR (13 KB), com `font-display: optional` e sem pré-carregamento. O logo é SVG em contornos e não depende dela.
8. **CDN da Cloudflare (proxy) não ligada por enquanto.** O IP real do cliente sustenta o teto do teste gratuito e o registro de auditoria; com o proxy, a API passaria a ver o IP da Cloudflare. Os arquivos já estão prontos para CDN (versão no endereço, cache longo); os passos para ligar estão no docs/10, seção 7.
9. **Compressão no Caddy:** `encode zstd gzip` (zstd nos navegadores novos).

## Alternativas descartadas

- **Next.js, Vite ou outro framework:** muitas dependências para três páginas; a imagem do serviço precisaria de Node ou de um passo de build separado.
- **Nome de arquivo com hash** (`cliente.3fa2.js`): igual em efeito ao `?v=`, mas obrigaria a API a rotear nomes variáveis.
- **Cache curto no HTML** (`max-age` de minutos): visitas de volta mais rápidas, mas uma página velha com SRI pode quebrar logo depois de uma atualização.
- **Token em cookie `HttpOnly`:** protegeria o token de um XSS, mas exigiria token CSRF em todo formulário e mudanças na API; com CSP estrita e Trusted Types, o ganho não pagou a mudança agora.
- **CSS crítico embutido no HTML:** exigiria hash na CSP por página; o CSS inteiro tem ~7 KB comprimido e fica um ano em cache.

## Consequências

- **Medidas** (docs/10, seção 5): no painel do cliente, LCP de 0,71 s para 0,54 s na 1ª visita e de 0,41 s para 0,30 s na volta, CLS de 0,028 para 0; a 1ª visita sem código ficou 0,08 s mais lenta em 4G lenta (CSS, HTML e fonte maiores). Tudo na faixa "bom" das Core Web Vitals.
- **Quem mexe nas páginas** edita `src/` e roda `npm run build`; `publico/` nunca é editado à mão.
- **Cor nova** só entra como token com contraste conferido (`test_web.py`); a acessibilidade é conferida pelo axe-core no CI.
- **Atualizar o servidor:** depois do `git pull`, `docker compose up -d --build` e `docker compose restart caddy` (o Caddyfile mudou).
- `/app.js` e `/estilo.css` continuam respondendo por algumas semanas, para navegadores com a página antiga em cache (o `/app.js` recarrega a página uma vez).
