"""render2go-servico: API, orquestrador de pods e tarefas de administração.

    render2go-servico api [--porta 8000] [--workers N]   API e páginas (uvicorn; N processos)
    render2go-servico orquestrar                  sobe e encerra pods conforme a fila
    render2go-servico analisar                    filtro das fotos e aviso de pessoas (RENDER2GO_ANALISE=separada)
    render2go-servico montar                      montagem de vídeos (ADR 0015): ffmpeg em CPU, fora da API
    render2go-servico limpar                      encerra pods registrados pelo orquestrador
    render2go-servico hibernados [--runpod]       os hibernados e o que esta versão faria com eles (só leitura;
        antes de uma atualização: docker compose run --rm --no-deps orquestrador render2go-servico hibernados)
    render2go-servico totp-novo                   segredo do autenticador da chave do painel (RENDER2GO_ADMIN_TOTP)
    render2go-servico admin-convite --usuario NOME [--papel dono|operador]
        convite de uso único (24 h) para um acesso ao painel: o 1º acesso do dono, pelo terminal do servidor
        (docker compose exec api render2go-servico admin-convite --usuario dono --papel dono)
    render2go-servico cliente NOME [--plano teste] [--cota 500] [--email E]   cadastra (sem e-mail: mostra o código)
    render2go-servico email CLIENTE EMAIL         liga um e-mail a um cliente antigo (entra pelo e-mail)
    render2go-servico clientes                    lista os clientes com plano e saldo
    render2go-servico saldo CLIENTE VALOR [--descricao "Pix 09/10"] [--referencia ID]
        recarga do pré-pago em reais (ex.: 50 ou 19,90); valor negativo desconta (ajuste)
    render2go-servico apagar-historico [--confirmar] [--cookies] [--forcar]
        apaga pedidos, prompts, trabalhos, autorizações, eventos, suporte e registro de pods, e
        os arquivos dos clientes no R2 (entrada/ e saida/; nunca o espelho dos modelos). Os
        clientes e os códigos de acesso ficam. Sem --confirmar, só mostra o que seria apagado.
        Ficam também, de propósito: aceites dos termos, registros de acesso (Marco Civil, 6 meses),
        extrato, pagamentos e a auditoria do painel. Com clientes de verdade, pedidos e autorizações
        são prova em caso de disputa: use só para limpar testes.

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


def admin_convite(banco, usuario: str, papel: str) -> int:
    """Convite de uso único para o painel (ADR 0009), criado pelo terminal: o caminho do 1º dono."""
    from render2go_servico.api import caminho_admin_do_ambiente
    from render2go_servico.contas import CONVITE_VALIDADE_S, normalizar_usuario
    from render2go_servico.db import BancoError

    try:
        usuario = normalizar_usuario(usuario)
    except ValueError as e:
        print(f"nome de acesso: {e}")
        return 2
    caminho = caminho_admin_do_ambiente()
    if not caminho:
        print("falta RENDER2GO_ADMIN_CAMINHO no .env: sem ele, não há painel (rode no contêiner da API)")
        return 2
    try:
        cid, token = banco.criar_convite(usuario, papel, "terminal", CONVITE_VALIDADE_S)
    except BancoError as e:
        print(e)
        return 1
    banco.auditar("terminal", "convite_criado", usuario, {"convite": cid, "depois": {"papel": papel, "ativo": True}})
    dominio = os.environ.get("DOMINIO", "").strip()
    print(
        f'Convite de uso único para "{usuario}" ({papel}), válido por 24 h:\n'
        f"  {'https://' + dominio if dominio else ''}{caminho}/#convite={token}\n"
        "Mande por um canal seguro (gerenciador de senhas ou mensagem que some), nunca por e-mail aberto nem print.\n"
        "A pessoa abre o endereço, lê o QR Code no aplicativo autenticador, confirma um código e só então cria a\n"
        "senha. Um convite novo para o mesmo nome cancela este (e, para quem já existe, refaz senha e autenticador)."
    )
    return 0


def hibernados(runpod: bool) -> int:
    """Antes de uma atualização (docs/09): com a imagem nova e o .env do servidor, os hibernados que ela encerraria
    (o disco de volume vai junto) e por quê. Não muda nada; nunca mostra os tokens dos agentes."""
    import time

    from render2go_servico.orquestrador import inventario_hibernados

    pods = None
    if runpod:
        from render2go_worker import runpod_pods

        r = runpod_pods._rest("GET", "/pods", runpod_pods._chave())
        pods = r.json() if r.ok and isinstance(r.json(), list) else None
        if pods is None:
            print(f"AVISO: a lista do RunPod não veio ({r.status_code}); só o conjunto em disco")
    inv = inventario_hibernados(pods_runpod=pods, api_url=os.environ.get("RENDER2GO_API_URL", ""))
    print(f"versão deste código: {inv['versao']}; RENDER2GO_PODS_HIBERNADOS={inv['maximo']}")
    if not inv["hibernados"]:
        print("nenhum pod hibernado no conjunto")
    for h in inv["hibernados"]:
        parado = time.strftime("%d/%m %H:%M UTC", time.gmtime(h["parado_em"])) if h.get("parado_em") else "?"
        destino = f"SERIA ENCERRADO ({h['sairia']}): o disco de volume vai junto" if h["sairia"] else "fica"
        print(
            f"  {h['id']}  {h.get('gpu') or '?'}  {h.get('datacentro') or '?'}  parado desde {parado}  "
            f"voltas {h.get('voltas') or 0}  falhas {h.get('falhas') or 0}  versão {h.get('versao')}  "
            f"~US$ {h['usd_dia_parado']:.2f}/dia parado  → {destino}"
        )
    for o in inv["orfaos"]:
        print(f"  {o['id']} ({o['nome']}): parado no RunPod e fora do conjunto: a partida do orquestrador o encerra")
    sairiam = [h for h in inv["hibernados"] if h["sairia"]] + inv["orfaos"]
    print(
        f"{len(sairiam)} pod(s) seriam encerrados com esta versão no ar; o 1º pedido depois disso sobe um pod do zero"
        if sairiam
        else "com esta versão no ar, nenhum pod hibernado é encerrado"
    )
    return 0


def socket_da_api(host: str, porta: int):
    """Socket de escuta da API, criado aqui e não pelo uvicorn: com IPPROTO_TCP e TCP_NODELAY. O do
    uvicorn (protocolo 0) faz o asyncio não desligar o algoritmo de Nagle nas conexões aceitas, e com
    vários processos cada resposta numa conexão reaproveitada (o Caddy reaproveita) esperava ~40 ms pelo
    ACK atrasado do outro lado (medido: 44 ms contra menos de 1 ms; docs/13, seção 4)."""
    import socket

    familia = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(familia, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # as conexões aceitas herdam (Linux)
    sock.bind((host, porta))
    sock.set_inheritable(True)
    return sock


def api(host: str, porta: int, workers: int) -> int:
    """Vários processos (--workers): o uvicorn cria um app por processo pela fábrica. O Caddy é o único que
    fala com a API e sempre manda o IP real em X-Render2Go-IP (proxy_headers desligado: o X-Forwarded-For
    de fora não vale). Sem o cabeçalho "server: uvicorn" nas respostas."""
    import uvicorn
    from uvicorn.supervisors import Multiprocess

    config = uvicorn.Config(
        "render2go_servico.api:criar_app",
        factory=True,
        host=host,
        port=porta,
        workers=max(1, workers),
        proxy_headers=False,
        server_header=False,
        timeout_keep_alive=30,
    )
    servidor = uvicorn.Server(config)
    sock = socket_da_api(host, porta)
    try:
        if config.workers > 1:
            Multiprocess(config, target=servidor.run, sockets=[sock]).run()
        else:
            servidor.run(sockets=[sock])
    except KeyboardInterrupt:
        pass
    # um processo só e a subida falhou (variável faltando): sai com erro, e o contêiner reinicia
    return 3 if config.workers == 1 and not servidor.started else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="render2go-servico", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s_api = sub.add_parser("api", help="API e páginas")
    s_api.add_argument("--host", default="0.0.0.0")
    s_api.add_argument("--porta", type=int, default=int(os.environ.get("PORT", 8000)))
    s_api.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("RENDER2GO_API_WORKERS", 1)),
        help="processos da API (cada um com o seu pool de conexões; padrão RENDER2GO_API_WORKERS ou 1)",
    )
    sub.add_parser("analisar", help="filtro das fotos e aviso de pessoas, fora da API")
    sub.add_parser("montar", help="montagem de vídeos (ADR 0015): ffmpeg em CPU, fora da API")
    sub.add_parser("totp-novo", help="gera o segredo do aplicativo autenticador da chave do painel")
    s_conv = sub.add_parser("admin-convite", help="convite de uso único para um acesso ao painel (ADR 0009)")
    s_conv.add_argument("--usuario", required=True, help="nome de acesso (3 a 32 letras minúsculas, números, . _ -)")
    s_conv.add_argument("--papel", choices=["dono", "operador"], default="dono")
    s_email = sub.add_parser("email", help="liga um e-mail a um cliente antigo (código de acesso)")
    s_email.add_argument("cliente", help="id do cliente (cli_...)")
    s_email.add_argument("email")
    sub.add_parser("orquestrar", help="sobe e encerra pods conforme a fila")
    sub.add_parser("limpar", help="encerra os pods registrados pelo orquestrador")
    s_hib = sub.add_parser(
        "hibernados", help="os pods hibernados e o que esta versão do código faria com eles (só leitura)"
    )
    s_hib.add_argument("--runpod", action="store_true", help="confere também a lista de pods do RunPod (só leitura)")
    s_cli = sub.add_parser("cliente", help="cadastra um cliente e mostra o token (uma vez)")
    s_cli.add_argument("nome")
    s_cli.add_argument("--plano", choices=["pago", "teste", "interno"], default="pago")
    s_cli.add_argument("--cota", type=int, default=500, help="fotos por dia")
    s_cli.add_argument("--email", help="entra pelo código que chega neste e-mail (sem código de acesso)")
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
        return api(a.host, a.porta, a.workers)
    if a.cmd == "hibernados":
        return hibernados(a.runpod)
    if a.cmd == "admin-convite":
        return admin_convite(_banco(), a.usuario, a.papel)
    if a.cmd == "totp-novo":
        from render2go_servico.contas import novo_segredo_totp, uri_totp

        segredo = novo_segredo_totp()
        print(
            "Segredo do autenticador do painel (coloque no .env como RENDER2GO_ADMIN_TOTP e no aplicativo):\n"
            f"  RENDER2GO_ADMIN_TOTP={segredo}\n"
            "No Google Authenticator, Authy ou 1Password: adicionar conta → digitar a chave, ou abrir este endereço:\n"
            f"  {uri_totp(segredo)}\n"
            "Guarde o segredo num lugar seguro (sem ele e sem o celular, só editando o .env de novo)."
        )
        return 0
    if a.cmd == "analisar":
        import tempfile
        import threading
        from pathlib import Path

        from render2go_servico import filtro_fotos, pessoas
        from render2go_servico.api import analisar_continuamente
        from render2go_servico.armazenamento import Armazenamento

        arm = Armazenamento.do_ambiente()
        if arm is None:
            raise SystemExit("faltam RENDER2GO_R2_ENDPOINT, _BUCKET, _ACCESS_KEY_ID ou _SECRET_ACCESS_KEY")
        parar = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: parar.set())
        signal.signal(signal.SIGINT, lambda *_: parar.set())
        print("[analise] rodando (filtro das fotos e aviso de pessoas)", flush=True)
        vivo = Path(tempfile.gettempdir()) / "render2go-analisador-vivo"  # healthcheck do contêiner (compose)
        # o classificador carrega na 1ª volta (10–20 s); sem ele, nenhuma foto é liberada (o painel mostra o motivo)
        filtro = filtro_fotos.FiltroFotos()
        analisar_continuamente(_banco(), arm, pessoas.analisador(), parar, vivo=vivo, filtro=filtro)
        return 0
    if a.cmd == "montar":
        import tempfile
        import threading
        from pathlib import Path

        from render2go_servico import montagem
        from render2go_servico.armazenamento import Armazenamento

        arm = Armazenamento.do_ambiente()
        if arm is None:
            raise SystemExit("faltam RENDER2GO_R2_ENDPOINT, _BUCKET, _ACCESS_KEY_ID ou _SECRET_ACCESS_KEY")
        try:  # a fonte dos textos (o build da imagem já conferiu): sem ela, a montagem com texto falha, sem custo
            montagem.conferido(montagem.pasta_padrao(), "fonte.ttf")
        except montagem.ArquivoIndisponivel as e:
            print(f"[montagem] AVISO: {e}", flush=True)
        parar = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: parar.set())
        signal.signal(signal.SIGINT, lambda *_: parar.set())
        print("[montagem] rodando (ffmpeg em CPU)", flush=True)
        vivo = Path(tempfile.gettempdir()) / "render2go-montador-vivo"  # healthcheck do contêiner (compose)
        montagem.montar_continuamente(_banco(), arm, parar, vivo=vivo)
        return 0
    if a.cmd == "email":
        from render2go_servico.contas import EmailRecusado, normalizar
        from render2go_servico.db import BancoError

        try:
            email, norm = normalizar(a.email, exigir_dominio=False)
            _banco().definir_email(a.cliente, email, norm)
        except (EmailRecusado, BancoError) as e:
            print(e)
            return 2
        print(f"{a.cliente}: entra agora pelo e-mail {email}")
        return 0
    if a.cmd == "cliente":
        if a.email:
            from render2go_servico.contas import EmailRecusado, normalizar

            try:
                email, norm = normalizar(a.email, exigir_dominio=False)
            except EmailRecusado as e:
                print(e)
                return 2
            cid, _ = _banco().criar_cliente(email, a.cota, a.plano, email=email, email_norm=norm)
            print(f"cliente {cid} ({a.plano}); entra pela página com o código que chega em {email}")
            return 0
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
