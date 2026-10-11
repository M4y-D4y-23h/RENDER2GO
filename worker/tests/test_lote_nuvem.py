"""lote_nuvem de ponta a ponta sem GPU: agentes reais em 127.0.0.1 fazem o papel dos pods,
com ComfyUI e Ollama falsos (conftest)."""

import csv
import os
import shutil
import sys
import threading
from pathlib import Path

import pytest
from conftest import WORKER
from PIL import Image

from render2go_worker import lote_nuvem as ln
from render2go_worker import pod_agente, runpod_pods

PADRAO = WORKER.parent / "fixtures" / "sfw" / "padrao-teste-640.png"


def _foto(path, size):
    with Image.open(PADRAO) as im:
        im.convert("RGB").resize(size).save(path)


@pytest.fixture
def pasta(tmp_path):
    d = tmp_path / "cliente"
    d.mkdir()
    for i, size in enumerate([(600, 600), (900, 1600), (1600, 900), (800, 800), (700, 1300)]):
        _foto(d / f"foto{i}.jpg", size)
    return d


class Provedor:
    """Cria "pods" que são agentes reais em 127.0.0.1; `quebrados` nunca respondem."""

    def __init__(self, quebrados=0, sem_vaga=False):
        self.criados, self.encerrados, self.envs = [], [], []
        self.servidores = {}
        self.quebrados, self.sem_vaga = quebrados, sem_vaga

    def preco(self, gpu, nuvem):
        return 0.79

    def criar(self, nome, opcoes, gpus, env):
        if self.sem_vaga == "recusa":
            raise runpod_pods.RunPodError("criação recusada (400)")
        if self.sem_vaga:
            raise runpod_pods.SemVaga("nada")
        pid = f"p{len(self.criados) + 1}"
        self.criados.append(pid)
        self.envs.append(env)
        if len(self.criados) <= self.quebrados:
            url = "http://127.0.0.1:9"  # nada escuta: agente nunca responde
        else:
            srv = pod_agente.servidor(env["AGENT_TOKEN"], "127.0.0.1", 0)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.servidores[pid] = srv
            url = f"http://127.0.0.1:{srv.server_address[1]}"
        return ln.PodInfo(pid, url, env["AGENT_TOKEN"], 0.79, f"teste/{nome}", gpus)

    def encerrar(self, pod_id):
        self.encerrados.append(pod_id)
        srv = self.servidores.pop(pod_id, None)
        if srv:
            srv.shutdown()


def _remoto(tmp_path, comfy, ollama):
    # shim: o lote do "pod" fala com os serviços falsos em vez de 127.0.0.1:8188+
    shim = tmp_path / "lote_shim.py"
    shim.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(WORKER / 'src')!r})\n"
        "from render2go_worker import lote\n"
        "a = sys.argv[1:]\n"
        "if '--gpus' in a:\n"
        "    i = a.index('--gpus'); del a[i:i + 2]\n"
        f"a += ['--comfy', {comfy!r}, '--ollama', {ollama!r}]\n"
        "sys.exit(lote.main(a))\n"
    )
    return ln.Remoto(
        raiz=str(tmp_path / "pod"),
        verificar_cuda=False,
        instalar="echo PRONTO > {raiz}/estado",
        servicos="echo '[render2go] pronto'",
        lote=f"{sys.executable} {shim}",
    )


@pytest.fixture
def sem_git(monkeypatch, tmp_path):
    def falso(destino):
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"repo")
        return destino

    monkeypatch.setattr(ln, "empacotar_repo", falso)
    monkeypatch.setattr(ln, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(ln, "ESPERA_VAGA_S", 0)
    monkeypatch.setattr(ln, "ESPERA_AGENTE_S", 3)
    # esperas de consulta (CONSULTA_S, 10 s) e de vaga não dormem; as curtas (repetições) dormem
    monkeypatch.setattr(ln.time, "sleep", lambda s: None if s >= ln.CONSULTA_S else _dormir(s))


_dormir = __import__("time").sleep


def _argv(pasta, saida, *extra):
    return [
        str(pasta),
        "--saida",
        str(saida),
        "--prompt",
        "a câmera se aproxima devagar do produto",
        "--sim",
        *extra,
    ]


def test_end_to_end_two_pods_compile_once_and_terminate(
    pasta, tmp_path, fake_comfy, fake_ollama, sem_git
):
    prov = Provedor()
    saida = tmp_path / "entrega"
    code = ln.main(
        _argv(pasta, saida, "--pods", "2"),
        provedor=prov,
        remoto=_remoto(tmp_path, fake_comfy.url, fake_ollama.url),
    )
    assert code == ln.EXIT_OK
    assert sorted(p.name for p in saida.glob("*.mp4")) == [f"foto{i}-v1.mp4" for i in range(5)]
    rows = list(csv.DictReader((saida / "relatorio.csv").open(encoding="utf-8")))
    assert [r["status"] for r in rows] == ["ok"] * 5 and {r["pod"] for r in rows} <= {"p1", "p2"}
    assert len(fake_ollama.requests) == 1  # um prompt para todos os pods
    assert len({wf["129:93"]["inputs"]["text"] for wf in fake_comfy.prompts.values()}) == 1
    # a foto chega ao ComfyUI já no tamanho do vídeo (preparada aqui, antes do envio)
    tamanhos = {Image.open(__import__("io").BytesIO(b)).size for b in fake_comfy.uploads.values()}
    assert tamanhos == {(640, 640), (480, 832), (832, 480)}
    assert sorted(prov.encerrados) == sorted(prov.criados) == ["p1", "p2"]
    env = prov.envs[0]
    assert int(env["DEADMAN_S"]) <= 2 * 3600 and len(env["AGENT_TOKEN"]) >= 32
    assert env["AGENT_TOKEN"] != prov.envs[1]["AGENT_TOKEN"]
    assert (saida / ".nuvem" / "pods.json").read_text().strip() == "[]"

    # rodar de novo: nada a fazer, nenhum pod criado
    prov2 = Provedor()
    assert ln.main(_argv(pasta, saida), provedor=prov2, remoto=_remoto(tmp_path, "x", "y")) == 0
    assert prov2.criados == []


def test_broken_pod_is_terminated_and_others_do_the_work(
    pasta, tmp_path, fake_comfy, fake_ollama, sem_git
):
    prov = Provedor(quebrados=1)
    saida = tmp_path / "entrega"
    code = ln.main(
        _argv(pasta, saida, "--pods", "2"),
        provedor=prov,
        remoto=_remoto(tmp_path, fake_comfy.url, fake_ollama.url),
    )
    assert code == ln.EXIT_OK and len(list(saida.glob("*.mp4"))) == 5
    assert "p1" in prov.encerrados and "p2" in prov.encerrados


def test_only_one_pod_writes_the_volume_cache_and_waits_for_it(
    pasta, tmp_path, fake_comfy, fake_ollama, sem_git, monkeypatch, capsys
):
    monkeypatch.setattr(ln, "ESPERA_GUARDAR_S", 5)
    # p1 dado como perdido já na 1ª verificação: assim ainda há trabalho e o substituto sempre sobe
    # (com 3 s de espera, o p2 às vezes terminava as 5 fotos antes e o substituto não era preciso)
    monkeypatch.setattr(ln, "ESPERA_AGENTE_S", 0)
    prov = Provedor(quebrados=1)  # p1 (gravador) nunca responde: o substituto herda a gravação
    remoto = _remoto(tmp_path, fake_comfy.url, fake_ollama.url)
    remoto.instalar = "echo PRONTO > {raiz}/estado; echo GUARDADO > {raiz}/guardado"
    code = ln.main(
        _argv(pasta, tmp_path / "entrega", "--pods", "2", "--volume", "vol1"),
        provedor=prov,
        remoto=remoto,
    )
    assert code == ln.EXIT_OK
    grava = [e.get("RENDER2GO_VOLUME_GRAVA") for e in prov.envs]
    # p1 gravava; quando ele cai, o papel passa para o próximo pod criado (o substituto dele ou o
    # do outro posto, conforme a ordem das threads): sempre um gravador só, e o papel não se perde
    assert len(grava) == 3 and grava[0] == "1" and grava.count("1") == 2
    assert "cache gravado no volume" in capsys.readouterr().err


@pytest.mark.skipif(not os.environ.get("RENDER2GO_TESTE_S3"), reason="sem S3 local")
def test_first_pod_fills_the_r2_mirror_and_next_lot_gets_signed_urls(
    pasta, tmp_path, fake_comfy, fake_ollama, sem_git, monkeypatch, capsys
):
    from test_espelho import modelos_pequenos  # mesma montagem de modelos pequenos

    from render2go_worker import espelho as esp_mod

    pasta_modelos, entradas = modelos_pequenos.__wrapped__(tmp_path, monkeypatch)
    for k, v in {
        "ENDPOINT": os.environ["RENDER2GO_TESTE_S3"],
        "BUCKET": "gpu2go",
        "ACCESS_KEY_ID": os.environ["RENDER2GO_TESTE_S3_CHAVE"],
        "SECRET_ACCESS_KEY": os.environ["RENDER2GO_TESTE_S3_SEGREDO"],
        "REGION": "us-east-1",
    }.items():
        monkeypatch.setenv(f"RENDER2GO_R2_{k}", v)
    s3 = esp_mod.Armazenamento.do_ambiente()
    for m in entradas.values():
        s3.apagar(f"modelos/{m.sha256}")
    monkeypatch.setattr(ln, "ESPERA_GUARDAR_S", 60)
    remoto = _remoto(tmp_path, fake_comfy.url, fake_ollama.url)
    remoto.instalar = "echo PRONTO > {raiz}/estado; echo GUARDADO > {raiz}/guardado"
    argv = _argv(pasta, tmp_path / "entrega", "--pods", "2")
    assert ln.main(argv, provedor=Provedor(), remoto=remoto) == ln.EXIT_OK
    err = capsys.readouterr().err
    assert "espelho (R2): 0 de 2" in err and "enviando 2 modelo(s) ao espelho" in err
    assert err.count("enviando") == 1  # só o pod gravador envia
    assert all(s3.tamanho(f"modelos/{m.sha256}") == m.bytes for m in entradas.values())
    # próximo lote: espelho completo; os pods recebem as URLs assinadas e ninguém envia
    (tmp_path / "pod" / "espelho.tsv").unlink(missing_ok=True)
    argv2 = _argv(pasta, tmp_path / "entrega2", "--pods", "1")
    assert ln.main(argv2, provedor=Provedor(), remoto=remoto) == ln.EXIT_OK
    err = capsys.readouterr().err
    assert "espelho (R2): 2 de 2" in err and "enviando" not in err
    assert len((tmp_path / "pod" / "espelho.tsv").read_text().splitlines()) == 2


def test_automatic_pods_keep_each_pod_busy_or_meet_the_deadline():
    escolher = ln.escolher_pods
    # 50 fotos, rápida 5 s (28 s): ~23 min de vídeos → 2 pods, não 4
    assert escolher(50, 25, 1, 28, None)[0] == 2
    # 50 fotos, alta (93 s): ~78 min → 7 pods (cada um com ≥ 10 min)
    assert escolher(50, 25, 1, 93, None)[0] == 7
    # 10 clientes × 10 fotos, teste de 3 s (~12 s): ~21 min → 2 pods
    assert escolher(100, 50, 1, 12.4, None)[0] == 2
    # lote pequeno: 1 pod; nunca mais pods que pacotes, nem acima do teto
    assert escolher(4, 2, 1, 28, None)[0] == 1
    assert escolher(500, 250, 1, 93, None)[0] == ln.MAX_PODS_AUTO
    # com prazo: o mínimo de pods que entrega no prazo (subida incluída)
    pods, _ = escolher(100, 50, 1, 12.4, 10)
    assert ln.SUBIDA_S + 100 * 12.4 / pods <= 10 * 60 < ln.SUBIDA_S + 100 * 12.4 / (pods - 1)
    pods, motivo = escolher(500, 250, 1, 93, 15)
    assert pods == ln.MAX_PODS_AUTO and "não cabe" in motivo


def test_cli_shows_the_automatic_choice_and_alternatives(pasta, tmp_path, capsys):
    assert ln.main([*_argv(pasta, tmp_path / "s"), "--simular"]) == ln.EXIT_OK
    err = capsys.readouterr().err
    assert "pods automáticos: 1" in err and "outras opções" in err
    with pytest.raises(SystemExit):
        ln.main([*_argv(pasta, tmp_path / "s"), "--pods", "2", "--prazo", "10", "--simular"])


def test_bootstrap_downloads_in_parts_and_caches_the_environment():
    s = (WORKER / "scripts" / "bootstrap_pod.sh").read_text()
    assert "aria2c" in s and "-x 8 -s 8" in s and "xargs -r -P 4" in s
    assert "ambiente-$CHAVE.tar.zst" in s and "requirements.lock" in s
    # volume não POSIX: sem mv/chmod; o .sha256 do ambiente é escrito por último
    corpo = s.split("set -uo pipefail", 1)[1]
    assert " mv " not in corpo and "chmod" not in corpo
    assert s.index('rsync --inplace $R/ambiente.tar.zst "$AMB"') < s.index('"$AMB.sha256" &&')


def test_invalid_request_budget_and_no_gpu_create_nothing(pasta, tmp_path, sem_git):
    prov = Provedor()
    vazia = tmp_path / "vazia"
    vazia.mkdir()
    assert ln.main(_argv(vazia, tmp_path / "s"), provedor=prov) == ln.EXIT_PEDIDO
    assert ln.main(_argv(pasta, tmp_path / "s", "--orcamento-usd", "0.01"), provedor=prov) == 4
    assert prov.criados == []
    assert ln.main(_argv(pasta, tmp_path / "s"), provedor=Provedor(sem_vaga=True)) == 4
    assert ln.main(_argv(pasta, tmp_path / "s"), provedor=Provedor(sem_vaga="recusa")) == 4


def test_estimate_and_simulation(pasta, tmp_path, capsys):
    assert ln.main(_argv(pasta, tmp_path / "s", "--simular")) == ln.EXIT_OK
    err = capsys.readouterr().err
    assert "5 foto(s), 5 vídeo(s) em 1 GPU(s)" in err and "(NVIDIA L40S, ~55 s por vídeo)" in err
    assert f"US$ {ln.PRECO_RESERVA_USD_H:.2f}/h" in err  # sem consulta de preço: o lado seguro
    e = ln.estimar(50, 4, 1, 55, 0.79)
    assert e.gpus == 4 and round(e.minutos) == round((ln.SUBIDA_S + 50 * 55 / 4) / 60)
    assert ln.segundos_por_video("NVIDIA L40S", "alta", 7) == pytest.approx(180 * 1.4**1.6)


def test_agent_rejects_wrong_token(tmp_path):
    srv = pod_agente.servidor("x" * 40, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert not ln.Agente(url, "y" * 40).saude()
        with pytest.raises(ln.AgenteError, match="token"):
            ln.Agente(url, "y" * 40).exec("true")
        ag = ln.Agente(url, "x" * 40)
        assert ag.exec("echo oi") == (0, "oi\n")
        f = tmp_path / "a.bin"
        f.write_bytes(b"\x00" * 3000)
        ag.put(f, str(tmp_path / "remoto" / "b.bin"))
        assert (tmp_path / "remoto" / "b.bin").read_bytes() == f.read_bytes()
        assert ag.get(str(tmp_path / "remoto" / "b.bin"), tmp_path / "c.bin")
        assert not ag.get(str(tmp_path / "nao-existe"), tmp_path / "d.bin")
    finally:
        srv.shutdown()
    with pytest.raises(SystemExit):
        pod_agente.servidor("curto")


def test_agent_deadline_can_be_rescheduled_but_never_past_two_hours(tmp_path):
    d = pod_agente.Desligamento(1200, log=str(tmp_path / "deadman.txt"))
    srv = pod_agente.servidor("x" * 40, "127.0.0.1", 0, deadman=d)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ag = ln.Agente(f"http://127.0.0.1:{srv.server_address[1]}", "x" * 40)
    try:
        assert 1190 < d.restante() <= 1200
        assert 3590 < ag.deadman(3600) <= 3600  # o preço real da GPU paga mais tempo
        assert ag.deadman(9 * 3600) <= 2 * 3600  # nunca além de 2 h da subida do contêiner
        assert ag.deadman(-5) == 0  # o saldo acabou: desliga agora
        with pytest.raises(ln.AgenteError, match="HTTP 400"):
            ag.deadman("x")
        assert "reprogramado" in (tmp_path / "deadman.txt").read_text()
    finally:
        srv.shutdown()
    sem = pod_agente.servidor("x" * 40, "127.0.0.1", 0)  # pod sem desligamento automático
    threading.Thread(target=sem.serve_forever, daemon=True).start()
    try:
        with pytest.raises(ln.AgenteError, match="409"):
            ln.Agente(f"http://127.0.0.1:{sem.server_address[1]}", "x" * 40).deadman(60)
    finally:
        sem.shutdown()


@pytest.mark.parametrize(("acao", "primeira"), [("parar", "podStop"), ("encerrar", "podTerminate")])
def test_agent_deadline_stops_a_hibernating_pod_before_terminating(
    monkeypatch, tmp_path, acao, primeira
):
    pedidos = []

    class Resposta:
        def __init__(self, corpo):
            self.corpo = corpo

        def read(self):
            return self.corpo

    def urlopen(req, timeout):
        pedidos.append(req.data.decode())
        if "podStop" in req.data.decode() and acao == "parar" and len(pedidos) == 1:
            return Resposta(b'{"errors": [{"message": "tente de novo"}]}')
        return Resposta(b'{"data": {}}')

    monkeypatch.setenv("RUNPOD_POD_ID", "abc")
    monkeypatch.setenv("RUNPOD_API_KEY", "k" * 20)
    monkeypatch.setattr(pod_agente.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(pod_agente.time, "sleep", lambda s: None)
    d = pod_agente.Desligamento(0.01, acao, log=str(tmp_path / "d.txt"))
    d.rodar()
    assert primeira in pedidos[0]
    if (
        acao == "parar"
    ):  # o 1º podStop voltou com erro: tenta de novo antes de partir para o terminate
        assert "podStop" in pedidos[1]


def test_bootstrap_reads_versions_from_dockerfile():
    script = (WORKER / "scripts" / "bootstrap_pod.sh").read_text()
    for arg in ("COMFYUI_COMMIT", "UMT5_PATCH_SHA256", "OLLAMA_SHA256", "COMPILER_MODEL_DIGEST"):
        assert f"arg {arg}" in script
        assert f"ARG {arg}=" in (WORKER / "Dockerfile").read_text()
    if shutil.which("bash"):
        import subprocess

        assert (
            subprocess.run(["bash", "-n", str(WORKER / "scripts" / "bootstrap_pod.sh")]).returncode
            == 0
        )


def test_clean_up_registered_pods(tmp_path):
    est = tmp_path / ".nuvem" / "pods.json"
    est.parent.mkdir(parents=True)
    est.write_text('[{"id": "p7", "descricao": "x"}]')
    prov = Provedor()
    assert ln.main(["--saida", str(tmp_path), "--limpar"], provedor=prov) == 0
    assert prov.encerrados == ["p7"] and est.read_text() == "[]"
    assert Path(est).exists()


def test_no_package_starts_without_safe_time_before_the_2h_limit(
    pasta, tmp_path, fake_comfy, fake_ollama, sem_git, monkeypatch
):
    monkeypatch.setattr(ln, "MARGEM_S", 2 * 3600)  # nenhum pacote cabe antes do limite
    prov = Provedor()
    code = ln.main(
        _argv(pasta, tmp_path / "s"),
        provedor=prov,
        remoto=_remoto(tmp_path, fake_comfy.url, fake_ollama.url),
    )
    assert code == ln.EXIT_PENDENTE and prov.encerrados == prov.criados == ["p1"]
    assert fake_comfy.prompts == {}


def test_default_gpu_order_is_secure_l40s_first():
    a = ln.build_parser().parse_args(["--saida", "s"])
    assert a.nuvem == "secure"
    # 08/10/2026: a RTX PRO 6000 subiu para US$ 2,49/h e virou reserva das de 48 GB
    assert ln.GPUS_PADRAO == [
        "NVIDIA L40S",
        "NVIDIA RTX 6000 Ada Generation",
        "NVIDIA RTX PRO 6000 Blackwell Server Edition",
        "NVIDIA GeForce RTX 4090",
    ]
    medidas = ("NVIDIA L40S", "NVIDIA RTX PRO 6000 Blackwell Server Edition")
    assert all(g in ln.MEDIDO_S for g in medidas)


def test_cuda_check_requires_working_gpus_and_driver_12_8():
    assert ln.cuda_ok("cuInit 0 gpus 1 driver 13020", 1) is None  # driver novo: aceito
    assert ln.cuda_ok("cuInit 0 gpus 2 driver 12080", 2) is None
    assert "defeito" in ln.cuda_ok("cuInit 999 gpus 1 driver 13000", 1)
    assert "defeito" in ln.cuda_ok("cuInit 0 gpus 1 driver 13000", 2)
    assert "antigo" in ln.cuda_ok("cuInit 0 gpus 1 driver 12040", 1)
    assert "sem resposta" in ln.cuda_ok("bash: python3: not found", 1)


def test_network_check_discards_slow_hosts():
    assert ln.rede_ok("rede 512.3") is None
    assert "rede lenta (2.9 MB/s" in ln.rede_ok("rede 2.9")
    assert "sem resposta" in ln.rede_ok("Traceback ...")
    assert ln.url_teste_rede().startswith("https://huggingface.co/")


def test_slow_pods_are_replaced_and_their_work_is_kept(
    pasta, tmp_path, fake_comfy, fake_ollama, sem_git, monkeypatch
):
    monkeypatch.setattr(ln, "segundos_por_video", lambda *a: 0.0001)  # todo pod parece lento
    monkeypatch.setattr(ln, "LENTO", 1.0)
    monkeypatch.setattr(ln, "FOLGA_S", 0)
    prov = Provedor()
    saida = tmp_path / "entrega"
    code = ln.main(
        _argv(pasta, saida, "--substitutos", "2"),
        provedor=prov,
        remoto=_remoto(tmp_path, fake_comfy.url, fake_ollama.url),
    )
    # 5 fotos em pacotes de 2: cada pod entrega um pacote e é trocado (1 + 2 substitutos)
    assert code == ln.EXIT_OK and len(list(saida.glob("*.mp4"))) == 5
    assert prov.criados == ["p1", "p2", "p3"] and sorted(prov.encerrados) == prov.criados


def test_slow_datacenters_are_remembered_for_a_week(tmp_path, monkeypatch):
    monkeypatch.setattr(ln, "CACHE", tmp_path)
    ln.lembrar_datacentro("US-KS-2", 12.0, "rede lenta")
    ln.lembrar_datacentro("US-NC-1", 329.0, "")
    ln.lembrar_datacentro(None, 1.0, "sem datacenter: ignorado")
    assert ln.datacentros_lentos() == {"US-KS-2"}
    dados = __import__("json").loads((tmp_path / "datacentros.json").read_text())
    dados["US-KS-2"]["quando"] -= 8 * 86400
    (tmp_path / "datacentros.json").write_text(__import__("json").dumps(dados))
    assert ln.datacentros_lentos() == set()


def test_entrypoint_supports_several_comfyui_per_gpu():
    import subprocess

    ep = WORKER / "entrypoint.sh"
    assert subprocess.run(["bash", "-n", str(ep)]).returncode == 0
    texto = ep.read_text()
    assert "COMFY_POR_GPU" in texto and "N_COMFY=$((COMFY_GPUS * COMFY_POR_GPU))" in texto


def test_accepted_datacenters_come_from_the_api_error(monkeypatch):
    class R:
        status_code = 400
        text = (
            '[{"problems":["At /pods/properties/dataCenterIds/items/enum: value must be one of '
            "'EU-RO-1', 'US-NC-1', 'US-KS-2'\"]}]"
        )

    monkeypatch.setattr(runpod_pods, "_chave", lambda: "k")
    monkeypatch.setattr(runpod_pods, "_rest", lambda *a, **k: R())
    assert ln.RunPod()._datacentros_aceitos() == {"EU-RO-1", "US-NC-1", "US-KS-2"}


def test_shutdown_follows_cost_and_account_balance(pasta, tmp_path, sem_git, capsys):
    class Pobre(Provedor):
        def saldo(self):
            return 1.05  # só US$ 0,05 disponíveis depois da reserva

    prov = Pobre()
    assert ln.main(_argv(pasta, tmp_path / "s"), provedor=prov) == ln.EXIT_SEM_GPU
    assert prov.criados == [] and "passa do saldo" in capsys.readouterr().err


def test_package_without_git_has_worker_and_tools(tmp_path, monkeypatch):
    import tarfile

    repo = tmp_path / "repo"
    (repo / "worker" / "scripts").mkdir(parents=True)
    (repo / "worker" / "scripts" / "bootstrap_pod.sh").write_text("#!/bin/bash")
    (repo / "worker" / ".venv").mkdir()
    (repo / "worker" / ".venv" / "lixo").write_text("x")
    (repo / "tools").mkdir()
    (repo / "tools" / "lote.py").write_text("")
    monkeypatch.setattr(ln, "REPO", repo)
    with tarfile.open(ln.empacotar_repo(tmp_path / "p.tgz")) as t:
        nomes = set(t.getnames())
    assert "worker/scripts/bootstrap_pod.sh" in nomes and "tools/lote.py" in nomes
    assert not any(".venv" in n for n in nomes)
