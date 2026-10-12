"""Medição em GPU de 12/10/2026: o worker novo (a GPU não espera), o aquecimento, a interpolação
RIFE e o SageAttention 2.2, num pod L40S secure (a 1ª GPU de produção).

1. Sobe um pod como o orquestrador (do zero, modelos do espelho R2) e, no estado AMBIENTE, começa
   em segundo plano o preparar_extras.sh (pesos do RIFE e a compilação do SageAttention).
2. No PRONTO, roda o no_pod.py em segundo plano e acompanha a cada minuto.
3. Baixa o resultado, os logs e as folhas de quadros para `saida/` e encerra o pod.

Travas: desligamento automático no agente (≤ PRAZO_MAX_S e o que o teto ainda paga, pelo preço
real da GPU que veio); teto = `--orcamento-usd`, abaixo do saldo do RunPod menos a reserva; o
acompanhamento para antes do desligamento, com margem para baixar os resultados; try/finally que
encerra o pod (e qualquer outro que tenha nascido).

Uso: uv run python evals/gpu-2026-10-12-velocidade/medir_velocidade.py --orcamento-usd 2.0
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from argparse import Namespace
from pathlib import Path

import render2go_worker  # noqa: F401 (GPU2GO_* → RENDER2GO_*)
from render2go_worker import lote_nuvem as ln
from render2go_worker import runpod_pods

AQUI = Path(__file__).resolve().parent
PRAZO_MAX_S = 100 * 60  # trava do teste: o pod nunca passa disso
MARGEM_S = 6 * 60  # antes do desligamento: para de acompanhar e baixa os resultados
GPUS = ["NVIDIA L40S", "NVIDIA RTX 6000 Ada Generation"]  # 48 GB, sm89
marcas: dict[str, float] = {}


def marca(nome: str, t0: float) -> None:
    marcas[nome] = round(time.monotonic() - t0, 1)
    ln._log(f"MARCA {nome}: {marcas[nome]:.1f} s")


def baixar(pod: ln.Pod, raiz: str, saida: Path) -> list[str]:
    """Resultado, logs e folhas (o que existir); devolve o que veio."""
    m = f"{raiz}/medidas"
    _, ls = pod.agente.exec(f"ls {m}/folhas 2>/dev/null; ls {m}/*.log 2>/dev/null", 30)
    nomes = ["resultado.json", "estado_extras"]
    nomes += [f"folhas/{x}" for x in ls.split() if x.endswith(".jpg") and "/" not in x]
    nomes += [x.removeprefix(f"{m}/") for x in ls.split() if x.startswith(f"{m}/")]
    veio = []
    for nome in nomes:
        destino = saida / nome
        destino.parent.mkdir(parents=True, exist_ok=True)
        try:
            if pod.agente.get(f"{m}/{nome}", destino):
                veio.append(nome)
        except Exception as e:  # noqa: BLE001 (baixa o que der)
            ln._log(f"AVISO: {nome} não veio: {e}")
    for nome in ("medidas.log", "extras.log", "bootstrap.log", "tempos"):
        try:
            if pod.agente.get(f"{raiz}/{nome}", saida / nome):
                veio.append(nome)
        except Exception as e:  # noqa: BLE001
            ln._log(f"AVISO: {nome} não veio: {e}")
    return veio


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--orcamento-usd", type=float, required=True)
    p.add_argument("--saida", type=Path, default=AQUI / "saida")
    p.add_argument(
        "--fases", default="124", help="fases do no_pod.py (1 frio, 2 worker e RIFE, 4 sage)"
    )
    a = p.parse_args()
    prov = ln.RunPod()
    saldo = prov.saldo()
    precos = [x for g in GPUS if (x := prov.preco(g, "secure"))]
    if saldo is None or not precos:
        sys.exit(f"sem saldo ({saldo}) ou sem preço das GPUs ({precos})")
    usd_h = max(precos)
    teto = min(a.orcamento_usd, saldo - max(ln.RESERVA_SALDO_USD, 0.1 * saldo))
    ln._log(
        f"saldo US$ {saldo:.2f}; teto do teste US$ {teto:.2f}; GPUs {GPUS} até US$ {usd_h:.2f}/h"
    )
    if teto / usd_h * 3600 < 30 * 60:
        sys.exit("o orçamento ou o saldo não pagam 30 min de pod")

    def prazo_s(preco: float) -> int:  # o que o teto paga, nunca mais que PRAZO_MAX_S
        return int(min(PRAZO_MAX_S, teto / preco * 3600))

    def ajustar(pod: ln.Pod) -> None:  # pelo preço real da GPU que veio
        vivo = time.monotonic() - pod.criado
        em = pod.agente.deadman(max(60, prazo_s(pod.info.usd_h) - vivo))
        pod.deadman_s = int(vivo + em)
        ln._log(f"desligamento automático em {em / 60:.1f} min")

    a.saida.mkdir(parents=True, exist_ok=True)
    ns = Namespace(
        opcoes=[runpod_pods.Opcao(g, "secure") for g in GPUS],
        gpus_por_pod=1,
        comfy_por_gpu=1,
        volume=None,
        espelho=ln.preparar_espelho(),
        repo_tgz=ln.empacotar_repo(a.saida / ln.ESTADO / "repo.tgz"),
        saida=a.saida,
        qualidade="rapida",
        orcamento_usd=a.orcamento_usd,
    )
    orq = ln.Orquestrador(ns, [], prov, ln.Remoto())
    raiz = orq.remoto.raiz
    pod: ln.Pod | None = None
    info: ln.PodInfo | None = None
    execucao: dict = {"teto_usd": round(teto, 2), "saldo_inicial_usd": round(saldo, 2)}
    t0 = time.monotonic()
    try:
        pod = orq.subir_pod(
            1, prazo_s(usd_h), cedo=True, ao_agente=ajustar, prefixo="render2go-teste-vel"
        )
        if pod is None:
            ln._log("o pod não subiu")
            return 4
        info = pod.info
        marca("servicos_no_ar", t0)
        pod.agente.put(AQUI / "preparar_extras.sh", f"{raiz}/preparar_extras.sh")
        pod.agente.bg(f"bash {raiz}/preparar_extras.sh {raiz}/medidas", f"{raiz}/extras.log")
        estado = orq._esperar(
            pod, f"cat {raiz}/estado 2>/dev/null", ("PRONTO", "FALHOU"), ln.ESPERA_INSTALACAO_S
        )
        marca("pronto", t0)
        if not estado.startswith("PRONTO"):
            raise ln.NuvemError(f"instalação: {estado}")
        pod.agente.put(AQUI / "no_pod.py", f"{raiz}/no_pod.py")
        pod.agente.bg(
            f"cd /opt/render2go/worker && PATH=/opt/venv/bin:$PATH PYTHONPATH=src "
            f"RENDER2GO_SAFETY=c1-revisao-humana python {raiz}/no_pod.py {raiz}/medidas "
            f"--fases {a.fases} > {raiz}/medidas.log 2>&1; "
            f"echo FIM_DO_ROTEIRO >> {raiz}/medidas.log",
            f"{raiz}/medidas.bg.log",
        )
        visto = ""
        while True:  # acompanha até o fim ou até a margem antes do desligamento automático
            limite = pod.criado + pod.deadman_s - MARGEM_S
            if time.monotonic() > limite:
                ln._log("perto do desligamento automático: baixando o que já saiu")
                execucao["interrompido"] = "margem do desligamento"
                break
            time.sleep(60)
            try:
                _, out = pod.agente.exec(
                    f"grep -h 'medida\\]' {raiz}/medidas.log | tail -4; "
                    f"tail -1 {raiz}/medidas.log; "
                    f"tail -2 {raiz}/medidas/estado_extras 2>/dev/null",
                    30,
                )
            except Exception as e:  # noqa: BLE001 (rede: tenta no próximo minuto)
                ln._log(f"AVISO: acompanhamento: {e}")
                continue
            if out != visto:
                ln._log("no pod:\n" + out.strip())
                visto = out
            if "FIM_DO_ROTEIRO" in out:
                marca("roteiro_terminado", t0)
                break
        execucao["baixado"] = baixar(pod, raiz, a.saida)
        marca("baixado", t0)
        return 0
    except (ln.NuvemError, runpod_pods.RunPodError, OSError) as e:
        ln._log(f"FALHA: {e}")
        execucao["falha"] = str(e)
        if pod is not None:
            try:
                execucao["baixado"] = baixar(pod, raiz, a.saida)
            except Exception as e2:  # noqa: BLE001
                ln._log(f"AVISO: nada baixado: {e2}")
        return 1
    finally:
        ligado = (time.monotonic() - pod.criado) if pod is not None else 0.0
        if info is not None:
            try:
                prov.encerrar(info.id)
                ln._log(f"pod {info.id} encerrado")
            except Exception as e:  # noqa: BLE001
                ln._log(f"AVISO: encerre à mão o pod {info.id}: {e}")
        for outro in orq.pods:  # qualquer outro que tenha nascido (substituto)
            if outro.vivo and (info is None or outro.info.id != info.id):
                orq._encerrar(outro, "fim do teste")
        custo = ligado / 3600 * (info.usd_h if info else usd_h)
        execucao |= {
            "marcas": marcas,
            "gpu": info.gpu if info else None,
            "usd_h": info.usd_h if info else None,
            "ligado_s": round(ligado),
            "custo_estimado_usd": round(custo, 3),
        }
        (a.saida / "execucao.json").write_text(json.dumps(execucao, indent=1, ensure_ascii=False))
        ln._log(f"EXECUÇÃO: {json.dumps(execucao, ensure_ascii=False)}")


if __name__ == "__main__":
    sys.exit(main())
