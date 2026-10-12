"""Roda no pod de medição (venv do worker), 12/10/2026: o worker novo (a GPU não espera), o
aquecimento, a interpolação RIFE e o SageAttention 2.2, com fotos SFW (fixtures/sfw) e seeds fixas.

Fases (cada uma grava resultado.json ao terminar; uma falha não apaga as anteriores):
1. frio: ComfyUI novo → 1 vídeo (frio) → o mesmo de novo (quente); ComfyUI novo → aquecer → o
   mesmo vídeo. O ComfyUI do entrypoint (8188) fica parado; cada fase sobe o seu, com os mesmos
   argumentos do entrypoint.
2. worker: os mesmos 6 vídeos 480p (5 s) com o worker de antes (um de cada vez, `adiantar`
   desligado) e o novo; depois 2 vídeos 720p (5 s). São a base do SageAttention.
3. rife: o template com RIFE ×2 (16 → 32 fps) em 2 dos vídeos 480p, no mesmo ComfyUI da fase 2.
4. sage: o wheel compilado neste pod (preparar_extras.sh), um teste da atenção sozinha e os mesmos
   vídeos das fases 2 com `--use-sage-attention`, comparados quadro a quadro (SSIM e PSNR).

A API e o armazenamento são de mentira, no mesmo processo, com a latência de rede de LATENCIA (a
da API é medida no pod): o que se mede é a GPU e o que roda no pod.

Uso: python no_pod.py <pasta de saída> [--fases 1234] [--simular]
(`--simular`: sem GPU nem ComfyUI, com um gerador falso, só para conferir o roteiro antes do pod)
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
from collections import Counter
from pathlib import Path

import requests

os.environ.setdefault("RENDER2GO_SAFETY", "c1-revisao-humana")
from render2go_worker import cli_submit, fila, filtro_imagem, lote, media, preparo  # noqa: E402

RAIZ = Path(os.environ.get("RENDER2GO_RAIZ", "/opt/render2go"))
COMFY_DIR = Path(os.environ.get("COMFY_DIR", "/opt/comfyui"))
PY = "/opt/venv/bin/python"
FOTOS = {
    "cafe": RAIZ / "fixtures/sfw/produto-cafe.jpg",
    "xicara": RAIZ / "fixtures/sfw/produto-xicara.jpg",
}
PROMPTS = {
    "cafe": "The camera slowly dollies in toward the coffee cup on the wooden table. Gentle "
    "steam rises from the cup. Soft natural window light.",
    "xicara": "The camera slowly orbits around the ceramic cup on the table. Soft studio light "
    "and a shallow depth of field.",
}
V480 = [
    ("cafe", 1001),
    ("xicara", 1002),
    ("cafe", 1003),
    ("xicara", 1004),
    ("cafe", 1005),
    ("xicara", 1006),
]
V720 = [("cafe", 2001), ("xicara", 2002)]
# segundos por operação de rede (a da API é trocada pela medida no pod, se der)
LATENCIA = {"api": 0.15, "foto": 0.5, "folha": 0.3, "video": 0.8}
RIFE = "rife_v4.25.safetensors"
SIMULAR = False
GERAR = cli_submit.run  # `--simular`: um gerador falso (ffmpeg), sem GPU


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} [medida] {msg}", flush=True)


# -- ComfyUI e GPU ------------------------------------------------------------------------------


class Comfy:
    """Um ComfyUI desta medição, com os argumentos do entrypoint (+ `extra`)."""

    def __init__(self, porta: int, extra: list[str], log_path: Path):
        self.porta, self.extra, self.log = porta, extra, log_path
        self.url = f"http://127.0.0.1:{porta}"
        self.proc: subprocess.Popen | None = None
        self.subida_s = 0.0

    def __enter__(self) -> Comfy:
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.log.touch()
        if SIMULAR:
            return self
        args = [
            PY,
            "main.py",
            "--listen",
            "127.0.0.1",
            "--port",
            str(self.porta),
            "--reserve-vram",
            "6",
            "--disable-metadata",
            "--disable-auto-launch",
            *self.extra,
        ]
        self.f = self.log.open("ab")
        env = {**os.environ, "PATH": f"/opt/venv/bin:{os.environ['PATH']}"}
        t0 = time.monotonic()
        self.proc = subprocess.Popen(
            args,
            cwd=COMFY_DIR,
            stdout=self.f,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        while time.monotonic() - t0 < 300:
            if self.proc.poll() is not None:
                raise RuntimeError(f"o ComfyUI saiu ({self.proc.returncode}); ver {self.log}")
            try:
                if requests.get(self.url + "/system_stats", timeout=2).ok:
                    self.subida_s = round(time.monotonic() - t0, 1)
                    log(f"ComfyUI {self.porta} no ar em {self.subida_s} s ({' '.join(self.extra)})")
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
                    capture_output=True,
                    text=True,
                    timeout=5,
                ).stdout
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
    r"Frame interpolation|out of memory|Error|Traceback"
)


def resumo_log(texto: str, n: int = 25) -> list[str]:
    linhas = [x.strip()[:220] for x in texto.splitlines() if LINHAS_LOG.search(x)]
    return linhas[:n]


def executado_s(texto: str) -> list[float]:
    return [float(x) for x in re.findall(r"Prompt executed in ([0-9.]+) seconds", texto)]


# -- vídeos ---------------------------------------------------------------------------------------


def dims(foto: str, qualidade: str) -> tuple[int, int]:
    p = lote.Pedido(
        receita="produto-close",
        acao="x",
        formato="auto",
        duracao=5,
        variacoes=1,
        consentimento=False,
    )
    ts, erros = lote._tarefas(FOTOS[foto], foto, p, qualidade, lote.load_receitas())
    assert not erros, erros
    return ts[0].width, ts[0].height


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def render(
    c: Comfy,
    foto: str,
    seed: int,
    qualidade: str,
    pasta: Path,
    nome: str,
    template: Path = lote.I2V_TEMPLATE,
) -> dict:
    """Um vídeo direto no ComfyUI (sem o worker), com os tempos do cli_submit."""
    pasta.mkdir(parents=True, exist_ok=True)
    w, h = dims(foto, qualidade)
    prep = preparo.preparar(FOTOS[foto], w, h, pasta / f"{nome}.png")
    out = pasta / f"{nome}.mp4"
    args = cli_submit.build_parser().parse_args(
        [
            "--comfy",
            c.url,
            "--template",
            str(template),
            "--image",
            str(prep.path),
            "--prompt-en",
            PROMPTS[foto],
            "--width",
            str(w),
            "--height",
            str(h),
            "--duration",
            "5",
            "--seed",
            str(seed),
            "--out",
            str(out),
            "--timeout",
            "1800",
        ]
    )
    pos = c.posicao()
    with PicoVram() as v:
        t0 = time.monotonic()
        code, res = GERAR(args)
        total = time.monotonic() - t0
    trecho = c.desde(pos)
    r = {
        "nome": nome,
        "foto": foto,
        "seed": seed,
        "dims": [w, h],
        "ok": code == cli_submit.EXIT_OK,
        "erro": res.get("error"),
        "tempos": res.get("timings"),
        "total_s": round(total, 2),
        "vram_pico_mib": v.pico,
        "executado_s": executado_s(trecho),
        "log": resumo_log(trecho),
        "sha": sha(out) if out.is_file() else None,
    }
    log(f"{nome}: {r['ok']} em {r['total_s']} s (ComfyUI {r['executado_s']}), VRAM {v.pico} MiB")
    return r


class ApiLocal:
    """A API de mentira: a fila de jobs e o que o worker conclui, com a latência de LATENCIA."""

    def __init__(self, jobs: list[dict]):
        self.fila, self.lock = list(jobs), threading.Lock()
        self.concluidos: dict[str, dict] = {}
        self.chamadas: Counter = Counter()

    def _rede(self, nome: str) -> None:
        time.sleep(LATENCIA["api"])
        with self.lock:
            self.chamadas[nome] += 1

    def reivindicar(self, tipos=None):
        self._rede("reivindicar")
        with self.lock:
            return self.fila.pop(0) if self.fila else None

    def concluir(self, job_id, status, **campos):
        self._rede("concluir")
        with self.lock:
            self.concluidos[job_id] = {"status": status, **campos}
        return status

    def iniciar(self, job_id):
        self._rede("iniciar")
        return True

    def devolver(self, job_id):
        self._rede("devolver")

    def prompt(self, *a, **kw):
        raise AssertionError("sem tradução nesta medição")


def jobs_de(rodada: str, lista: list[tuple[str, int]], qualidade: str) -> list[dict]:
    jobs = []
    for i, (foto, seed) in enumerate(lista, 1):
        arquivo = f"{foto}{i}-v1.mp4"
        stem = Path(arquivo).stem
        jobs.append(
            {
                "job_id": f"{rodada}-{i}",
                "tipo": "render",
                "lote": rodada,
                "aprovado_em": 0.0,
                "aprovacao": "",
                "prompt_en": PROMPTS[foto],
                "arquivo": arquivo,
                "seed": seed,
                "pedido": {
                    "receita": "produto-close",
                    "prompt": "a câmera se aproxima devagar",
                    "formato": "auto",
                    "duracao": 5,
                    "variacoes": 1,
                    "consentimento": False,
                    "qualidade": qualidade,
                    "modelo": "wan2.2-i2v",
                },
                "foto_url": f"local://{foto}",
                "foto_ext": "jpg",
                "video_url": f"local://{rodada}/{arquivo}",
                "folha_url": f"local://{rodada}/{stem}.jpg",
                "quarentena_video_url": f"local://{rodada}/quarentena-{arquivo}",
                "quarentena_folha_url": f"local://{rodada}/quarentena-{stem}.jpg",
            }
        )
    return jobs


def armazem_local(saida: Path) -> None:
    """media.download/upload do worker trocados por cópias locais com a latência da rede."""

    def baixar(url, dest, max_bytes):
        time.sleep(LATENCIA["foto"])
        shutil.copyfile(FOTOS[url.removeprefix("local://")], dest)

    def enviar(url, path, tipo):
        time.sleep(LATENCIA["video" if tipo == "video/mp4" else "folha"])
        dest = saida / url.removeprefix("local://")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)

    media.download, media.upload = baixar, enviar


def rodar_worker(c: Comfy, jobs: list[dict], adiantar: bool, clas, nome: str) -> dict:
    api = ApiLocal(jobs)
    cfg = fila.Config(comfy=[c.url], ocioso_s=0.3, adiantar=adiantar, aquecer=False)
    w = fila.WorkerFila(api, cfg, submit=GERAR, classificador=clas)
    pos = c.posicao()
    with PicoVram() as v:
        t0 = time.monotonic()
        w.rodar()
        total = time.monotonic() - t0
    trecho = c.desde(pos)
    m = w.medidas
    # do fim de um vídeo na GPU ao começo do próximo (o 1º não tem anterior)
    esperas = [x["gpu_parada"] for x in m if "gpu_parada" in x]
    r = {
        "nome": nome,
        "adiantar": adiantar,
        "videos": len(jobs),
        "ok": w.feitos,
        "falhas": w.falhas,
        "total_s": round(total, 1),
        "por_video_s": round(total / len(jobs), 1),
        "gpu_parada_media_s": round(statistics.mean(esperas), 2) if esperas else None,
        "render_medio_s": round(statistics.mean(x.get("render", x["gpu"]) for x in m), 1)
        if m
        else None,
        "medidas": [
            {k: (round(v, 2) if isinstance(v, float) else v) for k, v in x.items()} for x in m
        ],
        "concluidos": api.concluidos,
        "chamadas": dict(api.chamadas),
        "vram_pico_mib": v.pico,
        "executado_s": executado_s(trecho),
        "erros_sage": trecho.count("Error running sage"),
        "log": resumo_log(trecho, 12),
    }
    log(
        f"{nome}: {w.feitos}/{len(jobs)} ok em {r['total_s']} s ({r['por_video_s']} s/vídeo); "
        f"GPU parada {r['gpu_parada_media_s']} s entre vídeos"
    )
    return r


def comparar(a: Path, b: Path) -> dict:
    """SSIM e PSNR quadro a quadro entre dois vídeos (o mesmo pedido e a mesma seed)."""
    if not (a.is_file() and b.is_file()):
        return {"erro": "vídeo ausente"}
    r = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(a),
            "-i",
            str(b),
            "-lavfi",
            "[0:v]split[a0][a1];[1:v]split[b0][b1];[a0][b0]ssim;[a1][b1]psnr",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    s = re.search(r"SSIM .*All:([0-9.]+)", r.stderr)
    p = re.search(r"PSNR .*average:([0-9.]+|inf)", r.stderr)
    return {
        "ssim": float(s.group(1)) if s else None,
        "psnr": (float(p.group(1)) if p.group(1) != "inf" else 99.0) if p else None,
        "iguais": sha(a) == sha(b),
    }


def quadros(video: Path) -> dict | None:
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames,r_frame_rate",
            "-of",
            "json",
            str(video),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    try:
        s = json.loads(r.stdout)["streams"][0]
        return {"quadros": int(s["nb_read_frames"]), "fps": s["r_frame_rate"]}
    except (KeyError, IndexError, ValueError, json.JSONDecodeError):
        return None


def folha(linhas: list[tuple[str, Path, list[int]]], dest: Path, altura: int = 270) -> None:
    """Quadros lado a lado, uma linha por vídeo (para olhar a qualidade fora do pod)."""
    from PIL import Image, ImageDraw

    dest.parent.mkdir(parents=True, exist_ok=True)
    imgs = []
    for n, (rotulo, video, idx) in enumerate(linhas):
        fila_ = []
        for k in idx:
            png = dest.with_suffix(f".{n}.{k}.png")
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(video),
                    "-vf",
                    f"select=eq(n\\,{k}),scale=-2:{altura}",
                    "-vframes",
                    "1",
                    str(png),
                ],
                check=True,
                timeout=120,
            )
            with Image.open(png) as im:
                fila_.append(im.convert("RGB"))
            png.unlink()
        larg = sum(i.width for i in fila_)
        linha = Image.new("RGB", (larg, altura + 22), (255, 255, 255))
        x = 0
        for i in fila_:
            linha.paste(i, (x, 22))
            x += i.width
        ImageDraw.Draw(linha).text((6, 4), f"{rotulo}: quadros {idx}", fill=(0, 0, 0))
        imgs.append(linha)
    out = Image.new("RGB", (max(i.width for i in imgs), sum(i.height for i in imgs)), "white")
    y = 0
    for i in imgs:
        out.paste(i, (0, y))
        y += i.height
    out.save(dest, quality=85)


def esperar_extras(saida: Path, prefixo: str, prazo_s: float) -> str:
    """RIFE_OK / SAGE_OK (ou *_FALHOU) do preparar_extras.sh."""
    fim = time.monotonic() + prazo_s
    while time.monotonic() < fim:
        try:
            for linha in (saida / "estado_extras").read_text().splitlines():
                partes = linha.split(" ", 1)
                if len(partes) == 2 and partes[1].startswith(prefixo):
                    return partes[1]
        except OSError:
            pass
        if SIMULAR:
            return f"{prefixo}SIMULADO"
        time.sleep(5)
    return f"{prefixo}SEM_RESPOSTA em {prazo_s:.0f} s"


# -- fases ----------------------------------------------------------------------------------------


def fase_frio(saida: Path, clas, res: dict) -> dict:
    pasta = saida / "frio"
    with Comfy(8288, [], saida / "comfy_frio1.log") as c:
        frio = render(c, "cafe", 1001, "rapida", pasta, "frio")
        quente = render(c, "cafe", 1001, "rapida", pasta, "quente")
        subida1 = c.subida_s
    with Comfy(8289, [], saida / "comfy_frio2.log") as c:
        w = fila.WorkerFila(ApiLocal([]), fila.Config(comfy=[c.url]), GERAR, classificador=clas)
        pos = c.posicao()
        aquecer_s = w.aquecer(c.url)
        log_aquecer = c.desde(pos)
        depois = render(c, "cafe", 1001, "rapida", pasta, "depois_de_aquecer")
        subida2 = c.subida_s
    return {
        "subida_comfy_s": [subida1, subida2],
        "frio": frio,
        "quente": quente,
        "aquecer_s": round(aquecer_s, 1) if aquecer_s else None,
        "aquecer_executado_s": executado_s(log_aquecer),
        "aquecer_log": resumo_log(log_aquecer),
        "depois_de_aquecer": depois,
        "mesmo_video": len({frio["sha"], quente["sha"], depois["sha"]}) == 1,
    }


def template_rife(pasta: Path) -> Path:
    """O template de produção com RIFE ×2 entre o VAE e o vídeo (32 fps); o resto igual."""
    pasta.mkdir(parents=True, exist_ok=True)
    base = lote.I2V_TEMPLATE
    wf = json.loads(base.read_text())
    contrato = json.loads(
        base.with_name(base.name.replace(".api.json", ".contract.json")).read_text()
    )
    wf["900"] = {"class_type": "FrameInterpolationModelLoader", "inputs": {"model_name": RIFE}}
    wf["901"] = {
        "class_type": "FrameInterpolate",
        "inputs": {"interp_model": ["900", 0], "images": ["129:87", 0], "multiplier": 2},
    }
    assert wf["129:94"]["class_type"] == "CreateVideo" and wf["129:94"]["inputs"]["images"] == [
        "129:87",
        0,
    ]
    wf["129:94"]["inputs"]["images"] = ["901", 0]
    wf["129:94"]["inputs"]["fps"] = 32.0
    contrato["template"] = "i2v_r1v2_rife"
    dest = pasta / "i2v_r1v2_rife.v1.api.json"
    dest.write_text(json.dumps(wf))
    dest.with_name("i2v_r1v2_rife.v1.contract.json").write_text(json.dumps(contrato))
    return dest


def fase_worker_e_rife(saida: Path, clas, res: dict) -> dict:
    # a compilação do SageAttention usa a CPU: as medidas do worker esperam por ela (até 20 min)
    res["extras_antes_da_fase_2"] = esperar_extras(saida, "SAGE_", 20 * 60)
    armazem_local(saida / "videos")
    r: dict = res.setdefault("2_worker", {})  # parcial: uma falha no meio não apaga o que já mediu
    with Comfy(8290, [], saida / "comfy_base.log") as c:
        r["aquecimento"] = render(c, "cafe", 999, "rapida", saida / "tmp", "aquece")
        r["antes"] = rodar_worker(c, jobs_de("antes", V480, "rapida"), False, clas, "antes")
        r["novo"] = rodar_worker(c, jobs_de("novo", V480, "rapida"), True, clas, "novo")
        r["alta"] = rodar_worker(c, jobs_de("alta", V720, "alta"), True, clas, "alta")
        v = saida / "videos"
        r["antes_igual_novo"] = [
            comparar(v / "antes" / j["arquivo"], v / "novo" / j["arquivo"])
            for j in jobs_de("x", V480, "rapida")
        ]
        rife = esperar_extras(saida, "RIFE_", 60)
        res["3_rife"] = {"extras": rife}
        if rife.startswith("RIFE_OK") or SIMULAR:
            tpl = template_rife(saida / "tmp")
            rr = []
            for i, (foto, seed) in enumerate(V480[:2], 1):
                x = render(c, foto, seed, "rapida", saida / "rife", f"{foto}{i}-rife", tpl)
                arq = f"{foto}{i}-v1.mp4"
                base = next((m for m in r["novo"]["medidas"] if m["arquivo"] == arq), {})
                x["render_base_s"] = base.get("render")  # None: o vídeo não saiu certo no novo
                x["video_info"] = quadros(saida / "rife" / f"{foto}{i}-rife.mp4")
                x["bytes"] = (saida / "rife" / f"{foto}{i}-rife.mp4").stat().st_size
                vb = v / "novo" / arq
                x["bytes_base"] = vb.stat().st_size if vb.is_file() else None
                rr.append(x)
                folha(
                    [
                        (
                            f"{foto} 32 fps (RIFE)",
                            saida / "rife" / f"{foto}{i}-rife.mp4",
                            [40, 41, 42, 43, 44],
                        )
                    ],
                    saida / "folhas" / f"rife-{foto}{i}.jpg",
                )
            res["3_rife"]["videos"] = rr
    return r


def microbench_sage() -> dict:
    """A atenção sozinha, no tamanho do Wan 2.2 em 480p 5 s (40 cabeças, 32.760 tokens, dim 128)."""
    codigo = (
        "import time, torch, torch.nn.functional as F\n"
        "from sageattention import sageattn\n"
        "forma = (1, 40, 32760, 128)\n"
        "q, k, v = (torch.randn(forma, dtype=torch.bfloat16, device='cuda') for _ in range(3))\n"
        "def t(f, n=10):\n"
        "    f(); torch.cuda.synchronize(); t0 = time.perf_counter()\n"
        "    for _ in range(n): f()\n"
        "    torch.cuda.synchronize(); return (time.perf_counter() - t0) / n\n"
        "s = t(lambda: sageattn(q, k, v, tensor_layout='HND', is_causal=False))\n"
        "p = t(lambda: F.scaled_dot_product_attention(q, k, v))\n"
        "o1 = sageattn(q, k, v, tensor_layout='HND')\n"
        "o2 = F.scaled_dot_product_attention(q, k, v)\n"
        "cos = F.cosine_similarity(o1.flatten().float(), o2.flatten().float(), dim=0).item()\n"
        "print({'sage_ms': round(s*1000, 1), 'sdpa_ms': round(p*1000, 1), 'cos': round(cos, 5)})\n"
    )
    t0 = time.monotonic()
    r = subprocess.run([PY, "-c", codigo], capture_output=True, text=True, timeout=900)
    out = (r.stdout.strip().splitlines() or [""])[-1]
    return {
        "ok": r.returncode == 0,
        "saida": out,
        "erro": r.stderr[-1500:] if r.returncode else "",
        "segundos_com_compilacao": round(time.monotonic() - t0, 1),
    }


def fase_sage(saida: Path, clas, res: dict) -> dict:
    estado = esperar_extras(saida, "SAGE_", 25 * 60)
    r: dict = res.setdefault("4_sage", {})
    r["extras"] = estado
    if not (estado.startswith("SAGE_OK") or SIMULAR):
        return r
    if not SIMULAR:
        whl = saida / estado.split()[1]
        uv = shutil.which("uv") or "/usr/local/bin/uv"
        inst = subprocess.run(
            [uv, "pip", "install", "--python", PY, "--no-deps", str(whl)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        r["instalacao"] = (inst.stdout + inst.stderr)[-500:]
        r["atencao_sozinha"] = microbench_sage()
    armazem_local(saida / "videos")
    with Comfy(8291, ["--use-sage-attention"], saida / "comfy_sage.log") as c:
        r["sage_ligado"] = "Using sage attention" in c.desde(0) or SIMULAR
        r["primeiro_video"] = render(c, "cafe", 999, "rapida", saida / "tmp", "aquece-sage")
        r["sage480"] = rodar_worker(c, jobs_de("sage", V480, "rapida"), True, clas, "sage480")
        r["sage720"] = rodar_worker(c, jobs_de("sage-alta", V720, "alta"), True, clas, "sage720")
        r["erros_sage_no_log"] = c.desde(0).count("Error running sage")
    v = saida / "videos"
    r["comparacao"] = []
    for rodada, base, lista, qual in (
        ("sage", "novo", V480, "rapida"),
        ("sage-alta", "alta", V720, "alta"),
    ):
        for j in jobs_de(rodada, lista, qual):
            x = comparar(v / base / j["arquivo"], v / rodada / j["arquivo"])
            cs = r["sage480" if rodada == "sage" else "sage720"]["concluidos"].get(j["job_id"], {})
            r["comparacao"].append(
                {
                    "arquivo": f"{rodada}/{j['arquivo']}",
                    **x,
                    "status": cs.get("status"),
                    "similaridade": cs.get("similaridade"),
                    "deriva_cor": cs.get("deriva_cor"),
                    "salto_cor": cs.get("salto_cor"),
                    "nsfw": cs.get("nsfw"),
                }
            )
    for rodada, base, arq in (
        ("sage", "novo", "cafe1-v1.mp4"),
        ("sage", "novo", "xicara2-v1.mp4"),
        ("sage-alta", "alta", "xicara2-v1.mp4"),
    ):
        if (v / rodada / arq).is_file() and (v / base / arq).is_file():
            folha(
                [("atual", v / base / arq, [0, 40, 80]), ("sage", v / rodada / arq, [0, 40, 80])],
                saida / "folhas" / f"sage-{rodada}-{Path(arq).stem}.jpg",
            )
    return r


def gerar_falso(a) -> tuple[int, dict]:
    """`--simular`: um vídeo de 16·d + 1 quadros a partir da imagem (como nos testes do worker)."""
    t0 = time.monotonic()
    quadros_ = 16 * a.duration + 1
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-loop",
            "1",
            "-i",
            str(a.image),
            "-vf",
            f"scale={a.width}:{a.height},zoompan=z='1+0.002*on':d={quadros_}:s={a.width}x{a.height}:fps=16",
            "-frames:v",
            str(quadros_),
            "-pix_fmt",
            "yuv420p",
            str(a.out),
        ],
        check=True,
    )
    return cli_submit.EXIT_OK, {"timings": {"render_s": round(time.monotonic() - t0, 2)}}


class ClassificadorFalso:
    def probabilidades(self, qs):
        return [(0.99, 0.0, 0.01, 0.0)] * len(qs)


def ambiente() -> dict:
    a: dict = {"nproc": os.cpu_count()}
    try:
        a["mem_gb"] = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
        a["gpu"] = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        a["gpu"] = f"? ({e})"
    tempos = []
    s = requests.Session()
    for _ in range(0 if SIMULAR else 6):  # a API de verdade (página inicial): pod ↔ servidor
        try:
            t0 = time.monotonic()
            s.get("https://app.render2go.com/", timeout=10)
            tempos.append(time.monotonic() - t0)
        except requests.RequestException:
            pass
    if len(tempos) >= 3:
        a["api_rtt_s"] = [round(x, 3) for x in tempos]
        LATENCIA["api"] = round(statistics.median(tempos[1:]), 3)  # conexão já aberta
    a["latencia_usada"] = dict(LATENCIA)
    return a


def main() -> int:
    global SIMULAR
    p = argparse.ArgumentParser()
    p.add_argument("saida", type=Path)
    p.add_argument("--fases", default="124")
    p.add_argument("--simular", action="store_true")
    a = p.parse_args()
    SIMULAR = a.simular
    a.saida.mkdir(parents=True, exist_ok=True)
    res: dict = {"inicio": time.time(), "fases": a.fases}

    def gravar() -> None:
        (a.saida / "resultado.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))

    res["ambiente"] = ambiente()
    gravar()
    if SIMULAR:
        global GERAR
        GERAR, clas = gerar_falso, ClassificadorFalso()
    else:
        clas = filtro_imagem.Freepik(COMFY_DIR / "models" / filtro_imagem.PASTA)
        clas.carregar()
    fila.ESPERA_S = 0.2
    fases = [("1_frio", fase_frio), ("2_worker", fase_worker_e_rife), ("4_sage", fase_sage)]
    for nome, fase in fases:
        if nome[0] not in a.fases:
            continue
        log(f"fase {nome}")
        t0 = time.monotonic()
        try:
            res[nome] = fase(a.saida, clas, res)
        except Exception as e:  # noqa: BLE001 (a próxima fase ainda roda)
            erro = {"erro": f"{type(e).__name__}: {e!r}", "trace": traceback.format_exc()[-3000:]}
            res[nome] = {**(res.get(nome) or {}), **erro}  # o parcial da fase fica
            log(f"fase {nome} falhou: {e}")
        res[nome]["duracao_s"] = round(time.monotonic() - t0, 1)
        gravar()
    res["fim"] = time.time()
    gravar()
    log("FIM")
    return 0


if __name__ == "__main__":
    sys.exit(main())
