"""Conferência automática: o vídeo pertence à foto? (docs/08, seção 2)

No I2V, o 1º quadro do vídeo é a própria foto de entrada. Comparamos os dois em 32×32 tons de
cinza, normalizados (correlação de Pearson): ~1,00 com a foto certa. Nos 16 vídeos de 07/10, a
foto mais parecida que não era a certa deu no máximo 0,28. O worker só envia o vídeo se passar
do limiar, e a API recusa "ok" sem a similaridade (fail-closed).
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

LIMIAR = 0.9


class ConferenciaError(Exception):
    pass


def _vetor(im: Image.Image, tamanho: tuple[int, int]) -> list[float]:
    im = ImageOps.fit(ImageOps.exif_transpose(im).convert("L"), tamanho, Image.LANCZOS)
    pequena = im.resize((32, 32), Image.LANCZOS)
    dados = getattr(pequena, "get_flattened_data", None) or pequena.getdata  # Pillow ≥ 12
    px = list(dados())
    media = sum(px) / len(px)
    desvio = (sum((x - media) ** 2 for x in px) / len(px)) ** 0.5 or 1e-6
    return [(x - media) / desvio for x in px]


def quadro0(video: Path, destino: Path) -> Path:
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(video), "-vframes", "1",
         str(destino)],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0 or not destino.is_file():
        raise ConferenciaError(f"não extraiu o 1º quadro de {video.name}: {r.stderr[-200:]}")
    return destino


def similaridade(foto: Path, video: Path) -> float:
    """Correlação (−1 a 1) entre a foto e o 1º quadro do vídeo."""
    with tempfile.TemporaryDirectory() as d:
        q = quadro0(video, Path(d) / "q0.png")
        with Image.open(q) as iq, Image.open(foto) as ifoto:
            tamanho = iq.size
            a, b = _vetor(iq, tamanho), _vetor(ifoto, tamanho)
    return sum(x * y for x, y in zip(a, b, strict=True)) / len(a)


def confere(foto: Path, video: Path) -> float:
    """Devolve a similaridade; levanta ConferenciaError se o vídeo não for dessa foto."""
    s = similaridade(foto, video)
    if s < LIMIAR:
        raise ConferenciaError(
            f"o vídeo {video.name} não corresponde à foto {foto.name} (similaridade {s:.2f})"
        )
    return s
