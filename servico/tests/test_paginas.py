"""Páginas no navegador (Chromium headless): fluxo completo, sem erros no console nem bloqueios
da política de segurança (CSP). Precisa do pacote playwright e de um Chromium; senão, pula."""

import base64
import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")
from test_api import PAINEL, TOTP  # noqa: E402
from test_fila import ADMIN, _png, ambiente, entrar_painel  # noqa: E402,F401 (mesma API local e armazenamento)

from render2go_servico import contas, pagamentos  # noqa: E402

CHROMIUM = next(iter(sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))), None)
ESCOLHA_COOKIES = json.dumps(
    {"versao": "1", "estatisticas": False, "marketing": False, "quando": "2026-10-08T00:00:00Z"}
)
PAGINA = {"X-Render2Go": "1"}


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


def _codigo_do_email(amb) -> str:
    amb.cfg.esperar_fundo()
    return re.match(r"(\d{8}) ", amb.enviados[-1]["Subject"]).group(1)


def _totp_novo():
    """Um código do autenticador ainda não usado nesta janela (o mesmo não vale duas vezes)."""
    usados = _totp_novo.usados
    agora = int(time.time() // 30)
    for contador in (agora + 1, agora, agora - 1):
        if contador not in usados:
            usados.add(contador)
            return contas.totp(TOTP, contador)
    raise AssertionError("códigos do autenticador esgotados nesta janela")


_totp_novo.usados = set()


@pytest.fixture(autouse=True)
def _totp_limpo():
    _totp_novo.usados = set()


def test_client_and_reviewer_pages_end_to_end(ambiente, navegador, tmp_path):  # noqa: F811 (fixture)
    erros: list[str] = []
    ambiente.cfg.analisar = _analisar
    # o dono entra no painel (endereço secreto, token + autenticador)
    adm = _pagina(navegador, erros)
    adm.goto(f"{ambiente.url}{PAINEL}")  # sem a barra final: vai para o endereço com a barra
    adm.wait_for_url(f"**{PAINEL}/")
    adm.fill("#admin-token", ADMIN)
    adm.fill("#admin-totp", "000000")
    adm.click("#admin-entrar")
    adm.wait_for_selector("#msg-entrada:not([hidden])")
    assert "inválido" in adm.locator("#msg-entrada").inner_text()
    adm.fill("#admin-token", ADMIN)
    adm.fill("#admin-totp", _totp_novo())
    adm.click("#admin-entrar")
    adm.wait_for_selector("#titulo-painel:visible")
    assert adm.locator("#aviso-cookies").count() == 0  # o painel (só o dono) não mostra o aviso

    # cliente novo: cria a conta pelo e-mail, com o código que chega nele
    for n, (cor, forma) in {"bolo.png": ((40, 90, 160), "circulo"), "loja.png": ((200, 120, 60), "barras")}.items():
        (tmp_path / n).write_bytes(_png(cor, forma))
    cli = _pagina(navegador, erros)
    cli.goto(ambiente.url)
    # navegador novo: aviso de cookies; "Gerenciar" mostra as categorias, e salvar guarda só os necessários
    cli.wait_for_selector("#aviso-cookies")
    cli.click("#aviso-cookies button:has-text('Gerenciar')")
    assert cli.locator("#ck-necessarios").is_checked() and cli.locator("#ck-necessarios").is_disabled()
    assert not cli.locator("#ck-estatisticas").is_checked()
    cli.click("#aviso-cookies button:has-text('Salvar escolhas')")
    cli.wait_for_selector("#aviso-cookies", state="detached")
    with ambiente.banco.tx() as c:
        (ck,) = c.all("SELECT escolha FROM cookies")
    assert json.loads(ck["escolha"]) == {"necessarios": True, "estatisticas": False, "marketing": False}
    # e-mail fora da lista: recusado com a explicação; sem o aceite dos termos, também
    cli.fill("#email", "padaria@minhaempresa.com.br")
    cli.click("#enviar-email")
    assert "Para continuar, aceite" in cli.locator("#msg-entrada").inner_text()
    cli.check("#aceite")
    cli.click("#enviar-email")
    pw.expect(cli.locator("#msg-entrada")).to_contain_text("Gmail, Outlook/Hotmail")
    cli.fill("#email", "padaria.bairro@gmail.com")
    cli.click("#enviar-email")
    cli.wait_for_selector("#form-codigo:not([hidden])")
    assert "padaria.bairro@gmail.com" in cli.locator("#codigo-enviado").inner_text()
    assert cli.evaluate("document.activeElement.id") == "codigo"
    cli.fill("#codigo", _codigo_do_email(ambiente))
    cli.click("#enviar-codigo")
    cli.wait_for_selector("#receita option", state="attached")
    assert "padaria.bairro@gmail.com · plano teste gratuito" in cli.locator("#conta-resumo").inner_text()
    assert cli.evaluate("document.cookie") == ""  # a sessão é HttpOnly: o JavaScript não a vê
    assert cli.evaluate("localStorage.getItem('render2go-conectado')") == "1"
    assert cli.locator("#form-lote").is_hidden()  # antes do pedido, a escolha do que criar
    _escolher_finalidade(cli)
    # teste gratuito: só 480p e 3 s, e o aviso das regras
    assert cli.locator("#qualidade option").all_inner_texts() == ["Rápida (prévia, 480p)"]
    assert cli.locator("#duracao option").all_inner_texts() == ["3 s"]
    assert "envie todas juntas, em um único pedido" in cli.locator("#plano-aviso").inner_text()
    cli.set_input_files("#fotos", [str(tmp_path / "bolo.png"), str(tmp_path / "loja.png")])
    assert "2 de 5 fotos escolhidas · ainda cabem 3" in cli.locator("#contagem").inner_text()
    assert cli.locator("#saldo").is_hidden() and cli.locator("#custo").is_hidden()  # o teste não paga
    assert cli.locator("#enviar").inner_text() == "Enviar pedido com 2 fotos"
    pw.expect(cli.locator("#previa .miniatura :is(canvas, img)")).to_have_count(2)
    assert cli.locator("#modelo option").all_inner_texts() == ["Wan 2.2 · imagem para vídeo · 14B"]
    assert cli.locator("#declarante").count() == 0
    cli.check("#consentimento")
    _capturar(cli, "cliente-teste")
    cli.fill("#prompt", "a câmera se aproxima devagar do produto")
    cli.select_option("#duracao", "3")
    cli.click("#enviar")
    cli.wait_for_selector("#msg.ok", timeout=15000)
    cli.wait_for_selector("text=Em análise")
    assert "Ele começa em até" in cli.locator("#lotes .previsao").inner_text()
    assert sum(k.endswith(".png") for k in ambiente.objetos) == 2  # direto no armazenamento
    cli.wait_for_selector("#plano-aviso.alerta")  # teste usado: o formulário trava
    assert cli.locator("#enviar").is_disabled()

    # o dono vê as miniaturas, o aviso de pessoas por foto e o resumo do lote, e aprova
    ambiente.cfg.analises.esperar()
    adm.click("#atualizar")
    adm.wait_for_selector(".grade img")
    assert adm.locator(".grade img").count() == 2
    assert adm.locator(".selo.pessoas").count() == 1
    assert "sem pessoas detectadas" in adm.locator(".grade").inner_text()
    assert "Autorização marcada em" in adm.locator("#pendentes .autorizacao").inner_text()
    assert "marcou ter autorização" in adm.locator("#pendentes .aviso.alerta").inner_text()
    _capturar(adm, "painel")
    adm.click("#pendentes [data-acao=aprovar]")
    adm.wait_for_selector("text=Nada aguardando revisão.")
    hist = adm.locator("#historico .hist").first
    assert "padaria.bairro@gmail.com" in hist.inner_text() and "IP 127.0.0.1" in hist.inner_text()
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
    pw.expect(cli.locator("#suporte-conta")).to_contain_text("conectado como padaria.bairro@gmail.com")
    cli.fill("#suporte-mensagem", "Preciso de ajuda com o meu pedido de teste.")
    cli.click("#suporte-enviar")
    cli.wait_for_selector("#suporte-msg.ok")
    adm.click("#atualizar")
    adm.wait_for_selector("#suporte .msg-suporte")
    assert "Preciso de ajuda" in adm.locator("#suporte").inner_text()
    _capturar(cli, "cliente")
    # sair: a sessão termina no servidor e a página volta para a entrada
    cli.goto(ambiente.url)
    cli.wait_for_selector("#sair:visible")
    cli.click("#sair")
    cli.wait_for_selector("#form-email:visible")
    assert cli.evaluate("localStorage.getItem('render2go-conectado')") is None
    # só as recusas de propósito: o código errado do autenticador (401) e o e-mail fora da lista (422)
    esperados = ["401 (Unauthorized)", "422 (Unprocessable Entity)"]
    assert [e.rsplit("status of ", 1)[-1] for e in erros] == esperados, erros


def test_support_page_without_an_account_and_cookie_banner(ambiente, navegador):  # noqa: F811 (fixture)
    erros: list[str] = []
    pg = _pagina(navegador, erros)
    pg.goto(f"{ambiente.url}/suporte")
    pg.wait_for_selector("#aviso-cookies")
    _capturar(pg, "suporte-cookies")
    pg.click("#aviso-cookies button:has-text('Aceitar todos')")
    pg.wait_for_selector("#aviso-cookies", state="detached")
    assert pg.locator("#suporte-email").get_attribute("required") is not None  # sem conta: e-mail obrigatório
    assert pg.locator("#verificacao-suporte").is_hidden()  # verificação desligada nos testes: nada carrega
    pg.fill("#suporte-mensagem", "Não consigo entrar na minha conta, como faço?")
    pg.fill("#suporte-email", "dona@loja.com.br")
    pg.click("#suporte-enviar")
    pg.wait_for_selector("#suporte-msg.ok")
    (m,) = ambiente.banco.suporte_recente()
    assert m["cliente_id"] == "" and m["email"] == "dona@loja.com.br"
    # "Preferências de cookies" reabre o aviso com a escolha atual
    pg.click("#preferencias-cookies")
    assert pg.locator("#ck-estatisticas").is_checked() and pg.locator("#ck-marketing").is_checked()
    assert erros == [], erros


def test_legal_pages_are_linked_from_every_footer(ambiente, navegador):  # noqa: F811 (fixture)
    erros: list[str] = []
    pg = _pagina(navegador, erros)
    for caminho, titulo in (
        ("/termos", "Termos de Uso"),
        ("/privacidade", "Política de Privacidade"),
        ("/cookies", "Política de Cookies"),
    ):
        pg.goto(f"{ambiente.url}{caminho}")
        pw.expect(pg.locator("h1")).to_have_text(titulo)
        pw.expect(pg.locator(f".rodape a[href='{caminho}']")).to_have_attribute("aria-current", "page")
        assert "[preencher]" in pg.locator(".identificacao").inner_text()  # nome, CNPJ e endereço vêm do .env
    pw.expect(pg.locator(".texto-legal")).to_contain_text("__Host-render2go")
    pg.goto(f"{ambiente.url}/termos")
    pw.expect(pg.locator("#saldo")).to_contain_text("Direito de arrependimento")
    pg.goto(ambiente.url)
    pg.click(".rodape a[href='/privacidade']")
    pg.wait_for_url("**/privacidade")
    pw.expect(pg.locator(".texto-legal")).to_contain_text("art. 18")
    assert erros == [], erros


# -- acessibilidade, celular, teclado e sessão (docs/10) ------------------------------------------

AXE = Path(__file__).resolve().parents[1] / "web" / "node_modules" / "axe-core" / "axe.min.js"
REGRAS_AXE = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]
PAINEIS: dict[str, object] = {}  # sessão do painel (requests) de cada ambiente


def _painel(amb):
    if amb.url not in PAINEIS:
        PAINEIS[amb.url] = entrar_painel(amb.url)
    return PAINEIS[amb.url]


def _api(amb, metodo, caminho, token=None, **kw):
    """Painel: pela sessão do painel. Cliente: com o código de acesso (Bearer), como uma integração."""
    import requests

    if caminho.startswith(PAINEL):
        r = _painel(amb).request(metodo, f"{amb.url}{caminho}", timeout=10, **kw)
    else:
        r = requests.request(
            metodo, f"{amb.url}{caminho}", headers={"Authorization": f"Bearer {token}"}, timeout=10, **kw
        )
    assert r.status_code == 200, r.text
    return r.json()


def _cliente(amb, plano="pago", saldo="100"):
    cid, token = amb.banco.criar_cliente("Café da Esquina", 500, plano)
    if plano == "pago" and saldo:  # pré-pago: recarga antes de pedir
        _api(amb, "POST", f"{PAINEL}/api/clientes/{cid}/saldo", json={"valor": saldo, "descricao": "Pix"})
    return token


def _lote(amb, token, fotos: dict[str, bytes], aprovar=False, pronto=False, prompt=None):
    import requests

    corpo = {  # sem duração: 3 s no teste gratuito; a do estilo (5 s) no pré-pago
        "prompt": prompt or "vapor sobe da xícara e a câmera se aproxima devagar",
        "fotos": [{"nome": n, "tipo": "image/png", "bytes": len(b)} for n, b in fotos.items()],
    }
    r = _api(amb, "POST", "/api/lotes", token, json=corpo)
    for e in r["envios"]:
        requests.put(e["url"], data=fotos[e["original"]], timeout=10)
    _api(amb, "POST", f"/api/lotes/{r['lote']}/enviado", token)
    if aprovar or pronto:
        _api(amb, "POST", f"{PAINEL}/api/lotes/{r['lote']}/aprovar")
    if pronto:
        lote = amb.banco.lote(r["lote"])
        for j in amb.banco.jobs(r["lote"]):
            if j["tipo"] == "render":
                amb.objetos[f"saida/{lote['cliente_id']}/{lote['id']}/{j['arquivo']}"] = b"mp4"
        with amb.banco.tx() as c:
            c.run("UPDATE jobs SET status = 'ok' WHERE lote_id = ?", (r["lote"],))
            c.run("UPDATE lotes SET status = 'concluido' WHERE id = ?", (r["lote"],))
    return r["lote"]


def _escolher_finalidade(pg):
    """Tela "O que você quer criar?": o pedido começa pela finalidade (hoje só "Foto vira vídeo")."""
    pg.click("#blocos [data-foco='finalidade:imagem-video']")
    pg.wait_for_selector("#form-lote:not([hidden])")


def _com_sessao(contexto, amb, token=None, admin=False, fixa=True):
    """Navegador que já entrou (cookie da sessão, a marca de conectado e a escolha de cookies feita).
    fixa=False: a marca vai só na 1ª página da aba (a saída tira a marca, e ela não volta na seguinte)."""
    import requests

    host = amb.url.split("//")[1].split(":")[0]
    if admin:
        valor = entrar_painel(amb.url).cookies["render2go-painel"]
        contexto.add_cookies([{"name": "render2go-painel", "value": valor, "domain": host, "path": PAINEL}])
        marca = "render2go-painel-conectado"
    else:
        r = requests.post(
            f"{amb.url}/api/conta/entrar-codigo", json={"codigo": token, "aceite": True}, headers=PAGINA, timeout=10
        )
        assert r.status_code == 200, r.text
        contexto.add_cookies(
            [{"name": "render2go-sessao", "value": r.cookies["render2go-sessao"], "domain": host, "path": "/"}]
        )
        marca = "render2go-conectado"
    escolha = json.dumps(ESCOLHA_COOKIES)
    script = f"localStorage.setItem({json.dumps(marca)}, '1');localStorage.setItem('render2go-cookies', {escolha});"
    if not fixa:
        uma_vez = "if (!sessionStorage.getItem('teste-marcado')) { sessionStorage.setItem('teste-marcado', '1'); "
        script = f"{uma_vez}{script} }}"
    contexto.add_init_script(script)


def _axe(pg) -> list[dict]:
    if not pg.evaluate("typeof window.axe !== 'undefined'"):
        pg.evaluate(AXE.read_text())
    return pg.evaluate(
        """async (regras) => (await axe.run(document, {runOnly: {type: 'tag', values: regras},
            resultTypes: ['violations']})).violations.map((v) => ({id: v.id, impacto: v.impact,
            alvos: v.nodes.slice(0, 4).map((n) => n.target.join(' '))}))""",
        REGRAS_AXE,
    )


def _transbordando(pg, largura) -> list:
    """Os elementos que passam da largura da tela (para a mensagem de erro dizer qual é)."""
    return pg.evaluate(
        """(largura) => {
            const rola = (e) => { for (let p = e.parentElement; p; p = p.parentElement)
                if (['auto', 'scroll', 'hidden'].includes(getComputedStyle(p).overflowX)) return true; return false; };
            return [...document.querySelectorAll('body *')]
                .filter((e) => e.offsetParent !== null && e.getBoundingClientRect().right > largura + 0.5 && !rola(e))
                .slice(0, 6).map((e) => [e.tagName, e.id || e.className, Math.round(e.getBoundingClientRect().right)]);
        }""",
        largura,
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

    # páginas abertas por quem ainda não entrou (com o aviso de cookies)
    anonimo = navegador.new_context(color_scheme=tema, reduced_motion="reduce", bypass_csp=True)
    pg = anonimo.new_page()
    paginas = [
        ("/", "entrada"),
        ("/suporte", "suporte"),
        ("/termos", "termos"),
        ("/privacidade", "privacidade"),
        ("/cookies", "cookies"),
        (f"{PAINEL}/", "painel-entrada"),
        ("/nada", "404"),
    ]
    for caminho, estado in paginas:
        pg.goto(f"{ambiente.url}{caminho}")
        pg.wait_for_load_state("networkidle")
        conferir(pg, estado)
    pg.goto(ambiente.url)
    pg.fill("#email", "acessivel@gmail.com")
    pg.check("#aceite")
    pg.click("#enviar-email")
    pg.wait_for_selector("#form-codigo:not([hidden])")
    conferir(pg, "entrada-codigo")
    anonimo.close()

    # cliente com vídeos prontos, com o player aberto, e o dono com um pedido pendente
    cli = navegador.new_context(color_scheme=tema, reduced_motion="reduce", bypass_csp=True)
    _com_sessao(cli, ambiente, tok_pronto)
    pg = cli.new_page()
    pg.goto(ambiente.url)
    pg.wait_for_selector(".videos button.assistir")
    pg.wait_for_selector("#blocos [data-foco='finalidade:imagem-video']")
    conferir(pg, "finalidades")
    pg.click("#blocos [data-foco='finalidade:todas']")
    pg.wait_for_selector("dialog.todas-finalidades[open] .opcao")
    conferir(pg, "ver-todas")
    pg.keyboard.press("Escape")
    pg.click("#abrir-conta")
    pg.wait_for_selector("dialog.conta[open]")
    conferir(pg, "conta")
    pg.keyboard.press("Escape")
    _escolher_finalidade(pg)
    pg.set_input_files(
        "#fotos",
        [str(_salvar(tmp_path, "a.png", fotos["bolo.png"])), str(_salvar(tmp_path, "p.png", _png_de(300, 200)))],
    )
    pw.expect(pg.locator("#previa .miniatura :is(canvas, img)")).to_have_count(2)
    pg.wait_for_selector("#aviso-fotos:not([hidden])")  # foto pequena: aviso na lista e na miniatura
    pg.click(".exemplos-pedido summary")
    conferir(pg, "painel-cliente")
    pg.locator(".videos button.assistir").first.click()
    pg.wait_for_selector("dialog[open]")
    conferir(pg, "player")
    cli.close()
    g = ambiente.banco.gerador_subindo("NVIDIA L40S")  # relatório de GPUs e pods com uma GPU ligada
    ambiente.banco.gerador_criado(g, "pod-abc123", "NVIDIA L40S", 1.09, "EU-RO-1")
    adm = navegador.new_context(color_scheme=tema, reduced_motion="reduce", bypass_csp=True)
    _com_sessao(adm, ambiente, admin=True)
    pg = adm.new_page()
    pg.goto(f"{ambiente.url}{PAINEL}/")
    pg.wait_for_selector("#pendentes .selo.pessoas")
    pg.wait_for_selector("#ips table")
    pg.wait_for_selector("#gpus table")
    pg.wait_for_selector("#pods table")
    pg.wait_for_selector("#checagem li")
    conferir(pg, "painel-dono")
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
    for sessao, caminho, estado in [
        (None, "/", "entrada"),
        (None, "/suporte", "suporte"),
        (None, "/termos", "termos"),
        ("cliente", "/", "painel"),
        ("admin", f"{PAINEL}/", "painel-dono"),
    ]:
        ctx = navegador.new_context(**celular)
        if sessao:
            _com_sessao(ctx, ambiente, tok, admin=sessao == "admin")
        pg = ctx.new_page()
        pg.on("console", lambda m: m.type == "error" and erros.append(m.text))
        pg.on("pageerror", lambda e: erros.append(str(e)))
        pg.goto(f"{ambiente.url}{caminho}")
        pg.wait_for_load_state("networkidle")
        if estado == "painel":
            pg.wait_for_selector("#blocos [data-foco='finalidade:imagem-video']")
            assert pg.evaluate("document.documentElement.scrollWidth") <= largura, "finalidades"
            # no celular, as janelas sobem de baixo, na largura toda (perto do polegar)
            pg.click("#abrir-conta")
            pg.wait_for_function(  # a janela sobe com uma animação curta: mede depois que ela termina
                "() => document.querySelector('dialog.conta[open]')?.getAnimations()"
                ".every((a) => a.playState === 'finished')"
            )
            caixa = pg.locator("dialog.conta[open]").bounding_box()
            assert caixa["x"] == 0 and round(caixa["width"]) == largura, caixa
            assert abs(caixa["y"] + caixa["height"] - 740) <= 1, caixa
            pg.keyboard.press("Escape")
            pg.wait_for_selector("dialog.conta[open]", state="detached")
            _escolher_finalidade(pg)
            pg.set_input_files("#fotos", [str(_salvar(tmp_path, "c.png", _png((90, 160, 40), "circulo")))])
            pw.expect(pg.locator("#previa .miniatura :is(canvas, img)")).to_have_count(1)
        if estado == "painel-dono":
            pg.wait_for_selector("#checagem li")
        largura_pagina = pg.evaluate("document.documentElement.scrollWidth")
        assert largura_pagina <= largura, (estado, largura_pagina, _transbordando(pg, largura))
        # com uma fonte larga (a padrão de muitos Linux e de alguns Android), também sem rolagem lateral
        pg.evaluate(
            "() => { const r = document.documentElement.style; for (const v of ['--fonte-texto', '--fonte-titulos'])"
            " r.setProperty(v, '\"DejaVu Sans\", Verdana, sans-serif'); }"
        )
        largura_pagina = pg.evaluate("document.documentElement.scrollWidth")
        assert largura_pagina <= largura, (estado, "fonte larga", largura_pagina, _transbordando(pg, largura))
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
    assert erros == [], erros


def test_keyboard_skip_link_and_cookie_banner(ambiente, navegador):  # noqa: F811 (fixture)
    pg = navegador.new_page()
    pg.goto(ambiente.url)
    pg.wait_for_selector("#aviso-cookies")
    # "Aceitar todos" e "Só os necessários" com o mesmo destaque (recusar tão fácil quanto aceitar; guia da ANPD)
    aceitar = pg.locator("#aviso-cookies button", has_text="Aceitar todos")
    recusar = pg.locator("#aviso-cookies button", has_text="Só os necessários")
    assert aceitar.get_attribute("class") == recusar.get_attribute("class") == "botao"
    assert round(aceitar.bounding_box()["height"]) == round(recusar.bounding_box()["height"])  # (aviso ainda subindo)
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


def test_session_opens_the_panel_directly_and_a_disabled_account_returns_to_login(ambiente, navegador):  # noqa: F811
    tok = _cliente(ambiente)
    ctx = navegador.new_context()
    _com_sessao(ctx, ambiente, tok)
    # o <script> do <head> marca a sessão antes do primeiro quadro: o formulário de entrada nunca aparece
    ctx.add_init_script(
        "document.addEventListener('DOMContentLoaded', () => { window.__entrada = "
        "document.getElementById('form-email').checkVisibility(); window.__sessao = "
        "document.documentElement.classList.contains('com-sessao'); });"
    )
    pg = ctx.new_page()
    pg.goto(ambiente.url)
    pg.wait_for_selector("#receita option", state="attached")
    assert pg.evaluate("[window.__entrada, window.__sessao]") == [False, True]
    assert pg.locator("#painel").is_visible() and not pg.locator("#form-email").is_visible()
    # conta desativada: as sessões caem; a página volta para a entrada com a explicação
    with ambiente.banco.tx() as c:
        (cliente,) = c.all("SELECT id FROM clientes")
    _api(ambiente, "POST", f"{PAINEL}/api/clientes/{cliente['id']}/desativar")
    pg.reload()
    pg.wait_for_selector("#msg-entrada:not([hidden])")
    assert "Sua sessão terminou" in pg.locator("#msg-entrada").inner_text()
    assert pg.locator("#form-email").is_visible()
    assert pg.evaluate("localStorage.getItem('render2go-conectado')") is None
    assert pg.evaluate("document.activeElement.id") == "email"
    ctx.close()


def test_photo_preview_removal_suggestion_counter_and_required_authorization(ambiente, navegador, tmp_path):  # noqa: F811
    erros: list[str] = []
    tok = _cliente(ambiente)
    ctx = navegador.new_context()
    _com_sessao(ctx, ambiente, tok)
    pg = _pagina(ctx, erros)
    pg.goto(ambiente.url)
    pg.wait_for_selector("#receita option", state="attached")
    _escolher_finalidade(pg)
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
    assert erros == [], erros


def test_session_fetches_the_panel_data_early_and_only_once(ambiente, navegador):  # noqa: F811 (fixture)
    erros: list[str] = []
    tok = _cliente(ambiente)
    disp = "6f1c2a3b-4d5e-4f60-8a71-92b3c4d5e6f7"
    ctx = navegador.new_context()
    _com_sessao(ctx, ambiente, tok)
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
    pw.expect(pg.locator("#conta-resumo")).to_contain_text("Conectado")
    # o <script> do <head> já pediu receitas, lotes e conta; a página usou essas respostas, sem pedir de novo
    assert sorted(pedidos) == [("conta", disp), ("lotes", disp), ("receitas", disp)]
    ctx.close()
    assert erros == [], erros


def _texto(pg, seletor):
    """Texto visível sem os espaços não separáveis que o navegador põe nos valores em reais."""
    return pg.locator(seletor).inner_text().replace(" ", " ")


def test_prepaid_balance_price_and_top_up_by_the_owner(ambiente, navegador, tmp_path):  # noqa: F811 (fixture)
    erros: list[str] = []
    tok = _cliente(ambiente, saldo="3,00")
    ctx = navegador.new_context()
    _com_sessao(ctx, ambiente, tok)
    pg = _pagina(ctx, erros)
    pg.goto(ambiente.url)
    pg.wait_for_selector("#saldo:not([hidden])")
    _escolher_finalidade(pg)
    assert _texto(pg, "#saldo-valor") == "R$ 3,00"
    precos = _texto(pg, "#saldo-precos")
    assert precos == "480p: R$ 0,25 por segundo de vídeo · 720p: R$ 0,50 por segundo de vídeo"
    assert pg.locator("#adicionar-saldo").is_hidden()  # recarga por Pix desligada neste servidor
    assert "fale com a gente pelo suporte" in pg.locator("#saldo-recarga").inner_text()
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

    # o dono lança uma recarga pelo painel e baixa o extrato
    adm_ctx = navegador.new_context()
    _com_sessao(adm_ctx, ambiente, admin=True)
    adm = _pagina(adm_ctx, erros)
    adm.goto(f"{ambiente.url}{PAINEL}/")
    adm.wait_for_selector("#clientes table")
    assert "R$ 1,00" in _texto(adm, "#clientes table")
    assert "720p a R$ 0,50" in _texto(adm, "#precos-info")
    adm.click("#form-saldo button[type=submit]")
    assert "Escolha a conta" in adm.locator("#saldo-msg").inner_text()
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
        adm.click("#clientes button:has-text('Extrato')")
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
    _capturar(adm, "pre-pago-painel")
    ctx.close()
    adm_ctx.close()
    assert [e for e in erros if "status of 422" not in e] == []  # só a recusa do Pix repetido


class _MercadoPago:
    """Mercado Pago falso para a janela de Pix: QR de verdade (PNG) e a situação controlada pelo teste."""

    def __init__(self):
        from io import BytesIO

        from PIL import Image

        b = BytesIO()
        Image.new("RGB", (64, 64), (0, 0, 0)).save(b, "PNG")
        self.qr = base64.b64encode(b.getvalue()).decode()
        self.situacao: dict[str, dict] = {}

    def criar_pix(self, recarga_id, valor, email, doc):
        pid = str(5000 + len(self.situacao))
        self.situacao[pid] = {"status_mp": "pending", "valor": valor, "ref": recarga_id}
        return {
            "id": pid,
            "status": "pendente",
            "expira": time.time() + 3600,
            "copia_cola": "00020126BR.GOV.BCB.PIX",
            "qr_base64": self.qr,
        }

    def consultar(self, pid):
        s = self.situacao[pid]
        return {"id": pid, "status_mp": s["status_mp"], "status": pagamentos.SITUACOES[s["status_mp"]], "detalhe": None,
                "valor": s["valor"], "moeda": "BRL", "referencia": s["ref"], "metodo": "pix"}  # fmt: skip


def test_pix_top_up_dialog_shows_the_qr_and_the_trial_becomes_prepaid(ambiente, navegador):  # noqa: F811 (fixture)
    import requests

    erros: list[str] = []
    mp = _MercadoPago()
    ambiente.cfg.mp = pagamentos.ConfigMP("token-mp", "segredo", f"{ambiente.url}/api/pagamentos/mercadopago")
    ambiente.cfg.provedor_pagamento = lambda cfg: mp
    tok = _cliente(ambiente, plano="teste")
    with ambiente.banco.tx() as c:  # a recarga precisa de um e-mail na conta
        c.run("UPDATE clientes SET email = 'cafe.esquina@gmail.com', email_norm = 'cafeesquina@gmail.com'")
    ctx = navegador.new_context()
    _com_sessao(ctx, ambiente, tok)
    pg = _pagina(ctx, erros)
    pg.goto(ambiente.url)
    pg.wait_for_selector("#receita option", state="attached")
    pg.click("#abrir-conta")
    pg.click("dialog.conta[open] button:has-text('Adicionar saldo por Pix')")
    dialogo = pg.locator("dialog.pix[open]")
    pw.expect(dialogo.locator("button[aria-pressed=true]")).to_have_text("R$ 50,00")
    dialogo.locator("#pix-documento").fill("111.111.111-11")
    dialogo.locator("button[type=submit]").click()
    pw.expect(dialogo.locator(".aviso.erro")).to_contain_text("CPF ou o CNPJ")
    dialogo.locator("button:has-text('R$ 20,00')").click()
    dialogo.locator("#pix-documento").fill("529.982.247-25")
    dialogo.locator("button[type=submit]").click()
    pw.expect(dialogo.locator("img.qr-pix")).to_be_visible()
    assert dialogo.locator("img.qr-pix").evaluate("(i) => i.complete && i.naturalWidth") == 64  # o PNG abriu
    assert dialogo.locator("#pix-copia").input_value() == "00020126BR.GOV.BCB.PIX"
    pw.expect(dialogo.locator(".situacao-pix")).to_have_text("Aguardando o pagamento…")
    _capturar(pg, "pix")
    # o banco confirma: o Mercado Pago avisa (assinado) e a janela mostra o crédito na próxima consulta
    (pid,) = mp.situacao
    mp.situacao[pid]["status_mp"] = "approved"
    manifesto = f"id:{pid};request-id:r1;ts:1;"
    v1 = hmac.new(b"segredo", manifesto.encode(), hashlib.sha256).hexdigest()
    r = requests.post(
        f"{ambiente.url}/api/pagamentos/mercadopago?data.id={pid}&type=payment",
        headers={"x-signature": f"ts=1,v1={v1}", "x-request-id": "r1"},
        timeout=10,
    )
    assert r.status_code == 200
    pw.expect(dialogo.locator(".aviso.ok")).to_contain_text("R$ 20,00 entraram no seu saldo", timeout=15000)
    # a 1ª recarga tira a conta do teste: a página recarrega já no pré-pago, com o saldo
    pg.wait_for_selector("#saldo:not([hidden])", timeout=15000)
    assert _texto(pg, "#saldo-valor") == "R$ 20,00"
    ctx.close()
    assert erros == [], erros


def test_owner_panel_pods_emergency_interest_clicks_and_security(ambiente, navegador):  # noqa: F811 (fixture)
    erros: list[str] = []
    b = ambiente.banco
    g = b.gerador_subindo("NVIDIA L40S")
    b.gerador_pronto(g, "pod-abc123", "NVIDIA L40S", 1.09)
    cid, _ = b.criar_cliente(
        "loja.interesse@gmail.com", 500, "teste", email="loja.interesse@gmail.com", email_norm="lojainteresse@gmail.com"
    )
    b.registrar_interesse(cid, "texto-video")
    ctx = navegador.new_context()
    _com_sessao(ctx, ambiente, admin=True)
    adm = _pagina(ctx, erros)
    adm.goto(f"{ambiente.url}{PAINEL}/")
    adm.wait_for_selector("#pods table")
    assert "pod-abc123" in _texto(adm, "#pods table")
    pw.expect(adm.locator("#n-pods")).to_have_text("1")
    adm.click("#pods button:has-text('Desligar')")
    adm.click("dialog[open] button:has-text('Desligar')")
    pw.expect(adm.locator("#comandos")).to_contain_text("aguardando o orquestrador")
    (cmd,) = b.comandos_pendentes()
    assert (cmd["tipo"], cmd["alvo"]) == ("desligar_pod", g)
    adm.click("#emergencia")
    adm.click("dialog[open] button:has-text('Parar tudo')")
    pw.expect(adm.locator("#estado-orquestrador")).to_contain_text("PAUSADO")
    pw.expect(adm.locator("#retomar")).to_be_visible()
    adm.click("#retomar")
    pw.expect(adm.locator("#estado-orquestrador")).to_contain_text("Funcionando")
    # cada clique em "Tenho interesse", com o e-mail da conta
    pw.expect(adm.locator("#cliques table")).to_contain_text("loja.interesse@gmail.com")
    pw.expect(adm.locator("#cliques table")).to_contain_text("Vídeo a partir de um texto")
    # segurança: o que falta configurar aparece em destaque; as ações do painel ficam registradas
    pw.expect(adm.locator("#checagem li.falta").first).to_be_visible()
    pw.expect(adm.locator("#checagem")).to_contain_text("Painel com código do autenticador")
    pw.expect(adm.locator("#auditoria table")).to_contain_text("emergencia")
    _capturar(adm, "painel-pods")
    # sair do painel
    adm.click("#sair")
    adm.wait_for_selector("#form-admin:visible")
    ctx.close()
    assert erros == [], erros


def _png_de(largura, altura, cor=(40, 90, 160)):
    from io import BytesIO

    from PIL import Image

    b = BytesIO()
    Image.new("RGB", (largura, altura), cor).save(b, "PNG")
    return b.getvalue()


def test_small_photo_warning_order_help_and_gpu_report(ambiente, navegador, tmp_path):  # noqa: F811 (fixture)
    erros: list[str] = []
    tok = _cliente(ambiente)
    ctx = navegador.new_context()
    _com_sessao(ctx, ambiente, tok)
    pg = _pagina(ctx, erros)
    pg.goto(ambiente.url)
    _escolher_finalidade(pg)
    pg.wait_for_selector("#campos-lote:not([disabled])")
    # ajuda do texto: pedidos simples e detalhados, e o texto vale para cada foto
    assert "uma de cada vez" in pg.locator("#prompt-dica").inner_text()
    pg.click(".exemplos-pedido summary")
    pw.expect(pg.locator(".exemplos-pedido li")).to_have_count(4)
    # foto de 300×200 para um vídeo horizontal de 832×480 (2,8× de ampliação): aviso, sem bloquear
    pequena = _salvar(tmp_path, "vitrine.png", _png_de(300, 200))
    grande = _salvar(tmp_path, "loja.png", _png_de(1600, 900, (200, 120, 60)))
    pg.set_input_files("#fotos", [str(pequena), str(grande)])
    aviso = pw.expect(pg.locator("#aviso-fotos"))
    aviso.to_be_visible()
    aviso.to_contain_text("1 foto tem baixa resolução para o vídeo de 832×480: vitrine.png (300×200)")
    pw.expect(pg.locator("#previa .miniatura.pequena")).to_have_count(1)
    pw.expect(pg.locator("#previa .miniatura.pequena")).to_have_attribute("aria-label", "vitrine.png, baixa resolução")
    # em 720p a mesma conta usa o vídeo de 1280×720
    pg.select_option("#qualidade", "alta")
    aviso.to_contain_text("vídeo de 1280×720")
    pg.click("#previa .miniatura.pequena .remover")
    aviso.to_be_hidden()
    pw.expect(pg.locator("#previa .miniatura")).to_have_count(1)
    ctx.close()

    # relatório de GPUs no painel: um pod que gerou e um que falhou na instalação
    b = ambiente.banco
    g = b.gerador_subindo("NVIDIA L40S")
    b.gerador_criado(g, "pod-abc123", "NVIDIA L40S", 1.09, "EU-RO-1")
    b.gerador_pronto(g, "pod-abc123", "NVIDIA L40S", 1.09)
    with b.tx() as c:
        c.run("UPDATE geradores SET pod_criado_em = pod_criado_em - 600, criado = criado - 600 WHERE id = ?", (g,))
    b.gerador_fim(g, "fila vazia")
    b.gerador_desligado(g)
    f = b.gerador_subindo("NVIDIA L40S")
    b.gerador_criado(f, "pod-xyz789", "NVIDIA L40S", 1.09, None)
    b.gerador_fim(f, "não subiu", falhou=True)
    b.gerador_desligado(f)
    adm_ctx = navegador.new_context()
    _com_sessao(adm_ctx, ambiente, admin=True)
    adm = _pagina(adm_ctx, erros)
    adm.goto(f"{ambiente.url}{PAINEL}/")
    adm.wait_for_selector("#gpus table")
    tabela = _texto(adm, "#gpus table")
    assert "pod-abc123" in tabela and "EU-RO-1" in tabela and "1,09" in tabela and "10 min" in tabela
    assert "falhou na subida: não subiu" in tabela
    resumo = _texto(adm, "#resumo-gpus")
    assert "GPUs ligadas\n2" in resumo and "Custo estimado\nUS$ 0,18" in resumo
    adm.click("#filtros-gpus button[data-dias='7']")
    pw.expect(adm.locator("#filtros-gpus button[data-dias='7']")).to_have_attribute("aria-pressed", "true")
    with adm.expect_download() as baixado:
        adm.click("#baixar-gpus")
    rel = json.loads(Path(baixado.value.path()).read_text())
    assert {x["pod"] for x in rel["geradores"]} == {"pod-abc123", "pod-xyz789"}
    _capturar(adm, "painel-gpus")
    adm_ctx.close()
    assert erros == [], erros


def test_purpose_screen_blocks_full_list_interest_and_choice(ambiente, navegador):  # noqa: F811 (fixture)
    erros: list[str] = []
    tok = _cliente(ambiente)
    ctx = navegador.new_context()
    _com_sessao(ctx, ambiente, tok)
    pg = _pagina(ctx, erros)
    pg.goto(ambiente.url)
    # antes do pedido: 6 opções em destaque e "Ver todas"; só "Foto vira vídeo" disponível
    blocos = pg.locator("#blocos > li")
    pw.expect(blocos).to_have_count(7)
    assert pg.locator("#form-lote").is_hidden()
    disponivel = pg.locator("#blocos [data-foco='finalidade:imagem-video']")
    pw.expect(disponivel).to_contain_text("Foto vira vídeo")
    pw.expect(disponivel).to_contain_text("Disponível · Wan 2.2")
    pw.expect(pg.locator("#blocos .bloco.em-breve")).to_have_count(5)
    texto_video = pg.locator("#blocos [data-finalidade='texto-video']")
    pw.expect(texto_video).to_contain_text("Em breve")
    # "Tenho interesse": fica registrado e no lugar do botão aparece a confirmação, que recebe o foco
    texto_video.get_by_role("button", name="Tenho interesse em Vídeo a partir de um texto").click()
    pw.expect(texto_video.locator(".interesse-ok")).to_have_text("Interesse registrado")
    pw.expect(texto_video.locator(".interesse-ok")).to_be_focused()
    # "Ver todas": todas as opções, agrupadas pelo que o cliente envia
    pg.click("#blocos [data-foco='finalidade:todas']")
    dialogo = pg.locator("dialog.todas-finalidades[open]")
    pw.expect(dialogo.locator(".opcao")).to_have_count(12)
    pw.expect(dialogo.locator(".grupo-opcoes h3")).to_have_text(
        ["A partir de uma foto", "A partir de um texto", "A partir de um vídeo", "A partir de um áudio"]
    )
    pw.expect(dialogo.locator("[data-finalidade='texto-video'] .interesse-ok")).to_be_visible()
    pw.expect(dialogo.locator("[data-finalidade='imagem-video']")).to_contain_text("Wan 2.2: melhor para")
    pw.expect(dialogo.locator("[data-finalidade='texto-video']")).to_contain_text(
        "Modelos previstos: Wan 2.2 (texto para vídeo)."
    )
    dialogo.get_by_role("button", name="Tenho interesse em Trilha sonora a partir de um texto").click()
    pw.expect(dialogo.locator("[data-finalidade='texto-musica'] .interesse-ok")).to_be_visible()
    dialogo.get_by_role("button", name="Usar Foto vira vídeo").click()
    # escolhida: o modelo e para que ele é melhor, e o formulário do pedido
    pw.expect(pg.locator("#form-lote")).to_be_visible()
    pw.expect(pg.locator("#escolhida-titulo")).to_have_text("Foto vira vídeo")
    pw.expect(pg.locator("#escolhida-titulo")).to_be_focused()
    pw.expect(pg.locator("#escolhida-tipo")).to_have_text("Imagem para vídeo · modelo Wan 2.2")
    pw.expect(pg.locator("#modelo-uso")).to_contain_text("Wan 2.2 é melhor para: produto parado")
    assert pg.locator("#modelo option").all_inner_texts() == ["Wan 2.2 · imagem para vídeo · 14B"]
    pg.click("#trocar-finalidade")
    pw.expect(pg.locator("#blocos")).to_be_visible()
    assert pg.locator("#form-lote").is_hidden()
    pw.expect(disponivel).to_be_focused()
    # outro dia: o interesse continua marcado
    pg.reload()
    pw.expect(pg.locator("#blocos [data-finalidade='texto-video'] .interesse-ok")).to_be_visible()
    with ambiente.banco.tx() as c:
        assert sorted(r["finalidade"] for r in c.all("SELECT finalidade FROM interesses")) == [
            "texto-musica",
            "texto-video",
        ]
    _capturar(pg, "finalidades")
    ctx.close()
    # o dono vê o total por opção e cada clique no painel
    adm_ctx = navegador.new_context()
    _com_sessao(adm_ctx, ambiente, admin=True)
    adm = _pagina(adm_ctx, erros)
    adm.goto(f"{ambiente.url}{PAINEL}/")
    adm.wait_for_selector("#interesses table")
    tabela = _texto(adm, "#interesses table")
    assert "Vídeo a partir de um texto" in tabela and "Trilha sonora a partir de um texto" in tabela
    pw.expect(adm.locator("#cliques tbody tr")).to_have_count(2)
    adm_ctx.close()
    assert erros == [], erros


def test_stored_xss_payloads_show_as_text_in_the_owner_panel(ambiente, navegador):  # noqa: F811 (fixture)
    """Teste de invasão (docs/13): HTML e JavaScript no nome da conta, no texto do pedido e numa mensagem de
    suporte aparecem como texto no painel do dono; nada executa e a CSP não precisa nem bloquear."""
    erros: list[str] = []
    carga = '<img src=x onerror="window.__xss=1"><script>window.__xss=2</script><svg onload="window.__xss=3">'
    _, tok = ambiente.banco.criar_cliente(carga, 500, "teste")
    _lote(ambiente, tok, {"vitrine.png": _png((40, 90, 160), "circulo")}, prompt="a câmera se aproxima " + carga)
    _api(ambiente, "POST", "/api/suporte", tok, json={"mensagem": "preciso de ajuda " + carga, "email": "x@gmail.com"})
    ctx = navegador.new_context()
    _com_sessao(ctx, ambiente, admin=True)
    adm = _pagina(ctx, erros)
    dialogos: list[str] = []
    adm.on("dialog", lambda d: (dialogos.append(d.message), d.dismiss()))
    adm.goto(f"{ambiente.url}{PAINEL}/")
    pw.expect(adm.locator("#n-pendentes")).to_have_text("1")
    pw.expect(adm.locator("#pendentes")).to_contain_text(carga)
    adm.wait_for_selector("#suporte .msg-suporte")
    pw.expect(adm.locator("#suporte")).to_contain_text(carga)
    assert adm.locator("#pendentes img[src='x'], #suporte img[src='x'], svg[onload]").count() == 0
    assert adm.evaluate("window.__xss === undefined") and dialogos == []
    ctx.close()
    assert erros == [], erros  # nem um aviso da CSP: o texto nunca virou HTML


# -- topo por sessão, índice das páginas de texto e estabilidade do layout (docs/10, rodada de 10/10) ----


VISIVEIS_NO_HTML = (  # o que já está na tela antes de qualquer resposta da API (lugar reservado, sem CLS)
    "document.addEventListener('DOMContentLoaded', () => { const v = (id) => document.getElementById(id)"
    "?.checkVisibility() ?? null; window.__html = {sair: v('sair'), conta: v('suporte-conta'),"
    " robo: v('verificacao-suporte') ?? v('verificacao-entrar')}; });"
)


def test_top_navigation_follows_the_session_and_logout_returns_home(ambiente, navegador):  # noqa: F811 (fixture)
    erros: list[str] = []
    nav = ".navegacao a"
    # quem não entrou: "Entrar" (leva ao formulário) e "Suporte"; nada de "Pedidos" nem "Sair"
    ctx = navegador.new_context()
    ctx.add_init_script(f"localStorage.setItem('render2go-cookies', {json.dumps(ESCOLHA_COOKIES)});")
    ctx.add_init_script(VISIVEIS_NO_HTML)
    pg = _pagina(ctx, erros)
    pg.goto(f"{ambiente.url}/termos")
    pw.expect(pg.locator(nav, has_text="Entrar")).to_be_visible()
    pw.expect(pg.locator(nav, has_text="Pedidos")).to_be_hidden()
    pw.expect(pg.locator("#sair")).to_be_hidden()
    pg.click(f"{nav}:has-text('Entrar')")
    pg.wait_for_url(f"{ambiente.url}/#entrar")
    pw.expect(pg.locator("#form-email")).to_be_visible()
    # o quadro do "não sou um robô" já tem o lugar no HTML (aqui ele está desligado e some depois da API)
    assert pg.evaluate("window.__html") == {"sair": False, "conta": None, "robo": True}
    pw.expect(pg.locator("#verificacao-entrar")).to_be_hidden()
    pg.click(f"{nav}:has-text('Entrar')")  # na própria entrada: o foco vai para o cartão, e o Tab, para o e-mail
    pw.expect(pg.locator("#entrar")).to_be_focused()
    pg.keyboard.press("Tab")
    pw.expect(pg.locator("#email")).to_be_focused()
    ctx.close()
    # quem entrou: "Pedidos" e "Sair" desde o 1º quadro, em qualquer página; "Sair" encerra a sessão e volta
    tok = _cliente(ambiente)
    ctx = navegador.new_context()
    _com_sessao(ctx, ambiente, tok, fixa=False)
    ctx.add_init_script(VISIVEIS_NO_HTML)
    pg = _pagina(ctx, erros)
    pg.goto(f"{ambiente.url}/privacidade")
    assert pg.evaluate("window.__html")["sair"]  # já no HTML, sem esperar o script da página (nada empurra o topo)
    pw.expect(pg.locator(nav, has_text="Pedidos")).to_be_visible()
    pw.expect(pg.locator(nav, has_text="Entrar")).to_be_hidden()
    # suporte de quem entrou: o aviso da conta já vem no HTML (o e-mail entra depois, na mesma linha), sem o quadro
    pg.goto(f"{ambiente.url}/suporte")
    assert pg.evaluate("window.__html") == {"sair": True, "conta": True, "robo": False}
    pw.expect(pg.locator("#suporte-conta")).to_contain_text("Você está conectado")  # (esta conta não tem e-mail)
    assert pg.locator("#suporte-email").get_attribute("required") is None
    with pg.expect_request(lambda r: r.url.endswith("/api/conta/sair") and r.method == "POST"):
        pg.click("#sair")
    pg.wait_for_url(f"{ambiente.url}/")
    pw.expect(pg.locator("#form-email")).to_be_visible()
    pw.expect(pg.locator("#sair")).to_be_hidden()
    assert pg.evaluate("localStorage.getItem('render2go-conectado')") is None
    assert ctx.request.get(f"{ambiente.url}/api/conta", headers=PAGINA).status == 401  # a sessão acabou no servidor
    ctx.close()
    assert erros == [], erros
    # marca antiga sem sessão (venceu ou saiu em outro aparelho): o suporte confere e volta ao topo de visitante
    ctx = navegador.new_context()
    ctx.add_init_script("localStorage.setItem('render2go-conectado', '1');")
    pg = ctx.new_page()
    pg.goto(f"{ambiente.url}/suporte")
    pw.expect(pg.locator(nav, has_text="Entrar")).to_be_visible()
    pw.expect(pg.locator("#sair")).to_be_hidden()
    assert pg.evaluate("localStorage.getItem('render2go-conectado')") is None
    assert pg.locator("#suporte-email").get_attribute("required") is not None
    ctx.close()


def test_privacy_index_links_every_section_and_marks_the_current_one(ambiente, navegador):  # noqa: F811 (fixture)
    erros: list[str] = []
    ctx = navegador.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    ctx.add_init_script(f"localStorage.setItem('render2go-cookies', {json.dumps(ESCOLHA_COOKIES)});")
    pg = _pagina(ctx, erros)
    pg.goto(f"{ambiente.url}/privacidade")
    alvos = pg.locator(".indice a").evaluate_all("(as) => as.map((a) => a.hash.slice(1))")
    assert len(alvos) == 10 and len(set(alvos)) == 10
    for alvo in alvos:  # cada item do índice leva a uma seção com título
        assert pg.locator(f"section#{alvo} > h2").count() == 1, alvo
    # computador: o índice fica fixo ao lado do texto e marca a seção que está na tela
    assert pg.locator(".indice").evaluate("(e) => getComputedStyle(e).position") == "sticky"
    pg.click(".indice a[href='#direitos']")
    pw.expect(pg.locator(".indice a[aria-current='location']")).to_have_attribute("href", "#direitos")
    pw.expect(pg.locator(".indice a[aria-current]")).to_have_count(1)
    pw.expect(pg.locator(".indice a[href='#direitos']")).to_be_visible()  # continua na tela depois da rolagem
    pg.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")  # no fim, a última seção
    pw.expect(pg.locator(".indice a[aria-current='location']")).to_have_attribute("href", "#mudancas")
    # impressão: só o texto (sem topo, índice, botões nem aviso de cookies)
    pg.emulate_media(media="print")
    assert pg.locator(".topo").is_hidden() and pg.locator(".indice").is_hidden()
    assert pg.locator("#preferencias-cookies").is_hidden() and pg.locator("h1").is_visible()
    pg.emulate_media(media="screen")
    # celular: o índice vem no começo do texto, sem ficar fixo
    pg.set_viewport_size({"width": 390, "height": 800})
    assert pg.locator(".indice").evaluate("(e) => getComputedStyle(e).position") == "static"
    ctx.close()
    assert erros == [], erros


CLS = """() => { window.__cls = 0; window.__deslocados = [];
  new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) { window.__cls += e.value;
    for (const s of e.sources || []) if (s.node) window.__deslocados.push(s.node.id || s.node.className); } })
  .observe({type: 'layout-shift', buffered: true}); }"""


def test_panel_does_not_shift_when_the_api_answers_after_the_first_paint(ambiente, navegador):  # noqa: F811
    """Core Web Vitals (CLS): com a API lenta, a página pinta o esqueleto e depois troca tudo de uma vez; nada
    do que já estava na tela (topo, título, "Sua conta", pedidos, rodapé) é empurrado."""
    tok = _cliente(ambiente)
    _lote(ambiente, tok, {"bolo.png": _png((40, 90, 160), "circulo")}, pronto=True)
    _lote(ambiente, tok, {"loja.png": _png((200, 120, 60), "barras")})

    def atrasar(rota):
        time.sleep(0.8)  # a 1ª pintura sai antes da resposta (o navegador continua enquanto o teste espera)
        rota.continue_()

    for largura, altura, movel in ((390, 844, True), (1280, 820, False)):
        ctx = navegador.new_context(viewport={"width": largura, "height": altura}, is_mobile=movel, has_touch=movel)
        _com_sessao(ctx, ambiente, tok)
        ctx.add_init_script(f"document.addEventListener('DOMContentLoaded', () => ({CLS})());")
        pg = ctx.new_page()
        pg.route("**/api/receitas", atrasar)
        pg.goto(ambiente.url)
        pg.wait_for_selector("#blocos [data-foco='finalidade:imagem-video']")
        pg.wait_for_selector("#lotes .lote")
        pg.unroute("**/api/receitas")
        assert pg.locator("#esqueleto-pedido").is_hidden() and pg.locator(".suporte-link").is_visible()
        pg.wait_for_timeout(300)
        cls = pg.evaluate("window.__cls")
        assert cls < 0.001, (largura, cls, pg.evaluate("window.__deslocados"))
        ctx.close()
    # com erro na API, o esqueleto sai e fica a mensagem (nada de carregamento sem fim)
    ctx = navegador.new_context()
    _com_sessao(ctx, ambiente, tok)
    pg = ctx.new_page()
    pg.route("**/api/receitas", lambda rota: rota.fulfill(status=503, json={"detail": "manutenção"}))
    pg.goto(ambiente.url)
    pg.wait_for_selector("#msg-painel:not([hidden])")
    assert pg.locator("#esqueleto-pedido").is_hidden() and pg.locator("#finalidades").is_hidden()
    ctx.close()
