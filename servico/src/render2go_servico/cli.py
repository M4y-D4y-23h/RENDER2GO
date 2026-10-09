"""render2go-servico: API, orquestrador de pods e tarefas de administração.

    render2go-servico api [--porta 8000]          API e páginas (uvicorn)
    render2go-servico orquestrar                  sobe e encerra pods conforme a fila
    render2go-servico limpar                      encerra pods registrados pelo orquestrador
    render2go-servico cliente NOME [--plano teste] [--cota 500]   cadastra e mostra o token
    render2go-servico clientes                    lista os clientes com plano e saldo
    render2go-servico saldo CLIENTE VALOR [--descricao "Pix 09/10"] [--referencia ID]
        recarga do pré-pago em reais (ex.: 50 ou 19,90); valor negativo desconta (ajuste)
    render2go-servico apagar-historico [--confirmar] [--cookies] [--forcar]
        apaga pedidos, prompts, trabalhos, autorizações, eventos, suporte e registro de pods, e
        os arquivos dos clientes no R2 (entrada/ e saida/; nunca o espelho dos modelos). Os
        clientes e os códigos de acesso ficam. Sem --confirmar, só mostra o que seria apagado.

Configuração por variáveis de ambiente (ver docs/09-implantacao.md):
    RENDER2GO_DB_URL, RENDER2GO_R2_*, RENDER2GO_SEGREDO_APROVACAO, RENDER2GO_ADMIN_TOKENS,
    RENDER2GO_WORKER_TOKENS, RENDER2GO_API_URL (orquestrador), RUNPOD_API_KEY (orquestrador).
"""

from __future__ import annotations

import argparse
import os
import signal
import sys


def _banco():
    from render2go_servico.db import Banco

    url = os.environ.get("RENDER2GO_DB_URL")
    if not url:
        raise SystemExit("falta RENDER2GO_DB_URL")
    b = Banco(url)
    b.criar_esquema()
    return b


# prefixos dos arquivos dos clientes no R2; o espelho dos modelos (modelos/) nunca entra
PREFIXOS_CLIENTES = ("entrada/", "saida/")


def apagar_historico(banco, armazenamento, confirmar: bool, cookies: bool, forcar: bool) -> int:
    contagem = banco.contar_historico(cookies)
    print("no banco: " + ", ".join(f"{t} {n}" for t, n in contagem.items()))
    arquivos: list[str] = []
    if armazenamento is None:
        print("R2 não configurado (RENDER2GO_R2_*): os arquivos ficam como estão")
    else:
        try:
            for prefixo in PREFIXOS_CLIENTES:
                achados = armazenamento.listar(prefixo)
                print(f"no R2: {len(achados)} arquivo(s) em {prefixo}")
                arquivos += achados
        except Exception as e:  # noqa: BLE001 (o banco é apagado do mesmo jeito)
            print(f"AVISO: não deu para listar o R2 ({e}); os arquivos ficam (os vídeos somem em 30 dias)")
            armazenamento, arquivos = None, []
    if n := banco.em_geracao():
        print(f"AVISO: {n} pedido(s) em geração agora" + ("" if forcar else "; espere terminar ou use --forcar"))
        if not forcar:
            return 1
    if not confirmar:
        print("nada foi apagado; para apagar, rode de novo com --confirmar")
        return 0
    apagados = banco.apagar_historico(cookies=cookies, forcar=forcar)
    banco.compactar()  # reescreve as tabelas: as linhas apagadas não ficam no disco do banco
    print("apagado do banco: " + ", ".join(f"{t} {n}" for t, n in apagados.items()))
    falhas = []
    if armazenamento is not None and arquivos:
        from concurrent.futures import ThreadPoolExecutor

        def apagar(chave: str) -> str | None:
            try:
                armazenamento.apagar(chave)
                return None
            except Exception as e:  # noqa: BLE001 (lista as falhas no fim; o resto continua)
                return f"{chave}: {e}"

        with ThreadPoolExecutor(max_workers=16) as ex:
            falhas = [f for f in ex.map(apagar, arquivos) if f]
        print(f"apagados do R2: {len(arquivos) - len(falhas)} arquivo(s)")
        for f in falhas[:20]:
            print(f"  falhou: {f}")
    return 1 if falhas else 0


def saldo(banco, cliente_id: str, valor: str, descricao: str, referencia: str | None = None) -> int:
    from render2go_servico.db import BancoError
    from render2go_servico.precos import MAX_MOVIMENTO, PLANOS_COBRADOS, centavos, reais

    try:
        c = centavos(valor)
    except ValueError as e:
        print(e)
        return 2
    cli = banco.cliente(cliente_id)
    if cli is None or not 0 < abs(c) <= MAX_MOVIMENTO or cli["plano"] not in PLANOS_COBRADOS:
        print("cliente não encontrado, plano sem saldo ou valor fora de R$ 0,01 a R$ 100.000,00")
        return 2
    try:
        novo = banco.movimentar(cliente_id, c, descricao, "cli", f"pagamento:{referencia}" if referencia else None)
    except BancoError as e:
        print(e)
        return 1
    print(f"{cli['nome']}: {'+' if c > 0 else ''}{reais(c)}; saldo agora {reais(novo)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="render2go-servico", description=__doc__.split("\n")[0])
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
    sub.add_parser("clientes", help="lista os clientes com plano e saldo")
    s_saldo = sub.add_parser("saldo", help="recarga (ou ajuste, com valor negativo) do saldo de um cliente")
    s_saldo.add_argument("cliente", help="id do cliente (cli_...)")
    s_saldo.add_argument("valor", help="em reais, ex.: 50 ou 19,90; negativo desconta")
    s_saldo.add_argument("--descricao", default="recarga", help="aparece no extrato do cliente")
    s_saldo.add_argument("--referencia", help="id do pagamento (o mesmo pagamento nunca entra duas vezes)")
    s_hist = sub.add_parser(
        "apagar-historico",
        help="apaga pedidos, prompts, auditoria, suporte e os arquivos dos clientes (os clientes ficam)",
    )
    s_hist.add_argument("--confirmar", action="store_true", help="apaga de verdade (sem isso, só conta)")
    s_hist.add_argument("--cookies", action="store_true", help="apaga também as escolhas do aviso de cookies")
    s_hist.add_argument("--forcar", action="store_true", help="apaga mesmo com pedido em geração")
    a = p.parse_args(argv)

    if a.cmd == "api":
        import uvicorn

        from render2go_servico.api import criar_app

        uvicorn.run(criar_app(), host=a.host, port=a.porta, proxy_headers=True)
        return 0
    if a.cmd == "cliente":
        cid, token = _banco().criar_cliente(a.nome, a.cota, a.plano)
        print(f"cliente {cid} ({a.plano}); token (guarde, não aparece de novo):\n{token}")
        return 0
    if a.cmd == "clientes":
        from render2go_servico.precos import reais

        for k in _banco().clientes():
            ativo = "" if k["ativo"] else " (desativado)"
            print(f"{k['id']}  {k['plano']:<8} {reais(int(k['saldo'])):>14}  {k['nome']}{ativo}")
        return 0
    if a.cmd == "saldo":
        return saldo(_banco(), a.cliente, a.valor, a.descricao, a.referencia)
    if a.cmd == "apagar-historico":
        from render2go_servico.armazenamento import Armazenamento

        return apagar_historico(_banco(), Armazenamento.do_ambiente(), a.confirmar, a.cookies, a.forcar)

    from render2go_worker import lote_nuvem as ln

    from render2go_servico.orquestrador import ConfigOrq, Orquestrador

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
