"""Cliente mínimo da API HTTP/WebSocket do ComfyUI.

Endpoints usados: GET /system_stats, GET /object_info, POST /upload/image, POST /prompt,
WS /ws?clientId=..., GET /history/{prompt_id}, GET /view.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

VIDEO_EXTENSIONS = (".mp4", ".webm", ".mkv", ".mov")


class ComfyError(Exception):
    pass


class ComfyConnectionError(ComfyError):
    pass


class ComfyValidationError(ComfyError):
    """O ComfyUI recusou o workflow (HTTP 400 em /prompt)."""

    def __init__(self, message: str, details: Any = None):
        super().__init__(message)
        self.details = details


class ComfyExecutionError(ComfyError):
    def __init__(self, message: str, details: Any = None):
        super().__init__(message)
        self.details = details


ProgressFn = Callable[[dict[str, Any]], None]


class ComfyClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    # --- HTTP básico -------------------------------------------------------
    def _get(self, path: str, **kw) -> requests.Response:
        try:
            r = self.session.get(f"{self.base_url}{path}", timeout=self.timeout, **kw)
        except requests.RequestException as e:
            raise ComfyConnectionError(f"ComfyUI inacessível em {self.base_url}: {e}") from e
        r.raise_for_status()
        return r

    def system_stats(self) -> dict[str, Any]:
        return self._get("/system_stats").json()

    def object_info(self) -> dict[str, Any]:
        return self._get("/object_info").json()

    def wait_ready(self, timeout: float = 300.0, interval: float = 1.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            try:
                return self.system_stats()
            except (ComfyConnectionError, requests.HTTPError):
                if time.monotonic() > deadline:
                    raise
                time.sleep(interval)

    # --- Entradas ----------------------------------------------------------
    def upload_image(self, path: Path, name: str) -> str:
        """Envia a imagem para input/ e devolve o valor a usar no LoadImage."""
        with open(path, "rb") as f:
            try:
                r = self.session.post(
                    f"{self.base_url}/upload/image",
                    files={"image": (name, f, "application/octet-stream")},
                    data={"type": "input", "overwrite": "true"},
                    timeout=self.timeout,
                )
            except requests.RequestException as e:
                raise ComfyConnectionError(f"falha no upload: {e}") from e
        r.raise_for_status()
        info = r.json()
        sub = info.get("subfolder") or ""
        return f"{sub}/{info['name']}" if sub else info["name"]

    # --- Execução ----------------------------------------------------------
    def queue_prompt(self, workflow: dict[str, Any], client_id: str) -> str:
        try:
            r = self.session.post(
                f"{self.base_url}/prompt",
                json={"prompt": workflow, "client_id": client_id},
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise ComfyConnectionError(f"falha ao enfileirar: {e}") from e
        if r.status_code == 400:
            try:
                body = r.json()
            except ValueError:
                body = r.text
            msg = body.get("error", {}).get("message") if isinstance(body, dict) else None
            raise ComfyValidationError(msg or "workflow recusado pelo ComfyUI", body)
        r.raise_for_status()
        return r.json()["prompt_id"]

    def get_history(self, prompt_id: str) -> dict[str, Any] | None:
        data = self._get(f"/history/{prompt_id}").json()
        return data.get(prompt_id)

    def wait(
        self,
        prompt_id: str,
        client_id: str,
        timeout: float = 1800.0,
        use_ws: bool = True,
        on_progress: ProgressFn | None = None,
        poll_interval: float = 1.0,
    ) -> dict[str, Any]:
        """Espera o prompt terminar e devolve a entrada do /history."""
        if use_ws:
            try:
                self._wait_ws(prompt_id, client_id, timeout, on_progress)
            except ComfyExecutionError:
                raise
            except Exception as e:  # WebSocket caiu: segue por polling
                if on_progress:
                    on_progress({"type": "ws_fallback", "data": {"error": str(e)}})
        return self._wait_history(prompt_id, timeout, poll_interval)

    def _wait_ws(
        self, prompt_id: str, client_id: str, timeout: float, on_progress: ProgressFn | None
    ) -> None:
        import websocket  # websocket-client

        u = urlparse(self.base_url)
        scheme = "wss" if u.scheme == "https" else "ws"
        ws = websocket.create_connection(
            f"{scheme}://{u.netloc}{u.path}/ws?clientId={client_id}", timeout=timeout
        )
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                raw = ws.recv()
                if not isinstance(raw, str):
                    continue  # quadros binários de prévia
                msg = json.loads(raw)
                data = msg.get("data", {})
                if data.get("prompt_id") not in (None, prompt_id):
                    continue
                if on_progress:
                    on_progress(msg)
                kind = msg.get("type")
                if kind == "execution_error":
                    raise ComfyExecutionError(
                        data.get("exception_message", "erro de execução"), data
                    )
                if kind == "execution_success":
                    return
                if kind == "executing" and data.get("node") is None and data.get("prompt_id"):
                    return
            raise ComfyExecutionError(f"prompt {prompt_id} não terminou em {timeout:.0f} s")
        finally:
            ws.close()

    def _wait_history(self, prompt_id: str, timeout: float, interval: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            entry = self.get_history(prompt_id)
            if entry is not None:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    msgs = [m for m in status.get("messages", []) if m[0] == "execution_error"]
                    details = msgs[-1][1] if msgs else status
                    message = None
                    if isinstance(details, dict):
                        message = details.get("exception_message")
                    raise ComfyExecutionError(message or "erro de execução", details)
                if status.get("completed", True) and entry.get("outputs") is not None:
                    return entry
            if time.monotonic() > deadline:
                raise ComfyExecutionError(f"prompt {prompt_id} não terminou em {timeout:.0f} s")
            time.sleep(interval)

    # --- Saídas ------------------------------------------------------------
    @staticmethod
    def video_outputs(entry: dict[str, Any]) -> list[dict[str, str]]:
        """Arquivos de vídeo do histórico.

        O SaveVideo do ComfyUI 0.38 reporta `{"images": [...], "animated": [true]}`.
        """
        found = []
        for node_output in entry.get("outputs", {}).values():
            animated = any(node_output.get("animated") or [])
            for item in node_output.get("images", []) + node_output.get("gifs", []):
                if item.get("type") == "temp":
                    continue
                if animated or item.get("filename", "").lower().endswith(VIDEO_EXTENSIONS):
                    found.append(item)
        return found

    def download(self, item: dict[str, str], dest: Path) -> int:
        params = {
            "filename": item["filename"],
            "subfolder": item.get("subfolder", ""),
            "type": item.get("type", "output"),
        }
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with self._get("/view", params=params, stream=True) as r, open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                size += len(chunk)
        return size
