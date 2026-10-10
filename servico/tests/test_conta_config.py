"""Configurações da conta (10/10/2026): confirmação recente antes de ações sensíveis, verificação em duas etapas
(aplicativo autenticador e códigos de recuperação), troca de e-mail, aparelhos conectados, desativar (pausar) e
excluir a conta, perfil com nome e Gravatar, tema, aceite dos documentos novos e "Tenho interesse" nos recursos
em breve."""

import re
import types

import pytest
from test_api import ADM, PAINEL, Armazem, _cliente, anonimo, cliente_teste, config_teste, h
from test_contas import PAGINA

from render2go_servico import contas, perfil, suporte
from render2go_servico.db import sha

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
COOKIE = "__Host-render2go"


class Relogio:
    """O relógio do módulo contas (só o código do autenticador usa): os testes andam de 30 em 30 s."""

    def __init__(self) -> None:
        self.agora = 1_800_000_000.0

    def time(self) -> float:
        return self.agora

    def passo(self) -> int:
        return int(self.agora // 30)


@pytest.fixture
def relogio(monkeypatch):
    r = Relogio()
    monkeypatch.setattr(contas, "time", types.SimpleNamespace(time=r.time))
    return r


@pytest.fixture
def api(banco):
    enviados, fotos = [], []

    def gravatar(email):
        fotos.append(email)
        return (PNG, "image/png") if email.startswith("com.foto") else None

    cfg = config_teste(
        banco,
        Armazem(),
        email=suporte.ConfigEmail(host="smtp.teste", para="", de="Render2Go <nao-responda@render2go.com>"),
        enviar_email=lambda cfg_, msg: enviados.append(msg),
        baixar_gravatar=gravatar,
    )
    a = cliente_teste(cfg)
    a.enviados, a.cfg, a.fotos = enviados, cfg, fotos
    return a


def _codigo(api) -> str:
    api.cfg.esperar_fundo()
    return re.match(r"(\d{8}) ", api.enviados[-1]["Subject"]).group(1)


def _ultimo(api, para: str):
    api.cfg.esperar_fundo()
    return [m for m in api.enviados if m["To"] == para][-1]


def pedir_entrada(api, email, agente="testclient"):
    """1º passo da entrada (e-mail + código): devolve o navegador e a resposta (com ou sem o desafio do MFA)."""
    nav = anonimo(api)
    nav.headers["User-Agent"] = agente
    assert nav.post("/api/conta/codigo", json={"email": email, "aceite": True}, headers=PAGINA).status_code == 200
    r = nav.post("/api/conta/entrar", json={"email": email, "codigo": _codigo(api), "aceite": True}, headers=PAGINA)
    assert r.status_code == 200, r.text
    return nav, r


def entrar(api, email="fulana.silva@gmail.com", agente="testclient"):
    nav, r = pedir_entrada(api, email, agente)
    assert "mfa" not in r.json()
    nav.conta = r.json()["conta"]
    return nav


def envelhecer(api, nav, segundos=700):
    """A sessão deste navegador passa a ter sido aberta há `segundos` (sem a confirmação recente)."""
    with api.banco.tx() as c:
        c.run(
            "UPDATE sessoes SET criado = criado - ?, confirmada_ate = NULL WHERE id = ?",
            (segundos, sha(nav.cookies.get(COOKIE))),
        )


def confirmar_por_email(api, nav):
    assert nav.post("/api/conta/confirmar/codigo", headers=PAGINA).status_code == 200
    r = nav.post("/api/conta/confirmar", json={"codigo": _codigo(api)}, headers=PAGINA)
    assert r.status_code == 200, r.text


def ligar_mfa(api, nav, relogio) -> tuple[str, list[str]]:
    """Liga a verificação em duas etapas com o código do passo anterior (o do passo atual e o seguinte ficam
    livres para entrar). Devolve o segredo e os códigos de recuperação."""
    r = nav.post("/api/conta/mfa/iniciar", headers=PAGINA)
    assert r.status_code == 200, r.text
    segredo = r.json()["segredo"]
    r = nav.post("/api/conta/mfa/ativar", json={"codigo": contas.totp(segredo, relogio.passo() - 1)}, headers=PAGINA)
    assert r.status_code == 200, r.text
    return segredo, r.json()["codigos"]


def test_sensitive_actions_need_a_recent_confirmation_that_lasts_ten_minutes(api):
    nav = entrar(api)
    assert 590 <= nav.get("/api/conta").json()["confirmada_s"] <= 600  # quem acabou de entrar já confirmou
    envelhecer(api, nav)
    assert nav.get("/api/conta").json()["confirmada_s"] == 0
    r = nav.post("/api/conta/mfa/iniciar", headers=PAGINA)
    assert r.status_code == 403 and r.headers["x-render2go-motivo"] == "confirmar"
    for rota, corpo in (("/api/conta/email/codigo", {"email": "outra@gmail.com"}), ("/api/conta/mfa/desligar", None)):
        assert nav.post(rota, json=corpo, headers=PAGINA).headers.get("x-render2go-motivo") == "confirmar"
    r = nav.post("/api/conta/excluir", json={"confirmacao": "EXCLUIR"}, headers=PAGINA)
    assert r.status_code == 403 and r.headers["x-render2go-motivo"] == "confirmar"

    # o código de confirmação tem chave própria: não derruba um código de entrada pedido em outro aparelho
    outro = anonimo(api)
    corpo = {"email": "fulana.silva@gmail.com", "aceite": True}
    assert outro.post("/api/conta/codigo", json=corpo, headers=PAGINA).status_code == 200
    de_entrada = _codigo(api)
    assert nav.post("/api/conta/confirmar/codigo", headers=PAGINA).status_code == 200
    de_confirmacao = _codigo(api)
    msg = api.enviados[-1]
    assert "confirmar uma alteração" in msg["Subject"] and "http" not in msg.get_content()
    assert nav.post("/api/conta/confirmar", json={"codigo": "00000000"}, headers=PAGINA).status_code == 401
    r = nav.post("/api/conta/confirmar", json={"codigo": de_confirmacao}, headers=PAGINA)
    assert r.status_code == 200 and r.json()["confirmada_s"] == 600
    assert 590 <= nav.get("/api/conta").json()["confirmada_s"] <= 600
    assert nav.post("/api/conta/mfa/iniciar", headers=PAGINA).status_code == 200
    r = outro.post("/api/conta/entrar", json={**corpo, "codigo": de_entrada}, headers=PAGINA)
    assert r.status_code == 200
    # o código de confirmação vale uma vez só
    envelhecer(api, nav)
    assert nav.post("/api/conta/confirmar", json={"codigo": de_confirmacao}, headers=PAGINA).status_code == 401
    with api.banco.tx() as c:
        meios = [r["detalhe"] for r in c.all("SELECT detalhe FROM acessos WHERE evento = 'confirmacao'")]
    assert meios == ['{"meio": "email"}']


def test_account_settings_only_accept_the_browser_session(api):
    tok = _cliente(api, "Integração antiga")
    for rota in ("/api/conta/confirmar/codigo", "/api/conta/mfa/iniciar", "/api/conta/sessoes/encerrar-outras"):
        r = api.post(rota, headers={**h(tok), **PAGINA})
        assert r.status_code == 403 and "página" in r.json()["detail"], rota
    assert api.get("/api/conta/sessoes", headers=h(tok)).status_code == 403
    r = api.post("/api/conta/excluir", json={"confirmacao": "EXCLUIR"}, headers={**h(tok), **PAGINA})
    assert r.status_code == 403
    conta = api.get("/api/conta", headers=h(tok)).json()
    assert conta["sessao"] is False and conta["confirmada_s"] == 0


def test_two_factor_with_an_authenticator_app_and_recovery_codes(api, relogio):
    nav = entrar(api, "dona.loja@gmail.com")
    r = nav.post("/api/conta/mfa/iniciar", headers=PAGINA)
    segredo, uri = r.json()["segredo"], r.json()["uri"]
    assert uri.startswith("otpauth://totp/Render2Go%3Adona.loja%40gmail.com?secret=") and segredo in uri
    assert r.json()["qr_svg"].startswith("<svg") and "<script" not in r.json()["qr_svg"]
    errado = f"{(int(contas.totp(segredo, relogio.passo())) + 1) % 10**6:06d}"
    assert nav.post("/api/conta/mfa/ativar", json={"codigo": errado}, headers=PAGINA).status_code == 401
    envelhecer(api, nav)  # ler o QR Code pode passar dos 10 min da confirmação: o 2º passo não pede de novo
    r = nav.post("/api/conta/mfa/ativar", json={"codigo": contas.totp(segredo, relogio.passo() - 1)}, headers=PAGINA)
    codigos = r.json()["codigos"]
    assert (
        r.status_code == 200
        and len(codigos) == 10
        and all(re.fullmatch(r"[a-z2-9]{5}-[a-z2-9]{5}", k) for k in codigos)
    )
    assert "duas etapas foi ligada" in _ultimo(api, "dona.loja@gmail.com").get_content()
    conta = nav.get("/api/conta").json()
    assert conta["mfa"]["ativo"] is True and conta["mfa"]["codigos"] == 10
    assert nav.post("/api/conta/mfa/iniciar", headers=PAGINA).status_code == 403  # sem confirmar de novo, nada
    # no banco: o segredo cifrado e só o HMAC de cada código de recuperação
    with api.banco.tx() as c:
        linha = c.one("SELECT mfa_segredo, mfa_pendente FROM clientes WHERE email = 'dona.loja@gmail.com'")
        resumos = [r["resumo"] for r in c.all("SELECT resumo FROM codigos_recuperacao")]
    assert (
        linha["mfa_segredo"].startswith("v1:") and segredo not in linha["mfa_segredo"] and linha["mfa_pendente"] is None
    )
    assert len(resumos) == 10 and not any(k.replace("-", "") in r for k in codigos for r in resumos)

    # a entrada pede o aplicativo: o código do e-mail só devolve o desafio, sem sessão
    novo, r = pedir_entrada(api, "dona.loja@gmail.com")
    assert r.json()["mfa"] is True and "set-cookie" not in r.headers and novo.get("/api/conta").status_code == 401
    desafio = r.json()["desafio"]
    usado = {"desafio": desafio, "codigo": contas.totp(segredo, relogio.passo() - 1)}  # o mesmo da ativação
    assert novo.post("/api/conta/entrar-mfa", json=usado, headers=PAGINA).status_code == 401
    certo = {"desafio": desafio, "codigo": contas.totp(segredo, relogio.passo())}
    r = novo.post("/api/conta/entrar-mfa", json=certo, headers=PAGINA)
    assert (
        r.status_code == 200 and r.headers["set-cookie"].startswith(f"{COOKIE}=") and r.json()["mfa"] == "autenticador"
    )
    assert novo.get("/api/conta").status_code == 200
    assert anonimo(api).post("/api/conta/entrar-mfa", json=certo, headers=PAGINA).status_code == 401  # vale uma vez
    # o desafio não vale como sessão, e o código do mesmo passo não vale de novo
    assert anonimo(api).get("/api/conta", headers={"Cookie": f"{COOKIE}={desafio}"}).status_code == 401
    terceiro, r = pedir_entrada(api, "dona.loja@gmail.com")
    corpo = {"desafio": r.json()["desafio"], "codigo": contas.totp(segredo, relogio.passo())}
    assert terceiro.post("/api/conta/entrar-mfa", json=corpo, headers=PAGINA).status_code == 401

    # código de recuperação: vale uma vez, avisa no e-mail e a conta mostra quantos restam
    r = terceiro.post("/api/conta/entrar-mfa", json={**corpo, "codigo": codigos[0].upper()}, headers=PAGINA)
    assert r.status_code == 200 and r.json()["mfa"] == "recuperacao"
    assert "Restam 9" in _ultimo(api, "dona.loja@gmail.com").get_content()
    assert terceiro.get("/api/conta").json()["mfa"]["codigos"] == 9
    quarto, r = pedir_entrada(api, "dona.loja@gmail.com")
    corpo = {"desafio": r.json()["desafio"], "codigo": codigos[0]}
    assert quarto.post("/api/conta/entrar-mfa", json=corpo, headers=PAGINA).status_code == 401  # 3º erro
    # força bruta: depois de 5 erros em 15 min, nem o código certo entra
    for _ in range(2):
        assert (
            quarto.post("/api/conta/entrar-mfa", json={**corpo, "codigo": "123456"}, headers=PAGINA).status_code == 401
        )
    r = quarto.post("/api/conta/entrar-mfa", json={**corpo, "codigo": codigos[1]}, headers=PAGINA)
    assert r.status_code == 429
    with api.banco.tx() as c:
        eventos = [r["evento"] for r in c.all("SELECT evento FROM acessos ORDER BY quando")]
    assert eventos.count("codigo_recuperacao_usado") == 1 and "mfa_ligado" in eventos


def test_with_two_factor_the_confirmation_needs_the_app_and_codes_can_be_renewed_or_turned_off(api, relogio):
    nav = entrar(api, "seguranca@outlook.com")
    segredo, codigos = ligar_mfa(api, nav, relogio)
    envelhecer(api, nav)
    r = nav.post("/api/conta/confirmar/codigo", headers=PAGINA)
    assert r.status_code == 409 and "aplicativo" in r.json()["detail"]  # o e-mail sozinho não basta
    assert nav.post("/api/conta/confirmar", json={"codigo": "12345678"}, headers=PAGINA).status_code == 401
    r = nav.post("/api/conta/confirmar", json={"codigo": contas.totp(segredo, relogio.passo())}, headers=PAGINA)
    assert r.status_code == 200
    assert nav.post("/api/conta/mfa/iniciar", headers=PAGINA).status_code == 409  # já ligada
    r = nav.post("/api/conta/mfa/codigos", headers=PAGINA)
    novos = r.json()["codigos"]
    assert r.status_code == 200 and len(novos) == 10 and not set(novos) & set(codigos)
    assert "anteriores deixaram de valer" in _ultimo(api, "seguranca@outlook.com").get_content()
    outro, r = pedir_entrada(api, "seguranca@outlook.com")
    corpo = {"desafio": r.json()["desafio"], "codigo": codigos[0]}
    assert outro.post("/api/conta/entrar-mfa", json=corpo, headers=PAGINA).status_code == 401  # os antigos caíram
    assert outro.post("/api/conta/entrar-mfa", json={**corpo, "codigo": novos[0]}, headers=PAGINA).status_code == 200
    assert nav.post("/api/conta/mfa/desligar", headers=PAGINA).status_code == 200
    assert "desligada" in _ultimo(api, "seguranca@outlook.com").get_content()
    with api.banco.tx() as c:
        assert c.one("SELECT COUNT(*) AS n FROM codigos_recuperacao")["n"] == 0
    assert "mfa" not in pedir_entrada(api, "seguranca@outlook.com")[1].json()  # só o código do e-mail de novo
    assert nav.post("/api/conta/mfa/desligar", headers=PAGINA).status_code == 409


def test_a_secret_that_does_not_open_refuses_the_login_instead_of_skipping_the_check(api, relogio):
    """Fail-closed: o segredo cifrado copiado para outra conta (ou com a chave trocada) não abre, e a entrada é
    recusada; nunca passa sem o 2º fator."""
    nav = entrar(api, "dona.a@gmail.com")
    ligar_mfa(api, nav, relogio)
    entrar(api, "dono.b@gmail.com")
    with api.banco.tx() as c:
        c.run(
            "UPDATE clientes SET mfa_segredo = (SELECT mfa_segredo FROM clientes WHERE email = 'dona.a@gmail.com')"
            " WHERE email = 'dono.b@gmail.com'"
        )
    nav_b, r = pedir_entrada(api, "dono.b@gmail.com")
    assert r.json()["mfa"] is True
    r = nav_b.post("/api/conta/entrar-mfa", json={"desafio": r.json()["desafio"], "codigo": "123456"}, headers=PAGINA)
    assert r.status_code == 503 and nav_b.get("/api/conta").status_code == 401


def test_changing_the_email_needs_a_code_sent_to_the_new_address_and_warns_the_old_one(api):
    nav = entrar(api, "fulana.silva@gmail.com")
    entrar(api, "beltrano@gmail.com")
    pedir = "/api/conta/email/codigo"
    r = nav.post(pedir, json={"email": "Beltrano+loja@gmail.com"}, headers=PAGINA)
    assert r.status_code == 409 and "outra conta" in r.json()["detail"]
    assert nav.post(pedir, json={"email": "x@mailinator.com"}, headers=PAGINA).status_code == 422
    assert nav.post(pedir, json={"email": "fulanasilva@gmail.com"}, headers=PAGINA).status_code == 422  # a mesma caixa
    r = nav.post(pedir, json={"email": "Nova.Fulana@outlook.com"}, headers=PAGINA)
    assert r.status_code == 200 and r.json()["email"] == "nova.fulana@outlook.com"
    msg = _ultimo(api, "nova.fulana@outlook.com")
    assert "novo e-mail" in msg["Subject"]
    codigo = _codigo(api)
    envelhecer(api, nav)  # o e-mail pode demorar: o 2º passo vale com o código, sem pedir a confirmação de novo
    assert nav.post(pedir, json={"email": "outro@outlook.com"}, headers=PAGINA).status_code == 403  # o 1º pede
    trocar = "/api/conta/email"
    assert (
        nav.post(trocar, json={"email": "nova.fulana@outlook.com", "codigo": "00000000"}, headers=PAGINA).status_code
        == 401
    )
    r = nav.post(trocar, json={"email": "outra.coisa@outlook.com", "codigo": codigo}, headers=PAGINA)
    assert r.status_code == 401  # o código só vale para o endereço que o recebeu
    r = nav.post(trocar, json={"email": "nova.fulana@outlook.com", "codigo": codigo}, headers=PAGINA)
    assert r.status_code == 200, r.text  # 2 erros: o código segue valendo (morre no 5º)
    conta = nav.get("/api/conta").json()
    assert conta["email"] == "nova.fulana@outlook.com" and conta["nome"] == "nova.fulana@outlook.com"
    aviso = _ultimo(api, "fulana.silva@gmail.com")
    assert "no***@outlook.com" in aviso.get_content() and "nova.fulana" not in aviso.get_content()
    with api.banco.tx() as c:
        (reg,) = c.all("SELECT detalhe FROM acessos WHERE evento = 'email_trocado'")
    assert "fu***@gmail.com" in reg["detalhe"] and "no***@outlook.com" in reg["detalhe"]
    # o e-mail antigo pode abrir uma conta nova, mas sem outro teste grátis
    volta = entrar(api, "fulana.silva@gmail.com")
    assert volta.conta["id"] != conta["id"] and volta.conta["plano"] == "pago"


def test_connected_devices_can_be_listed_and_ended_one_by_one_or_all_others(api):
    celular = entrar(
        api, "aparelhos@gmail.com", "Mozilla/5.0 (Linux; Android 14) AppleWebKit Chrome/141.0 Mobile Safari"
    )
    iphone = entrar(
        api, "aparelhos@gmail.com", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Version/18.0 Mobile Safari/604"
    )
    pc = entrar(api, "aparelhos@gmail.com", "Mozilla/5.0 (Windows NT 10.0) Gecko/20100101 Firefox/143.0")
    lista = celular.get("/api/conta/sessoes").json()["sessoes"]
    assert [s["aparelho"] for s in lista][0] == "Chrome no Android" and lista[0]["atual"] is True
    assert {s["aparelho"] for s in lista} == {"Chrome no Android", "Safari no iPhone", "Firefox no Windows"}
    assert all(set(s) == {"ref", "aparelho", "ip", "criada", "ultimo_uso", "atual"} for s in lista)
    ref_iphone = next(s["ref"] for s in lista if s["aparelho"] == "Safari no iPhone")
    assert all(ref_iphone not in v for v in (sha(iphone.cookies.get(COOKIE)), iphone.cookies.get(COOKIE)))
    assert celular.post(f"/api/conta/sessoes/{ref_iphone}/encerrar", headers=PAGINA).json() == {
        "ok": True,
        "atual": False,
    }
    assert iphone.get("/api/conta").status_code == 401
    assert celular.post(f"/api/conta/sessoes/{ref_iphone}/encerrar", headers=PAGINA).status_code == 404
    assert celular.post("/api/conta/sessoes/" + "x" * 30 + "/encerrar", headers=PAGINA).status_code == 404
    # outra conta não encerra a sessão de ninguém pela referência
    estranho = entrar(api, "estranho@gmail.com")
    ref_pc = next(s["ref"] for s in lista if s["aparelho"] == "Firefox no Windows")
    assert estranho.post(f"/api/conta/sessoes/{ref_pc}/encerrar", headers=PAGINA).status_code == 404
    assert pc.post("/api/conta/sessoes/encerrar-outras", headers=PAGINA).json()["sessoes"] == 1
    assert celular.get("/api/conta").status_code == 401 and pc.get("/api/conta").status_code == 200
    (atual,) = pc.get("/api/conta/sessoes").json()["sessoes"]
    r = pc.post(f"/api/conta/sessoes/{atual['ref']}/encerrar", headers=PAGINA)
    assert r.json()["atual"] is True and "Max-Age=0" in r.headers["set-cookie"]
    assert pc.get("/api/conta").status_code == 401


def test_pausing_the_account_keeps_the_balance_and_the_email_code_brings_it_back(api):
    nav = entrar(api, "pausa@gmail.com")
    cid = nav.conta["id"]
    api.post(f"{PAINEL}/api/clientes/{cid}/plano", json={"plano": "pago"}, headers=ADM)
    api.post(f"{PAINEL}/api/clientes/{cid}/saldo", json={"valor": "30", "descricao": "recarga"}, headers=ADM)
    corpo = {
        "prompt": "a câmera se aproxima do bolo",
        "fotos": [{"nome": "bolo.jpg", "tipo": "image/jpeg", "bytes": 1000}],
    }
    envio = nav.post("/api/lotes", json=corpo, headers=PAGINA)
    assert envio.status_code == 200, envio.text
    assert nav.post("/api/conta/desativar", json={"confirmacao": "sim"}, headers=PAGINA).status_code == 422
    r = nav.post("/api/conta/desativar", json={"confirmacao": "DESATIVAR"}, headers=PAGINA)
    assert r.status_code == 200 and "Max-Age=0" in r.headers["set-cookie"]
    assert nav.get("/api/conta").status_code == 401
    assert "desativada" in _ultimo(api, "pausa@gmail.com").get_content()
    lote = api.banco.lote(envio.json()["lote"])
    assert lote["status"] == "recusado" and "desativada" in lote["motivo"]  # o envio que não terminou
    (k,) = api.get(f"{PAINEL}/api/clientes", params={"busca": "pausa@"}).json()["clientes"]
    assert k["pausada_em"] and k["ativo"] == 1 and k["saldo"] == 3000
    volta, r = pedir_entrada(api, "pausa@gmail.com")
    assert r.json()["reativada"] is True and r.json()["conta"]["saldo"] == 3000
    assert volta.get("/api/conta").status_code == 200
    with api.banco.tx() as c:
        eventos = [r["evento"] for r in c.all("SELECT evento FROM acessos WHERE cliente_id = ?", (cid,))]
    assert "conta_desativada" in eventos and "conta_reativada" in eventos
    # com pedido em andamento, não desativa
    envio = volta.post("/api/lotes", json=corpo, headers=PAGINA).json()
    api.armazem.objetos[envio["envios"][0]["url"].split("armazem.teste/")[1].split("?")[0]] = 1000
    assert volta.post(f"/api/lotes/{envio['lote']}/enviado", headers=PAGINA).status_code == 200
    r = volta.post("/api/conta/desativar", json={"confirmacao": "DESATIVAR"}, headers=PAGINA)
    assert r.status_code == 409 and "andamento" in r.json()["detail"]


def test_a_paused_old_access_code_account_comes_back_only_by_logging_in(api):
    tok = _cliente(api, "Cliente antigo", saldo="10")
    nav = anonimo(api)
    assert nav.post("/api/conta/entrar-codigo", json={"codigo": tok, "aceite": True}, headers=PAGINA).status_code == 200
    assert nav.post("/api/conta/desativar", json={"confirmacao": "DESATIVAR"}, headers=PAGINA).status_code == 200
    assert api.get("/api/lotes", headers=h(tok)).status_code == 401  # a integração também para
    r = anonimo(api).post("/api/conta/entrar-codigo", json={"codigo": tok, "aceite": True}, headers=PAGINA)
    assert r.status_code == 200 and r.json()["reativada"] is True
    assert api.get("/api/lotes", headers=h(tok)).status_code == 200


def test_profile_names_gravatar_photo_and_theme(api):
    nav = entrar(api, "com.foto@gmail.com")
    assert nav.conta["perfil"] == {"primeiro_nome": None, "ultimo_nome": None, "gravatar": False, "foto": None}
    assert nav.get("/api/conta/foto").status_code == 404 and api.fotos == []  # sem a escolha, nada vai ao Gravatar
    r = nav.post("/api/conta/perfil", json={"primeiro_nome": "  Maria   Júlia ", "ultimo_nome": "D'Ávila-Souza Jr."},
                 headers=PAGINA)  # fmt: skip
    assert r.status_code == 200 and r.json()["perfil"]["primeiro_nome"] == "Maria Júlia"
    assert r.json()["perfil"]["ultimo_nome"] == "D'Ávila-Souza Jr."
    for ruim in ("Ana123", "<script>", "a" * 61, "💥", "...", "Jo\u0000ão"):
        r = nav.post("/api/conta/perfil", json={"primeiro_nome": ruim}, headers=PAGINA)
        assert r.status_code == 422 and r.json()["detail"].startswith("nome:"), ruim
    assert nav.post("/api/conta/perfil", json={}, headers=PAGINA).status_code == 422
    r = nav.post("/api/conta/perfil", json={"gravatar": True}, headers=PAGINA)
    foto = r.json()["perfil"]["foto"]
    assert foto.startswith("/api/conta/foto?v=") and "com.foto" not in foto
    assert r.json()["perfil"]["primeiro_nome"] == "Maria Júlia"  # o que não foi enviado não muda
    for _ in range(2):  # a 2ª vem do cache do processo
        f = nav.get(foto)
        assert f.status_code == 200 and f.content == PNG and f.headers["content-type"] == "image/png"
        assert f.headers["cache-control"] == "private, max-age=86400"
    assert api.fotos == ["com.foto@gmail.com"]
    assert nav.get(foto, headers={"If-None-Match": f.headers["etag"]}).status_code == 304
    sem = entrar(api, "sem.foto@gmail.com")
    sem.post("/api/conta/perfil", json={"gravatar": True}, headers=PAGINA)
    # sem foto no Gravatar: 204 (a página mostra a inicial, sem erro no console do navegador)
    vazia = sem.get("/api/conta/foto")
    assert (
        vazia.status_code == 204 and vazia.content == b"" and vazia.headers["cache-control"] == "private, max-age=3600"
    )
    r = nav.post("/api/conta/perfil", json={"gravatar": False, "ultimo_nome": ""}, headers=PAGINA)
    assert r.json()["perfil"]["foto"] is None and r.json()["perfil"]["ultimo_nome"] is None
    assert nav.get("/api/conta/foto").status_code == 404
    for tema in ("escuro", "claro", "auto"):
        assert nav.post("/api/conta/preferencias", json={"tema": tema}, headers=PAGINA).json()["tema"] == tema
        assert nav.get("/api/conta").json()["tema"] == tema
    assert nav.post("/api/conta/preferencias", json={"tema": "roxo"}, headers=PAGINA).status_code == 422


def test_gravatar_download_takes_only_png_or_jpeg_from_the_fixed_host(monkeypatch):
    pedidos = []

    class Resposta:
        status = 200

        def __init__(self, dados):
            self.dados = dados

        def read(self, n):
            return self.dados[:n]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    respostas = [b"<svg onload=alert(1)>", PNG, b"\xff\xd8\xff" + b"x" * (perfil.GRAVATAR_MAX_BYTES + 10)]

    class Abridor:
        def open(self, req, timeout):
            pedidos.append((req.full_url, timeout))
            return Resposta(respostas.pop(0))

    monkeypatch.setattr(perfil.urllib.request, "build_opener", lambda *handlers: Abridor())
    assert perfil.baixar_gravatar(" Pessoa@Gmail.com ") is None  # nem PNG nem JPEG
    assert perfil.baixar_gravatar("pessoa@gmail.com") == (PNG, "image/png")
    assert perfil.baixar_gravatar("pessoa@gmail.com") is None  # grande demais
    esperado = perfil.GRAVATAR_URL.format(perfil.hash_gravatar("pessoa@gmail.com"))
    assert {u for u, _ in pedidos} == {esperado} and esperado.startswith("https://gravatar.com/avatar/")
    assert all(t == perfil.GRAVATAR_TEMPO_S for _, t in pedidos)
    cache = perfil.CacheFotos(lambda e: (_ for _ in ()).throw(OSError("sem rede")))
    assert cache.foto("x@gmail.com") is None  # rede fora: sem foto agora, sem erro 500


def test_deleting_the_account_needs_a_recent_confirmation_and_clears_the_settings(api, relogio):
    nav = entrar(api, "apagar.tudo@gmail.com")
    segredo, _ = ligar_mfa(api, nav, relogio)
    nav.post("/api/conta/perfil", json={"primeiro_nome": "Ana", "gravatar": True}, headers=PAGINA)
    nav.post("/api/conta/preferencias", json={"tema": "escuro"}, headers=PAGINA)
    envelhecer(api, nav)
    r = nav.post("/api/conta/excluir", json={"confirmacao": "EXCLUIR"}, headers=PAGINA)
    assert r.status_code == 403 and r.headers["x-render2go-motivo"] == "confirmar"
    assert nav.post(
        "/api/conta/confirmar", json={"codigo": contas.totp(segredo, relogio.passo())}, headers=PAGINA
    ).is_success
    r = nav.post("/api/conta/excluir", json={"confirmacao": "EXCLUIR"}, headers=PAGINA)
    assert r.status_code == 200
    assert "foi excluída" in _ultimo(api, "apagar.tudo@gmail.com").get_content()
    with api.banco.tx() as c:
        k = c.one("SELECT * FROM clientes WHERE id = ?", (nav.conta["id"],))
        rec = c.one("SELECT COUNT(*) AS n FROM codigos_recuperacao WHERE cliente_id = ?", (nav.conta["id"],))["n"]
    campos = (
        "email",
        "primeiro_nome",
        "ultimo_nome",
        "gravatar",
        "tema",
        "mfa_segredo",
        "mfa_ativo_em",
        "mfa_pendente",
    )
    assert all(k[campo] is None for campo in campos) and rec == 0


def test_new_document_versions_are_accepted_by_a_session_opened_before_them(api):
    nav = entrar(api, "termos.novos@gmail.com")
    assert nav.get("/api/conta").json()["documentos"]["pendentes"] == []
    with api.banco.tx() as c:  # a conta tinha aceitado a versão anterior
        c.run(
            "UPDATE clientes SET termos_versao = 'termos-v1', privacidade_versao = NULL WHERE id = ?",
            (nav.conta["id"],),
        )
    docs = nav.get("/api/conta").json()["documentos"]
    assert docs["pendentes"] == ["termos", "privacidade"] and docs["atuais"] == contas.DOCUMENTOS
    corpo = {
        "prompt": "a câmera se aproxima do bolo",
        "fotos": [{"nome": "bolo.jpg", "tipo": "image/jpeg", "bytes": 1000}],
    }
    r = nav.post("/api/lotes", json=corpo, headers=PAGINA)
    assert r.status_code == 428 and r.headers["x-render2go-motivo"] == "aceite"
    r = nav.post("/api/conta/aceitar", json={"documentos": {"termos": "termos-v1"}}, headers=PAGINA)
    assert r.status_code == 409
    r = nav.post("/api/conta/aceitar", json={"documentos": contas.DOCUMENTOS}, headers=PAGINA)
    assert r.status_code == 200 and nav.get("/api/conta").json()["documentos"]["pendentes"] == []
    assert nav.post("/api/lotes", json=corpo, headers=PAGINA).status_code == 200
    with api.banco.tx() as c:
        versoes = {r["versao"] for r in c.all("SELECT versao FROM aceites WHERE cliente_id = ?", (nav.conta["id"],))}
    assert set(contas.DOCUMENTOS.values()) <= versoes
    # integração antiga (código no cabeçalho): o aceite dela é combinado com o dono; não para
    tok = _cliente(api, "Integração")
    assert api.post("/api/lotes", json=corpo, headers=h(tok)).status_code == 200


def test_interest_in_coming_soon_account_features_is_counted_in_the_panel(api):
    nav = entrar(api, "curiosa@gmail.com")
    for recurso in ("recurso:mix", "recurso:indicacao", "recurso:mix"):
        assert nav.post("/api/interesses", json={"finalidade": recurso}, headers=PAGINA).status_code == 200
    assert nav.post("/api/interesses", json={"finalidade": "recurso:voar"}, headers=PAGINA).status_code == 422
    assert set(nav.get("/api/conta").json()["interesses"]) == {"recurso:mix", "recurso:indicacao"}
    r = api.get(f"{PAINEL}/api/interesses").json()
    titulos = {i["finalidade"]: i["titulo"] for i in r["resumo"]}
    assert titulos["recurso:mix"].startswith("Mix") and titulos["recurso:indicacao"] == "Ganhos por indicação"
    assert {i["email"] for i in r["cliques"]} == {"curiosa@gmail.com"}


def test_the_panel_turns_off_two_factor_for_someone_who_lost_the_phone(api, relogio):
    nav = entrar(api, "perdeu.celular@gmail.com")
    ligar_mfa(api, nav, relogio)
    cid = nav.conta["id"]
    (k,) = api.get(f"{PAINEL}/api/clientes", params={"busca": "perdeu"}).json()["clientes"]
    assert k["mfa_ativo_em"]
    assert api.post(f"{PAINEL}/api/clientes/{cid}/mfa/desligar").status_code == 403  # sem o cabeçalho da página
    assert api.post(f"{PAINEL}/api/clientes/{cid}/mfa/desligar", headers=ADM).status_code == 200
    assert "pelo suporte" in _ultimo(api, "perdeu.celular@gmail.com")["Subject"]
    assert api.post(f"{PAINEL}/api/clientes/{cid}/mfa/desligar", headers=ADM).status_code == 404
    assert "mfa" not in pedir_entrada(api, "perdeu.celular@gmail.com")[1].json()
    acoes = [a["acao"] for a in api.get(f"{PAINEL}/api/seguranca").json()["auditoria"]]
    assert "mfa_desligado" in acoes
    assert api.get(f"{PAINEL}/api/clientes").json()["resumo"]["com_mfa"] == 0


def test_the_data_copy_has_the_new_settings_but_never_the_secrets(api, relogio):
    nav = entrar(api, "copia.dados@gmail.com")
    segredo, codigos = ligar_mfa(api, nav, relogio)
    nav.post("/api/conta/perfil", json={"primeiro_nome": "Rita", "ultimo_nome": "Lee"}, headers=PAGINA)
    dados = nav.get("/api/conta/dados")
    texto = dados.text
    conta = dados.json()["conta"]
    assert (conta["primeiro_nome"], conta["ultimo_nome"]) == ("Rita", "Lee") and conta["mfa_ativo_em"]
    assert len(dados.json()["codigos_recuperacao"]) == 10
    assert set(dados.json()["codigos_recuperacao"][0]) == {"criado", "usado_em"}
    assert segredo not in texto and "mfa_segredo" not in texto and "resumo" not in texto
    assert not any(k.replace("-", "") in texto for k in codigos)


def test_account_helpers():
    assert contas.aparelho(None) == "Aparelho não identificado"
    ua_edge = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/141.0 Safari/537.36 Edg/141.0"
    assert contas.aparelho(ua_edge) == "Edge no Windows"
    assert (
        contas.aparelho("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) Version/18.0 Safari/605.1") == "Safari no macOS"
    )
    assert contas.aparelho("curl/8.0") == "Aparelho não identificado"
    assert contas.mascarar_email("fulana@gmail.com") == "fu***@gmail.com"
    assert contas.mascarar_email("ab@gmail.com") == "a***@gmail.com" and contas.mascarar_email(None) is None
    assert contas.limpar_recuperacao(" ABCDE-FGHJK ") == "abcdefghjk"
    assert contas.limpar_recuperacao("abcde-fghji") is None and contas.limpar_recuperacao("١٢٣٤٥-abcde") is None
    chave = contas.chave_segredos("x" * 40, "y" * 40)
    cifrado = contas.cifrar(chave, "SEGREDO", "cliente:cli_a")
    assert contas.decifrar(chave, cifrado, "cliente:cli_a") == "SEGREDO"
    outra_chave = contas.chave_segredos("z" * 40, "y" * 40)
    errados = [
        (chave, cifrado, "cliente:cli_b"),  # copiado para outra conta
        (outra_chave, cifrado, "cliente:cli_a"),  # RENDER2GO_SEGREDO_CONTAS trocado
        (chave, cifrado[:-4] + "AAAA", "cliente:cli_a"),  # alterado no banco
        (chave, "texto puro", "cliente:cli_a"),
    ]
    for errado in errados:
        with pytest.raises(ValueError):
            contas.decifrar(*errado)
    assert perfil.nome_pessoa("  José  ") == "José" and perfil.nome_pessoa("") is None
    assert perfil.nome_pessoa("José") == "José"  # acento combinado vira a letra acentuada (NFC)
