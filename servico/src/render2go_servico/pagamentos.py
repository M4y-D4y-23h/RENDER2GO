"""Recarga do saldo por Pix (ADR 0008): Mercado Pago, API de pagamentos (`/v1/payments`).

Fluxo:
1. O cliente escolhe o valor e informa o CPF ou o CNPJ de quem paga (o Mercado Pago exige o
   documento no Pix). A API cria o pagamento com `external_reference` = o id da recarga e uma chave
   de idempotência, e mostra o QR Code e o "copia e cola" (vale 60 min).
2. O Mercado Pago avisa no webhook (`/api/pagamentos/mercadopago`). A API confere a assinatura
   (cabeçalho x-signature, HMAC-SHA256 com o segredo do painel do Mercado Pago) e, mesmo assim,
   **consulta o pagamento na API do Mercado Pago**: só o que a API diz vale (status "approved",
   valor igual ao pedido, em reais, com a referência certa). Só então o valor entra no saldo, uma
   vez só (`Banco.creditar_pagamento`).
3. Sem webhook (fora do ar, atrasado), a página do cliente pergunta de tempos em tempos e a API
   confere direto no Mercado Pago (no máximo uma vez a cada 10 s por recarga).

Variáveis (sem elas, a recarga automática fica desligada e a página manda falar com o suporte):
    RENDER2GO_MP_ACCESS_TOKEN       Access Token de produção (Suas integrações → Credenciais)
    RENDER2GO_MP_WEBHOOK_SEGREDO    assinatura secreta dos webhooks (Webhooks → Configurar notificações)
    RENDER2GO_API_URL               https://<domínio> (o webhook é <domínio>/api/pagamentos/mercadopago)
    RENDER2GO_RECARGA_MIN, _MAX     limites de cada recarga em reais (padrões 10 e 2000)
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import requests

from render2go_servico import precos

API = "https://api.mercadopago.com"
VALIDADE_PIX_S = 60 * 60  # o Mercado Pago aceita de 30 min a 30 dias
CONFERIR_A_CADA_S = 10
# situação do Mercado Pago → situação da recarga
SITUACOES = {
    "pending": "pendente",
    "in_process": "pendente",
    "authorized": "pendente",
    "approved": "aprovado",
    "rejected": "recusado",
    "cancelled": "cancelado",
    "refunded": "devolvido",
    "charged_back": "contestado",
}
DIGITOS = re.compile(r"[^0-9]")  # só 0-9 (\D deixaria passar dígitos de outros alfabetos)


class PagamentoError(Exception):
    pass


@dataclass(frozen=True)
class ConfigMP:
    access_token: str
    segredo_webhook: str
    url_notificacao: str
    minimo: int = 10_00
    maximo: int = 2_000_00

    @classmethod
    def do_ambiente(cls) -> ConfigMP | None:
        token = os.environ.get("RENDER2GO_MP_ACCESS_TOKEN", "").strip()
        segredo = os.environ.get("RENDER2GO_MP_WEBHOOK_SEGREDO", "").strip()
        base = os.environ.get("RENDER2GO_API_URL", "").strip().rstrip("/")
        if not token or not segredo or not base.startswith("https://"):
            return None
        return cls(
            access_token=token,
            segredo_webhook=segredo,
            url_notificacao=f"{base}/api/pagamentos/mercadopago",
            minimo=precos.centavos(os.environ.get("RENDER2GO_RECARGA_MIN", "10")),
            maximo=precos.centavos(os.environ.get("RENDER2GO_RECARGA_MAX", "2000")),
        )


# -- CPF e CNPJ ----------------------------------------------------------------------------------


def _dv(numeros: str, pesos: list[int]) -> int:
    resto = sum(int(n) * p for n, p in zip(numeros, pesos, strict=True)) % 11
    return 0 if resto < 2 else 11 - resto


def documento(texto: str) -> tuple[str, str]:
    """('CPF' | 'CNPJ', só dígitos), com os dígitos verificadores conferidos; ValueError se não vale."""
    d = DIGITOS.sub("", texto or "")
    if len(d) == 11 and len(set(d)) > 1:
        if _dv(d[:9], list(range(10, 1, -1))) == int(d[9]) and _dv(d[:10], list(range(11, 1, -1))) == int(d[10]):
            return "CPF", d
    elif len(d) == 14 and len(set(d)) > 1:
        p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        if _dv(d[:12], p1) == int(d[12]) and _dv(d[:13], [6, *p1]) == int(d[13]):
            return "CNPJ", d
    raise ValueError("CPF ou CNPJ inválido")


def mascarar(tipo: str, numero: str) -> str:
    """O que fica no banco: só o fim do documento (o Mercado Pago guarda o número inteiro)."""
    return f"{tipo} •••{numero[-4:]}"


# -- Mercado Pago ---------------------------------------------------------------------------------


def assinatura_confere(segredo: str, x_signature: str, x_request_id: str | None, data_id: str | None) -> bool:
    """Confere o x-signature do webhook ("ts=...,v1=..."): HMAC-SHA256 do manifesto
    "id:<data.id em minúsculas>;request-id:<x-request-id>;ts:<ts>;" (partes ausentes saem)."""
    campos = dict(p.strip().split("=", 1) for p in (x_signature or "").split(",") if "=" in p)
    ts, v1 = campos.get("ts"), campos.get("v1")
    if not ts or not v1 or not segredo:
        return False
    partes = []
    if data_id:
        partes.append(f"id:{data_id.lower()}")
    if x_request_id:
        partes.append(f"request-id:{x_request_id}")
    partes.append(f"ts:{ts}")
    manifesto = ";".join(partes) + ";"
    esperado = hmac.new(segredo.encode(), manifesto.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado.encode(), v1.encode())  # em bytes: v1 pode vir com qualquer caractere


class MercadoPago:
    def __init__(self, cfg: ConfigMP, sessao: requests.Session | None = None, timeout: float = 20):
        self.cfg, self.timeout = cfg, timeout
        self.s = sessao or requests.Session()
        self.s.headers["Authorization"] = f"Bearer {cfg.access_token}"
        self.s.headers["User-Agent"] = "render2go"

    def criar_pix(self, recarga_id: str, valor: int, email: str, doc: tuple[str, str]) -> dict[str, Any]:
        """Cria o Pix da recarga; devolve id, situação, validade (epoch), copia e cola e QR (PNG em base64)."""
        expira = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=VALIDADE_PIX_S)
        corpo = {
            "transaction_amount": float(Decimal(valor) / 100),
            "description": f"Render2Go: recarga de saldo de {precos.reais(valor)}",
            "payment_method_id": "pix",
            "external_reference": recarga_id,
            "notification_url": self.cfg.url_notificacao,
            "date_of_expiration": expira.isoformat(timespec="milliseconds"),
            "statement_descriptor": "RENDER2GO",
            "payer": {"email": email, "identification": {"type": doc[0], "number": doc[1]}},
        }
        try:
            r = self.s.post(
                f"{API}/v1/payments",
                json=corpo,
                headers={"X-Idempotency-Key": recarga_id},  # a mesma recarga nunca vira dois Pix
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise PagamentoError(f"Mercado Pago inacessível: {type(e).__name__}") from e
        if r.status_code not in (200, 201):
            raise PagamentoError(f"Mercado Pago recusou a criação ({r.status_code}): {r.text[:300]}")
        p = r.json()
        dados = (p.get("point_of_interaction") or {}).get("transaction_data") or {}
        return {
            "id": str(p["id"]),
            "status": SITUACOES.get(p.get("status"), "pendente"),
            "expira": expira.timestamp(),
            "copia_cola": dados.get("qr_code"),
            "qr_base64": dados.get("qr_code_base64"),
        }

    def consultar(self, provedor_id: str) -> dict[str, Any]:
        """O pagamento como o Mercado Pago registrou: a única fonte que vale para creditar."""
        if not re.fullmatch(r"[0-9A-Za-z_-]{1,40}", provedor_id or ""):
            raise PagamentoError("id de pagamento inválido")
        try:
            r = self.s.get(f"{API}/v1/payments/{provedor_id}", timeout=self.timeout)
        except requests.RequestException as e:
            raise PagamentoError(f"Mercado Pago inacessível: {type(e).__name__}") from e
        if r.status_code != 200:
            raise PagamentoError(f"consulta recusada ({r.status_code})")
        p = r.json()
        valor = p.get("transaction_amount")
        return {
            "id": str(p.get("id")),
            "status_mp": p.get("status"),
            "status": SITUACOES.get(p.get("status"), "pendente"),
            "detalhe": p.get("status_detail"),
            "valor": int((Decimal(str(valor)) * 100).to_integral_value()) if valor is not None else None,
            "moeda": p.get("currency_id"),
            "referencia": p.get("external_reference"),
            "metodo": p.get("payment_method_id"),
        }
