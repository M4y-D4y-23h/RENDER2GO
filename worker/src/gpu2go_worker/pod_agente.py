"""Agente de controle dos pods de lote (ADR 0003, fase 2). Só biblioteca padrão.

Roda no pod efêmero, na porta 8888, atrás do proxy HTTPS do RunPod. Executa comandos e
troca arquivos para o `lote_nuvem`. Toda chamada exige o cabeçalho X-Token igual a
AGENT_TOKEN (aleatório, 256 bits, um por pod, comparado em tempo constante); sem token
válido, a resposta é 403. O ComfyUI e o Ollama continuam só em 127.0.0.1: o agente é o
único ponto de entrada, e o pod se encerra sozinho (desligamento automático) e no fim do
lote.

Desligamento automático: com DEADMAN_S definido, o agente encerra o próprio pod (GraphQL
podTerminate, depois podStop, com a chave restrita que o RunPod injeta no pod) depois de
DEADMAN_S segundos, contados do primeiro segundo do pod. Vale mesmo que o lote_nuvem, a
rede ou a instalação falhem.

O lote_nuvem injeta este arquivo no pod pela variável AGENT_B64 (sem imagem própria):
    bash -c 'echo "$AGENT_B64" | base64 -d > /agente.py && exec python3 /agente.py'

Rotas:
    GET  /saude                      → {"ok": true}
    POST /exec {"cmd", "timeout"}    → {"code", "out"} (últimos 20 kB)
    POST /bg   {"cmd", "log"}        → {"pid"} (processo destacado)
    PUT  /arquivo?caminho=...        ← corpo = conteúdo (até MAX_PUT bytes)
    GET  /arquivo?caminho=...        → conteúdo
"""

from __future__ import annotations

import hmac
import json
import os
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MAX_PUT = 2 * 1024**3  # 2 GB por arquivo (o código do repositório tem < 1 MB)
MAX_JSON = 1024**2


def handler(token: str):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _autorizado(self) -> bool:
            if hmac.compare_digest(self.headers.get("X-Token", ""), token):
                return True
            self._send(403, {"erro": "token inválido"})
            return False

        def _send(self, code: int, body, ctype: str = "application/json") -> None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _caminho(self) -> str:
            return parse_qs(urlparse(self.path).query)["caminho"][0]

        def do_GET(self):
            if not self._autorizado():
                return
            rota = urlparse(self.path).path
            if rota == "/saude":
                return self._send(200, {"ok": True})
            if rota == "/arquivo":
                try:
                    with open(self._caminho(), "rb") as f:
                        return self._send(200, f.read(), "application/octet-stream")
                except (OSError, KeyError) as e:
                    return self._send(404, {"erro": str(e)})
            return self._send(404, {"erro": "rota"})

        def do_PUT(self):
            if not self._autorizado():
                return
            n = int(self.headers.get("Content-Length", "0"))
            if urlparse(self.path).path != "/arquivo" or n > MAX_PUT:
                return self._send(400, {"erro": "rota ou tamanho"})
            caminho = self._caminho()
            os.makedirs(os.path.dirname(caminho) or ".", exist_ok=True)
            tmp, faltam = f"{caminho}.parcial-{os.urandom(4).hex()}", n  # envios simultâneos
            with open(tmp, "wb") as f:
                while faltam:
                    pedaco = self.rfile.read(min(faltam, 1 << 20))
                    if not pedaco:
                        break
                    f.write(pedaco)
                    faltam -= len(pedaco)
            if faltam:
                os.unlink(tmp)
                return self._send(400, {"erro": "corpo incompleto"})
            os.replace(tmp, caminho)
            return self._send(200, {"bytes": n})

        def do_POST(self):
            if not self._autorizado():
                return
            n = int(self.headers.get("Content-Length", "0"))
            if n > MAX_JSON:
                return self._send(400, {"erro": "corpo grande demais"})
            body = json.loads(self.rfile.read(n) or b"{}")
            rota = urlparse(self.path).path
            if rota == "/exec":
                try:
                    p = subprocess.run(
                        ["bash", "-lc", body["cmd"]],
                        capture_output=True,
                        text=True,
                        timeout=body.get("timeout", 90),
                    )
                    return self._send(
                        200, {"code": p.returncode, "out": (p.stdout + p.stderr)[-20000:]}
                    )
                except subprocess.TimeoutExpired:
                    return self._send(200, {"code": -1, "out": "[tempo esgotado]"})
            if rota == "/bg":
                log = body.get("log", "/root/bg.log")
                os.makedirs(os.path.dirname(log) or ".", exist_ok=True)
                with open(log, "ab") as out:
                    p = subprocess.Popen(
                        ["bash", "-lc", body["cmd"]],
                        stdout=out,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                return self._send(200, {"pid": p.pid})
            return self._send(404, {"erro": "rota"})

    return H


def autoencerrar(apos_s: float, log: str = "/root/deadman.txt") -> None:
    """Dorme `apos_s` segundos e encerra este pod no RunPod."""

    def anota(msg: str) -> None:
        with open(log, "a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%SZ ", time.gmtime()) + msg + "\n")

    anota(f"desligamento automático em {apos_s:.0f} s")
    time.sleep(apos_s)
    pod, chave = os.environ.get("RUNPOD_POD_ID"), os.environ.get("RUNPOD_API_KEY")
    if not pod or not chave:
        anota("sem RUNPOD_POD_ID/RUNPOD_API_KEY: nada a encerrar")
        return
    for mutation in (
        f'mutation {{ podTerminate(input:{{podId:"{pod}"}}) }}',
        f'mutation {{ podStop(input:{{podId:"{pod}"}}) {{ id }} }}',
    ):
        for _ in range(5):
            try:
                req = urllib.request.Request(
                    f"https://api.runpod.io/graphql?api_key={chave}",
                    json.dumps({"query": mutation}).encode(),
                    {"Content-Type": "application/json", "User-Agent": "gpu2go-agente"},
                )
                anota(urllib.request.urlopen(req, timeout=30).read().decode()[:300])
                break
            except Exception as e:  # noqa: BLE001 (registra e tenta de novo)
                anota(f"erro: {e}")
                time.sleep(10)
        time.sleep(60)


def servidor(token: str, host: str = "0.0.0.0", porta: int = 8888) -> ThreadingHTTPServer:
    if len(token) < 32:
        raise SystemExit("AGENT_TOKEN curto demais (mínimo 32 caracteres)")
    return ThreadingHTTPServer((host, porta), handler(token))


if __name__ == "__main__":
    if os.environ.get("DEADMAN_S"):
        threading.Thread(
            target=autoencerrar, args=(float(os.environ["DEADMAN_S"]),), daemon=True
        ).start()
    servidor(os.environ["AGENT_TOKEN"]).serve_forever()
