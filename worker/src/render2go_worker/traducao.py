"""Tradução do pedido do cliente para o prompt do Wan, com as regras do serviço (13/10/2026).

O compilador (`compiler.py`) é o LLM; este módulo junta as regras em volta dele, as mesmas no worker
da fila, no lote, no teste de fidelidade e, nas conferências, na API:

- **estilo Livre com câmera suave** (receitas v4, `camera_padrao`): a linha da câmera entra só
  quando o texto do cliente não fala da câmera. Com a câmera parada, o modelo mexia o que devia
  ficar parado (teste do dono, 12/10/2026, `docs/06`, seção 9.1);
- **pedido vago** (nenhuma palavra de movimento, mudança ou câmera no texto, `pedido_vago`; ou o
  VAGUE do compilador, a partir do v7): vira o movimento padrão, em inglês e fixo, sem o texto do
  LLM: o da categoria da foto no Livre (`movimentos`), o do próprio estilo nos outros (`acao_en`);
- **pessoa inventada**: com a foto sem pessoas (ou sem a análise: na dúvida, sem pessoas) e o texto
  do cliente sem ninguém, o prompt que cita uma pessoa é traduzido de novo, com outra seed;
  esgotadas as tentativas, vale o movimento padrão, sem pessoa. A API confere de novo
  (`prompt_cita_pessoa`) e troca pelo padrão o que ainda citar alguém: nenhum vídeo sai com pessoa
  inventada no prompt.

O compilador continua sem ser camada de segurança: o C1 e a regra REFUSED ficam como estão. As
listas de palavras abaixo só decidem se o prompt é traduzido de novo; errar para o lado de "cita
pessoa" no prompt custa uma tradução a mais ou o movimento padrão, nunca um vídeo com alguém que não
estava na foto.
"""

from __future__ import annotations

import dataclasses
import re
import time
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from render2go_worker import compiler, presets

TENTATIVAS = 3  # traduções por pedido antes do movimento padrão (cada uma leva 1–3 s na GPU)
CATEGORIA_PADRAO = "geral"

# Pessoas no prompt em inglês (palavra inteira; "hand-painted" não conta). Nenhum termo dos presets
# cita uma destas palavras (tests/test_traducao.py): o prompt pode ser conferido inteiro, com o
# sufixo. Ficam de fora palavras com outro sentido comum em foto de produto: body (carroceria), face
# (mostrador), skin (casca), arm (braço da cadeira), cook (o prato cozinha), guest (quarto de
# hóspedes).
_PESSOAS_EN = (
    "man men woman women person persons people human humans someone somebody anyone anybody"
    " everyone everybody he him his himself she her hers herself girl girls boy boys lady ladies"
    " gentleman gentlemen guy guys child children kid kids baby babies toddler toddlers teen teens"
    " teenager teenagers adult adults model models customer customers client clients shopper"
    " shoppers buyer buyers chef chefs waiter waiters waitress waitresses bartender bartenders"
    " barista baristas baker bakers worker workers employee employees owner owners seller sellers"
    " salesperson salesman saleswoman vendor vendors cashier cashiers couple couples family"
    " families bride groom mother father mom dad husband wife friend friends tourist tourists"
    " visitor visitors driver drivers rider riders passenger passengers pilot pilots cyclist"
    " cyclists athlete athletes dancer dancers singer singers actor actors actress actresses"
    " presenter presenters influencer influencers crowd audience pedestrian pedestrians hand"
    " hands finger fingers fingertip fingertips lips smile smiles smiling smiled"
)
# o possessivo descreve o produto ("women's handbag", "men's watch"), não põe alguém na cena
PESSOAS_EN = re.compile(
    r"(?<![\w-])(?:" + "|".join(_PESSOAS_EN.split()) + r")(?![\w-])(?!['’]s\b)", re.IGNORECASE
)

# Pessoas no texto do cliente (sem acentos, minúsculas). Errar para o lado de "cita pessoa" desliga
# a conferência da pessoa inventada, então a lista é curta: só substantivos e gestos que pedem
# alguém na cena. Ficam de fora "ele" e "ela" (também valem para objetos: "a bolsa... ela gira"), "o
# modelo" (do carro), "gente" ("a gente quer"), "cliente", "dono", "família", "amigo" e "turista"
# (quase sempre o público ou quem pede: "para atrair clientes", "sou dona da loja"), "jovem" (moda
# jovem), "braço" (do sofá), "pais" (sem acento, também é "país"), "bebe" e "moca" (bebe água, café
# moca: só com o acento).
_PESSOAS_PT = (
    r"pessoas?|alguem|homem|homens|mulher|mulheres|mocos?|rapaz|rapazes|garotas?|garotos?"
    r"|meninas?|meninos?|criancas?|adolescentes?|senhoras?|senhor|senhores|idosas?|idosos?"
    r"|atendentes?|garcom|garcons|garconetes?|cozinheiras?|cozinheiros?|chefs?|confeiteiras?"
    r"|confeiteiros?|padeiras?|padeiros?|baristas?|barbeiros?|cabeleireiras?|cabeleireiros?"
    r"|manicures?|maquiadora?s?|maquiadores|esteticistas?|funcionarias?|funcionarios?|noivos?"
    r"|maes?|pai|filhas?|filhos?|marido|esposa|namoradas?|namorados?|motoristas?|pilotos?"
    r"|ciclistas?|atletas?|jogadora?s?|jogadores|dancarinas?|dancarinos?|bailarinas?|bailarinos?"
    r"|cantora?s?|cantores|ator|atores|atriz|atrizes|apresentadora?s?|apresentadores"
    r"|influenciadora?s?|influenciadores|entregadora?s?|entregadores|motoboys?|dentistas?"
    r"|enfermeiras?|enfermeiros?|professora?s?|professores|alunas?|alunos?|instrutora?s?"
    r"|instrutores|personal|maos?|dedos?|rostos?|sorri|sorriem|sorrindo|sorrisos?|acena|acenam"
    r"|acenando|(?:a|as|uma|umas) modelos?"
)
PESSOAS_PT = re.compile(r"\b(?:" + _PESSOAS_PT + r")\b")
# com o acento, sem dúvida: bebê (bebe = bebe água) e moça (moca = café moca)
PESSOAS_PT_ACENTO = re.compile(r"\b(?:bebês?|moças?)\b", re.IGNORECASE)
PUBLICO_ACENTO = re.compile(
    r"\b(?:de|da|do|das|dos|para|pra|pro|por)\s+(?:\w+\s+)?(?:bebês?|moças?)\b"
)
# o público ou o tipo do produto, não alguém na cena: "bolsa de mulher", "roupa de criança", "para
# os clientes", "porção por pessoa"; e "feito à mão" (pintado, bordado, costurado...): tirados antes
_NAO_E_CENA = re.compile(
    r"\b(?:de|da|do|das|dos|para|pra|pro|pras|pros|por)\s+"
    r"(?:(?:a|o|as|os|um|uma|uns|umas|sua|seu|suas|seus|minha|meu|minhas|meus|nossa|nosso|nossas"
    r"|nossos|toda|todo|todas|todos)\s+)*(?:" + _PESSOAS_PT + r")\b"
    r"|\b\w+(?:ad|id|it)[oa]s?\s+a\s+maos?\b"
)


# Pedido vago: nenhuma palavra de movimento, mudança ou câmera no texto do cliente. O 7B abliterado
# não separa sozinho o pedido vago (sonda de 13/10/2026: 14 de 28 certos; num "algo que chame a
# atenção dos clientes", inventou um cachorro), então a decisão é deste vocabulário, fixa e testada;
# o VAGUE do compilador (v7) só soma. Verbo fora da lista: o pedido vira o movimento padrão (perde o
# movimento pedido, nunca inventa nada); a lista cresce com os pedidos reais. Ficam de fora, de
# propósito, os desejos de movimento sem dizer o quê ("movimento", "animado", "dinâmico") e o
# subjuntivo ("brilhe").
def _formas(raizes: str, fins: tuple[str, ...]) -> list[str]:
    return [r + f for r in raizes.split() for f in fins]


# verbos em -ar: 3ª pessoa do presente, plural, gerúndio e infinitivo
_RAIZES_AR = (
    "gir rod balanc moviment ping borbulh solt brilh pisc apag fech aproxim afast avanc recu pass"
    " caminh and danc vo pul salt sopr vent ondul tremul nev flutu desliz mud transform esvazi"
    " cort mistur levant abaix acen olh vir inclin orbit gotej respir aban entr pux empurr jog"
    " lanc quebr espirr jorr derram espalh desabroch aceler estacion decol pous rol quic vibr"
    " ilumin peg segur toc apert abrac mastig prov despej tamp destamp fati reg molh sec pint"
    " desenh coloc cozinh ass frit grelh decor mont dobr desdobr estic encolh infl estour escorreg"
)
_RAIZES_ER = (
    "mex mov desc escorr derret ferv acend ench corr aparec cresc floresc escurec amanhec anoitec"
    " nasc com beb"
)
_RAIZES_IR = "abr surg serv"
_SOLTAS = (  # verbos irregulares, o que se move na cena e a câmera
    "sobe sobem subindo subir cai caem caindo cair sai saem saindo sair dorme dormem dormindo"
    " dormir sorri sorriem sorrindo sorrir poe poem pondo flui fluem fluindo fluir chove chovendo"
    " late latem latindo reflete refletem refletindo refletir explode explodem explodindo escoa"
    " escoam escoando treme tremem tremer sacode sacodem sacudindo sacudir foge fogem fugindo freia"
    " freiam freando frear clareia clareiam clareando clarear fumaca vapor bolha bolhas gota gotas"
    " chuva neve vento brisa onda ondas faisca faiscas chamas fogo reflexo reflexos brilho nevoa"
    " neblina zoom camera close closeup panoramica travelling drone orbita timelapse"
)
# infinitivos que aparecem mais em desejos ("quero passar confiança", "mudar o visual") que na cena
_SO_DESEJO = {"passar", "mudar", "virar", "transformar", "levantar", "provar", "tocar"}
_MOVIMENTO_PT = sorted(
    {
        *_formas(_RAIZES_AR, ("a", "am", "ando", "ar")),
        *_formas(_RAIZES_ER, ("e", "em", "endo", "er")),
        *_formas(_RAIZES_IR, ("e", "em", "indo", "ir")),
        *_SOLTAS.split(),
    }
    - _SO_DESEJO
)
MOVIMENTO_PT = re.compile(r"\b(?:" + "|".join(_MOVIMENTO_PT) + r"|em volta|ao redor)\b")

# O texto do cliente fala da câmera? Então o Livre não ganha a câmera suave (ela contradiria o
# pedido). Errar para o lado de "fala" só tira a câmera padrão, como era até a v3.
CAMERA_PT = re.compile(
    r"\b(?:camera|zoom|close|closeup|plano|enquadra\w*|panoramic\w*|travelling|drone|aere\w*"
    r"|filma\w*|foco|desfoc\w*|aproxim\w*|afast\w*|recu[ae]\w*|avanc\w*|em volta|ao redor"
    r"|de cima|de baixo|de lado|de perfil|de frente|de costas|orbita\w*|gira em volta)\b"
)


class TraducaoError(Exception):
    pass


def _sem_acento(texto: str) -> str:
    nfd = unicodedata.normalize("NFD", texto.lower())
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")


def prompt_cita_pessoa(prompt: str) -> list[str]:
    """As palavras de pessoa no prompt em inglês (vazio: nenhuma)."""
    return sorted({m.group(0).lower() for m in PESSOAS_EN.finditer(prompt or "")})


def texto_cita_pessoa(texto: str) -> bool:
    """O texto do cliente pede alguém na cena (substantivo ou gesto que só uma pessoa faz)? O
    público do produto ("bolsa de mulher", "para os clientes") e "feito à mão" não contam."""
    if PESSOAS_PT.search(_NAO_E_CENA.sub(" ", _sem_acento(texto or ""))):
        return True
    return bool(PESSOAS_PT_ACENTO.search(PUBLICO_ACENTO.sub(" ", (texto or "").lower())))


def texto_fala_da_camera(texto: str) -> bool:
    return bool(CAMERA_PT.search(_sem_acento(texto or "")))


def pedido_vago(texto: str) -> bool:
    """Só um desejo ("deixa bonito", "vídeo profissional pra vender"), sem nada que se mova, mude ou
    acenda, e sem movimento de câmera: vale o movimento padrão da foto."""
    return not MOVIMENTO_PT.search(_sem_acento(texto or ""))


def tem_pessoas(analise: Any) -> bool:
    """A análise do servidor (render2go_servico.pessoas) diz que a foto tem pessoas ou rostos? Sem
    análise, com erro ou em outro formato: não (na dúvida, a foto vale como sem pessoas)."""
    if not isinstance(analise, dict) or analise.get("erro"):
        return False
    try:
        return int(analise.get("pessoas") or 0) > 0 or int(analise.get("rostos") or 0) > 0
    except (TypeError, ValueError):
        return False


def regra_sem_pessoas(texto: str, analises: Iterable[Any]) -> bool:
    """A regra da pessoa inventada vale para esta tradução? Sim, se o texto do cliente não cita
    ninguém e alguma das fotos que vão usar o prompt não tem pessoas (ou não tem a análise). Sem
    foto: sim."""
    if texto_cita_pessoa(texto):
        return False
    analises = list(analises)
    return not analises or not all(tem_pessoas(a) for a in analises)


def _receita(receitas: dict[str, Any], receita_id: str) -> dict[str, Any]:
    r = receitas["receitas"].get(receita_id)
    if r is None:
        raise TraducaoError(f"receita desconhecida {receita_id!r}")
    return r


def categoria_da_receita(receitas: dict[str, Any], receita_id: str) -> str:
    """A categoria de foto que o estilo supõe (receitas v4); o Livre não supõe nada ("geral")."""
    return _receita(receitas, receita_id).get("categoria") or CATEGORIA_PADRAO


def _movimento(receitas: dict[str, Any], categoria: str, sem_pessoas: bool) -> dict[str, Any]:
    movs = receitas.get("movimentos") or {}
    mov = movs.get(categoria) or movs.get(CATEGORIA_PADRAO)
    if mov is None:
        raise TraducaoError("receitas sem o movimento padrão (use as receitas v4)")
    if sem_pessoas and mov.get("com_pessoa"):  # foto sem pessoas: o padrão nunca põe uma
        mov = movs[CATEGORIA_PADRAO]
    return mov


def prompt_padrao(
    receitas: dict[str, Any],
    receita_id: str,
    categoria: str | None = None,
    *,
    sem_pessoas: bool = True,
    assunto: str = "",
    presets_data: dict[str, Any] | None = None,
) -> str:
    """O movimento padrão: texto fixo em inglês mais os termos dos presets, sem passar pelo LLM.

    Estilo Livre (com `camera_padrao`): o da categoria da foto (`movimentos`), com os presets dela
    por cima dos do estilo. Os outros estilos: o próprio (`acao_en`, com os presets do estilo).
    `assunto`: o da foto ("the red car", com a legenda por foto); vazio = o do texto fixo. Com a
    regra da pessoa inventada, nunca cita uma pessoa (cai no padrão "geral", sem o assunto)."""
    texto, sufixo = _padrao(receitas, receita_id, categoria, sem_pessoas, assunto, presets_data)
    return f"{texto} {sufixo}".strip()


def _padrao(
    receitas: dict[str, Any],
    receita_id: str,
    categoria: str | None,
    sem_pessoas: bool,
    assunto: str,
    presets_data: dict[str, Any] | None,
) -> tuple[str, str]:
    """(texto fixo, sufixo dos presets) do movimento padrão (`prompt_padrao`)."""
    r = _receita(receitas, receita_id)
    if r.get("camera_padrao") or not r.get("acao_en"):
        mov = _movimento(receitas, categoria or CATEGORIA_PADRAO, sem_pessoas)
        sel, acao, padrao = {**r["presets"], **mov["presets"]}, mov["acao_en"], mov["assunto"]
    else:
        sel, acao, padrao = dict(r["presets"]), r["acao_en"], r["assunto"]
    texto = compiler.fill_subject(acao, assunto or padrao)
    if sem_pessoas and prompt_cita_pessoa(texto):  # estilo com pessoa ou um assunto que é alguém
        mov = _movimento(receitas, CATEGORIA_PADRAO, True)
        sel = {**r["presets"], **mov["presets"]}
        texto = compiler.fill_subject(mov["acao_en"], mov["assunto"])
    return texto, presets.suffix(r["modo"], sel, presets_data)


def entrada(
    receitas: dict[str, Any],
    receita_id: str,
    texto: str,
    *,
    sem_pessoas: bool,
    versao: int,
    selections: dict[str, str] | None = None,
) -> presets.Composed:
    """A entrada do compilador: o texto, os presets do estilo (`selections` valem por cima) e, no
    Livre, a câmera suave se o texto não fala da câmera; com a regra da pessoa inventada e um prompt
    que a entende (v7 em diante), a linha "pessoas: nenhuma"."""
    r = _receita(receitas, receita_id)
    sel = {**r["presets"], **(selections or {})}
    cam = r.get("camera_padrao")
    if cam and "camera" not in sel and not texto_fala_da_camera(texto):
        sel["camera"] = cam
    composed = presets.compose(r["modo"], texto, sel)
    linha = compiler.PROFILES["i2v"].get("linha_pessoas")
    if sem_pessoas and linha and versao >= linha:
        composed = dataclasses.replace(composed, text=composed.text + "\npessoas: nenhuma")
    return composed


@dataclass(frozen=True)
class Traducao:
    prompt: str
    ref: str
    """Perfil e versão do prompt do compilador (ex.: i2v@7)."""
    model: str
    seconds: float
    tentativas: int = 1
    vago: bool = False
    """Pedido vago (`pedido_vago` ou o VAGUE do compilador): vale o movimento padrão."""
    padrao: bool = False
    """O prompt é o movimento padrão (pedido vago ou pessoa inventada em todas as tentativas)."""
    sem_pessoas: bool = True
    """A regra da pessoa inventada valeu nesta tradução."""
    pessoas_recusadas: tuple[str, ...] = ()
    """Palavras de pessoa das traduções recusadas (registro do pedido, só o painel vê)."""
    rejeitados: tuple[str, ...] = ()
    """As traduções recusadas por citar pessoa, inteiras (registro do pedido e da fidelidade)."""
    compiled: str = ""
    """O que o LLM escreveu na tradução aceita ("" com o movimento padrão)."""
    word_count: int = 0
    """Palavras do texto antes dos termos dos presets (o do LLM ou o fixo do movimento padrão)."""
    categoria: str = CATEGORIA_PADRAO


def traduzir(
    texto: str,
    receita_id: str,
    *,
    receitas: dict[str, Any],
    categoria: str | None = None,
    sem_pessoas: bool | None = None,
    assunto: str = "",
    compile_fn: Callable[..., compiler.CompileResult] = compiler.compile_prompt,
    tentativas: int = TENTATIVAS,
    version: int | None = None,
    seed: int | None = None,
    selections: dict[str, str] | None = None,
    **kw: Any,
) -> Traducao:
    """Traduz um pedido I2V com as regras do serviço. `sem_pessoas`: None = na dúvida, sim (a regra
    só não vale se o texto do cliente cita alguém). `assunto`: o da foto no movimento padrão (vazio
    = o do texto fixo). `kw` vai para o compilador (ollama_url, model, keep_alive...).
    CompilerRefused e os erros do compilador sobem para quem chama: o pedido vago também passa pelo
    compilador, para a regra REFUSED continuar valendo, mas o texto dele não é usado."""
    t0 = time.monotonic()
    version = version or compiler.PROFILES["i2v"]["version"]
    categoria = categoria or categoria_da_receita(receitas, receita_id)
    sem = (True if sem_pessoas is None else sem_pessoas) and not texto_cita_pessoa(texto)
    vago = pedido_vago(texto)
    composed = entrada(
        receitas, receita_id, texto, sem_pessoas=sem, versao=version, selections=selections
    )
    recusadas: list[str] = []
    rejeitados: list[str] = []
    model = str(kw.get("model") or compiler.DEFAULT_MODEL)
    for tentativa in range(1, max(1, tentativas) + 1):
        semente = None if seed is None else seed + 100 * (tentativa - 1)
        comp = compile_fn(
            composed.text,
            profile="i2v",
            version=version,
            seed=semente,
            suffix=composed.suffix,
            camera_preset=composed.camera,
            **kw,
        )
        model = comp.model
        comum = {
            "ref": f"i2v@{version}",
            "model": model,
            "tentativas": tentativa,
            "sem_pessoas": sem,
            "pessoas_recusadas": tuple(recusadas),
            "rejeitados": tuple(rejeitados),
            "categoria": categoria,
        }
        if vago or getattr(comp, "vague", False):
            fixo, sufixo = _padrao(receitas, receita_id, categoria, sem, assunto, None)
            return Traducao(
                prompt=f"{fixo} {sufixo}".strip(),
                seconds=time.monotonic() - t0,
                vago=True,
                padrao=True,
                word_count=len(fixo.split()),
                **comum,
            )
        achadas = prompt_cita_pessoa(comp.prompt) if sem else []
        if not achadas:
            return Traducao(
                prompt=comp.prompt,
                seconds=time.monotonic() - t0,
                compiled=comp.compiled,
                word_count=comp.word_count,
                **comum,
            )
        recusadas.extend(a for a in achadas if a not in recusadas)
        rejeitados.append(comp.prompt)
    fixo, sufixo = _padrao(receitas, receita_id, categoria, True, assunto, None)
    return Traducao(
        prompt=f"{fixo} {sufixo}".strip(),
        ref=f"i2v@{version}",
        model=model,
        seconds=time.monotonic() - t0,
        tentativas=max(1, tentativas),
        padrao=True,
        sem_pessoas=sem,
        pessoas_recusadas=tuple(recusadas),
        rejeitados=tuple(rejeitados),
        word_count=len(fixo.split()),
        categoria=categoria,
    )


def validar_receitas(receitas: dict[str, Any], presets_data: dict[str, Any]) -> list[str]:
    """Problemas nas receitas v4 (vazio: ok): categoria e movimento padrão de cada estilo, e nenhum
    texto fixo cita pessoa, a não ser o da categoria "pessoa" e o de estilo que exige a
    autorização."""
    erros: list[str] = []
    categorias = receitas.get("categorias") or {}
    movs = receitas.get("movimentos") or {}
    if CATEGORIA_PADRAO not in movs:
        erros.append(f"falta o movimento padrão {CATEGORIA_PADRAO!r}")
    for nome, mov in movs.items():
        if nome not in categorias:
            erros.append(f"movimento {nome}: categoria sem nome")
        texto = compiler.fill_subject(mov.get("acao_en", ""), mov.get("assunto", ""))
        if bool(prompt_cita_pessoa(texto)) != bool(mov.get("com_pessoa")):
            erros.append(f"movimento {nome}: 'com_pessoa' não confere com o texto")
        if "{assunto}" not in mov.get("acao_en", "") or not mov.get("assunto"):
            erros.append(f"movimento {nome}: sem {{assunto}} no acao_en ou sem o assunto padrão")
        try:
            presets.suffix("i2v", mov.get("presets") or {}, presets_data)
        except presets.PresetError as e:
            erros.append(f"movimento {nome}: {e}")
    for nome, r in receitas["receitas"].items():
        if r.get("categoria") not in categorias:
            erros.append(f"receita {nome}: categoria desconhecida {r.get('categoria')!r}")
        if not r.get("camera_padrao") and not (r.get("acao_en") and r.get("assunto")):
            erros.append(f"receita {nome}: sem acao_en e assunto, nem camera_padrao")
        texto = compiler.fill_subject(r.get("acao_en", ""), r.get("assunto", ""))
        if prompt_cita_pessoa(texto) and not r.get("exige_consentimento"):
            erros.append(f"receita {nome}: o texto fixo cita pessoa sem exigir a autorização")
        cam = r.get("camera_padrao")
        if cam and cam not in presets_data["categories"]["camera"]["options"]:
            erros.append(f"receita {nome}: câmera padrão desconhecida {cam!r}")
    return erros
