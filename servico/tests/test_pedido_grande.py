"""Pedido grande (12/10/2026, ADR 0012): sem limite de fotos no plano pago e no interno.

O pedido maior que um envio (FOTOS_POR_ENVIO) vai em partes; a página do cliente recebe os primeiros vídeos e
pede os seguintes; o painel mostra ao revisor uma amostra das fotos com os totais do pedido inteiro."""

import json

from test_api import ADM, PAINEL, WORKER, _cliente, api, h  # noqa: F401 (fixture api)

from render2go_servico import api as api_mod

PEDIDO = "a câmera se aproxima devagar do produto"


def _fotos(inicio, n, **extra):
    return [
        {"nome": f"foto {i}.jpg", "tipo": "image/jpeg", "bytes": 1000 + i, **extra} for i in range(inicio, inicio + n)
    ]


def _subir(a, envios):
    """Põe no armazenamento falso cada foto com os bytes que a URL assinada exige."""
    for e in envios:
        chave = e["url"].split("armazem.teste/")[1].split("?")[0]
        a.armazem.objetos[chave] = a.armazem.assinados[chave]["bytes"]


def test_a_big_order_goes_in_parts_without_a_photo_limit(api, monkeypatch):  # noqa: F811 (fixture)
    monkeypatch.setattr(api_mod, "FOTOS_POR_ENVIO", 3)
    tok = _cliente(api, "Atacado")
    assert api.get("/api/receitas", headers=h(tok)).json()["limites"]["fotos"] is None  # sem limite
    assert api.get("/api/receitas", headers=h(tok)).json()["limites"]["por_envio"] == 3
    # mais que um envio de uma vez: recusado, com a orientação de mandar em partes
    r = api.post("/api/lotes", json={"prompt": PEDIDO, "fotos": _fotos(0, 4)}, headers=h(tok))
    assert r.status_code == 422 and "em partes" in r.json()["detail"]
    r = api.post("/api/lotes", json={"prompt": PEDIDO, "fotos": _fotos(0, 3)}, headers=h(tok))
    assert r.status_code == 200
    lid, envios = r.json()["lote"], r.json()["envios"]
    for inicio, n in ((3, 3), (6, 2)):
        r = api.post(f"/api/lotes/{lid}/fotos", json={"fotos": _fotos(inicio, n)}, headers=h(tok))
        assert r.status_code == 200, r.text
        envios += r.json()["envios"]
    # o mesmo nome de arquivo de novo vira outro nome no pedido (nada é sobrescrito)
    r = api.post(f"/api/lotes/{lid}/fotos", json={"fotos": _fotos(0, 1)}, headers=h(tok))
    envios += r.json()["envios"]
    assert len(envios) == 9 and len({e["url"].split("?")[0] for e in envios}) == 9
    assert api.banco.lote(lid)["n_fotos"] == 9
    # outra conta não mexe no pedido; parte maior que um envio é recusada
    outra = h(_cliente(api, "Outra"))
    assert api.post(f"/api/lotes/{lid}/fotos", json={"fotos": _fotos(9, 1)}, headers=outra).status_code == 404
    assert api.post(f"/api/lotes/{lid}/fotos", json={"fotos": _fotos(9, 4)}, headers=h(tok)).status_code == 422
    _subir(api, envios)
    assert api.post(f"/api/lotes/{lid}/enviado", headers=h(tok)).status_code == 200
    # enviado: as fotos novas vão num pedido novo
    r = api.post(f"/api/lotes/{lid}/fotos", json={"fotos": _fotos(20, 1)}, headers=h(tok))
    assert r.status_code == 409
    api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM)
    renders = [j for j in api.banco.jobs(lid) if j["tipo"] == "render"]
    assert len(renders) == 9


def test_the_free_trial_keeps_its_limit_and_cannot_add_parts(api):  # noqa: F811 (fixture)
    tok = _cliente(api, "Teste grande", plano="teste")
    r = api.post("/api/lotes", json={"prompt": PEDIDO, "fotos": _fotos(0, 2)}, headers=h(tok))
    lid = r.json()["lote"]
    r = api.post(f"/api/lotes/{lid}/fotos", json={"fotos": _fotos(2, 1)}, headers=h(tok))
    assert r.status_code == 429 and "teste gratuito" in r.json()["detail"]


def test_paid_accounts_have_no_daily_photo_quota(api):  # noqa: F811 (fixture)
    """A cota diária (500 fotos por conta) fica só no teste gratuito (pedido do dono, 12/10/2026)."""
    tok = _cliente(api, "Atacado")
    for _ in range(2):  # 2 × 300 fotos no mesmo dia
        r = api.post("/api/lotes", json={"prompt": PEDIDO, "fotos": _fotos(0, 300)}, headers=h(tok))
        assert r.status_code == 200, r.text


def test_parts_add_texts_and_the_authorization_lists_every_photo(api, monkeypatch):  # noqa: F811 (fixture)
    monkeypatch.setattr(api_mod, "FOTOS_POR_ENVIO", 2)
    monkeypatch.setattr(api_mod, "MAX_TEXTOS_FOTO", 3)
    tok = _cliente(api, "Moda")
    corpo = {
        "prompt": PEDIDO,
        "receita": "pessoa-apresentacao",
        "consentimento": True,
        "declarante": "Dona da loja",
        "fotos": _fotos(0, 2, prompt="a modelo sorri para a câmera"),
    }
    r = api.post("/api/lotes", json=corpo, headers=h(tok))
    assert r.status_code == 200, r.text
    lid = r.json()["lote"]
    r = api.post(f"/api/lotes/{lid}/fotos", json={"fotos": _fotos(2, 2, prompt="a modelo acena")}, headers=h(tok))
    assert r.status_code == 200
    assert json.loads(api.banco.lote(lid)["pedido"])["textos_por_foto"] == 2
    # o filtro de conteúdo vale também nas partes, e o teto de textos diferentes conta o pedido inteiro
    arma = _fotos(4, 1, prompt="um homem segura uma arma")
    r = api.post(f"/api/lotes/{lid}/fotos", json={"fotos": arma}, headers=h(tok))
    assert r.status_code == 422
    r = api.post(
        f"/api/lotes/{lid}/fotos",
        json={"fotos": [*_fotos(4, 1, prompt="a modelo gira"), *_fotos(5, 1, prompt="a modelo pula")]},
        headers=h(tok),
    )
    assert r.status_code == 422 and "no máximo 3 textos" in r.json()["detail"]
    aut = api.banco.auditoria(lid)["autorizacao"]
    assert len(json.loads(aut["fotos"])) == 4  # o registro da autorização lista as fotos das partes também


def _pedido_pronto(api, tok, n):  # noqa: F811 (fixture)
    """Pedido de n fotos, aprovado e com todos os vídeos prontos (direto no banco, como um pod faria)."""
    r = api.post("/api/lotes", json={"prompt": PEDIDO, "fotos": _fotos(0, n)}, headers=h(tok))
    lid = r.json()["lote"]
    _subir(api, r.json()["envios"])
    assert api.post(f"/api/lotes/{lid}/enviado", headers=h(tok)).status_code == 200
    api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM)
    with api.banco.tx() as c:
        c.run("UPDATE jobs SET status = 'ok' WHERE lote_id = ?", (lid,))
        c.run("UPDATE lotes SET status = 'concluido' WHERE id = ?", (lid,))
    return lid


def test_a_big_order_lists_the_first_videos_and_the_rest_comes_in_pages(api, monkeypatch):  # noqa: F811 (fixture)
    monkeypatch.setattr(api_mod, "VIDEOS_POR_PAGINA", 4)
    tok = _cliente(api, "Atacado", plano="interno")
    lid = _pedido_pronto(api, tok, 10)
    with api.banco.tx() as c:  # um falhou e um foi excluído pelo cliente
        c.run("UPDATE jobs SET status = 'erro' WHERE lote_id = ? AND arquivo LIKE ?", (lid, "foto-9%"))
        c.run("UPDATE jobs SET excluido_em = 1 WHERE lote_id = ? AND arquivo LIKE ?", (lid, "foto-8%"))
    (lote,) = api.get("/api/lotes", headers=h(tok)).json()
    assert (lote["total"], lote["prontos"], lote["falhas"], lote["excluidos"]) == (10, 9, 1, 1)
    assert len(lote["videos"]) == 4 and all(v["url"] for v in lote["videos"]) and lote["videos_mais"] == 4
    p1 = api.get(f"/api/lotes/{lid}/videos?depois=4", headers=h(tok)).json()
    p2 = api.get(f"/api/lotes/{lid}/videos?depois=8", headers=h(tok)).json()
    assert (len(p1["videos"]), p1["mais"], len(p2["videos"]), p2["mais"]) == (4, 0, 0, 0)
    vistos = [v["arquivo"] for v in lote["videos"] + p1["videos"]]
    assert vistos == sorted(vistos) and len(set(vistos)) == 8  # na ordem da lista, sem repetir
    assert api.get(f"/api/lotes/{lid}/videos", headers=h(_cliente(api, "Outra"))).status_code == 404


def test_the_reviewer_sees_a_sample_of_a_big_order_with_the_whole_order_totals(api, monkeypatch):  # noqa: F811 (fixture)
    monkeypatch.setattr(api_mod, "PREVIA_REVISAO", 3)
    tok = _cliente(api, "Atacado")
    r = api.post("/api/lotes", json={"prompt": PEDIDO, "fotos": _fotos(0, 8, prompt="o vapor sobe da xícara")},
                 headers=h(tok))  # fmt: skip
    lid = r.json()["lote"]
    _subir(api, r.json()["envios"])
    api.post(f"/api/lotes/{lid}/enviado", headers=h(tok))
    fotos = api.banco.fotos(lid)
    api.banco.salvar_analise(fotos[6]["id"], {"versao": "pessoas-v1", "pessoas": 2, "rostos": 1, "maior": 0.3}, "x")
    for f in fotos[:3]:
        api.banco.salvar_analise(f["id"], {"versao": "pessoas-v1", "pessoas": 0, "rostos": 0, "maior": 0.0}, "y")
    (p,) = [x for x in api.get(f"{PAINEL}/api/lotes", headers=ADM).json() if x["lote"] == lid]
    assert [f["nome"] for f in p["fotos"]] == [fotos[i]["nome"] for i in (0, 1, 2, 6)]  # as 3 primeiras e a marcada
    assert (p["fotos_total"], p["fotos_amostra"], p["fotos_com_pessoas"], p["fotos_analisando"]) == (8, True, 1, 4)
    assert p["fotos_com_texto"] == 8
    assert p["textos"] == [{"texto": "o vapor sobe da xícara", "fotos": 8, "filtro_c1": "C1: ok"}]
