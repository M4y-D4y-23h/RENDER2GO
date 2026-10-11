"""API de ponta a ponta com armazenamento falso (sem rede) e o banco dos testes."""

import asyncio
import hashlib
import json
import math
import threading
import time

import pytest
from fastapi.testclient import TestClient

from render2go_servico import api as api_mod
from render2go_servico import contas
from render2go_servico.api import Config, criar_app

ADMIN = "a" * 40
WORKER = "w" * 40
PAINEL = "/painel-teste-0123456789"  # endereço do painel nos testes (RENDER2GO_ADMIN_CAMINHO)
TOTP = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"  # segredo do autenticador nos testes (RENDER2GO_ADMIN_TOTP)
ADM = {"X-Render2Go": "1"}  # o cabeçalho que a página do painel manda (a sessão vai no cookie)
# vídeo que fica na foto até o fim e passou no filtro de imagem (13 quadros, nota bem abaixo do limiar)
CONTINUO = {"deriva_cor": 6.5, "salto_cor": 1.2, "nsfw": 0.01, "nsfw_quadros": 13}


class Armazem:
    """Armazenamento falso: URLs legíveis e tamanhos num dicionário."""

    def __init__(self):
        self.objetos: dict[str, int] = {}
        self.apagados: list[str] = []
        self.assinados: dict[str, dict] = {}  # chave → o que a URL de envio exige (tipo e bytes)

    def url(self, metodo, chave, expira_s=900, content_type=None, extra=None, content_length=None, **kw):
        if metodo == "PUT":
            self.assinados[chave] = {"tipo": content_type, "bytes": content_length}
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


def config_teste(banco, armazem=None, **kw) -> Config:
    """Configuração dos testes: painel no endereço de teste, com autenticador; sem o Turnstile."""
    base = {
        "banco": banco,
        "armazenamento": armazem or Armazem(),
        "admin_tokens": [ADMIN],
        "worker_tokens": [WORKER],
        "segredo_aprovacao": "s" * 40,
        "admin_caminho": PAINEL,
        "admin_totp": TOTP,
        "turnstile_desligado": True,
        "retrato_s": 0,  # a previsão lê o banco a cada pedido (em produção, um retrato de 3 s)
    }
    return Config(**{**base, **kw})


def anonimo(api) -> TestClient:
    """Outro navegador, sem cookie nenhum."""
    return TestClient(api.app, base_url="https://testserver")


def entrar_admin(api, token=ADMIN) -> None:
    """Entra no painel com o token e o código do autenticador (o cookie fica no cliente de teste)."""
    agora = int(time.time() // 30)
    for contador in (agora - 1, agora, agora + 1):  # o mesmo código não vale duas vezes
        r = api.post(f"{PAINEL}/api/entrar", json={"token": token, "totp": contas.totp(TOTP, contador)}, headers=ADM)
        if r.status_code == 200:
            return
    raise AssertionError(r.text)


def cliente_teste(cfg: Config, admin: bool = True) -> TestClient:
    api = TestClient(criar_app(cfg), base_url="https://testserver")
    api.armazem, api.banco = cfg.armazenamento, cfg.banco
    if admin:
        entrar_admin(api)
    return api


@pytest.fixture
def api(banco):
    return cliente_teste(config_teste(banco))


def h(token):
    return {"Authorization": f"Bearer {token}"}


def _cliente(api, nome="Padaria", plano="pago", saldo="1000"):
    """Cliente com código de acesso (o jeito antigo, ainda aceito pela API); o do plano pago já
    recebe saldo pelo painel (pré-pago), salvo saldo=None."""
    cid, token = api.banco.criar_cliente(nome, 500, plano)
    if plano == "pago" and saldo:
        corpo = {"valor": saldo, "descricao": "recarga do teste"}
        rs = api.post(f"{PAINEL}/api/clientes/{cid}/saldo", json=corpo, headers=ADM)
        assert rs.status_code == 200, rs.text
    return token


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
    anon = anonimo(api)
    # o painel exige a sessão do painel (o token de worker, o de cliente ou o de admin no cabeçalho não servem)
    assert anon.post(f"{PAINEL}/api/clientes", json={"email": "x@gmail.com"}, headers=h(WORKER)).status_code == 401
    assert anon.get(f"{PAINEL}/api/clientes", headers=h(ADMIN)).status_code == 401
    assert api.post("/api/worker/reivindicar", json={"worker": "p"}, headers=h(ADMIN)).status_code == 401
    assert api.get("/saude").json() == {"ok": True}
    # o endereço antigo do painel e a API antiga dele não existem mais
    assert anon.get("/aprovar").status_code == 404
    assert anon.get("/api/admin/lotes", headers=h(ADMIN)).status_code == 404


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
    (pend,) = api.get(f"{PAINEL}/api/lotes", headers=ADM).json()
    assert pend["lote"] == lid and len(pend["fotos"]) == 2
    assert (pend["cliente_nome"], pend["plano"], pend["ip"]) == ("Loja A", "pago", "testclient")
    assert api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM).json()["videos"] == 2

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
    assert anonimo(api).get(f"{PAINEL}/api/geradores").status_code == 401
    assert anonimo(api).get(f"{PAINEL}/api/geradores", headers=h(WORKER)).status_code == 401
    assert api.get(f"{PAINEL}/api/geradores?dias=0", headers=ADM).status_code == 422
    r = api.get(f"{PAINEL}/api/geradores?dias=7", headers=ADM).json()
    (x,) = r["geradores"]
    assert (x["pod"], x["estado"], x["usd_h"]) == ("pod-abc", "subindo", 1.09)
    assert r["totais"]["ligados_agora"] == 1


def test_video_that_swaps_the_product_midway_is_redone_with_another_seed_then_refunded(api):
    tok = _cliente(api, "Revenda", saldo="10")
    r = _lote(api, tok, n=1, duracao=4)
    lid = r.json()["lote"]
    assert _enviar_tudo(api, tok, lid, r.json()["envios"]).status_code == 200
    api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM)
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
        "precos": {"rapida": 25, "alta": 50},  # o que custa depois do teste (recarga por Pix)
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
    api.post(f"{PAINEL}/api/lotes/{b}/recusar", json={"motivo": "foto com pessoa sem autorização"}, headers=ADM)
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
    cfg = config_teste(banco, analisar=lambda dados: {"versao": "pessoas-v1", "pessoas": 1, "rostos": 1, "maior": 0.2})
    api = cliente_teste(cfg)
    tok = _cliente(api, "Estúdio", plano="teste")
    texto = api.get("/api/receitas", headers=h(tok)).json()["autorizacao"]
    assert texto["versao"] == "autorizacao-v1" and "autorização por escrito" in texto["texto"]
    # marcar a caixa não pede nome: o registro usa a conta, a origem e os sinais do navegador
    r = _pedir(api, tok, n=2, dispositivo=D1, consentimento=True, navegador=NAVEGADOR)
    assert r.status_code == 200, r.text
    lid = r.json()["lote"]
    _enviar_tudo(api, tok, lid, r.json()["envios"])
    cfg.analises.esperar()
    (pend,) = api.get(f"{PAINEL}/api/lotes", headers=ADM).json()
    assert pend["autorizacao"]["quando"] and pend["dispositivo"] == D1 and pend["modelo"] == "Wan 2.2"

    a = api.get(f"{PAINEL}/api/lotes/{lid}/auditoria", headers=ADM).json()
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
    assert api.get(f"{PAINEL}/api/lotes/lote_x/auditoria", headers=ADM).status_code == 404
    assert anonimo(api).get(f"{PAINEL}/api/lotes/{lid}/auditoria", headers=h(tok)).status_code == 401


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
    api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM)
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
    assert api.get("/api/saldo", headers=h(tok)).json() == {"pre_pago": False, "pagamento": False}
    lotes = []
    for _ in range(3):  # 3 pedidos de 10 fotos ao mesmo tempo, 720p e 5 s
        r = _pedir(api, tok, n=10, dispositivo=D1, qualidade="alta", duracao=5)
        assert r.status_code == 200, r.text
        lid = r.json()["lote"]
        r = _enviar_tudo(api, tok, lid, r.json()["envios"])
        assert r.json() == {"lote": lid, "status": "aprovado", "videos": 10}
        lotes.append(lid)
    assert api.get(f"{PAINEL}/api/lotes", headers=ADM).json() == []  # nada esperando revisão
    hist = {x["lote"]: x for x in api.get(f"{PAINEL}/api/historico", headers=ADM).json()["lotes"]}
    assert all(hist[lid]["aprovado_por"] == "auto:interno" for lid in lotes)
    assert _job(api)["tipo"] == "compilar"  # já na fila
    # o filtro de conteúdo continua valendo
    assert _pedir(api, tok, prompt="um homem segura uma arma").status_code == 422


def test_device_table_flags_ip_changes_and_several_codes(api):
    a, b = _cliente(api, "Loja A", plano="teste"), _cliente(api, "Loja B", plano="teste")
    for tok, ip in ((a, "198.51.100.1"), (b, "198.51.100.2")):  # mesmo dispositivo, dois IPs e dois códigos
        r = _pedir(api, tok, n=2, dispositivo=D1, ip=ip)
        _enviar_tudo(api, tok, r.json()["lote"], r.json()["envios"])
    (d,) = api.get(f"{PAINEL}/api/historico", headers=ADM).json()["dispositivos"]
    assert d["dispositivo"] == D1 and sorted(d["ips"]) == ["198.51.100.1", "198.51.100.2"]
    assert len(d["clientes"]) == 2 and d["fotos_teste"] == 4 and d["fusos"] == ["America/Sao_Paulo"]
    ips = api.get(f"{PAINEL}/api/historico", headers=ADM).json()["ips"]
    assert all(u["dispositivos"] == 1 for u in ips)


def test_history_ip_usage_and_client_deactivation(api):
    tok = _cliente(api, "Loja A")
    r = _lote(api, tok, n=2)
    lid = r.json()["lote"]
    _enviar_tudo(api, tok, lid, r.json()["envios"])
    r2 = _lote(api, tok, n=1)
    lid2 = r2.json()["lote"]
    _enviar_tudo(api, tok, lid2, r2.json()["envios"])
    api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM)
    api.post(f"{PAINEL}/api/lotes/{lid2}/recusar", json={"motivo": "foto borrada"}, headers=ADM)

    todos = api.get(f"{PAINEL}/api/historico", headers=ADM).json()
    por_lote = {x["lote"]: x for x in todos["lotes"]}
    assert por_lote[lid]["status"] == "aprovado" and por_lote[lid]["cliente_nome"] == "Loja A"
    assert por_lote[lid]["aprovado_por"].startswith("admin-") and por_lote[lid]["total"] == 2
    assert por_lote[lid2]["motivo"] == "foto borrada" and por_lote[lid2]["ip"] == "testclient"
    aprovados = api.get(f"{PAINEL}/api/historico?grupo=aprovados", headers=ADM).json()["lotes"]
    recusados = api.get(f"{PAINEL}/api/historico?grupo=recusados", headers=ADM).json()["lotes"]
    assert [x["lote"] for x in aprovados] == [lid] and [x["lote"] for x in recusados] == [lid2]
    assert api.get(f"{PAINEL}/api/historico?grupo=xyz", headers=ADM).status_code == 422
    (uso,) = todos["ips"]  # o recusado não conta
    assert uso["ip"] == "testclient" and uso["fotos"] == 2 and uso["pedidos"] == 1

    # fotos do lote recusado apagadas do armazenamento
    api.app.state.cfg.esperar_fundo()
    assert all(f"/{lid2}/" not in k for k in api.armazem.objetos if k.startswith("entrada/"))
    assert api.get(f"{PAINEL}/api/historico?grupo=recusados", headers=ADM).json()["lotes"][0]["fotos_apagadas"]

    # código exposto: o cliente é desativado e o código deixa de valer
    cid = por_lote[lid]["cliente"]
    assert api.post(f"{PAINEL}/api/clientes/{cid}/desativar", headers=ADM).status_code == 200
    assert api.get("/api/lotes", headers=h(tok)).status_code == 401
    assert api.post(f"{PAINEL}/api/clientes/{cid}/desativar", headers=ADM).status_code == 404


def test_content_filter_blocks_at_entry_and_on_compiled_prompt(api):
    tok = _cliente(api)
    r = _lote(api, tok, prompt="um homem segura uma arma")
    # o cliente vê as regras, nunca qual palavra ou categoria barrou (ajudava a testar sinônimos)
    assert r.status_code == 422 and "filtro de conteúdo" in r.json()["detail"]
    assert "armas" in r.json()["detail"] and "C1" not in r.json()["detail"]  # a lista é a dos termos
    assert r.json()["detail"] == _lote(api, tok, prompt="uma modelo pelada").json()["detail"]
    # a categoria fica no registro de acesso (Marco Civil), para o dono ver quem insiste
    with api.banco.tx() as c:
        regs = c.all("SELECT detalhe FROM acessos WHERE evento = 'pedido_recusado_filtro'")
    assert len(regs) == 2 and any("armas" in x["detalhe"] for x in regs)
    r = _lote(api, tok, n=1)
    lid = r.json()["lote"]
    _enviar_tudo(api, tok, lid, r.json()["envios"])
    api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM)
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
    r = api.post(f"{PAINEL}/api/lotes/{lid}/recusar", json={"motivo": "foto sem autorização"}, headers=ADM)
    assert r.json()["status"] == "recusado"
    assert api.get(f"/api/lotes/{lid}", headers=h(tok)).json()["motivo"] == "foto sem autorização"
    corpo = {"email": "x@gmail.com", "plano": "vip"}
    assert api.post(f"{PAINEL}/api/clientes", json=corpo, headers=ADM).status_code == 422


def test_people_warning_is_computed_after_upload_and_shown_to_the_reviewer(banco):
    armazem = Armazem()

    def analisar(dados):  # detector falso: "pessoas" na foto 0, foto 1 ilegível
        if b"foto-1" in dados:
            raise ValueError("imagem ilegível")
        return {"versao": "pessoas-v1", "pessoas": 2 if b"foto-0" in dados else 0, "rostos": 1, "maior": 0.3}

    cfg = config_teste(banco, armazem, analisar=analisar)
    api = cliente_teste(cfg)
    tok = _cliente(api)
    r = _lote(api, tok, n=3)
    lid = r.json()["lote"]
    assert _enviar_tudo(api, tok, lid, r.json()["envios"]).status_code == 200
    cfg.analises.esperar()
    (p,) = api.get(f"{PAINEL}/api/lotes", headers=ADM).json()
    assert p["analise_ligada"] is True and p["pedido"]["consentimento"] is False
    por_nome = {f["nome"]: f["analise"] for f in p["fotos"]}
    assert por_nome["foto-0"]["pessoas"] == 2 and por_nome["foto-2"]["pessoas"] == 0
    assert "ilegível" in por_nome["foto-1"]["erro"]  # erro aparece como erro, nunca como "sem pessoas"

    # foto sem análise (ex.: a API reiniciou no meio): a próxima listagem agenda de novo
    with banco.tx() as c:
        c.run("UPDATE fotos SET analise = NULL WHERE lote_id = ?", (lid,))
    assert all(f["analise"] is None for f in api.get(f"{PAINEL}/api/lotes", headers=ADM).json()[0]["fotos"])
    cfg.analises.esperar()
    fotos = api.get(f"{PAINEL}/api/lotes", headers=ADM).json()[0]["fotos"]
    assert all(f["analise"] is not None for f in fotos)


def test_app_configured_from_environment(monkeypatch, tmp_path, capsys):
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
    # no servidor (uvicorn), a configuração é lida na subida, e o log diz o que falta configurar
    with TestClient(criar_app()) as api:
        assert api.get("/saude").json() == {"ok": True}
    saida = capsys.readouterr().out
    assert "[api] AVISO: SMTP não configurado" in saida and "[api] AVISO: painel desligado" in saida
    monkeypatch.setenv("RENDER2GO_SEGREDO_APROVACAO", "curto")
    app = criar_app()
    with pytest.raises(SystemExit, match="RENDER2GO_SEGREDO_APROVACAO"):  # variável faltando: a API nem sobe
        asyncio.run(app.router.lifespan_context(app).__aenter__())


def test_separate_analyzer_service_analyzes_the_photos_and_signals_it_is_alive(banco, tmp_path):
    from render2go_servico.api import analisar_continuamente

    armazem = Armazem()
    cfg = config_teste(banco, armazem, analise="separada")
    api = cliente_teste(cfg)
    tok = _cliente(api)
    r = _lote(api, tok, n=2)
    lid = r.json()["lote"]
    assert _enviar_tudo(api, tok, lid, r.json()["envios"]).status_code == 200
    assert cfg.analises is None and cfg.analise_ligada  # a API não analisa: o serviço separado analisa
    parar, vivo = threading.Event(), tmp_path / "vivo"
    t = threading.Thread(
        target=analisar_continuamente,
        args=(banco, armazem, lambda dados: {"versao": "pessoas-v1", "pessoas": 0, "rostos": 0, "maior": 0.0}, parar),
        kwargs={"intervalo_s": 0.01, "vivo": vivo},
    )
    t.start()
    try:
        for _ in range(500):
            if all(f["analise"] for f in banco.fotos(lid)):
                break
            time.sleep(0.01)
    finally:
        parar.set()
        t.join(10)
    assert all(json.loads(f["analise"])["pessoas"] == 0 for f in banco.fotos(lid))
    assert vivo.exists()  # healthcheck do contêiner
    seg = api.get(f"{PAINEL}/api/seguranca").json()
    assert seg["analise"] == "separada" and 0 <= seg["analisador_visto_s"] < 60


def _concluir(api, job, **corpo):
    return api.post(f"/api/worker/jobs/{job['job_id']}/concluir", headers=h(WORKER), json={"worker": "pod-1", **corpo})


def test_prepaid_balance_prices_charges_refunds_and_minimum_duration(api):
    cid, tok = api.banco.criar_cliente("Imobiliária", 500, "pago")
    plano = api.get("/api/receitas", headers=h(tok)).json()["plano"]
    assert plano == {"nome": "pago", "duracoes": [4, 5, 6, 7], "precos": {"rapida": 25, "alta": 50}, "saldo": 0}
    receitas = api.get("/api/receitas", headers=h(tok)).json()["receitas"]
    assert all(x["duracao"] >= 4 for x in receitas)

    # sem saldo: recusado antes de as fotos subirem, com o preço do pedido (2 vídeos × 5 s × R$ 0,25)
    r = _lote(api, tok, n=2)
    assert r.status_code == 402 and "R$ 2,50" in r.json()["detail"] and "R$ 0,00" in r.json()["detail"]

    # recarga pelo /aprovar (valores em reais); erros de digitação e plano sem saldo são recusados
    saldo = f"{PAINEL}/api/clientes/{cid}/saldo"
    assert api.post(saldo, json={"valor": "abc", "descricao": "Pix"}, headers=ADM).status_code == 422
    assert api.post(saldo, json={"valor": "0", "descricao": "Pix"}, headers=ADM).status_code == 422
    assert api.post(saldo, json={"valor": "-1", "descricao": "ajuste"}, headers=ADM).status_code == 422
    assert anonimo(api).post(saldo, json={"valor": "10", "descricao": "Pix"}, headers=h(tok)).status_code == 401
    assert api.post(saldo, json={"valor": "10", "descricao": "Pix"}).status_code == 403  # sem o cabeçalho da página
    pix = {"valor": "10,00", "descricao": "Pix de 09/10", "referencia": "E123"}
    assert api.post(saldo, json=pix, headers=ADM).json() == {"cliente": cid, "saldo": 1000}
    assert api.post(saldo, json=pix, headers=ADM).status_code == 422  # o mesmo pagamento não entra duas vezes
    r = api.post(f"{PAINEL}/api/clientes", json={"email": "teste.cliente@gmail.com", "plano": "teste"}, headers=ADM)
    teste = r.json()["cliente"]
    r = api.post(f"{PAINEL}/api/clientes/{teste}/saldo", json={"valor": "10", "descricao": "Pix"}, headers=ADM)
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
    api.post(f"{PAINEL}/api/lotes/{rec}/recusar", json={"motivo": "foto borrada"}, headers=ADM)
    assert api.get("/api/saldo", headers=h(tok)).json()["saldo"] == 600
    assert api.get(f"/api/lotes/{rec}", headers=h(tok)).json()["cobranca"] == {"cobrado": 125, "devolvido": 125}

    # um vídeo pronto e outro que falha de vez: só o que falhou volta
    api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM)
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
    api.post(saldo, json={"valor": "-7,00", "descricao": "ajuste"}, headers=ADM)
    r = _enviar_tudo(api, tok, corrida, r.json()["envios"])
    assert r.status_code == 402 and "R$ 1,00" in r.json()["detail"]
    assert api.get(f"/api/lotes/{corrida}", headers=h(tok)).json()["status"] == "recusado"
    assert api.get("/api/saldo", headers=h(tok)).json()["saldo"] == 100

    # o dono vê o saldo de cada cliente e o extrato completo
    clientes = {k["id"]: k for k in api.get(f"{PAINEL}/api/clientes", headers=ADM).json()["clientes"]}
    assert clientes[cid]["saldo"] == 100 and clientes[teste]["saldo"] == 0
    extrato = api.get(f"{PAINEL}/api/clientes/{cid}/extrato", headers=ADM).json()
    assert extrato["saldo"] == 100 and [m["tipo"] for m in extrato["movimentos"]][-1] == "recarga"
    assert anonimo(api).get(f"{PAINEL}/api/clientes", headers=h(tok)).status_code == 401


def test_purpose_catalog_and_interest_in_coming_soon_options(api):
    tok = _cliente(api, "Loja Interesse")
    tok_teste = _cliente(api, "Teste Interesse", plano="teste")
    r = api.get("/api/receitas", headers=h(tok)).json()
    fins = {f["id"]: f for f in r["finalidades"]}
    assert len(fins) == 12 and sum(f["destaque"] for f in fins.values()) == 6
    assert [i for i, f in fins.items() if f["disponivel"]] == ["imagem-video"] and r[
        "finalidade_padrao"
    ] == "imagem-video"
    (wan,) = fins["imagem-video"]["modelos"]
    assert wan["id"] == "wan2.2-i2v" and wan["melhor_para"] and "Apache 2.0" in wan["licenca"]
    assert all(f["modelos"] == [] for i, f in fins.items() if i != "imagem-video")
    # previstos só com licença comercial conferida (docs/12): nenhum no áudio para imagem
    assert fins["texto-video"]["previstos"][0]["nome"] == "Wan 2.2 (texto para vídeo)"
    assert fins["audio-imagem"]["previstos"] == [] and fins["video-audio"]["previstos"] == []
    assert set(r["entradas"]) == {f["entrada"] for f in fins.values()} and r["interesses"] == []

    def marcar(token, fin):
        return api.post("/api/interesses", json={"finalidade": fin}, headers=h(token))

    assert api.post("/api/interesses", json={"finalidade": "texto-video"}).status_code == 401
    assert marcar(tok, "nao-existe").status_code == 422
    assert marcar(tok, "imagem-video").json()["detail"] == "esta finalidade já está disponível"
    assert marcar(tok, "texto-video").json() == {"ok": True, "novo": True}
    assert marcar(tok, "texto-video").json() == {"ok": True, "novo": False}  # o mesmo cliente conta uma vez
    assert marcar(tok_teste, "texto-video").json()["novo"] is True
    assert marcar(tok, "audio-video").json()["novo"] is True
    assert api.get("/api/receitas", headers=h(tok)).json()["interesses"] == ["texto-video", "audio-video"]
    assert anonimo(api).get(f"{PAINEL}/api/interesses", headers=h(tok)).status_code == 401
    r = api.get(f"{PAINEL}/api/interesses", headers=ADM).json()
    lista = r["resumo"]
    assert [(x["finalidade"], x["clientes"], x["clientes_teste"]) for x in lista] == [
        ("texto-video", 2, 1),
        ("audio-video", 1, 0),
    ]
    assert lista[0]["titulo"] == "Vídeo a partir de um texto" and lista[0]["tipo"] == "Texto para vídeo"
    # cada clique chega ao painel: conta, plano e quando (o mais recente primeiro)
    cliques = r["cliques"]
    assert [(x["finalidade"], x["nome"], x["plano"]) for x in cliques] == [
        ("audio-video", "Loja Interesse", "pago"),
        ("texto-video", "Teste Interesse", "teste"),
        ("texto-video", "Loja Interesse", "pago"),
    ]
    assert all(x["quando"] and x["titulo"] for x in cliques)


def test_upload_urls_accept_only_the_declared_size_and_type(api):
    tok = _cliente(api)
    r = _lote(api, tok, n=2)
    assert r.status_code == 200
    assinados = api.armazem.assinados
    assert sorted(v["bytes"] for v in assinados.values()) == [1000, 1001]  # o R2 recusa arquivo de outro tamanho
    assert {v["tipo"] for v in assinados.values()} == {"image/jpeg"}


def test_the_real_ip_comes_from_the_proxy_header_and_ipv6_counts_by_network(api, monkeypatch):
    """O Caddy sobrescreve X-Render2Go-IP com o IP real; o teste grátis conta IPv6 pelo /64 (trocar de
    endereço dentro da mesma rede não dá outro teste)."""
    monkeypatch.setitem(api_mod.TESTE, "fotos_ip", 5)
    rede = "2001:db8:abcd:12::"
    feitos = []
    for i, disp in enumerate((D1, D2)):
        tok = _cliente(api, f"Teste IPv6 {i}", plano="teste")
        hd = {**h(tok), "X-Render2Go-IP": f"{rede}{i + 1}", "X-Render2Go-Porta": "51234", "X-Dispositivo": disp}
        corpo = {
            "prompt": "a câmera se aproxima",
            "fotos": [{"nome": f"f{j}.jpg", "tipo": "image/jpeg", "bytes": 10 + j} for j in range(3)],
        }
        r = api.post("/api/lotes", json=corpo, headers=hd)
        feitos.append(r)
        if r.status_code == 200:  # as fotos chegam com o tamanho declarado e o pedido passa do envio
            for e in r.json()["envios"]:
                api.armazem.objetos[e["url"].split("armazem.teste/")[1].split("?")[0]] = 10 + int(e["original"][1])
            assert api.post(f"/api/lotes/{r.json()['lote']}/enviado", headers=hd).status_code == 200
    assert feitos[0].status_code == 200
    assert feitos[1].status_code == 429 and "rede" in feitos[1].json()["detail"]  # mesmo /64, outro endereço
    lid = feitos[0].json()["lote"]
    with api.banco.tx() as c:
        lt = c.one("SELECT ip, rede, porta FROM lotes WHERE id = ?", (lid,))
    assert (lt["ip"], lt["rede"], lt["porta"]) == (f"{rede}1", "2001:db8:abcd:12::/64", 51234)


def test_the_frame_filter_is_required_and_a_blocked_video_never_reaches_the_client(api):
    """ADR 0005: "ok" sem a nota do filtro de imagem volta para a fila; com nota alta, o vídeo é apagado,
    não é refeito e volta para o saldo. Nada explícito no teste: só as notas que o worker mandaria."""
    tok = _cliente(api, "Loja", saldo="10")
    r = _lote(api, tok, n=2, duracao=4)
    lid = r.json()["lote"]
    assert _enviar_tudo(api, tok, lid, r.json()["envios"]).status_code == 200
    api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM)
    comp = _job(api)
    api.post(
        f"/api/worker/jobs/{comp['job_id']}/prompt",
        headers=h(WORKER),
        json={"worker": "pod-1", "prompt_en": "The camera slowly dollies in.", "compilador": "i2v@6"},
    )
    _concluir(api, comp, status="ok")
    sem_filtro = {k: v for k, v in CONTINUO.items() if not k.startswith("nsfw")}
    render = _job(api)
    chave = render["video_url"].split("armazem.teste/")[1].split("?")[0]
    api.armazem.objetos[chave] = 7000
    r = _concluir(api, render, status="ok", bytes_video=7000, similaridade=0.99, **sem_filtro)
    assert r.json()["status"] == "pendente" and chave in api.armazem.apagados  # fail-closed
    # nota acima do limiar mesmo com "ok" (worker de outra versão): barrado pelo servidor
    render = _job(api)
    api.armazem.objetos[chave] = 7000
    r = _concluir(
        api, render, status="ok", bytes_video=7000, similaridade=0.99, **sem_filtro, nsfw=0.8, nsfw_quadros=13
    )
    assert r.json()["status"] == "erro"  # sem repetição
    # o outro vídeo: o worker barrou e não enviou nada
    outro = _job(api)
    r = _concluir(api, outro, status="erro", erro="barrado", barrado=True, nsfw=0.97, nsfw_quadros=13)
    assert r.json()["status"] == "erro"
    jobs = [j for j in api.banco.jobs(lid) if j["tipo"] == "render"]
    assert all(j["barrado_em"] and j["nsfw"] >= 0.8 and "barrado pelo filtro de imagem" in j["erro"] for j in jobs)
    lote = api.get(f"/api/lotes/{lid}", headers=h(tok)).json()
    assert lote["status"] == "concluido_com_falhas" and lote["prontos"] == 0
    assert lote["cobranca"] == {"cobrado": 200, "devolvido": 200}  # como todo vídeo que não sai
    assert "barrado" not in json.dumps(lote) and not any(v.get("url") for v in lote["videos"])
    # o dono vê no histórico e no registro de auditoria
    (x,) = [x for x in api.get(f"{PAINEL}/api/historico", headers=ADM).json()["lotes"] if x["lote"] == lid]
    assert x["barrados"] == 2
    aud = api.get(f"{PAINEL}/api/lotes/{lid}/auditoria", headers=ADM).json()
    assert sum(e["tipo"] == "video_barrado" for e in aud["eventos"]) == 2
