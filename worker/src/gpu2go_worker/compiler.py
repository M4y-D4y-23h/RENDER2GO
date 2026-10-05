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

from gpu2go_worker import WORKER_DIR

PROMPTS_DIR = Path(os.environ.get("GPU2GO_PROMPTS_DIR", WORKER_DIR / "prompts"))
DEFAULT_MODEL = "huihui_ai/qwen2.5-abliterate:7b"

PROFILES: dict[str, dict] = {
    "i2v": {
        "version": 3,
        "file": "compiler-i2v.v3.txt",
        "options": {"temperature": 0.3, "num_ctx": 4096},
        "words": (40, 90),
    },
}


class CompilerError(Exception):
    pass


class CompilerRefused(CompilerError):
    pass


@dataclass(frozen=True)
class CompileResult:
    prompt: str
    profile: str
    version: int
    model: str
    seconds: float
    word_count: int
    word_range: tuple[int, int]

    @property
    def word_count_ok(self) -> bool:
        low, high = self.word_range
        return low <= self.word_count <= high

    @property
    def ref(self) -> str:
        return f"{self.profile}@{self.version}"


def system_prompt(profile: str) -> str:
    try:
        spec = PROFILES[profile]
    except KeyError:
        raise CompilerError(f"perfil desconhecido: {profile!r}") from None
    return (PROMPTS_DIR / spec["file"]).read_text().strip()


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
) -> CompileResult:
    if not text_pt.strip():
        raise CompilerError("pedido vazio")
    spec = PROFILES.get(profile)
    if spec is None:
        raise CompilerError(f"perfil desconhecido: {profile!r}")
    payload = {
        "model": model,
        "system": system_prompt(profile),
        "prompt": text_pt.strip(),
        "stream": False,
        "options": spec["options"],
        "keep_alive": _keep_alive(keep_alive),
    }
    t0 = time.monotonic()
    try:
        r = requests.post(f"{ollama_url.rstrip('/')}/api/generate", json=payload, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        raise CompilerError(f"Ollama inacessível ou com erro em {ollama_url}: {e}") from e
    raw = r.json().get("response", "")
    seconds = time.monotonic() - t0
    if is_refusal(raw):
        raise CompilerRefused("o compilador recusou o pedido (REFUSED)")
    prompt = normalize(raw)
    if not prompt:
        raise CompilerError("o compilador devolveu texto vazio")
    return CompileResult(
        prompt=prompt,
        profile=profile,
        version=spec["version"],
        model=model,
        seconds=seconds,
        word_count=len(prompt.split()),
        word_range=spec["words"],
    )
