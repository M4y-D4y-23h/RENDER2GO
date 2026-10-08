import json
import subprocess
import sys

import pytest
from conftest import I2V_TEMPLATE, WORKER

from render2go_worker import template as t
from render2go_worker.models import load_lock

BASE = {"image": "entrada.png", "prompt_en": "A woman walks toward the camera.", "seed": 42}


@pytest.fixture(scope="module")
def tpl():
    return t.load(I2V_TEMPLATE)


def test_template_matches_derivation_from_reference_export():
    root = WORKER.parent
    r = subprocess.run(
        [sys.executable, str(root / "tools" / "derive_i2v_template.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_ollama_nodes_removed_and_prompt_is_literal(tpl):
    classes = {n["class_type"] for n in tpl.workflow.values()}
    assert not classes & {"OllamaGenerateV2", "OllamaConnectivityV2"}
    assert tpl.workflow["129:93"]["inputs"]["text"] == ""
    assert len(tpl.workflow) == 30


def test_apply_fills_params_and_defaults(tpl):
    wf, p = tpl.apply(BASE)
    assert wf["97"]["inputs"]["image"] == "entrada.png"
    assert wf["129:93"]["inputs"]["text"] == BASE["prompt_en"]
    assert wf["129:86"]["inputs"]["noise_seed"] == 42
    assert (wf["129:98"]["inputs"]["width"], wf["129:98"]["inputs"]["height"]) == (640, 640)
    assert wf["129:161"]["inputs"]["value"] == 5.0
    assert isinstance(wf["129:161"]["inputs"]["value"], float)
    assert p["duration_s"] == 5
    assert tpl.workflow["97"]["inputs"]["image"] == ""  # o template não é alterado


@pytest.mark.parametrize("d,frames", [(3, 49), (4, 65), (5, 81), (6, 97), (7, 113)])
def test_durations_give_4k_plus_1_frames(d, frames):
    assert t.frames_for(d) == frames
    assert (frames - 1) % 4 == 0


@pytest.mark.parametrize(
    "extra,msg",
    [
        ({"width": 650}, "múltiplo de 16"),
        ({"width": 1280, "height": 1280}, "acima do limite"),
        ({"duration_s": 10}, "acima do máximo"),
        ({"duration_s": 2}, "abaixo do mínimo"),
        ({"duration_s": 5.5}, "esperado inteiro"),
        ({"seed": -1}, "abaixo do mínimo"),
        ({"prompt_en": "   "}, "não pode ser vazio"),
        ({"steps": 20}, "desconhecidos"),
    ],
)
def test_invalid_params_rejected(tpl, extra, msg):
    with pytest.raises(t.TemplateError, match=msg):
        tpl.apply({**BASE, **extra})


def test_missing_required_param(tpl):
    with pytest.raises(t.TemplateError, match="obrigatório"):
        tpl.apply({"prompt_en": "x", "seed": 1})


def test_720p_landscape_allowed(tpl):
    wf, _ = tpl.apply({**BASE, "width": 1280, "height": 720, "duration_s": 7})
    assert wf["129:98"]["inputs"]["width"] == 1280


def test_fixed_values_protect_turbo_and_models(tpl):
    wf = json.loads(json.dumps(tpl.workflow))
    wf["129:118"]["inputs"]["value"] = 20
    with pytest.raises(t.TemplateError, match="valor fixo alterado: 129:118.value"):
        t.validate_structure(wf, tpl.contract)


def test_forbidden_class_and_dangling_link(tpl):
    wf = json.loads(json.dumps(tpl.workflow))
    wf["999"] = {"class_type": "LoraLoaderModelOnly", "inputs": {}}
    with pytest.raises(t.TemplateError, match="proibida"):
        t.validate_structure(wf, tpl.contract)
    wf = json.loads(json.dumps(tpl.workflow))
    wf["129:93"]["inputs"]["text"] = ["165", 0]
    with pytest.raises(t.TemplateError, match="inexistente"):
        t.validate_structure(wf, tpl.contract)


def test_model_files_in_template_exist_in_models_lock(tpl):
    _, models = load_lock()
    dests = {m.dest.split("/", 1)[1] for m in models.values()}
    for ref, value in tpl.contract["fixed"].items():
        if ref.endswith((".unet_name", ".clip_name", ".vae_name")):
            assert value in dests, ref


def test_validate_against_object_info():
    wf = {
        "1": {"class_type": "ComfyMathExpression", "inputs": {"expression": "a", "values.a": 1}},
        "2": {
            "class_type": "SaveVideo",
            "inputs": {"format": "auto", "format.codec": "auto", "bogus": 1},
        },
        "3": {"class_type": "NoSuchNode", "inputs": {}},
    }
    info = {
        "ComfyMathExpression": {"input": {"required": {"expression": [], "values": []}}},
        "SaveVideo": {"input": {"required": {"format": []}, "optional": {}}},
    }
    problems = t.validate_against_object_info(wf, info)
    assert any("NoSuchNode" in p for p in problems)
    assert any("'bogus'" in p for p in problems)
    assert len(problems) == 2
