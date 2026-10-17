"""Diagnóstico do disco de volume do pod que hiberna (ADR 0011; 17/10/2026): render2go_worker.volume
e as funções do bootstrap que o usam (o motivo de cada volta que não usou o cache e a recusa de
gravar fora de uma montagem persistente)."""

import json
import os
import subprocess

import pytest

from render2go_worker import volume

WORKER = __import__("pathlib").Path(__file__).resolve().parents[1]
SCRIPT = WORKER / "scripts" / "bootstrap_pod.sh"


def _mountinfo(tmp_path, linhas):
    arq = tmp_path / f"mountinfo-{len(list(tmp_path.glob('mountinfo-*')))}"
    arq.write_text(
        "".join(
            f"{n} 1 0:{n} / {ponto} rw - {fs} origem rw\n" for n, (ponto, fs) in enumerate(linhas)
        )
    )
    return str(arq)


def test_mount_point_is_the_longest_prefix_and_ephemeral_filesystems_are_not_persistent(tmp_path):
    local = tmp_path / "workspace" / "r2g"
    local.mkdir(parents=True)
    info = _mountinfo(
        tmp_path,
        [("/", "overlay"), (str(tmp_path / "workspace"), "ext4"), (str(tmp_path), "tmpfs")],
    )
    m = volume.montagem(str(local), info)
    assert m["montagem"] == str(tmp_path / "workspace") and m["fs"] == "ext4"
    # o diretório do teste está no mesmo disco da raiz do contêiner de teste: não é persistente
    # aqui, mesmo com o ponto de montagem achado (a regra olha o dispositivo também)
    mesmo = os.stat(local).st_dev == os.stat("/").st_dev
    assert m["persistente"] is (not mesmo)
    so_raiz = _mountinfo(tmp_path, [("/", "overlay")])
    assert volume.montagem(str(local), so_raiz) == {
        "montagem": "/",
        "fs": "overlay",
        "persistente": False,
    }
    # o cache ainda não criado: vale o pai mais próximo que existe
    assert volume.montagem(str(local / "a" / "b"), info)["montagem"] == str(tmp_path / "workspace")


@pytest.fixture
def persistente(monkeypatch):
    def fazer(sim=True, fs="ext4"):
        monkeypatch.setattr(
            volume,
            "montagem",
            lambda c, *a: {"montagem": "/workspace", "fs": fs, "persistente": sim},
        )

    return fazer


def test_classification_of_a_cache_miss(tmp_path, persistente, monkeypatch):
    monkeypatch.setenv("RUNPOD_POD_ID", "podatual")
    local = tmp_path / "r2g"
    persistente(True)
    # pod novo, volume vazio: esperado
    d = volume.diagnosticar(str(local), "inicio", "v1", "fria", "sem_marcador")
    assert d["inicio"]["motivo"] == "primeira" and d["entradas"] is None and d["subida"] == "fria"
    # a volta da hibernação com o volume vazio: o caso do "volume vazio após a retomada"
    assert (
        volume.diagnosticar(str(local), "inicio", "v1", "retomada", "sem_marcador")["inicio"][
            "motivo"
        ]
        == "vazio"
    )
    # a montagem que não é persistente decide antes de tudo
    persistente(False, "overlay")
    assert volume.diagnosticar(str(local), "inicio", "v1", "retomada", "sem_marcador")["inicio"][
        "motivo"
    ] == ("sem_montagem")
    persistente(True)
    # arquivos e a sentinela deste pod, sem o marcador: a cópia anterior não terminou
    volume.escrever_sentinela(str(local), "v1")
    (local / "venv").mkdir()
    d = volume.diagnosticar(str(local), "inicio", "v1", "retomada", "sem_marcador")
    assert d["inicio"]["motivo"] == "sem_marcador" and d["sentinela"]["pod"] == "podatual"
    assert d["entradas"] == ["sentinela.json", "venv"]
    # outra versão: o marcador diz qual
    (local / "instalado").write_text("v0 1760000000\n")
    d = volume.diagnosticar(str(local), "inicio", "v1", "retomada", "outra_versao", "v0")
    assert d["inicio"] == {
        "motivo": "outra_versao",
        "detalhe": "v0",
        "em": pytest.approx(d["inicio"]["em"]),
    }
    assert d["marcador"] == {"versao": "v0", "em": 1760000000.0}
    # a sentinela de outro pod
    monkeypatch.setenv("RUNPOD_POD_ID", "outropod")
    assert volume.diagnosticar(str(local), "inicio", "v1", "retomada", "sem_marcador")["inicio"][
        "motivo"
    ] == ("outro_pod")
    # o que deu certo continua "ok"; um motivo desconhecido nunca vira texto livre (aqui, decide
    # a sentinela)
    assert (
        volume.diagnosticar(str(local), "inicio", "v1", "retomada", "ok")["inicio"]["motivo"]
        == "ok"
    )
    assert (
        volume.diagnosticar(str(local), "inicio", "v1", "retomada", "<b>x</b>")["inicio"]["motivo"]
        == "outro_pod"
    )


def test_the_end_of_the_first_boot_keeps_the_start_and_records_the_copy(
    tmp_path, persistente, monkeypatch
):
    monkeypatch.setenv("RUNPOD_POD_ID", "p1")
    persistente(True)
    local, saida = tmp_path / "r2g", tmp_path / "volume.json"
    assert (
        volume.main(
            [
                "diagnosticar",
                "--local",
                str(local),
                "--saida",
                str(saida),
                "--etapa",
                "inicio",
                "--versao",
                "v1",
                "--subida",
                "fria",
                "--motivo",
                "sem_marcador",
            ]
        )
        == 0
    )
    assert (
        volume.main(
            [
                "diagnosticar",
                "--local",
                str(local),
                "--saida",
                str(saida),
                "--etapa",
                "gravacao",
                "--versao",
                "v1",
                "--motivo",
                "sem_espaco",
                "--necessario",
                "40000000000",
            ]
        )
        == 0
    )
    d = json.loads(saida.read_text())
    assert d["inicio"]["motivo"] == "primeira" and d["gravacao"]["motivo"] == "sem_espaco"
    assert d["gravacao"]["necessario_b"] == 40_000_000_000


def test_fits_and_sentinel_commands(tmp_path):
    total = os.statvfs(tmp_path).f_blocks * os.statvfs(tmp_path).f_frsize
    assert volume.main(["cabe", "--local", str(tmp_path / "x"), "--bytes", "1000"]) == 0
    assert (
        volume.main(["cabe", "--local", str(tmp_path / "x"), "--bytes", str(total)]) == 1
    )  # sem a folga de 2%
    assert volume.main(["sentinela", "--local", str(tmp_path / "x"), "--versao", "v1"]) == 0
    assert volume.sentinela(str(tmp_path / "x"))["versao"] == "v1"


def test_orchestrator_reads_only_known_fields_with_limits():
    bruto = {
        "v": 1,
        "local": "/workspace/r2g",
        "montagem": "/workspace",
        "fs": "ext4",
        "persistente": True,
        "total_b": 75e9,
        "livre_b": 30e9,
        "pod": "ltuxxfgujgnpsm",
        "versao": "1ada2ec01194e930",
        "subida": "retomada",
        "sentinela": {"pod": "ltuxxfgujgnpsm", "criado": 1.0, "versao": "x", "segredo": "nao"},
        "marcador": {"versao": "1ada2ec01194e930", "em": 2.0},
        "entradas": ["venv", "<script>", 3],
        "inicio": {"motivo": "ok", "detalhe": None, "em": 3.0},
        "gravacao": {"motivo": "inventado", "detalhe": "x"},
        "RUNPOD_API_KEY": "nunca",
        "env": {"AGENT_TOKEN": "nunca"},
    }
    d = volume.ler_diagnostico(json.dumps(bruto))
    texto = json.dumps(d)
    assert "nunca" not in texto and "segredo" not in texto and "<" not in texto
    assert (
        d["inicio"]["motivo"] == "ok" and "gravacao" not in d
    )  # motivo fora da lista: a etapa sai
    assert d["entradas"] == ["venv", "script"] and d["sentinela"]["pod"] == "ltuxxfgujgnpsm"
    assert volume.ler_diagnostico("não é json") is None and volume.ler_diagnostico("[1]") is None
    assert volume.ler_diagnostico(json.dumps({"persistente": "sim"}))["persistente"] is None


def _bash(tmp_path, corpo, **env):
    raiz = tmp_path / "raiz"
    raiz.mkdir(exist_ok=True)
    cmd = f'source "{SCRIPT}" x teste; W="{WORKER}"; {corpo}'
    return subprocess.run(
        ["bash", "-c", cmd],
        env={**os.environ, "BOOTSTRAP_SO_FUNCOES": "1", "RENDER2GO_RAIZ": str(raiz), **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_bootstrap_records_why_a_resume_did_not_use_the_volume(tmp_path):
    local = tmp_path / "r2g"
    local.mkdir()
    (tmp_path / "raiz").mkdir()
    (tmp_path / "raiz" / "versao_hib").write_text("v1\n")
    (tmp_path / "raiz" / "subida").write_text("retomada\n")
    env = {"RENDER2GO_LOCAL": str(local)}
    r = _bash(tmp_path, 'retomada; echo "MOTIVO=$MOTIVO_VOL"', **env)
    assert "MOTIVO=sem_marcador" in r.stdout, r.stderr
    (local / "instalado").write_text("v0 1\n")
    r = _bash(tmp_path, 'retomada; echo "MOTIVO=$MOTIVO_VOL DETALHE=$DETALHE_VOL"', **env)
    assert "MOTIVO=outra_versao DETALHE=v0" in r.stdout
    r = _bash(tmp_path, 'retomada; diagnostico_volume inicio "$MOTIVO_VOL" "$DETALHE_VOL"', **env)
    d = json.loads((tmp_path / "raiz" / "volume.json").read_text())
    esperado = "outra_versao" if volume.montagem(str(local))["persistente"] else "sem_montagem"
    assert d["inicio"]["motivo"] == esperado and d["subida"] == "retomada" and d["versao"] == "v1"


def test_bootstrap_never_writes_the_cache_outside_a_persistent_mount(tmp_path):
    local = tmp_path / "r2g"
    (tmp_path / "raiz").mkdir()
    (tmp_path / "raiz" / "versao_hib").write_text("v1\n")
    if volume.montagem(str(tmp_path))["persistente"]:
        pytest.skip("o diretório de teste está numa montagem própria aqui")
    r = _bash(
        tmp_path,
        'guardar_local() { echo COPIOU; touch "$L/instalado"; }; gravar_volume; echo "rc=$?"',
        RENDER2GO_LOCAL=str(local),
    )
    assert "rc=1" in r.stdout and "COPIOU" not in r.stdout, r.stderr
    assert not (
        local / "instalado"
    ).exists()  # sem marcador: o orquestrador encerra em vez de hibernar
    d = json.loads((tmp_path / "raiz" / "volume.json").read_text())
    assert d["gravacao"]["motivo"] == "sem_montagem" and d["persistente"] is False
