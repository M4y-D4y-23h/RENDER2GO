"""Área do cliente no navegador (10/10/2026): o menu lateral e o endereço de cada tela, os extratos, os logs, o
perfil, as configurações (tema, e-mail, verificação em duas etapas, aparelhos, desativar e excluir), a entrada com o
aplicativo autenticador, o pedido em sequência (texto por foto) e o aceite dos termos novos; acessibilidade
(axe-core nos dois temas e no escuro escolhido na conta), celular e estabilidade do layout (CLS) de cada tela.
Precisa do pacote playwright e de um Chromium; senão, pula (como o test_paginas.py)."""

import json
import re
import time
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")
from test_api import PAINEL  # noqa: E402
from test_fila import _png, ambiente  # noqa: E402,F401 (mesma API local e armazenamento)
from test_paginas import (  # noqa: E402,F401
    AXE,
    CLS,
    ESCOLHA_COOKIES,
    PAGINA,
    _api,
    _axe,
    _capturar,
    _com_sessao,
    _escolher_finalidade,
    _lote,
    _pagina,
    _png_de,
    _salvar,
    _texto,
    _transbordando,
    navegador,
)

from render2go_servico import contas  # noqa: E402

TELAS = {
    "pedidos": "/",
    "modelos": "/modelos",
    "extratos": "/extratos",
    "logs": "/logs",
    "perfil": "/perfil",
    "configuracoes": "/configuracoes",
    "projetos": "/projetos",
    "mix": "/mix",
}
# o que cada tela mostra quando os dados chegaram (para esperar antes de medir)
PRONTA = {
    "pedidos": "#blocos [data-foco='finalidade:imagem-video']",
    "modelos": "#modelos-lista .cartao.modelo",
    "extratos": "#extratos-dados .numeros",
    "logs": "#logs-lista[aria-busy=false]",
    "perfil": "#interesse-indicacao button, #interesse-indicacao .interesse-ok",
    "configuracoes": "#aparelhos .aparelho",
    "projetos": "#interesse-projetos button, #interesse-projetos .interesse-ok",
    "mix": "#interesse-mix button, #interesse-mix .interesse-ok",
}


def _conta(amb, email: str | None = None, plano: str = "pago", saldo: str | None = "100") -> tuple[str, str]:
    """(id, código de acesso) de uma conta; com e-mail, a pessoa também entra pelo código que chega nele."""
    norm = contas.normalizar(email)[1] if email else None
    cid, tok = amb.banco.criar_cliente(email or "Café da Esquina", 500, plano, email=email, email_norm=norm)
    if plano == "pago" and saldo:
        _api(amb, "POST", f"{PAINEL}/api/clientes/{cid}/saldo", json={"valor": saldo, "descricao": "Pix"})
    return cid, tok


def _ultimo_email(amb) -> tuple[str, str, str]:
    """(para, assunto, código de 8 dígitos ou "") do último e-mail que a API mandaria."""
    amb.cfg.esperar_fundo()
    m = amb.enviados[-1]
    codigo = re.match(r"(\d{8}) ", m["Subject"])
    return m["To"], m["Subject"], codigo.group(1) if codigo else ""


def _entrar_pela_pagina(pg, amb, email: str) -> None:
    pg.fill("#email", email)
    pg.check("#aceite")
    pg.click("#enviar-email")
    pg.wait_for_selector("#form-codigo:not([hidden])")
    pg.fill("#codigo", _ultimo_email(amb)[2])
    pg.click("#enviar-codigo")


def _envelhecer(amb, cid: str) -> None:
    """A sessão do navegador deixa de contar como recente (aberta há mais de 10 min e sem confirmação)."""
    with amb.banco.tx() as c:
        c.run("UPDATE sessoes SET criado = criado - 1200, confirmada_ate = NULL WHERE cliente_id = ?", (cid,))


def _contexto(navegador, amb, tok, fixa=True, **kw):  # noqa: F811 (navegador: o navegador da fixture, como argumento)
    """Navegador que já entrou. fixa=False nos testes que saem da conta: a marca de "entrou" só na 1ª página."""
    ctx = navegador.new_context(**kw)
    _com_sessao(ctx, amb, tok, fixa=fixa)
    return ctx


def _sem_esperados(erros: list[str], *status: int) -> list[str]:
    """Os erros do console menos as recusas de propósito (ex.: 401 de um código errado)."""
    return [e for e in erros if not any(f"status of {s} " in e for s in status)]


# -- menu e endereços ------------------------------------------------------------------------------------------


def test_menu_opens_each_screen_by_address_without_reloading(ambiente, navegador):  # noqa: F811 (fixture)
    erros: list[str] = []
    _, tok = _conta(ambiente)
    ctx = _contexto(navegador, ambiente, tok, viewport={"width": 1280, "height": 800})
    # antes de qualquer script da página: a tela do endereço já marcada e só ela visível (nada pisca nem muda de lugar)
    ctx.add_init_script(
        "document.addEventListener('DOMContentLoaded', () => { window.__vista = document.documentElement.dataset.vista;"
        " window.__visiveis = [...document.querySelectorAll('.vista')].filter((v) => v.checkVisibility())"
        ".map((v) => v.id); });"
    )
    pg = _pagina(ctx, erros)
    pg.goto(f"{ambiente.url}/configuracoes")
    pg.wait_for_selector("#aparelhos .aparelho")
    assert pg.evaluate("[window.__vista, window.__visiveis]") == ["configuracoes", ["vista-configuracoes"]]
    assert pg.title() == "Configurações · Render2Go"
    pw.expect(pg.locator(".menu-conta a[aria-current=page]")).to_have_text("Configurações")
    assert pg.locator("#lateral").evaluate("(e) => getComputedStyle(e).position") == "sticky"
    # o menu troca a tela sem recarregar: o endereço e o título da aba mudam, e o foco vai para o título da tela
    pg.evaluate("window.__mesma = true")
    for nome, caminho in TELAS.items():
        pg.click(f".menu-conta a[data-vista={nome}]")
        pg.wait_for_url(f"{ambiente.url}{caminho}")
        pw.expect(pg.locator(f"#vista-{nome}")).to_be_visible()
        assert pg.locator(".vista:visible").count() == 1, nome
        pw.expect(pg.locator(f"#vista-{nome} h1")).to_be_focused()
        pw.expect(pg.locator(".menu-conta a[aria-current=page]")).to_have_attribute("data-vista", nome)
    assert pg.evaluate("window.__mesma") is True  # a página não recarregou
    assert pg.title() == "Mix · Render2Go"
    # voltar e avançar do navegador
    pg.go_back()
    pg.wait_for_url(f"{ambiente.url}/projetos")
    pw.expect(pg.locator("#vista-projetos")).to_be_visible()
    pw.expect(pg.locator("#vista-projetos h1")).to_be_focused()
    pg.go_forward()
    pw.expect(pg.locator("#vista-mix")).to_be_visible()
    assert pg.evaluate("window.__mesma") is True
    # Ctrl+clique abre a tela noutra aba (os itens do menu são links de verdade); esta aba fica onde estava
    with ctx.expect_page() as nova:
        pg.click(".menu-conta a[data-vista=logs]", modifiers=["Control"])
    nova.value.wait_for_load_state()
    assert nova.value.url == f"{ambiente.url}/logs"
    pw.expect(pg.locator("#vista-mix")).to_be_visible()
    # links dentro de uma tela também trocam de tela (no Mix, "Modelos de IA")
    pg.click("#vista-mix a[data-vista=modelos]")
    pg.wait_for_url(f"{ambiente.url}/modelos")
    pw.expect(pg.locator("#vista-modelos .cartao.modelo").first).to_be_visible()
    ctx.close()
    # quem não entrou e abre uma tela vê a entrada; ao entrar, a área já abre nela
    ctx = navegador.new_context()
    ctx.add_init_script(f"localStorage.setItem('render2go-cookies', {json.dumps(ESCOLHA_COOKIES)});")
    pg = _pagina(ctx, erros)
    pg.goto(f"{ambiente.url}/extratos")
    pw.expect(pg.locator("#form-email")).to_be_visible()
    assert pg.locator("#vista-extratos").is_hidden() and pg.locator("#lateral").is_hidden()
    _entrar_pela_pagina(pg, ambiente, "visita.extratos@gmail.com")
    pw.expect(pg.locator("#vista-extratos .numeros")).to_be_visible()
    pw.expect(pg.locator("#vista-extratos h1")).to_be_focused()
    assert pg.url == f"{ambiente.url}/extratos" and pg.title() == "Extratos · Render2Go"
    ctx.close()
    assert erros == [], erros


# -- extratos e logs -------------------------------------------------------------------------------------------


def test_statement_by_round_with_model_totals_period_csv_and_next_page(ambiente, navegador):  # noqa: F811
    erros: list[str] = []
    cid, tok = _conta(ambiente, "cafe.extrato@gmail.com")
    _lote(
        ambiente,
        tok,
        {"bolo.png": _png((40, 90, 160), "circulo"), "loja.png": _png((200, 120, 60), "barras")},
        pronto=True,
    )
    for i in range(55):  # a lista vem em páginas de 50
        _api(
            ambiente, "POST", f"{PAINEL}/api/clientes/{cid}/saldo", json={"valor": "1", "descricao": f"Pix {i + 1:02d}"}
        )
    ctx = _contexto(navegador, ambiente, tok, timezone_id="America/Sao_Paulo", accept_downloads=True)
    pg = _pagina(ctx, erros)
    pg.goto(f"{ambiente.url}/extratos")
    pg.wait_for_selector("#extratos-dados .numeros")
    numeros = _texto(pg, "#extratos-dados .numeros")
    for trecho in ("Saldo atual\nR$ 152,50", "Total gasto\nR$ 2,50", "Pedidos\n1", "Recargas\nR$ 155,00"):
        assert trecho in numeros, (trecho, numeros)
    # por modelo de IA: o tipo, os pedidos e o total (cobrado − devolvido)
    linha = _texto(pg, "#extratos-dados table tbody tr")
    assert linha.split("\t") == ["Wan 2.2", "Imagem para vídeo", "1", "R$ 2,50", "R$ 0,00", "R$ 2,50"], linha
    # rodadas e recargas, das mais novas para as mais velhas, 50 por vez
    itens = pg.locator("#extrato-itens > li")
    pw.expect(itens).to_have_count(50)
    pw.expect(itens.first).to_contain_text("Recarga · Pix 55")
    pg.click("#extrato-mais")
    pw.expect(itens).to_have_count(57)
    pw.expect(pg.locator("#extrato-mais")).to_have_count(0)  # acabou
    rodada = pg.locator("#extrato-itens > li.rodada")
    pw.expect(rodada).to_have_count(1)
    assert "Pedido · Wan 2.2 · Imagem para vídeo" in _texto(pg, "#extrato-itens > li.rodada")
    assert "2 vídeos de 5 s em 480p · cobrado R$ 2,50 · Pronto" in _texto(pg, "#extrato-itens > li.rodada")
    assert "−R$ 2,50" in _texto(pg, "#extrato-itens > li.rodada .linha-valor")
    # o período muda a lista e a planilha; a planilha sai no fuso deste aparelho
    pg.click("#extrato-periodo button[data-dias='7']")
    pw.expect(pg.locator("#extrato-periodo button[data-dias='7']")).to_have_attribute("aria-pressed", "true")
    assert pg.get_attribute("#extrato-csv", "href") == "/api/extrato.csv?dias=7&fuso_min=-180"
    pw.expect(itens).to_have_count(50)
    with pg.expect_download() as baixado:
        pg.click("#extrato-csv")
    planilha = Path(baixado.value.path()).read_text(encoding="utf-8-sig")
    assert planilha.startswith("Data;Tipo;Pedido;Tipo de modelo;Modelo de IA;Vídeos;Cobrado (R$)")
    assert ";Pedido;" in planilha and ";Imagem para vídeo;Wan 2.2;2;2,50;0,00;2,50;;" in planilha
    hora = time.strftime("%d/%m/%Y %H", time.gmtime(time.time() - 3 * 3600))  # horário de Brasília
    assert planilha.splitlines()[1].startswith(hora)
    _capturar(pg, "extratos")
    ctx.close()
    assert erros == [], erros


def test_generation_logs_watch_download_and_delete_with_confirmation(ambiente, navegador):  # noqa: F811
    erros: list[str] = []
    _, tok = _conta(ambiente)
    lid = _lote(
        ambiente,
        tok,
        {"bolo.png": _png((40, 90, 160), "circulo"), "loja.png": _png((200, 120, 60), "barras")},
        pronto=True,
    )
    ctx = _contexto(navegador, ambiente, tok)
    pg = _pagina(ctx, erros)
    pg.goto(f"{ambiente.url}/logs")
    itens = pg.locator("#logs-lista .geracao")
    pw.expect(itens).to_have_count(2)
    primeiro = itens.first
    pw.expect(primeiro).to_contain_text(
        re.compile(r"Wan 2\.2 · Foto vira vídeo · 5 s em 480p · gerado em \d\d/\d\d/\d{4}")
    )
    pw.expect(primeiro).to_contain_text("vapor sobe da xícara")
    nome = primeiro.locator(".nome").inner_text()
    assert "/saida/" in primeiro.locator("a.baixar").get_attribute("href")
    # assistir na própria página; Esc fecha e o foco volta
    primeiro.locator("button.assistir").click()
    player = pg.locator("dialog.player[open]")
    assert "/saida/" in player.locator("video").get_attribute("src")
    pg.keyboard.press("Escape")
    pw.expect(primeiro.locator("button.assistir")).to_be_focused()
    # excluir: pergunta antes; "Cancelar" não muda nada
    primeiro.get_by_role("button", name=f"Excluir {nome}").click()
    pg.click("dialog.confirmacao[open] button:has-text('Cancelar')")
    pw.expect(itens).to_have_count(2)
    primeiro.get_by_role("button", name=f"Excluir {nome}").click()
    pw.expect(pg.locator("dialog.confirmacao[open]")).to_contain_text("não dá para recuperar")
    pg.click("dialog.confirmacao[open] button:has-text('Excluir o vídeo')")
    pw.expect(itens).to_have_count(1)
    pw.expect(itens.first.locator("button.assistir")).to_be_focused()  # o foco vai para o vídeo que ficou
    lote = ambiente.banco.lote(lid)
    assert f"saida/{lote['cliente_id']}/{lid}/{nome}" not in ambiente.objetos
    with ambiente.banco.tx() as c:
        assert [r["arquivo"] for r in c.all("SELECT arquivo FROM jobs WHERE excluido_em IS NOT NULL")] == [nome]
    # o pedido continua na tela de pedidos, sem o link do vídeo excluído
    pg.click(".menu-conta a[data-vista=pedidos]")
    pw.expect(pg.locator("#lotes .lote")).to_contain_text("1 vídeo excluído por você.")
    pw.expect(pg.locator("#lotes .videos button.assistir")).to_have_count(1)
    ctx.close()
    assert erros == [], erros


# -- perfil ----------------------------------------------------------------------------------------------------


def test_profile_names_initial_and_gravatar_photo_only_by_choice(ambiente, navegador):  # noqa: F811
    erros: list[str] = []
    consultados: list[str] = []
    foto = _png_de(64, 64, (200, 60, 90))

    def gravatar(email):
        consultados.append(email)
        return (foto, "image/png") if email.startswith("com.foto") else None

    ambiente.cfg.baixar_gravatar = gravatar
    cid, tok = _conta(ambiente, "com.foto@gmail.com", plano="teste")
    ctx = _contexto(navegador, ambiente, tok)
    pg = _pagina(ctx, erros)
    pg.goto(f"{ambiente.url}/perfil")
    pw.expect(pg.locator("#perfil-email")).to_have_text("com.foto@gmail.com")
    pw.expect(pg.locator("#perfil-avatar")).to_have_text("C")  # sem foto: a inicial do e-mail
    assert "com-foto" not in pg.get_attribute("#perfil-avatar", "class") and consultados == []  # sem a escolha
    # nome: só letras, espaço, apóstrofo, hífen e ponto
    pg.fill("#perfil-primeiro", "M@ria")
    pg.click("#perfil-salvar")
    pw.expect(pg.locator("#perfil-msg")).to_contain_text("Nome: use só letras")
    assert pg.get_attribute("#perfil-primeiro", "aria-invalid") == "true"
    pg.fill("#perfil-primeiro", "Maria Júlia")
    pg.fill("#perfil-ultimo", "d’Ávila")
    pg.click("#perfil-salvar")
    pw.expect(pg.locator("#perfil-msg")).to_have_text("Perfil salvo.")
    pw.expect(pg.locator("#quem-nome")).to_have_text("Maria Júlia d’Ávila")  # o menu mostra o nome
    pw.expect(pg.locator("#avatar")).to_have_text("M")
    assert ambiente.banco.conta(cid)["primeiro_nome"] == "Maria Júlia"
    # Gravatar ligado: a foto vem do próprio site e entra por cima da inicial, no perfil e no menu
    pg.check("#perfil-gravatar")
    pw.expect(pg.locator("#perfil-avatar")).to_have_class(re.compile(r"\bcom-foto\b"))
    pw.expect(pg.locator("#avatar")).to_have_class(re.compile(r"\bcom-foto\b"))
    assert pg.get_attribute("#perfil-avatar img", "src").startswith("/api/conta/foto?v=")
    assert consultados == ["com.foto@gmail.com"]
    pg.reload()
    pw.expect(pg.locator("#avatar")).to_have_class(re.compile(r"\bcom-foto\b"))
    pg.uncheck("#perfil-gravatar")
    pw.expect(pg.locator("#perfil-avatar img")).to_have_count(0)
    pw.expect(pg.locator("#perfil-avatar")).to_have_text("M")
    # em breve: nome de usuário e ganhos por indicação, com "Tenho interesse"
    pg.get_by_role("button", name="Tenho interesse em Nome de usuário").click()
    pg.get_by_role("button", name="Tenho interesse em Ganhos por indicação").click()
    pw.expect(pg.locator("#vista-perfil .interesse-ok")).to_have_count(2)
    with ambiente.banco.tx() as c:
        assert sorted(r["finalidade"] for r in c.all("SELECT finalidade FROM interesses")) == [
            "recurso:indicacao",
            "recurso:nome-usuario",
        ]
    _capturar(pg, "perfil")
    ctx.close()
    # com o Gravatar ligado mas sem foto lá: fica a inicial, sem erro nenhum no console
    _, tok = _conta(ambiente, "sem.foto@gmail.com", plano="teste")
    ctx = _contexto(navegador, ambiente, tok)
    pg = _pagina(ctx, erros)
    pg.goto(f"{ambiente.url}/perfil")
    pg.check("#perfil-gravatar")
    pw.expect(pg.locator("#perfil-avatar img")).to_have_count(0)  # sem foto: a imagem sai, a inicial fica
    pw.expect(pg.locator("#perfil-avatar")).to_have_text("S")
    ctx.close()
    assert erros == [], erros


# -- configurações ---------------------------------------------------------------------------------------------


def test_theme_choice_applies_before_paint_on_every_page_and_follows_the_account(ambiente, navegador):  # noqa: F811
    erros: list[str] = []
    cid, tok = _conta(ambiente, "tema.escuro@gmail.com", plano="teste")
    antes = (
        "document.addEventListener('DOMContentLoaded', () => { window.__tema = document.documentElement.dataset.tema"
        " ?? null; window.__fundo = getComputedStyle(document.body).backgroundColor; });"
    )
    ctx = _contexto(navegador, ambiente, tok, color_scheme="light")
    ctx.add_init_script(antes)
    pg = _pagina(ctx, erros)
    pg.goto(f"{ambiente.url}/configuracoes")
    pw.expect(pg.locator("#tema-auto")).to_be_checked()
    assert pg.locator(".logo-claro img").is_visible() and pg.locator(".logo-escuro img").is_hidden()
    pg.check("#tema-escuro")
    assert pg.evaluate("document.documentElement.dataset.tema") == "escuro"
    assert pg.evaluate("localStorage.getItem('render2go-tema')") == "escuro"
    assert pg.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(15, 14, 23)"
    assert pg.locator(".logo-escuro img").is_visible() and pg.locator(".logo-claro img").is_hidden()
    assert (
        pg.evaluate("[...document.querySelectorAll('meta[name=theme-color]')].map((m) => m.content)") == ["#0f0e17"] * 2
    )
    for _ in range(50):  # a conta guarda a escolha (vale em todos os aparelhos)
        if ambiente.banco.conta(cid)["tema"] == "escuro":
            break
        time.sleep(0.1)
    assert ambiente.banco.conta(cid)["tema"] == "escuro"
    # as outras páginas já abrem no tema escolhido, antes de desenhar (sem piscar o claro)
    pg.goto(f"{ambiente.url}/suporte")
    assert pg.evaluate("[window.__tema, window.__fundo]") == ["escuro", "rgb(15, 14, 23)"]
    pg.goto(f"{ambiente.url}/termos")
    assert pg.evaluate("window.__tema") == "escuro"
    # outro aparelho (nada guardado nele): a conta acerta o tema assim que chega
    outro = _contexto(navegador, ambiente, tok, color_scheme="light")
    pg2 = _pagina(outro, erros)
    pg2.goto(ambiente.url)
    pg2.wait_for_function("document.documentElement.dataset.tema === 'escuro'")
    assert pg2.evaluate("localStorage.getItem('render2go-tema')") == "escuro"
    outro.close()
    # automático: volta a seguir o aparelho
    pg.goto(f"{ambiente.url}/configuracoes")
    pg.check("#tema-auto")
    assert pg.evaluate("document.documentElement.dataset.tema ?? null") is None
    assert pg.evaluate("localStorage.getItem('render2go-tema')") is None
    for _ in range(50):
        if ambiente.banco.conta(cid)["tema"] is None:
            break
        time.sleep(0.1)
    assert ambiente.banco.conta(cid)["tema"] is None
    ctx.close()
    assert erros == [], erros


def test_email_change_confirms_who_you_are_and_then_the_new_address(ambiente, navegador):  # noqa: F811
    erros: list[str] = []
    cid, tok = _conta(ambiente, "antigo.email@gmail.com", plano="teste")
    ctx = _contexto(navegador, ambiente, tok)
    pg = _pagina(ctx, erros)
    _envelhecer(ambiente, cid)  # a sessão já não conta como recente: a troca pede "confirme que é você"
    pg.goto(f"{ambiente.url}/configuracoes")
    pw.expect(pg.locator("#config-email-atual")).to_have_text("antigo.email@gmail.com")
    pg.click("#config-trocar-email")
    pw.expect(pg.locator("#email-novo")).to_be_focused()
    pg.fill("#email-novo", "novo.email@gmail.com")
    pg.click("#email-enviar")
    dialogo = pg.locator("dialog.confirmar-identidade[open]")
    pw.expect(dialogo).to_contain_text("enviamos um código de 8 dígitos para antigo.email@gmail.com")
    para, assunto, codigo = _ultimo_email(ambiente)
    assert para == "antigo.email@gmail.com" and "confirmar uma alteração" in assunto
    dialogo.locator("input").fill("00000000")
    dialogo.locator("button:has-text('Confirmar')").click()
    pw.expect(dialogo.locator(".aviso.erro")).to_contain_text(re.compile("código inválido", re.I))
    dialogo.locator("input").fill(codigo)
    dialogo.locator("button:has-text('Confirmar')").click()
    pw.expect(dialogo).to_have_count(0)
    # confirmado: o código vai para o endereço novo
    pw.expect(pg.locator("#email-msg")).to_contain_text("Enviamos um código de 8 dígitos para novo.email@gmail.com")
    para, _, codigo = _ultimo_email(ambiente)
    assert para == "novo.email@gmail.com"
    pg.fill("#email-novo-codigo", codigo)
    pg.click("#email-confirmar")
    pw.expect(pg.locator("#email-msg")).to_contain_text("o e-mail da conta agora é novo.email@gmail.com")
    pw.expect(pg.locator("#config-email-atual")).to_have_text("novo.email@gmail.com")
    pw.expect(pg.locator("#quem-nome")).to_have_text("novo.email@gmail.com")
    assert ambiente.banco.conta(cid)["email"] == "novo.email@gmail.com"
    para, assunto, _ = _ultimo_email(ambiente)  # o endereço antigo recebe o aviso
    assert para == "antigo.email@gmail.com" and assunto.startswith("Render2Go: ")
    ctx.close()
    assert _sem_esperados(erros, 401, 403) == [], erros  # 403: "confirme que é você"; 401: o código errado


def test_two_step_verification_with_qr_and_recovery_codes_then_login_asks_the_app(ambiente, navegador):  # noqa: F811
    erros: list[str] = []
    email = "duas.etapas@gmail.com"
    cid, tok = _conta(ambiente, email, plano="teste")
    ctx = _contexto(navegador, ambiente, tok, fixa=False)
    pg = _pagina(ctx, erros)
    pg.goto(f"{ambiente.url}/configuracoes")
    pw.expect(pg.locator("#mfa-estado")).to_have_text("Desligada: a entrada pede só o código do e-mail.")
    pg.click("#mfa-ligar")  # a sessão acabou de abrir: vale como confirmação recente
    d = pg.locator("dialog.mfa[open]")
    qr = d.locator("img.qr-mfa")
    pw.expect(qr).to_be_visible()
    assert qr.get_attribute("src").startswith("blob:") and qr.evaluate("(i) => i.complete && i.naturalWidth > 0")
    campo = d.locator("input[autocomplete=one-time-code]")
    pw.expect(campo).to_be_focused()  # a janela abre no campo do código
    d.locator(".chave-manual summary").click()
    segredo = d.locator(".chave-manual code").inner_text().replace(" ", "")
    assert re.fullmatch(r"[A-Z2-7]{16,}", segredo)
    agora = int(time.time() // 30)
    errado = next(
        f"{n:06d}" for n in range(1_000_000) if f"{n:06d}" not in {contas.totp(segredo, agora + k) for k in (-1, 0, 1)}
    )
    campo.fill(errado)
    d.locator("button:has-text('Ligar')").click()
    pw.expect(d.locator(".aviso.erro")).to_contain_text(re.compile("o código não confere", re.I))
    campo.fill(contas.totp(segredo, agora))
    d.locator("button:has-text('Ligar')").click()
    codigos = d.locator(".codigos-recuperacao code")
    pw.expect(codigos).to_have_count(10)
    lista = codigos.all_inner_texts()
    assert all(re.fullmatch(r"[a-z2-9]{5}-[a-z2-9]{5}", k) for k in lista), lista
    concluir = d.locator("button:has-text('Concluir')")
    assert concluir.is_disabled()  # só depois de "Guardei os códigos"
    d.locator("label.marcacao").click()
    concluir.click()
    pw.expect(pg.locator("#mfa-estado")).to_contain_text("Ligada desde")
    pw.expect(pg.locator("#mfa-estado")).to_contain_text("10 códigos de recuperação ainda valem")
    pw.expect(pg.locator("#mfa-ligar")).to_be_hidden()
    pw.expect(pg.locator("#mfa-desligar")).to_be_visible()
    assert ambiente.banco.conta(cid)["mfa_ativo_em"]
    # sair e entrar de novo: depois do código do e-mail, o do aplicativo (o mesmo código não vale duas vezes)
    pg.click("#sair")
    pg.wait_for_selector("#form-email:visible")
    _entrar_pela_pagina(pg, ambiente, email)
    pw.expect(pg.locator("#form-mfa")).to_be_visible()
    pw.expect(pg.locator("#codigo-mfa")).to_be_focused()
    assert pg.locator("#codigo-antigo").is_hidden()
    pg.fill("#codigo-mfa", contas.totp(segredo, agora))
    pg.click("#enviar-mfa")
    pw.expect(pg.locator("#msg-entrada")).to_contain_text(re.compile("código inválido", re.I))
    pg.fill("#codigo-mfa", contas.totp(segredo, agora + 1))
    pg.click("#enviar-mfa")
    pw.expect(pg.locator("#vista-pedidos h1")).to_be_focused()
    # perdeu o celular: um código de recuperação vale uma vez (e o e-mail avisa)
    pg.click("#sair")
    pg.wait_for_selector("#form-email:visible")
    _entrar_pela_pagina(pg, ambiente, email)
    pg.click("#mfa-recuperacao")
    pw.expect(pg.locator("#rotulo-mfa")).to_have_text("Código de recuperação")
    pg.fill("#codigo-mfa", lista[0].upper())
    pg.click("#enviar-mfa")
    pw.expect(pg.locator("#vista-pedidos h1")).to_be_focused()
    assert "código de recuperação" in _ultimo_email(ambiente)[1]
    pg.click("#sair")
    pg.wait_for_selector("#form-email:visible")
    _entrar_pela_pagina(pg, ambiente, email)
    pg.click("#mfa-recuperacao")
    pg.fill("#codigo-mfa", lista[0])
    pg.click("#enviar-mfa")
    pw.expect(pg.locator("#msg-entrada")).to_contain_text(re.compile("código inválido", re.I))
    _capturar(pg, "entrada-mfa")
    ctx.close()
    assert _sem_esperados(erros, 401) == [], erros


def test_connected_devices_end_another_session_and_this_one(ambiente, navegador):  # noqa: F811
    import requests

    erros: list[str] = []
    _, tok = _conta(ambiente, "dois.aparelhos@gmail.com", plano="teste")
    android = (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko)"
        " Chrome/126.0 Mobile Safari/537.36"
    )
    outro = requests.post(
        f"{ambiente.url}/api/conta/entrar-codigo",
        json={"codigo": tok, "aceite": True},
        headers={**PAGINA, "User-Agent": android},
        timeout=10,
    )
    assert outro.ok
    ctx = _contexto(navegador, ambiente, tok, fixa=False)
    pg = _pagina(ctx, erros)
    pg.goto(f"{ambiente.url}/configuracoes")
    itens = pg.locator("#aparelhos .aparelho")
    pw.expect(itens).to_have_count(2)
    pw.expect(itens.filter(has_text="Este aparelho")).to_have_count(1)
    celular = itens.filter(has_text="Chrome no Android")
    pw.expect(celular).to_contain_text("IP 127.0.0.1")
    celular.get_by_role("button", name="Encerrar a sessão de Chrome no Android").click()
    pw.expect(itens).to_have_count(1)
    assert (
        requests.get(f"{ambiente.url}/api/conta", cookies=outro.cookies, headers=PAGINA, timeout=10).status_code == 401
    )
    pw.expect(pg.locator("#encerrar-outras")).to_be_disabled()
    # "Sair deste aparelho": pergunta e volta para a entrada
    itens.first.get_by_role("button", name="Sair deste aparelho").click()
    pg.click("dialog.confirmacao[open] button:has-text('Sair')")
    pg.wait_for_url(f"{ambiente.url}/")
    pw.expect(pg.locator("#form-email")).to_be_visible()
    ctx.close()
    assert erros == [], erros


def test_pause_then_login_reactivates_and_delete_needs_the_word_and_confirmation(ambiente, navegador):  # noqa: F811
    erros: list[str] = []
    email = "pausa.conta@gmail.com"
    cid, tok = _conta(ambiente, email, plano="teste")
    ctx = _contexto(navegador, ambiente, tok, fixa=False)
    pg = _pagina(ctx, erros)
    pg.goto(f"{ambiente.url}/configuracoes")
    pg.click("#desativar-conta")
    pw.expect(pg.locator("dialog.confirmacao[open]")).to_contain_text(
        "O saldo, os pedidos e o histórico ficam guardados"
    )
    pg.click("dialog.confirmacao[open] button:has-text('Desativar a conta')")
    pg.wait_for_url(f"{ambiente.url}/")
    pw.expect(pg.locator("#msg-entrada")).to_contain_text("Conta desativada")
    assert ambiente.banco.conta(cid)["pausada_em"]
    # entrar de novo pelo código do e-mail reativa a conta
    _entrar_pela_pagina(pg, ambiente, email)
    pw.expect(pg.locator("#aviso-conta")).to_contain_text("Sua conta estava desativada e voltou a valer")
    assert ambiente.banco.conta(cid)["pausada_em"] is None
    # excluir: a palavra libera o botão, e a sessão antiga pede "confirme que é você"
    _envelhecer(ambiente, cid)
    pg.goto(f"{ambiente.url}/configuracoes")
    pg.click("#excluir-conta")
    d = pg.locator("dialog.confirmacao[open]")
    botao = d.locator("button:has-text('Excluir para sempre')")
    pw.expect(botao).to_be_disabled()
    pw.expect(d.locator("input")).to_be_focused()
    d.locator("input").fill("excluir")
    pw.expect(botao).to_be_enabled()
    botao.click()
    confirmar = pg.locator("dialog.confirmar-identidade[open]")
    pw.expect(confirmar).to_contain_text(email)
    confirmar.locator("input").fill(_ultimo_email(ambiente)[2])
    confirmar.locator("button:has-text('Confirmar')").click()
    pg.wait_for_url(f"{ambiente.url}/")
    pw.expect(pg.locator("#msg-entrada")).to_contain_text("Conta excluída")
    conta = ambiente.banco.conta(cid)
    assert conta["excluido_em"] and conta["email"] is None
    ctx.close()
    assert _sem_esperados(erros, 403) == [], erros


# -- pedido em sequência -------------------------------------------------------------------------------------


def test_sequence_order_gives_some_photos_their_own_text(ambiente, navegador, tmp_path):  # noqa: F811
    erros: list[str] = []
    _, tok = _conta(ambiente)
    ctx = _contexto(navegador, ambiente, tok)
    pg = _pagina(ctx, erros)
    pg.goto(ambiente.url)
    _escolher_finalidade(pg)
    arquivos = [_salvar(tmp_path, f"{n}.png", _png((40 * i, 90, 160), "circulo")) for i, n in enumerate("abc", 1)]
    pg.set_input_files("#fotos", [str(a) for a in arquivos])
    pw.expect(pg.locator("#previa .miniatura :is(canvas, img)")).to_have_count(3)
    # a aba "Sequência" (também pelo teclado, com as setas)
    pg.focus("#aba-mesmo")
    pg.keyboard.press("ArrowRight")
    pw.expect(pg.locator("#aba-sequencia")).to_be_focused()
    pw.expect(pg.locator("#aba-sequencia")).to_have_attribute("aria-selected", "true")
    pw.expect(pg.locator("#painel-texto")).to_have_attribute("aria-labelledby", "aba-sequencia")
    pw.expect(pg.locator("#rotulo-prompt")).to_have_text("Texto padrão")
    pw.expect(pg.locator("#seq-grade img[src^='blob:']")).to_have_count(3)  # miniaturas pequenas, feitas aqui
    pw.expect(pg.locator("#seq-contagem")).to_have_text("Nenhuma foto com texto próprio: as 3 seguem o texto padrão.")
    # marca a foto b e escreve o texto próprio dela
    pg.check("#seq-grade li:has-text('b.png') input[type=checkbox]")
    pw.expect(pg.locator("#seq-marcadas")).to_have_text("1 foto marcada")
    pg.click("#seq-escrever")
    janela = pg.locator("dialog.texto-proprio[open]")
    pw.expect(janela.locator("h2")).to_have_text("Texto próprio de b.png")
    janela.locator("textarea").fill("oi")
    janela.locator("button:has-text('Salvar o texto')").click()
    pw.expect(janela.locator(".aviso.erro")).to_contain_text("pelo menos 3 letras")
    janela.locator("textarea").fill("o café vira um café com leite")
    janela.locator("button:has-text('Salvar o texto')").click()
    pw.expect(janela).to_have_count(0)
    foto_b = pg.locator("#seq-grade li:has-text('b.png')")
    pw.expect(foto_b).to_have_class(re.compile(r"\btem-texto\b"))
    pw.expect(foto_b.locator(".seq-texto")).to_have_text("Texto próprio: o café vira um café com leite")
    pw.expect(foto_b.locator("input[type=checkbox]")).to_be_focused()
    pw.expect(pg.locator("#seq-contagem")).to_have_text(
        "1 foto com texto próprio; as outras 2 seguem o texto padrão · 1 texto diferente (máximo 100)"
    )
    pg.click("#seq-filtros button[data-filtro=com]")
    pw.expect(pg.locator("#seq-grade .seq-foto")).to_have_count(1)
    pg.click("#seq-filtros button[data-filtro=todas]")
    pw.expect(pg.locator("#seq-grade .seq-foto")).to_have_count(3)
    # na outra aba os textos ficam guardados, e todas as fotos seguem o texto único
    pg.click("#aba-mesmo")
    pw.expect(pg.locator("#nota-textos")).to_contain_text("1 foto tem texto próprio na aba “Sequência”")
    pw.expect(pg.locator("#sequencia")).to_be_hidden()
    pg.click("#aba-sequencia")
    # antes de enviar, a revisão dos textos; "Voltar e editar" não cria nada
    pg.fill("#prompt", "apresente o cardápio com um movimento suave")
    pg.click("#enviar")
    revisao = pg.locator("dialog.revisao-textos[open]")
    pw.expect(revisao).to_contain_text("2 fotos seguem este texto:")
    pw.expect(revisao.locator(".texto-padrao")).to_have_text("apresente o cardápio com um movimento suave")
    pw.expect(revisao.locator(".lista-textos li")).to_have_text(["o café vira um café com leite1 foto: b.png"])
    _capturar(pg, "sequencia-revisao")
    revisao.locator("button:has-text('Voltar e editar')").click()
    assert ambiente.banco.lotes() == []
    pg.click("#enviar")
    pg.locator("dialog.revisao-textos[open] button:has-text('Enviar pedido')").click()
    pg.wait_for_selector("#msg.ok", timeout=15000)
    (lote,) = ambiente.banco.lotes()
    assert {f["nome"]: f["prompt"] for f in ambiente.banco.fotos(lote["id"])} == {
        "a": None,
        "b": "o café vira um café com leite",
        "c": None,
    }
    assert json.loads(lote["pedido"])["textos_por_foto"] == 1
    pw.expect(pg.locator("#lotes .lote").first).to_contain_text(
        "Pedido em sequência: 1 texto próprio além do texto padrão."
    )
    pw.expect(pg.locator("#seq-contagem")).to_contain_text("Escolha as fotos no passo 1")  # o pedido saiu: tudo limpo
    ctx.close()
    assert erros == [], erros


def test_sequence_grid_pages_through_many_photos(ambiente, navegador, tmp_path):  # noqa: F811
    erros: list[str] = []
    _, tok = _conta(ambiente)
    ctx = _contexto(navegador, ambiente, tok)
    pg = _pagina(ctx, erros)
    pg.goto(ambiente.url)
    _escolher_finalidade(pg)
    pg.click("#aba-sequencia")
    pw.expect(pg.locator("#seq-contagem")).to_contain_text("Escolha as fotos no passo 1")
    muitas = [_salvar(tmp_path, f"f{i:02d}.png", _png_de(64, 48, (i * 5, 90, 160))) for i in range(45)]
    pg.set_input_files("#fotos", [str(f) for f in muitas])
    pw.expect(pg.locator("#seq-grade .seq-foto")).to_have_count(40)  # 40 por página
    pw.expect(pg.locator("#seq-pagina")).to_have_text("Página 1 de 2")
    pg.click("#seq-marcar-pagina")
    pw.expect(pg.locator("#seq-marcadas")).to_have_text("40 fotos marcadas")
    pw.expect(pg.locator("#seq-marcar-pagina")).to_have_text("Desmarcar as desta página")
    pg.click("#seq-proxima")
    pw.expect(pg.locator("#seq-grade .seq-foto")).to_have_count(5)
    pw.expect(pg.locator("#seq-pagina")).to_have_text("Página 2 de 2")
    pw.expect(pg.locator("#seq-proxima")).to_be_disabled()
    pg.click("#seq-marcar-pagina")
    pw.expect(pg.locator("#seq-marcadas")).to_have_text("45 fotos marcadas")
    pg.click("#seq-escrever")
    pg.locator("dialog.texto-proprio[open] textarea").fill("a câmera se aproxima devagar do prato")
    pg.click("dialog.texto-proprio[open] button:has-text('Salvar o texto')")
    pw.expect(pg.locator("#seq-contagem")).to_have_text(
        "45 fotos com texto próprio; nenhuma segue o texto padrão · 1 texto diferente (máximo 100)"
    )
    # tirar uma foto no passo 1 também tira o texto próprio dela
    pg.click("#previa .miniatura[title='f00.png'] .remover")
    pw.expect(pg.locator("#seq-contagem")).to_contain_text("44 fotos com texto próprio")
    pg.click("#limpar")
    pw.expect(pg.locator("#seq-contagem")).to_contain_text("Escolha as fotos no passo 1")
    ctx.close()
    assert erros == [], erros


# -- termos novos ----------------------------------------------------------------------------------------------


def test_new_terms_version_is_accepted_before_the_next_order(ambiente, navegador, tmp_path):  # noqa: F811
    erros: list[str] = []
    cid, tok = _conta(ambiente)
    ctx = _contexto(navegador, ambiente, tok)
    pg = _pagina(ctx, erros)
    with ambiente.banco.tx() as c:  # a sessão foi aberta antes da versão nova dos documentos
        c.run(
            "UPDATE clientes SET termos_versao = 'termos-v1', privacidade_versao = 'privacidade-v1' WHERE id = ?",
            (cid,),
        )
    pg.goto(ambiente.url)
    janela = pg.locator("dialog.aceite-documentos[open]")
    pw.expect(janela).to_be_visible()
    pw.expect(janela.locator("a[href='/termos']")).to_have_attribute("target", "_blank")
    pw.expect(janela.locator("a[href='/privacidade']")).to_be_visible()
    pw.expect(janela.locator("button:has-text('Aceitar e continuar')")).to_be_disabled()
    janela.locator("button:has-text('Agora não')").click()
    # o pedido pede o aceite antes de seguir (428), e segue sozinho depois dele
    _escolher_finalidade(pg)
    pg.set_input_files("#fotos", [str(_salvar(tmp_path, "a.png", _png((40, 90, 160), "circulo")))])
    pg.fill("#prompt", "a câmera se aproxima devagar do produto")
    pg.click("#enviar")
    pw.expect(janela).to_be_visible()
    janela.locator("label.marcacao").click()
    janela.locator("button:has-text('Aceitar e continuar')").click()
    pg.wait_for_selector("#msg.ok", timeout=15000)
    conta = ambiente.banco.conta(cid)
    assert (conta["termos_versao"], conta["privacidade_versao"]) == (
        contas.DOCUMENTOS["termos"],
        contas.DOCUMENTOS["privacidade"],
    )
    assert len(ambiente.banco.lotes()) == 1
    pg.reload()
    pg.wait_for_selector("#lotes .lote")
    pw.expect(janela).to_have_count(0)  # aceito: não pergunta de novo
    ctx.close()
    assert _sem_esperados(erros, 428) == [], erros


# -- acessibilidade, celular e estabilidade do layout de cada tela --------------------------------------------


def _esperar_tela(pg, nome):
    pg.wait_for_selector(PRONTA[nome])


@pytest.mark.skipif(not AXE.exists(), reason="axe-core ausente (cd servico/web && npm ci)")
@pytest.mark.parametrize("tema", ["light", "dark", "escuro-escolhido"])
def test_client_area_screens_and_dialogs_have_no_accessibility_violations(ambiente, navegador, tmp_path, tema):  # noqa: F811
    cid, tok = _conta(ambiente, "acessivel.conta@gmail.com")
    _lote(ambiente, tok, {"bolo.png": _png((40, 90, 160), "circulo")}, pronto=True)
    violacoes: dict[str, list] = {}

    def conferir(pg, estado):
        _capturar(pg, f"axe-conta-{estado}-{tema}")
        if v := _axe(pg):
            violacoes[estado] = v

    claro = tema != "dark"
    ctx = _contexto(
        navegador, ambiente, tok, color_scheme="light" if claro else "dark", reduced_motion="reduce", bypass_csp=True
    )
    if tema == "escuro-escolhido":  # o escuro escolhido na conta, num aparelho com o tema claro
        ctx.add_init_script("localStorage.setItem('render2go-tema', 'escuro');")
    pg = ctx.new_page()
    for nome, caminho in TELAS.items():
        pg.goto(f"{ambiente.url}{caminho}")
        _esperar_tela(pg, nome)
        if nome == "pedidos":  # o pedido em sequência, com uma foto marcada
            _escolher_finalidade(pg)
            pg.set_input_files("#fotos", [str(_salvar(tmp_path, "a.png", _png((40, 90, 160), "circulo")))])
            pg.click("#aba-sequencia")
            pg.check("#seq-grade input[type=checkbox]")
        conferir(pg, nome)
    # as janelas: texto próprio, revisão, "confirme que é você", verificação em duas etapas e termos novos
    pg.goto(ambiente.url)
    _escolher_finalidade(pg)
    pg.set_input_files("#fotos", [str(_salvar(tmp_path, "b.png", _png((90, 160, 40), "circulo")))])
    pg.click("#aba-sequencia")
    pg.check("#seq-grade input[type=checkbox]")
    pg.click("#seq-escrever")
    pg.wait_for_selector("dialog.texto-proprio[open]")
    conferir(pg, "texto-proprio")
    pg.locator("dialog.texto-proprio[open] textarea").fill("o vapor sobe do prato")
    pg.click("dialog.texto-proprio[open] button:has-text('Salvar o texto')")
    pg.fill("#prompt", "a câmera se aproxima devagar")
    pg.click("#enviar")
    pg.wait_for_selector("dialog.revisao-textos[open]")
    conferir(pg, "revisao")
    pg.keyboard.press("Escape")
    pg.goto(f"{ambiente.url}/configuracoes")
    _esperar_tela(pg, "configuracoes")
    pg.click("#mfa-ligar")
    pg.wait_for_selector("dialog.mfa[open] img.qr-mfa")
    conferir(pg, "mfa-qr")
    pg.keyboard.press("Escape")
    _envelhecer(ambiente, cid)
    pg.click("#mfa-ligar")
    pg.wait_for_selector("dialog.confirmar-identidade[open]")
    conferir(pg, "confirmar")
    pg.keyboard.press("Escape")
    with ambiente.banco.tx() as c:
        c.run("UPDATE clientes SET termos_versao = 'termos-v1' WHERE id = ?", (cid,))
    pg.reload()
    pg.wait_for_selector("dialog.aceite-documentos[open]")
    conferir(pg, "aceite")
    pg.keyboard.press("Escape")
    # celular: o menu numa janela de baixo
    pg.set_viewport_size({"width": 390, "height": 844})
    pg.click("#abrir-menu")
    pg.wait_for_selector("dialog.menu-celular[open]")
    conferir(pg, "menu-celular")
    ctx.close()
    # a entrada com o passo do aplicativo autenticador
    with ambiente.banco.tx() as c:  # liga a verificação direto no banco (o segredo cifrado, como a API guarda)
        segredo = contas.novo_segredo_totp()
        cifrado = contas.cifrar(ambiente.cfg.chave_segredos, segredo, f"cliente:{cid}")
        c.run("UPDATE clientes SET mfa_segredo = ?, mfa_ativo_em = ? WHERE id = ?", (cifrado, time.time(), cid))
    ctx = navegador.new_context(color_scheme="light" if claro else "dark", reduced_motion="reduce", bypass_csp=True)
    ctx.add_init_script(f"localStorage.setItem('render2go-cookies', {json.dumps(ESCOLHA_COOKIES)});")
    if tema == "escuro-escolhido":
        ctx.add_init_script("localStorage.setItem('render2go-tema', 'escuro');")
    pg = ctx.new_page()
    pg.goto(ambiente.url)
    _entrar_pela_pagina(pg, ambiente, "acessivel.conta@gmail.com")
    pg.wait_for_selector("#form-mfa:not([hidden])")
    conferir(pg, "entrada-mfa")
    ctx.close()
    assert violacoes == {}


@pytest.mark.parametrize("largura", [320, 390])
def test_client_area_screens_fit_small_phones_with_large_touch_targets(ambiente, navegador, largura):  # noqa: F811
    erros: list[str] = []
    _, tok = _conta(ambiente, "celular.pequeno@gmail.com")
    _lote(ambiente, tok, {"bolo.png": _png((40, 90, 160), "circulo")}, pronto=True)
    ctx = _contexto(
        navegador,
        ambiente,
        tok,
        viewport={"width": largura, "height": 740},
        is_mobile=True,
        has_touch=True,
        device_scale_factor=2,
    )
    pg = _pagina(ctx, erros)
    alvos = ".navegacao :is(a, button), .botao, .filtros button, .menu-conta a, [role=tab], select, textarea, input"
    for nome, caminho in TELAS.items():
        pg.goto(f"{ambiente.url}{caminho}")
        _esperar_tela(pg, nome)
        # os cartões que chegam entram com uma animação curta (escala de 0,98): mede depois que ela termina
        pg.wait_for_function(
            "() => document.getAnimations().filter((a) => a.effect?.getTiming().iterations !== Infinity)"
            ".every((a) => a.playState === 'finished')"
        )
        assert pg.locator("#lateral").is_hidden() and pg.locator("#abrir-menu").is_visible(), nome
        for fonte in (None, '"DejaVu Sans", Verdana, sans-serif'):  # também com uma fonte larga
            if fonte:
                pg.evaluate(
                    "(f) => { for (const v of ['--fonte-texto', '--fonte-titulos'])"
                    " document.documentElement.style.setProperty(v, f); }",
                    fonte,
                )
            largura_pagina = pg.evaluate("document.documentElement.scrollWidth")
            assert largura_pagina <= largura, (nome, fonte, largura_pagina, _transbordando(pg, largura))
        pequenos = pg.evaluate(
            """(alvos) => [...document.querySelectorAll(alvos)]
                .filter((e) => e.offsetParent !== null && !['file', 'checkbox', 'radio'].includes(e.type))
                .map((e) => [e.id || e.className || e.tagName, Math.round(e.getBoundingClientRect().height)])
                .filter(([, h]) => h < 44)""",
            alvos,
        )
        assert pequenos == [], (nome, pequenos)
        _capturar(pg, f"celular-conta-{largura}-{nome}")
    # o menu: o mesmo da lateral, numa janela de baixo; escolher uma tela fecha a janela e abre a tela
    pg.click("#abrir-menu")
    menu = pg.locator("dialog.menu-celular[open]")
    pw.expect(menu.locator(".menu-conta a")).to_have_count(8)
    menu.locator("a[data-vista=logs]").click()
    pw.expect(menu).to_have_count(0)
    pg.wait_for_url(f"{ambiente.url}/logs")
    pw.expect(pg.locator("#vista-logs h1")).to_be_focused()
    ctx.close()
    assert erros == [], erros


def test_client_area_screens_do_not_shift_when_the_api_answers_late(ambiente, navegador):  # noqa: F811
    """CLS de cada tela com a API lenta: a 1ª pintura mostra o esqueleto (ou o lugar reservado), e o que chega depois
    não empurra o que já estava na tela (título, filtros, menu, rodapé)."""
    _, tok = _conta(ambiente, "lenta.api@gmail.com")
    _lote(
        ambiente,
        tok,
        {"bolo.png": _png((40, 90, 160), "circulo"), "loja.png": _png((200, 120, 60), "barras")},
        pronto=True,
    )

    def atrasar(rota):
        time.sleep(0.8)  # a 1ª pintura sai antes da resposta
        rota.continue_()

    API = re.compile(r".*/api/(?!conta/foto).*")

    for largura, altura, movel in ((390, 844, True), (1280, 820, False)):
        ctx = _contexto(
            navegador, ambiente, tok, viewport={"width": largura, "height": altura}, is_mobile=movel, has_touch=movel
        )
        ctx.add_init_script(f"document.addEventListener('DOMContentLoaded', () => ({CLS})());")
        for nome, caminho in TELAS.items():
            if nome == "pedidos":
                continue  # a de pedidos tem o teste próprio (test_paginas.py)
            pg = ctx.new_page()
            pg.route(API, atrasar)  # tudo da API, inclusive a conta
            pg.goto(f"{ambiente.url}{caminho}")
            _esperar_tela(pg, nome)
            pg.unroute(API)
            pg.wait_for_timeout(300)
            cls = pg.evaluate("window.__cls")
            assert cls < 0.001, (nome, largura, cls, pg.evaluate("window.__deslocados"))
            pg.close()
        ctx.close()
