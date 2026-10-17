"""Painel no navegador (ADR 0009 e docs/10): uma seção por endereço (#pods...), cada uma com a própria rolagem e
os próprios dados; o papel de operador; o primeiro acesso por convite (QR Code → código → senha); a entrada da
pessoa; acessos; auditoria (filtros, página seguinte e planilha); e nada se mexe quando a API responde tarde.
Precisa do pacote playwright e de um Chromium; senão, pula."""

import csv
import io
import json
import re
import time
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")
from test_api import PAINEL  # noqa: E402
from test_fila import _png, ambiente  # noqa: E402,F401 (mesma API local e armazenamento)
from test_paginas import (  # noqa: E402,F401
    CLS,
    PAGINA,
    SECOES_PAINEL,
    _api,
    _axe,
    _cliente,
    _com_sessao,
    _lote,
    _pagina,
    navegador,
)

from render2go_servico import contas  # noqa: E402

CHAVE_PAPEL = "render2go-painel-papel"
SENHA = "uma frase comprida e só minha"
# o que cada seção pede à API quando abre (caminho depois de "<painel>/api/")
DADOS = {
    "visao-geral": "resumo",
    "revisao": "lotes",
    "historico": "historico?grupo=todos",
    "montagens": "montagens",
    "entregas": "entregas",
    "clientes": "clientes",
    "suporte": "suporte",
    "interesses": "interesses",
    "pods": "pods",
    "gpus": "geradores?dias=30",
    "pagamentos": "pagamentos",
    "acessos": "acessos",
    "auditoria": "auditoria",
    "seguranca": "seguranca",
}
VISIVEL_NO_HTML = (  # o que o <head> já decidiu antes de qualquer script da página (lugar reservado, sem CLS)
    "document.addEventListener('DOMContentLoaded', () => { const v = (s) => document.querySelector(s)"
    "?.checkVisibility() ?? null; window.__html = {secoes: [...document.querySelectorAll('.secao-painel')]"
    ".filter((e) => e.checkVisibility()).map((e) => e.id), entrada: v('#form-admin'), convite: v('#convite'),"
    " acessos: v('#menu-painel [data-secao=acessos]'), gpus: v('#menu-painel [data-secao=gpus]'),"
    " nova: v('#nova-conta')}; });"
)


def _host(amb) -> str:
    return amb.url.split("//")[1].split(":")[0]


def _convite(amb, usuario: str, papel: str) -> str:
    """Um convite de primeiro acesso, criado pelo dono (a chave do servidor); devolve o endereço (#convite=…)."""
    return _api(amb, "POST", f"{PAINEL}/api/acessos/convites", json={"usuario": usuario, "papel": papel})["endereco"]


def _pessoa(amb, usuario="maria.revisora", papel="operador", senha=SENHA) -> tuple[str, str]:
    """Uma pessoa com acesso próprio ao painel, pela API (convite → QR Code → código do aplicativo → senha).
    Devolve o cookie da sessão aberta no fim e o segredo do aplicativo dela."""
    import requests

    token = _convite(amb, usuario, papel).split("#convite=")[1]
    s = requests.Session()
    s.headers.update(PAGINA)
    r = s.post(f"{amb.url}{PAINEL}/api/convite/iniciar", json={"convite": token}, timeout=10)
    assert r.status_code == 200, r.text
    segredo = r.json()["segredo"]
    codigo = contas.totp(segredo, int(time.time() // 30) - 1)  # o passo anterior: o atual e o seguinte ficam livres
    r = s.post(f"{amb.url}{PAINEL}/api/convite/confirmar", json={"convite": token, "codigo": codigo}, timeout=10)
    assert r.status_code == 200, r.text
    r = s.post(f"{amb.url}{PAINEL}/api/convite/senha", json={"convite": token, "senha": senha}, timeout=10)
    assert r.status_code == 200, r.text
    return s.cookies["render2go-painel"], segredo


def _com_pessoa(ctx, amb, cookie: str, papel: str, fixa=True) -> None:
    """Navegador de uma pessoa que já entrou: o cookie, a marca de "entrou" e o papel guardado da última vez."""
    ctx.add_cookies([{"name": "render2go-painel", "value": cookie, "domain": _host(amb), "path": PAINEL}])
    script = (
        f"localStorage.setItem('render2go-painel-conectado', '1');localStorage.setItem({CHAVE_PAPEL!r}, '{papel}');"
    )
    if not fixa:
        uma_vez = "if (!sessionStorage.getItem('teste-marcado')) { sessionStorage.setItem('teste-marcado', '1'); "
        script = f"{uma_vez}{script} }}"
    ctx.add_init_script(script)


def _auditar_muito(amb, n: int) -> None:
    """n ações na auditoria: plano (i par) e saldo (i ímpar), de "ana.dona" (i múltiplo de 3) e de "maria"."""
    ua = "Mozilla/5.0 (X11; Linux x86_64) Chrome/141.0"
    for i in range(n):
        if i % 2:
            acao, detalhe = "saldo", {"antes": {"saldo": i}, "depois": {"saldo": i + 500}}
        else:
            acao, detalhe = "plano", {"antes": {"plano": "teste"}, "depois": {"plano": "pago"}}
        amb.banco.auditar("maria" if i % 3 else "ana.dona", acao, f"cli_{i:04d}", detalhe, "203.0.113.9", ua)


def _pedidos_api(pg) -> list[str]:
    pedidos: list[str] = []
    pg.on("request", lambda r: f"{PAINEL}/api/" in r.url and pedidos.append(r.url.split(f"{PAINEL}/api/")[1]))
    return pedidos


def _secao_pronta(pg, secao: str) -> None:
    pg.wait_for_selector(f"#sec-{secao} .dados-vista[aria-busy=false]")


def test_each_section_has_its_own_address_title_scroll_and_data(ambiente, navegador):  # noqa: F811
    erros: list[str] = []
    tok = _cliente(ambiente)
    _lote(ambiente, tok, {"bolo.png": _png((40, 90, 160), "circulo")})
    _auditar_muito(ambiente, 60)  # uma auditoria comprida, para rolar
    disp = "6f1c2a3b-4d5e-4f60-8a71-92b3c4d5e6f7"
    ctx = navegador.new_context(viewport={"width": 1280, "height": 800})
    _com_sessao(ctx, ambiente, admin=True)
    ctx.add_init_script(f"localStorage.setItem('render2go-dispositivo', '{disp}')")
    ctx.add_init_script(VISIVEL_NO_HTML)
    pg = _pagina(ctx, erros)
    pedidos = _pedidos_api(pg)
    pg.goto(f"{ambiente.url}{PAINEL}/")
    _secao_pronta(pg, "visao-geral")
    # sem # no endereço: a visão geral, desde o 1º quadro; o <head> já pediu a sessão e os números, uma vez só
    assert pg.evaluate("window.__html.secoes") == ["sec-visao-geral"]
    assert pg.url.endswith(f"{PAINEL}/#visao-geral")
    assert sorted(pedidos) == ["resumo", "sessao"]
    # cada seção: o endereço, o título da aba, o item do menu, o foco no título e só os dados dela
    for secao in SECOES_PAINEL[1:]:
        pedidos.clear()
        pg.click(f"#menu-painel a[data-secao='{secao}']")
        _secao_pronta(pg, secao)
        pw.expect(pg.locator(f"#t-{secao}")).to_be_focused()
        assert pg.url.endswith(f"#{secao}")
        assert pg.locator("section.secao-painel:visible").evaluate_all("(s) => s.map((e) => e.id)") == [f"sec-{secao}"]
        assert pg.locator("#menu-painel a[aria-current=page]").get_attribute("data-secao") == secao
        assert pg.title().endswith("· Painel · Render2Go")
        assert pedidos == [DADOS[secao]], (secao, pedidos)
    # voltar e avançar do navegador trocam de seção (sem recarregar a página)
    pg.go_back()
    pw.expect(pg.locator("#sec-auditoria")).to_be_visible()
    pg.go_forward()
    pw.expect(pg.locator("#sec-seguranca")).to_be_visible()
    # cada seção volta para onde estava a rolagem; a nova começa no alto
    pg.click("#menu-painel a[data-secao=auditoria]")
    pg.wait_for_selector("#auditoria-lista li")
    pg.evaluate("window.scrollTo({top: 900, behavior: 'instant'})")
    pg.click("#menu-painel a[data-secao=pods]")
    assert pg.evaluate("window.scrollY") == 0
    pg.click("#menu-painel a[data-secao=auditoria]")
    assert abs(pg.evaluate("window.scrollY") - 900) <= 1
    # o cartão da visão geral leva à seção
    pg.click("#menu-painel a[data-secao=visao-geral]")
    pg.click("#resumo-agora a[data-secao=revisao]")
    pw.expect(pg.locator("#t-revisao")).to_be_focused()
    pw.expect(pg.locator("#pendentes .pendente")).to_have_count(1)
    # "Pular para o conteúdo" não troca a seção (o # volta a ser o da seção)
    pg.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
    pg.locator(".pular").focus()
    pg.keyboard.press("Enter")
    pw.expect(pg.locator("#conteudo")).to_be_focused()
    pw.expect(pg).to_have_url(re.compile(r"#revisao$"))
    pw.expect(pg.locator("#sec-revisao")).to_be_visible()
    ctx.close()
    # endereço direto de uma seção (favorito): ela, desde o 1º quadro; # desconhecido: a visão geral
    ctx = navegador.new_context()
    _com_sessao(ctx, ambiente, admin=True)
    ctx.add_init_script(VISIVEL_NO_HTML)
    pg = _pagina(ctx, erros)
    pg.goto(f"{ambiente.url}{PAINEL}/#pods")
    _secao_pronta(pg, "pods")
    assert pg.evaluate("window.__html.secoes") == ["sec-pods"]
    pw.expect(pg).to_have_title("(1) Pods · Painel · Render2Go")  # o número da revisão no título
    pg = _pagina(ctx, erros)  # outra aba (na mesma, o goto só trocaria o # da página)
    pg.goto(f"{ambiente.url}{PAINEL}/#nada-disso")
    _secao_pronta(pg, "visao-geral")
    pw.expect(pg).to_have_url(re.compile(r"#visao-geral$"))
    ctx.close()
    assert erros == [], erros


def test_the_operator_sees_only_what_the_role_opens(ambiente, navegador):  # noqa: F811
    erros: list[str] = []
    tok = _cliente(ambiente)
    _lote(ambiente, tok, {"bolo.png": _png((40, 90, 160), "circulo")}, aprovar=True)
    _lote(ambiente, tok, {"loja.png": _png((200, 120, 60), "barras")})
    g = ambiente.banco.gerador_subindo("NVIDIA L40S")
    ambiente.banco.gerador_pronto(g, "pod-abc123", "NVIDIA L40S", 1.09)
    cookie, _ = _pessoa(ambiente, "maria.revisora", "operador")
    ctx = navegador.new_context(viewport={"width": 1280, "height": 800})
    _com_pessoa(ctx, ambiente, cookie, "operador")
    ctx.add_init_script(VISIVEL_NO_HTML)
    pg = _pagina(ctx, erros)
    pedidos = _pedidos_api(pg)
    pg.goto(f"{ambiente.url}{PAINEL}/#acessos")  # uma seção que o papel não abre: a visão geral
    _secao_pronta(pg, "visao-geral")
    # o papel guardado da última vez já esconde, antes de desenhar, o que o operador não abre
    html = pg.evaluate("window.__html")
    assert html["acessos"] is False and html["gpus"] is False and html["nova"] is False
    pw.expect(pg).to_have_url(re.compile(r"#visao-geral$"))
    pw.expect(pg.locator("#quem-nome")).to_have_text("maria.revisora")
    pw.expect(pg.locator("#quem-papel")).to_have_text("Operador")
    visiveis = pg.locator("#menu-painel a[data-secao]:visible").evaluate_all("(a) => a.map((x) => x.dataset.secao)")
    assert visiveis == ["visao-geral", "revisao", "historico", "montagens", "entregas", "clientes", "suporte",
                        "interesses", "pods"]  # fmt: skip
    assert pg.locator("#menu-painel .grupo:visible").count() == 4  # o grupo "Segurança" inteiro sai
    # visão geral: sem dinheiro e sem os Pix pendentes
    pw.expect(pg.locator("#resumo-hoje")).not_to_contain_text("Cobrado")
    assert pg.locator("#resumo-agora a[data-secao=pagamentos]").count() == 0
    # contas: só ver (sem "Nova conta", sem lançar no saldo nem mudar o plano); o extrato, sim
    pg.click("#menu-painel a[data-secao=clientes]")
    _secao_pronta(pg, "clientes")
    assert pg.locator("#nova-conta").is_hidden()
    botao = pg.locator("#clientes-tabela .botao-linha").first
    pw.expect(botao).to_contain_text("(ver a conta)")
    botao.click()
    janela = pg.locator("dialog.conta-cliente[open]")
    pw.expect(janela.locator("button:has-text('Baixar o extrato')")).to_be_visible()
    assert janela.locator("#saldo-valor, #conta-plano, button:has-text('Desativar')").count() == 0
    pg.keyboard.press("Escape")
    # pods: sem parada de emergência e sem desligar; histórico: sem desativar contas
    pg.click("#menu-painel a[data-secao=pods]")
    _secao_pronta(pg, "pods")
    pw.expect(pg.locator("#pods-tabela table")).to_contain_text("pod-abc123")
    assert pg.locator("#emergencia, #retomar, #pods-tabela button").count() == 0
    pg.click("#menu-painel a[data-secao=historico]")
    _secao_pronta(pg, "historico")
    pw.expect(pg.locator("#historico-lista .hist")).to_have_count(2)
    assert pg.locator("#historico-lista button:has-text('Desativar')").count() == 0
    # revisão: o operador aprova
    pg.click("#menu-painel a[data-secao=revisao]")
    pg.click("#pendentes [data-acao=aprovar]")
    pg.wait_for_selector("text=Nada aguardando revisão.")
    # a página nunca pediu uma rota que o papel não abre (nenhum 403 no console)
    proibidas = ("acessos", "auditoria", "seguranca", "geradores", "pagamentos")
    assert not [p for p in pedidos if p.startswith(proibidas)], pedidos
    ctx.close()
    assert erros == [], erros


def test_first_access_by_invite_qr_code_then_app_code_then_password(ambiente, navegador):  # noqa: F811
    erros: list[str] = []
    endereco = _convite(ambiente, "ana.dona", "dono")
    token = endereco.split("#convite=")[1]
    ctx = navegador.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    # mesmo com a marca de outra sessão neste navegador, o convite abre a entrada do primeiro acesso
    ctx.add_init_script("localStorage.setItem('render2go-painel-conectado', '1');")
    ctx.add_init_script(VISIVEL_NO_HTML)
    pg = _pagina(ctx, erros)
    pedidos = _pedidos_api(pg)
    pg.goto(f"{ambiente.url}{endereco}")
    html = pg.evaluate("window.__html")
    assert (html["entrada"], html["convite"], html["acessos"], html["nova"]) == (False, True, False, False)
    pg.wait_for_selector("#convite-qr:not([hidden])")
    assert pedidos == ["convite/iniciar"]  # nada de sessão nem de dados antes da senha
    pw.expect(pg.locator("#convite-quem")).to_contain_text("ana.dona")
    pw.expect(pg.locator("#convite-quem")).to_contain_text("papel de dono")
    assert pg.locator("#convite-qr").evaluate("(i) => i.complete && i.naturalWidth > 0 && i.src.startsWith('blob:')")
    assert pg.locator("#convite-senha").is_disabled()  # a senha só depois do código do aplicativo
    segredo = pg.locator("#convite-chave").text_content().replace(" ", "")
    # código errado: a mensagem e o foco no campo
    pg.fill("#convite-codigo", "000000")
    pg.click("#convite-confirmar")
    pw.expect(pg.locator("#msg-convite")).to_contain_text("não confere")
    pw.expect(pg.locator("#convite-codigo")).to_be_focused()
    pg.fill("#convite-codigo", contas.totp(segredo, int(time.time() // 30)))
    pg.click("#convite-confirmar")
    pw.expect(pg.locator("#convite-senha")).to_be_focused()
    assert pg.locator("#convite-codigo").is_disabled()
    pw.expect(pg.locator("#convite-confirmar")).to_have_text("Código confirmado")
    # senha: curta, com o nome de acesso, ou as duas diferentes: recusada com a explicação
    for senha, senha2, texto in (
        ("curta", "curta", "pelo menos 12"),
        ("ana.dona é a minha senha", "ana.dona é a minha senha", "nome de acesso"),
        (SENHA, SENHA + "x", "não são iguais"),
        ("aaaaaaaaaaaa", "aaaaaaaaaaaa", "caracteres diferentes"),  # esta, quem recusa é o servidor
    ):
        pg.fill("#convite-senha", senha)
        pg.fill("#convite-senha-2", senha2)
        pg.click("#convite-criar")
        pw.expect(pg.locator("#msg-convite")).to_contain_text(texto)
    pg.fill("#convite-senha", SENHA)
    pg.fill("#convite-senha-2", SENHA)
    pg.click("#convite-criar")
    _secao_pronta(pg, "visao-geral")
    # entrou como a pessoa nova; o convite saiu do endereço
    pw.expect(pg.locator("#t-visao-geral")).to_be_focused()
    assert token not in pg.url and pg.url.endswith(f"{PAINEL}/#visao-geral")
    pg.click("#abrir-menu")
    pw.expect(pg.locator("dialog.menu-celular[open]")).to_contain_text("ana.dona")
    pg.keyboard.press("Escape")
    ctx.close()
    # o mesmo endereço de novo (outra pessoa, ou o histórico do navegador): o convite já foi gasto
    ctx = navegador.new_context()
    pg = ctx.new_page()
    pg.goto(f"{ambiente.url}{endereco}")
    pw.expect(pg.locator("#msg-convite")).to_contain_text("já usado")
    assert pg.locator("#convite-codigo").is_disabled() and pg.locator("#convite-qr").is_hidden()
    pg.click("#convite-sair")  # "Não é o seu convite?": a entrada de sempre
    pw.expect(pg.locator("#form-admin")).to_be_visible()
    pw.expect(pg.locator("#admin-usuario")).to_be_focused()
    ctx.close()
    # endereço incompleto (copiado pela metade)
    ctx = navegador.new_context()
    pg = ctx.new_page()
    pg.goto(f"{ambiente.url}{PAINEL}/#convite=abc")
    pw.expect(pg.locator("#convite-quem")).to_have_text("Este endereço de convite está incompleto.")
    ctx.close()
    assert [e for e in erros if "status of 401" not in e and "status of 422" not in e] == [], erros


def test_a_person_logs_in_with_name_password_and_app_code_and_the_session_can_end(ambiente, navegador):  # noqa: F811
    erros: list[str] = []
    _, segredo = _pessoa(ambiente, "maria.revisora", "operador")
    ctx = navegador.new_context()
    pg = _pagina(ctx, erros)
    pg.goto(f"{ambiente.url}{PAINEL}/")
    pw.expect(pg.locator("#admin-usuario")).to_be_focused()
    pg.click("#entrar-pessoa")
    pw.expect(pg.locator("#msg-entrada")).to_have_text("Digite o seu nome de acesso.")
    pg.fill("#admin-usuario", "Maria.Revisora")  # o nome vale em maiúsculas ou minúsculas
    pg.fill("#admin-senha", SENHA)
    pg.click("#entrar-pessoa")
    pw.expect(pg.locator("#msg-entrada")).to_contain_text("6 dígitos")
    pw.expect(pg.locator("#admin-codigo")).to_be_focused()
    # código errado: a mesma mensagem para qualquer erro; a senha e o código saem dos campos
    pg.fill("#admin-codigo", "000000")
    pg.click("#entrar-pessoa")
    pw.expect(pg.locator("#msg-entrada")).to_contain_text("inválidos")
    assert pg.input_value("#admin-senha") == "" and pg.input_value("#admin-codigo") == ""
    pw.expect(pg.locator("#admin-senha")).to_be_focused()
    pg.click("#mostrar-senha")
    assert pg.get_attribute("#admin-senha", "type") == "text"
    pg.fill("#admin-senha", SENHA)
    pg.fill("#admin-codigo", contas.totp(segredo, int(time.time() // 30) + 1))
    pg.click("#entrar-pessoa")
    _secao_pronta(pg, "visao-geral")
    pw.expect(pg.locator("#t-visao-geral")).to_be_focused()
    assert pg.evaluate("localStorage.getItem('render2go-painel-papel')") == "operador"
    # o dono tira o acesso: a sessão cai, e o painel volta para a entrada com o aviso
    (pessoa,) = _api(ambiente, "GET", f"{PAINEL}/api/acessos")["pessoas"]
    _api(ambiente, "POST", f"{PAINEL}/api/acessos/{pessoa['id']}/desativar")
    pg.click("#atualizar")
    pw.expect(pg.locator("#msg-entrada")).to_have_text("Sua sessão do painel terminou. Entre de novo.")
    pw.expect(pg.locator("#form-admin")).to_be_visible()
    assert pg.evaluate("localStorage.getItem('render2go-painel-conectado')") is None
    ctx.close()
    assert [e for e in erros if "status of 401" not in e] == [], erros


def test_accesses_invites_roles_and_removal(ambiente, navegador):  # noqa: F811
    erros: list[str] = []
    cookie, _ = _pessoa(ambiente, "ana.dona", "dono")
    _pessoa(ambiente, "maria.revisora", "operador")
    ctx = navegador.new_context(permissions=["clipboard-read", "clipboard-write"])
    _com_pessoa(ctx, ambiente, cookie, "dono")
    pg = _pagina(ctx, erros)
    pg.goto(f"{ambiente.url}{PAINEL}/#acessos")
    _secao_pronta(pg, "acessos")
    pessoas = pg.locator("#acessos-pessoas tbody tr")
    pw.expect(pessoas).to_have_count(2)
    pw.expect(pessoas.filter(has_text="ana.dona")).to_contain_text("(você)")
    assert pessoas.filter(has_text="ana.dona").locator("button").count() == 0  # nada contra o próprio acesso
    # o papel novo, com confirmação
    linha = pessoas.filter(has_text="maria.revisora")
    linha.locator("select").select_option("dono")
    linha.locator("button:has-text('Mudar o papel')").click()
    pg.click("dialog.confirmacao[open] button:has-text('Mudar')")
    pw.expect(pessoas.filter(has_text="maria.revisora")).to_contain_text("Dono")
    # convite: nome inválido, depois um certo; o endereço numa janela, para copiar
    pg.fill("#convite-novo-usuario", "9 errado")
    pg.click("#convite-novo-enviar")
    pw.expect(pg.locator("#form-convite .aviso.erro")).to_contain_text("começando por uma letra")
    pg.fill("#convite-novo-usuario", "joao.operador")
    pg.select_option("#convite-novo-papel", "operador")
    pg.click("#convite-novo-enviar")
    janela = pg.locator("dialog.convite-criado[open]")
    pw.expect(janela).to_contain_text("joao.operador")
    endereco = janela.locator("#endereco-convite").inner_text()
    assert endereco.startswith(f"{ambiente.url}{PAINEL}/#convite=")
    pw.expect(pg.locator("#copiar-convite")).to_be_focused()
    pg.click("#copiar-convite")
    pw.expect(pg.locator("#copiar-convite")).to_have_text("Copiado")
    assert pg.evaluate("navigator.clipboard.readText()") == endereco
    pg.keyboard.press("Escape")
    pw.expect(pg.locator("#convite-novo-enviar")).to_be_focused()
    convites = pg.locator("#acessos-convites tbody tr")
    pw.expect(convites).to_have_count(1)
    pw.expect(convites).to_contain_text("esperando a pessoa")
    # convidar como dono pede confirmação
    pg.fill("#convite-novo-usuario", "carla.dona")
    pg.select_option("#convite-novo-papel", "dono")
    pw.expect(pg.locator("#convite-novo-papel-dica")).to_contain_text("Tudo:")
    pg.click("#convite-novo-enviar")
    pg.click("dialog.confirmacao[open] button:has-text('Cancelar')")
    pw.expect(convites).to_have_count(1)
    # cancelar o convite
    convites.first.locator("button:has-text('Cancelar')").click()
    pg.click("dialog.confirmacao[open] button:has-text('Cancelar o convite')")
    pw.expect(pg.locator("#acessos-convites")).to_contain_text("Nenhum convite esperando.")
    # tirar o acesso: a pessoa fica na lista, sem acesso
    pessoas.filter(has_text="maria.revisora").locator("button:has-text('Tirar o acesso')").click()
    pg.click("dialog.confirmacao[open] button:has-text('Tirar o acesso')")
    pw.expect(pessoas.filter(has_text="maria.revisora")).to_contain_text("sem acesso desde")
    pw.expect(pessoas.filter(has_text="maria.revisora").locator("button")).to_have_text(["Convidar de novo"])
    # tudo ficou na auditoria, com quem fez
    pg.click("#menu-painel a[data-secao=auditoria]")
    lista = pg.locator("#auditoria-lista")
    for texto in ("Mudou um papel", "papel: operador → dono", "Criou um convite", "Cancelou um", "Tirou um acesso"):
        pw.expect(lista).to_contain_text(texto)
    pw.expect(lista.locator("li").first).to_contain_text("ana.dona")
    ctx.close()
    assert [e for e in erros if "status of 422" not in e] == [], erros


def test_audit_filters_next_page_and_spreadsheet(ambiente, navegador, tmp_path):  # noqa: F811
    erros: list[str] = []
    _auditar_muito(ambiente, 130)
    ctx = navegador.new_context(viewport={"width": 1280, "height": 800})
    _com_sessao(ctx, ambiente, admin=True)
    pg = _pagina(ctx, erros)
    pg.goto(f"{ambiente.url}{PAINEL}/#auditoria")
    _secao_pronta(pg, "auditoria")
    itens = pg.locator("#auditoria-lista > li")
    pw.expect(itens).to_have_count(100)
    pw.expect(pg.locator("#auditoria-resumo")).to_contain_text("há mais")
    pw.expect(itens.filter(has_text="cli_0129")).to_contain_text("saldo: R$ 1,29 → R$ 6,29")
    pw.expect(itens.filter(has_text="cli_0129")).to_contain_text("Chrome no Linux")
    # a página seguinte entra embaixo; no fim, o botão sai
    pg.click("#auditoria-mais")
    pw.expect(itens).to_have_count(131)  # as 130 e a entrada da chave do servidor (a sessão do teste)
    pw.expect(pg.locator("#auditoria-mais")).to_be_hidden()
    # filtros: as pessoas e as ações que já aparecem; o resultado substitui a lista
    assert pg.locator("#aud-pessoa option").all_inner_texts()[:1] == ["Todas"]
    assert "ana.dona" in pg.locator("#aud-pessoa option").all_inner_texts()
    pg.select_option("#aud-acao", "plano")
    pg.select_option("#aud-pessoa", "ana.dona")
    pg.click("#aud-filtrar")
    pw.expect(itens).to_have_count(22)  # plano (pares) de ana.dona (múltiplos de 3): 0, 6, 12... 126
    pw.expect(itens.first).to_contain_text("plano: teste → pago")
    href = pg.get_attribute("#aud-csv", "href")
    assert "acao=plano" in href and "pessoa=ana.dona" in href and re.search(r"fuso_min=-?\d+", href)
    with pg.expect_download() as baixado:
        pg.click("#aud-csv")
    texto = Path(baixado.value.path()).read_text(encoding="utf-8-sig")
    linhas = list(csv.reader(io.StringIO(texto), delimiter=";"))
    assert linhas[0] == ["Data", "Pessoa", "Ação", "Alvo", "Antes", "Depois", "Detalhe", "IP", "Navegador"]
    assert len(linhas) == 23 and {r[1] for r in linhas[1:]} == {"ana.dona"}
    assert json.loads(linhas[1][4]) == {"plano": "teste"}
    # período: um dia sem nada; depois, limpar os filtros
    pg.fill("#aud-desde", "2020-01-01")
    pg.fill("#aud-ate", "2020-01-02")
    pg.click("#aud-filtrar")
    pw.expect(pg.locator("#auditoria-lista")).to_contain_text("Nenhuma ação com estes filtros.")
    pg.click("#aud-limpar")
    pw.expect(itens).to_have_count(100)
    assert pg.input_value("#aud-acao") == ""
    assert pg.get_attribute("#aud-csv", "href").startswith("api/auditoria.csv?fuso_min=")
    ctx.close()
    assert erros == [], erros


def test_panel_sections_do_not_shift_when_the_api_answers_late(ambiente, navegador):  # noqa: F811
    """Core Web Vitals (CLS): com a API lenta, cada seção pinta o esqueleto e troca tudo de uma vez quando os dados
    chegam; nada do que já estava na tela (topo, menu, título, filtros) é empurrado. O mesmo no convite."""
    tok = _cliente(ambiente)
    _lote(ambiente, tok, {"bolo.png": _png((40, 90, 160), "circulo")}, pronto=True)
    _lote(ambiente, tok, {"loja.png": _png((200, 120, 60), "barras")})
    _auditar_muito(ambiente, 20)

    def atrasar(rota):
        time.sleep(0.8)  # a 1ª pintura sai antes da resposta
        rota.continue_()

    for largura, altura, movel in ((390, 844, True), (1280, 820, False)):
        for secao in ("visao-geral", "revisao", "clientes", "pods", "auditoria", "acessos"):
            ctx = navegador.new_context(viewport={"width": largura, "height": altura}, is_mobile=movel, has_touch=movel)
            _com_sessao(ctx, ambiente, admin=True)
            ctx.add_init_script(f"document.addEventListener('DOMContentLoaded', () => ({CLS})());")
            pg = ctx.new_page()
            pg.route(f"**{PAINEL}/api/**", atrasar)
            pg.goto(f"{ambiente.url}{PAINEL}/#{secao}")
            _secao_pronta(pg, secao)
            pg.unroute(f"**{PAINEL}/api/**")
            pg.wait_for_timeout(300)
            cls = pg.evaluate("window.__cls")
            assert cls < 0.001, (largura, secao, cls, pg.evaluate("window.__deslocados"))
            ctx.close()
    # o convite: o QR Code e o texto de quem convidou chegam depois, nos lugares já reservados
    endereco = _convite(ambiente, "ana.dona", "dono")
    ctx = navegador.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    ctx.add_init_script(f"document.addEventListener('DOMContentLoaded', () => ({CLS})());")
    pg = ctx.new_page()
    pg.route(f"**{PAINEL}/api/**", atrasar)
    pg.goto(f"{ambiente.url}{endereco}")
    pg.wait_for_selector("#convite-qr:not([hidden])")
    pg.wait_for_timeout(300)
    assert pg.evaluate("window.__cls") < 0.001, pg.evaluate("window.__deslocados")
    ctx.close()


def test_deliveries_review_criteria_with_evidence_and_owner_decides_corrections(ambiente, navegador):  # noqa: F811
    """Entregas (17/10/2026): o vídeo entregue com os critérios do pedido; a decisão de cada um pede a evidência e
    muda a situação; o pedido de correção do cliente, decidido pelo dono com o crédito; o operador não decide."""
    erros: list[str] = []
    tok = _cliente(ambiente)
    lid = _lote(ambiente, tok, {"pote.png": _png((40, 90, 160), "circulo")}, pronto=True, prompt="o pote gira devagar")
    (job,) = [j for j in ambiente.banco.jobs(lid) if j["tipo"] == "render"]
    cid = ambiente.banco.lote(lid)["cliente_id"]
    ambiente.banco.registrar_retorno(job["id"], cid, False, "defeito_visual", None, "quadro estranho no fim")
    ambiente.banco.pedir_correcao(job["id"], cid, 3600)
    ctx = navegador.new_context(viewport={"width": 1280, "height": 800})
    _com_sessao(ctx, ambiente, admin=True)
    pg = _pagina(ctx, erros)
    pg.goto(f"{ambiente.url}{PAINEL}/#entregas")
    _secao_pronta(pg, "entregas")
    pw.expect(pg.locator("#entregas-correcoes")).to_contain_text("Defeito visual")
    pedido = pg.locator(".entrega-pedido", has_text="o pote gira devagar")
    pw.expect(pedido).to_contain_text("Ainda não verificado")
    pedido.locator(f"[data-foco='{job['id']}:revisar']").click()
    janela = pg.locator("dialog.entrega-video[open]")
    pw.expect(janela).to_contain_text("Mostra o que você pediu")
    item = janela.locator(".criterios-video li", has_text="Mostra o que você pediu")
    item.locator("button", has_text="Salvar").click()
    pw.expect(item).to_contain_text("a decisão precisa da evidência")
    item.locator("select").select_option("nao_atendido")
    item.locator("input").fill("o pote não gira, só a luz passa")
    item.locator("button", has_text="Salvar").click()
    pw.expect(janela.locator(".situacao-video")).to_contain_text("Não atende ao pedido")
    assert _axe(pg) == []
    janela.locator(".dialogo-cab button").click()
    pw.expect(pedido).to_contain_text("Não atende ao pedido")
    # a correção: o dono decide, com o crédito do valor cobrado do vídeo
    pg.locator("[data-foco$=':decidir']").click()
    decisao = pg.locator("dialog.entrega-correcao[open]")
    decisao.locator("select").select_option("falha_tecnica")
    decisao.locator("textarea").fill("quadro trocado no fim do vídeo")
    decisao.locator("input[type=checkbox]").check()
    decisao.locator("button", has_text="Salvar a decisão").click()
    pw.expect(decisao).to_have_count(0)
    pw.expect(pg.locator("#entregas-correcoes")).to_contain_text("crédito")
    with ambiente.banco.tx() as c:
        assert c.one("SELECT COUNT(*) AS n FROM movimentos WHERE tipo = 'credito'")["n"] == 1
        assert c.one("SELECT COUNT(*) AS n FROM auditoria_admin WHERE acao = 'criterio'")["n"] == 1
    ctx.close()
    # o operador revisa os vídeos, mas não decide correções (o crédito é do dono)
    tok2 = _cliente(ambiente, saldo="50")
    lid2 = _lote(ambiente, tok2, {"vaso.png": _png((200, 120, 60), "barras")}, pronto=True)
    (job2,) = [j for j in ambiente.banco.jobs(lid2) if j["tipo"] == "render"]
    cid2 = ambiente.banco.lote(lid2)["cliente_id"]
    ambiente.banco.registrar_retorno(job2["id"], cid2, False, "defeito_visual")
    ambiente.banco.pedir_correcao(job2["id"], cid2, 3600)
    cookie, _ = _pessoa(ambiente, "maria.revisora", "operador")
    ctx = navegador.new_context(viewport={"width": 1280, "height": 800})
    _com_pessoa(ctx, ambiente, cookie, "operador")
    pg = _pagina(ctx, erros)
    pg.goto(f"{ambiente.url}{PAINEL}/#entregas")
    _secao_pronta(pg, "entregas")
    pw.expect(pg.locator("#entregas-correcoes")).to_contain_text("Aberta")
    assert pg.locator("[data-foco$=':decidir']").count() == 0
    ctx.close()
    assert erros == [], erros
