import hashlib
import json
import shutil
import subprocess

import pytest
from conftest import FAKE_MP4, I2V_TEMPLATE, T2V_TEMPLATES

from gpu2go_worker import cli_submit


def _run(capsys, *args):
    code = cli_submit.main(["--template", str(I2V_TEMPLATE), "--no-ws", *args])
    return code, json.loads(capsys.readouterr().out)


def test_end_to_end_with_compiler(fake_comfy, fake_ollama, sfw_image, tmp_path, capsys):
    out = tmp_path / "video.mp4"
    code, res = _run(
        capsys,
        "--comfy",
        fake_comfy.url,
        "--ollama",
        fake_ollama.url,
        "--image",
        str(sfw_image),
        "--text-pt",
        "ela caminha, câmera parada",
        "--seed",
        "7",
        "--duration",
        "5",
        "--out",
        str(out),
    )
    assert code == 0, res
    assert out.read_bytes() == FAKE_MP4
    assert res["output"]["sha256"] == hashlib.sha256(FAKE_MP4).hexdigest()
    assert res["compiler"] == "i2v@5" and res["template"] == "i2v_r1v2@1"
    assert set(res["timings"]) >= {"compile_s", "upload_s", "render_s", "total_s"}

    wf = next(iter(fake_comfy.prompts.values()))
    image_name = wf["97"]["inputs"]["image"]
    assert fake_comfy.uploads[image_name] == sfw_image.read_bytes()
    assert wf["129:93"]["inputs"]["text"] == fake_ollama.response
    assert wf["129:86"]["inputs"]["noise_seed"] == 7
    assert not {"165", "166"} & set(wf)


def test_prompt_en_skips_compiler(fake_comfy, sfw_image, tmp_path, capsys):
    code, res = _run(
        capsys,
        "--comfy",
        fake_comfy.url,
        "--ollama",
        "http://127.0.0.1:9",
        "--image",
        str(sfw_image),
        "--prompt-en",
        "A cat sleeps.",
        "--out",
        str(tmp_path / "v.mp4"),
    )
    assert code == 0 and "compiler" not in res
    assert 0 <= res["params"]["seed"] <= 2**53 - 1


def test_refused_exit_code_and_nothing_queued(fake_comfy, fake_ollama, sfw_image, tmp_path, capsys):
    fake_ollama.response = "REFUSED"
    code, res = _run(
        capsys,
        "--comfy",
        fake_comfy.url,
        "--ollama",
        fake_ollama.url,
        "--image",
        str(sfw_image),
        "--text-pt",
        "x",
        "--out",
        str(tmp_path / "v.mp4"),
    )
    assert code == cli_submit.EXIT_REFUSED and res["status"] == "refused"
    assert fake_comfy.prompts == {} and fake_comfy.uploads == {}


def test_invalid_param_fails_before_any_work(fake_comfy, fake_ollama, sfw_image, tmp_path, capsys):
    code, res = _run(
        capsys,
        "--comfy",
        fake_comfy.url,
        "--ollama",
        fake_ollama.url,
        "--image",
        str(sfw_image),
        "--text-pt",
        "x",
        "--duration",
        "10",
        "--out",
        str(tmp_path / "v.mp4"),
    )
    assert code == cli_submit.EXIT_PARAM and "máximo" in res["error"]
    assert fake_ollama.requests == [] and fake_comfy.uploads == {}


def test_missing_image(fake_comfy, tmp_path, capsys):
    code, _ = _run(
        capsys, "--comfy", fake_comfy.url, "--prompt-en", "x", "--out", str(tmp_path / "v.mp4")
    )
    assert code == cli_submit.EXIT_PARAM


def test_comfy_validation_and_execution_errors(fake_comfy, sfw_image, tmp_path, capsys):
    args = (
        "--comfy",
        fake_comfy.url,
        "--image",
        str(sfw_image),
        "--prompt-en",
        "x",
        "--out",
        str(tmp_path / "v.mp4"),
    )
    fake_comfy.fail_validation = True
    code, res = _run(capsys, *args)
    assert code == cli_submit.EXIT_COMFY and "129:95" in json.dumps(res["details"])

    fake_comfy.fail_validation = False
    fake_comfy.fail_execution = True
    code, res = _run(capsys, *args)
    assert code == cli_submit.EXIT_COMFY and "out of memory" in res["error"]


def test_unreachable_comfy(sfw_image, tmp_path, capsys):
    code, _ = _run(
        capsys,
        "--comfy",
        "http://127.0.0.1:9",
        "--image",
        str(sfw_image),
        "--prompt-en",
        "x",
        "--out",
        str(tmp_path / "v.mp4"),
    )
    assert code == cli_submit.EXIT_UNREACHABLE


def test_presets_compose_input_and_append_terms(
    fake_comfy, fake_ollama, sfw_image, tmp_path, capsys
):
    code, res = _run(
        capsys,
        "--comfy",
        fake_comfy.url,
        "--ollama",
        fake_ollama.url,
        "--image",
        str(sfw_image),
        "--text-pt",
        "ela caminha pela areia",
        "--preset",
        "camera=parada-corpo-inteiro-costas",
        "--preset",
        "luz=por-do-sol",
        "--out",
        str(tmp_path / "v.mp4"),
    )
    assert code == 0, res
    assert fake_ollama.requests[0]["prompt"].startswith("ação: ela caminha pela areia\n")
    text = next(iter(fake_comfy.prompts.values()))["129:93"]["inputs"]["text"]
    assert text.endswith(
        "Static camera, full body shot, rear view, from behind. "
        "Golden hour sunlight, long soft shadows."
    )
    assert res["presets"] == {
        "version": 2,
        "camera": "parada-corpo-inteiro-costas",
        "luz": "por-do-sol",
    }


def test_preset_errors(fake_comfy, sfw_image, tmp_path, capsys):
    base = ["--comfy", fake_comfy.url, "--image", str(sfw_image), "--out", str(tmp_path / "v.mp4")]
    code, res = _run(capsys, *base, "--prompt-en", "x", "--preset", "camera=parada")
    assert code == 2 and "exige --text-pt" in res["error"]
    code, res = _run(capsys, *base, "--text-pt", "x", "--preset", "camera=drone")
    assert code == 2 and "opção desconhecida" in res["error"]
    assert not fake_comfy.prompts


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")
def test_continue_from_uses_last_frame(fake_comfy, tmp_path, capsys):
    clip = tmp_path / "anterior.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=16",
            "-frames:v",
            "17",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
    )
    code, res = _run(
        capsys,
        "--comfy",
        fake_comfy.url,
        "--continue-from",
        str(clip),
        "--prompt-en",
        "The camera keeps still.",
        "--out",
        str(tmp_path / "v.mp4"),
    )
    assert code == 0, res
    assert res["continued_from"] == str(clip)
    (uploaded,) = fake_comfy.uploads.values()
    assert uploaded.startswith(b"\x89PNG")


def test_t2v_template_without_image(fake_comfy, fake_ollama, sfw_image, tmp_path, capsys):
    tpl = str(T2V_TEMPLATES[0])
    args = [
        "--comfy",
        fake_comfy.url,
        "--ollama",
        fake_ollama.url,
        "--out",
        str(tmp_path / "v.mp4"),
    ]
    code = cli_submit.main(
        ["--template", tpl, "--no-ws", *args, "--image", str(sfw_image), "--prompt-en", "x"]
    )
    assert code == 2 and "não usa imagem" in json.loads(capsys.readouterr().out)["error"]
    code = cli_submit.main(["--template", tpl, "--no-ws", *args, "--text-pt", "um gato dorme"])
    res = json.loads(capsys.readouterr().out)
    assert code == 0, res
    assert res["compiler"] == "t2v@3" and not fake_comfy.uploads
