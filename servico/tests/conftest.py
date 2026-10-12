import os

import pytest

from render2go_servico.db import Banco

PG_URL = os.environ.get("RENDER2GO_TESTE_PG", "postgresql://postgres@/render2go_teste?host=/var/tmp&port=55432")


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
    """O banco dos testes, nos dois motores. O filtro das fotos (render2go_servico.filtro_fotos) fica fora da fila
    aqui: os testes dele (test_filtro_fotos.py) e o da fila de ponta a ponta (test_fila.py) ligam o portão."""
    if request.param == "sqlite":
        b = Banco(f"sqlite:///{tmp_path / 'b.db'}", exigir_filtro=False)
    else:
        if not PG:
            pytest.skip("Postgres de teste indisponível")
        import psycopg

        with psycopg.connect(PG_URL, autocommit=True) as con:
            for t in (
                "codigos_recuperacao",
                "admin_convites",
                "admins",
                "emails_antigos",
                "auditoria_admin",
                "pagamentos",
                "tokens_worker",
                "estado",
                "comandos",
                "acessos",
                "aceites",
                "limites",
                "codigos",
                "sessoes",
                "avaliacoes",
                "interesses",
                "movimentos",
                "geradores",
                "cookies",
                "suporte",
                "consentimentos",
                "eventos",
                "jobs",
                "fotos",
                "lotes",
                "clientes",
            ):
                con.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        b = Banco(PG_URL, pool=4, exigir_filtro=False)
    b.criar_esquema()
    yield b
    b.fechar()


@pytest.fixture(autouse=True)
def _sem_r2_de_verdade(monkeypatch):
    """Nenhum teste fala com o R2 de verdade (credenciais do ambiente, inclusive GPU2GO_R2_*)."""
    for nome in list(os.environ):
        if nome.startswith(("RENDER2GO_R2_", "GPU2GO_R2_")):
            monkeypatch.delenv(nome, raising=False)
