"""Satisfação depois de cada pedido terminado (pedido do dono, 12/10/2026): "O quão satisfeito você está com a sua
geração?", de "Muito pouco" (1) a "Muito satisfeito" (5), uma nota por pedido, e "Descartar e não ver novamente",
que vale para a conta inteira."""

import time

from test_api import ADM, PAINEL, _cliente, api, h  # noqa: F401 (fixture api)
from test_pedido_grande import PEDIDO, _fotos, _subir


def _pedido(api, tok, terminado_ha_s: float | None = 60):  # noqa: F811 (fixture)
    """Pedido de 1 foto; terminado (com o vídeo pronto) há `terminado_ha_s` segundos, ou só enviado (None)."""
    r = api.post("/api/lotes", json={"prompt": PEDIDO, "fotos": _fotos(0, 1)}, headers=h(tok))
    lid = r.json()["lote"]
    _subir(api, r.json()["envios"])
    assert api.post(f"/api/lotes/{lid}/enviado", headers=h(tok)).status_code == 200
    if terminado_ha_s is not None:
        api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM)
        with api.banco.tx() as c:
            c.run("UPDATE jobs SET status = 'ok' WHERE lote_id = ?", (lid,))
            c.run(
                "UPDATE lotes SET status = 'concluido', concluido_em = ? WHERE id = ?",
                (time.time() - terminado_ha_s, lid),
            )
    return lid


def _no_lista(api, tok, lid):  # noqa: F811 (fixture)
    (lt,) = [x for x in api.get("/api/lotes", headers=h(tok)).json() if x["lote"] == lid]
    return lt["pedir_avaliacao"], lt["avaliacao"]


def test_each_finished_order_asks_once_and_keeps_the_first_answer(api):  # noqa: F811 (fixture)
    tok = _cliente(api, "Atacado", plano="interno")
    lid = _pedido(api, tok)
    antigo = _pedido(api, tok, terminado_ha_s=8 * 86400)  # terminou há mais de 7 dias: não pergunta
    aberto = _pedido(api, tok, terminado_ha_s=None)  # ainda na revisão: não pergunta nem aceita nota
    assert _no_lista(api, tok, lid) == (True, None)
    assert _no_lista(api, tok, antigo) == (False, None) and _no_lista(api, tok, aberto) == (False, None)
    assert api.get(f"/api/lotes/{lid}", headers=h(tok)).json()["pedir_avaliacao"] is True
    for nota in (0, 6, "muito"):
        assert api.post(f"/api/lotes/{lid}/avaliacao", json={"nota": nota}, headers=h(tok)).status_code == 422
    assert api.post(f"/api/lotes/{aberto}/avaliacao", json={"nota": 3}, headers=h(tok)).status_code == 409
    outra = h(_cliente(api, "Outra"))
    assert api.post(f"/api/lotes/{lid}/avaliacao", json={"nota": 1}, headers=outra).status_code == 404
    assert api.post(f"/api/lotes/{lid}/avaliacao", json={"nota": 4}, headers=h(tok)).status_code == 200
    assert _no_lista(api, tok, lid) == (False, 4)
    assert api.post(f"/api/lotes/{lid}/avaliacao", json={"nota": 1}, headers=h(tok)).status_code == 200
    assert _no_lista(api, tok, lid) == (False, 4)  # a 1ª resposta vale
    assert api.post(f"/api/lotes/{antigo}/avaliacao", json={"nota": 5}, headers=h(tok)).status_code == 200


def test_discarding_hides_the_question_on_every_order_of_that_account_only(api):  # noqa: F811 (fixture)
    tok, outra = _cliente(api, "Loja", plano="interno"), _cliente(api, "Outra loja", plano="interno")
    a, b, c = _pedido(api, tok), _pedido(api, tok), _pedido(api, outra)
    assert api.post("/api/conta/avaliacoes/descartar", headers=h(tok)).status_code == 200
    assert _no_lista(api, tok, a) == (False, None) and _no_lista(api, tok, b) == (False, None)
    assert _no_lista(api, outra, c) == (True, None)
    novo = _pedido(api, tok)  # nem nos pedidos seguintes
    assert _no_lista(api, tok, novo) == (False, None)
    assert api.post("/api/conta/avaliacoes/descartar", headers=h(tok)).status_code == 200  # de novo: nada muda


def test_the_panel_shows_the_ratings_and_they_go_with_the_account_and_the_history(api):  # noqa: F811 (fixture)
    tok, outra = _cliente(api, "Loja", plano="interno"), _cliente(api, "Outra loja", plano="interno")
    a, b, c = _pedido(api, tok), _pedido(api, tok), _pedido(api, outra)
    for lid, nota, quem in ((a, 4, tok), (b, 5, tok), (c, 1, outra)):
        api.post(f"/api/lotes/{lid}/avaliacao", json={"nota": nota}, headers=h(quem))
    api.post("/api/conta/avaliacoes/descartar", headers=h(outra))
    s = api.get(f"{PAINEL}/api/resumo", headers=ADM).json()["satisfacao"]
    assert s == {"por_nota": [1, 0, 0, 1, 1], "total": 3, "media": 3.33, "descartaram": 1}
    notas = {x["lote"]: x["avaliacao"] for x in api.get(f"{PAINEL}/api/historico", headers=ADM).json()["lotes"]}
    assert (notas[a], notas[b], notas[c]) == (4, 5, 1)
    # a conta excluída leva as notas dela; apagar o histórico apaga as notas antes dos pedidos
    cid = api.banco.lote(c)["cliente_id"]
    api.banco.excluir_conta(cid)
    assert api.banco.resumo_avaliacoes(0)["total"] == 2
    api.banco.apagar_historico(forcar=True)
    assert api.banco.resumo_avaliacoes(0)["total"] == 0
