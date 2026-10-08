import json

from conftest import WORKER

from render2go_worker import experiment as ex
from render2go_worker import template as t

I2V = "workflows/i2v_r1v2.v1.api.json"


def test_overrides_change_fixed_values_only_in_experiments():
    tpl = t.load(WORKER / I2V)
    v = {"name": "8p", "template": I2V, "overrides": {"129:118.value": 8, "129:122.value": 1.5}}
    wf = ex.apply_variant(tpl, v, {"prompt_en": "x", "seed": 1, "image": "a.png"})
    assert wf["129:118"]["inputs"]["value"] == 8 and wf["129:122"]["inputs"]["value"] == 1.5
    bad = {"name": "b", "template": I2V, "overrides": {"129:999.value": 1}}
    try:
        ex.apply_variant(tpl, bad, {"prompt_en": "x", "seed": 1, "image": "a.png"})
    except ex.ExperimentError as e:
        assert "129:999" in str(e)
    else:
        raise AssertionError("override inválido aceito")


def test_shipped_plans_are_valid():
    for name in ("t2v-bakeoff.v1.json", "turbo-sweep.v1.json"):
        plan = ex.load_plan(WORKER / "evals" / name)
        assert plan["prompts"] and plan["seeds"]


def test_extra_model_gb():
    assert ex.extra_model_gb(t.load(WORKER / I2V)) == 0.0
    q6k = t.load(WORKER / "workflows/experimental/t2v_a14b_q6k_lightning.v1.api.json")
    assert ex.extra_model_gb(q6k) == 26.5  # 2 x 12,0 GB + 2 LoRAs de 1,23 GB


def test_run_resume_and_reports(fake_comfy, sfw_image, tmp_path, capsys):
    plan = {
        "name": "mini",
        "params": {"width": 320, "height": 320, "duration_s": 3},
        "seeds": [1, 2],
        "variants": [
            {"name": "base", "template": I2V},
            {"name": "8p", "template": I2V, "overrides": {"129:118.value": 8}},
        ],
        "prompts": [{"id": "p1", "en": "The person waves. Static camera."}],
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    out = tmp_path / "out"
    args = [str(path), "--comfy", fake_comfy.url, "--out", str(out), "--image", str(sfw_image)]
    assert ex.main(args) == 0
    lines = (out / "results.jsonl").read_text().splitlines()
    assert len(lines) == 4 and all(json.loads(x)["status"] == "ok" for x in lines)
    steps = sorted(w["129:118"]["inputs"]["value"] for w in fake_comfy.prompts.values())
    assert steps == [4, 4, 8, 8]
    summary = json.loads((out / "summary.json").read_text())
    assert summary["base"]["ok"] == 2 and summary["8p"]["extra_model_gb"] == 0.0
    page = (out / "index.html").read_text()
    assert "<video" in page and "8p</td>" in page  # nomes só na tabela de medições
    assert "8p" not in page.split("<h2>")[0]  # a grade é cega
    # retomada: nada é refeito
    assert ex.main(args) == 0 and len(fake_comfy.prompts) == 4

    # notas cegas → médias por variante
    key = json.loads((out / "blind_key.json").read_text())
    rows = (out / "ratings.csv").read_text().splitlines()
    assert len(rows) == 5 and rows[0].startswith("prompt_id,seed,codigo")
    filled = [rows[0]]
    for row in rows[1:]:
        pid, seed, code = row.split(",")[:3]
        note = "5" if key[f"{pid}|{seed}|{code}"] == "8p" else "3"
        filled.append(f"{pid},{seed},{code},{note},{note},{note},")
    sheet = tmp_path / "notas.csv"
    sheet.write_text("\n".join(filled) + "\n")
    assert ex.main([*args, "--score", str(sheet)]) == 0
    scores = json.loads((out / "scores.json").read_text())
    assert scores["8p"]["media_geral"] == 5.0 and scores["base"]["media_geral"] == 3.0
