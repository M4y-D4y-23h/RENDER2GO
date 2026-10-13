#!/usr/bin/env python3
"""Gera os templates I2V candidatos (ADR 0014, proposto) a partir do template I2V de produção.

Saída em worker/workflows/experimental/ (fora da validação da imagem slim, que só olha
worker/workflows/*.api.json, e fora da produção), com o contrato de cada um. Só vão para
worker/workflows/ com o ADR aceito (PRODUCTION, como no tools/build_t2v_templates.py):

- i2v_oficial_lightx2v: o mesmo grafo do i2v_r1v2 (ComfyUI v0.38.0; 4 passos, CFG 1, troca de expert
  no passo 2, shift 5, euler/simple), com os arquivos oficiais do Wan 2.2 I2V A14B em fp8 com escala
  (Comfy-Org), o codificador umt5_xxl original (sem o vocabulário a mais do NSFW-UMT5) e o LoRA
  lightx2v de 4 passos v1 (força 1,0) em cada expert, entre o UNETLoader e o ModelSamplingSD3: os
  valores do workflow de referência do lightx2v para o I2V. O LoRA da Comfy-Org é o mesmo arquivo
  (mesmo SHA-256) do lightx2v/Wan2.2-Lightning, Seko-V1.

NAG (guia negativa com CFG 1) fica de fora: o nó nativo do ComfyUI v0.38.0 (NAGuidance) usa um
gancho que o Wan desta versão não chama, e o nó da comunidade troca o forward do Wan por uma cópia
de outra versão do ComfyUI (ADR 0014).

Uso: python tools/build_i2v_templates.py [--check]
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
ADR = "docs/adr/0014-modelos-oficiais-tradutor-e-legenda.md"
# Candidatos promovidos a produção por ADR aceito: também gravados em worker/workflows/, com
# "experimental": false (e então validados pela imagem slim). Vazio até o ADR 0014 ser aceito.
PRODUCTION: dict[str, str] = {}

OFICIAL = {
    "high": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
    "low": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
    "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
}
LORAS = {
    "high": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
    "low": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
}
LORA_FORCA = 1.0


def oficial_lightx2v(wf: dict, contract: dict) -> tuple[dict, dict]:
    out = copy.deepcopy(wf)
    out["129:95"]["inputs"]["unet_name"] = OFICIAL["high"]
    out["129:96"]["inputs"]["unet_name"] = OFICIAL["low"]
    out["129:84"]["inputs"]["clip_name"] = OFICIAL["clip"]
    # o LoRA de cada expert entra entre o UNETLoader e a chave do turbo (129:116/129:117), que leva
    # ao ModelSamplingSD3: os dois lados da chave são o mesmo modelo, agora com o LoRA
    for no, etapa, unet, chave in (("129:201", "high", "129:95", "129:116"),
                                   ("129:202", "low", "129:96", "129:117")):  # fmt: skip
        out[no] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"lora_name": LORAS[etapa], "strength_model": LORA_FORCA, "model": [unet, 0]},
        }
        for lado in ("on_false", "on_true"):
            if out[chave]["inputs"][lado] != [unet, 0]:
                raise SystemExit(f"template inesperado: {chave}.{lado} não vem de {unet}")
            out[chave]["inputs"][lado] = [no, 0]

    c = copy.deepcopy(contract)
    c["_comment"] = (
        "Candidato I2V do ADR 0014 (proposto): Wan 2.2 I2V A14B oficial em fp8 com escala, "
        "codificador umt5_xxl original e LoRA lightx2v de 4 passos (gerado por "
        "tools/build_i2v_templates.py; só vai para a produção com o ADR aceito)."
    )
    c["template"] = "i2v_oficial_lightx2v"
    c["version"] = 1
    c["experimental"] = True
    c["model_set"] = "i2v_oficial"
    c["extra_custom_nodes"] = []
    fixed = {
        k: v
        for k, v in c["fixed"].items()
        if not k.startswith(("129:95.", "129:96.", "129:84.clip"))
    }
    fixed.update(
        {
            "129:95.unet_name": OFICIAL["high"],
            "129:96.unet_name": OFICIAL["low"],
            "129:84.clip_name": OFICIAL["clip"],
            "129:201.lora_name": LORAS["high"],
            "129:202.lora_name": LORAS["low"],
            "129:201.strength_model": LORA_FORCA,
            "129:202.strength_model": LORA_FORCA,
            "129:103.shift": wf["129:103"]["inputs"]["shift"],
            "129:104.shift": wf["129:104"]["inputs"]["shift"],
        }
    )
    c["fixed"] = dict(sorted(fixed.items()))
    c["forbidden_classes"] = [x for x in c["forbidden_classes"] if x != "LoraLoaderModelOnly"]
    return out, c


def build() -> dict[str, str]:
    wf = json.loads(SOURCE.read_text())
    contract = json.loads(SOURCE_CONTRACT.read_text())
    files: dict[str, str] = {}
    for workflow, c in (oficial_lightx2v(wf, contract),):
        stem = f"{c['template']}.v{c['version']}"
        wf_text = json.dumps(workflow, indent=2, ensure_ascii=False) + "\n"
        files[f"experimental/{stem}.api.json"] = wf_text
        files[f"experimental/{stem}.contract.json"] = (
            json.dumps(c, indent=2, ensure_ascii=False) + "\n"
        )
        if c["template"] in PRODUCTION:
            prod = {**c, "experimental": False, "adr": PRODUCTION[c["template"]]}
            prod["_comment"] = c["_comment"].replace("Candidato I2V do ADR 0014", "I2V de produção")
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
            print("templates I2V candidatos em dia")
        return 1 if stale else 0
    OUT.mkdir(parents=True, exist_ok=True)
    for n, text in files.items():
        (WORKFLOWS / n).write_text(text)
        print(f"escrito {(WORKFLOWS / n).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
