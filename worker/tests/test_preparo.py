import io
import shutil
import subprocess

import pytest
from conftest import WORKER
from PIL import Image

from render2go_worker import preparo

PADRAO = WORKER.parent / "fixtures" / "sfw" / "padrao-teste-640.png"


def test_resize_crop_and_exif(tmp_path):
    foto = tmp_path / "celular.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # deitada no arquivo, em pé na tela
    with Image.open(PADRAO) as im:
        im.convert("RGB").resize((4000, 2250)).save(foto, exif=exif)
    p = preparo.preparar(foto, 480, 832, tmp_path / "p.png")
    with Image.open(p.path) as out:
        assert out.size == (480, 832) and out.mode == "RGB"
    assert p.original == (2250, 4000) and p.ampliacao < 1 and p.aviso == ""


def test_small_and_transparent_photo(tmp_path):
    foto = tmp_path / "logo.png"
    Image.new("RGBA", (200, 200), (255, 0, 0, 0)).save(foto)
    p = preparo.preparar(foto, 640, 640, tmp_path / "p.png")
    with Image.open(p.path) as out:
        assert out.getpixel((10, 10)) == (255, 255, 255)  # fundo branco, não preto
    assert "ampliada 3.2x" in p.aviso


def test_rejects_huge_and_broken(tmp_path, monkeypatch):
    foto = tmp_path / "grande.png"
    Image.new("RGB", (300, 300)).save(foto)
    monkeypatch.setattr(preparo, "MAX_PIXELS", 10_000)
    with pytest.raises(preparo.PreparoError, match="grande demais"):
        preparo.preparar(foto, 256, 256, tmp_path / "p.png")
    ruim = tmp_path / "ruim.jpg"
    ruim.write_bytes(b"isto nao e imagem")
    with pytest.raises(preparo.PreparoError, match="ilegível"):
        preparo.preparar(ruim, 256, 256, tmp_path / "p.png")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")
def test_contact_sheet(tmp_path):
    clip = tmp_path / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=240x416:rate=16",
         "-frames:v", "49", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )  # fmt: skip
    out = preparo.folha(PADRAO, clip, tmp_path / "f.jpg", quadros_total=49, altura=128)
    with Image.open(io.BytesIO(out.read_bytes())) as im:
        # foto 128x128 + 8 px + 3 quadros de ~74x128
        assert im.height == 128 and im.width > 128 + 8 + 3 * 70
