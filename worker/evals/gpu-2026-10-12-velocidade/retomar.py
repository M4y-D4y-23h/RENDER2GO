"""Retoma o pod da medição de 12/10/2026 depois de o medir_velocidade.py ser parado de propósito.

Por quê: a fase 2 do no_pod.py perdeu o detalhe (ver complemento.py). Para refazer isso com os
vídeos que estão no disco do pod, o medir_velocidade.py foi parado antes de encerrar o pod (o
desligamento automático do agente seguiu armado). Este script:
1. pega o token do agente nas variáveis do pod (API do RunPod; nunca imprime);
2. espera o no_pod.py terminar, baixa os resultados;
3. roda o complemento.py no pod e baixa o que ele gerou;
4. encerra o pod (try/finally) e confere na API que ele não existe mais.

Uso: uv run python evals/gpu-2026-10-12-velocidade/retomar.py <id do pod> --ate-epoch <limite>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from render2go_worker import lote_nuvem as ln

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import medir_velocidade as mv  # noqa: E402

RAIZ = "/root/render2go"


def gql(consulta: str) -> dict:
    """GraphQL do RunPod pelo curl (o proxy da sessão inclui a credencial)."""
    out = subprocess.run(
        ["curl", "-sS", "--max-time", "30", "-X", "POST", "https://api.runpod.io/graphql",
         "-H", "Content-Type: application/json", "-d", json.dumps({"query": consulta})],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return json.loads(out)


def esperar(ag: ln.Agente, arquivo: str, marca: str, limite: float) -> bool:
    visto = ""
    while time.time() < limite:
        try:
            _, out = ag.exec(f"tail -2 {arquivo}", 30)
        except Exception as e:  # noqa: BLE001 (rede: tenta de novo)
            ln._log(f"AVISO: {e}")
            out = ""
        if out != visto:
            ln._log(f"no pod: {out.strip()[-300:]}")
            visto = out
        if marca in out:
            return True
        time.sleep(30)
    return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("pod")
    p.add_argument("--ate-epoch", type=float, required=True, help="parar de esperar aqui")
    p.add_argument("--saida", type=Path, default=AQUI / "saida")
    a = p.parse_args()
    d = gql(f'query {{ pod(input: {{podId: "{a.pod}"}}) {{ id desiredStatus env }} }}')
    env = d["data"]["pod"]["env"]
    token = next(e.split("=", 1)[1] for e in env if e.startswith("AGENT_TOKEN="))
    ag = ln.Agente(f"https://{a.pod}-8888.proxy.runpod.net", token)
    pod = SimpleNamespace(agente=ag)
    r: dict = {}
    try:
        r["roteiro_terminou"] = esperar(ag, f"{RAIZ}/medidas.log", "FIM_DO_ROTEIRO", a.ate_epoch)
        r["baixado_1"] = mv.baixar(pod, RAIZ, a.saida)
        ag.put(AQUI / "complemento.py", f"{RAIZ}/complemento.py")
        ag.bg(
            f"cd /opt/render2go/worker && PATH=/opt/venv/bin:$PATH PYTHONPATH=src "
            f"RENDER2GO_SAFETY=c1-revisao-humana python {RAIZ}/complemento.py {RAIZ}/medidas "
            f"> {RAIZ}/complemento.log 2>&1; echo FIM_DO_COMPLEMENTO >> {RAIZ}/complemento.log",
            f"{RAIZ}/complemento.bg.log",
        )
        r["complemento_terminou"] = esperar(
            ag, f"{RAIZ}/complemento.log", "FIM_DO_COMPLEMENTO", a.ate_epoch
        )
        r["baixado_2"] = mv.baixar(pod, RAIZ, a.saida)
        for remoto, local in ((f"{RAIZ}/medidas/complemento.json", "complemento.json"),
                              (f"{RAIZ}/complemento.log", "complemento.log")):  # fmt: skip
            try:
                r[local] = ag.get(remoto, a.saida / local)
            except Exception as e:  # noqa: BLE001
                ln._log(f"AVISO: {local}: {e}")
        return 0
    finally:
        try:
            ln.RunPod().encerrar(a.pod)
            ln._log(f"pod {a.pod} encerrado")
        except Exception as e:  # noqa: BLE001
            ln._log(f"AVISO: encerre à mão o pod {a.pod}: {e}")
        time.sleep(5)
        try:
            r["pod_depois"] = gql(
                f'query {{ pod(input: {{podId: "{a.pod}"}}) {{ id desiredStatus }} }}'
            )
        except Exception as e:  # noqa: BLE001
            r["pod_depois"] = str(e)
        (a.saida / "retomada.json").write_text(json.dumps(r, indent=1, ensure_ascii=False))
        ln._log(f"RETOMADA: {json.dumps(r, ensure_ascii=False)}")


if __name__ == "__main__":
    sys.exit(main())
