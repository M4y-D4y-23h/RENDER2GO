"""Regras do serviço em volta do tradutor (13/10/2026): câmera suave do Livre, pedido vago e pessoa
inventada (render2go_worker.traducao). Tudo SFW."""

import pytest

from render2go_worker import compiler, lote, presets
from render2go_worker import traducao as t

REC = lote.load_receitas()


def _resultado(prompt: str, vague: bool = False, model: str = "falso") -> compiler.CompileResult:
    return compiler.CompileResult(
        prompt=prompt,
        profile="i2v",
        version=7,
        model=model,
        seconds=0.1,
        word_count=len(prompt.split()),
        word_range=(15, 60),
        compiled=prompt,
        vague=vague,
    )


def _compilador(*respostas: str):
    """Compilador falso: devolve as respostas em ordem (a última se repete) e guarda as chamadas."""
    chamadas: list[dict] = []

    def compile_fn(text, **kw):
        chamadas.append({"text": text, **kw})
        resposta = respostas[min(len(chamadas), len(respostas)) - 1]
        if resposta == "REFUSED":
            raise compiler.CompilerRefused("recusou")
        if resposta == "VAGUE":
            return _resultado(compiler.fill_subject(compiler.VAGO_TEXTO), vague=True)
        return _resultado(f"{resposta} {kw['suffix']}".strip())

    compile_fn.chamadas = chamadas
    return compile_fn


@pytest.mark.parametrize(
    "texto,cita",
    [
        ("a moça sorri e acena para a câmera", True),
        ("o barbeiro corta o cabelo do cliente", True),
        ("a mão segura a xícara", True),
        ("a mao segura a xicara", True),
        ("uma mulher segura a bolsa", True),
        ("a modelo desfila com o vestido", True),
        ("o bebê sorri", True),
        ("sabonete feito à mão girando devagar", False),
        ("sabonete feito a mao girando devagar", False),
        ("bolsa de mulher girando", False),
        ("roupa de criança balançando", False),
        ("roupa de bebê no varal", False),
        ("quero atrair clientes", False),
        ("porção por pessoa, vapor subindo", False),
        ("a gente quer um vídeo bonito", False),
        ("sou dona da loja, deixa bonito", False),
        ("café moca com espuma", False),
        ("o gato bebe água", False),
        ("o modelo novo do carro brilha", False),
        ("vestido de noiva balançando", False),
        ("a bolsa gira e ela brilha", False),
    ],
)
def test_who_the_client_asks_for(texto, cita):
    assert t.texto_cita_pessoa(texto) is cita


@pytest.mark.parametrize(
    "prompt,palavras",
    [
        ("She stays in place while soft light glides across it.", ["she"]),
        ("A hand holds the cup.", ["hand"]),
        ("His smile widens.", ["his", "smile"]),
        ("Customers walk by the shop window.", ["customers"]),
        ("The model rotates slowly.", ["model"]),
        ("The hand-painted mug rotates.", []),
        ("The women's handbag rotates on the table.", []),
        ("The car body shines; full body shot.", []),
        ("The watch face glows.", []),
        ("The heater hums and the herbs sway.", []),
    ],
)
def test_who_the_prompt_mentions(prompt, palavras):
    assert t.prompt_cita_pessoa(prompt) == palavras


def test_no_preset_term_and_no_fixed_motion_mentions_a_person():
    """O prompt é conferido inteiro, com o sufixo: nenhum termo dos presets pode citar pessoa, e
    nenhum movimento padrão, com a regra valendo."""
    data = presets.load()
    for spec in data["categories"].values():
        for opt in spec["options"].values():
            for termo in opt["en"]:
                assert t.prompt_cita_pessoa(termo) == [], termo
    assert t.validar_receitas(REC, data) == []
    for receita in REC["receitas"]:
        for categoria in [None, *REC["categorias"]]:
            padrao = t.prompt_padrao(REC, receita, categoria, sem_pessoas=True)
            assert t.prompt_cita_pessoa(padrao) == [], (receita, categoria)


def test_every_fixed_motion_passes_the_text_filter():
    from render2go_worker import filtros  # o C1 que a API e o pod passam no prompt

    for receita in REC["receitas"]:
        for categoria in [None, *REC["categorias"]]:
            for sem in (True, False):
                padrao = t.prompt_padrao(REC, receita, categoria, sem_pessoas=sem)
                assert filtros.avaliar(padrao).allowed, padrao


@pytest.mark.parametrize(
    "texto",
    [
        "deixa bonito",
        "deixe bonito, com um movimento suave",
        "quero que fique bonito e profissional pra eu vender",
        "algo que chame a atenção dos clientes",
        "um vídeo que chama a atenção",
        "apresentação da minha loja nova",
        "faça um vídeo de divulgação dessas motos para o instagram",
        "quero passar confiança",
    ],
)
def test_a_wish_without_motion_is_vague(texto):
    assert t.pedido_vago(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "a câmera se aproxima devagar do bolo e o vapor sobe da xícara",
        "o produto gira devagar",
        "as folhas balançam com o vento",
        "o letreiro acende",
        "o queijo derrete",
        "o cachorro abana o rabo",
        "a moça sorri e acena",
        "close no rosto",
        "zoom lento na vitrine",
    ],
)
def test_a_concrete_motion_is_not_vague(texto):
    assert not t.pedido_vago(texto)


def test_the_free_style_gets_a_gentle_camera_only_when_the_text_does_not_talk_about_the_camera():
    c = t.entrada(REC, "livre", "o tênis gira devagar", sem_pessoas=True, versao=7)
    assert c.camera and c.selections["camera"] == "suave"
    assert 'câmera: "slow, gentle camera push-in"' in c.text and c.text.endswith("pessoas: nenhuma")
    c = t.entrada(REC, "livre", "a câmera gira em volta do carro", sem_pessoas=True, versao=7)
    assert "camera" not in c.selections
    # o v6 não conhece a linha "pessoas"; outros estilos já têm a câmera deles
    c = t.entrada(REC, "livre", "o tênis gira devagar", sem_pessoas=True, versao=6)
    assert "pessoas" not in c.text
    c = t.entrada(REC, "produto-giro", "o tênis gira devagar", sem_pessoas=False, versao=7)
    assert c.selections["camera"] == "orbita-produto" and "pessoas" not in c.text


def test_a_vague_request_becomes_the_default_motion_of_the_photo_category():
    fn = _compilador(
        "The woman smiles at the camera."
    )  # o LLM inventaria; o texto dele não é usado
    tr = t.traduzir("deixa bonito", "livre", receitas=REC, categoria="carro", compile_fn=fn)
    assert tr.vago and tr.padrao and len(fn.chamadas) == 1  # passa pelo LLM (regra REFUSED)
    assert tr.prompt == t.prompt_padrao(REC, "livre", "carro")
    assert (
        tr.prompt.startswith("The car stays parked in place")
        and "Slow orbit around the vehicle" in tr.prompt
    )
    # num estilo com movimento próprio, o do estilo
    tr = t.traduzir("capricha no visual", "produto-giro", receitas=REC, compile_fn=_compilador("x"))
    assert tr.prompt.startswith("The product slowly rotates in place on the surface")
    assert "Slow orbit around the product" in tr.prompt
    # o VAGUE do compilador também vale, mesmo com uma palavra de movimento no texto
    tr = t.traduzir("olha que lindo", "livre", receitas=REC, compile_fn=_compilador("VAGUE"))
    assert tr.vago and tr.prompt == t.prompt_padrao(REC, "livre", "geral")


def test_a_person_photo_gets_the_subtle_motion_only_when_it_has_people():
    tr = t.traduzir(
        "deixa bonito", "livre", receitas=REC, categoria="pessoa", sem_pessoas=False,
        compile_fn=_compilador("x"),
    )  # fmt: skip
    assert tr.prompt.startswith("The person keeps a calm, natural pose")
    assert tr.prompt.endswith("the face and facial features stay exactly the same.")
    tr = t.traduzir(
        "deixa bonito", "livre", receitas=REC, categoria="pessoa", sem_pessoas=True,
        compile_fn=_compilador("x"),
    )  # fmt: skip
    assert t.prompt_cita_pessoa(tr.prompt) == [] and tr.prompt.startswith("The main subject")


def test_an_invented_person_is_translated_again_with_another_seed():
    fn = _compilador("She smiles as the cup steams.", "Steam rises gently from the cup.")
    tr = t.traduzir("o vapor sobe da xícara", "livre", receitas=REC, compile_fn=fn, seed=7)
    assert tr.prompt.startswith("Steam rises gently from the cup.")
    assert not tr.padrao and tr.tentativas == 2 and tr.pessoas_recusadas == ("she", "smiles")
    assert tr.rejeitados[0].startswith("She smiles")
    assert [c["seed"] for c in fn.chamadas] == [7, 107]


def test_after_every_attempt_invents_someone_the_default_motion_without_people_wins():
    fn = _compilador("A woman lifts the cup.", "His hand holds the cup.", "The barista smiles.")
    tr = t.traduzir("o vapor sobe da xícara", "livre", receitas=REC, compile_fn=fn)
    assert tr.padrao and not tr.vago and tr.tentativas == t.TENTATIVAS == len(fn.chamadas)
    assert tr.prompt == t.prompt_padrao(REC, "livre", "geral", sem_pessoas=True)
    assert set(tr.pessoas_recusadas) == {"woman", "his", "hand", "barista", "smiles"}
    assert len(tr.rejeitados) == 3


def test_people_stay_when_the_client_asks_for_them_or_the_photo_has_them():
    pessoa = "The young woman smiles and waves at the camera."
    tr = t.traduzir("a moça sorri e acena", "livre", receitas=REC, compile_fn=_compilador(pessoa))
    assert tr.prompt.startswith(pessoa) and not tr.sem_pessoas
    tr = t.traduzir(
        "o vapor sobe da xícara", "livre", receitas=REC, sem_pessoas=False,
        compile_fn=_compilador("The barista smiles as the cup steams."),
    )  # fmt: skip
    assert tr.prompt.startswith("The barista smiles") and not tr.padrao


def test_refused_and_compiler_errors_go_up():
    with pytest.raises(compiler.CompilerRefused):
        t.traduzir("deixa bonito", "livre", receitas=REC, compile_fn=_compilador("REFUSED"))

    def quebrado(text, **kw):
        raise compiler.CompilerError("Ollama inacessível")

    with pytest.raises(compiler.CompilerError):
        t.traduzir("o produto gira", "livre", receitas=REC, compile_fn=quebrado)
    with pytest.raises(t.TraducaoError):
        t.traduzir("o produto gira", "nao-existe", receitas=REC, compile_fn=_compilador("x"))


def test_the_no_people_rule_from_the_server_analysis():
    com = {"versao": "pessoas-v1", "pessoas": 1, "rostos": 0, "maior": 0.2}
    so_rosto = {"versao": "pessoas-v1", "pessoas": 0, "rostos": 2, "maior": 0.0}
    sem = {"versao": "pessoas-v1", "pessoas": 0, "rostos": 0, "maior": 0.0}
    erro = {"versao": "pessoas-v1", "erro": "detector indisponível"}
    assert t.tem_pessoas(com) and t.tem_pessoas(so_rosto)
    assert not t.tem_pessoas(sem) and not t.tem_pessoas(erro) and not t.tem_pessoas(None)
    assert not t.tem_pessoas("lixo") and not t.tem_pessoas({"pessoas": "x"})
    assert not t.regra_sem_pessoas("deixa bonito", [com, so_rosto])  # todas com gente
    assert t.regra_sem_pessoas("deixa bonito", [com, sem])  # uma sem: vale para o prompt de todas
    assert t.regra_sem_pessoas("deixa bonito", [com, None])  # sem análise: na dúvida, sem pessoas
    assert t.regra_sem_pessoas("deixa bonito", [])
    assert not t.regra_sem_pessoas("a moça sorri", [sem])  # o cliente pede alguém


def test_the_compiler_vague_answer_becomes_the_generic_motion(fake_ollama):
    fake_ollama.response = "VAGUE"
    r = compiler.compile_prompt("deixa bonito", ollama_url=fake_ollama.url, suffix="Slow orbit.")
    assert r.vague and r.compiled.startswith("The main subject stays in place")
    assert r.prompt.endswith("Slow orbit.")
    fake_ollama.response = (
        "Vague shapes of the shelf glow softly."  # "Vague" no texto não é a resposta
    )
    assert not compiler.compile_prompt("x", ollama_url=fake_ollama.url).vague
    assert compiler.fill_subject("{assunto} rotates. then {assunto} stops.", "the shoe") == (
        "The shoe rotates. Then the shoe stops."
    )
