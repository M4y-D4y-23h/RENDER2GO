#!/usr/bin/env python3
"""Gera os Dockerfiles da integração GitHub do RunPod a partir de worker/Dockerfile.

A integração (ADR 0002) monta a imagem com o contexto na raiz do repositório e sem
`--target`, e a documentação não diz se usa BuildKit. Por isso cada endpoint ganha um
Dockerfile próprio em infra/runpod/, derivado do worker/Dockerfile (nunca editado à mão):

- só os estágios até o do endpoint (o último estágio é a imagem);
- caminhos de COPY com o prefixo worker/ (contexto = raiz);
- sem `RUN --mount` (secret da CA e tmpfs): formato clássico, aceito por qualquer builder.
  A CA extra só existe neste ambiente de desenvolvimento, não no RunPod;
- MODE=serverless no fim.

Uso: python tools/build_runpod_dockerfiles.py [--check]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "worker" / "Dockerfile"
OUT = ROOT / "infra" / "runpod"
TARGETS = {"Dockerfile.i2v": "full", "Dockerfile.t2v": "full-t2v"}
MOUNTS = re.compile(r"--mount=type=secret,id=extra_ca --mount=type=tmpfs,target=/run/render2go-ca ")
FROM = re.compile(r"^FROM\s+(\S+)(?:\s+AS\s+(\S+))?", re.IGNORECASE)


def stages(text: str) -> list[tuple[str | None, str | None, str]]:
    """(nome, base, texto) de cada estágio, na ordem; o preâmbulo vem com nome None."""
    out: list[tuple[str | None, str | None, list[str]]] = [(None, None, [])]
    for line in text.splitlines(keepends=True):
        m = FROM.match(line)
        if m:
            out.append((m.group(2), m.group(1), []))
        out[-1][2].append(line)
    return [(n, b, "".join(lines)) for n, b, lines in out]


def keep_chain(all_stages, target: str) -> list:
    by_name = {n: (n, b, t) for n, b, t in all_stages if n}
    needed, cur = set(), target
    while cur in by_name:
        needed.add(cur)
        cur = by_name[cur][1]
    # estágios usados em COPY --from=<estágio> (ex.: o do uv)
    needed |= {
        ref for n, _, t in all_stages if n in needed for ref in re.findall(r"COPY --from=(\S+)", t)
    }
    return [s for s in all_stages if s[0] is None or s[0] in needed]


def rewrite_copy(line: str) -> str:
    if not line.startswith("COPY ") or line.startswith("COPY --from="):
        return line
    parts = line.rstrip("\n").split()
    srcs, dest = parts[1:-1], parts[-1]
    return "COPY " + " ".join(f"worker/{s}" for s in srcs) + f" {dest}\n"


def build(target: str, nome: str) -> str:
    text = SOURCE.read_text()
    kept = keep_chain(stages(text), target)
    # preâmbulo: só os ARGs globais (os comentários de lá falam do contexto worker/)
    pre = "".join(line for line in kept[0][2].splitlines(keepends=True) if line.startswith("ARG "))
    body = pre + "\n" + "".join(t for _, _, t in kept[1:])
    body = MOUNTS.sub("", body)
    body = "".join(rewrite_copy(line) for line in body.splitlines(keepends=True))
    if "--mount" in body or "<<" in body:
        raise SystemExit(f"{nome}: sobrou recurso exclusivo do BuildKit")
    head = (
        f"# GERADO por tools/build_runpod_dockerfiles.py a partir de worker/Dockerfile\n"
        f"# (estágio `{target}`). Não edite: mude o worker/Dockerfile e rode o script.\n"
        f"# Integração GitHub do RunPod: Dockerfile Path = infra/runpod/{nome};\n"
        f"# contexto = raiz do repositório.\n\n"
    )
    return head + body.rstrip("\n") + "\n\nENV MODE=serverless\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--check", action="store_true", help="falha se algum arquivo estiver desatualizado"
    )
    a = p.parse_args()
    stale = []
    for nome, target in TARGETS.items():
        text = build(target, nome)
        path = OUT / nome
        if a.check:
            if not path.exists() or path.read_text() != text:
                stale.append(nome)
            continue
        path.write_text(text)
        print(f"escrito {path.relative_to(ROOT)}")
    if a.check:
        for n in stale:
            print(f"infra/runpod/{n} está desatualizado; rode o script sem --check")
        if not stale:
            print("Dockerfiles do RunPod em dia")
        return 1 if stale else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
