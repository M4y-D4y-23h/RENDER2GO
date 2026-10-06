import csv
import shutil

import pytest
from conftest import I2V_TEMPLATE, WORKER

from gpu2go_worker import cli_submit, lote, presets

PADRAO = WORKER.parent / "fixtures" / "sfw" / "padrao-teste-640.png"


@pytest.fixture
def pasta(tmp_path):
    d = tmp_path / "cliente"
    d.mkdir()
    for nome in ("caneca.png", "sala.png", "dona.png"):
        shutil.copy(PADRAO, d / nome)
    return d


def _pedidos(pasta, texto, sep=","):
    (pasta / "pedidos.csv").write_text(texto.replace(",", sep), encoding="utf-8")
    return pasta / "pedidos.csv"


def test_shipped_recipes_are_valid_and_use_v2_presets():
    data = lote.load_receitas()
    assert data["presets_version"] == presets.load()["version"] == 2
    assert {"produto-giro", "imovel-sala", "comida-prato", "pessoa-apresentacao"} <= set(
        data["receitas"]
    )
    assert data["receitas"]["pessoa-apresentacao"]["exige_consentimento"] is True
    for f in data["formatos"].values():
        assert f["width"] % 16 == 0 and f["height"] % 16 == 0


def test_plan_expands_variations_with_defaults_and_stable_seeds(pasta):
    p = _pedidos(
        pasta,
        "foto,receita,acao,formato,duracao,variacoes\n"
        "caneca.png,produto-giro,,,,2\n"
        "sala.png,imovel-sala,a câmera avança pela sala,vertical,6,\n",
        sep=";",
    )
    plano = lote.planejar(pasta, p)
    assert plano.erros == []
    a, b, c = plano.tarefas
    assert (a.arquivo, b.arquivo) == (
        "002-caneca-produto-giro-v1.mp4",
        "002-caneca-produto-giro-v2.mp4",
    )
    assert (a.width, a.height, a.duracao) == (640, 640, 5) and a.seed != b.seed
    assert a.selections["camera"] == "orbita-produto"
    assert (c.width, c.height, c.duracao, c.acao) == (480, 832, 6, "a câmera avança pela sala")
    assert lote.planejar(pasta, p).tarefas[0].seed == a.seed  # refazer dá a mesma seed


def test_plan_reports_every_error_before_any_gpu_time(pasta):
    p = _pedidos(
        pasta,
        "foto,receita,formato,duracao,variacoes,consentimento\n"
        "../fora.png,produto-giro,,,,\n"
        "nao-existe.png,produto-giro,,,,\n"
        "caneca.png,drone-voando,,,,\n"
        "caneca.png,produto-giro,cinema,9,7,\n"
        "dona.png,pessoa-apresentacao,,,,\n"
        "dona.png,pessoa-apresentacao,,,,sim\n",
    )
    plano = lote.planejar(pasta, p)
    texto = "\n".join(plano.erros)
    for trecho in (
        "linha 2: foto '../fora.png' fora da pasta",
        "linha 3: foto 'nao-existe.png' não encontrada",
        "linha 4: receita desconhecida",
        "linha 5: formato 'cinema'",
        "linha 5: duracao 9",
        "linha 5: variacoes 7",
        "linha 6: a receita pessoa-apresentacao exige consentimento=sim",
    ):
        assert trecho in texto
    assert "linha 7" not in texto  # com consentimento passa


def test_missing_columns_and_same_photo_twice(pasta):
    assert (
        "faltam as colunas" in lote.planejar(pasta, _pedidos(pasta, "foto\ncaneca.png\n")).erros[0]
    )
    p = _pedidos(pasta, "foto,receita\ncaneca.png,produto-giro\ncaneca.png,produto-giro\n")
    plano = lote.planejar(pasta, p)
    assert plano.erros == [] and len({t.arquivo for t in plano.tarefas}) == 2


def _args(pasta, tmp_path, comfy="http://127.0.0.1:9", ollama="http://127.0.0.1:9"):
    a = lote.build_parser().parse_args(
        [
            str(pasta),
            "--saida",
            str(tmp_path / "saida"),
            "--comfy",
            comfy,
            "--ollama",
            ollama,
            "--template",
            str(I2V_TEMPLATE),
        ]
    )
    a.pedidos = pasta / "pedidos.csv"
    return a


def test_invalid_sheet_stops_before_submitting(pasta, tmp_path):
    _pedidos(pasta, "foto,receita\ncaneca.png,drone\n")

    def submit(_):
        raise AssertionError("não deveria enviar nada")

    assert lote.executar(_args(pasta, tmp_path), submit) == lote.EXIT_PLANILHA
    assert not (tmp_path / "saida").exists()


def test_end_to_end_with_fake_services_and_resume(pasta, tmp_path, fake_comfy, fake_ollama):
    _pedidos(pasta, "foto,receita,variacoes\ncaneca.png,produto-giro,2\nsala.png,imovel-sala,1\n")
    a = _args(pasta, tmp_path, fake_comfy.url, fake_ollama.url)
    assert lote.executar(a) == lote.EXIT_OK
    saida = tmp_path / "saida"
    assert sorted(p.name for p in saida.glob("*.mp4")) == [
        "002-caneca-produto-giro-v1.mp4",
        "002-caneca-produto-giro-v2.mp4",
        "003-sala-imovel-sala-v1.mp4",
    ]
    rows = list(csv.DictReader((saida / "relatorio.csv").open(encoding="utf-8")))
    assert [r["status"] for r in rows] == ["ok"] * 3
    # termos do preset anexados pelo código, palavra por palavra
    assert rows[0]["prompt_en"].endswith(
        "Clean commercial look, high detail, vibrant natural colors, sharp focus."
    )
    assert "Slow orbit around the product" in rows[0]["prompt_en"]
    workflows = list(fake_comfy.prompts.values())
    assert len(workflows) == 3 and len(fake_comfy.uploads) == 3

    # rodar de novo não refaz nada
    assert lote.executar(a) == lote.EXIT_OK
    assert len(fake_comfy.prompts) == 3


def test_failures_are_recorded_and_unreachable_stops(pasta, tmp_path):
    _pedidos(pasta, "foto,receita,variacoes\ncaneca.png,produto-giro,3\n")
    codes = [cli_submit.EXIT_REFUSED, cli_submit.EXIT_UNREACHABLE]
    calls = []

    def submit(args):
        calls.append(args)
        code = codes.pop(0)
        return code, {"status": "error", "error": f"falha {code}"}

    assert lote.executar(_args(pasta, tmp_path), submit) == lote.EXIT_INACESSIVEL
    assert len(calls) == 2  # parou no serviço inacessível
    assert calls[0].width == 640 and calls[0].preset and calls[0].keep_alive == "0"
    rows = list(csv.DictReader((tmp_path / "saida" / "relatorio.csv").open(encoding="utf-8")))
    assert [r["status"] for r in rows] == ["recusado", "erro"]
    assert not list((tmp_path / "saida").glob("*.mp4"))
