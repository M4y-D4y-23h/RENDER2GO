"""Aviso de pessoas (gpu2go_servico.pessoas) com fotos SFW de domínio público (fixtures/sfw/FONTES.md)."""

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gpu2go_servico import SERVICO_DIR, pessoas

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
