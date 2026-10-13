"""Template I2V candidato do ADR 0014 (proposto): gerado por tools/build_i2v_templates.py."""

import json
import subprocess
import sys

from conftest import WORKER

from render2go_worker import models
from render2go_worker import template as t

CANDIDATO = WORKER / "workflows" / "experimental" / "i2v_oficial_lightx2v.v1.api.json"
PRODUCAO = WORKER / "workflows" / "i2v_r1v2.v1.api.json"


def test_generated_i2v_candidates_are_in_sync():
    r = subprocess.run(
        [sys.executable, str(WORKER.parent / "tools" / "build_i2v_templates.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout


def test_the_official_candidate_only_swaps_the_files_and_adds_the_4_step_lora():
    tpl = t.load(CANDIDATO)
    c = tpl.contract
    assert (
        c["experimental"] is True and c["model_set"] == "i2v_oficial" and c["comfyui"] == "0.38.0"
    )
    assert (
        "LoraLoaderModelOnly" not in c["forbidden_classes"]
        and "LoraLoader" in c["forbidden_classes"]
    )
    # fora da produção até o ADR ser aceito
    assert not (WORKER / "workflows" / CANDIDATO.name).exists()
    wf, eff = tpl.apply(
        {"image": "f.png", "prompt_en": "A cup.", "seed": 3, "width": 832, "height": 480}
    )
    assert eff["duration_s"] == 5 and wf["129:86"]["inputs"]["noise_seed"] == 3
    prod = json.loads(PRODUCAO.read_text())
    cand = json.loads(CANDIDATO.read_text())
    mudou = {n for n in prod if prod[n] != cand[n]}
    assert set(cand) - set(prod) == {"129:201", "129:202"}
    assert mudou == {"129:84", "129:95", "129:96", "129:116", "129:117"}
    # a mesma amostragem da produção: turbo, 4 passos, CFG 1, troca de expert no passo 2, shift 5
    for no, valor in (("129:131", True), ("129:118", 4), ("129:122", 1), ("129:124", 2)):
        assert cand[no]["inputs"]["value"] == prod[no]["inputs"]["value"] == valor
    assert (
        cand["129:103"]["inputs"]["shift"]
        == cand["129:104"]["inputs"]["shift"]
        == 5.000000000000001
    )
    # cada expert com o seu LoRA, força 1,0, nos dois lados da chave do turbo
    for lora, unet, chave, etapa in (
        ("129:201", "129:95", "129:116", "high"),
        ("129:202", "129:96", "129:117", "low"),
    ):
        assert (
            cand[lora]["inputs"]["model"] == [unet, 0]
            and cand[lora]["inputs"]["strength_model"] == 1.0
        )
        assert (
            cand[lora]["inputs"]["lora_name"]
            == f"wan2.2_i2v_lightx2v_4steps_lora_v1_{etapa}_noise.safetensors"
        )
        assert cand[chave]["inputs"]["on_true"] == cand[chave]["inputs"]["on_false"] == [lora, 0]
    # todo arquivo citado no template está no conjunto do lock, que mantém o filtro de imagem e o
    # VAE
    sets, entries = models.load_lock()
    arquivos = {e.dest.rsplit("/", 1)[1] for e in models.resolve([c["model_set"]], sets, entries)}
    citados = {
        v
        for n in wf.values()
        for k, v in n["inputs"].items()
        if k in ("unet_name", "lora_name", "clip_name", "vae_name")
    }
    assert citados <= arquivos, citados - arquivos
    assert {"nsfw_freepik_modelo", "nsfw_freepik_config", "wan21_vae"} <= set(sets["i2v_oficial"])
    # nada do conjunto "Uncensored" no candidato
    assert not any("R1" in x or "nsfw_wan" in x for x in citados)


def test_the_official_set_is_pinned_and_keeps_the_current_one():
    sets, entries = models.load_lock()
    assert sets["i2v_r1v2"] == [
        "wan22_i2v_r1v2_high",
        "wan22_i2v_r1v2_low",
        "nsfw_umt5_xxl_v2_fp8",
        "wan21_vae",
        "nsfw_freepik_modelo",
        "nsfw_freepik_config",
    ]
    for nome in sets["i2v_oficial"]:
        e = entries[nome]
        assert len(e.revision) == 40 and len(e.sha256) == 64 and e.bytes > 0
        assert e.repo.startswith(("Comfy-Org/", "Freepik/"))
