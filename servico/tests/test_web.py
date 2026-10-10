"""Entrega das páginas (docs/10): cabeçalhos de segurança, cache por versão, SRI, 404 em HTML,
proteção contra CSRF, arquivos de indexação e contraste das cores da identidade visual."""

import base64
import hashlib
import json
import re

import pytest
from test_api import PAINEL, Armazem, cliente_teste, config_teste, h

from render2go_servico.api import PUBLICO

PAGINAS = {
    "/": "index.html",
    "/suporte": "suporte.html",
    "/termos": "termos.html",
    "/privacidade": "privacidade.html",
    "/cookies": "cookies.html",
    f"{PAINEL}/": "painel.html",
}
TOKENS = PUBLICO.parent / "src" / "css" / "tokens.css"


R2 = "https://conta.r2.cloudflarestorage.com"
REFERENCIA = re.compile(
    r'(?:href|src|srcset|content)="(?:https://[^/"]+)?'
    r'(/(?:estatico/[a-z0-9._-]+|favicon\.ico|manifest\.webmanifest)\?v=[0-9a-f]+)"'
)
SRI = re.compile(r'<(script|link)[^>]*?(?:src|href)="(/estatico/[^"]+)" integrity="(sha384-[^"]+)"')


class ArmazemR2(Armazem):
    endpoint = R2  # a CSP libera só este endereço além da própria API


@pytest.fixture
def web(banco):
    return cliente_teste(config_teste(banco, ArmazemR2(), worker_tokens=[]), admin=False)


def test_pages_carry_security_headers_and_revalidate_with_etag(web):
    for caminho in PAGINAS:
        r = web.get(caminho)
        assert r.status_code == 200 and r.headers["content-type"] == "text/html; charset=utf-8"
        csp = r.headers["content-security-policy"]
        for diretiva in (
            "default-src 'self'",
            "script-src 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "require-trusted-types-for 'script'",
            "trusted-types 'none'",
            f"img-src 'self' blob: {R2};",
            f"media-src {R2};",
            f"connect-src 'self' {R2};",
        ):
            assert diretiva in csp
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["cross-origin-opener-policy"] == "same-origin"
        assert r.headers["cross-origin-resource-policy"] == "same-origin"
        assert "camera=()" in r.headers["permissions-policy"]
        assert r.headers["cache-control"] == ("no-store" if caminho == f"{PAINEL}/" else "no-cache")
        etag = r.headers["etag"]
        assert web.get(caminho, headers={"If-None-Match": etag}).status_code == 304
        assert web.get(caminho, headers={"If-None-Match": '"outro"'}).status_code == 200
        assert "server" not in r.headers  # sem anunciar o servidor
    assert web.get(f"{PAINEL}/").headers["x-robots-tag"] == "noindex, nofollow"
    assert "x-robots-tag" not in web.get("/").headers
    # sem o mapa da API, sem o painel antigo e sem lista de caminhos
    for caminho in ("/openapi.json", "/docs", "/redoc", "/aprovar", "/robots.txt", "/estatico/aprovar.js"):
        assert web.get(caminho).status_code == 404, caminho
    # o painel sem a barra final vai para o endereço com a barra (o script do painel é relativo a ela)
    r = web.get(PAINEL, follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == f"{PAINEL}/"
    # a página isolada do "não sou um robô": só ela carrega o script da Cloudflare, e só num quadro do site
    v = web.get("/verificacao")
    assert v.status_code == 200 and v.headers["x-frame-options"] == "SAMEORIGIN"
    csp = v.headers["content-security-policy"]
    assert "script-src 'self' https://challenges.cloudflare.com" in csp and "frame-ancestors 'self'" in csp
    assert "trusted-types" not in csp and v.headers["x-robots-tag"] == "noindex, nofollow"
    assert "challenges.cloudflare.com" not in web.get("/").headers["content-security-policy"]
    api = web.get("/api/receitas")  # sem token: 401, mas com os mesmos cuidados
    assert api.status_code == 401
    assert api.headers["cache-control"] == "no-store" and api.headers["x-robots-tag"] == "noindex, nofollow"


def test_static_files_are_versioned_cached_for_a_year_and_match_sri(web):
    vistos = set()
    for arquivo in [*PAGINAS.values(), "404.html"]:
        html = (PUBLICO / arquivo).read_text()
        refs = REFERENCIA.findall(html)
        assert refs, arquivo
        for url in refs:
            caminho, v = url.split("?v=")
            r = web.get(url)
            assert r.status_code == 200, url
            assert hashlib.sha256(r.content).hexdigest()[:12] == v, url  # a versão é o hash do conteúdo
            assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
            vistos.add(caminho)
        for tag, url, sri in re.findall(
            r'<(script|link)[^>]*?(?:src|href)="(/estatico/[^"]+)" integrity="(sha384-[^"]+)"', html
        ):
            corpo = web.get(url).content
            assert sri == "sha384-" + base64.b64encode(hashlib.sha384(corpo).digest()).decode(), (tag, url)
    # o script do painel: só no endereço secreto, com versão e SRI, nunca em /estatico/
    html = (PUBLICO / "painel.html").read_text()
    url, sri = re.search(r'<script src="(painel\.js\?v=[0-9a-f]+)" integrity="(sha384-[^"]+)"', html).groups()
    r = web.get(f"{PAINEL}/{url}")
    assert r.status_code == 200 and sri == "sha384-" + base64.b64encode(hashlib.sha384(r.content).digest()).decode()
    assert r.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert web.get("/estatico/painel.js").status_code == 404 and web.get("/privado/painel.js").status_code == 404
    css = web.get("/estatico/estilo.css").text  # a fonte dos títulos vem pelo CSS, também com versão
    for url in re.findall(r"url\((/estatico/[a-z0-9._-]+\?v=[0-9a-f]+)\)", css):
        r = web.get(url)
        assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert hashlib.sha256(r.content).hexdigest()[:12] == url.split("?v=")[1]
        vistos.add(url.split("?")[0])
    assert {"/estatico/estilo.css", "/estatico/cliente.js", "/estatico/fonte-titulos.woff2"} <= vistos
    # sem a versão (ou com uma antiga): entrega, mas sem guardar por muito tempo
    assert web.get("/estatico/estilo.css").headers["cache-control"] == "no-cache"
    assert web.get("/estatico/estilo.css?v=000000000000").headers["cache-control"] == "no-cache"
    assert web.get("/estatico/cliente.js").headers["content-type"] == "text/javascript; charset=utf-8"
    assert web.get("/estatico/fonte-titulos.woff2").headers["content-type"] == "font/woff2"
    # imagens da marca podem ser mostradas por outros sites (prévia de link); o resto não
    assert web.get("/estatico/og.jpg").headers["cross-origin-resource-policy"] == "cross-origin"
    assert web.get("/estatico/cliente.js").headers["cross-origin-resource-policy"] == "same-origin"
    assert web.get("/estatico/../src/ts/comum.ts").status_code == 404
    assert web.get("/estatico/nao-existe.js").status_code == 404


def test_unknown_address_shows_html_404_only_to_browsers(web):
    r = web.get("/pagina-que-nao-existe", headers={"Accept": "text/html,application/xhtml+xml"})
    assert r.status_code == 404 and "Página não encontrada" in r.text
    assert r.headers["content-type"].startswith("text/html")
    assert web.get("/pagina-que-nao-existe").json() == {"detail": "Not Found"}
    r = web.get("/api/lotes/lote_x", headers={"Accept": "text/html", **h("x" * 40)})
    assert r.status_code == 401 and r.json() == {"detail": "token inválido"}  # a API continua em JSON
    assert web.get("/api/nada", headers={"Accept": "text/html"}).json() == {"detail": "Not Found"}


def test_cross_site_requests_that_change_data_are_refused(web, banco):
    corpo = {"versao": "1", "estatisticas": False, "marketing": False}
    recusados = [
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "https://outro-site.example"},
        {"Origin": "null"},
    ]
    for cab in recusados:
        r = web.post("/api/cookies", json=corpo, headers=cab)
        assert r.status_code == 403 and r.json() == {"detail": "pedido de outro site recusado"}, cab
        assert r.headers["x-frame-options"] == "DENY"
    aceitos = [{"Sec-Fetch-Site": "same-origin"}, {"Origin": "http://testserver"}, {}]  # {} = pods, curl
    for cab in aceitos:
        assert web.post("/api/cookies", json=corpo, headers=cab).status_code == 200, cab
    with banco.tx() as c:
        assert len(c.all("SELECT id FROM cookies")) == len(aceitos)
    # leitura de outro site não muda nada (e a CSP/CORS do navegador já impede ler a resposta)
    assert web.get("/api/receitas", headers={"Sec-Fetch-Site": "cross-site", **h("x" * 40)}).status_code == 401


def test_indexing_files_manifest_and_icons(web):
    # sem robots.txt (seria uma lista pública de caminhos); o que não é público responde com noindex
    assert web.get("/robots.txt").status_code == 404
    mapa = web.get("/sitemap.xml")
    assert mapa.headers["content-type"].startswith("application/xml")
    for pagina in ("suporte", "termos", "privacidade", "cookies"):
        assert f"<loc>https://app.render2go.com/{pagina}</loc>" in mapa.text
    assert "painel" not in mapa.text and "aprovar" not in mapa.text and "verificacao" not in mapa.text
    manifesto = web.get("/manifest.webmanifest")
    assert manifesto.headers["content-type"] == "application/manifest+json"
    dados = json.loads(manifesto.text)
    assert dados["lang"] == "pt-BR" and dados["start_url"] == "/"
    for icone in dados["icons"]:
        assert web.get(icone["src"]).status_code == 200
    assert any(i.get("purpose") == "maskable" for i in dados["icons"])
    fav = web.get("/favicon.ico")
    assert fav.status_code == 200 and fav.headers["content-type"] == "image/x-icon"


def test_pages_have_semantic_structure_and_meta_tags():
    for arquivo in [*PAGINAS.values(), "404.html"]:
        html = (PUBLICO / arquivo).read_text()
        assert html.startswith('<!doctype html>\n<html lang="pt-BR">'), arquivo
        assert '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">' in html
        assert re.search(r"<title>[^<]*Render2Go[^<]*</title>", html), arquivo
        assert '<a class="pular" href="#conteudo">Pular para o conteúdo</a>' in html
        assert '<main id="conteudo"' in html and "<header" in html and "<h1" in html
        assert "style=" not in html  # a CSP não aceita estilo no meio do HTML
        assert not re.search(r"<script(?![^>]*\bsrc=)(?![^>]*application/ld\+json)[^>]*>", html)  # nem script embutido
        assert "  " not in html.split("<script type")[0]  # minificado (sem recuo)
    for publica in ("index.html", "suporte.html", "termos.html", "privacidade.html", "cookies.html"):
        html = (PUBLICO / publica).read_text()
        for marca in (
            '<meta name="description"',
            '<link rel="canonical"',
            '<meta property="og:image"',
            'name="twitter:card"',
        ):
            assert marca in html, (publica, marca)
        # rodapé: termos, privacidade, cookies e a identificação de quem vende (Decreto 7.962/2013)
        for link in ('href="/termos"', 'href="/privacidade"', 'href="/cookies"', "[[EMPRESA_DOCUMENTO]]"):
            assert link in html, (publica, link)
    for privada in ("painel.html", "404.html", "verificacao.html"):
        assert re.search(r'<meta name="robots" content="noindex', (PUBLICO / privada).read_text())
    # o mesmo topo e o mesmo rodapé em todas as páginas do cliente (também na 404), e o topo já sabe se a pessoa
    # entrou antes de desenhar (o script do <head> marca a sessão: "Pedidos" e "Sair" sem piscar nem empurrar)
    for arquivo in ("index.html", "suporte.html", "termos.html", "privacidade.html", "cookies.html", "404.html"):
        html = (PUBLICO / arquivo).read_text()
        for marca in ('<nav class="navegacao" aria-label="Principal">', 'id="preferencias-cookies"', 'href="/cookies"'):
            assert marca in html, (arquivo, marca)
        sessao = r'<script src="/estatico/inicio\.js\?v=[0-9a-f]+" integrity="[^"]+" data-chave="render2go-conectado"'
        assert re.search(sessao, html), arquivo
        assert html.index("inicio.js") < html.index("estilo.css"), arquivo  # antes do CSS: marca antes de desenhar


def test_old_cached_page_reloads_once_into_the_new_one(web):
    r = web.get("/app.js")
    assert r.headers["cache-control"] == "no-cache" and "location.reload()" in r.text
    assert web.get("/estilo.css").headers["content-type"] == "text/css; charset=utf-8"


# -- contraste das cores (WCAG 2.2, AA) -------------------------------------------------------------


def _cores(css: str) -> tuple[dict[str, str], dict[str, str]]:
    claro = dict(
        re.findall(r"--(cor-[a-z0-9-]+):\s*(#[0-9a-f]{6})", css.split("@media (prefers-color-scheme: dark)")[0])
    )
    escuro = dict(claro)
    bloco = css.split("@media (prefers-color-scheme: dark)")[1].split("@media")[0]
    escuro.update(re.findall(r"--(cor-[a-z0-9-]+):\s*(#[0-9a-f]{6})", bloco))
    return claro, escuro


def _contraste(a: str, b: str) -> float:
    def lum(cor: str) -> float:
        canais = [int(cor[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b_ = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in canais)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_

    claro, escuro = sorted((lum(a), lum(b)), reverse=True)
    return (claro + 0.05) / (escuro + 0.05)


# (texto, fundo, mínimo): 4,5 para texto; 3 para bordas de campos e o anel de foco (componentes)
PARES = [
    *[
        (t, f, 4.5)
        for t in ("cor-texto", "cor-texto-2", "cor-texto-3")
        for f in ("cor-fundo", "cor-superficie", "cor-superficie-2")
    ],
    ("cor-texto-3", "cor-marca-suave", 4.5),
    ("cor-marca", "cor-fundo", 4.5),
    ("cor-marca", "cor-superficie", 4.5),
    ("cor-sobre-marca", "cor-marca", 4.5),
    ("cor-sobre-marca", "cor-marca-forte", 4.5),
    ("cor-marca-texto", "cor-marca-suave", 4.5),
    ("cor-acento-texto", "cor-acento-suave", 4.5),
    ("cor-acento-texto", "cor-superficie", 4.5),
    ("cor-ok", "cor-ok-fundo", 4.5),
    ("cor-erro", "cor-erro-fundo", 4.5),
    ("cor-alerta", "cor-alerta-fundo", 4.5),
    ("cor-ok", "cor-superficie", 4.5),
    ("cor-erro", "cor-superficie", 4.5),
    ("cor-sobre-estado", "cor-ok", 4.5),
    ("cor-sobre-estado", "cor-erro", 4.5),
    ("cor-borda-campo", "cor-superficie", 3),
    ("cor-borda-campo", "cor-fundo", 3),
    ("cor-foco", "cor-superficie", 3),
    ("cor-foco", "cor-fundo", 3),
]


@pytest.mark.parametrize("tema", ["claro", "escuro"])
def test_identity_colors_meet_wcag_aa_contrast(tema):
    cores = dict(zip(("claro", "escuro"), _cores(TOKENS.read_text()), strict=True))[tema]
    falhas = [
        (t, f, round(_contraste(cores[t], cores[f]), 2))
        for t, f, minimo in PARES
        if _contraste(cores[t], cores[f]) < minimo
    ]
    assert not falhas, falhas


RGBA = r"--(cor-brilho-[a-z]+):\s*rgb\((\d+) (\d+) (\d+) / ([\d.]+)\)"


def _brilhos(css: str) -> tuple[dict, dict]:
    """Os brilhos decorativos (cor com transparência) do tema claro e do escuro."""

    def ler(trecho: str) -> dict:
        return {n: (int(r), int(g), int(b), float(a)) for n, r, g, b, a in re.findall(RGBA, trecho)}

    claro_css, resto = css.split("@media (prefers-color-scheme: dark)", 1)
    claro = ler(claro_css)
    return claro, {**claro, **ler(resto.split("@media")[0])}


@pytest.mark.parametrize("tema", ["claro", "escuro"])
def test_decorative_glow_keeps_text_contrast(tema):
    """O brilho do fundo da entrada fica atrás do título e do texto: mesmo no pior caso (os dois brilhos no
    ponto mais forte, um sobre o outro), o texto continua com contraste AA. Campos ficam em cartões opacos."""
    css = TOKENS.read_text()
    cores = dict(zip(("claro", "escuro"), _cores(css), strict=True))[tema]
    brilhos = dict(zip(("claro", "escuro"), _brilhos(css), strict=True))[tema]
    assert set(brilhos) == {"cor-brilho-marca", "cor-brilho-acento"}

    def sobre(fundo: str, brilho: tuple) -> str:
        r, g, b, a = brilho
        canais = [round(int(fundo[i : i + 2], 16) * (1 - a) + c * a) for i, c in zip((1, 3, 5), (r, g, b), strict=True)]
        return "#" + "".join(f"{c:02x}" for c in canais)

    marca, acento = brilhos["cor-brilho-marca"], brilhos["cor-brilho-acento"]
    fundos = [sobre(cores["cor-fundo"], marca), sobre(cores["cor-fundo"], acento)]
    fundos.append(sobre(fundos[0], acento))  # os dois juntos
    falhas = [
        (t, f, round(_contraste(cores[t], f), 2))
        for t in ("cor-texto", "cor-texto-2", "cor-texto-3", "cor-marca-texto")
        for f in fundos
        if _contraste(cores[t], f) < 4.5
    ]
    assert not falhas, falhas


def test_head_answers_like_get_without_body_except_on_the_api(web):
    """Monitores de disponibilidade usam HEAD: as páginas respondem; a API só aceita os métodos dela."""
    for caminho in ("/", "/termos", "/suporte", "/saude", f"{PAINEL}/"):
        get, head = web.get(caminho), web.head(caminho)
        assert head.status_code == 200 and head.content == b"", caminho
        assert head.headers["content-length"] == get.headers["content-length"], caminho
        assert head.headers["content-security-policy"] == get.headers["content-security-policy"], caminho
    assert web.head("/nao-existe").status_code == 404
    assert web.head("/api/publico").status_code == 405


def test_startup_warnings_name_what_is_missing(banco, monkeypatch):
    from render2go_servico.api import EMPRESA, avisos_de_configuracao

    for v in EMPRESA.values():
        monkeypatch.setenv(v, "preenchido")
    assert avisos_de_configuracao(config_teste(banco)) == [
        "SMTP não configurado: os códigos de entrada não saem e ninguém entra pelo e-mail",
        "Turnstile DESLIGADO de propósito (RENDER2GO_TURNSTILE=desligado): só para desenvolvimento",
        "recarga por Pix desligada (RENDER2GO_MP_*): o saldo só entra pelo painel",
    ]
    monkeypatch.delenv("RENDER2GO_EMPRESA_NOME")
    avisos = avisos_de_configuracao(config_teste(banco, admin_caminho=None, turnstile_desligado=False))
    assert avisos[0].startswith("painel desligado") and "Turnstile não configurado" in avisos[2]
    assert avisos[-1].endswith(": RENDER2GO_EMPRESA_NOME")
