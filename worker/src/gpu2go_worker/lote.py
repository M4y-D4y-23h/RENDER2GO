"""Geração em lote para o serviço de divulgação (docs/05, caminho A).

Entrada: uma pasta com as fotos do cliente e uma planilha `pedidos.csv` (separador `,` ou
`;`), uma linha por foto:

    foto,receita,acao,formato,duracao,variacoes,consentimento
    tenis.jpg,produto-giro,,,,2,
    sala.jpg,imovel-sala,,horizontal,6,1,
    dona.jpg,pessoa-apresentacao,,,,1,sim

Só `foto` e `receita` são obrigatórias; as outras colunas vazias usam o padrão da receita
(`presets/receitas.v1.json`). Receitas com pessoa exigem `consentimento=sim`: a foto é da
própria pessoa, que autorizou por escrito o uso em vídeo.

Saída: um MP4 por variação e o `relatorio.csv` na pasta de saída. A planilha inteira é
conferida antes do primeiro vídeo (nenhum minuto de GPU com pedido inválido). Rodar de
novo continua de onde parou: vídeos já prontos são pulados. A seed de cada variação sai
do nome da foto, da receita e do número da variação, então refazer dá o mesmo vídeo.

Roda contra o ComfyUI e o Ollama do pod (modo pod, ver worker/README.md).

Códigos de saída: 0 tudo pronto · 1 algum vídeo falhou · 2 planilha inválida ·
5 serviço inacessível (o lote para; rode de novo depois).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from argparse import Namespace
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from gpu2go_worker import WORKER_DIR, cli_submit, compiler, presets

RECEITAS_FILE = WORKER_DIR / "presets" / "receitas.v1.json"
I2V_TEMPLATE = WORKER_DIR / "workflows" / "i2v_r1v2.v1.api.json"
FOTOS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_VARIACOES = 4
DURACOES = range(3, 8)
RELATORIO = "relatorio.csv"
COLUNAS_RELATORIO = [
    "arquivo",
    "linha",
    "foto",
    "receita",
    "variacao",
    "formato",
    "duracao",
    "seed",
    "status",
    "segundos",
    "prompt_en",
    "erro",
]
EXIT_OK, EXIT_FALHA, EXIT_PLANILHA, EXIT_INACESSIVEL = 0, 1, 2, 5

Submit = Callable[[Namespace], tuple[int, dict]]


class LoteError(Exception):
    pass


@lru_cache(maxsize=2)
def load_receitas(path: str | Path = RECEITAS_FILE) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    preset_data = presets.load()
    if data["presets_version"] != preset_data["version"]:
        raise LoteError(
            f"receitas feitas para presets v{data['presets_version']}, "
            f"carregados v{preset_data['version']}"
        )
    for nome, r in data["receitas"].items():
        if r["formato"] not in data["formatos"] or r["duracao"] not in DURACOES:
            raise LoteError(f"receita {nome}: formato ou duração inválidos")
        try:
            presets.compose(r["modo"], r["acao"], r["presets"], preset_data)
        except presets.PresetError as e:
            raise LoteError(f"receita {nome}: {e}") from e
    for nome, f in data["formatos"].items():
        if f["width"] % 16 or f["height"] % 16:
            raise LoteError(f"formato {nome}: largura e altura precisam ser múltiplas de 16")
    return data


@dataclass(frozen=True)
class Tarefa:
    linha: int
    foto: Path
    receita: str
    variacao: int
    acao: str
    selections: dict[str, str]
    formato: str
    width: int
    height: int
    duracao: int
    seed: int

    @property
    def arquivo(self) -> str:
        return f"{self.linha:03d}-{self.foto.stem}-{self.receita}-v{self.variacao}.mp4"


@dataclass
class Plano:
    tarefas: list[Tarefa] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)


def seed_de(foto: str, receita: str, variacao: int) -> int:
    h = hashlib.sha256(f"{foto}|{receita}|{variacao}".encode()).digest()
    return int.from_bytes(h[:8], "big") % (cli_submit.SEED_MAX + 1)


def _ler_csv(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig")  # aceita o BOM do Excel
    header = text.splitlines()[0] if text else ""
    sep = ";" if ";" in header and "," not in header else ","  # Excel em pt-BR salva com ;
    rows = list(csv.DictReader(text.splitlines(), delimiter=sep))
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()} for row in rows]


def planejar(pasta: Path, pedidos: Path, receitas: dict[str, Any] | None = None) -> Plano:
    """Confere a planilha inteira e devolve as tarefas, ou a lista de erros."""
    receitas = receitas or load_receitas()
    plano = Plano()
    try:
        rows = _ler_csv(pedidos)
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        plano.erros.append(f"{pedidos}: {e}")
        return plano
    if not rows:
        plano.erros.append(f"{pedidos}: nenhuma linha de pedido")
        return plano
    faltando = {"foto", "receita"} - set(rows[0])
    if faltando:
        plano.erros.append(f"{pedidos}: faltam as colunas {sorted(faltando)}")
        return plano

    base = pasta.resolve()
    for n, row in enumerate(rows, start=2):  # linha 1 é o cabeçalho
        erros: list[str] = []
        foto = (base / row["foto"]).resolve()
        if not row["foto"] or base not in foto.parents:
            erros.append(f"foto {row['foto']!r} fora da pasta")
        elif foto.suffix.lower() not in FOTOS:
            erros.append(f"foto {row['foto']!r}: use {', '.join(sorted(FOTOS))}")
        elif not foto.is_file():
            erros.append(f"foto {row['foto']!r} não encontrada")
        r = receitas["receitas"].get(row["receita"])
        if r is None:
            erros.append(f"receita desconhecida {row['receita']!r}")
            plano.erros += [f"linha {n}: {e}" for e in erros]
            continue
        if r["modo"] != "i2v":
            erros.append(f"a receita {row['receita']} não é de foto (i2v)")
        if r.get("exige_consentimento") and row.get("consentimento", "").lower() != "sim":
            erros.append(f"a receita {row['receita']} exige consentimento=sim")
        formato = row.get("formato") or r["formato"]
        if formato not in receitas["formatos"]:
            erros.append(f"formato {formato!r}: use {', '.join(receitas['formatos'])}")
        try:
            duracao = int(row.get("duracao") or r["duracao"])
            variacoes = int(row.get("variacoes") or 1)
        except ValueError:
            erros.append("duracao e variacoes precisam ser números inteiros")
            duracao = variacoes = 0
        if duracao and duracao not in DURACOES:
            erros.append(f"duracao {duracao}: de 3 a 7 segundos")
        if variacoes and not 1 <= variacoes <= MAX_VARIACOES:
            erros.append(f"variacoes {variacoes}: de 1 a {MAX_VARIACOES}")
        acao = row.get("acao") or r["acao"]
        try:
            presets.compose(r["modo"], acao, r["presets"])
        except presets.PresetError as e:
            erros.append(str(e))
        if erros:
            plano.erros += [f"linha {n}: {e}" for e in erros]
            continue
        fmt = receitas["formatos"][formato]
        for k in range(1, variacoes + 1):
            plano.tarefas.append(
                Tarefa(
                    linha=n,
                    foto=foto,
                    receita=row["receita"],
                    variacao=k,
                    acao=acao,
                    selections=dict(r["presets"]),
                    formato=formato,
                    width=fmt["width"],
                    height=fmt["height"],
                    duracao=duracao,
                    seed=seed_de(row["foto"], row["receita"], k),
                )
            )
    return plano


def _ler_relatorio(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {row["arquivo"]: row for row in csv.DictReader(f)}


def _gravar_relatorio(path: Path, rows: dict[str, dict[str, Any]]) -> None:
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS_RELATORIO, extrasaction="ignore")
        w.writeheader()
        for arquivo in sorted(rows):
            w.writerow(rows[arquivo])
    tmp.replace(path)


def _args(t: Tarefa, out: Path, a: Namespace) -> Namespace:
    return cli_submit.build_parser().parse_args(
        [
            "--comfy",
            a.comfy,
            "--ollama",
            a.ollama,
            "--template",
            str(a.template),
            "--image",
            str(t.foto),
            "--text-pt",
            t.acao,
            *[x for cat, opt in t.selections.items() for x in ("--preset", f"{cat}={opt}")],
            "--profile",
            "i2v",
            "--model",
            a.model,
            "--keep-alive",
            a.keep_alive,
            "--width",
            str(t.width),
            "--height",
            str(t.height),
            "--duration",
            str(t.duracao),
            "--seed",
            str(t.seed),
            "--out",
            str(out),
            "--timeout",
            str(a.timeout),
        ]
    )


def executar(a: Namespace, submit: Submit = cli_submit.run) -> int:
    plano = planejar(a.pasta, a.pedidos)
    if plano.erros:
        for e in plano.erros:
            _log(f"ERRO {e}")
        _log(f"planilha inválida: {len(plano.erros)} erro(s); nenhum vídeo foi gerado")
        return EXIT_PLANILHA
    a.saida.mkdir(parents=True, exist_ok=True)
    rel_path = a.saida / RELATORIO
    relatorio = _ler_relatorio(rel_path)
    total = len(plano.tarefas)
    _log(f"{total} vídeo(s) planejado(s) em {a.saida}")
    if a.conferir:
        for t in plano.tarefas:
            _log(f"  {t.arquivo}: {t.formato} {t.width}x{t.height}, {t.duracao} s, seed {t.seed}")
        return EXIT_OK

    falhas = 0
    for i, t in enumerate(plano.tarefas, start=1):
        destino = a.saida / t.arquivo
        if destino.is_file() and relatorio.get(t.arquivo, {}).get("status") == "ok":
            _log(f"[{i}/{total}] {t.arquivo}: já pronto")
            continue
        _log(f"[{i}/{total}] {t.arquivo}")
        parcial = destino.with_name(destino.stem + ".parcial.mp4")
        t0 = time.monotonic()
        code, res = submit(_args(t, parcial, a))
        row: dict[str, Any] = {
            "arquivo": t.arquivo,
            "linha": t.linha,
            "foto": t.foto.name,
            "receita": t.receita,
            "variacao": t.variacao,
            "formato": t.formato,
            "duracao": t.duracao,
            "seed": t.seed,
            "segundos": round(time.monotonic() - t0, 1),
            "prompt_en": res.get("prompt_en", ""),
            "erro": "",
        }
        if code == cli_submit.EXIT_OK:
            parcial.replace(destino)
            row["status"] = "ok"
        else:
            parcial.unlink(missing_ok=True)
            row["status"] = "recusado" if code == cli_submit.EXIT_REFUSED else "erro"
            row["erro"] = str(res.get("error", ""))[:300]
            falhas += 1
            _log(f"  falhou: {row['erro']}")
        relatorio[t.arquivo] = row
        _gravar_relatorio(rel_path, relatorio)
        if code == cli_submit.EXIT_UNREACHABLE:
            _log("serviço inacessível: lote interrompido; rode de novo para continuar")
            return EXIT_INACESSIVEL
    _log(f"pronto: {total - falhas} ok, {falhas} com falha; relatório em {rel_path}")
    return EXIT_FALHA if falhas else EXIT_OK


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gpu2go-lote",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n\n".join(__doc__.split("\n\n")[1:]),
    )
    p.add_argument("pasta", type=Path, help="pasta com as fotos do cliente")
    p.add_argument("--pedidos", type=Path, help="planilha; padrão: <pasta>/pedidos.csv")
    p.add_argument("--saida", type=Path, required=True, help="pasta dos vídeos e do relatório")
    p.add_argument(
        "--conferir", action="store_true", help="só confere a planilha e lista os vídeos"
    )
    p.add_argument("--comfy", default="http://127.0.0.1:8188", help="URL do ComfyUI")
    p.add_argument("--ollama", default="http://127.0.0.1:11434", help="URL do Ollama")
    p.add_argument("--template", type=Path, default=I2V_TEMPLATE, help="template I2V")
    p.add_argument("--model", default=compiler.DEFAULT_MODEL, help="modelo do compilador")
    p.add_argument(
        "--keep-alive",
        default="0",
        help="keep_alive do Ollama; 0 libera a VRAM para o render (regra do projeto)",
    )
    p.add_argument("--timeout", type=float, default=1800.0, help="limite por vídeo, em segundos")
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    a.pedidos = a.pedidos or a.pasta / "pedidos.csv"
    try:
        return executar(a)
    except LoteError as e:
        _log(f"ERRO {e}")
        return EXIT_PLANILHA


if __name__ == "__main__":
    sys.exit(main())
