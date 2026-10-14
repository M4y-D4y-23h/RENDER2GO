"""Rodada de qualidade em GPU de 13/10/2026 (ADR 0014), do lado da sessão: sobe o pod, acompanha,
baixa os resultados e encerra. Protocolo e critérios: LEIAME.md.

Um pod vive no máximo 2 h (regra do projeto), então a rodada usa três, um depois do outro:
- `--pod 1`: fases P (prompts e legendas) e 4 (480p: A, B e D);
- `--pod 2`: fase 7 (720p: A e B), com os prompts do pod 1 (`--prompts`, o resultado.json dele);
- `--pod 3`: fase S (a vencedora em 480p sem e com o SageAttention), com os prompts do pod 1.

1. Registra o saldo do RunPod e cria um pod L40S secure como o orquestrador de produção, com o nome
   `render2go-teste-qualidade-<n>` (nunca o dos pods de produção, `render2go-fila-*`, nem os
   hibernados, que este script não toca) e o desligamento automático armado na criação (o agente
   encerra o pod sozinho no prazo, ajustado pelo preço real da GPU), os dois conjuntos de modelos
   (`i2v_r1v2` e `i2v_oficial`) e a legenda por foto ligada (o modelo de visão e o Ollama de CPU).
2. No AMBIENTE (pod 3), começa a compilação do SageAttention (preparar_sage.sh) em segundo plano.
3. No PRONTO, roda o no_pod.py em segundo plano e acompanha a cada minuto.
4. Baixa o resultado, os logs, as tiras e os vídeos para `--saida`, encerra o pod (try/finally),
   confere na API que ele não existe mais e registra o saldo de novo.

Sem teto de orçamento (decisão do dono de 13/10/2026): a trava é o prazo do pod (PRAZO_MAX_S).

Uso (da pasta `worker`):
    uv run python evals/gpu-2026-10-13-qualidade/medir_qualidade.py --pod 1 --saida <pasta>
    uv run python evals/gpu-2026-10-13-qualidade/medir_qualidade.py --pod 2 --saida <pasta> \\
        --prompts <pasta do pod 1>/resultado.json
    (o pod 3 como o 2)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from argparse import Namespace
from pathlib import Path

import render2go_worker  # noqa: F401 (GPU2GO_* → RENDER2GO_*)
from render2go_worker import lote_nuvem as ln
from render2go_worker import pod_agente, runpod_pods

AQUI = Path(__file__).resolve().parent
PRAZO_MAX_S = pod_agente.MAX_DEADMAN_S - 5 * 60  # o agente nunca passa de 2 h; 5 min de folga
MARGEM_S = 8 * 60  # antes do desligamento: para de acompanhar e baixa os resultados
GPUS = ["NVIDIA L40S"]  # a 1ª GPU de produção (RENDER2GO_GPUS)
CONJUNTOS = "i2v_r1v2,i2v_oficial"
FASES = {1: "P4", 2: "7", 3: "S"}
TENTATIVAS = 3  # pods que não sobem (o descartado custa só o tempo até o descarte)
marcas: dict[str, float] = {}


def marca(nome: str, t0: float) -> None:
    marcas[nome] = round(time.monotonic() - t0, 1)
    ln._log(f"MARCA {nome}: {marcas[nome]:.1f} s")


def pods_da_conta(prov: ln.RunPod) -> list[dict]:
    """Os pods da conta (id, nome, estado), só para conferir; nada aqui mexe em pod nenhum."""
    d = runpod_pods._graphql("query { myself { pods { id name desiredStatus } } }", prov.chave)
    return list((d.get("myself") or {}).get("pods") or [])


def baixar(pod: ln.Pod, raiz: str, saida: Path) -> list[str]:
    """O resultado, os logs, as tiras e os vídeos (o que existir; as fotos preparadas, não), cada
    um conferido pelo SHA-256 calculado no pod (até 3 tentativas): o proxy desta sessão já cortou
    envios no meio (13/10/2026)."""
    q = f"{raiz}/qualidade/saida"
    _, ls = pod.agente.exec(
        f"cd {q} 2>/dev/null && find . -type f \\( -name '*.json' -o -name '*.log' -o -name '*.jpg'"
        " -o -name '*.mp4' -o -name 'estado_sage' -o -name '*.whl' \\) ! -path './tmp/*'"
        " -exec sha256sum {} +",
        120,
    )
    esperado = {}
    for linha in ls.splitlines():
        partes = linha.split(maxsplit=1)
        if len(partes) == 2 and partes[1].startswith("./"):
            esperado[partes[1].removeprefix("./")] = partes[0]
    veio = []
    for nome in sorted(esperado, key=lambda n: (n.endswith(".mp4"), n)):  # os vídeos por último
        for tentativa in range(3):
            try:
                if pod.agente.get(f"{q}/{nome}", saida / nome):
                    h = hashlib.sha256((saida / nome).read_bytes()).hexdigest()
                    if h == esperado[nome]:
                        veio.append(nome)
                        break
                    ln._log(f"AVISO: {nome} veio diferente (tentativa {tentativa + 1})")
            except Exception as e:  # noqa: BLE001 (baixa o que der)
                ln._log(f"AVISO: {nome} não veio: {e}")
    for nome in ("qualidade.log", "bootstrap.log", "tempos", "servicos.log", "legenda.log"):
        try:
            if pod.agente.get(f"{raiz}/{nome}", saida / "pod" / nome):
                veio.append(f"pod/{nome}")
        except Exception as e:  # noqa: BLE001
            ln._log(f"AVISO: {nome} não veio: {e}")
    return veio


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pod", type=int, choices=(1, 2, 3), required=True)
    p.add_argument("--saida", type=Path, required=True)
    p.add_argument("--prompts", type=Path, help="pods 2 e 3: o resultado.json do pod 1")
    a = p.parse_args()
    if a.pod != 1 and not (a.prompts and a.prompts.is_file()):
        sys.exit("os pods 2 e 3 precisam do resultado.json do pod 1 (--prompts)")
    prov = ln.RunPod()
    saldo = prov.saldo()
    usd_h = prov.preco(GPUS[0], "secure")
    if saldo is None or not usd_h:
        sys.exit(f"sem saldo ({saldo}) ou sem preço da GPU ({usd_h})")
    antes = pods_da_conta(prov)
    ln._log(f"saldo US$ {saldo:.2f}; {GPUS[0]} secure US$ {usd_h:.2f}/h; pods na conta: "
            + ", ".join(f"{x['name']} ({x['desiredStatus']})" for x in antes))  # fmt: skip

    def ajustar(pod: ln.Pod) -> None:  # o prazo pelo relógio da subida (o agente conta da dele)
        vivo = time.monotonic() - pod.criado
        em = pod.agente.deadman(max(60, PRAZO_MAX_S - vivo))
        pod.deadman_s = int(vivo + em)
        ln._log(f"desligamento automático em {em / 60:.1f} min")

    a.saida.mkdir(parents=True, exist_ok=True)
    esp = ln.preparar_espelho()
    if esp is not None and esp.faltando():  # o pod de teste nunca grava no espelho de produção
        ln._log("o espelho está incompleto: este pod baixa tudo da origem e não grava nada nele")
        esp = None
    ns = Namespace(
        opcoes=[runpod_pods.Opcao(g, "secure") for g in GPUS],
        gpus_por_pod=1,
        comfy_por_gpu=1,
        volume=None,
        espelho=esp,
        repo_tgz=ln.empacotar_repo(a.saida / ln.ESTADO / "repo.tgz"),
        saida=a.saida,
        qualidade="rapida",
        orcamento_usd=usd_h * PRAZO_MAX_S / 3600,
        legenda=True,  # o modelo de visão no bootstrap e o Ollama de CPU nos serviços (ADR 0014)
    )
    remoto = ln.Remoto()
    remoto.instalar = remoto.instalar.replace(" i2v_r1v2 ", f" {CONJUNTOS} ")
    assert CONJUNTOS in remoto.instalar
    orq = ln.Orquestrador(ns, [], prov, remoto)
    raiz = orq.remoto.raiz
    q = f"{raiz}/qualidade"
    pod: ln.Pod | None = None
    info: ln.PodInfo | None = None
    execucao: dict = {"pod": a.pod, "saldo_inicial_usd": round(saldo, 4), "usd_h_tabela": usd_h,
                      "pods_antes": antes}  # fmt: skip
    encerrar = orq._encerrar
    descartados: list[dict] = []

    def encerrar_com_logs(p: ln.Pod, motivo: str) -> None:
        """Antes de encerrar um pod que falhou na subida, guarda o fim dos logs dele (o orquestrador
        de produção só registra o motivo; aqui, o diagnóstico fica com a rodada)."""
        if motivo != "fim do teste":
            try:
                _, out = p.agente.exec(
                    f"cat {raiz}/estado {raiz}/tempos {raiz}/rede 2>/dev/null; tail -n 25 "
                    f"{raiz}/bootstrap.log {raiz}/modelos.log {raiz}/ambiente.log 2>&1 "
                    "| tr '\\r' '\\n' | tail -n 60; df -h / | tail -1",
                    30,
                )
                (a.saida / f"falha-{p.info.id}.log").write_text(out)
                ln._log(f"pod {p.nome} ({motivo}): fim dos logs\n{out[-1500:]}")
            except Exception as e:  # noqa: BLE001 (diagnóstico: o encerramento vem de qualquer jeito)
                ln._log(f"AVISO: os logs do pod {p.nome} não vieram: {e}")
            descartados.append({"pod_id": p.info.id, "motivo": motivo,
                                "ligado_s": round(time.monotonic() - p.criado),
                                "usd_h": p.info.usd_h})  # fmt: skip
        encerrar(p, motivo)

    orq._encerrar = encerrar_com_logs
    t0 = time.monotonic()
    try:
        for tentativa in range(1, TENTATIVAS + 1):  # host que não sobe: outro pod, como na produção
            pod = orq.subir_pod(
                10 * a.pod + tentativa, PRAZO_MAX_S, cedo=True, ao_agente=ajustar,
                prefixo="render2go-teste-qualidade",
            )  # fmt: skip
            if pod is not None:
                break
            ln._log(f"o pod não subiu (tentativa {tentativa} de {TENTATIVAS})")
        execucao["descartados"] = descartados
        if pod is None:
            return 4
        info = pod.info
        execucao |= {"pod_id": info.id, "nome": pod.nome}
        marca("servicos_no_ar", t0)
        pod.agente.exec(f"mkdir -p {q}/saida", 30)
        for nome in ("no_pod.py", "plano.json", "preparar_sage.sh"):
            pod.agente.put(AQUI / nome, f"{q}/{nome}")
        if a.pod != 1:
            pod.agente.put(a.prompts, f"{q}/prompts.json")
        if "S" in FASES[a.pod]:
            pod.agente.bg(f"bash {q}/preparar_sage.sh {q}/saida", f"{raiz}/sage.log")
        estado = orq._esperar(
            pod, f"cat {raiz}/estado 2>/dev/null", ("PRONTO", "FALHOU"), ln.ESPERA_INSTALACAO_S
        )
        marca("pronto", t0)
        if not estado.startswith("PRONTO"):
            raise ln.NuvemError(f"instalação: {estado}")
        extra = f" --prompts {q}/prompts.json" if a.pod != 1 else ""
        pod.agente.bg(
            f"cd /opt/render2go/worker && PATH=/opt/venv/bin:$PATH PYTHONPATH=src "
            f"RENDER2GO_SAFETY=c1-revisao-humana python {q}/no_pod.py {q}/saida "
            f"--fases {FASES[a.pod]}{extra} > {raiz}/qualidade.log 2>&1; "
            f"echo FIM_DO_ROTEIRO >> {raiz}/qualidade.log",
            f"{raiz}/qualidade.bg.log",
        )
        visto = ""
        while True:  # acompanha até o fim ou até a margem antes do desligamento automático
            if time.monotonic() > pod.criado + pod.deadman_s - MARGEM_S:
                ln._log("perto do desligamento automático: baixando o que já saiu")
                execucao["interrompido"] = "margem do desligamento"
                break
            time.sleep(60)
            try:
                _, out = pod.agente.exec(
                    f"grep -h 'medida\\]' {raiz}/qualidade.log | tail -3; "
                    f"tail -1 {raiz}/qualidade.log; tail -1 {q}/saida/estado_sage 2>/dev/null",
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
        execucao["baixado"] = len(baixar(pod, raiz, a.saida))
        marca("baixado", t0)
        return 0
    except (ln.NuvemError, runpod_pods.RunPodError, OSError) as e:
        ln._log(f"FALHA: {e}")
        execucao["falha"] = str(e)
        if pod is not None:
            try:
                execucao["baixado"] = len(baixar(pod, raiz, a.saida))
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
        for outro in orq.pods:  # qualquer outro que este script tenha criado (substituto)
            if outro.vivo and (info is None or outro.info.id != info.id):
                orq._encerrar(outro, "fim do teste")
        time.sleep(20)
        try:  # confere na API: o pod deste script não existe mais; os de produção, como estavam
            depois = pods_da_conta(prov)
            execucao["pods_depois"] = depois
            execucao["pod_encerrado"] = info is None or all(x["id"] != info.id for x in depois)
            execucao["saldo_final_usd"] = prov.saldo()
        except Exception as e:  # noqa: BLE001
            execucao["conferencia_api"] = f"falhou: {e}"
        execucao |= {
            "marcas": marcas,
            "gpu": info.gpu if info else None,
            "usd_h": info.usd_h if info else None,
            "datacentro": info.datacentro if info else None,
            "ligado_s": round(ligado),
            "custo_estimado_usd": round(
                ligado / 3600 * (info.usd_h if info else usd_h)
                + sum(d["ligado_s"] / 3600 * d["usd_h"] for d in descartados),
                3,
            ),
        }
        (a.saida / "execucao.json").write_text(json.dumps(execucao, indent=1, ensure_ascii=False))
        ln._log(f"EXECUÇÃO: {json.dumps(execucao, ensure_ascii=False)}")


if __name__ == "__main__":
    sys.exit(main())
