import json
import subprocess
import sys

import pytest
from conftest import T2V_TEMPLATES, WORKER

from gpu2go_worker import models
from gpu2go_worker import template as t


def test_generated_templates_are_in_sync():
    r = subprocess.run(
        [sys.executable, str(WORKER.parent / "tools" / "build_t2v_templates.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("path", T2V_TEMPLATES, ids=lambda p: p.name.split(".")[0])
def test_t2v_candidates(path):
    tpl = t.load(path)
    assert tpl.contract["experimental"] is True and "image" not in tpl.contract["params"]
    wf, eff = tpl.apply({"prompt_en": "a cat sleeps", "seed": 9, "width": 832, "height": 480})
    assert eff["duration_s"] == 5
    classes = {n["class_type"] for n in wf.values()}
    assert "LoadImage" not in classes and not {"OllamaGenerateV2"} & classes
    sampler = wf["129:86"]["inputs"]
    assert sampler["noise_seed"] == 9 and wf["129:93"]["inputs"]["text"] == "a cat sleeps"
    # todo modelo citado no template existe no conjunto do lock
    sets, entries = models.load_lock()
    files = {
        e.dest.rsplit("/", 1)[1] for e in models.resolve([tpl.contract["model_set"]], sets, entries)
    }
    cited = {
        v
        for n in wf.values()
        for k, v in n["inputs"].items()
        if k in ("unet_name", "lora_name", "clip_name", "vae_name")
    }
    assert cited <= files, cited - files


def test_r1v2_noimg_differs_from_i2v_only_by_the_image():
    i2v = json.loads((WORKER / "workflows" / "i2v_r1v2.v1.api.json").read_text())
    noimg = json.loads(
        (WORKER / "workflows" / "experimental" / "t2v_r1v2_noimg.v1.api.json").read_text()
    )
    del i2v["97"]
    del i2v["129:98"]["inputs"]["start_image"]
    assert noimg == i2v


def test_a14b_lightning_settings():
    tpl = t.load(WORKER / "workflows" / "experimental" / "t2v_a14b_q6k_lightning.v1.api.json")
    wf = tpl.workflow
    hi, lo = wf["129:86"]["inputs"], wf["129:85"]["inputs"]
    assert (hi["steps"], hi["cfg"], hi["start_at_step"], hi["end_at_step"]) == (4, 1.0, 0, 2)
    assert (lo["steps"], lo["start_at_step"], lo["end_at_step"]) == (4, 2, 4)
    assert wf["129:104"]["inputs"]["model"] == ["129:201", 0]
    assert wf["129:95"]["class_type"] == "UnetLoaderGGUF"
    wf["129:201"]["inputs"]["strength_model"] = 0.5
    with pytest.raises(t.TemplateError, match="valor fixo"):
        t.validate_structure(wf, tpl.contract)


def test_promoted_t2v_matches_its_candidate():
    prod = t.load(WORKER / "workflows" / "t2v_a14b_fp8_lightning.v1.api.json")
    cand = t.load(WORKER / "workflows" / "experimental" / "t2v_a14b_fp8_lightning.v1.api.json")
    assert prod.workflow == cand.workflow
    assert prod.contract["experimental"] is False and prod.contract["adr"].endswith(
        "0001-modelo-t2v.md"
    )
    assert prod.contract["fixed"] == cand.contract["fixed"]
