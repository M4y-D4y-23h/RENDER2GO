"""Envia um template de workflow (formato API) ao ComfyUI e baixa o MP4 gerado.

Fluxo: [compila o pedido no Ollama] → envia a imagem (/upload/image) → preenche o
template pelo contrato → POST /prompt → acompanha /ws → /history → /view → grava o MP4.

Códigos de saída:
  0 sucesso · 2 parâmetro inválido · 3 recusado pelo compilador
  4 erro do ComfyUI (validação ou execução) · 5 serviço inacessível
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import time
import uuid
from pathlib import Path

from gpu2go_worker import compiler
from gpu2go_worker import template as tpl_mod
from gpu2go_worker.comfy_client import (
    ComfyClient,
    ComfyConnectionError,
    ComfyExecutionError,
    ComfyValidationError,
)

EXIT_OK, EXIT_PARAM, EXIT_REFUSED, EXIT_COMFY, EXIT_UNREACHABLE = 0, 2, 3, 4, 5
SEED_MAX = 2**53 - 1


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _progress(msg: dict) -> None:
    kind, data = msg.get("type"), msg.get("data", {})
    if kind == "progress":
        _log(f"  passo {data.get('value')}/{data.get('max')} (nó {data.get('node')})")
    elif kind == "executing" and data.get("node"):
        _log(f"  executando nó {data['node']}")
    elif kind == "ws_fallback":
        _log(f"  WebSocket indisponível ({data.get('error')}); acompanhando por /history")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gpu2go-submit",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n\n", 2)[2],
    )
    p.add_argument("--comfy", default="http://127.0.0.1:8188", help="URL do ComfyUI")
    p.add_argument("--template", type=Path, required=True, help="arquivo .api.json")
    p.add_argument("--image", type=Path, help="imagem de entrada (I2V)")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--text-pt", help="pedido em português (passa pelo compilador)")
    src.add_argument("--prompt-en", help="prompt já em inglês (pula o compilador)")
    p.add_argument("--ollama", default="http://127.0.0.1:11434", help="URL do Ollama")
    p.add_argument("--profile", default="i2v", choices=sorted(compiler.PROFILES))
    p.add_argument("--model", default=compiler.DEFAULT_MODEL, help="modelo do compilador")
    p.add_argument("--keep-alive", default="0", help="keep_alive do Ollama (0, 10m...)")
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--duration", type=int, help="segundos (inteiro, 3 a 7)")
    p.add_argument("--seed", type=int, help="padrão: aleatória")
    p.add_argument("--out", type=Path, required=True, help="caminho do MP4 de saída")
    p.add_argument("--timeout", type=float, default=1800.0, help="limite de espera em segundos")
    p.add_argument("--no-ws", action="store_true", help="acompanhar só por /history")
    return p


def run(a: argparse.Namespace) -> tuple[int, dict]:
    timings: dict[str, float] = {}
    result: dict = {"status": "error", "timings": timings}
    t_start = time.monotonic()

    try:
        template = tpl_mod.load(a.template)
    except (tpl_mod.TemplateError, OSError, json.JSONDecodeError) as e:
        return EXIT_PARAM, {**result, "error": f"template inválido: {e}"}
    result["template"] = template.ref

    comfy = ComfyClient(a.comfy)
    try:
        stats = comfy.system_stats()
    except ComfyConnectionError as e:
        return EXIT_UNREACHABLE, {**result, "error": str(e)}
    result["comfyui"] = stats.get("system", {}).get("comfyui_version")

    params: dict = {
        "seed": a.seed if a.seed is not None else secrets.randbelow(SEED_MAX + 1),
    }
    for name, value in (("width", a.width), ("height", a.height), ("duration_s", a.duration)):
        if value is not None:
            params[name] = value

    if "image" in template.contract["params"]:
        if a.image is None or not a.image.is_file():
            return EXIT_PARAM, {**result, "error": "este template exige --image com um arquivo"}
        params["image"] = "placeholder"  # valida o resto antes de compilar e enviar

    params["prompt_en"] = a.prompt_en or "placeholder"
    try:
        template.apply(params)
    except tpl_mod.TemplateError as e:
        return EXIT_PARAM, {**result, "error": str(e)}

    if a.text_pt is not None:
        _log("compilando o pedido...")
        try:
            comp = compiler.compile_prompt(
                a.text_pt,
                profile=a.profile,
                ollama_url=a.ollama,
                model=a.model,
                keep_alive=a.keep_alive,
            )
        except compiler.CompilerRefused as e:
            return EXIT_REFUSED, {**result, "status": "refused", "error": str(e)}
        except compiler.CompilerError as e:
            code = EXIT_UNREACHABLE if "inacessível" in str(e) else EXIT_PARAM
            return code, {**result, "error": str(e)}
        params["prompt_en"] = comp.prompt
        timings["compile_s"] = round(comp.seconds, 2)
        result["compiler"] = comp.ref
        _log(f"prompt ({comp.word_count} palavras): {comp.prompt}")
        if not comp.word_count_ok:
            low, high = comp.word_range
            _log(f"  AVISO: fora da faixa de {low}-{high} palavras do perfil {comp.ref}")
            result["warnings"] = [f"prompt com {comp.word_count} palavras (faixa {low}-{high})"]
    result["prompt_en"] = params["prompt_en"]

    try:
        if "image" in params:
            t0 = time.monotonic()
            name = f"gpu2go_{uuid.uuid4().hex}{a.image.suffix.lower() or '.png'}"
            params["image"] = comfy.upload_image(a.image, name)
            timings["upload_s"] = round(time.monotonic() - t0, 2)
        params["filename_prefix"] = f"video/gpu2go_{uuid.uuid4().hex[:12]}"
        workflow, effective = template.apply(params)
        result["params"] = {k: v for k, v in effective.items() if k != "prompt_en"}

        client_id = uuid.uuid4().hex
        t0 = time.monotonic()
        prompt_id = comfy.queue_prompt(workflow, client_id)
        result["prompt_id"] = prompt_id
        _log(f"enfileirado: {prompt_id}")
        entry = comfy.wait(
            prompt_id, client_id, timeout=a.timeout, use_ws=not a.no_ws, on_progress=_progress
        )
        timings["render_s"] = round(time.monotonic() - t0, 2)

        videos = comfy.video_outputs(entry)
        if not videos:
            return EXIT_COMFY, {**result, "error": "o workflow terminou sem vídeo na saída"}
        t0 = time.monotonic()
        size = comfy.download(videos[0], a.out)
        timings["download_s"] = round(time.monotonic() - t0, 2)
    except tpl_mod.TemplateError as e:
        return EXIT_PARAM, {**result, "error": str(e)}
    except ComfyConnectionError as e:
        return EXIT_UNREACHABLE, {**result, "error": str(e)}
    except (ComfyValidationError, ComfyExecutionError) as e:
        return EXIT_COMFY, {**result, "error": str(e), "details": e.details}

    digest = hashlib.sha256(a.out.read_bytes()).hexdigest()
    timings["total_s"] = round(time.monotonic() - t_start, 2)
    result.update(status="ok", output={"path": str(a.out), "bytes": size, "sha256": digest})
    return EXIT_OK, result


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    code, result = run(a)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if code != EXIT_OK:
        _log(f"falhou ({code}): {result.get('error')}")
    return code


if __name__ == "__main__":
    sys.exit(main())
