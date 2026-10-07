import shutil
import subprocess

import pytest
from PIL import Image, ImageDraw

from gpu2go_worker import conferencia

com_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")


def _foto(path, cor, forma):
    im = Image.new("RGB", (640, 480), cor)
    d = ImageDraw.Draw(im)
    if forma == "circulo":
        d.ellipse((200, 120, 440, 360), fill=(250, 250, 250))
    else:
        d.rectangle((60, 60, 300, 420), fill=(20, 20, 20))
        d.rectangle((380, 200, 600, 260), fill=(240, 200, 40))
    im.save(path)
    return path


def _video_de(foto, destino):
    # "vídeo I2V" sintético: começa na foto e aproxima devagar
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(foto), "-vf",
         "zoompan=z='1+0.004*on':d=33:s=640x480:fps=16", "-frames:v", "33", "-pix_fmt", "yuv420p",
         str(destino)],
        check=True,
    )
    return destino


@com_ffmpeg
def test_video_matches_only_its_own_photo(tmp_path):
    a = _foto(tmp_path / "a.png", (40, 90, 160), "circulo")
    b = _foto(tmp_path / "b.png", (200, 120, 60), "barras")
    va = _video_de(a, tmp_path / "a.mp4")
    assert conferencia.similaridade(a, va) > 0.95
    assert conferencia.similaridade(b, va) < 0.5
    assert conferencia.confere(a, va) > conferencia.LIMIAR
    with pytest.raises(conferencia.ConferenciaError, match="não corresponde"):
        conferencia.confere(b, va)


def test_broken_video_is_an_error(tmp_path):
    ruim = tmp_path / "ruim.mp4"
    ruim.write_bytes(b"isto nao e video")
    foto = _foto(tmp_path / "a.png", (0, 0, 0), "circulo")
    with pytest.raises(conferencia.ConferenciaError):
        conferencia.similaridade(foto, ruim)
