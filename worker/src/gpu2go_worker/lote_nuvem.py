"""Lote na nuvem com um comando (ADR 0003, fase 2): pasta de fotos → vídeos na pasta final.

    gpu2go-lote-nuvem fotos/loja --saida entregas/loja \\
        --prompt "a câmera se aproxima devagar do produto" --receita produto-close \\
        --pods 2 --gpu "NVIDIA L40S" --gpu "NVIDIA GeForce RTX 4090" --orcamento-usd 3

O que acontece:
1. **Aqui, sem GPU:** confere o pedido inteiro (`lote.planejar`), prepara as fotos
   (`preparo`: corte e Lanczos no tamanho do vídeo, ~0,3–1 MB cada) e mostra a estimativa
   de tempo e custo. Sem `--sim`, pede confirmação.
2. **Sobe os pods** (GPUs na ordem pedida, nuvem secure, conferindo a vaga na hora). Cada pod:
   - nasce com desligamento automático (`DEADMAN_S`, no agente, contado do 1º segundo),
     **no máximo 2 h**. O lote para de mandar pacotes 10 min antes, para baixar tudo;
   - passa por um teste de CUDA (`cuInit`) e é encerrado na hora se a GPU estiver com
     defeito, ou se o agente não responder em 10 min;
   - instala o worker (`scripts/bootstrap_pod.sh`) e sobe um ComfyUI por GPU.
3. **Compila o pedido uma vez** no primeiro pod pronto. Todos os pods usam o mesmo prompt.
4. **Distribui as fotos em pacotes** (2 fotos por GPU) conforme os pods ficam livres. Pod
   lento recebe menos; pod que cai devolve o pacote para os outros.
5. **Traz cada vídeo** para `--saida` assim que o pacote termina, com o `relatorio.csv`
   e as folhas de conferência (`conferencia/`). Com `--interpolar`, `--altura` ou `--zoom`,
   o acabamento roda aqui na CPU e grava em `--saida/final/`.
6. **Encerra todos os pods** no fim, em erro ou em Ctrl+C. Se este processo morrer, o
   desligamento automático encerra cada pod.

Rodar de novo com a mesma `--saida` continua: vídeos já prontos não são refeitos.
`--limpar --saida DIR` encerra os pods que ficaram no estado de uma execução interrompida.

Códigos de saída: 0 tudo pronto · 1 algum vídeo falhou · 2 pedido inválido ·
3 recusado pelo compilador · 4 sem GPU disponível ou orçamento · 5 pods caíram com
trabalho pendente (rode de novo).
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import queue
import re
import secrets
import shlex
import signal
import subprocess
import sys
import threading
import time
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import requests

from gpu2go_worker import WORKER_DIR, acabamento, lote, preparo, runpod_pods

REPO = WORKER_DIR.parent
EXIT_OK, EXIT_FALHA, EXIT_PEDIDO, EXIT_RECUSADO, EXIT_SEM_GPU, EXIT_PENDENTE = 0, 1, 2, 3, 4, 5
# Segundos por vídeo de 5 s medidos (worker/evals/gpu-2026-10-06*); o resto usa o pior caso.
MEDIDO_S = {
    "NVIDIA L40S": {"rapida": 55, "alta": 180},
    "NVIDIA GeForce RTX 4090": {"rapida": 80, "alta": 213},
}
PIOR_S = {"rapida": 80, "alta": 213}
# Ordem padrão (decisão de 07/10/2026), sempre na nuvem secure: L40S (medida); L40 e RTX 6000
# Ada (mesmo chip, 48 GB, ~metade do cálculo de IA da L40S; tempo ainda não medido, usa o
# pior caso); RTX 4090 por último (24 GB, troca de modelo a cada vídeo).
GPUS_PADRAO = [
    "NVIDIA L40S",
    "NVIDIA L40",
    "NVIDIA RTX 6000 Ada Generation",
    "NVIDIA GeForce RTX 4090",
]
SUBIDA_S = 12 * 60  # agente + instalação + modelos + serviços (medido: 8–12 min)
# Regra do dono do projeto (07/10/2026): pod nenhum fica ligado mais de 2 h. O lote para
# de mandar pacotes a tempo de baixar tudo (MARGEM_S) antes do desligamento automático.
MAX_POD_S = 2 * 3600
MARGEM_S = 10 * 60
ESPERA_AGENTE_S = 600
ESPERA_INSTALACAO_S = 1800
ESPERA_SERVICOS_S = 600
FOTOS_POR_GPU = 2
ESTADO = ".nuvem"


def _log(msg: str) -> None:
    print(time.strftime("%H:%M:%S ") + msg, file=sys.stderr, flush=True)


class NuvemError(Exception):
    pass


# --- agente do pod --------------------------------------------------------------------


class AgenteError(NuvemError):
    pass


@dataclass
class Agente:
    """Cliente do `pod_agente` (HTTPS pelo proxy do RunPod, token por pod)."""

    url: str
    token: str
    tentativas: int = 6

    def _req(self, method: str, rota: str, **kw) -> requests.Response:
        erro: Exception | None = None
        timeout = kw.pop("timeout", 120)
        for i in range(self.tentativas):
            try:
                r = requests.request(
                    method,
                    f"{self.url}{rota}",
                    headers={"X-Token": self.token},
                    timeout=timeout,
                    **kw,
                )
                if r.status_code == 403:
                    raise AgenteError("token recusado pelo agente")
                # O agente sempre responde JSON nos erros. 404/5xx sem JSON vêm do proxy do
                # RunPod (visto em 07/10: 404 vazio logo depois do pod ficar pronto): repete.
                do_agente = r.status_code < 400 or r.headers.get("Content-Type", "").startswith(
                    "application/json"
                )
                if do_agente and r.status_code < 500:
                    return r
                erro = AgenteError(f"{rota}: HTTP {r.status_code} (proxy)")
            except requests.RequestException as e:
                erro = e
            time.sleep(2 * (i + 1))
        raise AgenteError(f"agente inacessível ({rota}): {erro}")

    def saude(self) -> bool:
        try:
            r = requests.get(f"{self.url}/saude", headers={"X-Token": self.token}, timeout=15)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def exec(self, cmd: str, timeout: int = 90) -> tuple[int, str]:
        r = self._req("POST", "/exec", json={"cmd": cmd, "timeout": timeout}, timeout=timeout + 30)
        d = r.json()
        return d["code"], d["out"]

    def bg(self, cmd: str, log: str) -> None:
        self._req("POST", "/bg", json={"cmd": cmd, "log": log})

    def put(self, local: Path, remoto: str) -> None:
        with local.open("rb") as f:
            r = self._req("PUT", "/arquivo", params={"caminho": remoto}, data=f, timeout=600)
        if r.status_code != 200:
            raise AgenteError(f"envio de {local.name}: HTTP {r.status_code} {r.text[:200]}")

    def get(self, remoto: str, local: Path) -> bool:
        r = self._req("GET", "/arquivo", params={"caminho": remoto}, timeout=600)
        if r.status_code == 404:
            return False
        local.parent.mkdir(parents=True, exist_ok=True)
        tmp = local.with_name(local.name + ".parcial")
        tmp.write_bytes(r.content)
        tmp.replace(local)
        return True


# --- provedor de pods -----------------------------------------------------------------


@dataclass
class PodInfo:
    id: str
    url: str
    token: str
    usd_h: float
    descricao: str
    gpus: int


class Provedor(Protocol):
    def preco(self, gpu: str, nuvem: str) -> float | None: ...

    def criar(
        self, nome: str, opcoes: list[runpod_pods.Opcao], gpus: int, env: dict
    ) -> PodInfo: ...

    def encerrar(self, pod_id: str) -> None: ...


AGENTE_CMD = 'echo "$AGENT_B64" | base64 -d > /agente.py && exec python3 /agente.py'


class RunPod:
    """Pods do RunPod com o agente injetado (imagem pública python:3.12-slim)."""

    IMAGEM = "python:3.12-slim-bookworm"

    def __init__(self, datacentros: list[str] | None = None) -> None:
        self.chave = runpod_pods._chave()
        self.datacentros = datacentros or []

    def preco(self, gpu: str, nuvem: str) -> float | None:
        for row in runpod_pods.disponibilidade([gpu]):
            if row["nuvem"] == nuvem and row["oferecida"]:
                return row["usd_hora"]
        return None

    def criar(self, nome: str, opcoes: list[runpod_pods.Opcao], gpus: int, env: dict) -> PodInfo:
        tentativas = []
        for opcao in opcoes:
            corpo = runpod_pods.corpo_pod(opcao, self.IMAGEM, nome, 120, env, ["8888/http"])
            corpo["gpuCount"] = gpus
            corpo["dockerStartCmd"] = ["bash", "-c", AGENTE_CMD]
            if self.datacentros:
                corpo["dataCenterIds"] = self.datacentros
            try:
                pod = runpod_pods.criar(opcao, corpo, self.chave)
            except runpod_pods.SemVaga:
                tentativas.append(f"{opcao}: sem vaga")
                continue
            return PodInfo(
                id=pod["id"],
                url=f"https://{pod['id']}-8888.proxy.runpod.net",
                token=env["AGENT_TOKEN"],
                usd_h=float(pod.get("costPerHr") or 0),
                descricao=str(opcao),
                gpus=gpus,
            )
        raise runpod_pods.SemVaga("; ".join(tentativas) or "nenhuma opção")

    def encerrar(self, pod_id: str) -> None:
        runpod_pods.encerrar(pod_id, self.chave)


# --- comandos no pod ------------------------------------------------------------------


@dataclass
class Remoto:
    """Comandos executados no pod (os testes trocam por equivalentes locais)."""

    raiz: str = "/root/gpu2go"
    verificar_cuda: bool = True
    instalar: str = (
        "bash {raiz}/bootstrap_pod.sh {raiz}/repo.tgz i2v_r1v2 > {raiz}/bootstrap.log 2>&1"
    )
    servicos: str = (
        "cd /opt/gpu2go/worker && export PATH=/opt/venv/bin:$PATH "
        "PYTHONPATH=/opt/gpu2go/worker/src OLLAMA_MODELS=/opt/ollama/models "
        "COMFY_DIR=/opt/comfyui GPU2GO_WORKER_DIR=/opt/gpu2go/worker COMFY_GPUS=auto "
        "&& env -u PUBLIC_KEY bash entrypoint.sh"
    )
    lote: str = (
        "PATH=/opt/venv/bin:$PATH PYTHONPATH=/opt/gpu2go/worker/src "
        "python /opt/gpu2go/tools/lote.py"
    )


CUINIT = (
    'python3 -c \'import ctypes;l=ctypes.CDLL("libcuda.so.1");r=l.cuInit(0);'
    "n=ctypes.c_int();l.cuDeviceGetCount(ctypes.byref(n));v=ctypes.c_int();"
    'l.cuDriverGetVersion(ctypes.byref(v));print("cuInit",r,"gpus",n.value,"driver",v.value)\''
)
CUDA_MINIMA = 12080  # PyTorch +cu128 exige driver com CUDA ≥ 12.8 (driver ≥ 570)
# Rede mínima: 37 GB de modelos a 40 MB/s ≈ 15 min. Em 07/10/2026 um host RTX PRO 6000 baixava
# a 2,9 MB/s (3,5 h só de modelos, além do limite de 2 h do pod); o normal é ~500 MB/s.
REDE_MINIMA_MBS = 40
REDE = (  # 4 conexões por 15 s, como o download real (xargs -P 4); soma em MB/s
    "python3 -c 'import time,threading,urllib.request as u\n"
    "t=[0]*4;i=time.time()\n"
    "def f(k):\n"
    ' h={{"User-Agent":"gpu2go","Range":"bytes=%d-"%(k*2**30)}}\n'
    ' r=u.urlopen(u.Request("{url}",headers=h),timeout=20)\n'
    " while time.time()-i<15:\n"
    "  b=r.read(1<<22)\n"
    "  if not b: break\n"
    "  t[k]+=len(b)\n"
    "h=[threading.Thread(target=f,args=(k,)) for k in range(4)]\n"
    "[x.start() for x in h];[x.join(25) for x in h]\n"
    'print("rede",round(sum(t)/1e6/(time.time()-i),1))\''
)


def url_teste_rede() -> str:
    """O primeiro modelo do conjunto I2V (a mesma origem dos downloads de verdade)."""
    from gpu2go_worker import models

    sets, entradas = models.load_lock()
    return entradas[sets["i2v_r1v2"][0]].url


def rede_ok(saida: str) -> str | None:
    m = re.search(r"rede ([\d.]+)", saida)
    if not m:
        return f"teste de rede sem resposta ({saida.strip()[-120:]})"
    mbs = float(m.group(1))
    if mbs < REDE_MINIMA_MBS:
        return f"rede lenta ({mbs:.1f} MB/s; mínimo {REDE_MINIMA_MBS})"
    return None


def cuda_ok(saida: str, gpus: int) -> str | None:
    """Confere a saída de CUINIT; devolve o problema ou None."""
    m = re.search(r"cuInit (-?\d+) gpus (\d+) driver (\d+)", saida)
    if not m:
        return f"teste de CUDA sem resposta ({saida.strip()[:120]})"
    r, n, driver = map(int, m.groups())
    if r != 0 or n != gpus:
        return f"GPU com defeito (cuInit {r}, {n} de {gpus} GPUs)"
    if driver < CUDA_MINIMA:
        return f"driver antigo (CUDA {driver // 1000}.{driver % 1000 // 10}; mínimo 12.8)"
    return None


# --- plano local ----------------------------------------------------------------------


@dataclass
class Foto:
    nome: str
    preparada: Path
    tarefas: list[lote.Tarefa]
    aviso: str = ""


def nome_da(t: lote.Tarefa) -> str:
    return t.arquivo.rsplit("-v", 1)[0]


def planejar(a: Namespace) -> tuple[list[Foto], list[str]]:
    ns = Namespace(
        prompt=a.prompt,
        receita=a.receita,
        preset=a.preset,
        formato=a.formato,
        duracao=a.duracao,
        variacoes=a.variacoes,
        consentimento=a.consentimento,
        pasta=a.pasta,
        qualidade=a.qualidade,
        subpastas=a.subpastas,
        pedidos=None,
    )
    plano = lote.planejar(ns)
    if plano.erros:
        return [], plano.erros
    fotos: dict[str, Foto] = {}
    erros = []
    destino = a.saida / ESTADO / "fotos"
    for t in plano.tarefas:
        nome = nome_da(t)
        if nome not in fotos:
            try:
                p = preparo.preparar(t.foto, t.width, t.height, destino / f"{nome}.png")
            except preparo.PreparoError as e:
                erros.append(f"{t.foto.name}: {e}")
                continue
            fotos[nome] = Foto(nome, p.path, [], p.aviso)
        fotos[nome].tarefas.append(t)
    return list(fotos.values()), erros


def args_lote(a: Namespace) -> list[str]:
    """Argumentos do lote.py no pod (os mesmos do plano local)."""
    out = ["--prompt", a.prompt, "--receita", a.receita, "--qualidade", a.qualidade]
    out += ["--formato", a.formato, "--variacoes", str(a.variacoes), "--sem-preparo"]
    for p in a.preset:
        out += ["--preset", p]
    if a.duracao:
        out += ["--duracao", str(a.duracao)]
    if a.consentimento:
        out.append("--consentimento")
    return out


def segundos_por_video(gpu: str, qualidade: str, duracao: int) -> float:
    base = MEDIDO_S.get(gpu, PIOR_S)[qualidade]
    return base * duracao / 5


@dataclass
class Estimativa:
    videos: int
    gpus: int
    minutos: float
    usd: float


def estimar(n_videos: int, pods: int, gpus_por_pod: int, s_video: float, usd_h: float):
    gpus = pods * gpus_por_pod
    render_s = n_videos * s_video / gpus
    total_s = SUBIDA_S + render_s
    usd = pods * usd_h * total_s / 3600
    return Estimativa(n_videos, gpus, total_s / 60, usd)


# --- execução -------------------------------------------------------------------------


@dataclass
class Pod:
    info: PodInfo
    agente: Agente
    criado: float = field(default_factory=time.monotonic)
    vivo: bool = True

    @property
    def nome(self) -> str:
        return f"{self.info.id} ({self.info.descricao})"


COLUNAS = [*lote.COLUNAS_RELATORIO, "pod"]


class Orquestrador:
    def __init__(self, a: Namespace, fotos: list[Foto], provedor: Provedor, remoto: Remoto):
        self.a, self.fotos, self.provedor, self.remoto = a, fotos, provedor, remoto
        self.lock = threading.Lock()
        self.pods: list[Pod] = []
        self.pacotes: queue.Queue[list[Foto]] = queue.Queue()
        self.prompts_ok = threading.Event()
        self.compilando = threading.Lock()
        self.lote_json: Path = a.saida / ESTADO / "lote.json"
        self.recusado = False
        self.rel_path = a.saida / lote.RELATORIO
        self.relatorio = lote._ler_relatorio(self.rel_path)
        self.acab = ThreadPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) // 2))
        self.acab_futuros: list = []
        self.contador = 0

    # estado em disco, para --limpar depois de uma interrupção
    def _salvar_estado(self) -> None:
        est = self.a.saida / ESTADO / "pods.json"
        est.parent.mkdir(parents=True, exist_ok=True)
        dados = [{"id": p.info.id, "descricao": p.info.descricao} for p in self.pods if p.vivo]
        est.write_text(json.dumps(dados, indent=1))

    def _encerrar(self, pod: Pod, motivo: str) -> None:
        with self.lock:
            if not pod.vivo:
                return
            pod.vivo = False
        try:
            self.provedor.encerrar(pod.info.id)
            _log(f"pod {pod.nome} encerrado ({motivo})")
        except Exception as e:  # noqa: BLE001 (o desligamento automático cobre)
            _log(f"AVISO: falha ao encerrar {pod.nome}: {e}; o desligamento automático cobre")
        with self.lock:
            self._salvar_estado()

    def _subir_pod(self, i: int, deadman_s: int) -> Pod | None:
        token = secrets.token_urlsafe(32)
        env = {
            "AGENT_TOKEN": token,
            "AGENT_B64": base64.b64encode(Path(pod_agente_path()).read_bytes()).decode(),
            "DEADMAN_S": str(deadman_s),
            "NVIDIA_DRIVER_CAPABILITIES": "compute,utility",
        }
        try:
            info = self.provedor.criar(f"gpu2go-lote-{i}", self.a.opcoes, self.a.gpus_por_pod, env)
        except runpod_pods.SemVaga as e:
            _log(f"pod {i}: sem GPU disponível ({e})")
            return None
        except runpod_pods.RunPodError as e:
            _log(f"pod {i}: o RunPod recusou a criação ({e})")
            return None
        pod = Pod(info, Agente(info.url, info.token))
        with self.lock:
            self.pods.append(pod)
            self._salvar_estado()
        _log(f"pod {pod.nome} criado, US$ {info.usd_h:.2f}/h; esperando o agente...")
        try:
            self._instalar(pod)
        except (NuvemError, OSError) as e:
            _log(f"pod {pod.nome} descartado: {e}")
            self._encerrar(pod, "falhou na subida")
            return None
        return pod

    def _instalar(self, pod: Pod) -> None:
        r, ag = self.remoto, pod.agente
        limite = time.monotonic() + ESPERA_AGENTE_S
        while not ag.saude():
            if time.monotonic() > limite:
                raise NuvemError(f"agente não respondeu em {ESPERA_AGENTE_S // 60} min")
            time.sleep(10)
        if r.verificar_cuda:
            _, out = ag.exec(CUINIT, 30)
            problema = cuda_ok(out, pod.info.gpus)
            if problema:
                raise NuvemError(problema)
            _, out = ag.exec(REDE.format(url=url_teste_rede()), 60)
            problema = rede_ok(out)
            if problema:
                raise NuvemError(problema)
            _log(f"pod {pod.nome}: {out.strip()} MB/s")
        _log(f"pod {pod.nome}: agente, CUDA e rede ok; instalando o worker (~6–10 min)")
        ag.put(self.a.repo_tgz, f"{r.raiz}/repo.tgz")
        ag.put(WORKER_DIR / "scripts" / "bootstrap_pod.sh", f"{r.raiz}/bootstrap_pod.sh")
        ag.bg(r.instalar.format(raiz=r.raiz), f"{r.raiz}/instalar.log")
        estado = self._esperar(
            pod, f"cat {r.raiz}/estado 2>/dev/null", ("PRONTO", "FALHOU"), ESPERA_INSTALACAO_S
        )
        if not estado.startswith("PRONTO"):
            _, log = ag.exec(f"tail -5 {r.raiz}/bootstrap.log {r.raiz}/*.log 2>/dev/null", 30)
            raise NuvemError(f"instalação: {estado.strip()} | {log[-300:]}")
        ag.bg(r.servicos, f"{r.raiz}/servicos.log")
        self._esperar(
            pod,
            f"grep -q 'gpu2go\\] ERRO' {r.raiz}/servicos.log && echo ERRO; "
            f"grep -q 'gpu2go\\] pronto' {r.raiz}/servicos.log && echo PRONTO",
            ("PRONTO",),
            ESPERA_SERVICOS_S,
            falha="ERRO",
        )
        _log(f"pod {pod.nome}: pronto ({pod.info.gpus} GPU)")

    def _esperar(self, pod, cmd, fins, limite_s, falha: str | None = None) -> str:
        limite = time.monotonic() + limite_s
        while time.monotonic() < limite:
            _, out = pod.agente.exec(cmd, 30)
            out = out.strip()
            if falha and falha in out:
                raise NuvemError(f"falhou: {cmd.split()[0]}")
            if any(out.startswith(f) for f in fins):
                return out
            time.sleep(10)
        raise NuvemError(f"tempo esgotado ({limite_s // 60} min)")

    def _compilar(self, pod: Pod) -> None:
        """O primeiro pod pronto compila; os outros esperam e reaproveitam o lote.json."""
        with self.compilando:
            if self.prompts_ok.is_set() or self.recusado:
                return
            if self.lote_json.is_file():
                self.prompts_ok.set()
                return
            r, f = self.remoto, self.fotos[0]
            d = f"{r.raiz}/compilar"
            pod.agente.put(f.preparada, f"{d}/fotos/{f.nome}.png")
            cmd = shlex.join(["--saida", f"{d}/saida", "--so-compilar", *args_lote(self.a)])
            code, out = pod.agente.exec(f"{r.lote} {d}/fotos {cmd}", 300)
            if code == lote.EXIT_RECUSADO:
                self.recusado = True
                raise NuvemError("o compilador recusou o pedido")
            if code != 0 or not pod.agente.get(f"{d}/saida/lote.json", self.lote_json):
                raise NuvemError(f"compilação falhou: {out[-300:]}")
            prompts = json.loads(self.lote_json.read_text())["prompts"]
            for p in prompts.values():
                _log(f"prompt do lote: {p['prompt_en']}")
            self.prompts_ok.set()

    def _rodar_pacote(self, pod: Pod, pacote: list[Foto], n: int) -> None:
        r, ag = self.remoto, pod.agente
        d = f"{r.raiz}/p{n:04d}"
        for f in pacote:
            ag.put(f.preparada, f"{d}/fotos/{f.nome}.png")
        ag.put(self.lote_json, f"{d}/saida/lote.json")
        cmd = shlex.join(
            ["--saida", f"{d}/saida", "--gpus", str(pod.info.gpus), *args_lote(self.a)]
        )
        ag.bg(f"{r.lote} {d}/fotos {cmd}; echo $? > {d}/rc", f"{d}/lote.log")
        # nunca passa do ponto em que ainda dá para baixar antes do desligamento automático
        limite = min(
            time.monotonic() + 600 + 3 * self._tempo_pacote(pod, pacote),
            pod.criado + self.deadman_s - MARGEM_S / 2,
        )
        while True:
            _, rc = ag.exec(f"cat {d}/rc 2>/dev/null", 30)
            if rc.strip():
                break
            if time.monotonic() > limite:
                raise NuvemError(f"pacote {n} passou do tempo")
            time.sleep(10)
        tmp = self.a.saida / ESTADO / f"p{n:04d}-relatorio.csv"
        if not ag.get(f"{d}/saida/{lote.RELATORIO}", tmp):
            _, log = ag.exec(f"tail -20 {d}/lote.log", 30)
            raise NuvemError(f"pacote {n} sem relatório (rc {rc.strip()}): {log[-400:]}")
        avisos = {f.nome: f.aviso for f in pacote}
        with tmp.open(newline="", encoding="utf-8") as fh:
            linhas = list(csv.DictReader(fh))
        for row in linhas:
            if row["status"] == "ok":
                destino = self.a.saida / row["arquivo"]
                if not ag.get(f"{d}/saida/{row['arquivo']}", destino):
                    row["status"], row["erro"] = "erro", "vídeo não encontrado no pod"
                else:
                    stem = Path(row["arquivo"]).stem
                    ag.get(
                        f"{d}/saida/conferencia/{stem}.jpg",
                        self.a.saida / "conferencia" / f"{stem}.jpg",
                    )
                    self._acabar(destino)
            row["pod"] = pod.info.id
            nome = row["arquivo"].rsplit("-v", 1)[0]
            row["aviso"] = "; ".join(x for x in (avisos.get(nome, ""), row.get("aviso", "")) if x)
            with self.lock:
                self.relatorio[row["arquivo"]] = row
                self.contador += 1
                prog = f"[{self.contador}/{self.total}] {row['arquivo']}"
                _log(f"{prog}: {row['status']} ({pod.info.id})")
        with self.lock:
            self._gravar_relatorio()
        ag.exec(f"rm -rf {d}", 60)

    def _tempo_pacote(self, pod: Pod, pacote: list[Foto]) -> float:
        """Estimativa (s) de um pacote: envio e serviços + vídeos divididos pelas GPUs."""
        n_videos = sum(len(f.tarefas) for f in pacote)
        s = segundos_por_video(self.a.opcoes[0].gpu, self.a.qualidade, pacote[0].tarefas[0].duracao)
        return 120 + n_videos * s / pod.info.gpus

    def _gravar_relatorio(self) -> None:
        tmp = self.rel_path.with_suffix(".csv.tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUNAS, extrasaction="ignore")
            w.writeheader()
            for arq in sorted(self.relatorio):
                w.writerow(self.relatorio[arq])
        tmp.replace(self.rel_path)

    def _acabar(self, video: Path) -> None:
        o = self.a.acabamento
        if o is None:
            return
        final = self.a.saida / "final" / video.name
        self.acab_futuros.append(self.acab.submit(acabamento.acabar, [video], final, o))

    def _trabalhar(self, i: int, deadman_s: int) -> None:
        pod = self._subir_pod(i, deadman_s)
        if pod is None:
            return
        try:
            self._compilar(pod)
            self.prompts_ok.wait()
            n = 0
            while not self.recusado:
                try:
                    pacote = self.pacotes.get_nowait()
                except queue.Empty:
                    break
                vivo_s = time.monotonic() - pod.criado
                if vivo_s + self._tempo_pacote(pod, pacote) > deadman_s - MARGEM_S:
                    self.pacotes.put(pacote)
                    _log(f"pod {pod.nome}: sem tempo seguro antes do limite de 2 h; parando")
                    break
                with self.lock:
                    self.n_pacote += 1
                    n = self.n_pacote
                try:
                    self._rodar_pacote(pod, pacote, n)
                except (NuvemError, OSError) as e:
                    self.pacotes.put(pacote)  # outro pod pega
                    raise NuvemError(f"pacote {n} devolvido à fila: {e}") from e
        except NuvemError as e:
            _log(f"pod {pod.nome}: {e}")
            if self.recusado:
                self.prompts_ok.set()
        finally:
            self._encerrar(pod, "fim do trabalho")

    def rodar(self, deadman_s: int) -> int:
        self.deadman_s = deadman_s
        pendentes = []
        for f in self.fotos:
            prontos = all(
                self.relatorio.get(t.arquivo, {}).get("status") == "ok"
                and (self.a.saida / t.arquivo).is_file()
                for t in f.tarefas
            )
            if not prontos:
                pendentes.append(f)
        self.total = sum(len(f.tarefas) for f in pendentes)
        if not pendentes:
            _log("nada a fazer: todos os vídeos já estão prontos")
            return EXIT_OK
        tam = FOTOS_POR_GPU * self.a.gpus_por_pod
        for i in range(0, len(pendentes), tam):
            self.pacotes.put(pendentes[i : i + tam])
        self.n_pacote = 0
        pods = min(self.a.pods, self.pacotes.qsize())
        threads = [
            threading.Thread(target=self._trabalhar, args=(i, deadman_s), daemon=True)
            for i in range(pods)
        ]
        try:
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            for p in list(self.pods):
                self._encerrar(p, "fim do lote")
            for fut in self.acab_futuros:
                try:
                    fut.result()
                except (acabamento.AcabamentoError, OSError, subprocess.CalledProcessError) as e:
                    _log(f"AVISO: acabamento falhou: {e}")
            self.acab.shutdown()
        if self.recusado:
            return EXIT_RECUSADO
        if not self.pods:
            return EXIT_SEM_GPU
        if not self.pacotes.empty():
            _log(f"{self.pacotes.qsize()} pacote(s) pendente(s): rode de novo para continuar")
            return EXIT_PENDENTE
        falhas = sum(
            1
            for f in pendentes
            for t in f.tarefas
            if self.relatorio.get(t.arquivo, {}).get("status") != "ok"
        )
        return EXIT_FALHA if falhas else EXIT_OK


def _interromper(*_: object) -> None:
    raise KeyboardInterrupt("SIGTERM")


def pod_agente_path() -> Path:
    return Path(__file__).with_name("pod_agente.py")


def empacotar_repo(destino: Path) -> Path:
    """O código que vai para o pod: o commit atual do repositório (git archive)."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(REPO), "archive", "--format=tar.gz", "-o", str(destino), "HEAD"],
        check=True,
    )
    return destino


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gpu2go-lote-nuvem",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n\n".join(__doc__.split("\n\n")[1:]),
    )
    p.add_argument("pasta", type=Path, nargs="?", help="pasta com as fotos do cliente")
    p.add_argument("--saida", type=Path, required=True, help="pasta final dos vídeos")
    p.add_argument("--prompt", help="pedido em português, o mesmo para todas as fotos")
    p.add_argument("--receita", default="produto-close")
    p.add_argument("--preset", action="append", default=[], metavar="CATEGORIA=OPÇÃO")
    p.add_argument("--formato", default="auto")
    p.add_argument("--qualidade", choices=lote.QUALIDADES, default="rapida")
    p.add_argument("--duracao", type=int)
    p.add_argument("--variacoes", type=int, default=1)
    p.add_argument("--consentimento", action="store_true")
    p.add_argument("--subpastas", action="store_true")
    g = p.add_argument_group("pods")
    g.add_argument("--pods", type=int, default=1, help="quantos pods em paralelo (padrão 1)")
    g.add_argument(
        "--gpu",
        action="append",
        help="GPU em ordem de preferência (repetível); padrão: L40S, L40, RTX 6000 Ada, RTX 4090",
    )
    g.add_argument(
        "--nuvem",
        choices=["secure", "qualquer", "community"],
        default="secure",
        help="padrão: secure (decisão de 07/10/2026; hosts community falharam no CUDA)",
    )
    g.add_argument("--gpus-por-pod", type=int, default=1, help="GPUs por pod (1, 2, 4, 8)")
    g.add_argument(
        "--orcamento-usd", type=float, default=5.0, help="não sobe pods se a estimativa passar"
    )
    g.add_argument(
        "--datacentro",
        action="append",
        default=[],
        help="só estes datacenters do RunPod (repetível), ex.: US-KS-2",
    )
    g.add_argument("--sim", action="store_true", help="não pede confirmação")
    g.add_argument("--simular", action="store_true", help="só planeja e estima, sem pods")
    g.add_argument("--limpar", action="store_true", help="encerra pods de uma execução anterior")
    f = p.add_argument_group("acabamento (aqui, na CPU)")
    f.add_argument("--interpolar", action="store_true")
    f.add_argument("--altura", type=int)
    f.add_argument("--zoom", action="store_true")
    return p


def limpar(saida: Path, provedor: Provedor) -> int:
    est = saida / ESTADO / "pods.json"
    if not est.is_file():
        _log("nenhum pod registrado")
        return EXIT_OK
    for p in json.loads(est.read_text()):
        try:
            provedor.encerrar(p["id"])
            _log(f"encerrado {p['id']}")
        except Exception as e:  # noqa: BLE001
            _log(f"falha ao encerrar {p['id']}: {e}")
    est.write_text("[]")
    return EXIT_OK


def main(
    argv: list[str] | None = None, provedor: Provedor | None = None, remoto: Remoto | None = None
) -> int:
    p = build_parser()
    a = p.parse_args(argv)
    if threading.current_thread() is threading.main_thread():
        # kill/timeout (SIGTERM) passa pelo mesmo caminho do Ctrl+C: encerra os pods antes de sair
        signal.signal(signal.SIGTERM, _interromper)
    gpus = a.gpu or GPUS_PADRAO
    nuvens = ["community", "secure"] if a.nuvem == "qualquer" else [a.nuvem]
    a.opcoes = [runpod_pods.Opcao(g, n) for g in gpus for n in nuvens]
    a.acabamento = (
        acabamento.Opcoes(interpolar=a.interpolar, altura=a.altura, zoom=a.zoom)
        if (a.interpolar or a.altura or a.zoom)
        else None
    )
    try:
        provedor = provedor or (None if a.simular else RunPod(a.datacentro))
    except runpod_pods.RunPodError as e:
        _log(f"ERRO {e}")
        return EXIT_SEM_GPU
    if a.limpar:
        return limpar(a.saida, provedor)
    if a.pasta is None or a.prompt is None:
        p.error("informe a pasta e o --prompt")
    if not 1 <= a.pods <= 20 or a.gpus_por_pod not in (1, 2, 4, 8):
        p.error("--pods de 1 a 20; --gpus-por-pod 1, 2, 4 ou 8")

    try:
        fotos, erros = planejar(a)
    except (lote.LoteError, lote.presets.PresetError) as e:
        erros = [str(e)]
    if erros:
        for e in erros:
            _log(f"ERRO {e}")
        _log(f"pedido inválido: {len(erros)} erro(s); nenhum pod foi criado")
        return EXIT_PEDIDO
    n_videos = sum(len(f.tarefas) for f in fotos)
    s_video = segundos_por_video(gpus[0], a.qualidade, fotos[0].tarefas[0].duracao)
    usd_h = None
    if provedor is not None:
        usd_h = next((x for o in a.opcoes if (x := provedor.preco(o.gpu, o.nuvem))), None)
    usd_h = usd_h or 1.10
    est = estimar(
        n_videos, min(a.pods, len(fotos)), a.gpus_por_pod, s_video, usd_h * a.gpus_por_pod
    )
    _log(
        f"{len(fotos)} foto(s), {n_videos} vídeo(s) em {est.gpus} GPU(s) ({gpus[0]}, "
        f"~{s_video:.0f} s por vídeo): ~{est.minutos:.0f} min e ~US$ {est.usd:.2f} "
        f"(US$ {usd_h:.2f}/h por GPU; subida de ~{SUBIDA_S // 60} min incluída)"
    )
    if a.simular:
        return EXIT_OK
    if est.usd > a.orcamento_usd:
        _log(f"estimativa acima do orçamento (US$ {a.orcamento_usd:.2f}); use --orcamento-usd")
        return EXIT_SEM_GPU
    if not a.sim and (
        not sys.stdin.isatty() or input("subir os pods? [s/N] ").strip().lower() != "s"
    ):
        _log("cancelado (use --sim para não perguntar)")
        return EXIT_SEM_GPU
    a.repo_tgz = empacotar_repo(a.saida / ESTADO / "repo.tgz")
    # desligamento automático: 2× a estimativa + 20 min, no máximo 2 h (regra do projeto)
    deadman_s = int(min(MAX_POD_S, 2 * est.minutos * 60 + 1200))
    if est.minutos * 60 > MAX_POD_S - MARGEM_S:
        _log(
            f"AVISO: ~{est.minutos:.0f} min passa do limite de 2 h por pod; o que não couber "
            "fica pendente (rode de novo) ou use mais --pods"
        )
    t0 = time.monotonic()
    orq = Orquestrador(a, fotos, provedor, remoto or Remoto())
    code = orq.rodar(deadman_s)
    custo = sum(p.info.usd_h * (time.monotonic() - p.criado) / 3600 for p in orq.pods)
    _log(
        f"fim em {(time.monotonic() - t0) / 60:.1f} min; custo estimado dos pods ~US$ {custo:.2f}; "
        f"relatório em {orq.rel_path}"
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
