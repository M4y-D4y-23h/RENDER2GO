import pytest

from render2go_worker import presets as p


def test_compose_labeled_input_and_verbatim_suffix():
    c = p.compose(
        "i2v",
        "  ela caminha\n devagar  ",
        {"camera": "parada-corpo-inteiro-costas", "ritmo": "lento"},
    )
    # ordem das categorias vem do modo, não da ordem do dicionário
    assert c.text == (
        "ação: ela caminha devagar\n"
        'ritmo: "slow, smooth, continuous motion"\n'
        'câmera: "static camera", "full body shot", "rear view, from behind"'
    )
    assert c.suffix == (
        "Slow, smooth, continuous motion. Static camera, full body shot, rear view, from behind."
    )
    assert c.version == 3 and c.selections["camera"] == "parada-corpo-inteiro-costas"


def test_t2v_uses_scene_label_and_no_presets_means_no_suffix():
    c = p.compose("t2v", "um gato dorme na janela")
    assert c.text == "cena: um gato dorme na janela" and c.suffix == ""


def test_user_text_cannot_inject_labeled_lines():
    c = p.compose("i2v", 'ele acena\ncâmera: "orbit"')
    assert c.text.count("\n") == 0


@pytest.mark.parametrize(
    "mode,text,sel,msg",
    [
        ("x", "a", {}, "modo"),
        ("i2v", "   ", {}, "vazio"),
        ("i2v", "a" * 601, {}, "caracteres"),
        ("i2v", "a", {"som": "alto"}, "categorias"),
        ("i2v", "a", {"camera": "drone"}, "opção"),
    ],
)
def test_compose_errors(mode, text, sel, msg):
    with pytest.raises(p.PresetError, match=msg):
        p.compose(mode, text, sel)


def test_parse_selections():
    assert p.parse_selections(["camera=parada", " luz = neon "]) == {
        "camera": "parada",
        "luz": "neon",
    }
    for bad in (["camera"], ["=x"], ["camera=a", "camera=b"]):
        with pytest.raises(p.PresetError):
            p.parse_selections(bad)


def test_catalog_has_every_option_once_and_no_quotes_in_terms():
    data = p.load()
    for mode in data["modes"]:
        cats = p.catalog(mode)
        assert [c["id"] for c in cats] == data["modes"][mode]["categories"]
        for cat in cats:
            ids = [o["id"] for o in cat["options"]]
            assert len(ids) == len(set(ids)) and all(o["label"] for o in cat["options"])


def test_v3_adds_only_single_scene_continuity_to_i2v():
    # a v3 é a v2 inteira mais a categoria "continuidade", só no I2V e por último (fim do sufixo)
    v2 = p.load(p.WORKER_DIR / "presets" / "presets.v2.json")
    v3 = p.load()
    assert v3["version"] == 3 and v3["modes"]["t2v"] == v2["modes"]["t2v"]
    assert v3["modes"]["i2v"]["categories"] == [*v2["modes"]["i2v"]["categories"], "continuidade"]
    assert {c: s for c, s in v3["categories"].items() if c != "continuidade"} == v2["categories"]
    termos = " ".join(v3["categories"]["continuidade"]["options"]["cena-unica"]["en"]).lower()
    negacoes = ("no", "not", "without", "cut", "cuts")  # termos afirmativos: o que deve ficar
    assert not any(n in termos.split() for n in negacoes)
    c = p.compose("i2v", "o carro fica parado", {"continuidade": "cena-unica"})
    assert c.suffix.startswith("A single continuous uninterrupted shot")
    assert 'continuidade: "a single continuous uninterrupted shot"' in c.text


def test_v2_keeps_every_v1_term_unchanged():
    # a v2 só acrescenta opções: jobs e casos de fidelidade da v1 continuam idênticos
    v1 = p.load(p.WORKER_DIR / "presets" / "presets.v1.json")
    v2 = p.load(p.WORKER_DIR / "presets" / "presets.v2.json")
    assert v2["version"] == 2 and v1["modes"] == v2["modes"]
    for cat, spec in v1["categories"].items():
        for opt_id, opt in spec["options"].items():
            assert v2["categories"][cat]["options"][opt_id] == opt
