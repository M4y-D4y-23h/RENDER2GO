""" "Continuar vídeo": último quadro de um clipe e concatenação de clipes sem recodificar.

Fluxo (especificação, seção 9.2): o último quadro do clipe N vira a imagem de entrada do
I2V do clipe N+1; no fim, os clipes são concatenados com o demuxer concat do ffmpeg e
`-c copy` (sem perda e sem custo de GPU).

Limites e efeitos conhecidos:
- No máximo MAX_CHAIN_CLIPS clipes: depois de 3 a 4 elos o personagem e as cores desviam.
- O 1º quadro do clipe N+1 é praticamente igual ao último do clipe N, então o vídeo final
  repete um quadro (1/16 s) em cada emenda. Tirar esse quadro exigiria recodificar.
- Só concatena clipes com o mesmo codec, resolução, fps e formato de pixel.

Exige `ffmpeg` e `ffprobe` no PATH (já estão na imagem do worker).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

MAX_CHAIN_CLIPS = 4


class VideoError(Exception):
    pass


@dataclass(frozen=True)
class VideoInfo:
    codec: str
    width: int
    height: int
    pix_fmt: str
    fps: Fraction
    frames: int
    duration_s: float

    def compatible_with(self, other: VideoInfo) -> list[str]:
        diffs = []
        for name in ("codec", "width", "height", "pix_fmt", "fps"):
            a, b = getattr(self, name), getattr(other, name)
            if a != b:
                diffs.append(f"{name} {a} ≠ {b}")
        return diffs


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    if shutil.which(cmd[0]) is None:
        raise VideoError(f"{cmd[0]} não encontrado no PATH")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-5:])
        raise VideoError(f"{cmd[0]} falhou ({proc.returncode}): {tail}")
    return proc


def probe(path: str | Path) -> VideoInfo:
    path = Path(path)
    if not path.is_file():
        raise VideoError(f"arquivo inexistente: {path}")
    out = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=codec_name,width,height,pix_fmt,avg_frame_rate,nb_read_frames:format=duration",
            "-of",
            "json",
            str(path),
        ]
    ).stdout
    data = json.loads(out)
    if not data.get("streams"):
        raise VideoError(f"sem faixa de vídeo: {path}")
    s = data["streams"][0]
    return VideoInfo(
        codec=s["codec_name"],
        width=int(s["width"]),
        height=int(s["height"]),
        pix_fmt=s.get("pix_fmt", ""),
        fps=Fraction(s["avg_frame_rate"]),
        frames=int(s.get("nb_read_frames", 0)),
        duration_s=float(data.get("format", {}).get("duration", 0.0)),
    )


def last_frame(video: str | Path, out_png: str | Path) -> Path:
    """Grava o último quadro decodificado como PNG (sem perda) e confere as dimensões."""
    info = probe(video)
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    # -sseof -1: decodifica só o último segundo; -update 1 sobrescreve a cada quadro,
    # então o arquivo final é o último quadro.
    _run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-sseof",
            "-1",
            "-i",
            str(video),
            "-map",
            "0:v:0",
            "-update",
            "1",
            "-f",
            "image2",
            "-c:v",
            "png",
            str(out_png),
        ]
    )
    if not out_png.is_file() or out_png.stat().st_size == 0:
        raise VideoError("o ffmpeg não gravou o quadro")
    frame = probe(out_png)
    if (frame.width, frame.height) != (info.width, info.height):
        raise VideoError(
            f"quadro {frame.width}x{frame.height} difere do vídeo {info.width}x{info.height}"
        )
    return out_png


def concat(clips: list[str | Path], out: str | Path, max_clips: int = MAX_CHAIN_CLIPS) -> VideoInfo:
    """Concatena sem recodificar; confere compatibilidade antes e o total de quadros depois."""
    clips = [Path(c) for c in clips]
    if len(clips) < 2:
        raise VideoError("são precisos pelo menos 2 clipes")
    if len(clips) > max_clips:
        raise VideoError(
            f"{len(clips)} clipes: o limite é {max_clips} (depois disso o vídeo desvia)"
        )
    infos = [probe(c) for c in clips]
    for clip, info in zip(clips[1:], infos[1:], strict=True):
        diffs = infos[0].compatible_with(info)
        if diffs:
            raise VideoError(f"{clip.name} incompatível com {clips[0].name}: {'; '.join(diffs)}")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        listing = Path(tmp) / "lista.txt"
        lines = []
        for c in clips:
            escaped = str(c.resolve()).replace("'", "'\\''")
            lines.append(f"file '{escaped}'")
        listing.write_text("\n".join(lines) + "\n")
        _run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-map",
                "0:v:0",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(out),
            ]
        )
    result = probe(out)
    expected = sum(i.frames for i in infos)
    if result.frames != expected:
        raise VideoError(f"o vídeo final tem {result.frames} quadros; esperado {expected}")
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="render2go-video", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    lf = sub.add_parser("last-frame", help="grava o último quadro como PNG")
    lf.add_argument("video", type=Path)
    lf.add_argument("-o", "--out", type=Path, required=True)
    cc = sub.add_parser("concat", help=f"concatena até {MAX_CHAIN_CLIPS} clipes sem recodificar")
    cc.add_argument("clips", nargs="+", type=Path)
    cc.add_argument("-o", "--out", type=Path, required=True)
    pr = sub.add_parser("probe", help="mostra codec, resolução, fps e quadros")
    pr.add_argument("video", type=Path)
    a = p.parse_args(argv)
    try:
        if a.cmd == "last-frame":
            print(last_frame(a.video, a.out))
        elif a.cmd == "concat":
            info = concat(a.clips, a.out)
            print(f"{a.out}: {info.frames} quadros, {info.duration_s:.2f} s")
        else:
            info = probe(a.video)
            print(json.dumps({**info.__dict__, "fps": str(info.fps)}, ensure_ascii=False))
    except VideoError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
