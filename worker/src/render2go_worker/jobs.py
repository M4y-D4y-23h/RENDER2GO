"""Processamento de um job do worker, independente do provedor (doc 02, seção 4).

Entrada (campo `input` do /run do RunPod; nada de binário, só URLs assinadas):

    {
      "job_id": "job_01J9...",
      "mode": "i2v" | "t2v" | "continuar",
      "template": "i2v_r1v2@1",
      "compiler": {"action_pt": "...", "presets": {"camera": "parada"}, "version": 5,
                   "keep_alive": "0"},          # ou "prompt_en": "..." (pula o compilador)
      "params": {"width": 640, "height": 640, "duration_s": 5, "seed": 731942055},
      "input_image_url": "https://...",          # i2v
      "input_video_url": "https://...",          # continuar (usa o último quadro)
      "output_upload_url": "https://...",        # PUT do MP4
      "frame_upload_urls": ["https://...", ...]  # PUT dos quadros de amostra (0 a 12)
    }

Saída: `status` ok | refused | error; em erro, `error.code`, `error.message` e
`error.retryable` (o backend decide se repete o job com o mesmo `job_id`).

Idempotência: o backend é a fonte da verdade (repete com o mesmo job_id). No worker, um
job_id já concluído devolve o mesmo resultado sem renderizar de novo, e um job_id em
andamento é recusado.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from render2go_worker import compiler, media, presets, safety, video
from render2go_worker import template as tpl_mod
from render2go_worker.comfy_client import (
    ComfyClient,
    ComfyConnectionError,
    ComfyExecutionError,
    ComfyValidationError,
)

JOB_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
MODES = {"i2v": "i2v", "continuar": "i2v", "t2v": "t2v"}  # modo → perfil do compilador
STARTED = time.time()


class JobError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


@dataclass
class Context:
    comfy: ComfyClient
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = compiler.DEFAULT_MODEL
    filters: safety.Filters | None = None
    templates: dict[str, tpl_mod.Template] = field(default_factory=dict)
    timeout_s: float = float(os.environ.get("JOB_TIMEOUT_S", "600"))
    allow_discard: bool = os.environ.get("RENDER2GO_ALLOW_DISCARD_OUTPUT") == "1"
    image_version: str = os.environ.get("RENDER2GO_IMAGE_VERSION", "dev")
    comfyui_version: str | None = None
    jobs_done: int = 0


_lock = threading.Lock()
_running: set[str] = set()
_done: OrderedDict[str, dict] = OrderedDict()
_DONE_MAX = 64


def load_templates(workflows_dir: Path = tpl_mod.WORKFLOWS_DIR) -> dict[str, tpl_mod.Template]:
    """Só os templates de produção (worker/workflows/*.api.json, nunca experimental/)."""
    out = {}
    for path in sorted(workflows_dir.glob("*.api.json")):
        t = tpl_mod.load(path)
        if t.contract.get("experimental"):
            continue
        out[t.ref] = t
    return out


def _err(code: str, message: str, retryable: bool = False) -> JobError:
    return JobError(code, message, retryable)


def _validate(inp: dict[str, Any], ctx: Context) -> tuple[str, tpl_mod.Template]:
    if not isinstance(inp, dict):
        raise _err("INVALID_INPUT", "input deve ser um objeto")
    job_id = inp.get("job_id")
    if not isinstance(job_id, str) or not JOB_ID.match(job_id):
        raise _err("INVALID_INPUT", "job_id ausente ou inválido")
    mode = inp.get("mode")
    if mode not in MODES:
        raise _err("INVALID_INPUT", f"mode deve ser um de {sorted(MODES)}")
    tpl = ctx.templates.get(inp.get("template", ""))
    if tpl is None:
        raise _err("INVALID_INPUT", f"template desconhecido: {inp.get('template')!r}")
    needs_image = "image" in tpl.contract["params"]
    if needs_image != (mode in ("i2v", "continuar")):
        raise _err("INVALID_INPUT", f"o template {tpl.ref} não serve para o modo {mode}")
    if mode == "i2v" and not inp.get("input_image_url"):
        raise _err("INVALID_INPUT", "modo i2v exige input_image_url")
    if mode == "continuar" and not inp.get("input_video_url"):
        raise _err("INVALID_INPUT", "modo continuar exige input_video_url")
    comp = inp.get("compiler") or {}
    if bool(comp.get("action_pt")) == bool(comp.get("prompt_en")):
        raise _err("INVALID_INPUT", "compiler exige action_pt ou prompt_en (só um)")
    if not inp.get("output_upload_url") and not (inp.get("discard_output") and ctx.allow_discard):
        raise _err("INVALID_INPUT", "output_upload_url é obrigatório")
    frames = inp.get("frame_upload_urls") or []
    if not isinstance(frames, list) or len(frames) > media.MAX_FRAMES:
        raise _err("INVALID_INPUT", f"frame_upload_urls: lista de até {media.MAX_FRAMES} URLs")
    params = inp.get("params") or {}
    if not isinstance(params.get("seed"), int):
        raise _err("INVALID_INPUT", "params.seed é obrigatório (o backend sorteia)")
    for url in [
        inp.get(k)
        for k in ("input_image_url", "input_video_url", "output_upload_url")
        if inp.get(k)
    ] + frames:
        try:
            media.check_url(url)
        except media.UnsafeUrl as e:
            raise _err("UNSAFE_URL", str(e)) from e
    return job_id, tpl


def _decide(decision: safety.Decision, stage: str) -> None:
    if not decision.allowed:
        raise _err("REFUSED", f"{stage}: {decision.reason}")


def run(inp: dict[str, Any], ctx: Context) -> dict[str, Any]:
    """Processa um job e devolve só metadados. Nunca levanta exceção."""
    t_total = time.monotonic()
    job_id = inp.get("job_id") if isinstance(inp, dict) else None
    job_id = job_id if isinstance(job_id, str) else None
    with _lock:
        if job_id in _done:
            return {**_done[job_id], "replayed": True}
        if job_id in _running:
            return _error_result(
                job_id,
                _err("DUPLICATE_IN_PROGRESS", "job_id já está em execução neste worker", True),
            )
        if job_id:
            _running.add(job_id)
    workdir = Path(tempfile.mkdtemp(prefix="render2go_job_"))
    try:
        result = _run(inp, ctx, workdir, t_total)
        if result["status"] in ("ok", "refused"):
            with _lock:
                _done[job_id] = result
                while len(_done) > _DONE_MAX:
                    _done.popitem(last=False)
        return result
    except JobError as e:
        if e.code == "REFUSED":
            return {
                "status": "refused",
                "job_id": job_id,
                "refusal": str(e),
                "worker": _worker_info(ctx),
            }
        return _error_result(job_id, e, ctx)
    except Exception as e:  # noqa: BLE001 — qualquer falha vira erro tipado
        return _error_result(job_id, _err("INTERNAL", f"{type(e).__name__}: {e}", True), ctx)
    finally:
        with _lock:
            _running.discard(job_id)
        shutil.rmtree(workdir, ignore_errors=True)


def _worker_info(ctx: Context | None) -> dict[str, Any]:
    info = {"uptime_s": round(time.time() - STARTED, 1)}
    if ctx is not None:
        info["job_index"] = ctx.jobs_done
        info["safety"] = ctx.filters.mode if ctx.filters else None
    return info


def _error_result(job_id: Any, e: JobError, ctx: Context | None = None) -> dict[str, Any]:
    return {
        "status": "error",
        "job_id": job_id,
        "error": {"code": e.code, "message": str(e)[:1000], "retryable": e.retryable},
        "worker": _worker_info(ctx),
    }


def _run(inp: dict[str, Any], ctx: Context, workdir: Path, t_total: float) -> dict[str, Any]:
    if ctx.filters is None:
        raise _err("SAFETY_NOT_CONFIGURED", "filtros C1–C2 ausentes: job recusado (fail-closed)")
    job_id, tpl = _validate(inp, ctx)
    ctx.jobs_done += 1
    timings: dict[str, float] = {}
    mode = inp["mode"]
    comp_in = inp.get("compiler") or {}
    params = dict(inp.get("params") or {})

    # 1. Entrada de mídia
    image_path = None
    if mode in ("i2v", "continuar"):
        t0 = time.monotonic()
        try:
            if mode == "i2v":
                image_path = workdir / "entrada"
                media.download(inp["input_image_url"], image_path, media.MAX_IMAGE_BYTES)
            else:
                clip = workdir / "anterior.mp4"
                media.download(inp["input_video_url"], clip, media.MAX_VIDEO_BYTES)
                image_path = video.last_frame(clip, workdir / "entrada.png")
            media.check_image(image_path)
        except media.UnsafeUrl as e:
            raise _err("UNSAFE_URL", str(e)) from e
        except media.MediaError as e:
            code = "INPUT_DOWNLOAD_FAILED" if e.retryable else "INPUT_INVALID_MEDIA"
            raise _err(code, str(e), e.retryable) from e
        except video.VideoError as e:
            raise _err("INPUT_INVALID_MEDIA", str(e)) from e
        timings["input_s"] = round(time.monotonic() - t0, 2)

    # 2. Texto: filtros antes, compilador, filtros depois
    compiler_ref = None
    if comp_in.get("action_pt"):
        profile = MODES[mode]
        try:
            composed = presets.compose(profile, comp_in["action_pt"], comp_in.get("presets") or {})
        except presets.PresetError as e:
            raise _err("INVALID_INPUT", str(e)) from e
        _decide(ctx.filters.check_request(composed.text), "pedido")
        t0 = time.monotonic()
        try:
            res = compiler.compile_prompt(
                composed.text,
                profile=profile,
                ollama_url=ctx.ollama_url,
                model=ctx.model,
                keep_alive=comp_in.get("keep_alive", "0"),
                version=comp_in.get("version"),
                seed=params["seed"] % (2**31),
                suffix=composed.suffix,
                camera_preset=composed.camera,
            )
        except compiler.CompilerRefused as e:
            raise _err("REFUSED", f"compilador: {e}") from e
        except compiler.CompilerOutputError as e:
            raise _err("COMPILER_OUTPUT", str(e), True) from e
        except compiler.CompilerError as e:
            retry = "inacessível" in str(e)
            raise _err("COMPILER_UNAVAILABLE" if retry else "INVALID_INPUT", str(e), retry) from e
        timings["compile_s"] = round(time.monotonic() - t0, 2)
        prompt_en, compiler_ref = res.prompt, res.ref
    else:
        _decide(ctx.filters.check_request(comp_in["prompt_en"]), "pedido")
        prompt_en = comp_in["prompt_en"]
    _decide(ctx.filters.check_prompt(prompt_en), "prompt")

    # 3. Render
    try:
        if image_path is not None:
            t0 = time.monotonic()
            params["image"] = ctx.comfy.upload_image(
                image_path, f"{job_id}_{uuid.uuid4().hex[:8]}.png"
            )
            timings["comfy_upload_s"] = round(time.monotonic() - t0, 2)
        params["prompt_en"] = prompt_en
        params["filename_prefix"] = f"video/{job_id}_{uuid.uuid4().hex[:8]}"
        workflow, effective = tpl.apply(params)
    except tpl_mod.TemplateError as e:
        raise _err("INVALID_INPUT", str(e)) from e
    except ComfyConnectionError as e:
        raise _err("COMFY_UNAVAILABLE", str(e), True) from e
    t0 = time.monotonic()
    try:
        client_id = uuid.uuid4().hex
        prompt_id = ctx.comfy.queue_prompt(workflow, client_id)
        entry = ctx.comfy.wait(prompt_id, client_id, timeout=ctx.timeout_s)
        videos = ctx.comfy.video_outputs(entry)
        if not videos:
            raise _err("COMFY_EXECUTION", "o workflow terminou sem vídeo", True)
        mp4 = workdir / "saida.mp4"
        size = ctx.comfy.download(videos[0], mp4)
    except ComfyValidationError as e:
        raise _err("COMFY_VALIDATION", str(e)) from e
    except ComfyExecutionError as e:
        timeout = "não terminou" in str(e)
        raise _err("TIMEOUT" if timeout else "COMFY_EXECUTION", str(e), True) from e
    except ComfyConnectionError as e:
        raise _err("COMFY_UNAVAILABLE", str(e), True) from e
    timings["render_s"] = round(time.monotonic() - t0, 2)
    digest = hashlib.sha256(mp4.read_bytes()).hexdigest()

    # 4. Saída: quadros de amostra e uploads por URL assinada
    t0 = time.monotonic()
    frame_urls = inp.get("frame_upload_urls") or []
    try:
        frames = media.sample_frames(mp4, len(frame_urls), workdir / "quadros")
        if inp.get("output_upload_url"):
            media.upload(inp["output_upload_url"], mp4, "video/mp4")
        for url, frame in zip(frame_urls, frames, strict=True):
            media.upload(url, frame, "image/jpeg")
    except media.MediaError as e:
        raise _err("UPLOAD_FAILED", str(e), e.retryable) from e
    timings["upload_s"] = round(time.monotonic() - t0, 2)
    timings["total_s"] = round(time.monotonic() - t_total, 2)

    if ctx.comfyui_version is None:
        try:
            ctx.comfyui_version = ctx.comfy.system_stats()["system"]["comfyui_version"]
        except Exception:  # noqa: BLE001
            ctx.comfyui_version = "?"
    return {
        "status": "ok",
        "job_id": job_id,
        "compiled_prompt": prompt_en,
        "seed": effective["seed"],
        "params": {
            k: v for k, v in effective.items() if k not in ("prompt_en", "image", "filename_prefix")
        },
        "timings": timings,
        "versions": {
            "image": ctx.image_version,
            "comfyui": ctx.comfyui_version,
            "template": tpl.ref,
            "compiler": compiler_ref,
            "presets": presets.load()["version"] if comp_in.get("presets") else None,
        },
        "output": {"bytes": size, "sha256": digest, "uploaded": bool(inp.get("output_upload_url"))},
        "frames": len(frames),
        "worker": _worker_info(ctx),
    }
