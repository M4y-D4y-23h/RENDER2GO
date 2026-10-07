"""Banco e fila de jobs (ADR 0003): Postgres em produção, SQLite nos testes e no desenvolvimento.

Estados do lote:
    aguardando_envio → aguardando_aprovacao → aprovado → processando → concluido
                                ↘ recusado                         ↘ concluido_com_falhas

Nenhum job de GPU existe antes da aprovação (ADR 0004: revisão humana no lugar do C2
enquanto o classificador não existe). Aprovado o lote, nascem um job `compilar` (o pedido
vira prompt uma vez) e os jobs `render` (um por foto e variação), que só ficam disponíveis
depois que o prompt existe.

Fila: o worker reivindica um job com prazo (`lease`). Se o prazo vence sem conclusão, o job
volta para a fila, até MAX_TENTATIVAS. No Postgres a reivindicação usa
`FOR UPDATE SKIP LOCKED` (vários workers sem disputa); no SQLite, `BEGIN IMMEDIATE`.

Ordem da fila (docs/08): compilações primeiro; depois lotes pagos antes de testes gratuitos
(`lotes.prioridade`); dentro da mesma prioridade, **intercalado por lote**: vai primeiro o job do
lote com menos vídeos já iniciados, para o vídeo 1 de cada cliente sair antes do vídeo 2 de
qualquer um.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

MAX_TENTATIVAS = 3
FINAIS = ("ok", "erro", "cancelado")
PLANOS = {"pago": 0, "teste": 1}  # plano do cliente → prioridade dos lotes (menor sai antes)

ESQUEMA = [
    """CREATE TABLE IF NOT EXISTS clientes (
        id TEXT PRIMARY KEY, nome TEXT NOT NULL, token_sha TEXT NOT NULL UNIQUE,
        cota_fotos_dia INTEGER NOT NULL, plano TEXT NOT NULL DEFAULT 'pago',
        ativo INTEGER NOT NULL DEFAULT 1, criado DOUBLE PRECISION NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS lotes (
        id TEXT PRIMARY KEY, cliente_id TEXT NOT NULL REFERENCES clientes(id),
        status TEXT NOT NULL, pedido TEXT NOT NULL, prompt_en TEXT, compilador TEXT,
        n_fotos INTEGER NOT NULL, prioridade INTEGER NOT NULL DEFAULT 0, criado DOUBLE PRECISION NOT NULL,
        aprovado_em DOUBLE PRECISION, aprovado_por TEXT, concluido_em DOUBLE PRECISION, motivo TEXT)""",
    """CREATE TABLE IF NOT EXISTS fotos (
        id TEXT PRIMARY KEY, lote_id TEXT NOT NULL REFERENCES lotes(id), nome TEXT NOT NULL,
        tipo TEXT NOT NULL, bytes INTEGER NOT NULL, chave TEXT NOT NULL, enviada INTEGER NOT NULL DEFAULT 0,
        UNIQUE (lote_id, nome))""",
    """CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, lote_id TEXT NOT NULL REFERENCES lotes(id), tipo TEXT NOT NULL,
        foto_id TEXT REFERENCES fotos(id), variacao INTEGER, seed BIGINT, arquivo TEXT,
        status TEXT NOT NULL, tentativas INTEGER NOT NULL DEFAULT 0, lease_ate DOUBLE PRECISION,
        worker TEXT, segundos DOUBLE PRECISION, aviso TEXT, erro TEXT,
        similaridade DOUBLE PRECISION, bytes_video BIGINT,
        criado DOUBLE PRECISION NOT NULL, atualizado DOUBLE PRECISION NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS jobs_fila ON jobs (status, criado)",
    """CREATE TABLE IF NOT EXISTS eventos (
        lote_id TEXT, quando DOUBLE PRECISION NOT NULL, tipo TEXT NOT NULL, detalhe TEXT)""",
]


class BancoError(Exception):
    pass


def sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def novo_id(prefixo: str) -> str:
    return f"{prefixo}_{secrets.token_hex(8)}"


class Banco:
    """`url`: postgresql://... ou sqlite:///caminho (ou sqlite:///:memory:)."""

    def __init__(self, url: str):
        self.url = url
        self.pg = url.startswith(("postgresql://", "postgres://"))
        self._lock = threading.RLock()
        if self.pg:
            import psycopg
            from psycopg.rows import dict_row

            self._pg = lambda: psycopg.connect(url, row_factory=dict_row, autocommit=False)
        else:
            caminho = url.removeprefix("sqlite:///")
            self._sq = sqlite3.connect(caminho, check_same_thread=False, isolation_level=None)
            self._sq.row_factory = sqlite3.Row
            self._sq.execute("PRAGMA foreign_keys=ON")
            self._sq.execute("PRAGMA busy_timeout=5000")

    # -- conexão e transação --------------------------------------------------------------

    @contextmanager
    def tx(self) -> Iterator[Any]:
        """Transação; no SQLite, BEGIN IMMEDIATE (um escritor por vez)."""
        if self.pg:
            with self._pg() as con, con.cursor() as cur:
                yield _Cur(cur, pg=True)
            return
        with self._lock:
            self._sq.execute("BEGIN IMMEDIATE")
            try:
                yield _Cur(self._sq.cursor(), pg=False)
                self._sq.execute("COMMIT")
            except BaseException:
                self._sq.execute("ROLLBACK")
                raise

    def criar_esquema(self) -> None:
        with self.tx() as c:
            for sql in ESQUEMA:
                c.run(sql if self.pg else sql.replace("DOUBLE PRECISION", "REAL").replace("BIGINT", "INTEGER"))

    def evento(self, c, lote_id: str | None, tipo: str, detalhe: Any = None) -> None:
        c.run(
            "INSERT INTO eventos (lote_id, quando, tipo, detalhe) VALUES (?, ?, ?, ?)",
            (lote_id, time.time(), tipo, json.dumps(detalhe, ensure_ascii=False)),
        )

    # -- clientes -------------------------------------------------------------------------

    def criar_cliente(self, nome: str, cota_fotos_dia: int = 500, plano: str = "pago") -> tuple[str, str]:
        """Devolve (id, token). O token só existe aqui; o banco guarda o SHA-256."""
        if plano not in PLANOS:
            raise BancoError(f"plano desconhecido: {plano}")
        cid, token = novo_id("cli"), secrets.token_urlsafe(32)
        with self.tx() as c:
            c.run(
                "INSERT INTO clientes (id, nome, token_sha, cota_fotos_dia, plano, criado) VALUES (?, ?, ?, ?, ?, ?)",
                (cid, nome, sha(token), cota_fotos_dia, plano, time.time()),
            )
        return cid, token

    def fotos_total(self, cliente_id: str) -> int:
        """Fotos de todos os lotes não recusados do cliente (limite do teste gratuito)."""
        with self.tx() as c:
            r = c.one(
                "SELECT COALESCE(SUM(n_fotos), 0) AS n FROM lotes WHERE cliente_id = ? AND status <> 'recusado'",
                (cliente_id,),
            )
        return int(r["n"])

    def cliente_por_token(self, token: str) -> dict | None:
        with self.tx() as c:
            return c.one("SELECT * FROM clientes WHERE token_sha = ? AND ativo = 1", (sha(token),))

    def fotos_hoje(self, cliente_id: str) -> int:
        desde = time.time() - 86400
        with self.tx() as c:
            r = c.one(
                "SELECT COALESCE(SUM(n_fotos), 0) AS n FROM lotes WHERE cliente_id = ? AND criado > ?",
                (cliente_id, desde),
            )
        return int(r["n"])

    # -- lotes ----------------------------------------------------------------------------

    def criar_lote(self, cliente_id: str, pedido: dict, fotos: list[dict]) -> str:
        """fotos: [{nome, tipo, bytes, chave}] (já validadas pela API)."""
        lid = novo_id("lote")
        agora = time.time()
        with self.tx() as c:
            cli = c.one("SELECT plano FROM clientes WHERE id = ?", (cliente_id,))
            if cli is None:
                raise BancoError("cliente não encontrado")
            c.run(
                "INSERT INTO lotes (id, cliente_id, status, pedido, n_fotos, prioridade, criado)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    lid,
                    cliente_id,
                    "aguardando_envio",
                    json.dumps(pedido, ensure_ascii=False),
                    len(fotos),
                    PLANOS[cli["plano"]],
                    agora,
                ),
            )
            for f in fotos:
                c.run(
                    "INSERT INTO fotos (id, lote_id, nome, tipo, bytes, chave) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        novo_id("foto"),
                        lid,
                        f["nome"],
                        f["tipo"],
                        f["bytes"],
                        f["chave"].replace("{lote}", lid),
                    ),
                )
            self.evento(c, lid, "criado", {"fotos": len(fotos)})
        return lid

    def lote(self, lote_id: str, cliente_id: str | None = None) -> dict | None:
        with self.tx() as c:
            if cliente_id:
                return c.one("SELECT * FROM lotes WHERE id = ? AND cliente_id = ?", (lote_id, cliente_id))
            return c.one("SELECT * FROM lotes WHERE id = ?", (lote_id,))

    def lotes(self, status: str | None = None, cliente_id: str | None = None, limite: int = 50) -> list[dict]:
        sql, args = "SELECT * FROM lotes WHERE 1 = 1", []
        if status:
            sql, args = sql + " AND status = ?", [*args, status]
        if cliente_id:
            sql, args = sql + " AND cliente_id = ?", [*args, cliente_id]
        with self.tx() as c:
            return c.all(sql + " ORDER BY criado DESC LIMIT ?", (*args, limite))

    def fotos(self, lote_id: str) -> list[dict]:
        with self.tx() as c:
            return c.all("SELECT * FROM fotos WHERE lote_id = ? ORDER BY nome", (lote_id,))

    def confirmar_envio(self, lote_id: str, tamanhos: dict[str, int | None]) -> list[str]:
        """tamanhos: chave → bytes no armazenamento (None = ausente). Devolve os problemas."""
        problemas = []
        with self.tx() as c:
            lote = c.one("SELECT * FROM lotes WHERE id = ?", (lote_id,))
            if lote is None or lote["status"] != "aguardando_envio":
                raise BancoError("lote não está aguardando envio")
            for f in c.all("SELECT * FROM fotos WHERE lote_id = ?", (lote_id,)):
                t = tamanhos.get(f["chave"])
                if t is None:
                    problemas.append(f"{f['nome']}: não enviada")
                elif t != f["bytes"]:
                    problemas.append(f"{f['nome']}: {t} bytes, esperados {f['bytes']}")
            if not problemas:
                c.run("UPDATE fotos SET enviada = 1 WHERE lote_id = ?", (lote_id,))
                c.run("UPDATE lotes SET status = 'aguardando_aprovacao' WHERE id = ?", (lote_id,))
                self.evento(c, lote_id, "enviado")
        return problemas

    def aprovar(self, lote_id: str, quem: str, tarefas: list[dict]) -> None:
        """tarefas: [{foto_id, variacao, seed, arquivo}] — cria os jobs (fail-closed: só aqui)."""
        agora = time.time()
        with self.tx() as c:
            lote = c.one("SELECT * FROM lotes WHERE id = ?", (lote_id,))
            if lote is None or lote["status"] != "aguardando_aprovacao":
                raise BancoError("lote não está aguardando aprovação")
            c.run(
                "UPDATE lotes SET status = 'aprovado', aprovado_em = ?, aprovado_por = ? WHERE id = ?",
                (agora, quem, lote_id),
            )
            c.run(
                "INSERT INTO jobs (id, lote_id, tipo, status, criado, atualizado)"
                " VALUES (?, ?, 'compilar', 'pendente', ?, ?)",
                (novo_id("job"), lote_id, agora, agora),
            )
            for t in tarefas:
                c.run(
                    "INSERT INTO jobs (id, lote_id, tipo, foto_id, variacao, seed, arquivo, status, criado, atualizado)"
                    " VALUES (?, ?, 'render', ?, ?, ?, ?, 'pendente', ?, ?)",
                    (
                        novo_id("job"),
                        lote_id,
                        t["foto_id"],
                        t["variacao"],
                        t["seed"],
                        t["arquivo"],
                        agora,
                        agora,
                    ),
                )
            self.evento(c, lote_id, "aprovado", {"por": quem, "jobs": len(tarefas)})

    def recusar(self, lote_id: str, quem: str, motivo: str) -> None:
        with self.tx() as c:
            n = c.run(
                "UPDATE lotes SET status = 'recusado', motivo = ? WHERE id = ?"
                " AND status IN ('aguardando_envio', 'aguardando_aprovacao')",
                (motivo, lote_id),
            )
            if not n:
                raise BancoError("lote não pode mais ser recusado")
            self.evento(c, lote_id, "recusado", {"por": quem, "motivo": motivo})

    def definir_prompt(self, lote_id: str, prompt_en: str, compilador: str) -> None:
        with self.tx() as c:
            c.run(
                "UPDATE lotes SET prompt_en = ?, compilador = ?, status = 'processando'"
                " WHERE id = ? AND prompt_en IS NULL",
                (prompt_en, compilador, lote_id),
            )
            self.evento(c, lote_id, "prompt", {"compilador": compilador})

    # -- fila -----------------------------------------------------------------------------

    def reivindicar(self, worker: str, lease_s: float = 900) -> dict | None:
        """Próximo job (compilar antes de render; render só com o prompt pronto)."""
        agora = time.time()
        trava = " FOR UPDATE OF j SKIP LOCKED" if self.pg else ""
        with self.tx() as c:
            # prazos vencidos: volta para a fila ou falha de vez
            esgotados = c.all(
                "SELECT DISTINCT lote_id FROM jobs WHERE status = 'em_andamento' AND lease_ate < ? AND tentativas >= ?",
                (agora, MAX_TENTATIVAS),
            )
            c.run(
                "UPDATE jobs SET status = 'erro', erro = 'prazo vencido nas tentativas', atualizado = ?"
                " WHERE status = 'em_andamento' AND lease_ate < ? AND tentativas >= ?",
                (agora, agora, MAX_TENTATIVAS),
            )
            for r in esgotados:
                self._fechar_lote(c, r["lote_id"])
            c.run(
                "UPDATE jobs SET status = 'pendente', worker = NULL, atualizado = ?"
                " WHERE status = 'em_andamento' AND lease_ate < ?",
                (agora, agora),
            )
            job = c.one(
                "SELECT j.* FROM jobs j JOIN lotes l ON l.id = j.lote_id"
                " WHERE j.status = 'pendente' AND l.status IN ('aprovado', 'processando')"
                " AND (j.tipo = 'compilar' OR l.prompt_en IS NOT NULL)"
                " ORDER BY CASE j.tipo WHEN 'compilar' THEN 0 ELSE 1 END, l.prioridade,"
                " (SELECT COUNT(*) FROM jobs k WHERE k.lote_id = j.lote_id AND k.status <> 'pendente'),"
                " j.criado, j.id LIMIT 1" + trava,
            )
            if job is None:
                return None
            c.run(
                "UPDATE jobs SET status = 'em_andamento', worker = ?, lease_ate = ?, tentativas = tentativas + 1,"
                " atualizado = ? WHERE id = ?",
                (worker, agora + lease_s, agora, job["id"]),
            )
            job.update(status="em_andamento", worker=worker, tentativas=job["tentativas"] + 1)
            return job

    def concluir(self, job_id: str, worker: str, status: str, **campos: Any) -> dict:
        if status not in FINAIS:
            raise BancoError(f"status inválido: {status}")
        agora = time.time()
        with self.tx() as c:
            job = c.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
            if job is None or job["worker"] != worker or job["status"] != "em_andamento":
                raise BancoError("job não está com este worker")
            repetir = status == "erro" and campos.pop("repetir", False) and job["tentativas"] < MAX_TENTATIVAS
            novo = "pendente" if repetir else status
            c.run(
                "UPDATE jobs SET status = ?, segundos = ?, aviso = ?, erro = ?, similaridade = ?, bytes_video = ?,"
                " atualizado = ?, lease_ate = NULL WHERE id = ?",
                (
                    novo,
                    campos.get("segundos"),
                    campos.get("aviso"),
                    campos.get("erro"),
                    campos.get("similaridade"),
                    campos.get("bytes_video"),
                    agora,
                    job_id,
                ),
            )
            self._fechar_lote(c, job["lote_id"])
            return {**job, "status": novo}

    def _fechar_lote(self, c, lote_id: str) -> None:
        agora = time.time()
        # compilação que falhou de vez: os renders do lote não têm como rodar
        c.run(
            "UPDATE jobs SET status = 'cancelado', erro = 'compilação falhou', atualizado = ?"
            " WHERE lote_id = ? AND tipo = 'render' AND status = 'pendente' AND EXISTS"
            " (SELECT 1 FROM jobs k WHERE k.lote_id = ? AND k.tipo = 'compilar' AND k.status = 'erro')",
            (agora, lote_id, lote_id),
        )
        r = c.one(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN status IN ('ok', 'erro', 'cancelado') THEN 1 ELSE 0 END) AS fim,"
            " SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok FROM jobs WHERE lote_id = ?",
            (lote_id,),
        )
        if r["total"] and r["fim"] == r["total"]:
            final = "concluido" if r["ok"] == r["total"] else "concluido_com_falhas"
            c.run(
                "UPDATE lotes SET status = ?, concluido_em = ? WHERE id = ?",
                (final, agora, lote_id),
            )
            self.evento(c, lote_id, final, {"ok": int(r["ok"]), "total": int(r["total"])})

    def jobs(self, lote_id: str) -> list[dict]:
        with self.tx() as c:
            return c.all("SELECT * FROM jobs WHERE lote_id = ? ORDER BY tipo, arquivo", (lote_id,))

    def trabalho(self) -> list[dict]:
        """Para o orquestrador: renders pendentes e em andamento por lote, com o pedido
        (qualidade e duração decidem o tempo por vídeo) e a prioridade."""
        with self.tx() as c:
            return c.all(
                "SELECT l.id AS lote_id, l.pedido, l.prioridade,"
                " SUM(CASE WHEN j.status = 'pendente' THEN 1 ELSE 0 END) AS pendentes,"
                " SUM(CASE WHEN j.status = 'em_andamento' THEN 1 ELSE 0 END) AS em_andamento"
                " FROM jobs j JOIN lotes l ON l.id = j.lote_id"
                " WHERE l.status IN ('aprovado', 'processando') AND j.status IN ('pendente', 'em_andamento')"
                " GROUP BY l.id, l.pedido, l.prioridade ORDER BY l.prioridade, MIN(j.criado)"
            )

    def fila(self) -> dict[str, int]:
        """Para o orquestrador: jobs prontos para rodar e em andamento."""
        with self.tx() as c:
            r = c.one(
                "SELECT SUM(CASE WHEN j.status = 'pendente' THEN 1 ELSE 0 END) AS pendentes,"
                " SUM(CASE WHEN j.status = 'em_andamento' THEN 1 ELSE 0 END) AS andamento"
                " FROM jobs j JOIN lotes l ON l.id = j.lote_id WHERE l.status IN ('aprovado', 'processando')"
            )
        return {"pendentes": int(r["pendentes"] or 0), "em_andamento": int(r["andamento"] or 0)}


class _Cur:
    """Cursor com o mesmo SQL (placeholders `?`) para SQLite e Postgres."""

    def __init__(self, cur, pg: bool):
        self.cur, self.pg = cur, pg

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.pg else sql

    def run(self, sql: str, args: tuple = ()) -> int:
        self.cur.execute(self._sql(sql), args)
        return self.cur.rowcount

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        self.cur.execute(self._sql(sql), args)
        r = self.cur.fetchone()
        return dict(r) if r is not None else None

    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        self.cur.execute(self._sql(sql), args)
        return [dict(r) for r in self.cur.fetchall()]
