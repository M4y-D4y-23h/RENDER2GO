"""O que o texto do cliente pede, antes de qualquer LLM (17/10/2026).

Até 17/10/2026, `traducao.pedido_vago` decidia só "tem ou não tem uma palavra de movimento da
lista". O que a lista não conhecia virava o movimento padrão (luz passando e aproximação da
câmera), mesmo quando o cliente tinha pedido outra coisa:

- "Gire lentamente o produto." (imperativo: a lista só tinha "gira", "giram", "girando", "girar");
- "Que o letreiro brilhe suavemente." (subjuntivo);
- "Mantenha tudo imóvel, sem movimento." (uma restrição: o padrão mexia a câmera e a luz).

Aqui o texto é separado em seis casos, com a mesma regra no pod (tradução) e na API (o cartão
"Entendemos seu pedido assim" e as pendências, antes de qualquer pod):

- **vazio**: só um desejo ("deixa bonito", "vídeo profissional pra vender"): vale o movimento
  padrão, e o cliente vê que foi isso;
- **acao**: uma ação explícita ("gire o produto", "que o letreiro brilhe"): o tradutor traduz;
- **restricao**: só restrições ("sem movimento", "câmera parada", "não gire", "sem pessoas"): um
  texto fixo que as respeita, nunca um padrão que as contradiga;
- **ambiguo**: parece pedir algo que não reconhecemos com segurança: pendência (o cliente esclarece
  ou confirma o texto como está);
- **contraditorio**: pede e proíbe a mesma coisa, ou mexe o que pediu parado: pendência;
- **fora_capacidade**: o que o vídeo não faz (texto, preço ou logo escritos, música, mais de uma
  cena, outra duração, editar o produto): pendência, com o caminho certo quando ele existe.

Uma palavra positiva nunca apaga a negação perto dela ("não gire" não é um pedido de giro). Não
reconhecer um verbo nunca autoriza trocar a intenção pelo padrão: vira ambiguidade.

O vocabulário é fixo e testado (`worker/tests/test_intencao.py`). Não é camada de segurança: o C1,
a regra REFUSED e a revisão humana continuam onde estão.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any

VERSAO = 1  # muda junto com as regras abaixo; fica gravada na versão do pedido (render2go_servico)
TIPOS = ("vazio", "acao", "restricao", "ambiguo", "contraditorio", "fora_capacidade")
DURACAO_MAX_S = 5  # o maior vídeo dos pedidos novos (render2go_servico.precos.DURACAO_MAX)


def sem_acento(texto: str) -> str:
    nfd = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")


def _palavras(*linhas: str) -> set[str]:
    return {p for linha in linhas for p in linha.split()}


# -- ações -------------------------------------------------------------------------------------
# Raízes (sem acento) e as formas que um cliente escreve: presente (gira, giram), gerúndio
# (girando), infinitivo (girar), imperativo e subjuntivo (gire, girem; mexa, mexam), pretérito
# (girou). O "c" e o "g" do fim da raiz mudam no subjuntivo (coloque, apague); com o "ç" sem
# acento, "avance" fica igual: as duas formas entram (a que não existe não faz mal).
_RAIZES_AR = (
    "gir rod rotacion rodopi balanc moviment ping borbulh solt brilh cintil pisc apag fech aproxim"
    " afast avanc recu pass caminh and danc vo pul salt sopr vent ondul tremul nev flutu desliz mud"
    " transform esvazi cort mistur levant abaix acen olh vir inclin orbit gotej respir aban entr"
    " pux empurr jog lanc quebr espirr jorr derram espalh desabroch aceler estacion decol pous rol"
    " quic vibr ilumin peg segur toc apert abrac mastig prov despej tamp destamp fati reg molh sec"
    " pint desenh coloc cozinh ass frit grelh decor mont dobr desdobr estic encolh infl estour"
    " escorreg mostr destac revel explor focaliz foc acompanh panoramiz fumeg balan sacolej"
    " chacoalh"
)
_RAIZES_ER = (
    "mex mov desc escorr derret ferv acend ench corr aparec cresc floresc escurec amanhec anoitec"
    " nasc com beb percorr remex esvaec resplandec"
)
_RAIZES_IR = "abr surg serv reluz"
_IRREGULARES = _palavras(
    "sobe sobem subindo subir suba subam subiu cai caem caindo cair caia caiam caiu sai saem",
    "saindo",
    "sair saia saiam saiu dorme dormem dormindo dormir durma sorri sorriem sorrindo sorrir sorria",
    "poe poem pondo ponha ponham flui fluem fluindo fluir flua chove chovendo chova late latem",
    "latindo lata reflete refletem refletindo refletir reflita explode explodem explodindo exploda",
    "escoa escoam escoando escoe treme tremem tremer trema sacode sacodem sacudindo sacudir sacuda",
    "foge fogem fugindo fuja freia freiam freando frear freie clareia clareiam clareando clarear",
    "clareie segue seguem seguindo siga sigam surja surjam sirva sirvam abra abram reluza reluzam",
    "rodeia rodeiam rodeando rodear rodeie rodeiem",
)
# o que se move na cena e a câmera, sem verbo ("com vapor", "zoom lento", "close no rosto")
_ELEMENTOS = _palavras(
    "fumaca vapor bolha bolhas gota gotas chuva neve vento brisa onda ondas faisca faiscas chamas",
    "fogo reflexo reflexos brilho nevoa neblina zoom close closeup panoramica travelling drone",
    "orbita timelapse giro rotacao",
)
# infinitivos que aparecem mais em desejos ("quero passar confiança", "mudar o visual", "mostrar
# meu produto") que na cena; nas outras formas ("mostre os detalhes") contam como ação
_SO_DESEJO = _palavras("passar mudar virar transformar levantar provar tocar mostrar destacar")
# formas que são outra palavra comum (preposição, substantivo de pedido)
_NAO_ACAO = _palavras("entre passe corte monte sec seca seco come coma voo vire")


def _formas(raizes: str, fins: tuple[str, ...]) -> set[str]:
    out = {r + f for r in raizes.split() for f in fins}
    for r in raizes.split():  # c/g do fim da raiz: subjuntivo com "que"/"gue" (coloque, apague)
        if r.endswith("c"):
            out |= {r[:-1] + "que", r[:-1] + "quem"}
        if r.endswith("g"):
            out |= {r + "ue", r + "uem"}
    return out


_ACOES = (
    (
        _formas(_RAIZES_AR, ("a", "am", "ando", "ar", "e", "em", "ou"))
        | _formas(_RAIZES_ER, ("e", "em", "endo", "er", "a", "am", "eu"))
        | _formas(_RAIZES_IR, ("e", "em", "indo", "ir", "iu"))
        | _IRREGULARES
        | _ELEMENTOS
    )
    - _SO_DESEJO
    - _NAO_ACAO
)

# conceito de cada ação: negação, contradição e o texto fixo que não pode contradizer o pedido
_CONCEITOS = {
    "rotacao": ("gir", "rod", "rotacion", "rodopi", "orbit", "giro", "rotacao", "orbita"),
    "aproximacao": ("aproxim", "avanc", "zoom", "close", "closeup", "focaliz", "foc"),
    "afastamento": ("afast", "recu"),
    "luz": ("brilh", "cintil", "pisc", "ilumin", "acend", "reflet", "reflexo", "reluz", "clare"),
    "movimento": (
        "mex",
        "mov",
        "moviment",
        "balanc",
        "balan",
        "sacud",
        "sacod",
        "trem",
        "vibr",
        "sacolej",
        "chacoalh",
    ),  # fmt: skip
    "vapor": ("vapor", "fumaca", "fumeg"),
}
_SO_CAMERA = _palavras("zoom close closeup panoramica panoramiz travelling drone orbita")
_CAMERA = _palavras("camera cameras filmagem enquadramento plano tomada")
_MOV_CAMERA = (
    "aproxim", "afast", "avanc", "recu", "sob", "sub", "desc", "gir", "orbit", "pass", "percorr",
    "acompanh", "panoramiz", "segu", "sig", "explor",
)  # fmt: skip

# -- negação, restrições, fora da capacidade ---------------------------------------------------
_NEGACAO = _palavras(
    "nao sem nunca nem jamais evite evitar evitem nenhum nenhuma nada proibido proibida zero"
)
_FIM_ESCOPO = _palavras("mas porem entretanto contudo e enquanto so apenas somente")
_PARADO = re.compile(
    r"\b(?:imove(?:l|is)|estatic[oa]s?|parad[oa]s?|congelad[oa]s?|fix[oa]s?|quiet[oa]s?)\b"
)
_TUDO_PARADO = re.compile(
    r"\b(?:sem (?:nenhum )?movimentos?|sem (?:se )?mexer|nada (?:se )?(?:mexe|move|movimenta)"
    r"|nada se mova|(?:tudo|cena|imagem|video|foto) (?:\w+ ){0,2}"
    r"(?:imove(?:l|is)|parad[oa]|estatic[oa]|congelad[oa]))\b"
)
_PESSOAS = r"(?:pessoas?|gente|ninguem|modelos?|figurantes?|clientes? na cena)"
_SEM_PESSOAS = re.compile(
    r"\b(?:sem " + _PESSOAS + r"|nenhuma pessoa|ninguem (?:na|no|em) \w+"
    r"|nao (?:\w+ ){0,2}" + _PESSOAS + r")\b"
)
_ADICIONAR = (
    r"(?:coloqu\w*|coloca\w*|ponha|poe|por|adicion\w*|acrescent\w*|inclu\w*|insir\w*|insere"
    r"|bot\w*|escrev\w*)"
)
_TEXTO_NA_IMAGEM = re.compile(
    r"\b(?:" + _ADICIONAR + r" (?:\w+ ){0,3}(?:texto|frase|legenda|slogan|titulo|preco|valor|logo"
    r"|logotipo|nome|telefone|whatsapp|endereco|site|instagram|arroba|marca d.?agua)"
    r"|(?:com|mostrando|mostre|mostra) (?:o |a )?(?:preco|valor|logo|logotipo|slogan|telefone"
    r"|whatsapp)|escrit[oa] |escreva|escrever|digite|legenda)\b"
    r"|r\$\s*\d"
)
_AUDIO = re.compile(
    r"\b(?:musica|musicas|som|sons|sonoro|trilha sonora|narracao|narrador\w*|locucao|locutor\w*"
    r"|voz|audio|jingle)\b"
)
_CENAS = re.compile(
    r"\b(?:em seguida|depois (?:mostr\w*|aparec\w*|corta\w*)|corta(?:r)? para|outra cena"
    r"|varias cenas|segunda cena|cena \d|transic\w*|sequencia de cenas|e depois)\b"
)
_DURACAO = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(segundos?|seg|s|minutos?|min)\b")
_EDICAO = re.compile(
    r"\b(?:(?:troqu|troc|mud|alter|substitu)\w* (?:\w+ ){0,2}"
    r"(?:cor|fundo|cenario|rotulo|embalagem|tamanho)"
    r"|(?:remov|tir|apag|desfoqu|desfoc)\w* (?:\w+ ){0,2}fundo"
    r"|fundo (?:branco|transparente|diferente))\b"
)

# -- desejos sem instrução e verbos que não conhecemos ------------------------------------------
# imperativos e verbos de quem pede o vídeo, não do que acontece na cena
_PEDIDO = _palavras(
    "faca facam faz fazer deixa deixe deixar crie cria criar gere gera gerar produza produzir",
    "prepare preparar quero queria gostaria precisamos preciso use usa usar capricha caprichar",
    "capriche ficar fique fiquem fica vender venda divulgar divulgue atrair chamar chame chama",
    "passar mostrar destacar olha valorize valorizar realce realcar melhore melhorar transmita",
    "transmitir anunciar anuncie promover promova apresentar apresente postar poste publicar seja",
    "ser esteja tenha ter mantenha manter mantem ajude ajudar impressione impressionar convide",
    "convidar encante encantar lembre lembrar venha vir tem ta esta sao e inspire inspirar",
    "aumentar aumente parecer pareca",
)
_LIGACAO = _palavras(
    "o a os as um uma uns umas seu sua seus suas meu minha meus minhas nosso nossa esse essa",
    "este esta esses essas isso isto aquele aquela cada todo toda todos todas",
)
_VERBO_FORMA = re.compile(r"^[a-z]{3,}(?:ar|er|ir|ando|endo|indo)$")
# palavras comuns que terminam como verbo e não são ("devagar", "mulher", "lugar")
_NAO_VERBO = _palavras(
    "devagar lugar lugares bar mar altar particular popular familiar similar regular solar lunar",
    "militar escolar mulher colher talher prazer qualquer olhar jantar almocar lar par celular",
    "luar pomar ar sabor cor flor melhor maior menor interior exterior anterior posterior",
    "superior inferior que se de do da por para pra com como mais bem ate entre sobre sempre onde",
    "quando tambem lindo linda bonito bonita elegante moderno moderna suave leve clean loja",
    "vitrine foto fotos video videos produto produtos cena imagem",
)
_INTERJEICAO = re.compile(r"\bolh[ae] (?:que|so|isso|como)\b|\bveja (?:que|so)\b")
_FIM_VERBAL = re.compile(r"(?:e|em|a|am)$")


@dataclass(frozen=True)
class Pendencia:
    """O que precisa de uma decisão do cliente antes de gerar (nunca o raciocínio de um modelo).
    `codigo`: ambiguo, contraditorio, texto_na_imagem, audio, varias_cenas, duracao ou edicao.
    `confirmavel`: o cliente pode confirmar "seguir com o texto como está" (só a ambiguidade)."""

    codigo: str
    mensagem: str  # para o cliente, em português, com o que fazer
    trecho: str = ""  # o pedaço do texto que motivou
    confirmavel: bool = False


@dataclass(frozen=True)
class Intencao:
    tipo: str
    acoes: tuple[str, ...] = ()
    negadas: tuple[str, ...] = ()
    restricoes: tuple[str, ...] = ()
    pendencias: tuple[Pendencia, ...] = ()
    desconhecidas: tuple[str, ...] = ()
    versao: int = VERSAO
    conceitos: tuple[str, ...] = ()  # conceitos das ações positivas (rotacao, luz...)

    @property
    def bloqueia(self) -> bool:
        """Precisa de uma decisão do cliente antes de gerar."""
        return bool(self.pendencias)

    def como_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["pendencias"] = [asdict(p) for p in self.pendencias]
        return d


def _conceito(palavra: str) -> str | None:
    if palavra in _SO_CAMERA:
        return "aproximacao" if palavra in ("zoom", "close", "closeup") else "camera"
    for nome, raizes in _CONCEITOS.items():
        if any(palavra.startswith(r) for r in raizes):
            return nome
    return None


def _clausulas(texto: str) -> list[list[str]]:
    sem = _INTERJEICAO.sub(" ", sem_acento(texto))
    partes = re.split(r"[.;!?\n]+|,|\bmas\b|\bporem\b", sem)
    return [re.findall(r"[a-z0-9$%]+", p) for p in partes if p.strip()]


def _negada(tokens: list[str], i: int) -> bool:
    """A palavra em `i` está negada? Uma negação até 3 palavras antes, sem "mas"/"e" no meio."""
    for j in range(i - 1, max(-1, i - 4), -1):
        if tokens[j] in _FIM_ESCOPO:
            return False
        if tokens[j] in _NEGACAO:
            return True
    return False


def _fala_da_camera(tokens: list[str], i: int) -> bool:
    return any(t in _CAMERA for t in tokens[max(0, i - 4) : i + 3])


def _verbo_desconhecido(tokens: list[str], i: int) -> bool:
    """Parece um verbo que não conhecemos: infinitivo ou gerúndio, imperativo no começo da oração
    ("Arrodeie a vitrine") ou subjuntivo depois de "que o/a ..." ("que a marca resplenda")."""
    tok = tokens[i]
    if tok in _ACOES or tok in _PEDIDO or tok in _NEGACAO or tok in _LIGACAO or tok in _NAO_VERBO:
        return False
    if _negada(tokens, i):
        return False
    if _VERBO_FORMA.match(tok):
        return True
    if i == 0 and len(tokens) > 1 and tokens[1] in _LIGACAO and _FIM_VERBAL.search(tok):
        return True
    return (
        i >= 3
        and tokens[i - 3] == "que"
        and tokens[i - 2] in _LIGACAO
        and bool(_FIM_VERBAL.search(tok))
    )


def _pendencias_fora(base: str, duracao_max: int) -> list[Pendencia]:
    out = []
    if m := _TEXTO_NA_IMAGEM.search(base):
        out.append(Pendencia(
            "texto_na_imagem",
            "O vídeo gerado não escreve textos, preços nem logotipos com segurança (as letras saem "
            "tortas). Peça o vídeo sem o texto; título, preço e logo entram por cima, prontos, na "
            "Montagem, quando ela estiver disponível na sua conta.",
            m.group(0).strip(),
        ))  # fmt: skip
    if m := _AUDIO.search(base):
        out.append(Pendencia(
            "audio",
            "O vídeo sai sem som: música, narração e efeitos sonoros não fazem parte deste "
            "serviço.",
            m.group(0),
        ))  # fmt: skip
    if m := _CENAS.search(base):
        out.append(Pendencia(
            "varias_cenas",
            "Cada vídeo é uma cena só, contínua, a partir da foto. Para uma sequência, peça um "
            "vídeo por foto (e junte-os depois na Montagem, quando disponível).",
            m.group(0),
        ))  # fmt: skip
    for m in _DURACAO.finditer(base):
        valor = float(m.group(1).replace(",", "."))
        segundos = valor * 60 if m.group(2).startswith("min") else valor
        if segundos > duracao_max:
            out.append(Pendencia(
                "duracao",
                f"Cada vídeo tem até {duracao_max} segundos; a duração é escolhida no pedido, "
                "não no texto.",
                m.group(0),
            ))  # fmt: skip
            break
    if m := _EDICAO.search(base):
        out.append(Pendencia(
            "edicao",
            "O vídeo mantém o produto e a foto como são: trocar cor, fundo, rótulo ou embalagem é "
            "edição de imagem, que ainda não está disponível.",
            m.group(0),
        ))  # fmt: skip
    return out


def analisar(texto: str, *, duracao_max: int = DURACAO_MAX_S) -> Intencao:
    """O que o texto pede (ver o começo do módulo). Texto vazio: "vazio"."""
    clausulas = _clausulas(texto)
    base = " ".join(" ".join(c) for c in clausulas)
    acoes: list[str] = []
    negadas: list[str] = []
    conceitos: set[str] = set()
    negados: set[str] = set()
    restr: list[str] = []
    camera_acao = False
    desconhecidas: list[str] = []

    def restringe(r: str) -> None:
        if r not in restr:
            restr.append(r)

    if m := re.search(r"\b(?:nao |sem )?(?:\w+ )?(?:em volta|ao redor)\b", base):
        if re.match(r"(?:nao|sem)\b", m.group(0)):
            negadas.append("em volta")
            negados.add("rotacao")
        else:  # com a câmera é o movimento dela ("a câmera gira em volta"), não o do produto
            acoes.append("em volta")
            camera = any(t in _CAMERA for t in base.split())
            conceitos.add("camera" if camera else "rotacao")
            camera_acao = camera_acao or camera
    if _TUDO_PARADO.search(base):
        restringe("estatico")
    if _SEM_PESSOAS.search(base):
        restringe("sem_pessoas")
    for tokens in clausulas:
        for i, tok in enumerate(tokens):
            if tok in _ACOES:
                conceito = _conceito(tok)
                if _negada(tokens, i):
                    negadas.append(tok)
                    if conceito:
                        negados.add(conceito)
                    continue
                acoes.append(tok)
                if conceito:
                    conceitos.add(conceito)
                if _fala_da_camera(tokens, i) and any(tok.startswith(r) for r in _MOV_CAMERA):
                    camera_acao = True
                if conceito in ("aproximacao", "afastamento", "camera"):
                    camera_acao = True
            elif _PARADO.fullmatch(tok):
                antes = tokens[max(0, i - 3) : i]
                if _fala_da_camera(tokens, i):
                    restringe("camera_parada")
                elif any(t in ("tudo", "cena", "imagem", "video", "nada") for t in antes):
                    restringe("estatico")
                else:
                    restringe("assunto_parado")
            elif tok in _CAMERA and i + 1 < len(tokens) and tokens[i + 1] == "nao":
                restringe("camera_parada")  # "a câmera não se mexe"
        desconhecidas += [tok for i, tok in enumerate(tokens) if _verbo_desconhecido(tokens, i)]
    # negações sem o verbo positivo ("sem zoom", "sem brilho", "não gire")
    for c in sorted(negados):
        if c == "movimento":
            restringe("estatico")
        elif c == "camera":
            restringe("camera_parada")
        else:
            restringe(f"sem_{c}")

    pendencias = _pendencias_fora(base, duracao_max)
    contradicoes = []
    if conceitos & negados:
        mesmos = ", ".join(sorted(conceitos & negados))
        contradicoes.append(f"pede e proíbe o mesmo movimento ({mesmos})")
    if "estatico" in restr and (conceitos - {"aproximacao", "afastamento", "camera"}):
        contradicoes.append("pede a cena parada e também um movimento")
    if "camera_parada" in restr and camera_acao:
        contradicoes.append("pede a câmera parada e também um movimento de câmera")
    if contradicoes:
        msg = "O texto " + " e ".join(contradicoes) + ". Diga qual dos dois vale."
        pendencias.insert(0, Pendencia("contraditorio", msg))
    if pendencias:
        tipo = "contraditorio" if contradicoes else "fora_capacidade"
    elif acoes:
        tipo = "acao"
    elif restr:
        tipo = "restricao"
    elif desconhecidas:
        tipo = "ambiguo"
        trecho = ", ".join(dict.fromkeys(desconhecidas[:3]))
        pendencias = [Pendencia(
            "ambiguo",
            f"Não entendemos com segurança o que deve acontecer no vídeo (“{trecho}”). Diga "
            "o que se mexe, por exemplo “o produto gira devagar” ou “a câmera se "
            "aproxima”, ou confirme que podemos seguir com o texto como está.",
            trecho,
            confirmavel=True,
        )]  # fmt: skip
    else:
        tipo = "vazio"
    return Intencao(
        tipo=tipo,
        acoes=tuple(dict.fromkeys(acoes)),
        negadas=tuple(dict.fromkeys(negadas)),
        restricoes=tuple(restr),
        pendencias=tuple(pendencias),
        desconhecidas=tuple(dict.fromkeys(desconhecidas)),
        conceitos=tuple(sorted(conceitos)),
    )


def nega_pessoas(texto: str) -> bool:
    """O texto pede a cena sem pessoas ("sem pessoas", "ninguém na loja", "não mostre gente")."""
    return bool(_SEM_PESSOAS.search(" ".join(" ".join(c) for c in _clausulas(texto))))


def sem_mencao_negada_de_pessoas(texto_sem_acento: str) -> str:
    """O texto (já sem acento) sem as menções negadas a pessoas ("sem pessoas" não pede alguém)."""
    return _SEM_PESSOAS.sub(" ", texto_sem_acento)
