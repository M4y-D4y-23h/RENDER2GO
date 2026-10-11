"""Medição das páginas no navegador (docs/10, seção 5): FCP, LCP, CLS e bytes transferidos de cada tela, com a API
de verdade num Postgres de teste, atrás do Caddyfile de produção (HTTPS e HTTP/2), no Chromium do Playwright, com a
rede e a CPU limitadas pelo DevTools.

Uso (não é coletado pelo pytest; precisa do binário do Caddy 2.10, conferido pelo SHA-512 do release, docs/13):
    cd servico && uv run --with playwright==1.56.0 python tests/medir_paginas.py --caddy /caminho/caddy \\
        --db "postgresql://postgres@/render2go_paginas_teste?host=/var/tmp&port=55432"
O banco é APAGADO a cada rodada: o nome precisa conter "teste" ou "paginas". A rodada semeia uma conta do pré-pago
com 3 pedidos de 5 fotos (em revisão, na fila e pronto), a sessão dela e a do painel (chave do servidor), e mede
cada tela (--telas) em dois cenários (--cenarios):
  lenta: rede lenta para tudo, celular de 390 × 844 px, 4G lenta (1,6 Mbps, 150 ms) e CPU 4× mais lenta, na 1ª
         visita (cache vazio) e na visita de volta (com o que a 1ª deixou no cache);
  cheio: visita de volta com o cache cheio, ida e volta de 0, 120 e 250 ms, sem limite de banda, CPU 4×, em 390 e
         1280 px (a 1ª visita, sem limites, só enche o cache).
Mediana de --repeticoes medições (padrão 5); --saida grava todas em JSON. O CLS é a soma dos deslocamentos sem
toque recente (igual ou acima da janela de sessão das Core Web Vitals: o número é conservador).

O certificado local do Caddy é confiado pela chave pública (--ignore-certificate-errors-spki-list), como um
certificado de verdade: com erro de certificado (ignore_https_errors), o Chromium não guarda nada no cache, e a
visita de volta baixaria o HTML e o resto de novo, o que não acontece em produção. As fotos do armazenamento falso
não carregam (a CSP só libera a origem do R2 configurado, e o falso não tem uma): o painel é medido sem o download
das fotos da revisão. Para comparar versões, rode a mesma ferramenta em cada uma (git worktree), com o mesmo banco
e a mesma máquina.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import ssl
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import requests
import urllib3

TESTES = Path(__file__).resolve().parent
SERVICO = TESTES.parent
sys.path.insert(0, str(TESTES))
urllib3.disable_warnings()

import uvicorn  # noqa: E402
from test_api import ADM, ADMIN, PAINEL, TOTP, Armazem, cliente_teste, config_teste, h  # noqa: E402

from render2go_servico import contas, suporte  # noqa: E402
from render2go_servico.api import RETRATO_S, criar_app  # noqa: E402
from render2go_servico.db import Banco  # noqa: E402

AQUI = Path(tempfile.mkdtemp(prefix="render2go-paginas-"))  # Caddyfile, certificados e logs desta rodada
PORTA_API, PORTA_TLS, PORTA_HTTP = 18300, 18643, 18280  # fora das do teste de invasão e da medição de carga
BASE = f"https://localhost:{PORTA_TLS}"
# telas: endereço e a sessão que pedem (None: visitante, sem sessão)
TELAS = {
    "entrada": ("/", None),
    "pedidos": ("/", "cliente"),
    "extratos": ("/extratos", "cliente"),
    "configuracoes": ("/configuracoes", "cliente"),
    "painel": (f"{PAINEL}/", "painel"),
    "painel-revisao": (f"{PAINEL}/#revisao", "painel"),
    "painel-clientes": (f"{PAINEL}/#clientes", "painel"),
}
CENARIOS = ("lenta", "cheio")
LENTA = {"latencia": 150, "baixar": 1_600_000 // 8, "subir": 750_000 // 8}  # 4G lenta, em bytes por segundo
IDAS_E_VOLTAS = (0, 120, 250)
ALTURAS = {390: 844, 1280: 800}  # largura → altura da janela
CPU = 4
ESCOLHA_COOKIES = json.dumps({"versao": "1", "estatisticas": False, "marketing": False, "quando": "2026-10-08"})
DISPOSITIVO = str(uuid.uuid4())
# FCP, LCP e CLS, observados desde antes do primeiro script da página
OBSERVAR = """(() => {
  const m = (window.__medidas = { fcp: null, lcp: null, cls: 0, deslocados: [] });
  const ver = (tipo, f) =>
    new PerformanceObserver((l) => l.getEntries().forEach(f)).observe({ type: tipo, buffered: true });
  ver('paint', (e) => { if (e.name === 'first-contentful-paint') m.fcp = e.startTime; });
  ver('largest-contentful-paint', (e) => { m.lcp = e.startTime; });
  ver('layout-shift', (e) => {
    if (e.hadRecentInput) return;
    m.cls += e.value;
    for (const s of e.sources || [])
      if (s.node) m.deslocados.push(s.node.id || String(s.node.className || s.node.nodeName));
  });
})();"""
HTML = "() => { const n = performance.getEntriesByType('navigation')[0]; return [n.transferSize, n.encodedBodySize]; }"


# -- servidor -------------------------------------------------------------------------------------


def preparar_banco(db: str) -> Banco:
    nome = urlsplit(db).path.lstrip("/")
    if not db.startswith("postgresql://") or not any(p in nome for p in ("teste", "paginas")):
        raise SystemExit("use um banco Postgres de teste (o nome precisa conter teste ou paginas)")
    banco = Banco(db, pool=8)
    with banco.tx() as c:  # base limpa a cada rodada, com as tabelas de qualquer versão
        for t in c.all("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"):
            c.run(f'DROP TABLE IF EXISTS "{t["tablename"]}" CASCADE')
    banco.criar_esquema()
    return banco


def semear(banco: Banco, arm: Armazem) -> str:
    """A conta do pré-pago com saldo e 3 pedidos de 5 fotos (em revisão, na fila e pronto), como a de quem já usa o
    serviço; devolve o código de acesso dela (a entrada pelo código dá a mesma sessão em cookie que a pelo e-mail)."""
    api = cliente_teste(config_teste(banco, arm, analise="desligada"))
    email = "loja.medida@gmail.com"
    cid, token = banco.criar_cliente("Loja da medida", 500, "pago", email=email, email_norm=email)
    api.post(f"{PAINEL}/api/clientes/{cid}/saldo", json={"valor": "500", "descricao": "medida"}, headers=ADM)
    for k in range(3):
        fotos = [{"nome": f"foto {j}.jpg", "tipo": "image/jpeg", "bytes": 1000 + j} for j in range(5)]
        corpo = {"prompt": "a câmera se aproxima do produto", "fotos": fotos}
        r = api.post("/api/lotes", json=corpo, headers=h(token))
        for j, e in enumerate(r.json()["envios"]):
            arm.objetos[e["url"].split("armazem.teste/")[1].split("?")[0]] = 1000 + j
        lid = r.json()["lote"]
        api.post(f"/api/lotes/{lid}/enviado", headers=h(token))
        if k >= 1:
            api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM)
        if k == 2:
            with banco.tx() as c:
                c.run("UPDATE jobs SET status = 'ok' WHERE lote_id = ?", (lid,))
                c.run("UPDATE lotes SET status = 'concluido' WHERE id = ?", (lid,))
    with banco.tx() as c:  # estatísticas do planejador, como o autovacuum mantém em produção
        c.run("ANALYZE")
    return token


def subir(banco: Banco, arm: Armazem, caddy_bin: str) -> subprocess.Popen:
    """A API (limites por minuto altos: a medição abre a mesma tela dezenas de vezes) atrás do Caddyfile de produção,
    com TLS local no lugar do domínio. Como em produção, para a entrada não mostrar o que a produção não mostra:
    - o "não sou um robô" ligado, com as chaves públicas de teste da Cloudflare (desligado, a página esconde o lugar
      reservado do quadro depois da pintura);
    - a entrada por e-mail ligada, com um envio que não manda nada (sem ela, a entrada avisa "em manutenção")."""
    teste_cloudflare = contas.ConfigTurnstile("1x00000000000000000000AA", "1x0000000000000000000000000000000AA")
    cfg = config_teste(
        banco,
        arm,
        limite_por_minuto=1_000_000,
        retrato_s=RETRATO_S,
        analise="desligada",
        turnstile=teste_cloudflare,
        turnstile_desligado=False,
        email=suporte.ConfigEmail(host="smtp.teste", para="suporte@render2go.com", de="nao-responda@render2go.com"),
        enviar_email=lambda *_: None,
    )
    opcoes = {"proxy_headers": False, "server_header": False, "log_level": "error", "timeout_keep_alive": 30}
    servidor = uvicorn.Server(uvicorn.Config(criar_app(cfg), host="127.0.0.1", port=PORTA_API, **opcoes))
    threading.Thread(target=servidor.run, daemon=True).start()
    real = (SERVICO / "implantacao" / "Caddyfile").read_text()
    local = (
        real.replace("{$DOMINIO} {", f"localhost:{PORTA_TLS} {{\n\ttls internal")
        .replace("api:8000", f"127.0.0.1:{PORTA_API}")
        .replace("/data/logs/acesso.log", str(AQUI / "acesso.log"))
        .replace("{\n\tservers {", f"{{\n\tskip_install_trust\n\thttp_port {PORTA_HTTP}\n\tadmin off\n\tservers {{", 1)
    )
    (AQUI / "Caddyfile").write_text(local)
    caddy = subprocess.Popen(
        [caddy_bin, "run", "--config", str(AQUI / "Caddyfile"), "--adapter", "caddyfile"],
        stdout=(AQUI / "caddy.log").open("w"),
        stderr=subprocess.STDOUT,
        env={**os.environ, "XDG_DATA_HOME": str(AQUI / "dados"), "XDG_CONFIG_HOME": str(AQUI / "config")},
    )
    s = sessao_http()
    for _ in range(100):
        try:
            if s.get(f"{BASE}/saude", timeout=2).ok:
                return caddy
        except requests.RequestException:
            time.sleep(0.2)
    caddy.terminate()
    raise SystemExit(f"Caddy/API não subiram (veja {AQUI / 'caddy.log'})")


def sessao_http() -> requests.Session:
    s = requests.Session()
    s.trust_env = False  # direto no Caddy local, sem o proxy do ambiente
    s.verify = False
    s.headers.update({"User-Agent": "Mozilla/5.0 (medida)", "Sec-Fetch-Site": "same-origin", **ADM})
    return s


def entrar(token: str) -> dict[str, str]:
    """As duas sessões em cookie, abertas pela API como o navegador faria."""
    s = sessao_http()
    r = s.post(f"{BASE}/api/conta/entrar-codigo", json={"codigo": token, "aceite": True})
    if r.status_code != 200:
        raise SystemExit(f"entrada do cliente recusada: {r.status_code} {r.text[:200]}")
    sessoes = {"cliente": r.cookies["__Host-render2go"]}
    agora = int(time.time() // 30)
    for contador in (agora, agora + 1, agora + 2):  # o código usado na semeadura não vale de novo
        r = s.post(f"{BASE}{PAINEL}/api/entrar", json={"token": ADMIN, "totp": contas.totp(TOTP, contador)})
        if r.status_code == 200:
            sessoes["painel"] = r.cookies["__Secure-render2go-painel"]
            return sessoes
    raise SystemExit(f"entrada no painel recusada: {r.status_code} {r.text[:200]}")


def _elemento(der: bytes, i: int) -> tuple[int, int]:
    """Início do conteúdo e fim do elemento DER que começa em i."""
    n = der[i + 1]
    if n < 0x80:
        return i + 2, i + 2 + n
    k = n & 0x7F
    return i + 2 + k, i + 2 + k + int.from_bytes(der[i + 2 : i + 2 + k], "big")


def chave_publica(pem: str) -> str:
    """SHA-256 (base64) da chave pública do certificado (SubjectPublicKeyInfo), como o Chromium pede."""
    der = ssl.PEM_cert_to_DER_cert(pem)
    tbs, _ = _elemento(der, 0)
    i, fim = _elemento(der, tbs)
    campos = []
    while i < fim:
        campos.append((i, _elemento(der, i)[1]))
        i = campos[-1][1]
    # [0] versão (opcional), número de série, algoritmo, emissor, validade, titular, chave pública
    inicio, final = campos[6 if der[campos[0][0]] == 0xA0 else 5]
    return base64.b64encode(hashlib.sha256(der[inicio:final]).digest()).decode()


# -- navegador ------------------------------------------------------------------------------------


def contexto(nav, sessoes: dict[str, str], sessao: str | None, largura: int):
    """Um navegador novo (cache vazio), com a sessão em cookie e as marcas que a página guarda no localStorage."""
    ctx = nav.new_context(
        viewport={"width": largura, "height": ALTURAS[largura]}, locale="pt-BR", timezone_id="America/Sao_Paulo"
    )
    marcas = {"render2go-cookies": ESCOLHA_COOKIES, "render2go-dispositivo": DISPOSITIVO}
    if sessao == "cliente":
        ctx.add_cookies([{"name": "__Host-render2go", "value": sessoes["cliente"], "url": f"{BASE}/"}])
        marcas["render2go-conectado"] = "1"
    elif sessao == "painel":
        cookie = {"name": "__Secure-render2go-painel", "value": sessoes["painel"], "domain": "localhost"}
        ctx.add_cookies([{**cookie, "path": PAINEL, "secure": True, "httpOnly": True, "sameSite": "Strict"}])
        marcas |= {"render2go-painel-conectado": "1", "render2go-painel-papel": "dono"}
    ctx.add_init_script("".join(f"localStorage.setItem({json.dumps(k)}, {json.dumps(v)});" for k, v in marcas.items()))
    ctx.add_init_script(OBSERVAR)
    return ctx


def visitar(ctx, url: str, rede: dict | None, cpu: float) -> dict:
    """Abre a tela numa aba nova (o cache do contexto vale entre as abas) e devolve as medidas."""
    pg = ctx.new_page()
    cdp = ctx.new_cdp_session(pg)
    cdp.send("Network.enable")
    if rede:
        cdp.send(
            "Network.emulateNetworkConditions",
            {
                "offline": False,
                "latency": rede["latencia"],
                "downloadThroughput": rede.get("baixar", -1),
                "uploadThroughput": rede.get("subir", -1),
            },
        )
    cdp.send("Emulation.setCPUThrottlingRate", {"rate": cpu})
    recebidos: list[int] = []
    cdp.on("Network.loadingFinished", lambda e: recebidos.append(e.get("encodedDataLength", 0)))
    pg.goto(url, wait_until="load")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(300)  # o último quadro com os dados da API
    m = pg.evaluate("window.__medidas")
    transferido, corpo = pg.evaluate(HTML)
    pg.close()
    # HTML: baixado (transferido > corpo), revalidado com 304 (0 < transferido < corpo) ou direto do cache (0)
    html = "baixado" if transferido > corpo else ("304" if transferido else "cache")
    return {
        "fcp": m["fcp"],
        "lcp": m["lcp"],
        "cls": m["cls"],
        "deslocados": sorted(set(m["deslocados"])),  # o que se mexeu (id ou classe), para achar a causa de um CLS
        "bytes": sum(recebidos),
        "html": html,
    }


def medir(nav, sessoes, nome: str, cenarios: list[str], repeticoes: int) -> dict:
    caminho, sessao = TELAS[nome]
    url = BASE + caminho
    resultado: dict = {}
    if "lenta" in cenarios:
        primeira, volta = [], []
        for _ in range(repeticoes):
            ctx = contexto(nav, sessoes, sessao, 390)
            primeira.append(visitar(ctx, url, LENTA, CPU))
            volta.append(visitar(ctx, url, LENTA, CPU))
            ctx.close()
        resultado["lenta"] = {"1ª": primeira, "de volta": volta}
    if "cheio" in cenarios:
        resultado["cheio"] = {}
        for rtt in IDAS_E_VOLTAS:
            for largura in ALTURAS:
                amostras = []
                for _ in range(repeticoes):
                    ctx = contexto(nav, sessoes, sessao, largura)
                    visitar(ctx, url, None, 1)  # enche o cache
                    amostras.append(visitar(ctx, url, {"latencia": rtt}, CPU))
                    ctx.close()
                resultado["cheio"][f"{rtt} ms, {largura} px"] = amostras
    return resultado


# -- resumo ---------------------------------------------------------------------------------------


def maquina() -> str:
    try:
        linhas = Path("/proc/cpuinfo").read_text().splitlines()
        modelo = next(ln.split(":", 1)[1].strip() for ln in linhas if ln.startswith("model name"))
    except (OSError, StopIteration):
        modelo = platform.processor() or platform.machine()
    return f"{modelo}, {os.cpu_count()} CPU"


def mediana(amostras: list[dict], chave: str) -> float | None:
    valores = [a[chave] for a in amostras if a[chave] is not None]
    return statistics.median(valores) if valores else None


def segundos(ms: float | None) -> str:
    return "—" if ms is None else f"{ms / 1000:.2f} s".replace(".", ",")


def cls(v: float | None) -> str:
    return "—" if v is None else (f"{v:.3f}".replace(".", ",") if v else "0")


def resumir(nome: str, r: dict) -> None:
    for visita, amostras in r.get("lenta", {}).items():
        kb = f"{mediana(amostras, 'bytes') / 1024:.1f} KB".replace(".", ",")
        htmls = sorted({a["html"] for a in amostras})
        print(
            f"| {nome} | {visita} | {segundos(mediana(amostras, 'fcp'))} | {segundos(mediana(amostras, 'lcp'))}"
            f" | {cls(mediana(amostras, 'cls'))} | {kb} | HTML {', '.join(htmls)} |"
        )
    for chave, amostras in r.get("cheio", {}).items():
        lcp, deslocamento = segundos(mediana(amostras, "lcp")), cls(mediana(amostras, "cls"))
        kb = f"{mediana(amostras, 'bytes') / 1024:.1f} KB".replace(".", ",")
        print(f"| {nome} | cache cheio, {chave} | LCP {lcp} | CLS {deslocamento} | {kb} |")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--caddy", default="caddy", help="binário do Caddy 2.10 (o mesmo da imagem do compose)")
    p.add_argument(
        "--db",
        default=os.environ.get(
            "RENDER2GO_TESTE_PAGINAS_PG", "postgresql://postgres@/render2go_paginas_teste?host=/var/tmp&port=55432"
        ),
        help="Postgres de teste (APAGADO a cada rodada)",
    )
    p.add_argument("--telas", default=",".join(TELAS), help=f"separadas por vírgula, entre: {', '.join(TELAS)}")
    p.add_argument("--cenarios", default=",".join(CENARIOS), help="lenta, cheio ou os dois")
    p.add_argument("--repeticoes", type=int, default=5)
    p.add_argument("--saida", type=Path, help="arquivo JSON com todas as medições")
    a = p.parse_args()
    telas, cenarios = a.telas.split(","), a.cenarios.split(",")
    if set(telas) - set(TELAS) or set(cenarios) - set(CENARIOS):
        raise SystemExit(f"telas: {', '.join(TELAS)}; cenários: {', '.join(CENARIOS)}")

    from playwright.sync_api import sync_playwright

    banco = preparar_banco(a.db)
    arm = Armazem()
    token = semear(banco, arm)
    caddy = subir(banco, arm, a.caddy)
    try:
        sessoes = entrar(token)
        pki = AQUI / "dados" / "caddy" / "pki" / "authorities" / "local"
        chaves = ",".join(chave_publica((pki / n).read_text()) for n in ("root.crt", "intermediate.crt"))
        versao = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=SERVICO, capture_output=True, text=True)
        saida: dict = {"versao": versao.stdout.strip(), "maquina": maquina()}
        with sync_playwright() as pw:
            # sem internet na medição: o que não é da própria máquina vai para um proxy que não existe e falha na hora
            # (o Chromium nunca usa proxy para o localhost). Assim o script do "não sou um robô" (Cloudflare) não
            # carrega, como na medição de 10/10: com ele, o quadro nunca fica ocioso e a rede de fora entra no número.
            # Bloquear com route() não serve: interceptar pedidos desliga o cache HTTP, que a visita de volta mede.
            args = [f"--ignore-certificate-errors-spki-list={chaves}", "--proxy-server=http://127.0.0.1:9"]
            nav = pw.chromium.launch(args=[*args, "--disable-quic"])
            saida["chromium"] = nav.version
            print(f"versão {saida['versao']}, Chromium {nav.version}, {saida['maquina']}, mediana de {a.repeticoes}")
            print("| Tela | Visita | FCP | LCP | CLS | Transferido | |")
            for nome in telas:
                saida[nome] = medir(nav, sessoes, nome, cenarios, a.repeticoes)
                resumir(nome, saida[nome])
            nav.close()
    finally:
        caddy.terminate()
    if a.saida:
        a.saida.write_text(json.dumps(saida, ensure_ascii=False, indent=1))
        print(f"medições em {a.saida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
