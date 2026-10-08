"""Formulário de suporte da página do cliente: guarda no banco e manda por e-mail (SMTP).

Limite contra spam (por dispositivo, IP e cliente, em janelas de 1 h e 24 h): `LIMITES`.
A mensagem fica no banco mesmo sem e-mail configurado, e o /aprovar mostra as últimas.

E-mail (variáveis do .env; sem RENDER2GO_SMTP_HOST, só guarda):
    RENDER2GO_SMTP_HOST, RENDER2GO_SMTP_PORTA (587), RENDER2GO_SMTP_TLS (starttls | ssl | nenhum),
    RENDER2GO_SMTP_USUARIO, RENDER2GO_SMTP_SENHA, RENDER2GO_SUPORTE_DE (remetente), RENDER2GO_SUPORTE_PARA (você)
"""

from __future__ import annotations

import os
import re
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

# (campo, janela em segundos, máximo de mensagens)
LIMITES = [
    ("dispositivo", 3600, 3),
    ("dispositivo", 86400, 10),
    ("ip", 3600, 5),
    ("ip", 86400, 20),
    ("cliente_id", 3600, 5),
    ("cliente_id", 86400, 15),
]
EMAIL = re.compile(r"^[^@\s<>\"',;]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")


@dataclass(frozen=True)
class ConfigEmail:
    host: str
    para: str
    de: str
    porta: int = 587
    tls: str = "starttls"
    usuario: str | None = None
    senha: str | None = None

    @classmethod
    def do_ambiente(cls) -> ConfigEmail | None:
        host = os.environ.get("RENDER2GO_SMTP_HOST", "").strip()
        para = os.environ.get("RENDER2GO_SUPORTE_PARA", "").strip()
        if not host or not para:
            return None
        usuario = os.environ.get("RENDER2GO_SMTP_USUARIO", "").strip() or None
        return cls(
            host=host,
            para=para,
            de=os.environ.get("RENDER2GO_SUPORTE_DE", "").strip() or usuario or para,
            porta=int(os.environ.get("RENDER2GO_SMTP_PORTA", 587)),
            tls=os.environ.get("RENDER2GO_SMTP_TLS", "starttls").strip().lower(),
            usuario=usuario,
            senha=os.environ.get("RENDER2GO_SMTP_SENHA") or None,
        )


def _linha(texto: str, limite: int = 120) -> str:
    """Uma linha só (cabeçalho de e-mail não aceita quebra)."""
    return " ".join(texto.split())[:limite]


def montar(cfg: ConfigEmail, msg: dict) -> EmailMessage:
    """msg: id, cliente_id, cliente_nome, plano, email, assunto, mensagem, ip, dispositivo, quando."""
    e = EmailMessage()
    assunto = _linha(msg.get("assunto") or msg["mensagem"], 80)
    e["Subject"] = f"[Render2Go] Suporte: {assunto}"
    e["From"] = cfg.de
    e["To"] = cfg.para
    e["Date"] = formatdate(localtime=False)
    e["Message-ID"] = make_msgid(domain=cfg.de.split("@")[-1] if "@" in cfg.de else None)
    if msg.get("email"):
        e["Reply-To"] = msg["email"]  # responder vai direto para o cliente
    quando = time.strftime("%d/%m/%Y %H:%M UTC", time.gmtime(msg["quando"]))
    e.set_content(
        f"Cliente: {msg.get('cliente_nome') or '?'} ({msg['cliente_id']}, plano {msg.get('plano') or '?'})\n"
        f"E-mail para resposta: {msg.get('email') or 'não informado'}\n"
        f"Quando: {quando}\n"
        f"IP: {msg.get('ip') or '?'} · dispositivo: {msg.get('dispositivo') or '?'}\n"
        f"Mensagem {msg['id']}:\n\n{msg['mensagem']}\n"
    )
    return e


def enviar(cfg: ConfigEmail, mensagem: EmailMessage, timeout: float = 20) -> None:
    contexto = ssl.create_default_context()
    if cfg.tls == "ssl":
        servidor: smtplib.SMTP = smtplib.SMTP_SSL(cfg.host, cfg.porta, timeout=timeout, context=contexto)
    else:
        servidor = smtplib.SMTP(cfg.host, cfg.porta, timeout=timeout)
    with servidor as s:
        if cfg.tls == "starttls":
            s.starttls(context=contexto)
        if cfg.usuario and cfg.senha:
            s.login(cfg.usuario, cfg.senha)
        s.send_message(mensagem)
