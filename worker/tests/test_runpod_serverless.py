import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import WORKER

from render2go_worker import bench, runpod_endpoints, runpod_pods
from render2go_worker.safety import SFW_TESTS

INFRA = WORKER.parent / "infra" / "runpod"


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.ok = 200 <= status < 300
        self.text = json.dumps(body)

    def json(self):
        return self._body


def _cfg(name="i2v-economico.json"):
    return json.loads((INFRA / name).read_text())


def test_shipped_configs_build_only_with_filters_and_hosts():
    for name in ("i2v-economico.json", "t2v-economico.json"):
        cfg = _cfg(name)
        with pytest.raises(runpod_pods.RunPodError, match="fail-closed"):
            runpod_endpoints.build(cfg, "reg/img:1", "1", "storage.example", False)
        with pytest.raises(runpod_pods.RunPodError, match="teste"):
            runpod_endpoints.build(cfg, "reg/img:1", "1", "storage.example", True)
        with pytest.raises(runpod_pods.RunPodError, match="ALLOWED_URL_HOSTS"):
            runpod_endpoints.build(cfg, "reg/img:1", "1", None, True, "teste")
        tpl, ep = runpod_endpoints.build(
            cfg, "reg/img:2026.10.1", "2026.10.1", "storage.example", True, "teste"
        )
        assert ep["name"].endswith("-teste") and tpl["isServerless"] is True
        assert tpl["env"]["RENDER2GO_SAFETY"] == SFW_TESTS and tpl["ports"] == []
        assert tpl["env"]["RENDER2GO_IMAGE_VERSION"] == "2026.10.1"
        assert ep["allowedCudaVersions"][0] == "12.8" and ep["flashboot"] is True


def test_old_cuda_is_rejected():
    cfg = _cfg()
    cfg["endpoint"]["allowedCudaVersions"] = ["12.4", "12.8"]
    with pytest.raises(runpod_pods.RunPodError, match="12.8"):
        runpod_endpoints.build(cfg, "i", "1", "h", True, "teste")


def test_apply_creates_then_updates_by_name(monkeypatch):
    store = {"templates": [], "endpoints": []}
    calls = []

    def rest(method, path, chave, body=None):
        kind = path.strip("/").split("/")[0]
        calls.append((method, path))
        if method == "GET":
            return Resp(200, store[kind])
        if method == "POST":
            obj = {**body, "id": f"{kind[0]}{len(store[kind]) + 1}"}
            store[kind].append(obj)
            return Resp(200, obj)
        if method == "PATCH":
            obj = next(x for x in store[kind] if x["id"] == path.rsplit("/", 1)[1])
            assert "isServerless" not in body
            obj.update(body)
            return Resp(200, obj)
        raise AssertionError(method)

    monkeypatch.setattr(runpod_endpoints, "_rest", rest)
    tpl, ep = runpod_endpoints.build(_cfg(), "reg/img:1", "1", "s.example", True, "teste")
    first = runpod_endpoints.apply(tpl, ep, "k")
    tpl2, ep2 = runpod_endpoints.build(_cfg(), "reg/img:2", "2", "s.example", True, "teste")
    second = runpod_endpoints.apply(tpl2, ep2, "k")
    assert first == second == {"template_id": "t1", "endpoint_id": "e1", "name": ep["name"]}
    assert store["templates"][0]["imageName"] == "reg/img:2"
    assert store["endpoints"][0]["templateId"] == "t1"
    assert [m for m, _ in calls].count("POST") == 2 and [m for m, _ in calls].count("PATCH") == 2


def test_percentiles_and_summary():
    assert bench.pct([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5
    assert bench.pct([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert bench.pct([], 50) is None
    rows = [
        {
            "n": 1,
            "cell": "640x640_5s",
            "status": "ok",
            "cold": True,
            "delay_s": 95.0,
            "exec_s": 66.0,
            "error": None,
            "rp_status": "COMPLETED",
        },
        *[
            {
                "n": i,
                "cell": "640x640_5s",
                "status": "ok",
                "cold": False,
                "delay_s": 1.0,
                "exec_s": 58.0 + i % 3,
                "error": None,
                "rp_status": "COMPLETED",
            }
            for i in range(2, 12)
        ],
        {
            "n": 12,
            "cell": "640x640_5s",
            "status": "error",
            "cold": False,
            "delay_s": 1.0,
            "exec_s": 3.0,
            "error": "COMFY_EXECUTION",
            "rp_status": "COMPLETED",
        },
    ]
    s = bench.summarize(rows, price_usd_h=1.10, idle_timeout_s=30)
    c = s["celulas"]["640x640_5s"]
    assert s["ok"] == 11 and s["cold_starts"] == 1 and s["subida_p50_s"] == 94.0
    assert c["exec_p50_s"] == 59.0 and c["custo_quente_usd"] == round(59 * 1.10 / 3600, 4)
    assert c["custo_isolado_usd"] == round((59 + 94 + 30) * 1.10 / 3600, 4)
    assert s["falhas"] == [{"n": 12, "erro": "COMFY_EXECUTION"}]


@pytest.fixture
def fake_serverless():
    """API do RunPod Serverless falsa: /run guarda o job, /status devolve COMPLETED."""
    jobs: dict[str, dict] = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            inp = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["input"]
            rid = f"r{len(jobs) + 1}"
            jobs[rid] = inp
            self._send({"id": rid, "status": "IN_QUEUE"})

        def do_GET(self):
            rid = self.path.rsplit("/", 1)[1]
            n = int(rid[1:])
            out = {
                "status": "ok",
                "timings": {"render_s": 55.0},
                "worker": {"job_index": n, "uptime_s": 10.0 * n},
            }
            self._send(
                {
                    "id": rid,
                    "status": "COMPLETED",
                    "delayTime": 90000 if n == 1 else 800,
                    "executionTime": 60000,
                    "output": out,
                    "workerId": "w1",
                }
            )

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", jobs
    srv.shutdown()


def test_bench_end_to_end(fake_serverless, tmp_path, monkeypatch):
    url, jobs = fake_serverless
    monkeypatch.setenv("RUNPOD_API_KEY", "k")
    monkeypatch.setattr(bench, "API", url)
    plan = json.loads((WORKER / "evals" / "bench-t2v-economico.v1.json").read_text())
    plan["per_cell"] = 2
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan))
    assert bench.main([str(p), "--endpoint", "e1", "--out", str(tmp_path / "out")]) == 0
    s = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert s["jobs"] == 4 and s["cold_starts"] == 1 and s["subida_p50_s"] == 89.2
    first = jobs["r1"]
    assert first["discard_output"] is True and first["template"] == "t2v_a14b_fp8_lightning@1"
    assert first["params"]["seed"] != jobs["r2"]["params"]["seed"]
    assert "| 832x480_5s |" in (tmp_path / "out" / "summary.md").read_text()


def test_github_mode_only_adjusts_an_existing_endpoint(monkeypatch):
    store = {"endpoints": [], "templates": [{"id": "tg", "name": "rp-gerado", "imageName": "x"}]}
    patches = []

    def rest(method, path, chave, body=None):
        kind = path.strip("/").split("/")[0]
        if method == "GET":
            return Resp(200, store[kind])
        if method == "PATCH":
            patches.append((path, body))
            return Resp(200, {"id": path.rsplit("/", 1)[1]})
        raise AssertionError(f"{method} não deveria ser chamado")

    monkeypatch.setattr(runpod_endpoints, "_rest", rest)
    tpl, ep = runpod_endpoints.build(_cfg(), "github", "v0.3.0", "s.example", True, "teste")
    with pytest.raises(runpod_pods.RunPodError, match="console"):
        runpod_endpoints.apply_github(tpl, ep, "k")
    store["endpoints"].append({"id": "e9", "name": ep["name"], "templateId": "tg"})
    out = runpod_endpoints.apply_github(tpl, ep, "k")
    assert out == {"template_id": "tg", "endpoint_id": "e9", "name": ep["name"]}
    (p_ep, b_ep), (p_tpl, b_tpl) = patches
    assert p_ep == "/endpoints/e9" and b_ep["flashboot"] is True
    assert p_tpl == "/templates/tg" and set(b_tpl) == {"env"}  # a imagem não é tocada
    assert b_tpl["env"]["RENDER2GO_IMAGE_VERSION"] == "v0.3.0"


def test_cli_requires_exactly_one_image_source(monkeypatch, capsys):
    cfg = str(INFRA / "i2v-economico.json")
    assert (
        runpod_endpoints.main(["plan", cfg, "--hosts", "h", "--sfw-tests", "--sufixo", "teste"])
        == 2
    )
    assert "um dos dois" in capsys.readouterr().err
    assert (
        runpod_endpoints.main(
            ["plan", cfg, "--github", "--hosts", "h", "--sfw-tests", "--sufixo", "teste"]
        )
        == 2
    )
    assert (
        runpod_endpoints.main(
            [
                "plan",
                cfg,
                "--github",
                "--versao",
                "v1",
                "--hosts",
                "h",
                "--sfw-tests",
                "--sufixo",
                "teste",
            ]
        )
        == 0
    )


def test_runpod_dockerfiles_in_sync_and_classic():
    import subprocess
    import sys

    tool = WORKER.parent / "tools" / "build_runpod_dockerfiles.py"
    r = subprocess.run([sys.executable, str(tool), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    for name, last in (("Dockerfile.i2v", "full"), ("Dockerfile.t2v", "full-t2v")):
        text = (INFRA / name).read_text()
        assert "--mount" not in text and "<<" not in text  # formato clássico
        froms = [line.split()[-1] for line in text.splitlines() if line.startswith("FROM ")]
        assert froms[-1] == last and "bakeoff" not in froms
        copies = [
            line for line in text.splitlines() if line.startswith("COPY ") and "--from=" not in line
        ]
        assert copies and all(" worker/" in c for c in copies)
        assert text.rstrip().endswith("ENV MODE=serverless")
