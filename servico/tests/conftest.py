import os

import pytest

from gpu2go_servico.db import Banco

PG_URL = os.environ.get("GPU2GO_TESTE_PG", "postgresql://postgres@/gpu2go_teste?host=/var/tmp&port=55432")


def _pg_disponivel() -> bool:
    try:
        import psycopg

        psycopg.connect(PG_URL, connect_timeout=2).close()
        return True
    except Exception:
        return False


PG = _pg_disponivel()


@pytest.fixture(params=["sqlite", "postgres"])
def banco(request, tmp_path):
    if request.param == "sqlite":
        b = Banco(f"sqlite:///{tmp_path / 'b.db'}")
    else:
        if not PG:
            pytest.skip("Postgres de teste indisponível")
        import psycopg

        with psycopg.connect(PG_URL, autocommit=True) as con:
            for t in ("eventos", "jobs", "fotos", "lotes", "clientes"):
                con.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        b = Banco(PG_URL)
    b.criar_esquema()
    return b
