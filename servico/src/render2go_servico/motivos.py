"""Por que um vídeo não saiu ou um texto foi recusado (pedido do dono, 12/10/2026: "quero sim que haja uma
justificativa de recusa, tanto na recusa dos prompts, como na falha e recusa das gerações").

O cliente vê a categoria do motivo, nunca a palavra que barrou o texto (ajudaria a testar sinônimos); o painel vê,
além disso, o texto técnico gravado no vídeo (`jobs.erro`) e as notas dos filtros."""

from __future__ import annotations

# categorias do filtro de palavras (worker/filtros/c1.v2.json) como o cliente lê
CATEGORIAS_C1 = {
    "sexual": "conteúdo sexual ou adulto",
    "menores": "crianças ou adolescentes",
    "violencia": "violência",
    "armas": "armas",
    "drogas": "drogas",
    "autolesao": "autolesão",
    "pessoa_real": "pessoas reais ou famosas",
}

# por que o vídeo não saiu: o código e o texto do cliente (nada disso é cobrado; ADR 0012)
MOTIVOS = {
    "filtro_imagem": "barrado pelo filtro de imagem: o vídeo gerado mostrava conteúdo impróprio",
    "conferencia": "não ficou fiel à foto (o produto mudou ou o vídeo não começou na foto), nem refeito",
    "texto": "o texto foi recusado pelo filtro de conteúdo",
    "traducao": "a tradução do texto falhou",
    "foto": "a foto não pôde ser usada (arquivo, formato ou tamanho)",
    "interrompido_cliente": "cancelado: você interrompeu o pedido",
    "interrompido_painel": "cancelado: o pedido foi interrompido pela equipe",
    "prazo_fotos": "cancelado: as fotos ficam guardadas por 7 dias e o pedido não terminou nesse prazo",
    "tecnico": "erro técnico na geração",
}


# o que o pod grava quando a foto é o problema (render2go_worker.media e preparo)
ERROS_DA_FOTO = (
    "download recusado",
    "imagem inválida",
    "formato de imagem",
    "imagem grande",
    "foto grande",
    "imagem ilegível",
    "arquivo acima de",
)


def categoria_c1(reason: str | None) -> str | None:
    """ "C1: termo bloqueado (sexual)" → "conteúdo sexual ou adulto" (None se não for uma recusa do C1)."""
    if not reason or not reason.startswith("C1: termo bloqueado ("):
        return None
    cat = reason.removeprefix("C1: termo bloqueado (").rstrip(")")
    return CATEGORIAS_C1.get(cat, "conteúdo que não aceitamos")


def recusa_texto(reason: str | None) -> str:
    """A justificativa que o cliente vê quando o texto é recusado pelo filtro de palavras."""
    cat = categoria_c1(reason) or "conteúdo que não aceitamos"
    return (
        f"o texto foi recusado pelo filtro de conteúdo: ele pede {cat} (item 5 dos termos). Reescreva descrevendo "
        "só o produto, o ambiente e o movimento da câmera."
    )


def recusa_traducao(reason: str | None) -> str:
    """O motivo do pedido recusado porque o texto, depois de traduzido, caiu no filtro de palavras."""
    cat = categoria_c1(reason) or "conteúdo que não aceitamos"
    return f"o texto, traduzido, foi recusado pelo filtro de conteúdo: ele pede {cat} (item 5 dos termos)"


def motivo(job: dict) -> str | None:
    """O código do motivo de um vídeo que não saiu (erro ou cancelado); None para os outros."""
    if job.get("status") not in ("erro", "cancelado"):
        return None
    erro = (job.get("erro") or "").lower()
    if job.get("barrado_em") or erro.startswith("barrado pelo filtro de imagem"):
        return "filtro_imagem"
    if erro.startswith("interrompido pelo cliente"):
        return "interrompido_cliente"
    if erro.startswith("interrompido"):
        return "interrompido_painel"
    if erro.startswith("fotos apagadas"):
        return "prazo_fotos"
    if "compilação" in erro:
        return "traducao"
    if erro.startswith("c1:"):
        return "texto"
    if any(t in erro for t in ("conferência", "o produto mudou", "corte brusco")):
        return "conferencia"
    if erro.startswith("filtro de imagem"):  # o classificador falhou ou não respondeu: técnico, não a foto
        return "tecnico"
    if any(t in erro for t in ERROS_DA_FOTO):
        return "foto"
    return "tecnico"


def resumo(jobs: list[dict]) -> list[dict]:
    """Os vídeos que não saíram, agrupados por motivo: [{"motivo", "texto", "videos"}], o mais comum primeiro."""
    contagem: dict[str, int] = {}
    for j in jobs:
        m = motivo(j)
        if m:
            contagem[m] = contagem.get(m, 0) + 1
    return [
        {"motivo": m, "texto": MOTIVOS[m], "videos": n}
        for m, n in sorted(contagem.items(), key=lambda x: (-x[1], list(MOTIVOS).index(x[0])))
    ]
