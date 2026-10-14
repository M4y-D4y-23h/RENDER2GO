"""Montagem de vídeos (ADR 0015, 13/10/2026): junta de 2 a 10 vídeos prontos da conta, na ordem escolhida, com corte
seco ou esmaecimento curto, textos por cima (título, preço e chamada) e logo e trilha opcionais, num MP4 de 720p
(H.264 + AAC) em 9:16, 1:1 ou 16:9. Cada vídeo aparece inteiro, sobre uma cópia desfocada e escurecida dele mesmo
(o produto nunca é cortado quando o formato do vídeo e o da montagem são diferentes).

Roda no contêiner `montador` (`render2go-servico montar`), fora da API, só em CPU, com memória e tempo limitados
(o servidor tem 4 GB de RAM). Onde está a segurança:
- **o texto do cliente nunca chega ao ffmpeg**: título, preço e chamada (conferidos na API pelo C1, com tamanho e
  caracteres limitados) e o logo viram uma imagem PNG desenhada aqui, pelo Pillow, com a fonte fixada por SHA-256
  (`sobreposicao`). Nem a linha de filtros nem um arquivo de texto do ffmpeg (`drawtext`) recebem o que o cliente
  escreveu;
- **o logo do cliente nunca chega ao montador como foi enviado**: o analisador (o único contêiner que decodifica
  arquivos dos clientes) o abre por `pessoas.abrir_foto`, passa pelo filtro das fotos e o regrava em PNG
  (`logo_png`); o montador só abre esse PNG, de novo por `pessoas.abrir_foto`;
- **o ffmpeg só recebe arquivos do próprio serviço** (os vídeos que os pods geraram, a imagem desenhada aqui e a
  trilha da biblioteca), cada um com o formato explícito (nada é adivinhado pelo conteúdo), só o protocolo `file`,
  sem stdin, com o ambiente vazio (nenhum segredo), tempo de CPU, tamanho de arquivo e tempo de relógio limitados e
  a saída conferida pelo ffprobe (H.264, AAC e a duração esperada).

Os arquivos fixos (a fonte e as faixas da biblioteca) são baixados no build da imagem e conferidos por bytes e
SHA-256 (`python -m render2go_servico.montagem baixar <pasta>`), e `conferir` faz uma montagem pequena com vídeos
sintéticos (a build falha se o ffmpeg, a fonte ou o Pillow não funcionarem).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from render2go_servico import pessoas

VERSAO = "montagem-v1"
# formato → (largura, altura): 720p (o lado menor com 720 px)
FORMATOS = {"9:16": (720, 1280), "1:1": (720, 720), "16:9": (1280, 720)}
NOMES_FORMATO = {"9:16": "vertical (Stories e Reels)", "1:1": "quadrado (feed)", "16:9": "horizontal (YouTube)"}
TRANSICOES = ("corte", "fade")
FADE_S = 0.5  # esmaecimento entre dois vídeos: 0,25 s até o preto no fim de um e 0,25 s do preto no outro
FPS = 16  # o dos vídeos do Wan 2.2 (16 quadros por segundo)
MIN_VIDEOS, MAX_VIDEOS = 2, 10
# campo → máximo de caracteres (uma linha cada; o título e a chamada podem quebrar em duas na imagem)
TEXTOS = {"titulo": 40, "preco": 20, "chamada": 40}
NOMES_TEXTO = {"titulo": "o título", "preco": "o preço", "chamada": "a chamada"}  # nas mensagens ao cliente
# o que a fonte desenha: letras com os acentos do português (e do espanhol), números, espaço e a pontuação de um
# anúncio. O resto (emoji, outros alfabetos, caracteres de controle) é recusado com a explicação
CARACTERES = re.compile(r"[A-Za-z0-9À-ÖØ-öø-ÿ .,:;!?¡¿%$€£+\-–—/()&@#'\"“”‘’*ºª°]*")
LOGO_BYTES = 5 * 1024 * 1024  # o logo enviado (JPEG, PNG ou WebP)
LOGO_TIPOS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
LOGO_LADO = 512  # o PNG regravado pelo analisador tem no máximo isto no lado maior
VIDEO_BYTES = 150 * 1024 * 1024  # um vídeo de entrada (5 s em 720p: ~5 MB)
SAIDA_BYTES = 400 * 1024 * 1024  # a montagem (10 vídeos de 7 s em 720p: ~60 MB)
TEMPO_S = 600  # relógio do ffmpeg (10 vídeos de 5 s em 720p, 1 vCPU: ~1 min)
CPU_S = 900  # tempo de CPU do ffmpeg (com 2 threads, o relógio vence antes)
THREADS = 2
TENTATIVAS = 3  # montagens que não terminaram (o montador caiu): depois disso, erro
LEASE_S = 15 * 60  # o montador que pegou a montagem tem até isto para terminar (TEMPO_S + envio)

# arquivos fixos (baixados no build da imagem, conferidos por bytes e SHA-256): a fonte dos títulos das páginas
# (Bricolage Grotesque, SIL OFL 1.1, a mesma de tools/marca.py) e a licença dela
FONTE_COMMIT = "5e8a3ba899557829a76cfdac30fa512bda91d7ca"  # google/fonts, 08/10/2026
_FONTE_BASE = f"https://raw.githubusercontent.com/google/fonts/{FONTE_COMMIT}/ofl/bricolagegrotesque/"
ARQUIVOS = {
    "fonte.ttf": (
        _FONTE_BASE + "BricolageGrotesque%5Bopsz%2Cwdth%2Cwght%5D.ttf",
        408496,
        "413e7357809ddd12fd80a96a8a396de0e401638d4acd3cb3e37532f0472ac682",
    ),
    "fonte-OFL.txt": (
        _FONTE_BASE + "OFL.txt",
        4403,
        "4b5a7d8f37f5602621c8a8d7358a6a2e71317e6c231c661e15aef0275d3e07ba",
    ),
}
EIXOS_FONTE = {"Optical size": 32, "Weight": 700, "Width": 100}  # os dos títulos das páginas (docs/10)

# biblioteca de trilhas (ADR 0015, ponto 6): só faixas com a licença conferida e documentada (CC0, domínio público
# ou compradas), em servico/trilhas/FONTES.md. id → título, autor, licença, arquivo, bytes, SHA-256 e o formato do
# arquivo para o ffmpeg. Vazia: a página não mostra a opção, e toda montagem sai com o áudio em silêncio
TRILHAS: dict[str, dict[str, Any]] = {}

PASTA = "montagem"  # dentro de RENDER2GO_MODELOS_SERVICO (pessoas.DIR)


class MontagemError(Exception):
    """A montagem não saiu: o texto vai para o painel; o cliente vê só o motivo resumido (`motivo`)."""

    def __init__(self, mensagem: str, motivo: str = "a montagem não pôde ser feita"):
        super().__init__(mensagem)
        self.motivo = motivo


class ArquivoIndisponivel(Exception):
    """A fonte ou uma faixa da biblioteca está ausente ou diferente do esperado (fail-closed)."""


def pasta_padrao() -> Path:
    return pessoas.DIR / PASTA


def _sha(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _arquivos() -> dict[str, tuple[str, int, str]]:
    """A fonte, a licença e as faixas da biblioteca: nome → (URL, bytes, SHA-256)."""
    faixas = {t["arquivo"]: (t["url"], t["bytes"], t["sha256"]) for t in TRILHAS.values()}
    return {**ARQUIVOS, **faixas}


def conferido(pasta: Path, nome: str) -> Path:
    """O arquivo fixo `nome`, se ele existe com os bytes e o SHA-256 esperados; senão, ArquivoIndisponivel."""
    _, tamanho, sha = _arquivos()[nome]
    p = pasta / nome
    if not p.is_file() or p.stat().st_size != tamanho or _sha(p) != sha:
        raise ArquivoIndisponivel(f"{nome} ausente ou diferente do esperado em {pasta}")
    return p


def baixar(destino: Path | None = None) -> None:
    """Baixa o que falta (fonte, licença e faixas) e confere bytes e SHA-256 (falha se não bater)."""
    destino = destino or pasta_padrao()
    destino.mkdir(parents=True, exist_ok=True)
    for nome, (url, tamanho, sha) in _arquivos().items():
        alvo = destino / nome
        if alvo.is_file() and alvo.stat().st_size == tamanho and _sha(alvo) == sha:
            continue
        parcial = destino / f"{nome}.parcial"
        req = urllib.request.Request(url, headers={"User-Agent": "render2go-servico (build)"})
        with urllib.request.urlopen(req, timeout=120) as r, parcial.open("wb") as f:  # noqa: S310 (URL fixa)
            while bloco := r.read(1 << 20):
                f.write(bloco)
        if parcial.stat().st_size != tamanho or _sha(parcial) != sha:
            parcial.unlink()
            raise ArquivoIndisponivel(f"{nome}: bytes ou SHA-256 não conferem")
        parcial.replace(alvo)
        alvo.chmod(0o644)


# -- o que o cliente escolhe (conferido na API e de novo aqui) ---------------------------------------------------


def limpar_texto(texto: str | None) -> str:
    """Uma linha: espaços repetidos e quebras viram um espaço só (NFC: o acento composto vira um caractere)."""
    return " ".join(unicodedata.normalize("NFC", texto or "").split())


def problema_texto(campo: str, texto: str) -> str | None:
    """Por que o texto não pode ir para a montagem (tamanho ou caractere que a fonte não desenha); None se pode. O
    C1 (filtro de conteúdo) é conferido na API, com a mesma regra dos pedidos."""
    nome = NOMES_TEXTO.get(campo, campo)
    if campo not in TEXTOS:
        return f"campo de texto desconhecido: {campo}"
    if len(texto) > TEXTOS[campo]:
        return f"{nome} tem no máximo {TEXTOS[campo]} caracteres"
    if not CARACTERES.fullmatch(texto):
        ruins = sorted({ch for ch in texto if not CARACTERES.fullmatch(ch)})
        mostra = " ".join(ch if ch.isprintable() else f"U+{ord(ch):04X}" for ch in ruins[:5])
        return f"{nome} tem caracteres que a montagem não escreve ({mostra}); use letras, números e pontuação"
    return None


@dataclass(frozen=True)
class Entrada:
    """Um vídeo da montagem: o arquivo local (já baixado) e a duração do pedido dele, em segundos inteiros."""

    caminho: Path
    duracao: int


def duracao_total(duracoes: list[int], transicao: str) -> float:
    """Segundos da montagem: a soma dos vídeos (o esmaecimento passa pelo preto dentro de cada vídeo, sem sobrepor
    um ao outro)."""
    return float(sum(duracoes))


# -- a imagem por cima (textos e logo), desenhada aqui --------------------------------------------------------------

BRANCO = (255, 255, 255, 255)
CAIXA = (16, 14, 32, 150)  # tinta, translúcida: o texto branco fica legível sobre qualquer vídeo
DESTAQUE = (229, 83, 46, 235)  # urucum (o acento da marca, docs/10): a caixa do preço


def _fonte(caminho: Path, tamanho: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(caminho), tamanho)
    with contextlib.suppress(OSError, AttributeError):  # FreeType sem variações: fica o padrão da fonte
        eixos = f.get_variation_axes()
        valores = []
        for e in eixos:
            nome = e["name"].decode() if isinstance(e["name"], bytes) else str(e["name"])
            valores.append(EIXOS_FONTE.get(nome, e["default"]))
        f.set_variation_by_axes(valores)
    return f


def _linhas(texto: str, fonte_de: Callable[[int], ImageFont.FreeTypeFont], base: int, largura: int,
            duas: bool) -> tuple[list[str], ImageFont.FreeTypeFont]:  # fmt: skip
    """As linhas e a fonte em que o texto cabe em `largura`: uma linha no tamanho base; senão (se `duas`), a quebra
    em duas linhas mais equilibrada; senão, a fonte menor até caber (no mínimo 40% do tamanho base)."""
    opcoes = [[texto]]
    palavras = texto.split(" ")
    if duas and len(palavras) > 1:
        quebras = [[" ".join(palavras[:i]), " ".join(palavras[i:])] for i in range(1, len(palavras))]
        opcoes.append(min(quebras, key=lambda ls: max(len(x) for x in ls)))
    minimo = max(12, int(base * 0.4))
    for tamanho in range(base, minimo - 1, -2):
        f = fonte_de(tamanho)
        for linhas in opcoes:
            if max(f.getlength(x) for x in linhas) <= largura:
                return linhas, f
    return opcoes[-1], fonte_de(minimo)


def _bloco(desenho: ImageDraw.ImageDraw, linhas: list[str], fonte: ImageFont.FreeTypeFont, centro_x: int, topo: int,
           caixa: tuple[int, int, int, int]) -> int:  # fmt: skip
    """Desenha as linhas centradas numa caixa arredondada a partir de `topo`; devolve a base da caixa."""
    subida, descida = fonte.getmetrics()
    altura_linha = subida + descida
    pad_x, pad_y = int(fonte.size * 0.45), int(fonte.size * 0.22)
    largura = int(max(fonte.getlength(x) for x in linhas))
    altura = altura_linha * len(linhas)
    x0, y0 = centro_x - largura // 2 - pad_x, topo
    x1, y1 = centro_x + (largura + 1) // 2 + pad_x, topo + altura + 2 * pad_y
    desenho.rounded_rectangle((x0, y0, x1, y1), radius=int(fonte.size * 0.35), fill=caixa)
    for i, linha in enumerate(linhas):
        desenho.text((centro_x, y0 + pad_y + i * altura_linha), linha, font=fonte, fill=BRANCO, anchor="ma")
    return y1


def sobreposicao(formato: str, textos: dict[str, str], logo: bytes | None, fonte: Path) -> bytes | None:
    """A imagem (PNG RGBA do tamanho da montagem) com o logo e os textos, nas posições fixas: o logo no canto
    superior direito; o título no alto (abaixo do logo, se houver); o preço e a chamada embaixo. None sem textos e
    sem logo. `logo`: o PNG regravado pelo analisador (`logo_png`), aberto de novo por `pessoas.abrir_foto`."""
    textos = {k: v for k, v in textos.items() if v}
    if not textos and not logo:
        return None
    w, h = FORMATOS[formato]
    curto = min(w, h)
    margem = int(curto * 0.05)
    largura_texto = int(w - 2 * margem - curto * 0.06)
    imagem = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    desenho = ImageDraw.Draw(imagem)
    topo = margem
    if logo:
        with pessoas.abrir_foto(logo) as im:
            marca = ImageOps.exif_transpose(im).convert("RGBA")
        marca.thumbnail((int(w * 0.22), int(h * 0.12)), Image.Resampling.LANCZOS)
        imagem.alpha_composite(marca, (w - margem - marca.width, margem))
        topo = margem + marca.height + int(curto * 0.03)

    def fonte_de(tamanho: int) -> ImageFont.FreeTypeFont:
        return _fonte(fonte, tamanho)

    if t := textos.get("titulo"):
        linhas, f = _linhas(t, fonte_de, int(curto * 0.075), largura_texto, duas=True)
        _bloco(desenho, linhas, f, w // 2, topo, CAIXA)
    # embaixo, de baixo para cima: a chamada encostada na margem e o preço logo acima dela
    base = h - int(h * 0.07)
    blocos = []
    if t := textos.get("chamada"):
        blocos.append((_linhas(t, fonte_de, int(curto * 0.06), largura_texto, duas=True), CAIXA))
    if t := textos.get("preco"):
        blocos.append((_linhas(t, fonte_de, int(curto * 0.09), largura_texto, duas=False), DESTAQUE))
    for (linhas, f), caixa in blocos:
        subida, descida = f.getmetrics()
        altura = (subida + descida) * len(linhas) + 2 * int(f.size * 0.22)
        _bloco(desenho, linhas, f, w // 2, base - altura, caixa)
        base -= altura + int(curto * 0.025)
    out = io.BytesIO()
    imagem.save(out, "PNG")
    return out.getvalue()


def logo_png(dados: bytes) -> bytes:
    """O logo do cliente regravado em PNG pelo servidor (no analisador, depois do filtro das fotos): aberto por
    `pessoas.abrir_foto` (tipo pelos primeiros bytes, só JPEG, PNG e WebP, teto de pixels antes de decodificar), com a
    rotação do EXIF aplicada, até LOGO_LADO px e em RGBA (a transparência fica; metadados não). FotoInvalida ou erro
    do Pillow se o arquivo não abre."""
    with pessoas.abrir_foto(dados) as im:
        im.draft("RGB", (LOGO_LADO * 2, LOGO_LADO * 2))  # JPEG: decodifica já reduzido
        marca = ImageOps.exif_transpose(im).convert("RGBA")
    marca.thumbnail((LOGO_LADO, LOGO_LADO), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    marca.save(out, "PNG", optimize=True)
    return out.getvalue()


# -- o ffmpeg ------------------------------------------------------------------------------------------------------


def _par(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def _entrada(caminho: Path, formato_ffmpeg: str) -> list[str]:
    """Uma entrada do ffmpeg: um thread de decodificação, só o protocolo file e o formato explícito (nada é
    adivinhado pelo conteúdo do arquivo)."""
    return ["-threads", "1", "-protocol_whitelist", "file", "-f", formato_ffmpeg, "-i", str(caminho)]


FFMPEG = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
SAIDA_ARQUIVO = ["-map_metadata", "-1", "-f", "mp4", "-protocol_whitelist", "file"]


def comando_clipe(entrada: Entrada, formato: str, transicao: str, primeiro: bool, ultimo: bool,
                  saida: Path) -> list[str]:  # fmt: skip
    """1ª etapa, um ffmpeg por vídeo (a memória não cresce com o número de vídeos): 16 quadros por segundo, cortado
    na duração do pedido (o quadro a mais do Wan sai; o vídeo curto repete o último quadro), inteiro no centro, sobre
    uma cópia dele desfocada e escurecida, no tamanho da montagem. Com o esmaecimento, o vídeo escurece até o preto
    no fim e volta do preto no começo, menos nas pontas da montagem (o 1º quadro vira a capa nas redes)."""
    w, h = FORMATOS[formato]
    bw, bh = _par(w / 8), _par(h / 8)
    d = entrada.duracao
    fades = ""
    if transicao == "fade":
        meio = FADE_S / 2
        if not primeiro:
            fades += f",fade=t=in:st=0:d={meio}"
        if not ultimo:
            fades += f",fade=t=out:st={d - meio:.4f}:d={meio}"
    grafo = ";".join([
        f"[0:v]fps={FPS},format=yuv420p,tpad=stop_mode=clone:stop_duration={d},trim=duration={d},"
        "setpts=PTS-STARTPTS,split=2[a][b]",
        f"[a]scale={bw}:{bh}:force_original_aspect_ratio=increase:flags=bilinear,crop={bw}:{bh},"
        f"boxblur=6:2,scale={w}:{h}:flags=bilinear,eq=brightness=-0.12:saturation=0.85[g]",
        f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,setsar=1[p]",
        # o overlay perde a taxa de quadros no ffmpeg 7 (o da imagem do serviço): o fps a fixa de novo
        f"[g][p]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS},format=yuv420p{fades}[c]",
    ])  # fmt: skip
    return [
        *FFMPEG, *_entrada(entrada.caminho, "mp4"), "-filter_complex", grafo, "-filter_complex_threads", "1",
        "-map", "[c]", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-t", str(d), "-threads", "1", *SAIDA_ARQUIVO, str(saida),
    ]  # fmt: skip


def lista_concat(clipes: list[Path]) -> str:
    """O roteiro do demuxer concat: só nomes de arquivo deste módulo, relativos à pasta da montagem (o demuxer, no
    modo seguro, recusa caminho absoluto ou com caractere especial)."""
    for c in clipes:
        if not re.fullmatch(r"clipe-\d{2}\.mp4", c.name):
            raise MontagemError(f"nome de clipe inesperado: {c.name!r}")
    return "ffconcat version 1.0\n" + "".join(f"file {c.name}\n" for c in clipes)


def comando_final(lista: Path, formato: str, total: float, sobre: Path | None, trilha: Path | None,
                  trilha_formato: str | None, saida: Path, threads: int = THREADS) -> list[str]:  # fmt: skip
    """2ª etapa: os clipes um depois do outro (o demuxer concat abre um por vez: um decodificador só), a imagem por
    cima (textos e logo, desenhados aqui), a trilha ou o silêncio, e o MP4 final (H.264 + AAC). Só caminhos e
    números deste módulo entram na linha: nenhum texto do cliente."""
    cmd = [*FFMPEG, *_entrada(lista, "concat")]
    if sobre is not None:
        cmd += _entrada(sobre, "png_pipe")
    if trilha is not None:
        if trilha_formato not in ("mp3", "ogg", "flac", "wav", "mp4"):
            raise MontagemError(f"formato da trilha desconhecido: {trilha_formato!r}")
        cmd += ["-stream_loop", "-1", *_entrada(trilha, trilha_formato)]
    if sobre is not None:
        partes = ["[0:v][1:v]overlay=0:0:format=auto,format=yuv420p[v]"]
    else:
        partes = ["[0:v]format=yuv420p[v]"]
    fim_audio = max(0.0, total - 1.5)
    if trilha is not None:
        indice = 2 if sobre is not None else 1
        partes.append(
            f"[{indice}:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=duration={total:.4f},"
            f"afade=t=in:d=0.3,afade=t=out:st={fim_audio:.4f}:d=1.5,volume=0.85[a]"
        )
    else:
        partes.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={total:.4f}[a]")
    return [
        *cmd, "-filter_complex", ";".join(partes), "-filter_complex_threads", "1", "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", "-t", f"{total:.4f}",
        "-movflags", "+faststart", "-threads", str(threads), *SAIDA_ARQUIVO, str(saida),
    ]  # fmt: skip


def conferir_pedido(entradas: list[Entrada], formato: str, transicao: str) -> None:
    if not MIN_VIDEOS <= len(entradas) <= MAX_VIDEOS:
        raise MontagemError(f"{len(entradas)} vídeos: de {MIN_VIDEOS} a {MAX_VIDEOS}")
    if formato not in FORMATOS or transicao not in TRANSICOES:
        raise MontagemError(f"formato {formato!r} ou transição {transicao!r} desconhecidos")
    if any(not 1 <= e.duracao <= 60 for e in entradas):
        raise MontagemError("duração de vídeo fora de 1 a 60 s")


def _limitar(pid: int) -> None:
    """Limites do processo do ffmpeg, aplicados logo depois de ele nascer (preexec_fn não é seguro com threads)."""
    for lim, valor in (
        (resource.RLIMIT_CPU, CPU_S),
        (resource.RLIMIT_FSIZE, SAIDA_BYTES),
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_NOFILE, 256),
    ):
        with contextlib.suppress(OSError, ValueError, AttributeError):
            resource.prlimit(pid, lim, (valor, valor))


def executar(cmd: list[str], pasta: Path, tempo_s: float = TEMPO_S) -> None:
    """Roda o ffmpeg na pasta da montagem, com o ambiente vazio (nenhum segredo do contêiner) e os limites; o tempo
    de relógio vencido derruba o grupo inteiro do processo. MontagemError com o fim da saída de erro."""
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(pasta)}
    try:
        p = subprocess.Popen(
            cmd, cwd=pasta, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, start_new_session=True,
        )  # fmt: skip
    except OSError as e:
        raise MontagemError(f"o ffmpeg não abriu: {e}") from e
    _limitar(p.pid)
    try:
        _, erro = p.communicate(timeout=tempo_s)
    except subprocess.TimeoutExpired as e:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(p.pid, signal.SIGKILL)
        p.communicate()
        raise MontagemError(f"o ffmpeg passou de {tempo_s:.0f} s") from e
    if p.returncode != 0:
        fim = erro.decode("utf-8", "replace").strip()[-600:]
        raise MontagemError(f"ffmpeg saiu com {p.returncode}: {fim}")


def conferir_saida(caminho: Path, duracao: float) -> dict[str, Any]:
    """O ffprobe na montagem: um vídeo H.264 e um áudio AAC, com a duração esperada (meio segundo de folga)."""
    if not caminho.is_file() or not 0 < caminho.stat().st_size <= SAIDA_BYTES:
        raise MontagemError("o ffmpeg não gravou a montagem")
    cmd = [
        "ffprobe", "-v", "error", "-protocol_whitelist", "file", "-f", "mp4", "-show_entries",
        "stream=codec_type,codec_name,width,height:format=duration", "-of", "json", str(caminho),
    ]  # fmt: skip
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60, env={"PATH": "/usr/local/bin:/usr/bin:/bin"})
        info = json.loads(r.stdout or b"{}")
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        raise MontagemError(f"ffprobe: {e}") from e
    tipos = {(s.get("codec_type"), s.get("codec_name")) for s in info.get("streams", [])}
    real = float((info.get("format") or {}).get("duration") or 0)
    if ("video", "h264") not in tipos or ("audio", "aac") not in tipos:
        raise MontagemError(f"a montagem saiu sem H.264 e AAC: {sorted(map(str, tipos))}")
    if abs(real - duracao) > 0.5:
        raise MontagemError(f"a montagem tem {real:.2f} s, e eram {duracao:.2f}")
    video = next(s for s in info["streams"] if s.get("codec_type") == "video")
    return {"duracao": round(real, 3), "largura": video.get("width"), "altura": video.get("height"),
            "bytes": caminho.stat().st_size}  # fmt: skip


@dataclass(frozen=True)
class Resultado:
    caminho: Path
    duracao: float
    bytes: int
    segundos: float  # tempo do ffmpeg


def montar(entradas: list[Entrada], formato: str, transicao: str, textos: dict[str, str], logo: bytes | None,
           trilha: str | None, pasta: Path, arquivos: Path | None = None) -> Resultado:  # fmt: skip
    """A montagem inteira numa pasta de trabalho: a imagem por cima, o ffmpeg e a conferência da saída."""
    arquivos = arquivos or pasta_padrao()
    for campo, texto in textos.items():  # a API já conferiu (e o C1); aqui, de novo, o que a imagem precisa
        if problema := problema_texto(campo, texto):
            raise MontagemError(problema)
    fonte = None
    if any(textos.values()):
        try:
            fonte = conferido(arquivos, "fonte.ttf")
        except ArquivoIndisponivel as e:
            raise MontagemError(str(e)) from e
    try:
        png = sobreposicao(formato, textos, logo, fonte) if (any(textos.values()) or logo) else None
    except (pessoas.FotoInvalida, OSError, ValueError, Image.DecompressionBombError) as e:
        raise MontagemError(f"o logo não abriu: {e}", "o logo não pôde ser usado") from e
    sobre = None
    if png is not None:
        sobre = pasta / "sobre.png"
        sobre.write_bytes(png)
    faixa = formato_faixa = None
    if trilha:
        t = TRILHAS.get(trilha)
        if t is None:
            raise MontagemError(f"trilha desconhecida: {trilha}", "a trilha escolhida não está disponível")
        try:
            faixa = conferido(arquivos, t["arquivo"])
        except ArquivoIndisponivel as e:
            raise MontagemError(str(e), "a trilha escolhida não está disponível") from e
        formato_faixa = t["formato"]
    conferir_pedido(entradas, formato, transicao)
    t0 = time.monotonic()
    clipes = []
    for i, e in enumerate(entradas):
        clipe = pasta / f"clipe-{i:02d}.mp4"
        executar(comando_clipe(e, formato, transicao, i == 0, i == len(entradas) - 1, clipe), pasta)
        clipes.append(clipe)
    lista = pasta / "clipes.txt"
    lista.write_text(lista_concat(clipes))
    total = duracao_total([e.duracao for e in entradas], transicao)
    saida = pasta / "montagem.mp4"
    executar(comando_final(lista, formato, total, sobre, faixa, formato_faixa, saida), pasta)
    segundos = time.monotonic() - t0
    for clipe in clipes:  # o /tmp do montador conta na memória dele
        clipe.unlink(missing_ok=True)
    info = conferir_saida(saida, total)
    if (info["largura"], info["altura"]) != FORMATOS[formato]:
        raise MontagemError(f"a montagem saiu em {info['largura']}×{info['altura']}")
    return Resultado(saida, info["duracao"], info["bytes"], round(segundos, 2))


# -- o serviço (contêiner montador) ----------------------------------------------------------------------------


def chave_saida(cliente: str, montagem: str, arquivo: str) -> str:
    from render2go_servico.armazenamento import chave_saida as saida

    return saida(cliente, montagem, arquivo)


def chave_logo(cliente: str, montagem: str, nome: str) -> str:
    """O logo enviado (`logo-original.<ext>`) e o regravado pelo analisador (`logo.png`), na pasta de entrada da
    montagem (apagados em 7 dias pela regra do bucket, e antes, quando a montagem termina)."""
    from render2go_servico.armazenamento import chave_entrada

    return chave_entrada(cliente, montagem, nome)


def nome_arquivo(montagem_id: str, criado: float) -> str:
    """O nome do MP4 para o cliente: montagem-<data>-<6 caracteres do id>.mp4."""
    return f"montagem-{time.strftime('%Y-%m-%d', time.gmtime(criado))}-{montagem_id.split('_')[-1][:6]}.mp4"


def processar(banco: Any, armazenamento: Any, m: dict, arquivos: Path | None = None) -> None:
    """Uma montagem reivindicada (`Banco.reivindicar_montagem`): baixa os vídeos (e o logo regravado), monta, envia a
    saída para `saida/<cliente>/<montagem>/` e conclui (a cobrança é na conclusão "ok", ADR 0015). Qualquer erro
    conclui em "erro", sem custo, com o motivo resumido para o cliente e o texto técnico para o painel."""
    pedido = json.loads(m["pedido"])
    with tempfile.TemporaryDirectory(prefix="montagem-") as tmp:
        pasta = Path(tmp)
        try:
            entradas = []
            for i, v in enumerate(m["videos"]):
                dados = armazenamento.ler(chave_saida(m["cliente_id"], v["lote_id"], v["arquivo"]), VIDEO_BYTES)
                caminho = pasta / f"video-{i:02d}.mp4"
                caminho.write_bytes(dados)
                entradas.append(Entrada(caminho, int(v["duracao"])))
            logo = None
            if pedido.get("logo"):
                logo = armazenamento.ler(chave_logo(m["cliente_id"], m["id"], "logo.png"), LOGO_BYTES)
            r = montar(entradas, pedido["formato"], pedido["transicao"], pedido.get("textos") or {}, logo,
                       pedido.get("trilha"), pasta, arquivos)  # fmt: skip
            arquivo = nome_arquivo(m["id"], m["criado"])
            armazenamento.gravar(chave_saida(m["cliente_id"], m["id"], arquivo), r.caminho.read_bytes(), "video/mp4")
        except MontagemError as e:
            banco.concluir_montagem(m["id"], m["worker"], "erro", erro=str(e)[:1000], motivo=e.motivo)
            print(f"[montagem] {m['id']}: erro: {e}", flush=True)
            return
        except Exception as e:  # noqa: BLE001 (armazenamento, rede: erro técnico; o cliente vê o motivo genérico)
            banco.concluir_montagem(m["id"], m["worker"], "erro", erro=f"{e.__class__.__name__}: {e}"[:1000])
            print(f"[montagem] {m['id']}: erro: {e}", flush=True)
            return
    banco.concluir_montagem(m["id"], m["worker"], "ok", arquivo=arquivo, bytes_video=r.bytes, duracao=r.duracao,
                            segundos=r.segundos)  # fmt: skip
    print(f"[montagem] {m['id']}: pronta ({r.duracao:.1f} s, {r.bytes // 1024} KB, ffmpeg {r.segundos:.1f} s)",
          flush=True)  # fmt: skip


def apagar_logo(armazenamento: Any, m: dict) -> None:
    """O logo (enviado e regravado) sai do armazenamento quando a montagem termina (minimiza o que guardamos)."""
    pedido = json.loads(m["pedido"]) if isinstance(m.get("pedido"), str) else (m.get("pedido") or {})
    if not pedido.get("logo"):
        return
    for nome in ("logo.png", f"logo-original.{pedido.get('logo_ext') or 'png'}"):
        with contextlib.suppress(Exception):  # a regra de 7 dias do bucket apaga depois
            armazenamento.apagar(chave_logo(m["cliente_id"], m["id"], nome))


def montar_continuamente(banco: Any, armazenamento: Any, parar: threading.Event, worker: str = "montador",
                         intervalo_s: float = 5, vivo: Path | None = None,
                         arquivos: Path | None = None) -> None:  # fmt: skip
    """O laço do contêiner montador: vence o que ficou parado (`Banco.vencer_montagens`), pega a próxima montagem e a
    faz, uma de cada vez. Sinal de vida: o arquivo `vivo` (healthcheck) e o banco a cada minuto (o painel mostra)."""
    sinal = 0.0
    while not parar.is_set():
        feita = False
        try:
            if vivo is not None:
                vivo.touch()
            if time.time() - sinal >= 60:
                banco.definir_estado("montador_vivo", None)
                sinal = time.time()
            for m in banco.vencer_montagens():
                apagar_logo(armazenamento, m)
            m = banco.reivindicar_montagem(worker, LEASE_S)
            if m is not None:
                processar(banco, armazenamento, m, arquivos)
                apagar_logo(armazenamento, m)
                feita = True
        except Exception as e:  # noqa: BLE001 (o laço não pode morrer; tenta de novo no próximo intervalo)
            print(f"[montagem] AVISO: {e}", flush=True)
        if not feita:
            parar.wait(intervalo_s)


# -- linha de comando (build da imagem) --------------------------------------------------------------------------


def _video_sintetico(caminho: Path, duracao: int, w: int, h: int, cor: str) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         f"testsrc2=size={w}x{h}:rate={FPS}:duration={duracao + 1 / FPS:.4f}", "-vf", f"drawbox=c={cor}@0.3:t=fill",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-f", "mp4", str(caminho)],
        check=True, timeout=120,
    )  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    """`baixar [pasta]`: a fonte, a licença e as faixas da biblioteca, conferidas. `conferir [pasta]`: duas montagens
    pequenas com o ffmpeg da imagem (vídeos sintéticos): vertical com esmaecimento, textos e logo; horizontal com
    corte seco e sem nada por cima; cada uma conferida pelo ffprobe (formato, H.264, AAC e duração)."""
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in ("baixar", "conferir"):
        print("uso: python -m render2go_servico.montagem baixar|conferir [pasta]", file=sys.stderr)
        return 2
    pasta = Path(args[1]) if len(args) > 1 else pasta_padrao()
    if args[0] == "baixar":
        baixar(pasta)
        return 0
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("erro: ffmpeg e ffprobe não estão no PATH", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        _video_sintetico(t / "a.mp4", 3, 480, 832, "orange")
        _video_sintetico(t / "b.mp4", 3, 832, 480, "blue")
        logo = io.BytesIO()
        Image.new("RGBA", (300, 120), (67, 56, 202, 255)).save(logo, "PNG")
        entradas = [Entrada(t / "a.mp4", 3), Entrada(t / "b.mp4", 3)]
        textos = {"titulo": "Promoção de inverno", "preco": "R$ 49,90", "chamada": "Peça já pelo WhatsApp"}
        for formato, transicao, com_textos in (("9:16", "fade", True), ("16:9", "corte", False)):
            trabalho = t / formato.replace(":", "x")
            trabalho.mkdir()
            r = montar(entradas, formato, transicao, textos if com_textos else {},
                       logo_png(logo.getvalue()) if com_textos else None, None, trabalho, pasta)  # fmt: skip
            print(f"montagem {formato} ({transicao}): {r.duracao:.2f} s, {r.bytes} bytes, ffmpeg {r.segundos:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
