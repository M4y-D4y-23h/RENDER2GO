"""Capacidade da fila, política de ociosidade, métricas e simulação (14/10/2026; docs/15, seção 3)."""

import json
import time

import pytest
import test_orquestrador as base
from render2go_worker import lote_nuvem as ln

from render2go_servico import capacidade as cap
from render2go_servico import simulacao as sim

L40S, PRO = "NVIDIA L40S", "NVIDIA RTX PRO 6000 Blackwell Server Edition"
orq = base.orq


def _linha(pend=0, anda=0, trad=0, gasto=0.0, qualidade="rapida", duracao=5):
    return {"pedido": {"qualidade": qualidade, "duracao": duracao}, "pendentes": pend, "em_andamento": anda,
            "traducoes": trad, "andamento_s": gasto}  # fmt: skip


def test_a_translation_counts_its_time_not_a_whole_video():
    """Antes, a tradução do pedido contava como um vídeo: 1 vídeo + a tradução = 2 vídeos de trabalho."""
    t = cap.trabalho([_linha(pend=1, trad=1)], L40S, cap.Tempos())
    assert t.videos == 1 and t.traducoes == 1
    assert t.segundos == pytest.approx(ln.segundos_por_video(L40S, "rapida", 5) + cap.TRADUCAO_S)
    # um pedido que só espera a tradução ainda pede um pod (a tradução nunca fica sem capacidade)
    assert cap.pods_desejados(cap.trabalho([_linha(trad=1)], L40S, cap.Tempos()), None, 8) == 1
    assert cap.pods_desejados(cap.trabalho([], L40S, cap.Tempos()), None, 8) == 0


def test_a_running_video_counts_what_is_left_with_a_floor():
    s = ln.segundos_por_video(L40S, "alta", 5)
    quase = cap.trabalho([_linha(anda=1, gasto=0.9 * s, qualidade="alta")], L40S, cap.Tempos())
    assert quase.segundos == pytest.approx(cap.FRACAO_MIN_ANDAMENTO * s)  # nunca menos que o piso
    metade = cap.trabalho([_linha(anda=2, gasto=s, qualidade="alta")], L40S, cap.Tempos())
    assert metade.segundos == pytest.approx(s)  # dois vídeos na metade = um vídeo de trabalho


def test_the_history_moves_the_table_only_within_limits():
    tabela = ln.segundos_por_video(L40S, "rapida", 5)
    assert cap.Tempos({(L40S, "rapida", 5): [40.0] * 4}).video(L40S, "rapida", 5) == tabela  # poucas amostras
    assert cap.Tempos({(L40S, "rapida", 5): [40.0] * 9}).video(L40S, "rapida", 5) == 40.0
    assert cap.Tempos({(L40S, "rapida", 5): [5.0] * 9}).video(L40S, "rapida", 5) == 0.5 * tabela  # limite
    assert cap.Tempos({(L40S, "rapida", 5): [900.0] * 9}).video(L40S, "rapida", 5) == 2 * tabela
    # GPU sem medida: o pior caso (lote_nuvem.PIOR_S)
    assert cap.Tempos().video("GPU nova", "rapida", 5) == ln.PIOR_S["rapida"]


def test_a_faster_gpu_counts_as_more_than_one_pod():
    t = cap.Tempos()
    assert cap.equivalentes([PRO], 0, L40S, t) == pytest.approx(55 / 28)
    assert cap.equivalentes([L40S, L40S], 1, L40S, t) == 3.0


def test_the_old_input_format_still_works(orq):
    """Linhas sem `traducoes` (o formato de antes) contam só os vídeos, como antes."""
    grande = [{"pedido": {"qualidade": "rapida", "duracao": 5}, "pendentes": 5000, "em_andamento": 0}]
    assert orq(max_pods=30).desejados(grande) == 30


def test_the_queue_separates_translations_running_time_and_videos(banco):
    lid = base._lote(banco, 3, plano="pago")
    (linha,) = banco.trabalho()
    assert (linha["pendentes"], linha["em_andamento"], linha["traducoes"]) == (3, 0, 1)  # antes: 4 "vídeos"
    comp = banco.reivindicar("pod-1")
    banco.definir_prompt(lid, "The camera slowly dollies in.", "i2v@5")
    banco.concluir(comp["id"], "pod-1", "ok", tentativa=comp["tentativa_id"])
    job = banco.reivindicar("pod-1")
    with banco.tx() as c:  # o vídeo foi para a GPU há 20 s
        c.run("UPDATE jobs SET gpu_em = ? WHERE id = ?", (time.time() - 20, job["id"]))
    (linha,) = banco.trabalho()
    assert (linha["pendentes"], linha["em_andamento"], linha["traducoes"]) == (2, 1, 0)
    assert 19 <= float(linha["andamento_s"]) <= 30


def test_idle_policy_is_fixed_by_default_and_hysteresis_is_opt_in():
    fixa = cap.PoliticaOciosidade(curto_s=180)
    fixa.desligou_ocioso(0)
    fixa.nova_demanda(30)
    assert fixa.ocioso_s() == 180 and "fixa" in fixa.descricao
    h = cap.PoliticaOciosidade(curto_s=60, longo_s=180, janela_s=600, episodios=2)
    assert h.ocioso_s() == 60
    h.desligou_ocioso(1000)
    h.nova_demanda(1300)  # voltou 5 min depois de um pod sair por falta de pedido
    assert h.ocioso_s() == 180
    h.nova_demanda(9000)  # sem volta rápida: conta um episódio
    assert h.ocioso_s() == 180
    h.nova_demanda(20000)
    assert h.ocioso_s() == 60  # voltou para o curto


def test_the_long_idle_comes_only_from_the_environment(monkeypatch):
    from render2go_servico.orquestrador import ConfigOrq

    monkeypatch.setenv("RENDER2GO_API_URL", "https://api.teste")
    monkeypatch.setenv("RENDER2GO_R2_ENDPOINT", "https://r2.teste")
    monkeypatch.delenv("RENDER2GO_POD_OCIOSO_LONGO_MIN", raising=False)
    assert ConfigOrq.do_ambiente().ocioso_longo_s is None
    monkeypatch.setenv("RENDER2GO_POD_OCIOSO_LONGO_MIN", "3")
    assert ConfigOrq.do_ambiente().ocioso_longo_s == 180


def test_metrics_split_by_gpu_resolution_and_startup_kind_without_client_data(banco):
    lid = base._lote(banco, 2, plano="pago")
    g = banco.gerador_subindo(L40S)
    banco.gerador_criado(g, "pod-1", L40S, 1.09, "US-TX-3")
    banco.gerador_subida(g, "fria", versao="abc")
    banco.gerador_subida(g, agente=True)
    comp = banco.reivindicar("pod-1")
    banco.definir_prompt(lid, "The camera slowly dollies in.", "i2v@5")
    banco.concluir(comp["id"], "pod-1", "ok", tentativa=comp["tentativa_id"])
    for _ in range(2):
        j = banco.reivindicar("pod-1")
        banco.concluir(j["id"], "pod-1", "ok", tentativa=j["tentativa_id"], segundos=55.0,
                       etapas={"render": 52.0, "conferencia": 1.5})  # fmt: skip
    m = banco.metricas(0)
    (subida,) = m["subidas"].items()
    assert subida[0] == f"{L40S} | US-TX-3 | fria" and subida[1]["agente_s"]["n"] == 1
    (videos,) = m["videos"].items()
    assert videos[0] == f"{L40S} | 480p | 3 s" and videos[1]["n"] == 2
    assert videos[1]["etapas"]["render"] == {"n": 2, "p50": 52.0}  # sem p95 com poucas amostras
    texto = json.dumps(m)
    assert "Loja" not in texto and "entrada/" not in texto  # nada do cliente nem da foto
    assert banco.tempos_por_video(0) == {(L40S, "rapida", 3): [55.0, 55.0]}


def test_the_simulation_is_deterministic_and_respects_the_rules():
    c = sim.Cenario("teste", [sim.Pedido(0, "c0", 0, 6, bloqueados=2), sim.Pedido(1, "c1", 10, 4, pagos=1)],
                    max_pods=2)  # fmt: skip
    resultados = []
    for _ in range(2):
        s = sim.Sim(c, cap.PoliticaOciosidade(curto_s=180), True, semente=7)
        s.rodar()
        r = sim.Resultado("teste", "fixa", "nova")
        s.resultado(r)
        resultados.append(r.resumo())
    assert resultados[0] == resultados[1]  # a mesma semente, o mesmo resultado
    r = resultados[0]
    assert r["videos"] == 4 + 1  # sem as fotos bloqueadas; só o que o saldo paga
    assert r["pods"] <= 2 and r["nao_entregues"] == 0 and r["custo_usd"] > 0


def test_the_simulated_new_delivery_wastes_no_render_and_a_cancelled_order_stops():
    falha = sim.Cenario("envio", [sim.Pedido(0, "c0", 0, 10)], falha_envio=0.3)
    desperdicio = {}
    for nova in (True, False):
        r = sim.Resultado("envio", "fixa", str(nova))
        for semente in range(5):
            s = sim.Sim(falha, cap.PoliticaOciosidade(), nova, semente)
            s.rodar()
            s.resultado(r)
        desperdicio[nova] = r.desperdicio_s
    assert desperdicio[True] == 0 < desperdicio[False]
    cancelado = sim.Cenario("cancelado", [sim.Pedido(0, "c0", 0, 30, cancelado_em=60.0)])
    s = sim.Sim(cancelado, cap.PoliticaOciosidade(), True, 1)
    s.rodar()
    assert s.pedidos[0].iniciados == 0 and s.pedidos[0].prontos == 0  # cancelado antes de os pods subirem


def test_shorter_idle_never_costs_more_in_the_simulated_bursts():
    linhas = {x["politica"]: x for x in sim.simular(sementes=3) if x["cenario"].startswith("rajada")}
    assert linhas["fixa 60 s"]["custo_usd"] <= linhas["fixa 180 s (padrão)"]["custo_usd"]
    assert linhas["fixa 60 s"]["gpu_ociosa_s"] < linhas["fixa 180 s (padrão)"]["gpu_ociosa_s"]
