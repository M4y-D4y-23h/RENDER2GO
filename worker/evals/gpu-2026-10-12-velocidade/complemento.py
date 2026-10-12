"""Roda no pod depois do no_pod.py (12/10/2026): refaz o que a fase 2 perdeu.

A fase 2 caiu num StopIteration depois do 1º vídeo com RIFE: o roteiro procurava o tempo do mesmo
vídeo entre os vídeos certos do worker novo, e esse vídeo tinha reprovado na conferência. Com isso,
o detalhe da fase 2 (etapas, comparação antes × novo) não foi gravado e o 2º vídeo com RIFE não
rodou. Aqui, com os vídeos que ficaram no disco do pod:
1. o que existe em cada rodada;
2. antes × novo, vídeo a vídeo (mesma seed: devem ser iguais);
3. SageAttention × atual (SSIM e PSNR) e as folhas lado a lado, só com os dois vídeos presentes;
4. RIFE limpo: na mesma GPU quente, o mesmo vídeo sem e com RIFE, alternados, para 2 vídeos.
Grava complemento.json a cada parte.
"""

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/root/render2go")
import no_pod as m  # noqa: E402

saida = Path(sys.argv[1])
v = saida / "videos"
r: dict = {}


def gravar() -> None:
    (saida / "complemento.json").write_text(json.dumps(r, indent=1, ensure_ascii=False))


def parte(nome, fn) -> None:
    m.log(f"complemento: {nome}")
    try:
        r[nome] = fn()
    except Exception as e:  # noqa: BLE001 (a próxima parte ainda roda)
        r[nome] = {"erro": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]}
    gravar()


def inventario() -> dict:
    return {
        d.name: sorted(x.name for x in d.glob("*.mp4")) for d in sorted(v.iterdir()) if d.is_dir()
    }


def antes_x_novo() -> dict:
    return {
        a: m.comparar(v / "antes" / a, v / "novo" / a)
        for a in r["inventario"].get("antes", [])
        if (v / "novo" / a).is_file()
    }


def sage_x_atual() -> dict:
    out = {}
    for rodada, base in (("sage", "novo"), ("sage-alta", "alta")):
        for a in r["inventario"].get(rodada, []):
            if not (v / base / a).is_file():
                continue
            out[f"{rodada}/{a}"] = m.comparar(v / base / a, v / rodada / a)
            dest = saida / "folhas" / f"sage-{rodada}-{Path(a).stem}.jpg"
            m.folha(
                [("atual", v / base / a, [0, 40, 80]), ("sage", v / rodada / a, [0, 40, 80])], dest
            )
    return out


def rife() -> list:
    tpl = m.template_rife(saida / "tmp")
    pasta = saida / "rife2"
    lista = []
    with m.Comfy(8292, [], saida / "comfy_rife.log") as c:
        m.render(c, "cafe", 999, "rapida", saida / "tmp", "aquece-rife")  # a GPU quente
        for foto, seed in (("cafe", 1001), ("xicara", 1002)):
            base = m.render(c, foto, seed, "rapida", pasta, f"{foto}-{seed}-base")
            com = m.render(c, foto, seed, "rapida", pasta, f"{foto}-{seed}-rife", tpl)
            vb, vr = pasta / f"{foto}-{seed}-base.mp4", pasta / f"{foto}-{seed}-rife.mp4"
            com |= {"video_info": m.quadros(vr), "bytes": vr.stat().st_size}
            base |= {"video_info": m.quadros(vb), "bytes": vb.stat().st_size}
            m.folha(
                [
                    (f"{foto} 32 fps (RIFE)", vr, [40, 41, 42, 43, 44]),
                    (f"{foto} 16 fps", vb, [20, 21, 22]),
                ],
                saida / "folhas" / f"rife-{foto}.jpg",
            )
            lista.append({"base": base, "rife": com})
            gravar()
    return lista


parte("inventario", inventario)
parte("antes_x_novo", antes_x_novo)
parte("sage_x_atual", sage_x_atual)
parte("rife", rife)
m.log("FIM_COMPLEMENTO")
