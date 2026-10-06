"""Preparo da foto antes do render, e folha de conferência depois.

Preparo (CPU, ~0,1 s por foto):
- aplica a rotação do EXIF (foto de celular);
- converte para RGB, com transparência sobre fundo branco;
- corta no centro para a proporção do vídeo e redimensiona com Lanczos para o tamanho
  exato do render.

O ComfyUI faria esse ajuste com redimensionamento bilinear, que serrilha quando reduz
uma foto de celular (4000 px) para 480–720 px. Com o preparo, a foto chega pronta, e o
envio cai de alguns MB para algumas centenas de KB por foto.

Foto menor que o vídeo é ampliada e marcada no relatório. Ampliação com IA (ESRGAN e
afins) fica para depois, com medição em GPU. Foto acima de MAX_PIXELS é recusada
(proteção contra "bomba de descompressão").

Folha de conferência: a foto preparada ao lado dos quadros inicial, do meio e final do
vídeo, num JPEG pequeno. Serve para revisar um lote inteiro em minutos (cor e rótulo do
produto, rosto, movimento), já que nenhuma métrica automática testada separou os
defeitos (docs/06).
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

MAX_PIXELS = 50_000_000  # 50 MP: acima disso, recusa (o PIL avisa em 89 MP)
AMPLIACAO_AVISO = 1.5  # foto ampliada mais que isso vira aviso no relatório


class PreparoError(Exception):
    pass


@dataclass(frozen=True)
class Preparada:
    path: Path
    original: tuple[int, int]
    ampliacao: float
    """>1: a foto foi ampliada (menor que o vídeo)."""

    @property
    def aviso(self) -> str:
        if self.ampliacao >= AMPLIACAO_AVISO:
            w, h = self.original
            return f"foto pequena ({w}x{h}), ampliada {self.ampliacao:.1f}x"
        return ""


def preparar(foto: Path, width: int, height: int, dest: Path) -> Preparada:
    try:
        with Image.open(foto) as im:
            if im.width * im.height > MAX_PIXELS:
                raise PreparoError(f"foto grande demais ({im.width}x{im.height})")
            im = ImageOps.exif_transpose(im)
            original = im.size
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                fundo = Image.new("RGBA", im.size, (255, 255, 255, 255))
                im = Image.alpha_composite(fundo, im)
            im = im.convert("RGB")
            escala = max(width / original[0], height / original[1])
            out = ImageOps.fit(im, (width, height), Image.Resampling.LANCZOS)
    except (OSError, Image.DecompressionBombError) as e:
        raise PreparoError(f"imagem ilegível: {e}") from e
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.save(dest, "PNG")
    return Preparada(dest, original, round(escala, 2))


def folha(foto: Path, video: Path, dest: Path, quadros_total: int, altura: int = 256) -> Path:
    """Foto | primeiro quadro | quadro do meio | último quadro, lado a lado (JPEG)."""
    meio, fim = quadros_total // 2, quadros_total - 1
    with tempfile.TemporaryDirectory(prefix="gpu2go_folha_") as tmp:
        quadros = Path(tmp) / "q.png"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-i",
                str(video),
                "-vf",
                f"select='eq(n\\,0)+eq(n\\,{meio})+eq(n\\,{fim})',scale=-2:{altura},tile=3x1",
                "-frames:v",
                "1",
                str(quadros),
            ],
            check=True,
            capture_output=True,
        )
        with Image.open(quadros) as q, Image.open(foto) as f:
            f = ImageOps.contain(f.convert("RGB"), (10_000, altura))
            sheet = Image.new("RGB", (f.width + 8 + q.width, altura), (255, 255, 255))
            sheet.paste(f, (0, 0))
            sheet.paste(q.convert("RGB"), (f.width + 8, 0))
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest, "JPEG", quality=82)
    return dest
