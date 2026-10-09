"""Contas por e-mail (ADR 0007): domínios aceitos, código no e-mail, sessão em cookie HttpOnly, limites,
"não sou um robô", exclusão e cópia dos dados (LGPD)."""

import re

import pytest
from test_api import ADM, PAINEL, Armazem, _cliente, anonimo, cliente_teste, config_teste, h

from render2go_servico import contas, suporte

D1 = "11111111-0000-4000-8000-000000000000"
PAGINA = {"X-Render2Go": "1", "X-Dispositivo": D1}  # o que a página manda em todo pedido


@pytest.fixture
def conta_api(banco):
    enviados = []
    cfg = config_teste(
        banco,
        Armazem(),
        email=suporte.ConfigEmail(host="smtp.teste", para="", de="Render2Go <nao-responda@render2go.com>"),
        enviar_email=lambda cfg_, msg: enviados.append(msg),
    )
    api = cliente_teste(cfg)
    api.enviados, api.cfg = enviados, cfg
    return api


def _codigo(api) -> str:
    """O código do último e-mail enviado (o assunto começa por ele)."""
    api.cfg.esperar_fundo()
    return re.match(r"(\d{8}) ", api.enviados[-1]["Subject"]).group(1)


def entrar(api, email="fulana.silva@gmail.com"):
    """Entra como um navegador novo (sem cookie): pede o código, lê o e-mail e entra."""
    nav = anonimo(api)
    r = nav.post("/api/conta/codigo", json={"email": email, "aceite": True}, headers=PAGINA)
    assert r.status_code == 200, r.text
    r = nav.post("/api/conta/entrar", json={"email": email, "codigo": _codigo(api), "aceite": True}, headers=PAGINA)
    assert r.status_code == 200, r.text
    nav.conta = r.json()
    return nav


def test_email_rules_join_the_aliases_of_the_same_inbox():
    n = contas.normalizar
    assert n("Fulana.Silva+promo@GMAIL.com") == ("fulana.silva+promo@gmail.com", "fulanasilva@gmail.com")
    assert n("fulanasilva@googlemail.com")[1] == "fulanasilva@gmail.com"  # o mesmo Gmail
    assert n("dono+loja@outlook.com")[1] == "dono@outlook.com"  # "+" no Outlook, pontos valem
    assert n("d.ono@hotmail.com")[1] == "d.ono@hotmail.com"
    assert n("padaria-promo@yahoo.com.br")[1] == "padaria@yahoo.com.br"  # endereço descartável do Yahoo
    for ruim in ("sem-arroba", "a@b@gmail.com", ".comeca@gmail.com", "dois..pontos@gmail.com", "x" * 70 + "@gmail.com"):
        with pytest.raises(contas.EmailRecusado):
            n(ruim)
    with pytest.raises(contas.EmailRecusado):  # letra "а" cirílica no domínio (parece gmail.com)
        n("fulana@gmаil.com")
    for fora in ("x@mailinator.com", "x@protonmail.com", "x@minhaempresa.com.br", "x@gmail.com.br"):
        with pytest.raises(contas.DominioRecusado):
            n(fora)
    assert n("dono@minhaempresa.com.br", exigir_dominio=False)[1] == "dono@minhaempresa.com.br"


def test_first_access_creates_a_free_trial_account_with_the_code_from_the_email(conta_api):
    api = conta_api
    nav = anonimo(api)
    pub = nav.get("/api/publico").json()
    assert pub["entrada_email"] is True and "gmail.com" in pub["dominios"] and pub["turnstile"] is None
    corpo = {"email": "Fulana.Silva@gmail.com", "aceite": True}
    assert nav.post("/api/conta/codigo", json={**corpo, "aceite": False}, headers=PAGINA).status_code == 422
    r = nav.post("/api/conta/codigo", json={**corpo, "email": "x@mailinator.com"}, headers=PAGINA)
    assert r.status_code == 422 and "Gmail" in r.json()["detail"]
    r = nav.post("/api/conta/codigo", json=corpo, headers=PAGINA)
    assert r.status_code == 200 and r.json()["email"] == "fulana.silva@gmail.com"
    codigo = _codigo(api)
    msg = api.enviados[-1]
    assert msg["To"] == "fulana.silva@gmail.com" and "nao-responda@render2go.com" in msg["From"]
    texto = msg.get_content()
    assert codigo in texto and "10 minutos" in texto and "http" not in texto  # sem links no e-mail

    errado = {"email": "fulana.silva@gmail.com", "codigo": "00000000", "aceite": True}
    assert nav.post("/api/conta/entrar", json=errado, headers=PAGINA).status_code == 401
    certo = {**errado, "codigo": f"{codigo[:4]} {codigo[4:]}"}  # com espaço, como a pessoa digita
    r = nav.post("/api/conta/entrar", json=certo, headers=PAGINA)
    assert r.status_code == 200 and r.json()["nova"] is True
    assert r.json()["conta"]["plano"] == "teste" and r.json()["conta"]["teste"]["usado"] is False
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-render2go=")
    for atributo in ("HttpOnly", "Secure", "Path=/", "SameSite=lax", "Max-Age=2592000"):
        assert atributo in cookie, atributo
    assert "Domain" not in cookie
    conta = nav.get("/api/conta", headers=PAGINA).json()
    assert conta["email"] == "fulana.silva@gmail.com" and conta["plano"] == "teste"
    assert nav.get("/api/receitas").json()["plano"]["nome"] == "teste"
    # o código vale uma vez só
    assert anonimo(api).post("/api/conta/entrar", json=certo, headers=PAGINA).status_code == 401

    # o mesmo Gmail com pontos ou "+": a mesma conta (um teste grátis por caixa de e-mail)
    outro = entrar(api, "fulanasilva+qualquer@gmail.com")
    assert outro.conta["nova"] is False and outro.conta["conta"]["id"] == conta["id"]
    with api.banco.tx() as c:
        aceites = c.all("SELECT documento, versao, ip FROM aceites WHERE cliente_id = ?", (conta["id"],))
        acessos = c.all("SELECT evento, ip FROM acessos WHERE cliente_id = ?", (conta["id"],))
    assert sorted(a["documento"] for a in aceites) == ["privacidade", "termos"]  # uma vez por versão
    assert {a["versao"] for a in aceites} == {"termos-v1", "privacidade-v1"}
    assert [a["evento"] for a in acessos] == ["entrada", "entrada"] and acessos[0]["ip"] == "testclient"


def test_wrong_codes_and_too_many_requests_are_stopped(conta_api):
    api = conta_api
    nav = anonimo(api)
    email = "tentativas@outlook.com"
    assert nav.post("/api/conta/codigo", json={"email": email, "aceite": True}, headers=PAGINA).status_code == 200
    codigo = _codigo(api)
    for _ in range(5):  # 5 erros: o código deixa de valer, mesmo o certo depois
        r = nav.post("/api/conta/entrar", json={"email": email, "codigo": "12345678", "aceite": True}, headers=PAGINA)
        assert r.status_code == 401
    r = nav.post("/api/conta/entrar", json={"email": email, "codigo": codigo, "aceite": True}, headers=PAGINA)
    assert r.status_code == 401
    # códigos pedidos para o mesmo e-mail: 5 por hora
    for _ in range(4):
        assert nav.post("/api/conta/codigo", json={"email": email, "aceite": True}, headers=PAGINA).status_code == 200
    r = nav.post("/api/conta/codigo", json={"email": email, "aceite": True}, headers=PAGINA)
    assert r.status_code == 429 and "uma hora" in r.json()["detail"]
    # código novo invalida o anterior
    outra = "nova.pessoa@icloud.com"
    nav.post("/api/conta/codigo", json={"email": outra, "aceite": True}, headers=PAGINA)
    primeiro = _codigo(api)
    nav.post("/api/conta/codigo", json={"email": outra, "aceite": True}, headers=PAGINA)
    segundo = _codigo(api)
    corpo = {"email": outra, "aceite": True}
    assert nav.post("/api/conta/entrar", json={**corpo, "codigo": primeiro}, headers=PAGINA).status_code == 401
    assert nav.post("/api/conta/entrar", json={**corpo, "codigo": segundo}, headers=PAGINA).status_code == 200


def test_robot_check_is_required_and_fails_closed(banco, monkeypatch):
    email = suporte.ConfigEmail(host="smtp.teste", para="", de="x@render2go.com")
    enviados = []
    sem_chave = cliente_teste(
        config_teste(banco, email=email, enviar_email=lambda c, m: enviados.append(m), turnstile_desligado=False),
        admin=False,
    )
    corpo = {"email": "robo@gmail.com", "aceite": True}
    r = sem_chave.post("/api/conta/codigo", json=corpo, headers=PAGINA)
    assert r.status_code == 503 and "anti-robô" in r.json()["detail"] and not enviados

    def conferir(cfg, resposta, ip, acao=None):
        return None if resposta == "ok-do-turnstile" and acao == "codigo" else "verificação anti-robô recusada"

    monkeypatch.setattr(contas, "turnstile_confere", conferir)
    cfg = config_teste(
        banco,
        email=email,
        enviar_email=lambda c, m: enviados.append(m),
        turnstile_desligado=False,
        turnstile=contas.ConfigTurnstile("0x4AAAAAAA-site", "0x4AAAAAAA-segredo"),
    )
    api = cliente_teste(cfg, admin=False)
    assert api.get("/api/publico").json()["turnstile"] == "0x4AAAAAAA-site"
    assert api.post("/api/conta/codigo", json=corpo, headers=PAGINA).status_code == 403
    r = api.post("/api/conta/codigo", json={**corpo, "turnstile": "de-um-robo"}, headers=PAGINA)
    assert r.status_code == 403 and not enviados
    assert (
        api.post("/api/conta/codigo", json={**corpo, "turnstile": "ok-do-turnstile"}, headers=PAGINA).status_code == 200
    )
    cfg.esperar_fundo()
    assert len(enviados) == 1


def test_session_cookie_needs_the_page_header_and_leaving_revokes_it(conta_api):
    api = conta_api
    nav = entrar(api)
    corpo = {"finalidade": "texto-video"}
    # cookie sem o cabeçalho da página (um formulário de outro site, por exemplo): recusado
    assert nav.post("/api/interesses", json=corpo).status_code == 403
    r = nav.post("/api/interesses", json=corpo, headers={**PAGINA, "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403 and r.json()["detail"] == "pedido de outro site recusado"
    assert nav.post("/api/interesses", json=corpo, headers=PAGINA).status_code == 200
    # duas sessões; "sair de todos" derruba as duas
    outro = entrar(api)
    assert outro.get("/api/conta").status_code == 200
    r = nav.post("/api/conta/sair-de-todos", headers=PAGINA)
    assert r.status_code == 200 and r.json()["sessoes"] == 2
    assert nav.get("/api/conta").status_code == 401 and outro.get("/api/conta").status_code == 401
    # sair: a sessão termina e o cookie é apagado
    nav = entrar(api)
    r = nav.post("/api/conta/sair", headers=PAGINA)
    assert r.status_code == 200 and "Max-Age=0" in r.headers["set-cookie"]
    assert nav.get("/api/conta").status_code == 401


def test_a_disabled_account_drops_its_sessions_and_gets_no_codes(conta_api):
    api = conta_api
    nav = entrar(api, "bloqueada@yahoo.com.br")
    cid = nav.conta["conta"]["id"]
    assert api.post(f"{PAINEL}/api/clientes/{cid}/desativar", headers=ADM).status_code == 200
    r = nav.get("/api/conta")
    assert r.status_code == 401 and "Max-Age=0" in r.headers.get("set-cookie", "")  # o cookie vencido sai
    antes = len(api.enviados)
    r = anonimo(api).post("/api/conta/codigo", json={"email": "bloqueada@yahoo.com.br", "aceite": True}, headers=PAGINA)
    assert r.status_code == 200  # a resposta não revela nada...
    api.cfg.esperar_fundo()
    assert len(api.enviados) == antes  # ...mas nenhum código sai


def test_copy_of_the_data_and_account_deletion_without_a_second_free_trial(conta_api):
    api = conta_api
    nav = entrar(api, "apagar.me@gmail.com")
    cid = nav.conta["conta"]["id"]
    dados = nav.get("/api/conta/dados")
    assert dados.status_code == 200 and "attachment" in dados.headers["content-disposition"]
    d = dados.json()
    assert d["conta"]["email"] == "apagar.me@gmail.com" and {a["documento"] for a in d["aceites"]} == {
        "termos",
        "privacidade",
    }
    assert d["acessos"][0]["evento"] in ("entrada", "dados_exportados") and "codigo" not in str(d["conta"])
    # com saldo, o reembolso vem antes
    api.post(f"{PAINEL}/api/clientes/{cid}/plano", json={"plano": "pago"}, headers=ADM)
    api.post(f"{PAINEL}/api/clientes/{cid}/saldo", json={"valor": "5", "descricao": "recarga"}, headers=ADM)
    assert nav.post("/api/conta/excluir", json={"confirmacao": "sim"}, headers=PAGINA).status_code == 422
    r = nav.post("/api/conta/excluir", json={"confirmacao": "EXCLUIR"}, headers=PAGINA)
    assert r.status_code == 409 and "saldo" in r.json()["detail"]
    api.post(f"{PAINEL}/api/clientes/{cid}/saldo", json={"valor": "-5", "descricao": "reembolso"}, headers=ADM)
    r = nav.post("/api/conta/excluir", json={"confirmacao": "EXCLUIR"}, headers=PAGINA)
    assert r.status_code == 200 and "Max-Age=0" in r.headers["set-cookie"]
    assert nav.get("/api/conta").status_code == 401
    with api.banco.tx() as c:
        k = c.one("SELECT email, email_norm, ativo, nome, email_resumo FROM clientes WHERE id = ?", (cid,))
        movimentos = c.one("SELECT COUNT(*) AS n FROM movimentos WHERE cliente_id = ?", (cid,))["n"]
    assert (k["email"], k["email_norm"], k["ativo"], k["nome"]) == (None, None, 0, "conta excluída")
    assert k["email_resumo"] and "apagar" not in k["email_resumo"] and movimentos == 2  # o extrato fica
    # o mesmo e-mail pode voltar, mas sem outro teste grátis
    volta = entrar(api, "apagar.me@gmail.com")
    assert volta.conta["nova"] is True and volta.conta["conta"]["plano"] == "pago"


def test_old_access_code_logs_in_with_a_cookie_session(conta_api):
    api = conta_api
    token = _cliente(api, "Cliente antigo", saldo="10")
    nav = anonimo(api)
    assert nav.post("/api/conta/entrar-codigo", json={"codigo": token}, headers=PAGINA).status_code == 422  # sem aceite
    r = nav.post("/api/conta/entrar-codigo", json={"codigo": "x" * 43, "aceite": True}, headers=PAGINA)
    assert r.status_code == 401
    r = nav.post("/api/conta/entrar-codigo", json={"codigo": token, "aceite": True}, headers=PAGINA)
    assert r.status_code == 200 and r.headers["set-cookie"].startswith("__Host-render2go=")
    assert nav.get("/api/saldo").json()["saldo"] == 1000
    # muitas tentativas erradas da mesma rede: bloqueado por um tempo
    for _ in range(20):
        anonimo(api).post("/api/conta/entrar-codigo", json={"codigo": "y" * 43, "aceite": True}, headers=PAGINA)
    r = anonimo(api).post("/api/conta/entrar-codigo", json={"codigo": token, "aceite": True}, headers=PAGINA)
    assert r.status_code == 429


def test_company_email_only_when_the_panel_creates_the_account(conta_api):
    api = conta_api
    corpo = {"email": "dono@padariadobairro.com.br", "aceite": True}
    assert anonimo(api).post("/api/conta/codigo", json=corpo, headers=PAGINA).status_code == 422
    r = api.post(f"{PAINEL}/api/clientes", json={"email": "Dono@PadariaDoBairro.com.br", "plano": "pago"}, headers=ADM)
    assert r.status_code == 200 and r.json()["email"] == "dono@padariadobairro.com.br"
    assert (
        api.post(f"{PAINEL}/api/clientes", json={"email": "dono@padariadobairro.com.br"}, headers=ADM).status_code
        == 422
    )
    nav = entrar(api, "dono@padariadobairro.com.br")
    assert nav.conta["nova"] is False and nav.conta["conta"]["plano"] == "pago"


def test_legacy_bearer_code_still_works_for_integrations(conta_api):
    tok = _cliente(conta_api, "Integração")
    assert conta_api.get("/api/lotes", headers=h(tok)).status_code == 200
    assert conta_api.get("/api/lotes", headers=h("z" * 43)).json()["detail"] == "token inválido"
