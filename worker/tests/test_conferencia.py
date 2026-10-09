import shutil
import subprocess

import pytest
from PIL import Image, ImageDraw

from render2go_worker import conferencia

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
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-loop",
            "1",
            "-i",
            str(foto),
            "-vf",
            "zoompan=z='1+0.004*on':d=33:s=640x480:fps=16",
            "-frames:v",
            "33",
            "-pix_fmt",
            "yuv420p",
            str(destino),
        ],
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


def _video_que_troca(foto_a, foto_b, destino, fusao: bool):
    # 2 s na foto A e 2 s na foto B: com corte seco ou com fusão de 1 s (xfade) entre as duas
    filtro = (
        "[0][1]xfade=transition=fade:duration=1:offset=0.5,format=yuv420p"
        if fusao
        else "[0][1]concat=n=2:v=1,format=yuv420p"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-loop",
            "1",
            "-t",
            "2",
            "-r",
            "16",
            "-i",
            str(foto_a),
            "-loop",
            "1",
            "-t",
            "2",
            "-r",
            "16",
            "-i",
            str(foto_b),
            "-filter_complex",
            filtro,
            str(destino),
        ],
        check=True,
    )
    return destino


@com_ffmpeg
def test_continuity_passes_a_steady_video_and_catches_product_swaps(tmp_path):
    # produto escuro no centro (como o carro preto de 08/10) que vira um produto claro
    escuro = _foto(tmp_path / "escuro.png", (225, 225, 225), "barras")
    claro = tmp_path / "claro.png"
    Image.new("RGB", (640, 480), (235, 235, 235)).save(claro)
    im = Image.open(claro)
    ImageDraw.Draw(im).rectangle((60, 60, 600, 420), fill=(245, 245, 240))
    im.save(claro)

    bom = conferencia.continuidade(_video_de(escuro, tmp_path / "bom.mp4"))
    assert conferencia.problema_continuidade(**bom) is None
    assert bom["salto_cor"] < 3  # a aproximação muda a cor do centro aos poucos, sem saltos

    corte = conferencia.continuidade(_video_que_troca(escuro, claro, tmp_path / "c.mp4", False))
    assert corte["salto_cor"] > conferencia.LIMITE_SALTO_COR
    assert "corte brusco" in conferencia.problema_continuidade(**corte)

    fusao = conferencia.continuidade(_video_que_troca(escuro, claro, tmp_path / "f.mp4", True))
    assert fusao["salto_cor"] <= conferencia.LIMITE_SALTO_COR  # gradual: só a deriva pega
    assert fusao["deriva_cor"] > conferencia.LIMITE_DERIVA_COR
    assert "mudou de cor" in conferencia.problema_continuidade(**fusao)


def test_missing_continuity_measure_fails_closed(tmp_path):
    assert conferencia.problema_continuidade(None, 1.0) == "conferência da continuidade ausente"
    assert conferencia.problema_continuidade(5.0, None) == "conferência da continuidade ausente"
    ruim = tmp_path / "ruim.mp4"
    ruim.write_bytes(b"isto nao e video")
    with pytest.raises(conferencia.ConferenciaError):
        conferencia.continuidade(ruim)
