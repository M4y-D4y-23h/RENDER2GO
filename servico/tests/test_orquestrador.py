"""Orquestrador com pods falsos: agentes reais em 127.0.0.1 (como nos testes do lote_nuvem)."""

import json
import os
import threading
import time

import pytest
from render2go_worker import lote_nuvem as ln
from render2go_worker import pod_agente, runpod_pods

from render2go_servico.orquestrador import ConfigOrq, Orquestrador

_dormir = time.sleep


class Provedor:
    """Pods falsos: agentes reais em 127.0.0.1, com desligamento automático reprogramável. `parar` e
    `retomar` imitam a hibernação (ADR 0011): o agente volta na mesma porta, e o disco do contêiner
    (a raiz do pod, com fila.fim e o estado) é apagado na parada."""

    def __init__(self, saldo=100.0, preco=2.0, preco_real=None):
        self.criados, self.encerrados, self.envs, self.servidores = [], [], [], {}
        self.parados, self.retomados, self.volumes, self.deadman = [], [], {}, {}
        self.sem_gpu: set[str] = set()
        self._saldo, self._preco = saldo, preco
        self._real = preco if preco_real is None else preco_real
        self._env, self._porta, self.raiz = {}, {}, None

    def preco(self, gpu, nuvem):
        return self._preco

    def saldo(self):
        return self._saldo

    def _ligar(self, pid, env, porta=0):
        d = pod_agente.Desligamento(float(env["DEADMAN_S"]), env.get("DEADMAN_ACAO", "encerrar"), log=os.devnull)
        srv = pod_agente.servidor(env["AGENT_TOKEN"], "127.0.0.1", porta, deadman=d)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servidores[pid], self.deadman[pid] = srv, d
        self._porta[pid] = srv.server_address[1]

    def criar(self, nome, opcoes, gpus, env, volume_gb=0):
        assert all(o.nuvem == "secure" for o in opcoes)  # regra do projeto
        pid = f"p{len(self.criados) + 1}"
        self.criados.append(pid)
        self.envs.append(env)
        self.volumes[pid], self._env[pid] = volume_gb, env
        self._ligar(pid, env)
        url = f"http://127.0.0.1:{self._porta[pid]}"
        return ln.PodInfo(pid, url, env["AGENT_TOKEN"], self._real, f"teste/{nome}", gpus)

    def encerrar(self, pod_id):
        self.encerrados.append(pod_id)
        srv = self.servidores.pop(pod_id, None)
        if srv:
            srv.shutdown()
            srv.server_close()

    def parar(self, pod_id):
        assert self.volumes.get(pod_id), "só o pod com disco de volume hiberna"
        self.parados.append(pod_id)
        srv = self.servidores.pop(pod_id)
        srv.shutdown()
        srv.server_close()
        if self.raiz:  # a parada apaga o disco do contêiner
            for nome in ("fila.fim", "fila.motivo", "estado"):
                (self.raiz / nome).unlink(missing_ok=True)

    def retomar(self, pod_id):
        if pod_id in self.sem_gpu:
            raise runpod_pods.SemVaga(f"pod {pod_id}: sem GPU livre no host")
        self.retomados.append(pod_id)
        self._ligar(pod_id, self._env[pod_id], self._porta[pod_id])
        return self._real

    def situacao(self, pod_id):
        if pod_id in self.encerrados:
            return "TERMINATED"
        return "RUNNING" if pod_id in self.servidores else "EXITED"


def _lote(banco, n, plano="teste"):
    cid, _ = banco.criar_cliente("Loja", plano=plano)
    fotos = [
        {"nome": f"f{i}", "tipo": "image/jpeg", "bytes": 10, "chave": f"entrada/{cid}/{{lote}}/f{i}.jpg"}
        for i in range(n)
    ]
    lid = banco.criar_lote(
        cid, {"prompt": "a câmera se aproxima", "receita": "produto-close", "qualidade": "rapida", "duracao": 3}, fotos
    )
    banco.confirmar_envio(lid, {f["chave"]: 10 for f in banco.fotos(lid)})
    banco.aprovar(
        lid,
        "dono",
        [{"foto_id": f["id"], "variacao": 1, "seed": 1, "arquivo": f"{f['nome']}-v1.mp4"} for f in banco.fotos(lid)],
    )
    return lid


@pytest.fixture
def orq(banco, tmp_path, monkeypatch):
    monkeypatch.setattr(ln.time, "sleep", lambda s: _dormir(min(s, 0.05)))
    monkeypatch.setattr(ln, "ESPERA_AGENTE_S", 5)

    def fazer(provedor=None, fila_cmd="sleep 0.3", **cfg):
        c = ConfigOrq(
            api_url="https://api.teste",
            worker_token="w" * 40,
            hosts_permitidos="r2.teste",
            estado=tmp_path / "orq",
            fila_cmd=fila_cmd,
            **cfg,
        )
        remoto = ln.Remoto(
            raiz=str(tmp_path / "pod"),
            verificar_cuda=False,
            instalar="echo PRONTO > {raiz}/estado; echo GUARDADO > {raiz}/guardado",
            servicos="echo '[render2go] pronto'",
        )
        repo = tmp_path / "repo.tgz"
        repo.write_bytes(b"repo")
        provedor = provedor or Provedor()
        provedor.raiz = tmp_path / "pod"
        return Orquestrador(banco, c, provedor, remoto, repo_tgz=repo)

    return fazer


def _esperar(cond, s=20):
    fim = time.monotonic() + s
    while time.monotonic() < fim:
        if cond():
            return True
        _dormir(0.05)
    return False


def test_no_work_no_pods(orq):
    o = orq()

    def sem_consulta(gpu, nuvem):
        raise AssertionError("fila vazia não consulta o preço")

    o.provedor.preco = sem_consulta
    assert o.ciclo() == {"desejados": 0, "ativos": 0, "subindo": 0}
    assert o.provedor.criados == []


def test_a_big_queue_asks_for_up_to_the_configured_number_of_pods(orq):
    """Fila grande (5.000 vídeos de 5 s em 480p): o teto é RENDER2GO_MAX_PODS. Antes de 12/10/2026, a
    regra do lote_nuvem parava em 8, mesmo com RENDER2GO_MAX_PODS=30 (o docs/13 prometia 30)."""
    trabalho = [{"pedido": {"qualidade": "rapida", "duracao": 5}, "pendentes": 5000, "em_andamento": 0}]
    assert orq().desejados(trabalho) == ln.MAX_PODS_AUTO == 8  # sem RENDER2GO_MAX_PODS: os 8 de sempre
    assert orq(max_pods=30).desejados(trabalho) == 30
    assert orq(max_pods=4).desejados(trabalho) == 4
    poucos = [{"pedido": {"qualidade": "rapida", "duracao": 5}, "pendentes": 20, "em_andamento": 0}]
    assert orq(max_pods=30).desejados(poucos) == 1  # cada pod com pelo menos 10 min de vídeos


def test_each_cycle_marks_the_loop_alive(orq):
    o = orq(intervalo_s=0.05)  # o healthcheck do compose confere a idade do arquivo "vivo"
    t = threading.Thread(target=o.rodar, daemon=True)
    t.start()
    assert _esperar(lambda: (o.cfg.estado / "vivo").exists())
    o.parar.set()
    t.join(5)
    assert not t.is_alive()


def test_pods_follow_the_queue_and_shut_down_when_the_worker_ends(orq, banco):
    _lote(banco, 50)  # 50 vídeos de 3 s na L40S: ~20 min de GPU → 2 pods (cada um com ≥ 10 min)
    o = orq()
    assert o.ciclo()["desejados"] == 2
    assert _esperar(lambda: len(o.ativos) == 2)
    env = o.provedor.envs[0]
    assert int(env["DEADMAN_S"]) <= 2 * 3600 and "RENDER2GO_WORKER_TOKEN" not in env  # token não vai no env do pod
    # o worker recebeu o arquivo de ambiente com o token e os hosts permitidos
    pf = next(iter(o.ativos.values()))
    _, out = pf.pod.agente.exec(f"cat {o.remoto.raiz}/fila.env", 10)
    assert "RENDER2GO_SAFETY=c1-revisao-humana" in out and "RENDER2GO_ALLOWED_URL_HOSTS=r2.teste" in out
    # o worker falso termina; com a fila zerada, o próximo ciclo encerra os pods e não sobe outros
    with banco.tx() as c:
        c.run("UPDATE jobs SET status = 'ok'")
    assert _esperar(
        lambda: all(
            "FIM" in p.pod.agente.exec(f"cat {o.remoto.raiz}/fila.fim 2>/dev/null", 10)[1] for p in o.ativos.values()
        )
    )
    assert o.ciclo() == {"desejados": 0, "ativos": 0, "subindo": 0}
    assert sorted(o.provedor.encerrados) == ["p1", "p2"]
    # cada pod ficou registrado para a previsão do cliente: subiu, ficou pronto e foi encerrado
    with banco.tx() as c:
        linhas = c.all("SELECT worker, estado, pronto_em FROM geradores ORDER BY worker")
    assert [(x["worker"], x["estado"]) for x in linhas] == [("pod-p1", "encerrado"), ("pod-p2", "encerrado")]
    assert all(x["pronto_em"] for x in linhas)
    # relatório de GPUs: criação e desligamento registrados, com preço e tempo ligado
    rel = banco.relatorio_geradores(0)
    assert rel["totais"]["pods"] == 2 and rel["totais"]["ligados_agora"] == 0
    for g in rel["geradores"]:
        assert g["usd_h"] == 2.0 and g["desligado_em"] and not g["ligado_estimado"]
        assert g["custo_usd"] == pytest.approx(2.0 * g["ligado_s"] / 3600, abs=1e-4)


def test_a_pod_that_fails_installation_still_shows_its_id_and_cost(orq, banco):
    _lote(banco, 2, plano="pago")
    o = orq()
    o.remoto.instalar = "echo FALHOU instalação de teste > {raiz}/estado"
    assert o.ciclo()["subindo"] == 1
    assert _esperar(lambda: o.subindo == 0)
    assert o.provedor.encerrados == ["p1"] and not o.ativos
    (g,) = banco.relatorio_geradores(0)["geradores"]
    assert (g["pod"], g["estado"], g["motivo"], g["usd_h"]) == ("pod-p1", "falhou", "não subiu", 2.0)
    assert g["desligado_em"] and g["ligado_s"] > 0 and g["custo_usd"] is not None and g["videos_ok"] == 0


def test_services_and_worker_start_while_models_still_download(orq, banco, tmp_path):
    _lote(banco, 2, plano="pago")
    o = orq(fila_cmd="echo '{raiz} {modelos_ate} {ocioso}' > {raiz}/fila.args; sleep 30")
    pronto = tmp_path / "libera"
    o.remoto.instalar = (  # o ambiente fica pronto logo; os modelos só quando o teste liberar
        "echo AMBIENTE > {raiz}/estado; (while [ ! -e " + str(pronto) + " ]; do sleep 0.05; done;"
        " echo PRONTO > {raiz}/estado) > /dev/null 2>&1 &"
    )
    o.remoto.servicos = "echo REQUIRE_MODELS=${REQUIRE_MODELS:-1}; echo '[render2go] pronto'"
    assert o.ciclo()["subindo"] == 1
    assert _esperar(lambda: len(o.ativos) == 1)  # worker no ar antes do PRONTO
    pf = next(iter(o.ativos.values()))
    raiz = o.remoto.raiz
    assert pf.pod.agente.exec(f"cat {raiz}/estado", 10)[1].strip() == "AMBIENTE"
    assert "REQUIRE_MODELS=0" in pf.pod.agente.exec(f"cat {raiz}/servicos.log", 10)[1]
    args = pf.pod.agente.exec(f"cat {raiz}/fila.args", 10)[1].split()
    assert args[0] == raiz and int(args[1]) > time.time() + 600 and args[2] == "180"
    pronto.touch()
    assert _esperar(lambda: "PRONTO" in pf.pod.agente.exec(f"cat {raiz}/estado", 10)[1])
    o.encerrar_todos("fim do teste")
    with banco.tx() as c:
        assert c.one("SELECT estado FROM geradores")["estado"] == "encerrado"


def test_gpu_list_cycle_and_warm_time_come_from_the_environment(monkeypatch):
    for k, v in {
        "RENDER2GO_API_URL": "https://api.teste",
        "RENDER2GO_WORKER_TOKENS": "w" * 40,
        "RENDER2GO_R2_ENDPOINT": "https://conta.r2.cloudflarestorage.com",
    }.items():
        monkeypatch.setenv(k, v)
    c = ConfigOrq.do_ambiente()
    assert c.gpus == ln.GPUS_PADRAO and c.intervalo_s == 10 and c.ocioso_s == 180
    monkeypatch.setenv("RENDER2GO_GPUS", " NVIDIA L40S , NVIDIA GeForce RTX 4090,")
    monkeypatch.setenv("RENDER2GO_CICLO_S", "1")  # mínimo de 5 s
    monkeypatch.setenv("RENDER2GO_POD_OCIOSO_MIN", "5")
    c = ConfigOrq.do_ambiente()
    assert c.gpus == ["NVIDIA L40S", "NVIDIA GeForce RTX 4090"] and c.intervalo_s == 5 and c.ocioso_s == 300


def test_deadline_hourly_cap_and_balance(orq, banco):
    _lote(banco, 100)
    o = orq(prazo_min=10, max_usd_h=5.0)  # o prazo pede mais pods; o teto de US$ 5/h deixa 2
    r = o.ciclo()
    assert r["desejados"] > 2 and r["subindo"] == 2
    _esperar(lambda: o.subindo == 0)
    o.encerrar_todos("fim do teste")

    pobre = orq(provedor=Provedor(saldo=1.2))  # quase nada além da reserva: não sobe
    assert pobre.ciclo()["subindo"] == 0 and pobre.provedor.criados == []


def test_free_trials_wait_to_be_grouped_but_paid_orders_start_now(orq, banco):
    lid = _lote(banco, 3)  # 3 vídeos de teste: espera juntar 20 ou 10 min
    o = orq()
    assert o.ciclo()["desejados"] == 0 and o.provedor.criados == []
    trabalho = banco.trabalho()
    assert o.segurar_testes(trabalho, existentes=1) is False  # pod já ligado: ele pega na hora
    with banco.tx() as c:  # o teste mais antigo já esperou 11 min
        c.run("UPDATE jobs SET criado = criado - 660 WHERE lote_id = ?", (lid,))
    assert o.ciclo()["desejados"] == 1
    _esperar(lambda: o.subindo == 0)
    o.encerrar_todos("fim do teste")

    pago = orq()
    _lote(banco, 1, plano="pago")  # pago na fila: sobe sem esperar (e leva os testes junto)
    assert pago.segurar_testes(banco.trabalho(), existentes=0) is False
    _esperar(lambda: pago.subindo == 0)


class EspelhoIncompleto:
    """Espelho sem um dos modelos: o 1º pod é o gravador (envia o que falta depois do PRONTO)."""

    presentes = {"x"}

    def faltando(self):
        return ["um-modelo"]

    def tsv(self):
        return "modelo.safetensors\thttps://r2.teste/modelos/x\n"

    def plano(self):
        return {"arquivos": []}

    def abortar(self):
        pass


def test_mirror_writer_pod_is_closed_outside_the_cycle(orq, banco, monkeypatch):
    monkeypatch.setattr(ln, "ESPERA_GUARDAR_S", 1.5)
    _lote(banco, 2, plano="pago")
    o = orq()
    o.pods_ln.a.espelho = EspelhoIncompleto()
    o.remoto.instalar = "echo PRONTO > {raiz}/estado"  # sem o "guardado": o envio não termina
    assert o.ciclo()["subindo"] == 1
    assert _esperar(lambda: len(o.ativos) == 1)
    (pf,) = o.ativos.values()
    assert pf.pod.grava_volume and o.provedor.envs[0]["RENDER2GO_VOLUME_GRAVA"] == "1"  # gravador
    _, arquivos = pf.pod.agente.exec(f"ls {o.remoto.raiz}", 10)
    assert "espelho.tsv" in arquivos
    pf.pod.agente.exec(f"echo FIM > {o.remoto.raiz}/fila.fim", 10)
    with banco.tx() as c:
        c.run("UPDATE jobs SET status = 'ok'")
    t0 = time.monotonic()
    o.ciclo()  # o gravador espera o envio em outra thread; o ciclo não fica preso
    assert time.monotonic() - t0 < 1.0 and o.encerrando and not o.ativos
    with banco.tx() as c:  # ainda enviando ao espelho, mas já fora da previsão (não gera mais vídeos)
        assert c.one("SELECT estado FROM geradores")["estado"] == "encerrado"
    assert _esperar(lambda: not o.encerrando) and o.provedor.encerrados == ["p1"]


def test_each_pod_gets_its_own_worker_token_revoked_when_it_ends(orq, banco):
    _lote(banco, 2, plano="pago")
    o = orq()
    o.ciclo()
    assert _esperar(lambda: len(o.ativos) == 1)
    pf = next(iter(o.ativos.values()))
    _, env = pf.pod.agente.exec(f"cat {o.remoto.raiz}/fila.env", 10)
    token = next(
        linha.split("=", 1)[1].strip("'") for linha in env.splitlines() if linha.startswith("RENDER2GO_WORKER_TOKEN=")
    )
    assert token != o.cfg.worker_token and banco.worker_do_token(token) == pf.worker == f"pod-{pf.pod.info.id}"
    o.encerrar_todos("fim do teste")
    assert banco.worker_do_token(token) is None  # o pod saiu: o token não vale mais


def test_panel_commands_shut_down_one_pod_or_all_and_pause(orq, banco):
    _lote(banco, 50, plano="pago")  # pede 2 pods
    o = orq()
    o.ciclo()
    assert _esperar(lambda: len(o.ativos) == 2)
    alvo = next(iter(o.ativos.values()))
    banco.comando("desligar_pod", "admin-teste", alvo.gerador)
    o.comandos()
    assert alvo.pod.info.id in o.provedor.encerrados and alvo.pod.info.id not in o.ativos
    (cmd,) = banco.comandos_recentes()
    assert cmd["executado_em"] and "encerrado" in cmd["resultado"]
    # parada de emergência (a API grava a pausa e o comando): encerra todos e não sobe outros
    banco.definir_estado("pausado", "1")
    banco.comando("emergencia", "admin-teste")
    r = o.ciclo()
    assert r["pausado"] is True and not o.ativos and sorted(o.provedor.encerrados) == ["p1", "p2"]
    assert o.ciclo()["desejados"] == 0 and len(o.provedor.criados) == 2  # a fila ainda pede, mas nada sobe
    vivo = banco.ler_estado("orquestrador_vivo")
    assert vivo and float(vivo["valor"]) > 0  # o painel vê que o orquestrador está ativo
    # retomar: volta a subir pods conforme a fila
    banco.definir_estado("pausado", "0")
    banco.comando("retomar", "admin-teste")
    assert o.ciclo()["subindo"] >= 1
    _esperar(lambda: o.subindo == 0)
    o.encerrar_todos("fim do teste")


def test_pods_left_by_a_crashed_process_are_shut_down_at_start(orq, banco):
    o = orq(intervalo_s=0.05)
    sobras = o.cfg.estado / ln.ESTADO / "pods.json"
    sobras.parent.mkdir(parents=True, exist_ok=True)
    sobras.write_text('[{"id": "velho1", "descricao": "x"}, {"id": "velho2", "descricao": "y"}]')
    t = threading.Thread(target=o.rodar, daemon=True)
    t.start()
    assert _esperar(lambda: (o.cfg.estado / "vivo").exists())
    o.parar.set()
    t.join(5)
    assert {"velho1", "velho2"} <= set(o.provedor.encerrados) and sobras.read_text() == "[]"


def _fim(o, pf):
    return "FIM" in pf.pod.agente.exec(f"cat {o.remoto.raiz}/fila.fim 2>/dev/null", 10)[1]


def test_deadline_uses_the_real_gpu_price_once_the_agent_answers(orq, banco):
    # saldo de US$ 4: pela GPU mais cara da lista (US$ 2/h), 1,5 h; pela que veio (US$ 1/h), as 2 h
    # (até 11/10/2026 ficava o prazo da criação: com saldo baixo, o pod saía aos ~40 min)
    _lote(banco, 2, plano="pago")
    o = orq(provedor=Provedor(saldo=4.0, preco=2.0, preco_real=1.0))
    o.ciclo()
    assert _esperar(lambda: len(o.ativos) == 1)
    (pf,) = o.ativos.values()
    assert int(o.provedor.envs[0]["DEADMAN_S"]) == pytest.approx(5400, abs=5)
    assert pf.pod.deadman_s > 7100 and o.provedor.deadman["p1"].restante() > 7100
    assert pf.ate > time.time() + 7100 - ln.MARGEM_S - 60  # o worker para 10 min antes do prazo real
    o.encerrar_todos("fim do teste")


def test_the_balance_is_split_by_what_each_live_pod_can_still_spend(orq, banco):
    o = orq(provedor=Provedor(saldo=10.0, preco=2.0))
    assert o._deadman(2.0) == ln.MAX_POD_S  # US$ 9 livres: 4,5 h, limitado a 2 h
    # US$ 5 já prometidos a pods deste ciclo: sobra US$ 4 = 2 h; US$ 8: sobra US$ 1 = 30 min
    assert o._deadman(2.0, reservado=5.0) == ln.MAX_POD_S
    assert o._deadman(2.0, reservado=8.0) == pytest.approx(1800, abs=1)
    assert o._deadman(2.0, reservado=8.6) is None  # menos de 15 min: não sobe


def test_why_the_worker_left_goes_to_the_gpu_report(orq, banco):
    _lote(banco, 2, plano="pago")
    o = orq(fila_cmd="echo limite > {raiz}/fila.motivo")
    o.ciclo()
    assert _esperar(lambda: len(o.ativos) == 1)
    (pf,) = o.ativos.values()
    assert _esperar(lambda: _fim(o, pf))
    with banco.tx() as c:
        c.run("UPDATE jobs SET status = 'ok'")
    o.ciclo()
    (g,) = banco.relatorio_geradores(0)["geradores"]
    assert (g["estado"], g["motivo"]) == ("encerrado", "limite de tempo do pod")


def _instala_no_volume(o, completo=True):
    """Bootstrap falso do pod que hiberna: PRONTO e, depois, a cópia para o disco de volume (o marcador
    com a versão) e o GUARDADO, que o orquestrador espera antes de hibernar."""
    o.remoto.local_hib = vol = f"{o.remoto.raiz}/vol"
    marcador = f"mkdir -p {vol}; echo {ln.versao_hibernacao()} 1 > {vol}/instalado; " if completo else ""
    o.remoto.instalar = "echo PRONTO > {raiz}/estado; " + marcador + "echo GUARDADO > {raiz}/guardado"


def test_idle_pods_hibernate_and_come_back_without_a_new_pod(orq, banco):
    _lote(banco, 2, plano="pago")
    o = orq(hibernados=1, fila_cmd="echo ocioso > {raiz}/fila.motivo")
    _instala_no_volume(o)
    o.ciclo()
    assert _esperar(lambda: len(o.ativos) == 1)
    env = o.provedor.envs[0]
    assert o.provedor.volumes["p1"] == 70 and env["RENDER2GO_LOCAL"] == o.remoto.local_hib
    assert env["DEADMAN_ACAO"] == "parar" and int(env["DEADMAN_S"]) <= ln.PISO_HIB_S  # o real vem depois
    (pf,) = o.ativos.values()
    assert pf.pod.deadman_s > ln.PISO_HIB_S and o.prefixo in pf.pod.info.descricao
    assert _esperar(lambda: _fim(o, pf))
    with banco.tx() as c:
        c.run("UPDATE jobs SET status = 'ok'")
    o.ciclo()
    # sem pedidos: o pod para (a GPU deixa de cobrar) com ambiente e modelos no disco de volume; na 1ª
    # subida, isso espera a cópia para o volume, fora do ciclo
    assert _esperar(lambda: not o.encerrando)
    assert o.provedor.parados == ["p1"] and o.provedor.encerrados == [] and not o.ativos
    assert set(o.hib) == {"p1"} and o.hib["p1"]["versao"] == ln.versao_hibernacao()
    assert (o.cfg.estado / "hibernados.json").stat().st_mode & 0o777 == 0o600  # tem o token do agente
    o._estado_runpod()  # o próximo ciclo atualiza o resumo do painel
    painel = json.loads(banco.ler_estado("runpod")["valor"])
    assert painel["hibernados"][0]["id"] == "p1" and "token" not in painel["hibernados"][0]
    # pedido novo: o hibernado volta, sem pod novo
    _lote(banco, 1, plano="pago")
    assert o.ciclo()["subindo"] == 1
    assert _esperar(lambda: len(o.ativos) == 1)
    assert o.provedor.retomados == ["p1"] and o.provedor.criados == ["p1"] and o.hib["p1"]["voltas"] == 1
    (pf,) = o.ativos.values()
    assert pf.pod.retomado and pf.pod.deadman_s > ln.PISO_HIB_S
    with banco.tx() as c:
        linhas = c.all("SELECT worker, motivo FROM geradores ORDER BY criado")
    assert [x["worker"] for x in linhas] == ["pod-p1", "pod-p1"]
    assert linhas[0]["motivo"] == "hibernado (fila vazia)"
    o.encerrar_todos("fim do teste")  # encerrar de vez: sai do conjunto
    assert o.provedor.encerrados == ["p1"] and not o.hib


def test_a_host_without_free_gpu_falls_back_to_a_new_pod(orq, banco):
    o = orq(hibernados=2)
    info = {"url": "http://127.0.0.1:9", "token": "t" * 40, "usd_h": 2.0, "gpu": ln.GPUS_PADRAO[0], "gpus": 1}
    o.hib["velho"] = {"id": "velho", "versao": ln.versao_hibernacao(), "falhas": 2, **info}
    o.provedor.volumes["velho"] = 70
    o.provedor.sem_gpu.add("velho")
    _lote(banco, 1, plano="pago")
    o.ciclo()
    assert _esperar(lambda: len(o.ativos) == 1)
    assert o.provedor.criados == ["p1"]  # o host do hibernado estava cheio: pod novo
    assert "velho" not in o.hib and "velho" in o.provedor.encerrados  # 3ª vez sem GPU: sai de vez
    o.encerrar_todos("fim do teste")


def test_emergency_and_old_versions_terminate_hibernated_pods(orq, banco):
    o = orq(hibernados=2)
    base = {"url": "http://127.0.0.1:9", "token": "t" * 40, "usd_h": 2.0, "gpu": "x", "gpus": 1}
    o.hib["a"] = {"id": "a", "versao": "outra", **base}
    o.hib["b"] = {"id": "b", "versao": ln.versao_hibernacao(), **base}
    o._estado_runpod()  # a versão do ambiente ou dos modelos mudou: o disco do "a" não serve mais
    assert o.provedor.encerrados == ["a"] and set(o.hib) == {"b"}
    banco.definir_estado("pausado", "1")
    banco.comando("emergencia", "admin-teste")
    o.ciclo()
    assert o.provedor.encerrados == ["a", "b"] and not o.hib
    (cmd,) = banco.comandos_recentes()
    assert "1 hibernado(s) encerrado(s)" in cmd["resultado"]


def test_lowering_the_hibernated_limit_terminates_the_extra_stopped_pods(orq, banco):
    """O dono testa com a hibernação ligada e volta para 0 (ou diminui o número): nenhum pod fica
    parado cobrando o disco de volume sem uso."""
    o = orq(hibernados=0)
    base = {"url": "http://127.0.0.1:9", "token": "t" * 40, "usd_h": 2.0, "gpu": "x", "gpus": 1}
    v = ln.versao_hibernacao()
    o.hib["antigo"] = {"id": "antigo", "versao": v, "parado_em": 100.0, **base}
    o.hib["recente"] = {"id": "recente", "versao": v, "parado_em": 200.0, **base}
    o.hib["ligado"] = {"id": "ligado", "versao": v, "parado_em": 50.0, "em_uso": True, **base}
    o.cfg.hibernados = 2
    o._estado_runpod()  # 3 no conjunto, cabem 2: sai o parado há mais tempo; o ligado fica
    assert o.provedor.encerrados == ["antigo"] and set(o.hib) == {"recente", "ligado"}
    o.cfg.hibernados = 0
    o._estado_runpod()  # hibernação desligada: o parado sai; o ligado é encerrado quando a fila esvaziar
    assert o.provedor.encerrados == ["antigo", "recente"] and set(o.hib) == {"ligado"}
    painel = json.loads(banco.ler_estado("runpod")["valor"])
    assert [h["id"] for h in painel["hibernados"]] == ["ligado"] and painel["hibernados_max"] == 0


def test_a_pod_whose_volume_copy_did_not_finish_is_terminated(orq, banco, monkeypatch):
    monkeypatch.setattr(ln, "ESPERA_GUARDAR_S", 1)
    _lote(banco, 1, plano="pago")
    o = orq(hibernados=1, fila_cmd="echo ocioso > {raiz}/fila.motivo")
    _instala_no_volume(o, completo=False)
    o.ciclo()
    assert _esperar(lambda: len(o.ativos) == 1)
    (pf,) = o.ativos.values()
    assert _esperar(lambda: _fim(o, pf))
    with banco.tx() as c:
        c.run("UPDATE jobs SET status = 'ok'")
    o.ciclo()
    assert _esperar(lambda: not o.encerrando)
    assert o.provedor.encerrados == ["p1"] and o.provedor.parados == [] and not o.hib
    (g,) = banco.relatorio_geradores(0)["geradores"]
    assert g["motivo"].endswith("disco de volume incompleto")


def test_stopping_the_orchestrator_hibernates_what_fits(orq, banco):
    _lote(banco, 50, plano="pago")  # 2 pods; cabe 1 no conjunto
    o = orq(hibernados=1, fila_cmd="sleep 30")
    _instala_no_volume(o)
    o.ciclo()
    assert _esperar(lambda: len(o.ativos) == 2)
    o.encerrar_todos("orquestrador parou", hibernar=True)
    assert len(o.provedor.parados) == 1 and len(o.provedor.encerrados) == 1 and len(o.hib) == 1


def test_an_order_past_the_photo_retention_closes_without_starting_a_pod(orq, banco):
    """Fotos guardadas por 7 dias: o pedido aberto além do prazo fecha no ciclo, sem subir pod para ele."""
    lid = _lote(banco, 2)
    with banco.tx() as c:
        c.run("UPDATE lotes SET criado = criado - ? WHERE id = ?", (7 * 24 * 3600, lid))
    o = orq()
    assert o.ciclo() == {"desejados": 0, "ativos": 0, "subindo": 0}
    assert banco.lote(lid)["status"] == "concluido_com_falhas"
