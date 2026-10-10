"""Contas por e-mail, sessões e proteções de entrada (ADR 0007).

Entrada sem senha: a pessoa informa o e-mail, recebe um código de 8 dígitos (vale 10 min, uma vez,
5 tentativas; com os limites por e-mail, acertar por tentativa e erro fica fora de alcance) e entra.
No 1º acesso a conta nasce no teste gratuito. Só e-mails dos provedores grandes, que exigem
verificação para criar uma caixa (`DOMINIOS_PADRAO`): domínio descartável ou próprio de uma empresa
fica de fora, salvo se o dono do serviço acrescentar em RENDER2GO_EMAIL_DOMINIOS_EXTRA ou criar a
conta pelo painel. Os apelidos de uma mesma caixa (pontos e "+" no Gmail, "+" nos
outros, "-palavra" no Yahoo) são a mesma conta: um teste gratuito por caixa de e-mail.

Também aqui: o código do aplicativo autenticador (TOTP, RFC 6238) do painel e da verificação em duas
etapas do cliente, com o segredo cifrado no banco (AES-GCM, preso à conta) e os códigos de recuperação
guardados só como HMAC; a verificação "não sou um robô" (Cloudflare Turnstile, conferida no servidor);
o aparelho legível de uma sessão (pelo User-Agent); e a chave de rede dos limites (IPv6 pelo /64).
Biblioteca padrão, salvo a cifra (cryptography).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import ipaddress
import json
import os
import re
import secrets
import struct
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# provedores que pedem verificação (telefone, desafio) para abrir uma caixa; todos com donos conhecidos
DOMINIOS_PADRAO = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "outlook.com.br",
        "hotmail.com",
        "hotmail.com.br",
        "live.com",
        "msn.com",
        "yahoo.com",
        "yahoo.com.br",
        "ymail.com",
        "rocketmail.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "uol.com.br",
        "bol.com.br",
        "terra.com.br",
    }
)
GOOGLE = {"gmail.com", "googlemail.com"}
YAHOO = {"yahoo.com", "yahoo.com.br", "ymail.com", "rocketmail.com"}
LOCAL = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,63}$")
CODIGO = re.compile(r"^[0-9]{8}$")  # [0-9]: \d aceitaria dígitos de outros alfabetos
CODIGO_VALIDADE_S = 10 * 60
CODIGO_TENTATIVAS = 5
SESSAO_CLIENTE_S = 30 * 86400
SESSAO_ADMIN_S = 12 * 3600
# versões dos documentos que a pessoa aceita ao entrar (páginas /termos e /privacidade). Versão nova: quem
# já estava conectado aceita na própria página (GET /api/conta traz os pendentes; POST /api/conta/aceitar)
DOCUMENTOS = {"termos": "termos-v2", "privacidade": "privacidade-v2"}
TURNSTILE_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
# confirmação recente ("é você mesmo?") antes de ações sensíveis; a sessão aberta há menos que isso já conta
CONFIRMACAO_S = 10 * 60
MFA_DESAFIO_S = 5 * 60  # entre o código do e-mail e o do aplicativo, na entrada com verificação em duas etapas
MFA_PENDENTE_S = 15 * 60  # para ler o QR Code e confirmar o 1º código
CODIGOS_RECUPERACAO = 10
# sem letras e números que se confundem (0/o, 1/l/i): a pessoa copia de um papel
ALFABETO_RECUPERACAO = "abcdefghjkmnpqrstuvwxyz23456789"
RECUPERACAO = re.compile(r"^[a-hj-km-np-z2-9]{10}$")


class EmailRecusado(ValueError):
    pass


class DominioRecusado(EmailRecusado):
    """Formato certo, provedor fora da lista (a conta criada pelo painel ainda entra)."""


def dominios_aceitos() -> frozenset[str]:
    """RENDER2GO_EMAIL_DOMINIOS troca a lista inteira; RENDER2GO_EMAIL_DOMINIOS_EXTRA acrescenta."""

    def lista(nome: str) -> set[str]:
        return {d.strip().lower().lstrip("@") for d in os.environ.get(nome, "").split(",") if d.strip()}

    base = lista("RENDER2GO_EMAIL_DOMINIOS") or set(DOMINIOS_PADRAO)
    return frozenset(base | lista("RENDER2GO_EMAIL_DOMINIOS_EXTRA"))


def normalizar(email: str, aceitos: frozenset[str] | None = None, exigir_dominio: bool = True) -> tuple[str, str]:
    """(endereço para enviar o código, chave da conta). EmailRecusado se o formato não serve;
    DominioRecusado se o provedor não está na lista (`exigir_dominio=False`: o painel cria a conta de
    um e-mail de empresa). A chave junta os apelidos da mesma caixa: um teste gratuito por pessoa."""
    texto = (email or "").strip()
    if len(texto) > 254 or texto.count("@") != 1:
        raise EmailRecusado("e-mail inválido")
    local, dominio = texto.split("@")
    if not local.isascii() or not dominio.isascii():  # sem letras parecidas de outros alfabetos
        raise EmailRecusado("e-mail inválido")
    local, dominio = local.lower(), dominio.lower().rstrip(".")
    if not LOCAL.match(local) or ".." in local:
        raise EmailRecusado("e-mail inválido")
    if "." not in dominio or not re.fullmatch(r"[a-z0-9.-]{3,190}", dominio) or ".." in dominio:
        raise EmailRecusado("e-mail inválido")
    if exigir_dominio and dominio not in (aceitos or dominios_aceitos()):
        raise DominioRecusado(
            "use um e-mail do Gmail, Outlook/Hotmail, Yahoo, iCloud, UOL, BOL ou Terra"
            " (outros provedores não são aceitos, por segurança)"
        )
    chave = local.split("+", 1)[0]
    if dominio in GOOGLE:
        dominio_chave, chave = "gmail.com", chave.replace(".", "")
    elif dominio in YAHOO:
        dominio_chave, chave = dominio, chave.split("-", 1)[0]  # "nome-palavra": endereço descartável do Yahoo
    else:
        dominio_chave = dominio
    if not chave:
        raise EmailRecusado("e-mail inválido")
    return f"{local}@{dominio}", f"{chave}@{dominio_chave}"


# -- códigos de entrada ----------------------------------------------------------------------


def novo_codigo() -> str:
    return f"{secrets.randbelow(10**8):08d}"


def limpar_codigo(texto: str) -> str:
    """O código como a pessoa digitou ("1234 5678", "1234-5678") → só os dígitos."""
    return re.sub(r"[^0-9]", "", texto or "")


def chave_contas(segredo_contas: str | None, segredo_aprovacao: str) -> bytes:
    """Chave dos resumos dos códigos: RENDER2GO_SEGREDO_CONTAS ou, sem ele, uma chave derivada
    (com separação de domínio) do segredo de aprovação."""
    base = segredo_contas or segredo_aprovacao
    return hmac.new(base.encode(), b"render2go:codigos-de-entrada:v1", hashlib.sha256).digest()


def resumo_codigo(chave: bytes, email_norm: str, codigo: str) -> str:
    """O banco guarda só este HMAC (um código vazado do banco não serve sem a chave)."""
    return hmac.new(chave, f"{email_norm}:{codigo}".encode(), hashlib.sha256).hexdigest()


def resumo_email(chave: bytes, email_norm: str) -> str:
    """Resumo irreversível do e-mail de uma conta excluída (só serve para negar um 2º teste grátis)."""
    return hmac.new(chave, f"conta-excluida:{email_norm}".encode(), hashlib.sha256).hexdigest()


# -- TOTP do painel (aplicativo autenticador) ------------------------------------------------


def novo_segredo_totp() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _b32(segredo: str) -> bytes:
    s = segredo.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def totp(segredo: str, contador: int, digitos: int = 6) -> str:
    msg = struct.pack(">Q", contador)
    h = hmac.new(_b32(segredo), msg, hashlib.sha1).digest()
    o = h[-1] & 0x0F
    n = (struct.unpack(">I", h[o : o + 4])[0] & 0x7FFFFFFF) % 10**digitos
    return f"{n:0{digitos}d}"


def totp_confere(segredo: str, codigo: str, agora: float | None = None, janela: int = 1, passo: int = 30) -> int | None:
    """Contador (passo de 30 s) do código aceito, ou None. Aceita o passo anterior e o seguinte
    (relógio do celular um pouco fora). Quem chama recusa contador já usado (reuso do código)."""
    if not re.fullmatch(r"[0-9]{6}", codigo or ""):  # [0-9]: \d aceitaria dígitos de outros alfabetos
        return None
    atual = int((agora if agora is not None else time.time()) // passo)
    for c in range(atual - janela, atual + janela + 1):
        if iguais(totp(segredo, c), codigo):
            return c
    return None


def iguais(a: str, b: str) -> bool:
    """Comparação em tempo constante de textos vindos de fora: em bytes, porque o compare_digest de
    str recusa (TypeError, erro 500) qualquer caractere fora do ASCII."""
    return hmac.compare_digest(a.encode(), b.encode())


def uri_totp(segredo: str, conta: str = "painel", emissor: str = "Render2Go") -> str:
    rotulo = urllib.parse.quote(f"{emissor}:{conta}")
    return f"otpauth://totp/{rotulo}?secret={segredo}&issuer={urllib.parse.quote(emissor)}&digits=6&period=30"


def qr_svg(texto: str) -> str:
    """QR Code em SVG (preto no branco, com a margem de 4 módulos que os leitores pedem; o tamanho fica com
    a página). A página mostra por uma URL blob:, sem innerHTML."""
    import segno

    saida = io.BytesIO()
    segno.make(texto, error="m").save(
        saida, kind="svg", scale=1, border=4, dark="#000", light="#fff", xmldecl=False, omitsize=True, nl=False
    )
    return saida.getvalue().decode()


# -- segredos do autenticador no banco e códigos de recuperação ------------------------------------


def chave_segredos(segredo_contas: str | None, segredo_aprovacao: str) -> bytes:
    """Chave AES-256 dos segredos do aplicativo autenticador guardados no banco (com separação de domínio
    da chave dos códigos de entrada). Trocar RENDER2GO_SEGREDO_CONTAS depois de haver verificação em duas
    etapas ligada faz esses segredos deixarem de abrir: a entrada que depende deles é recusada (docs/13)."""
    base = segredo_contas or segredo_aprovacao
    return hmac.new(base.encode(), b"render2go:segredos-totp:v1", hashlib.sha256).digest()


def cifrar(chave: bytes, texto: str, contexto: str) -> str:
    """AES-GCM com nonce aleatório. `contexto` (ex.: "cliente:cli_x") entra como dado associado: o valor
    copiado para a linha de outra conta, no próprio banco, não abre."""
    nonce = secrets.token_bytes(12)
    cifrado = AESGCM(chave).encrypt(nonce, texto.encode(), contexto.encode())
    return "v1:" + base64.urlsafe_b64encode(nonce + cifrado).decode()


def decifrar(chave: bytes, valor: str | None, contexto: str) -> str:
    """O texto de `cifrar`; ValueError se a chave, o contexto ou o valor não conferem (quem chama recusa)."""
    if not valor or not valor.startswith("v1:"):
        raise ValueError("segredo em formato desconhecido")
    try:
        bruto = base64.urlsafe_b64decode(valor[3:])
        return AESGCM(chave).decrypt(bruto[:12], bruto[12:], contexto.encode()).decode()
    except (InvalidTag, ValueError) as e:  # binascii.Error e UnicodeDecodeError são ValueError
        raise ValueError("segredo não confere (chave trocada ou valor alterado)") from e


def novos_codigos_recuperacao(n: int = CODIGOS_RECUPERACAO) -> list[str]:
    """Códigos de uso único para entrar sem o celular: 10 letras e números (~49 bits cada)."""
    return ["".join(secrets.choice(ALFABETO_RECUPERACAO) for _ in range(10)) for _ in range(n)]


def formatar_recuperacao(codigo: str) -> str:
    return f"{codigo[:5]}-{codigo[5:]}"


def limpar_recuperacao(texto: str | None) -> str | None:
    """O código de recuperação como a pessoa digitou ("ABCDE-FGHJK", com espaços) → as 10 letras e números."""
    t = re.sub(r"[\s-]", "", texto or "").lower()
    return t if RECUPERACAO.match(t) else None


def resumo_recuperacao(chave: bytes, dono: str, codigo: str) -> str:
    """O banco guarda só este HMAC de cada código de recuperação (preso à conta)."""
    return hmac.new(chave, f"recuperacao:{dono}:{codigo}".encode(), hashlib.sha256).hexdigest()


# -- sessões e avisos --------------------------------------------------------------------------------


def ref_sessao(sid: str) -> str:
    """Referência pública de uma sessão (lista de aparelhos): derivada do id, que nunca sai do servidor."""
    return hashlib.sha256(f"ref:{sid}".encode()).hexdigest()[:20]


# a ordem importa: o Edge e o Opera também dizem "Chrome"; o Chrome também diz "Safari"; o Android, "Linux"
NAVEGADORES = (
    (r"Edg(e|A|iOS)?/", "Edge"),
    (r"OPR/|Opera", "Opera"),
    (r"SamsungBrowser/", "Samsung Internet"),
    (r"Firefox/|FxiOS/", "Firefox"),
    (r"Chrome/|CriOS/", "Chrome"),
    (r"Version/[0-9.]+.*Safari/", "Safari"),
)
SISTEMAS = (
    (r"iPhone", "iPhone"),
    (r"iPad", "iPad"),
    (r"Android", "Android"),
    (r"CrOS", "ChromeOS"),
    (r"Windows", "Windows"),
    (r"Macintosh|Mac OS X", "macOS"),
    (r"Linux", "Linux"),
)


def aparelho(agente: str | None) -> str:
    """ "Chrome no Android", "Safari no iPhone"...: só para a pessoa reconhecer o aparelho na lista."""
    ua = (agente or "")[:300]
    nav = next((nome for padrao, nome in NAVEGADORES if re.search(padrao, ua)), None)
    so = next((nome for padrao, nome in SISTEMAS if re.search(padrao, ua)), None)
    if nav and so:
        return f"{nav} no {so}"
    return nav or so or "Aparelho não identificado"


def mascarar_email(email: str | None) -> str | None:
    """ "fulana@gmail.com" → "fu***@gmail.com": registros e avisos sem o endereço inteiro."""
    if not email or "@" not in email:
        return None
    local, dominio = email.rsplit("@", 1)
    return f"{local[:2] if len(local) > 2 else local[:1]}***@{dominio}"


def documentos_pendentes(conta: dict) -> list[str]:
    """Documentos (termos, privacidade) cuja versão atual a conta ainda não aceitou."""
    aceitos = {"termos": conta.get("termos_versao"), "privacidade": conta.get("privacidade_versao")}
    return [doc for doc, versao in DOCUMENTOS.items() if aceitos.get(doc) != versao]


# -- acessos do painel por pessoa (ADR 0009) --------------------------------------------------------

PAPEIS = ("dono", "operador")
USUARIO_ADMIN = re.compile(r"^[a-z][a-z0-9._-]{2,31}$")  # "admin-<8 hex>" é a chave do servidor (emergência)
CONVITE_VALIDADE_S = 24 * 3600
SENHA_MIN, SENHA_MAX = 12, 128
# scrypt (RFC 7914): 32 MiB e ~0,1 s por conferência. No máximo 4 ao mesmo tempo no processo: tentativas em
# massa esperam a vez em vez de somar memória (o painel ainda fica atrás do endereço secreto e dos limites)
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1
_SCRYPT_VEZ = threading.BoundedSemaphore(4)
# senhas e pedaços que aparecem primeiro nas listas de vazamentos (a regra principal é o tamanho)
SENHAS_COMUNS = (
    "123456",
    "654321",
    "password",
    "senha",
    "qwerty",
    "asdfgh",
    "abc123",
    "admin",
    "render2go",
    "gpu2go",
    "painel",
    "iloveyou",
    "brasil",
    "102030",
    "111111",
    "000000",
)


def normalizar_usuario(texto: str | None) -> str:
    """O nome de acesso como fica guardado (minúsculas); ValueError se não serve."""
    u = (texto or "").strip().lower()
    if not USUARIO_ADMIN.match(u) or u.startswith("admin-"):
        raise ValueError("use de 3 a 32 letras minúsculas, números, ponto, hífen ou _, começando por uma letra")
    return u


def senha_fraca(senha: str, usuario: str) -> str | None:
    """Motivo para recusar a senha, ou None. Tamanho acima de tudo (NIST SP 800-63B): 12 ou mais caracteres,
    sem o nome de acesso e sem os pedaços mais comuns das listas de vazamentos."""
    senha = unicodedata.normalize("NFC", senha)
    if not SENHA_MIN <= len(senha) <= SENHA_MAX:
        return (
            f"use de {SENHA_MIN} a {SENHA_MAX} caracteres (uma frase curta é fácil de lembrar e difícil de adivinhar)"
        )
    baixa = senha.lower()
    if usuario and usuario.lower() in baixa:
        return "a senha não pode conter o nome de acesso"
    if len(set(baixa)) < 5:
        return "use mais caracteres diferentes"
    if any(comum in baixa for comum in SENHAS_COMUNS):
        return "a senha tem uma sequência muito comum; escolha outra"
    return None


def _scrypt(senha: str, sal: bytes, n: int, r: int, p: int) -> bytes:
    # NFC: a mesma senha com acento digitada no celular e no computador vira os mesmos bytes
    dados = unicodedata.normalize("NFC", senha).encode()
    with _SCRYPT_VEZ:
        return hashlib.scrypt(dados, salt=sal, n=n, r=r, p=p, maxmem=128 * r * n * 2, dklen=32)


def hash_senha(senha: str) -> str:
    """ "scrypt$n$r$p$sal$resumo" (base64): o banco nunca vê a senha."""
    sal = secrets.token_bytes(16)
    resumo = _scrypt(senha, sal, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    b64 = base64.b64encode
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64(sal).decode()}${b64(resumo).decode()}"


# conferência com um resumo de mentira quando o nome de acesso não existe: o tempo da resposta é o mesmo
_SENHA_FALSA = "scrypt$32768$8$1$AAAAAAAAAAAAAAAAAAAAAA==$" + "A" * 43 + "="


def senha_confere(senha: str, guardado: str | None) -> bool:
    """Compara em tempo constante; sem resumo guardado (nome inexistente), gasta o mesmo tempo e recusa."""
    try:
        nome, n, r, p, sal, resumo = (guardado or _SENHA_FALSA).split("$")
        if nome != "scrypt":
            return False
        calculado = _scrypt(senha, base64.b64decode(sal), int(n), int(r), int(p))
        return hmac.compare_digest(calculado, base64.b64decode(resumo)) and guardado is not None
    except (ValueError, TypeError):
        return False


# -- "não sou um robô" (Cloudflare Turnstile) ----------------------------------------------------


@dataclass(frozen=True)
class ConfigTurnstile:
    site: str  # chave pública (vai para a página)
    segredo: str  # chave secreta (só no servidor)
    hostname: str | None = None  # o domínio do serviço: o desafio resolvido em outro site não vale

    @classmethod
    def do_ambiente(cls) -> ConfigTurnstile | None:
        site = os.environ.get("RENDER2GO_TURNSTILE_SITE", "").strip()
        segredo = os.environ.get("RENDER2GO_TURNSTILE_SEGREDO", "").strip()
        if not site or not segredo:
            return None
        return cls(site, segredo, os.environ.get("DOMINIO", "").strip() or None)


def mesmo_site(servido: Any, esperado: str) -> bool:
    """O desafio foi resolvido no endereço do serviço (`DOMINIO`). A Cloudflare informa onde ele foi servido;
    vale o próprio endereço ou um domínio acima dele: o widget cadastrado com `render2go.com` cobre
    `app.render2go.com`, e todos esses subdomínios são do dono. Sem a informação, as outras conferências
    (chave secreta, uso único, ação) seguem valendo."""
    if servido is None:
        return True
    if not isinstance(servido, str):
        return False
    s, e = servido.strip().lower().rstrip("."), esperado.strip().lower().rstrip(".")
    return s == e or ("." in s and e.endswith("." + s))


def turnstile_confere(cfg: ConfigTurnstile, resposta: str, ip: str | None, acao: str | None = None) -> str | None:
    """None se a pessoa passou no desafio; senão, o motivo. Falha de rede = recusa (fail-closed)."""
    if not resposta or len(resposta) > 2048:
        return "verificação anti-robô ausente"
    dados = {"secret": cfg.segredo, "response": resposta, "idempotency_key": secrets.token_hex(16)}
    if ip:
        dados["remoteip"] = ip
    req = urllib.request.Request(
        TURNSTILE_URL, data=urllib.parse.urlencode(dados).encode(), method="POST", headers={"User-Agent": "render2go"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read(64 * 1024))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return f"verificação anti-robô indisponível ({type(e).__name__})"
    if not res.get("success"):
        return "verificação anti-robô recusada: " + ",".join(res.get("error-codes") or ["desconhecido"])
    if cfg.hostname and not mesmo_site(res.get("hostname"), cfg.hostname):
        return "verificação anti-robô de outro site"
    if acao and res.get("action") not in (acao, None, ""):
        return "verificação anti-robô de outra ação"
    return None


# -- rede de origem --------------------------------------------------------------------------


def rede(ip: str | None) -> str:
    """Chave de rede para limites: IPv4 inteiro; IPv6 pelo /64 (quem tem um endereço costuma ter o /64
    inteiro, e trocar de endereço não pode zerar o limite)."""
    if not ip:
        return "desconhecido"
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ip[:64]
    if a.version == 6:
        if a.ipv4_mapped:
            return str(a.ipv4_mapped)
        return str(ipaddress.ip_network(f"{a}/64", strict=False))
    return str(a)


def ip_permitido(ip: str | None, redes: list[str]) -> bool:
    """Lista vazia = qualquer IP. Senão, o IP precisa estar numa das redes (ex.: 200.1.2.3/32)."""
    if not redes:
        return True
    try:
        a = ipaddress.ip_address(ip or "")
    except ValueError:
        return False
    for r in redes:
        try:
            if a in ipaddress.ip_network(r.strip(), strict=False):
                return True
        except ValueError:
            continue
    return False
