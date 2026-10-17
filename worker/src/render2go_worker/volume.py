"""Diagnóstico do disco de volume de um pod que hiberna (ADR 0011; 17/10/2026).

O bootstrap (`scripts/bootstrap_pod.sh`) roda este arquivo no pod com `python3 -I` (só a biblioteca
padrão: nada do venv, que pode estar no próprio volume em dúvida) e grava `$R/volume.json`:

- onde o cache está (`local`, ex.: /workspace/r2g) e em que montagem: o ponto de montagem, o
  sistema de arquivos e se ele é **persistente** (uma montagem própria, que não é o disco do
  contêiner: overlay e tmpfs são apagados na parada do pod);
- espaço total e livre da montagem;
- a **sentinela** (`sentinela.json` no volume: o pod que gravou e quando), o **marcador**
  (`instalado`: a versão e quando a cópia terminou) e as entradas do volume;
- o motivo de o cache não ter sido usado na subida (`inicio`) e o resultado da gravação no fim da
  1ª subida (`gravacao`), com um código de MOTIVOS.

Sem segredos: nada do ambiente além do id do pod (RUNPOD_POD_ID), nada dos pedidos. O
orquestrador lê o arquivo com `ler_diagnostico` (só os campos conhecidos, com limites) e guarda o
diagnóstico da sessão para o painel (relatório de GPUs).

Uso no pod:
    volume.py diagnosticar --local L --saida R/volume.json --etapa inicio|gravacao
                           [--versao V] [--subida fria|retomada] [--motivo M] [--detalhe D]
                           [--necessario BYTES]
    volume.py persistente --local L        (código 0: montagem persistente; 1: não)
    volume.py cabe --local L --bytes N     (código 0: a montagem comporta N bytes; 1: não)
    volume.py sentinela --local L [--versao V]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

# o motivo de o cache não servir (ou "ok"). `inicio`: a subida; `gravacao`: a cópia no fim da 1ª
MOTIVOS = {
    "ok": "cache usado (ou gravado) sem problema",
    "primeira": "1ª subida do pod: o volume ainda não tinha nada (esperado)",
    "sem_local": "o pod não tem disco de volume configurado",
    "sem_montagem": "o local do cache não está numa montagem persistente (seria apagado na parada)",
    "vazio": "o volume está vazio (sem sentinela nem arquivos do cache)",
    "sem_marcador": "há arquivos, sem o marcador: a cópia anterior não terminou ou foi invalidada",
    "outra_versao": "o volume é de outra versão do ambiente ou dos modelos",
    "repo": "o código do serviço não abriu no pod",
    "incompleto": "falta um modelo no volume (ou o tamanho não confere)",
    "ambiente_incompleto": "falta o venv, o Ollama ou o modelo do compilador no volume",
    "corrompido": "o SHA-256 de um arquivo do volume não confere",
    "outro_pod": "a sentinela é de outro pod",
    "sem_espaco": "a montagem não comporta o cache",
    "copia_falhou": "a cópia para o volume falhou no meio",
}
SISTEMAS_EFEMEROS = ("overlay", "tmpfs", "ramfs", "aufs")
CAMPOS_ETAPA = ("motivo", "detalhe", "em", "necessario_b")
MAX_ENTRADAS = 20


def _limpo(texto: object, n: int = 200) -> str:
    """Só caracteres de nomes de arquivo e mensagens curtas (nada que vire marcação ou controle)."""
    return re.sub(r"[^\w .,:;/()+=@#%-]", "", str(texto))[:n]


def _existente(caminho: str) -> str:
    """O próprio caminho ou o pai mais próximo que existe (o cache pode ainda não existir)."""
    p = os.path.abspath(caminho)
    while not os.path.exists(p) and p != os.path.dirname(p):
        p = os.path.dirname(p)
    return os.path.realpath(p)


def montagem(caminho: str, mountinfo: str = "/proc/self/mountinfo") -> dict:
    """O ponto de montagem que contém `caminho` (o mais longo), o sistema de arquivos e se é
    persistente: uma montagem própria (não a raiz do contêiner) com um sistema de arquivos que não
    é apagado na parada."""
    alvo = _existente(caminho)
    melhor: tuple[str, str] = ("/", "?")
    try:
        with open(mountinfo, encoding="utf-8", errors="replace") as f:
            for linha in f:
                partes = linha.split()
                if "-" not in partes:
                    continue
                ponto = partes[4].replace("\\040", " ")
                fs = partes[partes.index("-") + 1]
                dentro = alvo == ponto or alvo.startswith(ponto.rstrip("/") + "/")
                if dentro and len(ponto) >= len(melhor[0]):
                    melhor = (ponto, fs)
    except OSError:
        pass
    ponto, fs = melhor
    # o mesmo dispositivo da raiz: o cache estaria no disco do contêiner, mesmo com o ponto achado
    try:
        mesmo_disco = os.stat(alvo).st_dev == os.stat("/").st_dev
    except OSError:
        mesmo_disco = True
    persistente = ponto != "/" and not mesmo_disco and not fs.startswith(SISTEMAS_EFEMEROS)
    return {"montagem": ponto, "fs": fs, "persistente": persistente}


def espaco(caminho: str) -> dict:
    try:
        s = os.statvfs(_existente(caminho))
    except OSError:
        return {"total_b": None, "livre_b": None}
    return {"total_b": s.f_blocks * s.f_frsize, "livre_b": s.f_bavail * s.f_frsize}


def _ler_json(caminho: str) -> dict | None:
    try:
        with open(caminho, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def sentinela(local: str) -> dict | None:
    d = _ler_json(os.path.join(local, "sentinela.json"))
    if d is None:
        return None
    return {
        "pod": _limpo(d.get("pod") or "", 64) or None,
        "criado": d.get("criado") if isinstance(d.get("criado"), int | float) else None,
        "versao": _limpo(d.get("versao") or "", 32) or None,
    }


def escrever_sentinela(local: str, versao: str = "") -> dict:
    """Na 1ª subida, antes de gravar o cache: o pod e quando. Se já existe uma do mesmo pod, fica
    (o volume continua o mesmo); de outro pod, é trocada (e o diagnóstico registra "outro_pod")."""
    pod = os.environ.get("RUNPOD_POD_ID", "")
    atual = sentinela(local)
    if atual and atual.get("pod") == (pod or None):
        return atual
    os.makedirs(local, exist_ok=True)
    d = {"pod": pod, "criado": time.time(), "versao": versao}
    # sem rename (volume que não é POSIX, regra do projeto): escrita direta
    with open(os.path.join(local, "sentinela.json"), "w", encoding="utf-8") as f:
        json.dump(d, f)
    return {"pod": pod or None, "criado": d["criado"], "versao": versao or None}


def marcador(local: str) -> dict | None:
    try:
        with open(os.path.join(local, "instalado"), encoding="utf-8") as f:
            partes = f.read(200).split()
    except OSError:
        return None
    em = None
    if len(partes) > 1:
        try:
            em = float(partes[1])
        except ValueError:
            em = None
    return {"versao": _limpo(partes[0], 32) if partes else None, "em": em}


def entradas(local: str) -> list[str] | None:
    try:
        return sorted(_limpo(n, 60) for n in os.listdir(local))[:MAX_ENTRADAS]
    except OSError:
        return None


def diagnosticar(
    local: str,
    etapa: str,
    versao: str = "",
    subida: str = "",
    motivo: str = "",
    detalhe: str = "",
    necessario: int | None = None,
    anterior: dict | None = None,
) -> dict:
    """O diagnóstico do volume agora, com o motivo desta etapa. A classificação olha primeiro a
    montagem (sem montagem persistente, nada mais importa) e a sentinela (de outro pod); no começo
    de uma subida, um volume sem nada é "primeira" num pod novo e "vazio" na volta da hibernação."""
    d = dict(anterior or {})
    m = montagem(local)
    ss = sentinela(local)
    pod = os.environ.get("RUNPOD_POD_ID") or None
    lista = entradas(local)
    d.update(
        v=1,
        local=_limpo(local, 120),
        **m,
        **espaco(local),
        pod=_limpo(pod, 64) if pod else None,
        sentinela=ss,
        marcador=marcador(local),
        versao=_limpo(versao, 32) or None,
        entradas=lista,
    )
    if subida in ("fria", "retomada"):
        d["subida"] = subida
    motivo = motivo if motivo in MOTIVOS else ("ok" if not motivo else "sem_marcador")
    if motivo != "ok":
        if not m["persistente"]:
            motivo = "sem_montagem"
        elif ss and pod and ss.get("pod") and ss["pod"] != pod:
            motivo = "outro_pod"
        elif etapa == "inicio" and not ss and motivo in ("sem_marcador", "outra_versao"):
            if not lista:
                motivo = "vazio" if d.get("subida") == "retomada" else "primeira"
            elif motivo == "sem_marcador" and d.get("subida") == "fria":
                motivo = "primeira"
    e = {"motivo": motivo, "detalhe": _limpo(detalhe) or None, "em": time.time()}
    if necessario is not None:
        e["necessario_b"] = int(necessario)
    d[etapa] = e
    return d


def ler_diagnostico(texto: str) -> dict | None:
    """Para o orquestrador: o JSON que o pod mandou, só com os campos conhecidos e com limites (o
    pod roda o conteúdo dos clientes; o painel nunca recebe um campo que não esteja aqui)."""
    try:
        d = json.loads(texto[:20000])
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None

    def num(x: object) -> float | int | None:
        return x if isinstance(x, int | float) and not isinstance(x, bool) else None

    def txt(x: object, n: int = 120) -> str | None:
        return _limpo(x, n) if isinstance(x, str) and x else None

    out: dict = {
        "v": num(d.get("v")),
        "local": txt(d.get("local")),
        "montagem": txt(d.get("montagem")),
        "fs": txt(d.get("fs"), 40),
        "persistente": d.get("persistente") if isinstance(d.get("persistente"), bool) else None,
        "total_b": num(d.get("total_b")),
        "livre_b": num(d.get("livre_b")),
        "pod": txt(d.get("pod"), 64),
        "versao": txt(d.get("versao"), 32),
        "subida": d.get("subida") if d.get("subida") in ("fria", "retomada") else None,
    }
    pares = (("sentinela", ("pod", "criado", "versao")), ("marcador", ("versao", "em")))
    for chave, campos in pares:
        x = d.get(chave)
        if isinstance(x, dict):
            out[chave] = {
                k: (num(x.get(k)) if k in ("criado", "em") else txt(x.get(k), 64)) for k in campos
            }
    if isinstance(d.get("entradas"), list):
        out["entradas"] = [t for x in d["entradas"][:MAX_ENTRADAS] if (t := txt(x, 60))]
    for etapa in ("inicio", "gravacao"):
        x = d.get(etapa)
        if isinstance(x, dict) and x.get("motivo") in MOTIVOS:
            out[etapa] = {
                "motivo": x["motivo"],
                "detalhe": txt(x.get("detalhe"), 200),
                "em": num(x.get("em")),
                "necessario_b": num(x.get("necessario_b")),
            }
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="volume.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("diagnosticar")
    d.add_argument("--local", required=True)
    d.add_argument("--saida", required=True)
    d.add_argument("--etapa", choices=("inicio", "gravacao"), required=True)
    d.add_argument("--versao", default="")
    d.add_argument("--subida", default="")
    d.add_argument("--motivo", default="")
    d.add_argument("--detalhe", default="")
    d.add_argument("--necessario", type=int)
    s = sub.add_parser("persistente")
    s.add_argument("--local", required=True)
    c = sub.add_parser("cabe")
    c.add_argument("--local", required=True)
    c.add_argument("--bytes", type=int, required=True)
    t = sub.add_parser("sentinela")
    t.add_argument("--local", required=True)
    t.add_argument("--versao", default="")
    a = p.parse_args(argv)
    if a.cmd == "persistente":
        return 0 if montagem(a.local)["persistente"] else 1
    if a.cmd == "cabe":
        total = espaco(a.local)["total_b"]
        return 0 if total is not None and a.bytes * 1.02 <= total else 1  # 2%: metadados e o apt
    if a.cmd == "sentinela":
        escrever_sentinela(a.local, a.versao)
        return 0
    anterior = _ler_json(a.saida) if a.etapa == "gravacao" else None
    diag = diagnosticar(
        a.local, a.etapa, a.versao, a.subida, a.motivo, a.detalhe, a.necessario, anterior
    )
    with open(a.saida, "w", encoding="utf-8") as f:
        json.dump(diag, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
