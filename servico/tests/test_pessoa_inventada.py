"""Pessoa inventada no prompt (pedido do dono, 13/10/2026): com a foto sem pessoas (ou sem a análise) e o
texto do cliente sem ninguém, nenhum vídeo sai com uma pessoa no prompt. O pod traduz de novo
(render2go_worker.traducao); a API confere o que chega e, na hora de cada vídeo ir para o pod, de novo."""

import json

from render2go_worker import lote, traducao
from test_api import ADM, PAINEL, WORKER, _cliente, _enviar_tudo, _job, _lote, api, h  # noqa: F401 (fixture api)

PESSOA = "She stays in place while soft light glides across her. Static camera."


def _aprovado(a, n=2, **pedido):
    tok = _cliente(a)
    r = _lote(a, tok, n=n, **pedido)
    assert r.status_code == 200, r.text
    lid = r.json()["lote"]
    assert _enviar_tudo(a, tok, lid, r.json()["envios"]).status_code == 200
    return tok, lid


def _aprovar(a, lid):
    assert a.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM).status_code == 200


def _prompt(a, job, texto, **extra):
    return a.post(
        f"/api/worker/jobs/{job['job_id']}/prompt",
        headers=h(WORKER),
        json={"worker": "pod-1", "prompt_en": texto, "compilador": "i2v@7", **extra},
    )


def _concluir_ok(a, job):
    corpo = {"worker": "pod-1", "status": "ok"}
    r = a.post(f"/api/worker/jobs/{job['job_id']}/concluir", headers=h(WORKER), json=corpo)
    assert r.status_code == 200, r.text


def _eventos(a, lid, tipo):
    with a.banco.tx() as c:
        return [
            json.loads(e["detalhe"]) for e in c.all("SELECT * FROM eventos WHERE lote_id = ? AND tipo = ?", (lid, tipo))
        ]


def _com_pessoas(a, lid):
    for f in a.banco.fotos(lid):
        a.banco.salvar_analise(f["id"], {"versao": "pessoas-v1", "pessoas": 1, "rostos": 1, "maior": 0.4})


def test_the_translation_job_carries_the_photo_category_and_the_no_people_rule(api):  # noqa: F811 (fixture)
    _, lid = _aprovado(api, receita="produto-giro", prompt="o produto gira devagar")
    _aprovar(api, lid)
    comp = _job(api)
    assert comp["tipo"] == "compilar"
    # sem a análise das fotos: na dúvida, foto sem pessoas
    assert comp["regras"] == {"sem_pessoas": True, "categoria": "produto"}


def test_a_prompt_that_invents_a_person_becomes_the_default_motion_without_anyone(api):  # noqa: F811 (fixture)
    _, lid = _aprovado(api, receita="livre", prompt="deixa bonito")
    _aprovar(api, lid)
    comp = _job(api)
    r = _prompt(api, comp, PESSOA, pessoas_recusadas=["she"], tentativas=3)
    assert r.status_code == 200 and r.json()["padrao"] is True
    lt = api.banco.lote(lid)
    padrao = traducao.prompt_padrao(lote.load_receitas(), "livre", "geral", sem_pessoas=True)
    assert lt["prompt_en"] == padrao and not traducao.prompt_cita_pessoa(lt["prompt_en"])
    (ev,) = _eventos(api, lid, "prompt_pessoa_inventada")
    assert ev["onde"] == "traducao" and ev["palavras"] == ["her", "she"] and ev["prompt"] == PESSOA
    (pr,) = _eventos(api, lid, "prompt")
    assert pr["padrao"] is True and pr["pessoas_recusadas"] == ["she"] and pr["tentativas"] == 3
    _concluir_ok(api, comp)
    render = _job(api)
    assert render["tipo"] == "render" and render["prompt_en"] == padrao


def test_people_are_kept_when_every_photo_has_people_or_the_client_asks_for_someone(api):  # noqa: F811 (fixture)
    _, lid = _aprovado(api, receita="livre", prompt="deixa bonito")
    _com_pessoas(api, lid)  # o aviso de pessoas achou gente nas duas fotos
    _aprovar(api, lid)
    comp = _job(api)
    assert comp["regras"]["sem_pessoas"] is False
    assert _prompt(api, comp, PESSOA).json()["padrao"] is False
    assert api.banco.lote(lid)["prompt_en"] == PESSOA
    _concluir_ok(api, comp)
    assert _job(api)["prompt_en"] == PESSOA

    # foto sem pessoas, mas o cliente pede alguém na cena: a regra não vale
    _, lid2 = _aprovado(api, receita="livre", prompt="a moça sorri e acena para a câmera")
    _aprovar(api, lid2)
    comp = _job(api)
    assert comp["regras"]["sem_pessoas"] is False
    texto = "A young woman smiles and waves at the camera. Static camera."
    assert _prompt(api, comp, texto).json()["padrao"] is False
    assert not _eventos(api, lid2, "prompt_pessoa_inventada")


def test_one_photo_without_people_is_enough_for_the_rule(api):  # noqa: F811 (fixture)
    _, lid = _aprovado(api, receita="livre", prompt="deixa bonito", n=2)
    fotos = api.banco.fotos(lid)
    api.banco.salvar_analise(fotos[0]["id"], {"versao": "pessoas-v1", "pessoas": 2, "rostos": 1, "maior": 0.3})
    api.banco.salvar_analise(fotos[1]["id"], {"versao": "pessoas-v1", "pessoas": 0, "rostos": 0, "maior": 0.0})
    _aprovar(api, lid)
    assert _job(api)["regras"]["sem_pessoas"] is True  # o mesmo prompt vale para as duas fotos


def test_the_last_barrier_replaces_a_person_prompt_translated_before_the_rule(api):  # noqa: F811 (fixture)
    """Pedido traduzido antes desta regra (o prompt antigo, com "She..."): na hora do vídeo ir para o pod, a
    foto sem pessoas recebe o movimento padrão."""
    _, lid = _aprovado(api, receita="livre", prompt="quero que fique bonito e profissional pra eu vender", n=2)
    fotos = api.banco.fotos(lid)
    api.banco.salvar_analise(fotos[0]["id"], {"versao": "pessoas-v1", "pessoas": 1, "rostos": 1, "maior": 0.4})
    _aprovar(api, lid)
    comp = _job(api)
    _concluir_ok(api, comp)
    with api.banco.tx() as c:  # o prompt de antes, gravado direto (sem passar pela regra da API), num pedido de antes
        # das versões (ADR 0018: com versões, o prompt que não confere com a versão não vai para o pod; test_versoes.py)
        c.run("UPDATE lotes SET prompt_en = ?, status = 'processando', versao_atual = NULL WHERE id = ?", (PESSOA, lid))
    vistos = {}
    for _ in range(2):
        r = _job(api)
        vistos[r["arquivo"].rsplit("-v", 1)[0]] = r["prompt_en"]
    com, sem = (fotos[0]["nome"], fotos[1]["nome"])
    assert vistos[com] == PESSOA  # a foto com pessoas fica com o prompt
    assert not traducao.prompt_cita_pessoa(vistos[sem])  # a sem pessoas, não
    (ev,) = _eventos(api, lid, "prompt_pessoa_inventada")
    assert ev["onde"] == "render" and ev["foto"] == sem


def test_a_photo_with_its_own_text_follows_the_rule_of_the_photos_with_that_text(api):  # noqa: F811 (fixture)
    tok = _cliente(api)
    corpo = {
        "prompt": "o produto gira devagar",
        "receita": "livre",
        "fotos": [
            {"nome": "a.jpg", "tipo": "image/jpeg", "bytes": 1000},
            {"nome": "b.jpg", "tipo": "image/jpeg", "bytes": 1001, "prompt": "o barbeiro corta o cabelo"},
        ],
    }
    r = api.post("/api/lotes", json=corpo, headers=h(tok))
    assert r.status_code == 200, r.text
    lid = r.json()["lote"]
    assert _enviar_tudo(api, tok, lid, r.json()["envios"]).status_code == 200
    _aprovar(api, lid)
    regras = {}
    while len(regras) < 2:  # a tradução do texto próprio entra na vez da foto, depois do vídeo da "a"
        comp = _job(api)
        if comp["tipo"] != "compilar":
            continue
        regras["foto" if comp["pedido"]["prompt"].startswith("o barbeiro") else "pedido"] = comp["regras"]
        _prompt(api, comp, "The product slowly rotates. Static camera.")
        _concluir_ok(api, comp)
    assert regras == {
        "pedido": {"sem_pessoas": True, "categoria": "geral"},
        "foto": {"sem_pessoas": False, "categoria": "geral"},  # o texto próprio pede alguém (barbeiro)
    }
