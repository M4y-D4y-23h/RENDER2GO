"""Conferência automática: o vídeo pertence à foto e continua nela até o fim? (docs/08, seção 2)

1º quadro: no I2V, o 1º quadro do vídeo é a própria foto de entrada. Comparamos os dois em 32×32
tons de cinza, normalizados (correlação de Pearson): ~1,00 com a foto certa. Nos 16 vídeos de
07/10, a foto mais parecida que não era a certa deu no máximo 0,28.

Continuidade (09/10/2026): o 1º quadro certo não basta. No teste gratuito de 08/10, 2 de 5 vídeos
começaram na foto e depois trocaram de carro (um corte seco para um carro branco aos 2 s; uma
fusão para outro modelo). A medida é a cor média do centro do quadro (metade da largura e da
altura, onde costuma estar o produto), comparada com a do 1º quadro (deriva) e com a do quadro
anterior (salto); diferença = média das diferenças absolutas de R, G e B (0–255). Calibração em
39 vídeos (os 37 aproveitáveis de 06 a 08/10 e os 2 com troca):
- deriva: aproveitáveis até 31,5 (tênis com aproximação forte); trocas em 54 e 86 → limite 40;
- salto: aproveitáveis até 5,8; o corte seco deu 16,2 (a fusão, gradual, cai pela deriva) → 12.
Limitação: a troca por um objeto da mesma cor passa (só a cor média é medida). Medidas de cada
vídeo e métricas descartadas: worker/evals/conferencia-2026-10-09/.

O worker só envia o vídeo que passa nas duas, e a API recusa "ok" sem as medidas (fail-closed).
O vídeo reprovado é refeito com outra seed (até as tentativas do job acabarem).
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

LIMIAR = 0.9
LIMITE_DERIVA_COR = 40.0
LIMITE_SALTO_COR = 12.0
LADO_CENTRO = 8  # o centro é reduzido a 8×8 (média por área) antes da média de cor


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
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video),
            "-vframes",
            "1",
            str(destino),
        ],
        capture_output=True,
        text=True,
        timeout=120,
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


def cores_do_centro(video: Path) -> list[tuple[float, float, float]]:
    """Cor média (R, G, B) do centro de cada quadro do vídeo."""
    lado = LADO_CENTRO
    r = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-vf",
            f"crop=iw/2:ih/2,scale={lado}:{lado}:flags=area",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        timeout=300,
    )
    n = lado * lado * 3
    if r.returncode != 0 or not r.stdout or len(r.stdout) % n:
        erro = r.stderr.decode(errors="replace")[-200:]
        raise ConferenciaError(f"não leu os quadros de {video.name}: {erro}")
    px = lado * lado
    return [
        tuple(sum(r.stdout[i + c : i + n : 3]) / px for c in range(3))  # type: ignore[misc]
        for i in range(0, len(r.stdout), n)
    ]


def _diferenca(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / 3


def continuidade(video: Path) -> dict[str, float]:
    """{"deriva_cor": maior diferença para o 1º quadro, "salto_cor": maior entre vizinhos}."""
    cores = cores_do_centro(video)
    if len(cores) < 2:
        raise ConferenciaError(f"o vídeo {video.name} tem menos de 2 quadros")
    saltos = [_diferenca(cores[i], cores[i + 1]) for i in range(len(cores) - 1)]
    return {
        "deriva_cor": round(max(_diferenca(cores[0], c) for c in cores), 2),
        "salto_cor": round(max(saltos), 2),
    }


def problema_continuidade(deriva_cor: float | None, salto_cor: float | None) -> str | None:
    """Motivo da reprovação, ou None se o vídeo continua na foto. Sem medida = reprovado."""
    if deriva_cor is None or salto_cor is None:
        return "conferência da continuidade ausente"
    if salto_cor > LIMITE_SALTO_COR:
        return f"corte brusco no meio do vídeo (salto de cor {salto_cor:.1f})"
    if deriva_cor > LIMITE_DERIVA_COR:
        return f"o produto mudou de cor ou de forma no vídeo (deriva de cor {deriva_cor:.1f})"
    return None
