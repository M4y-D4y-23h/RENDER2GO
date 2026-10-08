import shutil
import subprocess
from fractions import Fraction

import pytest
from conftest import WORKER

from render2go_worker import acabamento as ac
from render2go_worker import video

PADRAO = WORKER.parent / "fixtures" / "sfw" / "padrao-teste-640.png"
com_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")


def _info(w=480, h=832, dur=5.0625):
    return video.VideoInfo("h264", w, h, "yuv420p", Fraction(16), 81, dur)


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def _clip(path, size="240x416", frames=33):
    # padrão de teste sintético (SFW), 16 fps
    _ffmpeg("-f", "lavfi", "-i", f"testsrc2=size={size}:rate=16", "-frames:v", frames,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", path)  # fmt: skip
    return path


def test_command_layout_without_running_ffmpeg(tmp_path):
    logo, cartao, musica = tmp_path / "logo.png", tmp_path / "cartao.png", tmp_path / "m.mp3"
    for f in (logo, cartao, musica):
        f.write_bytes(b"x")
    clips = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    o = ac.Opcoes(logo=logo, cartao=cartao, cartao_s=2, musica=musica, fps=24)
    cmd, total = ac.comando(clips, [_info(), _info()], tmp_path / "final.mp4", o)
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert total == 12.125
    assert "concat=n=2:v=1:a=0[base]" in fc and "fps=24" in fc
    assert "[2:v]scale=86:-1" in fc  # logo: 18% de 480, par
    assert "overlay=main_w-overlay_w-19:main_h-overlay_h-19" in fc
    assert "[3:v]" in fc and "[comlogo][cartao]concat" in fc  # cartão depois do logo
    assert "[4:a]atrim=0:12.125" in fc and "afade=t=out:st=11.125" in fc
    assert cmd[cmd.index("-map") + 1] == "[comcartao]" and "[audio]" in cmd
    assert cmd[cmd.index("-t", cmd.index("-filter_complex")) + 1] == "12.125"


@pytest.mark.parametrize(
    "kw,msg",
    [
        ({"logo_pos": "centro"}, "posição"),
        ({"logo_largura": 0.9}, "largura"),
        ({"cartao_s": 30}, "cartão"),
        ({"volume": 5}, "volume"),
        ({"fps": 120}, "fps"),
    ],
)
def test_invalid_options(tmp_path, kw, msg):
    with pytest.raises(ac.AcabamentoError, match=msg):
        ac.comando([tmp_path / "a.mp4"], [_info()], tmp_path / "f.mp4", ac.Opcoes(**kw))


def test_invalid_files(tmp_path):
    a = tmp_path / "a.mp4"
    with pytest.raises(ac.AcabamentoError, match="sobrescrever"):
        ac.comando([a], [_info()], a, ac.Opcoes())
    with pytest.raises(ac.AcabamentoError, match="inexistente"):
        ac.comando([a], [_info()], tmp_path / "f.mp4", ac.Opcoes(logo=tmp_path / "nao.png"))
    with pytest.raises(ac.AcabamentoError, match="música"):
        ac.comando([a], [_info()], tmp_path / "f.mp4", ac.Opcoes(musica=PADRAO))
    with pytest.raises(ac.AcabamentoError, match="no máximo"):
        ac.comando([a] * 21, [_info()] * 21, tmp_path / "f.mp4", ac.Opcoes())


@com_ffmpeg
def test_full_finish_with_real_ffmpeg(tmp_path):
    a = _clip(tmp_path / "a.mp4")
    b = _clip(tmp_path / "b.mp4", size="320x320")  # tamanho diferente: entra com faixas
    musica = tmp_path / "trilha.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=10", musica)
    o = ac.Opcoes(logo=PADRAO, cartao=PADRAO, cartao_s=1, musica=musica, fps=24)
    info = ac.acabar([a, b], tmp_path / "final.mp4", o)
    assert (info.width, info.height, info.fps) == (240, 416, Fraction(24))
    assert abs(info.duration_s - (33 / 16 * 2 + 1)) < 0.15
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
         tmp_path / "final.mp4"], capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    assert sorted(streams) == ["audio", "video"]


@com_ffmpeg
def test_cli_only_clips(tmp_path, capsys):
    a = _clip(tmp_path / "a.mp4")
    assert ac.main([str(a), str(a), "--saida", str(tmp_path / "f.mp4")]) == 0
    assert video.probe(tmp_path / "f.mp4").frames == 66
    assert ac.main([str(a), "--saida", str(tmp_path / "f.mov")]) == 2
    assert ".mp4" in capsys.readouterr().err


@com_ffmpeg
def test_interpolate_zoom_and_upscale(tmp_path):
    a = _clip(tmp_path / "a.mp4", size="240x416", frames=33)
    o = ac.Opcoes(interpolar=True, zoom=True, altura=832)
    info = ac.acabar([a], tmp_path / "f.mp4", o)
    assert (info.width, info.height, info.fps) == (480, 832, Fraction(32))
    assert abs(info.duration_s - 33 / 16) < 0.1
    assert info.frames >= 62  # ~2× os 33 quadros (o minterpolate não repete o último)


def test_zoom_and_scale_in_command(tmp_path):
    cmd, _ = ac.comando(
        [tmp_path / "a.mp4"], [_info()], tmp_path / "f.mp4", ac.Opcoes(zoom=True, altura=1280)
    )
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "zoompan=z='min(zoom+0.001000,1.08)'" in fc and "scale=-2:1280:flags=lanczos" in fc
    with pytest.raises(ac.AcabamentoError, match="altura"):
        ac.comando([tmp_path / "a.mp4"], [_info()], tmp_path / "f.mp4", ac.Opcoes(altura=1281))
