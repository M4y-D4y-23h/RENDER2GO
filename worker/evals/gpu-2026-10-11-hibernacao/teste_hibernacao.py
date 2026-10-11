"""Teste real da hibernação (ADR 0011) e do filtro de imagem nos quadros (ADR 0005), 11/10/2026.

1. Sobe um pod do zero no modo disco de volume, como o orquestrador com RENDER2GO_PODS_HIBERNADOS:
   criação → agente → serviços (estado AMBIENTE) → PRONTO; gera 1 vídeo SFW e passa o filtro na GPU.
2. Hiberna (stop), espera e liga de novo (start), sem download: start → agente → serviços → PRONTO;
   gera o mesmo vídeo (modelos lidos do disco de volume) e espera a conferência do SHA-256.
3. Encerra o pod (o disco de volume vai junto) e imprime as marcas e o gasto estimado.

Travas: desligamento automático no agente em cada subida (≤ PRAZO_MAX_S e o que o orçamento ainda
paga), `--orcamento-usd` abaixo do saldo do RunPod menos a reserva, e try/finally que encerra o pod.

Uso: uv run python evals/gpu-2026-10-11-hibernacao/teste_hibernacao.py --orcamento-usd 0.6
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
PRAZO_MAX_S = 25 * 60  # trava do teste: nenhuma subida passa disso
GPUS = ["NVIDIA L40S", "NVIDIA RTX 6000 Ada Generation"]  # 48 GB, as primeiras da ordem padrão
marcas: dict[str, float] = {}
gasto = {"usd": 0.0}


def marca(nome: str, t0: float) -> None:
    marcas[nome] = round(time.monotonic() - t0, 1)
    ln._log(f"MARCA {nome}: {marcas[nome]:.1f} s")


def esperar_pronto(orq: ln.Orquestrador, pod: ln.Pod) -> str:
    return orq._esperar(
        pod,
        f"cat {orq.remoto.raiz}/estado 2>/dev/null",
        ("PRONTO", "FALHOU"),
        ln.ESPERA_INSTALACAO_S,
    )


def video(orq: ln.Orquestrador, pod: ln.Pod, nome: str) -> dict:
    """Em segundo plano no pod (o proxy do RunPod corta pedidos HTTP de ~100 s) e consultado."""
    r = orq.remoto
    pod.agente.put(AQUI / "video_e_filtro.py", f"{r.raiz}/video_e_filtro.py")
    pod.agente.bg(
        f"cd /opt/render2go/worker && PATH=/opt/venv/bin:$PATH PYTHONPATH=src "
        f"python {r.raiz}/video_e_filtro.py {r.raiz}/{nome} > {r.raiz}/{nome}.json; "
        f"echo FIM >> {r.raiz}/{nome}.json",
        f"{r.raiz}/{nome}.log",
    )
    orq._esperar(pod, f"tail -1 {r.raiz}/{nome}.json 2>/dev/null", ("FIM",), 900)
    _, out = pod.agente.exec(f"cat {r.raiz}/{nome}.json; tail -5 {r.raiz}/{nome}.log", 30)
    linhas = [x for x in out.splitlines() if x.startswith("{")]
    res = json.loads(linhas[-1]) if linhas else {"erro": out[-800:]}
    ln._log(f"vídeo e filtro: {res}")
    return res


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--orcamento-usd", type=float, required=True)
    p.add_argument("--saida", type=Path, default=AQUI / "saida")
    p.add_argument("--espera-parado-s", type=float, default=30)
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
    if teto / usd_h * 3600 < 10 * 60:
        sys.exit("o orçamento ou o saldo não pagam 10 min de pod")

    def restante_s(preco: float) -> int:
        return int(min(PRAZO_MAX_S, max(0.0, teto - gasto["usd"]) / preco * 3600))

    def ajustar(
        pod: ln.Pod,
    ) -> None:  # o prazo real, pelo preço da GPU que veio e o que sobra do teto
        vivo = time.monotonic() - pod.criado
        em = pod.agente.deadman(max(60, restante_s(pod.info.usd_h) - vivo))
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
    pod: ln.Pod | None = None
    info: ln.PodInfo | None = None
    resultados: dict = {}
    t_ligado = 0.0
    try:
        # 1. do zero, no modo disco de volume
        t0 = time.monotonic()
        pod = orq.subir_pod(
            1,
            restante_s(usd_h),
            cedo=True,
            ao_agente=ajustar,
            volume_gb=70,
            prefixo="render2go-teste-hib",
        )
        if pod is None:
            ln._log("o pod não subiu")
            return 4
        info = pod.info
        marca("A_servicos_no_ar", t0)
        estado = esperar_pronto(orq, pod)
        marca("A_pronto", t0)
        _, tempos = pod.agente.exec(f"cat {orq.remoto.raiz}/tempos", 30)
        resultados["A_tempos"] = tempos.strip().splitlines()
        if not estado.startswith("PRONTO"):
            raise ln.NuvemError(f"instalação: {estado}")
        _, du = pod.agente.exec(
            "du -sh /workspace/r2g/* 2>/dev/null; df -h /workspace | tail -1", 120
        )
        resultados["A_volume"] = du.strip().splitlines()
        resultados["A_video"] = video(orq, pod, "video_a")
        marca("A_video_pronto", t0)
        # 2. hiberna e volta
        t_ligado += time.monotonic() - pod.criado
        gasto["usd"] = t_ligado / 3600 * info.usd_h
        if not orq.hibernar(pod, "teste"):
            raise ln.NuvemError("não hibernou")
        pod = None
        ln._log(f"hibernado; gasto ~US$ {gasto['usd']:.3f}; esperando {a.espera_parado_s:.0f} s")
        time.sleep(a.espera_parado_s)
        t1 = time.monotonic()
        pod = orq.retomar_pod(info, cedo=True, ao_agente=ajustar)
        marca("B_servicos_no_ar", t1)
        estado = esperar_pronto(orq, pod)
        marca("B_pronto", t1)
        _, tempos = pod.agente.exec(f"cat {orq.remoto.raiz}/tempos", 30)
        resultados["B_tempos"] = tempos.strip().splitlines()
        resultados["B_video"] = video(orq, pod, "video_b")
        marca("B_video_pronto", t1)
        orq._esperar(pod, f"grep -c . {orq.remoto.raiz}/conferir.log 2>/dev/null", ("1", "2"), 600)
        _, conf = pod.agente.exec(
            f"cat {orq.remoto.raiz}/conferir.log; cat {orq.remoto.raiz}/estado", 30
        )
        resultados["B_conferencia"] = conf.strip().splitlines()
        marca("B_sha256_conferido", t1)
        return 0
    except (ln.NuvemError, runpod_pods.RunPodError, OSError) as e:
        ln._log(f"FALHA: {e}")
        resultados["falha"] = str(e)
        return 1
    finally:
        if pod is not None:
            t_ligado += time.monotonic() - pod.criado
        if info is not None:
            try:
                prov.encerrar(info.id)  # o disco de volume vai junto: nada fica cobrando
                ln._log(f"pod {info.id} encerrado")
            except Exception as e:  # noqa: BLE001
                ln._log(f"AVISO: encerre à mão o pod {info.id}: {e}")
        for p in orq.pods:  # qualquer outro que tenha nascido (substituto)
            if p.vivo and (info is None or p.info.id != info.id):
                orq._encerrar(p, "fim do teste")
        custo = t_ligado / 3600 * (info.usd_h if info else usd_h)
        resultados |= {
            "marcas": marcas,
            "gpu": info.gpu if info else None,
            "usd_h": info.usd_h if info else None,
        }
        resultados |= {"ligado_s": round(t_ligado), "custo_estimado_usd": round(custo, 3)}
        (a.saida / "resultado.json").write_text(
            json.dumps(resultados, indent=1, ensure_ascii=False)
        )
        ln._log(f"RESULTADO: {json.dumps(resultados, ensure_ascii=False)}")


if __name__ == "__main__":
    sys.exit(main())
