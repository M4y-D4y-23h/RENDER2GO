#!/usr/bin/env python3
"""Gera os templates T2V candidatos do bake-off (docs/adr/0001) a partir do template I2V.

Saída em worker/workflows/experimental/ (fora da validação da imagem slim, que só olha
worker/workflows/*.api.json). O candidato escolhido por ADR aceito (PRODUCTION) também
é gravado em worker/workflows/. Os três candidatos usam o mesmo encoder, VAE, duração e
saída do I2V de produção:

- t2v_r1v2_noimg: o próprio R1 v2 sem imagem de entrada. Sem `start_image`, o
  WanImageToVideo não manda imagem de condicionamento e o modelo recebe máscara 1 em
  todos os quadros (comfy/model_base.py, concat_cond), ou seja, gera tudo do zero. Uma
  imagem "em branco" de verdade fixaria o 1º quadro nessa imagem.
- t2v_a14b_q6k_lightning: Wan 2.2 T2V A14B oficial em GGUF Q6_K (nó UnetLoaderGGUF do
  ComfyUI-GGUF) + LoRAs Lightning T2V v1.1 (força 1,0), 4 passos, troca no passo 2,
  CFG 1, shift 5: os valores do workflow de referência do lightx2v.
- t2v_a14b_fp8_lightning: o mesmo com os pesos fp8 da Comfy-Org (UNETLoader nativo).

Uso: python tools/build_t2v_templates.py [--check]
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / "worker" / "workflows"
SOURCE = WORKFLOWS / "i2v_r1v2.v1.api.json"
SOURCE_CONTRACT = WORKFLOWS / "i2v_r1v2.v1.contract.json"
OUT = WORKFLOWS / "experimental"
# Candidatos promovidos a produção por ADR aceito: também gravados em worker/workflows/,
# com "experimental": false (e então validados pela imagem slim).
PRODUCTION = {"t2v_a14b_fp8_lightning": "docs/adr/0001-modelo-t2v.md"}

LORAS = {
    "high": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
    "low": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
}
A14B = {
    "q6k": {
        "loader": "UnetLoaderGGUF",
        "high": "Wan2.2-T2V-A14B-HighNoise-Q6_K.gguf",
        "low": "Wan2.2-T2V-A14B-LowNoise-Q6_K.gguf",
        "model_set": "t2v_a14b_q6k",
        "extra_nodes": ["ComfyUI-GGUF"],
    },
    "fp8": {
        "loader": "UNETLoader",
        "high": "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
        "low": "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
        "model_set": "t2v_a14b_fp8",
        "extra_nodes": [],
    },
}


def _base_contract(src: dict, name: str, comment: str) -> dict:
    c = copy.deepcopy(src)
    c["_comment"] = comment
    c["template"] = name
    c["version"] = 1
    c["experimental"] = True
    del c["params"]["image"]
    return c


def r1v2_noimg(wf: dict, contract: dict) -> tuple[dict, dict]:
    wf = copy.deepcopy(wf)
    del wf["97"]
    del wf["129:98"]["inputs"]["start_image"]
    c = _base_contract(
        contract,
        "t2v_r1v2_noimg",
        "Candidato T2V do bake-off: R1 v2 sem imagem (gerado por tools/build_t2v_templates.py).",
    )
    c["model_set"] = "i2v_r1v2"
    c["extra_custom_nodes"] = []
    return wf, c


def a14b_lightning(wf: dict, contract: dict, variant: str) -> tuple[dict, dict]:
    spec = A14B[variant]
    src = copy.deepcopy(wf)
    keep = [
        "108",
        "129:84",
        "129:89",
        "129:90",
        "129:93",
        "129:86",
        "129:85",
        "129:87",
        "129:94",
        "129:104",
        "129:103",
        "129:161",
        "129:162",
        "129:163",
    ]
    out = {k: src[k] for k in keep}
    out["129:95"] = {"class_type": spec["loader"], "inputs": {"unet_name": spec["high"]}}
    out["129:96"] = {"class_type": spec["loader"], "inputs": {"unet_name": spec["low"]}}
    if spec["loader"] == "UNETLoader":
        for n in ("129:95", "129:96"):
            out[n]["inputs"]["weight_dtype"] = "default"
    for node_id, stage, unet in (("129:201", "high", "129:95"), ("129:202", "low", "129:96")):
        out[node_id] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"lora_name": LORAS[stage], "strength_model": 1.0, "model": [unet, 0]},
        }
    out["129:104"]["inputs"]["model"] = ["129:201", 0]
    out["129:103"]["inputs"]["model"] = ["129:202", 0]
    out["129:98"] = {
        "class_type": "EmptyHunyuanLatentVideo",
        "inputs": {"width": 640, "height": 640, "length": ["129:163", 1], "batch_size": 1},
    }
    for node_id, start, end in (("129:86", 0, 2), ("129:85", 2, 4)):
        inputs = out[node_id]["inputs"]
        inputs.update(steps=4, cfg=1.0, start_at_step=start, end_at_step=end)
        inputs["positive"] = ["129:93", 0]
        inputs["negative"] = ["129:89", 0]
    out["129:86"]["inputs"]["latent_image"] = ["129:98", 0]

    name = f"t2v_a14b_{variant}_lightning"
    c = _base_contract(
        contract,
        name,
        f"Candidato T2V do bake-off: Wan 2.2 T2V A14B {variant} + Lightning T2V v1.1 "
        "(gerado por tools/build_t2v_templates.py).",
    )
    c["model_set"] = spec["model_set"]
    c["extra_custom_nodes"] = spec["extra_nodes"]
    fixed = {k: v for k, v in c["fixed"].items() if not k.startswith(("129:95.", "129:96."))}
    for k in ("129:131.value", "129:118.value", "129:122.value", "129:124.value"):
        fixed.pop(k, None)
    fixed.update(
        {
            "129:95.unet_name": spec["high"],
            "129:96.unet_name": spec["low"],
            "129:201.lora_name": LORAS["high"],
            "129:202.lora_name": LORAS["low"],
            "129:201.strength_model": 1.0,
            "129:202.strength_model": 1.0,
            "129:86.steps": 4,
            "129:85.steps": 4,
            "129:86.cfg": 1.0,
            "129:85.cfg": 1.0,
            "129:86.end_at_step": 2,
            "129:85.start_at_step": 2,
            "129:104.shift": wf["129:104"]["inputs"]["shift"],
            "129:103.shift": wf["129:103"]["inputs"]["shift"],
        }
    )
    c["fixed"] = fixed
    c["forbidden_classes"] = [x for x in c["forbidden_classes"] if x != "LoraLoaderModelOnly"]
    return out, c


def build() -> dict[str, str]:
    wf = json.loads(SOURCE.read_text())
    contract = json.loads(SOURCE_CONTRACT.read_text())
    files: dict[str, str] = {}
    for workflow, c in (
        r1v2_noimg(wf, contract),
        a14b_lightning(wf, contract, "q6k"),
        a14b_lightning(wf, contract, "fp8"),
    ):
        stem = f"{c['template']}.v{c['version']}"
        wf_text = json.dumps(workflow, indent=2, ensure_ascii=False) + "\n"
        files[f"experimental/{stem}.api.json"] = wf_text
        files[f"experimental/{stem}.contract.json"] = (
            json.dumps(c, indent=2, ensure_ascii=False) + "\n"
        )
        if c["template"] in PRODUCTION:
            prod = {**c, "experimental": False, "adr": PRODUCTION[c["template"]]}
            prod["_comment"] = c["_comment"].replace("Candidato T2V do bake-off", "T2V de produção")
            files[f"{stem}.api.json"] = wf_text
            files[f"{stem}.contract.json"] = json.dumps(prod, indent=2, ensure_ascii=False) + "\n"
    return files


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--check", action="store_true", help="falha se algum arquivo estiver desatualizado"
    )
    a = p.parse_args()
    files = build()
    stale = [
        n
        for n, text in files.items()
        if not (WORKFLOWS / n).exists() or (WORKFLOWS / n).read_text() != text
    ]
    if a.check:
        for n in stale:
            print(
                f"{(WORKFLOWS / n).relative_to(ROOT)} está desatualizado; rode o script sem --check"
            )
        if not stale:
            print("templates T2V em dia")
        return 1 if stale else 0
    OUT.mkdir(parents=True, exist_ok=True)  # worker/workflows/experimental
    for n, text in files.items():
        (WORKFLOWS / n).write_text(text)
        print(f"escrito {(WORKFLOWS / n).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
