"""Legenda por foto (ADR 0014, RENDER2GO_LEGENDA_POR_FOTO=1): cada foto do pedido aprovado ganha a sua tradução, com a
foto e a legenda dela; o vídeo da foto espera o prompt dela; a legenda fica com a foto (painel e auditoria, nunca o
cliente) e só deixa a regra da pessoa inventada mais rígida. Tudo SFW."""

import json
import threading

import pytest
from render2go_worker import legenda, lote, traducao
from test_api import (  # noqa: F401 (fixture api)
    ADM,
    PAINEL,
    WORKER,
    _cliente,
    _enviar_tudo,
    _job,
    _lote,
    cliente_teste,
    config_teste,
    h,
)

from render2go_servico import filtro_fotos

LEGENDA = {
    "descricao": "A slice of layered chocolate cake on a white plate.",
    "categoria": "comida",
    "assunto": "the chocolate cake",
    "pessoas": False,
    "texto": False,
    "modelo": legenda.MODELO,
    "versao": legenda.VERSAO,
    "segundos": 12.5,
}
PESSOA = "She smiles as soft light glides across the cake. Static camera."


def _criar(b, n=3, preco=None, cobranca="video", cid=None):
    cid = cid or b.criar_cliente("Doceria")[0]
    fotos = [
        {"nome": f"f{i}", "tipo": "image/jpeg", "bytes": 100, "chave": f"entrada/{cid}/{{lote}}/f{i}.jpg"}
        for i in range(n)
    ]
    pedido = {
        "prompt": "o produto gira devagar",
        "receita": "livre",
        "qualidade": "rapida",
        "duracao": 4,
        "variacoes": 1,
    }
    lid = b.criar_lote(cid, pedido, fotos, preco_video=preco, cobranca=cobranca)
    return cid, lid


def _aprovar(b, lid, legenda_ligada=True):
    b.confirmar_envio(lid, {f["chave"]: f["bytes"] for f in b.fotos(lid)})
    tarefas = [{"foto_id": f["id"], "variacao": 1, "seed": 11, "arquivo": f"{f['nome']}-v1.mp4"} for f in b.fotos(lid)]
    b.aprovar(lid, "dono", tarefas, legenda=legenda_ligada)


def _traduzir_pedido(b, lid):
    c = b.reivindicar("compilador", tipos=["compilar"])
    assert c["lote_id"] == lid and c["foto_id"] is None  # a do pedido passa na frente
    b.definir_prompt(lid, "The product slowly rotates. Static camera.", "i2v@8")
    b.concluir(c["id"], "compilador", "ok")


def _por_nome(b, lid):
    return {f["nome"]: f for f in b.fotos(lid)}


def test_each_photo_gets_its_own_translation_and_its_videos_wait_for_it(banco):
    _, lid = _criar(banco)
    _aprovar(banco, lid)
    jobs = banco.jobs(lid)
    comp = sorted(j["arquivo"] for j in jobs if j["tipo"] == "compilar" and j["foto_id"])
    assert banco.lote(lid)["legenda"] == 1 and comp == ["f0-v0", "f1-v0", "f2-v0"]
    assert sum(j["tipo"] == "compilar" and j["foto_id"] is None for j in jobs) == 1  # a do pedido fica
    _traduzir_pedido(banco, lid)
    assert banco.lote(lid)["status"] == "processando"
    # o prompt do pedido não libera vídeo nenhum: cada um espera o da própria foto
    assert banco.reivindicar("w1", tipos=["render"]) is None
    j = banco.reivindicar("w1")
    assert j["tipo"] == "compilar" and j["arquivo"] == "f0-v0"
    banco.definir_prompt_da_foto(j["foto_id"], "The chocolate cake slowly rotates.", "i2v@8", LEGENDA, {"vago": False})
    banco.concluir(j["id"], "w1", "ok")
    fotos = _por_nome(banco, lid)
    assert fotos["f0"]["prompt_en"] == "The chocolate cake slowly rotates."
    assert (
        json.loads(fotos["f0"]["legenda"])["assunto"] == "the chocolate cake" and fotos["f0"]["categoria"] == "comida"
    )
    assert fotos["f1"]["prompt_en"] is None and fotos["f1"]["legenda"] is None  # só a própria foto
    r = banco.reivindicar("w2", tipos=["render"])
    assert r["arquivo"] == "f0-v1.mp4" and banco.reivindicar("w3", tipos=["render"]) is None
    # a foto sem legenda (falhou no pod) segue com o prompt dela
    j = banco.reivindicar("w1")
    assert j["arquivo"] == "f1-v0"
    banco.definir_prompt_da_foto(j["foto_id"], "The product slowly rotates.", "i2v@8", None)
    banco.concluir(j["id"], "w1", "ok")
    assert _por_nome(banco, lid)["f1"]["legenda"] is None and banco.reivindicar("w3", tipos=["render"])["arquivo"] == (
        "f1-v1.mp4"
    )
    (ev,) = [e for e in banco.auditoria(lid)["eventos"] if e["tipo"] == "aprovado"]
    assert json.loads(ev["detalhe"])["legenda_por_foto"] == 3


def test_orders_approved_with_the_key_off_keep_one_prompt_per_text(banco):
    _, lid = _criar(banco, n=2)
    _aprovar(banco, lid, legenda_ligada=False)
    assert banco.lote(lid)["legenda"] is None
    assert [j["foto_id"] for j in banco.jobs(lid) if j["tipo"] == "compilar"] == [None]
    _traduzir_pedido(banco, lid)
    assert banco.reivindicar("w1", tipos=["render"])["arquivo"] == "f0-v1.mp4"  # o prompt do pedido vale


def test_a_failed_photo_translation_cancels_only_the_videos_of_that_photo(banco):
    _, lid = _criar(banco, n=2)
    _aprovar(banco, lid)
    _traduzir_pedido(banco, lid)
    j = banco.reivindicar("w1")
    banco.concluir(j["id"], "w1", "erro", erro="compilador recusou: REFUSED")
    renders = {r["arquivo"]: r for r in banco.jobs(lid) if r["tipo"] == "render"}
    assert renders["f0-v1.mp4"]["status"] == "cancelado" and renders["f0-v1.mp4"]["erro"] == "compilação da foto falhou"
    assert renders["f1-v1.mp4"]["status"] == "pendente"
    banco.marcar_recusa_texto(lid, j["foto_id"], "c1: termo proibido")  # o C1 da API: o motivo de verdade
    renders = {r["arquivo"]: r for r in banco.jobs(lid) if r["tipo"] == "render"}
    assert renders["f0-v1.mp4"]["erro"] == "c1: termo proibido" and renders["f1-v1.mp4"]["erro"] is None


def test_the_pod_count_never_counts_a_photo_translation_as_one_more_video(banco):
    _, com = _criar(banco, n=3)
    _aprovar(banco, com)
    _, sem = _criar(banco, n=3)
    _aprovar(banco, sem, legenda_ligada=False)
    trabalho = banco.trabalho()
    pendentes = {t["lote_id"]: int(t["pendentes"]) for t in trabalho}
    traducoes = {t["lote_id"]: int(t["traducoes"]) for t in trabalho}
    # os 3 vídeos e, à parte (14/10/2026: render2go_servico.capacidade), só a tradução do pedido, nos dois
    assert pendentes == {com: 3, sem: 3} and traducoes == {com: 1, sem: 1}


def test_the_photo_translation_waits_for_the_photo_filter_and_a_refused_photo_cancels_it(banco):
    banco.exigir_filtro = True
    _, lid = _criar(banco, n=2)
    _aprovar(banco, lid)
    _traduzir_pedido(banco, lid)  # a do pedido é só texto: passa sem o filtro
    assert banco.reivindicar("w1") is None  # a de cada foto leva a foto ao pod: só liberada
    assert banco.trabalho() == []  # e nenhum pod sobe por ela
    f0, f1 = banco.fotos(lid)
    banco.salvar_filtro(f0["id"], "ok", {"versao": filtro_fotos.VERSAO, "nota": 0.01})
    j = banco.reivindicar("w1")
    assert j["tipo"] == "compilar" and j["foto_id"] == f0["id"]
    banco.salvar_filtro(f1["id"], "recusada", {"versao": filtro_fotos.VERSAO, "nota": 0.9, "nota_alta": 0.8})
    comp_f1 = next(x for x in banco.jobs(lid) if x["tipo"] == "compilar" and x["foto_id"] == f1["id"])
    assert comp_f1["status"] == "cancelado"  # não fica pendente para sempre (o pedido fecharia nunca)
    banco.definir_prompt_da_foto(f0["id"], "The chocolate cake slowly rotates.", "i2v@8", LEGENDA)
    banco.concluir(j["id"], "w1", "ok")
    r = banco.reivindicar("w2")
    banco.concluir(r["id"], "w2", "ok", similaridade=0.99, bytes_video=10)
    assert banco.lote(lid)["status"] == "concluido_com_falhas"


def test_many_pods_with_photo_translations_never_take_more_videos_than_the_balance_pays(banco):
    """O teste dos 10 pods com saldo para 3 (ADR 0012), com a tradução por foto: só 3 vídeos saem."""
    cid, _ = banco.criar_cliente("Loja")
    banco.movimentar(cid, 900, "Pix", "dono", referencia="pagamento:pix-1")
    _, lid = _criar(banco, n=10, preco=300, cobranca="video", cid=cid)
    _aprovar(banco, lid)
    _traduzir_pedido(banco, lid)
    for _ in range(10):  # todas as fotos traduzidas
        j = banco.reivindicar("tradutor", tipos=["compilar"])
        banco.definir_prompt_da_foto(j["foto_id"], "The product slowly rotates.", "i2v@8", LEGENDA)
        banco.concluir(j["id"], "tradutor", "ok")
    largada, pegos, trava = threading.Barrier(10), [], threading.Lock()

    def pod(n):
        largada.wait()
        x = banco.reivindicar(f"w{n}")
        with trava:
            pegos.append(x)

    ts = [threading.Thread(target=pod, args=(n,)) for n in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    saidos = [x for x in pegos if x is not None]
    assert len(saidos) == 3 and all(x["tipo"] == "render" for x in saidos) and banco.saldo_livre(cid) == 0


@pytest.fixture
def api_leg(banco):
    return cliente_teste(config_teste(banco, legenda_por_foto=True))


def _pedido_aprovado(a, n=2, **pedido):
    tok = _cliente(a)
    r = _lote(a, tok, n=n, receita="livre", prompt="o produto gira devagar", **pedido)
    assert r.status_code == 200, r.text
    lid = r.json()["lote"]
    assert _enviar_tudo(a, tok, lid, r.json()["envios"]).status_code == 200
    assert a.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM).status_code == 200
    return tok, lid


def _prompt(a, job, texto, **extra):
    corpo = {"worker": "pod-1", "prompt_en": texto, "compilador": "i2v@8", **extra}
    return a.post(f"/api/worker/jobs/{job['job_id']}/prompt", headers=h(WORKER), json=corpo)


def _ok(a, job):
    r = a.post(
        f"/api/worker/jobs/{job['job_id']}/concluir", headers=h(WORKER), json={"worker": "pod-1", "status": "ok"}
    )
    assert r.status_code == 200, r.text


def _traduzir_tudo(a, legendas):
    """A tradução do pedido e a de cada foto, na ordem da fila (a de cada foto logo antes do vídeo dela);
    `legendas`: [(legenda da foto ou None, prompt)], na ordem das fotos. Devolve os vídeos pegos no caminho, em
    andamento com o pod-1."""
    comp = _job(a)
    assert comp["tipo"] == "compilar" and "foto_url" not in comp  # a do pedido: só texto
    assert _prompt(a, comp, "The product slowly rotates. Static camera.").status_code == 200
    _ok(a, comp)
    pendentes, renders = list(legendas), []
    while pendentes:
        j = _job(a)
        if j["tipo"] == "render":  # o vídeo da foto já traduzida vem antes da tradução da próxima
            renders.append(j)
            continue
        assert j["legenda"] is True and j["arquivo"].endswith("-v0")
        assert j["foto_url"].startswith("https://armazem.teste/entrada/") and j["foto_ext"] == "jpg"
        leg, texto = pendentes.pop(0)
        r = _prompt(a, j, texto, legenda=leg)
        assert r.status_code == 200, r.text
        _ok(a, j)
    return renders


def test_the_photo_job_carries_the_photo_and_the_api_checks_the_caption_again(api_leg):
    _, lid = _pedido_aprovado(api_leg)
    ruim = {**LEGENDA, "categoria": "nao-existe"}  # fora do formato: a foto segue sem legenda
    renders = _traduzir_tudo(
        api_leg, [(LEGENDA, "The chocolate cake slowly rotates."), (ruim, "The product slowly rotates.")]
    )
    f0, f1 = api_leg.banco.fotos(lid)
    assert json.loads(f0["legenda"])["descricao"] == LEGENDA["descricao"] and f0["categoria"] == "comida"
    assert f1["legenda"] is None and f1["prompt_en"] == "The product slowly rotates."
    renders.append(_job(api_leg))
    vistos = {r["arquivo"]: r["prompt_en"] for r in renders}
    assert vistos == {
        "foto-0-v1.mp4": "The chocolate cake slowly rotates.",
        "foto-1-v1.mp4": "The product slowly rotates.",
    }


def test_a_caption_too_big_is_refused_by_the_api(api_leg):
    _pedido_aprovado(api_leg, n=1)
    comp = _job(api_leg)
    _prompt(api_leg, comp, "The product slowly rotates. Static camera.")
    _ok(api_leg, comp)
    j = _job(api_leg)
    r = _prompt(api_leg, j, "The product slowly rotates.", legenda={**LEGENDA, "descricao": "x" * 3000})
    assert r.status_code == 422


def test_the_caption_makes_the_people_rule_stricter_and_names_the_default_subject(api_leg):
    """A análise acha alguém na foto, mas a legenda diz que não há pessoa: a regra vale, e o movimento padrão usa a
    categoria e o assunto da legenda."""
    _, lid = _pedido_aprovado(api_leg, n=1)
    (f,) = api_leg.banco.fotos(lid)
    api_leg.banco.salvar_analise(f["id"], {"versao": "pessoas-v1", "pessoas": 1, "rostos": 1, "maior": 0.4})
    _traduzir_tudo(api_leg, [(LEGENDA, PESSOA)])
    (f,) = api_leg.banco.fotos(lid)
    padrao = traducao.prompt_padrao(
        lote.load_receitas(), "livre", "comida", sem_pessoas=True, assunto="the chocolate cake"
    )
    assert f["prompt_en"] == padrao and padrao.startswith("The chocolate cake stays in place")
    assert not traducao.prompt_cita_pessoa(f["prompt_en"])
    eventos = [d for e in api_leg.banco.auditoria(lid)["eventos"] if (d := json.loads(e["detalhe"] or "null"))]
    assert any(e.get("onde") == "traducao" and e.get("palavras") == ["she", "smiles"] for e in eventos)
    # sem a legenda, a análise com pessoas manteria o prompt (a regra só fica mais rígida, nunca mais frouxa)
    assert traducao.regra_sem_pessoas("o produto gira devagar", [{"pessoas": 1, "rostos": 1}]) is False


def test_the_client_never_sees_the_caption_but_the_panel_does(api_leg):
    tok, lid = _pedido_aprovado(api_leg)
    renders = _traduzir_tudo(
        api_leg, [(LEGENDA, "The chocolate cake slowly rotates."), (LEGENDA, "The chocolate cake rotates.")]
    )
    r = renders[0]  # um vídeo que não sai: aparece em "vídeos que não saíram", com a legenda da foto
    erro = {"worker": "pod-1", "status": "erro", "erro": "o produto mudou no meio (conferência)"}
    assert api_leg.post(f"/api/worker/jobs/{r['job_id']}/concluir", headers=h(WORKER), json=erro).status_code == 200
    # o cliente: nem na lista, nem no pedido
    for rota in ("/api/lotes", f"/api/lotes/{lid}"):
        corpo = api_leg.get(rota, headers=h(tok)).text
        assert LEGENDA["descricao"] not in corpo and "chocolate cake" not in corpo
    # o painel: o histórico, os que não saíram e a auditoria
    hist = api_leg.get(f"{PAINEL}/api/historico", headers=ADM).json()
    (item,) = [x for x in hist["lotes"] if x["lote"] == lid]
    assert item["legendas"]["total"] == 2 and item["legendas"]["fotos"][0]["legenda"] == LEGENDA["descricao"]
    (falha,) = api_leg.get(f"{PAINEL}/api/lotes/{lid}/falhas", headers=ADM).json()["videos"]
    assert falha["legenda"] == LEGENDA["descricao"] and falha["categoria"] == "comida"
    aud = api_leg.get(f"{PAINEL}/api/lotes/{lid}/auditoria", headers=ADM).json()
    assert all(f["legenda"]["assunto"] == "the chocolate cake" for f in aud["fotos"])
