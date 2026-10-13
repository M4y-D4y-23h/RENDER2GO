"""Testes de fidelidade do compilador: pedido em pt → propriedades esperadas na saída.

Cada caso (worker/evals/compiler/cases.v4.json: os da v3, iguais, mais os com a legenda da foto)
declara:
- `must`: grupos de termos; cada grupo precisa aparecer (basta um termo do grupo);
- `forbid`: termos que indicam invenção ou tradução errada (nenhum pode aparecer);
- `order`: grupos que precisam aparecer nessa ordem (ordem das ações);
- texto entre aspas na entrada: conferido automaticamente, sem diferenciar maiúsculas;
- faixa de palavras do perfil;
- `expect_refused`: casos informativos da regra REFUSED (nunca são gating: o compilador
  não é camada de segurança).

Casos com `receita` (v3) passam pelo caminho do serviço (render2go_worker.traducao): presets do
estilo, câmera suave do Livre, pedido vago (VAGUE) e, com `sem_pessoas`, a regra da pessoa inventada
(traduz de novo e, no fim, o movimento padrão), com a conferência "pessoa" (nenhuma pessoa no prompt
final). `categoria`: a da foto (padrão: a do estilo). Os casos só com `presets` seguem como antes.
Casos com `legenda` (v4, ADR 0014): a legenda da foto escrita à mão (descricao, categoria,
assunto, pessoas, texto), como o modelo de visão a daria; ela vale no código para qualquer versão
(categoria e assunto do movimento padrão, e a regra da pessoa, que também vale se a legenda diz
"sem pessoas"), e só o prompt v8 em diante a recebe na entrada.

A comparação é pelo início da palavra (`walk` casa com `walks`, `walking`), sem
diferenciar maiúsculas; um termo terminado em espaço (`"man "`) exige a palavra inteira.
Tudo SFW: a regra "não suavizar termos explícitos" não é testada
aqui por política do repositório (CLAUDE.md).
"""

from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from render2go_worker import WORKER_DIR, compiler, lote, presets, traducao

CASES_FILE = WORKER_DIR / "evals" / "compiler" / "cases.v4.json"
QUOTED = re.compile(r'"([^"\n]+)"')


@dataclass
class CaseResult:
    case_id: str
    profile: str
    version: int
    model: str
    seed: int
    gating: bool
    input: str
    output: str
    refused: bool
    seconds: float
    words: int
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    vago: bool = False  # o compilador respondeu VAGUE (movimento padrão)
    padrao: bool = False  # o prompt final é o movimento padrão (vago ou pessoa inventada)
    sem_pessoas: bool = False  # a regra da pessoa inventada valeu no caso
    rejeitados: list[str] = field(default_factory=list)  # traduções que citaram pessoa

    @property
    def passed(self) -> bool:
        return not self.failures


def _sem_pessoas(case: dict[str, Any]) -> bool:
    """A regra da pessoa inventada vale no caso: a foto sem pessoas (`sem_pessoas`, ou a legenda que
    diz que não tem) e o pedido sem ninguém."""
    if traducao.texto_cita_pessoa(case["text"]):
        return False
    leg = traducao.campos_legenda(case.get("legenda"))
    return bool(case.get("sem_pessoas")) or (leg is not None and not leg["pessoas"])


def _find(text: str, term: str) -> int:
    """Posição do termo no início de palavra; termo terminado em espaço = palavra inteira."""
    pattern = r"(?<![a-z0-9])" + re.escape(_fold(term.strip()))
    if term.endswith(" "):
        pattern += r"(?![a-z0-9])"
    m = re.search(pattern, _fold(text))
    return m.start() if m else -1


def _fold(text: str) -> str:
    """Minúsculas e hífen como espaço (`close-up` = `close up`, `high-angle` = `high angle`)."""
    return text.lower().replace("-", " ")


def case_input(
    case: dict[str, Any], version: int | None = None, receitas: dict[str, Any] | None = None
) -> tuple[str, str]:
    """(entrada do compilador, sufixo dos presets). Caso com `receita`: a do caminho do serviço."""
    if case.get("receita"):
        c = traducao.entrada(
            receitas or lote.load_receitas(),
            case["receita"],
            case["text"],
            sem_pessoas=_sem_pessoas(case),
            versao=version or compiler.PROFILES[case["profile"]]["version"],
            legenda=case.get("legenda"),
        )
        return c.text, c.suffix
    if case.get("presets") is not None:
        c = presets.compose(case["profile"], case["text"], case["presets"])
        return c.text, c.suffix
    return case["text"], ""


def check(
    case: dict[str, Any],
    text_in: str,
    output: str,
    refused: bool,
    words: tuple[int, int],
    compiled_words: int | None = None,
) -> tuple[dict[str, bool], list[str]]:
    """Confere a saída final. `compiled_words`: palavras do trecho do LLM (sem presets)."""
    checks: dict[str, bool] = {}
    failures: list[str] = []

    def record(name: str, ok: bool, why: str) -> None:
        checks[name] = ok
        if not ok:
            failures.append(why)

    expect_refused = bool(case.get("expect_refused", False))
    record(
        "refusal",
        refused == expect_refused,
        "recusou sem motivo" if refused else "não recusou (esperado REFUSED)",
    )
    if refused or expect_refused:
        return checks, failures

    for i, group in enumerate(case.get("must", [])):
        group = [group] if isinstance(group, str) else group
        record(f"must[{i}]", any(_find(output, t) >= 0 for t in group), f"faltou {group}")
    hits = [t for t in case.get("forbid", []) if _find(output, t) >= 0]
    record("forbid", not hits, f"inventou ou traduziu errado: {hits}")
    if case.get("order"):
        positions = []
        for group in case["order"]:
            group = [group] if isinstance(group, str) else group
            found = [p for p in (_find(output, t) for t in group) if p >= 0]
            positions.append(min(found) if found else -1)
        ok = all(p >= 0 for p in positions) and positions == sorted(positions)
        record("order", ok, f"ordem das ações trocada ou incompleta: {case['order']}")
    # caso com receita: só as aspas do cliente (os termos dos presets vêm do código, e o movimento
    # padrão troca os do estilo pelos da categoria)
    quoted = QUOTED.findall(case["text"] if case.get("receita") else text_in)
    if quoted:
        missing = [q for q in quoted if q.lower() not in output.lower()]
        record("quoted", not missing, f"texto entre aspas alterado: {missing}")
    if case.get("receita") and _sem_pessoas(case):  # foto sem pessoas e o pedido não cita ninguém
        hits = traducao.prompt_cita_pessoa(output)
        record("pessoa", not hits, f"pessoa inventada no prompt: {hits}")
    n = len(output.split()) if compiled_words is None else compiled_words
    low, high = words
    record("words", low <= n <= high, f"{n} palavras (faixa {low}-{high})")
    return checks, failures


def run_case(
    case: dict[str, Any],
    *,
    model: str,
    ollama_url: str,
    version: int | None,
    seed: int,
    keep_alive: str | int = "30m",
) -> CaseResult:
    profile = case["profile"]
    spec = compiler.PROFILES[profile]
    version = version or spec["version"]
    text_in, suffix = case_input(case, version)
    extra: dict[str, Any] = {}
    try:
        if case.get("receita"):  # o caminho do serviço (render2go_worker.traducao)
            tr = traducao.traduzir(
                case["text"],
                case["receita"],
                receitas=lote.load_receitas(),
                categoria=case.get("categoria"),
                sem_pessoas=bool(case.get("sem_pessoas")),
                legenda=case.get("legenda"),
                version=version,
                seed=seed,
                ollama_url=ollama_url,
                model=model,
                keep_alive=keep_alive,
            )
            output, refused, seconds, n = tr.prompt, False, tr.seconds, tr.word_count
            extra = {
                "vago": tr.vago,
                "padrao": tr.padrao,
                "sem_pessoas": tr.sem_pessoas,
                "rejeitados": list(tr.rejeitados),
            }
        else:
            r = compiler.compile_prompt(
                text_in,
                profile=profile,
                ollama_url=ollama_url,
                model=model,
                keep_alive=keep_alive,
                version=version,
                seed=seed,
                suffix=suffix,
                camera_preset=bool((case.get("presets") or {}).get("camera")),
            )
            output, refused, seconds, n = r.prompt, False, r.seconds, r.word_count
            extra = {"vago": r.vague, "padrao": r.vague}
    except compiler.CompilerRefused:
        output, refused, seconds, n = "REFUSED", True, 0.0, 0
    except compiler.CompilerOutputError as e:
        output, refused, seconds, n = f"<{e}>", False, 0.0, 0
    words = compiler.word_range(profile, version)
    checks, failures = check(case, text_in, output, refused, words, n)
    if output.startswith("<"):
        checks["format"] = False
        failures.insert(0, output.strip("<>"))
    return CaseResult(
        case_id=case["id"],
        profile=profile,
        version=version,
        model=model,
        seed=seed,
        gating=bool(case.get("gating", True)) and not case.get("expect_refused", False),
        input=text_in,
        output=output,
        refused=refused,
        seconds=round(seconds, 2),
        words=n,
        checks=checks,
        failures=failures,
        **extra,
    )


def summarize(results: list[CaseResult], thresholds: dict[str, float]) -> dict[str, Any]:
    gating = [r for r in results if r.gating]
    info = [r for r in results if not r.gating]
    by_check: dict[str, list[bool]] = defaultdict(list)
    for r in gating:
        for name, ok in r.checks.items():
            by_check[name.split("[", 1)[0]].append(ok)
    rate = {k: round(sum(v) / len(v), 3) for k, v in sorted(by_check.items())}
    case_rate = round(sum(r.passed for r in gating) / len(gating), 3) if gating else 0.0
    secs = [r.seconds for r in results if r.seconds]
    gates = {
        "case_pass_rate": case_rate >= thresholds.get("case_pass_rate", 1.0),
        "quoted": rate.get("quoted", 1.0) >= thresholds.get("quoted", 1.0),
        "forbid": rate.get("forbid", 1.0) >= thresholds.get("forbid", 1.0),
    }
    sem_pessoas = [r for r in gating if r.sem_pessoas]
    por_caso: dict[str, list[bool]] = defaultdict(list)
    for r in info:
        por_caso[r.case_id].append(r.refused)
    return {
        "runs": len(results),
        "gating_runs": len(gating),
        "case_pass_rate": case_rate,
        "check_pass_rate": rate,
        "refusal_info": {
            "runs": len(info),
            "refused": sum(r.refused for r in info),
            "by_case": {k: sum(v) for k, v in sorted(por_caso.items())},
        },
        # caminho do serviço (casos com receita): pedido vago, movimento padrão e pessoa inventada
        # pelo próprio tradutor (antes da regra: alguma tradução recusada por citar pessoa)
        "service": {
            "vague": sum(r.vago for r in gating),
            "default_motion": sum(r.padrao for r in gating),
            "no_people_runs": len(sem_pessoas),
            "invented_person_raw": sum(bool(r.rejeitados) for r in sem_pessoas),
            "invented_person_final": sum(not r.checks.get("pessoa", True) for r in sem_pessoas),
        },
        "median_seconds": round(statistics.median(secs), 2) if secs else None,
        "thresholds": thresholds,
        "gates": gates,
        "passed": all(gates.values()),
    }


def load_cases(path: Path = CASES_FILE) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    ids = [c["id"] for c in data["cases"]]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ValueError(f"ids repetidos: {sorted(dup)}")
    for c in data["cases"]:
        if c["profile"] not in compiler.PROFILES:
            raise ValueError(f"{c['id']}: perfil desconhecido {c['profile']!r}")
        case_input(c)  # presets válidos
    return data


def to_json(results: list[CaseResult]) -> list[dict[str, Any]]:
    return [asdict(r) | {"passed": r.passed} for r in results]
