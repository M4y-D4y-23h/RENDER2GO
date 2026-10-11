import hashlib
import json

import pytest

from render2go_worker import models as m

# Tamanhos exatos da seção 2 da especificação (referencia/especificacao-pipeline-manual.md)
SPEC_BYTES = {
    "Wan2.2_I2V_High_R1_n54w_v2.safetensors": 14_803_879_958,
    "Wan2.2_I2V_Low_R1_n54w_v2.safetensors": 14_803_879_958,
    "nsfw_wan_umt5_xxl_v2_fp8_e4m3fn_scaled.safetensors": 6_741_537_445,
    "wan_2.1_vae.safetensors": 253_815_318,
}
# fora da especificação: o filtro de imagem nos quadros (ADR 0005), fixado em 11/10/2026
FILTRO_BYTES = {"model.safetensors": 172_725_672, "config.json": 989}


def test_lock_matches_spec_sizes_and_is_pinned():
    sets, models = m.load_lock()
    entries = m.resolve(["i2v_r1v2"], sets, models)
    assert {e.dest.rsplit("/", 1)[1]: e.bytes for e in entries} == SPEC_BYTES | FILTRO_BYTES
    for e in entries:
        assert len(e.revision) == 40, "revisão do HF deve ser um commit fixo"
        assert len(e.sha256) == 64
        assert e.revision in e.url


@pytest.fixture
def tiny_lock(tmp_path):
    data = b"pesos de teste" * 100
    lock = {
        "sets": {"mini": ["a"]},
        "models": {
            "a": {
                "repo": "org/repo",
                "revision": "0" * 40,
                "path": "a.safetensors",
                "dest": "vae/a.safetensors",
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        },
    }
    p = tmp_path / "lock.json"
    p.write_text(json.dumps(lock))
    src = tmp_path / "volume"
    (src / "vae").mkdir(parents=True)
    (src / "vae" / "a.safetensors").write_bytes(data)
    return p, src, data


def test_copy_from_volume_and_verify(tmp_path, tiny_lock):
    lock, src, data = tiny_lock
    dest = tmp_path / "models"
    assert (
        m.main(["mini", "--lock", str(lock), "--models-dir", str(dest), "--from-dir", str(src)])
        == 0
    )
    assert (dest / "vae" / "a.safetensors").read_bytes() == data
    # segunda vez: já está ok, não copia de novo
    assert m.main(["mini", "--lock", str(lock), "--models-dir", str(dest), "--check-only"]) == 0


def test_truncated_copy_is_detected(tmp_path, tiny_lock):
    lock, src, data = tiny_lock
    (src / "vae" / "a.safetensors").write_bytes(data[:-10])  # como o cp truncado da seção 8
    dest = tmp_path / "models"
    assert (
        m.main(["mini", "--lock", str(lock), "--models-dir", str(dest), "--from-dir", str(src)])
        == 1
    )


def test_wrong_sha_is_detected(tmp_path, tiny_lock):
    lock, src, data = tiny_lock
    corrupt = bytearray(data)
    corrupt[0] ^= 0xFF
    (src / "vae" / "a.safetensors").write_bytes(bytes(corrupt))
    dest = tmp_path / "models"
    args = ["mini", "--lock", str(lock), "--models-dir", str(dest), "--from-dir", str(src)]
    assert m.main(args) == 1
    assert m.main([*args, "--no-sha"]) == 0  # só bytes: passa (por isso o padrão confere sha)


def test_unknown_selection(tiny_lock):
    lock, _, _ = tiny_lock
    assert m.main(["nada", "--lock", str(lock), "--models-dir", "/tmp/x", "--check-only"]) == 1
