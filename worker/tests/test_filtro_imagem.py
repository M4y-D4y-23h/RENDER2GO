"""Filtro de imagem nos quadros (ADR 0005), sem o classificador de verdade: a regra do projeto
proíbe conteúdo explícito em teste. O bloqueio é testado com notas falsas; a taxa de acerto vem do
cartão do modelo."""

import shutil
import subprocess

import pytest
from conftest import WORKER

from render2go_worker import filtro_imagem as fi

com_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")
FOTO = WORKER.parent / "fixtures" / "sfw" / "produto-cafe.jpg"


@pytest.fixture
def video(tmp_path):
    """3 s a 16 fps (49 quadros), como os vídeos de verdade."""
    v = tmp_path / "video.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-loop", "1", "-i", str(FOTO)]
        + ["-vf", "scale=832:480,fps=16", "-frames:v", "49", "-pix_fmt", "yuv420p", str(v)],
        check=True,
    )
    return v


class Notas:
    def __init__(self, notas):
        self.notas = notas

    def probabilidades(self, quadros):
        assert all(len(q) == fi.LADO * fi.LADO * 3 for q in quadros)
        return [(1 - n, 0.0, n / 2, n / 2) for n in self.notas[: len(quadros)]]


@com_ffmpeg
def test_one_frame_in_four_including_the_first_and_the_last(video):
    qs = fi.quadros(video)
    assert [i for i, _ in qs] == list(range(0, 49, 4))  # 0, 4, ..., 48 (o último quadro)


@com_ffmpeg
def test_any_frame_over_the_threshold_blocks_the_video(video):
    notas = [0.01] * 13
    r = fi.avaliar(video, Notas(notas))
    assert not r.barrado and r.quadros == 13 and r.nota == pytest.approx(0.01)
    notas[7] = fi.LIMIAR  # um quadro no meio (o 29º do vídeo) basta
    r = fi.avaliar(video, Notas(notas))
    assert r.barrado and r.pior_quadro == 28 and r.nota == pytest.approx(fi.LIMIAR)


@com_ffmpeg
def test_fail_closed_when_the_classifier_or_the_video_fails(video, tmp_path):
    class Quebrado:
        def probabilidades(self, quadros):
            raise RuntimeError("CUDA out of memory")

    with pytest.raises(fi.FiltroError, match="classificador falhou"):
        fi.avaliar(video, Quebrado())
    with pytest.raises(fi.FiltroError, match="sem resposta"):
        fi.avaliar(video, Notas([]))
    ruim = tmp_path / "ruim.mp4"
    ruim.write_bytes(b"nao e video")
    with pytest.raises(fi.FiltroError, match="não leu os quadros"):
        fi.avaliar(ruim, Notas([0.0] * 13))
    # sem os pesos (ou sem o timm/torch neste ambiente): não avalia, e nada é enviado
    with pytest.raises(fi.FiltroError):
        fi.Freepik(tmp_path / "nao-existe").probabilidades([b"\0" * (fi.LADO * fi.LADO * 3)])
