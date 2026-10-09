"""API de ponta a ponta com armazenamento falso (sem rede) e o banco dos testes."""

import hashlib
import json
import math

import pytest
from fastapi.testclient import TestClient

from render2go_servico import api as api_mod
from render2go_servico.api import Config, criar_app

ADMIN = "a" * 40
WORKER = "w" * 40
CONTINUO = {"deriva_cor": 6.5, "salto_cor": 1.2}  # vídeo que fica na foto até o fim


class Armazem:
    """Armazenamento falso: URLs legíveis e tamanhos num dicionário."""

    def __init__(self):
        self.objetos: dict[str, int] = {}
        self.apagados: list[str] = []

    def url(self, metodo, chave, expira_s=900, content_type=None, extra=None, **kw):
        extras = "".join(f"&{k}={v}" for k, v in (extra or {}).items())
        return f"https://armazem.teste/{chave}?{metodo}{extras}"

    def tamanho(self, chave):
        return self.objetos.get(chave)

    def ler(self, chave, limite):
        if chave not in self.objetos:
            raise OSError(f"{chave}: não existe")
        return f"foto {chave}".encode()

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
    cli.banco = banco
    return cli


def h(token):
    return {"Authorization": f"Bearer {token}"}


def _cliente(api, nome="Padaria", plano="pago", saldo="1000"):
    """Cadastra o cliente; o do plano pago já recebe saldo (pré-pago), salvo saldo=None."""
    r = api.post("/api/admin/clientes", json={"nome": nome, "plano": plano}, headers=h(ADMIN))
    assert r.status_code == 200, r.text
    if plano == "pago" and saldo:
        corpo = {"valor": saldo, "descricao": "recarga do teste"}
        rs = api.post(f"/api/admin/clientes/{r.json()['cliente']}/saldo", json=corpo, headers=h(ADMIN))
        assert rs.status_code == 200, rs.text
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
    assert (pend["cliente_nome"], pend["plano"], pend["ip"]) == ("Loja A", "pago", "testclient")
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
                **CONTINUO,
            },
        )
        assert r.json()["status"] == "ok"
    lote = api.get(f"/api/lotes/{lid}", headers=h(tok_a)).json()
    assert lote["status"] == "concluido" and lote["prontos"] == 2
    assert all("/saida/" in v["url"] and f"/{lid}/" in v["url"] for v in lote["videos"])
    # lote fechado: as fotos de entrada saem do armazenamento; os vídeos ficam
    api.app.state.cfg.esperar_fundo()
    assert not [k for k in api.armazem.objetos if k.startswith("entrada/")]
    assert len([k for k in api.armazem.objetos if k.startswith("saida/")]) == 2


def test_gpu_report_is_admin_only(api):
    g = api.banco.gerador_subindo("NVIDIA L40S")
    api.banco.gerador_criado(g, "pod-abc", "NVIDIA L40S", 1.09, "EU-RO-1")
    assert api.get("/api/admin/geradores").status_code == 401
    assert api.get("/api/admin/geradores", headers=h(WORKER)).status_code == 401
    assert api.get("/api/admin/geradores?dias=0", headers=h(ADMIN)).status_code == 422
    r = api.get("/api/admin/geradores?dias=7", headers=h(ADMIN)).json()
    (x,) = r["geradores"]
    assert (x["pod"], x["estado"], x["usd_h"]) == ("pod-abc", "subindo", 1.09)
    assert r["totais"]["ligados_agora"] == 1


def test_video_that_swaps_the_product_midway_is_redone_with_another_seed_then_refunded(api):
    tok = _cliente(api, "Revenda", saldo="10")
    r = _lote(api, tok, n=1, duracao=4)
    lid = r.json()["lote"]
    assert _enviar_tudo(api, tok, lid, r.json()["envios"]).status_code == 200
    api.post(f"/api/admin/lotes/{lid}/aprovar", headers=h(ADMIN))
    comp = _job(api)
    api.post(
        f"/api/worker/jobs/{comp['job_id']}/prompt",
        headers=h(WORKER),
        json={"worker": "pod-1", "prompt_en": "The camera slowly orbits the car.", "compilador": "i2v@6"},
    )
    _concluir(api, comp, status="ok")
    seeds = []
    medidas = [
        {"deriva_cor": 54.2, "salto_cor": 16.2},  # corte seco para outro carro (teste de 08/10)
        {"deriva_cor": 85.8, "salto_cor": 6.3},  # fusão para outro modelo
        {"deriva_cor": 6.5},  # sem a medida do salto: fail-closed
    ]
    for i, m in enumerate(medidas):
        render = _job(api)
        seeds.append(render["seed"])
        chave = render["video_url"].split("armazem.teste/")[1].split("?")[0]
        api.armazem.objetos[chave] = 7000
        r = _concluir(api, render, status="ok", bytes_video=7000, similaridade=0.99, **m)
        # nada que trocou de produto chega ao cliente; o job volta com outra seed até as tentativas acabarem
        assert r.json()["status"] == ("pendente" if i < 2 else "erro")
        assert chave in api.armazem.apagados
    assert len(set(seeds)) == 3
    (job,) = [j for j in api.banco.jobs(lid) if j["tipo"] == "render"]
    assert job["erro"] == "conferência da continuidade ausente" and job["deriva_cor"] == pytest.approx(6.5)
    lote = api.get(f"/api/lotes/{lid}", headers=h(tok)).json()
    assert lote["status"] == "concluido_com_falhas" and lote["cobranca"] == {"cobrado": 100, "devolvido": 100}


D1, D2, D3 = (f"{n}{n}{n}{n}{n}{n}{n}{n}-0000-4000-8000-000000000000" for n in "123")


def _pedir(api, token, n=1, dispositivo=None, ip=None, **pedido):
    corpo = {
        "prompt": "a câmera se aproxima devagar do produto",
        "fotos": [{"nome": f"foto {i}.jpg", "tipo": "image/jpeg", "bytes": 1000 + i} for i in range(n)],
        **pedido,
    }
    hd = {**h(token), "X-Fuso": "America/Sao_Paulo"}
    if dispositivo:
        hd["X-Dispositivo"] = dispositivo
    if ip:
        hd["X-Forwarded-For"] = ip
    return api.post("/api/lotes", json=corpo, headers=hd)


def test_free_trial_limits(api, monkeypatch):
    tok = _cliente(api, "Teste", plano="teste")
    plano = api.get("/api/receitas", headers=h(tok)).json()["plano"]
    assert plano == {
        "nome": "teste",
        "duracoes": [3],
        "qualidades": ["rapida"],
        "max_fotos": 5,
        "disponivel": True,
        "motivo": None,
    }
    assert _pedir(api, tok, qualidade="alta").status_code == 422  # 720p fica para o plano pago
    assert _pedir(api, tok, duracao=5).status_code == 422
    assert _pedir(api, tok, n=6).status_code == 429
    a = _pedir(api, tok, n=5, dispositivo=D1).json()["lote"]  # envio que não terminou...
    r = _pedir(api, tok, n=2, dispositivo=D1)  # ...é substituído pelo pedido novo
    assert r.status_code == 200 and api.get(f"/api/lotes/{a}", headers=h(tok)).json()["status"] == "recusado"
    b = r.json()["lote"]
    assert _enviar_tudo(api, tok, b, r.json()["envios"]).status_code == 200
    assert api.get(f"/api/lotes/{b}", headers=h(tok)).json()["pedido"]["duracao"] == 3
    # um pedido só por cliente de teste
    assert _pedir(api, tok, n=1, dispositivo=D1).status_code == 429
    assert api.get("/api/receitas", headers=h(tok)).json()["plano"]["disponivel"] is False

    # outro código de teste no MESMO dispositivo: o dispositivo já tem 2 fotos, o teto é 5
    tok2 = _cliente(api, "Teste 2", plano="teste")
    r = _pedir(api, tok2, n=4, dispositivo=D1)
    assert r.status_code == 429 and "dispositivo" in r.json()["detail"]
    assert _pedir(api, tok2, n=3, dispositivo=D1).status_code == 200
    # colega da mesma empresa (mesmo IP), outro dispositivo: tem o teste dele
    tok3 = _cliente(api, "Teste 3", plano="teste")
    r = _pedir(api, tok3, n=5, dispositivo=D2)
    assert r.status_code == 200
    _enviar_tudo(api, tok3, r.json()["lote"], r.json()["envios"])
    # teto por rede (IP): segura quem troca de navegador para juntar testes
    monkeypatch.setitem(api_mod.TESTE, "fotos_ip", 8)
    tok4 = _cliente(api, "Teste 4", plano="teste")
    r = _pedir(api, tok4, n=2, dispositivo=D3)
    assert r.status_code == 429 and "rede" in r.json()["detail"]
    assert _pedir(api, tok4, n=2, dispositivo=D3, ip="203.0.113.7").status_code == 200  # outra rede
    # pedido recusado libera o teste do cliente
    api.post(f"/api/admin/lotes/{b}/recusar", json={"motivo": "foto com pessoa sem autorização"}, headers=h(ADMIN))
    assert _pedir(api, tok, n=2, dispositivo=D1).status_code == 200


NAVEGADOR = {
    "idioma": "pt-BR",
    "idiomas": ["pt-BR", "en"],
    "tela": "1920x1080@1",
    "plataforma": "Win32",
    "nucleos": 8,
    "memoria": 8,
    "toque": 0,
    "marcado_em": "2026-10-08T12:00:00.000Z",
}


def test_authorization_is_recorded_for_audit_with_origin_and_photo_prints(banco):
    armazem = Armazem()
    cfg = Config(
        banco=banco,
        armazenamento=armazem,
        admin_tokens=[ADMIN],
        worker_tokens=[WORKER],
        segredo_aprovacao="s" * 40,
        analisar=lambda dados: {"versao": "pessoas-v1", "pessoas": 1, "rostos": 1, "maior": 0.2},
    )
    api = TestClient(criar_app(cfg))
    api.armazem = armazem
    tok = _cliente(api, "Estúdio", plano="teste")
    texto = api.get("/api/receitas", headers=h(tok)).json()["autorizacao"]
    assert texto["versao"] == "autorizacao-v1" and "autorização por escrito" in texto["texto"]
    # marcar a caixa não pede nome: o registro usa a conta, a origem e os sinais do navegador
    r = _pedir(api, tok, n=2, dispositivo=D1, consentimento=True, navegador=NAVEGADOR)
    assert r.status_code == 200, r.text
    lid = r.json()["lote"]
    _enviar_tudo(api, tok, lid, r.json()["envios"])
    cfg.analises.esperar()
    (pend,) = api.get("/api/admin/lotes", headers=h(ADMIN)).json()
    assert pend["autorizacao"]["quando"] and pend["dispositivo"] == D1 and pend["modelo"] == "Wan 2.2"

    a = api.get(f"/api/admin/lotes/{lid}/auditoria", headers=h(ADMIN)).json()
    aut = a["autorizacao"]
    assert aut["cliente_nome"] == "Estúdio" and aut["texto"] == texto["texto"]
    assert aut["texto_sha256"] == hashlib.sha256(texto["texto"].encode()).hexdigest()
    assert (aut["ip"], aut["dispositivo"], aut["fuso"]) == ("testclient", D1, "America/Sao_Paulo")
    sinais = aut["sinais"]
    assert sinais["marcado_em"] == NAVEGADOR["marcado_em"] and sinais["tela"] == "1920x1080@1"
    assert len(sinais["impressao"]) == 24 and sinais["impressao"] == a["lote"]["impressao"]
    assert aut["agente"] and [f["nome"] for f in aut["fotos"]] == ["foto-0", "foto-1"]
    assert all(len(f["sha256"]) == 64 for f in a["fotos"])  # impressão fica depois que a foto é apagada
    assert a["lote"]["pedido"]["consentimento"] is True and "navegador" not in a["lote"]["pedido"]
    criado = a["eventos"][0]
    assert criado["tipo"] == "criado" and criado["detalhe"]["autorizacao"] is True
    assert criado["detalhe"]["dispositivo"] == D1 and criado["detalhe"]["ip"] == "testclient"
    assert api.get("/api/admin/lotes/lote_x/auditoria", headers=h(ADMIN)).status_code == 404
    assert api.get(f"/api/admin/lotes/{lid}/auditoria", headers=h(tok)).status_code == 401


def test_cookie_choice_is_recorded_and_free_style_is_offered(api, banco):
    r = api.post(
        "/api/cookies",
        json={"versao": "1", "estatisticas": False, "marketing": False},
        headers={"X-Dispositivo": D1},
    )
    assert r.status_code == 200
    with banco.tx() as c:
        (ck,) = c.all("SELECT * FROM cookies")
    assert ck["dispositivo"] == D1 and json.loads(ck["escolha"]) == {
        "necessarios": True,
        "estatisticas": False,
        "marketing": False,
    }
    tok = _cliente(api)
    receitas = api.get("/api/receitas", headers=h(tok)).json()["receitas"]
    assert receitas[0]["id"] == "livre" and receitas[0]["label"].startswith("Livre")
    assert _pedir(api, tok, n=1, receita="livre", prompt="as folhas balançam com o vento").status_code == 200


def test_generation_model_is_listed_validated_and_shown(api):
    tok = _cliente(api)
    r = api.get("/api/receitas", headers=h(tok)).json()
    assert r["modelo_padrao"] == "wan2.2-i2v" and [m["nome"] for m in r["modelos"]] == ["Wan 2.2"]
    assert _pedir(api, tok, modelo="modelo-que-nao-existe").status_code == 422
    lid = _pedir(api, tok, n=1).json()["lote"]
    lote = api.get(f"/api/lotes/{lid}", headers=h(tok)).json()
    assert lote["modelo"] == "Wan 2.2" and lote["pedido"]["modelo"] == "wan2.2-i2v"


def test_client_sees_a_time_estimate_and_open_or_download_links(api):
    tok = _cliente(api, plano="teste")
    r = _pedir(api, tok, n=2, dispositivo=D1)
    lid = r.json()["lote"]
    _enviar_tudo(api, tok, lid, r.json()["envios"])
    lote = api.get(f"/api/lotes/{lid}", headers=h(tok)).json()
    assert lote["previsao"] == {"fase": "analise", "inicio_max_s": api_mod.PRAZO_ANALISE_S}
    api.post(f"/api/admin/lotes/{lid}/aprovar", headers=h(ADMIN))
    prev = api.get(f"/api/lotes/{lid}", headers=h(tok)).json()["previsao"]
    # nenhum gerador ligado: subida + ciclo do orquestrador + espera de juntar testes (até 10 min)
    teto = api_mod.PREPARO_S + api_mod.CICLO_S / 2 + 600
    assert prev["fase"] == "fila" and api_mod.PREPARO_S < prev["inicio_s"] <= math.ceil(teto)
    assert prev["fim_s"] == prev["inicio_s"] + math.ceil(2 * api_mod.VIDEO_PADRAO_S)
    # o orquestrador decidiu subir um pod há 100 s: falta o resto da subida (sem a espera do grupo)
    gid = api.banco.gerador_subindo("NVIDIA L40S")
    with api.banco.tx() as cur:
        cur.run("UPDATE geradores SET criado = criado - 100 WHERE id = ?", (gid,))
    prev = api.get(f"/api/lotes/{lid}", headers=h(tok)).json()["previsao"]
    assert api_mod.PREPARO_S - 102 <= prev["inicio_s"] <= api_mod.PREPARO_S - 99

    comp = _job(api)  # o gerador liga e pega o trabalho
    api.post(
        f"/api/worker/jobs/{comp['job_id']}/prompt",
        headers=h(WORKER),
        json={"worker": "pod-1", "prompt_en": "The camera slowly dollies in.", "compilador": "i2v@5"},
    )
    api.post(f"/api/worker/jobs/{comp['job_id']}/concluir", headers=h(WORKER), json={"worker": "pod-1", "status": "ok"})
    render = _job(api)
    chave = render["video_url"].split("armazem.teste/")[1].split("?")[0]
    api.armazem.objetos[chave] = 7000
    api.post(
        f"/api/worker/jobs/{render['job_id']}/concluir",
        headers=h(WORKER),
        json={"worker": "pod-1", "status": "ok", "bytes_video": 7000, "similaridade": 0.99, "segundos": 40.0}
        | CONTINUO,
    )
    lote = api.get(f"/api/lotes/{lid}", headers=h(tok)).json()
    assert lote["previsao"] == {"fase": "gerando", "fim_s": 40}  # 1 vídeo restante × 40 s medidos
    (pronto,) = [v for v in lote["videos"] if v.get("url")]
    assert "response-content-disposition" not in pronto["url"]  # abrir: toca no navegador
    assert "response-content-disposition" in pronto["baixar"] and pronto["arquivo"] in pronto["baixar"]


def test_internal_plan_has_no_trial_limits_and_is_approved_automatically(api):
    tok = _cliente(api, "Dono", plano="interno")
    # sem cobrança e sem limites de teste; vídeos de 4 a 7 s, como no plano pago
    assert api.get("/api/receitas", headers=h(tok)).json()["plano"] == {"nome": "interno", "duracoes": [4, 5, 6, 7]}
    assert api.get("/api/saldo", headers=h(tok)).json() == {"pre_pago": False}
    lotes = []
    for _ in range(3):  # 3 pedidos de 10 fotos ao mesmo tempo, 720p e 5 s
        r = _pedir(api, tok, n=10, dispositivo=D1, qualidade="alta", duracao=5)
        assert r.status_code == 200, r.text
        lid = r.json()["lote"]
        r = _enviar_tudo(api, tok, lid, r.json()["envios"])
        assert r.json() == {"lote": lid, "status": "aprovado", "videos": 10}
        lotes.append(lid)
    assert api.get("/api/admin/lotes", headers=h(ADMIN)).json() == []  # nada esperando revisão
    hist = {x["lote"]: x for x in api.get("/api/admin/historico", headers=h(ADMIN)).json()["lotes"]}
    assert all(hist[lid]["aprovado_por"] == "auto:interno" for lid in lotes)
    assert _job(api)["tipo"] == "compilar"  # já na fila
    # o filtro de conteúdo continua valendo
    assert _pedir(api, tok, prompt="um homem segura uma arma").status_code == 422


def test_device_table_flags_ip_changes_and_several_codes(api):
    a, b = _cliente(api, "Loja A", plano="teste"), _cliente(api, "Loja B", plano="teste")
    for tok, ip in ((a, "198.51.100.1"), (b, "198.51.100.2")):  # mesmo dispositivo, dois IPs e dois códigos
        r = _pedir(api, tok, n=2, dispositivo=D1, ip=ip)
        _enviar_tudo(api, tok, r.json()["lote"], r.json()["envios"])
    (d,) = api.get("/api/admin/historico", headers=h(ADMIN)).json()["dispositivos"]
    assert d["dispositivo"] == D1 and sorted(d["ips"]) == ["198.51.100.1", "198.51.100.2"]
    assert len(d["clientes"]) == 2 and d["fotos_teste"] == 4 and d["fusos"] == ["America/Sao_Paulo"]
    ips = api.get("/api/admin/historico", headers=h(ADMIN)).json()["ips"]
    assert all(u["dispositivos"] == 1 for u in ips)


def test_history_ip_usage_and_client_deactivation(api):
    tok = _cliente(api, "Loja A")
    r = _lote(api, tok, n=2)
    lid = r.json()["lote"]
    _enviar_tudo(api, tok, lid, r.json()["envios"])
    r2 = _lote(api, tok, n=1)
    lid2 = r2.json()["lote"]
    _enviar_tudo(api, tok, lid2, r2.json()["envios"])
    api.post(f"/api/admin/lotes/{lid}/aprovar", headers=h(ADMIN))
    api.post(f"/api/admin/lotes/{lid2}/recusar", json={"motivo": "foto borrada"}, headers=h(ADMIN))

    todos = api.get("/api/admin/historico", headers=h(ADMIN)).json()
    por_lote = {x["lote"]: x for x in todos["lotes"]}
    assert por_lote[lid]["status"] == "aprovado" and por_lote[lid]["cliente_nome"] == "Loja A"
    assert por_lote[lid]["aprovado_por"].startswith("admin-") and por_lote[lid]["total"] == 2
    assert por_lote[lid2]["motivo"] == "foto borrada" and por_lote[lid2]["ip"] == "testclient"
    aprovados = api.get("/api/admin/historico?grupo=aprovados", headers=h(ADMIN)).json()["lotes"]
    recusados = api.get("/api/admin/historico?grupo=recusados", headers=h(ADMIN)).json()["lotes"]
    assert [x["lote"] for x in aprovados] == [lid] and [x["lote"] for x in recusados] == [lid2]
    assert api.get("/api/admin/historico?grupo=xyz", headers=h(ADMIN)).status_code == 422
    (uso,) = todos["ips"]  # o recusado não conta
    assert uso["ip"] == "testclient" and uso["fotos"] == 2 and uso["pedidos"] == 1

    # fotos do lote recusado apagadas do armazenamento
    api.app.state.cfg.esperar_fundo()
    assert all(f"/{lid2}/" not in k for k in api.armazem.objetos if k.startswith("entrada/"))
    assert api.get("/api/admin/historico?grupo=recusados", headers=h(ADMIN)).json()["lotes"][0]["fotos_apagadas"]

    # código exposto: o cliente é desativado e o código deixa de valer
    cid = por_lote[lid]["cliente"]
    assert api.post(f"/api/admin/clientes/{cid}/desativar", headers=h(ADMIN)).status_code == 200
    assert api.get("/api/lotes", headers=h(tok)).status_code == 401
    assert api.post(f"/api/admin/clientes/{cid}/desativar", headers=h(ADMIN)).status_code == 404


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


def test_people_warning_is_computed_after_upload_and_shown_to_the_reviewer(banco):
    armazem = Armazem()

    def analisar(dados):  # detector falso: "pessoas" na foto 0, foto 1 ilegível
        if b"foto-1" in dados:
            raise ValueError("imagem ilegível")
        return {"versao": "pessoas-v1", "pessoas": 2 if b"foto-0" in dados else 0, "rostos": 1, "maior": 0.3}

    cfg = Config(
        banco=banco,
        armazenamento=armazem,
        admin_tokens=[ADMIN],
        worker_tokens=[WORKER],
        segredo_aprovacao="s" * 40,
        analisar=analisar,
    )
    api = TestClient(criar_app(cfg))
    api.armazem = armazem
    tok = _cliente(api)
    r = _lote(api, tok, n=3)
    lid = r.json()["lote"]
    assert _enviar_tudo(api, tok, lid, r.json()["envios"]).status_code == 200
    cfg.analises.esperar()
    (p,) = api.get("/api/admin/lotes", headers=h(ADMIN)).json()
    assert p["analise_ligada"] is True and p["pedido"]["consentimento"] is False
    por_nome = {f["nome"]: f["analise"] for f in p["fotos"]}
    assert por_nome["foto-0"]["pessoas"] == 2 and por_nome["foto-2"]["pessoas"] == 0
    assert "ilegível" in por_nome["foto-1"]["erro"]  # erro aparece como erro, nunca como "sem pessoas"

    # foto sem análise (ex.: a API reiniciou no meio): a próxima listagem agenda de novo
    with banco.tx() as c:
        c.run("UPDATE fotos SET analise = NULL WHERE lote_id = ?", (lid,))
    assert all(f["analise"] is None for f in api.get("/api/admin/lotes", headers=h(ADMIN)).json()[0]["fotos"])
    cfg.analises.esperar()
    fotos = api.get("/api/admin/lotes", headers=h(ADMIN)).json()[0]["fotos"]
    assert all(f["analise"] is not None for f in fotos)


def test_app_configured_from_environment(monkeypatch, tmp_path):
    for k, v in {
        "RENDER2GO_DB_URL": f"sqlite:///{tmp_path / 'env.db'}",
        "RENDER2GO_R2_ENDPOINT": "https://conta.r2.cloudflarestorage.com",
        "RENDER2GO_R2_BUCKET": "gpu2go",
        "RENDER2GO_R2_ACCESS_KEY_ID": "k",
        "RENDER2GO_R2_SECRET_ACCESS_KEY": "s",
        "RENDER2GO_SEGREDO_APROVACAO": "s" * 40,
        "RENDER2GO_ADMIN_TOKENS": ADMIN,
    }.items():
        monkeypatch.setenv(k, v)
    api = TestClient(criar_app())  # sem Config: lê do ambiente na 1ª requisição
    r = api.get("/")
    assert r.status_code == 200 and 'data-pagina="cliente"' in r.text
    csp = r.headers["Content-Security-Policy"]
    assert "connect-src 'self' https://conta.r2.cloudflarestorage.com" in csp
    assert api.get("/estilo.css").status_code == 200 and api.get("/app.js").status_code == 200


def _concluir(api, job, **corpo):
    return api.post(f"/api/worker/jobs/{job['job_id']}/concluir", headers=h(WORKER), json={"worker": "pod-1", **corpo})


def test_prepaid_balance_prices_charges_refunds_and_minimum_duration(api):
    r = api.post("/api/admin/clientes", json={"nome": "Imobiliária", "plano": "pago"}, headers=h(ADMIN)).json()
    cid, tok = r["cliente"], r["token"]
    plano = api.get("/api/receitas", headers=h(tok)).json()["plano"]
    assert plano == {"nome": "pago", "duracoes": [4, 5, 6, 7], "precos": {"rapida": 25, "alta": 50}, "saldo": 0}
    receitas = api.get("/api/receitas", headers=h(tok)).json()["receitas"]
    assert all(x["duracao"] >= 4 for x in receitas)

    # sem saldo: recusado antes de as fotos subirem, com o preço do pedido (2 vídeos × 5 s × R$ 0,25)
    r = _lote(api, tok, n=2)
    assert r.status_code == 402 and "R$ 2,50" in r.json()["detail"] and "R$ 0,00" in r.json()["detail"]

    # recarga pelo /aprovar (valores em reais); erros de digitação e plano sem saldo são recusados
    saldo = f"/api/admin/clientes/{cid}/saldo"
    assert api.post(saldo, json={"valor": "abc", "descricao": "Pix"}, headers=h(ADMIN)).status_code == 422
    assert api.post(saldo, json={"valor": "0", "descricao": "Pix"}, headers=h(ADMIN)).status_code == 422
    assert api.post(saldo, json={"valor": "-1", "descricao": "ajuste"}, headers=h(ADMIN)).status_code == 422
    assert api.post(saldo, json={"valor": "10", "descricao": "Pix"}, headers=h(tok)).status_code == 401
    pix = {"valor": "10,00", "descricao": "Pix de 09/10", "referencia": "E123"}
    assert api.post(saldo, json=pix, headers=h(ADMIN)).json() == {"cliente": cid, "saldo": 1000}
    assert api.post(saldo, json=pix, headers=h(ADMIN)).status_code == 422  # o mesmo pagamento não entra duas vezes
    r = api.post("/api/admin/clientes", json={"nome": "Teste", "plano": "teste"}, headers=h(ADMIN))
    teste = r.json()["cliente"]
    r = api.post(f"/api/admin/clientes/{teste}/saldo", json={"valor": "10", "descricao": "Pix"}, headers=h(ADMIN))
    assert r.status_code == 422

    # 3 s só no teste gratuito: no pré-pago, de 4 a 7 s
    r = _lote(api, tok, n=1, duracao=3)
    assert r.status_code == 422 and "4 a 7 s" in r.json()["detail"]

    # 2 vídeos de 4 s em 720p: R$ 4,00, cobrados quando as fotos chegam
    r = _lote(api, tok, n=2, duracao=4, qualidade="alta")
    assert r.status_code == 200 and r.json()["custo"] == 400
    lid = r.json()["lote"]
    assert api.get("/api/saldo", headers=h(tok)).json()["saldo"] == 1000
    assert _enviar_tudo(api, tok, lid, r.json()["envios"]).status_code == 200
    s = api.get("/api/saldo", headers=h(tok)).json()
    assert s["pre_pago"] and s["saldo"] == 600 and s["precos"] == {"rapida": 25, "alta": 50}
    assert (s["extrato"][0]["tipo"], s["extrato"][0]["valor"]) == ("pedido", -400)
    assert s["extrato"][0]["descricao"] == "2 vídeo(s) de 4 s em 720p"
    assert api.get(f"/api/lotes/{lid}", headers=h(tok)).json()["cobranca"] == {"cobrado": 400, "devolvido": 0}

    # pedido recusado na revisão: volta inteiro (sem duração escolhida: a do estilo, 5 s)
    r = _lote(api, tok, n=1)
    assert r.json()["custo"] == 125
    rec = r.json()["lote"]
    _enviar_tudo(api, tok, rec, r.json()["envios"])
    assert api.get("/api/saldo", headers=h(tok)).json()["saldo"] == 475
    api.post(f"/api/admin/lotes/{rec}/recusar", json={"motivo": "foto borrada"}, headers=h(ADMIN))
    assert api.get("/api/saldo", headers=h(tok)).json()["saldo"] == 600
    assert api.get(f"/api/lotes/{rec}", headers=h(tok)).json()["cobranca"] == {"cobrado": 125, "devolvido": 125}

    # um vídeo pronto e outro que falha de vez: só o que falhou volta
    api.post(f"/api/admin/lotes/{lid}/aprovar", headers=h(ADMIN))
    comp = _job(api)
    api.post(
        f"/api/worker/jobs/{comp['job_id']}/prompt",
        headers=h(WORKER),
        json={"worker": "pod-1", "prompt_en": "The camera slowly dollies in.", "compilador": "i2v@5"},
    )
    _concluir(api, comp, status="ok")
    ok = _job(api)
    api.armazem.objetos[ok["video_url"].split("armazem.teste/")[1].split("?")[0]] = 7000
    assert _concluir(api, ok, status="ok", bytes_video=7000, similaridade=0.99, **CONTINUO).json()["status"] == "ok"
    assert _concluir(api, _job(api), status="erro", erro="sem memória na GPU").json()["status"] == "erro"
    lote = api.get(f"/api/lotes/{lid}", headers=h(tok)).json()
    assert lote["status"] == "concluido_com_falhas" and lote["cobranca"] == {"cobrado": 400, "devolvido": 200}
    assert api.get("/api/saldo", headers=h(tok)).json()["saldo"] == 800

    # o saldo acabou enquanto as fotos subiam (outro pedido, ajuste): recusado, sem cobrança
    r = _lote(api, tok, n=2, duracao=4)  # R$ 2,00
    corrida = r.json()["lote"]
    api.post(saldo, json={"valor": "-7,00", "descricao": "ajuste"}, headers=h(ADMIN))
    r = _enviar_tudo(api, tok, corrida, r.json()["envios"])
    assert r.status_code == 402 and "R$ 1,00" in r.json()["detail"]
    assert api.get(f"/api/lotes/{corrida}", headers=h(tok)).json()["status"] == "recusado"
    assert api.get("/api/saldo", headers=h(tok)).json()["saldo"] == 100

    # o dono vê o saldo de cada cliente e o extrato completo
    clientes = {k["id"]: k for k in api.get("/api/admin/clientes", headers=h(ADMIN)).json()["clientes"]}
    assert clientes[cid]["saldo"] == 100 and clientes[teste]["saldo"] == 0
    extrato = api.get(f"/api/admin/clientes/{cid}/extrato", headers=h(ADMIN)).json()
    assert extrato["saldo"] == 100 and [m["tipo"] for m in extrato["movimentos"]][-1] == "recarga"
    assert api.get("/api/admin/clientes", headers=h(tok)).status_code == 401
