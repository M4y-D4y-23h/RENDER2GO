"""Montagem de vídeos (ADR 0015): o que o cliente escolhe (textos, logo, vídeos), o ffmpeg de verdade (vídeos
sintéticos SFW), o logo pelo analisador, a fila do montador, a cobrança só na montagem pronta e as rotas da API."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from PIL import Image
from test_api import (
    ADM,
    CONTINUO,
    PAINEL,
    WORKER,
    Armazem,
    _cliente,
    _enviar_tudo,
    _job,
    _lote,
    cliente_teste,
    config_teste,
    h,
)

from render2go_servico import montagem as mt
from render2go_servico import pessoas
from render2go_servico.api import analisar_logos
from render2go_servico.db import MONTAGEM_TENTATIVAS, BancoError, SaldoInsuficiente
from render2go_servico.filtro_fotos import Avaliacao

FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
com_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg e ffprobe ausentes")
WOFF2 = Path(__file__).resolve().parents[1] / "web" / "publico" / "estatico" / "fonte-titulos.woff2"
# textos que quebrariam uma linha de filtros do ffmpeg se fossem interpolados nela (aspas, dois-pontos, vírgula,
# ponto e vírgula, %, barra): a fonte escreve todos, e nenhum deles chega ao ffmpeg
TEXTOS_ARDILOSOS = {
    "titulo": "Promoção: 'leve 2', pague 1; 100%",
    "preco": "R$ 49,90 / 50% off",
    "chamada": "Peça já: (11) 99999-0000, #pão & café",
}


class ArmazemComDados(Armazem):
    """O armazenamento falso dos testes da API, guardando os bytes (os vídeos e os logos de verdade)."""

    def __init__(self):
        super().__init__()
        self.dados: dict[str, bytes] = {}

    def ler(self, chave, limite):
        if chave in self.dados:
            if len(self.dados[chave]) > limite:
                raise OSError(f"{chave}: acima de {limite} bytes")
            return self.dados[chave]
        return super().ler(chave, limite)

    def gravar(self, chave, dados, tipo="application/octet-stream"):
        self.dados[chave] = bytes(dados)
        self.objetos[chave] = len(dados)

    def apagar(self, chave):
        super().apagar(chave)
        self.dados.pop(chave, None)


@pytest.fixture
def arquivos(tmp_path, monkeypatch):
    """A fonte dos textos: nos testes, a mesma Bricolage Grotesque das páginas (o recorte pt-BR em WOFF2, que o
    FreeType abre), conferida como o build confere a de verdade (bytes e SHA-256)."""
    pasta = tmp_path / "arquivos"
    pasta.mkdir()
    dados = WOFF2.read_bytes()
    (pasta / "fonte.ttf").write_bytes(dados)
    monkeypatch.setitem(mt.ARQUIVOS, "fonte.ttf", ("teste://fonte", len(dados), hashlib.sha256(dados).hexdigest()))
    return pasta


def _video(caminho: Path, segundos: int, w: int, h: int, cor: str = "orange") -> bytes:
    """Um vídeo sintético como os do Wan 2.2 (16 quadros por segundo, 16·d + 1 quadros), em H.264."""
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         f"testsrc2=size={w}x{h}:rate=16:duration={segundos + 1 / 16:.4f}", "-vf", f"drawbox=c={cor}@0.3:t=fill",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-f", "mp4", str(caminho)],
        check=True, timeout=120,
    )  # fmt: skip
    return caminho.read_bytes()


def _png(w=300, h=120, cor=(67, 56, 202, 255), modo="RGBA", formato="PNG") -> bytes:
    b = io.BytesIO()
    Image.new(modo, (w, h), cor if modo == "RGBA" else cor[:3]).save(b, formato)
    return b.getvalue()


def _sonda(caminho: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate"
         ":format=duration", "-of", "json", str(caminho)],
        capture_output=True, check=True, timeout=60,
    )  # fmt: skip
    return json.loads(r.stdout)


# -- o que o cliente escreve --------------------------------------------------------------------------------------


def test_texts_are_one_line_with_limits_and_only_characters_the_font_writes():
    assert mt.limpar_texto("  Promoção\n de\tinverno  ") == "Promoção de inverno"
    assert mt.limpar_texto("Promoção") == "Promoção"  # acento composto vira um caractere (NFC)
    for campo, texto in TEXTOS_ARDILOSOS.items():
        assert mt.problema_texto(campo, texto) is None, texto
    assert "40 caracteres" in mt.problema_texto("titulo", "x" * 41)
    assert mt.problema_texto("preco", "R$ " + "9" * 17) is None and "20" in mt.problema_texto("preco", "9" * 21)
    # o que não é letra, número ou a pontuação de um anúncio fica de fora, com a explicação (e nada é escrito)
    for ruim in ("🍕 pizza", "[0:v]drawtext=text=x", "a\\b", "{x}", "a|b", "<b>", "olá‮mundo", "\x00"):
        assert "caracteres que a montagem não escreve" in mt.problema_texto("chamada", ruim), ruim
    assert "U+202E" in mt.problema_texto("chamada", "olá‮mundo")  # o invisível aparece pelo código
    assert "desconhecido" in mt.problema_texto("legenda", "x")


def test_total_duration_is_the_sum_because_the_fade_dips_to_black_inside_each_video():
    assert mt.duracao_total([4, 5, 5], "corte") == mt.duracao_total([4, 5, 5], "fade") == 14


# -- a linha do ffmpeg ----------------------------------------------------------------------------------------------


def _entradas_do(cmd: list[str]) -> list[tuple[str, str]]:
    """(formato, arquivo) de cada entrada, conferindo que cada uma tem 1 thread, só o protocolo file e o formato."""
    out = []
    for i in (i for i, x in enumerate(cmd) if x == "-i"):
        assert cmd[i - 6 : i - 2] == ["-threads", "1", "-protocol_whitelist", "file"] and cmd[i - 2] == "-f"
        out.append((cmd[i - 1], cmd[i + 1]))
    return out


def test_each_video_is_normalized_alone_with_explicit_format_and_no_client_text(tmp_path):
    e = mt.Entrada(tmp_path / "video-00.mp4", 5)
    cmd = mt.comando_clipe(e, "9:16", "fade", primeiro=False, ultimo=False, saida=tmp_path / "clipe-00.mp4")
    assert cmd[:2] == ["ffmpeg", "-nostdin"] and _entradas_do(cmd) == [("mp4", str(e.caminho))]
    grafo = cmd[cmd.index("-filter_complex") + 1]
    assert "trim=duration=5" in grafo and "tpad=stop_mode=clone" in grafo and "fps=16" in grafo
    assert "fade=t=in:st=0:d=0.25" in grafo and "fade=t=out:st=4.7500:d=0.25" in grafo  # passa pelo preto
    assert "scale=720:1280" in grafo and "drawtext" not in grafo and "textfile" not in grafo
    assert cmd[-1] == str(tmp_path / "clipe-00.mp4") and cmd[-3:-1] == ["-protocol_whitelist", "file"]
    # nas pontas da montagem, sem o preto (o 1º quadro vira a capa); com o corte seco, sem esmaecimento nenhum
    ponta = mt.comando_clipe(e, "9:16", "fade", primeiro=True, ultimo=True, saida=tmp_path / "clipe-00.mp4")
    assert "fade=" not in ponta[ponta.index("-filter_complex") + 1]
    corte = mt.comando_clipe(e, "16:9", "corte", primeiro=False, ultimo=False, saida=tmp_path / "clipe-00.mp4")
    assert "fade=" not in corte[corte.index("-filter_complex") + 1]


def test_final_pass_reads_the_clips_one_at_a_time_and_adds_the_drawn_image_and_audio(tmp_path):
    clipes = [tmp_path / f"clipe-{i:02d}.mp4" for i in range(3)]
    assert mt.lista_concat(clipes) == "ffconcat version 1.0\nfile clipe-00.mp4\nfile clipe-01.mp4\nfile clipe-02.mp4\n"
    for ruim in ("clipe-0.mp4", "../x.mp4", "clipe-00.mp4'\nfile /etc/passwd", "clipe-00.mov"):
        with pytest.raises(mt.MontagemError, match="nome de clipe"):
            mt.lista_concat([tmp_path / ruim])
    lista, saida = tmp_path / "clipes.txt", tmp_path / "montagem.mp4"
    cmd = mt.comando_final(lista, "9:16", 14, tmp_path / "sobre.png", tmp_path / "trilha.ogg", "ogg", saida)
    assert _entradas_do(cmd) == [("concat", str(lista)), ("png_pipe", str(tmp_path / "sobre.png")),
                                 ("ogg", str(tmp_path / "trilha.ogg"))]  # fmt: skip
    assert cmd[cmd.index("-stream_loop") + 1] == "-1"  # a trilha curta se repete
    grafo = cmd[cmd.index("-filter_complex") + 1]
    assert "[0:v][1:v]overlay=0:0" in grafo and "[2:a]" in grafo and "atrim=duration=14.0000" in grafo
    assert "drawtext" not in grafo and "lavfi" not in cmd and "-safe" not in cmd  # o modo seguro do concat fica
    assert cmd[-1] == str(saida) and "+faststart" in cmd and cmd[cmd.index("-t") + 1] == "14.0000"
    # sem imagem e sem trilha: o vídeo como veio e o áudio em silêncio
    cmd = mt.comando_final(lista, "16:9", 9, None, None, None, saida)
    grafo = cmd[cmd.index("-filter_complex") + 1]
    assert len(_entradas_do(cmd)) == 1 and "anullsrc" in grafo and "overlay" not in grafo
    with pytest.raises(mt.MontagemError, match="trilha"):
        mt.comando_final(lista, "1:1", 9, None, tmp_path / "x.aiff", "aiff", saida)
    with pytest.raises(mt.MontagemError, match="de 2 a 10"):
        mt.conferir_pedido([mt.Entrada(tmp_path / "a.mp4", 4)], "9:16", "corte")
    with pytest.raises(mt.MontagemError, match="desconhecidos"):
        mt.conferir_pedido([mt.Entrada(tmp_path / "a.mp4", 4)] * 2, "4:5", "corte")


def test_ffmpeg_runs_without_the_container_secrets_and_is_killed_on_the_clock(tmp_path, monkeypatch):
    monkeypatch.setenv("RENDER2GO_R2_SECRET_ACCESS_KEY", "segredo-que-nao-pode-vazar")
    monkeypatch.setenv("RENDER2GO_DB_URL", "postgresql://gpu2go:senha@postgres/gpu2go")
    visto = {}
    real = subprocess.Popen

    def popen(cmd, **kw):
        visto.update(kw)
        return real(cmd, **kw)

    monkeypatch.setattr(mt.subprocess, "Popen", popen)
    mt.executar(["true"], tmp_path)
    assert set(visto["env"]) == {"PATH", "LANG", "HOME"} and visto["stdin"] == subprocess.DEVNULL
    assert visto["start_new_session"] and visto["cwd"] == tmp_path
    t0 = time.monotonic()
    with pytest.raises(mt.MontagemError, match="passou de"):
        mt.executar(["sleep", "30"], tmp_path, tempo_s=0.5)
    assert time.monotonic() - t0 < 10
    with pytest.raises(mt.MontagemError, match="saiu com 3"):
        mt.executar(["sh", "-c", "echo falhou >&2; exit 3"], tmp_path)


# -- o logo e a imagem por cima --------------------------------------------------------------------------------------


def test_logo_is_reencoded_as_a_small_png_and_anything_else_is_refused():
    grande = mt.logo_png(_png(2000, 800))
    with Image.open(io.BytesIO(grande)) as im:
        assert im.format == "PNG" and im.mode == "RGBA" and max(im.size) == mt.LOGO_LADO
    with Image.open(io.BytesIO(mt.logo_png(_png(200, 80, modo="RGB", formato="JPEG")))) as im:
        assert im.format == "PNG" and im.size == (200, 80)
    with Image.open(io.BytesIO(mt.logo_png(_png(120, 120, formato="WEBP")))) as im:
        assert im.format == "PNG"
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    gif = _png(10, 10, modo="RGB", formato="GIF")
    for ruim in (svg, b"<html><body>oi</body></html>", gif, b"", b"\x89PNG\r\n\x1a\n" + b"lixo" * 10):
        with pytest.raises((pessoas.FotoInvalida, OSError, ValueError, SyntaxError)):
            mt.logo_png(ruim)
    jpeg = _png(300, 300, modo="RGB", formato="JPEG")
    with pytest.raises((OSError, ValueError, SyntaxError)):
        mt.logo_png(jpeg[: len(jpeg) // 3])  # cortado no meio
    # acima do teto do serviço (50 MP, lido do cabeçalho): 8000 × 8000 (64 MP) nem é decodificado
    b = io.BytesIO()
    Image.new("1", (8000, 8000)).save(b, "PNG")
    with pytest.raises(pessoas.FotoInvalida, match="megapixels"):
        mt.logo_png(b.getvalue())


def test_overlay_has_the_video_size_and_is_skipped_without_texts_or_logo(arquivos):
    fonte = arquivos / "fonte.ttf"
    assert mt.sobreposicao("9:16", {"titulo": "", "preco": ""}, None, fonte) is None
    for formato, (largura, altura) in mt.FORMATOS.items():
        png = mt.sobreposicao(formato, TEXTOS_ARDILOSOS, mt.logo_png(_png()), fonte)
        with Image.open(io.BytesIO(png)) as im:
            assert im.size == (largura, altura) and im.mode == "RGBA"
            alfa = im.getchannel("A")
            assert alfa.getpixel((largura // 2, altura // 2)) == 0  # o meio do vídeo fica livre
            assert alfa.getbbox() is not None
    # só o logo, sem texto: não precisa da fonte
    assert mt.sobreposicao("1:1", {}, mt.logo_png(_png()), Path("/nao/existe.ttf")) is not None


# -- a montagem de verdade ----------------------------------------------------------------------------------------


@com_ffmpeg
@pytest.mark.parametrize(("formato", "transicao"), [("9:16", "fade"), ("1:1", "corte"), ("16:9", "fade")])
def test_real_montage_has_h264_aac_the_format_and_the_expected_duration(tmp_path, arquivos, formato, transicao):
    # um vídeo vertical, um horizontal e um quadrado: cada um inteiro, sobre o fundo desfocado
    entradas = [
        mt.Entrada(tmp_path / "a.mp4", 3), mt.Entrada(tmp_path / "b.mp4", 2), mt.Entrada(tmp_path / "c.mp4", 2),
    ]  # fmt: skip
    _video(entradas[0].caminho, 3, 480, 832)
    _video(entradas[1].caminho, 2, 832, 480, "blue")
    _video(entradas[2].caminho, 1, 624, 624, "green")  # mais curto que o pedido: o último quadro se repete
    pasta = tmp_path / "trabalho"
    pasta.mkdir()
    r = mt.montar(entradas, formato, transicao, TEXTOS_ARDILOSOS, mt.logo_png(_png()), None, pasta, arquivos)
    info = _sonda(r.caminho)
    largura, altura = mt.FORMATOS[formato]
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (video["codec_name"], video["width"], video["height"]) == ("h264", largura, altura)
    assert video["r_frame_rate"] == "16/1"
    assert audio["codec_name"] == "aac"
    assert abs(float(info["format"]["duration"]) - 7) <= 0.15
    assert r.bytes == r.caminho.stat().st_size > 0


@com_ffmpeg
def test_montage_with_a_library_track_and_fail_closed_without_the_font(tmp_path, arquivos, monkeypatch):
    for nome in ("a", "b"):
        _video(tmp_path / f"{nome}.mp4", 2, 480, 832)
    entradas = [mt.Entrada(tmp_path / "a.mp4", 2), mt.Entrada(tmp_path / "b.mp4", 2)]
    faixa = arquivos / "faixa.ogg"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
                    "-c:a", "libvorbis", "-f", "ogg", str(faixa)], check=True, timeout=60)  # fmt: skip
    dados = faixa.read_bytes()
    monkeypatch.setitem(mt.TRILHAS, "teste", {
        "titulo": "Lá", "autor": "teste", "licenca": "CC0", "arquivo": "faixa.ogg", "formato": "ogg",
        "url": "teste://faixa", "bytes": len(dados), "sha256": hashlib.sha256(dados).hexdigest(),
    })  # fmt: skip
    pasta = tmp_path / "t1"
    pasta.mkdir()
    r = mt.montar(entradas, "9:16", "corte", {}, None, "teste", pasta, arquivos)  # a faixa curta se repete
    assert abs(r.duracao - 4) <= 0.15 and any(s["codec_name"] == "aac" for s in _sonda(r.caminho)["streams"])
    # a faixa mudou depois do build (outro SHA-256): a montagem não sai
    faixa.write_bytes(dados + b"x")
    (tmp_path / "t2").mkdir()
    with pytest.raises(mt.MontagemError) as e:
        mt.montar(entradas, "9:16", "corte", {}, None, "teste", tmp_path / "t2", arquivos)
    assert e.value.motivo == "a trilha escolhida não está disponível"
    # sem a fonte conferida, a montagem com texto não sai; sem texto, sai
    (arquivos / "fonte.ttf").write_bytes(b"outra fonte")
    (tmp_path / "t3").mkdir()
    with pytest.raises(mt.MontagemError, match="fonte.ttf"):
        mt.montar(entradas, "9:16", "corte", {"titulo": "Oi"}, None, None, tmp_path / "t3", arquivos)
    (tmp_path / "t4").mkdir()
    assert mt.montar(entradas, "9:16", "corte", {}, None, None, tmp_path / "t4", arquivos).duracao > 3.8


@com_ffmpeg
def test_corrupt_source_video_fails_the_montage(tmp_path, arquivos):
    _video(tmp_path / "a.mp4", 2, 480, 832)
    (tmp_path / "b.mp4").write_bytes(b"\x00\x00\x00\x18ftypisom" + os.urandom(2000))
    with pytest.raises(mt.MontagemError):
        mt.montar([mt.Entrada(tmp_path / "a.mp4", 2), mt.Entrada(tmp_path / "b.mp4", 2)], "1:1", "corte", {},
                  None, None, tmp_path, arquivos)  # fmt: skip


# -- banco: reserva, cobrança e fila -----------------------------------------------------------------------------


def _videos_prontos(b, cid, n, duracao=4):
    """n vídeos prontos da conta (um pedido do plano pago já entregue, cobrado por vídeo)."""
    fotos = [
        {"nome": f"f{i}", "tipo": "image/jpeg", "bytes": 100, "chave": f"entrada/{cid}/{{lote}}/f{i}.jpg"}
        for i in range(n)
    ]
    lid = b.criar_lote(cid, {"prompt": "a câmera se aproxima", "duracao": duracao, "qualidade": "rapida"}, fotos,
                       preco_video=None)  # fmt: skip
    b.confirmar_envio(lid, {f["chave"]: f["bytes"] for f in b.fotos(lid)})
    with b.tx() as c:  # as fotos liberadas pelo filtro das fotos (o banco do teste de invasão exige, como a produção)
        c.run("UPDATE fotos SET filtro = 'ok' WHERE lote_id = ?", (lid,))
    tarefas = [{"foto_id": f["id"], "variacao": 1, "seed": 1, "arquivo": f"{f['nome']}-v1.mp4"} for f in b.fotos(lid)]
    b.aprovar(lid, "dono", tarefas)
    comp = b.reivindicar("pod", tipos=["compilar"])
    b.definir_prompt(comp["lote_id"], "p", "i2v@6")
    b.concluir(comp["id"], "pod", "ok")
    ids = []
    while (j := b.reivindicar("pod")) is not None:
        b.concluir(j["id"], "pod", "ok")
        ids.append(j["id"])
    return lid, ids


PEDIDO = {"versao": mt.VERSAO, "formato": "9:16", "transicao": "fade", "textos": {"titulo": "Oi"}, "trilha": None}


def test_montage_reserves_the_price_charges_once_when_ready_and_never_on_error(banco):
    cid, _ = banco.criar_cliente("Loja")
    banco.movimentar(cid, 300, "Pix", "dono", referencia="pagamento:pix-1")
    _, ids = _videos_prontos(banco, cid, 3)
    m = banco.criar_montagem(cid, ids[:2], PEDIDO, 200)
    assert m["status"] == "pendente" and [v["duracao"] for v in m["pedido"]["videos"]] == [4, 4]
    assert banco.saldo(cid) == 300 and banco.saldo_livre(cid) == 100  # reservado, ainda não cobrado
    with pytest.raises(SaldoInsuficiente):  # a reserva vale para a próxima montagem
        banco.criar_montagem(cid, ids[1:], PEDIDO, 200)
    x = banco.reivindicar_montagem("montador", 60)
    assert x["id"] == m["id"] and [v["job"] for v in x["videos"]] == ids[:2] and x["tentativas"] == 1
    assert banco.reivindicar_montagem("outro", 60) is None
    with pytest.raises(BancoError):
        banco.concluir_montagem(m["id"], "outro", "ok")
    banco.concluir_montagem(m["id"], "montador", "ok", arquivo="montagem-x.mp4", bytes_video=10, duracao=7.5,
                            segundos=2.0)  # fmt: skip
    assert banco.saldo(cid) == 100 and banco.saldo_livre(cid) == 100
    (mov,) = [x for x in banco.extrato(cid) if x["tipo"] == "montagem"]
    assert mov["valor"] == -200 and mov["lote_id"] == m["id"] and "2 vídeo(s) em 9:16" in mov["descricao"]
    with pytest.raises(BancoError):  # concluir de novo não cobra de novo
        banco.concluir_montagem(m["id"], "montador", "ok")
    # a que falha não custa nada, e a reserva volta
    banco.movimentar(cid, 200, "Pix", "dono", referencia="pagamento:pix-2")
    m2 = banco.criar_montagem(cid, ids[1:], PEDIDO, 200)
    assert banco.saldo_livre(cid) == 100
    banco.reivindicar_montagem("montador", 60)
    banco.concluir_montagem(m2["id"], "montador", "erro", erro="ffmpeg saiu com 1", motivo="a montagem não saiu")
    assert banco.saldo(cid) == 300 and banco.saldo_livre(cid) == 300
    assert banco.montagem(m2["id"])["motivo"] == "a montagem não saiu"


def test_montage_only_takes_ready_videos_of_the_same_account(banco):
    a, _ = banco.criar_cliente("A")
    b, _ = banco.criar_cliente("B")
    _, de_a = _videos_prontos(banco, a, 2)
    _, de_b = _videos_prontos(banco, b, 1)
    with pytest.raises(BancoError, match="não encontrado"):
        banco.criar_montagem(a, [de_a[0], de_b[0]], PEDIDO, None)  # o vídeo de outra conta
    banco.excluir_video(de_a[1], a)
    with pytest.raises(BancoError, match="não encontrado"):
        banco.criar_montagem(a, de_a, PEDIDO, None)  # excluído
    with banco.tx() as c:  # pronto há mais de 7 dias
        c.run("UPDATE jobs SET atualizado = ? WHERE id = ?", (time.time() - 8 * 86400, de_b[0]))
    _, mais_b = _videos_prontos(banco, b, 1)
    with pytest.raises(BancoError, match="7 dias"):
        banco.criar_montagem(b, [de_b[0], mais_b[0]], PEDIDO, None)


def test_video_deleted_while_the_montage_waits_ends_it_without_charge(banco):
    cid, _ = banco.criar_cliente("Loja")
    banco.movimentar(cid, 500, "Pix", "dono", referencia="pagamento:pix-1")
    _, ids = _videos_prontos(banco, cid, 2)
    m = banco.criar_montagem(cid, ids, PEDIDO, 200)
    banco.excluir_video(ids[0], cid)
    assert banco.reivindicar_montagem("montador", 60) is None
    feita = banco.montagem(m["id"])
    assert feita["status"] == "erro" and feita["motivo"] == "um dos vídeos escolhidos foi excluído"
    assert banco.saldo(cid) == 500 and banco.saldo_livre(cid) == 500


def test_stuck_montages_go_back_to_the_queue_then_fail_and_unsent_logos_cancel(banco):
    cid, _ = banco.criar_cliente("Loja")
    _, ids = _videos_prontos(banco, cid, 2)
    m = banco.criar_montagem(cid, ids, PEDIDO, None)
    for tentativa in range(1, MONTAGEM_TENTATIVAS + 1):
        x = banco.reivindicar_montagem(f"montador-{tentativa}", -1)  # o prazo já vencido: o montador caiu
        assert x["id"] == m["id"] and x["tentativas"] == tentativa
    fechadas = banco.vencer_montagens()
    assert [f["id"] for f in fechadas] == [m["id"]] and banco.montagem(m["id"])["status"] == "erro"
    # o logo declarado que nunca chegou: cancelada em 1 h, sem custo
    com_logo = banco.criar_montagem(cid, ids, PEDIDO, None, logo={"tipo": "image/png", "bytes": 10, "ext": "png"})
    assert com_logo["status"] == "aguardando_envio"
    with banco.tx() as c:
        c.run("UPDATE montagens SET criado = ? WHERE id = ?", (time.time() - 2 * 3600, com_logo["id"]))
    assert [f["status"] for f in banco.vencer_montagens()] == ["cancelado"]
    assert banco.montagem(com_logo["id"])["motivo"] == "o logo não chegou"


def test_montage_reservation_holds_against_ten_pods_taking_videos_at_once(banco):
    """O teste dos 10 pods com saldo para 3 (ADR 0012), com uma montagem reservada na mesma conta: o saldo de
    R$ 12 paga a montagem (R$ 3) e só 3 vídeos de R$ 3."""
    cid, _ = banco.criar_cliente("Loja")
    banco.movimentar(cid, 1200, "Pix", "dono", referencia="pagamento:pix-1")
    _, prontos = _videos_prontos(banco, cid, 2)
    banco.criar_montagem(cid, prontos, PEDIDO, 300)
    fotos = [
        {"nome": f"g{i}", "tipo": "image/jpeg", "bytes": 100, "chave": f"entrada/{cid}/{{lote}}/g{i}.jpg"}
        for i in range(10)
    ]
    lid = banco.criar_lote(cid, {"prompt": "gira", "duracao": 4, "qualidade": "rapida"}, fotos, preco_video=300,
                           cobranca="video")  # fmt: skip
    banco.confirmar_envio(lid, {f["chave"]: 100 for f in banco.fotos(lid)})
    banco.aprovar(lid, "dono", [{"foto_id": f["id"], "variacao": 1, "seed": 1, "arquivo": f"{f['nome']}.mp4"}
                                for f in banco.fotos(lid)])  # fmt: skip
    comp = banco.reivindicar("pod", tipos=["compilar"])
    banco.definir_prompt(comp["lote_id"], "p", "i2v@6")
    banco.concluir(comp["id"], "pod", "ok")
    largada, pegos, lock = threading.Barrier(10), [], threading.Lock()

    def pod(n):
        largada.wait()
        j = banco.reivindicar(f"w{n}")
        with lock:
            pegos.append(j)

    ts = [threading.Thread(target=pod, args=(n,)) for n in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    saidos = [j for j in pegos if j is not None]
    assert len(saidos) == 3 and banco.saldo_livre(cid) == 0
    for j in saidos:
        banco.concluir(j["id"], j["worker"], "ok")
    m = banco.reivindicar_montagem("montador", 60)
    banco.concluir_montagem(m["id"], "montador", "ok", arquivo="m.mp4")
    assert banco.saldo(cid) == 0  # nunca negativo


def test_two_montadores_never_take_the_same_montage(banco):
    cid, _ = banco.criar_cliente("Loja")
    _, ids = _videos_prontos(banco, cid, 2)
    criadas = {banco.criar_montagem(cid, ids, PEDIDO, None)["id"] for _ in range(4)}
    largada, pegas, lock = threading.Barrier(6), [], threading.Lock()

    def montador(n):
        largada.wait()
        m = banco.reivindicar_montagem(f"montador-{n}", 60)
        with lock:
            pegas.append(m and m["id"])

    ts = [threading.Thread(target=montador, args=(n,)) for n in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(x for x in pegas if x) == sorted(criadas) and pegas.count(None) == 2


def test_account_data_has_montages_and_open_montage_blocks_deletion(banco):
    cid, _ = banco.criar_cliente("Loja")
    _, ids = _videos_prontos(banco, cid, 2)
    m = banco.criar_montagem(cid, ids, PEDIDO, None, ip="203.0.113.7")
    assert [x["id"] for x in banco.dados_da_conta(cid)["montagens"]] == [m["id"]]
    with pytest.raises(BancoError, match="montagens em andamento"):
        banco.excluir_conta(cid)


# -- o logo no analisador ------------------------------------------------------------------------------------------


class FiltroFalso:
    def __init__(self, resultado="ok"):
        self.resultado, self.vistos = resultado, []

    def avaliar(self, dados, pedido):
        self.vistos.append(pedido)
        return Avaliacao(self.resultado, {"versao": "fotos-v1", "nota": 0.9 if self.resultado != "ok" else 0.01,
                                          "nota_alta": 0.0})  # fmt: skip


def _com_logo(banco, armazem, dados, tipo="image/png"):
    cid, _ = banco.criar_cliente("Loja")
    _, ids = _videos_prontos(banco, cid, 2)
    ext = mt.LOGO_TIPOS[tipo]
    m = banco.criar_montagem(cid, ids, PEDIDO, None, logo={"tipo": tipo, "bytes": len(dados), "ext": ext})
    chave = mt.chave_logo(cid, m["id"], f"logo-original.{ext}")
    armazem.gravar(chave, dados, tipo)
    banco.confirmar_logo(m["id"], cid, armazem.tamanho(chave))
    return cid, m, chave


def test_logo_passes_the_photo_filter_and_is_rewritten_as_png_before_the_queue(banco):
    armazem = ArmazemComDados()
    cid, m, chave = _com_logo(banco, armazem, _png(900, 300, formato="WEBP"), "image/webp")
    assert banco.montagem(m["id"])["status"] == "aguardando_logo"
    assert analisar_logos(banco, armazem, None) == 0  # sem o classificador, nada é liberado (fail-closed)
    assert banco.reivindicar_montagem("montador", 60) is None
    filtro = FiltroFalso("ok")
    assert analisar_logos(banco, armazem, filtro) == 1
    png = armazem.dados[mt.chave_logo(cid, m["id"], "logo.png")]
    with Image.open(io.BytesIO(png)) as im:
        assert im.format == "PNG" and max(im.size) <= mt.LOGO_LADO
    feita = banco.montagem(m["id"])
    assert feita["status"] == "pendente" and feita["logo_filtro"] == "ok"
    assert json.loads(feita["logo_detalhe"])["sha256"] == hashlib.sha256(armazem.dados[chave]).hexdigest()
    assert banco.reivindicar_montagem("montador", 60)["id"] == m["id"]


@pytest.mark.parametrize(("resultado", "dados"), [("recusada", None), ("ok", b"<svg>nao sou foto</svg>")])
def test_refused_or_invalid_logo_ends_the_montage_without_charge_and_is_deleted(banco, resultado, dados):
    armazem = ArmazemComDados()
    cid, m, chave = _com_logo(banco, armazem, dados or _png())
    banco.movimentar(cid, 500, "Pix", "dono", referencia="pagamento:pix-1")
    with banco.tx() as c:
        c.run("UPDATE montagens SET preco = 200 WHERE id = ?", (m["id"],))
    assert banco.saldo_livre(cid) == 300
    assert analisar_logos(banco, armazem, FiltroFalso(resultado)) == 1
    feita = banco.montagem(m["id"])
    assert feita["status"] == "erro" and feita["motivo"] == "o logo não pôde ser usado"
    if resultado == "recusada":
        assert feita["logo_filtro"] == "recusada" and "nota 0.90" in feita["erro"]
    else:  # o filtro passou, mas o arquivo não abre como foto: inválido
        assert feita["logo_filtro"] == "invalida" and "inválida" in feita["erro"]
    assert banco.saldo(cid) == 500 and banco.saldo_livre(cid) == 500
    assert chave in armazem.apagados and mt.chave_logo(cid, m["id"], "logo.png") not in armazem.dados


def test_logo_that_never_finishes_analysis_is_given_up(banco):
    armazem = ArmazemComDados()
    _, m, _ = _com_logo(banco, armazem, _png())

    class Derruba:
        def avaliar(self, dados, pedido):
            raise RuntimeError("o classificador caiu")

    for _ in range(3):
        assert analisar_logos(banco, armazem, Derruba()) == 0
    assert analisar_logos(banco, armazem, FiltroFalso()) == 1  # a 4ª vez desiste
    assert banco.montagem(m["id"])["status"] == "erro"


# -- o montador de ponta a ponta (armazenamento falso, ffmpeg de verdade) ----------------------------------------


@com_ffmpeg
def test_montador_downloads_mounts_uploads_and_concludes(banco, tmp_path, arquivos):
    armazem = ArmazemComDados()
    cid, _ = banco.criar_cliente("Loja")
    banco.movimentar(cid, 500, "Pix", "dono", referencia="pagamento:pix-1")
    lid, ids = _videos_prontos(banco, cid, 2, duracao=2)
    arquivos_job = {x["id"]: x["arquivo"] for x in banco.jobs(lid)}
    for i, j in enumerate(ids):
        video = _video(tmp_path / f"v{i}.mp4", 2, 480, 832)
        armazem.gravar(f"saida/{cid}/{lid}/{arquivos_job[j]}", video, "video/mp4")
    m = banco.criar_montagem(cid, ids, {**PEDIDO, "textos": TEXTOS_ARDILOSOS}, 150)
    parar = threading.Event()
    vivo = tmp_path / "vivo"

    def rodar():
        mt.montar_continuamente(banco, armazem, parar, intervalo_s=0.1, vivo=vivo, arquivos=arquivos)

    t = threading.Thread(target=rodar)
    t.start()
    try:
        for _ in range(300):
            if banco.montagem(m["id"])["status"] in ("ok", "erro"):
                break
            time.sleep(0.1)
    finally:
        parar.set()
        t.join(30)
    feita = banco.montagem(m["id"])
    assert feita["status"] == "ok", feita["erro"]
    chave = f"saida/{cid}/{m['id']}/{feita['arquivo']}"
    saida = tmp_path / "saida.mp4"
    saida.write_bytes(armazem.dados[chave])
    assert abs(float(_sonda(saida)["format"]["duration"]) - 4) <= 0.15
    assert feita["arquivo"].startswith("montagem-") and feita["duracao"] and feita["segundos"] is not None
    assert banco.saldo(cid) == 350 and vivo.exists() and banco.ler_estado("montador_vivo") is not None


# -- API: quem pode, o que é conferido e o que o cliente vê --------------------------------------------------------


def _api(banco, admin=True, **kw):
    """A API dos testes com o armazenamento que guarda bytes; `admin`: já com a sessão do painel (o mesmo código do
    autenticador não vale duas vezes: as outras instâncias no mesmo teste entram sem o painel)."""
    return cliente_teste(config_teste(banco, ArmazemComDados(), **kw), admin=admin)


def _prontos_pela_api(api, tok, n=2):
    """n vídeos de 4 s prontos pela fila de verdade (worker de teste)."""
    r = _lote(api, tok, n=n, duracao=4)
    lid = r.json()["lote"]
    _enviar_tudo(api, tok, lid, r.json()["envios"])
    api.post(f"{PAINEL}/api/lotes/{lid}/aprovar", headers=ADM)
    comp = _job(api)
    api.post(f"/api/worker/jobs/{comp['job_id']}/prompt", headers=h(WORKER),
             json={"worker": "pod-1", "prompt_en": "The camera slowly dollies in.", "compilador": "i2v@6"})  # fmt: skip
    api.post(f"/api/worker/jobs/{comp['job_id']}/concluir", headers=h(WORKER), json={"worker": "pod-1", "status": "ok"})
    ids = []
    for _ in range(n):
        j = _job(api)
        chave = j["video_url"].split("armazem.teste/")[1].split("?")[0]
        api.armazem.objetos[chave] = 7000
        corpo = {"worker": "pod-1", "status": "ok", "bytes_video": 7000, "similaridade": 0.999, **CONTINUO}
        assert api.post(f"/api/worker/jobs/{j['job_id']}/concluir", headers=h(WORKER), json=corpo).status_code == 200
        ids.append(j["job_id"])
    return ids


def _corpo(ids, **kw):
    return {"videos": ids, "formato": "9:16", "transicao": "fade", **kw}


def test_only_the_internal_plan_mounts_by_default_and_paid_needs_mode_and_price(banco):
    api = _api(banco)
    interno = _cliente(api, "Dono", plano="interno")
    pago = _cliente(api, "Loja", plano="pago", saldo="50")
    teste = _cliente(api, "Visita", plano="teste")
    ids = _prontos_pela_api(api, interno)
    info = api.get("/api/montagens", headers=h(interno)).json()
    assert info["disponivel"] and info["preco"] is None and info["limites"]["videos"] == [2, 10]
    assert {f["id"] for f in info["formatos"]} == {"9:16", "1:1", "16:9"} and info["trilhas"] == []
    r = api.post("/api/montagens", json=_corpo(ids), headers=h(interno))
    assert r.status_code == 200 and r.json()["status"] == "pendente" and r.json()["envio"] is None
    for tok in (pago, teste):
        assert not api.get("/api/montagens", headers=h(tok)).json()["disponivel"]
        assert api.post("/api/montagens", json=_corpo(ids), headers=h(tok)).status_code == 403
    # "Tenho interesse" de quem ainda não pode usar (painel, Interesses)
    assert api.post("/api/interesses", json={"finalidade": "recurso:montagem"}, headers=h(pago)).status_code == 200
    assert api.get("/api/montagens", headers=h(pago)).json()["interesse"] is True
    # todos, sem preço: o pago continua sem; com preço, monta (e o teste gratuito nunca)
    assert not _api(banco, False, montagem="todos").get("/api/montagens", headers=h(pago)).json()["disponivel"]
    todos = _api(banco, False, montagem="todos", preco_montagem=150)
    info = todos.get("/api/montagens", headers=h(pago)).json()
    assert info["disponivel"] and info["preco"] == 150
    assert not todos.get("/api/montagens", headers=h(teste)).json()["disponivel"]
    desligada = _api(banco, False, montagem="desligada")
    assert desligada.post("/api/montagens", json=_corpo(ids), headers=h(interno)).status_code == 403


def test_create_checks_videos_texts_c1_and_logo_and_logs_the_access(banco):
    api = _api(banco)
    tok = _cliente(api, "Dono", plano="interno")
    ids = _prontos_pela_api(api, tok, 3)
    outro = _prontos_pela_api(api, _cliente(api, "Outro", plano="interno"), 2)

    def criar(**kw):
        return api.post("/api/montagens", json=_corpo(**{"ids": ids[:2], **kw}), headers=h(tok))

    assert "de 2 a 10" in criar(ids=ids[:1]).json()["detail"]
    assert "duas vezes" in criar(ids=[ids[0], ids[0]]).json()["detail"]
    assert criar(ids=[ids[0], outro[0]]).status_code == 422  # vídeo de outra conta
    assert criar(formato="4:5").status_code == 422 and criar(transicao="girar").status_code == 422
    assert criar(trilha="nao-existe").status_code == 422
    assert "40 caracteres" in criar(textos={"titulo": "x" * 41}).json()["detail"]
    assert "não escreve" in criar(textos={"chamada": "[0:v]drawtext=text=oi"}).json()["detail"]
    r = criar(textos={"titulo": "um homem segura uma arma"})
    assert r.status_code == 422 and r.json()["detail"].startswith("Título:") and "armas" in r.json()["detail"]
    with banco.tx() as c:
        (reg,) = c.all("SELECT detalhe FROM acessos WHERE evento = 'montagem_recusada_filtro'")
    assert "titulo" in reg["detalhe"]
    assert "JPG, PNG ou WebP" in criar(logo={"tipo": "image/svg+xml", "bytes": 100}).json()["detail"]
    assert "MB" in criar(logo={"tipo": "image/png", "bytes": mt.LOGO_BYTES + 1}).json()["detail"]
    # os textos ardilosos para o ffmpeg passam (a fonte escreve todos) e ficam como o cliente escreveu
    r = criar(textos=TEXTOS_ARDILOSOS, logo={"tipo": "image/png", "bytes": 1234})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["status"] == "aguardando_envio" and corpo["envio"]["tipo"] == "image/png"
    chave = corpo["envio"]["url"].split("armazem.teste/")[1].split("?")[0]
    assert chave == f"entrada/{banco.cliente_por_token(tok)['id']}/{corpo['montagem']}/logo-original.png"
    assert api.armazem.assinados[chave] == {"tipo": "image/png", "bytes": 1234}  # só os bytes declarados
    m = banco.montagem(corpo["montagem"])
    assert json.loads(m["pedido"])["textos"] == TEXTOS_ARDILOSOS
    # o logo ainda não chegou: o envio não confirma; chegou: espera o analisador
    assert api.post(f"/api/montagens/{corpo['montagem']}/enviado", headers=h(tok)).status_code == 409
    api.armazem.objetos[chave] = 1234
    r = api.post(f"/api/montagens/{corpo['montagem']}/enviado", headers=h(tok))
    assert r.status_code == 200 and banco.montagem(corpo["montagem"])["status"] == "aguardando_logo"
    with banco.tx() as c:
        assert c.one("SELECT COUNT(*) AS n FROM acessos WHERE evento = 'montagem'")["n"] == 1
    # até 3 na fila por conta (a do logo já é uma)
    assert criar().status_code == criar().status_code == 200
    r = criar()
    assert r.status_code == 429 and "3 montagens" in r.json()["detail"]


def test_client_sees_the_short_reason_links_when_ready_and_can_delete(banco):
    api = _api(banco)
    tok = _cliente(api, "Dono", plano="interno")
    cid = banco.cliente_por_token(tok)["id"]
    ids = _prontos_pela_api(api, tok)
    pronta = api.post("/api/montagens", json=_corpo(ids), headers=h(tok)).json()["montagem"]
    falha = api.post("/api/montagens", json=_corpo(ids, formato="1:1"), headers=h(tok)).json()["montagem"]
    m = banco.reivindicar_montagem("montador", 60)
    banco.concluir_montagem(m["id"], "montador", "ok", arquivo="montagem-2026-10-13-abc123.mp4", duracao=7.5)
    m = banco.reivindicar_montagem("montador", 60)
    banco.concluir_montagem(m["id"], "montador", "erro", erro="ffmpeg saiu com 1: Invalid data at /tmp/x",
                            motivo="a montagem não pôde ser feita")  # fmt: skip
    itens = {x["id"]: x for x in api.get("/api/montagens", headers=h(tok)).json()["itens"]}
    assert itens[pronta]["status"] == "ok" and "url" in itens[pronta] and "attachment" in itens[pronta]["baixar"]
    assert itens[falha]["motivo"] == "a montagem não pôde ser feita"
    assert "ffmpeg" not in json.dumps(itens) and "/tmp" not in json.dumps(itens)  # o erro técnico é do painel
    # o painel vê tudo (também o operador), e o erro técnico
    painel = api.get(f"{PAINEL}/api/montagens", headers=ADM).json()
    assert {x["id"] for x in painel["itens"]} == {pronta, falha} and painel["modo"] == "interno"
    assert "ffmpeg" in next(x for x in painel["itens"] if x["id"] == falha)["erro"]
    # excluir: o arquivo sai, o registro de acesso fica; outra conta não exclui
    outro = _cliente(api, "Outro", plano="interno")
    assert api.post(f"/api/montagens/{pronta}/excluir", headers=h(outro)).status_code == 404
    assert api.post(f"/api/montagens/{pronta}/excluir", headers=h(tok)).status_code == 200
    assert f"saida/{cid}/{pronta}/montagem-2026-10-13-abc123.mp4" in api.armazem.apagados
    assert "url" not in next(x for x in api.get("/api/montagens", headers=h(tok)).json()["itens"] if x["id"] == pronta)
    assert api.post(f"/api/montagens/{pronta}/excluir", headers=h(tok)).status_code == 404


def test_paid_montage_appears_in_the_statement_as_its_own_round(banco):
    api = _api(banco, montagem="todos", preco_montagem=150)
    tok = _cliente(api, "Loja", plano="pago", saldo="20")
    ids = _prontos_pela_api(api, tok)
    mid = api.post("/api/montagens", json=_corpo(ids), headers=h(tok)).json()["montagem"]
    m = banco.reivindicar_montagem("montador", 60)
    banco.concluir_montagem(m["id"], "montador", "ok", arquivo="montagem-x.mp4", duracao=7.5)
    x = api.get("/api/extrato?dias=30", headers=h(tok)).json()
    (rodada,) = [i for i in x["itens"] if i.get("lote") == mid]
    assert (rodada["modelo"], rodada["finalidade"], rodada["montagem"]) == ("Montagem", "Montagem de vídeos", True)
    assert (rodada["videos"], rodada["duracao"], rodada["resolucao"], rodada["cobrado"]) == (2, 8, "720p", 150)
    csv = api.get("/api/extrato.csv?dias=30", headers=h(tok)).content.decode("utf-8-sig")
    assert any(linha.split(";")[1] == "Montagem" and linha.split(";")[2] == mid for linha in csv.splitlines())
    # sem saldo livre para o preço: 402, com o valor (R$ 2,50 pagam os 2 vídeos e sobram R$ 0,50)
    pobre = _cliente(api, "Pobre", plano="pago", saldo="2,50")
    ids2 = _prontos_pela_api(api, pobre)
    r = api.post("/api/montagens", json=_corpo(ids2), headers=h(pobre))
    assert r.status_code == 402 and "R$ 1,50" in r.json()["detail"] and "R$ 0,50" in r.json()["detail"]
