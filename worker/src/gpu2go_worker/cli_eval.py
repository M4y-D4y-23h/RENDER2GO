"""Roda os testes de fidelidade do compilador contra um Ollama e grava o relatório.

Exemplo (compara 7B × 14B e duas versões do prompt de I2V):

  python tools/eval_compiler.py --ollama http://127.0.0.1:11434 \\
      --model huihui_ai/qwen2.5-abliterate:7b --model huihui_ai/qwen2.5-abliterate:14b \\
      --version i2v=3 --version i2v=4 --out worker/evals/compiler/reports/run

Códigos de saída: 0 todos os limites atingidos · 1 algum limite não atingido · 2 erro.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from gpu2go_worker import compiler, fidelity


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gpu2go-eval-compiler",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n\n", 1)[1],
    )
    p.add_argument("--ollama", default="http://127.0.0.1:11434")
    p.add_argument("--model", action="append", help="repetível; padrão: o 7B de produção")
    p.add_argument(
        "--version",
        action="append",
        default=[],
        help="perfil=versão do prompt (repetível); padrão: a versão em produção",
    )
    p.add_argument("--profile", action="append", help="só estes perfis (i2v, t2v)")
    p.add_argument("--case", action="append", help="só estes ids de caso")
    p.add_argument("--seeds", help="lista separada por vírgula; padrão: a do arquivo de casos")
    p.add_argument("--cases", type=Path, default=fidelity.CASES_FILE)
    p.add_argument("--out", type=Path, required=True, help="prefixo dos arquivos .json e .md")
    return p


def _versions(items: list[str], profiles: list[str]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {p: [] for p in profiles}
    for item in items:
        prof, _, ver = item.partition("=")
        if prof not in out or not ver.isdigit():
            raise SystemExit(f"--version inválido: {item!r}")
        out[prof].append(int(ver))
    return {p: v or [compiler.PROFILES[p]["version"]] for p, v in out.items()}


def render_markdown(report: dict) -> str:
    lines = [
        f"# Fidelidade do compilador — {report['started']}",
        "",
        f"Casos: `{report['cases_file']}` (v{report['cases_version']}), "
        f"seeds {report['seeds']}, Ollama {report['ollama_version']}.",
        "",
        "| Perfil | Prompt | Modelo | Casos ok | Aspas | Sem invenção | Câmera/termos | Ordem "
        "| Palavras | REFUSED (informativo) | Mediana (s) | Limites |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for g in report["groups"]:
        s = g["summary"]
        r = s["check_pass_rate"]

        def pct(key: str, rates: dict = r) -> str:
            return f"{rates[key]:.0%}" if key in rates else "—"

        ri = s["refusal_info"]
        lines.append(
            f"| {g['profile']} | v{g['version']} | `{g['model']}` | {s['case_pass_rate']:.0%} "
            f"| {pct('quoted')} | {pct('forbid')} | {pct('must')} | {pct('order')} "
            f"| {pct('words')} | {ri['refused']}/{ri['runs']} | {s['median_seconds']} "
            f"| {'atingidos' if s['passed'] else 'não atingidos'} |"
        )
    lines += ["", "## Falhas", ""]
    for g in report["groups"]:
        fails = [r for r in g["results"] if not r["passed"]]
        if not fails:
            continue
        lines.append(f"### {g['profile']} v{g['version']} — `{g['model']}`")
        lines.append("")
        for r in fails:
            tag = "" if r["gating"] else " (informativo)"
            lines.append(f"- **{r['case_id']}** seed {r['seed']}{tag}: {'; '.join(r['failures'])}")
            lines.append(f"  > {r['output']}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    data = fidelity.load_cases(a.cases)
    profiles = a.profile or sorted({c["profile"] for c in data["cases"]})
    versions = _versions(a.version, profiles)
    models = a.model or [compiler.DEFAULT_MODEL]
    seeds = [int(s) for s in a.seeds.split(",")] if a.seeds else data["seeds"]
    cases = [c for c in data["cases"] if c["profile"] in profiles]
    if a.case:
        cases = [c for c in cases if c["id"] in a.case]
    if not cases:
        _log("nenhum caso selecionado")
        return 2

    import requests

    try:
        ollama_version = requests.get(f"{a.ollama}/api/version", timeout=10).json()["version"]
    except Exception as e:  # noqa: BLE001
        _log(f"Ollama inacessível em {a.ollama}: {e}")
        return 2

    report = {
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cases_file": str(a.cases.name),
        "cases_version": data["version"],
        "seeds": seeds,
        "ollama_version": ollama_version,
        "groups": [],
    }
    for model in models:
        for profile in profiles:
            for version in versions[profile]:
                results = []
                for case in (c for c in cases if c["profile"] == profile):
                    for seed in seeds:
                        try:
                            r = fidelity.run_case(
                                case, model=model, ollama_url=a.ollama, version=version, seed=seed
                            )
                        except compiler.CompilerError as e:
                            _log(f"erro em {case['id']}: {e}")
                            return 2
                        mark = "ok " if r.passed else "FALHA"
                        _log(f"{mark} {model} {profile}@{version} {r.case_id} s{seed} {r.seconds}s")
                        results.append(r)
                summary = fidelity.summarize(results, data["thresholds"])
                report["groups"].append(
                    {
                        "model": model,
                        "profile": profile,
                        "version": version,
                        "summary": summary,
                        "results": fidelity.to_json(results),
                    }
                )
                _log(f"== {model} {profile}@{version}: {json.dumps(summary, ensure_ascii=False)}")

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    a.out.with_suffix(".md").write_text(render_markdown(report))
    _log(f"relatório: {a.out.with_suffix('.md')}")
    return 0 if all(g["summary"]["passed"] for g in report["groups"]) else 1


if __name__ == "__main__":
    sys.exit(main())
