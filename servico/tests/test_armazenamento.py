import datetime as dt

import pytest

from render2go_servico.armazenamento import Armazenamento, ArmazenamentoError


def test_sigv4_presigned_get_matches_aws_documented_example():
    # Exemplo oficial do S3 ("Authenticating Requests: Using Query Parameters"):
    # GET examplebucket/test.txt, 24/05/2013 00:00:00 UTC, 86400 s.
    s3 = Armazenamento(
        "https://examplebucket.s3.amazonaws.com",
        "",
        "AKIAIOSFODNN7EXAMPLE",
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "us-east-1",
    )
    url = s3.url("GET", "test.txt", 86400, dt.datetime(2013, 5, 24, tzinfo=dt.UTC))
    assert url.startswith("https://examplebucket.s3.amazonaws.com/test.txt?")
    assert url.endswith("X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404")


def test_validation():
    r2 = Armazenamento("https://conta.r2.cloudflarestorage.com", "render2go", "k", "s")
    for chave in ("../x", "a b", ""):
        with pytest.raises(ArmazenamentoError):
            r2.url("GET", chave)
    with pytest.raises(ArmazenamentoError):
        r2.url("POST", "a")
    with pytest.raises(ArmazenamentoError):
        r2.url("GET", "a", expira_s=8 * 24 * 3600)
    u = r2.url("PUT", "entrada/c1/l1/f.jpg", content_type="image/jpeg")
    assert u.startswith("https://conta.r2.cloudflarestorage.com/render2go/entrada/c1/l1/f.jpg?")
    assert "X-Amz-SignedHeaders=content-type%3Bhost" in u


def _s3_listagem(paginas):
    """S3 local que responde ListObjectsV2 em páginas e guarda o que recebeu."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlsplit

    pedidos = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
            pedidos.append((u.path, q, self.headers.get("Authorization", "")))
            i = int(q.get("continuation-token", "0"))
            chaves, prox = paginas[i], (str(i + 1) if i + 1 < len(paginas) else None)
            corpo = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                f"<Name>render2go</Name><Prefix>{q['prefix']}</Prefix><KeyCount>{len(chaves)}</KeyCount>"
                f"<IsTruncated>{'true' if prox else 'false'}</IsTruncated>"
                + (f"<NextContinuationToken>{prox}</NextContinuationToken>" if prox else "")
                + "".join(f"<Contents><Key>{k}</Key><Size>10</Size></Contents>" for k in chaves)
                + "</ListBucketResult>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, pedidos


def test_listing_follows_pages_and_only_whole_folders():
    srv, pedidos = _s3_listagem([["saida/c1/l1/a.mp4", "saida/c1/l1/b.mp4"], ["saida/c2/l2/c.mp4"]])
    try:
        s3 = Armazenamento(f"http://127.0.0.1:{srv.server_address[1]}", "render2go", "k", "s")
        assert s3.listar("saida/") == ["saida/c1/l1/a.mp4", "saida/c1/l1/b.mp4", "saida/c2/l2/c.mp4"]
    finally:
        srv.shutdown()
    assert [p[0] for p in pedidos] == ["/render2go", "/render2go"]  # o bucket, não um objeto
    assert pedidos[0][1]["list-type"] == "2" and pedidos[0][1]["prefix"] == "saida/"
    assert pedidos[1][1]["continuation-token"] == "1"
    assert all(p[2].startswith("AWS4-HMAC-SHA256 Credential=k/") for p in pedidos)  # assinado
    for ruim in ("saida", "", "../x/", "a b/"):  # só "pastas" inteiras e chaves válidas
        with pytest.raises(ArmazenamentoError):
            s3.listar(ruim)


def test_copy_sends_a_signed_copy_source_and_detects_an_error_in_the_body():
    """A publicação do vídeo da tentativa (14/10/2026): CopyObject com x-amz-copy-source assinado."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    pedidos, respostas = [], [b"<CopyObjectResult><ETag>x</ETag></CopyObjectResult>", b"<Error><Code>X</Code></Error>"]

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_PUT(self):
            pedidos.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
            corpo = respostas.pop(0)
            self.send_response(200)
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        s3 = Armazenamento(f"http://127.0.0.1:{srv.server_address[1]}", "render2go", "k", "s")
        s3.copiar("saida/c/l/tentativas/j-0123456789abcdef.mp4", "saida/c/l/v.mp4")
        caminho, cab = pedidos[0]
        assert caminho == "/render2go/saida/c/l/v.mp4"
        assert cab["x-amz-copy-source"] == "/render2go/saida/c/l/tentativas/j-0123456789abcdef.mp4"
        assert "x-amz-copy-source" in cab["authorization"]  # assinado junto
        with pytest.raises(ArmazenamentoError):  # o S3 pode responder 200 com o erro no corpo
            s3.copiar("saida/c/l/a.mp4", "saida/c/l/b.mp4")
        with pytest.raises(ArmazenamentoError):
            s3.copiar("../fora", "saida/c/l/b.mp4")
    finally:
        srv.shutdown()
