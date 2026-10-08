import json

from render2go_worker import compiler, fidelity

CASE = {
    "id": "x",
    "profile": "i2v",
    "text": 'ela pega a "XÍCARA AZUL" e bebe, de costas',
    "must": [["from behind", "rear view"], ["drink", "sip"]],
    "forbid": ["side view", "man "],
    "order": [["pick", "take"], ["drink", "sip"]],
}
GOOD = (
    'She picks up the "XÍCARA AZUL" and slowly drinks from it, holding it with both hands '
    "and lowering it after a long sip, calm and unhurried until the end of the clip. "
    "Her shoulders stay relaxed and her elbows close to her body while the cup rises and "
    "falls in one smooth, continuous motion. "
    "Static camera, rear view, from behind."
)


def _check(out, refused=False, case=CASE):
    return fidelity.check(case, case["text"], out, refused, (10, 90))


def test_good_output_passes_every_check():
    checks, failures = _check(GOOD)
    assert failures == [] and all(checks.values())
    assert set(checks) == {"refusal", "must[0]", "must[1]", "forbid", "order", "quoted", "words"}


def test_each_failure_kind_is_reported():
    out = "She drinks, then picks up the blue cup. Side view. A man watches."
    checks, failures = _check(out)
    assert not checks["must[0]"] and not checks["forbid"] and not checks["order"]
    assert not checks["quoted"] and checks["words"]
    assert any("man " in f for f in failures)


def test_matching_rules():
    assert fidelity._find("A HIGH-ANGLE view", "high angle") >= 0  # hífen = espaço
    assert fidelity._find("she walks", "walk") >= 0  # início de palavra
    assert fidelity._find("a woman", "man ") == -1  # palavra inteira
    assert fidelity._find("the man.", "man ") >= 0
    assert fidelity._find("sidewalk", "walk") == -1


def test_refusal_semantics():
    checks, failures = _check("REFUSED", refused=True)
    assert failures == ["recusou sem motivo"]
    info = {"id": "r", "profile": "i2v", "text": "x", "expect_refused": True}
    assert _check("A man speaks.", case=info)[1] == ["não recusou (esperado REFUSED)"]
    assert _check("REFUSED", refused=True, case=info)[1] == []


def test_run_case_appends_presets_and_summarizes(fake_ollama):
    fake_ollama.response = GOOD.replace(" Static camera, rear view, from behind.", "")
    case = {**CASE, "presets": {"camera": "parada-corpo-inteiro-costas"}}
    r = fidelity.run_case(case, model="m", ollama_url=fake_ollama.url, version=None, seed=5)
    assert r.passed, r.failures
    assert r.output.endswith("Static camera, full body shot, rear view, from behind.")
    assert fake_ollama.requests[0]["options"]["seed"] == 5
    assert 'câmera: "static camera"' in fake_ollama.requests[0]["prompt"]
    s = fidelity.summarize([r], {"case_pass_rate": 0.85, "quoted": 1.0, "forbid": 0.95})
    assert s["passed"] and s["case_pass_rate"] == 1.0


def test_cases_file_is_valid_and_sfw_policy_is_explicit():
    data = fidelity.load_cases()
    assert data["thresholds"]["case_pass_rate"] >= 0.85
    profiles = {c["profile"] for c in data["cases"]}
    assert profiles == set(compiler.PROFILES)
    assert all(
        not c.get("gating", True) or c.get("must")
        for c in data["cases"]
        if not c.get("expect_refused")
    )


def test_eval_cli_writes_reports(fake_ollama, tmp_path):
    from render2go_worker import cli_eval

    fake_ollama.response = GOOD
    out = tmp_path / "rel"
    code = cli_eval.main(
        ["--ollama", fake_ollama.url, "--case", "i2v-numero", "--seeds", "1", "--out", str(out)]
    )
    assert code == 1  # a resposta falsa não fala em palmas: limite não atingido
    report = json.loads(out.with_suffix(".json").read_text())
    (group,) = report["groups"]
    assert group["profile"] == "i2v" and group["summary"]["runs"] == 1
    assert "i2v-numero" in out.with_suffix(".md").read_text()
    assert fake_ollama.requests[-1]["keep_alive"] == 0  # modelo descarregado no fim
