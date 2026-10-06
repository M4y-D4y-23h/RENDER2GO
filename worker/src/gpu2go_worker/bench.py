"""Benchmark de um endpoint serverless: cold start, tempo quente, p50/p95 e custo por vídeo.

Um plano (worker/evals/bench-*.json) diz o modo, o template, as células (resolução ×
duração), quantos jobs por célula e o preço por hora do tier. Os jobs vão em série (um de
cada vez), para medir o tempo de um worker sem fila. O primeiro job de cada worker é
frio; `--frios N` força N cold starts extras, esperando o idle timeout entre eles.

Para cada job, o RunPod devolve `delayTime` (fila + subida do worker) e `executionTime`
(o handler), e o worker devolve `worker.job_index` (1 = primeiro job daquele worker).

Custo:
- quente = executionTime × preço/s (worker já de pé, fila contínua);
- isolado = (subida + executionTime + idle timeout) × preço/s, um vídeo sozinho com o
  worker ligando e desligando só para ele. É o pior caso.

Saída: results.jsonl (um job por linha) e summary.json/summary.md. Só conteúdo SFW: o
endpoint de teste roda com GPU2GO_SAFETY=disabled-sfw-tests.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import secrets
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import requests

from gpu2go_worker.runpod_pods import RunPodError, _chave, auth_headers

API = os.environ.get("RUNPOD_SERVERLESS_API", "https://api.runpod.ai/v2")
FIM = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def pct(values: list[float], q: float) -> float | None:
    """Percentil pelo método do posto mais próximo (sem interpolar)."""
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(q / 100 * len(s)) - 1)]


class Client:
    def __init__(self, endpoint_id: str, chave: str, api: str | None = None):
        self.base = f"{(api or API).rstrip('/')}/{endpoint_id}"
        self.h = auth_headers(chave)  # sem chave: o proxy do ambiente inclui a credencial

    def run(self, inp: dict) -> str:
        r = requests.post(f"{self.base}/run", json={"input": inp}, headers=self.h, timeout=60)
        if not r.ok:
            raise RunPodError(f"/run: {r.status_code} {r.text[:300]}")
        return r.json()["id"]

    def status(self, rid: str) -> dict:
        r = requests.get(f"{self.base}/status/{rid}", headers=self.h, timeout=60)
        if not r.ok:
            raise RunPodError(f"/status: {r.status_code} {r.text[:300]}")
        return r.json()

    def wait(self, rid: str, timeout: float, intervalo: float = 2.0) -> dict:
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            st = self.status(rid)
            if st.get("status") in FIM:
                return st
            time.sleep(intervalo)
        return {"id": rid, "status": "CLIENT_TIMEOUT"}


def job_input(plan: dict, cell: dict, seed: int, n: int) -> dict:
    inp = {
        "job_id": f"bench_{secrets.token_hex(4)}_{n}",
        "mode": plan["mode"],
        "template": plan["template"],
        "compiler": dict(plan["compiler"]),
        "params": {**cell, "seed": seed},
        "discard_output": True,
        "frame_upload_urls": [],
    }
    for k in ("input_image_url", "input_video_url", "output_upload_url"):
        if plan.get(k):
            inp[k] = plan[k]
    if plan.get("output_upload_url"):
        inp.pop("discard_output")
    return inp


def registro(cell: dict, st: dict, n: int) -> dict:
    out = st.get("output") or {}
    worker = out.get("worker") or {}
    return {
        "n": n,
        "cell": f"{cell['width']}x{cell['height']}_{cell['duration_s']}s",
        "rp_status": st.get("status"),
        "status": out.get("status"),
        # em erro o SDK move `error` para fora da saída; `error_code` fica nela
        "error": out.get("error_code") or (out.get("error") or {}).get("code") or st.get("error"),
        "delay_s": round((st.get("delayTime") or 0) / 1000, 2),
        "exec_s": round((st.get("executionTime") or 0) / 1000, 2),
        "render_s": (out.get("timings") or {}).get("render_s"),
        "compile_s": (out.get("timings") or {}).get("compile_s"),
        "cold": worker.get("job_index") == 1,
        "worker_uptime_s": worker.get("uptime_s"),
        "worker_id": st.get("workerId"),
    }


def summarize(rows: list[dict], price_usd_h: float, idle_timeout_s: float) -> dict[str, Any]:
    ps = price_usd_h / 3600
    ok = [r for r in rows if r["status"] == "ok"]
    warm = [r for r in ok if not r["cold"]]
    cold = [r for r in ok if r["cold"]]
    warm_delay = statistics.median([r["delay_s"] for r in warm]) if warm else 0.0
    boot = [max(r["delay_s"] - warm_delay, 0) for r in cold]
    cells = {}
    for cell in sorted({r["cell"] for r in rows}):
        cw = [r["exec_s"] for r in warm if r["cell"] == cell]
        if not cw:
            continue
        p50 = pct(cw, 50)
        cells[cell] = {
            "n_quentes": len(cw),
            "exec_p50_s": p50,
            "exec_p95_s": pct(cw, 95),
            "custo_quente_usd": round(p50 * ps, 4),
            "custo_isolado_usd": round(
                (p50 + (statistics.median(boot) if boot else 0) + idle_timeout_s) * ps, 4
            ),
        }
    return {
        "jobs": len(rows),
        "ok": len(ok),
        "falhas": [
            {"n": r["n"], "erro": r["error"] or r["rp_status"]} for r in rows if r["status"] != "ok"
        ],
        "cold_starts": len(cold),
        "subida_p50_s": round(statistics.median(boot), 2) if boot else None,
        "subida_max_s": round(max(boot), 2) if boot else None,
        "fila_quente_p50_s": round(warm_delay, 2),
        "preco_usd_h": price_usd_h,
        "idle_timeout_s": idle_timeout_s,
        "celulas": cells,
    }


def render_md(plan: dict, s: dict) -> str:
    linhas = [
        f"# Bench: {plan['name']}",
        "",
        f"{s['ok']}/{s['jobs']} jobs ok; {s['cold_starts']} cold starts "
        f"(subida p50 {s['subida_p50_s']} s, máx. {s['subida_max_s']} s); "
        f"US$ {s['preco_usd_h']}/h; idle timeout {s['idle_timeout_s']} s.",
        "",
        "| Célula | Jobs quentes | p50 (s) | p95 (s) | Custo quente (US$) | Custo isolado (US$) |",
        "|---|---|---|---|---|---|",
    ]
    for cell, c in s["celulas"].items():
        linhas.append(
            f"| {cell} | {c['n_quentes']} | {c['exec_p50_s']} | {c['exec_p95_s']} "
            f"| {c['custo_quente_usd']} | {c['custo_isolado_usd']} |"
        )
    if s["falhas"]:
        linhas += ["", "Falhas: " + ", ".join(f"#{f['n']} {f['erro']}" for f in s["falhas"])]
    return "\n".join(linhas) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gpu2go-bench", description=__doc__.split("\n\n")[0])
    p.add_argument("plan", type=Path)
    p.add_argument("--endpoint", required=True, help="id do endpoint serverless de teste")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--frios", type=int, default=0, help="cold starts extras forçados")
    p.add_argument("--timeout", type=float, default=900, help="limite por job (s)")
    a = p.parse_args(argv)
    plan = json.loads(a.plan.read_text())
    try:
        cli = Client(a.endpoint, _chave())
    except RunPodError as e:
        _log(f"erro: {e}")
        return 2
    a.out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    seed = plan.get("seed", 1000)
    n = 0
    fila = [(c, i) for c in plan["cells"] for i in range(plan["per_cell"])]
    fila += [(plan["cells"][0], -1)] * a.frios
    with (a.out / "results.jsonl").open("a") as f:
        for cell, i in fila:
            if i == -1:
                espera = plan["idle_timeout_s"] + 30
                _log(f"esperando {espera} s para o worker desligar (cold start forçado)")
                time.sleep(espera)
            n += 1
            try:
                rid = cli.run(job_input(plan, cell, seed + n, n))
                st = cli.wait(rid, a.timeout)
            except RunPodError as e:
                st = {"status": "CLIENT_ERROR", "error": str(e)}
            row = registro(cell, st, n)
            rows.append(row)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            _log(
                f"#{n} {row['cell']} {row['status'] or row['rp_status']} "
                f"fila {row['delay_s']} s, exec {row['exec_s']} s{' (frio)' if row['cold'] else ''}"
            )
    s = summarize(rows, plan["price_usd_h"], plan["idle_timeout_s"])
    (a.out / "summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n")
    (a.out / "summary.md").write_text(render_md(plan, s))
    print(render_md(plan, s))
    return 0 if not s["falhas"] else 1


if __name__ == "__main__":
    sys.exit(main())
