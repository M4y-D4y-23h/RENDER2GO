"""Intenção do pedido (17/10/2026): o texto do cliente nunca vira o movimento padrão por não termos
reconhecido um verbo, e as negações e restrições valem (render2go_worker.intencao e traducao).
SFW."""

import pytest

from render2go_worker import compiler, lote
from render2go_worker import intencao as i
from render2go_worker import traducao as t

REC = lote.load_receitas()


def _resultado(prompt: str, vague: bool = False) -> compiler.CompileResult:
    return compiler.CompileResult(
        prompt=prompt, profile="i2v", version=6, model="falso", seconds=0.1,
        word_count=len(prompt.split()), word_range=(15, 60), compiled=prompt, vague=vague,
    )  # fmt: skip


def _compilador(resposta: str = "The product slowly rotates in place.", vague: bool = False):
    chamadas: list[dict] = []

    def compile_fn(text, **kw):
        chamadas.append({"text": text, **kw})
        if resposta == "REFUSED":
            raise compiler.CompilerRefused("recusou")
        return _resultado(f"{resposta} {kw['suffix']}".strip(), vague=vague)

    compile_fn.chamadas = chamadas
    return compile_fn


# -- os quatro casos da análise de 17/10/2026 (na main e1c98bf, os três últimos viravam o padrão)


def test_a_present_tense_action_is_translated():
    fn = _compilador()
    tr = t.traduzir("O produto gira devagar.", "livre", receitas=REC, compile_fn=fn)
    assert not tr.padrao and tr.prompt.startswith("The product slowly rotates")
    assert tr.intencao == "acao"


def test_an_imperative_action_is_translated_not_replaced_by_the_default():
    fn = _compilador()
    tr = t.traduzir("Gire lentamente o produto.", "livre", receitas=REC, compile_fn=fn)
    assert not tr.padrao and not tr.vago, "o imperativo virava o movimento padrão"
    assert tr.prompt.startswith("The product slowly rotates") and tr.intencao == "acao"


def test_keep_everything_still_becomes_a_still_scene_never_the_moving_default():
    fn = _compilador(
        "The woman walks in."
    )  # o texto do LLM não é usado (nem poderia inventar alguém)
    tr = t.traduzir("Mantenha tudo imóvel, sem movimento.", "livre", receitas=REC, compile_fn=fn)
    assert len(fn.chamadas) == 1  # passou pelo compilador: a regra REFUSED continua valendo
    assert tr.intencao == "restricao" and tr.restrito and not tr.padrao
    assert "Static camera." in tr.prompt and "stay completely still" in tr.prompt
    for proibido in ("push-in", "dolly", "orbit", "glides", "sweeps", "steam", "fluid motion"):
        assert proibido not in tr.prompt, proibido


def test_a_subjunctive_action_is_translated():
    fn = _compilador("The sign glows softly.")
    tr = t.traduzir("Que o letreiro brilhe suavemente.", "livre", receitas=REC, compile_fn=fn)
    assert not tr.padrao and tr.prompt.startswith("The sign glows softly") and tr.intencao == "acao"


# -- a classificação ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto,tipo",
    [
        # ausência de instrução: o movimento padrão, como antes
        ("deixa bonito", "vazio"),
        ("deixe bonito, com um movimento suave", "vazio"),
        ("quero que fique bonito e profissional pra eu vender", "vazio"),
        ("algo que chame a atenção dos clientes", "vazio"),
        ("apresentação da minha loja nova", "vazio"),
        ("quero mostrar meu produto", "vazio"),
        ("olha que lindo", "vazio"),
        # ação explícita: presente, imperativo, subjuntivo, infinitivo, sinônimos, câmera
        ("o produto gira devagar", "acao"),
        ("gire o produto", "acao"),
        ("girem as taças", "acao"),
        ("mexa a colher devagar", "acao"),
        ("que a fumaça suba do café", "acao"),
        ("faça a xícara rodopiar", "acao"),
        ("rotacione o relógio lentamente", "acao"),
        ("o anel cintila", "acao"),
        ("coloque a câmera mais perto do bolo e aproxime devagar", "acao"),
        ("mostre os detalhes do relógio", "acao"),
        ("a câmera gira em volta do carro", "acao"),
        ("close no rosto", "acao"),
        ("a câmera fica parada e o vapor sobe", "acao"),
        ("câmera em volta, sem girar o produto", "acao"),
        # restrições
        ("Mantenha tudo imóvel, sem movimento.", "restricao"),
        ("nada se mexe", "restricao"),
        ("câmera parada", "restricao"),
        ("não gire o produto", "restricao"),
        ("sem zoom", "restricao"),
        ("deixa bonito, sem pessoas", "restricao"),
        # ambíguo: parece pedir algo que não reconhecemos
        ("arrodeie a vitrine", "ambiguo"),
        ("que a marca resplenda", "ambiguo"),
        # contradição
        ("gire o produto e mantenha tudo parado", "contraditorio"),
        ("gire o produto, mas não gire", "contraditorio"),
        ("a câmera parada e a câmera se aproxima", "contraditorio"),
        # fora da capacidade
        ("coloque o preço R$ 29,90 na tela", "fora_capacidade"),
        ("escreva promoção em cima", "fora_capacidade"),
        ("com música animada", "fora_capacidade"),
        ("um vídeo de 30 segundos", "fora_capacidade"),
        ("depois mostra a loja por dentro", "fora_capacidade"),
        ("troque a cor do fundo para branco", "fora_capacidade"),
    ],
)
def test_classification(texto, tipo):
    r = i.analisar(texto)
    assert r.tipo == tipo, (texto, r)
    assert r.bloqueia == (tipo in ("ambiguo", "contraditorio", "fora_capacidade"))


def test_a_negation_is_never_read_as_the_positive_action():
    r = i.analisar("não gire o produto")
    assert r.acoes == () and r.negadas == ("gire",) and "sem_rotacao" in r.restricoes
    r = i.analisar("sem brilho, só a luz natural")
    assert "brilho" not in r.acoes and "sem_luz" in r.restricoes
    r = i.analisar("evite zoom; a câmera parada")
    assert r.acoes == () and {"sem_aproximacao", "camera_parada"} <= set(r.restricoes)


def test_only_ambiguity_can_be_confirmed_as_written():
    assert i.analisar("arrodeie a vitrine").pendencias[0].confirmavel
    for texto in ("gire e não gire o produto", "coloque o preço R$ 10", "com música"):
        assert not any(p.confirmavel for p in i.analisar(texto).pendencias), texto


def test_a_longer_duration_than_the_order_allows_is_a_pending_item():
    assert i.analisar("vídeo de 5 segundos girando").tipo == "acao"
    assert i.analisar("vídeo de 7 s girando", duracao_max=7).tipo == "acao"
    assert i.analisar("vídeo de 1 minuto girando").pendencias[0].codigo == "duracao"


# -- restrições na tradução --------------------------------------------------------------


def test_no_people_keeps_the_invented_person_rule_on():
    # "sem pessoas" cita "pessoas": antes, isso desligava a regra (o texto "pedia" alguém)
    assert not t.texto_cita_pessoa("deixa bonito, sem pessoas")
    assert not t.texto_cita_pessoa("não coloque ninguém na cena")
    assert t.texto_cita_pessoa("uma pessoa segura o copo")
    fn = _compilador("A woman holds the cup.")
    tr = t.traduzir("a xícara gira devagar, sem pessoas", "livre", receitas=REC, sem_pessoas=False,
                    compile_fn=fn, tentativas=2)  # fmt: skip
    assert tr.sem_pessoas and t.prompt_cita_pessoa(tr.prompt) == []
    assert tr.substituiu  # a ação pedida não chegou ao prompt: a API pede a decisão do cliente


def test_a_static_camera_request_never_gets_the_gentle_camera():
    fn = _compilador("The steam rises from the cup.")
    tr = t.traduzir("a câmera fica parada e o vapor sobe", "livre", receitas=REC, compile_fn=fn)
    assert "Static camera." in tr.prompt and "push-in" not in tr.prompt
    # e num estilo com a câmera própria, a câmera parada pedida vale por cima
    tr = t.traduzir("câmera parada, o produto brilha", "produto-giro", receitas=REC, compile_fn=fn)
    assert "Static camera." in tr.prompt and "orbit" not in tr.prompt.lower()


def test_a_negated_motion_is_respected_by_the_fixed_text():
    fn = _compilador("x")
    tr = t.traduzir("não gire o carro", "livre", receitas=REC, categoria="carro", compile_fn=fn)
    assert tr.restrito and "orbit" not in tr.prompt.lower() and "Static camera." in tr.prompt
    tr = t.traduzir("sem zoom, por favor", "livre", receitas=REC, compile_fn=fn)
    assert tr.restrito and "push-in" not in tr.prompt
    tr = t.traduzir("sem vapor", "livre", receitas=REC, categoria="comida", compile_fn=fn)
    assert tr.restrito and "steam" not in tr.prompt.lower()


@pytest.mark.parametrize("texto", ["arrodeie a vitrine", "gire o produto e mantenha tudo parado"])
def test_ambiguous_and_contradictory_texts_are_never_replaced_by_the_default(texto):
    fn = _compilador("The shop window shines.")
    tr = t.traduzir(texto, "livre", receitas=REC, compile_fn=fn)
    assert not tr.padrao and tr.prompt.startswith("The shop window shines")


def test_the_compiler_vague_answer_on_an_explicit_action_is_flagged_not_silently_used():
    tr = t.traduzir(
        "gire o produto", "livre", receitas=REC, compile_fn=_compilador("x", vague=True)
    )
    assert tr.substituiu and tr.intencao == "acao"


def test_refused_still_goes_up_for_every_kind_of_text():
    for texto in ("deixa bonito", "Mantenha tudo imóvel, sem movimento.", "gire o produto"):
        with pytest.raises(compiler.CompilerRefused):
            t.traduzir(texto, "livre", receitas=REC, compile_fn=_compilador("REFUSED"))


def test_every_fixed_text_passes_the_text_filter_and_cites_nobody():
    from render2go_worker import filtros  # o C1 que a API e o pod passam no prompt

    casos = (("estatico",), ("camera_parada",), ("sem_rotacao",), ("sem_luz",), ("sem_vapor",),
             ("sem_aproximacao", "assunto_parado"))  # fmt: skip
    for restricoes in casos:
        for receita in REC["receitas"]:
            for categoria in [None, *REC["categorias"]]:
                p = t.prompt_restrito(REC, receita, restricoes, categoria)
                assert t.prompt_cita_pessoa(p) == [] or REC["receitas"][receita].get(
                    "exige_consentimento"
                ), p
                assert filtros.avaliar(p).allowed, p


# -- restrições marcadas no pedido (fora do texto) ----------------------------------------------


def test_marked_restrictions_count_as_written_ones():
    r = i.analisar("deixa bonito", restricoes=("camera_parada",))
    assert r.tipo == "restricao" and r.restricoes == ("camera_parada",)
    r = i.analisar("gire o produto", restricoes=("sem_rotacao",))
    assert r.tipo == "contraditorio" and "giro" in r.pendencias[0].mensagem
    r = i.analisar("a câmera se aproxima do bolo", restricoes=("camera_parada",))
    assert r.tipo == "contraditorio"
    r = i.analisar("o vapor sobe", restricoes=("sem_pessoas", "camera_parada"))
    assert r.tipo == "acao" and set(r.restricoes) == {"sem_pessoas", "camera_parada"}
    with pytest.raises(ValueError):
        i.analisar("deixa bonito", restricoes=("qualquer",))
    assert set(i.RESTRICOES_MARCAVEIS) <= set(i.RESTRICOES)


def test_marked_restrictions_reach_the_translation():
    fn = _compilador("The cake sits on the table.")
    tr = t.traduzir("deixa bonito", "livre", receitas=REC, categoria="comida", compile_fn=fn,
                    restricoes_extras=("sem_aproximacao",))  # fmt: skip
    assert tr.restrito and "push-in" not in tr.prompt and tr.restricoes == ("sem_aproximacao",)
    tr = t.traduzir("o vapor sobe da xícara", "livre", receitas=REC, compile_fn=fn,
                    restricoes_extras=("camera_parada",))  # fmt: skip
    assert tr.intencao == "acao" and "Static camera." in tr.prompt


def test_the_pod_refuses_unknown_marked_restrictions():
    from render2go_worker import fila

    assert fila._restricoes(None) == () and fila._restricoes(["sem_pessoas"]) == ("sem_pessoas",)
    for ruim in (["apagar_tudo"], "sem_pessoas", [1]):
        with pytest.raises(t.TraducaoError):
            fila._restricoes(ruim)


# -- o plano (a mesma decisão, sem o LLM, para a API mostrar antes do pod) ------------------------


@pytest.mark.parametrize(
    "texto,restr",
    [
        ("deixa bonito", ()),
        ("Mantenha tudo imóvel, sem movimento.", ()),
        ("não gire o produto", ()),
        ("sem vapor", ()),
        ("Gire lentamente o produto.", ()),
        ("a câmera fica parada e o vapor sobe", ()),
        ("deixa bonito", ("camera_parada",)),
        ("o produto brilha", ("sem_aproximacao", "sem_pessoas")),
        ("arrodeie a vitrine", ()),
    ],
)
def test_the_plan_matches_what_the_translation_does(texto, restr):
    from render2go_worker import presets

    for receita in REC["receitas"]:
        for categoria in (None, "comida", "carro", "pessoa"):
            fn = _compilador("The subject shines.")
            tr = t.traduzir(texto, receita, receitas=REC, categoria=categoria, compile_fn=fn,
                            restricoes_extras=restr)  # fmt: skip
            pl = t.plano(texto, receita, receitas=REC, categoria=categoria, restricoes_extras=restr)
            sufixo = presets.suffix(REC["receitas"][receita]["modo"], pl.presets)
            assert pl.intencao.tipo == tr.intencao and pl.sem_pessoas == tr.sem_pessoas
            if pl.modo == "traduzir":
                assert fn.chamadas[0]["suffix"] == sufixo and not tr.padrao and not tr.restrito
            else:
                assert tr.prompt == f"{pl.texto_fixo} {sufixo}".strip(), (receita, categoria)
                assert tr.restrito == (pl.modo == "restrito") and tr.padrao == (pl.modo == "padrao")
