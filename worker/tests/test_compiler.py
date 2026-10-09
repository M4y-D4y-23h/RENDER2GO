import pytest

from render2go_worker import compiler as c


def test_compile_sends_versioned_system_prompt(fake_ollama):
    r = c.compile_prompt("ela caminha em direção à câmera", ollama_url=fake_ollama.url)
    req = fake_ollama.requests[0]
    assert req["system"] == c.system_prompt("i2v")
    assert "REFUSED" in req["system"]  # a salvaguarda continua no prompt
    assert req["options"] == {"temperature": 0.3, "num_ctx": 4096}
    assert req["keep_alive"] == 0 and req["stream"] is False
    assert r.ref == "i2v@6" and r.word_count > 10
    assert r.word_range == (15, 60) and r.word_count_ok


@pytest.mark.parametrize("out", ["REFUSED", "  refused.", '"REFUSED"', "REFUSED\n"])
def test_refusal_detected(fake_ollama, out):
    fake_ollama.response = out
    with pytest.raises(c.CompilerRefused):
        c.compile_prompt("pedido", ollama_url=fake_ollama.url)


def test_keep_alive_duration_passthrough(fake_ollama):
    c.compile_prompt("pedido", ollama_url=fake_ollama.url, keep_alive="10m")
    assert fake_ollama.requests[0]["keep_alive"] == "10m"


def test_output_normalized(fake_ollama):
    fake_ollama.response = '  "A man\n  walks   slowly."  '
    assert c.compile_prompt("x", ollama_url=fake_ollama.url).prompt == "A man walks slowly."


def test_empty_and_unreachable():
    with pytest.raises(c.CompilerError, match="vazio"):
        c.compile_prompt("  ")
    with pytest.raises(c.CompilerError, match="inacessível"):
        c.compile_prompt("x", ollama_url="http://127.0.0.1:9", timeout=2)


def test_versions_and_t2v_profile(fake_ollama):
    assert c.prompt_path("i2v", 3).name == "compiler-i2v.v3.txt"
    for profile in c.PROFILES:
        text = c.system_prompt(profile)
        assert "REFUSED" in text and "static camera" in text  # regra e glossário em todos
    r = c.compile_prompt("um gato dorme", profile="t2v", ollama_url=fake_ollama.url, seed=3)
    assert r.ref == f"t2v@{c.PROFILES['t2v']['version']}" and r.word_range == (50, 110)
    assert fake_ollama.requests[0]["options"]["seed"] == 3
    with pytest.raises(c.CompilerError, match="inexistente"):
        c.compile_prompt("x", ollama_url=fake_ollama.url, version=99)


def test_suffix_is_appended_verbatim_and_not_counted(fake_ollama):
    fake_ollama.response = "He waves slowly."
    r = c.compile_prompt(
        "x", ollama_url=fake_ollama.url, suffix='Static camera, "rear view, from behind".'
    )
    assert r.prompt == 'He waves slowly. Static camera, "rear view, from behind".'
    assert r.compiled == "He waves slowly." and r.word_count == 3


def test_non_english_output_is_retried_with_next_seed(fake_ollama):
    fake_ollama.queue = ["She walks, 她的步伐自然。"]
    r = c.compile_prompt("x", ollama_url=fake_ollama.url, seed=10)
    assert r.attempts == 2 and r.prompt == fake_ollama.response
    assert [q["options"]["seed"] for q in fake_ollama.requests] == [10, 11]


def test_non_english_output_fails_after_attempts(fake_ollama):
    fake_ollama.response = "Она идёт."
    with pytest.raises(c.CompilerOutputError, match="fora do inglês"):
        c.compile_prompt("x", ollama_url=fake_ollama.url, attempts=2)
    assert len(fake_ollama.requests) == 2


def test_static_camera_sentences_are_dropped_only_with_camera_preset():
    from render2go_worker import compiler as c

    texto = (
        "A slow dolly-in towards the mug. Steam rises gently. Static camera. "
        "The camera remains still while light shifts."
    )
    assert c.drop_static_camera(texto) == "A slow dolly-in towards the mug. Steam rises gently."
    assert c.drop_static_camera("Steam rises.") == "Steam rises."


def test_compile_with_camera_preset_drops_llm_static_camera(fake_ollama):
    from render2go_worker import compiler as c

    fake_ollama.response = "The product turns slowly on the table. Static camera."
    r = c.compile_prompt(
        "ação: gira", ollama_url=fake_ollama.url, suffix="Slow orbit.", camera_preset=True
    )
    assert r.prompt == "The product turns slowly on the table. Slow orbit."
    sem = c.compile_prompt("ação: gira", ollama_url=fake_ollama.url, suffix="Slow orbit.")
    assert "Static camera." in sem.prompt


def test_camera_motion_written_by_the_llm_wins_over_its_own_static_camera(fake_ollama):
    # rodada da v6 (09/10/2026): "The camera slowly pushes in." e "Static camera." na mesma saída
    d = c.drop_contradictory_static_camera
    assert d("The car stays in place. The camera slowly pushes in. Static camera.") == (
        "The car stays in place. The camera slowly pushes in."
    )
    assert d("The camera gently orbits the car. The camera remains still.") == (
        "The camera gently orbits the car."
    )
    # quem se move é a pessoa, não a câmera: o "Static camera." fica
    texto = "She walks away from the camera, moving slowly along the beach. Static camera."
    assert d(texto) == texto
    assert d("Steam rises. The camera stays still. Static camera.") == (
        "Steam rises. The camera stays still. Static camera."
    )
    fake_ollama.response = "The bag rotates. The camera slowly pushes in. Static camera."
    assert c.compile_prompt("ação: gira", ollama_url=fake_ollama.url).prompt == (
        "The bag rotates. The camera slowly pushes in."
    )
