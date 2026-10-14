"""Tempos de cada pod a partir do log do lote_nuvem (stderr), para o experimento da imagem dos pods
(LEIAME.md desta pasta). Sem rede; só lê os logs.

    python coletar.py --variante bootstrap logs/bootstrap-*.log --variante imagem logs/imagem-*.log

Para cada pod: segundos da criação até o agente (inclui o pull da imagem), até os serviços no ar,
até o PRONTO e até o 1º vídeo, e as etapas da instalação (`tempos` do bootstrap). Imprime CSV e a
mediana por variante, com o tamanho da amostra.
"""

from __future__ import annotations

import argparse
import csv
import re
import statistics
import sys
from pathlib import Path

HORA = re.compile(r"^(\d\d):(\d\d):(\d\d) (.*)$")
POD = re.compile(r"pod (\S+) \(")
VIDEO = re.compile(r"^\[\d+/\d+\] \S+: ok \((\S+)\)")


def _s(h: str, m: str, s: str) -> int:
    return int(h) * 3600 + int(m) * 60 + int(s)


def pods_do_log(texto: str) -> dict[str, dict]:
    """pod → {criado, agente_s, servicos_s, pronto_s, video_s, etapas}; segundos desde a criação."""
    out: dict[str, dict] = {}
    for linha in texto.splitlines():
        m = HORA.match(linha)
        if not m:
            continue
        t, msg = _s(*m.groups()[:3]), m.group(4)
        v = VIDEO.match(msg)
        if v and v.group(1) in out and "video_s" not in out[v.group(1)]:
            out[v.group(1)]["video_s"] = t - out[v.group(1)]["criado"]
            continue
        p = POD.search(msg)
        if not p:
            continue
        pid = p.group(1)
        if " criado, US$ " in msg:
            out[pid] = {"criado": t, "etapas": {}}
        elif pid not in out:
            continue
        elif "agente e CUDA ok" in msg:
            out[pid]["agente_s"] = t - out[pid]["criado"]
        elif "serviços no ar" in msg or msg.endswith("GPU)") and ": pronto (" in msg:
            out[pid].setdefault("servicos_s", t - out[pid]["criado"])
            if ": pronto (" in msg:
                out[pid]["pronto_s"] = t - out[pid]["criado"]
        elif "tempos da instalação:" in msg:
            for par in msg.split("tempos da instalação:", 1)[1].split("|"):
                nome, _, seg = par.strip().rpartition(" ")
                if nome and seg.isdigit():
                    out[pid]["etapas"][nome] = int(seg)
    return out


def main(argv: list[str] | None = None) -> int:
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("--variante", action="append", nargs="+", metavar=("NOME", "LOG"), required=True)
    args = a.parse_args(argv)
    campos = ["agente_s", "servicos_s", "pronto_s", "video_s"]
    linhas = []
    for nome, *logs in args.variante:
        for log in logs:
            for pid, d in pods_do_log(Path(log).read_text(errors="replace")).items():
                linha = {"variante": nome, "pod": pid, **{k: d.get(k) for k in campos}}
                linhas.append({**linha, "etapas": d["etapas"]})
    w = csv.DictWriter(sys.stdout, fieldnames=["variante", "pod", *campos, "etapas"])
    w.writeheader()
    w.writerows(linhas)
    for nome in dict.fromkeys(x["variante"] for x in linhas):
        for campo in ("agente_s", "servicos_s", "pronto_s", "video_s"):
            v = [x[campo] for x in linhas if x["variante"] == nome and x[campo] is not None]
            if v:
                print(
                    f"# {nome} {campo}: mediana {statistics.median(v):.0f} s (n={len(v)})",
                    file=sys.stderr,
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
