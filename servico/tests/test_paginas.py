"""Páginas no navegador (Chromium headless): fluxo completo, sem erros no console nem bloqueios
da política de segurança (CSP). Precisa do pacote playwright e de um Chromium; senão, pula."""

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
    adm.click("#form-token button")
    adm.fill("#cliente-nome", "Padaria do Bairro")
    adm.select_option("#cliente-plano", "teste")
    adm.click("#form-cliente button")
    adm.wait_for_selector("#token-novo:not([hidden])")
    texto = adm.locator("#token-novo").inner_text()
    token = re.search(r"seguro\): (\S+)", texto).group(1)

    # cliente envia 2 fotos
    for n, (cor, forma) in {"bolo.png": ((40, 90, 160), "circulo"), "loja.png": ((200, 120, 60), "barras")}.items():
        (tmp_path / n).write_bytes(_png(cor, forma))
    cli = _pagina(navegador, erros)
    cli.goto(ambiente.url)
    cli.fill("#token", token)
    cli.click("#form-token button")
    cli.wait_for_selector("#receita option", state="attached")
    cli.set_input_files("#fotos", [str(tmp_path / "bolo.png"), str(tmp_path / "loja.png")])
    assert "2 foto(s)" in cli.locator("#contagem").inner_text()
    cli.fill("#prompt", "a câmera se aproxima devagar do produto")
    cli.select_option("#duracao", "3")
    cli.click("#enviar")
    cli.wait_for_selector("#msg.ok", timeout=15000)
    cli.wait_for_selector("text=Em revisão")
    assert sum(k.endswith(".png") for k in ambiente.objetos) == 2  # direto no armazenamento

    # aprovador vê as miniaturas, o aviso de pessoas por foto e o resumo do lote, e aprova
    ambiente.cfg.analises.esperar()
    adm.click("#atualizar")
    adm.wait_for_selector(".grade img")
    assert adm.locator(".grade img").count() == 2
    assert adm.locator(".selo.pessoas").count() == 1
    assert "sem pessoas detectadas" in adm.locator(".grade").inner_text()
    assert "NÃO marcou a autorização" in adm.locator(".cartao .aviso.erro").inner_text()
    if os.environ.get("GPU2GO_CAPTURAS"):
        adm.screenshot(path=os.path.join(os.environ["GPU2GO_CAPTURAS"], "aprovar.png"), full_page=True)
    adm.click(".acoes button.primario")
    adm.wait_for_selector("text=Nada aguardando revisão.")

    # o worker termina (simulado no banco) e o cliente vê os links
    (lote,) = ambiente.banco.lotes()
    for j in ambiente.banco.jobs(lote["id"]):
        if j["tipo"] == "render":
            ambiente.objetos[f"saida/{lote['cliente_id']}/{lote['id']}/{j['arquivo']}"] = b"mp4"
    with ambiente.banco.tx() as c:
        c.run("UPDATE jobs SET status = 'ok'")
        c.run("UPDATE lotes SET status = 'concluido'")
    cli.reload()
    cli.wait_for_selector("text=Pronto")
    assert cli.locator(".videos a").count() == 2
    if os.environ.get("GPU2GO_CAPTURAS"):
        cli.screenshot(path=os.path.join(os.environ["GPU2GO_CAPTURAS"], "cliente.png"), full_page=True)
    assert erros == []
