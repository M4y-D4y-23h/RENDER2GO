"""Páginas no navegador (Chromium headless): fluxo completo, sem erros no console nem bloqueios
da política de segurança (CSP). Precisa do pacote playwright e de um Chromium; senão, pula."""

import json
import os
import re
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")
from test_fila import ADMIN, _png, ambiente  # noqa: E402,F401 (mesma API local e armazenamento)

CHROMIUM = next(iter(sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))), None)


@pytest.fixture
def navegador():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=str(CHROMIUM) if CHROMIUM else None)
        yield b
        b.close()


def _pagina(navegador, erros):
    pg = navegador.new_page()
    pg.on("console", lambda m: m.type == "error" and erros.append(m.text))
    pg.on("pageerror", lambda e: erros.append(str(e)))
    return pg


def _analisar(dados: bytes) -> dict:
    """Detector falso e determinístico: a foto de fundo azul "tem" uma pessoa."""
    from io import BytesIO

    from PIL import Image

    azul = Image.open(BytesIO(dados)).convert("RGB").getpixel((0, 0)) == (40, 90, 160)
    return {"versao": "pessoas-v1", "pessoas": int(azul), "rostos": int(azul), "maior": 0.2}


def test_client_and_reviewer_pages_end_to_end(ambiente, navegador, tmp_path):  # noqa: F811 (fixture)
    erros: list[str] = []
    ambiente.cfg.analisar = _analisar
    # aprovador cadastra um cliente de teste gratuito
    adm = _pagina(navegador, erros)
    adm.goto(f"{ambiente.url}/aprovar")
    adm.fill("#token", ADMIN)
    adm.click("#form-token button[type=submit]")
    adm.fill("#cliente-nome", "Padaria do Bairro")
    adm.select_option("#cliente-plano", "teste")
    adm.click("#form-cliente button[type=submit]")
    adm.wait_for_selector("#token-novo:not([hidden])")
    assert "envie ao cliente por um canal seguro" in adm.locator("#token-novo").inner_text()
    token = adm.locator("#token-valor").inner_text().strip()
    assert re.fullmatch(r"[A-Za-z0-9_-]{20,}", token)

    # cliente envia 2 fotos
    for n, (cor, forma) in {"bolo.png": ((40, 90, 160), "circulo"), "loja.png": ((200, 120, 60), "barras")}.items():
        (tmp_path / n).write_bytes(_png(cor, forma))
    cli = _pagina(navegador, erros)
    cli.goto(ambiente.url)
    # navegador novo: aviso de cookies; "Gerenciar" mostra as categorias, e salvar guarda só os necessários
    cli.wait_for_selector("#aviso-cookies")
    assert adm.locator("#aviso-cookies").count() == 0  # o /aprovar (só o dono) não mostra o aviso
    cli.click("#aviso-cookies button:has-text('Gerenciar')")
    assert cli.locator("#ck-necessarios").is_checked() and cli.locator("#ck-necessarios").is_disabled()
    assert not cli.locator("#ck-estatisticas").is_checked()
    cli.click("#aviso-cookies button:has-text('Salvar escolhas')")
    cli.wait_for_selector("#aviso-cookies", state="detached")
    with ambiente.banco.tx() as c:
        (ck,) = c.all("SELECT escolha FROM cookies")
    assert json.loads(ck["escolha"]) == {"necessarios": True, "estatisticas": False, "marketing": False}
    cli.fill("#token", token)
    cli.click("#form-token button[type=submit]")
    cli.wait_for_selector("#receita option", state="attached")
    # teste gratuito: só 480p e 3 s, e o aviso das regras
    assert cli.locator("#qualidade option").all_inner_texts() == ["Rápida (prévia, 480p)"]
    assert cli.locator("#duracao option").all_inner_texts() == ["3 s"]
    assert "envie todas juntas, em um único pedido" in cli.locator("#plano-aviso").inner_text()
    cli.set_input_files("#fotos", [str(tmp_path / "bolo.png"), str(tmp_path / "loja.png")])
    assert "2 de 5 fotos escolhidas · ainda cabem 3" in cli.locator("#contagem").inner_text()
    assert cli.locator("#saldo").is_hidden() and cli.locator("#custo").is_hidden()  # o teste não paga
    assert cli.locator("#enviar").inner_text() == "Enviar pedido com 2 fotos"
    # prévia das fotos escolhidas, desenhada no próprio navegador
    pw.expect(cli.locator("#previa .miniatura :is(canvas, img)")).to_have_count(2)
    # modelo de geração visível; autorização sem pedir nome (o registro usa conta, origem e navegador)
    assert cli.locator("#modelo option").all_inner_texts() == ["Wan 2.2 · imagem para vídeo · 14B"]
    assert cli.locator("#declarante").count() == 0
    cli.check("#consentimento")
    if os.environ.get("RENDER2GO_CAPTURAS"):
        cli.screenshot(path=os.path.join(os.environ["RENDER2GO_CAPTURAS"], "cliente-teste.png"), full_page=True)
    cli.fill("#prompt", "a câmera se aproxima devagar do produto")
    cli.select_option("#duracao", "3")
    cli.click("#enviar")
    cli.wait_for_selector("#msg.ok", timeout=15000)
    cli.wait_for_selector("text=Em análise")
    assert "Ele começa em até" in cli.locator("#lotes .previsao").inner_text()
    assert sum(k.endswith(".png") for k in ambiente.objetos) == 2  # direto no armazenamento
    cli.wait_for_selector("#plano-aviso.erro")  # teste usado: o formulário trava
    assert cli.locator("#enviar").is_disabled()

    # aprovador vê as miniaturas, o aviso de pessoas por foto e o resumo do lote, e aprova
    ambiente.cfg.analises.esperar()
    adm.click("#atualizar")
    adm.wait_for_selector(".grade img")
    assert adm.locator(".grade img").count() == 2
    assert adm.locator(".selo.pessoas").count() == 1
    assert "sem pessoas detectadas" in adm.locator(".grade").inner_text()
    assert "Autorização marcada em" in adm.locator("#pendentes .autorizacao").inner_text()
    assert "marcou ter autorização" in adm.locator("#pendentes .aviso.alerta").inner_text()
    if os.environ.get("RENDER2GO_CAPTURAS"):
        adm.screenshot(path=os.path.join(os.environ["RENDER2GO_CAPTURAS"], "aprovar.png"), full_page=True)
    adm.click("#pendentes [data-acao=aprovar]")
    adm.wait_for_selector("text=Nada aguardando revisão.")
    # histórico: o pedido aprovado, com cliente e IP; e o uso por IP
    hist = adm.locator("#historico .hist").first
    assert "Padaria do Bairro" in hist.inner_text() and "IP 127.0.0.1" in hist.inner_text()
    assert "Na fila" in hist.inner_text()
    assert "127.0.0.1" in adm.locator("#ips table").inner_text()
    assert adm.locator("#dispositivos table tbody tr").count() == 1  # o navegador do cliente
    with adm.expect_download() as baixado:
        adm.click("#historico .hist button:has-text('auditoria')")
    registro = json.loads(Path(baixado.value.path()).read_text())
    aut = registro["autorizacao"]
    assert aut["dispositivo"] and aut["sinais"]["marcado_em"] and aut["sinais"]["impressao"]
    adm.click("#filtros-historico button[data-grupo=recusados]")
    adm.wait_for_selector("text=Nenhum pedido neste filtro.")
    adm.click("#filtros-historico button[data-grupo=todos]")
    adm.wait_for_selector("#historico .hist")

    # o worker termina (simulado no banco) e o cliente vê os links
    (lote,) = ambiente.banco.lotes()
    for j in ambiente.banco.jobs(lote["id"]):
        if j["tipo"] == "render":
            ambiente.objetos[f"saida/{lote['cliente_id']}/{lote['id']}/{j['arquivo']}"] = b"mp4"
    with ambiente.banco.tx() as c:
        c.run("UPDATE jobs SET status = 'ok'")
        c.run("UPDATE lotes SET status = 'concluido'")
    cli.reload()
    cli.wait_for_selector("#lotes .selo.estado-concluido:has-text('Pronto')")
    assert cli.locator(".videos button.assistir").count() == 2  # assiste na própria página
    assert cli.locator(".videos a.baixar").count() == 2  # e salva como arquivo
    cli.locator(".videos button.assistir").first.click()
    player = cli.locator("dialog[open]")
    assert "/saida/" in player.locator("video").get_attribute("src")
    assert player.locator("a[target=_blank]").count() == 1  # abrir em outra aba
    cli.keyboard.press("Escape")
    cli.wait_for_selector("dialog[open]", state="detached")
    assert cli.evaluate("document.activeElement.classList.contains('assistir')")  # o foco volta
    assert cli.locator("#aviso-cookies").count() == 0  # a escolha ficou guardada
    # suporte: um link para a página /suporte; conectado, a mensagem vai ligada à conta
    assert cli.locator("#link-suporte").inner_text().endswith("/suporte")
    cli.click("#link-suporte")
    cli.wait_for_url("**/suporte")
    assert "conectado" in cli.locator("#suporte-conta").inner_text()
    cli.fill("#suporte-mensagem", "Preciso de ajuda com o meu pedido de teste.")
    cli.click("#suporte-enviar")
    cli.wait_for_selector("#suporte-msg.ok")
    adm.click("#atualizar")
    adm.wait_for_selector("#suporte .msg-suporte")
    assert "Preciso de ajuda" in adm.locator("#suporte").inner_text()
    if os.environ.get("RENDER2GO_CAPTURAS"):
        cli.screenshot(path=os.path.join(os.environ["RENDER2GO_CAPTURAS"], "cliente.png"), full_page=True)
    assert erros == []


def test_support_page_without_access_code_and_cookie_banner(ambiente, navegador):  # noqa: F811 (fixture)
    erros: list[str] = []
    pg = _pagina(navegador, erros)
    pg.goto(f"{ambiente.url}/suporte")
    pg.wait_for_selector("#aviso-cookies")
    if os.environ.get("RENDER2GO_CAPTURAS"):
        pg.screenshot(path=os.path.join(os.environ["RENDER2GO_CAPTURAS"], "suporte-cookies.png"), full_page=True)
    pg.click("#aviso-cookies button:has-text('Aceitar todos')")
    pg.wait_for_selector("#aviso-cookies", state="detached")
    assert pg.locator("#suporte-email").get_attribute("required") is not None  # sem código: e-mail obrigatório
    pg.fill("#suporte-mensagem", "Perdi o meu código de acesso, como faço?")
    pg.fill("#suporte-email", "dona@loja.com.br")
    pg.click("#suporte-enviar")
    pg.wait_for_selector("#suporte-msg.ok")
    (m,) = ambiente.banco.suporte_recente()
    assert m["cliente_id"] == "" and m["email"] == "dona@loja.com.br"
    # "Preferências de cookies" reabre o aviso com a escolha atual
    pg.click("#preferencias-cookies")
    assert pg.locator("#ck-estatisticas").is_checked() and pg.locator("#ck-marketing").is_checked()
    assert erros == []


# -- acessibilidade, celular, teclado e sessão (docs/10) ------------------------------------------

AXE = Path(__file__).resolve().parents[1] / "web" / "node_modules" / "axe-core" / "axe.min.js"
REGRAS_AXE = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]


def _api(amb, metodo, caminho, token=ADMIN, **kw):
    import requests

    r = requests.request(metodo, f"{amb.url}{caminho}", headers={"Authorization": f"Bearer {token}"}, timeout=10, **kw)
    assert r.status_code == 200, r.text
    return r.json()


def _cliente(amb, plano="pago", saldo="100"):
    r = _api(amb, "POST", "/api/admin/clientes", json={"nome": "Café da Esquina", "plano": plano})
    if plano == "pago" and saldo:  # pré-pago: recarga antes de pedir
        _api(amb, "POST", f"/api/admin/clientes/{r['cliente']}/saldo", json={"valor": saldo, "descricao": "Pix"})
    return r["token"]


def _lote(amb, token, fotos: dict[str, bytes], aprovar=False, pronto=False):
    import requests

    corpo = {  # sem duração: 3 s no teste gratuito; a do estilo (5 s) no pré-pago
        "prompt": "vapor sobe da xícara e a câmera se aproxima devagar",
        "fotos": [{"nome": n, "tipo": "image/png", "bytes": len(b)} for n, b in fotos.items()],
    }
    r = _api(amb, "POST", "/api/lotes", token, json=corpo)
    for e in r["envios"]:
        requests.put(e["url"], data=fotos[e["original"]], timeout=10)
    _api(amb, "POST", f"/api/lotes/{r['lote']}/enviado", token)
    if aprovar or pronto:
        _api(amb, "POST", f"/api/admin/lotes/{r['lote']}/aprovar")
    if pronto:
        lote = amb.banco.lote(r["lote"])
        for j in amb.banco.jobs(r["lote"]):
            if j["tipo"] == "render":
                amb.objetos[f"saida/{lote['cliente_id']}/{lote['id']}/{j['arquivo']}"] = b"mp4"
        with amb.banco.tx() as c:
            c.run("UPDATE jobs SET status = 'ok' WHERE lote_id = ?", (r["lote"],))
            c.run("UPDATE lotes SET status = 'concluido' WHERE id = ?", (r["lote"],))
    return r["lote"]


def _com_codigo(contexto, chave, token):
    """Navegador que já tem o código guardado (e a escolha de cookies feita)."""
    escolha = json.dumps({"versao": "1", "estatisticas": False, "marketing": False, "quando": "2026-10-08T00:00:00Z"})
    contexto.add_init_script(
        f"localStorage.setItem({json.dumps(chave)}, {json.dumps(token)});"
        f"localStorage.setItem('render2go-cookies', {json.dumps(escolha)});"
    )


def _axe(pg) -> list[dict]:
    if not pg.evaluate("typeof window.axe !== 'undefined'"):
        pg.evaluate(AXE.read_text())
    return pg.evaluate(
        """async (regras) => (await axe.run(document, {runOnly: {type: 'tag', values: regras},
            resultTypes: ['violations']})).violations.map((v) => ({id: v.id, impacto: v.impact,
            alvos: v.nodes.slice(0, 4).map((n) => n.target.join(' '))}))""",
        REGRAS_AXE,
    )


def _capturar(pg, nome):
    if os.environ.get("RENDER2GO_CAPTURAS"):
        pg.screenshot(path=os.path.join(os.environ["RENDER2GO_CAPTURAS"], f"{nome}.png"), full_page=True)


@pytest.mark.skipif(not AXE.exists(), reason="axe-core ausente (cd servico/web && npm ci)")
@pytest.mark.parametrize("tema", ["light", "dark"])
def test_pages_have_no_accessibility_violations(ambiente, navegador, tmp_path, tema):  # noqa: F811 (fixture)
    ambiente.cfg.analisar = _analisar
    fotos = {"bolo.png": _png((40, 90, 160), "circulo"), "loja.png": _png((200, 120, 60), "barras")}
    tok_pronto = _cliente(ambiente)
    _lote(ambiente, tok_pronto, fotos, pronto=True)
    tok_espera = _cliente(ambiente, "teste")
    _lote(ambiente, tok_espera, {"vitrine.png": fotos["bolo.png"]})
    ambiente.cfg.analises.esperar()
    violacoes: dict[str, list] = {}

    def conferir(pg, estado):
        _capturar(pg, f"axe-{estado}-{tema}")
        if v := _axe(pg):
            violacoes[estado] = v

    # páginas abertas por quem ainda não tem código (com o aviso de cookies)
    anonimo = navegador.new_context(color_scheme=tema, reduced_motion="reduce", bypass_csp=True)
    pg = anonimo.new_page()
    paginas = [("/", "entrada"), ("/suporte", "suporte"), ("/aprovar", "aprovar-entrada"), ("/nada", "404")]
    for caminho, estado in paginas:
        pg.goto(f"{ambiente.url}{caminho}")
        pg.wait_for_load_state("networkidle")
        conferir(pg, estado)
    anonimo.close()

    # cliente com vídeos prontos, com o player aberto, e o aprovador com um pedido pendente
    cli = navegador.new_context(color_scheme=tema, reduced_motion="reduce", bypass_csp=True)
    _com_codigo(cli, "render2go-token-cliente", tok_pronto)
    pg = cli.new_page()
    pg.goto(ambiente.url)
    pg.wait_for_selector(".videos button.assistir")
    pg.set_input_files("#fotos", [str(_salvar(tmp_path, "a.png", fotos["bolo.png"]))])
    pw.expect(pg.locator("#previa .miniatura :is(canvas, img)")).to_have_count(1)
    conferir(pg, "painel")
    pg.locator(".videos button.assistir").first.click()
    pg.wait_for_selector("dialog[open]")
    conferir(pg, "player")
    cli.close()
    adm = navegador.new_context(color_scheme=tema, reduced_motion="reduce", bypass_csp=True)
    _com_codigo(adm, "render2go-token-admin", ADMIN)
    pg = adm.new_page()
    pg.goto(f"{ambiente.url}/aprovar")
    pg.wait_for_selector("#pendentes .selo.pessoas")
    pg.wait_for_selector("#ips table")
    conferir(pg, "aprovar-painel")
    adm.close()
    assert violacoes == {}


def _salvar(pasta, nome, dados):
    (pasta / nome).write_bytes(dados)
    return pasta / nome


@pytest.mark.parametrize("largura", [320, 390])
def test_mobile_layout_has_no_side_scroll_and_large_touch_targets(ambiente, navegador, tmp_path, largura):  # noqa: F811
    erros: list[str] = []
    tok = _cliente(ambiente)
    _lote(ambiente, tok, {"bolo.png": _png((40, 90, 160), "circulo")}, pronto=True)
    _lote(ambiente, tok, {"loja.png": _png((200, 120, 60), "barras")})
    celular = dict(viewport={"width": largura, "height": 740}, is_mobile=True, has_touch=True, device_scale_factor=2)
    for chave, token, caminho, estado in [
        (None, None, "/", "entrada"),
        (None, None, "/suporte", "suporte"),
        ("render2go-token-cliente", tok, "/", "painel"),
        ("render2go-token-admin", ADMIN, "/aprovar", "aprovar"),
    ]:
        ctx = navegador.new_context(**celular)
        if chave:
            _com_codigo(ctx, chave, token)
        pg = ctx.new_page()
        pg.on("console", lambda m: m.type == "error" and erros.append(m.text))
        pg.on("pageerror", lambda e: erros.append(str(e)))
        pg.goto(f"{ambiente.url}{caminho}")
        pg.wait_for_load_state("networkidle")
        if estado == "painel":
            pg.set_input_files("#fotos", [str(_salvar(tmp_path, "c.png", _png((90, 160, 40), "circulo")))])
            pw.expect(pg.locator("#previa .miniatura :is(canvas, img)")).to_have_count(1)
        largura_pagina = pg.evaluate("document.documentElement.scrollWidth")
        assert largura_pagina <= largura, (estado, largura_pagina)
        # com uma fonte larga (a padrão de muitos Linux e de alguns Android), também sem rolagem lateral
        pg.evaluate(
            "() => { const r = document.documentElement.style; for (const v of ['--fonte-texto', '--fonte-titulos'])"
            " r.setProperty(v, '\"DejaVu Sans\", Verdana, sans-serif'); }"
        )
        largura_pagina = pg.evaluate("document.documentElement.scrollWidth")
        assert largura_pagina <= largura, (estado, "fonte larga", largura_pagina)
        # alvos de toque: botões, campos e a navegação com pelo menos 44 px de altura (WCAG 2.5.5)
        alvos = ".navegacao :is(a, button), .botao, .filtros button, .secoes a, select, textarea, input"
        pequenos = pg.evaluate(
            """(alvos) => [...document.querySelectorAll(alvos)]
                .filter((e) => e.offsetParent !== null && !['file', 'checkbox'].includes(e.type))
                .map((e) => [e.id || e.className || e.tagName, Math.round(e.getBoundingClientRect().height)])
                .filter(([, h]) => h < 44)""",
            alvos,
        )
        assert pequenos == [], (estado, pequenos)
        _capturar(pg, f"celular-{largura}-{estado}")
        ctx.close()
    assert erros == []


def test_keyboard_skip_link_and_cookie_banner(ambiente, navegador):  # noqa: F811 (fixture)
    pg = navegador.new_page()
    pg.goto(ambiente.url)
    pg.wait_for_selector("#aviso-cookies")
    pg.keyboard.press("Tab")
    assert pg.evaluate("document.activeElement.textContent") == "Pular para o conteúdo"
    pg.keyboard.press("Tab")  # o aviso de cookies vem logo depois, embora apareça no rodapé da tela
    assert pg.evaluate("document.activeElement.closest('#aviso-cookies') !== null")
    pg.keyboard.press("Shift+Tab")
    pg.keyboard.press("Enter")
    assert pg.evaluate("document.activeElement.id") == "conteudo"
    pg.click("#aviso-cookies button:has-text('Só os necessários')")
    pg.wait_for_selector("#aviso-cookies", state="detached")
    # "Preferências de cookies" abre o aviso com o foco nele; Esc fecha e devolve o foco ao link
    pg.click("#preferencias-cookies")
    assert pg.evaluate("document.activeElement.id") == "titulo-cookies"
    pg.keyboard.press("Escape")
    pg.wait_for_selector("#aviso-cookies", state="detached")
    assert pg.evaluate("document.activeElement.id") == "preferencias-cookies"


def test_saved_code_opens_the_panel_directly_and_a_revoked_code_returns_to_login(ambiente, navegador):  # noqa: F811
    tok = _cliente(ambiente)
    ctx = navegador.new_context()
    _com_codigo(ctx, "render2go-token-cliente", tok)
    # o <script> do <head> marca a sessão antes do primeiro quadro: o formulário de entrada nunca aparece
    ctx.add_init_script(
        "document.addEventListener('DOMContentLoaded', () => { window.__entrada = "
        "document.getElementById('form-token').checkVisibility(); window.__sessao = "
        "document.documentElement.classList.contains('com-sessao'); });"
    )
    pg = ctx.new_page()
    pg.goto(ambiente.url)
    pg.wait_for_selector("#receita option", state="attached")
    assert pg.evaluate("[window.__entrada, window.__sessao]") == [False, True]
    assert pg.locator("#painel").is_visible() and not pg.locator("#form-token").is_visible()
    # código desativado: volta ao formulário com a explicação, e o código guardado é apagado
    with ambiente.banco.tx() as c:
        (cliente,) = c.all("SELECT id FROM clientes")
    _api(ambiente, "POST", f"/api/admin/clientes/{cliente['id']}/desativar")
    pg.reload()
    pg.wait_for_selector("#msg-entrada:not([hidden])")
    assert "inválido ou desativado" in pg.locator("#msg-entrada").inner_text()
    assert pg.locator("#form-token").is_visible()
    assert pg.evaluate("localStorage.getItem('render2go-token-cliente')") is None
    assert pg.evaluate("document.activeElement.id") == "token"
    ctx.close()


def test_photo_preview_removal_suggestion_counter_and_required_authorization(ambiente, navegador, tmp_path):  # noqa: F811
    erros: list[str] = []
    tok = _cliente(ambiente)
    ctx = navegador.new_context()
    _com_codigo(ctx, "render2go-token-cliente", tok)
    pg = _pagina(ctx, erros)
    pg.goto(ambiente.url)
    pg.wait_for_selector("#receita option", state="attached")
    arquivos = [_salvar(tmp_path, f"foto-{i}.png", _png((40 * i, 90, 160), "circulo")) for i in range(1, 4)]
    pg.set_input_files("#fotos", [str(a) for a in arquivos])
    pw.expect(pg.locator("#previa .miniatura :is(canvas, img)")).to_have_count(3)
    assert pg.locator("#contagem").inner_text().startswith("3 fotos escolhidas")
    pg.click("#previa .miniatura[aria-label='foto-1.png'] .remover")
    pw.expect(pg.locator("#previa .miniatura")).to_have_count(2)
    assert pg.evaluate("document.activeElement.classList.contains('remover')")  # o foco não se perde
    assert pg.locator("#enviar").inner_text() == "Enviar pedido com 2 fotos"
    pg.click("#limpar")
    assert pg.locator("#previa").is_hidden() and pg.locator("#enviar").inner_text() == "Enviar pedido"
    # sugestão do estilo e contador de caracteres
    pg.select_option("#receita", "comida-prato")
    assert pg.locator("#sugestao-texto").inner_text() == "vapor sobe do prato quente, a câmera se aproxima devagar"
    pg.click("#usar-sugestao")
    assert pg.input_value("#prompt") == "vapor sobe do prato quente, a câmera se aproxima devagar"
    assert pg.locator("#prompt-contador").inner_text() == "56/600"
    # sem fotos, o envio avisa e leva o foco para a escolha das fotos
    pg.click("#enviar")
    assert "Escolha as fotos" in pg.locator("#msg").inner_text()
    assert pg.evaluate("document.activeElement.id") == "fotos"
    # estilo com pessoas: a autorização é obrigatória antes de enviar
    pg.set_input_files("#fotos", [str(arquivos[0])])
    pg.select_option("#receita", "pessoa-apresentacao")
    assert "exigida" in pg.get_attribute("#caixa-consentimento", "class")
    pg.click("#enviar")
    assert "marque a autorização" in pg.locator("#msg").inner_text()
    assert pg.evaluate("document.activeElement.id") == "consentimento"
    assert pg.get_attribute("#consentimento", "aria-invalid") == "true"
    assert ambiente.banco.lotes() == []  # nada foi criado
    ctx.close()
    assert erros == []


def test_saved_code_fetches_the_panel_data_early_and_only_once(ambiente, navegador):  # noqa: F811 (fixture)
    erros: list[str] = []
    tok = _cliente(ambiente)
    disp = "6f1c2a3b-4d5e-4f60-8a71-92b3c4d5e6f7"
    ctx = navegador.new_context()
    _com_codigo(ctx, "render2go-token-cliente", tok)
    ctx.add_init_script(f"localStorage.setItem('render2go-dispositivo', '{disp}')")
    pedidos: list[tuple[str, str | None]] = []
    pg = _pagina(ctx, erros)
    pg.on(
        "request",
        lambda r: "/api/" in r.url and pedidos.append((r.url.split("/api/")[1], r.headers.get("x-dispositivo"))),
    )
    pg.goto(ambiente.url)
    pg.wait_for_selector("#receita option", state="attached")
    pg.wait_for_selector("#lotes .vazio")
    # o <script> do <head> já pediu receitas e lotes; a página usou essas respostas, sem pedir de novo
    assert sorted(pedidos) == [("lotes", disp), ("receitas", disp)]
    ctx.close()
    assert erros == []


def _texto(pg, seletor):
    """Texto visível sem os espaços não separáveis que o navegador põe nos valores em reais."""
    return pg.locator(seletor).inner_text().replace("\u00a0", " ")


def test_prepaid_balance_price_and_top_up_by_the_reviewer(ambiente, navegador, tmp_path):  # noqa: F811 (fixture)
    erros: list[str] = []
    tok = _cliente(ambiente, saldo="3,00")
    ctx = navegador.new_context()
    _com_codigo(ctx, "render2go-token-cliente", tok)
    pg = _pagina(ctx, erros)
    pg.goto(ambiente.url)
    pg.wait_for_selector("#saldo:not([hidden])")
    assert _texto(pg, "#saldo-valor") == "R$ 3,00"
    precos = _texto(pg, "#saldo-precos")
    assert precos == "480p: R$ 0,25 por segundo de vídeo · 720p: R$ 0,50 por segundo de vídeo"
    # 3 s é só do teste gratuito; o padrão do estilo mostra a duração
    assert pg.locator("#duracao option").all_inner_texts() == ["Padrão do estilo (5 s)", "4 s", "5 s", "6 s", "7 s"]
    assert _texto(pg, "#custo") == "Cada vídeo de 5 s em 480p custa R$ 1,25."
    fotos = [_salvar(tmp_path, f"f{i}.png", _png((40 * i, 90, 160), "circulo")) for i in range(1, 3)]
    pg.set_input_files("#fotos", [str(f) for f in fotos])
    custo = pw.expect(pg.locator("#custo"))
    custo.to_have_text("Este pedido: 2 vídeos de 5 s em 480p = R$ 2,50 · saldo depois: R$ 0,50")
    pg.select_option("#qualidade", "alta")
    custo.to_have_text("Este pedido: 2 vídeos de 5 s em 720p = R$ 5,00 · faltam R$ 2,00 de saldo")
    assert "falta" in pg.get_attribute("#custo", "class")
    pg.fill("#prompt", "a câmera se aproxima devagar do produto")
    pg.click("#enviar")
    assert "Saldo insuficiente" in _texto(pg, "#msg")
    assert ambiente.banco.lotes() == []  # nada foi criado
    # 4 s em 480p: R$ 2,00, cobrados quando as fotos chegam
    pg.select_option("#qualidade", "rapida")
    pg.select_option("#duracao", "4")
    custo.to_have_text("Este pedido: 2 vídeos de 4 s em 480p = R$ 2,00 · saldo depois: R$ 1,00")
    pg.click("#enviar")
    pg.wait_for_selector("#msg.ok", timeout=15000)
    pw.expect(pg.locator("#saldo-valor")).to_have_text("R$ 1,00")
    pg.wait_for_selector("#lotes .lote:has-text('Custo do pedido: R$ 2,00')")
    pg.click("#extrato summary")
    pw.expect(pg.locator("#extrato-lista li").first).to_contain_text("Pedido: 2 vídeo(s) de 4 s em 480p")
    pw.expect(pg.locator("#extrato-lista li").first).to_contain_text("-R$ 2,00")

    # o dono lança uma recarga pelo /aprovar e baixa o extrato
    adm_ctx = navegador.new_context()
    _com_codigo(adm_ctx, "render2go-token-admin", ADMIN)
    adm = _pagina(adm_ctx, erros)
    adm.goto(f"{ambiente.url}/aprovar")
    adm.wait_for_selector("#clientes table")
    assert "R$ 1,00" in _texto(adm, "#clientes table")
    assert "720p a R$ 0,50" in _texto(adm, "#precos-info")
    adm.click("#form-saldo button[type=submit]")
    assert "Escolha o cliente" in adm.locator("#saldo-msg").inner_text()
    adm.select_option("#saldo-cliente", index=1)
    adm.fill("#saldo-valor", "20")
    adm.fill("#saldo-descricao", "Pix de 09/10")
    adm.fill("#saldo-referencia", "E2026100912345")
    adm.click("#form-saldo button[type=submit]")
    adm.click("dialog[open] button:has-text('Lançar')")
    adm.wait_for_selector("#saldo-msg.ok")
    assert "R$ 21,00" in _texto(adm, "#saldo-msg")
    pw.expect(adm.locator("#clientes table")).to_contain_text("R$ 21,00")
    with adm.expect_download() as baixado:
        adm.click("#clientes button:has-text('Baixar')")
    extrato = json.loads(Path(baixado.value.path()).read_text())
    assert extrato["saldo"] == 2100 and extrato["movimentos"][0]["descricao"] == "Pix de 09/10"
    # o mesmo Pix de novo: recusado
    adm.select_option("#saldo-cliente", index=1)
    adm.fill("#saldo-valor", "20")
    adm.fill("#saldo-descricao", "Pix de 09/10")
    adm.fill("#saldo-referencia", "E2026100912345")
    adm.click("#form-saldo button[type=submit]")
    adm.click("dialog[open] button:has-text('Lançar')")
    adm.wait_for_selector("#saldo-msg.erro")
    assert "já registrado" in adm.locator("#saldo-msg").inner_text()
    _capturar(pg, "pre-pago-cliente")
    _capturar(adm, "pre-pago-aprovar")
    ctx.close()
    adm_ctx.close()
    assert [e for e in erros if "status of 422" not in e] == []  # só a recusa do Pix repetido
