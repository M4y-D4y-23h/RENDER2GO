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
from PIL import Image, ImageDraw
from render2go_worker import cli_submit, fila, media
from test_api import ADM, PAINEL, TOTP

from render2go_servico import contas, suporte
from render2go_servico.api import Config, criar_app
from render2go_servico.db import Banco

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
    enviados = []  # e-mails (códigos de entrada) que a API mandaria
    cfg = Config(
        banco=banco,
        armazenamento=armazem,
        admin_tokens=[ADMIN],
        worker_tokens=[WORKER],
        segredo_aprovacao="s" * 40,
        admin_caminho=PAINEL,
        admin_totp=TOTP,
        turnstile_desligado=True,
        cookie_seguro=False,  # http local (em produção, só HTTPS)
        retrato_s=0,
        email=suporte.ConfigEmail(host="smtp.teste", para="", de="Render2Go <nao-responda@render2go.com>"),
        enviar_email=lambda cfg_, msg: enviados.append(msg),
        baixar_gravatar=lambda email: None,  # nenhum teste fala com o Gravatar de verdade (o teste troca, se precisar)
    )
    porta = _porta()
    srv = uvicorn.Server(uvicorn.Config(criar_app(cfg), host="127.0.0.1", port=porta, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    monkeypatch.setattr(media, "check_url", lambda url: None)  # local: http em 127.0.0.1
    monkeypatch.setenv("RENDER2GO_SAFETY", "c1-revisao-humana")
    yield SimpleNamespace(
        url=f"http://127.0.0.1:{porta}",
        banco=banco,
        objetos=Deposito.objetos,
        armazem=armazem,
        cfg=cfg,
        enviados=enviados,
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


def entrar_painel(url: str):
    """Sessão do painel (token + código do autenticador), como o navegador do dono."""
    import requests

    s = requests.Session()
    s.headers.update(ADM)
    agora = int(time.time() // 30)
    for contador in (agora - 1, agora, agora + 1):  # o mesmo código não vale duas vezes
        r = s.post(f"{url}{PAINEL}/api/entrar", json={"token": ADMIN, "totp": contas.totp(TOTP, contador)})
        if r.ok:
            return s
    raise AssertionError(r.text)


def _lote_aprovado(amb, fotos: dict[str, bytes]):
    import requests

    adm = entrar_painel(amb.url)
    cid, token = amb.banco.criar_cliente("Padaria", 500, "pago")
    saldo = {"valor": "100", "descricao": "recarga do teste"}  # plano pago: pré-pago
    assert adm.post(f"{amb.url}{PAINEL}/api/clientes/{cid}/saldo", json=saldo).ok
    hc = {"Authorization": f"Bearer {token}"}
    corpo = {
        "prompt": "a câmera se aproxima devagar do produto",
        "duracao": 4,
        "fotos": [{"nome": n, "tipo": "image/png", "bytes": len(b)} for n, b in fotos.items()],
    }
    r = requests.post(f"{amb.url}/api/lotes", json=corpo, headers=hc).json()
    for e in r["envios"]:  # o "navegador" envia direto ao armazenamento
        requests.put(e["url"], data=fotos[e["original"]], timeout=10)
    assert requests.post(f"{amb.url}/api/lotes/{r['lote']}/enviado", headers=hc).status_code == 200
    assert adm.post(f"{amb.url}{PAINEL}/api/lotes/{r['lote']}/aprovar").status_code == 200
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
    assert all(j["deriva_cor"] is not None and j["salto_cor"] is not None for j in jobs)
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


@com_ffmpeg
def test_a_video_that_swaps_the_product_midway_is_redone_with_another_seed(ambiente, monkeypatch):
    import requests

    monkeypatch.setattr(fila, "ESPERA_S", 0.2)
    lid, hc = _lote_aprovado(ambiente, {"bolo.png": _png((40, 90, 160), "circulo")})
    certo = _submit_de()
    seeds = []

    def troca_na_primeira(a):  # 1ª tentativa: começa na foto e corta para outro produto no meio
        seeds.append(a.seed)
        if len(seeds) > 1:
            return certo(a)
        outra = a.out.with_name("outra.png")
        outra.write_bytes(_png((235, 235, 235), "barras"))
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y",
                "-loop", "1", "-t", "1", "-r", "16", "-i", str(a.image),
                "-loop", "1", "-t", "1", "-r", "16", "-i", str(outra),
                "-filter_complex", f"[0]scale={a.width}:{a.height}[a];[1]scale={a.width}:{a.height}[b];"
                "[a][b]concat=n=2:v=1,format=yuv420p",
                str(a.out),
            ],
            check=True,
        )  # fmt: skip
        return cli_submit.EXIT_OK, {}

    assert _worker(ambiente, troca_na_primeira).rodar() == 0
    lote = requests.get(f"{ambiente.url}/api/lotes/{lid}", headers=hc).json()
    assert lote["status"] == "concluido" and lote["prontos"] == 1
    assert len(seeds) == 2 and seeds[0] != seeds[1]  # refeito com outra seed
    (job,) = [j for j in ambiente.banco.jobs(lid) if j["tipo"] == "render"]
    assert job["tentativas"] == 2 and job["seed"] == seeds[1] and job["salto_cor"] < 12


@com_ffmpeg
def test_worker_translates_while_models_download_and_renders_after_ready(ambiente, monkeypatch, tmp_path):
    import requests

    monkeypatch.setattr(fila, "ESPERA_S", 0.1)
    monkeypatch.setattr(fila, "ESPERA_MODELOS_S", 0.05)
    estado = tmp_path / "estado"
    estado.write_text("AMBIENTE\n")
    lid, hc = _lote_aprovado(ambiente, {"bolo.png": _png((40, 90, 160), "circulo")})
    api = fila.ApiFila(ambiente.url, WORKER, "pod-teste")
    w = fila.WorkerFila(
        api,
        fila.Config(comfy=["http://127.0.0.1:1"], ocioso_s=1.0, modelos=estado),
        submit=_submit_de(),
        compile_fn=_compilar,
    )
    t = threading.Thread(target=w.rodar, daemon=True)
    t.start()
    deadline = time.monotonic() + 20
    while ambiente.banco.lote(lid)["prompt_en"] is None and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(1.5)  # mais que o tempo de pod quente: esperando os modelos, o worker não sai
    assert ambiente.banco.lote(lid)["prompt_en"]  # traduzido antes dos modelos
    assert t.is_alive()
    assert all(j["status"] == "pendente" for j in ambiente.banco.jobs(lid) if j["tipo"] == "render")
    estado.write_text("PRONTO\n")
    t.join(30)
    assert not t.is_alive()
    lote = requests.get(f"{ambiente.url}/api/lotes/{lid}", headers=hc).json()
    assert lote["status"] == "concluido" and lote["prontos"] == 1


def test_worker_leaves_when_the_model_install_fails_or_times_out(monkeypatch, tmp_path):
    monkeypatch.setenv("RENDER2GO_SAFETY", "c1-revisao-humana")
    estado = tmp_path / "estado"
    estado.write_text("FALHOU modelos (ver modelos.log)\n")

    class Api:
        def reivindicar(self, tipos=None):
            raise AssertionError("não pede job com a instalação falha")

    assert fila.WorkerFila(Api(), fila.Config(comfy=["x"], modelos=estado)).rodar() == 0
    estado.write_text("AMBIENTE\n")
    w = fila.WorkerFila(Api(), fila.Config(comfy=["x"], modelos=estado, modelos_ate=time.time() - 1))
    assert w.estado_modelos() == "falhou"
    assert fila.WorkerFila(Api(), fila.Config(comfy=["x"])).estado_modelos() == "ok"  # sem o arquivo


def test_worker_refuses_to_start_without_filters(monkeypatch):
    monkeypatch.delenv("RENDER2GO_SAFETY", raising=False)
    with pytest.raises(Exception, match="filtros"):
        fila.WorkerFila(fila.ApiFila("http://127.0.0.1:1", WORKER, "p"), fila.Config(comfy=["x"]))
    assert fila.carimbo_ok("s" * 40, {"lote": "l", "aprovado_em": 1.0, "aprovacao": "falso"}) is False


def test_worker_refuses_a_model_it_does_not_have(monkeypatch):
    monkeypatch.setenv("RENDER2GO_SAFETY", "c1-revisao-humana")
    chamadas = []

    class Api:
        def concluir(self, job_id, status, **kw):
            chamadas.append((job_id, status, kw))

    w = fila.WorkerFila(Api(), fila.Config(comfy=["x"]))
    monkeypatch.setattr(fila, "carimbo_ok", lambda segredo, job: True)
    assert w.processar({"job_id": "j1", "tipo": "render", "pedido": {"modelo": "outro-modelo"}}, "x") is True
    assert chamadas == [("j1", "erro", {"erro": "modelo outro-modelo não disponível neste gerador"})]


def test_worker_writes_why_it_left(monkeypatch, tmp_path):
    """O orquestrador mostra no painel por que o pod saiu: sem pedidos não é limite de tempo."""
    monkeypatch.setenv("RENDER2GO_SAFETY", "c1-revisao-humana")
    monkeypatch.setattr(fila, "ESPERA_S", 0.01)

    class Api:
        def reivindicar(self, tipos=None):
            return None

    motivo = tmp_path / "fila.motivo"
    cfg = fila.Config(comfy=["x"], ocioso_s=0.05, motivo_em=motivo)
    assert fila.WorkerFila(Api(), cfg).rodar() == 0 and motivo.read_text() == "ocioso\n"
    # o desligamento automático está perto (2 h ou o que o saldo do RunPod paga): para de pegar jobs
    cfg = fila.Config(comfy=["x", "y"], ate=time.time() - 1, motivo_em=motivo)
    assert fila.WorkerFila(Api(), cfg).rodar() == 0 and motivo.read_text() == "limite\n"
    # na volta da hibernação, o SHA-256 dos modelos termina depois do PRONTO e pode dar errado
    estado = tmp_path / "estado"
    estado.write_text("PRONTO\n")
    w = fila.WorkerFila(Api(), fila.Config(comfy=["x"], modelos=estado, ocioso_s=60, motivo_em=motivo))
    assert w.estado_modelos() == "ok"
    estado.write_text("FALHOU disco de volume com arquivo corrompido: /opt/comfyui/models/x\n")
    assert w.rodar() == 0 and motivo.read_text() == "modelos\n"
