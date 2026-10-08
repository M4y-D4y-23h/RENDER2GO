import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import I2V_TEMPLATE, WORKER

from render2go_worker import handler_runpod, jobs, media, safety
from render2go_worker.comfy_client import ComfyClient

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """MP4 sintético (SFW) de 33 quadros, 16 fps."""
    p = tmp_path_factory.mktemp("v") / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=16",
            "-frames:v",
            "33",
            "-pix_fmt",
            "yuv420p",
            str(p),
        ],
        check=True,
    )
    return p.read_bytes()


@pytest.fixture
def storage(clip):
    """Storage falso de URLs assinadas: GET de entradas, PUT de saídas."""
    png = (WORKER.parent / "fixtures" / "sfw" / "padrao-teste-640.png").read_bytes()
    objs = {"/in/img.png": png, "/in/clip.mp4": clip}
    puts: dict[str, tuple[str, bytes]] = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = objs.get(self.path.split("?")[0])
            self.send_response(200 if body is not None else 404)
            self.send_header("Content-Length", str(len(body or b"")))
            self.end_headers()
            self.wfile.write(body or b"")

        def do_PUT(self):
            data = self.rfile.read(int(self.headers["Content-Length"]))
            puts[self.path.split("?")[0]] = (self.headers["Content-Type"], data)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host = f"127.0.0.1:{srv.server_address[1]}"
    yield {"url": f"http://{host}", "host": host, "puts": puts}
    srv.shutdown()


@pytest.fixture
def ctx(fake_comfy, fake_ollama, storage, clip, monkeypatch):
    monkeypatch.setattr(media, "SCHEMES", ("https", "http"))
    monkeypatch.setenv(media.HOSTS_ENV, storage["host"])
    fake_comfy.video_bytes = clip
    jobs._done.clear()
    return jobs.Context(
        comfy=ComfyClient(fake_comfy.url),
        ollama_url=fake_ollama.url,
        filters=safety.DisabledForSfwTests(),
        templates=jobs.load_templates(),
        timeout_s=30,
    )


def _job(storage, **over):
    base = storage["url"]
    job = {
        "job_id": "job_teste_1",
        "mode": "i2v",
        "template": "i2v_r1v2@1",
        "compiler": {"action_pt": "ela caminha pela praia", "presets": {"camera": "parada"}},
        "params": {"width": 640, "height": 640, "duration_s": 5, "seed": 731942055},
        "input_image_url": f"{base}/in/img.png?sig=1",
        "output_upload_url": f"{base}/out/saida.mp4?sig=2",
        "frame_upload_urls": [f"{base}/out/q{i}.jpg?sig=3" for i in range(3)],
    }
    job.update(over)
    return job


def test_production_templates_only():
    refs = set(jobs.load_templates())
    assert refs == {"i2v_r1v2@1", "t2v_a14b_fp8_lightning@1"}  # nada de experimental/
    assert I2V_TEMPLATE.parent == WORKER / "workflows"


def test_i2v_end_to_end(ctx, storage, fake_comfy, fake_ollama, clip):
    res = jobs.run(_job(storage), ctx)
    assert res["status"] == "ok", res
    assert res["output"]["bytes"] == len(clip) and res["output"]["uploaded"]
    assert storage["puts"]["/out/saida.mp4"] == ("video/mp4", clip)
    frames = [storage["puts"][f"/out/q{i}.jpg"] for i in range(3)]
    assert all(ct == "image/jpeg" and data[:2] == b"\xff\xd8" for ct, data in frames)
    assert res["frames"] == 3 and res["seed"] == 731942055
    assert res["compiled_prompt"].endswith("Static camera.")  # preset anexado pelo código
    assert res["versions"]["compiler"] == "i2v@5" and res["versions"]["template"] == "i2v_r1v2@1"
    assert res["worker"]["safety"] == safety.SFW_TESTS
    wf = next(iter(fake_comfy.prompts.values()))
    assert wf["129:86"]["inputs"]["noise_seed"] == 731942055
    assert fake_ollama.requests[0]["prompt"].startswith("ação: ela caminha pela praia")


def test_replay_is_idempotent(ctx, storage, fake_comfy):
    first = jobs.run(_job(storage), ctx)
    again = jobs.run(_job(storage), ctx)
    assert again["replayed"] and again["output"] == first["output"]
    assert len(fake_comfy.prompts) == 1  # não renderizou de novo


def test_continue_and_t2v(ctx, storage, fake_comfy):
    cont = _job(
        storage,
        job_id="job_cont",
        mode="continuar",
        input_image_url=None,
        input_video_url=f"{storage['url']}/in/clip.mp4",
    )
    del cont["input_image_url"]
    assert jobs.run(cont, ctx)["status"] == "ok"
    t2v = _job(
        storage,
        job_id="job_t2v",
        mode="t2v",
        template="t2v_a14b_fp8_lightning@1",
        compiler={"prompt_en": "A cat sleeps on a window sill. Static camera."},
        params={"width": 832, "height": 480, "duration_s": 5, "seed": 1},
    )
    del t2v["input_image_url"]
    res = jobs.run(t2v, ctx)
    assert res["status"] == "ok" and res["versions"]["compiler"] is None


@pytest.mark.parametrize(
    "over,code",
    [
        ({"job_id": "../x"}, "INVALID_INPUT"),
        ({"mode": "t2v"}, "INVALID_INPUT"),  # template de imagem no modo t2v
        ({"template": "i2v_r1v2@9"}, "INVALID_INPUT"),
        ({"params": {"seed": 1, "width": 650}}, "INVALID_INPUT"),
        ({"compiler": {}}, "INVALID_INPUT"),
        ({"output_upload_url": None}, "INVALID_INPUT"),
        ({"input_image_url": "https://evil.example/x.png"}, "UNSAFE_URL"),
        ({"input_image_url": "http://169.254.169.254/latest"}, "UNSAFE_URL"),
        ({"compiler": {"action_pt": "x", "presets": {"camera": "drone"}}}, "INVALID_INPUT"),
    ],
)
def test_typed_input_errors(ctx, storage, over, code):
    res = jobs.run(_job(storage, **over), ctx)
    assert res["status"] == "error" and res["error"]["code"] == code, res
    assert res["error"]["retryable"] is False


def test_bad_media_and_upload_failures(ctx, storage):
    res = jobs.run(_job(storage, input_image_url=f"{storage['url']}/in/clip.mp4"), ctx)
    assert res["error"]["code"] == "INPUT_INVALID_MEDIA"
    res = jobs.run(_job(storage, job_id="j2", input_image_url=f"{storage['url']}/in/nada.png"), ctx)
    assert res["error"]["code"] == "INPUT_INVALID_MEDIA"  # 404: não adianta repetir


def test_refusal_and_compiler_down(ctx, storage, fake_ollama, fake_comfy):
    fake_ollama.response = "REFUSED"
    res = jobs.run(_job(storage), ctx)
    assert res["status"] == "refused" and "compilador" in res["refusal"]
    assert not fake_comfy.prompts
    ctx.ollama_url = "http://127.0.0.1:9"
    res = jobs.run(_job(storage, job_id="j3"), ctx)
    assert res["error"] == {**res["error"], "code": "COMPILER_UNAVAILABLE", "retryable": True}


def test_comfy_errors_are_typed(ctx, storage, fake_comfy):
    fake_comfy.fail_validation = True
    assert jobs.run(_job(storage), ctx)["error"]["code"] == "COMFY_VALIDATION"
    fake_comfy.fail_validation, fake_comfy.fail_execution = False, True
    res = jobs.run(_job(storage, job_id="j4"), ctx)
    assert res["error"]["code"] == "COMFY_EXECUTION" and res["error"]["retryable"]


def test_fail_closed_without_filters_or_hosts(ctx, storage, monkeypatch):
    ctx.filters = None
    assert jobs.run(_job(storage), ctx)["error"]["code"] == "SAFETY_NOT_CONFIGURED"
    monkeypatch.delenv("RENDER2GO_SAFETY", raising=False)
    with pytest.raises(safety.SafetyNotConfigured):
        safety.load_filters()
    monkeypatch.delenv(media.HOSTS_ENV)
    with pytest.raises(media.UnsafeUrl, match="não configurado"):
        media.check_url("https://storage.example/x")


def test_handler_requests_new_worker_when_comfy_is_down(ctx, storage):
    ctx.comfy = ComfyClient("http://127.0.0.1:9")
    out = handler_runpod.make_handler(ctx)({"id": "rp1", "input": _job(storage)})
    assert out["error"]["code"] == "COMFY_UNAVAILABLE" and out["refresh_worker"] is True
    assert out["error_code"] == "COMFY_UNAVAILABLE" and out["retryable"] is True
