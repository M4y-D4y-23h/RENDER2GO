"""Roda no pod da rodada de qualidade de 13/10/2026 (ADR 0014; venv do worker): as combinações A,
B e D em 480p e 720p e o SageAttention 2.2 na vencedora, com as fotos SFW do plano (`plano.json`)
e seeds fixas. Protocolo, critérios e a lista de "aproveitável": LEIAME.md.

Combinações (ADR 0014, "Critérios"):
- A: a produção depois da Fase 1: o template de produção (R1 v2 + NSFW-UMT5) e os prompts do
  tradutor de produção (abliterado, v6), pelo caminho do serviço (render2go_worker.traducao), com a
  seed fixa por pedido do worker (`fila.semente_do_pedido`).
- B: o template candidato (modelos oficiais + LoRA lightx2v de 4 passos), os mesmos prompts de A.
- D (redefinida antes da rodada): B + a legenda de cada foto só no código (o v6 não a vê). Só os
  vídeos cujo prompt muda são gerados; nos outros, vale o vídeo de B.

Fases (cada uma grava resultado.json ao terminar; a que falha guarda o parcial):
P. prompts: a tradução de cada pedido no Ollama da GPU e a legenda de cada foto no Ollama de CPU,
   com a GPU parada (o tempo da legenda sozinha; a 1ª, com a carga do modelo).
4. 480p: A, B e D, cada grupo depois de um aquecimento; durante D, a legenda das fotos roda de novo
   no Ollama de CPU (o tempo da GPU com ela e o tempo dela com a GPU ocupada).
7. 720p: A e B nos pedidos concretos; durante B, a legenda no Ollama de CPU e uma tradução no
   Ollama da GPU entre os vídeos (o pior caso de memória do pod com a legenda ligada).
S. SageAttention 2.2 (compilado neste pod por preparar_sage.sh): os vídeos 480p da vencedora (B se
   ela não teve mais reprovações automáticas que A em 480p; senão, A) sem e com ele.

Cada vídeo: o tempo do ComfyUI ("Prompt executed in"), o pico de VRAM, a conferência e o filtro de
imagem de produção (conferencia, filtro_imagem), o prompt e se ele cita pessoa, e a tira de quadros
(0, 20, 40, 60 e 80, com a foto preparada antes) para o julgamento às cegas fora do pod.

Um pod vive no máximo 2 h (regra do projeto, `pod_agente.MAX_DEADMAN_S`): a rodada usa três. O 1º
roda P e 4; o 2º, 7; o 3º, S; o 2º e o 3º com os prompts do 1º (`--prompts`, o resultado.json
dele), as mesmas fotos e seeds. Cada comparação fica toda num pod só (a mesma GPU): A × B × D em
480p no 1º, A × B em 720p no 2º, e a vencedora sem e com o SageAttention no 3º.

Uso: python no_pod.py <pasta de saída> [--fases P4] [--prompts resultado.json] [--simular]
(`--simular`: sem GPU, ComfyUI nem Ollama, com geradores falsos, para conferir o roteiro antes)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import requests

os.environ.setdefault("RENDER2GO_SAFETY", "c1-revisao-humana")
from render2go_worker import (  # noqa: E402
    cli_submit,
    compiler,
    conferencia,
    fila,
    filtro_imagem,
    legenda,
    lote,
    preparo,
    traducao,
)

AQUI = Path(__file__).resolve().parent
RAIZ = Path(os.environ.get("RENDER2GO_RAIZ", "/opt/render2go"))
COMFY_DIR = Path(os.environ.get("COMFY_DIR", "/opt/comfyui"))
PY = "/opt/venv/bin/python"
OLLAMA_GPU = "http://127.0.0.1:11434"
CANDIDATO = lote.WORKER_DIR / "workflows" / "experimental" / "i2v_oficial_lightx2v.v1.api.json"
TEMPLATES = {"A": lote.I2V_TEMPLATE, "B": CANDIDATO, "D": CANDIDATO}
QUALIDADE = {"480": "rapida", "720": "alta"}
QUADROS_FOLHA = [0, 20, 40, 60, 80]  # 5 s a 16 fps: 81 quadros
SIMULAR = False
GERAR = cli_submit.run  # `--simular`: um gerador falso (ffmpeg), sem GPU
LEGENDAR = legenda.legendar  # `--simular`: uma legenda falsa
COMPILAR = compiler.compile_prompt  # `--simular`: um tradutor falso


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} [medida] {msg}", flush=True)


# -- plano ----------------------------------------------------------------------------------------


def carregar_plano() -> tuple[list[dict], dict]:
    """Os pedidos do plano: um concreto e um vago por foto, com a seed do vídeo de cada um."""
    plano = json.loads((AQUI / "plano.json").read_text())
    pedidos = []
    for i, f in enumerate(plano["fotos"]):
        caminho = RAIZ / f["arquivo"]
        analise = {"pessoas": int(f["pessoas"]), "rostos": int(f["pessoas"])}  # a do servidor
        for tipo, base in (("concreto", f["concreto"]), ("vago", plano["vago"])):
            pedidos.append(
                {
                    "id": f"{f['id']}-{tipo}",
                    "foto": f["id"],
                    "caminho": caminho,
                    "pessoas": bool(f["pessoas"]),
                    "analise": analise,
                    "tipo": tipo,
                    "receita": base["receita"],
                    "texto": base["texto"],
                    "seed": plano["seed_base"] + 10 * i + (0 if tipo == "concreto" else 1),
                }
            )
    return pedidos, plano


def tarefa(p: dict, qualidade: str) -> lote.Tarefa:
    pedido = lote.Pedido(
        receita=p["receita"],
        acao=p["texto"],
        formato="auto",
        duracao=5,
        variacoes=1,
        consentimento=False,
    )
    ts, erros = lote._tarefas(p["caminho"], p["id"], pedido, qualidade, lote.load_receitas())
    if erros:
        raise RuntimeError(f"{p['id']}: {'; '.join(erros)}")
    return ts[0]


# -- ComfyUI e GPU --------------------------------------------------------------------------------


class Comfy:
    """Um ComfyUI desta medição, com os argumentos do entrypoint (+ `extra`)."""

    def __init__(self, porta: int, extra: list[str], log_path: Path):
        self.porta, self.extra, self.log = porta, extra, log_path
        self.url = f"http://127.0.0.1:{porta}"
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> Comfy:
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.log.touch()
        if SIMULAR:
            return self
        args = [PY, "main.py", "--listen", "127.0.0.1", "--port", str(self.porta),
                "--reserve-vram", "6", "--disable-metadata", "--disable-auto-launch",
                *self.extra]  # fmt: skip
        self.f = self.log.open("ab")
        env = {**os.environ, "PATH": f"/opt/venv/bin:{os.environ['PATH']}"}
        t0 = time.monotonic()
        self.proc = subprocess.Popen(args, cwd=COMFY_DIR, stdout=self.f, stderr=subprocess.STDOUT,
                                     env=env, start_new_session=True)  # fmt: skip
        while time.monotonic() - t0 < 300:
            if self.proc.poll() is not None:
                raise RuntimeError(f"o ComfyUI saiu ({self.proc.returncode}); ver {self.log}")
            try:
                if requests.get(self.url + "/system_stats", timeout=2).ok:
                    log(f"ComfyUI {self.porta} no ar em {time.monotonic() - t0:.1f} s {self.extra}")
                    return self
            except requests.RequestException:
                pass
            time.sleep(0.5)
        raise RuntimeError("o ComfyUI não respondeu em 300 s")

    def __exit__(self, *exc) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(10)
        self.f.close()
        time.sleep(3)  # a VRAM volta para o próximo

    def posicao(self) -> int:
        return self.log.stat().st_size

    def desde(self, pos: int) -> str:
        return self.log.read_bytes()[pos:].decode(errors="replace")


class PicoVram:
    """Maior memória usada na GPU (MiB, a GPU toda) enquanto o bloco roda."""

    def __enter__(self) -> PicoVram:
        self.pico, self._fim = None, threading.Event()
        self._t = threading.Thread(target=self._amostrar, daemon=True)
        self._t.start()
        return self

    def _amostrar(self) -> None:
        while not self._fim.is_set():
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5,
                ).stdout  # fmt: skip
                v = int(out.split()[0])
                self.pico = v if self.pico is None else max(self.pico, v)
            except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                pass
            self._fim.wait(0.5)

    def __exit__(self, *exc) -> None:
        self._fim.set()
        self._t.join(10)


LINHAS_LOG = re.compile(
    r"Prompt executed in|loaded completely|loaded partially|Requested to load|sage|Sage|"
    r"out of memory|OutOfMemory|Error|Traceback|lora|LoRA"
)


def resumo_log(texto: str, n: int = 20) -> list[str]:
    return [x.strip()[:220] for x in texto.splitlines() if LINHAS_LOG.search(x)][:n]


def executado_s(texto: str) -> list[float]:
    return [float(x) for x in re.findall(r"Prompt executed in ([0-9.]+) seconds", texto)]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


# -- um vídeo -------------------------------------------------------------------------------------


def aquecer(c: Comfy, template: Path, pasta: Path, nome: str) -> dict:
    """Um vídeo pequeno e descartado (256×256, 3 s, cinza), como o aquecimento do worker: o grupo
    seguinte mede a GPU quente, com os modelos dele já carregados."""
    from PIL import Image

    pasta.mkdir(parents=True, exist_ok=True)
    img = pasta / f"{nome}.png"
    Image.new("RGB", (256, 256), (128, 128, 128)).save(img)
    args = cli_submit.build_parser().parse_args(
        ["--comfy", c.url, "--template", str(template), "--image", str(img),
         "--prompt-en", fila.AQUECER_PROMPT, "--width", "256", "--height", "256",
         "--duration", "3", "--seed", "0", "--out", str(pasta / f"{nome}.mp4"),
         "--timeout", "1800"]
    )  # fmt: skip
    pos = c.posicao()
    t0 = time.monotonic()
    code, res = GERAR(args)
    trecho = c.desde(pos)
    r = {"ok": code == cli_submit.EXIT_OK, "erro": res.get("error"),
         "total_s": round(time.monotonic() - t0, 1), "executado_s": executado_s(trecho),
         "log": resumo_log(trecho)}  # fmt: skip
    log(f"aquecimento {nome}: {r['ok']} em {r['total_s']} s")
    return r


def gerar(c: Comfy, p: dict, combo: str, res: str, prompt: str, pasta: Path) -> dict:
    """Um vídeo direto no ComfyUI, com a foto preparada como o worker prepara."""
    pasta.mkdir(parents=True, exist_ok=True)
    t = tarefa(p, QUALIDADE[res])
    nome = f"{combo}-{res}-{p['id']}"
    prep = preparo.preparar(p["caminho"], t.width, t.height, pasta / f"{nome}.png")
    out = pasta / f"{nome}.mp4"
    args = cli_submit.build_parser().parse_args(
        ["--comfy", c.url, "--template", str(TEMPLATES[combo]), "--image", str(prep.path),
         "--prompt-en", prompt, "--width", str(t.width), "--height", str(t.height),
         "--duration", "5", "--seed", str(p["seed"]), "--out", str(out), "--timeout", "1800"]
    )  # fmt: skip
    pos = c.posicao()
    with PicoVram() as v:
        t0 = time.monotonic()
        code, r_ = GERAR(args)
        total = time.monotonic() - t0
    trecho = c.desde(pos)
    ex = executado_s(trecho)
    r = {
        "nome": nome, "combo": combo, "res": res, "pedido": p["id"], "foto": p["foto"],
        "tipo": p["tipo"], "seed": p["seed"], "dims": [t.width, t.height], "prompt": prompt,
        "prompt_cita_pessoa": traducao.prompt_cita_pessoa(prompt),
        "ok": code == cli_submit.EXIT_OK and out.is_file(), "erro": r_.get("error"),
        "render_s": (r_.get("timings") or {}).get("render_s"), "total_s": round(total, 2),
        "executado_s": ex[-1] if ex else None, "vram_pico_mib": v.pico,
        "sem_memoria": bool(re.search(r"out of memory|OutOfMemory", trecho)),
        "log": resumo_log(trecho, 8), "video": str(out), "foto_preparada": str(prep.path),
    }  # fmt: skip
    log(f"{nome}: {r['ok']} em {r['total_s']} s (ComfyUI {r['executado_s']}), VRAM {v.pico} MiB")
    return r


def conferir(r: dict, clas, folhas: Path) -> dict:
    """A conferência e o filtro de imagem de produção, e a tira de quadros para o julgamento."""
    if not r["ok"]:
        return r
    video, prep = Path(r["video"]), Path(r["foto_preparada"])
    try:
        sim = conferencia.similaridade(prep, video)
        cont = conferencia.continuidade(video)
        r |= {"similaridade": round(sim, 4), **{k: round(v, 2) for k, v in cont.items()},
              "conferencia": None if sim >= conferencia.LIMIAR else "outra foto"}  # fmt: skip
        r["conferencia"] = r["conferencia"] or conferencia.problema_continuidade(**cont)
    except conferencia.ConferenciaError as e:
        r["conferencia"] = f"erro: {e}"
    try:
        f = filtro_imagem.avaliar(video, clas)
        r |= {"nsfw": round(f.nota, 4), "nsfw_alto": round(f.nota_alta, 4),
              "nsfw_quadro": round(f.nota_quadro, 4), "barrado": f.barrado,
              "pior_quadro": f.pior_quadro}  # fmt: skip
    except filtro_imagem.FiltroError as e:
        r["filtro_erro"] = str(e)
    r["reprovado"] = bool(r.get("conferencia")) or bool(r.get("barrado"))
    r["sha"] = sha(video)
    try:
        tira(prep, video, folhas / f"{r['nome']}.jpg")
        r["tira"] = f"{r['nome']}.jpg"
    except (OSError, subprocess.SubprocessError) as e:
        r["tira_erro"] = str(e)[:300]
    return r


def tira(foto: Path, video: Path, dest: Path, altura: int = 240) -> None:
    """A foto preparada e os quadros QUADROS_FOLHA lado a lado (sem rótulo: o julgamento é às
    cegas, e a folha com o nome da combinação é feita depois, fora do pod)."""
    from PIL import Image

    dest.parent.mkdir(parents=True, exist_ok=True)
    partes = []
    with Image.open(foto) as im:
        partes.append(im.convert("RGB").resize((round(im.width * altura / im.height), altura)))
    for k in QUADROS_FOLHA:
        png = dest.with_suffix(f".{k}.png")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf",
             f"select=eq(n\\,{k}),scale=-2:{altura}", "-vframes", "1", str(png)],
            check=True, timeout=120,
        )  # fmt: skip
        with Image.open(png) as im:
            partes.append(im.convert("RGB"))
        png.unlink()
    larg = sum(i.width for i in partes) + 8 * (len(partes) - 1)
    out = Image.new("RGB", (larg, altura), (255, 255, 255))
    x = 0
    for i in partes:
        out.paste(i, (x, 0))
        x += i.width + 8
    out.save(dest, quality=82)


def comparar(a: Path, b: Path) -> dict:
    """SSIM e PSNR quadro a quadro entre dois vídeos (o mesmo pedido e a mesma seed)."""
    if not (a.is_file() and b.is_file()):
        return {"erro": "vídeo ausente"}
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(a), "-i", str(b), "-lavfi",
         "[0:v]split[a0][a1];[1:v]split[b0][b1];[a0][b0]ssim;[a1][b1]psnr", "-f", "null", "-"],
        capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    s = re.search(r"SSIM .*All:([0-9.]+)", r.stderr)
    p = re.search(r"PSNR .*average:([0-9.]+|inf)", r.stderr)
    return {
        "ssim": float(s.group(1)) if s else None,
        "psnr": (float(p.group(1)) if p.group(1) != "inf" else 99.0) if p else None,
    }


# -- tradução e legenda ---------------------------------------------------------------------------


class Tradutor:
    """O caminho do worker: traducao.traduzir com a seed do pedido e o cache por entrada (a mesma
    entrada não volta ao tradutor)."""

    def __init__(self) -> None:
        self.cache: dict[tuple, compiler.CompileResult] = {}
        self.chamadas = 0
        self.receitas = lote.load_receitas()

    def _compilar(self, texto: str, **kw) -> compiler.CompileResult:
        chave = (texto, kw.get("version"), kw.get("seed"), kw.get("suffix"),
                 kw.get("camera_preset"), kw.get("model"))  # fmt: skip
        if chave not in self.cache:
            self.chamadas += 1
            self.cache[chave] = COMPILAR(texto, **kw)
        return self.cache[chave]

    def traduzir(self, p: dict, leg: legenda.Legenda | None) -> dict:
        t0 = time.monotonic()
        tr = traducao.traduzir(
            p["texto"], p["receita"], receitas=self.receitas,
            categoria=traducao.categoria_da_receita(self.receitas, p["receita"]),
            sem_pessoas=traducao.regra_sem_pessoas(p["texto"], [p["analise"]]),
            legenda=leg, compile_fn=self._compilar, seed=fila.semente_do_pedido(p["id"]),
            ollama_url=OLLAMA_GPU, model=compiler.DEFAULT_MODEL, keep_alive=0,
        )  # fmt: skip
        return {"prompt": tr.prompt, "ref": tr.ref, "modelo": tr.model, "vago": tr.vago,
                "padrao": tr.padrao, "tentativas": tr.tentativas,
                "pessoas_recusadas": list(tr.pessoas_recusadas), "categoria": tr.categoria,
                "segundos": round(time.monotonic() - t0, 2)}  # fmt: skip


def legendar_foto(p: dict, pasta: Path) -> dict:
    """A legenda da foto como o worker a faz: a foto no formato do vídeo 480p, metade das CPUs."""
    t = tarefa(p, "rapida")
    prep = preparo.preparar(p["caminho"], t.width, t.height, pasta / f"legenda-{p['foto']}.png")
    threads = legenda.threads_da_legenda()
    t0 = time.monotonic()
    try:
        leg = LEGENDAR(prep.path, ollama_url=legenda.OLLAMA_CPU, threads=threads, timeout=900)
        return {"legenda": leg.como_dict(), "erro": None, "threads": threads,
                "segundos": round(time.monotonic() - t0, 1)}  # fmt: skip
    except legenda.LegendaError as e:
        return {"legenda": None, "erro": str(e)[:300], "threads": threads,
                "segundos": round(time.monotonic() - t0, 1)}  # fmt: skip


def legendas_em_fundo(fotos: list[dict], pasta: Path, saida: list) -> threading.Thread:
    """A legenda de cada foto, uma depois da outra, numa thread (enquanto a GPU gera vídeos)."""

    def rodar() -> None:
        for p in fotos:
            inicio = time.time()
            r = legendar_foto(p, pasta)
            r |= {"foto": p["foto"], "inicio": inicio, "fim": time.time()}
            saida.append(r)
            log(f"legenda (GPU ocupada) {p['foto']}: {r['segundos']} s {r['erro'] or ''}")

    t = threading.Thread(target=rodar, daemon=True, name="legendas")
    t.start()
    return t


# -- fases ----------------------------------------------------------------------------------------


def esperar_modelo_legenda(prazo_s: float = 15 * 60) -> float | None:
    """O modelo de visão baixa em segundo plano depois do AMBIENTE (bootstrap): espera ele aparecer
    no Ollama de CPU. Devolve os segundos de espera (None: não apareceu no prazo)."""
    t0 = time.monotonic()
    while not SIMULAR and time.monotonic() - t0 < prazo_s:
        try:
            tags = requests.get(f"{legenda.OLLAMA_CPU}/api/tags", timeout=10).json()
            if any(m.get("name") == legenda.MODELO for m in tags.get("models", [])):
                return round(time.monotonic() - t0, 1)
        except (requests.RequestException, ValueError):
            pass
        time.sleep(5)
    return 0.0 if SIMULAR else None


def fase_prompts(saida: Path, pedidos: list[dict], res: dict) -> dict:
    r: dict = {"A": {}, "D": {}, "legendas": {}, "cpu": legenda.cpus_do_conteiner()}
    res["P_prompts"] = r
    r["espera_modelo_legenda_s"] = esperar_modelo_legenda()
    log(f"modelo da legenda: espera de {r['espera_modelo_legenda_s']} s")
    trad = Tradutor()
    for p in pedidos:
        r["A"][p["id"]] = trad.traduzir(p, None)
        log(f"prompt A {p['id']}: {r['A'][p['id']]['prompt'][:100]}")
    r["chamadas_tradutor_A"] = trad.chamadas
    if (saida / "estado_sage").exists():  # a compilação do SageAttention usa a CPU: as medidas
        t0 = time.monotonic()  # da legenda e dos vídeos esperam por ela (como em 12/10/2026)
        r["sage_antes_das_medidas"] = esperar_sage(saida, 15 * 60)
        r["espera_sage_s"] = round(time.monotonic() - t0, 1)
        log(f"SageAttention: {r['sage_antes_das_medidas']} (espera de {r['espera_sage_s']} s)")
    fotos = list({p["foto"]: p for p in pedidos if p["tipo"] == "concreto"}.values())
    for p in fotos:  # a GPU parada: o tempo da legenda sozinha (a 1ª com a carga do modelo)
        r["legendas"][p["foto"]] = legendar_foto(p, saida / "tmp")
        lg = r["legendas"][p["foto"]]
        log(f"legenda {p['foto']}: {lg['segundos']} s {lg['legenda'] or lg['erro']}")
    for p in pedidos:  # D: a mesma seed e o mesmo cache (como o worker)
        d = r["legendas"][p["foto"]]["legenda"]
        leg = legenda.de_dict(d) if d else None
        r["D"][p["id"]] = trad.traduzir(p, leg)
        r["D"][p["id"]]["muda"] = r["D"][p["id"]]["prompt"] != r["A"][p["id"]]["prompt"]
    r["chamadas_tradutor_total"] = trad.chamadas
    return r


def grupo(c: Comfy, combo: str, res: str, lista: list[tuple[dict, str]], saida: Path, clas,
          extra=None) -> tuple[list[dict], dict]:  # fmt: skip
    """Aquecimento e os vídeos do grupo; a conferência e o filtro de cada um correm durante o
    próximo (como no worker), numa thread. Devolve os vídeos e o aquecimento."""
    pasta, folhas = saida / "videos" / f"{combo}{res}", saida / "tiras"
    aq = aquecer(c, TEMPLATES[combo], saida / "tmp", f"aquece-{combo}{res}")
    futuros: list[Future] = []
    with ThreadPoolExecutor(max_workers=1) as fundo:
        for i, (p, prompt) in enumerate(lista):
            inicio = time.time()
            r = gerar(c, p, combo, res, prompt, pasta)
            r |= {"inicio": inicio, "fim": time.time(), "pessoas": p["pessoas"]}
            futuros.append(fundo.submit(conferir, r, clas, folhas))
            if extra is not None and i < len(lista) - 1:
                extra(i)
        return [f.result() for f in futuros], aq


def resumo_grupo(videos: list[dict]) -> dict:
    ex = [v["executado_s"] for v in videos if v.get("executado_s")]
    return {
        "videos": len(videos),
        "ok": sum(v["ok"] for v in videos),
        "reprovados": sum(v.get("reprovado", False) for v in videos),
        "conferencia": sum(bool(v.get("conferencia")) for v in videos),
        "barrados": sum(bool(v.get("barrado")) for v in videos),
        "nsfw_media": round(statistics.mean([v["nsfw"] for v in videos if "nsfw" in v]), 4)
        if any("nsfw" in v for v in videos)
        else None,
        "executado_medio_s": round(statistics.mean(ex), 1) if ex else None,
        "executado_mediano_s": round(statistics.median(ex), 1) if ex else None,
        "vram_pico_mib": max((v["vram_pico_mib"] or 0) for v in videos) if videos else None,
        "sem_memoria": sum(v.get("sem_memoria", False) for v in videos),
        "prompt_cita_pessoa_sem_pessoa": sum(
            bool(v["prompt_cita_pessoa"]) and not v.get("pessoas") for v in videos
        ),
    }


INTERFERENCIA = 4  # pedidos de D com o prompt de B (o mesmo vídeo de novo): a interferência


def fase_480(saida: Path, pedidos: list[dict], clas, res: dict) -> dict:
    """A, B e D em 480p. D gera os pedidos cujo prompt muda e mais INTERFERENCIA cujo prompt não
    muda, com a legenda das fotos rodando no Ollama de CPU: nesses, é o vídeo de B de novo, e a
    diferença de tempo é a interferência da legenda. Para os critérios (ADR 0014), D é o vídeo
    gerado onde o prompt muda e o de B onde não muda (a regra escrita antes da rodada)."""
    pr = res["P_prompts"]
    r: dict = res.setdefault("4_480p", {})
    with Comfy(8290, [], saida / "comfy_480.log") as c:
        for combo in ("A", "B"):
            lista = [(p, pr["A"][p["id"]]["prompt"]) for p in pedidos]
            r[combo], r[f"aquecimento_{combo}"] = grupo(c, combo, "480", lista, saida, clas)
            r[f"resumo_{combo}"] = resumo_grupo(r[combo])
            gravar(saida, res)
        legs: list[dict] = []
        fotos = list({p["foto"]: p for p in pedidos if p["tipo"] == "concreto"}.values())
        th = legendas_em_fundo(fotos, saida / "tmp", legs)  # a legenda com a GPU ocupada
        iguais = [p for p in pedidos if not pr["D"][p["id"]]["muda"]][:INTERFERENCIA]
        lista = [(p, pr["D"][p["id"]]["prompt"]) for p in pedidos
                 if pr["D"][p["id"]]["muda"] or p in iguais]  # fmt: skip
        r["D"], r["aquecimento_D"] = grupo(c, "D", "480", lista, saida, clas)
        th.join(timeout=1800)
        r["legendas_com_gpu"] = legs
    b_por = {v["pedido"]: v for v in r["B"]}
    d_por = {v["pedido"]: v for v in r["D"]}
    muda = {p["id"] for p in pedidos if pr["D"][p["id"]]["muda"]}
    criterios = [d_por[v["pedido"]] if v["pedido"] in muda else v for v in r["B"]]
    r["D_criterios"] = [v["nome"] for v in criterios]
    r["resumo_D"] = resumo_grupo(criterios)
    r["resumo_D_todos_gerados"] = resumo_grupo(r["D"])
    # interferência da legenda: o mesmo prompt e a mesma seed, sem (B) e com a legenda rodando (D)
    r["interferencia"] = [
        {"pedido": v["pedido"], "sem_s": b_por[v["pedido"]].get("executado_s"),
         "com_s": v.get("executado_s"),
         **comparar(Path(b_por[v["pedido"]]["video"]), Path(v["video"]))}
        for v in r["D"] if v["pedido"] not in muda and v["ok"] and b_por[v["pedido"]]["ok"]
    ]  # fmt: skip
    return r


def fase_720(saida: Path, pedidos: list[dict], clas, res: dict) -> dict:
    """A e B em 720p, nos pedidos concretos. Durante B: a legenda no Ollama de CPU e, entre dois
    vídeos, uma tradução no Ollama da GPU (o pior caso de memória do pod com a legenda ligada)."""
    pr = res["P_prompts"]
    r: dict = res.setdefault("7_720p", {})
    concretos = [p for p in pedidos if p["tipo"] == "concreto"]
    trad = Tradutor()
    traducoes: list[dict] = []

    def traduzir_entre(i: int) -> None:
        p = concretos[i]
        with PicoVram() as v:
            t0 = time.monotonic()
            trad.cache.clear()
            try:
                trad.traduzir(p, None)
                erro = None
            except Exception as e:  # noqa: BLE001 (medida: registra e segue)
                erro = str(e)[:200]
        traducoes.append({"pedido": p["id"], "segundos": round(time.monotonic() - t0, 1),
                          "vram_pico_mib": v.pico, "erro": erro})  # fmt: skip

    with Comfy(8291, [], saida / "comfy_720.log") as c:
        lista = [(p, pr["A"][p["id"]]["prompt"]) for p in concretos]
        r["A"], r["aquecimento_A"] = grupo(c, "A", "720", lista, saida, clas)
        r["resumo_A"] = resumo_grupo(r["A"])
        gravar(saida, res)
        legs: list[dict] = []
        th = legendas_em_fundo(concretos, saida / "tmp", legs)
        r["B"], r["aquecimento_B"] = grupo(c, "B", "720", lista, saida, clas, extra=traduzir_entre)
        th.join(timeout=1800)
        r["legendas_com_gpu"], r["traducoes_entre_videos"] = legs, traducoes
        r["resumo_B"] = resumo_grupo(r["B"])
    return r


def esperar_sage(saida: Path, prazo_s: float) -> str:
    fim = time.monotonic() + prazo_s
    while time.monotonic() < fim:
        try:
            for linha in (saida / "estado_sage").read_text().splitlines():
                partes = linha.split(" ", 1)
                if len(partes) == 2 and partes[1].startswith(("SAGE_OK", "SAGE_FALHOU")):
                    return partes[1]
        except OSError:
            pass
        if SIMULAR:
            return "SAGE_SIMULADO"
        time.sleep(5)
    return f"SAGE_SEM_RESPOSTA em {prazo_s:.0f} s"


def vencedora(res: dict) -> str:
    """A regra escrita antes (LEIAME): B, se em 480p ela não teve mais reprovações automáticas
    (conferência e filtro) que A; senão, A. Os resumos vêm deste pod ou do pod 1 (`--prompts`)."""
    base = res.get("4_480p") or res.get("pod1_480p") or {}
    ra, rb = base.get("resumo_A") or {}, base.get("resumo_B") or {}
    return "B" if rb and ra and rb["reprovados"] <= ra["reprovados"] else "A"


def fase_sage(saida: Path, pedidos: list[dict], clas, res: dict) -> dict:
    """Os vídeos 480p da vencedora, primeiro com a atenção de hoje e depois com o SageAttention,
    no mesmo pod (a mesma GPU), com os mesmos prompts e seeds; a comparação é vídeo a vídeo."""
    r: dict = res.setdefault("S_sage", {})
    combo = r["vencedora"] = vencedora(res)
    pr = res["P_prompts"]
    lista = [(p, pr["A"][p["id"]]["prompt"]) for p in pedidos]
    t0 = time.monotonic()  # a compilação usa a CPU: os dois grupos rodam com ela terminada
    estado = esperar_sage(saida, 30 * 60)
    r["sage"], r["espera_sage_s"] = estado, round(time.monotonic() - t0, 1)
    log(f"SageAttention: {estado} (espera de {r['espera_sage_s']} s)")
    with Comfy(8292, [], saida / "comfy_sem_sage.log") as c:
        r["sem"], r["aquecimento_sem"] = grupo(c, combo, "480", lista, saida / "sem_sage", clas)
    r["resumo_sem"] = resumo_grupo(r["sem"])
    gravar(saida, res)
    if not (estado.startswith("SAGE_OK") or SIMULAR):
        return r
    if not SIMULAR:
        whl = saida / estado.split()[1]
        uv = shutil.which("uv") or "/usr/local/bin/uv"
        inst = subprocess.run([uv, "pip", "install", "--python", PY, "--no-deps", str(whl)],
                              capture_output=True, text=True, timeout=300)  # fmt: skip
        r["instalacao"] = (inst.stdout + inst.stderr)[-500:]
    with Comfy(8293, ["--use-sage-attention"], saida / "comfy_sage.log") as c:
        r["sage_ligado"] = SIMULAR or "Using sage attention" in c.desde(0)
        r["com"], r["aquecimento_com"] = grupo(c, combo, "480", lista, saida / "sage", clas)
        r["erros_sage_no_log"] = c.desde(0).count("Error running sage")
    r["resumo_com"] = resumo_grupo(r["com"])
    sem = {v["pedido"]: v for v in r["sem"]}
    r["comparacao"] = [
        {"pedido": v["pedido"], **comparar(Path(sem[v["pedido"]]["video"]), Path(v["video"])),
         "reprovado_sem": sem[v["pedido"]].get("reprovado"), "reprovado_com": v.get("reprovado"),
         "executado_sem": sem[v["pedido"]].get("executado_s"),
         "executado_com": v.get("executado_s")}
        for v in r["com"] if v["pedido"] in sem and v["ok"] and sem[v["pedido"]]["ok"]
    ]  # fmt: skip
    return r


# -- simulação ------------------------------------------------------------------------------------


def gerar_falso(a) -> tuple[int, dict]:
    """`--simular`: um vídeo de 16·d + 1 quadros a partir da imagem."""
    t0 = time.monotonic()
    n = 16 * a.duration + 1
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(a.image), "-vf",
         f"scale={a.width}:{a.height},zoompan=z='1+0.002*on':d={n}:s={a.width}x{a.height}:fps=16",
         "-frames:v", str(n), "-pix_fmt", "yuv420p", str(a.out)],
        check=True,
    )  # fmt: skip
    return cli_submit.EXIT_OK, {"timings": {"render_s": round(time.monotonic() - t0, 2)}}


def legendar_falso(foto, **kw) -> legenda.Legenda:
    return legenda.conferir(
        {"description": "A product on a table.", "category": "produto",
         "subject": "the product", "people": False, "text": False},
        segundos=0.1,
    )  # fmt: skip


def compilar_falso(texto, **kw) -> compiler.CompileResult:
    prompt = "The camera slowly pushes in toward the product on the table, soft light."
    return compiler.CompileResult(prompt=prompt, profile="i2v", version=kw.get("version") or 6,
                                  model="falso", seconds=0.0, word_count=13,
                                  word_range=(15, 60), compiled=prompt)  # fmt: skip


class ClassificadorFalso:
    def probabilidades(self, qs):
        return [(0.99, 0.0, 0.01, 0.0)] * len(qs)


# -- principal ------------------------------------------------------------------------------------


def ambiente() -> dict:
    a: dict = {"cpus_conteiner": legenda.cpus_do_conteiner(), "os_cpu_count": os.cpu_count()}
    try:
        a["mem_gb"] = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
        a["gpu"] = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()  # fmt: skip
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        a["gpu"] = f"? ({e})"
    a["templates"] = {k: str(v) for k, v in TEMPLATES.items()}
    a["tradutor"] = compiler.DEFAULT_MODEL
    a["versao_prompt"] = compiler.PROFILES["i2v"]["version"]
    return a


def gravar(saida: Path, res: dict) -> None:
    (saida / "resultado.json").write_text(
        json.dumps(res, indent=1, ensure_ascii=False, default=str)
    )


def main() -> int:
    global SIMULAR, GERAR, LEGENDAR, COMPILAR
    ap = argparse.ArgumentParser()
    ap.add_argument("saida", type=Path)
    ap.add_argument("--fases", default="P4")
    ap.add_argument("--prompts", type=Path, help="resultado.json de um pod anterior (sem a fase P)")
    ap.add_argument("--simular", action="store_true")
    a = ap.parse_args()
    SIMULAR = a.simular
    a.saida.mkdir(parents=True, exist_ok=True)
    res: dict = {"inicio": time.time(), "fases": a.fases}
    pedidos, plano = carregar_plano()
    res["plano"] = plano
    res["ambiente"] = ambiente()
    if a.prompts:  # os prompts e as legendas do pod 1 (a mesma tradução em todos os pods)
        anterior = json.loads(a.prompts.read_text())
        res["P_prompts"] = anterior["P_prompts"]
        res["P_prompts"]["de"] = str(a.prompts)
        resumos = {
            k: v for k, v in (anterior.get("4_480p") or {}).items() if k.startswith("resumo")
        }
        res["pod1_480p"] = resumos  # a vencedora da regra do SageAttention
    gravar(a.saida, res)
    if SIMULAR:
        GERAR, LEGENDAR, COMPILAR = gerar_falso, legendar_falso, compilar_falso
        clas = ClassificadorFalso()
    else:
        clas = filtro_imagem.Freepik(COMFY_DIR / "models" / filtro_imagem.PASTA)
        clas.carregar()
    fases = [("P", "P_prompts", lambda: fase_prompts(a.saida, pedidos, res)),
             ("4", "4_480p", lambda: fase_480(a.saida, pedidos, clas, res)),
             ("7", "7_720p", lambda: fase_720(a.saida, pedidos, clas, res)),
             ("S", "S_sage", lambda: fase_sage(a.saida, pedidos, clas, res))]  # fmt: skip
    for letra, nome, fase in fases:
        if letra not in a.fases:
            continue
        if nome != "P_prompts" and "P_prompts" not in res:
            log(f"fase {nome} pulada: sem os prompts")
            continue
        log(f"fase {nome}")
        t0 = time.monotonic()
        try:
            res[nome] = fase()
        except Exception as e:  # noqa: BLE001 (a próxima fase ainda roda)
            erro = {"erro": f"{type(e).__name__}: {e!r}", "trace": traceback.format_exc()[-3000:]}
            res[nome] = {**(res.get(nome) or {}), **erro}
            log(f"fase {nome} falhou: {e}")
        res[nome]["duracao_s"] = round(time.monotonic() - t0, 1)
        gravar(a.saida, res)
    res["fim"] = time.time()
    gravar(a.saida, res)
    log("FIM")
    return 0


if __name__ == "__main__":
    sys.exit(main())
