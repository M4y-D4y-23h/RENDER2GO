"""gpu2go-servico: API, orquestrador de pods e tarefas de administração.

    gpu2go-servico api [--porta 8000]          API e páginas (uvicorn)
    gpu2go-servico orquestrar                  sobe e encerra pods conforme a fila
    gpu2go-servico limpar                      encerra pods registrados pelo orquestrador
    gpu2go-servico cliente NOME [--plano teste] [--cota 500]   cadastra e mostra o token

Configuração por variáveis de ambiente (ver docs/09-implantacao.md):
    GPU2GO_DB_URL, GPU2GO_R2_*, GPU2GO_SEGREDO_APROVACAO, GPU2GO_ADMIN_TOKENS,
    GPU2GO_WORKER_TOKENS, GPU2GO_API_URL (orquestrador), RUNPOD_API_KEY (orquestrador).
"""

from __future__ import annotations

import argparse
import os
import signal
import sys


def _banco():
    from gpu2go_servico.db import Banco

    url = os.environ.get("GPU2GO_DB_URL")
    if not url:
        raise SystemExit("falta GPU2GO_DB_URL")
    b = Banco(url)
    b.criar_esquema()
    return b


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gpu2go-servico", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s_api = sub.add_parser("api", help="API e páginas")
    s_api.add_argument("--host", default="0.0.0.0")
    s_api.add_argument("--porta", type=int, default=int(os.environ.get("PORT", 8000)))
    sub.add_parser("orquestrar", help="sobe e encerra pods conforme a fila")
    sub.add_parser("limpar", help="encerra os pods registrados pelo orquestrador")
    s_cli = sub.add_parser("cliente", help="cadastra um cliente e mostra o token (uma vez)")
    s_cli.add_argument("nome")
    s_cli.add_argument("--plano", choices=["pago", "teste"], default="pago")
    s_cli.add_argument("--cota", type=int, default=500, help="fotos por dia")
    a = p.parse_args(argv)

    if a.cmd == "api":
        import uvicorn

        from gpu2go_servico.api import criar_app

        uvicorn.run(criar_app(), host=a.host, port=a.porta, proxy_headers=True)
        return 0
    if a.cmd == "cliente":
        cid, token = _banco().criar_cliente(a.nome, a.cota, a.plano)
        print(f"cliente {cid} ({a.plano}); token (guarde, não aparece de novo):\n{token}")
        return 0

    from gpu2go_worker import lote_nuvem as ln

    from gpu2go_servico.orquestrador import ConfigOrq, Orquestrador

    if a.cmd == "limpar":
        return ln.limpar(ConfigOrq.do_ambiente().estado, ln.RunPod())
    cfg = ConfigOrq.do_ambiente()
    o = Orquestrador(_banco(), cfg, espelho=ln.preparar_espelho())

    def parar(*_):
        o.parar.set()

    signal.signal(signal.SIGTERM, parar)
    signal.signal(signal.SIGINT, parar)
    o.rodar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
