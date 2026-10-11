"""Aviso "contém pessoas" nas fotos dos lotes (ADR 0004, item 5), para o revisor, no painel do dono.

Roda no servidor, em CPU, em segundo plano depois do envio:
    - YOLOX-tiny (COCO, classe pessoa; Apache-2.0): acha pessoas inteiras, inclusive de costas;
    - YuNet (OpenCV Zoo; MIT): conta rostos visíveis.

É um aviso, não uma barreira: quem decide continua sendo a revisão humana. A falha da análise
aparece como "análise indisponível", nunca como "sem pessoas".

Os modelos são baixados no build da imagem e conferidos por bytes e SHA-256 (`MODELOS`):
    python -m render2go_servico.pessoas baixar /opt/render2go/modelos-servico
"""

from __future__ import annotations

import hashlib
import os
import sys
import threading
import urllib.request
from io import BytesIO
from pathlib import Path

VERSAO = "pessoas-v1"
DIR = Path(os.environ.get("RENDER2GO_MODELOS_SERVICO", Path.home() / ".cache" / "render2go" / "modelos-servico"))
# arquivo → (URL fixada, bytes, SHA-256)
MODELOS = {
    "yolox_tiny.onnx": (
        "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx",
        20219662,
        "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7",
    ),
    "face_detection_yunet_2023mar.onnx": (
        "https://huggingface.co/opencv/face_detection_yunet/resolve/"
        "3cc26e7f1014a5ee5d74a42acee58bafc9d0a310/face_detection_yunet_2023mar.onnx",
        232589,
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
}
TAM_YOLOX = 416  # entrada do yolox_tiny
LIMIAR_PESSOA = 0.5  # objeto × classe "pessoa"
AREA_MIN = 0.005  # caixa com menos de 0,5% da foto não conta (gente minúscula ao fundo)
LIMIAR_ROSTO = 0.8
LADO_MAX = 1280  # a foto é reduzida antes da análise
# a foto do cliente só é aberta depois de conferida (11/10/2026): o tipo pelos primeiros bytes, só os
# leitores de JPEG, PNG e WebP do Pillow (os outros, como TIFF, PSD e EPS, nem são tentados) e o tamanho em
# pixels lido do cabeçalho antes de decodificar (bomba de descompressão). O mesmo teto do pod (preparo.py)
MAX_PIXELS_FOTO = 50_000_000
FORMATOS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


class FotoInvalida(ValueError):
    """O arquivo não é uma foto que o serviço aceita: não é decodificado."""


def tipo_pelos_bytes(inicio: bytes) -> str | None:
    """image/jpeg, image/png ou image/webp pela assinatura do arquivo; None para qualquer outra coisa."""
    if inicio[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if inicio[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if inicio[:4] == b"RIFF" and inicio[8:12] == b"WEBP":
        return "image/webp"
    return None


def abrir_foto(dados: bytes):
    """Abre a foto (sem decodificar ainda) só se ela for JPEG, PNG ou WebP de verdade e couber no teto."""
    from PIL import Image

    tipo = tipo_pelos_bytes(dados[:16])
    if tipo is None:
        raise FotoInvalida("o arquivo não é uma foto JPEG, PNG ou WebP")
    try:
        im = Image.open(BytesIO(dados), formats=[FORMATOS[tipo]])
    except Exception as e:  # noqa: BLE001 (inclui a bomba que o próprio Pillow recusa)
        raise FotoInvalida(f"foto ilegível: {str(e)[:120]}") from e
    w, h = im.size
    if w * h > MAX_PIXELS_FOTO:
        im.close()
        raise FotoInvalida(f"foto de {w}×{h} pixels, acima de {MAX_PIXELS_FOTO // 1_000_000} megapixels")
    return im


class DetectorIndisponivel(Exception):
    pass


def _sha(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def baixar(destino: Path = DIR) -> None:
    """Baixa os modelos que faltam e confere bytes e SHA-256 (falha se não bater)."""
    destino.mkdir(parents=True, exist_ok=True)
    for nome, (url, tamanho, sha) in MODELOS.items():
        alvo = destino / nome
        if alvo.exists() and alvo.stat().st_size == tamanho and _sha(alvo) == sha:
            continue
        parcial = destino / f"{nome}.parcial"
        req = urllib.request.Request(url, headers={"User-Agent": "render2go-servico"})
        with urllib.request.urlopen(req, timeout=120) as r, parcial.open("wb") as f:
            while bloco := r.read(1 << 20):
                f.write(bloco)
        if parcial.stat().st_size != tamanho or _sha(parcial) != sha:
            parcial.unlink()
            raise DetectorIndisponivel(f"{nome}: bytes ou SHA-256 não conferem")
        parcial.replace(alvo)
        alvo.chmod(0o644)


class Detector:
    """Pessoas (YOLOX-tiny) e rostos (YuNet). Uma instância por processo; usa um lock."""

    def __init__(self, pasta: Path = DIR):
        try:
            import cv2
            import numpy as np
        except ImportError as e:  # pragma: no cover (dependência do serviço)
            raise DetectorIndisponivel(f"OpenCV ausente: {e}") from e
        for nome, (_, tamanho, sha) in MODELOS.items():
            p = pasta / nome
            if not p.exists() or p.stat().st_size != tamanho or _sha(p) != sha:
                raise DetectorIndisponivel(f"modelo ausente ou diferente: {p}")
        self.cv2, self.np = cv2, np
        self.yolox = cv2.dnn.readNetFromONNX(str(pasta / "yolox_tiny.onnx"))
        self.yunet = cv2.FaceDetectorYN.create(
            str(pasta / "face_detection_yunet_2023mar.onnx"), "", (320, 320), LIMIAR_ROSTO, 0.3, 100
        )
        grades, passos = [], []
        for s in (8, 16, 32):
            n = TAM_YOLOX // s
            xv, yv = np.meshgrid(np.arange(n), np.arange(n))
            g = np.stack((xv, yv), 2).reshape(-1, 2)
            grades.append(g)
            passos.append(np.full((g.shape[0], 1), s))
        self._grade, self._passo = np.concatenate(grades), np.concatenate(passos)
        self._lock = threading.Lock()

    def _pessoas(self, bgr) -> list[float]:
        """Áreas relativas das pessoas encontradas (caixas depois do NMS)."""
        cv2, np = self.cv2, self.np
        h, w = bgr.shape[:2]
        r = min(TAM_YOLOX / h, TAM_YOLOX / w)
        nh, nw = max(1, int(h * r)), max(1, int(w * r))
        tela = np.full((TAM_YOLOX, TAM_YOLOX, 3), 114, np.uint8)
        tela[:nh, :nw] = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
        self.yolox.setInput(tela.transpose(2, 0, 1)[None].astype(np.float32))
        out = self.yolox.forward()[0]
        nota = out[:, 4] * out[:, 5]  # objeto × classe 0 (pessoa)
        k = nota > LIMIAR_PESSOA
        if not k.any():
            return []
        centro = (out[k, :2] + self._grade[k]) * self._passo[k]
        lados = np.exp(out[k, 2:4]) * self._passo[k]
        caixas = np.concatenate([centro - lados / 2, lados], 1) / r
        idx = np.array(cv2.dnn.NMSBoxes(caixas.tolist(), nota[k].tolist(), LIMIAR_PESSOA, 0.45)).flatten()
        areas = [float(caixas[i, 2] * caixas[i, 3] / (h * w)) for i in idx]
        return sorted((a for a in areas if a >= AREA_MIN), reverse=True)

    def _rostos(self, bgr) -> int:
        self.yunet.setInputSize((bgr.shape[1], bgr.shape[0]))
        _, faces = self.yunet.detect(bgr)
        return 0 if faces is None else len(faces)

    def analisar(self, dados: bytes) -> dict:
        from PIL import ImageOps

        with abrir_foto(dados) as im:
            im.draft("RGB", (LADO_MAX, LADO_MAX))  # JPEG: decodifica já reduzida (memória e tempo)
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((LADO_MAX, LADO_MAX))
            bgr = self.np.ascontiguousarray(self.np.asarray(im)[:, :, ::-1])
        with self._lock:
            areas = self._pessoas(bgr)
            rostos = self._rostos(bgr)
        return {
            "versao": VERSAO,
            "pessoas": len(areas),
            "rostos": rostos,
            "maior": round(areas[0], 3) if areas else 0.0,
        }


def analisador(pasta: Path = DIR):
    """Função bytes → resultado. Sem modelos, devolve o erro (o revisor vê "indisponível")."""
    try:
        det = Detector(pasta)
    except DetectorIndisponivel as e:
        motivo = f"detector indisponível: {e}"
        print(f"[pessoas] AVISO: {motivo}", file=sys.stderr, flush=True)
        return lambda dados: {"versao": VERSAO, "erro": motivo}
    return det.analisar


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] != "baixar":
        print("uso: python -m render2go_servico.pessoas baixar [pasta]", file=sys.stderr)
        return 2
    baixar(Path(args[1]) if len(args) > 1 else DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
