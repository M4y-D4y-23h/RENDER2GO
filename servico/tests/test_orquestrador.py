"""Orquestrador com pods falsos: agentes reais em 127.0.0.1 (como nos testes do lote_nuvem)."""

import threading
import time

import pytest
from render2go_worker import lote_nuvem as ln
from render2go_worker import pod_agente

from render2go_servico.orquestrador import ConfigOrq, Orquestrador

_dormir = time.sleep


class Provedor:
    def __init__(self, saldo=100.0, preco=2.0):
        self.criados, self.encerrados, self.envs, self.servidores = [], [], [], {}
        self._saldo, self._preco = saldo, preco

    def preco(self, gpu, nuvem):
        return self._preco

    def saldo(self):
        return self._saldo

    def criar(self, nome, opcoes, gpus, env):
        assert all(o.nuvem == "secure" for o in opcoes)  # regra do projeto
        pid = f"p{len(self.criados) + 1}"
        self.criados.append(pid)
        self.envs.append(env)
        srv = pod_agente.servidor(env["AGENT_TOKEN"], "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servidores[pid] = srv
        return ln.PodInfo(
            pid, f"http://127.0.0.1:{srv.server_address[1]}", env["AGENT_TOKEN"], self._preco, f"teste/{nome}", gpus
        )

    def encerrar(self, pod_id):
        self.encerrados.append(pod_id)
        srv = self.servidores.pop(pod_id, None)
        if srv:
            srv.shutdown()


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
        return Orquestrador(banco, c, provedor or Provedor(), remoto, repo_tgz=repo)

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
