import datetime as dt

import pytest

from gpu2go_servico.armazenamento import Armazenamento, ArmazenamentoError


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
    r2 = Armazenamento("https://conta.r2.cloudflarestorage.com", "gpu2go", "k", "s")
    for chave in ("../x", "a b", ""):
        with pytest.raises(ArmazenamentoError):
            r2.url("GET", chave)
    with pytest.raises(ArmazenamentoError):
        r2.url("POST", "a")
    with pytest.raises(ArmazenamentoError):
        r2.url("GET", "a", expira_s=8 * 24 * 3600)
    u = r2.url("PUT", "clientes/c1/l1/entrada/f.jpg", content_type="image/jpeg")
    assert u.startswith("https://conta.r2.cloudflarestorage.com/gpu2go/clientes/c1/l1/entrada/f.jpg?")
    assert "X-Amz-SignedHeaders=content-type%3Bhost" in u
