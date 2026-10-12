"""Filtro das fotos no servidor (render2go_servico.filtro_fotos; ADR 0005, ponto 1): a foto é avaliada antes de
qualquer pod recebê-la, e a que não passa nunca vira vídeo.

Só conteúdo SFW (regra do projeto): as fotos são formas coloridas geradas aqui ou as do fixtures/sfw, e o caminho de
bloqueio usa um classificador falso que devolve a nota que quisermos. A taxa de acerto vem do cartão do modelo."""

import io
import json
import threading
import time

import pytest
from PIL import Image, ImageDraw
from render2go_worker import filtro_imagem
from test_api import (
    ADM,
    PAINEL,
    WORKER,
    Armazem,
    _cliente,
    _concluir,
    _enviar_tudo,
    _job,
    _lote,
    cliente_teste,
    config_teste,
    h,
)

from render2go_servico import SERVICO_DIR, filtro_fotos, motivos
from render2go_servico import api as api_mod
from render2go_servico.api import analisar_continuamente, analisar_lote

FIXTURES = SERVICO_DIR.parent / "fixtures" / "sfw"
NEUTRA = (0.98, 0.02, 0.0, 0.0)
VAGO = "a foto não pôde ser usada; envie outra foto"


def _foto(tamanho=(800, 600), cor=(40, 90, 160), formato="JPEG", **kw) -> bytes:
    im = Image.new("RGB", tamanho, cor)
    w, h = tamanho
    ImageDraw.Draw(im).ellipse((w // 4, h // 4, w // 2, h // 2), fill=(250, 250, 250))  # um "produto" no meio
    b = io.BytesIO()
    im.save(b, formato, **kw)
    return b.getvalue()


class Falso:
    """Classificador falso: a nota de cada imagem vem de `notas` (por ordem de chamada) ou é neutra."""

    def __init__(self, notas=None, erro: Exception | None = None):
        self.notas, self.erro, self.chamadas = list(notas or []), erro, []

    def probabilidades(self, quadros):
        self.chamadas.append(len(quadros))
        if self.erro is not None:
            raise self.erro
        return [self.notas.pop(0) if self.notas else NEUTRA for _ in quadros]


class PorFoto:
    """Classificador falso que decide pela cor do canto da foto (as fotos dos testes têm cores diferentes)."""

    def __init__(self, recusar: set[tuple[int, int, int]]):
        self.recusar = recusar

    def probabilidades(self, quadros):
        saida = []
        for q in quadros:
            canto = tuple(q[:3])
            saida.append((0.1, 0.1, 0.3, 0.5) if any(_perto(canto, c) for c in self.recusar) else NEUTRA)
        return saida


def _perto(a, b, tol=12):
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def _pronto(classificador) -> filtro_fotos.FiltroFotos:
    f = filtro_fotos.FiltroFotos(classificador)
    assert f.disponivel()
    return f


# -- a foto que o classificador vê ------------------------------------------------------------


def test_the_whole_photo_and_the_video_crop_are_evaluated():
    quadros, info = filtro_fotos.imagens(_foto((800, 600)), {"formato": "auto", "qualidade": "rapida"})
    # 4:3 → horizontal (832×480): o recorte deixa de fora 23% da foto, e também é avaliado
    assert info["formato"] == "horizontal" and info["corte"] is True and info["original"] == [800, 600]
    assert len(quadros) == 2 and all(len(q) == filtro_imagem.LADO**2 * 3 for q in quadros)
    # a foto já na proporção do vídeo: o recorte é a própria foto
    quadros, info = filtro_fotos.imagens(_foto((832, 480)), {"formato": "auto", "qualidade": "alta"})
    assert len(quadros) == 1 and info["corte"] is False
    # formato pedido (não automático): o recorte é o do pedido
    _, info = filtro_fotos.imagens(_foto((800, 600)), {"formato": "vertical", "qualidade": "rapida"})
    assert info["formato"] == "vertical" and info["corte"] is True


def test_the_exif_rotation_and_transparency_are_applied_like_on_the_pod():
    im = Image.new("RGB", (400, 200), (200, 30, 30))
    exif = im.getexif()
    exif[0x0112] = 6  # celular: a foto deitada no arquivo, em pé na tela
    b = io.BytesIO()
    im.save(b, "JPEG", exif=exif.tobytes())
    _, info = filtro_fotos.imagens(b.getvalue(), {"formato": "auto", "qualidade": "rapida"})
    assert info["formato"] == "vertical"  # 200×400 depois da rotação, como o pod vê
    # PNG com paleta e fundo transparente: o fundo fica branco (preparo do pod), não preto
    p = Image.new("P", (300, 300), 0)
    p.putpalette([0, 0, 0, 30, 160, 60] + [0] * 762)
    ImageDraw.Draw(p).rectangle((100, 100, 200, 200), fill=1)
    p.info["transparency"] = 0
    b = io.BytesIO()
    p.save(b, "PNG", transparency=0)
    quadros, _ = filtro_fotos.imagens(b.getvalue(), {"formato": "quadrado", "qualidade": "rapida"})
    assert _perto(tuple(quadros[0][:3]), (255, 255, 255))


def test_the_reduction_averages_pixels_instead_of_sampling_them():
    """Imagem escondida em pixels espaçados ("ataque de escala"): a redução por área vê a média, como o preparo do
    pod (Lanczos); a amostragem (vizinho mais próximo, o que o Pillow faz com paleta) veria só uma das cores."""
    listras = Image.new("P", (2560, 2560), 0)
    listras.putpalette([0, 0, 0, 255, 255, 255] + [0] * 762)
    d = ImageDraw.Draw(listras)
    for x in range(0, 2560, 2):
        d.line((x, 0, x, 2559), fill=1)
    b = io.BytesIO()
    listras.save(b, "PNG")
    quadros, _ = filtro_fotos.imagens(b.getvalue(), {"formato": "quadrado", "qualidade": "rapida"})
    media = sum(quadros[0]) / len(quadros[0])
    assert 100 < media < 160  # cinza médio, nunca só preto ou só branco


def test_invalid_files_are_refused_without_decoding_and_the_classifier_is_never_called():
    falso = Falso()
    f = _pronto(falso)
    pedido = {"formato": "auto", "qualidade": "rapida"}
    for dados in (b"<html>nao sou foto</html>", b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, _foto()[:300]):
        r = f.avaliar(dados, pedido)
        assert r.filtro == "invalida" and r.detalhe["erro"] and r.detalhe["versao"] == filtro_fotos.VERSAO
    assert falso.chamadas == []


# -- a decisão --------------------------------------------------------------------------------


def test_the_worst_of_the_two_images_decides_with_the_frame_rule(monkeypatch):
    pedido = {"formato": "auto", "qualidade": "rapida"}
    r = _pronto(Falso()).avaliar(_foto(), pedido)
    assert r.filtro == "ok" and r.detalhe["nota"] == 0.0 and r.detalhe["corte"] is True
    assert r.detalhe["limiares"] == [filtro_imagem.LIMIAR, filtro_imagem.LIMIAR_ALTO]
    # só o recorte (2ª imagem) passa do limiar: a foto é recusada
    r = _pronto(Falso([NEUTRA, (0.2, 0.1, 0.4, 0.3)])).avaliar(_foto(), pedido)
    assert r.filtro == "recusada" and r.detalhe["nota"] == pytest.approx(0.7)
    # explícita num lugar só basta; sugestivo (low) não barra
    assert _pronto(Falso([(0.5, 0.0, 0.1, 0.4)])).avaliar(_foto((832, 480)), pedido).filtro == "recusada"
    assert _pronto(Falso([(0.1, 0.85, 0.05, 0.0)])).avaliar(_foto((832, 480)), pedido).filtro == "ok"
    # o .env aperta (dentro dos limites): 0,45 passa a barrar
    monkeypatch.setenv("RENDER2GO_FILTRO_LIMIAR", "0,4")
    assert _pronto(Falso([(0.5, 0.05, 0.45, 0.0)])).avaliar(_foto((832, 480)), pedido).filtro == "recusada"


def test_without_the_classifier_nothing_is_evaluated_and_loading_is_retried_later(monkeypatch):
    class Quebrado(Falso):
        def carregar(self):
            raise filtro_imagem.FiltroError("classificador ausente: /opt/x/model.safetensors")

    f = filtro_fotos.FiltroFotos(Quebrado())
    with pytest.raises(filtro_fotos.FiltroIndisponivel):
        f.avaliar(_foto(), {})
    assert f.disponivel() is False and "ausente" in f.erro and f.estado()["ok"] is False
    agora = time.monotonic()
    monkeypatch.setattr(filtro_fotos.time, "monotonic", lambda: agora + 60)
    assert f.disponivel() is False  # não tenta de novo a cada volta
    f.classificador = Falso()
    monkeypatch.setattr(filtro_fotos.time, "monotonic", lambda: agora + filtro_fotos.RECARREGAR_S + 1)
    assert f.disponivel() is True and f.erro is None
    g = _pronto(Falso(erro=RuntimeError("sem memória")))
    with pytest.raises(filtro_imagem.FiltroError):  # o classificador falhou numa foto: ela não é avaliada
        g.avaliar(_foto(), {})


def test_the_customer_reason_is_vague_and_the_same_for_refused_and_invalid_photos():
    for erro in ("foto recusada pelo filtro de imagem (nota 0.71, explícita 0.40)", "foto inválida: não é uma foto"):
        assert motivos.motivo({"status": "cancelado", "erro": erro}) == "foto_nao_aceita"
    assert motivos.MOTIVOS["foto_nao_aceita"] == VAGO
    assert not any(t in VAGO for t in ("filtro", "nota", "nudez", "impróprio", "recusad"))


# -- a fila: nenhuma foto vai para um pod sem passar pelo filtro ------------------------------


@pytest.fixture
def com_portao(banco):
    banco.exigir_filtro = True
    return banco


def _pedido_traduzido(api, tok, n=3, plano_interno=False, **pedido):
    r = _lote(api, tok, n=n, **pedido)
    lid = r.json()["lote"]
    assert _enviar_tudo(api, tok, lid, r.json()["envios"]).status_code == 200
    return lid


def _fotos_reais(api, lid, cores):
    """Troca o conteúdo falso do armazém dos testes por fotos de verdade (SFW), uma cor por foto."""
    fotos = api.banco.fotos(lid)
    conteudo = {f["chave"]: _foto(cor=c) for f, c in zip(fotos, cores, strict=True)}
    api.armazem.ler = lambda chave, limite: conteudo[chave]
    return fotos


def test_photos_are_evaluated_before_review_and_refused_ones_never_become_videos(com_portao):
    api = cliente_teste(config_teste(com_portao))
    tok = _cliente(api, "Loja", saldo="10")
    lid = _pedido_traduzido(api, tok, n=3, duracao=4)
    fotos = _fotos_reais(api, lid, [(40, 90, 160), (200, 30, 30), (30, 160, 60)])
    filtro = _pronto(PorFoto({(200, 30, 30)}))
    assert analisar_lote(api.banco, api.armazem, None, lid, filtro) == 3
    por_nome = {f["nome"]: f for f in api.banco.fotos(lid)}
    assert [por_nome[f["nome"]]["filtro"] for f in fotos] == ["ok", "recusada", "ok"]
    # o revisor vê o resultado e as notas; a foto recusada não vem como imagem (o dono abre pelo botão)
    (p,) = api.get(f"{PAINEL}/api/lotes", headers=ADM).json()
    assert p["fotos_recusadas"] == 1 and p["fotos_filtrando"] == 0
    recusada = next(f for f in p["fotos"] if f["filtro"] == "recusada")
    assert recusada["url"] is None and recusada["filtro_detalhe"]["nota"] == pytest.approx(0.8)
    assert all(f["url"] for f in p["fotos"] if f["filtro"] == "ok")
    # aprovado: o vídeo da foto recusada nasce cancelado e nunca vai para um pod
    assert api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM).status_code == 200
    comp = _job(api)
    api.post(
        f"/api/worker/jobs/{comp['job_id']}/prompt",
        headers=h(WORKER),
        json={"worker": "pod-1", "prompt_en": "The camera slowly dollies in.", "compilador": "i2v@6"},
    )
    _concluir(api, comp, status="ok")
    renders = [_job(api), _job(api)]
    assert _job(api) is None and {r["arquivo"] for r in renders} == {"foto-0-v1.mp4", "foto-2-v1.mp4"}
    lote = api.get(f"/api/lotes/{lid}", headers=h(tok)).json()
    (v,) = [v for v in lote["videos"] if v["arquivo"] == "foto-1-v1.mp4"]
    assert v["status"] == "cancelado" and v["motivo"] == VAGO
    texto = json.dumps(lote, ensure_ascii=False)
    assert "nota" not in texto and "recusada pelo filtro" not in texto  # o cliente não vê a nota nem o motivo real
    # o registro do pedido guarda a recusa (auditoria)
    aud = api.get(f"{PAINEL}/api/lotes/{lid}/auditoria", headers=ADM).json()
    (ev,) = [e for e in aud["eventos"] if e["tipo"] == "foto_recusada"]
    assert ev["detalhe"]["foto"] == "foto-1" and ev["detalhe"]["nota"] == pytest.approx(0.8)
    falhas = api.get(f"{PAINEL}/api/lotes/{lid}/falhas", headers=ADM).json()["videos"]
    assert falhas[0]["erro"].startswith("foto recusada pelo filtro de imagem (nota 0.80")


def test_an_auto_approved_order_waits_for_the_filter_and_starts_no_pod_before_it(com_portao):
    """Plano interno (aprovado sozinho no envio): os vídeos esperam o filtro. O orquestrador não conta o que espera o
    filtro, nem a tradução de um pedido sem foto liberada: com o analisador parado, nenhum pod sobe à toa. Um pod já
    ligado adianta a tradução (só texto)."""
    api = cliente_teste(config_teste(com_portao))
    tok = _cliente(api, "Dono", plano="interno")
    lid = _pedido_traduzido(api, tok, n=2)
    assert api.banco.lote(lid)["status"] == "aprovado"
    assert api.banco.trabalho() == [] and api.banco.fila()["pendentes"] == 0  # nenhum pod sobe por ele
    comp = _job(api)
    assert comp["tipo"] == "compilar"
    api.post(
        f"/api/worker/jobs/{comp['job_id']}/prompt",
        headers=h(WORKER),
        json={"worker": "pod-1", "prompt_en": "The camera slowly dollies in.", "compilador": "i2v@6"},
    )
    _concluir(api, comp, status="ok")
    assert _job(api) is None and api.banco.trabalho() == [] and api.banco.fila()["pendentes"] == 0
    _fotos_reais(api, lid, [(40, 90, 160), (30, 160, 60)])
    assert analisar_lote(api.banco, api.armazem, None, lid, _pronto(Falso()), limite=1) == 1  # a vez dos outros
    assert [t["pendentes"] for t in api.banco.trabalho()] == [1]  # agora sim: um vídeo liberado
    assert _job(api)["arquivo"] == "foto-0-v1.mp4" and _job(api) is None
    analisar_lote(api.banco, api.armazem, None, lid, _pronto(Falso()))
    assert _job(api)["arquivo"] == "foto-1-v1.mp4"


def test_an_order_with_every_photo_refused_closes_without_a_pod_and_the_photos_are_deleted(com_portao):
    api = cliente_teste(config_teste(com_portao))
    tok = _cliente(api, "Dono", plano="interno")
    lid = _pedido_traduzido(api, tok, n=2)  # aprovado no envio: a tradução está na fila
    _fotos_reais(api, lid, [(200, 30, 30), (201, 31, 31)])
    assert analisar_lote(api.banco, api.armazem, None, lid, _pronto(PorFoto({(200, 30, 30)}))) == 2
    lt = api.banco.lote(lid)
    assert lt["status"] == "concluido_com_falhas" and lt["fotos_apagadas"]  # fechou e as fotos saíram
    jobs = api.banco.jobs(lid)
    assert all(j["status"] == "cancelado" for j in jobs)  # a tradução também: nenhum pod sobe para nada
    assert api.banco.trabalho() == [] and _job(api) is None
    assert all(f["chave"] in api.armazem.apagados for f in api.banco.fotos(lid))
    lote = api.get(f"/api/lotes/{lid}", headers=h(tok)).json()
    assert lote["nao_sairam"] == [{"motivo": "foto_nao_aceita", "texto": VAGO, "videos": 2}]
    assert not lote.get("cobranca")  # plano interno: sem cobrança (no pago, o vídeo cancelado nunca é cobrado)


def test_the_owner_sees_the_refused_photo_blurred_and_can_release_it_before_approval(com_portao):
    api = cliente_teste(config_teste(com_portao))
    tok = _cliente(api, "Loja", saldo="10")
    lid = _pedido_traduzido(api, tok, n=2, duracao=4)
    _fotos_reais(api, lid, [(200, 30, 30), (40, 90, 160)])
    analisar_lote(api.banco, api.armazem, None, lid, _pronto(PorFoto({(200, 30, 30)})))
    recusada, liberada = api.banco.fotos(lid)
    assert (recusada["filtro"], liberada["filtro"]) == ("recusada", "ok")
    r = api.get(f"{PAINEL}/api/fotos/{recusada['id']}/recusada", headers=ADM)
    assert r.status_code == 200 and r.json()["expira_s"] == 600 and recusada["chave"] in r.json()["foto"]
    assert api.get(f"{PAINEL}/api/fotos/{liberada['id']}/recusada", headers=ADM).status_code == 404
    assert api.post(f"{PAINEL}/api/fotos/{liberada['id']}/liberar", headers=ADM).status_code == 409
    assert api.post(f"{PAINEL}/api/fotos/{recusada['id']}/liberar", headers=ADM).status_code == 200
    f = api.banco.foto(recusada["id"])
    assert f["filtro"] == "ok" and json.loads(f["filtro_detalhe"])["liberada_por"]
    with api.banco.tx() as c:
        acoes = [r["acao"] for r in c.all("SELECT acao FROM auditoria_admin ORDER BY quando")]
    assert "ver_foto_recusada" in acoes and "liberar_foto" in acoes
    eventos = [e["tipo"] for e in api.get(f"{PAINEL}/api/lotes/{lid}/auditoria", headers=ADM).json()["eventos"]]
    assert "foto_recusada" in eventos and "foto_liberada" in eventos
    # depois da aprovação, não dá mais para liberar
    assert api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM).status_code == 200
    assert all(j["status"] == "pendente" for j in api.banco.jobs(lid))  # a liberada vai virar vídeo
    assert api.post(f"{PAINEL}/api/fotos/{recusada['id']}/liberar", headers=ADM).status_code == 409


def test_a_photo_that_keeps_failing_is_given_up_and_storage_errors_are_retried(com_portao):
    api = cliente_teste(config_teste(com_portao))
    tok = _cliente(api, "Loja", saldo="10")
    lid = _pedido_traduzido(api, tok, n=3, duracao=4)
    fotos = _fotos_reais(api, lid, [(40, 90, 160), (30, 160, 60), (90, 90, 90)])
    a, b, d = fotos
    lidos = api.armazem.ler

    def ler(chave, limite):
        if chave == b["chave"]:
            raise OSError("conexão recusada")  # rede: tenta de novo, sem contar tentativa
        if chave == d["chave"]:
            raise OSError("não existe")
        return lidos(chave, limite)

    api.armazem.ler = ler
    api.armazem.objetos.pop(d["chave"])  # a foto d sumiu do armazenamento: nunca vai abrir
    quebrado = _pronto(Falso(erro=RuntimeError("sem memória")))
    for vez in range(filtro_fotos.TENTATIVAS):
        assert analisar_lote(api.banco, api.armazem, None, lid, quebrado) == (1 if vez == 0 else 0)
    estado = {f["nome"]: (f["filtro"], f["tentativas_analise"]) for f in api.banco.fotos(lid)}
    assert estado == {"foto-0": (None, 3), "foto-1": (None, None), "foto-2": ("invalida", None)}
    # a 4ª vez (o processo caiu nas 3 anteriores, por exemplo): desiste da foto, que vira inválida
    assert analisar_lote(api.banco, api.armazem, None, lid, quebrado) == 1
    f0 = api.banco.fotos(lid)[0]
    assert f0["filtro"] == "invalida" and "não terminou em 3 tentativas" in json.loads(f0["filtro_detalhe"])["erro"]
    assert api.banco.fotos(lid)[1]["filtro"] is None  # a da rede continua esperando


def test_the_separate_analyzer_interleaves_orders_and_reports_the_filter_state(com_portao, tmp_path, monkeypatch):
    armazem = Armazem()
    cfg = config_teste(com_portao, armazem, analise="separada")
    api = cliente_teste(cfg)
    tok = _cliente(api, "Loja", saldo="100")
    grande = _pedido_traduzido(api, tok, n=7, duracao=4)
    outro = _cliente(api, "Outra loja", saldo="100")
    pequeno = _pedido_traduzido(api, outro, n=1, duracao=4)
    armazem.ler = lambda chave, limite: _foto()
    feitos: list[str] = []

    def contar(banco, arm, analisar, lid, filtro=None, limite=None):
        n = analisar_lote(banco, arm, analisar, lid, filtro, limite)
        feitos.extend([lid] * n)
        return n

    monkeypatch.setattr(api_mod, "analisar_lote", contar)
    parar = threading.Event()
    t = threading.Thread(
        target=analisar_continuamente,
        args=(com_portao, armazem, None, parar),
        kwargs={"intervalo_s": 0.01, "vivo": tmp_path / "vivo", "filtro": filtro_fotos.FiltroFotos(Falso())},
    )
    t.start()
    try:
        for _ in range(500):
            if all(f["filtro"] for lid in (grande, pequeno) for f in com_portao.fotos(lid)):
                break
            time.sleep(0.01)
    finally:
        parar.set()
        t.join(10)
    assert all(f["filtro"] == "ok" for lid in (grande, pequeno) for f in com_portao.fotos(lid))
    # o pedido pequeno, que chegou depois, não esperou as 7 fotos do grande: 5 de um, a vez do outro
    assert feitos.index(pequeno) == api_mod.FOTOS_POR_VEZ
    seg = api.get(f"{PAINEL}/api/seguranca").json()
    assert seg["filtro_fotos"]["ok"] is True and seg["filtro_fotos"]["versao"] == filtro_fotos.VERSAO


def test_a_broken_classifier_blocks_every_video_and_the_panel_says_why(com_portao, tmp_path):
    class Quebrado(Falso):
        def carregar(self):
            raise filtro_imagem.FiltroError("dependência do classificador ausente: torch")

    armazem = Armazem()
    cfg = config_teste(com_portao, armazem, analise="separada")
    api = cliente_teste(cfg)
    tok = _cliente(api, "Dono", plano="interno")
    lid = _pedido_traduzido(api, tok, n=1)
    armazem.ler = lambda chave, limite: _foto()
    parar = threading.Event()
    pessoas = lambda dados: {"versao": "pessoas-v1", "pessoas": 0, "rostos": 0, "maior": 0.0}  # noqa: E731
    t = threading.Thread(
        target=analisar_continuamente,
        args=(com_portao, armazem, pessoas, parar),
        kwargs={"intervalo_s": 0.01, "filtro": filtro_fotos.FiltroFotos(Quebrado())},
    )
    t.start()
    try:
        for _ in range(500):
            if all(f["analise"] for f in com_portao.fotos(lid)):
                break
            time.sleep(0.01)
    finally:
        parar.set()
        t.join(10)
    (f,) = com_portao.fotos(lid)
    assert f["analise"] and f["filtro"] is None  # o aviso de pessoas segue; a foto não é liberada
    comp = _job(api)
    assert comp["tipo"] == "compilar"
    api.post(
        f"/api/worker/jobs/{comp['job_id']}/prompt",
        headers=h(WORKER),
        json={"worker": "pod-1", "prompt_en": "The camera slowly dollies in.", "compilador": "i2v@6"},
    )
    _concluir(api, comp, status="ok")
    assert _job(api) is None and com_portao.trabalho() == []
    seg = api.get(f"{PAINEL}/api/seguranca").json()
    assert seg["filtro_fotos"]["ok"] is False and "torch" in seg["filtro_fotos"]["erro"]
    assert any("filtro das fotos indisponível" in a for a in seg["avisos"])


def test_many_pods_never_take_a_video_whose_photo_was_not_released(com_portao):
    """10 pods ao mesmo tempo, saldo para 3 e metade das fotos ainda sem o filtro: saem só vídeos de fotos liberadas,
    nunca mais do que o saldo paga (no Postgres, com a trava do cliente e SKIP LOCKED)."""
    from test_db import _lote_pago, _pronto_para_render

    cid, _ = com_portao.criar_cliente("Loja")
    com_portao.movimentar(cid, 900, "Pix", "dono", referencia="pagamento:pix-1")
    lid = _lote_pago(com_portao, cid, 10, 300, cobranca="video")
    _pronto_para_render(com_portao, lid)
    fotos = com_portao.fotos(lid)
    liberadas = {f["id"] for f in fotos[::2]}
    for f in fotos:
        if f["id"] in liberadas:
            com_portao.salvar_filtro(f["id"], "ok", {"versao": filtro_fotos.VERSAO, "nota": 0.0})
    largada, pegos, lock = threading.Barrier(10), [], threading.Lock()

    def pod(n):
        largada.wait()
        j = com_portao.reivindicar(f"w{n}")
        with lock:
            pegos.append(j)

    ts = [threading.Thread(target=pod, args=(n,)) for n in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    saidos = [j for j in pegos if j is not None]
    assert len(saidos) == 3 and all(j["foto_id"] in liberadas for j in saidos)
    assert com_portao.saldo_livre(cid) == 0


def test_the_real_classifier_lets_the_sfw_fixtures_through():
    """O Freepik de verdade em CPU (só com `uv sync --extra filtro` e os pesos em RENDER2GO_MODELOS_SERVICO): as fotos
    SFW do repositório passam, com nota bem abaixo do limiar."""
    f = filtro_fotos.FiltroFotos()
    if not f.disponivel():
        pytest.skip(f"classificador indisponível neste ambiente: {f.erro}")
    for nome in ("produto-cafe.jpg", "produto-xicara.jpg", "pessoas-padaria-1900.jpg", "padrao-teste-640.png"):
        r = f.avaliar((FIXTURES / nome).read_bytes(), {"formato": "auto", "qualidade": "rapida"})
        assert r.filtro == "ok" and r.detalhe["nota"] < 0.1, (nome, r.detalhe)


def test_a_refused_photo_cancels_the_translation_only_of_its_own_text(com_portao):
    """Pedido em sequência: a foto 1, a única com o texto próprio, é recusada; a tradução desse texto é cancelada (não
    liga pod para nada), e a do texto padrão continua para as outras."""
    from test_sequencia import TEXTO, _sequencia

    api = cliente_teste(config_teste(com_portao))
    tok = _cliente(api, "Cafeteria", saldo="10")
    r = _sequencia(api, tok, [None, TEXTO, None])
    lid = r.json()["lote"]
    assert _enviar_tudo(api, tok, lid, r.json()["envios"]).status_code == 200
    assert api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM).status_code == 200
    _fotos_reais(api, lid, [(40, 90, 160), (200, 30, 30), (30, 160, 60)])
    analisar_lote(api.banco, api.armazem, None, lid, _pronto(PorFoto({(200, 30, 30)})))
    compilar = {(j["foto_id"] is None): j["status"] for j in api.banco.jobs(lid) if j["tipo"] == "compilar"}
    assert compilar == {True: "pendente", False: "cancelado"}  # o do pedido fica; o do texto da foto 1 sai
    renders = {j["arquivo"]: j["status"] for j in api.banco.jobs(lid) if j["tipo"] == "render"}
    assert renders == {"foto-0-v1.mp4": "pendente", "foto-1-v1.mp4": "cancelado", "foto-2-v1.mp4": "pendente"}
    assert api.banco.lote(lid)["status"] == "aprovado"
