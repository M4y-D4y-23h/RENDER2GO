"""Reproduções da revisão de 14/10/2026 (provedores falsos, sem rede, sem pods). Roda contra
qualquer versão do código (antes.json: commit 0a8149b; depois.json: esta branch):

    cd <repo> && PYTHONPATH=worker/src:servico/src servico/.venv/bin/python \
        worker/evals/ciclo-de-vida-2026-10-14/reproduzir.py
"""

import json
import tempfile
from argparse import Namespace
from pathlib import Path

from render2go_servico.orquestrador import ConfigOrq, Orquestrador

from render2go_worker import lote_nuvem as ln
from render2go_worker import runpod_pods

saida = {}


# 1. _encerrar com o DELETE falhando (OSError), chamado duas vezes
class Falha:
    def __init__(self):
        self.chamadas = []

    def encerrar(self, pid):
        self.chamadas.append(pid)
        raise OSError("rede caiu")


d = Path(tempfile.mkdtemp())
prov = Falha()
o = ln.Orquestrador(Namespace(saida=d, acabamento=None), [], prov, ln.Remoto())
info = ln.PodInfo("p1", "http://127.0.0.1:9", "t" * 40, 1.0, "teste", 1)
pod = ln.Pod(info, ln.Agente(info.url, info.token))
o.pods.append(pod)
o._salvar_estado()
o._encerrar(pod, "fim")
o._encerrar(pod, "fim")
saida["encerrar"] = {
    "chamadas_ao_provedor": len(prov.chamadas),
    "vivo": pod.vivo,
    "pods_json": json.loads((d / ln.ESTADO / "pods.json").read_text()),
}

# 3. _retomar_algum três vezes no mesmo estado de demanda, host sem GPU livre


class Prov:
    def __init__(self):
        self.encerrados, self.retomadas = [], 0

    def preco(self, g, n):
        return 2.0

    def saldo(self):
        return 100.0

    def retomar(self, pid):
        self.retomadas += 1
        raise runpod_pods.SemVaga("not enough free GPUs on the host")

    def encerrar(self, pid):
        self.encerrados.append(pid)

    def parar(self, pid):
        pass


class BancoNada:  # o _retomar_algum não usa o banco
    pass


e = Path(tempfile.mkdtemp())
repo = e / "repo.tgz"
repo.write_bytes(b"x")
cfg = ConfigOrq(
    api_url="https://api.teste",
    worker_token="w" * 40,
    hosts_permitidos="r2",
    estado=e / "orq",
    hibernados=1,
)
p = Prov()
orq = Orquestrador(BancoNada(), cfg, p, ln.Remoto(verificar_cuda=False), repo_tgz=repo)
orq.hib["velho"] = {
    "id": "velho",
    "versao": ln.versao_hibernacao(),
    "falhas": 0,
    "url": "http://127.0.0.1:9",
    "token": "t" * 40,
    "usd_h": 2.0,
    "gpu": ln.GPUS_PADRAO[0],
    "gpus": 1,
}
if hasattr(orq, "nova_demanda"):
    orq.nova_demanda()
contagens = []
for _ in range(3):
    orq._retomar_algum(lambda i: None)
    contagens.append(orq.hib.get("velho", {}).get("falhas", "descartado"))
saida["retomar"] = {
    "falhas_apos_cada_chamada": contagens,
    "chamadas_retomar": p.retomadas,
    "encerrado_com_o_disco": "velho" in p.encerrados,
}
print(json.dumps(saida, indent=1, ensure_ascii=False))
