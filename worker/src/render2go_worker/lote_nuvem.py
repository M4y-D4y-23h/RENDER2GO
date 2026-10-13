"""Lote na nuvem com um comando (ADR 0003, fase 2): pasta de fotos → vídeos na pasta final.

    render2go-lote-nuvem fotos/loja --saida entregas/loja \\
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
import dataclasses
import hashlib
import json
import os
import queue
import re
import secrets
import shlex
import signal
import subprocess
import sys
import tarfile
import threading
import time
from argparse import Namespace
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import requests

from render2go_worker import WORKER_DIR, acabamento, lote, preparo, runpod_pods
from render2go_worker.armazenamento import ArmazenamentoError
from render2go_worker.espelho import Espelho

REPO = WORKER_DIR.parent
EXIT_OK, EXIT_FALHA, EXIT_PEDIDO, EXIT_RECUSADO, EXIT_SEM_GPU, EXIT_PENDENTE = 0, 1, 2, 3, 4, 5
# Segundos por vídeo de 5 s medidos (worker/evals/gpu-2026-10-06*); o resto usa o pior caso.
MEDIDO_S = {
    "NVIDIA RTX PRO 6000 Blackwell Server Edition": {"rapida": 28, "alta": 93},
    "NVIDIA L40S": {"rapida": 55, "alta": 180},
    "NVIDIA GeForce RTX 4090": {"rapida": 80, "alta": 213},
}
PIOR_S = {"rapida": 80, "alta": 213}
EXPOENTE_DURACAO = 1.6  # 160 s / 93 s para 7 s / 5 s (720p, RTX PRO 6000)
# Ordem padrão (08/10/2026), sempre na nuvem secure. A RTX PRO 6000 subiu de US$ 2,09 para
# US$ 2,49/h e deixou de ser a 1ª: a subida (~3 min) e o pod quente (3 min) são pagos por hora em
# todo pedido, e a L40S cobra menos da metade por hora com custo por vídeo ~14% menor.
#   1. L40S, 48 GB (medida: ~55 s, US$ 1,09/h);
#   2. RTX 6000 Ada, 48 GB (o mesmo chip AD102 e o mesmo cálculo de IA da L40S, US$ 0,99/h; sem
#      medição, as estimativas usam o pior caso);
#   3. RTX PRO 6000, 96 GB (medida: ~28 s, a mais rápida), quando as de 48 GB não têm vaga;
#   4. RTX 4090, 24 GB, por último (troca de modelo a cada vídeo).
# Fora: L40 (metade do cálculo de IA da L40S) e as Ampere (A40, A100, A6000: sem fp8).
# O orquestrador do serviço troca a ordem por RENDER2GO_GPUS, sem mudar o código.
GPUS_PADRAO = [
    "NVIDIA L40S",
    "NVIDIA RTX 6000 Ada Generation",
    "NVIDIA RTX PRO 6000 Blackwell Server Edition",
    "NVIDIA GeForce RTX 4090",
]
# sem preço na consulta: o da mais cara da lista (orçamento e desligamento automático ficam do lado
# seguro, já que a mais barata vem primeiro)
PRECO_RESERVA_USD_H = 2.49
SUBIDA_S = 5 * 60  # agente + instalação + serviços (medido em 07/10, download em partes: 2,5–3 min)
# Regra do dono do projeto (07/10/2026): pod nenhum fica ligado mais de 2 h. O lote para
# de mandar pacotes a tempo de baixar tudo (MARGEM_S) antes do desligamento automático.
MAX_POD_S = 2 * 3600
MARGEM_S = 10 * 60
ESPERA_AGENTE_S = 600
# 37 GB a 40 MB/s (o mínimo aceito) ≈ 15 min; o normal é 4–7 min. Passou disso, troca o pod.
ESPERA_INSTALACAO_S = 18 * 60
# Pod com vazão pior que LENTO × o medido para a GPU é trocado (servidor lento ou com defeito).
LENTO = 1.6
FOLGA_S = 15  # tolerância fixa por vídeo (envio, download, carga do 1º vídeo)
SUBSTITUTOS = 2  # pods trocados por pod pedido, no máximo
ESPERA_VAGA_S = 60  # sem vaga: espera antes da próxima tentativa
CACHE = Path(os.environ.get("RENDER2GO_CACHE", Path.home() / ".cache" / "render2go"))
MEMORIA_DIAS = 7  # datacenter lento fica de fora por esse tempo
RESERVA_SALDO_USD = 1.0  # nunca gastar o saldo da conta até o fim
MIN_POD_S = 15 * 60  # se o saldo/orçamento não pagam nem isso, não sobe
ESPERA_SERVICOS_S = 600
ESPERA_GUARDAR_S = 15 * 60  # pod gravador: prazo para terminar o cache no volume
# intervalo das consultas ao agente durante a subida (era 10 s: perdia ~5 s em média por etapa)
CONSULTA_S = 3
FOTOS_POR_GPU = 2
ESTADO = ".nuvem"
# Pods que hibernam (ADR 0011): ambiente e modelos no disco de volume do próprio pod (local; fica no
# host quando o pod é parado, cobrado à parte). Na volta, nada é baixado nem instalado.
VOLUME_HIB = "/workspace"
LOCAL_HIB = "/workspace/r2g"
PISO_HIB_S = 20 * 60  # prazo inicial do pod que hiberna, até o orquestrador mandar o de verdade


def _log(msg: str) -> None:
    print(time.strftime("%H:%M:%S ") + msg, file=sys.stderr, flush=True)


class NuvemError(Exception):
    pass


# --- agente do pod --------------------------------------------------------------------


class AgenteError(NuvemError):
    pass


class PodLento(NuvemError):
    """O pod funciona, mas rende abaixo do esperado: é trocado por outro."""


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

    def deadman(self, s: float) -> float:
        """Reprograma o desligamento automático para `s` segundos a partir de agora (o agente nunca
        passa de 2 h da subida); devolve os segundos que valeram."""
        r = self._req("POST", "/deadman", json={"s": s}, timeout=30)
        if r.status_code != 200:
            raise AgenteError(f"desligamento automático: HTTP {r.status_code} {r.text[:200]}")
        return float(r.json()["em_s"])

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
    gpu: str = ""
    datacentro: str | None = None


class Provedor(Protocol):
    def preco(self, gpu: str, nuvem: str) -> float | None: ...

    def criar(
        self, nome: str, opcoes: list[runpod_pods.Opcao], gpus: int, env: dict
    ) -> PodInfo: ...

    def encerrar(self, pod_id: str) -> None: ...


def _memoria_path() -> Path:
    return CACHE / "datacentros.json"


def _memoria() -> dict:
    path = _memoria_path()
    try:
        dados = json.loads(path.read_text()) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return {}
    return dados if isinstance(dados, dict) else {}


def lembrar_datacentro(dc: str | None, mbs: float | None, motivo: str = "") -> None:
    """Anota a rede de um datacenter (aqui, na máquina de quem roda o comando): a última medida,
    a média das recentes (metade de cada, para um host ruim não condenar o datacenter de vez) e o
    motivo, se ele foi descartado (lento ou com instalação que não terminou)."""
    if not dc:
        return
    dados = _memoria()
    antes = dados.get(dc) or {}
    media = antes.get("media")
    if mbs is not None:
        recente = time.time() - float(antes.get("quando") or 0) < MEMORIA_DIAS * 86400
        media = round(0.5 * float(media) + 0.5 * mbs, 1) if recente and media is not None else mbs
    dados[dc] = {
        "mbs": mbs,
        "media": media,
        "n": int(antes.get("n") or 0) + (mbs is not None),
        "quando": time.time(),
        "motivo": motivo,
    }
    path = _memoria_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dados, indent=1, ensure_ascii=False))


def velocidades() -> dict[str, float]:
    """MB/s médio do download dos modelos em cada datacenter medido nos últimos MEMORIA_DIAS."""
    limite = time.time() - MEMORIA_DIAS * 86400
    out = {}
    for dc, d in _memoria().items():
        v = d.get("media") if d.get("media") is not None else d.get("mbs")
        if v is not None and float(d.get("quando") or 0) > limite:
            out[dc] = float(v)
    return out


def datacentros_lentos() -> set[str]:
    limite = time.time() - MEMORIA_DIAS * 86400
    return {dc for dc, d in _memoria().items() if d.get("quando", 0) > limite and d.get("motivo")}


# Sem medida, a ordem dos datacenters é esta (o resto vem por último): a API fica na Alemanha, e os
# hosts medidos até 12/10/2026 eram dos EUA (US-NC-1 e US-NC-2: os 37 GB em 63–85 s). Em 12/10, um
# pod do serviço caiu em OC-AU-1 (Austrália), sem regra nenhuma de lugar.
CONTINENTES = {"United States": 0, "Canada": 0, "Europe": 1, "France": 1}
# filtros do RunPod para a 1ª tentativa de cada subida: máquina com download e disco medidos pelo
# RunPod acima disto (0 desliga). Sem vaga com eles, a subida tenta de novo sem os filtros
REDE_MIN_MBPS = 2000  # megabits/s (~250 MB/s: 37 GB em ~2,5 min)
DISCO_MIN_MBS = 1000  # megabytes/s
SEM_RAPIDOS_S = 600  # sem vaga com os filtros: a próxima subida nem tenta com eles por 10 min


AGENTE_CMD = 'echo "$AGENT_B64" | base64 -d > /agente.py && exec python3 /agente.py'


class RunPod:
    """Pods do RunPod com o agente injetado (imagem pública python:3.12-slim)."""

    IMAGEM = "python:3.12-slim-bookworm"

    def __init__(self, datacentros: list[str] | None = None, volume: str | None = None) -> None:
        self.chave = runpod_pods._chave()
        self.datacentros = datacentros or []
        self.volume = volume
        # filtros de máquina rápida da 1ª tentativa (RENDER2GO_REDE_MIN_MBPS e
        # RENDER2GO_DISCO_MIN_MBS; 0 desliga)
        self.rede_min_mbps = int(os.environ.get("RENDER2GO_REDE_MIN_MBPS", REDE_MIN_MBPS))
        self.disco_min_mbs = int(os.environ.get("RENDER2GO_DISCO_MIN_MBS", DISCO_MIN_MBS))
        self._sem_rapidos_ate = 0.0
        self._dcs_cache: tuple[float, list[dict]] | None = None
        if volume:  # o pod precisa nascer no datacenter do volume
            r = runpod_pods._rest("GET", f"/networkvolumes/{volume}", self.chave)
            dc = (r.json() or {}).get("dataCenterId") if r.status_code == 200 else None
            if not dc:
                raise runpod_pods.RunPodError(f"volume {volume} não encontrado ({r.status_code})")
            self.datacentros = [dc]

    def preco(self, gpu: str, nuvem: str) -> float | None:
        for row in runpod_pods.disponibilidade([gpu]):
            if row["nuvem"] == nuvem and row["oferecida"]:
                return row["usd_hora"]
        return None

    def criar(
        self,
        nome: str,
        opcoes: list[runpod_pods.Opcao],
        gpus: int,
        env: dict,
        volume_gb: int = 0,
    ) -> PodInfo:
        """`volume_gb`: disco de volume do próprio pod em /workspace (pod que hiberna, ADR 0011).

        Escolha do host (12/10/2026), em camadas, cada uma com as GPUs na ordem pedida:
        1. máquina rápida: download e disco medidos pelo RunPod acima de RENDER2GO_REDE_MIN_MBPS e
           RENDER2GO_DISCO_MIN_MBS, nos datacenters da GPU do mais rápido para o mais lento na
           medida dos nossos downloads (`velocidades`), sem os lentos lembrados;
        2. os mesmos datacenters, sem os filtros;
        3. qualquer datacenter (como antes; o RunPod escolhe), menos os lentos lembrados.
        Prefere uma GPU de outra linha num host rápido a uma L40S num host lento: o que pesa na
        espera do cliente é a subida (o dono, 12/10/2026: "não pelo custo, mas pela espera")."""
        tentativas = []
        for camada, filtro in self._camadas():
            for opcao in opcoes:
                dcs = self._dcs_da_camada(camada, opcao.gpu)
                if (
                    dcs is None
                ):  # sem estoque da GPU em datacenter nenhum: fica para a última camada
                    continue
                corpo = self._corpo(nome, opcao, gpus, env, volume_gb, dcs, filtro, camada < 3)
                try:
                    pod = runpod_pods.criar(opcao, corpo, self.chave)
                except runpod_pods.SemVaga:
                    tentativas.append(f"{opcao}{' (máquina rápida)' if filtro else ''}: sem vaga")
                    continue
                dc = self._datacentro_do_pod(pod["id"])
                if self.volume and dc and dc not in self.datacentros:
                    runpod_pods.encerrar(pod["id"], self.chave)
                    raise runpod_pods.RunPodError(
                        f"pod nasceu em {dc}, fora do datacenter do volume"
                    )
                return PodInfo(
                    id=pod["id"],
                    url=f"https://{pod['id']}-8888.proxy.runpod.net",
                    token=env["AGENT_TOKEN"],
                    usd_h=float(pod.get("costPerHr") or 0),
                    descricao=str(opcao) + (" · máquina rápida" if filtro else ""),
                    gpus=gpus,
                    gpu=opcao.gpu,
                    datacentro=dc,
                )
            if (
                filtro
            ):  # nenhuma GPU com os filtros: as próximas subidas pulam a camada por um tempo
                self._sem_rapidos_ate = time.monotonic() + SEM_RAPIDOS_S
        raise runpod_pods.SemVaga("; ".join(tentativas) or "nenhuma opção")

    def _corpo(self, nome, opcao, gpus, env, volume_gb, dcs, filtro, ordenar) -> dict:
        # 120 GB também no pod que hiberna: a 1ª subida instala tudo no disco do contêiner
        # (rápido) e só depois copia para o volume (com 40 GB, em 11/10, o disco encheu)
        corpo = runpod_pods.corpo_pod(opcao, self.IMAGEM, nome, 120, env, ["8888/http"])
        corpo["gpuCount"] = gpus
        corpo["dockerStartCmd"] = ["bash", "-c", AGENTE_CMD]
        if volume_gb and not self.volume:
            corpo["volumeInGb"] = volume_gb
            corpo["volumeMountPath"] = VOLUME_HIB
        if self.volume:
            corpo["networkVolumeId"] = self.volume
            corpo["env"] = {**env, "RENDER2GO_VOLUME": "1"}
        if dcs:
            corpo["dataCenterIds"] = dcs
            if ordenar:  # na ordem da lista: os mais rápidos primeiro
                corpo["dataCenterPriority"] = "custom"
        return corpo | filtro

    def _camadas(self) -> list[tuple[int, dict]]:
        """(camada, filtros do RunPod): 1 rápida, 2 datacenters ordenados, 3 qualquer um."""
        if self.volume or self.datacentros:  # datacenter decidido (volume de rede ou --datacentro)
            return [(3, {})]
        rapido = {}
        if self.rede_min_mbps > 0:
            rapido["minDownloadMbps"] = self.rede_min_mbps
        if self.disco_min_mbs > 0:
            rapido["minDiskBandwidthMBps"] = self.disco_min_mbs
        camadas = [(2, {}), (3, {})]
        if rapido and time.monotonic() >= self._sem_rapidos_ate:
            camadas.insert(0, (1, rapido))
        return camadas

    def _dcs_da_camada(self, camada: int, gpu: str) -> list[str] | None:
        """Datacenters da tentativa: a lista (na ordem), [] = qualquer um, None = pular."""
        if self.volume:  # o volume de rede prende o datacenter (e há datacenters com volume, como
            return []  # o US-NC-2, que a lista dataCenterIds da API REST não aceita)
        if self.datacentros:
            return self.datacentros
        if camada < 3:
            return self._datacentros_ordenados(gpu) or None
        lentos = datacentros_lentos()  # última camada: qualquer um, menos os lentos lembrados
        aceitos = self._datacentros_aceitos() if lentos else set()
        return sorted(aceitos - lentos) if aceitos else []

    def _dcs(self) -> list[dict]:
        """Datacenters e estoque de GPU (GraphQL), lidos no máximo uma vez por minuto."""
        agora = time.monotonic()
        if self._dcs_cache and agora - self._dcs_cache[0] < 60:
            return self._dcs_cache[1]
        d = runpod_pods._graphql(
            "{ dataCenters { id location gpuAvailability { gpuTypeId available } } }", self.chave
        )
        dcs = [dc for dc in d.get("dataCenters") or [] if dc.get("id")]
        self._dcs_cache = (agora, dcs)
        return dcs

    def _datacentros_ordenados(self, gpu: str) -> list[str]:
        """Datacenters com estoque desta GPU agora, sem os lentos lembrados, do mais rápido para o
        mais lento na medida dos nossos downloads (`velocidades`). Os sem medida entram entre os
        rápidos (≥ REDE_BOA_MBS) e os mais lentos, na ordem de CONTINENTES. Só IDs que a API REST
        aceita."""
        try:
            dcs = self._dcs()
        except runpod_pods.RunPodError:
            return []
        lentos, vel, aceitos = datacentros_lentos(), velocidades(), self._datacentros_aceitos()
        com_gpu = [
            dc
            for dc in dcs
            if dc["id"] not in lentos
            and (not aceitos or dc["id"] in aceitos)
            and any(
                g.get("gpuTypeId") == gpu and g.get("available")
                for g in dc.get("gpuAvailability") or []
            )
        ]

        def chave(dc: dict) -> tuple:
            v = vel.get(dc["id"])
            if v is None:
                return (1, CONTINENTES.get(dc.get("location") or "", 2), 0.0)
            return (0 if v >= REDE_BOA_MBS else 2, 0, -v)

        if lentos & {dc["id"] for dc in dcs}:
            _log(f"evitando datacenters lentos: {', '.join(sorted(lentos))}")
        return [dc["id"] for dc in sorted(com_gpu, key=chave)]

    def encerrar(self, pod_id: str) -> None:
        runpod_pods.encerrar(pod_id, self.chave)

    def parar(self, pod_id: str) -> None:
        runpod_pods.parar(pod_id, self.chave)

    def retomar(self, pod_id: str) -> float | None:
        """Liga de novo o pod parado (SemVaga: o host está sem GPU livre agora); devolve o US$/h."""
        pod = runpod_pods.retomar(pod_id, self.chave)
        return float(pod["costPerHr"]) if pod.get("costPerHr") else None

    def situacao(self, pod_id: str) -> str | None:
        """RUNNING, EXITED (parado) ou TERMINATED; None se o pod não existe mais."""
        pod = runpod_pods.ler(pod_id, self.chave)
        return pod.get("desiredStatus") if pod else None

    def saldo(self) -> float | None:
        try:
            d = runpod_pods._graphql("{ myself { clientBalance } }", self.chave)
            return float(d["myself"]["clientBalance"])
        except (runpod_pods.RunPodError, KeyError, TypeError, ValueError):
            return None

    def _datacentros_aceitos(self) -> set[str]:
        """IDs que a API REST aceita em dataCenterIds (lista fixa, menor que a do GraphQL).

        A API devolve a lista na mensagem de erro de um ID inválido; consultada uma vez."""
        if not hasattr(self, "_aceitos"):
            corpo = {"name": "x", "imageName": "x", "gpuTypeIds": ["x"], "dataCenterIds": ["?"]}
            r = runpod_pods._rest("POST", "/pods", self.chave, corpo)
            m = re.search(r"dataCenterIds/items/enum: value must be one of ([^\"\]]+)", r.text)
            self._aceitos = set(re.findall(r"'([A-Z0-9-]+)'", m.group(1))) if m else set()
        return self._aceitos

    def _datacentro_do_pod(self, pod_id: str) -> str | None:
        q = f'{{ pod(input:{{podId:"{pod_id}"}}) {{ machine {{ dataCenterId }} }} }}'
        try:
            return (
                (runpod_pods._graphql(q, self.chave).get("pod") or {}).get("machine") or {}
            ).get("dataCenterId")
        except runpod_pods.RunPodError:
            return None


# --- comandos no pod ------------------------------------------------------------------


@dataclass
class Remoto:
    """Comandos executados no pod (os testes trocam por equivalentes locais)."""

    raiz: str = "/root/render2go"
    local_hib: str = LOCAL_HIB  # disco de volume do pod que hiberna (RENDER2GO_LOCAL, ADR 0011)
    verificar_cuda: bool = True
    instalar: str = (
        "bash {raiz}/bootstrap_pod.sh {raiz}/repo.tgz i2v_r1v2 > {raiz}/bootstrap.log 2>&1"
    )
    servicos: str = (
        "cd /opt/render2go/worker && export PATH=/opt/venv/bin:$PATH "
        "PYTHONPATH=/opt/render2go/worker/src OLLAMA_MODELS=/opt/ollama/models "
        "COMFY_DIR=/opt/comfyui RENDER2GO_WORKER_DIR=/opt/render2go/worker COMFY_GPUS=auto "
        "&& env -u PUBLIC_KEY bash entrypoint.sh"
    )
    lote: str = (
        "PATH=/opt/venv/bin:$PATH PYTHONPATH=/opt/render2go/worker/src "
        "python /opt/render2go/tools/lote.py"
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
# Desde 12/10/2026, o teste de rede é o próprio download dos modelos: o bootstrap mede os primeiros
# 10 s (aria2, 16 conexões por arquivo, 4 arquivos) e escreve "rede <MB/s>" em $R/rede. Antes, eram
# 15 s de teste com 4 conexões, antes de começar a instalar: a subida esperava, e a medida ficava
# abaixo do que o download de verdade fazia (73 MB/s no teste, ~310 MB/s no download, 08/10).
ESPERA_REDE_S = 180  # a medida sai ~15 s depois do início (apt, código e 10 s de download)
# Abaixo disto, o datacenter vai para o fim da fila nas próximas subidas (não é descartado)
REDE_BOA_MBS = 200


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
    # o tempo cresce mais rápido que a duração: 7 s levaram 1,72× os 5 s (RTX PRO 6000, 07/10)
    return base * (duracao / 5) ** EXPOENTE_DURACAO


@dataclass
class Estimativa:
    videos: int
    gpus: int
    minutos: float
    usd: float


# Pods automáticos: cada pod paga uma subida (~3 min, medido em 07/10) antes do 1º vídeo; com
# menos de ~10 min de trabalho por pod (~3× a subida), a subida passa de ~25% do custo.
TRABALHO_MIN_POD_S = 10 * 60
MAX_PODS_AUTO = 8


def escolher_pods(
    n_videos: int,
    n_pacotes: int,
    gpus_por_pod: int,
    s_video: float,
    prazo_min: int | None,
    max_pods: int = MAX_PODS_AUTO,
) -> tuple[int, str]:
    """Quantos pods usar: o mínimo que cumpre o prazo, ou (sem prazo) o máximo que mantém
    cada pod com pelo menos TRABALHO_MIN_POD_S de vídeos. Nunca mais pods que pacotes nem que
    `max_pods` (o serviço passa RENDER2GO_MAX_PODS; a linha de comando fica nos 8 de sempre)."""
    trabalho_s = n_videos * s_video / gpus_por_pod  # GPU-segundos por pod, se fosse um só
    if prazo_min:
        livre_s = prazo_min * 60 - SUBIDA_S
        pods = -(-int(trabalho_s) // int(livre_s)) if livre_s > 0 else MAX_PODS_AUTO
        motivo = f"prazo de {prazo_min} min"
    else:
        pods = int(trabalho_s // TRABALHO_MIN_POD_S)
        motivo = f"cada pod com ≥ {TRABALHO_MIN_POD_S // 60} min de vídeos"
    teto = min(max_pods, max(1, n_pacotes))
    if prazo_min and pods > teto:
        motivo += f" (não cabe: no máximo {teto} pods)"
    return max(1, min(pods, teto)), motivo


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
    fim: float | None = None
    vazao: list[float] = field(default_factory=list)
    grava_volume: bool = False
    espelhando: bool = False
    deadman_s: int = 0  # desligamento automático deste pod (s desde a criação ou a volta)
    hibernavel: bool = False  # ambiente e modelos no disco de volume do pod (ADR 0011)
    retomado: bool = False  # voltou da hibernação (nada foi baixado)
    # o disco de volume foi conferido nesta subida (GUARDADO e o marcador desta versão): só assim o
    # pod que o desligamento automático parou pode ficar no conjunto de hibernados
    volume_ok: bool = False

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
        self.parar = threading.Event()
        self.rel_path = a.saida / lote.RELATORIO
        self.relatorio = lote._ler_relatorio(self.rel_path)
        self.acab = ThreadPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) // 2))
        self.acab_futuros: list = []
        self.contador = 0
        self.gravador: Pod | None = None  # com --volume, só um pod por vez grava o cache
        self.gravador_pendente = False

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
            pod.vivo, pod.fim = False, time.monotonic()
        try:
            self.provedor.encerrar(pod.info.id)
            _log(f"pod {pod.nome} encerrado ({motivo})")
        except Exception as e:  # noqa: BLE001 (o desligamento automático cobre)
            _log(f"AVISO: falha ao encerrar {pod.nome}: {e}; o desligamento automático cobre")
        with self.lock:
            self._salvar_estado()

    def _subir_pod(
        self,
        i: int,
        deadman_s: int,
        cedo: bool = False,
        ao_criar: Callable[[PodInfo], None] | None = None,
        ao_agente: Callable[[Pod], None] | None = None,
        volume_gb: int = 0,
        prefixo: str = "render2go-lote",
    ) -> Pod | None:
        token = secrets.token_urlsafe(32)
        env = {
            "AGENT_TOKEN": token,
            "AGENT_B64": base64.b64encode(Path(pod_agente_path()).read_bytes()).decode(),
            "DEADMAN_S": str(deadman_s),
            "NVIDIA_DRIVER_CAPABILITIES": "compute,utility",
        }
        if volume_gb:  # hiberna em vez de encerrar; prazo curto até o orquestrador mandar o real
            deadman_s = min(deadman_s, PISO_HIB_S)
            env |= {
                "DEADMAN_S": str(deadman_s),
                "DEADMAN_ACAO": "parar",
                "RENDER2GO_LOCAL": self.remoto.local_hib,
            }
        esp = getattr(self.a, "espelho", None)
        with self.lock:  # o 1º pod (ou o substituto dele) grava o cache no volume e no espelho
            grava = bool(getattr(self.a, "volume", None) or (esp and esp.faltando())) and not (
                self.gravador or self.gravador_pendente
            )
            self.gravador_pendente |= grava
        if grava:
            env["RENDER2GO_VOLUME_GRAVA"] = "1"
        extra = {"volume_gb": volume_gb} if volume_gb else {}
        try:
            info = self.provedor.criar(
                f"{prefixo}-{i}", self.a.opcoes, self.a.gpus_por_pod, env, **extra
            )
        except (runpod_pods.SemVaga, runpod_pods.RunPodError) as e:
            if grava:
                with self.lock:
                    self.gravador_pendente = False
            if isinstance(e, runpod_pods.SemVaga):
                _log(f"pod {i}: sem GPU disponível ({e})")
            else:
                _log(f"pod {i}: o RunPod recusou a criação ({e})")
            return None
        pod = Pod(
            info,
            Agente(info.url, info.token),
            grava_volume=grava,
            deadman_s=deadman_s,
            hibernavel=bool(volume_gb),
        )
        if ao_criar:  # a partir daqui o pod custa: quem chamou registra id, GPU e preço
            try:
                ao_criar(info)
            except Exception as e:  # noqa: BLE001 (registro não derruba a subida)
                _log(f"AVISO: registro do pod {pod.nome}: {e}")
        if grava:
            with self.lock:
                self.gravador, self.gravador_pendente = pod, False
        with self.lock:
            self.pods.append(pod)
            self._salvar_estado()
        _log(f"pod {pod.nome} criado, US$ {info.usd_h:.2f}/h; esperando o agente...")
        try:
            self._instalar(pod, cedo, ao_agente=ao_agente)
        except (NuvemError, OSError) as e:
            _log(f"pod {pod.nome} descartado: {e}")
            if "tempo esgotado" in str(e):
                lembrar_datacentro(pod.info.datacentro, None, f"instalação lenta: {e}")
            self._encerrar(pod, "falhou na subida")
            with self.lock:  # o substituto passa a gravar o cache
                if self.gravador is pod:
                    self.gravador = None
            return None
        return pod

    # -- uso pelo orquestrador da fila (servico/orquestrador.py) ----------------------------

    def subir_pod(
        self,
        i: int,
        deadman_s: int,
        cedo: bool = False,
        ao_criar: Callable[[PodInfo], None] | None = None,
        ao_agente: Callable[[Pod], None] | None = None,
        volume_gb: int = 0,
        prefixo: str = "render2go-lote",
    ) -> Pod | None:
        """Cria, testa (CUDA, rede, datacenter) e instala um pod; None se não conseguiu.

        `cedo`: sobe os serviços (Ollama e ComfyUI) assim que o ambiente fica pronto (estado
        AMBIENTE), enquanto os modelos ainda baixam, e devolve o pod nesse ponto. Quem usa o pod
        só renderiza depois do estado PRONTO (o worker da fila, com --modelos-prontos).
        `ao_criar(info)`: chamado assim que o RunPod cria o pod, mesmo que a instalação falhe
        depois (relatório de custo do orquestrador).
        `ao_agente(pod)`: chamado quando o agente responde (o orquestrador ajusta o desligamento
        automático pelo preço real da GPU, `Agente.deadman`).
        `volume_gb`: o pod nasce com disco de volume próprio e instala tudo nele, para hibernar no
        fim em vez de ser encerrado (ADR 0011, `hibernar` e `retomar_pod`)."""
        return self._subir_pod(i, deadman_s, cedo, ao_criar, ao_agente, volume_gb, prefixo)

    def retomar_pod(
        self,
        info: PodInfo,
        cedo: bool = True,
        ao_agente: Callable[[Pod], None] | None = None,
        ao_criar: Callable[[PodInfo], None] | None = None,
    ) -> Pod:
        """Liga de novo um pod hibernado (ADR 0011): sem teste de rede nem download, o bootstrap só
        refaz os links do disco de volume e confere os modelos. Levanta SemVaga (o host está sem
        GPU livre) ou NuvemError; quem chama decide se o pod volta a hibernar ou é encerrado.
        `ao_criar(info)`: chamado quando o RunPod liga o pod (a GPU passa a cobrar)."""
        preco = self.provedor.retomar(info.id)
        if preco:  # o preço da volta (o RunPod pode ter mudado o da GPU)
            info = dataclasses.replace(info, usd_h=float(preco))
        if ao_criar:
            try:
                ao_criar(info)
            except Exception as e:  # noqa: BLE001 (registro não derruba a subida)
                _log(f"AVISO: registro do pod {info.id}: {e}")
        # o agente volta com o prazo curto do ambiente do pod (PISO_HIB_S); ao_agente manda o real
        pod = Pod(
            info, Agente(info.url, info.token), deadman_s=PISO_HIB_S, hibernavel=True, retomado=True
        )
        with self.lock:
            self.pods.append(pod)
            self._salvar_estado()
        _log(f"pod {pod.nome} voltando da hibernação, US$ {info.usd_h:.2f}/h...")
        try:
            self._instalar(pod, cedo, ao_agente=ao_agente)
        except (NuvemError, OSError):
            with self.lock:  # quem chama encerra ou hiberna; aqui o pod só sai da lista de ativos
                self.pods.remove(pod)
                self._salvar_estado()
            raise
        return pod

    def hibernar(self, pod: Pod, motivo: str) -> bool:
        """Para o pod (a GPU para de cobrar; o disco de volume fica no host). Se o RunPod recusar,
        encerra. Devolve True se o pod ficou parado."""
        with self.lock:
            if not pod.vivo:
                return False
        try:
            self.provedor.parar(pod.info.id)
        except Exception as e:  # noqa: BLE001 (sem a parada, o pod não pode ficar ligado)
            _log(f"AVISO: o pod {pod.nome} não hibernou ({e}); encerrando")
            self._encerrar(pod, f"{motivo}; não hibernou")
            return False
        with self.lock:
            pod.vivo, pod.fim = False, time.monotonic()
            self._salvar_estado()
        _log(f"pod {pod.nome} hibernado ({motivo})")
        return True

    def encerrar(self, pod: Pod, motivo: str, esperar_espelho: bool = True) -> None:
        """Encerra; se o pod grava o cache (volume/espelho), espera terminar antes (com prazo).
        `esperar_espelho=False`: encerra na hora (o orquestrador está parando)."""
        if esperar_espelho and pod.grava_volume and pod.vivo:
            self._esperar_guardar(pod)
        self._encerrar(pod, motivo)

    def _instalar(
        self, pod: Pod, cedo: bool = False, ao_agente: Callable[[Pod], None] | None = None
    ) -> None:
        r, ag = self.remoto, pod.agente
        limite = time.monotonic() + ESPERA_AGENTE_S
        while not ag.saude():
            if time.monotonic() > limite:
                raise NuvemError(f"agente não respondeu em {ESPERA_AGENTE_S // 60} min")
            time.sleep(CONSULTA_S)
        if ao_agente:  # ex.: o desligamento automático com o preço real da GPU
            try:
                ao_agente(pod)
            except Exception as e:  # noqa: BLE001 (fica o prazo com que o pod nasceu)
                _log(f"AVISO: pod {pod.nome}: {e}")
        if r.verificar_cuda:
            _, out = ag.exec(CUINIT, 30)
            problema = cuda_ok(out, pod.info.gpus)
            if problema:
                raise NuvemError(problema)
        if pod.retomado:
            _log(f"pod {pod.nome}: agente e CUDA ok; conferindo o disco de volume")
        else:
            _log(f"pod {pod.nome}: agente e CUDA ok; instalando o worker (~1–3 min)")
        ag.put(self.a.repo_tgz, f"{r.raiz}/repo.tgz")
        ag.put(WORKER_DIR / "scripts" / "bootstrap_pod.sh", f"{r.raiz}/bootstrap_pod.sh")
        if pod.hibernavel:  # o bootstrap marca o disco de volume com esta versão (ADR 0011)
            self._enviar_texto(pod, versao_hibernacao() + "\n", "versao_hib")
        if getattr(self.a, "legenda", False):  # legenda por foto (ADR 0014): o modelo de visão
            self._enviar_texto(pod, "1\n", "legenda")
        esp = getattr(self.a, "espelho", None)
        if esp and esp.presentes:  # URLs assinadas do espelho (GET, 6 h), uma por modelo
            self._enviar_texto(pod, esp.tsv(), "espelho.tsv")
            # o que está no espelho vem só dele: em 08/10/2026 ele baixou os 37 GB em 40 s, com o
            # Hugging Face a 25–39 MB/s nos mesmos hosts, e o download misto ia no passo do mais
            # lento. O curl de cada arquivo continua com a origem de reserva
            self._enviar_texto(pod, "1\n", "so_espelho")
        ag.bg(r.instalar.format(raiz=r.raiz), f"{r.raiz}/instalar.log")
        if r.verificar_cuda and not pod.retomado:  # de volta da hibernação: nada a baixar
            self._medir_rede(pod)
        # cedo: o pod gravador (volume/espelho) não, porque grava os modelos depois do PRONTO
        cedo = cedo and not pod.grava_volume
        fins = ("AMBIENTE", "PRONTO", "FALHOU") if cedo else ("PRONTO", "FALHOU")
        estado = self._esperar(pod, f"cat {r.raiz}/estado 2>/dev/null", fins, ESPERA_INSTALACAO_S)
        if not estado.startswith(fins[:-1]):
            # o disco cheio e o fim dos logs (sem os \r do progresso do aria2)
            _, log = ag.exec(
                f"df -h / | tail -1; tail -n 4 {r.raiz}/ambiente.log {r.raiz}/bootstrap.log 2>&1"
                " | tr '\\r' '\\n' | tail -n 12",
                30,
            )
            raise NuvemError(f"instalação: {estado.strip()} | {log[-700:]}")
        modelos_prontos = estado.startswith("PRONTO")
        if modelos_prontos:
            if pod.grava_volume and esp and esp.faltando():
                self._espelhar(pod)
            _, tempos = ag.exec(f"cat {r.raiz}/tempos 2>/dev/null", 30)
            if tempos.strip():  # etapas em s desde o início da instalação (para medir o cache)
                etapas = " | ".join(tempos.strip().splitlines())
                _log(f"pod {pod.nome}: tempos da instalação: {etapas}")
        # sem os modelos ainda, o entrypoint sobe mesmo assim (REQUIRE_MODELS=0): ninguém renderiza
        # antes do PRONTO, e o pedido já pode ser traduzido (Ollama) enquanto o download termina
        sem_modelos = "" if modelos_prontos else "export REQUIRE_MODELS=0; "
        # na volta da hibernação, a GPU é a mesma em que o PyTorch já rodou (o cuInit confere)
        sem_teste = "export SKIP_GPU_CHECK=1; " if pod.retomado else ""
        # legenda por foto (ADR 0014): o entrypoint sobe o 2º Ollama, só em CPU
        legenda = (
            "export RENDER2GO_LEGENDA_POR_FOTO=1; " if getattr(self.a, "legenda", False) else ""
        )
        ag.bg(
            f"export COMFY_POR_GPU={self.a.comfy_por_gpu}; "
            f"{sem_modelos}{sem_teste}{legenda}{r.servicos}",
            f"{r.raiz}/servicos.log",
        )
        self._esperar(
            pod,
            f"grep -q 'render2go\\] ERRO' {r.raiz}/servicos.log && echo ERRO; "
            f"grep -q 'render2go\\] pronto' {r.raiz}/servicos.log && echo PRONTO",
            ("PRONTO",),
            ESPERA_SERVICOS_S,
            falha="ERRO",
        )
        if modelos_prontos:
            _log(f"pod {pod.nome}: pronto ({pod.info.gpus} GPU)")
        else:
            _log(f"pod {pod.nome}: serviços no ar ({pod.info.gpus} GPU); modelos ainda baixando")

    def _enviar_texto(self, pod: Pod, texto: str, nome: str) -> None:
        """Arquivo pequeno para o pod (URLs assinadas do espelho e marcas para o bootstrap)."""
        tmp = self.a.saida / ESTADO / f"{nome}-{pod.info.id}"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(texto)
        try:
            pod.agente.put(tmp, f"{self.remoto.raiz}/{nome}")
        finally:
            tmp.unlink(missing_ok=True)

    def _medir_rede(self, pod: Pod) -> None:
        """A velocidade dos primeiros 10 s do download dos modelos ("rede <MB/s>" em $R/rede,
        escrito pelo bootstrap): host abaixo de REDE_MINIMA_MBS é descartado (o pod é encerrado e
        outro sobe), e a medida fica na memória dos datacenters, que ordena as próximas subidas
        (`RunPod._datacentros_ordenados`)."""
        r = self.remoto
        out = self._esperar(
            pod, f"cat {r.raiz}/rede {r.raiz}/estado 2>/dev/null", ("rede", "FALHOU"), ESPERA_REDE_S
        )
        if not out.startswith("rede") or out.startswith("rede -"):
            return  # falha: a espera da instalação mostra; sem download (cache): nada a medir
        m = re.search(r"rede ([\d.]+)", out)
        problema = rede_ok(out)
        lembrar_datacentro(pod.info.datacentro, float(m.group(1)) if m else None, problema or "")
        if problema:
            raise NuvemError(f"{problema} (datacenter {pod.info.datacentro or '?'})")
        _log(f"pod {pod.nome}: modelos baixando a {m.group(1)} MB/s ({pod.info.datacentro or '?'})")

    def _espelhar(self, pod: Pod) -> None:
        """Pod gravador: envia ao espelho, em segundo plano, os modelos que faltam lá."""
        esp, r = self.a.espelho, self.remoto
        try:
            with self.lock:
                plano = esp.plano()
            if not plano["arquivos"]:
                return
            arq = self.a.saida / ESTADO / f"espelho-plano-{pod.info.id}.json"
            arq.write_text(json.dumps(plano))
            pod.agente.put(arq, f"{r.raiz}/espelho-plano.json")
            arq.unlink()
            pod.agente.put(WORKER_DIR / "scripts" / "espelhar.py", f"{r.raiz}/espelhar.py")
            pod.agente.bg(
                f"python3 {r.raiz}/espelhar.py {r.raiz}/espelho-plano.json "
                f"{r.raiz}/espelho-resultado.json",
                f"{r.raiz}/espelhar.log",
            )
            pod.espelhando = True
            n = len(plano["arquivos"])
            _log(f"pod {pod.nome}: enviando {n} modelo(s) ao espelho (R2) em segundo plano")
        except (ArmazenamentoError, NuvemError, OSError) as e:
            _log(f"AVISO: espelho não iniciado: {e}")
            with self.lock:
                esp.abortar()

    def _concluir_espelho(self, pod: Pod, out: str) -> None:
        esp, r = self.a.espelho, self.remoto
        try:
            if "ESPELHADO" not in out:
                raise NuvemError(out.strip()[-200:] or "não terminou no prazo")
            _, txt = pod.agente.exec(f"cat {r.raiz}/espelho-resultado.json", 30)
            with self.lock:
                feitos = esp.concluir(json.loads(txt))
            _log(
                f"espelho (R2): {', '.join(feitos) or 'nada'} enviado(s)"
                + (f"; AVISO: {esp.erro}" if esp.erro else "")
            )
        except (ArmazenamentoError, NuvemError, OSError, ValueError) as e:
            _log(f"AVISO: espelho incompleto ({e}); o próximo lote completa")
            with self.lock:
                esp.abortar()
        pod.espelhando = False

    def _esperar(self, pod, cmd, fins, limite_s, falha: str | None = None) -> str:
        limite = time.monotonic() + limite_s
        while time.monotonic() < limite:
            _, out = pod.agente.exec(cmd, 30)
            out = out.strip()
            if falha and falha in out:
                raise NuvemError(f"falhou: {cmd.split()[0]}")
            if any(out.startswith(f) for f in fins):
                return out
            time.sleep(CONSULTA_S)
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
        instancias = pod.info.gpus * self.a.comfy_por_gpu
        cmd = shlex.join(["--saida", f"{d}/saida", "--gpus", str(instancias), *args_lote(self.a)])
        t0 = time.monotonic()
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
        parede = time.monotonic() - t0
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
        # vazão real deste pod: segundos de GPU por vídeo (já com o paralelismo)
        ok = sum(1 for row in linhas if row["status"] == "ok")
        if ok:
            por_video = parede * pod.info.gpus / ok
            esperado = segundos_por_video(
                pod.info.gpu or self.a.opcoes[0].gpu, self.a.qualidade, self._duracao(pacote)
            )
            pod.vazao.append(por_video)
            _log(f"pod {pod.nome}: ~{por_video:.0f} s de GPU por vídeo (medido: ~{esperado:.0f} s)")
            if por_video > LENTO * esperado + FOLGA_S:
                raise PodLento(
                    f"{por_video:.0f} s por vídeo, esperado ~{esperado:.0f} s: servidor lento"
                )

    def _duracao(self, pacote: list[Foto]) -> int:
        return pacote[0].tarefas[0].duracao

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

    def gasto(self) -> float:
        agora = time.monotonic()
        return sum(p.info.usd_h * ((p.fim or agora) - p.criado) / 3600 for p in self.pods)

    def _trabalhar(self, i: int, deadman_s: int) -> None:
        """Um "posto" do lote: sobe um pod; se ele falhar ou for lento, troca por outro."""
        for tentativa in range(1 + self.a.substitutos):
            if self.recusado or self.pacotes.empty() or self.parar.is_set():
                return
            if tentativa:
                _log(f"posto {i}: trocando o pod ({tentativa}/{self.a.substitutos})")
            pod = self._subir_pod(i, deadman_s)
            if pod is None:
                time.sleep(ESPERA_VAGA_S)  # sem vaga agora: espera antes de gastar outra troca
                continue
            if self._usar(pod, deadman_s):
                return  # terminou sem falha (fila vazia ou limite de tempo)

    def _usar(self, pod: Pod, deadman_s: int) -> bool:
        """Roda pacotes no pod; devolve False se o pod falhou e deve ser trocado."""
        try:
            self._compilar(pod)
            self.prompts_ok.wait()
            n = 0
            while not self.recusado and not self.parar.is_set():
                if self.gasto() > self.a.orcamento_usd * 1.5:
                    _log(f"gasto passou de 1,5× o orçamento (US$ {self.gasto():.2f}); parando")
                    self.parar.set()
                    break
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
                except PodLento:
                    raise  # o pacote foi entregue; só o pod sai
                except (NuvemError, OSError) as e:
                    self.pacotes.put(pacote)  # outro pod pega
                    raise NuvemError(f"pacote {n} devolvido à fila: {e}") from e
            return True
        except PodLento as e:
            _log(f"pod {pod.nome}: {e}")
            lembrar_datacentro(pod.info.datacentro, None, f"lento: {e}")
            return False
        except NuvemError as e:
            _log(f"pod {pod.nome}: {e}")
            if self.recusado:
                self.prompts_ok.set()
                return True
            return False
        finally:
            if pod.grava_volume:
                self._esperar_guardar(pod)
            self._encerrar(pod, "fim do trabalho")

    def _esperar_guardar(self, pod: Pod) -> None:
        """O pod gravador só sai depois de terminar o cache no volume e o envio ao espelho."""
        limite = min(
            time.monotonic() + ESPERA_GUARDAR_S,
            pod.criado + (pod.deadman_s or getattr(self, "deadman_s", 0)) - 60,
        )
        r = self.remoto.raiz
        _log(f"pod {pod.nome}: terminando de gravar o cache (volume/espelho)")
        out = ""
        while time.monotonic() < limite:
            try:
                _, out = pod.agente.exec(
                    f"cat {r}/guardado 2>/dev/null; tail -2 {r}/espelhar.log 2>/dev/null", 30
                )
            except (NuvemError, OSError):
                break
            volume_ok = "GUARDADO" in out
            espelho_ok = not pod.espelhando or "ESPELHADO" in out or "FALHOU" in out
            if volume_ok and espelho_ok:
                if getattr(self.a, "volume", None):
                    _log(f"pod {pod.nome}: cache gravado no volume")
                break
            time.sleep(15)
        else:
            _log(f"pod {pod.nome}: AVISO: o cache não terminou de gravar; o próximo pod completa")
        if pod.espelhando:
            self._concluir_espelho(pod, out)
        with self.lock:
            if self.gravador is pod:
                self.gravador = None

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
        tam = FOTOS_POR_GPU * self.a.gpus_por_pod * self.a.comfy_por_gpu
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


def versao_hibernacao() -> str:
    """Versão do que fica no disco de volume de um pod que hiberna (ADR 0011): dependências, versões
    do ComfyUI e do Ollama, modelos, agente (vai no ambiente do pod, fixado na criação) e o layout
    do bootstrap. Mudou: o pod hibernado está velho e é encerrado em vez de voltar."""
    h = hashlib.sha256()
    for p in (
        WORKER_DIR / "requirements.lock",
        WORKER_DIR / "Dockerfile",
        WORKER_DIR / "models.lock.json",
        pod_agente_path(),
    ):
        h.update(p.read_bytes())
    layout = re.search(
        r"^LAYOUT_HIB=(\S+)", (WORKER_DIR / "scripts" / "bootstrap_pod.sh").read_text(), re.M
    )
    h.update((layout.group(1) if layout else "?").encode())
    # legenda por foto (ADR 0014): o volume ganha o modelo de visão; sem a chave, a versão de antes
    if os.environ.get("RENDER2GO_LEGENDA_POR_FOTO", "").strip() == "1":
        h.update(b"legenda")
    return h.hexdigest()[:16]


def preparar_espelho() -> Espelho | None:
    """Espelho dos modelos no R2, se RENDER2GO_R2_* estiver configurado (reserva do HF)."""
    esp = Espelho.do_ambiente()
    if esp is None:
        return None
    try:
        esp.verificar()
    except ArmazenamentoError as e:
        _log(f"AVISO: espelho (R2) inacessível, seguindo só com a origem: {e}")
        return None
    falta = len(esp.faltando())
    _log(
        f"espelho (R2): {len(esp.presentes)} de {len(esp.modelos)} modelo(s)"
        + (f"; o 1º pod envia os {falta} que faltam" if falta else "")
    )
    return esp


PACOTE_DIRS = ("worker", "tools")  # o que o pod usa (bootstrap, worker, lote)
PACOTE_FORA = {".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", "evals"}


def empacotar_repo(destino: Path) -> Path:
    """O código que vai para o pod: o commit atual (git archive) ou, sem .git (contêiner do
    serviço), as pastas worker/ e tools/ como estão no disco."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    if (REPO / ".git").exists():
        subprocess.run(
            ["git", "-C", str(REPO), "archive", "--format=tar.gz", "-o", str(destino), "HEAD"],
            check=True,
        )
        return destino

    def filtro(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        return None if set(Path(info.name).parts) & PACOTE_FORA else info

    with tarfile.open(destino, "w:gz") as tar:
        for d in PACOTE_DIRS:
            tar.add(REPO / d, arcname=d, filter=filtro)
    return destino


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="render2go-lote-nuvem",
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
    g.add_argument(
        "--pods",
        default="auto",
        help="quantos pods em paralelo, ou auto (padrão): cada pod com pelo menos "
        f"{TRABALHO_MIN_POD_S // 60} min de vídeos, até {MAX_PODS_AUTO}",
    )
    g.add_argument(
        "--prazo",
        type=int,
        metavar="MIN",
        help="com --pods auto: o mínimo de pods para entregar em MIN minutos (custa mais)",
    )
    g.add_argument(
        "--gpu",
        action="append",
        help="GPU em ordem de preferência (repetível); padrão: L40S, RTX 6000 Ada, RTX PRO 6000, "
        "RTX 4090",
    )
    g.add_argument(
        "--nuvem",
        choices=["secure", "qualquer", "community"],
        default="secure",
        help="padrão: secure (decisão de 07/10/2026; hosts community falharam no CUDA)",
    )
    g.add_argument("--gpus-por-pod", type=int, default=1, help="GPUs por pod (1, 2, 4, 8)")
    g.add_argument(
        "--comfy-por-gpu",
        type=int,
        default=1,
        help="vídeos ao mesmo tempo em cada GPU (1 a 4; a VRAM é dividida entre eles)",
    )
    g.add_argument(
        "--substitutos",
        type=int,
        default=SUBSTITUTOS,
        help=f"quantas vezes trocar um pod lento ou com defeito (padrão {SUBSTITUTOS})",
    )
    g.add_argument(
        "--orcamento-usd", type=float, default=5.0, help="não sobe pods se a estimativa passar"
    )
    g.add_argument(
        "--datacentro",
        action="append",
        default=[],
        help="só estes datacenters do RunPod (repetível), ex.: US-KS-2",
    )
    g.add_argument(
        "--volume",
        help="volume de rede do RunPod (id) com o cache dos modelos; o pod sobe no datacenter dele",
    )
    g.add_argument(
        "--sem-espelho",
        action="store_true",
        help="não usa o espelho dos modelos no R2 (variáveis RENDER2GO_R2_*), mesmo configurado",
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
        provedor = provedor or (None if a.simular else RunPod(a.datacentro, a.volume))
    except runpod_pods.RunPodError as e:
        _log(f"ERRO {e}")
        return EXIT_SEM_GPU
    if a.limpar:
        return limpar(a.saida, provedor)
    if a.pasta is None or a.prompt is None:
        p.error("informe a pasta e o --prompt")
    if a.pods != "auto" and not (a.pods.isdigit() and 1 <= int(a.pods) <= 20):
        p.error("--pods de 1 a 20, ou auto")
    if a.gpus_por_pod not in (1, 2, 4, 8):
        p.error("--gpus-por-pod 1, 2, 4 ou 8")
    if a.prazo is not None and (a.pods != "auto" or a.prazo < 5):
        p.error("--prazo só com --pods auto, a partir de 5 min")

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
    if provedor is not None:  # a opção mais cara com preço agora: o pod pode cair em qualquer uma
        precos = [x for o in a.opcoes if (x := provedor.preco(o.gpu, o.nuvem))]
        usd_h = max(precos) if precos else None
    usd_h = usd_h or PRECO_RESERVA_USD_H
    n_pacotes = -(-len(fotos) // (FOTOS_POR_GPU * a.gpus_por_pod * a.comfy_por_gpu))
    if a.pods == "auto":
        a.pods, motivo = escolher_pods(n_videos, n_pacotes, a.gpus_por_pod, s_video, a.prazo)
        opcoes = sorted({1, 2, 4, 8, a.pods} & set(range(1, min(MAX_PODS_AUTO, n_pacotes) + 1)))
        _log(
            f"pods automáticos: {a.pods} ({motivo}); outras opções (--pods N): "
            + "; ".join(
                f"{n}: ~{e.minutos:.0f} min, ~US$ {e.usd:.2f}"
                for n in opcoes
                if (e := estimar(n_videos, n, a.gpus_por_pod, s_video, usd_h * a.gpus_por_pod))
            )
        )
    else:
        a.pods = int(a.pods)
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
    a.espelho = None if a.sem_espelho else preparar_espelho()
    # Desligamento automático por pod: o menor entre
    #   2 h (regra do projeto), 2× a estimativa + 20 min,
    #   o que o orçamento paga (com 20% de folga) e o que o saldo da conta paga (menos a reserva).
    # Pod caro para cedo; pod barato pode ir até as 2 h.
    custo_h = usd_h * a.gpus_por_pod * min(a.pods, len(fotos))
    limites = {
        "regra de 2 h": MAX_POD_S,
        "estimativa": 2 * est.minutos * 60 + 1200,
        "orçamento": a.orcamento_usd * 1.2 / custo_h * 3600,
    }
    saldo = provedor.saldo() if hasattr(provedor, "saldo") else None
    if saldo is not None:
        disponivel = saldo - max(RESERVA_SALDO_USD, 0.1 * saldo)
        limites["saldo da conta"] = max(0.0, disponivel) / custo_h * 3600
        _log(f"saldo da conta: US$ {saldo:.2f} (reserva US$ {saldo - disponivel:.2f})")
        if est.usd > disponivel:
            _log(f"estimativa (US$ {est.usd:.2f}) passa do saldo disponível; adicione créditos")
            return EXIT_SEM_GPU
    motivo, deadman = min(limites.items(), key=lambda kv: kv[1])
    deadman_s = int(deadman)
    if deadman_s < MIN_POD_S:
        _log(f"limite de tempo curto demais ({deadman_s // 60} min, por {motivo}); nada criado")
        return EXIT_SEM_GPU
    _log(f"desligamento automático de cada pod em {deadman_s // 60} min (limite: {motivo})")
    if est.minutos * 60 > MAX_POD_S - MARGEM_S:
        _log(
            f"AVISO: ~{est.minutos:.0f} min passa do limite de 2 h por pod; o que não couber "
            "fica pendente (rode de novo) ou use mais --pods"
        )
    t0 = time.monotonic()
    orq = Orquestrador(a, fotos, provedor, remoto or Remoto())
    code = orq.rodar(deadman_s)
    custo = orq.gasto()
    _log(
        f"fim em {(time.monotonic() - t0) / 60:.1f} min; custo estimado dos pods ~US$ {custo:.2f}; "
        f"relatório em {orq.rel_path}"
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
