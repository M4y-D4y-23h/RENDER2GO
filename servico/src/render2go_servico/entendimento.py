"""O cartão "Entendemos seu pedido assim" (17/10/2026; ADR 0018).

Tudo aqui é determinístico e sai do mesmo código que decide a tradução no pod (`render2go_worker.traducao.plano` e
`render2go_worker.intencao`): o cliente vê o que pediu, o que entendemos, o que o sistema acrescenta e por quê, o que
precisa continuar igual, os limites do vídeo e, separado, o texto técnico final que foi para o gerador (em inglês, com a
versão). O resumo em português não é uma tradução do prompt: é a explicação do que o código decidiu, escrita à mão para
cada caso (os textos fixos em inglês têm cada um o seu equivalente aqui, conferido por teste).

Cada item diz de onde vem (`origem`): "cliente" (o que você escreveu ou marcou), "estilo" (o estilo escolhido) ou
"sistema" (o que o serviço acrescenta sozinho, sempre com o motivo).
"""

from __future__ import annotations

import functools
from typing import Any

from render2go_worker import compiler, intencao, presets, traducao

from render2go_servico.motivos import MOTIVOS

USOS = {
    "redes": "Post, reels ou stories",
    "anuncio": "Anúncio",
    "site": "Site ou loja virtual",
    "whatsapp": "WhatsApp",
    "outro": "Outro uso",
}
RESTRICOES = {
    "sem_pessoas": "Sem pessoas na cena",
    "camera_parada": "Câmera parada",
    "sem_rotacao": "O produto não gira",
    "sem_aproximacao": "Sem zoom nem aproximação",
    "estatico": "Nada se mexe na cena",
    "assunto_parado": "O assunto fica parado",
    "sem_afastamento": "A câmera não se afasta",
    "sem_luz": "Sem brilho nem luz passando",
    "sem_vapor": "Sem vapor nem fumaça",
}
# o movimento padrão de cada categoria (receitas v4, `movimentos`), em português, como o texto fixo em inglês diz
MOVIMENTO_PT = {
    "geral": "a cena fica no lugar enquanto uma luz suave passa devagar, destacando a forma e os detalhes",
    "comida": "o prato fica no lugar, com brilhos suaves na superfície e um fio de vapor subindo devagar",
    "produto": "o produto fica no lugar enquanto uma luz suave passa devagar, destacando forma, cores e detalhes",
    "imovel": "o lugar fica como está, calmo, com um movimento contínuo e suave",
    "roupa": "a roupa fica no lugar, com o tecido se mexendo de leve, como numa brisa",
    "carro": "o carro fica estacionado enquanto uma luz suave passa devagar pela pintura",
    "pessoa": "a pessoa mantém uma pose calma e natural, com a respiração sutil",
}
ESTATICO_PT = "tudo fica completamente parado: nada se mexe na cena"
NEUTRO_PT = "o assunto fica no lugar, calmo e estável"
# o que já vimos sair errado nos vídeos de produção (docs/06, seção 9.5), por categoria da foto
LIMITES_CATEGORIA = {
    "comida": "Em bebidas e vidros, às vezes aparece líquido sendo despejado que não estava no pedido.",
    "pessoa": "Rostos podem mudar quando as pessoas se mexem; um movimento de câmera muda menos que mexer nelas.",
    "imovel": "Quando a câmera avança ou sobe muito, o gerador pode inventar o que a foto não mostra.",
}
MATERIAL = {
    "substituiu": "A ação que você pediu não chegou ao texto do gerador (o tradutor não conseguiu descrevê-la sem "
    "acrescentar algo que não está na foto); no lugar dela ficou o movimento mostrado abaixo.",
    "restricoes": "Uma restrição do seu pedido não foi aplicada pelo gerador desta vez.",
    "pessoa_inventada": "Na última conferência, o texto desta foto citava uma pessoa que não está nela e foi trocado "
    "por um movimento sem pessoas.",
    "prompt_diferente": "O texto desta foto não confere com a versão liberada.",
}


@functools.lru_cache(maxsize=1)
def dados_presets() -> dict[str, Any]:
    """Os presets em vigor (presets.v4.json), lidos uma vez por processo."""
    return presets.load()


def _fixos_pt(receitas: dict[str, Any]) -> dict[str, str]:
    """Cada texto fixo em inglês que a tradução pode usar → o mesmo em português."""
    out: dict[str, str] = {}
    assuntos = {m["assunto"] for m in receitas["movimentos"].values()}
    assuntos |= {r["assunto"] for r in receitas["receitas"].values() if r.get("assunto")}
    for nome, mov in receitas["movimentos"].items():
        out[compiler.fill_subject(mov["acao_en"], mov["assunto"])] = MOVIMENTO_PT[nome]
    for r in receitas["receitas"].values():
        if r.get("acao_en"):
            out[compiler.fill_subject(r["acao_en"], r["assunto"])] = r["acao"]
    for a in assuntos:
        out[compiler.fill_subject(traducao.ESTATICO_EN, a)] = ESTATICO_PT
        out[compiler.fill_subject(traducao.NEUTRO_EN, a)] = NEUTRO_PT
    return out


def _rotulo(presets_data: dict[str, Any], categoria: str, opcao: str) -> str:
    try:
        return presets_data["categories"][categoria]["options"][opcao]["label"]
    except KeyError:
        return opcao


def texto(
    texto_cliente: str,
    receita_id: str,
    *,
    receitas: dict[str, Any],
    presets_data: dict[str, Any],
    restricoes_extras: tuple[str, ...] = (),
    sem_pessoas: bool | None = None,
    duracao_max: int = intencao.DURACAO_MAX_S,
) -> dict[str, Any]:
    """O que entendemos de um texto (o do pedido ou um texto próprio): a intenção, a frase em português, os ajustes
    (com a origem e o motivo) e as pendências. `sem_pessoas`: a regra da pessoa inventada, quando já se sabe (a análise
    das fotos chega depois do envio); None, ainda não se sabe."""
    pl = traducao.plano(texto_cliente, receita_id, receitas=receitas, sem_pessoas=sem_pessoas,
                        restricoes_extras=restricoes_extras)  # fmt: skip
    it = intencao.analisar(texto_cliente, restricoes=restricoes_extras, duracao_max=duracao_max)
    r = receitas["receitas"][receita_id]
    fixo_pt = _fixos_pt(receitas).get(pl.texto_fixo, "um movimento fixo, escrito por nós, sem o tradutor")
    restr = [RESTRICOES.get(x, x) for x in it.restricoes]
    if it.tipo == "vazio":
        frase = f"Seu texto não descreve um movimento. Vamos usar o movimento padrão: {fixo_pt}."
    elif it.tipo == "restricao":
        frase = (f"Seu texto diz o que não pode acontecer ({', '.join(restr).lower()}). O vídeo usa um movimento que "
                 f"respeita isso: {fixo_pt}.")  # fmt: skip
    elif it.tipo == "acao":
        acoes = ", ".join(f"“{a}”" for a in it.acoes[:4])
        frase = (f"Seu texto pede uma ação ({acoes}). O tradutor descreve o que você escreveu em inglês para o "
                 "gerador, sem trocar por um movimento padrão.")  # fmt: skip
    elif it.tipo == "ambiguo":
        frase = ("Não reconhecemos a ação com segurança; você confirmou que o texto segue como está, e ele vai para o "
                 "tradutor assim.")  # fmt: skip
    else:
        frase = "Este texto precisa de uma decisão sua antes de gerar (veja as pendências)."
    ajustes: list[dict[str, str]] = []
    if pl.modo == "padrao":
        ajustes.append({"o_que": f"Movimento padrão: {fixo_pt}", "por_que": "o texto não descreve um movimento",
                        "origem": "sistema"})  # fmt: skip
    elif pl.modo == "restrito":
        ajustes.append({"o_que": f"Movimento: {fixo_pt}", "por_que": "o texto só diz o que não pode acontecer",
                        "origem": "sistema"})  # fmt: skip
    cam = pl.presets.get("camera")
    if cam and pl.camera_sugerida:
        ajustes.append({"o_que": f"Câmera: {_rotulo(presets_data, 'camera', cam)}",
                        "por_que": "o texto não fala da câmera; com a câmera parada, o gerador costuma mexer o que "
                        "devia ficar parado", "origem": "sistema"})  # fmt: skip
    elif cam == traducao.CAMERA_PARADA and {"camera_parada", "estatico"} & set(it.restricoes):
        ajustes.append({"o_que": "Câmera parada", "por_que": "você pediu", "origem": "cliente"})
    elif cam == traducao.CAMERA_PARADA and r["presets"].get("camera") != cam:
        ajustes.append({"o_que": "Câmera parada no lugar da câmera do estilo",
                        "por_que": "a câmera do estilo faria o que você pediu para não acontecer",
                        "origem": "sistema"})  # fmt: skip
    elif cam and pl.modo != "traduzir":
        ajustes.append({"o_que": f"Câmera: {_rotulo(presets_data, 'camera', cam)}",
                        "por_que": "a do movimento padrão desta categoria", "origem": "estilo"})  # fmt: skip
    for cat in ("luz", "estilo", "ritmo"):
        if pl.presets.get(cat):
            nome = presets_data["categories"][cat]["label"]
            ajustes.append({"o_que": f"{nome}: {_rotulo(presets_data, cat, pl.presets[cat])}",
                            "por_que": f"do estilo “{r['label']}”", "origem": "estilo"})  # fmt: skip
    if sem_pessoas and pl.sem_pessoas:
        por = "você pediu" if "sem_pessoas" in it.restricoes else "a foto não tem pessoas"
        ajustes.append({"o_que": "Nenhuma pessoa que não está na foto", "por_que": por,
                        "origem": "cliente" if "sem_pessoas" in it.restricoes else "sistema"})  # fmt: skip
    return {
        "tipo": it.tipo,
        "frase": frase,
        "ajustes": ajustes,
        "continuidade": _rotulo(presets_data, "continuidade", pl.presets["continuidade"])
        if pl.presets.get("continuidade")
        else None,
        "restricoes": [{"codigo": x, "texto": RESTRICOES.get(x, x)} for x in it.restricoes],
        "pendencias": [
            {"codigo": p.codigo, "mensagem": p.mensagem, "trecho": p.trecho, "confirmavel": p.confirmavel}
            for p in it.pendencias
        ],  # fmt: skip
    }


def limites(categoria: str, duracao: int | None) -> list[str]:
    """O que o vídeo não faz ou pode errar (sem promessa de resultado perfeito)."""
    out = [
        "O vídeo sai sem som.",
        f"Uma cena contínua{f' de {duracao} s' if duracao else ''} a partir da foto: não troca de cena nem de produto.",
        "Textos, preços e logotipos não são escritos no vídeo; os que já estão na foto podem se deformar no movimento.",
    ]
    if categoria in LIMITES_CATEGORIA:
        out.append(LIMITES_CATEGORIA[categoria])
    out.append("Cada vídeo passa pela conferência automática (cor e 1º quadro); ela não pega todos os defeitos.")
    return out


def preservar(pedido: dict[str, Any], continuidade: str | None) -> list[dict[str, str]]:
    """O que precisa continuar igual: o que o estilo garante (continuidade) e o que o cliente escreveu ou marcou. O
    texto livre do cliente não vai para o gerador: vale na conferência do resultado."""
    out = []
    if continuidade:
        out.append({"texto": continuidade, "origem": "estilo", "como": "vai no texto do gerador"})
    b = pedido.get("briefing") or {}
    for x in b.get("evitar") or ():
        out.append({"texto": RESTRICOES.get(x, x), "origem": "cliente", "como": "vale na geração e na conferência"})
    if b.get("manter"):
        out.append({"texto": b["manter"], "origem": "cliente", "como": "vale na conferência do resultado"})
    if b.get("outros"):
        out.append({"texto": f"Não pode mudar: {b['outros']}", "origem": "cliente",
                    "como": "vale na conferência do resultado"})  # fmt: skip
    return out


def cartao(
    est: dict[str, Any],
    *,
    receitas: dict[str, Any],
    presets_data: dict[str, Any],
    videos: int | None = None,
    preco_video: int | None = None,
    duracao_max: int = intencao.DURACAO_MAX_S,
) -> dict[str, Any]:
    """O cartão de um pedido com versões (`est`: render2go_servico.versoes.estado com a liberação, de
    `Banco.estados_versao`). `videos`: os vídeos do pedido (depois da revisão de conteúdo, os jobs; antes, fotos ×
    variações)."""
    pedido = est["pedido"]
    receita = pedido.get("receita") or "livre"
    extras = tuple((est.get("briefing") or {}).get("evitar") or ())
    categoria = traducao.categoria_da_receita(receitas, receita)
    grupos = []
    continuidade = None
    for g in est["grupos"]:
        if not g["fotos"] and not g["proprio"]:
            continue  # o texto do pedido sem nenhuma foto (todas com texto próprio): nada a mostrar
        info = (g.get("traducao") or {}) if g.get("prompt_en") else {}
        e = texto(g["texto"], receita, receitas=receitas, presets_data=presets_data, restricoes_extras=extras,
                  sem_pessoas=info.get("sem_pessoas"), duracao_max=duracao_max)  # fmt: skip
        cont = e.pop("continuidade")
        continuidade = continuidade or cont
        grupos.append({
            "id": g["id"],
            "texto": g["texto"],
            "proprio": g["proprio"],
            "fotos": g["fotos"],
            "exemplos": g["exemplos"],
            **e,
            "estado": "recusado" if g["recusado"] else "pronto" if g["prompt_en"] else "preparando",
            # nunca a palavra nem a categoria que barrou o texto: só que ele não pôde ser usado
            "recusado": MOTIVOS["texto"] if g["recusado"] else None,
            # o texto técnico final, em inglês, que vai para o gerador (só depois da tradução)
            "prompt_en": g["prompt_en"],
            "compilador": g.get("compilador"),
        })  # fmt: skip
    excecoes = [{"foto": x["foto"], "prompt_en": x["prompt_en"], "motivo": MATERIAL.get(x["motivo"], x["motivo"])}
                for x in est["excecoes"]]  # fmt: skip
    # o que ainda pede a decisão do cliente: os passos materiais desde a última liberação
    lib = est.get("numero_liberado")
    pendentes = [m for m in est["materiais"] if lib is None or m["numero"] > lib]
    decisoes = [{"motivo": MATERIAL.get(m["motivo"], m["motivo"]), "foto": m.get("foto"), "grupo": m.get("grupo")}
                for m in pendentes] if not est["liberada"] else []  # fmt: skip
    variacoes = int(pedido.get("variacoes") or 1)
    videos = int(est["fotos"]["n"]) * variacoes if videos is None else videos
    b = est.get("briefing") or {}
    traduzido = all(g["estado"] != "preparando" for g in grupos)
    return {
        "versao": {
            "numero": est["numero"],
            "hash": est["hash"],
            "curto": est["hash"][:12],
            "criada": est["criado"],
            "liberada": est["liberada"],
            "liberada_por": est.get("liberada_por") if est["liberada"] else None,
            "numero_liberado": est.get("numero_liberado"),
            "editada": est.get("origem_base") == "edicao",
            "anterior": est.get("anterior"),
        },
        "revisao": est["revisao"],
        "original": {"texto": pedido.get("prompt"), "uso": USOS.get(b.get("uso") or "", None)},
        "grupos": grupos,
        "excecoes": excecoes,
        "preservar": preservar({"briefing": b}, continuidade),
        "limites": limites(categoria, pedido.get("duracao")),
        "especificacoes": {
            "estilo": receitas["receitas"].get(receita, {}).get("label", receita),
            "formato": pedido.get("formato"),
            "qualidade": pedido.get("qualidade"),
            "duracao": pedido.get("duracao"),
            "variacoes": variacoes,
            "videos": videos,
            "preco_video": preco_video,
            "estimativa": videos * preco_video if preco_video else None,
        },
        "traduzido": traduzido,
        "decisoes": decisoes,
        # o que o cliente pode fazer agora: aprovar (no modo revisar, com tudo traduzido; no seguir, uma decisão)
        "aprovar": not est["liberada"] and (
            (est["revisao"] == "revisar" and traduzido and not est.get("compilando"))
            or (est["revisao"] == "seguir" and bool(decisoes))
        ),
        "regras": est.get("regras"),
    }  # fmt: skip
