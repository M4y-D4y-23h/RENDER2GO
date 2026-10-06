import json

import pytest
import requests

from gpu2go_worker import runpod_pods as rp


class Resp:
    def __init__(self, status: int, body):
        self.status_code = status
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.ok = 200 <= status < 300

    def json(self):
        return self._body


def test_options_order_community_first_then_secure_per_gpu():
    ops = rp._opcoes(["RTX A", "RTX B"], "qualquer")
    assert [str(o) for o in ops] == [
        "RTX A (community)",
        "RTX A (secure)",
        "RTX B (community)",
        "RTX B (secure)",
    ]
    assert [o.nuvem for o in rp._opcoes(["RTX A"], "secure")] == ["secure"]


@pytest.mark.parametrize(
    "status,body,erro",
    [
        (500, "internal", rp.SemVaga),
        (400, "There are no instances currently available", rp.SemVaga),
        (400, "This machine does not have the resources to deploy your pod", rp.SemVaga),
        (401, "unauthorized", rp.RunPodError),
        (400, "imageName is required", rp.RunPodError),
    ],
)
def test_create_errors_are_classified(monkeypatch, status, body, erro):
    monkeypatch.setattr(rp, "_rest", lambda *a, **k: Resp(status, body))
    with pytest.raises(erro) as exc:
        rp.criar(rp.Opcao("RTX A", "community"), {}, "k")
    if erro is rp.RunPodError:
        assert not isinstance(exc.value, rp.SemVaga)


def test_up_falls_back_and_terminates_pods_that_never_start(monkeypatch):
    criados, encerrados = [], []

    def rest(method, path, chave, body=None):
        if method == "POST":
            nuvem = body["cloudType"]
            if nuvem == "COMMUNITY" and body["gpuTypeIds"] == ["RTX A"]:
                return Resp(400, "There are no instances currently available")
            pod_id = f"p{len(criados) + 1}"
            criados.append((pod_id, body["gpuTypeIds"][0], nuvem))
            return Resp(201, {"id": pod_id, "costPerHr": 0.74})
        if method == "DELETE":
            encerrados.append(path.rsplit("/", 1)[1])
            return Resp(204, "")
        raise AssertionError(method)

    def estado(pod_id, chave):
        # p1 (RTX A secure) nunca sobe; p2 (RTX B community) sobe
        return {"id": pod_id, "runtime": {"uptimeInSeconds": 12} if pod_id == "p2" else None}

    monkeypatch.setattr(rp, "_rest", rest)
    monkeypatch.setattr(rp, "estado", estado)
    corpo = rp.corpo_pod(
        rp.Opcao("RTX A", "community"), "img", "n", 80, {"MODE": "pod"}, ["22/tcp"]
    )
    res = rp.subir(
        rp._opcoes(["RTX A", "RTX B"], "qualquer"), corpo, "k", espera_s=0.05, intervalo=0.01
    )
    assert res["pod"]["id"] == "p2" and res["opcao"] == "RTX B (community)"
    assert [t["resultado"] for t in res["tentativas"]] == ["sem vaga", "não subiu"]
    assert encerrados == ["p1"]
    assert criados[0][1:] == ("RTX A", "SECURE")


def test_up_raises_when_nothing_is_available(monkeypatch):
    monkeypatch.setattr(rp, "_rest", lambda *a, **k: Resp(500, "no capacity"))
    with pytest.raises(rp.SemVaga, match="nenhuma opção"):
        rp.subir([rp.Opcao("RTX A", "secure")], {}, "k", espera_s=0.01)


def test_pod_body_pins_cuda_and_keeps_secrets_out():
    corpo = rp.corpo_pod(rp.Opcao("RTX A", "secure"), "img", "n", 80, {"MODE": "pod"}, ["22/tcp"])
    assert corpo["allowedCudaVersions"][0] == "12.8" and corpo["cloudType"] == "SECURE"
    assert corpo["ports"] == ["22/tcp"]  # ComfyUI e Ollama nunca expostos
    assert "RUNPOD_API_KEY" not in json.dumps(corpo)


def test_availability_parsing(monkeypatch):
    def gql(query, chave=None, timeout=30):
        return {
            "gpuTypes": [
                {
                    "id": "NVIDIA GeForce RTX 4090",
                    "displayName": "RTX 4090",
                    "memoryInGb": 24,
                    "secureCloud": True,
                    "communityCloud": True,
                    "secure": {"stockStatus": "Medium", "uninterruptablePrice": 0.74},
                    "community": {"stockStatus": None, "uninterruptablePrice": 0.34},
                }
            ]
        }

    monkeypatch.setattr(rp, "_graphql", gql)
    rows = {r["nuvem"]: r for r in rp.disponibilidade(["NVIDIA GeForce RTX 4090"])}
    assert rows["secure"]["provavel"] and rows["secure"]["usd_hora"] == 0.74
    assert not rows["community"]["provavel"] and rows["community"]["estoque"] == "sem estoque"


def test_self_terminate_falls_back_to_stop(monkeypatch):
    chamadas = []

    def gql(query, chave=None, timeout=30):
        chamadas.append(query.split("(")[0].split()[-1])
        if "podTerminate" in query:
            raise rp.RunPodError("not allowed")
        return {"podStop": {"id": "p1"}}

    monkeypatch.setattr(rp, "_graphql", gql)
    monkeypatch.setenv("RUNPOD_POD_ID", "p1")
    monkeypatch.setenv("RUNPOD_API_KEY", "chave-do-pod")
    assert rp.autoencerrar() == 0
    assert chamadas == ["podTerminate", "podStop"]
    monkeypatch.delenv("RUNPOD_POD_ID")
    assert rp.autoencerrar() == 1


def test_without_key_the_proxy_credential_is_used(monkeypatch, capsys):
    # sem RUNPOD_API_KEY, a chamada sai sem Authorization (o proxy do ambiente inclui)
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    vistos = []

    def request(method, url, json=None, timeout=None, headers=None):
        vistos.append(headers)
        r = requests.Response()
        r.status_code = 401
        return r

    monkeypatch.setattr(rp.requests, "request", request)
    assert rp.main(["listar"]) == 2
    assert "Authorization" not in vistos[0]
    assert "RUNPOD_API_KEY" in capsys.readouterr().err
    monkeypatch.setenv("RUNPOD_API_KEY", "k")
    rp.main(["listar"])
    assert vistos[1]["Authorization"] == "Bearer k"
