"""Versões do pedido e revisão pelo cliente (ADR 0018, 17/10/2026): a versão liberada é a única que vai para os pods
(no modo "revisar", só depois da aprovação do cliente; no "seguir", sozinha, menos num passo material), a aprovação vale
para uma versão só (número e hash), a edição volta para a revisão de conteúdo e a guarda final nunca gera outra versão
calada. Nos dois motores; a concorrência, no Postgres. SFW."""

import json
import threading
import time

import pytest
from render2go_worker import lote as wlote
from render2go_worker import traducao
from test_api import (  # noqa: F401 (fixture api)
    ADM,
    PAINEL,
    WORKER,
    _cliente,
    _enviar_tudo,
    _lote,
    api,
    cliente_teste,
    config_teste,
    h,
)

from render2go_servico import versoes
from render2go_servico.db import PRAZO_FOTOS_S, BancoError, VersaoMudou

GIRO = "The product slowly rotates in place. Static camera."
PESSOA = "She stays in place while soft light glides across her. Static camera."


def _pedido(a, tok=None, n=2, **pedido):
    tok = tok or _cliente(a)
    r = _lote(a, tok, n=n, **pedido)
    assert r.status_code == 200, r.text
    lid = r.json()["lote"]
    assert _enviar_tudo(a, tok, lid, r.json()["envios"]).status_code == 200
    return tok, lid


def _aprovar_conteudo(a, lid):
    assert a.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM).status_code == 200


def _job(a, worker="pod-1"):
    r = a.post("/api/worker/reivindicar", json={"worker": worker}, headers=h(WORKER))
    assert r.status_code == 200, r.text
    return r.json()["job"]


def _prompt(a, job, texto, worker="pod-1", **extra):
    corpo = {"worker": worker, "prompt_en": texto, "compilador": "i2v@6", "tentativa": job["tentativa"],
             "intencao": "acao", "restricoes": [], **extra}  # fmt: skip
    return a.post(f"/api/worker/jobs/{job['job_id']}/prompt", headers=h(WORKER), json=corpo)


def _concluir(a, job, worker="pod-1"):
    corpo = {"worker": worker, "status": "ok", "tentativa": job["tentativa"]}
    r = a.post(f"/api/worker/jobs/{job['job_id']}/concluir", headers=h(WORKER), json=corpo)
    assert r.status_code == 200, r.text


def _traduzir(a, texto=GIRO, **extra):
    comp = _job(a)
    assert comp is not None and comp["tipo"] == "compilar", comp
    assert _prompt(a, comp, texto, **extra).status_code == 200
    _concluir(a, comp)


def _cartao(a, tok, lid):
    r = a.get(f"/api/lotes/{lid}", headers=h(tok))
    assert r.status_code == 200, r.text
    return r.json()


def _aprovar(a, tok, lid, numero=None, hash_=None):
    e = _cartao(a, tok, lid)["entendimento"]["versao"]
    corpo = {"numero": numero or e["numero"], "hash": hash_ or e["hash"]}
    return a.post(f"/api/lotes/{lid}/versao/aprovar", json=corpo, headers=h(tok))


def _renders_na_fila(a) -> int:
    return sum(int(t["pendentes"] or 0) for t in a.banco.trabalho())


# -- modo "seguir" (o padrão): nada muda para quem não pede a revisão ------------------------------


def test_follow_mode_releases_each_version_by_itself_and_the_video_records_what_it_ran(api):  # noqa: F811
    tok, lid = _pedido(api)
    lt = api.banco.lote(lid)
    assert lt["revisao"] == "seguir" and lt["versao_atual"] and lt["versao_liberada"] == lt["versao_atual"]
    _aprovar_conteudo(api, lid)
    _traduzir(api)
    render = _job(api)
    assert render["tipo"] == "render" and render["prompt_en"] == GIRO
    with api.banco.tx() as c:
        j = c.one("SELECT versao_id, prompt_sha FROM jobs WHERE id = ?", (render["job_id"],))
    assert j["versao_id"] == api.banco.lote(lid)["versao_liberada"] and j["prompt_sha"] == versoes.sha(GIRO)
    card = _cartao(api, tok, lid)["entendimento"]
    assert card["versao"]["numero"] == 2 and card["versao"]["liberada_por"] == "automatico"
    assert card["grupos"][0]["prompt_en"] == GIRO and card["grupos"][0]["tipo"] == "acao"
    assert card["aprovar"] is False and not card["decisoes"]


def test_an_order_without_versions_keeps_working_as_before(banco):
    cid, _ = banco.criar_cliente("Antiga", 500, "interno")
    lid = banco.criar_lote(cid, {"prompt": "o produto gira", "receita": "livre", "variacoes": 1},
                           [{"nome": "a", "tipo": "image/jpeg", "bytes": 10, "chave": "{lote}/a.jpg"}])  # fmt: skip
    assert banco.confirmar_envio(lid, {f["chave"]: 10 for f in banco.fotos(lid)}) == []
    assert banco.lote(lid)["versao_atual"] is None
    f = banco.fotos(lid)[0]
    banco.aprovar(lid, "x", [{"foto_id": f["id"], "variacao": 1, "seed": 1, "arquivo": "a-v1.mp4"}])
    comp = banco.reivindicar("pod")
    banco.definir_prompt(lid, GIRO, "i2v@6")
    banco.concluir(comp["id"], "pod", "ok", tentativa=comp["tentativa_id"])
    assert banco.reivindicar("pod")["tipo"] == "render"


# -- modo "revisar": o vídeo só sai com a versão aprovada pelo cliente --------------------------------


def test_review_mode_blocks_every_video_until_the_client_approves_the_final_text(api):  # noqa: F811
    tok, lid = _pedido(api, revisao="revisar")
    _aprovar_conteudo(api, lid)
    r = _aprovar(api, tok, lid)
    assert r.status_code == 409 and "preparado" in r.json()["detail"]  # sem o texto final, nada para aprovar
    _traduzir(api)
    assert _job(api) is None, "o vídeo não pode ir para um pod antes da aprovação do cliente"
    assert _renders_na_fila(api) == 0 and api.banco.fila()["pendentes"] == 0  # nem liga pod
    pedido = _cartao(api, tok, lid)
    assert pedido["espera_cliente"] and pedido["previsao"] == {"fase": "revisao", "pronto": True}
    card = pedido["entendimento"]
    assert card["aprovar"] and card["traduzido"] and card["grupos"][0]["prompt_en"] == GIRO
    assert card["versao"]["liberada"] is False
    assert _aprovar(api, tok, lid).status_code == 200
    assert _aprovar(api, tok, lid).json()["ja"] is True  # a 2ª aprovação (outra aba) não muda nada
    render = _job(api)
    assert render["tipo"] == "render" and render["prompt_en"] == GIRO
    card = _cartao(api, tok, lid)["entendimento"]
    assert card["versao"]["liberada_por"] == "cliente" and card["aprovar"] is False


def test_an_obsolete_or_wrong_approval_changes_nothing(api):  # noqa: F811
    tok, lid = _pedido(api, revisao="revisar", n=2)
    _aprovar_conteudo(api, lid)
    vista = _cartao(api, tok, lid)["entendimento"]["versao"]  # a aba 1 abre antes da tradução
    _traduzir(api)
    r = _aprovar(api, tok, lid, numero=vista["numero"], hash_=vista["hash"])
    assert r.status_code == 409 and r.headers.get("x-render2go-motivo") == "versao"
    atual = _cartao(api, tok, lid)["entendimento"]["versao"]
    assert _aprovar(api, tok, lid, numero=atual["numero"], hash_="0" * 64).status_code == 409
    assert api.banco.lote(lid)["versao_liberada"] is None and _job(api) is None
    # outra conta não aprova nem vê
    outro = _cliente(api, nome="Outra")
    corpo = {"numero": atual["numero"], "hash": atual["hash"]}
    assert api.post(f"/api/lotes/{lid}/versao/aprovar", json=corpo, headers=h(outro)).status_code == 404
    assert api.get(f"/api/lotes/{lid}", headers=h(outro)).status_code == 404


def test_editing_the_text_invalidates_the_approval_and_goes_back_to_content_review(api):  # noqa: F811
    tok, lid = _pedido(api, revisao="revisar")
    _aprovar_conteudo(api, lid)
    _traduzir(api)
    vista = _cartao(api, tok, lid)["entendimento"]["versao"]
    assert _aprovar(api, tok, lid).status_code == 200  # aprovada, mas nenhum vídeo começou
    corpo = {"numero": vista["numero"], "hash": vista["hash"], "grupo": "pedido", "texto": "a câmera gira em volta"}
    r = api.post(f"/api/lotes/{lid}/texto", json=corpo, headers=h(tok))
    assert r.status_code == 200, r.text
    lt = api.banco.lote(lid)
    assert lt["status"] == "aguardando_aprovacao" and lt["prompt_en"] is None and lt["versao_liberada"] is None
    assert json.loads(lt["pedido"])["prompt"] == "a câmera gira em volta"
    assert api.banco.jobs(lid) == []  # nada sobrou para um pod
    card = _cartao(api, tok, lid)["entendimento"]
    assert card["versao"]["editada"] and card["versao"]["anterior"]["texto"].startswith("a câmera se aproxima")
    # a versão de antes não vale mais (a aba que ainda a mostra recebe 409)
    assert api.post(f"/api/lotes/{lid}/texto", json=corpo, headers=h(tok)).status_code == 409
    _aprovar_conteudo(api, lid)
    _traduzir(api, "The camera orbits around the product.")
    assert _job(api) is None  # a versão nova também espera o cliente
    assert _aprovar(api, tok, lid).status_code == 200
    assert _job(api)["prompt_en"] == "The camera orbits around the product."


def test_the_text_cannot_change_after_a_video_started(api):  # noqa: F811
    tok, lid = _pedido(api, revisao="revisar")
    _aprovar_conteudo(api, lid)
    _traduzir(api)
    assert _aprovar(api, tok, lid).status_code == 200
    assert _job(api)["tipo"] == "render"  # um vídeo já está num pod
    v = _cartao(api, tok, lid)["entendimento"]["versao"]
    corpo = {"numero": v["numero"], "hash": v["hash"], "grupo": "pedido", "texto": "a câmera gira em volta"}
    r = api.post(f"/api/lotes/{lid}/texto", json=corpo, headers=h(tok))
    assert r.status_code == 409 and "pare o pedido" in r.json()["detail"]


def test_a_review_order_waiting_for_the_client_does_not_hold_the_next_order_of_the_account(api):  # noqa: F811
    tok, antigo = _pedido(api, revisao="revisar", n=1)
    _aprovar_conteudo(api, antigo)
    _traduzir(api)
    _, novo = _pedido(api, tok=tok, n=1)
    _aprovar_conteudo(api, novo)
    _traduzir(api)
    r = _job(api)
    assert r["tipo"] == "render" and r["lote"] == novo  # o pedido novo não espera o que está com o cliente
    assert _aprovar(api, tok, antigo).status_code == 200
    assert _job(api)["lote"] == antigo


def test_a_review_order_without_an_answer_expires_without_cost(api):  # noqa: F811
    tok, lid = _pedido(api, revisao="revisar", n=1)
    _aprovar_conteudo(api, lid)
    _traduzir(api)
    with api.banco.tx() as c:
        c.run("UPDATE lotes SET criado = ? WHERE id = ?", (time.time() - PRAZO_FOTOS_S - 60, lid))
    assert api.banco.vencer_pedidos() == 1
    lt = api.banco.lote(lid)
    assert lt["status"] == "concluido_com_falhas" and api.banco.cobranca(lid)["cobrado"] == 0


# -- mudança material: nem no modo "seguir" a falta de resposta é aprovação -------------------------


def test_an_action_replaced_by_the_default_waits_for_the_client_even_in_follow_mode(api):  # noqa: F811
    tok, lid = _pedido(api, prompt="gire lentamente o produto", receita="livre")
    _aprovar_conteudo(api, lid)
    _traduzir(api, "The main subject stays in place as soft light slowly glides across it.", substituiu=True,
              padrao=True)  # fmt: skip
    assert _job(api) is None and _renders_na_fila(api) == 0
    pedido = _cartao(api, tok, lid)
    assert pedido["previsao"]["fase"] == "decisao" and pedido["espera_cliente"]
    card = pedido["entendimento"]
    assert card["aprovar"] and "não chegou ao texto do gerador" in card["decisoes"][0]["motivo"]
    assert _aprovar(api, tok, lid).status_code == 200  # o cliente decide seguir com esta adaptação
    assert _job(api)["tipo"] == "render"


def test_a_restriction_an_old_pod_did_not_apply_waits_for_the_client(api):  # noqa: F811
    tok, lid = _pedido(api, prompt="Mantenha tudo imóvel, sem movimento.", receita="livre")
    _aprovar_conteudo(api, lid)
    comp = _job(api)
    # o pod de antes de 17/10/2026: sem intenção nem restrições, com o movimento padrão que mexe a câmera
    corpo = {"worker": "pod-1", "prompt_en": "The main subject stays in place. Slow, gentle camera push-in.",
             "compilador": "i2v@6", "tentativa": comp["tentativa"], "vago": True, "padrao": True}  # fmt: skip
    assert api.post(f"/api/worker/jobs/{comp['job_id']}/prompt", headers=h(WORKER), json=corpo).status_code == 200
    _concluir(api, comp)
    assert _job(api) is None
    assert "restrição" in _cartao(api, tok, lid)["entendimento"]["decisoes"][0]["motivo"]


def test_a_restricted_translation_from_a_current_pod_goes_on_by_itself(api):  # noqa: F811
    tok, lid = _pedido(api, prompt="Mantenha tudo imóvel, sem movimento.", receita="livre")
    _aprovar_conteudo(api, lid)
    fixo = traducao.prompt_restrito(wlote.load_receitas(), "livre", ("estatico",))
    _traduzir(api, fixo, intencao="restricao", restricoes=["estatico"], restrito=True)
    assert _job(api)["prompt_en"] == fixo


def test_the_final_guard_never_renders_another_version_silently(api):  # noqa: F811
    tok, lid = _pedido(api, prompt="o produto gira devagar", receita="livre", n=1)
    _aprovar_conteudo(api, lid)
    _traduzir(api)
    with api.banco.tx() as c:  # um prompt que cita alguém aparece no lugar do traduzido (bug ou dado antigo)
        c.run("UPDATE lotes SET prompt_en = ? WHERE id = ?", (PESSOA, lid))
    assert _job(api) is None, "a guarda trocou o prompt: o vídeo não vai sem a decisão"
    with api.banco.tx() as c:
        j = c.one("SELECT status, tentativas, worker FROM jobs WHERE lote_id = ? AND tipo = 'render'", (lid,))
    assert j["status"] == "pendente" and j["tentativas"] == 0 and j["worker"] is None
    card = _cartao(api, tok, lid)["entendimento"]
    assert card["excecoes"] and not traducao.prompt_cita_pessoa(card["excecoes"][0]["prompt_en"])
    assert "pessoa" in card["decisoes"][0]["motivo"]
    assert _aprovar(api, tok, lid).status_code == 200
    render = _job(api)
    assert render["prompt_en"] == card["excecoes"][0]["prompt_en"]  # exatamente o que o cliente viu


# -- pendências no envio e o detalhamento ---------------------------------------------------------


def test_contradictions_and_out_of_scope_texts_are_pending_items_before_anything_is_created(api):  # noqa: F811
    tok = _cliente(api)
    r = _lote(api, tok, prompt="gire o produto e mantenha tudo parado")
    assert r.status_code == 422 and r.json()["detail"]["pendencias"][0]["codigo"] == "contraditorio"
    assert r.json()["detail"]["confirmavel"] is False
    r = _lote(api, tok, prompt="coloque o preço R$ 29,90 na tela")
    assert r.status_code == 422 and r.json()["detail"]["pendencias"][0]["codigo"] == "texto_na_imagem"
    r = _lote(api, tok, prompt="gire o produto", briefing={"evitar": ["sem_rotacao"]})
    assert r.status_code == 422 and "giro" in r.json()["detail"]["pendencias"][0]["mensagem"]
    # ambíguo: só com a confirmação do cliente
    r = _lote(api, tok, prompt="arrodeie a vitrine")
    assert r.status_code == 422 and r.json()["detail"]["confirmavel"] is True
    r = _lote(api, tok, prompt="arrodeie a vitrine", confirmar_texto=True)
    assert r.status_code == 200 and api.banco.lote(r.json()["lote"])["pedido"].count("texto_confirmado")
    # texto próprio de uma foto também
    fotos = [{"nome": "a.jpg", "tipo": "image/jpeg", "bytes": 10, "prompt": "com música animada"}]
    r = api.post("/api/lotes", json={"prompt": "deixa bonito", "fotos": fotos}, headers=h(tok))
    assert r.status_code == 422 and r.json()["detail"]["pendencias"][0]["texto"].startswith("Texto próprio")


def test_the_briefing_is_stored_never_sent_to_the_pod_and_its_restrictions_are(api):  # noqa: F811
    briefing = {"uso": "redes", "manter": "o rótulo legível", "evitar": ["sem_pessoas", "camera_parada"],
                "outros": "a cor da embalagem"}  # fmt: skip
    tok, lid = _pedido(api, prompt="o vapor sobe da xícara", receita="livre", briefing=briefing)
    assert json.loads(api.banco.lote(lid)["pedido"])["briefing"] == briefing
    _aprovar_conteudo(api, lid)
    comp = _job(api)
    assert "briefing" not in comp["pedido"] and comp["regras"]["restricoes"] == ["sem_pessoas", "camera_parada"]
    card = _cartao(api, tok, lid)["entendimento"]
    textos = [p["texto"] for p in card["preservar"]]
    assert "o rótulo legível" in textos and "Câmera parada" in textos and card["original"]["uso"]
    # palavra proibida nos detalhes também é recusada (C1), sem dizer qual
    r = _lote(api, tok, briefing={"manter": "a arma do vendedor"})
    assert r.status_code == 422 and r.json()["detail"].startswith("Detalhes do pedido")


def test_the_preview_shows_what_we_understood_before_sending(api):  # noqa: F811
    tok = _cliente(api)
    corpo = {"prompt": "Gire lentamente o produto.", "receita": "livre", "textos": ["deixa bonito"],
             "briefing": {"evitar": ["sem_aproximacao"]}}  # fmt: skip
    r = api.post("/api/pedido/entendimento", json=corpo, headers=h(tok))
    assert r.status_code == 200, r.text
    g1, g2 = r.json()["grupos"]
    assert g1["tipo"] == "acao" and "gire" in g1["frase"]
    assert g2["tipo"] == "restricao"  # "deixa bonito" com "sem zoom" marcado: o padrão sem aproximação
    assert all(a["origem"] in ("cliente", "estilo", "sistema") for g in (g1, g2) for a in g["ajustes"])
    assert r.json()["limites"][0] == "O vídeo sai sem som."


def test_review_mode_is_not_offered_when_the_switch_is_off(banco):
    sem = cliente_teste(config_teste(banco, revisao_cliente=False))
    tok = _cliente(sem)
    assert sem.get("/api/receitas", headers=h(tok)).json()["revisao_cliente"] is False
    r = _lote(sem, tok, revisao="revisar")
    assert r.status_code == 200 and sem.banco.lote(r.json()["lote"])["revisao"] == "seguir"


# -- concorrência (Postgres): duas abas, pods e a edição ao mesmo tempo -------------------------------


def _versionado(banco, revisao="revisar", traduzir=True):
    cid, _ = banco.criar_cliente("Conc", 500, "interno")
    lid = banco.criar_lote(cid, {"prompt": "o produto gira", "receita": "livre", "variacoes": 1, "duracao": 5},
                           [{"nome": f"f{i}", "tipo": "image/jpeg", "bytes": 10, "chave": f"{{lote}}/f{i}.jpg"}
                            for i in range(3)], revisao=revisao)  # fmt: skip
    assert banco.confirmar_envio(lid, {f["chave"]: 10 for f in banco.fotos(lid)}) == []
    tarefas = [{"foto_id": f["id"], "variacao": 1, "seed": i, "arquivo": f"{f['nome']}-v1.mp4"}
               for i, f in enumerate(banco.fotos(lid))]  # fmt: skip
    banco.aprovar(lid, "x", tarefas)
    if traduzir:
        comp = banco.reivindicar("pod-c")
        banco.definir_prompt(lid, GIRO, "i2v@6", traducao={"intencao": "acao"})
        banco.concluir(comp["id"], "pod-c", "ok", tentativa=comp["tentativa_id"])
    return cid, lid


def _atual(banco, lid):
    est = banco.estados_versao([lid])[lid]
    return est["numero"], est["hash"]


def test_two_tabs_approving_at_once_release_once(banco):
    if not banco.pg:
        pytest.skip("concorrência de verdade só no Postgres")
    cid, lid = _versionado(banco)
    numero, hash_ = _atual(banco, lid)
    res, erros = [], []

    def aprovar():
        try:
            res.append(banco.aprovar_versao(lid, cid, numero, hash_))
        except BancoError as e:
            erros.append(e)

    ts = [threading.Thread(target=aprovar) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not erros and sum(1 for r in res if not r["ja"]) == 1
    with banco.tx() as c:
        n = c.one("SELECT COUNT(*) AS n FROM eventos WHERE lote_id = ? AND tipo = 'versao_aprovada'", (lid,))["n"]
    assert n == 1


def _rodada_edicao(banco, rodada: int) -> None:
    cid, lid = _versionado(banco, revisao="seguir")  # liberada: os pods podem pegar os vídeos agora
    numero, hash_ = _atual(banco, lid)
    pegos, editado, erros = [], [], []

    def pod(n):
        j = banco.reivindicar(f"pod-{rodada}-{n}", tipos=["render"])
        if j is not None and j["lote_id"] == lid:  # os de rodadas anteriores não contam
            pegos.append(j)

    def editar():
        try:
            editado.append(banco.editar_texto(lid, cid, numero, hash_, "pedido", "a câmera gira em volta"))
        except BancoError as e:
            erros.append(e)

    ts = [threading.Thread(target=pod, args=(i,)) for i in range(3)] + [threading.Thread(target=editar)]
    for t in reversed(ts) if rodada % 2 else ts:
        t.start()
    for t in ts:
        t.join()
    jobs = banco.jobs(lid)
    if editado:  # a edição ganhou: nenhum vídeo do texto antigo existe mais, nem num pod
        assert not pegos and jobs == [] and banco.lote(lid)["status"] == "aguardando_aprovacao"
    else:  # um pod pegou antes: a edição foi recusada e o pedido segue como estava
        assert pegos and erros and "já começou" in str(erros[0])
        assert {j["status"] for j in jobs if j["tipo"] == "render"} <= {"pendente", "em_andamento"}


def test_an_edit_racing_pods_never_leaves_a_video_of_the_old_text_in_a_pod(banco):
    if not banco.pg:
        pytest.skip("concorrência de verdade só no Postgres")
    for rodada in range(8):
        _rodada_edicao(banco, rodada)


def _rodada_aprovacao(banco, cid: str) -> None:
    fotos = [{"nome": f"f{i}", "tipo": "image/jpeg", "bytes": 10, "chave": f"{{lote}}/f{i}.jpg",
              "prompt": f"o produto {i} gira"} for i in range(2)]  # fmt: skip
    lid = banco.criar_lote(cid, {"prompt": "o produto gira", "receita": "livre", "variacoes": 1, "duracao": 5},
                           fotos, revisao="seguir")  # fmt: skip
    banco.confirmar_envio(lid, {f["chave"]: 10 for f in banco.fotos(lid)})
    banco.aprovar(lid, "x", [])
    # um passo material deixa o pedido esperando o cliente; ele aprova enquanto outra tradução chega
    banco.definir_prompt(lid, GIRO, "i2v@6", material="substituiu")
    numero, hash_ = _atual(banco, lid)
    f1 = next(f for f in banco.fotos(lid) if f["nome"] == "f1")
    resultado = []

    def aprovar():
        try:
            resultado.append(banco.aprovar_versao(lid, cid, numero, hash_))
        except VersaoMudou:
            resultado.append("mudou")

    ts = [threading.Thread(target=aprovar),
          threading.Thread(target=banco.definir_prompt_foto, args=(f1["id"], GIRO, "i2v@6"))]  # fmt: skip
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    lt = banco.lote(lid)
    if resultado == ["mudou"]:  # a tradução entrou antes: a versão vista ficou velha, nada liberado
        assert lt["versao_liberada"] != lt["versao_atual"] and lt["decisao_em"] is not None
    else:  # a aprovação entrou antes: a tradução seguinte (não material) se libera sozinha no modo "seguir"
        assert lt["versao_liberada"] == lt["versao_atual"] and lt["decisao_em"] is None


def test_a_translation_arriving_while_the_client_approves_never_releases_an_unseen_version(banco):
    if not banco.pg:
        pytest.skip("concorrência de verdade só no Postgres")
    cid, _ = banco.criar_cliente("Conc2", 500, "interno")
    for _ in range(6):
        _rodada_aprovacao(banco, cid)


def test_the_version_chain_is_tamper_evident():
    linhas, ant = [], None
    for i, (origem, conteudo) in enumerate([
        ("envio", versoes.base("envio", {"prompt": "o produto gira"}, "revisar", [],
                               versoes.grupos_de("o produto gira", []), {})),
        ("traducao", versoes.passo_traducao("pedido", GIRO, "i2v@6", {}, None)),
    ]):  # fmt: skip
        h_ = versoes.encadear(ant, conteudo)
        linhas.append({"numero": i + 1, "hash": h_, "origem": origem, "conteudo": versoes.canonico(conteudo),
                       "criado": 0.0})  # fmt: skip
        ant = h_
    est = versoes.estado(linhas)
    assert est["grupos"][0]["prompt_sha"] == versoes.sha(GIRO) and versoes.traduzida(est)
    # o mesmo conteúdo com outra versão anterior dá outro hash: aprovar uma é aprovar só ela
    assert versoes.encadear("x", json.loads(linhas[1]["conteudo"])) != linhas[1]["hash"]


def test_ten_pods_with_balance_for_three_never_take_a_video_waiting_for_the_client(banco):
    """O teste dos 10 pods com saldo para 3 (ADR 0012), com um pedido no modo "revisar" esperando o cliente na mesma
    conta: saem exatamente 3 vídeos, todos do pedido liberado; o que espera o cliente nem reserva saldo."""
    cid, _ = banco.criar_cliente("Loja")
    banco.movimentar(cid, 900, "Pix", "dono", referencia="pagamento:pix-v")
    lotes = {}
    for modo in ("revisar", "seguir"):  # o que espera o cliente é o mais antigo da conta
        fotos = [{"nome": f"{modo}{i}", "tipo": "image/jpeg", "bytes": 10, "chave": f"{{lote}}/{modo}{i}.jpg"}
                 for i in range(6)]  # fmt: skip
        lid = banco.criar_lote(cid, {"prompt": "o produto gira", "receita": "livre", "variacoes": 1, "duracao": 5},
                               fotos, preco_video=300, revisao=modo)  # fmt: skip
        banco.confirmar_envio(lid, {f["chave"]: 10 for f in banco.fotos(lid)})
        banco.aprovar(lid, "x", [{"foto_id": f["id"], "variacao": 1, "seed": i, "arquivo": f"{f['nome']}-v1.mp4"}
                                 for i, f in enumerate(banco.fotos(lid))])  # fmt: skip
        comp = banco.reivindicar("trad")
        banco.definir_prompt(lid, GIRO, "i2v@6")
        banco.concluir(comp["id"], "trad", "ok", tentativa=comp["tentativa_id"])
        lotes[modo] = lid
    largada, pegos, lock = threading.Barrier(10), [], threading.Lock()

    def pod(n):
        largada.wait()
        j = banco.reivindicar(f"w{n}")
        with lock:
            pegos.append(j)

    ts = [threading.Thread(target=pod, args=(n,)) for n in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    saidos = [j for j in pegos if j is not None]
    assert len(saidos) == 3 and {j["lote_id"] for j in saidos} == {lotes["seguir"]}
    assert banco.saldo_livre(cid) == 0
    assert sum(int(t["pendentes"] or 0) for t in banco.trabalho() if t["lote_id"] == lotes["revisar"]) == 0
