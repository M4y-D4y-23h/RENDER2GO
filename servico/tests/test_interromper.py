"""Pedido interrompido com segurança, o motivo de cada vídeo que não saiu e a quarentena dos vídeos barrados
(pedidos do dono, 12/10/2026)."""

import json
import time

import pytest
from test_api import (  # noqa: F401 (fixture api)
    ADM,
    PAINEL,
    WORKER,
    _cliente,
    _concluir,
    _enviar_tudo,
    _job,
    _lote,
    api,
    h,
)
from test_sequencia import _prompt

from render2go_servico import motivos

OK = {"bytes_video": 7000, "similaridade": 0.999, "deriva_cor": 6.5, "salto_cor": 1.2, "nsfw": 0.01, "nsfw_quadros": 13}


def _gerando(api, tok, n=3):  # noqa: F811 (fixture)
    """Pedido aprovado e traduzido: os vídeos esperam na fila."""
    r = _lote(api, tok, n=n)
    lid = r.json()["lote"]
    assert _enviar_tudo(api, tok, lid, r.json()["envios"]).status_code == 200
    api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM)
    comp = _job(api)
    assert comp["tipo"] == "compilar" and _prompt(api, comp, "the camera slowly moves closer").status_code == 200
    _concluir(api, comp, status="ok")
    return lid


def _entregar(api, job):  # noqa: F811 (fixture)
    chave = job["video_url"].split("armazem.teste/")[1].split("?")[0]
    api.armazem.objetos[chave] = 7000
    return _concluir(api, job, status="ok", **OK).json()["status"]


def test_the_client_stops_an_order_and_only_what_was_already_on_a_pod_finishes(api):  # noqa: F811 (fixture)
    tok = _cliente(api)
    lid = _gerando(api, tok, n=3)
    v1 = _job(api)  # um vídeo num pod
    assert api.post(f"/api/lotes/{lid}/interromper", headers=h(_cliente(api, "Outra"))).status_code == 404
    r = api.post(f"/api/lotes/{lid}/interromper", headers=h(tok))
    assert r.status_code == 200 and r.json()["cancelados"] == 2
    assert _job(api) is None  # nada mais sai para os pods
    lote = api.get(f"/api/lotes/{lid}", headers=h(tok)).json()
    assert lote["status"] == "processando" and lote["interrompido_por"] == "cliente" and lote["interrompido_em"]
    assert _entregar(api, v1) == "ok"  # o que estava no pod termina, é entregue e cobrado
    lote = api.get(f"/api/lotes/{lid}", headers=h(tok)).json()
    assert lote["status"] == "concluido_com_falhas" and lote["prontos"] == 1
    assert lote["nao_sairam"] == [{"motivo": "interrompido_cliente", "texto": motivos.MOTIVOS["interrompido_cliente"],
                                   "videos": 2}]  # fmt: skip
    assert lote["cobranca"] == {"cobrado": lote["preco_video"], "devolvido": 0}  # só o entregue (ADR 0012)
    # parar de novo, ou um pedido que já terminou: recusado
    assert api.post(f"/api/lotes/{lid}/interromper", headers=h(tok)).status_code == 409


def test_a_stopped_order_never_goes_back_to_the_queue(api):  # noqa: F811 (fixture)
    """O vídeo que estava num pod e voltaria para a fila (erro que seria refeito, ou o prazo do pod vencido) é
    cancelado: depois de parar, nada mais é gerado."""
    tok = _cliente(api)
    lid = _gerando(api, tok, n=2)
    a, b = _job(api), _job(api)
    assert api.post(f"/api/lotes/{lid}/interromper", headers=h(tok)).json()["cancelados"] == 0
    # a: a conferência reprova e pediria para refazer; b: o pod some e o prazo vence
    _concluir(api, a, status="erro", erro="o produto mudou de cor ou de forma no vídeo (deriva de cor 61.0)",
              repetir=True)  # fmt: skip
    with api.banco.tx() as c:
        c.run("UPDATE jobs SET lease_ate = 1 WHERE id = ?", (b["job_id"],))
    assert _job(api) is None
    jobs = {j["id"]: j for j in api.banco.jobs(lid) if j["tipo"] == "render"}
    assert {j["status"] for j in jobs.values()} == {"cancelado"}
    assert api.banco.lote(lid)["status"] == "concluido_com_falhas"


def _worker(api, job, rota, worker="pod-1"):  # noqa: F811 (fixture)
    return api.post(f"/api/worker/jobs/{job['job_id']}/{rota}", headers=h(WORKER), json={"worker": worker})


def test_a_video_picked_ahead_is_cancelled_if_the_order_stops_before_it_reaches_the_gpu(api):  # noqa: F811
    """A GPU não espera (12/10/2026): o pod pega o próximo vídeo enquanto gera o atual. Se o cliente para o pedido
    nesse meio-tempo, o vídeo adiantado não vai para a GPU: é cancelado sem custo, como os que estavam na fila."""
    tok = _cliente(api)
    lid = _gerando(api, tok, n=2)
    atual, adiantado = _job(api), _job(api)
    assert _worker(api, adiantado, "iniciar", worker="pod-2").status_code == 409  # de outro pod
    r = _worker(api, adiantado, "iniciar")
    assert r.status_code == 200 and r.json() == {"seguir": True}
    with api.banco.tx() as c:  # o prazo do pod recomeça na GPU
        assert c.one("SELECT lease_ate FROM jobs WHERE id = ?", (adiantado["job_id"],))["lease_ate"] > time.time() + 800
    _concluir(api, adiantado, status="erro", erro="x", repetir=True)  # volta para a fila; o pod pega de novo
    adiantado = _job(api)
    assert api.post(f"/api/lotes/{lid}/interromper", headers=h(tok)).json()["cancelados"] == 0  # os dois num pod
    assert _worker(api, adiantado, "iniciar").json() == {"seguir": False}
    assert _worker(api, adiantado, "iniciar").status_code == 409  # já cancelado
    assert _entregar(api, atual) == "ok"
    lote = api.get(f"/api/lotes/{lid}", headers=h(tok)).json()
    assert lote["status"] == "concluido_com_falhas" and lote["prontos"] == 1
    assert lote["nao_sairam"] == [{"motivo": "interrompido_cliente", "texto": motivos.MOTIVOS["interrompido_cliente"],
                                   "videos": 1}]  # fmt: skip
    assert lote["cobranca"] == {"cobrado": lote["preco_video"], "devolvido": 0}


def test_a_video_picked_ahead_and_not_rendered_goes_back_without_counting_an_attempt(api):  # noqa: F811 (fixture)
    tok = _cliente(api)
    lid = _gerando(api, tok, n=1)
    job = _job(api)
    assert _worker(api, job, "devolver", worker="pod-2").status_code == 409
    assert _worker(api, job, "devolver").status_code == 200
    (j,) = [j for j in api.banco.jobs(lid) if j["tipo"] == "render"]
    assert j["status"] == "pendente" and j["tentativas"] == 0 and j["worker"] is None
    assert _worker(api, job, "devolver").status_code == 409  # não está mais com o pod
    de_novo = _job(api)
    assert de_novo["job_id"] == job["job_id"] and de_novo["seed"] == job["seed"]  # a mesma seed: nunca foi gerado
    assert _entregar(api, de_novo) == "ok"


def test_the_panel_stops_an_order_and_the_audit_records_it(api):  # noqa: F811 (fixture)
    tok = _cliente(api)
    lid = _gerando(api, tok, n=2)
    r = api.post(f"{PAINEL}/api/lotes/{lid}/interromper", headers=ADM)
    assert r.status_code == 200 and r.json()["cancelados"] == 2
    lote = api.get(f"/api/lotes/{lid}", headers=h(tok)).json()
    assert lote["status"] == "concluido_com_falhas" and lote["interrompido_por"] == "equipe"  # sem o nome de acesso
    assert lote["nao_sairam"][0]["motivo"] == "interrompido_painel"
    (x,) = [x for x in api.get(f"{PAINEL}/api/historico", headers=ADM).json()["lotes"] if x["lote"] == lid]
    assert x["interrompido_em"] and x["nao_sairam"][0]["videos"] == 2
    with api.banco.tx() as c:
        assert c.one("SELECT COUNT(*) AS n FROM auditoria_admin WHERE acao = 'interromper'")["n"] == 1


def test_blocked_videos_go_to_quarantine_that_only_the_owner_opens_and_it_is_audited(api):  # noqa: F811 (fixture)
    tok = _cliente(api)
    lid = _gerando(api, tok, n=2)
    a, b = _job(api), _job(api)
    for j, enviado in ((a, True), (b, False)):  # b: o pod diz que enviou, mas o vídeo não está lá
        chave = j["quarentena_video_url"].split("armazem.teste/")[1].split("?")[0]
        assert chave.startswith("quarentena/") and "saida/" not in j["quarentena_folha_url"]
        if enviado:
            api.armazem.objetos[chave] = 5000
        corpo = {"erro": "barrado", "barrado": True, "quarentena": True, "nsfw": 0.91, "nsfw_quadros": 13}
        assert _concluir(api, j, status="erro", **corpo).json()["status"] == "erro"
    falhas = api.get(f"{PAINEL}/api/lotes/{lid}/falhas", headers=ADM).json()["videos"]
    assert [(f["quarentena"], f["nsfw"]) for f in falhas] == [(True, 0.91), (False, 0.91)]
    assert falhas[0]["motivo"] == motivos.MOTIVOS["filtro_imagem"] and "nota 0.91" in falhas[0]["erro"]
    r = api.get(f"{PAINEL}/api/jobs/{falhas[0]['job']}/quarentena", headers=ADM)
    assert r.status_code == 200 and "quarentena/" in r.json()["video"] and r.json()["expira_s"] == 600
    assert api.get(f"{PAINEL}/api/jobs/{falhas[1]['job']}/quarentena", headers=ADM).status_code == 404
    with api.banco.tx() as c:
        (aud,) = c.all("SELECT detalhe FROM auditoria_admin WHERE acao = 'ver_quarentena'")
        assert falhas[0]["arquivo"] in aud["detalhe"]
        c.run("UPDATE jobs SET quarentena_em = quarentena_em - 8 * 86400")  # a quarentena dura 7 dias
    assert api.get(f"{PAINEL}/api/jobs/{falhas[0]['job']}/quarentena", headers=ADM).status_code == 404
    lote = api.get(f"/api/lotes/{lid}", headers=h(tok)).json()  # o cliente vê o motivo, nunca a quarentena
    assert "quarentena" not in json.dumps(lote) and lote["nao_sairam"][0]["videos"] == 2


@pytest.mark.parametrize(
    ("erro", "barrado", "codigo"),
    [
        ("barrado pelo filtro de imagem (nota 0.91)", True, "filtro_imagem"),
        ("o produto mudou de cor ou de forma no vídeo (deriva de cor 61.0)", False, "conferencia"),
        ("corte brusco no meio do vídeo (salto de cor 30.0)", False, "conferencia"),
        ("conferência do 1º quadro reprovada (0.41)", False, "conferencia"),
        ("C1: termo bloqueado (armas)", False, "texto"),
        ("compilação do texto da foto falhou", False, "traducao"),
        ("download recusado (403)", False, "foto"),
        ("imagem inválida (aceitas: JPEG, PNG e WebP): x", False, "foto"),
        ("filtro de imagem: timm ausente", False, "tecnico"),
        ("filtro de imagem ausente: o vídeo não foi conferido", False, "tecnico"),
        ("prazo vencido nas tentativas", False, "tecnico"),
        ("interrompido pelo cliente", False, "interrompido_cliente"),
        ("interrompido no painel", False, "interrompido_painel"),
        ("fotos apagadas (prazo de 7 dias)", False, "prazo_fotos"),
    ],
)
def test_each_recorded_failure_maps_to_a_reason(erro, barrado, codigo):
    assert motivos.motivo({"status": "erro", "erro": erro, "barrado_em": 1 if barrado else None}) == codigo
    assert motivos.motivo({"status": "ok", "erro": erro}) is None


def test_the_text_refusal_names_the_category_never_the_word():
    assert "ele pede armas" in motivos.recusa_texto("C1: termo bloqueado (armas)")
    assert "ele pede pessoas reais ou famosas" in motivos.recusa_traducao("C1: termo bloqueado (pessoa_real)")
    assert "conteúdo que não aceitamos" in motivos.recusa_texto("C1: termo bloqueado (nova)")
