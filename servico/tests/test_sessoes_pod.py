"""Sessões de pod no relatório de GPUs (17/10/2026): o pod que volta da hibernação tem o mesmo nome (pod-<id>), e cada
subida é uma linha de `geradores`. Até aqui, o relatório ligava os jobs pelo nome e contava os vídeos de todas as
sessões em cada uma delas."""

import time

import pytest
from test_db import _aprovar, _lote

from render2go_servico.db import HORAS_MES, VOLUME_PARADO_USD_GB_MES


def _sessao(b, worker, tipo, volume_gb=70, disco_gb=120):
    g = b.gerador_subindo("NVIDIA L40S")
    b.gerador_criado(g, worker, "NVIDIA L40S", 1.09, "OC-AU-1", disco_gb=disco_gb, volume_gb=volume_gb)
    b.gerador_subida(g, tipo, versao="v1")
    b.gerador_pronto(g, worker, "NVIDIA L40S", 1.09)
    return g


def _gerar(b, worker, n, falhar_primeiro=False):
    """`n` vídeos certos no pod `worker`; com `falhar_primeiro`, o 1º falha uma vez (volta para a fila) antes."""
    for i in range(n):
        j = b.reivindicar(worker)
        assert j is not None and j["tipo"] == "render"
        if falhar_primeiro and i == 0:
            b.concluir(j["id"], worker, "erro", tentativa=j["tentativa_id"], erro="CUDA", repetir=True, segundos=30.0)
            j = b.reivindicar(worker)
        b.concluir(j["id"], worker, "ok", tentativa=j["tentativa_id"], segundos=50.0)


def _desligar(b, g, motivo):
    b.gerador_fim(g, motivo)
    b.gerador_motivo(g, motivo)
    b.gerador_desligado(g)


def test_two_sessions_of_the_same_pod_do_not_count_each_others_videos(banco):
    worker = "pod-ltuxxfgujgnpsm"
    _, _, lid = _lote(banco, n_fotos=5)
    _aprovar(banco, lid)
    a = _sessao(banco, worker, "fria")
    c = banco.reivindicar(worker)
    banco.definir_prompt(lid, "The camera slowly moves in.", "i2v@6")
    banco.concluir(c["id"], worker, "ok", tentativa=c["tentativa_id"], segundos=20.0)
    _gerar(banco, worker, 2)
    _desligar(banco, a, "hibernado (fila vazia)")
    b = _sessao(banco, worker, "retomada")  # a volta da hibernação: o mesmo nome, outra sessão
    _gerar(banco, worker, 3, falhar_primeiro=True)

    r = banco.relatorio_geradores(0)
    por_id = {x["id"]: x for x in r["geradores"]}
    sa, sb = por_id[a], por_id[b]
    # antes da correção: as duas sessões contavam os 5 vídeos (10 no total) e a tradução duas vezes
    assert (sa["videos_ok"], sb["videos_ok"]) == (2, 3)
    assert (sa["traducoes"], sb["traducoes"]) == (1, 0)
    assert (sa["sessao"], sb["sessao"]) == (1, 2)
    assert r["totais"]["videos_ok"] == 5 and r["totais"]["retomadas"] == 1
    # tentativas: na 2ª sessão, o vídeo que falhou uma vez conta duas tentativas, uma delas falha
    assert (sa["tentativas_render"], sa["tentativas_falhas"]) == (2, 0)
    assert (sb["tentativas_render"], sb["tentativas_falhas"]) == (4, 1)
    assert sb["segundos_tentativas"] == pytest.approx(30.0 + 3 * 50.0)
    assert sb["segundos_render"] == pytest.approx(150.0)  # só os vídeos certos, como antes
    assert r["totais"]["tentativas_render"] == 6 and r["totais"]["tentativas_falhas"] == 1
    # o 1º vídeo de cada sessão é o dela: a 1ª sessão não ganha o da 2ª
    assert sa["primeiro_video_em"] < sb["inicio"] <= sb["primeiro_video_em"]
    assert sa["primeira_entrega_s"] is not None and sb["primeira_entrega_s"] is not None
    assert not sa["incertezas"] and not sb["incertezas"]


def test_old_jobs_without_session_are_split_by_time_and_flagged(banco):
    worker = "pod-antigo"
    _, _, lid = _lote(banco, n_fotos=3)
    _aprovar(banco, lid)
    a = _sessao(banco, worker, "fria")
    c = banco.reivindicar(worker)
    banco.definir_prompt(lid, "p", "i2v@6")
    banco.concluir(c["id"], worker, "ok", tentativa=c["tentativa_id"])
    _gerar(banco, worker, 1)
    _desligar(banco, a, "hibernado (fila vazia)")
    b = _sessao(banco, worker, "retomada")
    _gerar(banco, worker, 2)
    with banco.tx() as cur:  # como os jobs e as sessões de antes de 17/10/2026: sem a sessão nem as tentativas
        cur.run("UPDATE jobs SET gerador_id = NULL")
        cur.run("DELETE FROM tentativas")
    r = banco.relatorio_geradores(0)
    por_id = {x["id"]: x for x in r["geradores"]}
    assert (por_id[a]["videos_ok"], por_id[b]["videos_ok"]) == (1, 2)  # pelo horário de cada tentativa
    assert "atribuicao_por_horario" in por_id[a]["incertezas"] and "sem_tentativas" in por_id[b]["incertezas"]
    assert por_id[b]["tentativas_render"] is None  # não se sabe: o registro por tentativa é de 17/10/2026
    assert r["totais"]["sessoes_incertas"] == 2 and r["totais"]["videos_ok"] == 3


def test_a_single_old_session_keeps_the_usual_attribution(banco):
    worker = "pod-unico"
    _, _, lid = _lote(banco, n_fotos=2)
    _aprovar(banco, lid)
    g = _sessao(banco, worker, "fria", volume_gb=None, disco_gb=None)
    c = banco.reivindicar(worker)
    banco.definir_prompt(lid, "p", "i2v@6")
    banco.concluir(c["id"], worker, "ok", tentativa=c["tentativa_id"])
    _gerar(banco, worker, 2)
    with banco.tx() as cur:
        cur.run("UPDATE jobs SET gerador_id = NULL")
    (x,) = banco.relatorio_geradores(0)["geradores"]
    assert x["id"] == g and x["videos_ok"] == 2 and "atribuicao_por_horario" not in x["incertezas"]
    assert "disco_desconhecido" in x["incertezas"] and x["disco_ligado_usd"] is None


def test_a_failed_resume_keeps_its_own_session_and_cost(banco):
    g = banco.gerador_subindo("NVIDIA L40S")
    banco.gerador_criado(g, "pod-hib", "NVIDIA L40S", 1.09, "EU-RO-1", disco_gb=120, volume_gb=70)
    with banco.tx() as cur:  # a volta ficou 10 min ligada sem ficar pronta
        cur.run("UPDATE geradores SET pod_criado_em = pod_criado_em - 600 WHERE id = ?", (g,))
    # a mesma subida liga outro pod (um do zero) depois de parar o hibernado de novo
    banco.gerador_criado(g, "pod-novo", "NVIDIA L40S", 1.09, "US-TX-3", disco_gb=120, volume_gb=70)
    r = banco.relatorio_geradores(0)
    falha = next(x for x in r["geradores"] if x["pod"] == "pod-hib")
    novo = next(x for x in r["geradores"] if x["pod"] == "pod-novo")
    assert falha["id"] != g and novo["id"] == g
    assert falha["estado"] == "falhou" and falha["ligado_s"] == pytest.approx(600, abs=5)
    assert falha["custo_usd"] == pytest.approx(1.09 * 600 / 3600, abs=0.002)
    assert "não ficou pronta" in falha["motivo"]
    # o disco do hibernado continua parado depois da volta que falhou (ele foi parado de novo)
    assert falha["parado_s"] is not None


def test_stopped_volume_is_charged_between_sessions_and_until_its_end(banco):
    worker = "pod-parado"
    a = _sessao(banco, worker, "fria")
    _desligar(banco, a, "hibernado (fila vazia)")
    b = _sessao(banco, worker, "retomada")
    with banco.tx() as cur:  # 2 h parado entre as sessões; a 2ª ficou 1 h ligada
        cur.run("UPDATE geradores SET pod_criado_em = ?, desligado_em = ? WHERE id = ?", (1000.0, 2000.0, a))
        cur.run("UPDATE geradores SET criado = criado, pod_criado_em = ? WHERE id = ?", (2000.0 + 7200, b))
    _desligar(banco, b, "hibernado (fila vazia)")
    with banco.tx() as cur:
        cur.run("UPDATE geradores SET desligado_em = ? WHERE id = ?", (2000.0 + 7200 + 3600, b))
    banco.volume_encerrado(worker, "versão anterior do ambiente ou dos modelos")
    with banco.tx() as cur:  # o disco foi encerrado 1 h depois da 2ª hibernação
        cur.run("UPDATE geradores SET volume_fim_em = ? WHERE id = ?", (2000.0 + 7200 + 7200, b))
    r = banco.relatorio_geradores(0)
    por_id = {x["id"]: x for x in r["geradores"]}
    hora_parado = 70 * VOLUME_PARADO_USD_GB_MES / HORAS_MES
    assert hora_parado == pytest.approx(0.019444, abs=1e-6)  # o faturamento de 12–17/10/2026
    assert por_id[a]["parado_s"] == pytest.approx(7200) and por_id[a]["disco_parado_usd"] == pytest.approx(
        2 * hora_parado, abs=1e-4
    )
    assert por_id[b]["parado_s"] == pytest.approx(3600) and por_id[b]["volume_fim_motivo"].startswith("versão")
    assert por_id[b]["disco_ligado_usd"] == pytest.approx(190 * 0.10 / HORAS_MES, abs=1e-4)  # 1 h ligada
    t = r["totais"]
    assert t["custo_total_usd"] == pytest.approx(t["custo_usd"] + t["disco_ligado_usd"] + t["disco_parado_usd"])


def test_report_panel_numbers_match_the_owner_example(banco):
    """O exemplo do painel em 17/10/2026: 796 s ligado a US$ 1,09/h = US$ 0,241 só de GPU; com 120 GB de contêiner e
    70 GB de volume ligados, mais US$ 0,0058. A média por vídeo do painel antigo (US$ 0,0482) era só a GPU."""
    g = _sessao(banco, "pod-exemplo", "fria")
    with banco.tx() as cur:
        cur.run("UPDATE geradores SET pod_criado_em = ?, desligado_em = ? WHERE id = ?", (1000.0, 1796.0, g))
    _desligar(banco, g, "hibernado (fila vazia)")
    with banco.tx() as cur:
        cur.run("UPDATE geradores SET desligado_em = ? WHERE id = ?", (1796.0, g))
        cur.run("UPDATE geradores SET volume_fim_em = ? WHERE id = ?", (1796.0, g))  # sem tempo parado aqui
    (x,) = banco.relatorio_geradores(0)["geradores"]
    assert x["custo_usd"] == pytest.approx(0.241, abs=1e-3)
    assert x["disco_ligado_usd"] == pytest.approx(190 * 0.10 / 720 * 796 / 3600, abs=1e-4)
    assert x["custo_total_usd"] == pytest.approx(0.2468, abs=1e-3)


def test_attempts_record_expired_leases_returns_and_cancellations(banco):
    worker = "pod-tentativas"
    _, _, lid = _lote(banco, n_fotos=2)
    _aprovar(banco, lid)
    g = _sessao(banco, worker, "fria")
    c = banco.reivindicar(worker)
    banco.definir_prompt(lid, "p", "i2v@6")
    banco.concluir(c["id"], worker, "ok", tentativa=c["tentativa_id"])
    j = banco.reivindicar(worker, lease_s=-1)  # o prazo vence: a tentativa termina como "prazo"
    j2 = banco.reivindicar(worker)
    assert j2["id"] == j["id"] and j2["tentativas"] == 2
    banco.devolver(j2["id"], worker, j2["tentativa_id"])  # pego adiantado e devolvido: não conta como tentativa
    j3 = banco.reivindicar(worker)
    banco.concluir(j3["id"], worker, "ok", tentativa=j3["tentativa_id"], segundos=40.0)
    with banco.tx() as cur:
        rs = cur.all("SELECT n, resultado, gerador_id FROM tentativas WHERE job_id = ? ORDER BY inicio", (j["id"],))
    assert [(r["n"], r["resultado"]) for r in rs] == [(1, "prazo"), (2, "devolvido"), (2, "ok")]
    assert {r["gerador_id"] for r in rs} == {g}
    (x,) = banco.relatorio_geradores(0)["geradores"]
    assert (x["tentativas_render"], x["tentativas_falhas"], x["videos_ok"]) == (2, 1, 1)


def test_client_feedback_separates_delivered_from_usable(banco):
    worker = "pod-retorno"
    cid, _, lid = _lote(banco, n_fotos=2)
    _aprovar(banco, lid)
    _sessao(banco, worker, "fria")
    c = banco.reivindicar(worker)
    banco.definir_prompt(lid, "p", "i2v@6")
    banco.concluir(c["id"], worker, "ok", tentativa=c["tentativa_id"])
    feitos = []
    for _ in range(2):
        j = banco.reivindicar(worker)
        banco.concluir(j["id"], worker, "ok", tentativa=j["tentativa_id"], segundos=50.0)
        feitos.append(j["id"])
    agora = time.time()
    with banco.tx() as cur:
        for jid, usavel in zip(feitos, (1, 0), strict=True):
            cur.run(
                "INSERT INTO retornos (job_id, lote_id, cliente_id, usavel, criado, atualizado)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (jid, lid, cid, usavel, agora, agora),
            )
    r = banco.relatorio_geradores(0)
    (x,) = r["geradores"]
    assert (x["videos_ok"], x["aproveitaveis"], x["recusados_cliente"], x["sem_retorno"]) == (2, 1, 1, 0)
    assert r["totais"]["custo_total_por_aproveitavel_usd"] == pytest.approx(r["totais"]["custo_total_usd"], abs=1e-4)


def test_hibernated_inventory_shows_what_this_version_would_terminate(tmp_path, monkeypatch, capsys):
    """Antes de uma atualização: o que esta versão do código faria com os hibernados (só leitura, sem tokens). Em
    17/10/2026 a versão mudou e o hibernado de 12/10 foi encerrado com o disco de volume, sem aviso."""
    import json as _json

    from render2go_worker import lote_nuvem as ln

    from render2go_servico import cli
    from render2go_servico.orquestrador import HIBERNADOS, inventario_hibernados

    atual = ln.versao_hibernacao()
    dados = {
        "hf4dbmglblw7sh": {"id": "hf4dbmglblw7sh", "token": "segredo-a" * 4, "versao": "83ade25e23f2a735",
                           "parado_em": 1000.0, "gpu": "NVIDIA L40S", "volume_gb": 70},
        "ltuxxfgujgnpsm": {"id": "ltuxxfgujgnpsm", "token": "segredo-b" * 4, "versao": atual, "parado_em": 2000.0,
                           "gpu": "NVIDIA L40S", "volume_gb": 70},
        "novo": {"id": "novo", "token": "segredo-c" * 4, "versao": atual, "parado_em": 3000.0},
    }  # fmt: skip
    (tmp_path / HIBERNADOS).write_text(_json.dumps(dados))
    api = "https://app.exemplo.com"
    prefixo = "render2go-fila-" + __import__("hashlib").sha256(api.encode()).hexdigest()[:6]
    pods = [
        {"id": "orfao1", "name": f"{prefixo}-3", "desiredStatus": "EXITED", "volumeInGb": 70},
        {"id": "ltuxxfgujgnpsm", "name": f"{prefixo}-1", "desiredStatus": "EXITED"},
        {"id": "outro", "name": "outro-servico", "desiredStatus": "EXITED"},
    ]
    inv = inventario_hibernados(tmp_path, maximo=1, pods_runpod=pods, api_url=api)
    por_id = {h["id"]: h for h in inv["hibernados"]}
    assert por_id["hf4dbmglblw7sh"]["sairia"].startswith("versão anterior (83ade25e23f2a735 →")
    assert por_id["ltuxxfgujgnpsm"]["sairia"] == "acima de RENDER2GO_PODS_HIBERNADOS"  # o parado há mais tempo
    assert por_id["novo"]["sairia"] is None
    assert [o["id"] for o in inv["orfaos"]] == ["orfao1"]
    assert por_id["ltuxxfgujgnpsm"]["usd_dia_parado"] == pytest.approx(70 * 0.20 / 720 * 24, abs=1e-4)
    assert "segredo" not in _json.dumps(inv)
    # o comando imprime o mesmo, sem os tokens
    monkeypatch.setattr(ln, "CACHE", tmp_path.parent)
    (tmp_path.parent / "orquestrador").mkdir(exist_ok=True)
    (tmp_path.parent / "orquestrador" / HIBERNADOS).write_text(_json.dumps(dados))
    monkeypatch.setenv("RENDER2GO_PODS_HIBERNADOS", "2")
    assert cli.main(["hibernados"]) == 0
    saida = capsys.readouterr().out
    assert "segredo" not in saida and "SERIA ENCERRADO (versão anterior" in saida and "2 pod(s)" not in saida
    assert "1 pod(s) seriam encerrados" in saida


def test_startup_stage_times_are_summarized_per_gpu_datacenter_and_kind(banco):
    for tipo, pronto in (("fria", 160.0), ("fria", 200.0), ("retomada", 40.0)):
        g = _sessao(banco, f"pod-{tipo}-{pronto:.0f}", tipo)
        etapas = {"ambiente_pronto": pronto - 60, "pronto": pronto, "nao_listada": 1.0, "texto": "x"}
        if tipo == "retomada":
            etapas = {"retomada_links": 12.0, "retomada_apt": 30.0, "pronto": pronto}
        banco.gerador_etapas(g, etapas)
    m = banco.metricas(0)["subidas"]
    fria, volta = m["NVIDIA L40S | OC-AU-1 | fria"], m["NVIDIA L40S | OC-AU-1 | retomada"]
    assert fria["bootstrap_pronto_s"] == {"n": 2, "p50": 180.0} and "bootstrap_nao_listada_s" not in fria
    assert volta["bootstrap_retomada_links_s"]["p50"] == 12.0 and "bootstrap_ambiente_pronto_s" not in volta
