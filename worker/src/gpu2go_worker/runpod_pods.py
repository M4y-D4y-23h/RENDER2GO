"""Pods do RunPod para o modo `pod` do worker: community ou secure, com disponibilidade no ato.

Comandos (tools/runpod_pod.py):
  disponibilidade  estoque e preço de cada GPU nas nuvens community e secure (sem chave)
  subir            cria o pod tentando as opções em ordem (GPU × nuvem) até uma ter vaga
  listar           pods da conta
  encerrar ID      encerra (terminate) um pod
  autoencerrar     usado pelo entrypoint dentro do pod: encerra o próprio pod

A chave vem só da variável RUNPOD_API_KEY, nunca de argumento nem de arquivo do repositório.
Sem a variável, as chamadas saem sem chave, para o proxy do ambiente de nuvem incluir a
credencial cadastrada nas configurações (cabeçalho Authorization nos hosts do RunPod).
Dentro do pod, o RunPod injeta RUNPOD_API_KEY (restrita ao pod, aceita só na API GraphQL)
e RUNPOD_POD_ID.

Disponibilidade: `stockStatus` do GraphQL (High/Medium/Low, ou vazio sem estoque) é só um
indicativo. Quem decide é a criação: sem vaga, a API recusa e a próxima opção é tentada.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

REST = "https://rest.runpod.io/v1"
GRAPHQL = "https://api.runpod.io/graphql"
# O Cloudflare do RunPod recusa o User-Agent padrão do urllib (erro 1010); o do requests
# passa, mas uma identificação explícita evita depender disso.
USER_AGENT = "gpu2go-runpod/0.1 (+https://github.com/M4y-D4y-23h/GPU2GO)"
NUVENS = {"community": "COMMUNITY", "secure": "SECURE"}
MAX_HORAS_POD = 2  # regra do dono do projeto (07/10/2026): nenhum pod ligado mais de 2 h
SEM_VAGA = (
    "no instances",
    "not available",
    "unavailable",
    "no longer available",
    "does not have the resources",
    "insufficient",
    "capacity",
    "out of stock",
)


class RunPodError(Exception):
    pass


class SemVaga(RunPodError):
    """A opção (GPU × nuvem) não tem máquina livre agora."""


@dataclass(frozen=True)
class Opcao:
    gpu: str
    nuvem: str  # community | secure

    def __str__(self) -> str:
        return f"{self.gpu} ({self.nuvem})"


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _chave() -> str:
    """Chave do ambiente; "" = sem chave aqui (o proxy do ambiente inclui a credencial)."""
    return os.environ.get("RUNPOD_API_KEY", "").strip()


SEM_CHAVE = (
    "o RunPod recusou a autenticação: cadastre a chave nas configurações do ambiente "
    "(credencial Bearer para *.runpod.io e *.runpod.ai, ou a variável RUNPOD_API_KEY)"
)


def auth_headers(chave: str | None) -> dict[str, str]:
    h = {"User-Agent": USER_AGENT}
    if chave:
        h["Authorization"] = f"Bearer {chave}"
    return h


def _graphql(query: str, chave: str | None = None, timeout: float = 30) -> dict[str, Any]:
    # a chave restrita do pod (autoencerrar) só é aceita como parâmetro api_key
    params = {"api_key": chave} if chave else None
    try:
        r = requests.post(
            GRAPHQL,
            json={"query": query},
            params=params,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        )
        if r.status_code in (401, 403) and not chave:
            raise RunPodError(SEM_CHAVE)
        r.raise_for_status()
    except requests.RequestException as e:
        raise RunPodError(f"API GraphQL do RunPod inacessível: {e}") from e
    data = r.json()
    if data.get("errors"):
        raise RunPodError(f"API GraphQL: {data['errors'][0].get('message')}")
    return data["data"]


def _rest(method: str, path: str, chave: str, body: dict | None = None) -> requests.Response:
    try:
        r = requests.request(
            method, f"{REST}{path}", json=body, timeout=60, headers=auth_headers(chave)
        )
    except requests.RequestException as e:
        raise RunPodError(f"API REST do RunPod inacessível: {e}") from e
    if r.status_code in (401, 403) and not chave:
        raise RunPodError(SEM_CHAVE)
    return r


def disponibilidade(gpus: list[str]) -> list[dict[str, Any]]:
    """Estoque e preço por GPU e nuvem. Não precisa de chave."""
    out = []
    for gpu in gpus:
        campos = "{ stockStatus uninterruptablePrice }"
        q = (
            f"{{ gpuTypes(input:{{id:{json.dumps(gpu)}}}) {{ id displayName memoryInGb "
            f"secureCloud communityCloud "
            f"secure: lowestPrice(input:{{gpuCount:1, secureCloud:true}}) {campos} "
            f"community: lowestPrice(input:{{gpuCount:1, secureCloud:false}}) {campos} }} }}"
        )
        # com RUNPOD_API_KEY (servidor do serviço, sem o proxy do ambiente), a chave vai junto
        tipos = _graphql(q, _chave() or None).get("gpuTypes") or []
        if not tipos:
            out.append(
                {
                    "gpu": gpu,
                    "erro": "GPU desconhecida (use o id exato, ex.: NVIDIA GeForce RTX 4090)",
                }
            )
            continue
        t = tipos[0]
        for nuvem in ("community", "secure"):
            oferta = t.get(f"{nuvem}Cloud")
            preco = (t.get(nuvem) or {}).get("uninterruptablePrice")
            estoque = (t.get(nuvem) or {}).get("stockStatus")
            out.append(
                {
                    "gpu": t["id"],
                    "nome": t.get("displayName"),
                    "vram_gb": t.get("memoryInGb"),
                    "nuvem": nuvem,
                    "oferecida": bool(oferta),
                    "estoque": estoque or "sem estoque",
                    "usd_hora": preco,
                    "provavel": bool(oferta and estoque),
                }
            )
    return out


def corpo_pod(
    opcao: Opcao, imagem: str, nome: str, disco_gb: int, env: dict[str, str], portas: list[str]
) -> dict[str, Any]:
    return {
        "name": nome,
        "imageName": imagem,
        "gpuTypeIds": [opcao.gpu],
        "gpuCount": 1,
        "cloudType": NUVENS[opcao.nuvem],
        "containerDiskInGb": disco_gb,
        "volumeInGb": 0,
        "ports": portas,
        # sem allowedCudaVersions: a API só aceita até "13.0" e excluía hosts com driver mais
        # novo (RTX PRO 6000, 07/10/2026). O driver ≥ 570 é conferido no pod (gpu_check/cuInit).
        "env": env,
    }


def criar(opcao: Opcao, corpo: dict[str, Any], chave: str) -> dict[str, Any]:
    r = _rest("POST", "/pods", chave, corpo)
    if r.status_code in (200, 201):
        return r.json()
    texto = r.text[:500]
    if r.status_code in (401, 403):
        raise RunPodError(f"chave recusada pela API ({r.status_code}): {texto}")
    if r.status_code >= 500 or any(s in texto.lower() for s in SEM_VAGA):
        raise SemVaga(f"{opcao}: sem vaga ({r.status_code}): {texto}")
    raise RunPodError(f"{opcao}: criação recusada ({r.status_code}): {texto}")


def estado(pod_id: str, chave: str) -> dict[str, Any]:
    campos = "id desiredStatus costPerHr runtime { uptimeInSeconds } machine { location }"
    q = f"{{ pod(input:{{podId:{json.dumps(pod_id)}}}) {{ {campos} }} }}"
    return _graphql(q, chave).get("pod") or {}


def encerrar(pod_id: str, chave: str) -> None:
    r = _rest("DELETE", f"/pods/{pod_id}", chave)
    if r.status_code not in (200, 204, 404):
        raise RunPodError(f"encerrar {pod_id}: {r.status_code} {r.text[:300]}")


def subir(
    opcoes: list[Opcao],
    corpo_base: dict[str, Any],
    chave: str,
    espera_s: float,
    intervalo: float = 10.0,
) -> dict[str, Any]:
    """Tenta cada opção em ordem; devolve o pod que ficou de pé (container rodando)."""
    tentativas = []
    for opcao in opcoes:
        corpo = {**corpo_base, "gpuTypeIds": [opcao.gpu], "cloudType": NUVENS[opcao.nuvem]}
        try:
            pod = criar(opcao, corpo, chave)
        except SemVaga as e:
            _log(f"  {e}")
            tentativas.append({"opcao": str(opcao), "resultado": "sem vaga"})
            continue
        pod_id = pod["id"]
        _log(
            f"  criado {pod_id} em {opcao}, US$ {pod.get('costPerHr')}/h; esperando o container..."
        )
        limite = time.monotonic() + espera_s
        while time.monotonic() < limite:
            st = estado(pod_id, chave)
            if (st.get("runtime") or {}).get("uptimeInSeconds"):
                return {"pod": st, "opcao": str(opcao), "tentativas": tentativas}
            time.sleep(intervalo)
        _log(f"  {pod_id} não subiu em {espera_s:.0f} s; encerrando para não cobrar à toa")
        encerrar(pod_id, chave)
        tentativas.append({"opcao": str(opcao), "resultado": "não subiu", "pod": pod_id})
    raise SemVaga(f"nenhuma opção disponível agora: {json.dumps(tentativas, ensure_ascii=False)}")


def autoencerrar() -> int:
    """Dentro do pod: encerra o próprio pod (desligamento automático ou GPU com defeito)."""
    pod_id, chave = os.environ.get("RUNPOD_POD_ID"), os.environ.get("RUNPOD_API_KEY")
    if not pod_id or not chave:
        _log("autoencerrar: RUNPOD_POD_ID/RUNPOD_API_KEY ausentes (fora do RunPod?)")
        return 1
    for mutation in (
        "mutation { podTerminate(input:{podId:%s}) }",
        "mutation { podStop(input:{podId:%s}) { id desiredStatus } }",
    ):
        try:
            _graphql(mutation % json.dumps(pod_id), chave)
            _log(f"autoencerrar: pedido aceito ({mutation.split('(')[0].split()[-1]})")
            return 0
        except RunPodError as e:
            _log(f"autoencerrar: {e}")
    return 1


def _opcoes(gpus: list[str], nuvem: str) -> list[Opcao]:
    ordem = ["community", "secure"] if nuvem == "qualquer" else [nuvem]
    return [Opcao(g, n) for g in gpus for n in ordem]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gpu2go-runpod", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("disponibilidade", help="estoque e preço por GPU e nuvem (sem chave)")
    d.add_argument("--gpu", action="append", default=None, help="id da GPU (repetível)")
    s = sub.add_parser("subir", help="cria um pod do worker (modo pod)")
    s.add_argument(
        "--gpu", action="append", required=True, help="id da GPU, repetível em ordem de preferência"
    )
    s.add_argument(
        "--nuvem",
        choices=["secure", "qualquer", "community"],
        default="secure",
        help="qualquer = tenta community (mais barata) e depois secure",
    )
    s.add_argument(
        "--imagem", required=True, help="imagem do worker num registry (ex.: …/gpu2go-worker:full)"
    )
    s.add_argument("--nome", default="gpu2go-worker")
    s.add_argument("--disco", type=int, default=80, help="disco do container em GB")
    s.add_argument("--model-set", default="i2v_r1v2")
    s.add_argument(
        "--auto-encerrar-horas",
        type=float,
        default=2.0,
        help="o próprio pod se encerra depois disso (de 0,25 a 2 h; regra do projeto)",
    )
    s.add_argument("--chave-ssh", type=Path, help="chave pública para o túnel (padrão: a da conta)")
    s.add_argument("--env", action="append", default=[], metavar="K=V")
    s.add_argument("--espera", type=float, default=600, help="segundos para o container subir")
    sub.add_parser("listar", help="pods da conta")
    e = sub.add_parser("encerrar", help="encerra um pod")
    e.add_argument("pod_id")
    sub.add_parser("autoencerrar", help="(dentro do pod) encerra o próprio pod")
    a = p.parse_args(argv)

    if a.cmd == "subir" and not 0.25 <= a.auto_encerrar_horas <= MAX_HORAS_POD:
        p.error(f"--auto-encerrar-horas: de 0,25 a {MAX_HORAS_POD:g} h (pod nunca fica sem limite)")
    try:
        if a.cmd == "disponibilidade":
            gpus = a.gpu or [
                "NVIDIA GeForce RTX 4090",
                "NVIDIA GeForce RTX 5090",
                "NVIDIA RTX PRO 4500 Blackwell",
            ]
            for row in disponibilidade(gpus):
                if "erro" in row:
                    print(f"{row['gpu']}: {row['erro']}")
                    continue
                preco = f"US$ {row['usd_hora']:.2f}/h" if row["usd_hora"] else "—"
                oferta = row["estoque"] if row["oferecida"] else "não oferecida"
                print(f"{row['nome'] or row['gpu']:<28} {row['nuvem']:<10} {oferta:<14} {preco}")
            return 0
        if a.cmd == "autoencerrar":
            return autoencerrar()
        chave = _chave()
        if a.cmd == "listar":
            r = _rest("GET", "/pods", chave)
            for pod in r.json() if r.ok else []:
                print(
                    pod.get("id"), pod.get("name"), pod.get("desiredStatus"), pod.get("costPerHr")
                )
            return 0 if r.ok else 1
        if a.cmd == "encerrar":
            encerrar(a.pod_id, chave)
            print(f"{a.pod_id} encerrado")
            return 0
        env = {
            "MODE": "pod",
            "MODEL_SET": a.model_set,
            "AUTO_TERMINATE_HOURS": str(a.auto_encerrar_horas),
        }
        if a.chave_ssh:
            env["PUBLIC_KEY"] = a.chave_ssh.read_text().strip()
        for item in a.env:
            k, sep, v = item.partition("=")
            if not sep:
                p.error(f"--env inválido: {item}")
            env[k] = v
        opcoes = _opcoes(a.gpu, a.nuvem)
        corpo = corpo_pod(opcoes[0], a.imagem, a.nome, a.disco, env, ["22/tcp"])
        _log("tentando: " + ", ".join(map(str, opcoes)))
        res = subir(opcoes, corpo, chave, a.espera)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    except SemVaga as e:
        _log(str(e))
        return 3
    except RunPodError as e:
        _log(f"erro: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
