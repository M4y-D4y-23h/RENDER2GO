"""Worker da fila contra a API de verdade (uvicorn local), com armazenamento HTTP falso,
compilador falso e um "ComfyUI" falso que gera o vídeo a partir da foto preparada."""

import shutil
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import uvicorn
from PIL import Image, ImageDraw
from render2go_worker import cli_submit, compiler, fila, legenda, media
from test_api import ADM, PAINEL, TOTP

from render2go_servico import contas, filtro_fotos, suporte
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


class Neutro:
    """Classificador falso do filtro das fotos: tudo neutro (as fotos dos testes são formas coloridas)."""

    def probabilidades(self, quadros):
        return [(0.99, 0.01, 0.0, 0.0) for _ in quadros]


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

    def gravar(self, chave, dados, tipo="application/octet-stream"):  # o logo regravado (montagem, ADR 0015)
        self.objetos[chave] = bytes(dados)

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
        # o filtro das fotos dentro da API, com o portão da fila ligado (Banco.exigir_filtro): a foto só vai para o
        # pod depois de liberada, como em produção
        filtro=filtro_fotos.FiltroFotos(Neutro()),
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


def _lote_aprovado(amb, fotos: dict[str, bytes], prompt="a câmera se aproxima devagar do produto"):
    import requests

    adm = entrar_painel(amb.url)
    cid, token = amb.banco.criar_cliente("Padaria", 500, "pago")
    saldo = {"valor": "100", "descricao": "recarga do teste"}  # plano pago: pré-pago
    assert adm.post(f"{amb.url}{PAINEL}/api/clientes/{cid}/saldo", json=saldo).ok
    hc = {"Authorization": f"Bearer {token}"}
    corpo = {
        "prompt": prompt,
        "duracao": 4,
        "fotos": [{"nome": n, "tipo": "image/png", "bytes": len(b)} for n, b in fotos.items()],
    }
    r = requests.post(f"{amb.url}/api/lotes", json=corpo, headers=hc).json()
    for e in r["envios"]:  # o "navegador" envia direto ao armazenamento
        requests.put(e["url"], data=fotos[e["original"]], timeout=10)
    assert requests.post(f"{amb.url}/api/lotes/{r['lote']}/enviado", headers=hc).status_code == 200
    amb.cfg.analises.esperar()  # o filtro das fotos liberou as duas (em segundo plano, na API)
    assert all(f["filtro"] == "ok" for f in amb.banco.fotos(r["lote"]))
    assert adm.post(f"{amb.url}{PAINEL}/api/lotes/{r['lote']}/aprovar").status_code == 200
    return r["lote"], hc


def _compilar(text, **kw):
    """Compilador falso com o mesmo resultado do de verdade (render2go_worker.traducao lê o modelo e o texto)."""
    prompt = "The camera slowly dollies in toward the product."
    return compiler.CompileResult(
        prompt=prompt,
        profile="i2v",
        version=kw.get("version") or 5,
        model=kw.get("model") or "falso",
        seconds=0.0,
        word_count=8,
        word_range=(15, 60),
        compiled=prompt,
    )


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


class Classificador:
    """Filtro de imagem falso (regra do projeto: nada explícito em teste): a mesma nota para todo quadro,
    ou um erro (classificador que não carregou)."""

    def __init__(self, nota=0.01, erro=None):
        self.nota, self.erro, self.quadros = nota, erro, 0

    def probabilidades(self, quadros):
        if self.erro:
            raise self.erro
        self.quadros += len(quadros)
        return [(1 - self.nota, 0.0, self.nota, 0.0)] * len(quadros)


def _worker(amb, submit, classificador=None):
    api = fila.ApiFila(amb.url, WORKER, "pod-teste")
    cfg = fila.Config(comfy=["http://127.0.0.1:1"], ocioso_s=1.5)
    return fila.WorkerFila(
        api, cfg, submit=submit, compile_fn=_compilar, classificador=classificador or Classificador()
    )


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
    assert all(j["nsfw"] == pytest.approx(0.01) and not j["barrado_em"] for j in jobs)  # filtro de imagem
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
        classificador=Classificador(),
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


@com_ffmpeg
def test_a_video_blocked_by_the_frame_filter_never_leaves_the_pod(ambiente, monkeypatch):
    """ADR 0005: nota alta em algum quadro = nem o vídeo nem a folha vão para o cliente; os dois vão para a
    quarentena, que só o dono vê pelo painel (12/10/2026)."""
    import requests

    monkeypatch.setattr(fila, "ESPERA_S", 0.2)
    lid, hc = _lote_aprovado(ambiente, {"bolo.png": _png((40, 90, 160), "circulo")})
    filtro = Classificador(nota=0.9)
    w = _worker(ambiente, _submit_de(), filtro)
    assert w.rodar() == 0 and filtro.quadros >= 3  # 1 a cada 4 quadros, com o 1º e o último
    lote = requests.get(f"{ambiente.url}/api/lotes/{lid}", headers=hc).json()
    assert lote["status"] == "concluido_com_falhas" and lote["prontos"] == 0
    (job,) = [j for j in ambiente.banco.jobs(lid) if j["tipo"] == "render"]
    assert job["barrado_em"] and job["tentativas"] == 1 and job["nsfw"] == pytest.approx(0.9)  # não refaz
    assert not any(k.startswith("saida/") for k in ambiente.objetos)  # nada para o cliente
    quarentena = sorted(k.rsplit("/", 1)[1] for k in ambiente.objetos if k.startswith("quarentena/"))
    assert quarentena == ["bolo-v1.jpg", "bolo-v1.mp4"] and job["quarentena_em"]


@com_ffmpeg
def test_without_the_frame_filter_nothing_is_delivered_and_the_worker_leaves(ambiente, monkeypatch, tmp_path):
    import requests

    monkeypatch.setattr(fila, "ESPERA_S", 0.2)
    lid, hc = _lote_aprovado(ambiente, {"bolo.png": _png((40, 90, 160), "circulo")})
    w = _worker(ambiente, _submit_de(), Classificador(erro=RuntimeError("timm ausente")))
    w.cfg.motivo_em = tmp_path / "fila.motivo"
    assert w.rodar() == 0 and w.cfg.motivo_em.read_text() == "filtro\n"  # o orquestrador encerra o pod
    (job,) = [j for j in ambiente.banco.jobs(lid) if j["tipo"] == "render"]
    assert job["status"] == "pendente" and "filtro de imagem" in job["erro"]  # outro pod tenta
    assert not any(k.startswith("saida/") for k in ambiente.objetos)
    assert requests.get(f"{ambiente.url}/api/lotes/{lid}", headers=hc).json()["prontos"] == 0


def test_the_image_filter_is_preloaded_when_the_models_are_ready(monkeypatch, tmp_path):
    """Na 1ª chamada, o filtro levava 11–17 s (PyTorch e modelo): agora carrega no PRONTO."""
    monkeypatch.setenv("RENDER2GO_SAFETY", "c1-revisao-humana")
    estado = tmp_path / "estado"
    estado.write_text("AMBIENTE\n")

    class Previo(Classificador):
        def __init__(self):
            super().__init__()
            self.carregado = threading.Event()

        def carregar(self):
            self.carregado.set()

    class Api:
        pass

    c = Previo()
    w = fila.WorkerFila(Api(), fila.Config(comfy=["x"], modelos=estado), classificador=c)
    assert w.estado_modelos() == "esperando" and not c.carregado.is_set()
    estado.write_text("PRONTO\n")
    assert w.estado_modelos() == "ok" and c.carregado.wait(5)


def _em_andamento(amb, lid, n, prazo=10):
    """Espera `n` vídeos do pedido num pod (o adiantado inclusive)."""
    fim = time.monotonic() + prazo
    while time.monotonic() < fim:
        jobs = [j for j in amb.banco.jobs(lid) if j["tipo"] == "render" and j["status"] == "em_andamento"]
        if len(jobs) >= n:
            return True
        time.sleep(0.02)
    return False


@com_ffmpeg
@pytest.mark.parametrize("adiantar", [True, False])
def test_the_gpu_does_not_wait_for_the_upload_or_the_next_photo(ambiente, monkeypatch, tmp_path, adiantar):
    """12/10/2026: enquanto a GPU gera um vídeo, o próximo já está pego e preparado e o anterior é conferido e
    enviado. Aqui, o envio do 1º vídeo só termina depois que o 2º começa na GPU; um de cada vez, nunca."""
    import requests

    monkeypatch.setattr(fila, "ESPERA_S", 0.2)
    fotos = {f"f{i}.png": _png((40 + 60 * i, 90, 160), "circulo") for i in range(3)}
    lid, hc = _lote_aprovado(ambiente, fotos)
    gerar, na_gpu, segundo = _submit_de(), [], threading.Event()

    def submit(a):
        na_gpu.append(a.image)
        if len(na_gpu) == 2:
            segundo.set()
        return gerar(a)

    enviar, esperou = media.upload, []

    def upload(url, path, tipo):
        if tipo == "video/mp4" and not esperou:  # o 1º vídeo: o 2º já está na GPU?
            esperou.append(segundo.wait(10 if adiantar else 1))
        return enviar(url, path, tipo)

    monkeypatch.setattr(media, "upload", upload)
    pastas = tmp_path / "tmp"
    pastas.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(pastas))
    w = _worker(ambiente, submit)
    w.cfg.adiantar = adiantar
    assert w.rodar() == 0
    assert esperou == [adiantar] and not list(pastas.iterdir())
    lote = requests.get(f"{ambiente.url}/api/lotes/{lid}", headers=hc).json()
    assert lote["status"] == "concluido" and lote["prontos"] == 3
    assert len(w.medidas) == 3 and all({"preparo", "espera_gpu", "gpu", "envio"} <= set(m) for m in w.medidas)
    jobs = [j for j in ambiente.banco.jobs(lid) if j["tipo"] == "render"]
    assert all(j["tentativas"] == 1 and j["segundos"] is not None for j in jobs)


@com_ffmpeg
def test_a_video_picked_ahead_is_not_rendered_when_the_client_stops_the_order(ambiente, monkeypatch):
    import requests

    monkeypatch.setattr(fila, "ESPERA_S", 0.2)
    lid, hc = _lote_aprovado(ambiente, {f"f{i}.png": _png((40 + 90 * i, 90, 160), "circulo") for i in range(2)})
    gerar, na_gpu = _submit_de(), []

    def submit(a):  # durante o 1º vídeo, o 2º já foi pego; o cliente para o pedido
        na_gpu.append(a.image)
        if len(na_gpu) == 1:
            assert _em_andamento(ambiente, lid, 2)
            assert requests.post(f"{ambiente.url}/api/lotes/{lid}/interromper", headers=hc).json()["cancelados"] == 0
        return gerar(a)

    assert _worker(ambiente, submit).rodar() == 0
    assert len(na_gpu) == 1  # o adiantado não foi para a GPU
    lote = requests.get(f"{ambiente.url}/api/lotes/{lid}", headers=hc).json()
    assert lote["status"] == "concluido_com_falhas" and lote["prontos"] == 1
    assert lote["nao_sairam"][0]["motivo"] == "interrompido_cliente" and lote["nao_sairam"][0]["videos"] == 1
    assert lote["cobranca"]["cobrado"] == lote["preco_video"]  # só o entregue


@com_ffmpeg
def test_when_the_gpu_fails_the_video_picked_ahead_goes_back_without_an_attempt(ambiente, monkeypatch, tmp_path):
    monkeypatch.setattr(fila, "ESPERA_S", 0.2)
    lid, _ = _lote_aprovado(ambiente, {f"f{i}.png": _png((40 + 90 * i, 90, 160), "circulo") for i in range(2)})

    def submit(a):
        assert _em_andamento(ambiente, lid, 2)
        return cli_submit.EXIT_UNREACHABLE, {"error": "ComfyUI fora do ar"}

    pastas = tmp_path / "tmp"
    pastas.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(pastas))  # as pastas dos vídeos, para conferir a limpeza
    w = _worker(ambiente, submit)
    w.cfg.motivo_em = tmp_path / "fila.motivo"
    assert w.rodar() == 0 and w.cfg.motivo_em.read_text() == "gpu\n"
    jobs = sorted((j for j in ambiente.banco.jobs(lid) if j["tipo"] == "render"), key=lambda j: j["arquivo"])
    assert [(j["status"], j["tentativas"]) for j in jobs] == [("pendente", 1), ("pendente", 0)]
    assert not list(pastas.iterdir())  # nem a foto do adiantado fica no disco


@com_ffmpeg
def test_near_the_pod_time_limit_the_next_video_is_not_picked_ahead(ambiente, monkeypatch):
    monkeypatch.setattr(fila, "ESPERA_S", 0.2)
    lid, _ = _lote_aprovado(ambiente, {f"f{i}.png": _png((40 + 90 * i, 90, 160), "circulo") for i in range(2)})
    gerar, no_pod = _submit_de(), []

    def submit(a):  # durante o 1º vídeo, o 2º não é pego (esperando 1 s para dar tempo a ele)
        no_pod.append(_em_andamento(ambiente, lid, 2, prazo=1))
        return gerar(a)

    w = _worker(ambiente, submit)
    w.cfg.ate = time.time() + fila.GPU_PADRAO_S / 2  # cabe um vídeo, não dois
    assert w.rodar() == 0 and no_pod[0] is False


def test_the_gpu_warms_up_only_when_there_is_no_order_waiting(monkeypatch):
    monkeypatch.setenv("RENDER2GO_SAFETY", "c1-revisao-humana")
    monkeypatch.setattr(fila, "ESPERA_S", 0.01)
    chamadas = []

    def submit(a):
        chamadas.append((a.width, a.height, a.duration, a.prompt_en))
        assert a.image.is_file()
        a.out.write_bytes(b"x")
        return cli_submit.EXIT_OK, {}

    class Vazia:
        def reivindicar(self, tipos=None):
            return None

    cfg = fila.Config(comfy=["x"], ocioso_s=0.05, aquecer=True)
    assert fila.WorkerFila(Vazia(), cfg, submit=submit).rodar() == 0
    assert chamadas == [(256, 256, 3, fila.AQUECER_PROMPT)]  # uma vez só, e nada sai do pod

    def falha(a):
        raise RuntimeError("ComfyUI fora do ar")

    cfg = fila.Config(comfy=["x"], ocioso_s=0.05, aquecer=True)  # aquecer que falha não muda nada
    assert fila.WorkerFila(Vazia(), cfg, submit=falha).rodar() == 0


@com_ffmpeg
def test_with_an_order_waiting_the_gpu_does_not_warm_up(ambiente, monkeypatch):
    monkeypatch.setattr(fila, "ESPERA_S", 0.2)
    _lote_aprovado(ambiente, {"bolo.png": _png((40, 90, 160), "circulo")})
    gerar, prompts = _submit_de(), []

    def submit(a):
        prompts.append(a.prompt_en)
        return gerar(a)

    w = _worker(ambiente, submit)
    w.cfg.aquecer = True
    assert w.rodar() == 0 and len(prompts) == 1 and fila.AQUECER_PROMPT not in prompts


@com_ffmpeg
def test_with_the_caption_per_photo_each_video_gets_the_prompt_of_its_own_photo(ambiente, monkeypatch):
    """Legenda por foto (ADR 0014): cada foto ganha a sua tradução, com a legenda feita no Ollama de CPU enquanto
    a GPU gera o vídeo da foto anterior. Num pedido vago, o movimento padrão nomeia o assunto de cada foto; a
    legenda que falha (qualquer erro) deixa a foto sem ela, e o pedido termina do mesmo jeito."""
    import json

    import requests

    monkeypatch.setattr(fila, "ESPERA_S", 0.2)
    ambiente.cfg.legenda_por_foto = True
    fotos = {f"f{i}.png": _png((40 + 60 * i, 90, 160), "circulo") for i in range(3)}
    lid, hc = _lote_aprovado(ambiente, fotos, prompt="deixa bonito")
    gerar, gerando, prompts, chamadas = _submit_de(), threading.Event(), [], []
    assuntos = iter(["the chocolate cake", None, "the coffee cup"])  # None: a legenda da 2ª foto falha
    legendou = [threading.Event() for _ in range(3)]

    def submit(a):
        gerando.set()
        prompts.append(a.prompt_en)
        if len(prompts) < 3:  # a GPU "gera" até a legenda da próxima foto chegar (ou 10 s, e o teste falha)
            legendou[len(prompts)].wait(10)
        try:
            return gerar(a)
        finally:
            gerando.clear()

    def legendar(foto, **kw):
        chamadas.append((gerando.is_set(), kw.get("threads")))
        legendou[len(chamadas) - 1].set()
        assunto = next(assuntos)
        if assunto is None:
            raise RuntimeError("Ollama de CPU caiu no meio")
        return legenda.Legenda(
            descricao=f"A photo of {assunto} on a table.", categoria="comida", assunto=assunto, pessoas=False,
            texto=False, segundos=0.1,
        )  # fmt: skip

    monkeypatch.setattr(legenda, "legendar", legendar)
    w, traducoes = _worker(ambiente, submit), []
    w.compile_fn = lambda texto, **kw: traducoes.append(kw["seed"]) or _compilar(texto, **kw)
    w.cfg.legenda = True
    assert w.rodar() == 0
    # o pedido e as 3 fotos têm a mesma entrada no tradutor (o v6 não vê a legenda): ele responde uma vez só, com a
    # seed do pedido
    assert traducoes == [fila.semente_do_pedido(lid)]
    lote = requests.get(f"{ambiente.url}/api/lotes/{lid}", headers=hc).json()
    assert lote["status"] == "concluido" and lote["prontos"] == 3
    assert "chocolate" not in json.dumps(lote)  # a legenda nunca vai para o cliente
    # a 1ª foto, sem vídeo na GPU ainda; as outras, durante o vídeo da foto anterior; metade das CPUs, no mínimo 2
    assert [g for g, _ in chamadas] == [False, True, True] and all(t >= 2 for _, t in chamadas)
    fotos_b = {f["nome"]: f for f in ambiente.banco.fotos(lid)}
    assert json.loads(fotos_b["f0"]["legenda"])["assunto"] == "the chocolate cake"
    assert fotos_b["f1"]["legenda"] is None and fotos_b["f2"]["categoria"] == "comida"
    assert prompts[0].startswith("The chocolate cake ") and prompts[2].startswith("The coffee cup ")
    assert not prompts[1].startswith(("The chocolate", "The coffee"))  # sem legenda: o movimento padrão do estilo


def test_the_translator_answers_the_same_input_once_and_the_order_has_its_own_seed(monkeypatch):
    """O cache do worker (ADR 0014): a mesma entrada não volta ao tradutor (que, a cada chamada, carregaria na GPU
    entre dois vídeos); outra seed (a nova tentativa da pessoa inventada) pergunta de novo; recusa não fica guardada."""
    monkeypatch.setenv("RENDER2GO_SAFETY", "c1-revisao-humana")
    chamadas = []

    def compilar(texto, **kw):
        chamadas.append(kw["seed"])
        if texto == "recusa":
            raise compiler.CompilerRefused("REFUSED")
        return _compilar(texto, **kw)

    w = fila.WorkerFila(SimpleNamespace(), fila.Config(comfy=["x"]), compile_fn=compilar)
    kw = {"profile": "i2v", "version": 6, "seed": 7, "suffix": "", "camera_preset": None, "model": "m"}
    primeira = w._compilar_em_cache("o produto gira", **kw)
    assert w._compilar_em_cache("o produto gira", **kw) is primeira and chamadas == [7]
    w._compilar_em_cache("o produto gira", **{**kw, "seed": 107})
    w._compilar_em_cache("o produto gira", **{**kw, "model": "outro"})
    assert chamadas == [7, 107, 7]
    for _ in range(2):
        with pytest.raises(compiler.CompilerRefused):
            w._compilar_em_cache("recusa", **kw)
    assert chamadas == [7, 107, 7, 7, 7]
    for i in range(fila.TRADUCOES_EM_CACHE + 5):
        w._compilar_em_cache(f"texto {i}", **kw)
    assert len(w._traducoes) == fila.TRADUCOES_EM_CACHE
    assert fila.semente_do_pedido("lote_a") == fila.semente_do_pedido("lote_a") != fila.semente_do_pedido("lote_b")
