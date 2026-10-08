"""Worker da fila contra a API de verdade (uvicorn local), com armazenamento HTTP falso,
compilador falso e um "ComfyUI" falso que gera o vídeo a partir da foto preparada."""

import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import uvicorn
from gpu2go_worker import cli_submit, fila, media
from PIL import Image, ImageDraw

from gpu2go_servico.api import Config, criar_app
from gpu2go_servico.db import Banco

ADMIN, WORKER = "a" * 40, "w" * 40
com_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")


class Deposito(BaseHTTPRequestHandler):
    """Armazenamento falso: PUT guarda, GET devolve (a query da "assinatura" é ignorada)."""

    objetos: dict[str, bytes] = {}

    def log_message(self, *a):
        pass

    def _chave(self):
        return urlsplit(self.path).path.lstrip("/")

    def end_headers(self):  # CORS, como a regra do bucket R2 para o navegador do cliente
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_PUT(self):
        n = int(self.headers.get("Content-Length", 0))
        self.objetos[self._chave()] = self.rfile.read(n)
        self.send_response(200)
        self.send_header("ETag", '"x"')
        self.end_headers()

    def do_GET(self):
        corpo = self.objetos.get(self._chave())
        if corpo is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


class Armazem:
    def __init__(self, base, objetos):
        self.base, self.objetos = base, objetos

    def url(self, metodo, chave, expira_s=900, content_type=None, **kw):
        return f"{self.base}/{chave}?metodo={metodo}"

    def tamanho(self, chave):
        b = self.objetos.get(chave)
        return None if b is None else len(b)

    def ler(self, chave, limite):
        return self.objetos[chave]

    def apagar(self, chave):
        self.objetos.pop(chave, None)


def _porta():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    Deposito.objetos = {}
    dep = ThreadingHTTPServer(("127.0.0.1", 0), Deposito)
    threading.Thread(target=dep.serve_forever, daemon=True).start()
    armazem = Armazem(f"http://127.0.0.1:{dep.server_address[1]}", Deposito.objetos)
    banco = Banco(f"sqlite:///{tmp_path / 'b.db'}")
    banco.criar_esquema()
    cfg = Config(
        banco=banco, armazenamento=armazem, admin_tokens=[ADMIN], worker_tokens=[WORKER], segredo_aprovacao="s" * 40
    )
    porta = _porta()
    srv = uvicorn.Server(uvicorn.Config(criar_app(cfg), host="127.0.0.1", port=porta, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    monkeypatch.setattr(media, "check_url", lambda url: None)  # local: http em 127.0.0.1
    monkeypatch.setenv("GPU2GO_SAFETY", "c1-revisao-humana")
    yield SimpleNamespace(
        url=f"http://127.0.0.1:{porta}", banco=banco, objetos=Deposito.objetos, armazem=armazem, cfg=cfg
    )
    srv.should_exit = True
    dep.shutdown()


def _png(cor, forma):
    im = Image.new("RGB", (800, 600), cor)
    d = ImageDraw.Draw(im)
    if forma == "circulo":
        d.ellipse((250, 150, 550, 450), fill=(250, 250, 250))
    else:
        d.rectangle((80, 80, 360, 520), fill=(10, 10, 10))
        d.rectangle((450, 260, 740, 330), fill=(240, 200, 40))
    b = BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def _lote_aprovado(amb, fotos: dict[str, bytes]):
    import requests

    h = {"Authorization": f"Bearer {ADMIN}"}
    tok = requests.post(f"{amb.url}/api/admin/clientes", json={"nome": "Padaria"}, headers=h).json()["token"]
    hc = {"Authorization": f"Bearer {tok}"}
    corpo = {
        "prompt": "a câmera se aproxima devagar do produto",
        "duracao": 3,
        "fotos": [{"nome": n, "tipo": "image/png", "bytes": len(b)} for n, b in fotos.items()],
    }
    r = requests.post(f"{amb.url}/api/lotes", json=corpo, headers=hc).json()
    for e in r["envios"]:  # o "navegador" envia direto ao armazenamento
        requests.put(e["url"], data=fotos[e["original"]], timeout=10)
    assert requests.post(f"{amb.url}/api/lotes/{r['lote']}/enviado", headers=hc).status_code == 200
    assert requests.post(f"{amb.url}/api/admin/lotes/{r['lote']}/aprovar", headers=h).status_code == 200
    return r["lote"], hc


def _compilar(text, **kw):
    return SimpleNamespace(prompt="The camera slowly dollies in toward the product.", ref="i2v@5", word_count=8)


def _submit_de(trocar_por: bytes | None = None):
    """'ComfyUI' falso: o vídeo começa na imagem enviada (ou numa outra, para simular troca)."""

    def submit(a):
        origem = a.image
        if trocar_por is not None:
            origem = a.out.with_name("outra.png")
            origem.write_bytes(trocar_por)
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-loop",
                "1",
                "-i",
                str(origem),
                "-vf",
                f"scale={a.width}:{a.height},zoompan=z='1+0.004*on':d=17:s={a.width}x{a.height}:fps=16",
                "-frames:v",
                "17",
                "-pix_fmt",
                "yuv420p",
                str(a.out),
            ],
            check=True,
        )
        return cli_submit.EXIT_OK, {}

    return submit


def _worker(amb, submit):
    api = fila.ApiFila(amb.url, WORKER, "pod-teste")
    cfg = fila.Config(comfy=["http://127.0.0.1:1"], ocioso_s=1.5)
    return fila.WorkerFila(api, cfg, submit=submit, compile_fn=_compilar)


@com_ffmpeg
def test_queue_worker_renders_checks_and_delivers(ambiente, monkeypatch):
    import requests

    monkeypatch.setattr(fila, "ESPERA_S", 0.2)
    fotos = {"bolo.png": _png((40, 90, 160), "circulo"), "loja.png": _png((200, 120, 60), "barras")}
    lid, hc = _lote_aprovado(ambiente, fotos)
    assert _worker(ambiente, _submit_de()).rodar() == 0
    lote = requests.get(f"{ambiente.url}/api/lotes/{lid}", headers=hc).json()
    assert lote["status"] == "concluido" and lote["prontos"] == 2
    jobs = [j for j in ambiente.banco.jobs(lid) if j["tipo"] == "render"]
    assert all(j["similaridade"] > 0.9 and j["bytes_video"] > 0 for j in jobs)
    # cada vídeo está no endereço do próprio cliente e lote, com a folha de conferência
    chaves = sorted(ambiente.objetos)
    assert sum(k.startswith("saida/") and k.endswith(".mp4") and f"/{lid}/" in k for k in chaves) == 2
    assert sum("/conferencia/" in k for k in chaves) == 2
    assert ambiente.banco.lote(lid)["prompt_en"].startswith("The camera")


@com_ffmpeg
def test_a_video_of_the_wrong_photo_never_reaches_the_client(ambiente, monkeypatch):
    import requests

    monkeypatch.setattr(fila, "ESPERA_S", 0.2)
    fotos = {"bolo.png": _png((40, 90, 160), "circulo")}
    lid, hc = _lote_aprovado(ambiente, fotos)
    outra = _png((200, 120, 60), "barras")
    assert _worker(ambiente, _submit_de(trocar_por=outra)).rodar() == 0
    lote = requests.get(f"{ambiente.url}/api/lotes/{lid}", headers=hc).json()
    assert lote["status"] == "concluido_com_falhas" and lote["prontos"] == 0
    (job,) = [j for j in ambiente.banco.jobs(lid) if j["tipo"] == "render"]
    assert "não corresponde" in job["erro"] and job["tentativas"] == 3
    assert not any(k.endswith(".mp4") for k in ambiente.objetos)  # nunca foi enviado
    assert "url" not in lote["videos"][0]


def test_worker_refuses_to_start_without_filters(monkeypatch):
    monkeypatch.delenv("GPU2GO_SAFETY", raising=False)
    with pytest.raises(Exception, match="filtros"):
        fila.WorkerFila(fila.ApiFila("http://127.0.0.1:1", WORKER, "p"), fila.Config(comfy=["x"]))
    assert fila.carimbo_ok("s" * 40, {"lote": "l", "aprovado_em": 1.0, "aprovacao": "falso"}) is False


def test_worker_refuses_a_model_it_does_not_have(monkeypatch):
    monkeypatch.setenv("GPU2GO_SAFETY", "c1-revisao-humana")
    chamadas = []

    class Api:
        def concluir(self, job_id, status, **kw):
            chamadas.append((job_id, status, kw))

    w = fila.WorkerFila(Api(), fila.Config(comfy=["x"]))
    monkeypatch.setattr(fila, "carimbo_ok", lambda segredo, job: True)
    assert w.processar({"job_id": "j1", "tipo": "render", "pedido": {"modelo": "outro-modelo"}}, "x") is True
    assert chamadas == [("j1", "erro", {"erro": "modelo outro-modelo não disponível neste gerador"})]
