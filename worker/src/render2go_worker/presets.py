"""Presets de cena: o usuário escreve só a ação e escolhe ritmo, câmera, luz etc. em botões.

A entrada do compilador vira o formato rotulado da especificação (seção 9.3):

    ação: ela caminha devagar pela praia
    câmera: "static camera", "full body shot", "rear view, from behind"

Os termos em inglês dos presets **não** dependem do LLM: no teste de fidelidade, o 7B
parafraseava os termos entre aspas mesmo com instrução explícita. Por isso o LLM só vê as
linhas rotuladas para não contradizê-las, descreve a ação, e o worker anexa os termos ao
final do prompt, palavra por palavra (`Composed.suffix`).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from render2go_worker import WORKER_DIR

PRESETS_FILE = Path(os.environ.get("RENDER2GO_PRESETS", WORKER_DIR / "presets" / "presets.v4.json"))
MAX_TEXT_CHARS = 600


class PresetError(Exception):
    pass


@dataclass(frozen=True)
class Composed:
    text: str
    """Entrada do compilador (pt, com as linhas rotuladas)."""
    suffix: str
    """Termos em inglês dos presets, anexados depois do texto compilado ("" se nenhum)."""
    mode: str
    selections: dict[str, str]
    version: int

    @property
    def camera(self) -> bool:
        """Há preset de câmera (a câmera não deve vir do LLM)."""
        return "camera" in self.selections


@lru_cache(maxsize=4)
def load(path: str | Path = PRESETS_FILE) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    for mode, spec in data["modes"].items():
        unknown = [c for c in spec["categories"] if c not in data["categories"]]
        if unknown:
            raise PresetError(f"modo {mode!r} cita categorias inexistentes: {unknown}")
    for cat, spec in data["categories"].items():
        for opt_id, opt in spec["options"].items():
            if not opt.get("en") or any('"' in term for term in opt["en"]):
                raise PresetError(f"{cat}.{opt_id}: termos vazios ou com aspas")
    return data


def clean_text(text: str) -> str:
    """Uma linha só: impede que o texto do usuário crie linhas rotuladas falsas."""
    return re.sub(r"\s+", " ", text).strip()


def compose(
    mode: str,
    text: str,
    selections: dict[str, str] | None = None,
    data: dict[str, Any] | None = None,
) -> Composed:
    data = data or load()
    spec = data["modes"].get(mode)
    if spec is None:
        raise PresetError(f"modo desconhecido: {mode!r}")
    text = clean_text(text)
    if not text:
        raise PresetError("o texto da cena/ação não pode ser vazio")
    if len(text) > MAX_TEXT_CHARS:
        raise PresetError(f"texto com mais de {MAX_TEXT_CHARS} caracteres")
    selections = dict(selections or {})
    unknown = sorted(set(selections) - set(spec["categories"]))
    if unknown:
        raise PresetError(f"categorias desconhecidas para {mode}: {unknown}")

    lines = [f"{spec['text_label']}: {text}"]
    for cat, opt in _opcoes(spec, selections, data):
        terms = ", ".join(f'"{t}"' for t in opt["en"])
        lines.append(f"{data['categories'][cat].get('line', cat)}: {terms}")
    return Composed(
        text="\n".join(lines),
        suffix=suffix(mode, selections, data),
        mode=mode,
        selections=selections,
        version=int(data["version"]),
    )


def _opcoes(spec: dict[str, Any], selections: dict[str, str], data: dict[str, Any]):
    """(categoria, opção) escolhidas, na ordem das categorias do modo."""
    for cat in spec["categories"]:
        opt_id = selections.get(cat)
        if opt_id is None:
            continue
        opt = data["categories"][cat]["options"].get(opt_id)
        if opt is None:
            raise PresetError(f"opção desconhecida: {cat}={opt_id}")
        yield cat, opt


def suffix(mode: str, selections: dict[str, str], data: dict[str, Any] | None = None) -> str:
    """Só os termos em inglês dos presets, como o código os anexa ao prompt ("" se nenhum). O
    movimento padrão (render2go_worker.traducao) usa direto, sem texto do cliente."""
    data = data or load()
    spec = data["modes"].get(mode)
    if spec is None:
        raise PresetError(f"modo desconhecido: {mode!r}")
    unknown = sorted(set(selections) - set(spec["categories"]))
    if unknown:
        raise PresetError(f"categorias desconhecidas para {mode}: {unknown}")
    frases = []
    for _, opt in _opcoes(spec, selections, data):
        sentence = ", ".join(opt["en"])
        frases.append(sentence[0].upper() + sentence[1:] + ".")
    return " ".join(frases)


def parse_selections(items: list[str]) -> dict[str, str]:
    """`["camera=parada", "luz=neon"]` → `{"camera": "parada", "luz": "neon"}`."""
    out: dict[str, str] = {}
    for item in items:
        cat, sep, opt = item.partition("=")
        if not sep or not cat.strip() or not opt.strip():
            raise PresetError(f"preset inválido {item!r}: use categoria=opção")
        if cat.strip() in out:
            raise PresetError(f"categoria repetida: {cat.strip()}")
        out[cat.strip()] = opt.strip()
    return out


def catalog(mode: str, data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Lista para a interface: categorias do modo com ids e rótulos em pt-BR."""
    data = data or load()
    spec = data["modes"].get(mode)
    if spec is None:
        raise PresetError(f"modo desconhecido: {mode!r}")
    return [
        {
            "id": cat,
            "label": data["categories"][cat]["label"],
            "options": [
                {"id": opt_id, "label": opt["label"]}
                for opt_id, opt in data["categories"][cat]["options"].items()
            ],
        }
        for cat in spec["categories"]
    ]
