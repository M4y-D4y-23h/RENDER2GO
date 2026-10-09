"""Recarga por Pix (ADR 0008) com um Mercado Pago falso: o crédito entra uma vez só, só com a confirmação
da API do Mercado Pago (o aviso é só o gatilho), com a assinatura do aviso conferida e o valor certo."""

import hashlib
import hmac

import pytest
from test_api import PAINEL, Armazem, anonimo, cliente_teste, config_teste
from test_contas import PAGINA, entrar

from render2go_servico import pagamentos, suporte

SEGREDO = "segredo-do-webhook-de-teste"
CPF = "529.982.247-25"  # CPF válido de exemplo (dígitos verificadores certos)


class MercadoPagoFalso:
    """Guarda os Pix criados; `situacao[id]` decide o que a consulta responde."""

    def __init__(self):
        self.criados: list[dict] = []
        self.situacao: dict[str, dict] = {}
        self.consultas = 0

    def criar_pix(self, recarga_id, valor, email, doc):
        pid = str(1000 + len(self.criados))
        self.criados.append({"id": pid, "ref": recarga_id, "valor": valor, "email": email, "doc": doc})
        self.situacao[pid] = {"status_mp": "pending", "valor": valor, "ref": recarga_id}
        return {
            "id": pid,
            "status": "pendente",
            "expira": 4102444800.0,
            "copia_cola": "00020126pix",
            "qr_base64": "iVBORw0KGgo=",
        }

    def consultar(self, provedor_id):
        self.consultas += 1
        s = self.situacao[provedor_id]
        return {
            "id": provedor_id,
            "status_mp": s["status_mp"],
            "status": pagamentos.SITUACOES[s["status_mp"]],
            "detalhe": None,
            "valor": s["valor"],
            "moeda": "BRL",
            "referencia": s["ref"],
            "metodo": "pix",
        }


@pytest.fixture
def pix(banco):
    mp = MercadoPagoFalso()
    enviados = []
    cfg = config_teste(
        banco,
        Armazem(),
        email=suporte.ConfigEmail(host="smtp.teste", para="", de="x@render2go.com"),
        enviar_email=lambda c, m: enviados.append(m),
        mp=pagamentos.ConfigMP("token-mp", SEGREDO, "https://app.teste/api/pagamentos/mercadopago"),
        provedor_pagamento=lambda cfg_: mp,
    )
    api = cliente_teste(cfg)
    api.enviados, api.cfg, api.mp = enviados, cfg, mp
    return api


def _aviso(api, data_id, segredo=SEGREDO, ts="1704908010", rid="req-1"):
    manifesto = f"id:{data_id.lower()};request-id:{rid};ts:{ts};"
    v1 = hmac.new(segredo.encode(), manifesto.encode(), hashlib.sha256).hexdigest()
    return anonimo(api).post(
        f"/api/pagamentos/mercadopago?data.id={data_id}&type=payment",
        json={"type": "payment", "data": {"id": data_id}},
        headers={"x-signature": f"ts={ts},v1={v1}", "x-request-id": rid},
    )


def test_documents_and_webhook_signature():
    assert pagamentos.documento(CPF) == ("CPF", "52998224725")
    assert pagamentos.documento("11.222.333/0001-81") == ("CNPJ", "11222333000181")
    for ruim in ("111.111.111-11", "529.982.247-24", "123", "11.222.333/0001-80"):
        with pytest.raises(ValueError):
            pagamentos.documento(ruim)
    assert pagamentos.mascarar("CPF", "52998224725") == "CPF •••4725"
    v1 = hmac.new(b"s", b"id:abc123;request-id:r;ts:1;", hashlib.sha256).hexdigest()
    assert pagamentos.assinatura_confere("s", f"ts=1,v1={v1}", "r", "ABC123")  # o id vai em minúsculas
    assert not pagamentos.assinatura_confere("s", f"ts=1,v1={v1}", "outro", "ABC123")
    assert not pagamentos.assinatura_confere("s", "ts=1", "r", "ABC123")
    assert not pagamentos.assinatura_confere("", f"ts=1,v1={v1}", "r", "ABC123")


def test_pix_top_up_is_credited_once_and_moves_the_trial_to_prepaid(pix):
    nav = entrar(pix, "cliente.pix@gmail.com")
    conta = nav.get("/api/conta").json()
    assert conta["plano"] == "teste" and conta["pagamento"] == {"ativo": True, "minimo": 1000, "maximo": 200000}
    pedir = lambda **c: nav.post("/api/pagamentos/pix", json={"valor": "50", "documento": CPF, **c}, headers=PAGINA)  # noqa: E731
    assert pedir(documento="111.111.111-11").status_code == 422
    assert pedir(valor="5").status_code == 422 and pedir(valor="2000,01").status_code == 422
    assert pedir(valor="abc").status_code == 422
    r = pedir()
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["status"] == "pendente" and rec["valor"] == 5000 and rec["copia_cola"] and rec["qr_base64"]
    (criado,) = pix.mp.criados
    assert (
        criado["ref"] == rec["id"]
        and criado["doc"] == ("CPF", "52998224725")
        and criado["email"] == "cliente.pix@gmail.com"
    )
    with pix.banco.tx() as c:
        assert c.one("SELECT documento FROM pagamentos")["documento"] == "CPF •••4725"  # só o fim do CPF fica aqui
    # aviso com a assinatura errada: recusado, nada muda
    assert _aviso(pix, criado["id"], segredo="outro").status_code == 401
    # aviso certo, mas o Mercado Pago ainda diz "pendente": nada entra
    assert _aviso(pix, criado["id"]).status_code == 200
    assert nav.get("/api/conta").json()["saldo"] == 0
    # pago: o aviso faz a API consultar o Mercado Pago, e o valor entra uma vez só
    pix.mp.situacao[criado["id"]]["status_mp"] = "approved"
    for _ in range(3):
        assert _aviso(pix, criado["id"]).status_code == 200
    conta = nav.get("/api/conta").json()
    assert conta["saldo"] == 5000 and conta["plano"] == "pago"  # a 1ª recarga tira a conta do teste
    extrato = nav.get("/api/saldo").json()["extrato"]
    assert [(m["tipo"], m["valor"], m["descricao"]) for m in extrato] == [("recarga", 5000, "Recarga por Pix")]
    rec = nav.get(f"/api/pagamentos/{rec['id']}").json()
    assert rec["status"] == "aprovado" and rec["creditado_em"] and rec["copia_cola"] is None  # o QR some depois
    # o painel vê a recarga
    (p,) = pix.get(f"{PAINEL}/api/pagamentos").json()["pagamentos"]
    assert (p["email"], p["valor"], p["status"]) == ("cliente.pix@gmail.com", 5000, "aprovado")


def test_wrong_amount_or_someone_elses_payment_is_never_credited(pix):
    nav = entrar(pix, "valor.errado@gmail.com")
    rec = nav.post("/api/pagamentos/pix", json={"valor": "100", "documento": CPF}, headers=PAGINA).json()
    pid = pix.mp.criados[0]["id"]
    pix.mp.situacao[pid].update(status_mp="approved", valor=100)  # pagou R$ 1,00 em vez de R$ 100,00
    assert _aviso(pix, pid).status_code == 200
    assert nav.get("/api/conta").json()["saldo"] == 0
    assert nav.get(f"/api/pagamentos/{rec['id']}").json()["status"] == "divergente"
    # aviso de um pagamento que não é de nenhuma recarga daqui: ignorado
    pix.mp.situacao["999"] = {"status_mp": "approved", "valor": 10000, "ref": "pag_de_outro_lugar"}
    assert _aviso(pix, "999").json() == {"ok": True, "ignorado": True}
    # outra conta não vê a recarga (nem por id)
    outro = entrar(pix, "curioso@gmail.com")
    assert outro.get(f"/api/pagamentos/{rec['id']}").status_code == 404
    assert outro.get("/api/pagamentos").json() == []


def test_without_the_webhook_the_page_polling_confirms_with_mercado_pago(pix):
    nav = entrar(pix, "sem.aviso@gmail.com")
    rec = nav.post("/api/pagamentos/pix", json={"valor": "20", "documento": CPF}, headers=PAGINA).json()
    pid = pix.mp.criados[0]["id"]
    pix.mp.situacao[pid]["status_mp"] = "approved"
    assert nav.get(f"/api/pagamentos/{rec['id']}").json()["status"] == "pendente"  # conferido há menos de 10 s
    with pix.banco.tx() as c:
        c.run("UPDATE pagamentos SET conferido_em = conferido_em - 60, criado = criado - 60")
    assert nav.get(f"/api/pagamentos/{rec['id']}").json()["status"] == "aprovado"
    assert nav.get("/api/conta").json()["saldo"] == 2000


def test_a_refund_after_the_credit_takes_the_balance_back(pix):
    nav = entrar(pix, "estorno@gmail.com")
    nav.post("/api/pagamentos/pix", json={"valor": "30", "documento": CPF}, headers=PAGINA)
    pid = pix.mp.criados[0]["id"]
    pix.mp.situacao[pid]["status_mp"] = "approved"
    _aviso(pix, pid)
    assert nav.get("/api/conta").json()["saldo"] == 3000
    pix.mp.situacao[pid]["status_mp"] = "refunded"
    _aviso(pix, pid)
    assert nav.get("/api/conta").json()["saldo"] == 0
    tipos = [m["tipo"] for m in nav.get("/api/saldo").json()["extrato"]]
    assert tipos == ["ajuste", "recarga"]


def test_limits_on_open_pix_and_disabled_payments(pix, banco):
    nav = entrar(pix, "muitos.pix@gmail.com")
    for _ in range(3):
        assert (
            nav.post("/api/pagamentos/pix", json={"valor": "10", "documento": CPF}, headers=PAGINA).status_code == 200
        )
    r = nav.post("/api/pagamentos/pix", json={"valor": "10", "documento": CPF}, headers=PAGINA)
    assert r.status_code == 429 and "aguardando" in r.json()["detail"]
    sem_mp = cliente_teste(config_teste(banco), admin=False)
    assert sem_mp.post("/api/pagamentos/mercadopago?data.id=1&type=payment").status_code == 404
