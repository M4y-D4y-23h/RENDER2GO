import shutil
import subprocess

import pytest

from render2go_worker import video

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")


def _clip(path, color, size="160x120", frames=81, codec="libx264"):
    # padrão de teste sintético (SFW), 16 fps, 81 quadros = 5 s do Wan; com B-frames
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={size}:rate=16,format=yuv420p",
            "-vf",
            f"drawbox=c={color}:t=10",
            "-frames:v",
            str(frames),
            "-c:v",
            codec,
            str(path),
        ],
        check=True,
    )
    return path


def test_last_frame_is_exact_last_decoded_frame(tmp_path):
    clip = _clip(tmp_path / "a.mp4", "red")
    out = video.last_frame(clip, tmp_path / "q.png")
    ref = tmp_path / "ref.png"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(clip),
            "-vf",
            r"select=eq(n\,80)",
            "-frames:v",
            "1",
            str(ref),
        ],
        check=True,
    )
    assert out.read_bytes() == ref.read_bytes()


def test_concat_without_reencoding(tmp_path):
    clips = [_clip(tmp_path / f"{c}.mp4", c) for c in ("red", "blue", "green")]
    info = video.concat(clips, tmp_path / "final.mp4")
    assert info.frames == 243 and info.codec == "h264" and info.fps == 16


def test_concat_limits_and_compatibility(tmp_path):
    a = _clip(tmp_path / "a.mp4", "red", frames=17)
    with pytest.raises(video.VideoError, match="pelo menos 2"):
        video.concat([a], tmp_path / "x.mp4")
    with pytest.raises(video.VideoError, match="limite"):
        video.concat([a] * 5, tmp_path / "x.mp4")
    b = _clip(tmp_path / "b.mp4", "red", size="128x96", frames=17)
    with pytest.raises(video.VideoError, match="incompatível.*width"):
        video.concat([a, b], tmp_path / "x.mp4")


def test_missing_file(tmp_path):
    with pytest.raises(video.VideoError, match="inexistente"):
        video.probe(tmp_path / "nada.mp4")
