"""Extrato por rodada (pedido) e logs de geração das últimas 24 h (10/10/2026)."""

import time

from test_api import ADM, CONTINUO, PAINEL, WORKER, _cliente, _enviar_tudo, _job, _lote, api, h  # noqa: F401 (fixture)


def _prompt_e_concluir(a, comp):
    a.post(
        f"/api/worker/jobs/{comp['job_id']}/prompt",
        headers=h(WORKER),
        json={"worker": "pod-1", "prompt_en": "The camera slowly dollies in.", "compilador": "i2v@5"},
    )
    a.post(f"/api/worker/jobs/{comp['job_id']}/concluir", headers=h(WORKER), json={"worker": "pod-1", "status": "ok"})


def _pronto(a, render):
    chave = render["video_url"].split("armazem.teste/")[1].split("?")[0]
    a.armazem.objetos[chave] = 7000
    corpo = {"worker": "pod-1", "status": "ok", "bytes_video": 7000, "similaridade": 0.999, **CONTINUO}
    r = a.post(f"/api/worker/jobs/{render['job_id']}/concluir", headers=h(WORKER), json=corpo)
    assert r.json()["status"] == "ok"
    return chave


def _falhou(a, render):
    corpo = {"worker": "pod-1", "status": "erro", "erro": "GPU caiu"}
    a.post(f"/api/worker/jobs/{render['job_id']}/concluir", headers=h(WORKER), json=corpo)


def _pedido_com_um_video_e_uma_falha(a, tok):
    r = _lote(a, tok, n=2, duracao=4)
    lid = r.json()["lote"]
    _enviar_tudo(a, tok, lid, r.json()["envios"])
    a.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM)
    _prompt_e_concluir(a, _job(a))
    chave = _pronto(a, _job(a))
    _falhou(a, _job(a))
    return lid, chave


def test_statement_groups_each_round_with_model_type_and_net_total(api):  # noqa: F811 (fixture)
    tok = _cliente(api, "Pizzaria", saldo="20")
    lid, _ = _pedido_com_um_video_e_uma_falha(api, tok)  # 2 vídeos de 4 s em 480p: só o entregue (R$ 1,00) é cobrado
    r = _lote(api, tok, n=1)  # 1 vídeo de 5 s, recusado na revisão: nada cobrado (ADR 0012), não vira rodada
    rec = r.json()["lote"]
    _enviar_tudo(api, tok, rec, r.json()["envios"])
    api.post(f"{PAINEL}/api/lotes/{rec}/recusar", json={"motivo": "foto borrada"}, headers=ADM)

    x = api.get("/api/extrato?dias=30", headers=h(tok)).json()
    rodadas = [i for i in x["itens"] if i["tipo"] == "rodada"]
    assert [i["lote"] for i in rodadas] == [lid]  # o recusado não custou nada: fica fora do extrato
    (a,) = rodadas
    assert (a["modelo"], a["finalidade"], a["tipo_modelo"]) == ("Wan 2.2", "Foto vira vídeo", "Imagem para vídeo")
    assert (a["videos"], a["duracao"], a["resolucao"]) == (1, 4, "480p")  # os vídeos entregues (cobrados)
    assert (a["cobrado"], a["devolvido"], a["liquido"], a["status"]) == (100, 0, 100, "concluido_com_falhas")
    (recarga,) = [i for i in x["itens"] if i["tipo"] == "recarga"]
    assert recarga["valor"] == 2000 and recarga["descricao"] == "recarga do teste"
    assert x["resumo"] == {
        "gasto": 100,
        "devolvido": 0,
        "liquido": 100,
        "rodadas": 1,
        "recargas": 2000,
        "ajustes": 0,
        "saldo": 1900,
    }
    (m,) = x["por_modelo"]
    assert (m["modelo"], m["rodadas"], m["liquido"]) == ("Wan 2.2", 1, 100)

    # paginação por "antes": a página seguinte continua de onde a anterior parou
    corte = x["itens"][0]["quando"]
    seguinte = api.get(f"/api/extrato?dias=30&antes={corte}", headers=h(tok)).json()
    assert [i.get("lote") for i in seguinte["itens"]] == [i.get("lote") for i in x["itens"][1:]]
    assert api.get("/api/extrato?dias=12", headers=h(tok)).status_code == 422
    # outra conta não vê nada desta
    assert api.get("/api/extrato", headers=h(_cliente(api, "Outra"))).json()["itens"][0]["tipo"] == "recarga"

    # pedido que saiu do histórico (limpeza) e movimento sem o modelo (antes da coluna): a rodada continua
    with api.banco.tx() as c:
        c.run("UPDATE movimentos SET modelo = NULL WHERE lote_id = ?", (lid,))
    api.banco.apagar_historico(forcar=True)
    x = api.get("/api/extrato?dias=0", headers=h(tok)).json()
    a = next(i for i in x["itens"] if i.get("lote") == lid)
    assert (a["modelo"], a["videos"], a["duracao"], a["resolucao"], a["status"]) == ("Wan 2.2", 1, 4, "480p", None)


def test_statement_spreadsheet_is_excel_friendly_and_safe(api):  # noqa: F811 (fixture)
    cid, tok = api.banco.criar_cliente("Doceria", 500, "pago")
    corpo = {"valor": "15,50", "descricao": '=HYPERLINK("http://x")'}  # texto que viraria fórmula no Excel
    api.post(f"{PAINEL}/api/clientes/{cid}/saldo", json=corpo, headers=ADM)
    r = api.get("/api/extrato.csv?dias=30&fuso_min=-180", headers=h(tok))
    assert r.status_code == 200 and r.headers["content-type"] == "text/csv; charset=utf-8"
    assert r.headers["content-disposition"].startswith('attachment; filename="render2go-extrato-')
    texto = r.content.decode("utf-8")
    assert texto.startswith("﻿Data;Tipo;Pedido;Tipo de modelo;Modelo de IA;")
    linha = texto.splitlines()[1].split(";", 2)
    assert linha[1] == "Recarga" and ";15,50;" in texto and "'=HYPERLINK" in texto
    hoje = time.strftime("%d/%m/%Y", time.gmtime(time.time() - 3 * 3600))
    assert linha[0].startswith(hoje)
    assert api.get("/api/extrato.csv?fuso_min=9999", headers=h(tok)).status_code == 422


def test_generation_logs_list_the_last_24_hours_and_a_video_can_be_deleted(api):  # noqa: F811 (fixture)
    tok = _cliente(api, "Hamburgueria")
    lid, chave = _pedido_com_um_video_e_uma_falha(api, tok)
    g = api.get("/api/geracoes", headers=h(tok)).json()
    (v,) = g["itens"]  # só o vídeo que saiu (a falha não é uma geração)
    assert (v["arquivo"], v["lote"], v["modelo"], v["finalidade"]) == (
        "foto-0-v1.mp4",
        lid,
        "Wan 2.2",
        "Foto vira vídeo",
    )
    assert v["texto"] == "a câmera se aproxima devagar do produto" and v["texto_proprio"] is False
    assert time.time() - v["gerado_em"] < 60 and v["url"].endswith("?GET") and "attachment" in v["baixar"]
    assert api.get("/api/geracoes?horas=0", headers=h(tok)).status_code == 422
    assert api.get("/api/geracoes?horas=169", headers=h(tok)).status_code == 422

    # outra conta não exclui (nem descobre) o vídeo; o dono exclui, e o arquivo sai do armazenamento
    outra = _cliente(api, "Outra")
    assert api.post(f"/api/geracoes/{v['id']}/excluir", headers=h(outra)).status_code == 404
    assert api.post(f"/api/geracoes/{v['id']}/excluir", headers=h(tok)).json() == {"ok": True}
    assert (
        chave in api.armazem.apagados
        and chave.replace("foto-0-v1.mp4", "conferencia/foto-0-v1.jpg") in api.armazem.apagados
    )
    assert api.post(f"/api/geracoes/{v['id']}/excluir", headers=h(tok)).status_code == 404
    assert api.get("/api/geracoes", headers=h(tok)).json()["itens"] == []
    lote = api.get(f"/api/lotes/{lid}", headers=h(tok)).json()
    excluido = next(x for x in lote["videos"] if x["arquivo"] == "foto-0-v1.mp4")
    assert excluido["excluido"] is True and "url" not in excluido and lote["prontos"] == 1
