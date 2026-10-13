"""Compilador de prompts: pedido em português → um parágrafo em inglês para o Wan.

Os system prompts são arquivos versionados em `worker/prompts/` e vão a cada chamada no
campo `system` da API do Ollama (não é preciso `ollama create`).

Atenção: o modelo é abliterado. A saída `REFUSED` é tratada como bloqueio, mas o
compilador **não** é camada de segurança (docs/03, seção 5).

Pedido vago (I2V, 13/10/2026): quem decide é o código (render2go_worker.traducao.pedido_vago), que
troca o texto pelo movimento padrão, fixo e em inglês. A partir do v7, o compilador também pode
responder `VAGUE`; aqui, isso vira `VAGO_TEXTO` com o sufixo dos presets.
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
        "version": 6,
        "options": {"temperature": 0.3, "num_ctx": 4096},
        "words": (40, 90),
        # faixa própria de cada versão do prompt (o padrão é "words")
        "words_by_version": {5: (15, 60), 6: (15, 60), 7: (15, 60), 8: (15, 60)},
        # 1ª versão que entende a linha "pessoas: nenhuma" (foto sem pessoas;
        # render2go_worker.traducao)
        "linha_pessoas": 7,
        # 1ª versão que entende as linhas "foto" e "categoria" (legenda por foto, ADR 0014)
        "linha_legenda": 8,
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


# Câmera em movimento descrita pelo LLM. Na rodada da v6 (09/10/2026), o 7B escreveu "The camera
# slowly pushes in." e, na mesma resposta, "Static camera.": com movimento, a frase parada sai.
# Só com a câmera como sujeito ("the camera slowly pushes in"): "walks toward the camera, moving
# slowly" é a pessoa que se move.
CAMERA_MOVING = re.compile(
    r"\bcamera\s+(?:\w+ly\s+)?(?:push|pull|dolly|dollies|track|orbit|circl|rotat|pan|tilt|zoom|"
    r"rise|move|moving|approach|follow|glide|sweep|crane)",
    re.IGNORECASE,
)


def drop_static_camera(text: str) -> str:
    """Remove as frases que dizem que a câmera fica parada."""
    frases = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(f for f in frases if not STATIC_CAMERA.search(f)).strip()


def drop_contradictory_static_camera(text: str) -> str:
    """Com uma frase de câmera em movimento, remove as de câmera parada (contradição do LLM)."""
    frases = re.split(r"(?<=[.!?])\s+", text.strip())
    if not any(CAMERA_MOVING.search(f) and not STATIC_CAMERA.search(f) for f in frases):
        return text.strip()
    return drop_static_camera(text)


# Movimento de quem chama sem o padrão da categoria (cli_submit, jobs.py): o mesmo texto do padrão
# "geral" das receitas v4 (render2go_worker.traducao usa o da categoria da foto)
VAGO_TEXTO = (
    "{assunto} stays in place as soft light slowly glides across it, "
    "highlighting its shape and details."
)
ASSUNTO_PADRAO = "the main subject"


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
    vague: bool = False
    """O compilador respondeu VAGUE: `compiled` é `VAGO_TEXTO` (render2go_worker.traducao troca pelo
    movimento padrão da categoria da foto)."""

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


def is_vague(text: str) -> bool:
    """Pedido sem movimento concreto (v7 em diante): a resposta começa com VAGUE, em maiúsculas."""
    return bool(re.match(r"^\W*VAGUE\b", text.strip()))


def fill_subject(text: str, subject: str = "") -> str:
    """Põe o assunto no lugar de {assunto} (o padrão do texto fixo ou, com a legenda por foto, o da
    foto), com maiúscula no começo de cada frase."""
    out = text.replace("{assunto}", subject or ASSUNTO_PADRAO)
    return re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)


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
        if profile == "i2v" and is_vague(raw):
            vago = fill_subject(VAGO_TEXTO)
            return CompileResult(
                prompt=f"{vago} {suffix.strip()}".strip(),
                profile=profile,
                version=version,
                model=model,
                seconds=time.monotonic() - t0,
                word_count=len(vago.split()),
                word_range=word_range(profile, version),
                compiled=vago,
                attempts=attempt,
                vague=True,
            )
        compiled = normalize(raw)
        if camera_preset:  # a câmera vem do preset, palavra por palavra
            compiled = drop_static_camera(compiled)
        else:  # movimento pedido (ou o padrão do pedido leigo) vence o "Static camera." do fim
            compiled = drop_contradictory_static_camera(compiled)
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
