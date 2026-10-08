"""Compilador de prompts: pedido em português → um parágrafo em inglês para o Wan.

Os system prompts são arquivos versionados em `worker/prompts/` e vão a cada chamada no
campo `system` da API do Ollama (não é preciso `ollama create`).

Atenção: o modelo é abliterado. A saída `REFUSED` é tratada como bloqueio, mas o
compilador **não** é camada de segurança (docs/03, seção 5).
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from render2go_worker import WORKER_DIR

PROMPTS_DIR = Path(os.environ.get("RENDER2GO_PROMPTS_DIR", WORKER_DIR / "prompts"))
DEFAULT_MODEL = "huihui_ai/qwen2.5-abliterate:7b"

# `version` é a versão em produção; o arquivo é `compiler-<perfil>.v<versão>.txt`.
# Versões anteriores continuam no diretório para comparação (tools/eval_compiler.py).
# Escolha das versões em produção: worker/evals/compiler/RESULTADOS.md.
PROFILES: dict[str, dict] = {
    "i2v": {
        "version": 5,
        "options": {"temperature": 0.3, "num_ctx": 4096},
        "words": (40, 90),
        # faixa própria de cada versão do prompt (o padrão é "words")
        "words_by_version": {5: (15, 60)},
    },
    "t2v": {
        "version": 3,
        "options": {"temperature": 0.3, "num_ctx": 4096},
        "words": (80, 130),
        "words_by_version": {3: (50, 110)},
    },
}


class CompilerError(Exception):
    pass


class CompilerRefused(CompilerError):
    pass


class CompilerOutputError(CompilerError):
    """Saída fora do formato (vazia ou com texto fora do inglês) depois das tentativas."""


# O Qwen às vezes muda para chinês no meio do parágrafo (visto no teste de fidelidade).
NON_ENGLISH = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\u0400-\u04ff]")
# O 7B escreve "Static camera." mesmo com preset de movimento (rodada de 06/10/2026), o que
# contradiz os termos do preset. Com preset de câmera, essas frases saem pelo código.
STATIC_CAMERA = re.compile(
    r"static camera|still camera|camera (?:remains|stays|is|holds) "
    r"(?:still|static|stationary|steady|fixed)",
    re.IGNORECASE,
)


def drop_static_camera(text: str) -> str:
    """Remove as frases que dizem que a câmera fica parada."""
    frases = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(f for f in frases if not STATIC_CAMERA.search(f)).strip()


@dataclass(frozen=True)
class CompileResult:
    prompt: str
    """Prompt final: texto compilado + termos dos presets."""
    profile: str
    version: int
    model: str
    seconds: float
    word_count: int
    """Palavras do texto compilado pelo LLM (sem os termos dos presets)."""
    word_range: tuple[int, int]
    compiled: str = ""
    attempts: int = 1

    @property
    def word_count_ok(self) -> bool:
        low, high = self.word_range
        return low <= self.word_count <= high

    @property
    def ref(self) -> str:
        return f"{self.profile}@{self.version}"


def prompt_path(profile: str, version: int | None = None) -> Path:
    try:
        spec = PROFILES[profile]
    except KeyError:
        raise CompilerError(f"perfil desconhecido: {profile!r}") from None
    return PROMPTS_DIR / f"compiler-{profile}.v{version or spec['version']}.txt"


def word_range(profile: str, version: int | None = None) -> tuple[int, int]:
    spec = PROFILES[profile]
    return spec.get("words_by_version", {}).get(version or spec["version"], spec["words"])


def system_prompt(profile: str, version: int | None = None) -> str:
    path = prompt_path(profile, version)
    if not path.is_file():
        raise CompilerError(f"prompt inexistente: {path.name}")
    return path.read_text().strip()


def _keep_alive(value: str | int) -> str | int:
    if isinstance(value, int):
        return value
    return int(value) if value.isdigit() else value


def normalize(text: str) -> str:
    text = text.strip().strip('"').strip()
    return re.sub(r"\s+", " ", text)


def is_refusal(text: str) -> bool:
    return bool(re.match(r"^\W*REFUSED\b", text.strip(), flags=re.IGNORECASE))


def compile_prompt(
    text_pt: str,
    profile: str = "i2v",
    ollama_url: str = "http://127.0.0.1:11434",
    model: str = DEFAULT_MODEL,
    keep_alive: str | int = 0,
    timeout: float = 180.0,
    version: int | None = None,
    seed: int | None = None,
    suffix: str = "",
    attempts: int = 2,
    camera_preset: bool = False,
) -> CompileResult:
    """Compila o pedido; `suffix` (termos dos presets) é anexado sem passar pelo LLM.

    Saída vazia ou com texto fora do inglês é refeita (seed + 1) até `attempts` vezes.
    `camera_preset`: há preset de câmera, então as frases de "câmera parada" escritas pelo
    LLM são removidas (contradiriam o preset).
    """
    if not text_pt.strip():
        raise CompilerError("pedido vazio")
    spec = PROFILES.get(profile)
    if spec is None:
        raise CompilerError(f"perfil desconhecido: {profile!r}")
    version = version or spec["version"]
    options = dict(spec["options"])
    if seed is not None:
        options["seed"] = seed
    payload = {
        "model": model,
        "system": system_prompt(profile, version),
        "prompt": text_pt.strip(),
        "stream": False,
        "options": options,
        "keep_alive": _keep_alive(keep_alive),
    }
    t0 = time.monotonic()
    problem = ""
    for attempt in range(1, attempts + 1):  # noqa: B007 (usado depois do laço)
        try:
            r = requests.post(
                f"{ollama_url.rstrip('/')}/api/generate", json=payload, timeout=timeout
            )
            r.raise_for_status()
        except requests.RequestException as e:
            raise CompilerError(f"Ollama inacessível ou com erro em {ollama_url}: {e}") from e
        raw = r.json().get("response", "")
        if is_refusal(raw):
            raise CompilerRefused("o compilador recusou o pedido (REFUSED)")
        compiled = normalize(raw)
        if camera_preset:  # a câmera vem do preset, palavra por palavra
            compiled = drop_static_camera(compiled)
        if not compiled:
            problem = "o compilador devolveu texto vazio"
        elif NON_ENGLISH.search(compiled):
            problem = "o compilador devolveu texto fora do inglês"
        else:
            break
        if "seed" in options:
            options["seed"] += 1
    else:
        raise CompilerOutputError(f"{problem} ({attempts} tentativas)")
    seconds = time.monotonic() - t0
    prompt = f"{compiled} {suffix.strip()}".strip()
    return CompileResult(
        prompt=prompt,
        profile=profile,
        version=version,
        model=model,
        seconds=seconds,
        word_count=len(compiled.split()),
        word_range=word_range(profile, version),
        compiled=compiled,
        attempts=attempt,
    )
