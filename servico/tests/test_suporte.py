"""Formulário de suporte: limite contra spam, registro no banco e envio por SMTP (servidor falso local)."""

import socketserver
import threading
from email import message_from_bytes

import pytest
from test_api import PAINEL, _cliente, cliente_teste, config_teste, h

from render2go_servico import contas, suporte

D1, D2 = "11111111-0000-4000-8000-000000000000", "22222222-0000-4000-8000-000000000000"


class SMTPFalso(socketserver.StreamRequestHandler):
    """O mínimo de SMTP (sem TLS) para o smtplib entregar uma mensagem."""

    recebidas: list[bytes] = []

    def _r(self, linha: str) -> None:
        self.wfile.write(f"{linha}\r\n".encode())

    def handle(self):
        self._r("220 falso")
        while linha := self.rfile.readline():
            cmd = linha.decode().strip().upper()
            if cmd.startswith(("EHLO", "HELO")):
                self._r("250 falso")
            elif cmd.startswith(("MAIL", "RCPT", "RSET", "NOOP")):
                self._r("250 ok")
            elif cmd == "DATA":
                self._r("354 manda")
                corpo = b""
                while (parte := self.rfile.readline()) != b".\r\n":
                    corpo += parte
                SMTPFalso.recebidas.append(corpo)
                self._r("250 aceito")
            elif cmd == "QUIT":
                self._r("221 tchau")
                return


@pytest.fixture
def smtp():
    SMTPFalso.recebidas = []
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SMTPFalso)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def _api(banco, email=None, enviar=None, **kw):
    cfg = config_teste(banco, email=email, **kw)
    if enviar:
        cfg.enviar_email = enviar
    return cliente_teste(cfg), cfg


def _mandar(api, tok, dispositivo=D1, **corpo):
    corpo = {"mensagem": "Não consigo baixar o vídeo do pedido de ontem.", **corpo}
    return api.post("/api/suporte", json=corpo, headers={**h(tok), "X-Dispositivo": dispositivo})


def test_message_is_emailed_to_the_owner_with_reply_to_the_client(banco, smtp):
    email = suporte.ConfigEmail(
        host="127.0.0.1", porta=smtp, tls="nenhum", para="dono@exemplo.com", de="suporte@render2go.com"
    )
    api, cfg = _api(banco, email)
    tok = _cliente(api, "Padaria Pão Bom")
    r = _mandar(api, tok, assunto="Download\r\nBcc: alguem@x.com", email="cliente@padaria.com.br")
    assert r.status_code == 200 and r.json()["ok"]
    cfg.esperar_fundo()
    (bruto,) = SMTPFalso.recebidas
    m = message_from_bytes(bruto)
    assert m["To"] == "dono@exemplo.com" and m["Reply-To"] == "cliente@padaria.com.br"
    assert m["Subject"] == "[Render2Go] Suporte: Download Bcc: alguem@x.com" and m["Bcc"] is None  # sem injeção
    texto = m.get_payload(decode=True).decode()
    assert "Padaria Pão Bom" in texto and "baixar o vídeo" in texto and D1 in texto
    msgs = api.get(f"{PAINEL}/api/suporte").json()
    assert msgs["email_configurado"] is True and msgs["mensagens"][0]["enviado"] == 1


def test_rate_limit_per_device_ip_and_client(banco):
    api, _ = _api(banco)  # sem e-mail configurado: só guarda
    tok = _cliente(api)
    for _ in range(3):
        assert _mandar(api, tok).status_code == 200
    r = _mandar(api, tok)
    assert r.status_code == 429 and "uma hora" in r.json()["detail"]  # 3 por hora por dispositivo
    assert _mandar(api, tok, dispositivo=D2).status_code == 200  # outro dispositivo...
    assert _mandar(api, tok, dispositivo=D2).status_code == 200
    assert _mandar(api, tok, dispositivo=D2).status_code == 429  # ...mas o IP chegou a 5 por hora
    msgs = api.get(f"{PAINEL}/api/suporte").json()
    assert msgs["email_configurado"] is False and len(msgs["mensagens"]) == 5


def test_invalid_input_and_failed_email_are_handled(banco):
    def falha(cfg, mensagem):
        raise OSError("conexão recusada")

    email = suporte.ConfigEmail(host="smtp.exemplo.com", para="dono@exemplo.com", de="suporte@render2go.com")
    api, cfg = _api(banco, email, enviar=falha)
    tok = _cliente(api)
    assert _mandar(api, tok, email="nao-e-email").status_code == 422
    assert _mandar(api, tok, mensagem="oi").status_code == 422  # curta demais
    assert api.post("/api/suporte", json={"mensagem": "x" * 20}).status_code == 422  # sem código: pede e-mail
    assert _mandar(api, tok).status_code == 200  # o cliente não vê a falha do e-mail
    cfg.esperar_fundo()
    (m,) = api.get(f"{PAINEL}/api/suporte").json()["mensagens"]
    assert m["enviado"] == 0 and "conexão recusada" in m["erro"]  # fica no banco e o painel mostra


def test_support_page_works_without_an_access_code_with_stricter_limits(banco):
    api, _ = _api(banco)
    assert api.get("/suporte").status_code == 200 and 'data-pagina="suporte"' in api.get("/suporte").text
    sem = {"X-Dispositivo": D1}
    corpo = {"mensagem": "Perdi o meu código de acesso, como recupero?"}
    r = api.post("/api/suporte", json=corpo, headers=sem)
    assert r.status_code == 422 and "e-mail" in r.json()["detail"]  # sem código, o e-mail é obrigatório
    for _ in range(2):
        assert api.post("/api/suporte", json={**corpo, "email": "dona@loja.com"}, headers=sem).status_code == 200
    assert api.post("/api/suporte", json={**corpo, "email": "dona@loja.com"}, headers=sem).status_code == 429
    (m, _) = api.get(f"{PAINEL}/api/suporte").json()["mensagens"]
    assert m["cliente_id"] == "" and m["email"] == "dona@loja.com"
    # código inválido continua sendo recusado (não vira mensagem anônima)
    assert api.post("/api/suporte", json=corpo, headers={"Authorization": "Bearer " + "x" * 40}).status_code == 401


def test_support_without_an_account_needs_the_robot_check(banco, monkeypatch):
    """Sem conta, o "não sou um robô" é obrigatório; sem a chave configurada, recusa (fail-closed)."""
    vistos = []

    def conferir(cfg, resposta, ip, acao=None):
        vistos.append((resposta, acao))
        return None if resposta == "token-bom" else "verificação anti-robô recusada: invalid-input-response"

    monkeypatch.setattr(contas, "turnstile_confere", conferir)
    api, _ = _api(banco, turnstile_desligado=False)  # sem chave: o suporte sem conta fica fechado
    corpo = {"mensagem": "Não consigo entrar na minha conta.", "email": "dona@gmail.com"}
    r = api.post("/api/suporte", json=corpo, headers={"X-Dispositivo": D1})
    assert r.status_code == 503 and "anti-robô" in r.json()["detail"]

    api, _ = _api(banco, turnstile_desligado=False, turnstile=contas.ConfigTurnstile("1x" + "0" * 20, "segredo"))
    assert api.post("/api/suporte", json=corpo, headers={"X-Dispositivo": D1}).status_code == 403
    r = api.post("/api/suporte", json={**corpo, "turnstile": "token-ruim"}, headers={"X-Dispositivo": D1})
    assert r.status_code == 403 and "robô" in r.json()["detail"]
    r = api.post("/api/suporte", json={**corpo, "turnstile": "token-bom"}, headers={"X-Dispositivo": D1})
    assert r.status_code == 200 and vistos[-1] == ("token-bom", "suporte")
    # com conta (código de acesso), sem verificação
    tok = _cliente(api)
    assert _mandar(api, tok, dispositivo=D2).status_code == 200
