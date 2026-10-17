"""Versões do pedido e revisão pelo cliente (ADR 0018, 17/10/2026).

Cada pedido novo (`lotes.revisao` preenchido) tem uma cadeia de versões:

- a **base**: o envio (`confirmar_envio`) ou uma edição do texto pelo cliente. Guarda o pedido original (texto, estilo,
  formato, qualidade, duração, variações, modelo), o detalhamento opcional (`briefing`), um resumo das fotos (quantas e
  o SHA-256 da lista de nomes, bytes e textos) e os grupos de texto: o texto do pedido e cada texto próprio distinto,
  com as fotos que o usam e a intenção lida pela API (render2go_worker.intencao);
- por cima, **um passo por acontecimento**: cada tradução que chega do pod (o prompt final, em inglês, de um grupo),
  cada troca da guarda final (o prompt de uma foto trocado na hora de ir para o pod) e cada texto recusado pelo filtro.

O hash de cada versão encadeia o da anterior com o conteúdo dela (`encadear`): aprovar uma versão é aprovar exatamente
aquele conteúdo, e qualquer mudança cria outra versão, que a aprovação da anterior não cobre. Os vídeos só vão para um
pod quando a versão atual do pedido é a liberada (`Banco.reivindicar`). Quem libera:

- modo **"seguir"** (o padrão): a própria versão, a cada passo, menos quando o passo é **material** (uma instrução do
  cliente não chegou ao prompt, uma restrição não foi aplicada ou a guarda final trocou o prompt): aí o pedido espera a
  decisão do cliente (`lotes.decisao_em`), e a falta de resposta não é aprovação (o pedido vence com as fotos, sem
  custo);
- modo **"revisar"**: só o cliente, aprovando a versão completa que ele viu (número e hash, `Banco.aprovar_versao`).

Este módulo é puro (sem banco): o conteúdo, o hash e a reconstrução do estado de uma versão.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

FORMATO = 1  # muda com a estrutura do conteúdo; fica gravado na base
MODOS = ("seguir", "revisar")
ORIGENS = ("envio", "edicao", "traducao", "guarda", "recusa")
BASES = ("envio", "edicao")
GRUPO_PEDIDO = "pedido"  # o grupo do texto do pedido (as fotos sem texto próprio)
EXEMPLOS = 3  # nomes de fotos guardados em cada grupo (o cartão mostra; a lista inteira fica no banco)


def canonico(conteudo: Any) -> str:
    """O JSON de sempre para o mesmo conteúdo (chaves em ordem, sem espaços): a base do hash."""
    return json.dumps(conteudo, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()


def encadear(anterior: str | None, conteudo: dict[str, Any]) -> str:
    """O hash de uma versão: o da anterior (vazio na primeira) e o conteúdo desta."""
    return sha(f"{anterior or ''}\n{canonico(conteudo)}")


def grupos_de(pedido_texto: str, fotos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Os grupos de texto do pedido, na ordem: o do pedido e cada texto próprio distinto. O id de um texto próprio é o
    da 1ª foto (pelo nome) que o usa, o mesmo da tradução dele (`Banco.aprovar`). `fotos`: id, nome, prompt (as que
    o filtro das fotos já recusou entram também: o cartão diz que não viram vídeo)."""
    ordenadas = sorted(fotos, key=lambda f: f["nome"])
    sem_texto = [f for f in ordenadas if not f.get("prompt")]
    grupos = [{"id": GRUPO_PEDIDO, "texto": pedido_texto, "proprio": False, "fotos": len(sem_texto),
               "exemplos": [f["nome"] for f in sem_texto[:EXEMPLOS]]}]  # fmt: skip
    por_texto: dict[str, dict[str, Any]] = {}
    for f in ordenadas:
        if not f.get("prompt"):
            continue
        g = por_texto.get(f["prompt"])
        if g is None:
            g = por_texto[f["prompt"]] = {"id": f["id"], "texto": f["prompt"], "proprio": True, "fotos": 0,
                                          "exemplos": []}  # fmt: skip
            grupos.append(g)
        g["fotos"] += 1
        if len(g["exemplos"]) < EXEMPLOS:
            g["exemplos"].append(f["nome"])
    return grupos


def resumo_fotos(fotos: list[dict[str, Any]]) -> dict[str, Any]:
    """Quantas fotos e o SHA-256 da lista (nome, bytes e texto próprio de cada uma): foto trocada, acrescentada ou
    com outro texto muda o hash da versão."""
    lista = sorted([f["nome"], int(f["bytes"]), f.get("prompt") or ""] for f in fotos)
    return {"n": len(lista), "sha256": sha(canonico(lista))}


def base(
    origem: str,
    pedido: dict[str, Any],
    revisao: str,
    fotos: list[dict[str, Any]],
    grupos: list[dict[str, Any]],
    regras: dict[str, Any],
    anterior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """O conteúdo da base (envio ou edição). `pedido`: o JSON do pedido (com o `briefing`, se houver). `grupos`: os de
    `grupos_de`, cada um já com a `intencao` lida pela API. `regras`: as versões do que decide a adaptação (intenção,
    receitas, presets, prompt do compilador). `anterior`: na edição, o texto que saiu (grupo e texto)."""
    if origem not in BASES or revisao not in MODOS:
        raise ValueError(f"base inválida: {origem}, {revisao}")
    campos = ("prompt", "receita", "formato", "qualidade", "duracao", "variacoes", "modelo")
    out = {
        "tipo": "base",
        "formato": FORMATO,
        "origem": origem,
        "revisao": revisao,
        "pedido": {k: pedido.get(k) for k in campos},
        "briefing": pedido.get("briefing") or None,
        "fotos": resumo_fotos(fotos),
        "grupos": grupos,
        "regras": regras,
    }
    if anterior:
        out["anterior"] = anterior
    return out


def passo_traducao(
    grupo: str, prompt_en: str, compilador: str, detalhe: dict[str, Any], material: str | None
) -> dict[str, Any]:
    """Uma tradução que chegou: o prompt final do grupo, como saiu e, se for o caso, por que é material."""
    return {"tipo": "traducao", "grupo": grupo, "prompt_en": prompt_en, "prompt_sha": sha(prompt_en),
            "compilador": compilador, "detalhe": detalhe, "material": material}  # fmt: skip


def passo_guarda(grupo: str, foto: str, foto_id: str, prompt_en: str, motivo: str) -> dict[str, Any]:
    """A guarda final trocou o prompt de uma foto (sempre material: nenhum vídeo sai de outra versão sem decisão)."""
    return {"tipo": "guarda", "grupo": grupo, "foto": foto, "foto_id": foto_id, "prompt_en": prompt_en,
            "prompt_sha": sha(prompt_en), "motivo": motivo, "material": motivo}  # fmt: skip


def passo_recusa(grupo: str, motivo: str) -> dict[str, Any]:
    """Um texto próprio recusado pelo filtro depois da tradução: as fotos dele não viram vídeo (sem custo)."""
    return {"tipo": "recusa", "grupo": grupo, "motivo": motivo, "material": None}


def estado(linhas: list[dict[str, Any]]) -> dict[str, Any] | None:
    """O estado de uma versão: a última base e os passos depois dela, aplicados em ordem. `linhas`: as versões do
    pedido até a desejada (numero, hash, origem, conteudo em JSON, criado, liberada_em, liberada_por), em ordem."""
    if not linhas:
        return None
    inicio = max((i for i, v in enumerate(linhas) if v["origem"] in BASES), default=None)
    if inicio is None:
        return None
    b = json.loads(linhas[inicio]["conteudo"])
    grupos = {g["id"]: {**g, "prompt_en": None, "prompt_sha": None, "recusado": None} for g in b["grupos"]}
    excecoes: dict[str, dict[str, Any]] = {}
    materiais: list[dict[str, Any]] = []
    for v in linhas[inicio + 1 :]:
        p = json.loads(v["conteudo"])
        g = grupos.get(p.get("grupo"))
        if p["tipo"] == "traducao" and g is not None:
            g.update(prompt_en=p["prompt_en"], prompt_sha=p["prompt_sha"], compilador=p["compilador"],
                     traducao=p["detalhe"])  # fmt: skip
        elif p["tipo"] == "guarda":
            excecoes[p["foto_id"]] = {k: p[k] for k in ("grupo", "foto", "foto_id", "prompt_en", "prompt_sha",
                                                        "motivo")}  # fmt: skip
        elif p["tipo"] == "recusa" and g is not None:
            g["recusado"] = p["motivo"]
        if p.get("material"):
            materiais.append({"numero": v["numero"], "tipo": p["tipo"], "grupo": p.get("grupo"),
                              "foto": p.get("foto"), "motivo": p["material"]})  # fmt: skip
    ultima = linhas[-1]
    return {
        "numero": ultima["numero"],
        "hash": ultima["hash"],
        "criado": ultima["criado"],
        "base": linhas[inicio]["numero"],
        "origem_base": b["origem"],
        "revisao": b["revisao"],
        "pedido": b["pedido"],
        "briefing": b.get("briefing"),
        "fotos": b["fotos"],
        "regras": b["regras"],
        "anterior": b.get("anterior"),
        "grupos": list(grupos.values()),
        "excecoes": list(excecoes.values()),
        "materiais": materiais,
    }


def traduzida(est: dict[str, Any]) -> bool:
    """Todo grupo com fotos tem o prompt final (ou foi recusado): não falta tradução para mostrar."""
    return all(g["prompt_en"] or g["recusado"] or not g["fotos"] for g in est["grupos"])


def prompt_esperado(est: dict[str, Any], foto_id: str, grupo: str) -> str | None:
    """O SHA-256 do prompt com que esta foto deve ir para o pod nesta versão: o da exceção dela (guarda) ou o do grupo
    do texto dela; None se a versão ainda não tem o prompt."""
    exc = next((e for e in est["excecoes"] if e["foto_id"] == foto_id), None)
    if exc is not None:
        return exc["prompt_sha"]
    g = next((x for x in est["grupos"] if x["id"] == grupo), None)
    return g["prompt_sha"] if g else None
