"""Experimentos de qualidade em GPU: bake-off do T2V e variações do turbo.

Um plano (worker/evals/*.json) define variantes (template + mudanças nos valores fixos),
prompts já em inglês (para tirar a variação do compilador da comparação), seeds e
parâmetros. Para cada prompt × seed × variante, o script enfileira no ComfyUI, mede o
tempo e o pico de VRAM observado (/system_stats), baixa o MP4 e no fim grava:

- results.jsonl: uma linha por vídeo;
- summary.json: mediana de tempo, pico de VRAM e GB extras de modelos por variante;
- index.html: grade cega lado a lado (linhas = prompt × seed; vídeos só com código e em
  ordem embaralhada por linha) e as medições por variante;
- ratings.csv + blind_key.json: planilha para as notas humanas (aderência, movimento,
  artefatos, 1 a 5) e a chave código → variante. `--score notas.csv` junta os dois.

`overrides` muda valores que o contrato trava (passos, CFG, modelos). Por isso este
caminho só existe aqui, rotulado como experimento, e nunca no envio de produção.

Só conteúdo SFW nos planos (CLAUDE.md).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import random
import secrets
import statistics
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from render2go_worker import WORKER_DIR, models
from render2go_worker import template as tpl_mod
from render2go_worker.comfy_client import ComfyClient, ComfyError

BASE_MODEL_SET = "i2v_r1v2"
CRITERIA = ("aderencia_ao_prompt", "qualidade_do_movimento", "artefatos")


class ExperimentError(Exception):
    pass


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def load_plan(path: Path) -> dict[str, Any]:
    plan = json.loads(path.read_text())
    names = [v["name"] for v in plan["variants"]]
    if len(set(names)) != len(names):
        raise ExperimentError("nomes de variante repetidos")
    for v in plan["variants"]:
        tpl = tpl_mod.load(WORKER_DIR / v["template"])
        apply_variant(tpl, v, {**plan.get("params", {}), **_dummy_inputs(tpl)})
    if not plan["prompts"] or not plan["seeds"]:
        raise ExperimentError("o plano precisa de prompts e seeds")
    return plan


def _dummy_inputs(tpl: tpl_mod.Template) -> dict[str, Any]:
    p: dict[str, Any] = {"prompt_en": "test", "seed": 0}
    if "image" in tpl.contract["params"]:
        p["image"] = "placeholder.png"
    return p


def apply_variant(
    tpl: tpl_mod.Template, variant: dict[str, Any], params: dict[str, Any]
) -> dict[str, Any]:
    """Preenche pelo contrato e depois aplica `overrides` ("nó.entrada": valor)."""
    wf, _ = tpl.apply(params)
    for ref, value in variant.get("overrides", {}).items():
        node_id, _, input_name = ref.rpartition(".")
        node = wf.get(node_id)
        if node is None or input_name not in node["inputs"]:
            raise ExperimentError(f"{variant['name']}: entrada {ref} não existe em {tpl.ref}")
        node["inputs"][input_name] = value
    return wf


def extra_model_gb(tpl: tpl_mod.Template) -> float:
    """GB de modelos que a variante soma aos do I2V de produção."""
    sets, entries = models.load_lock()
    needed = {
        e.name
        for e in models.resolve([tpl.contract.get("model_set", BASE_MODEL_SET)], sets, entries)
    }
    base = set(sets[BASE_MODEL_SET])
    return round(sum(entries[n].bytes for n in needed - base) / 1e9, 1)


class VramSampler:
    """Pico de VRAM em uso (total - livre) no 1º dispositivo de /system_stats."""

    def __init__(self, comfy: ComfyClient, interval: float = 0.5):
        self.comfy, self.interval = comfy, interval
        self.peak_gb: float | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                dev = self.comfy.system_stats()["devices"][0]
                used = (dev["vram_total"] - dev["vram_free"]) / 1e9
                self.peak_gb = max(self.peak_gb or 0.0, round(used, 2))
            except (ComfyError, KeyError, IndexError):
                pass
            self._stop.wait(self.interval)

    def __enter__(self) -> VramSampler:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


def run(
    plan: dict[str, Any],
    comfy: ComfyClient,
    out_dir: Path,
    timeout: float,
    image: Path | None = None,
) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    done = set()
    results: list[dict] = []
    if results_path.exists():  # retoma um experimento interrompido
        for line in results_path.read_text().splitlines():
            r = json.loads(line)
            if r["status"] == "ok":
                done.add((r["prompt_id"], r["seed"], r["variant"]))
                results.append(r)

    templates = {v["name"]: tpl_mod.load(WORKER_DIR / v["template"]) for v in plan["variants"]}
    image_name = None
    image = image or (WORKER_DIR.parent / plan["image"] if plan.get("image") else None)
    if image is not None:
        image_name = comfy.upload_image(image, f"render2go_exp_{uuid.uuid4().hex}{image.suffix}")
    # uma variante por vez: alternar variantes faria o ComfyUI trocar de modelo a cada
    # vídeo, e o tempo "quente" passaria a incluir a carga
    combos = [(v, p, s) for v in plan["variants"] for p in plan["prompts"] for s in plan["seeds"]]
    for variant, prompt, seed in combos:
        key = (prompt["id"], seed, variant["name"])
        if key in done:
            continue
        tpl = templates[variant["name"]]
        params = {**plan.get("params", {}), "prompt_en": prompt["en"], "seed": seed}
        if "image" in tpl.contract["params"]:
            if image_name is None:
                raise ExperimentError(f"{tpl.ref} exige imagem: use --image")
            params["image"] = image_name
        stem = f"{prompt['id']}__s{seed}__{variant['name']}"
        params["filename_prefix"] = f"video/exp_{secrets.token_hex(4)}_{stem}"
        wf = apply_variant(tpl, variant, params)
        rec: dict[str, Any] = {
            "prompt_id": prompt["id"],
            "seed": seed,
            "variant": variant["name"],
            "template": tpl.ref,
        }
        _log(f"→ {stem}")
        t0 = time.monotonic()
        try:
            cid = uuid.uuid4().hex
            with VramSampler(comfy) as vram:
                pid = comfy.queue_prompt(wf, cid)
                entry = comfy.wait(pid, cid, timeout=timeout, use_ws=False)
            rec["render_s"] = round(time.monotonic() - t0, 2)
            rec["vram_peak_gb"] = vram.peak_gb
            videos = comfy.video_outputs(entry)
            if not videos:
                raise ExperimentError("sem vídeo na saída")
            # nome opaco: o arquivo não pode revelar a variante na avaliação cega
            digest = hashlib.sha256(f"{plan['name']}|{stem}".encode()).hexdigest()
            dest = out_dir / "videos" / f"{digest[:16]}.mp4"
            comfy.download(videos[0], dest)
            rec.update(status="ok", video=str(dest.relative_to(out_dir)))
        except (ComfyError, ExperimentError) as e:
            rec.update(status="error", error=str(e), render_s=round(time.monotonic() - t0, 2))
            _log(f"  erro: {e}")
        with results_path.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        results.append(rec)
    return results


def summarize(plan: dict[str, Any], results: list[dict]) -> dict[str, Any]:
    out = {}
    for v in plan["variants"]:
        rs = [r for r in results if r["variant"] == v["name"]]
        ok = [r for r in rs if r["status"] == "ok"]
        times = sorted(r["render_s"] for r in ok)
        # o 1º render de cada variante inclui a carga dos modelos; a mediana dos demais
        # é o tempo "quente"
        warm = times[1:] if len(times) > 1 else times
        vram = [r["vram_peak_gb"] for r in ok if r.get("vram_peak_gb") is not None]
        tpl = tpl_mod.load(WORKER_DIR / v["template"])
        out[v["name"]] = {
            "template": tpl.ref,
            "ok": len(ok),
            "errors": len(rs) - len(ok),
            "first_render_s": next((r["render_s"] for r in ok), None),
            "median_warm_s": round(statistics.median(warm), 2) if warm else None,
            "vram_peak_gb": max(vram) if vram else None,
            "extra_model_gb": extra_model_gb(tpl),
            "notes": v.get("notes", ""),
        }
    return out


def blind_order(plan: dict[str, Any], prompt_id: str, seed: int) -> list[str]:
    """Ordem embaralhada (e reprodutível) das variantes numa linha da grade."""
    names = [v["name"] for v in plan["variants"]]
    rng = random.Random(f"{plan['name']}|{prompt_id}|{seed}")
    rng.shuffle(names)
    return names


def write_reports(plan: dict[str, Any], results: list[dict], out_dir: Path) -> None:
    """Relatórios cegos: na grade e na planilha, cada vídeo aparece só com um código (A, B…)
    em ordem embaralhada por linha; a chave fica em blind_key.json (não abrir antes de
    terminar as notas)."""
    summary = summarize(plan, results)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    by_key = {(r["prompt_id"], r["seed"], r["variant"]): r for r in results}
    codes = [chr(ord("A") + i) for i in range(len(plan["variants"]))]
    key: dict[str, str] = {}
    rows, sheet = [], []
    for p in plan["prompts"]:
        for s in plan["seeds"]:
            cells = []
            for code, v in zip(codes, blind_order(plan, p["id"], s), strict=True):
                key[f"{p['id']}|{s}|{code}"] = v
                sheet.append([p["id"], s, code, "", "", "", ""])
                r = by_key.get((p["id"], s, v))
                if r and r["status"] == "ok":
                    cells.append(
                        f'<td><b>{code}</b><br><video src="{html.escape(r["video"])}" controls '
                        'loop muted preload="metadata"></video></td>'
                    )
                else:
                    cells.append(f"<td class=err><b>{code}</b><br>sem vídeo</td>")
            label = (
                f"<b>{html.escape(p['id'])}</b> · seed {s}<br><small>{html.escape(p['en'])}</small>"
            )
            rows.append(f"<tr><th class=p>{label}</th>{''.join(cells)}</tr>")
    (out_dir / "blind_key.json").write_text(json.dumps(key, ensure_ascii=False, indent=2) + "\n")
    with (out_dir / "ratings.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["prompt_id", "seed", "codigo", *CRITERIA, "comentario"])
        w.writerows(sheet)

    stats = "".join(
        f"<tr><td>{html.escape(k)}</td><td>{v['median_warm_s']}</td><td>{v['first_render_s']}</td>"
        f"<td>{v['vram_peak_gb']}</td><td>{v['extra_model_gb']}</td>"
        f"<td>{v['ok']}/{v['ok'] + v['errors']}</td></tr>"
        for k, v in summary.items()
    )
    head = "".join(f"<th>{c}</th>" for c in codes)
    (out_dir / "index.html").write_text(
        f"""<!doctype html><meta charset=utf-8><title>{html.escape(plan["name"])}</title>
<style>body{{font:14px system-ui;margin:16px}}table{{border-collapse:collapse}}
td,th{{border:1px solid #ccc;padding:6px;vertical-align:top}}video{{width:320px}}
th.p{{max-width:280px;text-align:left;font-weight:normal}}.err{{color:#b00}}</style>
<h1>{html.escape(plan["name"])}</h1><p>{html.escape(plan.get("description", ""))}</p>
<p>Avaliação cega: dê notas de 1 a 5 em <code>ratings.csv</code> pelo código de cada vídeo
(a ordem muda a cada linha). Só depois rode <code>--score</code>.</p>
<table><tr><th></th>{head}</tr>{"".join(rows)}</table>
<h2>Medições por variante</h2>
<table><tr><th>variante</th><th>mediana quente (s)</th><th>1º render (s)</th>
<th>pico VRAM (GB)</th><th>GB extras de modelos</th><th>ok</th></tr>{stats}</table>
"""
    )


def score(plan: dict[str, Any], out_dir: Path, ratings: list[Path]) -> dict[str, Any]:
    """Junta as planilhas de notas (uma por avaliador) com a chave e calcula médias."""
    key = json.loads((out_dir / "blind_key.json").read_text())
    per_variant: dict[str, dict[str, list[float]]] = {
        v["name"]: {c: [] for c in CRITERIA} for v in plan["variants"]
    }
    for path in ratings:
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                variant = key.get(f"{row['prompt_id']}|{row['seed']}|{row['codigo']}")
                if variant is None:
                    raise ExperimentError(f"{path.name}: linha sem chave: {row}")
                for c in CRITERIA:
                    if row.get(c, "").strip():
                        value = float(row[c].replace(",", "."))
                        if not 1 <= value <= 5:
                            raise ExperimentError(f"{path.name}: nota fora de 1–5: {row}")
                        per_variant[variant][c].append(value)
    summary = json.loads((out_dir / "summary.json").read_text())
    out = {}
    for name, crit in per_variant.items():
        means = {c: round(statistics.mean(v), 2) if v else None for c, v in crit.items()}
        filled = [m for m in means.values() if m is not None]
        out[name] = {
            **means,
            "media_geral": round(statistics.mean(filled), 2) if filled else None,
            "notas": sum(len(v) for v in crit.values()),
            **{
                k: summary.get(name, {}).get(k)
                for k in ("median_warm_s", "vram_peak_gb", "extra_model_gb")
            },
        }
    (out_dir / "scores.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="render2go-experiment", description=__doc__.splitlines()[0])
    p.add_argument("plan", type=Path, help="ex.: worker/evals/t2v-bakeoff.v1.json")
    p.add_argument("--comfy", default="http://127.0.0.1:8188")
    p.add_argument("--out", type=Path, required=True, help="diretório dos resultados")
    p.add_argument("--timeout", type=float, default=1800.0, help="limite por vídeo (s)")
    p.add_argument("--image", type=Path, help="imagem SFW de entrada (planos de I2V)")
    p.add_argument("--dry-run", action="store_true", help="só valida o plano")
    p.add_argument(
        "--score",
        nargs="+",
        type=Path,
        metavar="NOTAS.csv",
        help="não renderiza: junta as planilhas de notas à chave cega e grava scores.json",
    )
    a = p.parse_args(argv)
    try:
        plan = load_plan(a.plan)
    except (ExperimentError, tpl_mod.TemplateError, OSError, KeyError) as e:
        _log(f"plano inválido: {e}")
        return 2
    if a.score:
        try:
            print(json.dumps(score(plan, a.out, a.score), ensure_ascii=False, indent=2))
        except (ExperimentError, OSError, KeyError, ValueError) as e:
            _log(f"erro ao calcular as notas: {e}")
            return 2
        return 0
    n = len(plan["prompts"]) * len(plan["seeds"]) * len(plan["variants"])
    _log(f"plano {plan['name']}: {n} vídeos")
    if a.image is not None and not a.image.is_file():
        _log(f"imagem inexistente: {a.image}")
        return 2
    if plan.get("image_required") and a.image is None and not plan.get("image"):
        _log(f"este plano exige --image: {plan.get('image_hint', '')}")
        return 2
    if a.dry_run:
        return 0
    comfy = ComfyClient(a.comfy)
    try:
        comfy.system_stats()
    except ComfyError as e:
        _log(str(e))
        return 5
    results = run(plan, comfy, a.out, a.timeout, a.image)
    write_reports(plan, results, a.out)
    _log(f"relatório: {a.out / 'index.html'}")
    return 0 if all(r["status"] == "ok" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
