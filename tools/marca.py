#!/usr/bin/env python3
"""Gera os arquivos da marca Render2Go para as páginas (docs/10-identidade-visual-e-front-end.md).

Saída em servico/web/publico/ (versionada; o build das páginas só calcula as versões):
    estatico/fonte-titulos.woff2      Bricolage Grotesque 700 (opsz 32), só o pt-BR (~13 KB)
    estatico/fonte-titulos-OFL.txt    licença da fonte (SIL OFL 1.1), que acompanha o arquivo
    estatico/logo.svg, logo-escuro.svg  símbolo + palavra em contornos (não depende da fonte)
    estatico/icone.svg                símbolo (favicon)
    estatico/icone-180.png, icone-192.png, icone-512.png, icone-mascaravel-512.png
    estatico/og.jpg                   imagem de compartilhamento (1200×630)
    favicon.ico                       16, 32 e 48 px

A fonte vem do repositório google/fonts num commit fixo, conferida por SHA-256. As imagens PNG
saem do Chromium (playwright), a partir dos SVGs e da própria fonte. Uso (precisa de rede):

    uv run --no-project --with fonttools==4.60.1 --with brotli==1.1.0 --with uharfbuzz==0.51.1 \\
        --with playwright==1.56.0 --with pillow==12.3.0 python tools/marca.py
    ... python tools/marca.py --check    # confere fonte e SVGs contra os arquivos versionados
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import tempfile
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PUBLICO = RAIZ / "servico" / "web" / "publico"
CACHE = Path.home() / ".cache" / "render2go" / "marca"

COMMIT = "5e8a3ba899557829a76cfdac30fa512bda91d7ca"  # google/fonts, 08/10/2026
BASE = f"https://raw.githubusercontent.com/google/fonts/{COMMIT}/ofl/bricolagegrotesque/"
FONTE = (
    "BricolageGrotesque%5Bopsz%2Cwdth%2Cwght%5D.ttf",
    408496,
    "413e7357809ddd12fd80a96a8a396de0e401638d4acd3cb3e37532f0472ac682",
)
LICENCA = ("OFL.txt", 4403, "4b5a7d8f37f5602621c8a8d7358a6a2e71317e6c231c661e15aef0275d3e07ba")

# o mesmo unicode-range do @font-face em src/css/tokens.css: o que os títulos em pt-BR usam
# (letras com acento, ç, aspas curvas, travessões, reticências); outro caractere usa a fonte do
# sistema
UNICODES = (
    "U+0020-007E,U+00A0,U+00AA,U+00B0,U+00B7,U+00BA,U+00C0-00C3,U+00C7-00CA,U+00CD,U+00D3-00D5,"
    "U+00DA,U+00DC,U+00E0-00E3,U+00E7-00EA,U+00ED,U+00F3-00F5,U+00FA,U+00FC,U+2013-2014,U+2018-2019,"
    "U+201C-201D,U+2026,U+20AC"
)
TITULOS = {"opsz": 32, "wdth": 100, "wght": 700}
PALAVRA = {"opsz": 32, "wdth": 100, "wght": 800}

# pigmentos (os mesmos tokens do CSS): anil (ação), urucum (acento), tinta (texto)
ANIL, URUCUM, URUCUM_CLARO = "#4338CA", "#E5532E", "#FF7A54"
TINTA, TINTA_CLARA, URUCUM_ESCURO_FUNDO = "#1A1830", "#EEEDF6", "#FF7D57"
PAPEL = "#F7F7FB"

# símbolo em 64 × 64: quadro arredondado anil, play branco e dois traços de movimento ("to go")
TRACOS = (
    '<rect x="9" y="24" width="12" height="5" rx="2.5"/>'
    '<rect x="13" y="35" width="8" height="5" rx="2.5"/>'
)
PLAY = (
    "M27 20.6v22.8c0 1.7 1.8 2.7 3.3 1.9l18.4-11.4c1.4-.9 1.4-2.9 0-3.8"
    "L30.3 18.7c-1.5-.9-3.3.2-3.3 1.9z"
)


def baixar(nome: str, tamanho: int, sha: str) -> bytes:
    destino = CACHE / f"{sha[:16]}-{nome}"
    if destino.exists():
        dados = destino.read_bytes()
    else:
        with urllib.request.urlopen(BASE + nome, timeout=60) as r:  # noqa: S310 (URL fixa)
            dados = r.read()
    if len(dados) != tamanho or hashlib.sha256(dados).hexdigest() != sha:
        raise SystemExit(f"{nome}: bytes ou SHA-256 diferentes do esperado")
    CACHE.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(dados)
    return dados


def instancia(ttf: bytes, eixos: dict):
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    # recalcTimestamp=False: sem a data de agora no cabeçalho, a saída é a mesma a cada execução
    f = instancer.instantiateVariableFont(
        TTFont(io.BytesIO(ttf), lazy=False, recalcTimestamp=False), eixos
    )
    b = io.BytesIO()
    f.save(b)  # salvar e reabrir evita um erro do recorte com tabelas carregadas sob demanda
    return TTFont(io.BytesIO(b.getvalue()), lazy=False, recalcTimestamp=False), b.getvalue()


def fonte_titulos(ttf: bytes) -> bytes:
    from fontTools import subset

    fonte, _ = instancia(ttf, TITULOS)
    opcoes = subset.Options()
    opcoes.flavor = "woff2"
    opcoes.layout_features = ["kern"]  # títulos: só o kerning (acentos já vêm compostos no pt-BR)
    opcoes.name_IDs = ["*"]  # mantém o copyright e a licença dentro da fonte (OFL)
    opcoes.notdef_outline = True
    opcoes.hinting = False  # tamanhos de título não precisam de hinting; o arquivo fica menor
    recorte = subset.Subsetter(opcoes)
    recorte.populate(unicodes=subset.parse_unicodes(UNICODES))
    recorte.subset(fonte)
    fonte.flavor = "woff2"
    b = io.BytesIO()
    fonte.save(b)
    return b.getvalue()


def contornos(ttf: bytes) -> tuple[str, str, tuple[float, float, float, float]]:
    """'Render' e '2Go' em caminhos SVG (unidades da fonte, y para baixo), com kerning."""
    import uharfbuzz as hb
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen

    fonte, dados = instancia(ttf, PALAVRA)
    glifos = fonte.getGlyphSet()
    ordem = fonte.getGlyphOrder()
    buf = hb.Buffer()
    buf.add_str("Render2Go")
    buf.guess_segment_properties()
    hb.shape(hb.Font(hb.Face(dados)), buf, {"kern": True, "liga": True})
    canetas = (SVGPathPen(glifos), SVGPathPen(glifos))
    limites = BoundsPen(glifos)
    x = 0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions, strict=True):
        t = (1, 0, 0, -1, x + pos.x_offset, -pos.y_offset)
        alvo = canetas[0] if info.cluster < len("Render") else canetas[1]
        glifos[ordem[info.codepoint]].draw(TransformPen(alvo, t))
        glifos[ordem[info.codepoint]].draw(TransformPen(limites, t))
        x += pos.x_advance
    return canetas[0].getCommands(), canetas[1].getCommands(), limites.bounds


def simbolo(cheio: bool = False) -> str:
    """Símbolo em 64 × 64. cheio: sem cantos e com margem (ícone de tela inicial e máscara)."""
    if cheio:
        return (
            f'<rect width="64" height="64" fill="{ANIL}"/>'
            f'<g transform="translate(6.4 6.4) scale(.8)"><g fill="{URUCUM_CLARO}">{TRACOS}</g>'
            f'<path fill="#fff" d="{PLAY}"/></g>'
        )
    return (
        f'<rect width="64" height="64" rx="16" fill="{ANIL}"/>'
        f'<g fill="{URUCUM_CLARO}">{TRACOS}</g><path fill="#fff" d="{PLAY}"/>'
    )


def svgs(ttf: bytes) -> dict[str, str]:
    render, dois_go, (xmin, ymin, xmax, _) = contornos(ttf)
    lado = 1000  # símbolo de 1000 unidades; a palavra fica centrada na altura das maiúsculas
    vao = 260
    base = lado / 2 - ymin / 2  # ymin < 0: topo das maiúsculas
    largura = round(lado + vao + (xmax - xmin))
    escala = lado / 64

    def logo(cor_render: str, cor_go: str) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {largura} {lado}" '
            f'width="{largura / lado * 32:.0f}" height="32" role="img" aria-label="Render2Go">'
            f'<g transform="scale({escala})">{simbolo()}</g>'
            f'<g transform="translate({lado + vao - xmin:.0f} {base:.1f})">'
            f'<path fill="{cor_render}" d="{render}"/>'
            f'<path fill="{cor_go}" d="{dois_go}"/></g></svg>\n'
        )

    return {
        "estatico/logo.svg": logo(TINTA, URUCUM),
        "estatico/logo-escuro.svg": logo(TINTA_CLARA, URUCUM_ESCURO_FUNDO),
        "estatico/icone.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">' + simbolo() + "</svg>\n"
        ),
    }


OG = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><style>
@font-face{{font-family:T;font-weight:700;
src:url("data:font/woff2;base64,{fonte}") format("woff2")}}
html,body{{margin:0;width:1200px;height:630px;overflow:hidden}}
body{{background:{papel};font-family:T,sans-serif;color:{tinta};position:relative}}
.mancha{{position:absolute;border-radius:50%;filter:blur(70px);opacity:.55}}
.a{{width:620px;height:620px;right:-140px;top:-260px;background:{anil}}}
.b{{width:520px;height:520px;right:120px;bottom:-330px;background:{urucum}}}
.c{{position:absolute;inset:0;padding:80px 88px;display:flex;flex-direction:column;
justify-content:space-between;align-items:flex-start}}
h1{{font-size:64px;line-height:1.05;letter-spacing:-.025em;margin:0;max-width:980px}}
p{{margin:0;font:600 30px/1.35 sans-serif;color:#45425C;max-width:900px}}
.url{{font:700 26px sans-serif;color:{anil}}}
</style><div class="mancha a"></div><div class="mancha b"></div><div class="c">
<img src="data:image/svg+xml;base64,{logo}" height="64" alt="">
<div><h1>Suas fotos viram vídeos curtos, prontos para postar</h1>
<p style="margin-top:22px">Envie as fotos, diga em português o que deve acontecer e receba um vídeo
por foto.</p></div>
<div class="url">app.render2go.com</div></div></html>"""


def pngs(saida: Path, woff2: bytes, arquivos: dict[str, str]) -> None:
    import base64

    from PIL import Image
    from playwright.sync_api import sync_playwright

    chromium = next(
        iter(sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))), None
    )
    cheio = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">' + simbolo(True) + "</svg>"
    )
    b64 = lambda s: base64.b64encode(s if isinstance(s, bytes) else s.encode()).decode()  # noqa: E731
    with sync_playwright() as p:
        nav = p.chromium.launch(executable_path=str(chromium) if chromium else None)

        def foto(html: str, w: int, h: int, destino: Path, transparente: bool = False) -> None:
            pg = nav.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
            pg.set_content(html)
            pg.wait_for_timeout(150)
            pg.screenshot(path=str(destino), omit_background=transparente)
            pg.close()

        def svg_png(svg: str, lado: int, destino: Path, transparente: bool = True) -> None:
            html = (
                "<style>html,body{margin:0}img{display:block}</style>"
                f'<img src="data:image/svg+xml;base64,{b64(svg)}" width="{lado}" height="{lado}">'
            )
            foto(html, lado, lado, destino, transparente)

        est = saida / "estatico"
        svg_png(arquivos["estatico/icone.svg"], 192, est / "icone-192.png")
        svg_png(arquivos["estatico/icone.svg"], 512, est / "icone-512.png")
        svg_png(cheio, 180, est / "icone-180.png", transparente=False)
        svg_png(cheio, 512, est / "icone-mascaravel-512.png", transparente=False)
        with tempfile.TemporaryDirectory() as tmp:
            grande = Path(tmp) / "ico.png"
            svg_png(arquivos["estatico/icone.svg"], 256, grande)
            Image.open(grande).save(saida / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
        html = OG.format(
            fonte=b64(woff2),
            logo=b64(arquivos["estatico/logo.svg"]),
            papel=PAPEL,
            tinta=TINTA,
            anil=ANIL,
            urucum=URUCUM,
        )
        with tempfile.TemporaryDirectory() as tmp:
            og = Path(tmp) / "og.png"
            foto(html, 1200, 630, og)
            Image.open(og).convert("RGB").save(
                est / "og.jpg", quality=86, optimize=True, progressive=True
            )
        nav.close()
    for png in [*est.glob("*.png")]:  # recomprime sem perdas
        Image.open(png).save(png, optimize=True)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--check", action="store_true", help="confere fonte e SVGs versionados (sem gravar)"
    )
    args = ap.parse_args()
    ttf = baixar(*FONTE)
    licenca = baixar(*LICENCA)
    textos = {**svgs(ttf)}
    binarios = {
        "estatico/fonte-titulos.woff2": fonte_titulos(ttf),
        "estatico/fonte-titulos-OFL.txt": licenca,
    }
    if args.check:
        diferentes = [
            nome
            for nome, conteudo in [*textos.items(), *binarios.items()]
            if not (PUBLICO / nome).exists()
            or (PUBLICO / nome).read_bytes()
            != (conteudo.encode() if isinstance(conteudo, str) else conteudo)
        ]
        for nome in diferentes:
            print(f"desatualizado: servico/web/publico/{nome}", file=sys.stderr)
        return 1 if diferentes else 0
    (PUBLICO / "estatico").mkdir(parents=True, exist_ok=True)
    for nome, conteudo in textos.items():
        (PUBLICO / nome).write_text(conteudo)
    for nome, conteudo in binarios.items():
        (PUBLICO / nome).write_bytes(conteudo)
    pngs(PUBLICO, binarios["estatico/fonte-titulos.woff2"], textos)
    imagens = [f"estatico/{p.name}" for p in (PUBLICO / "estatico").glob("*.[pj][np]g")]
    for nome in sorted([*textos, *binarios, "favicon.ico", *imagens]):
        print(f"{(PUBLICO / nome).stat().st_size:>8}  servico/web/publico/{nome}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
