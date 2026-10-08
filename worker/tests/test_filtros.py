import pytest

from gpu2go_worker import filtros, safety


@pytest.mark.parametrize(
    "texto",
    [
        "a câmera se aproxima devagar do produto",
        "vapor sobe do prato quente, câmera parada",
        "a pessoa sorri e acena para a câmera",
        "bolhas sobem no copo de refrigerante",
        "o bolo de chocolate gira na mesa",
        "The camera slowly dollies in toward the product.",
        # palavras comuns em marketing que a v2 não pode bloquear
        "um bolo gostoso de chocolate",
        "a sedução do chocolate amargo",
        "vestido com decote em V gira na vitrine",
        "o chef tira a tampa da panela",
        "o garçom despeja o café na xícara",
        "a família sorri e acena",
    ],
)
def test_sfw_marketing_requests_pass(texto):
    assert filtros.avaliar(texto).allowed


@pytest.mark.parametrize(
    "texto,categoria",
    [
        ("modelo de lingerie na vitrine", "sexual"),
        ("uma criança brinca com o brinquedo", "menores"),
        ("CRIANÇA", "menores"),
        ("um homem segura uma arma", "armas"),
        ("sangue escorrendo", "violencia"),
        ("deepfake do apresentador", "pessoa_real"),
        ("p e l a d o", "sexual"),  # letras separadas
        ("s3xy", "sexual"),  # número no lugar de letra
        ("Niños jugando", "menores"),
        # v2: despir, roupa íntima e movimentos sexualizados
        ("tire a roupa dela", "sexual"),
        ("deixe ela sem roupa", "sexual"),
        ("s e m   r o u p a", "sexual"),  # expressão com as letras separadas
        ("ela tira o biquíni devagar", "sexual"),
        ("ela rebolando na praia", "sexual"),
        ("she takes off her clothes", "sexual"),
        ("make her undress", "sexual"),
        ("remove her bikini", "sexual"),
        ("close no sutiã", "sexual"),
    ],
)
def test_blocked_terms_with_disguises(texto, categoria):
    d = filtros.avaliar(texto)
    assert not d.allowed and categoria in d.reason


def test_whole_words_only():
    assert filtros.avaliar("armário de cozinha").allowed  # "arma" dentro de outra palavra
    assert filtros.avaliar("o kit de nutrição").allowed  # "nu" dentro de outra palavra


def test_safety_mode_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("GPU2GO_SAFETY", "c1-revisao-humana")
    f = safety.load_filters()
    assert f.mode == "c1-revisao-humana" and f.exige_aprovacao
    assert not f.check_request("modelo de lingerie").allowed
    monkeypatch.setenv("GPU2GO_SAFETY", "")
    with pytest.raises(safety.SafetyNotConfigured):
        safety.load_filters()
