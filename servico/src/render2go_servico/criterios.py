"""Critérios de entrega por pedido, retorno do cliente e pedidos de correção (17/10/2026; ADR 0018).

Os critérios saem da versão do pedido (render2go_servico.versoes): o que o cliente pediu e marcou e o que o serviço
promete. Cada um diz se é **obrigatório** ou **preferência**, de onde vem (`origem`: "cliente", "estilo" ou "sistema")
e de qual texto (`fonte`). Nunca é "atendido" só porque está no prompt: o estado vem de uma avaliação com evidência —
de uma pessoa (a equipe na revisão do piloto, `modo` "decisao") ou de um avaliador automático novo, que começa só em
observação (`modo` "observacao"): fica registrado ao lado, sem nota de confiança e sem mudar a situação do vídeo.

A situação de um vídeo olha só os obrigatórios: um "não atendido" decide (a média das outras não compensa); sem
avaliação ou "não verificável", o vídeo fica "não verificado" (nunca aprovado por falta de resposta). As preferências
aparecem à parte.

Este módulo é puro (sem banco).
"""

from __future__ import annotations

from typing import Any

from render2go_servico import entendimento

ESTADOS = ("atendido", "parcial", "nao_atendido", "nao_verificavel")
ROTULO_ESTADO = {
    "atendido": "Atendido",
    "parcial": "Atendido em parte",
    "nao_atendido": "Não atendido",
    "nao_verificavel": "Não verificável",
}
MODOS = ("decisao", "observacao")
SITUACOES = {
    "atende": "Atende ao pedido",
    "atende_em_parte": "Atende em parte",
    "nao_atende": "Não atende ao pedido",
    "nao_verificado": "Ainda não verificado",
}
# "O que precisa mudar?" (o retorno do cliente sobre um vídeo): o motivo, do jeito que o cliente diz
MOTIVOS_RETORNO = {
    "pedido_nao_atendido": "O que eu pedi não aconteceu",
    "produto_alterado": "O produto mudou",
    "texto_incorreto": "Um texto, preço ou logotipo saiu errado",
    "movimento_inadequado": "O movimento não ficou bom",
    "defeito_visual": "Defeito visual (borrão, deformação, quadro estranho)",
    "problema_arquivo": "Problema no arquivo (não abre, corta, sem qualidade)",
}
# a classificação da equipe para um pedido de correção: só as duas primeiras podem gerar crédito
CLASSIFICACOES = {
    "falha_tecnica": "Falha técnica: o vídeo saiu com defeito",
    "fora_do_combinado": "Fora do combinado: um critério obrigatório da versão aprovada não foi atendido",
    "pedido_novo": "Pedido novo: o que o cliente quer agora não estava no pedido",
}
COM_CREDITO = ("falha_tecnica", "fora_do_combinado")


def _criterio(id_: str, texto: str, tipo: str, origem: str, fonte: str, como: str, grupo: str | None = None) -> dict:
    return {"id": id_, "texto": texto, "tipo": tipo, "origem": origem, "fonte": fonte, "como": como, "grupo": grupo}


def do_pedido(est: dict[str, Any], receitas: dict[str, Any]) -> list[dict[str, Any]]:
    """Os critérios de um pedido com versões, a partir do estado da versão (o que o cliente viu e aprovou). O id é
    estável entre versões com o mesmo texto: `pedido:<grupo>`, `restricao:<código>`, `manter`, `outros`, `produto`,
    `pessoas` e `estilo`. `grupo`: o grupo de texto (None: vale para todas as fotos)."""
    out: list[dict[str, Any]] = []
    b = est.get("briefing") or {}
    restricoes: dict[str, str] = {}
    for g in est["grupos"]:
        if not g["fotos"] and not g["proprio"]:
            continue
        it = g.get("intencao") or {}
        tipo = it.get("tipo")
        grupo = g["id"]
        if tipo in ("acao", "ambiguo"):  # o que o cliente pediu: a ação do texto (o ambíguo, confirmado por ele)
            out.append(_criterio(f"pedido:{grupo}", f"Mostra o que você pediu: “{g['texto']}”", "obrigatorio",
                                 "cliente", "texto", "revisão do vídeo inteiro", grupo))  # fmt: skip
        elif tipo == "vazio":  # sem instrução: o movimento suave do serviço (sugestão, não um pedido)
            out.append(_criterio(f"pedido:{grupo}", "Um movimento suave, escolhido pelo serviço para a foto",
                                 "preferencia", "sistema", "movimento padrão", "revisão do vídeo inteiro",
                                 grupo))  # fmt: skip
        for r in it.get("restricoes") or ():
            restricoes.setdefault(r, "detalhamento" if r in (b.get("evitar") or ()) else "texto")
    for r in b.get("evitar") or ():
        restricoes.setdefault(r, "detalhamento")
    for r, fonte in restricoes.items():
        out.append(_criterio(f"restricao:{r}", f"Não pode acontecer: {entendimento.RESTRICOES.get(r, r).lower()}",
                             "obrigatorio", "cliente", fonte, "revisão do vídeo inteiro"))  # fmt: skip
    if b.get("manter"):
        out.append(_criterio("manter", f"Continua igual: {b['manter']}", "obrigatorio", "cliente", "detalhamento",
                             "revisão do vídeo inteiro"))  # fmt: skip
    if b.get("outros"):
        out.append(_criterio("outros", f"Não pode mudar: {b['outros']}", "obrigatorio", "cliente", "detalhamento",
                             "revisão do vídeo inteiro"))  # fmt: skip
    # o que o serviço promete em todo vídeo (os limites do cartão): a mesma cena, o mesmo produto, ninguém inventado
    out.append(_criterio("produto", "O produto e a cena da foto continuam os mesmos do início ao fim", "obrigatorio",
                         "sistema", "limites do vídeo", "revisão do vídeo inteiro; a conferência de cor é só "
                         "observação"))  # fmt: skip
    out.append(_criterio("pessoas", "Ninguém que não está na foto aparece no vídeo", "obrigatorio", "sistema",
                         "regra da pessoa inventada", "revisão do vídeo inteiro"))  # fmt: skip
    receita = receitas["receitas"].get((est.get("pedido") or {}).get("receita") or "")
    if receita:
        out.append(_criterio("estilo", f"O jeito do estilo “{receita['label']}” (luz, ritmo e câmera)", "preferencia",
                             "estilo", "estilo escolhido", "revisão do vídeo inteiro"))  # fmt: skip
    return out


def do_video(criterios: list[dict[str, Any]], grupo: str | None) -> list[dict[str, Any]]:
    """Os critérios que valem para um vídeo: os do pedido todo e os do grupo de texto da foto dele."""
    return [c for c in criterios if c["grupo"] is None or c["grupo"] == grupo]


def situacao(criterios: list[dict[str, Any]], decisoes: dict[str, str]) -> dict[str, Any]:
    """A situação de um vídeo pelas decisões (critério → estado; só as de pessoas, nunca as observações).
    Obrigatórios: um "não atendido" decide; "parcial" vem depois; sem decisão ou "não verificável", não verificado.
    Preferências: só a contagem, à parte (não compensam um obrigatório)."""
    obrig = [decisoes.get(c["id"]) for c in criterios if c["tipo"] == "obrigatorio"]
    if any(e == "nao_atendido" for e in obrig):
        s = "nao_atende"
    elif any(e == "parcial" for e in obrig):
        s = "atende_em_parte"
    elif any(e in (None, "nao_verificavel") for e in obrig):
        s = "nao_verificado"
    else:
        s = "atende"
    pref = [decisoes.get(c["id"]) for c in criterios if c["tipo"] == "preferencia"]
    return {
        "situacao": s,
        "texto": SITUACOES[s],
        "obrigatorios": {
            "total": len(obrig),
            "atendidos": sum(1 for e in obrig if e == "atendido"),
            "sem_decisao": sum(1 for e in obrig if e is None),
        },
        "preferencias": {"total": len(pref), "atendidas": sum(1 for e in pref if e == "atendido")},
    }


def observacoes_tecnicas(job: dict[str, Any]) -> list[dict[str, Any]]:
    """O que a conferência automática que já existe mediu no vídeo, como observação (sem decidir nada): a cor do
    centro do vídeo (deriva e salto) para o critério "produto". Sem a medida, nada."""
    out = []
    if job.get("deriva_cor") is not None:
        out.append({"criterio": "produto", "avaliador": "auto:conferencia", "modo": "observacao",
                    "evidencia": f"cor do centro: deriva {job['deriva_cor']:.1f}, salto {job.get('salto_cor') or 0:.1f}"
                    " (dentro dos limites da conferência)"})  # fmt: skip
    return out
