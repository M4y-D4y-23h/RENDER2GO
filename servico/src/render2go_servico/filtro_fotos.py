"""Filtro das fotos no servidor (ADR 0005, ponto 1; pedido do dono em 12/10/2026): cada foto é avaliada antes de
qualquer pod recebê-la, e a que não passa nunca vira vídeo (não gasta GPU nem saldo).

Roda no contêiner `analisador` (o único que decodifica fotos de clientes, com memória limitada), na mesma volta do
aviso de pessoas, com o classificador e a regra dos quadros do vídeo (`render2go_worker.filtro_imagem`: Freepik
EVA02, aqui em CPU). Por foto, até duas imagens:
    - a foto inteira, redimensionada para 448 × 448 sem cortar (como no cartão do modelo);
    - o recorte no formato do vídeo, que vai ser o 1º quadro (a conversão do preparo do pod: rotação do EXIF,
      transparência sobre branco e corte no centro), quando ele deixa de fora 10% ou mais da foto.
Vale a pior das duas (`filtro_imagem.notas_foto`), com os limiares do .env (`filtro_imagem.limiares`). A redução
usa sempre filtro de área: amostrar pixels (vizinho mais próximo) deixaria passar uma imagem escondida que só
aparece em outra escala.

Resultado em `fotos.filtro`:
    ok         os vídeos da foto podem ir para um pod (`Banco.reivindicar` só entrega esses);
    recusada   o classificador barrou: os vídeos da foto são cancelados, sem custo;
    invalida   o arquivo não abre como foto (o pod também recusaria) ou a análise não terminou em TENTATIVAS vezes.
O cliente vê só "a foto não pôde ser usada", sem a nota e sem diferença entre recusada e inválida (pedido do dono:
"justificativa pouco clara, evitando que tentem burlar a segurança"); o painel mostra as notas e o dono pode ver a
foto recusada (desfocada, com registro na auditoria) e liberá-la antes da aprovação.

Fail-closed: sem o classificador (pesos ausentes, PyTorch que não carrega), nenhuma foto é liberada e nenhum vídeo
sai; o painel mostra o motivo em Segurança. Erro ao avaliar uma foto: ela fica sem resultado e é tentada de novo.

Imagem do serviço (Dockerfile): `uv sync --extra filtro` (PyTorch de CPU), os pesos do `models.lock.json`
(`nsfw_freepik_modelo`, conferidos por bytes e SHA-256) e `python -m render2go_servico.filtro_fotos conferir`, que
carrega o classificador e avalia uma imagem lisa (a build falha se algo não funcionar).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps
from render2go_worker import filtro_imagem, lote, preparo

from render2go_servico import pessoas

VERSAO = "fotos-v1"
LADO_MAX = 1280  # a foto é reduzida antes da avaliação (o classificador vê 448 px)
CORTE_MIN = 0.9  # o recorte do vídeo também é avaliado quando deixa de fora 10% ou mais da foto
TENTATIVAS = 3  # análises que não terminaram (erro, processo derrubado): depois disso, a foto é inválida
RECARREGAR_S = 600  # classificador que não carregou: nova tentativa a cada 10 min
THREADS = 1  # padrão de RENDER2GO_FILTRO_THREADS: o servidor (2 vCPU) divide a CPU com a API
RESULTADOS = ("ok", "recusada", "invalida")
# o que não abre como foto (o pod recusaria do mesmo jeito): vira "invalida", sem nova tentativa
FOTO_RUIM = (pessoas.FotoInvalida, OSError, ValueError, SyntaxError, EOFError, Image.DecompressionBombError)


class FiltroIndisponivel(Exception):
    """O classificador não está carregado: nenhuma foto é avaliada (fail-closed)."""


def pasta_padrao() -> Path:
    """Onde a imagem do serviço guarda os pesos (RENDER2GO_MODELOS_SERVICO + a pasta do models.lock.json)."""
    return pessoas.DIR / filtro_imagem.PASTA


def threads_do_ambiente() -> int:
    bruto = os.environ.get("RENDER2GO_FILTRO_THREADS", "").strip()
    try:
        n = int(bruto) if bruto else THREADS
    except ValueError:
        print(f"[filtro] AVISO: RENDER2GO_FILTRO_THREADS={bruto!r} não é número: usando {THREADS}", file=sys.stderr)
        n = THREADS
    return min(max(n, 1), os.cpu_count() or 1)


@dataclass(frozen=True)
class Avaliacao:
    filtro: str  # "ok", "recusada" ou "invalida"
    detalhe: dict  # versão, notas, formato e recorte (ou o erro): só o painel vê


def formato_do_video(pedido: dict, largura: int, altura: int) -> tuple[str, int, int]:
    """O formato e o tamanho do vídeo desta foto, como o pod calcula (render2go_worker.fila e lote)."""
    formatos = lote.load_receitas()["formatos"]
    formato = pedido.get("formato") or "auto"
    if formato not in formatos:  # "auto" (ou um pedido sem formato válido): pela proporção da foto
        formato = lote.formato_da_proporcao(largura / altura)
    dims = formatos[formato].get(pedido.get("qualidade") or "rapida") or formatos[formato]["rapida"]
    return formato, dims["width"], dims["height"]


def _reduzida(im: Image.Image) -> Image.Image:
    """Até LADO_MAX px, sempre com filtro de área. A paleta (e o preto e branco de 1 bit) vira RGBA (ou tons de
    cinza) antes: o Pillow reduziria esses modos por amostragem de pixels."""
    im.draft("RGB", (LADO_MAX, LADO_MAX))  # JPEG: decodifica já reduzida (memória e tempo)
    if im.mode in ("P", "PA"):
        im = im.convert("RGBA")
    elif im.mode == "1":
        im = im.convert("L")
    im.thumbnail((LADO_MAX, LADO_MAX), Image.Resampling.LANCZOS)
    return im


def imagens(dados: bytes, pedido: dict) -> tuple[list[bytes], dict]:
    """As imagens RGB 448 × 448 que o classificador avalia (a foto inteira e, se ele corta, o recorte do vídeo) e o
    que foi avaliado. FOTO_RUIM: o arquivo não abre como foto."""
    lado = filtro_imagem.LADO
    with pessoas.abrir_foto(dados) as im:
        original = im.size
        foto = preparo.rgb(_reduzida(im))
    w, h = foto.size
    formato, largura, altura = formato_do_video(pedido, w, h)
    r_foto, r_video = w / h, largura / altura
    corte = min(r_foto, r_video) / max(r_foto, r_video) < CORTE_MIN
    saida = [foto.resize((lado, lado), Image.Resampling.BICUBIC)]
    if corte:
        recorte = ImageOps.fit(foto, (largura, altura), Image.Resampling.LANCZOS)
        saida.append(recorte.resize((lado, lado), Image.Resampling.BICUBIC))
    return [i.tobytes() for i in saida], {"formato": formato, "corte": corte, "original": list(original)}


class FiltroFotos:
    """O classificador carregado uma vez por processo (o analisador), com as threads de CPU do .env."""

    def __init__(
        self,
        classificador: filtro_imagem.Classificador | None = None,
        pasta: Path | None = None,
        threads: int | None = None,
    ):
        self.classificador = classificador or filtro_imagem.Freepik(pasta or pasta_padrao(), "cpu")
        self.threads = threads if threads is not None else threads_do_ambiente()
        self.erro: str | None = None
        self._pronto = False
        self._tentado: float | None = None
        self._lock = threading.Lock()

    def disponivel(self) -> bool:
        """Carrega o classificador na 1ª vez (10–20 s) e, se não carregou, tenta de novo a cada RECARREGAR_S."""
        with self._lock:
            if self._pronto:
                return True
            agora = time.monotonic()
            if self._tentado is not None and agora - self._tentado < RECARREGAR_S:
                return False
            self._tentado = agora
            try:
                self._carregar()
            except Exception as e:  # noqa: BLE001 (pesos ausentes, PyTorch quebrado: nada é liberado)
                self.erro = (str(e) or e.__class__.__name__)[:300]
                print(f"[filtro] AVISO: classificador indisponível: {self.erro}", file=sys.stderr, flush=True)
                return False
            self._pronto, self.erro = True, None
            return True

    def _carregar(self) -> None:
        if isinstance(self.classificador, filtro_imagem.Freepik):
            import torch

            torch.set_num_threads(self.threads)
            with contextlib.suppress(RuntimeError):  # só vale antes do 1º trabalho em paralelo
                torch.set_num_interop_threads(1)
        carregar = getattr(self.classificador, "carregar", None)
        if carregar is not None:
            carregar()

    def estado(self) -> dict:
        """Para o painel (Segurança): ligado ou não, por quê, a versão e os limiares em uso."""
        limiar, limiar_alto = filtro_imagem.limiares()
        return {
            "ok": self._pronto,
            "erro": self.erro,
            "versao": VERSAO,
            "limiar": limiar,
            "limiar_alto": limiar_alto,
            "threads": self.threads,
        }

    def avaliar(self, dados: bytes, pedido: dict) -> Avaliacao:
        """A decisão sobre uma foto. FiltroIndisponivel ou filtro_imagem.FiltroError: o classificador não respondeu
        (a foto fica sem resultado e é tentada de novo)."""
        if not self._pronto:
            raise FiltroIndisponivel(self.erro or "classificador não carregado")
        try:
            quadros, info = imagens(dados, pedido)
        except FOTO_RUIM as e:
            return Avaliacao("invalida", {"versao": VERSAO, "erro": (str(e) or e.__class__.__name__)[:200]})
        try:
            probs = self.classificador.probabilidades(quadros)
        except filtro_imagem.FiltroError:
            raise
        except Exception as e:  # noqa: BLE001 (qualquer falha do classificador = não avaliada)
            raise filtro_imagem.FiltroError(f"classificador falhou: {e}") from e
        if len(probs) != len(quadros):
            raise filtro_imagem.FiltroError("classificador sem resposta para a foto")
        nota, nota_alta = filtro_imagem.notas_foto(probs)
        limiar, limiar_alto = filtro_imagem.limiares()
        detalhe = {
            "versao": VERSAO,
            "nota": round(nota, 4),
            "nota_alta": round(nota_alta, 4),
            "nota_baixa": round(max(p[1] + p[2] + p[3] for p in probs), 4),  # sugestivo: só informação
            "limiares": [limiar, limiar_alto],
            **info,
        }
        return Avaliacao("recusada" if filtro_imagem.barrado(nota, nota_alta) else "ok", detalhe)


def main(argv: list[str] | None = None) -> int:
    """`conferir [pasta]`: carrega o classificador e avalia uma imagem lisa, cinza (na build da imagem: falha se o
    PyTorch, o timm ou os pesos não funcionarem)."""
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] != "conferir":
        print("uso: python -m render2go_servico.filtro_fotos conferir [pasta]", file=sys.stderr)
        return 2
    filtro = FiltroFotos(pasta=Path(args[1]) if len(args) > 1 else None)
    if not filtro.disponivel():
        print(f"erro: {filtro.erro}", file=sys.stderr)
        return 1
    b = io.BytesIO()
    Image.new("RGB", (640, 480), (128, 128, 128)).save(b, "JPEG")
    r = filtro.avaliar(b.getvalue(), {"formato": "auto", "qualidade": "rapida"})
    print(f"filtro das fotos: {r.filtro} {json.dumps(r.detalhe, ensure_ascii=False)}")
    return 0 if r.filtro == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
