"""ComfyClient.wait com um prazo só (14/10/2026): o WebSocket que cai perto do fim não recomeça a
espera inteira no polling. Relógio simulado; nenhuma rede."""

import pytest

from render2go_worker import comfy_client as cc


class Relogio:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def dormir(self, s):
        self.t += s


@pytest.fixture
def relogio(monkeypatch):
    r = Relogio()
    monkeypatch.setattr(cc.time, "monotonic", r)
    monkeypatch.setattr(cc.time, "sleep", r.dormir)
    return r


class _Vazio:
    def raise_for_status(self):
        pass

    def json(self):
        return {}  # o prompt ainda não está no /history


def test_a_websocket_that_drops_near_the_end_keeps_the_same_deadline(relogio, monkeypatch):
    c = cc.ComfyClient("http://127.0.0.1:1")

    def ws_cai(pid, cid, prazo, on_progress):
        relogio.t += 95  # a conexão caiu aos 95 s de 100
        raise OSError("conexão fechada")

    tempos = []

    def get(url, timeout):
        tempos.append(timeout)
        relogio.t += 0.01
        return _Vazio()

    monkeypatch.setattr(c, "_wait_ws", ws_cai)
    monkeypatch.setattr(c.session, "get", get)
    with pytest.raises(cc.ComfyExecutionError, match="não terminou em 100 s"):
        c.wait("p", "c", timeout=100, poll_interval=1)
    # antes: o polling recomeçava com os 100 s inteiros (~195 s no total)
    assert relogio.t <= 101.5
    assert max(tempos) <= 5.1  # cada chamada HTTP também ficou dentro do que restava


def test_the_last_look_comes_before_the_error(relogio, monkeypatch):
    """No limite, uma última consulta ao /history antes de desistir: o vídeo pode ter terminado."""
    c = cc.ComfyClient("http://127.0.0.1:1")

    def ws_cai(pid, cid, prazo, on_progress):
        relogio.t = prazo + 0.5
        raise OSError("timeout do recv")

    class Pronto:
        def raise_for_status(self):
            pass

        def json(self):
            return {"p": {"status": {"completed": True}, "outputs": {}}}

    monkeypatch.setattr(c, "_wait_ws", ws_cai)
    monkeypatch.setattr(c.session, "get", lambda url, timeout: Pronto())
    assert c.wait("p", "c", timeout=10)["outputs"] == {}


def test_polling_only_respects_the_deadline(relogio, monkeypatch):
    c = cc.ComfyClient("http://127.0.0.1:1")
    monkeypatch.setattr(c.session, "get", lambda url, timeout: _Vazio())
    with pytest.raises(cc.ComfyExecutionError):
        c.wait("p", "c", timeout=30, use_ws=False, poll_interval=2)
    assert 30 <= relogio.t <= 32
