"""Fotos enviadas pelo cliente, como o pod as abre (11/10/2026): só JPEG, PNG e WebP, o tamanho em
pixels lido do cabeçalho antes de decodificar e o teto do preparo (50 MP) no caminho da fila."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest
from PIL import Image

from render2go_worker import media, preparo


def _png_cabecalho(caminho: Path, largura: int, altura: int) -> None:
    """PNG válido no cabeçalho, com um tamanho enorme em poucos bytes (bomba de descompressão)."""

    def bloco(tipo: bytes, dados: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(tipo + dados))
        return struct.pack(">I", len(dados)) + tipo + dados + crc

    ihdr = struct.pack(">IIBBBBB", largura, altura, 8, 2, 0, 0, 0)
    idat = bloco(b"IDAT", zlib.compress(b"\0" * 64))
    caminho.write_bytes(b"\x89PNG\r\n\x1a\n" + bloco(b"IHDR", ihdr) + idat + bloco(b"IEND", b""))


@pytest.mark.parametrize("formato", ["JPEG", "PNG", "WEBP"])
def test_the_three_accepted_formats_open(tmp_path, formato):
    foto = tmp_path / f"foto.{formato.lower()}"
    Image.new("RGB", (64, 48), (200, 120, 40)).save(foto, formato)
    assert media.check_image(foto) == (64, 48)


@pytest.mark.parametrize(
    "conteudo",
    [
        b"<html><script>alert(1)</script></html>",  # página disfarçada de foto
        b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64,  # executável do Windows
        b"PK\x03\x04" + b"\x00" * 64,  # arquivo zip
        b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n",  # EPS: chamaria o Ghostscript
    ],
)
def test_files_that_are_not_photos_are_refused(tmp_path, conteudo):
    foto = tmp_path / "foto.jpg"
    foto.write_bytes(conteudo)
    with pytest.raises(media.MediaError, match="imagem inválida"):
        media.check_image(foto)


def test_other_image_formats_are_not_even_parsed(tmp_path):
    """TIFF e outros leitores do Pillow ficam de fora: o arquivo nem chega a ser interpretado."""
    for formato, ext in (("TIFF", "tif"), ("BMP", "bmp"), ("GIF", "gif"), ("ICO", "ico")):
        foto = tmp_path / f"foto.{ext}"
        Image.new("RGB", (32, 32)).save(foto, formato)
        disfarce = tmp_path / f"{ext}.jpg"
        disfarce.write_bytes(foto.read_bytes())
        with pytest.raises(media.MediaError, match="imagem inválida"):
            media.check_image(disfarce)


def test_a_decompression_bomb_is_refused_from_the_header(tmp_path):
    foto = tmp_path / "bomba.png"
    _png_cabecalho(foto, 100_000, 100_000)  # 10 bilhões de pixels em ~100 bytes
    assert foto.stat().st_size < 200
    with pytest.raises(media.MediaError):
        media.check_image(foto, max_pixels=preparo.MAX_PIXELS)
    _png_cabecalho(foto, 8_000, 8_000)  # 64 MP: acima do teto de 50 MP do preparo
    with pytest.raises(media.MediaError, match="grande demais"):
        media.check_image(foto, max_pixels=preparo.MAX_PIXELS)


def test_a_24_megapixel_camera_photo_passes_the_queue_limit(tmp_path):
    """Antes de 11/10/2026, a fila usava o padrão de 16,7 MP (4096²): a foto de uma câmera de 24 MP
    falhava no pod. O caminho da fila usa o teto do preparo (50 MP)."""
    foto = tmp_path / "camera.jpg"
    Image.new("L", (6000, 4000), 128).save(foto, "JPEG", quality=50)
    with pytest.raises(media.MediaError, match="grande demais"):
        media.check_image(foto)  # o padrão continua para o caminho do endpoint (jobs.py)
    assert media.check_image(foto, max_pixels=preparo.MAX_PIXELS) == (6000, 4000)
