"""Critérios de entrega, retorno do cliente e pedidos de correção (17/10/2026; render2go_servico.criterios): o critério
nunca é "atendido" só por estar no prompt, um obrigatório não atendido decide (a média não compensa), a observação de um
avaliador automático não decide, o retorno e a correção ficam ligados ao vídeo e à versão, e o crédito de uma correção
sai uma vez só (referência correcao:<id>), também com duas decisões ao mesmo tempo (Postgres). SFW."""

import threading
import time

import pytest
from render2go_worker import lote as wlote
from test_api import (  # noqa: F401 (fixture api)
    ADM,
    CONTINUO,
    PAINEL,
    WORKER,
    _cliente,
    _enviar_tudo,
    _lote,
    api,
    h,
)

from render2go_servico import criterios, versoes
from render2go_servico.db import BancoError

GIRO = "The product slowly rotates in place. Static camera."
BRIEFING = {"uso": "redes", "manter": "o rótulo do pote", "evitar": ["sem_pessoas"]}


def _worker(a, rota, job, **corpo):
    r = a.post(f"/api/worker/jobs/{job['job_id']}/{rota}", headers=h(WORKER),
               json={"worker": "pod-1", "tentativa": job["tentativa"], **corpo})  # fmt: skip
    assert r.status_code == 200, r.text
    return r.json()


def _reivindicar(a):
    r = a.post("/api/worker/reivindicar", json={"worker": "pod-1"}, headers=h(WORKER))
    assert r.status_code == 200, r.text
    return r.json()["job"]


def _entregue(a, tok=None, n=1, briefing=BRIEFING):
    """Um pedido pago no modo "seguir", com o detalhamento, traduzido e com os vídeos entregues (o pod de mentira)."""
    tok = tok or _cliente(a)
    r = _lote(a, tok, n=n, prompt="o pote gira devagar", briefing=briefing)
    assert r.status_code == 200, r.text
    lid = r.json()["lote"]
    assert _enviar_tudo(a, tok, lid, r.json()["envios"]).status_code == 200
    assert a.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM).status_code == 200
    comp = _reivindicar(a)
    assert comp["tipo"] == "compilar"
    _worker(a, "prompt", comp, prompt_en=GIRO, compilador="i2v@6", intencao="acao",
            restricoes=list(briefing.get("evitar") or ()))  # fmt: skip
    _worker(a, "concluir", comp, status="ok")
    jobs = []
    for _ in range(n):
        j = _reivindicar(a)
        assert j is not None and j["tipo"] == "render", j
        a.armazem.objetos[j["video_url"].split("armazem.teste/")[1].split("?")[0]] = 7000
        _worker(a, "concluir", j, status="ok", bytes_video=7000, similaridade=0.99, **CONTINUO)
        jobs.append(j["job_id"])
    return tok, lid, jobs


def _pedido(a, tok, lid):
    r = a.get(f"/api/lotes/{lid}", headers=h(tok))
    assert r.status_code == 200, r.text
    return r.json()


def _retorno(a, tok, job, **corpo):
    return a.post(f"/api/videos/{job}/retorno", json=corpo, headers=h(tok))


# -- o módulo puro --------------------------------------------------------------------------------------------------


def _estado(texto, intencao_tipo, restricoes=(), briefing=None, receita="produto-close"):
    pedido = {"prompt": texto, "receita": receita, "briefing": briefing}
    grupos = versoes.grupos_de(texto, [{"id": "f1", "nome": "a.jpg", "prompt": None}])
    grupos[0]["intencao"] = {"tipo": intencao_tipo, "restricoes": list(restricoes)}
    b = versoes.base("envio", pedido, "seguir", [{"nome": "a.jpg", "bytes": 1}], grupos, {})
    linha = {"numero": 1, "hash": versoes.encadear(None, b), "origem": "envio", "conteudo": versoes.canonico(b),
             "criado": 0.0}  # fmt: skip
    return versoes.estado([linha])


def test_criteria_come_from_the_version_with_type_origin_and_source():
    rec = wlote.load_receitas()
    est = _estado("o pote gira devagar", "acao", ["sem_pessoas"], BRIEFING)
    cs = {c["id"]: c for c in criterios.do_pedido(est, rec)}
    assert cs["pedido:pedido"]["tipo"] == "obrigatorio" and cs["pedido:pedido"]["origem"] == "cliente"
    assert "o pote gira devagar" in cs["pedido:pedido"]["texto"]
    sem = cs["restricao:sem_pessoas"]
    assert sem["fonte"] == "detalhamento" and sem["tipo"] == "obrigatorio"
    assert cs["manter"]["texto"] == "Continua igual: o rótulo do pote" and cs["manter"]["origem"] == "cliente"
    assert cs["produto"]["origem"] == cs["pessoas"]["origem"] == "sistema"
    assert cs["estilo"]["tipo"] == "preferencia" and cs["estilo"]["origem"] == "estilo"
    assert "uso" not in cs  # o uso é só contexto: não vira critério
    # sem instrução, o movimento do serviço é uma sugestão (preferência), não um pedido do cliente
    vazio = {c["id"]: c for c in criterios.do_pedido(_estado("deixa bonito", "vazio"), rec)}
    assert vazio["pedido:pedido"]["tipo"] == "preferencia" and vazio["pedido:pedido"]["origem"] == "sistema"


def test_a_failed_mandatory_criterion_decides_and_preferences_never_offset_it():
    cs = criterios.do_pedido(_estado("o pote gira devagar", "acao", [], BRIEFING), wlote.load_receitas())
    obrig = [c["id"] for c in cs if c["tipo"] == "obrigatorio"]
    tudo = dict.fromkeys(obrig, "atendido") | {"estilo": "atendido"}
    assert criterios.situacao(cs, tudo)["situacao"] == "atende"
    assert criterios.situacao(cs, {**tudo, "manter": "nao_atendido"})["situacao"] == "nao_atende"
    assert criterios.situacao(cs, {**tudo, "manter": "parcial"})["situacao"] == "atende_em_parte"
    # sem decisão ou "não verificável": nunca aprovado por falta de resposta
    assert criterios.situacao(cs, {k: v for k, v in tudo.items() if k != "produto"})["situacao"] == "nao_verificado"
    assert criterios.situacao(cs, {**tudo, "produto": "nao_verificavel"})["situacao"] == "nao_verificado"
    # a preferência não atendida não muda a situação; aparece à parte
    s = criterios.situacao(cs, {**tudo, "estilo": "nao_atendido"})
    assert s["situacao"] == "atende" and s["preferencias"] == {"total": 1, "atendidas": 0}
    assert criterios.situacao(cs, {})["situacao"] == "nao_verificado"


def test_the_existing_color_check_is_only_an_observation():
    obs = criterios.observacoes_tecnicas({"deriva_cor": 12.0, "salto_cor": 3.0})
    assert obs[0]["modo"] == "observacao" and obs[0]["criterio"] == "produto" and "deriva 12.0" in obs[0]["evidencia"]
    assert "%" not in obs[0]["evidencia"]  # sem nota de confiança inventada
    assert criterios.observacoes_tecnicas({"deriva_cor": None}) == []


# -- retorno do cliente, revisão da equipe e correção (nos dois motores) --------------------------------------------


def test_client_feedback_correction_and_team_review_end_to_end(api):  # noqa: F811
    tok, lid, (job,) = _entregue(api)
    pedido = _pedido(api, tok, lid)
    ids = {c["id"] for c in pedido["criterios"]}
    assert {"pedido:pedido", "restricao:sem_pessoas", "manter", "produto", "pessoas"} <= ids
    video = pedido["videos"][0]
    assert video["retorno"] is None and video["pode_corrigir"] is False and video["grupo"] == "pedido"
    # "Pode usar como está?" → não: o motivo é obrigatório; o critério precisa ser do vídeo; o comentário passa pelo C1
    assert _retorno(api, tok, job, usavel=False).status_code == 422
    assert _retorno(api, tok, job, usavel=False, motivo="produto_alterado", criterio="qualquer").status_code == 422
    r = _retorno(api, tok, job, usavel=False, motivo="produto_alterado", comentario="tem armas na mesa")
    assert r.status_code == 422 and "armas" not in r.text
    r = _retorno(api, tok, job, usavel=False, motivo="produto_alterado", criterio="manter",
                 comentario="o rótulo   ficou torto")  # fmt: skip
    assert r.status_code == 200, r.text
    assert r.json()["retorno"]["comentario"] == "o rótulo ficou torto" and r.json()["pode_corrigir"] is True
    # outra conta não vê nem responde o vídeo
    outro = _cliente(api, "Outra loja")
    assert _retorno(api, outro, job, usavel=True).status_code == 404
    assert api.post(f"/api/videos/{job}/correcao", headers=h(outro)).status_code == 404
    # o pedido de correção: um por vídeo (a 2ª aba recebe o mesmo), ligado ao vídeo, à versão e ao critério
    c1 = api.post(f"/api/videos/{job}/correcao", headers=h(tok))
    assert c1.status_code == 200 and c1.json()["correcao"]["status"] == "aberta" and c1.json()["ja"] is False
    assert api.post(f"/api/videos/{job}/correcao", headers=h(tok)).json()["ja"] is True
    (x,) = api.banco.correcoes()
    lt = api.banco.lote(lid)
    assert (x["job_id"], x["versao_id"], x["criterio"], x["motivo"]) == (job, lt["versao_atual"], "manter",
                                                                        "produto_alterado")  # fmt: skip
    assert _pedido(api, tok, lid)["videos"][0]["pode_corrigir"] is False  # já pedida
    # a equipe: o vídeo (link de 10 min, na auditoria), os critérios com a evidência e a situação
    ent = api.get(f"{PAINEL}/api/entregas", headers=ADM).json()["pedidos"]
    (p,) = [e for e in ent if e["lote"] == lid]
    (v,) = p["videos"]
    assert v["situacao"]["situacao"] == "nao_verificado" and v["correcao"]["status"] == "aberta"
    assert any(o["avaliador"] == "auto:conferencia" for o in v["observacoes"])  # a medida de cor, só observação
    assert api.get(f"{PAINEL}/api/jobs/{job}/video", headers=ADM).json()["expira_s"] == 600
    with api.banco.tx() as c:
        assert c.one("SELECT COUNT(*) AS n FROM auditoria_admin WHERE acao = 'ver_video_entrega'")["n"] == 1
    corpo = {"criterio": "manter", "estado": "nao_atendido", "evidencia": "o rótulo muda de forma no segundo 3"}
    r = api.post(f"{PAINEL}/api/jobs/{job}/criterios", json=corpo, headers=ADM)
    assert r.status_code == 200 and r.json()["situacao"]["situacao"] == "nao_atende"
    sem = {**corpo, "criterio": "inventado"}
    assert api.post(f"{PAINEL}/api/jobs/{job}/criterios", json=sem, headers=ADM).status_code == 422
    # a decisão: pedido novo não tem crédito; fora do combinado devolve o valor do vídeo, uma vez só
    saldo = api.banco.saldo(lt["cliente_id"])
    rota = f"{PAINEL}/api/correcoes/{x['id']}/decidir"
    novo = {"classificacao": "pedido_novo", "nota": "quer outro texto", "creditar": True}
    assert api.post(rota, json=novo, headers=ADM).status_code == 422
    lista = api.get(f"{PAINEL}/api/entregas", headers=ADM).json()["correcoes"]
    preco = lista[0]["valor_video"]
    assert preco == lt["preco_video"] > 0
    dec = {"classificacao": "fora_do_combinado", "nota": "o rótulo era obrigatório e mudou", "creditar": True}
    r = api.post(rota, json=dec, headers=ADM)
    assert r.status_code == 200 and r.json()["credito"] == preco and r.json()["ja"] is False
    assert api.post(rota, json=dec, headers=ADM).json()["ja"] is True
    assert api.banco.saldo(lt["cliente_id"]) == saldo + preco
    with api.banco.tx() as c:
        movs = c.all("SELECT tipo, valor FROM movimentos WHERE referencia = ?", (f"correcao:{x['id']}",))
    assert movs == [{"tipo": "credito", "valor": preco}]
    # o cliente vê a resposta e o crédito, nunca a nota da equipe
    corr = _pedido(api, tok, lid)["videos"][0]["correcao"]
    assert corr["status"] == "decidida" and corr["credito"] == preco and "voltaram para o seu saldo" in corr["texto"]
    assert "obrigatório e mudou" not in str(_pedido(api, tok, lid))


def test_feedback_only_for_delivered_videos_and_corrections_have_a_deadline(api):  # noqa: F811
    tok, lid, (job,) = _entregue(api, briefing={"evitar": []})
    assert _retorno(api, tok, job, usavel=True).json()["pode_corrigir"] is False  # pode usar: nada a corrigir
    assert api.post(f"/api/videos/{job}/correcao", headers=h(tok)).status_code == 409
    assert _retorno(api, tok, job, usavel=False, motivo="defeito_visual").status_code == 200  # muda de ideia
    with api.banco.tx() as c:
        c.run("UPDATE jobs SET atualizado = ? WHERE id = ?", (time.time() - 73 * 3600, job))
    assert _pedido(api, tok, lid)["videos"][0]["pode_corrigir"] is False
    r = api.post(f"/api/videos/{job}/correcao", headers=h(tok))
    assert r.status_code == 409 and "72 h" in r.json()["detail"]
    with api.banco.tx() as c:  # o vídeo excluído pelo cliente não recebe retorno
        c.run("UPDATE jobs SET excluido_em = ?, atualizado = ? WHERE id = ?", (time.time(), time.time(), job))
    assert _retorno(api, tok, job, usavel=True).status_code == 409


def test_crediting_a_correction_is_owner_only_and_the_routes_are_in_the_permission_map(api):  # noqa: F811
    from render2go_servico import api as api_mod

    papeis = api_mod.PERMISSOES_PAINEL
    assert papeis[("POST", "/api/correcoes/{correcao_id}/decidir")] == api_mod._DONO
    rotas = [("GET", "/api/entregas"), ("GET", "/api/jobs/{job_id}/video"), ("POST", "/api/jobs/{job_id}/criterios")]
    assert all(r in papeis for r in rotas)


def test_feedback_and_corrections_can_be_switched_off(banco):
    from test_api import cliente_teste, config_teste

    a = cliente_teste(config_teste(banco, correcoes=False))
    tok, _lid, (job,) = _entregue(a, briefing={"evitar": []})
    r = _retorno(a, tok, job, usavel=False, motivo="defeito_visual")
    assert r.status_code == 200 and r.json()["pode_corrigir"] is False
    assert a.post(f"/api/videos/{job}/correcao", headers=h(tok)).status_code == 409


def test_bad_correction_deadline_config_does_not_start():
    from render2go_servico import api as api_mod

    assert api_mod._prazo_correcao("48") == 48
    for ruim in ("0", "721", "x", ""):
        with pytest.raises(ValueError):
            api_mod._prazo_correcao(ruim)


def test_deleting_the_account_removes_feedback_and_keeps_the_credit_trail(api):  # noqa: F811
    tok, lid, (job,) = _entregue(api, briefing={"evitar": []})
    assert _retorno(api, tok, job, usavel=False, motivo="defeito_visual", comentario="borrão no fim").status_code == 200
    api.post(f"/api/videos/{job}/correcao", headers=h(tok))
    cid = api.banco.lote(lid)["cliente_id"]
    api.banco.movimentar(cid, -api.banco.saldo(cid), "reembolso do saldo", "teste")  # a exclusão pede o saldo zerado
    api.banco.excluir_conta(cid)
    with api.banco.tx() as c:
        assert c.one("SELECT COUNT(*) AS n FROM retornos WHERE cliente_id = ?", (cid,))["n"] == 0
        x = c.one("SELECT comentario, status FROM correcoes WHERE job_id = ?", (job,))
    assert x["comentario"] is None and x["status"] == "aberta"


# -- concorrência (Postgres) ------------------------------------------------------------------------------------------


def _em_paralelo(*fns):
    erros: list[BaseException] = []
    barreira = threading.Barrier(len(fns))

    def rodar(fn):
        barreira.wait()
        try:
            fn()
        except BancoError as e:
            erros.append(e)

    ts = [threading.Thread(target=rodar, args=(fn,)) for fn in fns]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return erros


def _rodada_correcao(a) -> None:
    """Uma rodada: duas abas pedem a correção ao mesmo tempo e duas pessoas a decidem com crédito ao mesmo tempo."""
    tok, lid, (job,) = _entregue(a, briefing={"evitar": []})
    assert _retorno(a, tok, job, usavel=False, motivo="defeito_visual").status_code == 200
    cid = a.banco.lote(lid)["cliente_id"]
    _em_paralelo(*(lambda: a.banco.pedir_correcao(job, cid, 3600) for _ in "ab"))
    (x,) = [y for y in a.banco.correcoes() if y["job_id"] == job]
    saldo = a.banco.saldo(cid)
    decidir = [lambda q=q: a.banco.decidir_correcao(x["id"], q, "falha_tecnica", "quadro estranho", True)
               for q in ("dono", "outra aba")]  # fmt: skip
    assert _em_paralelo(*decidir) == []
    with a.banco.tx() as c:
        n = c.one("SELECT COUNT(*) AS n FROM movimentos WHERE referencia = ?", (f"correcao:{x['id']}",))["n"]
    assert n == 1 and a.banco.saldo(cid) == saldo + a.banco.lote(lid)["preco_video"]


def test_two_tabs_deciding_the_same_correction_credit_once_on_postgres(api):  # noqa: F811
    if not api.banco.pg:
        pytest.skip("concorrência de verdade só no Postgres")
    for _ in range(5):
        _rodada_correcao(api)


def test_pilot_metrics_count_feedback_corrections_and_say_what_is_not_measured(api):  # noqa: F811
    tok, lid, (a, b) = _entregue(api, n=2, briefing={"evitar": []})
    assert _retorno(api, tok, a, usavel=True).status_code == 200
    assert _retorno(api, tok, b, usavel=False, motivo="movimento_inadequado").status_code == 200
    assert api.post(f"/api/videos/{b}/correcao", headers=h(tok)).status_code == 200
    m = api.get(f"{PAINEL}/api/entregas", headers=ADM).json()["piloto"]
    assert m["pedidos"]["total"] == 1 and m["pedidos"]["seguir"] == 1 and m["pedidos"]["esperando_cliente"] == 0
    assert m["videos"] == {"entregues": 2, "com_retorno": 2, "sem_retorno": 0, "usaveis": 1, "precisam_mudar": 1,
                           "por_motivo": {"movimento_inadequado": 1}}  # fmt: skip
    assert m["primeira_entrega"] == {"pedidos_respondidos": 1, "utilizaveis": 0}
    assert m["correcoes"]["total"] == 1 and m["correcoes"]["abertas"] == 1 and m["correcoes"]["creditos"] == 0
    assert m["tempos"]["ate_peca_utilizavel"]["n"] == 1
    assert m["conferencia"]["nao_verificado"] == 2  # sem decisão da equipe: nada aprovado por omissão
    assert "trabalho da equipe (revisão e correções)" in m["nao_medido"]
