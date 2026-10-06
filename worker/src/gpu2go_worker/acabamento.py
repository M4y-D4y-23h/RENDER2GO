"""Acabamento dos vídeos para postar: junta clipes, põe o logo, o cartão final e a música.

    gpu2go-acabamento v1.mp4 v2.mp4 --saida final.mp4 \\
        --logo logo.png --cartao cartao.png --musica trilha.mp3 --fps 24

- Os clipes são juntados na ordem, ajustados ao tamanho do primeiro (com faixas, sem
  cortar) e recodificados em H.264 (diferente do `video concat`, que não recodifica).
- Logo: PNG (de preferência com fundo transparente) num canto, com largura proporcional
  ao vídeo. Não aparece no cartão final.
- Cartão final: uma imagem (contato, endereço, oferta) que fica `--cartao-s` segundos.
- Música: o arquivo enviado pelo cliente ou com licença de uso comercial. Entra e sai
  suave (fade) e é cortada no fim do vídeo.
- `--fps 24` ou `30`: os vídeos do Wan têm 16 fps. Sem `--interpolar`, os quadros são
  repetidos e o movimento não fica mais suave.
- `--interpolar`: cria os quadros intermediários por estimativa de movimento (ffmpeg
  `minterpolate`, CPU, ~20 s por clipe de 5 s): 16 → 32 fps, ou o `--fps` pedido. É bem
  mais barato que renderizar mais quadros na GPU.
- `--altura 1280` (ou 1920): amplia a saída com Lanczos. Não cria detalhe novo como uma
  ampliação com IA, mas entrega no tamanho que as redes esperam, sem custo de GPU.
- `--zoom`: zoom lento e contínuo no centro (até 8%, "cinematic zoom"). Garante movimento
  de câmera quando o modelo deixa a cena parada (ex.: fachada, rodada de 06/10/2026).

Exige `ffmpeg` e `ffprobe` no PATH.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from gpu2go_worker import video

IMAGENS = {".png", ".jpg", ".jpeg", ".webp"}
AUDIOS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}
POSICOES = ("inferior-direito", "inferior-esquerdo", "superior-direito", "superior-esquerdo")
MAX_CLIPES = 20
FADE_ENTRADA_S = 0.5
FADE_SAIDA_S = 1.0


class AcabamentoError(Exception):
    pass


@dataclass(frozen=True)
class Opcoes:
    logo: Path | None = None
    logo_pos: str = "inferior-direito"
    logo_largura: float = 0.18
    cartao: Path | None = None
    cartao_s: float = 2.0
    musica: Path | None = None
    volume: float = 0.8
    fps: int | None = None
    crf: int = 18
    interpolar: bool = False
    altura: int | None = None
    zoom: bool = False


def _confere(entradas: list[Path], saida: Path, o: Opcoes) -> None:
    if not entradas:
        raise AcabamentoError("informe pelo menos um clipe")
    if len(entradas) > MAX_CLIPES:
        raise AcabamentoError(f"no máximo {MAX_CLIPES} clipes")
    if saida.suffix.lower() != ".mp4":
        raise AcabamentoError("a saída precisa ser .mp4")
    if saida.resolve() in {e.resolve() for e in entradas}:
        raise AcabamentoError("a saída não pode sobrescrever um dos clipes")
    arquivos = (("logo", o.logo, IMAGENS), ("cartão", o.cartao, IMAGENS))
    for nome, path, tipos in (*arquivos, ("música", o.musica, AUDIOS)):
        if path is None:
            continue
        if path.suffix.lower() not in tipos:
            raise AcabamentoError(f"{nome}: use {', '.join(sorted(tipos))}")
        if not path.is_file():
            raise AcabamentoError(f"{nome}: arquivo inexistente: {path}")
    if o.logo_pos not in POSICOES:
        raise AcabamentoError(f"posição do logo: use {', '.join(POSICOES)}")
    if not 0.05 <= o.logo_largura <= 0.5:
        raise AcabamentoError("largura do logo: de 0,05 a 0,5 da largura do vídeo")
    if not 0.5 <= o.cartao_s <= 10:
        raise AcabamentoError("duração do cartão: de 0,5 a 10 s")
    if not 0 <= o.volume <= 2:
        raise AcabamentoError("volume: de 0 a 2")
    if o.fps is not None and not 8 <= o.fps <= 60:
        raise AcabamentoError("fps: de 8 a 60")
    if o.altura is not None and (not 240 <= o.altura <= 2160 or o.altura % 2):
        raise AcabamentoError("altura: par, de 240 a 2160")


ZOOM_MAX = 1.08


def _fit(w: int, h: int, fps: str) -> str:
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p"
    )


def _clipe(w: int, h: int, fps: str, info: video.VideoInfo, o: Opcoes) -> str:
    """Filtro de um clipe: tamanho, zoom opcional e fps (repetindo ou interpolando)."""
    partes = [
        f"scale={w}:{h}:force_original_aspect_ratio=decrease",
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1",
    ]
    if o.zoom:
        passo = (ZOOM_MAX - 1) / max(1, info.frames - 1)
        partes.append(
            f"zoompan=z='min(zoom+{passo:.6f},{ZOOM_MAX})':d=1"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={info.fps}"
        )
    if o.interpolar:
        partes.append(f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1")
    else:
        partes.append(f"fps={fps}")
    return ",".join([*partes, "format=yuv420p"])


def comando(
    entradas: list[Path], infos: list[video.VideoInfo], saida: Path, o: Opcoes
) -> tuple[list[str], float]:
    """Linha de comando do ffmpeg e duração final em segundos (sem executar nada)."""
    _confere(entradas, saida, o)
    w, h = infos[0].width, infos[0].height
    base_fps = infos[0].fps * 2 if o.interpolar else infos[0].fps
    fps = str(o.fps) if o.fps else str(base_fps)
    total = sum(i.duration_s for i in infos) + (o.cartao_s if o.cartao else 0.0)
    total = round(total, 3)

    cmd = ["ffmpeg", "-v", "error", "-y"]
    for e in entradas:
        cmd += ["-i", str(e)]
    n = len(entradas)
    filtros = [f"[{i}:v]{_clipe(w, h, fps, infos[i], o)}[v{i}]" for i in range(n)]
    filtros.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[base]")
    atual, idx = "base", n

    if o.logo:
        cmd += ["-i", str(o.logo)]
        lw = max(2, int(w * o.logo_largura) // 2 * 2)
        m = max(4, round(min(w, h) * 0.04))
        x = f"main_w-overlay_w-{m}" if o.logo_pos.endswith("direito") else str(m)
        y = f"main_h-overlay_h-{m}" if o.logo_pos.startswith("inferior") else str(m)
        filtros.append(f"[{idx}:v]scale={lw}:-1,format=rgba[logo]")
        filtros.append(f"[{atual}][logo]overlay={x}:{y}:format=auto,format=yuv420p[comlogo]")
        atual, idx = "comlogo", idx + 1

    if o.cartao:
        cmd += ["-loop", "1", "-framerate", fps, "-t", str(o.cartao_s), "-i", str(o.cartao)]
        filtros.append(f"[{idx}:v]{_fit(w, h, fps)}[cartao]")
        filtros.append(f"[{atual}][cartao]concat=n=2:v=1:a=0[comcartao]")
        atual, idx = "comcartao", idx + 1

    if o.altura and o.altura != h:
        filtros.append(f"[{atual}]scale=-2:{o.altura}:flags=lanczos,setsar=1[final]")
        atual = "final"

    mapas = ["-map", f"[{atual}]"]
    audio: list[str] = []
    if o.musica:
        cmd += ["-i", str(o.musica)]
        saida_fade = max(0.0, total - FADE_SAIDA_S)
        filtros.append(
            f"[{idx}:a]atrim=0:{total},asetpts=PTS-STARTPTS,volume={o.volume},"
            f"afade=t=in:d={FADE_ENTRADA_S},afade=t=out:st={saida_fade}:d={FADE_SAIDA_S},"
            f"apad[audio]"
        )
        mapas += ["-map", "[audio]"]
        audio = ["-c:a", "aac", "-b:a", "192k"]

    cmd += ["-filter_complex", ";".join(filtros), *mapas]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(o.crf), "-pix_fmt", "yuv420p"]
    cmd += [*audio, "-t", str(total), "-movflags", "+faststart", str(saida)]
    return cmd, total


def acabar(entradas: list[Path], saida: Path, o: Opcoes) -> video.VideoInfo:
    _confere(entradas, saida, o)
    infos = [video.probe(e) for e in entradas]
    cmd, _ = comando(entradas, infos, saida, o)
    saida.parent.mkdir(parents=True, exist_ok=True)
    video._run(cmd)
    return video.probe(saida)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gpu2go-acabamento",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n\n".join(__doc__.split("\n\n")[1:]),
    )
    p.add_argument("clipes", nargs="+", type=Path, help="clipes MP4, na ordem")
    p.add_argument("--saida", type=Path, required=True, help="MP4 final")
    p.add_argument("--logo", type=Path, help="imagem do logo (PNG transparente)")
    p.add_argument("--logo-pos", choices=POSICOES, default="inferior-direito")
    p.add_argument(
        "--logo-largura", type=float, default=0.18, help="fração da largura (padrão 0,18)"
    )
    p.add_argument("--cartao", type=Path, help="imagem do cartão final")
    p.add_argument("--cartao-s", type=float, default=2.0, help="segundos do cartão (padrão 2)")
    p.add_argument("--musica", type=Path, help="música (do cliente ou com licença comercial)")
    p.add_argument("--volume", type=float, default=0.8, help="volume da música (padrão 0,8)")
    p.add_argument("--fps", type=int, help="fps da saída; padrão: o do primeiro clipe")
    p.add_argument("--crf", type=int, default=18, help="qualidade do H.264 (menor = melhor)")
    p.add_argument(
        "--interpolar", action="store_true", help="cria quadros intermediários (16 → 32 fps)"
    )
    p.add_argument("--altura", type=int, help="altura final em pixels, ex.: 1280 ou 1920")
    p.add_argument("--zoom", action="store_true", help="zoom lento e contínuo (até 8%%)")
    p.add_argument("--mostrar", action="store_true", help="só mostra o comando do ffmpeg")
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    o = Opcoes(
        logo=a.logo,
        logo_pos=a.logo_pos,
        logo_largura=a.logo_largura,
        cartao=a.cartao,
        cartao_s=a.cartao_s,
        musica=a.musica,
        volume=a.volume,
        fps=a.fps,
        crf=a.crf,
        interpolar=a.interpolar,
        altura=a.altura,
        zoom=a.zoom,
    )
    try:
        if a.mostrar:
            infos = [video.probe(c) for c in a.clipes]
            cmd, total = comando(a.clipes, infos, a.saida, o)
            print(" ".join(cmd))
            print(f"duração final: {total:.2f} s", file=sys.stderr)
            return 0
        info = acabar(a.clipes, a.saida, o)
    except (AcabamentoError, video.VideoError) as e:
        print(f"ERRO {e}", file=sys.stderr)
        return 2
    print(
        f"{a.saida}: {info.width}x{info.height}, {info.fps} fps, {info.duration_s:.2f} s",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
