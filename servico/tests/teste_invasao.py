"""Teste de invasão caixa-preta do Render2Go (docs/13, seção 3): a API real, num Postgres de teste, atrás
do Caddyfile de produção (com TLS local), atacada por fora como um invasor faria: descoberta de caminhos,
travessia, cabeçalhos e cookies, autenticação, IDOR, CSRF, injeção, webhook, sessões e limites.

Uso (não é coletado pelo pytest; precisa do binário do Caddy, conferido pelo SHA-512 do release):
    cd servico && uv run python tests/teste_invasao.py --caddy /caminho/caddy \\
        --db "postgresql://postgres@/render2go_invasao?host=/var/tmp&port=55432"
O banco é APAGADO a cada rodada: o nome precisa conter "teste", "invasao" ou "pentest".
Imprime uma linha por verificação, OK (a defesa segurou) ou FALHA (achado), e o resumo no fim; sai com 1
se houver achado. Cada grupo de ataques sai de um IP de loopback próprio (127.0.0.x), como redes diferentes.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from pathlib import Path
from urllib.parse import urlsplit

import requests
import urllib3

TESTES = Path(__file__).resolve().parent
SERVICO = TESTES.parent
sys.path.insert(0, str(TESTES))
warnings.filterwarnings("ignore")
urllib3.disable_warnings()

import uvicorn  # noqa: E402
from test_api import ADMIN, TOTP, Armazem  # noqa: E402
from test_pagamentos import MercadoPagoFalso  # noqa: E402

from render2go_servico import contas, pagamentos, suporte  # noqa: E402
from render2go_servico.api import Config, criar_app  # noqa: E402
from render2go_servico.db import Banco  # noqa: E402

AQUI = Path(tempfile.mkdtemp(prefix="render2go-invasao-"))  # Caddyfile, certificados e logs desta rodada
PORTA_API, PORTA_TLS, PORTA_HTTP = 18100, 18443, 18080
BASE = f"https://localhost:{PORTA_TLS}"
PAINEL = "/painel-" + secrets.token_urlsafe(18)
WORKER = secrets.token_urlsafe(32)
SEGREDO_MP = "segredo-webhook-" + secrets.token_hex(8)
TABELAS = [
    "codigos_recuperacao",
    "admin_convites",
    "admins",
    "emails_antigos",
    "auditoria_admin",
    "pagamentos",
    "tokens_worker",
    "estado",
    "comandos",
    "acessos",
    "aceites",
    "limites",
    "codigos",
    "sessoes",
    "avaliacoes",
    "montagens",
    "interesses",
    "movimentos",
    "geradores",
    "cookies",
    "suporte",
    "consentimentos",
    "eventos",
    "jobs",
    "fotos",
    "lotes",
    "clientes",
]

resultados: list[tuple[bool, str, str]] = []


def conferir(ok: bool, nome: str, detalhe: str = "") -> bool:
    resultados.append((ok, nome, detalhe))
    print(f"{'OK    ' if ok else 'FALHA '} {nome}" + (f"  [{detalhe}]" if detalhe and not ok else ""), flush=True)
    return ok


# -- ambiente -------------------------------------------------------------------------------------


def subir(db: str, caddy_bin: str) -> tuple[Config, list, MercadoPagoFalso, subprocess.Popen]:
    nome = urlsplit(db).path.lstrip("/")
    if not db.startswith("postgresql://") or not any(p in nome for p in ("teste", "invasao", "pentest")):
        raise SystemExit("use um banco Postgres de teste (o nome precisa conter teste, invasao ou pentest)")
    banco = Banco(db, pool=8)
    with banco.tx() as c:  # base limpa a cada rodada
        for t in TABELAS:
            c.run(f"DROP TABLE IF EXISTS {t} CASCADE")
    banco.criar_esquema()
    enviados: list = []
    mp = MercadoPagoFalso()
    cfg = Config(
        banco=banco,
        armazenamento=Armazem(),
        admin_tokens=[ADMIN],
        worker_tokens=[],
        segredo_aprovacao=secrets.token_urlsafe(32),
        email=suporte.ConfigEmail(host="smtp.teste", para="suporte@render2go.com", de="nao-responda@render2go.com"),
        enviar_email=lambda c, m: enviados.append(m),
        segredo_contas=secrets.token_urlsafe(32),
        turnstile_desligado=True,  # o Turnstile foi testado à parte, com as chaves de teste da Cloudflare
        admin_caminho=PAINEL,
        admin_totp=TOTP,
        mp=pagamentos.ConfigMP("token-mp", SEGREDO_MP, "https://localhost/api/pagamentos/mercadopago"),
        provedor_pagamento=lambda cfg_: mp,
        analise="desligada",
    )
    app = criar_app(cfg)
    opcoes = {"proxy_headers": False, "server_header": False, "log_level": "error", "timeout_keep_alive": 30}
    servidor = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORTA_API, **opcoes))
    threading.Thread(target=servidor.run, daemon=True).start()
    real = (SERVICO / "implantacao" / "Caddyfile").read_text()
    local = (
        real.replace("{$DOMINIO} {", f"localhost:{PORTA_TLS} {{\n\ttls internal")
        .replace("api:8000", f"127.0.0.1:{PORTA_API}")
        .replace("/data/logs/acesso.log", str(AQUI / "acesso.log"))
        .replace("{\n\tservers {", f"{{\n\tskip_install_trust\n\thttp_port {PORTA_HTTP}\n\tadmin off\n\tservers {{", 1)
    )
    (AQUI / "Caddyfile").write_text(local)
    log = (AQUI / "caddy.log").open("w")
    caddy = subprocess.Popen(
        [caddy_bin, "run", "--config", str(AQUI / "Caddyfile"), "--adapter", "caddyfile"],
        stdout=log,
        stderr=subprocess.STDOUT,
        env={**os.environ, "XDG_DATA_HOME": str(AQUI / "dados"), "XDG_CONFIG_HOME": str(AQUI / "config")},
    )
    for _ in range(100):
        try:
            if requests.get(f"{BASE}/saude", verify=False, timeout=2).ok:
                break
        except requests.RequestException:
            time.sleep(0.2)
    else:
        raise SystemExit(f"Caddy/API não subiram (veja {AQUI / 'caddy.log'})")
    return cfg, enviados, mp, caddy


class Origem(requests.adapters.HTTPAdapter):
    """Conexões saindo de um IP de loopback escolhido (127.0.0.x): cada grupo de testes é uma "rede"."""

    def __init__(self, ip: str):
        self.ip = ip
        super().__init__()

    def init_poolmanager(self, *a, **kw):
        kw["source_address"] = (self.ip, 0)
        super().init_poolmanager(*a, **kw)


def navegador(ip: str = "127.0.0.1") -> requests.Session:
    s = requests.Session()
    s.mount("https://", Origem(ip))
    s.trust_env = False  # sem o proxy e o CA do ambiente: direto no Caddy local (certificado da CA local)
    s.verify = False
    s.headers.update({"User-Agent": "Mozilla/5.0 (pentest)", "Sec-Fetch-Site": "same-origin"})
    return s


PAGINA = {"X-Render2Go": "1"}


def totp_novo(usados=set()) -> str:  # noqa: B006 (memória dos contadores já usados nesta rodada)
    agora = int(time.time() // 30)
    for c in (agora + 1, agora, agora - 1):
        if c not in usados:
            usados.add(c)
            return contas.totp(TOTP, c)
    time.sleep(31)
    return totp_novo(usados)


def entrar_cliente(enviados, email, ip="127.0.0.1") -> requests.Session:
    nav = navegador(ip)
    r = nav.post(f"{BASE}/api/conta/codigo", json={"email": email, "aceite": True}, headers=PAGINA)
    assert r.status_code == 200, r.text
    email = r.json()["email"]  # o endereço como a API normalizou (minúsculas)
    for _ in range(50):
        if any(m["To"] == email for m in enviados):
            break
        time.sleep(0.05)
    msg = [m for m in enviados if m["To"] == email][-1]
    codigo = re.search(r"\b(\d{4}) ?(\d{4})\b", msg.get_content())
    r = nav.post(
        f"{BASE}/api/conta/entrar",
        json={"email": email, "codigo": codigo.group(1) + codigo.group(2), "aceite": True},
        headers=PAGINA,
    )
    assert r.status_code == 200, r.text
    nav.set_cookie = r.headers.get("set-cookie", "")
    return nav


def entrar_admin() -> requests.Session:
    adm = navegador()
    r = adm.post(f"{BASE}{PAINEL}/api/entrar", json={"token": ADMIN, "totp": totp_novo()}, headers=PAGINA)
    assert r.status_code == 200, r.text
    adm.set_cookie = r.headers.get("set-cookie", "")
    return adm


_ULTIMOS: dict[str, int] = {}


def codigo_de(segredo: str) -> str:
    """Um código do aplicativo ainda não usado: o servidor só aceita passos de 30 s crescentes (o mesmo código não
    vale duas vezes), dentro de ±1 passo em torno de agora. Sem passo livre, espera o próximo."""
    while True:
        agora = int(time.time() // 30)
        c = max(_ULTIMOS.get(segredo, agora - 1) + 1, agora)
        if c <= agora + 1:
            _ULTIMOS[segredo] = c
            return contas.totp(segredo, c)
        time.sleep(3)


def codigo_por_email(enviados, email: str, depois: int) -> str:
    """O código de 8 dígitos do e-mail mais novo para `email` (enviado depois dos `depois` primeiros)."""
    for _ in range(100):
        novos = [m for m in enviados[depois:] if m["To"] == email]
        if novos:
            m = re.search(r"\b(\d{4}) ?(\d{4})\b", novos[-1].get_content())
            return m.group(1) + m.group(2)
        time.sleep(0.05)
    raise AssertionError(f"nenhum código para {email}")


# -- verificações ---------------------------------------------------------------------------------


def descoberta(anon):
    caminhos = [
        "/aprovar",
        "/admin",
        "/painel",
        "/painel/",
        "/administrator",
        "/dashboard",
        "/api",
        "/api/",
        "/api/admin",
        "/api/admin/lotes",
        "/api/docs",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/swagger",
        "/swagger.json",
        "/graphql",
        "/.env",
        "/.git/config",
        "/.git/HEAD",
        "/robots.txt",
        "/server-status",
        "/phpmyadmin",
        "/wp-login.php",
        "/wp-admin/",
        "/backup.sql",
        "/dump.sql",
        "/config.json",
        "/privado/painel.js",
        "/painel.js",
        "/estatico/painel.js",
        "/estatico/aprovar.js",
        "/publico/index.html",
        "/web/publico/index.html",
        "/src/render2go_servico/api.py",
        "/.DS_Store",
        "/metrics",
        "/debug",
        "/status",
        "/api/worker",
        "/api/worker/jobs",
        "/api/pagamentos/mercadopago",
        "/actuator/health",
        "/console",
    ]
    abertos = []
    for c in caminhos:
        r = anon.get(f"{BASE}{c}", allow_redirects=False)
        # 401 em /api/...: a rota existe, mas exige credencial (ex.: /api/pagamentos/{id}), sem dados
        if r.status_code not in (404, 405) and not (c.startswith("/api/") and r.status_code == 401):
            abertos.append(f"{c}={r.status_code}")
    conferir(
        not abertos,
        "descoberta: nenhum caminho conhecido de painel, docs, segredos ou código responde",
        ", ".join(abertos),
    )
    publicos = [
        "/",
        "/suporte",
        "/termos",
        "/privacidade",
        "/cookies",
        "/verificacao",
        "/sitemap.xml",
        "/manifest.webmanifest",
        "/nao-existe",
    ]
    textos = []
    for c in publicos:
        r = anon.get(f"{BASE}{c}", headers={"Accept": "text/html"})
        textos.append(r.text)
        for src in re.findall(r'(?:src|href)="(/estatico/[^"?]+)', r.text):
            textos.append(anon.get(f"{BASE}{src}").text)
    vazou = [t for t in textos if PAINEL in t or "painel.js" in t or PAINEL[1:] in t]
    conferir(not vazou, "o endereço do painel não aparece em nenhuma página, script, CSS, sitemap ou 404")
    trav = [
        "/estatico/..%2f..%2fsrc%2frender2go_servico%2fapi.py",
        "/estatico/%2e%2e/%2e%2e/etc/passwd",
        "/estatico/....//....//etc/passwd",
        "/estatico/..\\..\\pyproject.toml",
        "/%2e%2e/%2e%2e/etc/passwd",
        "/estatico/%00",
        "/estatico/estilo.css%00.js",
        "/estatico/" + "a" * 5000,
    ]
    ruins = [
        f"{t[:40]}={r.status_code}"
        for t in trav
        if (r := anon.get(f"{BASE}{t}")).status_code not in (400, 404, 414) or "root:" in r.text or "import" in r.text
    ]
    conferir(
        not ruins, "travessia de caminho em /estatico e na raiz: nada fora das páginas publicadas", ", ".join(ruins)
    )
    r = anon.get(f"{BASE}//evil.example/", allow_redirects=False)
    conferir(r.status_code == 404 and "location" not in r.headers, "sem redirecionamento aberto (//outro-site)")
    r = anon.get(f"{BASE}{PAINEL}", allow_redirects=False)
    conferir(
        r.status_code == 308 and r.headers["location"] == f"{PAINEL}/", "painel sem barra redireciona só para ele mesmo"
    )


def cabecalhos(anon, a, adm):
    falta = []
    for c in ("/", "/api/publico", "/estatico/estilo.css", "/nao-existe", "/saude", f"{PAINEL}/"):
        h = anon.get(f"{BASE}{c}").headers
        for k, v in {
            "strict-transport-security": "max-age=31536000",
            "x-content-type-options": "nosniff",
            "x-frame-options": "DENY",
            "referrer-policy": "no-referrer",
            "cross-origin-opener-policy": "same-origin",
            "content-security-policy": "trusted-types 'none'",
        }.items():
            if v not in h.get(k, ""):
                falta.append(f"{c}:{k}")
        for k in ("server", "via", "x-powered-by"):
            if k in h:
                falta.append(f"{c}:{k}={h[k]}")
    conferir(not falta, "cabeçalhos de segurança em toda resposta e nada que revele o servidor", ", ".join(falta))
    r = anon.get(f"{BASE}/api/conta", headers={"Origin": "https://evil.example"})
    conferir("access-control-allow-origin" not in r.headers, "sem CORS: outro site não lê respostas da API")
    r = anon.options(
        f"{BASE}/api/lotes", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    )
    conferir(r.status_code in (404, 405) and "access-control-allow-origin" not in r.headers, "preflight CORS recusado")
    for metodo in ("TRACE", "PUT", "DELETE", "PATCH"):
        r = anon.request(metodo, f"{BASE}/")
        conferir(r.status_code == 405, f"método {metodo} na página inicial recusado (405)", str(r.status_code))
    cookie = a.set_cookie
    conferir(
        cookie.startswith("__Host-render2go=")
        and all(a in cookie for a in ("HttpOnly", "Secure", "Path=/", "SameSite=lax")),
        "cookie da conta: __Host-, HttpOnly, Secure, SameSite=Lax",
        cookie,
    )
    adm_cookie = adm.set_cookie
    conferir(
        adm_cookie.startswith("__Secure-render2go-painel=")
        and all(a in adm_cookie for a in ("HttpOnly", "Secure", f"Path={PAINEL}", "SameSite=strict")),
        "cookie do painel: __Secure-, HttpOnly, Secure, só no caminho do painel, SameSite=Strict",
        adm_cookie,
    )


def autenticacao(anon, a, adm, tok_pod_a):
    rotas = [
        ("GET", "/api/conta"),
        ("GET", "/api/lotes"),
        ("GET", "/api/saldo"),
        ("GET", "/api/conta/dados"),
        ("POST", "/api/lotes"),
        ("POST", "/api/pagamentos/pix"),
        ("POST", "/api/conta/excluir"),
    ]
    ruins = []
    for m, c in rotas:
        r = anon.request(m, f"{BASE}{c}", json={}, headers=PAGINA)
        if r.status_code != 401:
            ruins.append(f"{m} {c}={r.status_code}")
    conferir(not ruins, "rotas da conta exigem sessão (401 sem cookie)", ", ".join(ruins))
    falsos = ["x", "' OR '1'='1", "a" * 300, "é" * 40, "١" * 43, "null", "../../etc/passwd", "%00"]
    ruins = []
    for f in falsos:
        for cab in ({"Cookie": f"__Host-render2go={f}".encode()}, {"Authorization": f"Bearer {f}".encode()}):
            try:
                r = anon.get(f"{BASE}/api/conta", headers=cab)
                if r.status_code != 401:
                    ruins.append(f"{list(cab)[0]}:{f[:12]}={r.status_code}")
            except requests.RequestException as e:
                ruins.append(f"{f[:12]}:{type(e).__name__}")
    conferir(not ruins, "cookie e token forjados (injeção, Unicode, enormes): sempre 401, nunca 500", ", ".join(ruins))
    painel = [
        ("GET", "/api/lotes"),
        ("GET", "/api/clientes"),
        ("GET", "/api/pods"),
        ("POST", "/api/pods/emergencia"),
        ("GET", "/api/seguranca"),
        ("GET", "/api/pagamentos"),
        ("POST", "/api/clientes"),
    ]
    ruins = []
    for m, c in painel:
        r = anon.request(m, f"{BASE}{PAINEL}{c}", json={}, headers=PAGINA)
        r2 = a.request(m, f"{BASE}{PAINEL}{c}", json={}, headers=PAGINA)  # sessão de cliente
        r3 = anon.request(m, f"{BASE}{PAINEL}{c}", json={}, headers={**PAGINA, "Authorization": f"Bearer {ADMIN}"})
        if {r.status_code, r2.status_code, r3.status_code} != {401}:
            ruins.append(f"{m} {c}={r.status_code}/{r2.status_code}/{r3.status_code}")
    conferir(
        not ruins,
        "painel: sem a sessão do painel, nada (nem com sessão de cliente nem com o token no cabeçalho)",
        ", ".join(ruins),
    )
    sessao_adm = adm.cookies.get("__Secure-render2go-painel")
    r = anon.get(f"{BASE}/api/conta", headers={"Cookie": f"__Host-render2go={sessao_adm}"})
    conferir(r.status_code == 401, "a sessão do painel não vale como sessão de cliente")
    r = anon.post(f"{BASE}{PAINEL}/api/entrar", json={"token": "é" * 40, "totp": "١٢٣٤٥٦"}, headers=PAGINA)
    conferir(r.status_code == 401, "painel com token e código em Unicode: 401 (sem erro 500)", str(r.status_code))
    w = {"Authorization": f"Bearer {tok_pod_a}"}
    r = anon.post(f"{BASE}/api/worker/reivindicar", json={"worker": "pod-b"}, headers=w)
    conferir(r.status_code == 403, "token de um pod não serve para outro worker", str(r.status_code))
    r = anon.post(f"{BASE}/api/worker/reivindicar", json={"worker": "pod-é"}, headers=w)
    conferir(r.status_code in (403, 422), "nome de worker em Unicode: recusado sem erro 500", str(r.status_code))
    r = a.post(f"{BASE}/api/worker/reivindicar", json={"worker": "pod-a"}, headers=PAGINA)
    conferir(r.status_code == 401, "sessão de cliente não pega trabalho de worker", str(r.status_code))


def idor(a, b, anon, lote_a, pag_a):
    r = b.get(f"{BASE}/api/lotes/{lote_a}")
    conferir(r.status_code == 404, "outra conta não vê o pedido (IDOR em /api/lotes/{id})", str(r.status_code))
    r = b.post(f"{BASE}/api/lotes/{lote_a}/enviado", headers=PAGINA)
    conferir(r.status_code == 404, "outra conta não fecha o envio do pedido alheio", str(r.status_code))
    r = b.get(f"{BASE}/api/pagamentos/{pag_a}")
    conferir(r.status_code == 404, "outra conta não vê a recarga (IDOR em /api/pagamentos/{id})", str(r.status_code))
    lotes_b = b.get(f"{BASE}/api/lotes").json()
    conferir(
        all(x.get("lote") != lote_a and x.get("id") != lote_a for x in lotes_b),
        "a lista de pedidos é só da própria conta",
    )
    for lid in ("' OR 1=1--", "lote_%", "..%2f..%2f", "lote_" + "x" * 3000):
        r = a.get(f"{BASE}/api/lotes/{lid}")
        conferir(r.status_code in (404, 414), f"id malicioso em /api/lotes/{lid[:16]}: 404", str(r.status_code))


def csrf(a, adm):
    corpo = {"prompt": "câmera aproxima", "fotos": [{"nome": "a.jpg", "tipo": "image/jpeg", "bytes": 1000}]}
    for nome, cab in {
        "Sec-Fetch-Site cross-site": {"Sec-Fetch-Site": "cross-site", **PAGINA},
        "Sec-Fetch-Site same-site": {"Sec-Fetch-Site": "same-site", **PAGINA},
        "Origin de outro site (navegador antigo)": {"Sec-Fetch-Site": None, "Origin": "https://evil.example", **PAGINA},
        "sem o cabeçalho X-Render2Go": {"Sec-Fetch-Site": "same-origin"},
    }.items():
        r = a.post(f"{BASE}/api/lotes", json=corpo, headers=cab)
        conferir(r.status_code == 403, f"CSRF ({nome}) com a sessão da conta: 403", str(r.status_code))
    r = adm.post(f"{BASE}{PAINEL}/api/pods/emergencia", headers={"Sec-Fetch-Site": "cross-site", **PAGINA})
    conferir(r.status_code == 403, "CSRF no painel (parada de emergência vinda de outro site): 403", str(r.status_code))
    r = adm.post(f"{BASE}{PAINEL}/api/pods/emergencia")
    conferir(r.status_code == 403, "painel sem o cabeçalho da página: 403", str(r.status_code))


def injecao(a, adm):
    anon = navegador("127.0.0.7")
    for busca in ("' OR '1'='1", "%' OR 1=1 --", "'; DROP TABLE clientes; --", "\\", "\x00", "a" * 500):
        r = adm.get(f"{BASE}{PAINEL}/api/clientes", params={"busca": busca})
        # NUL sai do texto: a busca "\x00" vira a busca vazia (lista tudo, para o dono, que está logado)
        ok = r.status_code in (200, 422) and (r.status_code != 200 or busca == "\x00" or len(r.json()["clientes"]) == 0)
        conferir(
            ok,
            f"busca do painel com injeção ({busca[:14]!r}): sem vazamento nem erro",
            f"{r.status_code} {r.text[:80]}",
        )
    for email in (
        "a@gmail.com' OR 1=1--",
        "a@gmail.com\r\nBcc: x@evil.example",
        ["a@gmail.com"],
        {"$ne": ""},
        12345,
        "a" * 300 + "@gmail.com",
        "ａ@gmail.com",
        "a@gmаil.com",
    ):
        r = anon.post(f"{BASE}/api/conta/codigo", json={"email": email, "aceite": True}, headers=PAGINA)
        conferir(r.status_code == 422, f"e-mail malicioso {str(email)[:24]!r}: 422", f"{r.status_code} {r.text[:60]}")
    r = anon.post(
        f"{BASE}/api/suporte",
        json={"email": "x@gmail.com\r\nBcc: y@evil.example", "mensagem": "oi " * 10},
        headers=PAGINA,
    )
    conferir(
        r.status_code in (403, 422),
        "suporte: e-mail com quebra de linha (injeção de cabeçalho) recusado",
        str(r.status_code),
    )
    r = a.post(f"{BASE}/api/lotes", json={"prompt": ["x"], "fotos": "x"}, headers=PAGINA)
    conferir(r.status_code == 422, "tipos trocados no JSON: 422", str(r.status_code))
    r = a.post(f"{BASE}/api/lotes", data="{nao é json", headers={**PAGINA, "Content-Type": "application/json"})
    conferir(
        r.status_code == 422 and "Traceback" not in r.text,
        "JSON quebrado: 422 sem rastro do código",
        str(r.status_code),
    )
    corpo = {
        "prompt": "câmera",
        "plano": "interno",
        "prioridade": -100,
        "cliente_id": "cli_outro",
        "aprovado": True,
        "fotos": [{"nome": "a.jpg", "tipo": "image/jpeg", "bytes": 1000}],
    }
    r = a.post(f"{BASE}/api/lotes", json=corpo, headers=PAGINA)
    if r.status_code == 200:
        lid = r.json()["lote"]
        d = a.get(f"{BASE}/api/lotes/{lid}").json()
        conferir(
            d.get("status") == "aguardando_envio",
            "campos extras no pedido (plano, aprovado, cliente) são ignorados",
            json.dumps(d)[:120],
        )
    else:
        conferir(r.status_code in (422, 429), "campos extras no pedido: recusado", str(r.status_code))
    r = a.post(
        f"{BASE}/api/lotes",
        json={"prompt": "câmera", "fotos": [{"nome": "../../../x\\..\\y.jpg", "tipo": "image/jpeg", "bytes": 10}]},
        headers=PAGINA,
    )
    chaves = (
        [e["url"].split("armazem.teste/")[1].split("?")[0] for e in r.json().get("envios", [])]
        if r.status_code == 200
        else []
    )
    conferir(
        r.status_code in (422, 429)
        or all(".." not in k and "\\" not in k and k.startswith("entrada/") for k in chaves),
        "nome de foto com ../ e \\: a chave no armazenamento fica dentro da pasta do pedido",
        str(chaves),
    )
    for fotos in (
        [{"nome": "a.jpg", "tipo": "image/jpeg", "bytes": -1}],
        [{"nome": "a.jpg", "tipo": "text/html", "bytes": 10}],
        [{"nome": "a.jpg", "tipo": "image/jpeg", "bytes": 10**12}],
        [{"nome": f"{i}.jpg", "tipo": "image/jpeg", "bytes": 10} for i in range(10000)],
    ):
        r = a.post(f"{BASE}/api/lotes", json={"prompt": "câmera", "fotos": fotos}, headers=PAGINA)
        conferir(
            r.status_code in (413, 422, 429),
            f"fotos inválidas ({str(fotos)[:40]}): recusado",
            f"{r.status_code} {r.text[:80]}",
        )
    r = anon.post(
        f"{BASE}/api/conta/codigo", data="x" * 2_000_000, headers={**PAGINA, "Content-Type": "application/json"}
    )
    conferir(r.status_code == 413, "corpo de 2 MB barrado no Caddy (413)", str(r.status_code))


def montagem(cfg, a, b, anon):
    """Montagem de vídeos (ADR 0015): o plano decide quem monta; IDOR nas montagens e nos vídeos de outra conta;
    CSRF; textos que quebrariam uma linha de filtros do ffmpeg ou o HTML; logo de outro tipo ou tamanho; ids
    maliciosos; o painel das montagens só com a sessão do painel."""
    from test_montagem import _videos_prontos

    banco = cfg.banco
    ida = banco.conta_por_email(contas.normalizar("cliente.a@gmail.com")[1])["id"]
    idb = banco.conta_por_email(contas.normalizar("cliente.b@gmail.com")[1])["id"]
    banco.mudar_plano(ida, "interno")  # o padrão: só o plano interno monta (B segue no teste gratuito)
    _, de_a = _videos_prontos(banco, ida, 2)
    _, de_b = _videos_prontos(banco, idb, 1)
    corpo = {"videos": de_a, "formato": "9:16", "transicao": "fade"}
    r = b.post(f"{BASE}/api/montagens", json={**corpo, "videos": de_b * 2}, headers=PAGINA)
    conferir(r.status_code == 403, "montagem: o teste gratuito não monta (403)", str(r.status_code))
    r = a.post(f"{BASE}/api/montagens", json=corpo, headers={"Sec-Fetch-Site": "same-origin"})
    conferir(r.status_code == 403, "montagem: CSRF sem o cabeçalho da página (403)", str(r.status_code))
    r = a.post(f"{BASE}/api/montagens", json={**corpo, "videos": [de_a[0], de_b[0]]}, headers=PAGINA)
    conferir(r.status_code == 422, "montagem com o vídeo de outra conta: 422 (IDOR)", str(r.status_code))
    for texto in (
        "[0:v]drawtext=text='x':fontfile=/etc/passwd",
        "%{pts:hms}",
        "x\\:y;[a]",
        "a\x00b",
        "{{7*7}}",
        "<script>alert(1)</script>",
        "x" * 41,
        ["lista"],
    ):
        r = a.post(f"{BASE}/api/montagens", json={**corpo, "textos": {"titulo": texto}}, headers=PAGINA)
        conferir(r.status_code == 422, f"montagem: texto que quebraria o ffmpeg ou o HTML ({str(texto)[:16]!r}): 422",
                 f"{r.status_code} {r.text[:60]}")  # fmt: skip
    for logo in (
        {"tipo": "image/svg+xml", "bytes": 100},
        {"tipo": "text/html", "bytes": 100},
        {"tipo": "image/png", "bytes": 10**9},
        {"tipo": "image/png", "bytes": -1},
    ):
        r = a.post(f"{BASE}/api/montagens", json={**corpo, "logo": logo}, headers=PAGINA)
        conferir(r.status_code == 422, f"montagem: logo recusado ({logo['tipo']}, {logo['bytes']} bytes): 422",
                 str(r.status_code))  # fmt: skip
    r = a.post(f"{BASE}/api/montagens", json={**corpo, "logo": {"tipo": "image/png", "bytes": 1234}}, headers=PAGINA)
    mid = r.json().get("montagem", "") if r.status_code == 200 else ""
    envio = (r.json().get("envio") or {}).get("url", "") if r.status_code == 200 else ""
    chave = envio.split("armazem.teste/")[-1].split("?")[0]
    conferir(chave == f"entrada/{ida}/{mid}/logo-original.png", "montagem: o logo só sobe para a pasta da montagem",
             chave)  # fmt: skip
    r = b.post(f"{BASE}/api/montagens/{mid}/enviado", headers=PAGINA)
    conferir(r.status_code == 404, "outra conta não confirma o logo da montagem alheia (IDOR)", str(r.status_code))
    r = b.post(f"{BASE}/api/montagens/{mid}/excluir", headers=PAGINA)
    conferir(r.status_code == 404, "outra conta não exclui a montagem alheia (IDOR)", str(r.status_code))
    lista = b.get(f"{BASE}/api/montagens").json().get("itens", [])
    conferir(all(x.get("id") != mid for x in lista), "a lista de montagens é só da própria conta")
    for ruim in ("' OR 1=1--", "mon_%", "..%2f..%2f", "mon_" + "x" * 3000):
        r = a.post(f"{BASE}/api/montagens/{ruim}/excluir", headers=PAGINA)
        conferir(r.status_code in (404, 405, 414), f"id malicioso em /api/montagens/{ruim[:16]}: recusado",
                 str(r.status_code))  # fmt: skip
    for quem, nav in (("anônimo", anon), ("sessão de cliente", a)):
        r = nav.get(f"{BASE}{PAINEL}/api/montagens")
        conferir(r.status_code in (401, 403), f"painel das montagens com {quem}: recusado", str(r.status_code))


def fidelidade(cfg, a, b, anon, adm, lote_a):
    """ADR 0018 (17/10/2026): versões, texto, entendimento, retorno, correção e as rotas novas do painel."""
    foto = cfg.banco.fotos(lote_a)[0]
    job = "job_" + secrets.token_hex(8)
    agora = time.time()
    with cfg.banco.tx() as c:  # um vídeo entregue da conta A (como se o pod tivesse gerado)
        c.run(
            "INSERT INTO jobs (id, lote_id, tipo, foto_id, arquivo, status, criado, atualizado)"
            " VALUES (?, ?, 'render', ?, 'bolo-v1.mp4', 'ok', ?, ?)",
            (job, lote_a, foto["id"], agora, agora),
        )
    versao = {"numero": 1, "hash": "0" * 64}
    r = b.post(f"{BASE}/api/lotes/{lote_a}/versao/aprovar", json=versao, headers=PAGINA)
    conferir(r.status_code == 404, "outra conta não aprova a versão do pedido alheio (IDOR)", str(r.status_code))
    r = b.post(f"{BASE}/api/lotes/{lote_a}/texto", json={**versao, "grupo": "pedido", "texto": "o bolo gira"},
               headers=PAGINA)  # fmt: skip
    conferir(r.status_code == 404, "outra conta não muda o texto do pedido alheio (IDOR)", str(r.status_code))
    r = a.post(f"{BASE}/api/lotes/{lote_a}/versao/aprovar", json={"numero": 1, "hash": "' OR 1=1--"}, headers=PAGINA)
    conferir(r.status_code == 422, "hash malicioso na aprovação da versão: 422", str(r.status_code))
    r = anon.post(f"{BASE}/api/pedido/entendimento", json={"prompt": "o bolo gira"}, headers=PAGINA)
    conferir(r.status_code == 401, "entendimento do pedido sem sessão: 401", str(r.status_code))
    corpo = {"usavel": False, "motivo": "defeito_visual", "comentario": "borrão no fim"}
    r = b.post(f"{BASE}/api/videos/{job}/retorno", json=corpo, headers=PAGINA)
    conferir(r.status_code == 404, "outra conta não responde sobre o vídeo alheio (IDOR)", str(r.status_code))
    r = a.post(f"{BASE}/api/videos/{job}/retorno", json=corpo, headers={"Sec-Fetch-Site": "cross-site", **PAGINA})
    conferir(r.status_code == 403, "CSRF no retorno sobre um vídeo: 403", str(r.status_code))
    r = a.post(f"{BASE}/api/videos/{job}/retorno", json={**corpo, "motivo": "' OR 1=1--"}, headers=PAGINA)
    conferir(r.status_code == 422, "motivo fora da lista no retorno: 422", str(r.status_code))
    r = a.post(f"{BASE}/api/videos/{job}/retorno", json=corpo, headers=PAGINA)
    conferir(r.status_code == 200, "a própria conta responde sobre o vídeo", str(r.status_code))
    r = b.post(f"{BASE}/api/videos/{job}/correcao", headers=PAGINA)
    conferir(r.status_code == 404, "outra conta não pede correção do vídeo alheio (IDOR)", str(r.status_code))
    r = a.post(f"{BASE}/api/videos/{job}/correcao", headers=PAGINA)
    conferir(r.status_code == 200, "a própria conta pede a correção", str(r.status_code))
    cid = (cfg.banco.correcoes() or [{}])[0].get("id", "cor_x")
    decisao = {"classificacao": "falha_tecnica", "nota": "quadro estranho", "creditar": True}
    for quem, nav in (("anônimo", anon), ("sessão de cliente", a)):
        r = nav.post(f"{BASE}{PAINEL}/api/correcoes/{cid}/decidir", json=decisao, headers=PAGINA)
        conferir(r.status_code in (401, 403), f"decidir correção (crédito) com {quem}: recusado", str(r.status_code))
        r = nav.get(f"{BASE}{PAINEL}/api/entregas")
        conferir(r.status_code in (401, 403), f"painel das entregas com {quem}: recusado", str(r.status_code))
        r = nav.get(f"{BASE}{PAINEL}/api/jobs/{job}/video")
        conferir(r.status_code in (401, 403), f"vídeo entregue pelo painel com {quem}: recusado", str(r.status_code))
    r = adm.post(f"{BASE}{PAINEL}/api/correcoes/{cid}/decidir", json={**decisao, "classificacao": "pedido_novo"},
                 headers=PAGINA)  # fmt: skip
    conferir(r.status_code == 422, "pedido novo não tem crédito: 422", str(r.status_code))
    primeira = adm.post(f"{BASE}{PAINEL}/api/correcoes/{cid}/decidir", json=decisao, headers=PAGINA)
    segunda = adm.post(f"{BASE}{PAINEL}/api/correcoes/{cid}/decidir", json=decisao, headers=PAGINA)
    with cfg.banco.tx() as c:
        n = c.one("SELECT COUNT(*) AS n FROM movimentos WHERE referencia = ?", (f"correcao:{cid}",))["n"]
    conferir(primeira.status_code == 200 and segunda.json().get("ja") is True and n <= 1,
             "a mesma correção decidida duas vezes: no máximo um crédito", f"{primeira.status_code} {n}")  # fmt: skip


def webhook(anon, mp, pid_mp):
    url = f"{BASE}/api/pagamentos/mercadopago?data.id={pid_mp}&type=payment"
    r = anon.post(url, json={"type": "payment", "data": {"id": pid_mp}})
    conferir(r.status_code == 401, "aviso do Mercado Pago sem assinatura: 401", str(r.status_code))

    def assinado(segredo, ts="1704908010", rid="req-x", data_id=pid_mp):
        v1 = hmac.new(
            segredo.encode(), f"id:{data_id.lower()};request-id:{rid};ts:{ts};".encode(), hashlib.sha256
        ).hexdigest()
        return {"x-signature": f"ts={ts},v1={v1}", "x-request-id": rid}

    r = anon.post(url, json={}, headers=assinado("segredo-errado"))
    conferir(r.status_code == 401, "aviso com assinatura de outro segredo: 401", str(r.status_code))
    r = anon.post(url, json={}, headers={"x-signature": "ts=1,v1=" + "é" * 64, "x-request-id": "r"})
    conferir(r.status_code == 401, "assinatura com caracteres fora do ASCII: 401 (sem erro 500)", str(r.status_code))
    antes = mp.consultas
    r = anon.post(url, json={}, headers=assinado(SEGREDO_MP))
    conferir(
        r.status_code == 200 and mp.consultas == antes + 1,
        "aviso válido só dispara a consulta ao Mercado Pago (o corpo não decide nada)",
    )


def limites(enviados):
    anon = navegador("127.0.0.3")
    email = "limite.teste@gmail.com"
    codes = [
        anon.post(f"{BASE}/api/conta/codigo", json={"email": email, "aceite": True}, headers=PAGINA).status_code
        for _ in range(7)
    ]
    conferir(
        codes[:5] == [200] * 5 and 429 in codes[5:], "códigos por e-mail: o 6º em uma hora é recusado (429)", str(codes)
    )
    nav = navegador("127.0.0.4")
    alvo = "forca.bruta@gmail.com"
    nav.post(f"{BASE}/api/conta/codigo", json={"email": alvo, "aceite": True}, headers=PAGINA)
    time.sleep(0.3)
    msg = [m for m in enviados if m["To"] == alvo][-1]
    m = re.search(r"\b(\d{4}) ?(\d{4})\b", msg.get_content())
    certo = m.group(1) + m.group(2)
    errado = f"{(int(certo) + 1) % 10**8:08d}"
    for _ in range(5):
        nav.post(f"{BASE}/api/conta/entrar", json={"email": alvo, "codigo": errado, "aceite": True}, headers=PAGINA)
    r = nav.post(f"{BASE}/api/conta/entrar", json={"email": alvo, "codigo": certo, "aceite": True}, headers=PAGINA)
    conferir(
        r.status_code == 401,
        "código de 8 dígitos: depois de 5 erros, nem o certo vale (força bruta inviável)",
        str(r.status_code),
    )
    anon = navegador("127.0.0.5")
    falhas = [
        anon.post(f"{BASE}{PAINEL}/api/entrar", json={"token": ADMIN, "totp": "000000"}, headers=PAGINA).status_code
        for _ in range(6)
    ]
    r = anon.post(f"{BASE}{PAINEL}/api/entrar", json={"token": ADMIN, "totp": totp_novo()}, headers=PAGINA)
    conferir(
        r.status_code == 429 and 429 in falhas,
        "painel: 5 erros bloqueiam a rede por 15 min, mesmo com o código certo",
        f"{falhas} {r.status_code}",
    )
    anon = navegador("127.0.0.6")
    ruins = 0
    for _ in range(65):
        ruins = anon.post(
            f"{BASE}/api/worker/reivindicar",
            json={"worker": "pod-x"},
            headers={"Authorization": "Bearer " + secrets.token_urlsafe(32)},
        ).status_code
    conferir(ruins == 429, "tokens de worker chutados: a rede é bloqueada depois de 60 erros em 10 min", str(ruins))


def sessoes(enviados):
    ip = "127.0.0.2"
    nav = entrar_cliente(enviados, "sessao.teste@gmail.com", ip)
    velho = nav.cookies.get("__Host-render2go")
    nav.post(f"{BASE}/api/conta/sair", headers=PAGINA)
    r = navegador().get(f"{BASE}/api/conta", headers={"Cookie": f"__Host-render2go={velho}"})
    conferir(
        r.status_code == 401, "sair encerra a sessão no servidor (o cookie copiado deixa de valer)", str(r.status_code)
    )
    n1, n2 = (
        entrar_cliente(enviados, "dois.aparelhos@gmail.com", ip),
        entrar_cliente(enviados, "dois.aparelhos@gmail.com", ip),
    )
    n1.post(f"{BASE}/api/conta/sair-de-todos", headers=PAGINA)
    conferir(n2.get(f"{BASE}/api/conta").status_code == 401, "sair de todos os aparelhos encerra as outras sessões")
    n3 = entrar_cliente(enviados, "excluir.conta@gmail.com", ip)
    cookie = n3.cookies.get("__Host-render2go")
    r = n3.post(f"{BASE}/api/conta/excluir", json={"confirmacao": "EXCLUIR"}, headers=PAGINA)
    r2 = navegador().get(f"{BASE}/api/conta", headers={"Cookie": f"__Host-render2go={cookie}"})
    conferir(
        r.status_code == 200 and r2.status_code == 401,
        "conta excluída: a sessão some junto",
        f"{r.status_code} {r2.status_code}",
    )
    n4 = entrar_cliente(enviados, "excluir.conta@gmail.com", ip)
    conferir(n4.get(f"{BASE}/api/conta").json()["plano"] == "pago", "o mesmo e-mail volta sem um 2º teste grátis")
    n5 = entrar_cliente(enviados, "Excluir.Conta+outro@gmail.com", ip)
    conta = n5.get(f"{BASE}/api/conta").json()
    conferir(conta["plano"] == "pago", "apelido do Gmail (ponto e +) cai na mesma caixa: sem teste extra", str(conta))


SENHA_OP = "frase longa do operador 42"


def acessos_painel(adm):
    """Acessos do painel por pessoa (ADR 0009): convite de uso único, código do aplicativo de uso único, força
    bruta na entrada por pessoa (rede e nome), papel de operador e mensagens iguais para qualquer erro."""
    entrar = f"{BASE}{PAINEL}/api/entrar"
    r = adm.post(
        f"{BASE}{PAINEL}/api/acessos/convites", json={"usuario": "op.pentest", "papel": "operador"}, headers=PAGINA
    )
    token = r.json()["endereco"].split("#convite=")[1]
    conv = navegador("127.0.0.8")
    segredo = conv.post(f"{BASE}{PAINEL}/api/convite/iniciar", json={"convite": token}, headers=PAGINA).json()[
        "segredo"
    ]
    r = conv.post(f"{BASE}{PAINEL}/api/convite/senha", json={"convite": token, "senha": SENHA_OP}, headers=PAGINA)
    conferir(
        r.status_code == 409, "convite: a senha só vem depois de um código do aplicativo conferir", str(r.status_code)
    )
    corpo = {"convite": token, "codigo": codigo_de(segredo)}
    assert conv.post(f"{BASE}{PAINEL}/api/convite/confirmar", json=corpo, headers=PAGINA).status_code == 200
    r = conv.post(f"{BASE}{PAINEL}/api/convite/senha", json={"convite": token, "senha": SENHA_OP}, headers=PAGINA)
    assert r.status_code == 200, r.text
    outro = navegador("127.0.0.9")
    reuso = [
        outro.post(
            f"{BASE}{PAINEL}/api/convite/{passo}",
            json={"convite": token, "codigo": "123456", "senha": SENHA_OP},
            headers=PAGINA,
        ).status_code
        for passo in ("iniciar", "confirmar", "senha")
    ]
    conferir(reuso == [401, 401, 401], "convite reaproveitado (o mesmo endereço, de outra rede): recusado", str(reuso))
    chutes = {
        outro.post(f"{BASE}{PAINEL}/api/convite/iniciar", json={"convite": c}, headers=PAGINA).status_code
        for c in (secrets.token_urlsafe(32), "' OR 1=1 --" + "x" * 20, "é" * 30)
    }
    conferir(chutes == {401}, "convite chutado, com injeção ou Unicode: 401", str(chutes))
    codigo = codigo_de(segredo)
    op = navegador("127.0.0.10")
    r1 = op.post(entrar, json={"usuario": "op.pentest", "senha": SENHA_OP, "totp": codigo}, headers=PAGINA)
    r2 = navegador("127.0.0.11").post(
        entrar, json={"usuario": "op.pentest", "senha": SENHA_OP, "totp": codigo}, headers=PAGINA
    )
    conferir(
        r1.status_code == 200 and r2.status_code == 401,
        "o mesmo código do aplicativo não entra duas vezes (pessoa do painel)",
        f"{r1.status_code} {r2.status_code}",
    )
    donos = [("POST", "/api/pods/emergencia"), ("GET", "/api/acessos"), ("GET", "/api/auditoria"),
             ("POST", "/api/acessos/convites"), ("GET", "/api/pagamentos"), ("GET", "/api/seguranca"),
             ("GET", "/api/geradores"), ("GET", "/api/auditoria.csv")]  # fmt: skip
    ruins = [f"{m} {c}={s}" for m, c in donos if (s := op.request(m, f"{BASE}{PAINEL}{c}", json={},
             headers=PAGINA).status_code) != 403]  # fmt: skip
    conferir(not ruins, "operador do painel não chega a pods, dinheiro, acessos nem auditoria (403)", ", ".join(ruins))
    erros = {
        navegador(ip).post(entrar, json=corpo, headers=PAGINA).text
        for ip, corpo in (
            ("127.0.0.15", {"usuario": "op.pentest", "senha": "senha errada demais", "totp": "123456"}),
            ("127.0.0.16", {"usuario": "nao.existe", "senha": SENHA_OP, "totp": "123456"}),
            ("127.0.0.17", {"usuario": "op.pentest", "senha": SENHA_OP, "totp": "000000"}),
        )
    }
    conferir(len(erros) == 1, "entrada por pessoa: a mesma resposta para nome inexistente, senha e código errados")
    rede = navegador("127.0.0.12")
    for i in range(5):
        rede.post(entrar, json={"usuario": f"chute{i}", "senha": "chute chute chute", "totp": "000000"}, headers=PAGINA)
    r = rede.post(entrar, json={"usuario": "op.pentest", "senha": SENHA_OP, "totp": "123456"}, headers=PAGINA)
    conferir(r.status_code == 429, "força bruta por pessoa: 5 erros bloqueiam a rede por 15 min", str(r.status_code))
    for i in range(2):  # com os 3 erros da verificação anterior, o nome soma 5
        navegador(f"127.0.0.{18 + i}").post(
            entrar, json={"usuario": "op.pentest", "senha": "outra senha errada", "totp": "1"}, headers=PAGINA
        )
    r = navegador("127.0.0.20").post(
        entrar, json={"usuario": "op.pentest", "senha": SENHA_OP, "totp": "123456"}, headers=PAGINA
    )
    conferir(r.status_code == 429, "força bruta por pessoa: o nome fica bloqueado também vindo de outra rede")


def configuracoes(cfg, enviados, a, b):
    """Configurações da conta (10/10/2026): só a sessão do navegador, confirmação recente, aparelhos de outra conta e
    o desafio da verificação em duas etapas (não vale como sessão; força bruta e reuso de código barrados)."""
    _, tok = cfg.banco.criar_cliente("Integração do pentest", 500, "pago")
    rotas = [("POST", "/api/conta/confirmar/codigo"), ("POST", "/api/conta/mfa/iniciar"),
             ("POST", "/api/conta/email/codigo"), ("GET", "/api/conta/sessoes"), ("POST", "/api/conta/desativar"),
             ("POST", "/api/conta/excluir"), ("POST", "/api/conta/perfil"), ("POST", "/api/conta/aceitar")]  # fmt: skip
    anon = navegador("127.0.0.21")
    ruins = []
    for m, c in rotas:
        sem = anon.request(m, f"{BASE}{c}", json={}, headers=PAGINA).status_code
        com_token = anon.request(m, f"{BASE}{c}", json={}, headers={**PAGINA, "Authorization": f"Bearer {tok}"})
        if sem != 401 or com_token.status_code != 403:
            ruins.append(f"{m} {c}={sem}/{com_token.status_code}")
    conferir(
        not ruins, "configurações da conta: sem sessão 401; com o código de acesso no cabeçalho, 403", ", ".join(ruins)
    )
    ref_a = a.get(f"{BASE}/api/conta/sessoes").json()["sessoes"][0]["ref"]
    r = b.post(f"{BASE}/api/conta/sessoes/{ref_a}/encerrar", headers=PAGINA)
    conferir(r.status_code == 404 and a.get(f"{BASE}/api/conta").status_code == 200,
             "outra conta não encerra a sessão de ninguém pela referência (IDOR)", str(r.status_code))  # fmt: skip
    with cfg.banco.tx() as c:  # a sessão de A passa a ser de 20 min atrás: sem a confirmação recente
        c.run("UPDATE sessoes SET criado = criado - 1200 WHERE cliente_id = (SELECT id FROM clientes WHERE email = ?)",
              ("cliente.a@gmail.com",))  # fmt: skip
    r = a.post(f"{BASE}/api/conta/excluir", json={"confirmacao": "EXCLUIR"}, headers=PAGINA)
    conferir(
        r.status_code == 403 and r.headers.get("x-render2go-motivo") == "confirmar",
        "excluir a conta pede a confirmação recente (só a sessão roubada não basta)",
        str(r.status_code),
    )
    # verificação em duas etapas
    email, ip = "mfa.pentest@gmail.com", "127.0.0.14"
    nav = entrar_cliente(enviados, email, ip)
    segredo = nav.post(f"{BASE}/api/conta/mfa/iniciar", headers=PAGINA).json()["segredo"]
    r = nav.post(f"{BASE}/api/conta/mfa/ativar", json={"codigo": codigo_de(segredo)}, headers=PAGINA)
    recuperacao = r.json()["codigos"]

    def desafio() -> tuple[requests.Session, str]:
        novo = navegador(ip)
        n = len(enviados)
        novo.post(f"{BASE}/api/conta/codigo", json={"email": email, "aceite": True}, headers=PAGINA)
        corpo = {"email": email, "codigo": codigo_por_email(enviados, email, n), "aceite": True}
        r = novo.post(f"{BASE}/api/conta/entrar", json=corpo, headers=PAGINA)
        assert r.json().get("mfa") is True and "set-cookie" not in r.headers, r.text
        return novo, r.json()["desafio"]

    novo, d = desafio()
    r = navegador().get(f"{BASE}/api/conta", headers={"Cookie": f"__Host-render2go={d}"})
    conferir(r.status_code == 401, "o desafio da verificação em duas etapas não vale como sessão", str(r.status_code))
    r = novo.post(f"{BASE}/api/conta/entrar-mfa", json={"desafio": d, "codigo": recuperacao[0]}, headers=PAGINA)
    r2 = novo.post(f"{BASE}/api/conta/entrar-mfa", json={"desafio": d, "codigo": recuperacao[1]}, headers=PAGINA)
    conferir(r.status_code == 200 and r2.status_code == 401, "o desafio vale uma vez só",
             f"{r.status_code} {r2.status_code}")  # fmt: skip
    novo, d = desafio()
    r = novo.post(f"{BASE}/api/conta/entrar-mfa", json={"desafio": d, "codigo": recuperacao[0]}, headers=PAGINA)
    conferir(r.status_code == 401, "código de recuperação já usado: recusado", str(r.status_code))
    for _ in range(4):  # com o código já usado, 5 erros
        novo.post(f"{BASE}/api/conta/entrar-mfa", json={"desafio": d, "codigo": "000000"}, headers=PAGINA)
    r = novo.post(f"{BASE}/api/conta/entrar-mfa", json={"desafio": d, "codigo": recuperacao[2]}, headers=PAGINA)
    conferir(r.status_code == 429, "força bruta no código do aplicativo: 5 erros bloqueiam a conta por 15 min",
             str(r.status_code))  # fmt: skip


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--caddy", default="caddy", help="binário do Caddy 2.10 (o mesmo da imagem do compose)")
    p.add_argument(
        "--db",
        default=os.environ.get(
            "RENDER2GO_TESTE_INVASAO_PG", "postgresql://postgres@/render2go_invasao?host=/var/tmp&port=55432"
        ),
        help="Postgres de teste (APAGADO a cada rodada)",
    )
    a = p.parse_args()
    cfg, enviados, mp, caddy = subir(a.db, a.caddy)
    try:
        anon = navegador()
        a = entrar_cliente(enviados, "cliente.a@gmail.com")
        b = entrar_cliente(enviados, "cliente.b@gmail.com")
        adm = entrar_admin()
        # um pedido e uma recarga da conta A (alvos do IDOR)
        r = a.post(
            f"{BASE}/api/lotes",
            json={
                "prompt": "a câmera se aproxima do bolo",
                "fotos": [{"nome": "bolo.jpg", "tipo": "image/jpeg", "bytes": 1000}],
            },
            headers=PAGINA,
        )
        lote_a = r.json()["lote"]
        r = a.post(f"{BASE}/api/pagamentos/pix", json={"valor": "20", "documento": "529.982.247-25"}, headers=PAGINA)
        pag_a, pid_mp = r.json()["id"], mp.criados[-1]["id"]
        tok_pod_a = cfg.banco.criar_token_worker("pod-a", None, 3600)

        descoberta(anon)
        cabecalhos(anon, a, adm)
        autenticacao(anon, a, adm, tok_pod_a)
        idor(a, b, anon, lote_a, pag_a)
        csrf(a, adm)
        injecao(a, adm)
        webhook(anon, mp, pid_mp)
        acessos_painel(adm)
        configuracoes(cfg, enviados, a, b)
        montagem(cfg, a, b, anon)
        fidelidade(cfg, a, b, anon, adm, lote_a)
        sessoes(enviados)
        limites(enviados)
    finally:
        caddy.terminate()
    falhas = [r for r in resultados if not r[0]]
    print(f"\n{len(resultados) - len(falhas)} de {len(resultados)} verificações OK; {len(falhas)} achado(s)")
    for _, nome, det in falhas:
        print(f"  ACHADO: {nome} [{det}]")
    saida = [{"ok": o, "nome": n, "detalhe": d} for o, n, d in resultados]
    (AQUI / "resultado.json").write_text(json.dumps(saida, ensure_ascii=False, indent=1))
    print(f"resultado em {AQUI / 'resultado.json'}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
