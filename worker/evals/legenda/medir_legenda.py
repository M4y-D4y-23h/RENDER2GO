"""Mede a legenda por foto (ADR 0014) em CPU: o `qwen2.5vl:7b` num Ollama só de CPU, nas fotos SFW
do repositório, preparadas no formato do vídeo 480p (832×480) como o worker faz.

Uso (da pasta `worker`, com o Ollama de CPU em 127.0.0.1:11435 e o modelo já baixado):
    uv run python evals/legenda/medir_legenda.py <pasta de saída> [threads ...]

A 1ª foto inclui a carga do modelo. Resultado de 13/10/2026: `2026-10-13-cpu.json` (ADR 0014,
"Medições sem GPU").
"""

import json
import sys
import time
from pathlib import Path

from render2go_worker import legenda, preparo

REPO = Path(__file__).resolve().parents[3]
FOTOS = ["produto-xicara.jpg", "produto-cafe.jpg", "pessoas-padaria-1900.jpg"]


def main() -> None:
    saida = Path(sys.argv[1])
    saida.mkdir(parents=True, exist_ok=True)
    res = []
    for threads in [int(t) for t in sys.argv[2:]] or [4, 2]:
        for nome in FOTOS:
            foto = REPO / "fixtures" / "sfw" / nome
            prep = preparo.preparar(foto, 832, 480, saida / f"prep-{nome}.png")
            t0 = time.monotonic()
            try:
                leg = legenda.legendar(
                    prep.path, ollama_url=legenda.OLLAMA_CPU, threads=threads, timeout=900
                )
                d, erro = leg.como_dict(), None
            except legenda.LegendaError as e:
                d, erro = None, str(e)
            linha = {
                "foto": nome,
                "threads": threads,
                "segundos": round(time.monotonic() - t0, 1),
                "legenda": d,
                "erro": erro,
            }
            print(json.dumps(linha, ensure_ascii=False), flush=True)
            res.append(linha)
    (saida / "medicao.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
