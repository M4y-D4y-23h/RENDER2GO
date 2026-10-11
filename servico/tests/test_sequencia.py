"""Pedido em sequência (10/10/2026): um texto padrão para todas as fotos e textos próprios em algumas.

O worker do pod não muda: a compilação de um texto próprio é um job `compilar` como o do pedido, com o
texto da foto no lugar do texto padrão; o render da foto recebe o prompt compilado dela."""

from test_api import ADM, PAINEL, WORKER, _cliente, _enviar_tudo, _job, api, h  # noqa: F401 (fixture api)

from render2go_servico import api as api_mod

TEXTO = "o café nesta foto vira um café com leite"


def _sequencia(a, tok, textos):
    corpo = {
        "prompt": "a câmera se aproxima devagar do produto",
        "fotos": [
            {"nome": f"foto {i}.jpg", "tipo": "image/jpeg", "bytes": 1000 + i, "prompt": t}
            for i, t in enumerate(textos)
        ],
    }
    return a.post("/api/lotes", json=corpo, headers=h(tok))


def _prompt(a, job, texto_en):
    r = a.post(
        f"/api/worker/jobs/{job['job_id']}/prompt",
        headers=h(WORKER),
        json={"worker": "pod-1", "prompt_en": texto_en, "compilador": "i2v@5"},
    )
    return r


def _concluir(a, job, status="ok", **extra):
    return a.post(
        f"/api/worker/jobs/{job['job_id']}/concluir",
        headers=h(WORKER),
        json={"worker": "pod-1", "status": status, **extra},
    )


def test_photo_texts_are_reviewed_compiled_once_per_distinct_text_and_used_only_by_their_photos(api):  # noqa: F811 (fixture)
    tok = _cliente(api, "Cafeteria")
    # foto 0 segue o padrão; 1 e 2 têm o mesmo texto próprio (uma compilação só); espaços não contam
    r = _sequencia(api, tok, [None, TEXTO, f"  {TEXTO}  "])
    assert r.status_code == 200, r.text
    lid = r.json()["lote"]
    assert _enviar_tudo(api, tok, lid, r.json()["envios"]).status_code == 200

    # o revisor vê o texto de cada foto (e o filtro de conteúdo dele) antes de aprovar
    (pend,) = api.get(f"{PAINEL}/api/lotes", headers=ADM).json()
    assert [(f["nome"], f["texto"], f["filtro_c1"]) for f in pend["fotos"]] == [
        ("foto-0", None, None),
        ("foto-1", TEXTO, "C1: ok"),
        ("foto-2", TEXTO, "C1: ok"),
    ]
    assert pend["pedido"]["textos_por_foto"] == 1
    assert api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM).json()["videos"] == 3

    # 1º: a compilação do texto padrão (passa na frente, como sempre)
    comp = _job(api)
    assert comp["tipo"] == "compilar" and comp["pedido"]["prompt"] == "a câmera se aproxima devagar do produto"
    assert _prompt(api, comp, "The camera slowly dollies in.").status_code == 200
    _concluir(api, comp)
    # a foto sem texto próprio já pode renderizar; a compilação do texto vem logo antes das fotos dele
    r0 = _job(api)
    assert (r0["tipo"], r0["arquivo"], r0["prompt_en"]) == ("render", "foto-0-v1.mp4", "The camera slowly dollies in.")
    comp_foto = _job(api)
    assert comp_foto["tipo"] == "compilar" and comp_foto["pedido"]["prompt"] == TEXTO
    assert _job(api) is None  # os renders das fotos 1 e 2 esperam o prompt delas
    assert _prompt(api, comp_foto, "The coffee turns into a latte.").status_code == 200
    _concluir(api, comp_foto)
    r1, r2 = _job(api), _job(api)
    assert [(x["arquivo"], x["prompt_en"]) for x in (r1, r2)] == [
        ("foto-1-v1.mp4", "The coffee turns into a latte."),
        ("foto-2-v1.mp4", "The coffee turns into a latte."),
    ]
    assert _job(api) is None

    # o cliente vê quais vídeos usaram um texto próprio (no pedido e na lista); a cópia dos dados (LGPD) traz os textos
    lote = api.get(f"/api/lotes/{lid}", headers=h(tok)).json()
    assert [v["texto"] for v in lote["videos"]] == [None, TEXTO, TEXTO]
    (na_lista,) = api.get("/api/lotes", headers=h(tok)).json()
    assert [v["texto"] for v in na_lista["videos"]] == [None, TEXTO, TEXTO]
    auditoria = api.get(f"{PAINEL}/api/lotes/{lid}/auditoria", headers=ADM).json()
    assert [f["texto"] for f in auditoria["fotos"]] == [None, TEXTO, TEXTO]
    dados = api.banco.dados_da_conta(api.banco.lote(lid)["cliente_id"])
    assert [(t["nome"], t["texto"]) for t in dados["textos_por_foto"]] == [("foto-1", TEXTO), ("foto-2", TEXTO)]


def test_photo_texts_pass_the_same_checks_as_the_main_text(api, monkeypatch):  # noqa: F811 (fixture)
    tok = _cliente(api)
    r = _sequencia(api, tok, [None, "um homem segura uma arma"])
    assert r.status_code == 422 and "foto 1.jpg" in r.json()["detail"] and "filtro" in r.json()["detail"]
    r = _sequencia(api, tok, ["ok"])
    assert r.status_code == 422 and "3 letras" in r.json()["detail"]
    r = _sequencia(api, tok, ["x" * 601])
    assert r.status_code == 422
    # o mesmo texto do padrão não vira texto próprio (nem uma compilação a mais)
    r = _sequencia(api, tok, ["a câmera se aproxima devagar do produto"])
    assert r.status_code == 200 and "textos_por_foto" not in api.banco.lote(r.json()["lote"])["pedido"]
    monkeypatch.setattr(api_mod, "MAX_TEXTOS_FOTO", 2)
    assert _sequencia(api, tok, ["texto um", "texto dois", "texto um"]).status_code == 200
    r = _sequencia(api, tok, ["texto um", "texto dois", "texto três"])
    assert r.status_code == 422 and "no máximo 2 textos" in r.json()["detail"]


def test_a_photo_text_refused_after_compiling_cancels_and_refunds_only_its_photos(api):  # noqa: F811 (fixture)
    tok = _cliente(api, "Restaurante", saldo="10")
    r = _sequencia(api, tok, [None, "o prato fica cheio de vapor quente"])
    lid = r.json()["lote"]
    _enviar_tudo(api, tok, lid, r.json()["envios"])
    saldo_antes = api.get("/api/saldo", headers=h(tok)).json()["saldo"]
    api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM)
    comp = _job(api)
    _prompt(api, comp, "The camera slowly dollies in.")
    _concluir(api, comp)
    r0 = _job(api)
    comp_foto = _job(api)
    assert comp_foto["tipo"] == "compilar"
    # o prompt compilado do texto próprio cai no filtro: só a foto 1 é cancelada e devolvida
    assert _prompt(api, comp_foto, "a man holds a gun").status_code == 422
    assert _job(api) is None
    chave = r0["video_url"].split("armazem.teste/")[1].split("?")[0]
    api.armazem.objetos[chave] = 7000
    ok = {
        "bytes_video": 7000,
        "similaridade": 0.999,
        "deriva_cor": 6.5,
        "salto_cor": 1.2,
        "nsfw": 0.01,
        "nsfw_quadros": 13,
    }
    assert _concluir(api, r0, **ok).json()["status"] == "ok"
    resp = api.get(f"/api/lotes/{lid}", headers=h(tok))
    lote = resp.json()
    assert lote["status"] == "concluido_com_falhas"
    # recusa neutra (decisão do dono, 11/10/2026): o cliente não vê o motivo interno da recusa
    assert not any("erro" in v for v in lote["videos"]) and "filtro" not in resp.text
    assert [(v["arquivo"], v["status"]) for v in lote["videos"]] == [
        ("foto-0-v1.mp4", "ok"),
        ("foto-1-v1.mp4", "cancelado"),
    ]
    assert lote["cobranca"] == {"cobrado": 250, "devolvido": 125}
    assert api.get("/api/saldo", headers=h(tok)).json()["saldo"] == saldo_antes + 125
