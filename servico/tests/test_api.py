"""API de ponta a ponta com armazenamento falso (sem rede) e o banco dos testes."""

import pytest
from fastapi.testclient import TestClient

from gpu2go_servico.api import Config, criar_app

ADMIN = "a" * 40
WORKER = "w" * 40


class Armazem:
    """Armazenamento falso: URLs legíveis e tamanhos num dicionário."""

    def __init__(self):
        self.objetos: dict[str, int] = {}
        self.apagados: list[str] = []

    def url(self, metodo, chave, expira_s=900, content_type=None, **kw):
        return f"https://armazem.teste/{chave}?{metodo}"

    def tamanho(self, chave):
        return self.objetos.get(chave)

    def apagar(self, chave):
        self.apagados.append(chave)
        self.objetos.pop(chave, None)


@pytest.fixture
def api(banco):
    armazem = Armazem()
    cfg = Config(
        banco=banco,
        armazenamento=armazem,
        admin_tokens=[ADMIN],
        worker_tokens=[WORKER],
        segredo_aprovacao="s" * 40,
    )
    cli = TestClient(criar_app(cfg))
    cli.armazem = armazem
    return cli


def h(token):
    return {"Authorization": f"Bearer {token}"}


def _cliente(api, nome="Padaria", plano="pago"):
    r = api.post("/api/admin/clientes", json={"nome": nome, "plano": plano}, headers=h(ADMIN))
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _lote(api, token, n=2, **pedido):
    corpo = {
        "prompt": "a câmera se aproxima devagar do produto",
        "fotos": [{"nome": f"foto {i}.jpg", "tipo": "image/jpeg", "bytes": 1000 + i} for i in range(n)],
        **pedido,
    }
    return api.post("/api/lotes", json=corpo, headers=h(token))


def _enviar_tudo(api, token, lid, envios):
    for e, f in zip(envios, range(len(envios)), strict=True):
        chave = e["url"].split("armazem.teste/")[1].split("?")[0]
        api.armazem.objetos[chave] = 1000 + f
    return api.post(f"/api/lotes/{lid}/enviado", headers=h(token))


def _job(api):
    r = api.post("/api/worker/reivindicar", json={"worker": "pod-1"}, headers=h(WORKER))
    assert r.status_code == 200
    return r.json()["job"]


def test_tokens_are_required_and_checked(api):
    assert api.get("/api/receitas").status_code == 401
    assert api.get("/api/receitas", headers=h("x" * 40)).status_code == 401
    assert api.post("/api/admin/clientes", json={"nome": "x"}, headers=h(WORKER)).status_code == 401
    assert api.post("/api/worker/reivindicar", json={"worker": "p"}, headers=h(ADMIN)).status_code == 401
    assert api.get("/saude").json() == {"ok": True}


def test_full_flow_with_isolation_and_first_frame_check(api):
    tok_a, tok_b = _cliente(api, "Loja A"), _cliente(api, "Loja B")
    r = _lote(api, tok_a)
    assert r.status_code == 200, r.text
    lid, envios = r.json()["lote"], r.json()["envios"]
    # cada foto tem endereço próprio, decidido pelo servidor, dentro do cliente e do lote
    assert all("/entrada/" in e["url"] and f"/{lid}/" in e["url"] and "PUT" in e["url"] for e in envios)
    assert api.post(f"/api/lotes/{lid}/enviado", headers=h(tok_a)).status_code == 422  # nada enviado
    assert _enviar_tudo(api, tok_a, lid, envios).json()["status"] == "aguardando_aprovacao"

    # isolamento: B não vê nem mexe no lote de A
    assert api.get(f"/api/lotes/{lid}", headers=h(tok_b)).status_code == 404
    assert api.post(f"/api/lotes/{lid}/enviado", headers=h(tok_b)).status_code == 404
    assert api.get("/api/lotes", headers=h(tok_b)).json() == []

    assert _job(api) is None  # nada roda antes da aprovação
    (pend,) = api.get("/api/admin/lotes", headers=h(ADMIN)).json()
    assert pend["lote"] == lid and len(pend["fotos"]) == 2
    assert api.post(f"/api/admin/lotes/{lid}/aprovar", headers=h(ADMIN)).json()["videos"] == 2

    comp = _job(api)
    assert comp["tipo"] == "compilar" and comp["aprovacao"]
    r = api.post(
        f"/api/worker/jobs/{comp['job_id']}/prompt",
        headers=h(WORKER),
        json={
            "worker": "pod-1",
            "prompt_en": "The camera slowly dollies in.",
            "compilador": "i2v@5",
        },
    )
    assert r.status_code == 200
    api.post(
        f"/api/worker/jobs/{comp['job_id']}/concluir",
        headers=h(WORKER),
        json={"worker": "pod-1", "status": "ok"},
    )

    render = _job(api)
    chave_video = render["video_url"].split("armazem.teste/")[1].split("?")[0]
    assert chave_video.startswith("saida/") and chave_video.endswith(f"/{lid}/{render['arquivo']}")
    assert render["foto_url"].endswith("?GET") and "PUT" in render["video_url"]
    # outro worker não conclui o job
    r = api.post(
        f"/api/worker/jobs/{render['job_id']}/concluir",
        headers=h(WORKER),
        json={"worker": "pod-2", "status": "ok"},
    )
    assert r.status_code == 409

    # "ok" sem conferência do 1º quadro: vira erro, o vídeo é apagado e o job volta à fila
    api.armazem.objetos[chave_video] = 5000
    r = api.post(
        f"/api/worker/jobs/{render['job_id']}/concluir",
        headers=h(WORKER),
        json={"worker": "pod-1", "status": "ok", "bytes_video": 5000},
    )
    assert r.json()["status"] == "pendente" and chave_video in api.armazem.apagados
    # similaridade baixa (vídeo de outra foto): também recusado
    render = _job(api)
    api.armazem.objetos[chave_video] = 5000
    r = api.post(
        f"/api/worker/jobs/{render['job_id']}/concluir",
        headers=h(WORKER),
        json={"worker": "pod-1", "status": "ok", "bytes_video": 5000, "similaridade": 0.2},
    )
    assert r.json()["status"] == "pendente"

    # os dois vídeos certos: conferidos e no armazenamento
    for _ in range(2):
        render = _job(api)
        chave = render["video_url"].split("armazem.teste/")[1].split("?")[0]
        api.armazem.objetos[chave] = 7000
        r = api.post(
            f"/api/worker/jobs/{render['job_id']}/concluir",
            headers=h(WORKER),
            json={
                "worker": "pod-1",
                "status": "ok",
                "bytes_video": 7000,
                "similaridade": 0.999,
                "segundos": 12.0,
            },
        )
        assert r.json()["status"] == "ok"
    lote = api.get(f"/api/lotes/{lid}", headers=h(tok_a)).json()
    assert lote["status"] == "concluido" and lote["prontos"] == 2
    assert all("/saida/" in v["url"] and f"/{lid}/" in v["url"] for v in lote["videos"])


def test_free_trial_limits(api):
    tok = _cliente(api, "Teste", plano="teste")
    assert _lote(api, tok, qualidade="alta").status_code == 422
    assert _lote(api, tok, duracao=5).status_code == 422
    assert _lote(api, tok, n=11).status_code == 429
    r = _lote(api, tok, n=6)
    assert r.status_code == 200 and r.json()["envios"]
    assert _lote(api, tok, n=5).status_code == 429  # 6 + 5 passa das 10 fotos do teste
    assert _lote(api, tok, n=4).status_code == 200


def test_content_filter_blocks_at_entry_and_on_compiled_prompt(api):
    tok = _cliente(api)
    assert _lote(api, tok, prompt="um homem segura uma arma").status_code == 422
    r = _lote(api, tok, n=1)
    lid = r.json()["lote"]
    _enviar_tudo(api, tok, lid, r.json()["envios"])
    api.post(f"/api/admin/lotes/{lid}/aprovar", headers=h(ADMIN))
    comp = _job(api)
    r = api.post(
        f"/api/worker/jobs/{comp['job_id']}/prompt",
        headers=h(WORKER),
        json={"worker": "pod-1", "prompt_en": "a man holds a gun", "compilador": "i2v@5"},
    )
    assert r.status_code == 422
    assert api.get(f"/api/lotes/{lid}", headers=h(tok)).json()["status"] in (
        "recusado",
        "concluido_com_falhas",
    )
    assert _job(api) is None


def test_refusal_and_admin_client_creation(api):
    tok = _cliente(api)
    r = _lote(api, tok, n=1)
    lid = r.json()["lote"]
    _enviar_tudo(api, tok, lid, r.json()["envios"])
    r = api.post(f"/api/admin/lotes/{lid}/recusar", json={"motivo": "foto sem autorização"}, headers=h(ADMIN))
    assert r.json()["status"] == "recusado"
    assert api.get(f"/api/lotes/{lid}", headers=h(tok)).json()["motivo"] == "foto sem autorização"
    assert api.post("/api/admin/clientes", json={"nome": "X", "plano": "vip"}, headers=h(ADMIN)).status_code == 422


def test_app_configured_from_environment(monkeypatch, tmp_path):
    for k, v in {
        "GPU2GO_DB_URL": f"sqlite:///{tmp_path / 'env.db'}",
        "GPU2GO_R2_ENDPOINT": "https://conta.r2.cloudflarestorage.com",
        "GPU2GO_R2_BUCKET": "gpu2go",
        "GPU2GO_R2_ACCESS_KEY_ID": "k",
        "GPU2GO_R2_SECRET_ACCESS_KEY": "s",
        "GPU2GO_SEGREDO_APROVACAO": "s" * 40,
        "GPU2GO_ADMIN_TOKENS": ADMIN,
    }.items():
        monkeypatch.setenv(k, v)
    api = TestClient(criar_app())  # sem Config: lê do ambiente na 1ª requisição
    r = api.get("/")
    assert r.status_code == 200 and 'data-pagina="cliente"' in r.text
    csp = r.headers["Content-Security-Policy"]
    assert "connect-src 'self' https://conta.r2.cloudflarestorage.com" in csp
    assert api.get("/estilo.css").status_code == 200 and api.get("/app.js").status_code == 200
