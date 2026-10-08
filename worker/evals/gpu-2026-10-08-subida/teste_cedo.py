"""Teste real da subida "cedo" (08/10/2026): pod RunPod secure com a ordem padrão de GPUs.

Mede: criação → AMBIENTE (serviços e tradução durante o download) → PRONTO → 1º vídeo.
Usa o lote_nuvem com o _subir_pod em cedo=True e espera o PRONTO antes de renderizar,
como o worker da fila faz com --modelos-prontos. O pod é encerrado no fim (e tem
desligamento automático)."""

import sys
import time

import render2go_worker  # noqa: F401 (GPU2GO_* → RENDER2GO_*)
from render2go_worker import lote_nuvem as ln

T0 = time.monotonic()
marcas: dict[str, float] = {}


def marca(nome: str) -> None:
    marcas[nome] = time.monotonic() - T0
    ln._log(f"MARCA {nome}: {marcas[nome]:.0f} s")


orig_subir = ln.Orquestrador._subir_pod
orig_usar = ln.Orquestrador._usar
orig_compilar = ln.Orquestrador._compilar
orig_rodar = ln.Orquestrador._rodar_pacote


def subir(self, i, deadman_s, cedo=False):
    marca("pedido_de_pod")
    pod = orig_subir(self, i, deadman_s, cedo=True)
    if pod is not None:
        marca("servicos_no_ar")
        _, est = pod.agente.exec(f"cat {self.remoto.raiz}/estado", 30)
        ln._log(f"estado ao subir os serviços: {est.strip()}")
    return pod


def usar(self, pod, deadman_s):
    try:
        orig_compilar(self, pod)  # durante o download, como o worker da fila
        marca("traducao_pronta")
        _, est = pod.agente.exec(f"cat {self.remoto.raiz}/estado", 30)
        ln._log(f"estado depois da tradução: {est.strip()}")
        cmd = f"cat {self.remoto.raiz}/estado 2>/dev/null"
        est = self._esperar(pod, cmd, ("PRONTO", "FALHOU"), ln.ESPERA_INSTALACAO_S)
        marca("modelos_prontos")
        _, tempos = pod.agente.exec(f"cat {self.remoto.raiz}/tempos 2>/dev/null", 30)
        ln._log("tempos da instalação: " + " | ".join(tempos.strip().splitlines()))
        cmd = f"grep -E 'render2go\\]|ERRO' {self.remoto.raiz}/servicos.log | head -20"
        _, log = pod.agente.exec(cmd, 30)
        ln._log("servicos.log: " + " / ".join(log.strip().splitlines()))
        if not est.startswith("PRONTO"):
            raise ln.NuvemError(f"instalação: {est}")
    except ln.NuvemError as e:
        ln._log(f"FALHA: {e}")
        self._encerrar(pod, "falhou no teste")
        return True
    return orig_usar(self, pod, deadman_s)


def rodar(self, pod, pacote, n):
    orig_rodar(self, pod, pacote, n)
    marca("primeiro_pacote_pronto")


ln.Orquestrador._subir_pod = subir
ln.Orquestrador._usar = usar
ln.Orquestrador._rodar_pacote = rodar
code = ln.main(sys.argv[1:])
ln._log(f"MARCAS: {marcas}")
sys.exit(code)
