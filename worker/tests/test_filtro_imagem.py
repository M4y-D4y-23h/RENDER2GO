"""Filtro de imagem nos quadros (ADR 0005), sem o classificador de verdade: a regra do projeto
proíbe conteúdo explícito em teste. O bloqueio é testado com notas falsas; a taxa de acerto vem do
cartão do modelo."""

import shutil
import subprocess

import pytest
from conftest import WORKER

from render2go_worker import filtro_imagem as fi

com_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")
FOTO = WORKER.parent / "fixtures" / "sfw" / "produto-cafe.jpg"


@pytest.fixture
def video(tmp_path):
    """3 s a 16 fps (49 quadros), como os vídeos de verdade."""
    v = tmp_path / "video.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-loop", "1", "-i", str(FOTO)]
        + ["-vf", "scale=832:480,fps=16", "-frames:v", "49", "-pix_fmt", "yuv420p", str(v)],
        check=True,
    )
    return v


class Notas:
    """Classificador falso: (P(medium), P(high)) de cada quadro; o resto vai para neutral."""

    def __init__(self, notas):
        self.notas = [n if isinstance(n, tuple) else (n / 2, n / 2) for n in notas]

    def probabilidades(self, quadros):
        assert all(len(q) == fi.LADO * fi.LADO * 3 for q in quadros)
        return [(1 - m - h, 0.0, m, h) for m, h in self.notas[: len(quadros)]]


@com_ffmpeg
def test_one_frame_in_four_including_the_first_and_the_last(video):
    qs = fi.quadros(video)
    assert [i for i, _ in qs] == list(range(0, 49, 4))  # 0, 4, ..., 48 (o último quadro)


@com_ffmpeg
def test_sustained_content_blocks_but_an_isolated_transition_frame_does_not(video):
    """12/10/2026 (o dono: "muitos vídeos bloqueados por motivos bestas"): até então, um quadro só
    com P(medium) + P(high) ≥ 0,35 barrava. Agora vale a média de 2 quadros seguidos (0,25 s) contra
    o 0,5 do cartão do modelo; o explícito (P(high)) continua barrando num quadro só."""
    r = fi.avaliar(video, Notas([0.01] * 13))
    assert not r.barrado and r.quadros == 13 and r.nota == pytest.approx(0.01)
    isolado = [(0.02, 0.0)] * 13
    isolado[7] = (0.6, 0.1)  # um quadro de transição, cercado de quadros limpos (antes: barrado)
    r = fi.avaliar(video, Notas(isolado))
    assert not r.barrado and r.nota_quadro == pytest.approx(0.7) and r.pior_quadro == 28
    assert r.nota == pytest.approx((0.7 + 0.02) / 2)
    sustentado = [(0.02, 0.0)] * 13
    sustentado[7] = sustentado[8] = (fi.LIMIAR, 0.0)  # 0,25 s seguidos no limiar do cartão
    assert fi.avaliar(video, Notas(sustentado)).barrado
    explicito = [(0.02, 0.0)] * 13
    explicito[3] = (0.0, fi.LIMIAR_ALTO)  # explícito num quadro só: barra
    r = fi.avaliar(video, Notas(explicito))
    assert r.barrado and r.nota_alta == pytest.approx(fi.LIMIAR_ALTO) and r.nota < fi.LIMIAR


def test_the_thresholds_come_from_the_env_only_within_limits(monkeypatch, capsys):
    assert fi.limiares() == (fi.LIMIAR, fi.LIMIAR_ALTO)
    monkeypatch.setenv("RENDER2GO_FILTRO_LIMIAR", "0,4")  # vírgula do .env em português
    monkeypatch.setenv("RENDER2GO_FILTRO_LIMIAR_ALTO", "0.3")
    assert fi.limiares() == (0.4, 0.3) and fi.barrado(0.4, 0.0) and fi.barrado(0.0, 0.3)
    monkeypatch.setenv("RENDER2GO_FILTRO_LIMIAR", "1")  # "desligar" pelo .env não vale: fica em 0,8
    monkeypatch.setenv("RENDER2GO_FILTRO_LIMIAR_ALTO", "x")
    assert fi.limiares() == (0.8, fi.LIMIAR_ALTO)
    assert "fora de" in capsys.readouterr().err
    # uma foto (um quadro só) vale como uma foto parada: a janela é ela mesma
    assert fi.notas([(0.5, 0.0, 0.3, 0.2)]) == (pytest.approx(0.5), pytest.approx(0.2))


@com_ffmpeg
def test_fail_closed_when_the_classifier_or_the_video_fails(video, tmp_path):
    class Quebrado:
        def probabilidades(self, quadros):
            raise RuntimeError("CUDA out of memory")

    with pytest.raises(fi.FiltroError, match="classificador falhou"):
        fi.avaliar(video, Quebrado())
    with pytest.raises(fi.FiltroError, match="sem resposta"):
        fi.avaliar(video, Notas([]))
    ruim = tmp_path / "ruim.mp4"
    ruim.write_bytes(b"nao e video")
    with pytest.raises(fi.FiltroError, match="não leu os quadros"):
        fi.avaliar(ruim, Notas([0.0] * 13))
    # sem os pesos (ou sem o timm/torch neste ambiente): não avalia, e nada é enviado
    with pytest.raises(fi.FiltroError):
        fi.Freepik(tmp_path / "nao-existe").probabilidades([b"\0" * (fi.LADO * fi.LADO * 3)])
