import pytest

from gpu2go_worker import compiler as c


def test_compile_sends_versioned_system_prompt(fake_ollama):
    r = c.compile_prompt("ela caminha em direção à câmera", ollama_url=fake_ollama.url)
    req = fake_ollama.requests[0]
    assert req["system"] == c.system_prompt("i2v")
    assert "REFUSED" in req["system"]  # a salvaguarda continua no prompt
    assert req["options"] == {"temperature": 0.3, "num_ctx": 4096}
    assert req["keep_alive"] == 0 and req["stream"] is False
    assert r.ref == "i2v@3" and r.word_count > 10
    assert not r.word_count_ok  # resposta falsa tem menos de 40 palavras


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
