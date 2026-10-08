"""Armazenamento de objetos compatível com S3 (Cloudflare R2): URLs pré-assinadas e envio em partes.

Os bytes nunca passam por quem tem as chaves (ADR 0003): navegadores e pods enviam e baixam
direto do armazenamento, com URLs assinadas por SigV4, curtas e válidas para um único objeto e
um único método. Só quem orquestra (a API ou o `lote_nuvem`) tem as chaves e faz as chamadas
assinadas no cabeçalho (início e fim do envio em partes). Só biblioteca padrão.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import hmac
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree

CHAVE_VALIDA = re.compile(r"^[A-Za-z0-9/_.=-]{1,512}$")
SEM_CORPO = "UNSIGNED-PAYLOAD"
METODOS_URL = ("GET", "PUT", "HEAD", "DELETE")


class ArmazenamentoError(Exception):
    pass


def _hmac(chave: bytes, msg: str) -> bytes:
    return hmac.new(chave, msg.encode(), hashlib.sha256).digest()


def _q(texto: str) -> str:
    return quote(texto, safe="~")


@dataclass(frozen=True)
class Armazenamento:
    """endpoint: https://<conta>.r2.cloudflarestorage.com (R2) ou de outro S3."""

    endpoint: str
    bucket: str
    access_key: str
    secret_key: str
    regiao: str = "auto"  # R2 usa "auto"

    @classmethod
    def do_ambiente(cls, prefixo: str = "RENDER2GO_R2_") -> Armazenamento | None:
        """RENDER2GO_R2_ENDPOINT, _BUCKET, _ACCESS_KEY_ID e _SECRET_ACCESS_KEY; None se faltar."""
        v = {
            k: os.environ.get(prefixo + k, "").strip()
            for k in ("ENDPOINT", "BUCKET", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY")
        }
        if not all(v.values()):
            return None
        return cls(
            v["ENDPOINT"].rstrip("/"),
            v["BUCKET"],
            v["ACCESS_KEY_ID"],
            v["SECRET_ACCESS_KEY"],
            os.environ.get(prefixo + "REGION", "auto") or "auto",
        )

    # -- assinatura ------------------------------------------------------------------------

    def _caminho(self, chave: str) -> tuple[str, str, str]:
        if not CHAVE_VALIDA.match(chave) or ".." in chave:
            raise ArmazenamentoError(f"chave inválida: {chave!r}")
        base = urlsplit(self.endpoint)
        objeto = quote(chave, safe="/~")
        # R2: bucket no caminho; bucket vazio = estilo "virtual host" (bucket já no endpoint)
        caminho = f"/{self.bucket}/{objeto}" if self.bucket else f"/{objeto}"
        return base.scheme, base.netloc, caminho

    def _assinatura(self, data: str, canonico: str) -> tuple[str, str]:
        dia = data[:8]
        escopo = f"{dia}/{self.regiao}/s3/aws4_request"
        texto = "\n".join(
            ["AWS4-HMAC-SHA256", data, escopo, hashlib.sha256(canonico.encode()).hexdigest()]
        )
        k = _hmac(("AWS4" + self.secret_key).encode(), dia)
        for parte in (self.regiao, "s3", "aws4_request"):
            k = _hmac(k, parte)
        return escopo, hmac.new(k, texto.encode(), hashlib.sha256).hexdigest()

    def url(
        self,
        metodo: str,
        chave: str,
        expira_s: int = 900,
        agora: dt.datetime | None = None,
        content_type: str | None = None,
        extra: dict[str, str] | None = None,
    ) -> str:
        """URL pré-assinada (SigV4 por query string) para um objeto e um método.

        `extra`: parâmetros assinados junto (ex.: partNumber e uploadId de uma parte)."""
        if metodo not in METODOS_URL:
            raise ArmazenamentoError(f"método não permitido: {metodo}")
        if not 1 <= expira_s <= 7 * 24 * 3600:
            raise ArmazenamentoError("expiração de 1 s a 7 dias")
        esquema, host, caminho = self._caminho(chave)
        agora = agora or dt.datetime.now(dt.UTC)
        data = agora.strftime("%Y%m%dT%H%M%SZ")
        cabecalhos = {"host": host}
        if content_type:  # o PUT só aceita o tipo assinado
            cabecalhos["content-type"] = content_type
        assinados = ";".join(sorted(cabecalhos))
        escopo = f"{data[:8]}/{self.regiao}/s3/aws4_request"
        params = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.access_key}/{escopo}",
            "X-Amz-Date": data,
            "X-Amz-Expires": str(expira_s),
            "X-Amz-SignedHeaders": assinados,
            **(extra or {}),
        }
        query = "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(params.items()))
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
        _, assinatura = self._assinatura(data, canonico)
        return f"{esquema}://{host}{caminho}?{query}&X-Amz-Signature={assinatura}"

    def _chamar(
        self,
        metodo: str,
        chave: str,
        query: dict[str, str],
        corpo: bytes = b"",
        timeout: float = 60,
    ) -> bytes:
        """Chamada assinada no cabeçalho (só quem tem as chaves)."""
        esquema, host, caminho = self._caminho(chave)
        data = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        sha = hashlib.sha256(corpo).hexdigest()
        cabecalhos = {"host": host, "x-amz-content-sha256": sha, "x-amz-date": data}
        assinados = ";".join(sorted(cabecalhos))
        qs = "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(query.items()))
        canonico = "\n".join(
            [
                metodo,
                caminho,
                qs,
                "".join(f"{k}:{cabecalhos[k]}\n" for k in sorted(cabecalhos)),
                assinados,
                sha,
            ]
        )
        escopo, assinatura = self._assinatura(data, canonico)
        req = urllib.request.Request(
            f"{esquema}://{host}{caminho}" + (f"?{qs}" if qs else ""),
            data=corpo or None,
            method=metodo,
        )
        for k, v in cabecalhos.items():
            if k != "host":
                req.add_header(k, v)
        req.add_header(
            "Authorization",
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{escopo}, "
            f"SignedHeaders={assinados}, Signature={assinatura}",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise ArmazenamentoError(f"{metodo} {chave}: HTTP {e.code} {e.read()[:200]!r}") from e
        except OSError as e:
            raise ArmazenamentoError(f"{metodo} {chave}: {e}") from e

    # -- objetos ---------------------------------------------------------------------------

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

    def ler(self, chave: str, limite: int) -> bytes:
        """Conteúdo do objeto (GET assinado), recusando o que passar de `limite` bytes."""
        try:
            with urllib.request.urlopen(self.url("GET", chave, 300), timeout=60) as r:
                dados = r.read(limite + 1)
        except (urllib.error.HTTPError, OSError) as e:
            raise ArmazenamentoError(f"GET {chave}: {e}") from e
        if len(dados) > limite:
            raise ArmazenamentoError(f"GET {chave}: acima de {limite} bytes")
        return dados

    def apagar(self, chave: str) -> None:
        req = urllib.request.Request(self.url("DELETE", chave, 60), method="DELETE")
        try:
            urllib.request.urlopen(req, timeout=30).close()
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise ArmazenamentoError(f"DELETE {chave}: HTTP {e.code}") from e

    # -- envio em partes (arquivos acima de 5 GB) ------------------------------------------

    def iniciar_partes(self, chave: str) -> str:
        xml = self._chamar("POST", chave, {"uploads": ""})
        upload_id = _texto_xml(xml, "UploadId")
        if not upload_id:
            raise ArmazenamentoError(f"sem UploadId para {chave}")
        return upload_id

    def url_parte(self, chave: str, upload_id: str, n: int, expira_s: int = 6 * 3600) -> str:
        return self.url("PUT", chave, expira_s, extra={"partNumber": str(n), "uploadId": upload_id})

    def concluir_partes(self, chave: str, upload_id: str, etags: dict[int, str]) -> None:
        partes = "".join(
            f"<Part><PartNumber>{n}</PartNumber><ETag>{etags[n]}</ETag></Part>"
            for n in sorted(etags)
        )
        corpo = f"<CompleteMultipartUpload>{partes}</CompleteMultipartUpload>".encode()
        xml = self._chamar("POST", chave, {"uploadId": upload_id}, corpo, timeout=300)
        if b"<Error>" in xml:  # o S3 pode responder 200 com erro no corpo
            raise ArmazenamentoError(f"concluir {chave}: {xml[:200]!r}")

    def abortar_partes(self, chave: str, upload_id: str) -> None:
        # se falhar, a regra de ciclo de vida do bucket limpa envios abandonados
        with contextlib.suppress(ArmazenamentoError):
            self._chamar("DELETE", chave, {"uploadId": upload_id})


def _texto_xml(xml: bytes, tag: str) -> str | None:
    for el in ElementTree.fromstring(xml).iter():
        if el.tag.rsplit("}", 1)[-1] == tag:
            return el.text
    return None
