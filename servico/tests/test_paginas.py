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
    # teste gratuito: só 480p e 3 s, e o aviso das regras
    assert cli.locator("#qualidade option").all_inner_texts() == ["Rápida (prévia, 480p)"]
    assert cli.locator("#duracao option").all_inner_texts() == ["3 s"]
    assert "envie todas juntas, em um único pedido" in cli.locator("#plano-aviso").inner_text()
    cli.set_input_files("#fotos", [str(tmp_path / "bolo.png"), str(tmp_path / "loja.png")])
    assert "2 de 10 fotos escolhidas · ainda cabem 8" in cli.locator("#contagem").inner_text()
    assert cli.locator("#enviar").inner_text() == "Enviar pedido com 2 foto(s)"
    # autorização: marcar a caixa pede o nome de quem declara (vai para o registro de auditoria)
    assert cli.locator("#bloco-declarante").is_hidden()
    cli.check("#consentimento")
    cli.fill("#declarante", "Maria da Silva")
    if os.environ.get("GPU2GO_CAPTURAS"):
        cli.screenshot(path=os.path.join(os.environ["GPU2GO_CAPTURAS"], "cliente-teste.png"), full_page=True)
    cli.fill("#prompt", "a câmera se aproxima devagar do produto")
    cli.select_option("#duracao", "3")
    cli.click("#enviar")
    cli.wait_for_selector("#msg.ok", timeout=15000)
    cli.wait_for_selector("text=Em revisão")
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
    assert "Autorização declarada por Maria da Silva" in adm.locator("#pendentes .autorizacao").inner_text()
    assert "marcou ter autorização" in adm.locator("#pendentes .aviso.alerta").inner_text()
    if os.environ.get("GPU2GO_CAPTURAS"):
        adm.screenshot(path=os.path.join(os.environ["GPU2GO_CAPTURAS"], "aprovar.png"), full_page=True)
    adm.click(".acoes button.primario")
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
    assert registro["autorizacao"]["declarante"] == "Maria da Silva" and registro["autorizacao"]["dispositivo"]
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
    cli.wait_for_selector("text=Pronto")
    assert cli.locator(".videos a").count() == 2
    if os.environ.get("GPU2GO_CAPTURAS"):
        cli.screenshot(path=os.path.join(os.environ["GPU2GO_CAPTURAS"], "cliente.png"), full_page=True)
    assert erros == []
