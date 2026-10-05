"""Servidores falsos de ComfyUI e Ollama para testar sem GPU.

As respostas imitam o ComfyUI 0.38.0: o SaveVideo aparece no /history como
`{"images": [{"filename", "subfolder", "type": "output"}], "animated": [true]}`
(comfy_api/latest/_ui.py, PreviewVideo.as_dict).
"""

from __future__ import annotations

import json
import threading
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

WORKER = Path(__file__).resolve().parents[1]
I2V_TEMPLATE = WORKER / "workflows" / "i2v_r1v2.v1.api.json"
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


class FakeComfyState:
    def __init__(self):
        self.uploads: dict[str, bytes] = {}
        self.prompts: dict[str, dict] = {}
        self.fail_validation = False
        self.fail_execution = False
        self.history_polls = 0


def _handler(state: FakeComfyState):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code: int, body) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/system_stats":
                return self._json(200, {"system": {"comfyui_version": "0.38.0"}, "devices": []})
            if u.path.startswith("/history/"):
                pid = u.path.rsplit("/", 1)[1]
                state.history_polls += 1
                if pid not in state.prompts or state.history_polls < 2:
                    return self._json(200, {})  # ainda rodando
                prefix = state.prompts[pid]["108"]["inputs"]["filename_prefix"]
                sub, _, base = prefix.rpartition("/")
                if state.fail_execution:
                    entry = {
                        "outputs": {},
                        "status": {
                            "status_str": "error",
                            "completed": False,
                            "messages": [
                                ["execution_error", {"exception_message": "CUDA out of memory"}]
                            ],
                        },
                    }
                else:
                    entry = {
                        "outputs": {
                            "108": {
                                "images": [
                                    {
                                        "filename": f"{base}_00001_.mp4",
                                        "subfolder": sub,
                                        "type": "output",
                                    }
                                ],
                                "animated": [True],
                            }
                        },
                        "status": {"status_str": "success", "completed": True, "messages": []},
                    }
                return self._json(200, {pid: entry})
            if u.path == "/view":
                q = parse_qs(u.query)
                assert q["type"] == ["output"] and q["filename"][0].endswith(".mp4")
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(FAKE_MP4)))
                self.end_headers()
                self.wfile.write(FAKE_MP4)
                return None
            return self._json(404, {})

        def do_POST(self):
            u = urlparse(self.path)
            if u.path == "/upload/image":
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                head = f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode()
                msg = BytesParser(policy=email_policy).parsebytes(head + raw)
                fields = {
                    part.get_param("name", header="content-disposition"): part
                    for part in msg.iter_parts()
                }
                filename = fields["image"].get_filename()
                state.uploads[filename] = fields["image"].get_payload(decode=True)
                return self._json(200, {"name": filename, "subfolder": "", "type": "input"})
            if u.path == "/prompt":
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if state.fail_validation:
                    return self._json(
                        400,
                        {
                            "error": {"message": "Prompt outputs failed validation"},
                            "node_errors": {"129:95": {"errors": ["unet_name not in list"]}},
                        },
                    )
                pid = f"p{len(state.prompts) + 1}"
                state.prompts[pid] = body["prompt"]
                return self._json(200, {"prompt_id": pid, "number": 1, "node_errors": {}})
            return self._json(404, {})

    return H


class FakeOllamaState:
    def __init__(self):
        self.response = (
            "She walks steadily toward the camera with a relaxed stride, arms swinging "
            "naturally, in a medium shot. Static camera, soft daylight."
        )
        self.requests: list[dict] = []


def _ollama_handler(state: FakeOllamaState):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.requests.append(body)
            data = json.dumps(
                {"model": body["model"], "response": state.response, "done": True}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return H


def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


@pytest.fixture
def fake_comfy():
    state = FakeComfyState()
    srv = _serve(_handler(state))
    state.url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield state
    srv.shutdown()


@pytest.fixture
def fake_ollama():
    state = FakeOllamaState()
    srv = _serve(_ollama_handler(state))
    state.url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield state
    srv.shutdown()


@pytest.fixture
def sfw_image(tmp_path) -> Path:
    # PNG 1x1 válido; o conteúdo não importa para os testes sem GPU
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
    )
    p = tmp_path / "entrada.png"
    p.write_bytes(png)
    return p
