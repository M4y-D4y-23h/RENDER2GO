"""Geração em lote: uma pasta de fotos, um pedido, um vídeo por foto, em várias GPUs.

Fluxo principal (lotes de 50+ fotos, docs/05):

    render2go-lote fotos/loja --saida saida/loja \\
        --prompt "a câmera se aproxima devagar do produto" --receita produto-close \\
        --comfy http://127.0.0.1:18188 --comfy http://127.0.0.1:28188

- Todas as fotos da pasta (`--subpastas` inclui as subpastas) recebem o mesmo pedido.
- O pedido é compilado **uma vez** para o lote inteiro, antes do primeiro vídeo. O Ollama
  libera a VRAM e as GPUs só renderizam.
- O formato sai da proporção de cada foto (`--formato auto`): vertical, quadrado ou
  horizontal. Assim o corte é o menor possível.
- `--qualidade rapida` (padrão, medida) ou `alta` (área máxima do contrato, ainda sem
  medição de tempo e de VRAM).
- Cada `--comfy` é uma GPU (pod, pelo túnel SSH: o ComfyUI só escuta em 127.0.0.1).
  Num pod com várias GPUs (`COMFY_GPUS=auto` no entrypoint), `--gpus N` usa as N.
  Paralelismo de verdade é uma GPU por vídeo: dois vídeos na mesma GPU não saem mais
  rápido (medido em 06/10: fila 1 e fila 2 deram ~80 s por vídeo). `--fila 2` só ajuda
  quando o envio e o download são lentos (túnel, rede distante).

Planilha (opcional, para pedidos diferentes por foto): `pedidos.csv` com as colunas
foto, receita, acao, formato, duracao, variacoes, consentimento (só `foto` e `receita`
obrigatórias; separador `,` ou `;`). Pedidos iguais são compilados uma vez só.

Tudo é conferido antes do primeiro vídeo (nenhum minuto de GPU com pedido inválido).
Rodar de novo continua de onde parou. O prompt compilado fica em `lote.json` e é
reaproveitado. A seed vem da foto e da variação, então refazer dá o mesmo vídeo.
Receitas com pessoa exigem consentimento (foto da própria pessoa, com autorização dela
por escrito).

Códigos de saída: 0 tudo pronto · 1 algum vídeo falhou · 2 pedido inválido ·
3 recusado pelo compilador · 5 serviço inacessível (rode de novo para continuar).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import queue
import subprocess
import sys
import threading
import time
from argparse import Namespace
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from render2go_worker import WORKER_DIR, cli_submit, compiler, preparo, presets

RECEITAS_FILE = WORKER_DIR / "presets" / "receitas.v1.json"
I2V_TEMPLATE = WORKER_DIR / "workflows" / "i2v_r1v2.v1.api.json"
FOTOS = {".jpg", ".jpeg", ".png", ".webp"}
QUALIDADES = ("rapida", "alta")
MAX_VARIACOES = 4
MAX_FOTOS = 1000
DURACOES = range(3, 8)
RELATORIO = "relatorio.csv"
ESTADO = "lote.json"
# proporção largura/altura: abaixo de VERTICAL é vertical, acima de HORIZONTAL é horizontal
VERTICAL, HORIZONTAL = 0.8, 1.25
EXIF_ORIENTATION, GIRADAS = 0x0112, {5, 6, 7, 8}  # o ComfyUI aplica a rotação do EXIF
COLUNAS_RELATORIO = [
    "arquivo",
    "foto",
    "receita",
    "variacao",
    "formato",
    "width",
    "height",
    "duracao",
    "seed",
    "gpu",
    "status",
    "segundos",
    "aviso",
    "erro",
]
EXIT_OK, EXIT_FALHA, EXIT_PEDIDO, EXIT_RECUSADO, EXIT_INACESSIVEL = 0, 1, 2, 3, 5

Submit = Callable[[Namespace], tuple[int, dict]]
Compile = Callable[..., compiler.CompileResult]


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
        if r["modo"] != "i2v" or r["formato"] not in data["formatos"]:
            raise LoteError(f"receita {nome}: modo ou formato inválidos")
        if r["duracao"] not in DURACOES:
            raise LoteError(f"receita {nome}: duração inválida")
        try:
            presets.compose(r["modo"], r["acao"], r["presets"], preset_data)
        except presets.PresetError as e:
            raise LoteError(f"receita {nome}: {e}") from e
    for nome, f in data["formatos"].items():
        for q in QUALIDADES:
            if f[q]["width"] % 16 or f[q]["height"] % 16:
                raise LoteError(f"formato {nome}/{q}: lados precisam ser múltiplos de 16")
    return data


@dataclass(frozen=True)
class Tarefa:
    arquivo: str
    foto: Path
    receita: str
    variacao: int
    acao: str
    selections: tuple[tuple[str, str], ...]
    formato: str
    width: int
    height: int
    duracao: int
    seed: int

    @property
    def pedido(self) -> str:
        """Chave do pedido compilado: tarefas com a mesma chave usam o mesmo prompt."""
        raw = json.dumps([self.acao, list(self.selections)], ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class Plano:
    tarefas: list[Tarefa] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)


def seed_de(*partes: object) -> int:
    h = hashlib.sha256("|".join(map(str, partes)).encode()).digest()
    return int.from_bytes(h[:8], "big") % (cli_submit.SEED_MAX + 1)


def proporcao(foto: Path) -> float:
    """Largura/altura como o ComfyUI vê a foto (com a rotação do EXIF aplicada)."""
    try:
        with Image.open(foto) as im:
            w, h = im.size
            if im.getexif().get(EXIF_ORIENTATION) in GIRADAS:
                w, h = h, w
    except (OSError, UnidentifiedImageError) as e:
        raise LoteError(f"imagem ilegível: {e}") from e
    return w / h


def formato_auto(foto: Path) -> str:
    r = proporcao(foto)
    return "vertical" if r < VERTICAL else "horizontal" if r > HORIZONTAL else "quadrado"


@dataclass(frozen=True)
class Pedido:
    """O que vale para uma foto: receita, ação e ajustes (vazios = padrão da receita)."""

    receita: str
    acao: str = ""
    selections: dict[str, str] = field(default_factory=dict)
    formato: str = ""
    duracao: int = 0
    variacoes: int = 1
    consentimento: bool = False


def _tarefas(
    foto: Path, nome: str, p: Pedido, qualidade: str, receitas: dict[str, Any]
) -> tuple[list[Tarefa], list[str]]:
    erros: list[str] = []
    if foto.suffix.lower() not in FOTOS:
        return [], [f"use {', '.join(sorted(FOTOS))}"]
    if not foto.is_file():
        return [], ["foto não encontrada"]
    r = receitas["receitas"].get(p.receita)
    if r is None:
        return [], [f"receita desconhecida {p.receita!r}"]
    if r.get("exige_consentimento") and not p.consentimento:
        erros.append(f"a receita {p.receita} exige consentimento")
    selections = {**r["presets"], **p.selections}
    acao = p.acao or r["acao"]
    try:
        presets.compose(r["modo"], acao, selections)
    except presets.PresetError as e:
        erros.append(str(e))
    duracao = p.duracao or r["duracao"]
    if duracao not in DURACOES:
        erros.append(f"duracao {duracao}: de 3 a 7 segundos")
    if not 1 <= p.variacoes <= MAX_VARIACOES:
        erros.append(f"variacoes {p.variacoes}: de 1 a {MAX_VARIACOES}")
    formato = p.formato or r["formato"]
    if formato == "auto":
        try:
            formato = formato_auto(foto)
        except LoteError as e:
            erros.append(str(e))
    elif formato not in receitas["formatos"]:
        erros.append(f"formato {formato!r}: use auto, {', '.join(receitas['formatos'])}")
    if erros:
        return [], erros
    dims = receitas["formatos"][formato][qualidade]
    return [
        Tarefa(
            arquivo=f"{nome}-v{k}.mp4",
            foto=foto,
            receita=p.receita,
            variacao=k,
            acao=acao,
            selections=tuple(sorted(selections.items())),
            formato=formato,
            width=dims["width"],
            height=dims["height"],
            duracao=duracao,
            seed=seed_de(nome, k),
        )
        for k in range(1, p.variacoes + 1)
    ], []


def _nomes(fotos: list[Path], base: Path) -> dict[Path, str]:
    """Nome de saída estável por foto (caminho relativo; extensão só se houver colisão)."""
    rel = {f: f.relative_to(base).with_suffix("").as_posix().replace("/", "__") for f in fotos}
    contagem: dict[str, int] = {}
    for n in rel.values():
        contagem[n] = contagem.get(n, 0) + 1
    return {f: n if contagem[n] == 1 else f"{n}-{f.suffix[1:].lower()}" for f, n in rel.items()}


def planejar_pasta(pasta: Path, pedido: Pedido, qualidade: str, subpastas: bool = False) -> Plano:
    receitas = load_receitas()
    plano = Plano()
    base = pasta.resolve()
    if not base.is_dir():
        plano.erros.append(f"pasta inexistente: {pasta}")
        return plano
    padrao = "**/*" if subpastas else "*"
    fotos = sorted(f for f in base.glob(padrao) if f.is_file() and f.suffix.lower() in FOTOS)
    if not fotos:
        plano.erros.append(f"nenhuma foto ({', '.join(sorted(FOTOS))}) em {pasta}")
    if len(fotos) > MAX_FOTOS:
        plano.erros.append(f"{len(fotos)} fotos: no máximo {MAX_FOTOS} por lote")
        return plano
    for foto, nome in _nomes(fotos, base).items():
        tarefas, erros = _tarefas(foto, nome, pedido, qualidade, receitas)
        plano.tarefas += tarefas
        plano.erros += [f"{foto.relative_to(base)}: {e}" for e in erros]
    return plano


def _ler_csv(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig")  # aceita o BOM do Excel
    header = text.splitlines()[0] if text else ""
    sep = ";" if ";" in header and "," not in header else ","  # Excel em pt-BR salva com ;
    rows = list(csv.DictReader(text.splitlines(), delimiter=sep))
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in rows]


def planejar_planilha(pasta: Path, pedidos: Path, qualidade: str) -> Plano:
    receitas = load_receitas()
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
        foto = (base / row["foto"]).resolve()
        if not row["foto"] or base not in foto.parents:
            plano.erros.append(f"linha {n}: foto {row['foto']!r} fora da pasta")
            continue
        try:
            pedido = Pedido(
                receita=row["receita"],
                acao=row.get("acao", ""),
                formato=row.get("formato", ""),
                duracao=int(row.get("duracao") or 0),
                variacoes=int(row.get("variacoes") or 1),
                consentimento=row.get("consentimento", "").lower() == "sim",
            )
        except ValueError:
            plano.erros.append(f"linha {n}: duracao e variacoes precisam ser números inteiros")
            continue
        nome = f"{n:03d}-{foto.stem}-{row['receita']}"
        tarefas, erros = _tarefas(foto, nome, pedido, qualidade, receitas)
        plano.tarefas += tarefas
        plano.erros += [f"linha {n}: foto {row['foto']!r}: {e}" for e in erros]
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


def compilar(
    tarefas: list[Tarefa], estado: dict[str, Any], a: Namespace, compile_fn: Compile
) -> dict[str, str]:
    """Um prompt por pedido distinto, reaproveitando o que já está em lote.json."""
    prompts: dict[str, str] = {}
    feitos = estado.setdefault("prompts", {})
    for t in tarefas:
        if t.pedido in prompts:
            continue
        if t.pedido in feitos:
            prompts[t.pedido] = feitos[t.pedido]["prompt_en"]
            continue
        composed = presets.compose("i2v", t.acao, dict(t.selections))
        _log(f"compilando o pedido: {t.acao}")
        comp = compile_fn(
            composed.text,
            profile="i2v",
            ollama_url=a.ollama,
            model=a.model,
            keep_alive=0,  # libera a VRAM do Ollama antes dos renders
            suffix=composed.suffix,
            camera_preset=composed.camera,
        )
        _log(f"  prompt ({comp.word_count} palavras): {comp.prompt}")
        feitos[t.pedido] = {
            "acao": t.acao,
            "presets": {"version": composed.version, **dict(t.selections)},
            "prompt_en": comp.prompt,
            "compilador": comp.ref,
        }
        prompts[t.pedido] = comp.prompt
    return prompts


def _args(
    t: Tarefa, prompt_en: str, out: Path, comfy: str, a: Namespace, foto: Path | None = None
) -> Namespace:
    return cli_submit.build_parser().parse_args(
        [
            "--comfy",
            comfy,
            "--template",
            str(a.template),
            "--image",
            str(foto or t.foto),
            "--prompt-en",
            prompt_en,
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


class Execucao:
    """Distribui as tarefas entre as GPUs, `fila` vídeos de cada vez por GPU."""

    def __init__(self, a: Namespace, prompts: dict[str, str], relatorio, rel_path, submit):
        self.a, self.prompts, self.submit = a, prompts, submit
        self.relatorio, self.rel_path = relatorio, rel_path
        self.fila: queue.Queue[Tarefa] = queue.Queue()
        self.lock = threading.Lock()
        self.total = self.feitos = self.falhas = 0
        self.inacessiveis: set[str] = set()

    def _registrar(
        self, t: Tarefa, gpu: str, code: int, res: dict, segundos: float, aviso: str = ""
    ) -> None:
        ok = code == cli_submit.EXIT_OK
        row: dict[str, Any] = {
            "arquivo": t.arquivo,
            "foto": t.foto.name,
            "receita": t.receita,
            "variacao": t.variacao,
            "formato": t.formato,
            "width": t.width,
            "height": t.height,
            "duracao": t.duracao,
            "seed": t.seed,
            "gpu": gpu,
            "status": "ok" if ok else "erro",
            "segundos": round(segundos, 1),
            "aviso": aviso,
            "erro": "" if ok else str(res.get("error", ""))[:300],
        }
        with self.lock:
            self.relatorio[t.arquivo] = row
            _gravar_relatorio(self.rel_path, self.relatorio)
            self.feitos += 1
            self.falhas += not ok
            progresso = f"[{self.feitos}/{self.total}] {t.arquivo}"
            _log(f"{progresso}: {row['status']} ({gpu}, {segundos:.0f} s)")
            if not ok:
                _log(f"  {row['erro']}")
            if aviso:
                _log(f"  aviso: {aviso}")

    def _trabalhar(self, gpu: str) -> None:
        while gpu not in self.inacessiveis:
            try:
                t = self.fila.get_nowait()
            except queue.Empty:
                return
            destino = self.a.saida / t.arquivo
            parcial = destino.with_name(f"{destino.stem}.parcial.mp4")
            t0 = time.monotonic()
            foto, aviso = t.foto, ""
            if self.a.preparo:
                try:
                    prep = preparo.preparar(
                        t.foto, t.width, t.height, self.a.saida / ".preparo" / f"{destino.stem}.png"
                    )
                except preparo.PreparoError as e:
                    self._registrar(t, gpu, cli_submit.EXIT_PARAM, {"error": str(e)}, 0.0)
                    continue
                foto, aviso = prep.path, prep.aviso
            code, res = self.submit(_args(t, self.prompts[t.pedido], parcial, gpu, self.a, foto))
            if code == cli_submit.EXIT_UNREACHABLE:
                parcial.unlink(missing_ok=True)
                self.fila.put(t)  # outra GPU pega
                with self.lock:
                    if gpu not in self.inacessiveis:
                        self.inacessiveis.add(gpu)
                        _log(f"GPU inacessível, fora do lote: {gpu} ({res.get('error')})")
                return
            if code == cli_submit.EXIT_OK:
                parcial.replace(destino)
                if self.a.conferencia:
                    try:
                        preparo.folha(
                            foto,
                            destino,
                            self.a.saida / "conferencia" / f"{destino.stem}.jpg",
                            16 * t.duracao + 1,
                        )
                    except (OSError, subprocess.CalledProcessError) as e:
                        aviso = f"{aviso}; sem folha de conferência ({e})".lstrip("; ")
            else:
                parcial.unlink(missing_ok=True)
            self._registrar(t, gpu, code, res, time.monotonic() - t0, aviso)

    def rodar(self, tarefas: list[Tarefa]) -> int:
        for t in tarefas:
            self.fila.put(t)
        self.total = len(tarefas)
        threads = [
            threading.Thread(target=self._trabalhar, args=(gpu,), daemon=True)
            for gpu in self.a.comfy
            for _ in range(self.a.fila)
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        if not self.fila.empty():
            _log(f"{self.fila.qsize()} vídeo(s) pendente(s): nenhuma GPU acessível")
            return EXIT_INACESSIVEL
        return EXIT_FALHA if self.falhas else EXIT_OK


def planejar(a: Namespace) -> Plano:
    if a.prompt is None:
        return planejar_planilha(a.pasta, a.pedidos, a.qualidade)
    pedido = Pedido(
        receita=a.receita,
        acao=a.prompt,
        selections=presets.parse_selections(a.preset),
        formato=a.formato,
        duracao=a.duracao or 0,
        variacoes=a.variacoes,
        consentimento=a.consentimento,
    )
    return planejar_pasta(a.pasta, pedido, a.qualidade, a.subpastas)


def executar(
    a: Namespace, submit: Submit = cli_submit.run, compile_fn: Compile = compiler.compile_prompt
) -> int:
    plano = planejar(a)
    if plano.erros:
        for e in plano.erros:
            _log(f"ERRO {e}")
        _log(f"pedido inválido: {len(plano.erros)} erro(s); nenhum vídeo foi gerado")
        return EXIT_PEDIDO

    total = len(plano.tarefas)
    formatos = sorted({f"{t.formato} {t.width}x{t.height}" for t in plano.tarefas})
    _log(f"{total} vídeo(s) em {len(a.comfy)} GPU(s) ({', '.join(formatos)}) → {a.saida}")
    if a.conferir:
        for t in plano.tarefas:
            _log(f"  {t.arquivo}: {t.formato} {t.width}x{t.height}, {t.duracao} s, seed {t.seed}")
        return EXIT_OK

    a.saida.mkdir(parents=True, exist_ok=True)
    est_path, rel_path = a.saida / ESTADO, a.saida / RELATORIO
    estado = json.loads(est_path.read_text()) if est_path.is_file() else {}
    try:
        prompts = compilar(plano.tarefas, estado, a, compile_fn)
    except compiler.CompilerRefused as e:
        _log(f"RECUSADO {e}; nenhum vídeo foi gerado")
        return EXIT_RECUSADO
    except compiler.CompilerError as e:
        _log(f"ERRO no compilador: {e}")
        return EXIT_INACESSIVEL if "inacessível" in str(e) else EXIT_PEDIDO
    est_path.write_text(json.dumps(estado, ensure_ascii=False, indent=2) + "\n")
    if a.so_compilar:
        _log(f"prompt(s) em {est_path}")
        return EXIT_OK

    relatorio = _ler_relatorio(rel_path)

    def pronto(t: Tarefa) -> bool:
        feito = relatorio.get(t.arquivo, {}).get("status") == "ok"
        return feito and (a.saida / t.arquivo).is_file()

    pendentes = [t for t in plano.tarefas if not pronto(t)]
    if len(pendentes) < total:
        _log(f"{total - len(pendentes)} vídeo(s) já pronto(s), {len(pendentes)} a gerar")
    t0 = time.monotonic()
    ex = Execucao(a, prompts, relatorio, rel_path, submit)
    code = ex.rodar(pendentes)
    minutos = (time.monotonic() - t0) / 60
    _log(f"fim: {ex.feitos - ex.falhas} ok, {ex.falhas} com falha, {minutos:.1f} min; {rel_path}")
    return code


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="render2go-lote",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n\n".join(__doc__.split("\n\n")[1:]),
    )
    p.add_argument("pasta", type=Path, help="pasta com as fotos")
    p.add_argument("--saida", type=Path, required=True, help="pasta dos vídeos e do relatório")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--prompt", help="pedido em português, o mesmo para todas as fotos")
    src.add_argument("--pedidos", type=Path, help="planilha; padrão: <pasta>/pedidos.csv")
    p.add_argument(
        "--receita",
        default="produto-close",
        help="receita base (presets e duração) do --prompt; padrão: produto-close",
    )
    p.add_argument(
        "--preset",
        action="append",
        default=[],
        metavar="CATEGORIA=OPÇÃO",
        help="troca um preset da receita (repetível), ex.: camera=parada",
    )
    p.add_argument(
        "--formato",
        default="auto",
        help="auto (pela proporção da foto), vertical, quadrado ou horizontal",
    )
    p.add_argument("--qualidade", choices=QUALIDADES, default="rapida")
    p.add_argument("--duracao", type=int, help="segundos (3 a 7); padrão: o da receita")
    p.add_argument("--variacoes", type=int, default=1, help="vídeos por foto (1 a 4)")
    p.add_argument(
        "--consentimento",
        action="store_true",
        help="as pessoas das fotos autorizaram por escrito (receitas com pessoa)",
    )
    p.add_argument("--subpastas", action="store_true", help="inclui as fotos das subpastas")
    p.add_argument(
        "--sem-preparo",
        dest="preparo",
        action="store_false",
        help="envia a foto original (sem corte e redimensionamento Lanczos)",
    )
    p.add_argument(
        "--sem-conferencia",
        dest="conferencia",
        action="store_false",
        help="não gera as folhas de conferência (foto + 3 quadros)",
    )
    p.add_argument("--conferir", action="store_true", help="só confere e lista os vídeos")
    p.add_argument(
        "--so-compilar",
        action="store_true",
        help="só compila o(s) pedido(s) em lote.json, sem renderizar (lote_nuvem)",
    )
    p.add_argument(
        "--comfy",
        action="append",
        help="URL do ComfyUI de cada GPU (repetível); padrão: http://127.0.0.1:8188",
    )
    p.add_argument(
        "--gpus",
        type=int,
        help="pod com N GPUs (COMFY_GPUS=N): usa as portas 8188 a 8188+N-1 deste pod",
    )
    p.add_argument("--fila", type=int, default=1, help="vídeos por GPU ao mesmo tempo (1 a 4)")
    p.add_argument("--ollama", default="http://127.0.0.1:11434", help="URL do Ollama")
    p.add_argument("--template", type=Path, default=I2V_TEMPLATE, help="template I2V")
    p.add_argument("--model", default=compiler.DEFAULT_MODEL, help="modelo do compilador")
    p.add_argument("--timeout", type=float, default=1800.0, help="limite por vídeo, em segundos")
    return p


def parse(argv: list[str] | None = None) -> Namespace:
    p = build_parser()
    a = p.parse_args(argv)
    if a.gpus is not None:
        if a.comfy:
            p.error("use --gpus ou --comfy, não os dois")
        if not 1 <= a.gpus <= 16:
            p.error("--gpus: de 1 a 16")
        a.comfy = [f"http://127.0.0.1:{8188 + i}" for i in range(a.gpus)]
    a.comfy = a.comfy or ["http://127.0.0.1:8188"]
    if len(set(a.comfy)) != len(a.comfy):
        p.error("--comfy repetido")
    if not 1 <= a.fila <= 4:
        p.error("--fila: de 1 a 4")
    if a.prompt is None:
        a.pedidos = a.pedidos or a.pasta / "pedidos.csv"
        if not a.pedidos.is_file():
            p.error(f"use --prompt (o mesmo pedido para todas as fotos) ou crie {a.pedidos}")
    return a


def main(argv: list[str] | None = None) -> int:
    a = parse(argv)
    try:
        return executar(a)
    except (LoteError, presets.PresetError) as e:
        _log(f"ERRO {e}")
        return EXIT_PEDIDO


if __name__ == "__main__":
    sys.exit(main())
