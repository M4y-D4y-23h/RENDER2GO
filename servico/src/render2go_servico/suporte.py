"""E-mail do serviço (SMTP): mensagens do suporte para o dono e códigos de entrada para os clientes.

Suporte: guarda no banco e manda por e-mail. Limite contra spam (por dispositivo, IP e cliente, em
janelas de 1 h e 24 h): `LIMITES`. A mensagem fica no banco mesmo sem e-mail configurado, e o painel
mostra as últimas.

Códigos de entrada (ADR 0007): `montar_codigo`, do remetente RENDER2GO_EMAIL_DE (ex.: "Render2Go
<nao-responda@render2go.com>"). Sem SMTP, ninguém consegue entrar pelo e-mail: a API recusa (503).

E-mail (variáveis do .env; sem RENDER2GO_SMTP_HOST, o suporte só guarda e o login por e-mail fica fora):
    RENDER2GO_SMTP_HOST, RENDER2GO_SMTP_PORTA (587), RENDER2GO_SMTP_TLS (starttls | ssl | nenhum),
    RENDER2GO_SMTP_USUARIO, RENDER2GO_SMTP_SENHA, RENDER2GO_SUPORTE_DE (remetente), RENDER2GO_SUPORTE_PARA (você),
    RENDER2GO_EMAIL_DE (remetente dos códigos; padrão: RENDER2GO_SUPORTE_DE)
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
# sem código de acesso (quem não consegue entrar): e-mail obrigatório e limites menores
LIMITES_ANONIMO = [
    ("dispositivo", 3600, 2),
    ("ip", 3600, 3),
    ("ip", 86400, 10),
]
EMAIL = re.compile(r"^[^@\s<>\"',;]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")


@dataclass(frozen=True)
class ConfigEmail:
    host: str
    para: str  # quem recebe as mensagens do suporte ("" = o suporte só guarda no banco)
    de: str
    porta: int = 587
    tls: str = "starttls"
    usuario: str | None = None
    senha: str | None = None
    de_sistema: str | None = None  # remetente dos códigos de entrada (padrão: `de`)

    @classmethod
    def do_ambiente(cls) -> ConfigEmail | None:
        host = os.environ.get("RENDER2GO_SMTP_HOST", "").strip()
        if not host:
            return None
        para = os.environ.get("RENDER2GO_SUPORTE_PARA", "").strip()
        usuario = os.environ.get("RENDER2GO_SMTP_USUARIO", "").strip() or None
        de_sistema = os.environ.get("RENDER2GO_EMAIL_DE", "").strip() or None
        # remetente do suporte: o configurado; senão o usuário do SMTP, se for um e-mail (Gmail); senão o
        # dos códigos. No SES o usuário do SMTP não é e-mail, e um remetente inválido faz o envio ser recusado
        de = (
            os.environ.get("RENDER2GO_SUPORTE_DE", "").strip()
            or (usuario if usuario and "@" in usuario else None)
            or de_sistema
            or para
        )
        return cls(
            host=host,
            para=para,
            de=de,
            porta=int(os.environ.get("RENDER2GO_SMTP_PORTA", 587)),
            tls=os.environ.get("RENDER2GO_SMTP_TLS", "starttls").strip().lower(),
            usuario=usuario,
            senha=os.environ.get("RENDER2GO_SMTP_SENHA") or None,
            de_sistema=de_sistema,
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
    cliente = (
        f"{msg.get('cliente_nome') or '?'} ({msg['cliente_id']}, plano {msg.get('plano') or '?'})"
        if msg.get("cliente_id")
        else "sem código de acesso (página de suporte)"
    )
    e.set_content(
        f"Cliente: {cliente}\n"
        f"E-mail para resposta: {msg.get('email') or 'não informado'}\n"
        f"Quando: {quando}\n"
        f"IP: {msg.get('ip') or '?'} · dispositivo: {msg.get('dispositivo') or '?'}\n"
        f"Mensagem {msg['id']}:\n\n{msg['mensagem']}\n"
    )
    return e


def montar_codigo(cfg: ConfigEmail, para: str, codigo: str, minutos: int) -> EmailMessage:
    """E-mail com o código de entrada: só texto, sem links (nada para clicar = nada para imitar)."""
    e = EmailMessage()
    remetente = cfg.de_sistema or cfg.de
    e["Subject"] = f"{codigo} é o seu código de acesso ao Render2Go"
    e["From"] = remetente
    e["To"] = para
    e["Date"] = formatdate(localtime=False)
    e["Message-ID"] = make_msgid(domain=remetente.rsplit("@", 1)[-1].strip("> ") if "@" in remetente else None)
    e["Auto-Submitted"] = "auto-generated"  # resposta automática de férias não volta para cá
    e.set_content(
        f"Seu código de acesso ao Render2Go é:\n\n    {codigo}\n\n"
        f"Ele vale por {minutos} minutos e só uma vez. Digite o código na página em que você pediu.\n\n"
        "Se não foi você que pediu, ignore este e-mail: ninguém entra na sua conta sem o código.\n"
        "Nunca pedimos este código por telefone, WhatsApp ou outro e-mail.\n\n"
        "Render2Go · vídeos curtos com IA para pequenos negócios\n"
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
