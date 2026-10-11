"""Medição de carga da API (docs/13, seção 4): contas com pedidos num Postgres de teste, a API de verdade
com N processos (`render2go-servico api --workers N`) e clientes fazendo o que a página faz.

Uso (não é coletado pelo pytest):
    cd servico && uv run python tests/medir_carga.py --processos 2 --conexoes 2 \\
        --db "postgresql://postgres@/render2go_carga_teste?host=/var/tmp&port=55432"
O banco é APAGADO e semeado de novo: o nome precisa conter "teste" ou "carga". Cenários (cada um com
os segundos pedidos, depois de um aquecimento): "lotes" (a página do cliente acompanhando os pedidos,
GET /api/lotes a cada 10 s enquanto há pedido em andamento), "misto" (85% pedidos, 10% conta, 5% página
inicial) e "pagina" (só a página inicial). Gerador e API dividem a mesma CPU: os números são um piso.
"""

from __future__ import annotations

import argparse
import http.client
import json
import multiprocessing as mp
import os
import random
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

TESTES = Path(__file__).resolve().parent
SERVICO = TESTES.parent


def semear(db: str, n: int) -> list[str]:
    """n contas pré-pagas com 3 pedidos de 5 fotos cada (em revisão, na fila e pronto); devolve as sessões."""
    sys.path.insert(0, str(TESTES))
    from test_api import ADM, PAINEL, Armazem, cliente_teste, config_teste, entrar_admin, h

    from render2go_servico.db import Banco

    banco = Banco(db, pool=4)
    with banco.tx() as c:  # base limpa, com todas as tabelas (também as que versões novas acrescentarem)
        for t in c.all("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"):
            c.run(f'DROP TABLE IF EXISTS "{t["tablename"]}" CASCADE')
    banco.criar_esquema()
    arm = Armazem()
    api = cliente_teste(config_teste(banco, arm))
    entrar_admin(api)
    sessoes = []
    for i in range(n):
        email = f"carga{i}@gmail.com"
        cid, tok = banco.criar_cliente(email, 500, "pago", email=email, email_norm=email)
        api.post(f"{PAINEL}/api/clientes/{cid}/saldo", json={"valor": "500", "descricao": "carga"}, headers=ADM)
        for k in range(3):
            fotos = [{"nome": f"foto {j}.jpg", "tipo": "image/jpeg", "bytes": 1000 + j} for j in range(5)]
            corpo = {"prompt": "a câmera se aproxima do produto", "fotos": fotos}
            r = api.post("/api/lotes", json=corpo, headers=h(tok))
            for j, e in enumerate(r.json()["envios"]):
                arm.objetos[e["url"].split("armazem.teste/")[1].split("?")[0]] = 1000 + j
            lid = r.json()["lote"]
            api.post(f"/api/lotes/{lid}/enviado", headers=h(tok))
            if k >= 1:
                api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM)
            if k == 2:
                with banco.tx() as c:
                    c.run("UPDATE jobs SET status = 'ok' WHERE lote_id = ?", (lid,))
                    c.run("UPDATE lotes SET status = 'concluido' WHERE id = ?", (lid,))
        ip = f"10.0.{i // 250}.{i % 250}"
        sessoes.append(banco.criar_sessao("cliente", 86400, {"ip": ip, "rede": ip}, cliente_id=cid))
    with banco.tx() as c:  # estatísticas do planejador, como o autovacuum mantém em produção
        c.run("ANALYZE")
    banco.fechar()
    return sessoes


def subir_api(db: str, porta: int, processos: int) -> subprocess.Popen:
    env = {
        **os.environ,
        "RENDER2GO_DB_URL": db,
        "RENDER2GO_R2_ENDPOINT": "https://conta.r2.cloudflarestorage.com",  # só assina URLs (sem rede)
        "RENDER2GO_R2_BUCKET": "carga",
        "RENDER2GO_R2_ACCESS_KEY_ID": "x",
        "RENDER2GO_R2_SECRET_ACCESS_KEY": "y",
        "RENDER2GO_SEGREDO_APROVACAO": "s" * 40,
        "RENDER2GO_ANALISE": "desligada",
        "RENDER2GO_COOKIE_INSEGURO": "1",  # HTTP local, sem o Caddy
        "RENDER2GO_TURNSTILE": "desligado",
    }
    api = subprocess.Popen(
        [sys.executable, "-m", "render2go_servico.cli", "api", "--workers", str(processos), "--porta", str(porta)],
        cwd=SERVICO,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # o grupo inteiro (supervisor e processos) sai junto no fim
    )
    for _ in range(120):
        try:
            con = http.client.HTTPConnection("127.0.0.1", porta, timeout=2)
            con.request("GET", "/saude")
            if con.getresponse().status == 200:
                return api
        except OSError:
            pass
        time.sleep(0.5)
    os.killpg(api.pid, signal.SIGTERM)
    raise SystemExit("a API não subiu")


def _conectar(porta: int) -> http.client.HTTPConnection:
    con = http.client.HTTPConnection("127.0.0.1", porta, timeout=10)
    con.connect()
    con.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return con


def _cliente_de_carga(porta, sessoes, ate, cenario, fila):
    con, lat, erros = _conectar(porta), [], 0
    while time.time() < ate:
        s, r = random.choice(sessoes), random.random()
        if cenario == "pagina" or (cenario == "misto" and r > 0.95):
            caminho, cab = "/", {"Accept": "text/html"}
        elif cenario == "misto" and r > 0.85:
            caminho, cab = "/api/conta", {"Cookie": f"render2go-sessao={s}"}
        else:
            caminho, cab = "/api/lotes", {"Cookie": f"render2go-sessao={s}"}
        ip = f"10.9.{random.randint(0, 255)}.{random.randint(1, 254)}"  # como o Caddy manda
        t0 = time.perf_counter()
        try:
            con.request("GET", caminho, headers={**cab, "X-Render2Go-IP": ip})
            resp = con.getresponse()
            resp.read()
            if resp.status != 200:
                erros += 1
        except OSError:
            erros += 1
            con.close()
            con = _conectar(porta)
            continue
        lat.append(time.perf_counter() - t0)
    fila.put((lat, erros))


def medir(porta: int, sessoes: list[str], segundos: float, conexoes: int, cenario: str) -> dict:
    fila: mp.Queue = mp.Queue()
    ate = time.time() + segundos
    ps = [mp.Process(target=_cliente_de_carga, args=(porta, sessoes, ate, cenario, fila)) for _ in range(conexoes)]
    for p in ps:
        p.start()
    res = [fila.get() for _ in ps]
    for p in ps:
        p.join()
    lat = sorted(x for r in res for x in r[0])
    n = len(lat)

    def pct(q: float) -> float:
        return round(lat[min(n - 1, int(q * n))] * 1000, 1) if n else 0.0

    return {
        "cenario": cenario,
        "req_s": round(n / segundos, 1),
        "p50_ms": pct(0.5),
        "p95_ms": pct(0.95),
        "p99_ms": pct(0.99),
        "erros": sum(r[1] for r in res),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--db", default="postgresql://postgres@/render2go_carga_teste?host=/var/tmp&port=55432")
    p.add_argument("--contas", type=int, default=300)
    p.add_argument("--processos", type=int, default=2, help="processos da API (RENDER2GO_API_WORKERS)")
    p.add_argument("--conexoes", type=int, default=2, help="clientes simultâneos (um processo cada)")
    p.add_argument("--segundos", type=float, default=20)
    p.add_argument("--porta", type=int, default=18200)
    a = p.parse_args()
    nome = urlsplit(a.db).path.lstrip("/")
    if not a.db.startswith("postgresql://") or not any(x in nome for x in ("teste", "carga")):
        raise SystemExit("use um banco Postgres de teste (o nome precisa conter teste ou carga)")
    print(f"semeando {a.contas} contas...", flush=True)
    sessoes = semear(a.db, a.contas)
    api = subir_api(a.db, a.porta, a.processos)
    try:
        medir(a.porta, sessoes, 5, a.conexoes, "misto")  # aquecimento: conexões do banco e 1º uso das sessões
        for cenario in ("lotes", "misto", "pagina"):
            r = medir(a.porta, sessoes, a.segundos, a.conexoes, cenario)
            print(json.dumps({"processos_api": a.processos, "conexoes": a.conexoes, **r}), flush=True)
    finally:
        os.killpg(api.pid, signal.SIGTERM)
        api.wait(30)
    return 0


if __name__ == "__main__":
    sys.exit(main())
