"""O projeto mudou de nome (GPU2GO → Render2Go): o prefixo antigo das variáveis continua valendo."""

import os
import subprocess
import sys

ANTIGO = "GPU" + "2GO_"
LER = (
    "import os, render2go_worker as w; "
    "print(os.environ['RENDER2GO_R2_BUCKET'], os.environ['RENDER2GO_SAFETY'])"
)


def test_old_prefix_variables_still_work_without_overriding_new_ones():
    env = {
        **os.environ,
        f"{ANTIGO}R2_BUCKET": "bucket-antigo",
        f"{ANTIGO}SAFETY": "c1-revisao-humana",
        "RENDER2GO_SAFETY": "disabled-sfw-tests",  # a nova vence
    }
    env.pop("RENDER2GO_R2_BUCKET", None)
    out = subprocess.run(
        [sys.executable, "-c", LER], env=env, capture_output=True, text=True, check=True
    )
    assert out.stdout.split() == ["bucket-antigo", "disabled-sfw-tests"]
