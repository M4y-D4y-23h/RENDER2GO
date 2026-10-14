"""Entrega sem geração desperdiçada (14/10/2026): tentativa de cada job, conclusão idempotente, objeto por tentativa,
prazo renovado só com progresso, e o worker que repete o MESMO vídeo aprovado quando só o envio ou a confirmação
falham. Na API: SQLite e Postgres (fixture `banco`). No worker: API e armazenamento falsos; nenhuma rede."""

import json
import time

import pytest
import test_api
from render2go_worker import fila, media
from test_api import ADM, CONTINUO, PAINEL, WORKER, _cliente, _enviar_tudo, _job, _lote, h

from render2go_servico import db as dbmod

api = test_api.api  # a mesma API de teste (SQLite e Postgres)

# -- API: tentativa, publicação e cobrança uma vez só -------------------------------------------------------------


def _render_pronto(api, n=1):
    """Um pedido pago aprovado, com o prompt pronto: o próximo job é o vídeo."""
    tok = _cliente(api)
    r = _lote(api, tok, n=n, duracao=4)
    lid = r.json()["lote"]
    assert _enviar_tudo(api, tok, lid, r.json()["envios"]).status_code == 200
    assert api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM).status_code == 200
    comp = _job(api)
    api.post(
        f"/api/worker/jobs/{comp['job_id']}/prompt",
        headers=h(WORKER),
        json={"worker": "pod-1", "prompt_en": "The camera slowly dollies in.", "compilador": "i2v@5"},
    )
    api.post(f"/api/worker/jobs/{comp['job_id']}/concluir", headers=h(WORKER), json={"worker": "pod-1", "status": "ok"})
    return lid, tok


def _chave(url):
    return url.split("armazem.teste/")[1].split("?")[0]


def _ok(api, job, worker="pod-1", **extra):
    corpo = {"worker": worker, "status": "ok", "bytes_video": 7000, "similaridade": 0.99, **CONTINUO, **extra}
    return api.post(f"/api/worker/jobs/{job['job_id']}/concluir", headers=h(WORKER), json=corpo)


def _cobrancas(api, lid):
    with api.banco.tx() as c:
        return c.all("SELECT referencia, valor FROM movimentos WHERE lote_id = ? AND tipo = 'video'", (lid,))


def test_an_old_attempt_cannot_conclude_or_overwrite_after_its_lease_expired(api):
    lid, _ = _render_pronto(api)
    a = _job(api)  # pod-1
    with api.banco.tx() as c:  # o prazo de pod-1 venceu (ex.: o vídeo travou)
        c.run("UPDATE jobs SET lease_ate = 0 WHERE id = ?", (a["job_id"],))
    b = api.post("/api/worker/reivindicar", json={"worker": "pod-2"}, headers=h(WORKER)).json()["job"]
    assert b["job_id"] == a["job_id"] and b["tentativa"] != a["tentativa"]
    assert _chave(b["video_url"]) != _chave(a["video_url"])  # cada tentativa envia para o seu objeto
    # pod-1 termina depois: envia para o objeto dele, mas não conclui (nem com o nome de pod-2)
    api.armazem.objetos[_chave(a["video_url"])] = 7000
    assert _ok(api, a, tentativa=a["tentativa"]).status_code == 409
    assert _ok(api, a, worker="pod-2", tentativa=a["tentativa"]).status_code == 409
    # pod-2 entrega: o vídeo dele é publicado no endereço do cliente e cobrado uma vez
    api.armazem.objetos[_chave(b["video_url"])] = 7100
    assert _ok(api, b, worker="pod-2", tentativa=b["tentativa"], bytes_video=7100).json()["status"] == "ok"
    final = _chave(b["video_url"]).split("/tentativas/")[0] + f"/{b['arquivo']}"
    assert api.armazem.objetos[final] == 7100
    # o envio atrasado de pod-1 cai no objeto da tentativa dele, que nunca é publicado
    api.armazem.objetos[_chave(a["video_url"])] = 6000
    assert _ok(api, a, tentativa=a["tentativa"]).status_code == 409 and api.armazem.objetos[final] == 7100
    assert len(_cobrancas(api, lid)) == 1


def test_a_lost_response_is_answered_again_without_charging_twice(api):
    lid, _ = _render_pronto(api)
    j = _job(api)
    api.armazem.objetos[_chave(j["video_url"])] = 7000
    assert _ok(api, j, tentativa=j["tentativa"]).json() == {"status": "ok"}
    again = _ok(api, j, tentativa=j["tentativa"])  # a resposta se perdeu: o pod repete
    assert again.status_code == 200 and again.json() == {"status": "ok", "repetida": True}
    assert _cobrancas(api, lid) == [{"referencia": f"video:{j['job_id']}", "valor": -100}]
    assert _ok(api, j).status_code == 409  # sem a tentativa (pod de antes): o job não está mais com ele


def test_a_failed_publication_does_not_redo_the_video(api, monkeypatch):
    lid, _ = _render_pronto(api)
    j = _job(api)
    api.armazem.objetos[_chave(j["video_url"])] = 7000
    copiar = api.armazem.copiar
    api.armazem.copiar = lambda o, d: (_ for _ in ()).throw(OSError("R2 fora do ar"))
    r = _ok(api, j, tentativa=j["tentativa"])
    assert r.status_code == 503  # o pod repete a conclusão; o vídeo não volta para a GPU
    (job,) = [x for x in api.banco.jobs(lid) if x["tipo"] == "render"]
    assert job["status"] == "em_andamento" and job["tentativas"] == 1 and not _cobrancas(api, lid)
    api.armazem.copiar = copiar
    assert _ok(api, j, tentativa=j["tentativa"]).json()["status"] == "ok"
    assert len(_cobrancas(api, lid)) == 1


def test_a_transport_failure_returns_the_job_with_the_same_seed(api):
    lid, _ = _render_pronto(api)
    j = _job(api)
    corpo = {"worker": "pod-1", "status": "erro", "erro": "envio falhou", "repetir": True, "transporte": True}
    r = api.post(
        f"/api/worker/jobs/{j['job_id']}/concluir", headers=h(WORKER), json={**corpo, "tentativa": j["tentativa"]}
    )
    assert r.json()["status"] == "pendente"
    (job,) = [x for x in api.banco.jobs(lid) if x["tipo"] == "render"]
    assert job["seed"] == j["seed"] and job["falhas_envio"] == 1  # o vídeo estava certo: a mesma seed
    k = _job(api)
    corpo = {"worker": "pod-1", "status": "erro", "erro": "deriva de cor", "repetir": True, "tentativa": k["tentativa"]}
    api.post(f"/api/worker/jobs/{k['job_id']}/concluir", headers=h(WORKER), json=corpo)
    (job,) = [x for x in api.banco.jobs(lid) if x["tipo"] == "render"]
    assert job["seed"] != j["seed"]  # falha de conteúdo: outra seed, como antes


def test_renewal_needs_the_attempt_and_progress_and_stops_at_the_absolute_deadline(api):
    _render_pronto(api)
    j = _job(api)
    url = f"/api/worker/jobs/{j['job_id']}/renovar"

    def renovar(etapa, progresso, tentativa=j["tentativa"]):
        corpo = {"worker": "pod-1", "tentativa": tentativa, "etapa": etapa, "progresso": progresso}
        return api.post(url, headers=h(WORKER), json=corpo)

    assert renovar("gpu", 1).status_code == 200
    assert renovar("gpu", 2).status_code == 200
    assert renovar("gpu", 2, tentativa="0" * 16).status_code == 409  # outra tentativa
    with api.banco.tx() as c:  # o mesmo progresso há mais de 10 min: o batimento sozinho não segura o job
        c.run("UPDATE jobs SET progresso_em = ? WHERE id = ?", (time.time() - dbmod.SEM_PROGRESSO_S - 1, j["job_id"]))
    assert renovar("gpu", 2).status_code == 409
    assert renovar("envio", 2).status_code == 200  # etapa nova é progresso
    with api.banco.tx() as c:
        lease = c.one("SELECT lease_ate, prazo_tentativa FROM jobs WHERE id = ?", (j["job_id"],))
        assert lease["lease_ate"] <= lease["prazo_tentativa"]
        c.run("UPDATE jobs SET prazo_tentativa = ? WHERE id = ?", (time.time() - 1, j["job_id"]))
    assert renovar("envio", 3).status_code == 409  # o prazo absoluto venceu


def test_new_upload_urls_only_for_the_current_attempt(api):
    _render_pronto(api)
    j = _job(api)
    url = f"/api/worker/jobs/{j['job_id']}/urls"
    r = api.post(url, headers=h(WORKER), json={"worker": "pod-1", "tentativa": j["tentativa"]})
    assert r.status_code == 200 and _chave(r.json()["video_url"]) == _chave(j["video_url"])
    assert api.post(url, headers=h(WORKER), json={"worker": "pod-1", "tentativa": "f" * 16}).status_code == 409
    assert api.post(url, headers=h(WORKER), json={"worker": "pod-2", "tentativa": j["tentativa"]}).status_code == 409


# -- worker: a entrega repete o mesmo vídeo ------------------------------------------------------------------------


class ApiFalsa:
    """O que a API responderia (sem rede): `concluir` devolve "ok" (ou levanta o que estiver em `falhas`)."""

    def __init__(self, falhas_concluir=(), urls=None, renovar=True):
        self.chamadas: list[tuple] = []
        self.falhas = list(falhas_concluir)
        self._urls = urls or {"video_url": "https://r2.teste/novo-video", "folha_url": "https://r2.teste/nova-folha"}
        self._renovar = renovar

    def concluir(self, job_id, status, **kw):
        self.chamadas.append(("concluir", status, kw))
        if self.falhas:
            raise self.falhas.pop(0)
        return "pendente" if kw.get("repetir") else status

    def urls(self, job_id, tentativa):
        self.chamadas.append(("urls", tentativa))
        if isinstance(self._urls, Exception):
            raise self._urls
        return self._urls

    def renovar(self, job_id, tentativa, etapa, progresso):
        self.chamadas.append(("renovar", etapa, progresso))
        if isinstance(self._renovar, Exception):
            raise self._renovar
        return self._renovar


@pytest.fixture
def worker_falso(monkeypatch, tmp_path):
    monkeypatch.setenv("RENDER2GO_SAFETY", "c1-revisao-humana")
    monkeypatch.setattr(fila.time, "sleep", lambda s: None)
    monkeypatch.setattr(media, "check_url", lambda url: None)

    def fazer(api, **cfg):
        c = fila.Config(comfy=["x"], entregas=tmp_path / "entregas", **cfg)
        return fila.WorkerFila(api, c, classificador=object())

    return fazer


def _entrega(w, tmp_path, tentativa="ab" * 8, video=b"video aprovado" * 100):
    pasta = tmp_path / "job"
    pasta.mkdir()
    (pasta / "video.mp4").write_bytes(video)
    job = {"job_id": "j1", "tentativa": tentativa, "lote": "l1", "arquivo": "f-v1.mp4",
           "video_url": "https://r2.teste/video", "folha_url": "https://r2.teste/folha"}  # fmt: skip
    p = fila.Preparado(job, pasta, None, None, time.monotonic(), {})
    return w._reter(p, {"bytes_video": len(video), "similaridade": 0.99})


def _uploads(monkeypatch, respostas):
    """media.upload falso: cada chamada consome a próxima resposta (None = aceito; exceção = falha)."""
    feitos = []

    def upload(url, path, tipo, **kw):
        feitos.append((url, path.read_bytes() if path.is_file() else None, tipo))
        r = respostas.pop(0) if respostas else None
        if r is not None:
            raise r

    monkeypatch.setattr(media, "upload", upload)
    return feitos


def test_an_intermittent_upload_failure_sends_the_same_video_again(worker_falso, tmp_path, monkeypatch):
    api = ApiFalsa()
    w = worker_falso(api)
    e = _entrega(w, tmp_path)
    assert (e.pasta / fila.MANIFESTO).is_file() and e.pasta.parent == w.cfg.entregas
    feitos = _uploads(monkeypatch, [media.MediaError("500", retryable=True), None])
    assert w._entregar(e) == "ok"
    videos = [f for f in feitos if f[2] == "video/mp4"]
    assert len(videos) == 2 and videos[0][1] == videos[1][1]  # o mesmo vídeo, nada voltou para a GPU
    (conclusao,) = [c for c in api.chamadas if c[0] == "concluir"]
    assert conclusao[1] == "ok" and conclusao[2]["tentativa"] == "ab" * 8 and conclusao[2]["sha256"] == e.sha256
    assert not e.pasta.exists()  # limpeza no fim


def test_an_expired_url_asks_for_new_urls_of_the_same_attempt(worker_falso, tmp_path, monkeypatch):
    api = ApiFalsa()
    w = worker_falso(api)
    e = _entrega(w, tmp_path)
    feitos = _uploads(monkeypatch, [media.MediaError("upload recusado: 403", status=403)])
    assert w._entregar(e) == "ok"
    assert ("urls", "ab" * 8) in api.chamadas
    assert [f[0] for f in feitos if f[2] == "video/mp4"] == ["https://r2.teste/video", "https://r2.teste/novo-video"]


def test_a_lost_confirmation_is_repeated_with_the_same_attempt(worker_falso, tmp_path, monkeypatch):
    api = ApiFalsa(falhas_concluir=[fila.FilaError("API inacessível"), fila.FilaError("HTTP 503")])
    w = worker_falso(api)
    e = _entrega(w, tmp_path)
    feitos = _uploads(monkeypatch, [])
    assert w._entregar(e) == "ok"
    conclusoes = [c for c in api.chamadas if c[0] == "concluir"]
    assert len(conclusoes) == 3 and {c[2]["tentativa"] for c in conclusoes} == {"ab" * 8}
    assert len([f for f in feitos if f[2] == "video/mp4"]) == 1  # enviado uma vez; só a confirmação repetiu


def test_an_exhausted_delivery_returns_the_job_as_a_transport_failure(worker_falso, tmp_path, monkeypatch):
    monkeypatch.setattr(fila, "ENTREGA_MAX_S", 0)
    api = ApiFalsa()
    w = worker_falso(api)
    e = _entrega(w, tmp_path)
    _uploads(monkeypatch, [media.MediaError("500", retryable=True)] * 5)
    assert w._entregar(e) == "pendente"
    (c,) = [c for c in api.chamadas if c[0] == "concluir"]
    assert c[1] == "erro" and c[2]["repetir"] and c[2]["transporte"] and "envio do vídeo falhou" in c[2]["erro"]
    assert not e.pasta.exists()


def test_a_lost_attempt_sends_nothing(worker_falso, tmp_path, monkeypatch):
    api = ApiFalsa(urls=fila.Perdida("job não está com esta tentativa"))
    w = worker_falso(api)
    e = _entrega(w, tmp_path)
    feitos = _uploads(monkeypatch, [media.MediaError("upload recusado: 403", status=403)])
    assert w._entregar(e) == "perdida"
    assert not [c for c in api.chamadas if c[0] == "concluir"] and len(feitos) == 1 and not e.pasta.exists()


def test_a_corrupted_artifact_is_never_sent(worker_falso, tmp_path, monkeypatch):
    api = ApiFalsa()
    w = worker_falso(api)
    e = _entrega(w, tmp_path)
    e.video.write_bytes(b"outro conteudo" * 100)  # o disco do pod mudou o arquivo
    feitos = _uploads(monkeypatch, [])
    assert w._entregar(e) == "pendente" and feitos == []
    (c,) = [c for c in api.chamadas if c[0] == "concluir"]
    assert c[1] == "erro" and c[2]["repetir"] and not c[2].get("transporte")  # refeito (falha de conteúdo)


def test_a_full_disk_still_delivers_without_retention(worker_falso, tmp_path, monkeypatch):
    api = ApiFalsa()
    w = worker_falso(api)
    real = fila.Path.rename

    def cheio(self, alvo):
        if "entregas" in str(alvo):
            raise OSError(28, "No space left on device")
        return real(self, alvo)

    monkeypatch.setattr(fila.Path, "rename", cheio)
    e = _entrega(w, tmp_path)
    assert e.pasta == tmp_path / "job" and not (e.pasta / fila.MANIFESTO).exists()
    _uploads(monkeypatch, [])
    assert w._entregar(e) == "ok" and not e.pasta.exists()


def test_a_restarted_worker_resumes_the_pending_delivery(worker_falso, tmp_path, monkeypatch):
    """O processo caiu com o vídeo aprovado no disco: o próximo worker do mesmo pod pede URLs novas (a mesma
    tentativa) e confirma; manifestos velhos demais saem sem envio."""
    api = ApiFalsa()
    w = worker_falso(api)
    e = _entrega(w, tmp_path)
    velho = w.cfg.entregas / "velho"
    velho.mkdir()
    (velho / "video.mp4").write_bytes(b"x")
    (velho / fila.MANIFESTO).write_text(
        json.dumps(
            {
                "job": {"job_id": "j0", "tentativa": "cd" * 8},
                "sha256": "0",
                "bytes": 1,
                "campos": {},
                "criada_em": time.time() - fila.ENTREGA_VELHA_S - 1,
            }
        )  # fmt: skip
    )
    novo = worker_falso(api)  # outro processo, a mesma pasta
    feitos = _uploads(monkeypatch, [])
    assert novo.retomar_entregas() == 1
    assert ("urls", "ab" * 8) in api.chamadas  # as URLs não ficam no manifesto
    (c,) = [c for c in api.chamadas if c[0] == "concluir"]
    assert c[1] == "ok" and c[2]["sha256"] == e.sha256 and len(feitos) == 1
    assert not velho.exists() and not e.pasta.exists()


def test_the_heartbeat_marks_the_attempt_lost_and_interrupts(worker_falso, tmp_path, monkeypatch):
    api = ApiFalsa(renovar=fila.Perdida("sem progresso"))
    interrompido = []
    b = fila.Batimento(api, {"job_id": "j1", "tentativa": "ab" * 8}, ao_perder=lambda: interrompido.append(1))
    b.marcar("gpu")
    b.renovar()
    b.parar()
    assert b.perdida.is_set() and interrompido == [1] and ("renovar", "gpu", 1) in api.chamadas
    sem = fila.Batimento(api, {"job_id": "j1"})  # API de antes: sem tentativa, nada a renovar
    sem.renovar()
    sem.parar()
    assert len([c for c in api.chamadas if c[0] == "renovar"]) == 1


# -- de ponta a ponta: worker de verdade, API de verdade, SQLite e Postgres -----------------------------------------


@pytest.fixture
def ambiente_db(banco, tmp_path, monkeypatch):
    """O ambiente do test_fila.py (API em uvicorn, armazenamento HTTP falso, filtro das fotos ligado), com o banco
    da fixture `banco` (SQLite e Postgres): a cobrança e a concorrência também no Postgres."""
    import threading
    from http.server import ThreadingHTTPServer

    import test_fila as tf
    import uvicorn

    from render2go_servico import filtro_fotos, suporte
    from render2go_servico.api import Config, criar_app

    tf.Deposito.objetos = {}
    dep = ThreadingHTTPServer(("127.0.0.1", 0), tf.Deposito)
    threading.Thread(target=dep.serve_forever, daemon=True).start()
    armazem = tf.Armazem(f"http://127.0.0.1:{dep.server_address[1]}", tf.Deposito.objetos)
    banco.exigir_filtro = True  # como em produção: só foto liberada vai para o pod
    cfg = Config(
        banco=banco, armazenamento=armazem, admin_tokens=[tf.ADMIN], worker_tokens=[tf.WORKER],
        segredo_aprovacao="s" * 40, admin_caminho=PAINEL, admin_totp=test_api.TOTP, turnstile_desligado=True,
        cookie_seguro=False, retrato_s=0,
        email=suporte.ConfigEmail(host="smtp.teste", para="", de="Render2Go <nao-responda@render2go.com>"),
        enviar_email=lambda cfg_, msg: None, baixar_gravatar=lambda email: None,
        filtro=filtro_fotos.FiltroFotos(tf.Neutro()),
    )  # fmt: skip
    porta = tf._porta()
    srv = uvicorn.Server(uvicorn.Config(criar_app(cfg), host="127.0.0.1", port=porta, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    monkeypatch.setattr(media, "check_url", lambda url: None)
    monkeypatch.setenv("RENDER2GO_SAFETY", "c1-revisao-humana")
    monkeypatch.setattr(fila, "ESPERA_S", 0.2)
    from types import SimpleNamespace

    yield SimpleNamespace(url=f"http://127.0.0.1:{porta}", banco=banco, objetos=tf.Deposito.objetos, cfg=cfg)
    srv.should_exit = True
    dep.shutdown()


def _worker_real(amb, submit, tmp_path):
    import test_fila as tf

    api = fila.ApiFila(amb.url, tf.WORKER, "pod-teste")
    cfg = fila.Config(comfy=["http://127.0.0.1:1"], ocioso_s=1.5, entregas=tmp_path / "entregas")
    return fila.WorkerFila(api, cfg, submit=submit, compile_fn=tf._compilar, classificador=tf.Classificador())


@pytest.mark.skipif(__import__("shutil").which("ffmpeg") is None, reason="ffmpeg ausente")
def test_end_to_end_upload_failures_and_a_lost_answer_render_and_charge_once(ambiente_db, tmp_path, monkeypatch):
    import requests
    import test_fila as tf

    lid, hc = tf._lote_aprovado(ambiente_db, {"bolo.png": tf._png((40, 90, 160), "circulo")})
    gerar, renders = tf._submit_de(), []

    def submit(a):
        renders.append(a.seed)
        return gerar(a)

    enviar, falhas = media.upload, [2]

    def upload_instavel(url, path, tipo, **kw):  # as 2 primeiras idas do vídeo caem (rede)
        if tipo == "video/mp4" and falhas[0]:
            falhas[0] -= 1
            raise media.MediaError("upload falhou: conexão reiniciada", retryable=True)
        return enviar(url, path, tipo, **kw)

    monkeypatch.setattr(media, "upload", upload_instavel)
    dormir = time.sleep
    monkeypatch.setattr(fila.time, "sleep", lambda s: dormir(min(s, 0.05)))
    post, perdeu = fila.ApiFila._post, []

    def resposta_perdida(self, rota, corpo, tentativas=5):  # a 1ª conclusão "ok" chega, a resposta não volta
        r = post(self, rota, corpo, tentativas)
        if rota.endswith("/concluir") and corpo.get("status") == "ok" and corpo.get("sha256") and not perdeu:
            perdeu.append(r.status_code)
            raise fila.FilaError("API inacessível (/concluir): Read timed out")
        return r

    monkeypatch.setattr(fila.ApiFila, "_post", resposta_perdida)
    assert _worker_real(ambiente_db, submit, tmp_path).rodar() == 0
    assert len(renders) == 1 and perdeu == [200]  # uma geração só; a confirmação que "se perdeu" tinha valido
    lote = requests.get(f"{ambiente_db.url}/api/lotes/{lid}", headers=hc).json()
    assert lote["status"] == "concluido" and lote["prontos"] == 1
    with ambiente_db.banco.tx() as c:
        cobr = c.all("SELECT valor FROM movimentos WHERE lote_id = ? AND tipo = 'video'", (lid,))
    assert len(cobr) == 1
    (job,) = [j for j in ambiente_db.banco.jobs(lid) if j["tipo"] == "render"]
    assert job["tentativas"] == 1 and job["sha256_video"] and job["conclusao_status"] == "ok"
    assert not [k for k in ambiente_db.objetos if "/tentativas/" in k]  # o objeto da tentativa foi publicado
    assert not (tmp_path / "entregas").exists()
