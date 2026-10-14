"""Orquestrador (14/10/2026): orçamento com tudo o que pode estar cobrando e pods hibernados que não se perdem
por falta de vaga num mesmo episódio de demanda. Provedores falsos; nenhuma chamada de rede."""

import json
import threading
import time

import test_orquestrador as base
from render2go_worker import lote_nuvem as ln
from render2go_worker import runpod_pods
from test_orquestrador import _esperar, _hibernado, _lote

from render2go_servico import orquestrador as om
from render2go_servico.orquestrador import PodFila

orq = base.orq  # a mesma fábrica de orquestradores com pods falsos


def _pod_no_inventario(o, pid, usd_h, vivo=True):
    info = ln.PodInfo(pid, "http://127.0.0.1:9", "t" * 40, usd_h, "teste", 1, ln.GPUS_PADRAO[0])
    pod = ln.Pod(info, ln.Agente(info.url, info.token), vivo=vivo)
    with o.pods_ln.lock:
        o.pods_ln.pods.append(pod)
    return pod


def test_a_draining_pod_counts_in_the_hourly_cap(orq, banco):
    """Reprodução (14/10/2026): o gravador terminando o cache (em `encerrando`) ainda cobra a GPU. Antes, o teto por
    hora somava só os ativos e os que subiam: com o teto de US$ 2,50/h e um pod de US$ 2/h encerrando, outro de
    US$ 2/h subia (US$ 4/h cobrando)."""
    o = orq(max_usd_h=2.5)
    pod = _pod_no_inventario(o, "gravador", 2.0)
    o.encerrando["gravador"] = PodFila(pod, ate=0)
    _lote(banco, 2, plano="pago")
    r = o.ciclo()
    assert r["subindo"] == 0 and o.provedor.criados == []
    assert r["faturaveis_usd_h"] == 2.0  # o painel vê o que pode estar cobrando
    # ele não conta como capacidade: a fila continua pedindo um pod que gere vídeos
    assert r["desejados"] == 1


def test_a_close_that_was_not_confirmed_still_counts(orq, banco):
    o = orq(max_usd_h=2.5)
    pod = _pod_no_inventario(o, "x", 2.0, vivo=False)
    pod.encerramento = ln.ENC_FALHOU  # o DELETE falhou: pode estar ligado (a nova tentativa é mais tarde)
    pod.enc_proxima = time.monotonic() + 3600
    _lote(banco, 2, plano="pago")
    assert o.ciclo()["subindo"] == 0
    pod.encerramento = ln.ENC_CONFIRMADO  # o fim foi observado: libera o teto
    assert o.ciclo()["subindo"] == 1
    assert _esperar(lambda: o.subindo == 0)
    o.encerrar_todos("fim do teste")


def test_concurrent_cycles_do_not_double_count_a_creation(orq, banco):
    """A reserva de quem sobe vale até o pod existir; depois conta o pod (nunca os dois)."""
    o = orq(max_usd_h=100)
    _lote(banco, 2, plano="pago")
    o.ciclo()
    assert _esperar(lambda: len(o.ativos) == 1)
    assert o.faturaveis_usd_h() == 2.0 and not o.reservas
    o.encerrar_todos("fim do teste")


def test_a_full_host_counts_one_failure_per_demand_episode(orq, banco):
    """Reprodução (14/10/2026): três chamadas seguidas a `_retomar_algum` no mesmo estado de demanda, com o host
    sem GPU livre, davam as contagens 1, 2 e o descarte do pod com o disco de volume. A regra do ADR 0011 é por
    pedidos distintos: agora, uma falha por episódio de demanda, e um intervalo antes de tentar o mesmo pod."""
    o = orq(hibernados=1)
    _hibernado(o, "velho")
    o.provedor.sem_gpu.add("velho")
    tentativas = []
    retomar = o.provedor.retomar
    o.provedor.retomar = lambda pid: (tentativas.append(pid), retomar(pid))[1]
    o.nova_demanda()
    for _ in range(3):
        assert o._retomar_algum(lambda i: None) is None
    assert "velho" in o.hib and "velho" not in o.provedor.encerrados
    assert o.hib["velho"]["falhas"] == 1 and tentativas == ["velho"]  # o intervalo segurou as outras
    assert o.hib["velho"]["nao_antes"] > o.hib["velho"]["ultima_falha_em"]


def test_failures_in_distinct_episodes_still_discard_the_pod(orq, banco, monkeypatch):
    o = orq(hibernados=1)
    _hibernado(o, "velho")
    o.provedor.sem_gpu.add("velho")
    agora = [1_000_000.0]
    monkeypatch.setattr(om.time, "time", lambda: agora[0])
    for i in range(om.FALHAS_RETOMADA):
        o.nova_demanda()
        agora[0] += om.ESPERA_RETOMADA_S + 1
        assert o._retomar_algum(lambda i: None) is None
        if i < om.FALHAS_RETOMADA - 1:
            assert o.hib["velho"]["falhas"] == i + 1
    assert "velho" not in o.hib and "velho" in o.provedor.encerrados


def test_the_cooldown_and_the_episode_survive_a_restart(orq, banco):
    o = orq(hibernados=1)
    _hibernado(o, "velho")
    o.provedor.sem_gpu.add("velho")
    o.nova_demanda()
    o._salvar_hibernados()
    assert o._retomar_algum(lambda i: None) is None
    episodio = o.episodio
    novo = orq(hibernados=1, provedor=o.provedor)  # o processo reiniciou: lê o mesmo estado em disco
    assert novo.episodio == episodio and novo.hib["velho"]["falhas"] == 1
    assert novo._retomar_algum(lambda i: None) is None  # ainda no intervalo: nem tenta
    assert novo.hib["velho"]["falhas"] == 1


def test_simultaneous_resumes_take_each_hibernated_pod_once(orq, banco):
    o = orq(hibernados=2)
    _hibernado(o, "a")
    _hibernado(o, "b")
    o.provedor.sem_gpu.update({"a", "b"})
    o.nova_demanda()
    tentativas = []
    retomar = o.provedor.retomar
    trava = threading.Lock()

    def contar(pid):
        with trava:
            tentativas.append(pid)
        return retomar(pid)

    o.provedor.retomar = contar
    ts = [threading.Thread(target=o._retomar_algum, args=(lambda i: None,)) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(tentativas) == ["a", "b"]  # cada um uma vez, mesmo com 4 subidas ao mesmo tempo
    assert o.hib["a"]["falhas"] == o.hib["b"]["falhas"] == 1


def test_a_corrupt_volume_still_discards_at_once(orq, banco):
    o = orq(hibernados=1)
    env = {"AGENT_TOKEN": "a" * 40, "DEADMAN_S": "600", "DEADMAN_ACAO": "parar"}
    info = o.provedor.criar("teste", [runpod_pods.Opcao(ln.GPUS_PADRAO[0], "secure")], 1, env, volume_gb=70)
    o.provedor.parar(info.id)
    o.hib[info.id] = {"id": info.id, "versao": ln.versao_hibernacao(), "falhas": 0, "url": info.url,
                      "token": info.token, "usd_h": 2.0, "gpu": ln.GPUS_PADRAO[0], "gpus": 1}  # fmt: skip
    o.remoto.instalar = "echo FALHOU disco de volume: SHA-256 > {raiz}/estado"
    o.nova_demanda()
    assert o._retomar_algum(lambda i: None) is None
    assert info.id not in o.hib and info.id in o.provedor.encerrados


def test_a_failed_terminate_of_a_hibernated_pod_is_retried(orq, banco):
    """Antes, o descarte de um hibernado que falhava no DELETE tirava o pod do conjunto e do controle: o disco
    seguia cobrando até a próxima partida. Agora ele fica no inventário do lote_nuvem e é tentado de novo."""
    o = orq(hibernados=1)
    _hibernado(o, "velho")
    falhas = [OSError("rede caiu")]
    encerrar = o.provedor.encerrar

    def instavel(pid):
        if falhas:
            raise falhas.pop()
        encerrar(pid)

    o.provedor.encerrar = instavel
    o._descartar_hibernado("velho", "teste")
    assert "velho" not in o.hib and "velho" not in o.provedor.encerrados
    pendentes = json.loads((o.cfg.estado / ln.ESTADO / "pods.json").read_text())
    assert [p["id"] for p in pendentes] == ["velho"]
    o.pods_ln.reconciliar(forcar=True)
    assert "velho" in o.provedor.encerrados


def test_the_cache_wait_has_a_cost_budget(orq, monkeypatch):
    """Esperar o cache (volume/espelho) é opcional: com a GPU cara, o prazo cai pelo orçamento do cache."""
    o = orq()
    barato = ln.PodInfo("a", "http://x", "t", 1.0, "d", 1)
    caro = ln.PodInfo("b", "http://x", "t", 6.0, "d", 1)
    pa, pb = ln.Pod(barato, ln.Agente("http://x", "t")), ln.Pod(caro, ln.Agente("http://x", "t"))
    assert o.pods_ln.prazo_cache(pa) == ln.ESPERA_GUARDAR_S
    assert o.pods_ln.prazo_cache(pb) == ln.CACHE_MAX_USD / 6.0 * 3600 < ln.ESPERA_GUARDAR_S


def test_the_gpu_report_counts_a_pending_close_until_it_is_observed(orq, banco):
    o = orq()
    gid = banco.gerador_subindo(ln.GPUS_PADRAO[0])
    banco.gerador_criado(gid, "pod-x", ln.GPUS_PADRAO[0], 3600.0)
    banco.gerador_fim(gid, "fila vazia")
    pod = _pod_no_inventario(o, "x", 3600.0, vivo=False)
    pod.encerramento = ln.ENC_FALHOU
    o._fechamento(pod)
    banco.gerador_desligado(gid)  # o caminho antigo não fecha o que o ciclo de vida acompanha
    (g,) = banco.relatorio_geradores(0)["geradores"]
    assert g["fechamento"] == "falhou" and g["desligado_em"] is None and g["ligado_estimado"]
    assert banco.relatorio_geradores(0)["totais"]["fechando"] == 1
    antes = g["custo_usd"]
    time.sleep(0.05)
    assert banco.relatorio_geradores(0)["geradores"][0]["custo_usd"] > antes  # conta até agora
    pod.encerramento, pod.confirmacao = ln.ENC_CONFIRMADO, "404"
    o._fechamento(pod)
    (g,) = banco.relatorio_geradores(0)["geradores"]
    assert g["fechamento"] == "confirmado" and g["confirmacao"] == "404" and g["desligado_em"]
    assert banco.relatorio_geradores(0)["totais"]["fechando"] == 0
