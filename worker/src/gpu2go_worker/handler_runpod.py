"""Handler do RunPod Serverless: camada fina sobre gpu2go_worker.jobs.

O entrypoint (MODE=serverless) sobe o Ollama e o ComfyUI em 127.0.0.1 e depois este
processo, que recebe os jobs pelo SDK oficial do RunPod (pacote `runpod`, MIT).

- Sem filtros C1–C2 configurados, todo job é recusado (fail-closed; ver safety.py).
- O resultado vai na saída do job, com `status` ok | refused | error, e o RunPod o
  repassa ao webhook do backend. Em erro, o SDK tira a chave `error` da saída e marca o
  job como FAILED (comportamento conferido com o SDK 1.12.0 em modo --test_input); por
  isso `error_code` e `retryable` também ficam na saída, para o backend decidir se repete.
- Se o ComfyUI parar de responder, o handler pede ao RunPod um worker novo
  (`refresh_worker`), em vez de seguir quebrado.
"""

from __future__ import annotations

import logging
import os

from gpu2go_worker import jobs, safety
from gpu2go_worker.comfy_client import ComfyClient

log = logging.getLogger("gpu2go.handler")


def build_context() -> jobs.Context:
    comfy = ComfyClient(f"http://127.0.0.1:{os.environ.get('COMFY_PORT', '8188')}")
    comfy.wait_ready(timeout=300)
    try:
        filters = safety.load_filters()
    except safety.SafetyNotConfigured as e:
        log.error("%s", e)
        filters = None
    ctx = jobs.Context(
        comfy=comfy,
        ollama_url=f"http://{os.environ.get('OLLAMA_HOST', '127.0.0.1:11434')}",
        filters=filters,
        templates=jobs.load_templates(),
    )
    log.info(
        "pronto: templates %s, filtros %s",
        sorted(ctx.templates),
        filters.mode if filters else "AUSENTES (jobs recusados)",
    )
    return ctx


def make_handler(ctx: jobs.Context):
    def handler(job: dict) -> dict:
        result = jobs.run(job.get("input") or {}, ctx)
        err = result.get("error")
        if err:
            result["error_code"], result["retryable"] = err["code"], err["retryable"]
            if err["code"] == "COMFY_UNAVAILABLE":
                result["refresh_worker"] = True  # o SDK recicla o worker depois deste job
        return result

    return handler


def main() -> None:
    import runpod  # só na imagem; os testes usam make_handler direto

    logging.basicConfig(level=logging.INFO, format="[gpu2go] %(message)s")
    runpod.serverless.start({"handler": make_handler(build_context())})


if __name__ == "__main__":
    main()
