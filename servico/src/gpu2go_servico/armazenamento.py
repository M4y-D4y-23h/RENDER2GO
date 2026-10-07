"""Armazenamento de objetos compatível com S3 (Cloudflare R2), só com URLs pré-assinadas.

Os bytes nunca passam pela API (ADR 0003): o navegador do cliente e os workers enviam e
baixam direto do armazenamento, com URLs assinadas por SigV4 (query string), curtas e
válidas para um único objeto e um único método. Só biblioteca padrão.

Chaves dos objetos:
    clientes/<cliente>/<lote>/entrada/<foto>.<ext>    (apagadas em 7 dias, regra no bucket)
    clientes/<cliente>/<lote>/saida/<video>.mp4       (apagadas em 30 dias)
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import re
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import urllib.request

CHAVE_VALIDA = re.compile(r"^[A-Za-z0-9/_.=-]{1,512}$")
SEM_CORPO = "UNSIGNED-PAYLOAD"


class ArmazenamentoError(Exception):
    pass


def _hmac(chave: bytes, msg: str) -> bytes:
    return hmac.new(chave, msg.encode(), hashlib.sha256).digest()


@dataclass(frozen=True)
class Armazenamento:
    """endpoint: https://<conta>.r2.cloudflarestorage.com (R2) ou de outro S3."""

    endpoint: str
    bucket: str
    access_key: str
    secret_key: str
    regiao: str = "auto"  # R2 usa "auto"

    def url(
        self,
        metodo: str,
        chave: str,
        expira_s: int = 900,
        agora: dt.datetime | None = None,
        content_type: str | None = None,
    ) -> str:
        """URL pré-assinada (SigV4 por query string) para um objeto e um método."""
        if metodo not in ("GET", "PUT", "HEAD", "DELETE"):
            raise ArmazenamentoError(f"método não permitido: {metodo}")
        if not CHAVE_VALIDA.match(chave) or ".." in chave:
            raise ArmazenamentoError(f"chave inválida: {chave!r}")
        if not 1 <= expira_s <= 7 * 24 * 3600:
            raise ArmazenamentoError("expiração de 1 s a 7 dias")
        agora = agora or dt.datetime.now(dt.UTC)
        data, dia = agora.strftime("%Y%m%dT%H%M%SZ"), agora.strftime("%Y%m%d")
        base = urlsplit(self.endpoint)
        host = base.netloc
        objeto = quote(chave, safe="/~")
        # R2: bucket no caminho; bucket vazio = estilo "virtual host" (bucket já no endpoint)
        caminho = f"/{self.bucket}/{objeto}" if self.bucket else f"/{objeto}"
        escopo = f"{dia}/{self.regiao}/s3/aws4_request"
        cabecalhos = {"host": host}
        if content_type:  # o PUT só aceita o tipo assinado
            cabecalhos["content-type"] = content_type
        assinados = ";".join(sorted(cabecalhos))
        params = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.access_key}/{escopo}",
            "X-Amz-Date": data,
            "X-Amz-Expires": str(expira_s),
            "X-Amz-SignedHeaders": assinados,
        }
        query = "&".join(
            f"{quote(k, safe='~')}={quote(v, safe='~')}" for k, v in sorted(params.items())
        )
        canonico = "\n".join(
            [
                metodo,
                caminho,
                query,
                "".join(f"{k}:{cabecalhos[k]}\n" for k in sorted(cabecalhos)),
                assinados,
                SEM_CORPO,
            ]
        )
        texto = "\n".join(
            ["AWS4-HMAC-SHA256", data, escopo, hashlib.sha256(canonico.encode()).hexdigest()]
        )
        k = _hmac(("AWS4" + self.secret_key).encode(), dia)
        for parte in (self.regiao, "s3", "aws4_request"):
            k = _hmac(k, parte)
        assinatura = hmac.new(k, texto.encode(), hashlib.sha256).hexdigest()
        return f"{base.scheme}://{host}{caminho}?{query}&X-Amz-Signature={assinatura}"

    def tamanho(self, chave: str) -> int | None:
        """Bytes do objeto (HEAD assinado), ou None se não existe."""
        req = urllib.request.Request(self.url("HEAD", chave, 60), method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return int(r.headers.get("Content-Length", "0"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise ArmazenamentoError(f"HEAD {chave}: HTTP {e.code}") from e
        except OSError as e:
            raise ArmazenamentoError(f"HEAD {chave}: {e}") from e

    def apagar(self, chave: str) -> None:
        req = urllib.request.Request(self.url("DELETE", chave, 60), method="DELETE")
        try:
            urllib.request.urlopen(req, timeout=30).close()
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise ArmazenamentoError(f"DELETE {chave}: HTTP {e.code}") from e


def chave_entrada(cliente: str, lote: str, nome: str) -> str:
    return f"clientes/{cliente}/{lote}/entrada/{nome}"


def chave_saida(cliente: str, lote: str, nome: str) -> str:
    return f"clientes/{cliente}/{lote}/saida/{nome}"
