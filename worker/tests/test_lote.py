import csv
import io
import json
import threading

import pytest
from conftest import I2V_TEMPLATE, WORKER
from PIL import Image

from render2go_worker import cli_submit, compiler, lote, presets

PADRAO = WORKER.parent / "fixtures" / "sfw" / "padrao-teste-640.png"


def _foto(path, size):
    # padrão de teste SFW redimensionado (a proporção decide o formato)
    with Image.open(PADRAO) as im:
        im.convert("RGB").resize(size).save(path)
    return path


@pytest.fixture
def pasta(tmp_path):
    d = tmp_path / "cliente"
    d.mkdir()
    _foto(d / "caneca.jpg", (600, 600))
    _foto(d / "vitrine.jpg", (900, 1600))
    _foto(d / "fachada.png", (1600, 900))
    (d / "notas.txt").write_text("ignorado")
    return d


def _args(pasta, tmp_path, *extra, comfy=("http://127.0.0.1:9",)):
    argv = [str(pasta), "--saida", str(tmp_path / "saida"), "--template", str(I2V_TEMPLATE)]
    for c in comfy:
        argv += ["--comfy", c]
    return lote.parse([*argv, *extra])


def _compile_falso(chamadas):
    def compile_fn(text, **kw):
        chamadas.append(text)
        prompt = f"The product sits still while the camera slowly moves in. {kw['suffix']}"
        return compiler.CompileResult(
            prompt=prompt,
            profile="i2v",
            version=5,
            model="falso",
            seconds=0.1,
            word_count=10,
            word_range=(15, 60),
            compiled=prompt,
            attempts=1,
        )

    return compile_fn


def _relatorio(tmp_path):
    with (tmp_path / "saida" / "relatorio.csv").open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_shipped_recipes_are_valid_and_use_v4_presets():
    data = lote.load_receitas()
    assert data["presets_version"] == presets.load()["version"] == 4
    # v4: todas as receitas da v3, iguais, mais a categoria, o movimento fixo e o exemplo da página
    v3 = json.loads((presets.WORKER_DIR / "presets" / "receitas.v3.json").read_text())
    assert set(data["receitas"]) == set(v3["receitas"])
    novos = {"categoria", "acao_en", "assunto", "exemplo", "camera_padrao"}
    for nome, r in data["receitas"].items():
        assert {k: v for k, v in r.items() if k not in novos} == v3["receitas"][nome]
        assert r["categoria"] in data["categorias"] and r["exemplo"]
    assert data["receitas"]["livre"]["camera_padrao"] == "suave"
    assert set(data["movimentos"]) == set(data["categorias"])
    # v3: todas as receitas da v2, iguais, mais a continuidade "cena-unica" em todas
    data = v3
    v2 = json.loads((presets.WORKER_DIR / "presets" / "receitas.v2.json").read_text())
    assert set(data["receitas"]) == set(v2["receitas"])
    for nome, r in data["receitas"].items():
        antes = v2["receitas"][nome]
        assert r["presets"] == {**antes["presets"], "continuidade": "cena-unica"}
        assert {k: v for k, v in r.items() if k != "presets"} == {
            k: v for k, v in antes.items() if k != "presets"
        }
    assert data["receitas"]["pessoa-apresentacao"]["exige_consentimento"] is True
    for f in data["formatos"].values():
        for q in lote.QUALIDADES:
            w, h = f[q]["width"], f[q]["height"]
            assert w % 16 == 0 and h % 16 == 0 and w * h <= 921_600


def test_folder_plan_auto_format_and_quality(pasta, tmp_path):
    a = _args(pasta, tmp_path, "--prompt", "a câmera se aproxima devagar", "--variacoes", "2")
    plano = lote.planejar(a)
    assert plano.erros == []
    por_nome = {t.arquivo: t for t in plano.tarefas}
    assert sorted(por_nome) == [
        "caneca-v1.mp4",
        "caneca-v2.mp4",
        "fachada-v1.mp4",
        "fachada-v2.mp4",
        "vitrine-v1.mp4",
        "vitrine-v2.mp4",
    ]
    assert (por_nome["caneca-v1.mp4"].width, por_nome["caneca-v1.mp4"].height) == (640, 640)
    assert por_nome["vitrine-v1.mp4"].formato == "vertical"
    assert por_nome["fachada-v1.mp4"].formato == "horizontal"
    assert len({t.pedido for t in plano.tarefas}) == 1  # um prompt para o lote todo
    assert por_nome["caneca-v1.mp4"].seed != por_nome["caneca-v2.mp4"].seed
    alta = lote.planejar(_args(pasta, tmp_path, "--prompt", "x", "--qualidade", "alta"))
    assert {(t.width, t.height) for t in alta.tarefas} == {(960, 960), (720, 1280), (1280, 720)}


def test_exif_rotation_counts_for_the_format(tmp_path):
    foto = tmp_path / "girada.jpg"
    with Image.open(PADRAO) as im:
        exif = Image.Exif()
        exif[lote.EXIF_ORIENTATION] = 6  # celular deitado: o ComfyUI gira na leitura
        im.convert("RGB").resize((1600, 900)).save(foto, exif=exif)
    assert lote.formato_auto(foto) == "vertical"


def test_folder_errors_before_any_gpu_time(pasta, tmp_path):
    (pasta / "quebrada.jpg").write_bytes(b"nao e imagem")
    a = _args(pasta, tmp_path, "--prompt", "x", "--receita", "pessoa-apresentacao")
    erros = "\n".join(lote.planejar(a).erros)
    assert "quebrada.jpg: imagem ilegível" in erros
    assert "caneca.jpg: a receita pessoa-apresentacao exige consentimento" in erros
    ok = _args(
        pasta, tmp_path, "--prompt", "x", "--receita", "pessoa-apresentacao", "--consentimento"
    )
    assert [e for e in lote.planejar(ok).erros if "consentimento" in e] == []
    vazia = tmp_path / "vazia"
    vazia.mkdir()
    assert "nenhuma foto" in lote.planejar(_args(vazia, tmp_path, "--prompt", "x")).erros[0]
    drone = lote.planejar(_args(pasta, tmp_path, "--prompt", "x", "--preset", "camera=drone"))
    assert "opção desconhecida: camera=drone" in drone.erros[0]


def test_name_collisions_and_subfolders(tmp_path):
    d = tmp_path / "f"
    (d / "sub").mkdir(parents=True)
    for nome in ("a.jpg", "a.png", "sub/a.jpg"):
        _foto(d / nome, (300, 300))
    a = _args(d, tmp_path, "--prompt", "x", "--subpastas")
    assert sorted(t.arquivo for t in lote.planejar(a).tarefas) == [
        "a-jpg-v1.mp4",
        "a-png-v1.mp4",
        "sub__a-v1.mp4",
    ]


def test_sheet_mode_still_works(pasta, tmp_path):
    (pasta / "pedidos.csv").write_text(
        "foto;receita;acao;formato;duracao;variacoes\n"
        "caneca.jpg;produto-giro;;;;2\n"
        "../fora.jpg;produto-giro;;;;\n"
        "vitrine.jpg;comida-bebida;;auto;9;\n",
        encoding="utf-8",
    )
    plano = lote.planejar(_args(pasta, tmp_path))
    assert "linha 3: foto '../fora.jpg' fora da pasta" in plano.erros
    assert "linha 4: foto 'vitrine.jpg': duracao 9: de 3 a 7 segundos" in plano.erros
    assert [t.arquivo for t in plano.tarefas] == [
        "002-caneca-produto-giro-v1.mp4",
        "002-caneca-produto-giro-v2.mp4",
    ]


def test_cli_requires_prompt_or_sheet(tmp_path, capsys):
    with pytest.raises(SystemExit):
        lote.parse([str(tmp_path), "--saida", str(tmp_path / "s")])
    assert "--prompt" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        lote.parse([str(tmp_path), "--saida", "s", "--prompt", "x", "--comfy", "u", "--comfy", "u"])


def test_end_to_end_compiles_once_and_resumes(pasta, tmp_path, fake_comfy, fake_ollama):
    a = _args(pasta, tmp_path, "--prompt", "a câmera se aproxima devagar", comfy=(fake_comfy.url,))
    a.ollama = fake_ollama.url
    fake_ollama.response = "The product stays in place as the camera slowly moves closer."
    assert lote.executar(a) == lote.EXIT_OK
    saida = tmp_path / "saida"
    assert sorted(p.name for p in saida.glob("*.mp4")) == [
        "caneca-v1.mp4",
        "fachada-v1.mp4",
        "vitrine-v1.mp4",
    ]
    assert len(fake_ollama.requests) == 1  # um pedido, uma compilação
    assert fake_ollama.requests[0]["keep_alive"] == 0
    estado = json.loads((saida / "lote.json").read_text())
    (pedido,) = estado["prompts"].values()
    assert pedido["prompt_en"].endswith(  # estilo e, por último, a continuidade (receitas v3)
        "Clean commercial look, high detail, vibrant natural colors, sharp focus. "
        "A single continuous uninterrupted shot, the same subject from the image stays in view "
        "from the first frame to the last, its shape, colors and details stay exactly the same."
    )
    textos = {wf["129:93"]["inputs"]["text"] for wf in fake_comfy.prompts.values()}
    assert textos == {pedido["prompt_en"]}
    larguras = sorted(wf["129:98"]["inputs"]["width"] for wf in fake_comfy.prompts.values())
    assert larguras == [480, 640, 832]
    assert [r["status"] for r in _relatorio(tmp_path)] == ["ok"] * 3
    # a foto chega ao ComfyUI já no tamanho exato do vídeo (preparo com Lanczos)
    tamanhos = {Image.open(io.BytesIO(b)).size for b in fake_comfy.uploads.values()}
    assert tamanhos == {(640, 640), (480, 832), (832, 480)}

    # rodar de novo: nada é recompilado nem renderizado
    assert lote.executar(a) == lote.EXIT_OK
    assert len(fake_comfy.prompts) == 3 and len(fake_ollama.requests) == 1


def test_work_is_spread_over_gpus_and_survives_one_going_down(pasta, tmp_path):
    a = _args(pasta, tmp_path, "--prompt", "x", "--variacoes", "4", comfy=("gpu-a", "gpu-b"))
    usadas, lock = [], threading.Lock()

    def submit(args):
        with lock:
            usadas.append(args.comfy)
        if args.comfy == "gpu-b":
            return cli_submit.EXIT_UNREACHABLE, {"error": "caiu"}
        args.out.write_bytes(b"mp4")
        return cli_submit.EXIT_OK, {"status": "ok"}

    assert lote.executar(a, submit, _compile_falso([])) == lote.EXIT_OK
    rows = _relatorio(tmp_path)
    assert len(rows) == 12 and {r["gpu"] for r in rows} == {"gpu-a"}
    assert "gpu-b" in usadas  # tentou, caiu, e as tarefas voltaram para a fila
    assert len(list((tmp_path / "saida").glob("*.mp4"))) == 12
    assert not list((tmp_path / "saida").glob("*.parcial.mp4"))


def test_all_gpus_down_keeps_pending_for_next_run(pasta, tmp_path):
    a = _args(pasta, tmp_path, "--prompt", "x")

    def submit(args):
        return cli_submit.EXIT_UNREACHABLE, {"error": "sem pod"}

    assert lote.executar(a, submit, _compile_falso([])) == lote.EXIT_INACESSIVEL
    assert not (tmp_path / "saida" / "relatorio.csv").exists()


def test_refusal_stops_before_rendering(pasta, tmp_path):
    def recusa(*_, **__):
        raise compiler.CompilerRefused("REFUSED")

    def submit(_):
        raise AssertionError("não deveria renderizar")

    assert lote.executar(_args(pasta, tmp_path, "--prompt", "x"), submit, recusa) == 3


def test_failed_video_is_reported_and_retried_next_run(pasta, tmp_path):
    a = _args(pasta, tmp_path, "--prompt", "x", "--fila", "1")
    falhar = {"caneca"}

    def submit(args):
        if any(args.out.name.startswith(n) for n in falhar):
            return cli_submit.EXIT_COMFY, {"error": "CUDA out of memory"}
        args.out.write_bytes(b"mp4")
        return cli_submit.EXIT_OK, {}

    chamadas = []
    assert lote.executar(a, submit, _compile_falso(chamadas)) == lote.EXIT_FALHA
    assert {r["arquivo"]: r["status"] for r in _relatorio(tmp_path)}["caneca-v1.mp4"] == "erro"
    falhar.clear()
    assert lote.executar(a, submit, _compile_falso(chamadas)) == lote.EXIT_OK
    assert len(chamadas) == 1  # o prompt veio do lote.json
    assert [r["status"] for r in _relatorio(tmp_path)] == ["ok"] * 3


def test_gpus_shortcut_uses_consecutive_ports(tmp_path):
    a = lote.parse([str(tmp_path), "--saida", "s", "--prompt", "x", "--gpus", "3"])
    assert a.comfy == [f"http://127.0.0.1:{p}" for p in (8188, 8189, 8190)] and a.fila == 1
    with pytest.raises(SystemExit):
        lote.parse([str(tmp_path), "--saida", "s", "--prompt", "x", "--gpus", "2", "--comfy", "u"])


def test_orders_made_before_the_5s_cap_still_render_up_to_7s(tmp_path):
    # o serviço vende até 5 s desde 13/10/2026 (render2go_servico.precos.DURACAO_MAX); os pedidos de
    # antes, com 6 e 7 s, seguem como foram feitos: o pod continua aceitando até 7 s
    foto = tmp_path / "f.png"
    Image.new("RGB", (64, 64), (120, 90, 60)).save(foto)
    for d in (6, 7):
        p = lote.Pedido(receita="livre", duracao=d)
        tarefas, erros = lote._tarefas(foto, "f", p, "rapida", lote.load_receitas())
        assert not erros and tarefas[0].duracao == d
    _, erros = lote._tarefas(
        foto, "f", lote.Pedido(receita="livre", duracao=8), "rapida", lote.load_receitas()
    )
    assert erros == ["duracao 8: de 3 a 7 segundos"]
