"""Entrada e saída de mídia do job por URLs assinadas, e quadros de amostra.

- Só https e só hosts da lista RENDER2GO_ALLOWED_URL_HOSTS (evita que um job faça o worker
  acessar endereços internos). Sem a lista configurada, nenhuma URL é aceita (fail-closed).
- Downloads com limite de tamanho e conferência do conteúdo (a imagem é aberta pelo
  Pillow; o vídeo, pelo ffprobe).
- Uploads por PUT com novas tentativas em falhas de rede ou 5xx.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from render2go_worker import video

HOSTS_ENV = "RENDER2GO_ALLOWED_URL_HOSTS"
SCHEMES = ("https",)  # os testes acrescentam http para o storage falso local
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_VIDEO_BYTES = 300 * 1024 * 1024
MAX_FRAMES = 12


class MediaError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class UnsafeUrl(MediaError):
    pass


def allowed_hosts() -> list[str]:
    return [h.strip().lower() for h in os.environ.get(HOSTS_ENV, "").split(",") if h.strip()]


def check_url(url: str) -> None:
    """https + host na lista (entrada `.exemplo.com` aceita subdomínios)."""
    hosts = allowed_hosts()
    if not hosts:
        raise UnsafeUrl(f"{HOSTS_ENV} não configurado: nenhuma URL é aceita")
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if u.scheme not in SCHEMES or not host:
        raise UnsafeUrl("só URLs https são aceitas")
    if u.username or u.password:
        raise UnsafeUrl("URL com credenciais embutidas")
    netloc = u.netloc.lower()
    ok = any(host == h or netloc == h or (h.startswith(".") and host.endswith(h)) for h in hosts)
    if not ok:
        raise UnsafeUrl(f"host {host} fora de {HOSTS_ENV}")


def download(url: str, dest: Path, max_bytes: int, timeout: float = 60) -> int:
    check_url(url)
    try:
        with requests.get(url, stream=True, timeout=timeout, allow_redirects=False) as r:
            if r.status_code in (301, 302, 303, 307, 308):
                raise UnsafeUrl("redirecionamento recusado (a URL assinada deve ser direta)")
            if r.status_code >= 500:
                raise MediaError(f"download falhou ({r.status_code})", retryable=True)
            if r.status_code != 200:
                raise MediaError(f"download recusado ({r.status_code})")
            size = 0
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise MediaError(f"arquivo acima de {max_bytes} bytes")
                    f.write(chunk)
    except requests.RequestException as e:
        raise MediaError(f"download falhou: {e}", retryable=True) from e
    return size


FORMATOS_IMAGEM = ("JPEG", "PNG", "WEBP")


def check_image(path: Path, max_pixels: int = 4096 * 4096) -> tuple[int, int]:
    """Só JPEG, PNG e WebP: o Pillow nem tenta os outros leitores (TIFF, PSD, EPS...), que são a
    maior superfície de ataque de um arquivo malicioso. O tamanho em pixels vem do cabeçalho, antes
    de decodificar."""
    from PIL import Image  # vem com o ComfyUI

    try:
        with Image.open(path, formats=FORMATOS_IMAGEM) as im:
            im.verify()
        with Image.open(path, formats=FORMATOS_IMAGEM) as im:
            w, h = im.size
            fmt = im.format
    except Exception as e:  # noqa: BLE001
        raise MediaError(f"imagem inválida (aceitas: JPEG, PNG e WebP): {e}") from e
    if fmt not in FORMATOS_IMAGEM:
        raise MediaError(f"formato de imagem não aceito: {fmt}")
    if w * h > max_pixels:
        raise MediaError(f"imagem grande demais: {w}x{h}")
    return w, h


def upload(
    url: str, path: Path, content_type: str, attempts: int = 3, timeout: float = 120
) -> None:
    check_url(url)
    last = ""
    for i in range(attempts):
        try:
            with open(path, "rb") as f:
                r = requests.put(
                    url, data=f, headers={"Content-Type": content_type}, timeout=timeout
                )
            if r.status_code < 300:
                return
            last = f"{r.status_code} {r.text[:200]}"
            if r.status_code < 500:
                raise MediaError(f"upload recusado: {last}")
        except requests.RequestException as e:
            last = str(e)
        time.sleep(2**i)
    raise MediaError(f"upload falhou depois de {attempts} tentativas: {last}", retryable=True)


def sample_frames(mp4: Path, n: int, out_dir: Path) -> list[Path]:
    """n quadros JPEG espalhados pelo vídeo (primeiro e último incluídos)."""
    if n <= 0:
        return []
    n = min(n, MAX_FRAMES)
    info = video.probe(mp4)
    total = max(info.frames, 1)
    idx = sorted({round(i * (total - 1) / max(n - 1, 1)) for i in range(n)})
    out_dir.mkdir(parents=True, exist_ok=True)
    expr = "+".join(f"eq(n\\,{i})" for i in idx)
    pattern = out_dir / "q%02d.jpg"
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(mp4),
            "-vf",
            f"select={expr}",
            "-vsync",
            "0",
            "-q:v",
            "3",
            str(pattern),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise MediaError(f"falha ao extrair quadros: {proc.stderr[-300:]}")
    frames = sorted(out_dir.glob("q*.jpg"))
    if len(frames) != len(idx):
        raise MediaError(f"esperados {len(idx)} quadros, extraídos {len(frames)}")
    return frames
