// Build das páginas do serviço (docs/10-identidade-visual-e-front-end.md). Rodar com `npm run build`.
//
// - TypeScript (src/ts) e CSS (src/css) passam pelo esbuild: um arquivo por página, minificado.
// - HTML (src/paginas) é montado com as partes comuns (src/partes), os ícones (src/icones.json) e o
//   endereço público, e minificado (sem recuo nem comentários).
// - Cada referência a /estatico/... ganha ?v=<hash do conteúdo> (cache de 1 ano na API) e os
//   <script> e a folha de estilo ganham integrity (SRI).
// - A saída fica em publico/, versionada: a imagem do serviço não roda Node, e o CI confere se
//   publico/ está em dia com src/. Os arquivos da marca (fonte, logo, ícones, og.jpg) vêm do
//   tools/marca.py e não são tocados aqui.
// - O script do painel vai para publico/privado/ (fora de /estatico/): a API só o entrega no endereço
//   secreto do painel, e a página do painel o chama pelo caminho relativo "painel.js".
// - Sem robots.txt: ele seria uma lista pública de caminhos. O que não é público responde com
//   X-Robots-Tag: noindex (a API), e o sitemap lista só as páginas públicas.
import { build } from "esbuild";
import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const RAIZ = dirname(fileURLToPath(import.meta.url));
const SRC = join(RAIZ, "src");
const PUBLICO = join(RAIZ, "publico");
const ESTATICO = join(PUBLICO, "estatico");
// endereço usado nas tags de compartilhamento, no canonical e no sitemap
const ORIGEM = (process.env.RENDER2GO_URL_PUBLICA || "https://app.render2go.com").replace(/\/+$/, "");
// navegadores suportados: os dos últimos anos (Safari 15+, Chrome/Edge/Firefox 100+)
const ALVOS = ["es2020", "chrome100", "edge100", "firefox100", "safari15", "ios15"];
const PAGINAS_JS = ["inicio", "cliente", "suporte", "legal", "verificacao"];
const PRIVADO = join(PUBLICO, "privado"); // script do painel: só no endereço secreto (não em /estatico/)
const PUBLICAS = ["/", "/suporte", "/termos", "/privacidade", "/cookies"]; // páginas do sitemap
const ICONES = JSON.parse(readFileSync(join(SRC, "icones.json"), "utf8"));

const ler = (caminho) => readFileSync(caminho, "utf8");
const hash = (dados, alg = "sha256") => createHash(alg).update(dados).digest();
const versao = (dados) => hash(dados).toString("hex").slice(0, 12);

// 1) limpa o que este script gera (os arquivos da marca ficam)
for (const nome of readdirSync(PUBLICO)) {
  if (/\.(html|webmanifest|txt|xml)$/.test(nome)) rmSync(join(PUBLICO, nome));
}
for (const nome of readdirSync(ESTATICO)) {
  if (/\.js$/.test(nome) || nome === "estilo.css") rmSync(join(ESTATICO, nome));
}
rmSync(PRIVADO, { recursive: true, force: true });

// 2) JavaScript e CSS
const comum = { bundle: true, minify: true, target: ALVOS, charset: "utf8", legalComments: "none", logLevel: "warning" };
await build({
  ...comum,
  entryPoints: PAGINAS_JS.map((n) => join(SRC, "ts", `${n}.ts`)),
  outdir: ESTATICO,
  entryNames: "[name]",
  format: "iife",
});
await build({ ...comum, entryPoints: [join(SRC, "ts", "painel.ts")], outfile: join(PRIVADO, "painel.js"), format: "iife" });
await build({ ...comum, entryPoints: [join(SRC, "css", "estilo.css")], outfile: join(ESTATICO, "estilo.css"), external: ["/estatico/*"] });

// 3) versões: ?v=<hash> em toda referência a um arquivo existente de publico/estatico (e da raiz)
const conteudo = (url) => {
  const arquivo = url.startsWith("/estatico/") ? join(ESTATICO, url.slice(10)) : join(PUBLICO, url.slice(1));
  return existsSync(arquivo) ? readFileSync(arquivo) : null;
};
const versionar = (texto) =>
  texto.replace(/(?<=["'(\s}])(\/estatico\/[a-z0-9._-]+|\/favicon\.ico|\/manifest\.webmanifest)(?=["')\s])/g, (url) => {
    const dados = conteudo(url);
    if (!dados) throw new Error(`referência a arquivo inexistente: ${url}`);
    return `${url}?v=${versao(dados)}`;
  });

const css = join(ESTATICO, "estilo.css");
writeFileSync(css, versionar(ler(css)));

// 4) manifesto e sitemap (sem robots.txt, de propósito)
const manifesto = {
  name: "Render2Go",
  short_name: "Render2Go",
  description: "Suas fotos viram vídeos curtos, prontos para postar.",
  lang: "pt-BR",
  dir: "ltr",
  start_url: "/",
  scope: "/",
  display: "standalone",
  background_color: "#f7f7fb",
  theme_color: "#f7f7fb",
  icons: [
    { src: "/estatico/icone-192.png", sizes: "192x192", type: "image/png" },
    { src: "/estatico/icone-512.png", sizes: "512x512", type: "image/png" },
    { src: "/estatico/icone-mascaravel-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    { src: "/estatico/icone.svg", sizes: "any", type: "image/svg+xml" },
  ],
};
writeFileSync(join(PUBLICO, "manifest.webmanifest"), versionar(JSON.stringify(manifesto, null, 2)) + "\n");
writeFileSync(
  join(PUBLICO, "sitemap.xml"),
  '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
    PUBLICAS.map((c) => `  <url><loc>${ORIGEM}${c}</loc></url>\n`).join("") +
    "</urlset>\n",
);

// 5) HTML
const icone = (nome, classes = "") => {
  if (!ICONES[nome]) throw new Error(`ícone desconhecido: ${nome}`);
  const cls = ["icone", ...classes.split(/\s+/).filter(Boolean)].join(" ");
  return `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="${ICONES[nome]}"/></svg>`;
};
const integridade = (url) => `sha384-${hash(conteudo(url.split("?")[0]), "sha384").toString("base64")}`;

for (const arquivo of readdirSync(join(SRC, "paginas")).filter((n) => n.endsWith(".html")).sort()) {
  let html = ler(join(SRC, "paginas", arquivo));
  for (let nivel = 0; nivel < 4 && /<!--@[a-z-]+-->/.test(html); nivel++) {
    html = html.replace(/<!--@([a-z-]+)-->/g, (_, parte) => ler(join(SRC, "partes", `${parte}.html`)));
  }
  html = html.replace(/<!--@icone ([a-z-]+)( [a-z -]+)?-->/g, (_, nome, classes) => icone(nome, classes ?? ""));
  html = versionar(html).replaceAll("{{ORIGEM}}", ORIGEM);
  const pagina = /<body[^>]*data-pagina="([a-z-]+)"/.exec(html)?.[1];
  if (pagina) html = html.replaceAll(`data-nav="${pagina}"`, `data-nav="${pagina}" aria-current="page"`);
  html = html.replace(/<(script|link)\b([^>]*?)(src|href)="(\/estatico\/[a-z0-9._-]+\.(?:js|css)\?v=[0-9a-f]+)"/g,
    (tag, nome, antes, attr, url) => `<${nome}${antes}${attr}="${url}" integrity="${integridade(url)}"`);
  // painel: o script relativo ("painel.js", servido só no endereço secreto) também ganha versão e SRI
  html = html.replace(/<script src="painel\.js"/g, () => {
    const dados = readFileSync(join(PRIVADO, "painel.js"));
    return `<script src="painel.js?v=${versao(dados)}" integrity="sha384-${hash(dados, "sha384").toString("base64")}"`;
  });
  const restante = /<!--@|\{\{[A-Z]+\}\}/.exec(html);
  if (restante) throw new Error(`${arquivo}: marcador não expandido perto de "${html.slice(restante.index, restante.index + 40)}"`);
  // minificação segura: sem comentários, sem recuo e sem linhas vazias (as páginas não têm <pre>)
  html = html.replace(/<!--[\s\S]*?-->/g, "");
  html = html.split("\n").map((l) => l.trim()).filter(Boolean).join("\n") + "\n";
  writeFileSync(join(PUBLICO, arquivo), html);
}

const tamanhos = [...readdirSync(PUBLICO).filter((n) => /\.(html|webmanifest|txt|xml)$/.test(n)), ...readdirSync(ESTATICO).filter((n) => /\.(js|css)$/.test(n)).map((n) => `estatico/${n}`), "privado/painel.js"]
  .sort()
  .map((n) => `${String(readFileSync(join(PUBLICO, n)).length).padStart(7)}  publico/${n}`);
console.log(tamanhos.join("\n"));
