"""Julgamento às cegas e tabela da rodada de qualidade de 13/10/2026 (fora do pod).

1. `preparar <pod1> <pod2> <pasta>`: copia a tira de cada vídeo a julgar (A, B e D em 480p, A e B
   em 720p) para `<pasta>/cegas/<código>.jpg`, com um código aleatório no lugar da combinação, e
   escreve `<pasta>/julgamento.json` (código → o pedido do cliente e o tipo, com "aproveitavel" e
   "motivo" vazios) e `<pasta>/chave.json` (código → vídeo). A chave só é aberta depois do
   julgamento inteiro escrito (LEIAME, "Aproveitável"). Os vídeos de D cujo prompt não muda não
   entram: para os critérios, eles são os de B (ADR 0014).
2. `tabela <pod1> <pod2> <pasta>`: junta o julgamento com a chave e os resultados dos pods e
   escreve `<pasta>/tabela.md` (A × B × D pelos critérios do ADR 0014) e as folhas lado a lado,
   com o nome da combinação, em `<pasta>/folhas/` (uma por foto e resolução).

Uso (da pasta `worker`): uv run python evals/gpu-2026-10-13-qualidade/julgar.py preparar ...
"""

from __future__ import annotations

import json
import secrets
import shutil
import statistics
import sys
from pathlib import Path

GRUPOS = [("4_480p", "A", "480"), ("4_480p", "B", "480"), ("4_480p", "D", "480"),
          ("7_720p", "A", "720"), ("7_720p", "B", "720")]  # fmt: skip


def _res(pasta: Path) -> dict:
    return json.loads((pasta / "resultado.json").read_text())


def _textos(res: dict) -> dict[str, tuple[str, str]]:
    """pedido → (texto do cliente, tipo), do plano."""
    plano = res["plano"]
    out = {}
    for f in plano["fotos"]:
        out[f"{f['id']}-concreto"] = (f["concreto"]["texto"], "concreto")
        out[f"{f['id']}-vago"] = (plano["vago"]["texto"], "vago")
    return out


def videos(pod1: Path, pod2: Path) -> list[tuple[dict, Path]]:
    """(vídeo, pasta da tira) de cada vídeo a julgar."""
    out = []
    for pasta in (pod1, pod2):
        if not (pasta / "resultado.json").is_file():
            continue
        res = _res(pasta)
        muda = {k for k, v in (res.get("P_prompts", {}).get("D") or {}).items() if v.get("muda")}
        for fase, combo, _ in GRUPOS:
            for v in res.get(fase, {}).get(combo, []) or []:
                if combo == "D" and v["pedido"] not in muda:
                    continue
                out.append((v, pasta / "tiras"))
    return out


def preparar(pod1: Path, pod2: Path, pasta: Path) -> None:
    cegas = pasta / "cegas"
    if (pasta / "chave.json").exists():
        sys.exit("já preparado: apague a pasta para sortear de novo (o julgamento seria outro)")
    cegas.mkdir(parents=True, exist_ok=True)
    textos = _textos(_res(pod1))
    itens = videos(pod1, pod2)
    secrets.SystemRandom().shuffle(itens)
    chave, julg = {}, {}
    for v, tiras in itens:
        if not v.get("tira"):
            continue
        codigo = secrets.token_hex(3)
        shutil.copyfile(tiras / v["tira"], cegas / f"{codigo}.jpg")
        chave[codigo] = v["nome"]
        texto, tipo = textos[v["pedido"]]
        julg[codigo] = {"pedido": texto, "tipo": tipo, "aproveitavel": None, "motivo": ""}
    (pasta / "chave.json").write_text(json.dumps(chave, indent=1, ensure_ascii=False))
    (pasta / "julgamento.json").write_text(json.dumps(julg, indent=1, ensure_ascii=False))
    print(f"{len(julg)} tiras em {cegas}; julgamento em {pasta / 'julgamento.json'}")


def _mediana(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def tabela(pod1: Path, pod2: Path, pasta: Path) -> None:
    julg = json.loads((pasta / "julgamento.json").read_text())
    if any(j["aproveitavel"] is None for j in julg.values()):
        sys.exit("julgamento incompleto: nenhuma chave antes de todas as tiras julgadas")
    chave = json.loads((pasta / "chave.json").read_text())
    nota = {chave[c]: j for c, j in julg.items()}
    linhas = []
    por: dict[tuple[str, str], list[dict]] = {}
    for pasta_pod in (pod1, pod2):
        if not (pasta_pod / "resultado.json").is_file():
            continue
        res = _res(pasta_pod)
        muda = {k for k, v in (res.get("P_prompts", {}).get("D") or {}).items() if v.get("muda")}
        for fase, combo, r in GRUPOS:
            vs = res.get(fase, {}).get(combo, []) or []
            if combo == "D":  # para os critérios: o gerado onde o prompt muda, o de B onde não
                d = {v["pedido"]: v for v in vs}
                vs = [d[v["pedido"]] if v["pedido"] in muda else v
                      for v in res.get(fase, {}).get("B", [])]  # fmt: skip
            if vs:
                por[(combo, r)] = vs
    for (combo, r), vs in sorted(por.items(), key=lambda x: (x[0][1], x[0][0])):
        # "aproveitável" (LEIAME): o julgamento às cegas (itens 1 a 4) e, no item 5, a
        # conferência e o filtro aprovados
        ap = [
            nota[v["nome"]]["aproveitavel"] and not v.get("conferencia") and not v.get("barrado")
            for v in vs
            if v["nome"] in nota
        ]
        ok = [v for v in vs if v.get("ok")]
        nsfw = [v["nsfw"] for v in ok if "nsfw" in v]
        sem_pessoa = [v for v in vs if not v.get("pessoas")]
        inventada = sum(
            bool(v.get("prompt_cita_pessoa"))
            or "pessoa" in (nota.get(v["nome"], {}).get("motivo") or "").lower()
            for v in sem_pessoa
        )
        celulas = [
            combo, f"{r}p", len(vs), sum(bool(x) for x in ap),
            sum(bool(v.get("conferencia")) for v in vs), sum(bool(v.get("barrado")) for v in vs),
            round(statistics.mean(nsfw), 4) if nsfw else "—", inventada,
            _mediana([v.get("executado_s") for v in ok]),
            max((v.get("vram_pico_mib") or 0) for v in vs),
            sum(bool(v.get("sem_memoria")) for v in vs),
        ]  # fmt: skip
        linhas.append("| " + " | ".join(str(c) for c in celulas) + " |")
    colunas = [
        "Combinação", "Resolução", "Vídeos", "Aproveitáveis", "Reprovados na conferência",
        "Barrados pelo filtro", "Nota média do filtro", "Pessoa inventada (fotos sem pessoa)",
        "Tempo mediano do ComfyUI (s)", "Pico de VRAM (MiB)", "Falta de memória",
    ]  # fmt: skip
    cab = "| " + " | ".join(colunas) + " |\n|" + "---|" * len(colunas)
    (pasta / "tabela.md").write_text(cab + "\n" + "\n".join(linhas) + "\n")
    folhas(por, pod1, pod2, pasta / "folhas")
    print((pasta / "tabela.md").read_text())


def folhas(por: dict, pod1: Path, pod2: Path, destino: Path) -> None:
    """Uma folha por foto e resolução: as tiras de A, B e D, uma embaixo da outra, com o nome."""
    from PIL import Image, ImageDraw

    destino.mkdir(parents=True, exist_ok=True)
    tiras = {}
    for pasta_pod in (pod1, pod2):
        for t in (pasta_pod / "tiras").glob("*.jpg"):
            tiras[t.stem] = t
    fotos = sorted({(v["foto"], r) for (_, r), vs in por.items() for v in vs})
    for foto, r in fotos:
        for tipo in ("concreto", "vago"):
            linhas = []
            for combo in ("A", "B", "D"):
                v = next((v for v in por.get((combo, r), []) if v["pedido"] == f"{foto}-{tipo}"),
                         None)  # fmt: skip
                if v is None or v["nome"] not in tiras:
                    continue
                with Image.open(tiras[v["nome"]]) as im:
                    linha = Image.new("RGB", (im.width, im.height + 22), "white")
                    linha.paste(im.convert("RGB"), (0, 22))
                rotulo = f"{combo} {r}p ({v['nome']})" if combo != "D" else f"D {r}p ({v['nome']})"
                ImageDraw.Draw(linha).text((6, 4), rotulo, fill=(0, 0, 0))
                linhas.append(linha)
            if not linhas:
                continue
            out = Image.new("RGB", (max(i.width for i in linhas), sum(i.height for i in linhas)),
                            "white")  # fmt: skip
            y = 0
            for i in linhas:
                out.paste(i, (0, y))
                y += i.height
            out.save(destino / f"{foto}-{tipo}-{r}p.jpg", quality=80)


def main() -> None:
    if len(sys.argv) != 5 or sys.argv[1] not in ("preparar", "tabela"):
        sys.exit(__doc__)
    pod1, pod2, pasta = (Path(x) for x in sys.argv[2:])
    (preparar if sys.argv[1] == "preparar" else tabela)(pod1, pod2, pasta)


if __name__ == "__main__":
    main()
