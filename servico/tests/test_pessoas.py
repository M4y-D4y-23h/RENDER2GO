"""Aviso de pessoas (render2go_servico.pessoas) com fotos SFW de domínio público (fixtures/sfw/FONTES.md)."""

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from render2go_servico import SERVICO_DIR, pessoas

FIXTURES = SERVICO_DIR.parent / "fixtures" / "sfw"


@pytest.fixture(scope="module")
def analisar():
    try:
        pessoas.baixar(pessoas.DIR)  # já baixados: só confere bytes e SHA-256
        return pessoas.Detector(pessoas.DIR).analisar
    except Exception as e:  # noqa: BLE001 (sem rede para baixar os modelos)
        pytest.skip(f"modelos do detector indisponíveis: {e}")


def test_people_and_faces_are_detected_and_products_are_not(analisar):
    r = analisar((FIXTURES / "pessoas-padaria-1900.jpg").read_bytes())
    assert r["versao"] == pessoas.VERSAO and r["pessoas"] >= 3 and r["rostos"] >= 2
    for produto in ("produto-xicara.jpg", "produto-cafe.jpg"):
        r = analisar((FIXTURES / produto).read_bytes())
        assert (r["pessoas"], r["rostos"]) == (0, 0), produto


def test_unreadable_photo_raises_and_missing_models_report_an_error(analisar, tmp_path):
    with pytest.raises(Exception):  # noqa: B017 (o PIL decide a exceção; a fila da API grava o erro)
        analisar(b"isto nao e uma imagem")
    r = pessoas.analisador(tmp_path / "vazia")(b"x")
    assert "erro" in r and "indisponível" in r["erro"]  # nunca "sem pessoas"


def test_download_refuses_bytes_that_do_not_match(monkeypatch, tmp_path):
    corpo = b"modelo adulterado"

    class Servidor(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Servidor)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/m.onnx"
    try:
        monkeypatch.setattr(pessoas, "MODELOS", {"m.onnx": (url, len(corpo), "0" * 64)})
        with pytest.raises(pessoas.DetectorIndisponivel, match="não conferem"):
            pessoas.baixar(tmp_path)
        assert list(tmp_path.iterdir()) == []  # nada fica para trás
        monkeypatch.setattr(pessoas, "MODELOS", {"m.onnx": (url, len(corpo), hashlib.sha256(corpo).hexdigest())})
        pessoas.baixar(tmp_path)
        assert (tmp_path / "m.onnx").read_bytes() == corpo
    finally:
        srv.shutdown()


# -- arquivos maliciosos (11/10/2026): o analisador é o único lugar do servidor que decodifica uma foto --


def _png_cabecalho(largura: int, altura: int) -> bytes:
    """PNG válido no cabeçalho que declara um tamanho enorme com poucos bytes (bomba de descompressão)."""
    import struct
    import zlib

    def bloco(tipo: bytes, dados: bytes) -> bytes:
        return struct.pack(">I", len(dados)) + tipo + dados + struct.pack(">I", zlib.crc32(tipo + dados))

    ihdr = struct.pack(">IIBBBBB", largura, altura, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + bloco(b"IHDR", ihdr) + bloco(b"IDAT", zlib.compress(b"\0" * 64)) + bloco(b"IEND", b"")


def _imagem(formato: str, tamanho=(64, 48)) -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", tamanho, (200, 120, 40)).save(buf, formato)
    return buf.getvalue()


@pytest.mark.parametrize("formato", ["JPEG", "PNG", "WEBP"])
def test_real_photos_of_the_three_formats_open(formato):
    with pessoas.abrir_foto(_imagem(formato)) as im:
        assert im.size == (64, 48) and im.format == formato


@pytest.mark.parametrize(
    "dados",
    [
        b"<html><script>alert(1)</script></html>",  # página disfarçada de foto
        b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64,  # executável do Windows
        b"PK\x03\x04" + b"\x00" * 64,  # zip
        b"%!PS-Adobe-3.0 EPSF-3.0\n",  # EPS: o Pillow chamaria o Ghostscript para decodificar
        b"\xff\xd8\xff" + b"<?php system($_GET['c']); ?>" * 10,  # começa como JPEG, mas não é
    ],
)
def test_files_that_are_not_photos_are_never_decoded(dados):
    with pytest.raises(pessoas.FotoInvalida):
        pessoas.abrir_foto(dados)


def test_other_image_formats_are_not_even_parsed():
    for formato in ("TIFF", "BMP", "GIF", "ICO", "PPM"):
        with pytest.raises(pessoas.FotoInvalida, match="não é uma foto"):
            pessoas.abrir_foto(_imagem(formato, (32, 32)))


def test_a_decompression_bomb_is_refused_before_decoding():
    bomba = _png_cabecalho(100_000, 100_000)  # 10 bilhões de pixels em ~100 bytes
    assert len(bomba) < 200
    with pytest.raises(pessoas.FotoInvalida):
        pessoas.abrir_foto(bomba)
    with pytest.raises(pessoas.FotoInvalida, match="acima de 50 megapixels"):
        pessoas.abrir_foto(_png_cabecalho(8_000, 8_000))  # 64 MP


def test_an_invalid_file_becomes_an_analysis_error_for_the_reviewer(monkeypatch):
    """O revisor vê o motivo na análise; nada é decodificado e o laço segue para a próxima foto."""
    from render2go_servico import api as api_mod

    salvas = []

    class Banco:
        def fotos(self, lote_id):
            return [{"id": "f1", "chave": "k1", "analise": None, "enviada": True}]

        def salvar_analise(self, foto_id, analise, impressao):
            salvas.append((foto_id, analise, impressao))

        def tentar_analise(self, foto_id):  # a 1ª análise desta foto
            return 1

    class Armazem:
        def ler(self, chave, limite):
            return b"<html>nao sou foto</html>"

    def analisar(dados):
        with pessoas.abrir_foto(dados):
            raise AssertionError("não devia chegar aqui")

    assert api_mod.analisar_lote(Banco(), Armazem(), analisar, "l1") == 1
    ((foto, analise, impressao),) = salvas
    assert foto == "f1" and "não é uma foto" in analise["erro"] and len(impressao) == 64
