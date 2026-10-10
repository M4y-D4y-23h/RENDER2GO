"""Acessos do painel por pessoa (ADR 0009): convite de uso único (QR Code do aplicativo → um código dele → a
senha), entrada com nome de acesso + senha + código (cada código uma vez), papéis com o mapa de permissões por
rota, bloqueios, redefinição e desativação na hora, a chave do servidor como emergência, auditoria completa
(filtros, página seguinte, planilha, antes e depois) e a visão geral."""

import pytest
from fastapi.testclient import TestClient
from test_api import ADM, ADMIN, PAINEL, TOTP, _cliente, _enviar_tudo, _lote, anonimo, config_teste, h
from test_conta_config import Relogio

from render2go_servico import api as api_mod
from render2go_servico import cli, contas
from render2go_servico.api import PERMISSOES_PAINEL, criar_app

SENHA = "cafe com pao de queijo 7"
SEM_SESSAO = {"", "/", "/painel.js", "/api/entrar", "/api/sair", "/api/sessao", "/api/convite/iniciar",
              "/api/convite/confirmar", "/api/convite/senha"}  # fmt: skip


@pytest.fixture
def relogio(monkeypatch):
    r = Relogio()
    monkeypatch.setattr(contas, "time", type("T", (), {"time": staticmethod(r.time)}))
    return r


@pytest.fixture
def painel(banco, relogio):
    """O dono entrou com a chave do servidor (emergência), com o relógio dos testes."""
    cfg = config_teste(banco)
    api = TestClient(criar_app(cfg), base_url="https://testserver")
    api.armazem, api.banco, api.cfg, api.relogio = cfg.armazenamento, banco, cfg, relogio
    r = api.post(f"{PAINEL}/api/entrar", json={"token": ADMIN, "totp": contas.totp(TOTP, relogio.passo())}, headers=ADM)
    assert r.status_code == 200, r.text
    return api


def convidar(api, usuario="maria.revisora", papel="operador") -> str:
    r = api.post(f"{PAINEL}/api/acessos/convites", json={"usuario": usuario, "papel": papel}, headers=ADM)
    assert r.status_code == 200, r.text
    return r.json()["endereco"].split("#convite=")[1]


def aceitar(api, token, senha=SENHA, ip="198.51.100.20") -> tuple[TestClient, str]:
    """A pessoa convidada: lê o QR Code, confirma um código (do passo anterior: o atual e o seguinte ficam livres)
    e cria a senha. Devolve o navegador dela (já com a sessão) e o segredo do aplicativo."""
    nav = anonimo(api)
    nav.headers.update({**ADM, "X-Render2Go-IP": ip, "User-Agent": "Firefox/143.0 (convidada)"})
    r = nav.post(f"{PAINEL}/api/convite/iniciar", json={"convite": token})
    assert r.status_code == 200, r.text
    segredo = r.json()["segredo"]
    r = nav.post(f"{PAINEL}/api/convite/confirmar", json={"convite": token, "codigo": totp(api, segredo, -1)})
    assert r.status_code == 200, r.text
    r = nav.post(f"{PAINEL}/api/convite/senha", json={"convite": token, "senha": senha})
    assert r.status_code == 200, r.text
    return nav, segredo


def totp(api, segredo, delta=0) -> str:
    return contas.totp(segredo, api.relogio.passo() + delta)


def entrar(api, usuario, senha, codigo, ip="203.0.113.30"):
    nav = anonimo(api)
    nav.headers.update({**ADM, "X-Render2Go-IP": ip})
    return nav, nav.post(f"{PAINEL}/api/entrar", json={"usuario": usuario, "senha": senha, "totp": codigo})


def test_every_panel_route_with_a_session_is_in_the_permission_map(banco):
    app = criar_app(config_teste(banco))
    rotas = {
        (m, r.path[len(PAINEL) :]) for r in app.routes if r.path.startswith(PAINEL) for m in getattr(r, "methods", ())
    }
    com_sessao = {(m, p) for m, p in rotas if p not in SEM_SESSAO}
    assert com_sessao == set(PERMISSOES_PAINEL), "rota do painel sem papel definido (ou papel de rota que não existe)"
    for papeis in PERMISSOES_PAINEL.values():
        assert papeis and papeis <= {"dono", "operador"}


def test_an_invite_becomes_a_personal_access_qr_code_then_app_code_then_password(painel):
    api = painel
    for ruim in ("ab", "admin-1a2b3c4d", "ma ria", "1maria", "maria@x", "á" * 5):
        r = api.post(f"{PAINEL}/api/acessos/convites", json={"usuario": ruim, "papel": "operador"}, headers=ADM)
        assert r.status_code == 422, ruim
    assert (
        api.post(f"{PAINEL}/api/acessos/convites", json={"usuario": "maria", "papel": "rei"}, headers=ADM).status_code
        == 422
    )
    r = api.post(f"{PAINEL}/api/acessos/convites", json={"usuario": "Maria.Revisora", "papel": "operador"}, headers=ADM)
    assert r.json()["usuario"] == "maria.revisora" and r.json()["endereco"].startswith(f"{PAINEL}/#convite=")
    token = r.json()["endereco"].split("#convite=")[1]
    (pendente,) = api.get(f"{PAINEL}/api/acessos").json()["convites"]
    assert pendente["usuario"] == "maria.revisora" and token not in str(pendente)

    nav = anonimo(api)
    nav.headers.update(ADM)
    assert nav.post(f"{PAINEL}/api/convite/iniciar", json={"convite": "x" * 43}).status_code == 401
    r = nav.post(f"{PAINEL}/api/convite/iniciar", json={"convite": token})
    segredo = r.json()["segredo"]
    assert r.json()["usuario"] == "maria.revisora" and r.json()["papel"] == "operador"
    assert r.json()["uri"].startswith("otpauth://totp/Render2Go%3Apainel%3Amaria.revisora?secret=")
    assert r.json()["qr_svg"].startswith("<svg")
    # a senha só depois de um código do aplicativo conferir
    assert nav.post(f"{PAINEL}/api/convite/senha", json={"convite": token, "senha": SENHA}).status_code == 409
    assert nav.post(f"{PAINEL}/api/convite/confirmar", json={"convite": token, "codigo": "000000"}).status_code == 401
    r = nav.post(f"{PAINEL}/api/convite/confirmar", json={"convite": token, "codigo": totp(api, segredo, -1)})
    assert r.status_code == 200
    for fraca in ("curta", "maria.revisora e mais", "aaaaaaaaaaaaaaa", "minha senha 123456"):
        r = nav.post(f"{PAINEL}/api/convite/senha", json={"convite": token, "senha": fraca})
        assert r.status_code == 422, fraca
    r = nav.post(f"{PAINEL}/api/convite/senha", json={"convite": token, "senha": SENHA})
    assert r.status_code == 200 and r.json() == {"ok": True, "admin": "maria.revisora", "papel": "operador"}
    assert r.headers["set-cookie"].startswith("__Secure-render2go-painel=")
    sessao = nav.get(f"{PAINEL}/api/sessao").json()
    assert (sessao["admin"], sessao["papel"], sessao["emergencia"]) == ("maria.revisora", "operador", False)
    # o convite vale uma vez só (também para quem copiou o endereço)
    for passo in ("iniciar", "confirmar", "senha"):
        r = anonimo(api).post(f"{PAINEL}/api/convite/{passo}", json={"convite": token, "codigo": "1", "senha": SENHA},
                              headers=ADM)  # fmt: skip
        assert r.status_code == 401, passo
    assert api.get(f"{PAINEL}/api/acessos").json()["convites"] == []
    with api.banco.tx() as c:
        a = c.one("SELECT senha_hash, totp_segredo FROM admins WHERE usuario = 'maria.revisora'")
    assert a["senha_hash"].startswith("scrypt$32768$8$1$") and SENHA not in a["senha_hash"]
    assert a["totp_segredo"].startswith("v1:") and segredo not in a["totp_segredo"]
    # entrar: o código usado no convite não vale de novo; o seguinte, sim
    _, r = entrar(api, "maria.revisora", SENHA, totp(api, segredo, -1))
    assert r.status_code == 401
    outra, r = entrar(api, "Maria.Revisora", SENHA, totp(api, segredo))
    assert r.status_code == 200 and outra.get(f"{PAINEL}/api/sessao").json()["admin"] == "maria.revisora"
    _, r = entrar(api, "maria.revisora", SENHA, totp(api, segredo))  # o mesmo código, de novo
    assert r.status_code == 401
    acoes = [(i["admin"], i["acao"]) for i in api.get(f"{PAINEL}/api/auditoria").json()["itens"]]
    for esperado in (("maria.revisora", "convite_aberto"), ("maria.revisora", "convite_usado"),
                     ("maria.revisora", "entrada"), ("?", "entrada_recusada")):  # fmt: skip
        assert esperado in acoes, esperado
    assert any(q.startswith("admin-") and a == "convite_criado" for q, a in acoes)


def test_an_operator_reviews_orders_and_consults_but_does_not_touch_money_pods_or_accesses(painel):
    api = painel
    operador, _ = aceitar(api, convidar(api, "op.um", "operador"))
    tok = _cliente(api, "Loja", saldo="100")
    lid = _lote(api, tok).json()["lote"]
    envios = api.get(f"/api/lotes/{lid}", headers=h(tok)).json()
    assert envios["status"] == "aguardando_envio"
    r = api.post("/api/lotes", json={"prompt": "a câmera se aproxima", "fotos": [{"nome": "a.jpg", "tipo": "image/jpeg",
                 "bytes": 1000}]}, headers=h(tok))  # fmt: skip
    _enviar_tudo(api, tok, r.json()["lote"], r.json()["envios"])
    for rota in ("/api/lotes", "/api/historico", "/api/suporte", "/api/interesses", "/api/pods", "/api/clientes"):
        assert operador.get(f"{PAINEL}{rota}").status_code == 200, rota
    resumo = operador.get(f"{PAINEL}/api/resumo").json()
    assert resumo["revisao"] == 1 and "cobrado_hoje" not in resumo and "avisos" not in resumo
    assert "cobrado_hoje" in api.get(f"{PAINEL}/api/resumo").json()  # o dono vê o dinheiro
    r = operador.post(f"{PAINEL}/api/lotes/{r.json()['lote']}/aprovar")
    assert r.status_code == 200
    assert api.banco.lote(r.json()["lote"])["aprovado_por"] == "op.um"
    cid = api.get(f"{PAINEL}/api/clientes").json()["clientes"][0]["id"]
    proibidas = [
        ("POST", f"/api/clientes/{cid}/saldo", {"valor": "10", "descricao": "presente"}),
        ("POST", f"/api/clientes/{cid}/plano", {"plano": "interno"}),
        ("POST", f"/api/clientes/{cid}/desativar", None),
        ("POST", "/api/clientes", {"email": "x@gmail.com"}),
        ("POST", "/api/pods/emergencia", None),
        ("GET", "/api/geradores", None),
        ("GET", "/api/pagamentos", None),
        ("GET", "/api/seguranca", None),
        ("GET", "/api/acessos", None),
        ("POST", "/api/acessos/convites", {"usuario": "intruso", "papel": "dono"}),
        ("GET", "/api/auditoria", None),
        ("GET", "/api/auditoria.csv", None),
    ]
    for metodo, rota, corpo in proibidas:
        r = operador.request(metodo, f"{PAINEL}{rota}", json=corpo)
        assert r.status_code == 403 and "papel" in r.json()["detail"], rota
    rotas = operador.get(f"{PAINEL}/api/sessao").json()["rotas"]
    assert "GET /api/lotes" in rotas and "GET /api/acessos" not in rotas


def test_login_errors_look_the_same_and_lock_the_name_and_the_network(painel):
    api = painel
    _, segredo = aceitar(api, convidar(api, "ana", "operador"))
    respostas = [
        entrar(api, "ana", "senha errada demais", totp(api, segredo), "203.0.113.1")[1],
        entrar(api, "ninguem", SENHA, totp(api, segredo), "203.0.113.2")[1],
        entrar(api, "ana", SENHA, "000000", "203.0.113.3")[1],
        entrar(api, "ana", SENHA, "", "203.0.113.4")[1],
    ]
    assert {r.status_code for r in respostas} == {401} and len({r.text for r in respostas}) == 1
    entrar(api, "ana", "outra senha errada", "123456", "203.0.113.5")
    entrar(api, "ana", "mais uma senha errada", "123456", "203.0.113.7")
    # 5 erros com o nome "ana", de redes diferentes: o nome fica bloqueado 15 min, mesmo com tudo certo
    r = entrar(api, "ana", SENHA, totp(api, segredo), "203.0.113.6")[1]
    assert r.status_code == 429
    api.relogio.agora += 30
    # outra pessoa, da mesma rede de quem errou: só o bloqueio da rede (5 erros da mesma rede)
    _, seg_b = aceitar(api, convidar(api, "beto", "operador"), ip="198.51.100.99")
    for i in range(5):  # nomes diferentes: só a rede acumula os 5 erros
        entrar(api, f"outro{i}", "errada errada errada", "111111", "203.0.113.77")
    assert entrar(api, "beto", SENHA, totp(api, seg_b), "203.0.113.77")[1].status_code == 429
    assert entrar(api, "beto", SENHA, totp(api, seg_b), "203.0.113.78")[1].status_code == 200


def test_a_new_invite_for_the_same_name_resets_password_and_authenticator_and_drops_sessions(painel):
    api = painel
    antigo, _ = aceitar(api, convidar(api, "carla", "operador"))
    assert antigo.get(f"{PAINEL}/api/lotes").status_code == 200
    token = convidar(api, "carla", "dono")  # refaz e promove
    assert antigo.get(f"{PAINEL}/api/lotes").status_code == 200  # vale até o convite novo ser usado
    api.relogio.agora += 60
    novo, segredo = aceitar(api, token, senha="outra frase bem longa 9")
    assert antigo.get(f"{PAINEL}/api/lotes").status_code == 401
    assert novo.get(f"{PAINEL}/api/sessao").json()["papel"] == "dono"
    assert entrar(api, "carla", SENHA, totp(api, segredo, 1))[1].status_code == 401  # a senha antiga caiu
    assert entrar(api, "carla", "outra frase bem longa 9", totp(api, segredo, 1))[1].status_code == 200
    # um convite novo cancela o anterior do mesmo nome
    primeiro, segundo = convidar(api, "davi", "operador"), convidar(api, "davi", "operador")
    r = anonimo(api).post(f"{PAINEL}/api/convite/iniciar", json={"convite": primeiro}, headers=ADM)
    assert r.status_code == 401
    assert anonimo(api).post(f"{PAINEL}/api/convite/iniciar", json={"convite": segundo}, headers=ADM).status_code == 200


def test_turning_off_an_access_and_changing_a_role_work_right_away_and_keep_one_owner(painel):
    api = painel
    dona, seg_dona = aceitar(api, convidar(api, "dona", "dono"))
    operador, _ = aceitar(api, convidar(api, "eva", "operador"))
    pessoas = {p["usuario"]: p for p in api.get(f"{PAINEL}/api/acessos").json()["pessoas"]}
    assert pessoas["eva"]["sessoes"] == 1
    # o único dono não perde o papel nem o acesso
    r = api.post(f"{PAINEL}/api/acessos/{pessoas['dona']['id']}/papel", json={"papel": "operador"}, headers=ADM)
    assert r.status_code == 409 and "único dono" in r.json()["detail"]
    r = api.post(f"{PAINEL}/api/acessos/{pessoas['dona']['id']}/desativar", headers=ADM)
    assert r.status_code == 409
    r = dona.post(f"{PAINEL}/api/acessos/{pessoas['dona']['id']}/desativar")
    assert r.status_code == 409 and "próprio acesso" in r.json()["detail"]
    # promover vale na próxima ação, sem entrar de novo
    assert operador.get(f"{PAINEL}/api/acessos").status_code == 403
    assert dona.post(f"{PAINEL}/api/acessos/{pessoas['eva']['id']}/papel", json={"papel": "dono"}).status_code == 200
    assert operador.get(f"{PAINEL}/api/acessos").status_code == 200
    # desativar derruba a sessão na hora e a entrada passa a ser recusada (com a mesma mensagem)
    assert dona.post(f"{PAINEL}/api/acessos/{pessoas['eva']['id']}/desativar").status_code == 200
    assert operador.get(f"{PAINEL}/api/lotes").status_code == 401
    assert entrar(api, "eva", SENHA, "123456")[1].status_code == 401
    # um convite novo traz a pessoa de volta
    de_volta, _ = aceitar(api, convidar(api, "eva", "operador"), senha="voltei com uma frase nova")
    assert de_volta.get(f"{PAINEL}/api/sessao").json()["papel"] == "operador"
    itens = api.get(f"{PAINEL}/api/auditoria", params={"acao": "papel"}).json()["itens"]
    assert itens[0]["detalhe"]["antes"] == {"papel": "operador"} and itens[0]["detalhe"]["depois"] == {"papel": "dono"}
    assert itens[0]["admin"] == "dona" and itens[0]["alvo"] == "eva"


def test_audit_keeps_who_what_when_where_and_before_after_with_filters_pages_and_a_spreadsheet(painel):
    api = painel
    api.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0) Firefox/143.0"
    tok = _cliente(api, "=Loja Fórmula", saldo="10")
    cid = api.get(f"{PAINEL}/api/clientes").json()["clientes"][0]["id"]
    assert tok
    api.post(f"{PAINEL}/api/clientes/{cid}/plano", json={"plano": "interno"}, headers=ADM)
    api.post(f"{PAINEL}/api/clientes/{cid}/desativar", headers=ADM)
    r = api.get(f"{PAINEL}/api/auditoria").json()
    (plano,) = [i for i in r["itens"] if i["acao"] == "plano"]
    assert plano["detalhe"]["antes"] == {"plano": "pago"} and plano["detalhe"]["depois"] == {"plano": "interno"}
    assert plano["ip"] == "testclient" and "Firefox" in plano["agente"] and plano["admin"].startswith("admin-")
    (saldo,) = [i for i in r["itens"] if i["acao"] == "saldo"]
    assert saldo["detalhe"]["antes"] == {"saldo": 0} and saldo["detalhe"]["depois"] == {"saldo": 1000}
    assert {"plano", "saldo", "desativar", "entrada_emergencia"} <= set(r["acoes"]) and r["pessoas"]
    filtrado = api.get(f"{PAINEL}/api/auditoria", params={"acao": "plano", "alvo": cid}).json()["itens"]
    assert [i["acao"] for i in filtrado] == ["plano"]
    assert api.get(f"{PAINEL}/api/auditoria", params={"pessoa": "ninguem"}).json()["itens"] == []
    agora = api.relogio.agora
    assert api.get(f"{PAINEL}/api/auditoria", params={"desde": agora * 2}).json()["itens"] == []
    # página seguinte: 100 por página, a próxima pelo `ate`
    for i in range(105):
        api.banco.auditar("robo", "teste", f"alvo-{i}", None, "203.0.113.9")
    p1 = api.get(f"{PAINEL}/api/auditoria", params={"pessoa": "robo"}).json()
    assert len(p1["itens"]) == 100 and p1["proximo"] == p1["itens"][-1]["quando"]
    p2 = api.get(f"{PAINEL}/api/auditoria", params={"pessoa": "robo", "ate": p1["proximo"]}).json()
    assert len(p2["itens"]) == 5 and p2["proximo"] is None
    assert {i["alvo"] for i in p1["itens"] + p2["itens"]} == {f"alvo-{i}" for i in range(105)}
    # planilha: filtros, antes/depois em colunas e sem fórmula do Excel vinda de um texto
    api.banco.auditar("robo", "teste", "=HYPERLINK(1)", {"antes": {"x": 1}, "depois": {"x": 2}}, "203.0.113.9")
    csv = api.get(f"{PAINEL}/api/auditoria.csv", params={"pessoa": "robo", "fuso_min": -180})
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    linhas = csv.content.decode("utf-8-sig").splitlines()
    assert linhas[0] == "Data;Pessoa;Ação;Alvo;Antes;Depois;Detalhe;IP;Navegador" and len(linhas) == 107
    assert "'=HYPERLINK(1)" in linhas[1] and '"{""x"": 1}"' in linhas[1]
    assert "auditoria_exportada" in api.get(f"{PAINEL}/api/auditoria").json()["acoes"]


def test_the_emergency_key_session_ends_when_the_key_leaves_the_env(painel):
    api = painel
    assert api.get(f"{PAINEL}/api/lotes").status_code == 200
    api.cfg.admin_tokens = []  # o dono tirou RENDER2GO_ADMIN_TOKENS do .env
    assert api.get(f"{PAINEL}/api/sessao").json() == {"admin": None}
    assert api.get(f"{PAINEL}/api/lotes").status_code == 401
    assert (
        anonimo(api).post(f"{PAINEL}/api/entrar", json={"token": ADMIN, "totp": "123456"}, headers=ADM).status_code
        == 401
    )


def test_the_first_owner_invite_comes_from_the_server_terminal(banco, monkeypatch, capsys):
    monkeypatch.setenv("RENDER2GO_ADMIN_CAMINHO", PAINEL)
    monkeypatch.setenv("DOMINIO", "app.render2go.com")
    assert cli.admin_convite(banco, "Dono", "dono") == 0
    saida = capsys.readouterr().out
    assert f"https://app.render2go.com{PAINEL}/#convite=" in saida and "canal seguro" in saida
    (conv,) = banco.convites_pendentes()
    assert (conv["usuario"], conv["papel"], conv["criado_por"]) == ("dono", "dono", "terminal")
    assert banco.auditoria_filtrada(admin="terminal")[0]["acao"] == "convite_criado"
    assert cli.admin_convite(banco, "x", "dono") == 2
    monkeypatch.delenv("RENDER2GO_ADMIN_CAMINHO")
    assert cli.admin_convite(banco, "dono", "dono") == 2


def test_overview_numbers_for_the_first_screen(painel):
    api = painel
    tok = _cliente(api, "Loja", saldo="100")
    r = _lote(api, tok)
    _enviar_tudo(api, tok, r.json()["lote"], r.json()["envios"])
    resumo = api.get(f"{PAINEL}/api/resumo", params={"fuso_min": -180}).json()
    assert resumo["revisao"] == 1 and resumo["revisao_mais_antiga"] and resumo["pedidos_hoje"] == 1
    assert resumo["cobrado_hoje"] > 0 and resumo["pods"] == 0 and resumo["pausado"] is False
    assert isinstance(resumo["avisos"], list) and resumo["orquestrador_visto_s"] is None
    assert api.get(f"{PAINEL}/api/resumo", params={"fuso_min": 9999}).status_code == 422


def test_scrypt_password_helpers():
    guardado = contas.hash_senha("Uma frase com acentuação ó")
    assert contas.senha_confere("Uma frase com acentuação ó", guardado)
    assert contas.senha_confere("Uma frase com acentuação ó", guardado)  # NFD → NFC
    assert not contas.senha_confere("Uma frase com acentuação o", guardado)
    assert not contas.senha_confere("qualquer", None) and not contas.senha_confere("x", "md5$abc")
    assert contas.senha_fraca("doze letras!", "ana") is None
    assert "nome de acesso" in contas.senha_fraca("bom dia ana carolina", "ana")
    assert api_mod.PAPEIS_PAINEL.keys() == {"dono", "operador"}
