#!/usr/bin/env python3
"""Deriva worker/workflows/i2v_r1v2.v1.api.json do export original do ComfyUI.

O export (referencia/wan-i2v-com-ollama.api.json) não é editado. Este script:
- remove os nós do Ollama (165, 166): o worker compila o prompt antes e o injeta
  como texto literal em 129:93;
- zera os campos preenchidos por job (imagem, prompt, seed);
- troca os valores de teste do export pelos padrões da especificação
  (5 s em vez de 10 s, 640x640 em vez de 360x360) e um prefixo de saída neutro.

Uso: python tools/derive_i2v_template.py [--check]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "referencia" / "wan-i2v-com-ollama.api.json"
TARGET = ROOT / "worker" / "workflows" / "i2v_r1v2.v1.api.json"

OLLAMA_NODES = ("165", "166")
DEFAULTS = {
    ("97", "image"): "",
    ("129:93", "text"): "",
    ("129:86", "noise_seed"): 0,
    ("129:161", "value"): 5.0,
    ("129:98", "width"): 640,
    ("129:98", "height"): 640,
    ("108", "filename_prefix"): "video/render2go",
}


def derive(source: dict) -> dict:
    wf = json.loads(json.dumps(source))
    for node_id in OLLAMA_NODES:
        wf.pop(node_id)
    for (node_id, key), value in DEFAULTS.items():
        if key not in wf[node_id]["inputs"]:
            raise SystemExit(f"export inesperado: {node_id}.{key} não existe")
        wf[node_id]["inputs"][key] = value
    return wf


def render(wf: dict) -> str:
    return json.dumps(wf, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--check", action="store_true", help="falha se o arquivo estiver desatualizado")
    a = p.parse_args()
    expected = render(derive(json.loads(SOURCE.read_text())))
    if a.check:
        if not TARGET.exists() or TARGET.read_text() != expected:
            print(f"{TARGET.relative_to(ROOT)} está desatualizado; rode o script sem --check")
            return 1
        print("template em dia")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(expected)
    print(f"escrito {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
