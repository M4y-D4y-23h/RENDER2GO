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
(`lotes.prioridade`); dentro da mesma prioridade, **intercalado por cliente**: vai primeiro o
cliente com menos vídeos já iniciados nos pedidos em andamento, para o vídeo 1 de cada cliente
sair antes do vídeo 2 de qualquer um. Os pedidos de um mesmo cliente seguem a **ordem de
chegada**: o pedido novo espera os anteriores (até 08/10/2026 a vez era por pedido, e o pedido
novo de uma conta passava na frente do antigo da mesma conta).

Geradores (`geradores`): o orquestrador registra cada pod (subindo, pronto, encerrado) e a API usa
esse estado e as subidas medidas na previsão mostrada ao cliente.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import statistics
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

MAX_TENTATIVAS = 3
FINAIS = ("ok", "erro", "cancelado")
# plano do cliente → prioridade dos lotes (menor sai antes). "interno": acesso do dono do serviço,
# sem limites de teste e com aprovação automática (ADR 0004); o C1 continua valendo.
PLANOS = {"interno": 0, "pago": 0, "teste": 1}

ESQUEMA = [
    """CREATE TABLE IF NOT EXISTS clientes (
        id TEXT PRIMARY KEY, nome TEXT NOT NULL, token_sha TEXT NOT NULL UNIQUE,
        cota_fotos_dia INTEGER NOT NULL, plano TEXT NOT NULL DEFAULT 'pago',
        ativo INTEGER NOT NULL DEFAULT 1, criado DOUBLE PRECISION NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS lotes (
        id TEXT PRIMARY KEY, cliente_id TEXT NOT NULL REFERENCES clientes(id),
        status TEXT NOT NULL, pedido TEXT NOT NULL, prompt_en TEXT, compilador TEXT,
        n_fotos INTEGER NOT NULL, prioridade INTEGER NOT NULL DEFAULT 0, criado DOUBLE PRECISION NOT NULL,
        aprovado_em DOUBLE PRECISION, aprovado_por TEXT, concluido_em DOUBLE PRECISION, motivo TEXT,
        ip TEXT, fotos_apagadas DOUBLE PRECISION, dispositivo TEXT, agente TEXT, fuso TEXT)""",
    """CREATE TABLE IF NOT EXISTS fotos (
        id TEXT PRIMARY KEY, lote_id TEXT NOT NULL REFERENCES lotes(id), nome TEXT NOT NULL,
        tipo TEXT NOT NULL, bytes INTEGER NOT NULL, chave TEXT NOT NULL, enviada INTEGER NOT NULL DEFAULT 0,
        analise TEXT, sha256 TEXT, UNIQUE (lote_id, nome))""",
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
    # declaração de autorização das pessoas das fotos (auditoria): só inserção, nunca alteração
    """CREATE TABLE IF NOT EXISTS consentimentos (
        id TEXT PRIMARY KEY, lote_id TEXT NOT NULL REFERENCES lotes(id), cliente_id TEXT NOT NULL,
        cliente_nome TEXT NOT NULL, declarante TEXT NOT NULL, texto_versao TEXT NOT NULL,
        texto_sha256 TEXT NOT NULL, texto TEXT NOT NULL, ip TEXT, dispositivo TEXT, agente TEXT, fuso TEXT,
        fotos TEXT NOT NULL, quando DOUBLE PRECISION NOT NULL)""",
    # mensagens do formulário de suporte (guardadas mesmo sem e-mail configurado)
    """CREATE TABLE IF NOT EXISTS suporte (
        id TEXT PRIMARY KEY, cliente_id TEXT NOT NULL, ip TEXT, dispositivo TEXT, email TEXT,
        assunto TEXT, mensagem TEXT NOT NULL, quando DOUBLE PRECISION NOT NULL,
        enviado INTEGER NOT NULL DEFAULT 0, erro TEXT)""",
    # escolhas do aviso de cookies (prova do consentimento, LGPD): só inserção
    """CREATE TABLE IF NOT EXISTS cookies (
        id TEXT PRIMARY KEY, dispositivo TEXT, ip TEXT, agente TEXT, versao TEXT NOT NULL,
        escolha TEXT NOT NULL, quando DOUBLE PRECISION NOT NULL)""",
    # pods do orquestrador: estado atual (previsão para o cliente) e tempos de subida medidos
    """CREATE TABLE IF NOT EXISTS geradores (
        id TEXT PRIMARY KEY, worker TEXT, gpu TEXT, usd_h DOUBLE PRECISION, estado TEXT NOT NULL,
        criado DOUBLE PRECISION NOT NULL, pronto_em DOUBLE PRECISION, primeiro_video_em DOUBLE PRECISION,
        encerrado_em DOUBLE PRECISION, motivo TEXT)""",
]
# vídeos já iniciados (ou prontos) do cliente nos pedidos em andamento: a vez na fila é por cliente
INICIADOS_DO_CLIENTE = (
    "SELECT COUNT(*) FROM jobs k JOIN lotes m ON m.id = k.lote_id WHERE m.cliente_id = l.cliente_id"
    " AND m.status IN ('aprovado', 'processando') AND k.tipo = 'render' AND k.status <> 'pendente'"
)
TIPOS_JOB = ("compilar", "render")
# tabelas do histórico (pedidos e tudo o que vem deles), na ordem de apagar (chaves estrangeiras)
HISTORICO = ("jobs", "consentimentos", "fotos", "eventos", "lotes", "suporte", "geradores")
# colunas que entraram depois da primeira implantação: (tabela, coluna, tipo)
COLUNAS_NOVAS = [
    ("fotos", "analise", "TEXT"),  # aviso de pessoas no /aprovar (render2go_servico.pessoas)
    ("lotes", "ip", "TEXT"),  # IP de quem criou o pedido (limite do teste gratuito por IP)
    ("lotes", "fotos_apagadas", "DOUBLE PRECISION"),  # fotos de entrada apagadas ao fechar o lote
    ("lotes", "dispositivo", "TEXT"),  # identificador do navegador (limite do teste por dispositivo)
    ("lotes", "agente", "TEXT"),  # User-Agent do navegador
    ("lotes", "fuso", "TEXT"),  # fuso horário informado pelo navegador (sinal de VPN)
    ("fotos", "sha256", "TEXT"),  # impressão da foto, que fica depois que a foto é apagada
    ("lotes", "impressao", "TEXT"),  # resumo dos sinais do navegador (acha o mesmo navegador sem o identificador)
    ("consentimentos", "sinais", "TEXT"),  # sinais do navegador no momento da declaração (JSON)
]
# lote que já passou do envio e não foi recusado: conta nos limites do teste gratuito
ATIVO = "status NOT IN ('aguardando_envio', 'recusado')"
ATIVO_L = "l." + ATIVO  # o mesmo, com a tabela lotes como "l"
APROVADOS = ("aprovado", "processando", "concluido", "concluido_com_falhas")


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
            for tabela, coluna, tipo in COLUNAS_NOVAS:
                tipo = tipo if self.pg else tipo.replace("DOUBLE PRECISION", "REAL")
                if self.pg:  # IF NOT EXISTS: a API e o orquestrador sobem juntos
                    c.run(f"ALTER TABLE {tabela} ADD COLUMN IF NOT EXISTS {coluna} {tipo}")
                elif not any(r["name"] == coluna for r in c.all(f"PRAGMA table_info({tabela})")):
                    c.run(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")

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

    def pedidos_ativos(self, cliente_id: str, exceto: str | None = None) -> int:
        """Lotes do cliente que já passaram do envio e não foram recusados (teste: um pedido só)."""
        with self.tx() as c:
            r = c.one(
                f"SELECT COUNT(*) AS n FROM lotes WHERE cliente_id = ? AND {ATIVO} AND id <> ?",
                (cliente_id, exceto or ""),
            )
        return int(r["n"])

    def fotos_teste_ip(self, ip: str, exceto: str | None = None) -> int:
        """Fotos de teste gratuito pedidas a partir deste IP, somando todos os clientes de teste."""
        with self.tx() as c:
            r = c.one(
                "SELECT COALESCE(SUM(l.n_fotos), 0) AS n FROM lotes l JOIN clientes k ON k.id = l.cliente_id"
                f" WHERE l.ip = ? AND k.plano = 'teste' AND {ATIVO_L}"
                " AND l.id <> ?",
                (ip, exceto or ""),
            )
        return int(r["n"])

    def fotos_teste_dispositivo(self, dispositivo: str, exceto: str | None = None) -> int:
        """Fotos de teste gratuito pedidas a partir deste dispositivo (navegador)."""
        with self.tx() as c:
            r = c.one(
                "SELECT COALESCE(SUM(l.n_fotos), 0) AS n FROM lotes l JOIN clientes k ON k.id = l.cliente_id"
                f" WHERE l.dispositivo = ? AND k.plano = 'teste' AND {ATIVO_L} AND l.id <> ?",
                (dispositivo, exceto or ""),
            )
        return int(r["n"])

    def substituir_envios(self, cliente_id: str) -> int:
        """Pedidos do cliente cujo envio não terminou viram "recusado" (o teste vale um pedido só)."""
        with self.tx() as c:
            velhos = c.all("SELECT id FROM lotes WHERE cliente_id = ? AND status = 'aguardando_envio'", (cliente_id,))
            for v in velhos:
                c.run(
                    "UPDATE lotes SET status = 'recusado', motivo = ? WHERE id = ?",
                    ("envio não concluído; substituído por um pedido novo", v["id"]),
                )
                self.evento(c, v["id"], "substituido", None)
        return len(velhos)

    def desativar_cliente(self, cliente_id: str, quem: str) -> bool:
        with self.tx() as c:
            n = c.run("UPDATE clientes SET ativo = 0 WHERE id = ? AND ativo = 1", (cliente_id,))
            if n:
                self.evento(c, None, "cliente_desativado", {"cliente": cliente_id, "por": quem})
        return bool(n)

    def historico(self, grupo: str = "todos", limite: int = 100) -> list[dict]:
        """Lotes mais recentes com o cliente e o andamento, para o /aprovar."""
        filtros = {
            "todos": ("1 = 1", ()),
            "revisao": ("l.status = 'aguardando_aprovacao'", ()),
            "aprovados": (f"l.status IN ({', '.join('?' * len(APROVADOS))})", APROVADOS),
            "recusados": ("l.status = 'recusado'", ()),
            "envio": ("l.status = 'aguardando_envio'", ()),
        }
        if grupo not in filtros:
            raise BancoError(f"grupo desconhecido: {grupo}")
        onde, args = filtros[grupo]
        with self.tx() as c:
            return c.all(
                "SELECT l.id, l.cliente_id, k.nome AS cliente_nome, k.plano, k.ativo AS cliente_ativo, l.status,"
                " l.pedido, l.n_fotos, l.ip, l.criado, l.aprovado_em, l.aprovado_por, l.concluido_em, l.motivo,"
                " l.fotos_apagadas, l.dispositivo, l.fuso, a.declarante, a.quando AS autorizado_em,"
                " (SELECT COUNT(*) FROM jobs j WHERE j.lote_id = l.id AND j.tipo = 'render' AND j.status = 'ok')"
                " AS prontos,"
                " (SELECT COUNT(*) FROM jobs j WHERE j.lote_id = l.id AND j.tipo = 'render') AS total"
                " FROM lotes l JOIN clientes k ON k.id = l.cliente_id"
                f" LEFT JOIN consentimentos a ON a.lote_id = l.id WHERE {onde}"
                " ORDER BY l.criado DESC LIMIT ?",
                (*args, limite),
            )

    def uso_por_ip(self, limite: int = 50) -> list[dict]:
        """Fotos e pedidos por IP (só lotes que passaram do envio e não foram recusados)."""
        with self.tx() as c:
            return c.all(
                "SELECT l.ip, COUNT(*) AS pedidos, COALESCE(SUM(l.n_fotos), 0) AS fotos,"
                " COALESCE(SUM(CASE WHEN k.plano = 'teste' THEN l.n_fotos ELSE 0 END), 0) AS fotos_teste,"
                " COUNT(DISTINCT l.cliente_id) AS clientes, COUNT(DISTINCT l.dispositivo) AS dispositivos,"
                " MAX(l.criado) AS ultimo FROM lotes l JOIN clientes k ON k.id = l.cliente_id"
                f" WHERE l.ip IS NOT NULL AND {ATIVO_L}"
                " GROUP BY l.ip ORDER BY ultimo DESC LIMIT ?",
                (limite,),
            )

    def uso_por_dispositivo(self, dias: int = 90, limite: int = 50) -> list[dict]:
        """Por dispositivo: IPs, códigos de cliente, fusos e impressões do navegador (todos os pedidos
        dos últimos `dias`), e fotos de teste ativas. Sinais: mais de um IP (troca de rede, VPN), mais de
        um código (alguém juntando testes) e a mesma impressão em outro identificador (janela anônima
        ou dados do navegador apagados)."""
        with self.tx() as c:
            linhas = c.all(
                "SELECT l.dispositivo, l.ip, l.cliente_id, l.n_fotos, l.status, l.criado, l.fuso, l.impressao,"
                " k.plano FROM lotes l JOIN clientes k ON k.id = l.cliente_id"
                " WHERE l.dispositivo IS NOT NULL AND l.criado > ? ORDER BY l.criado",
                (time.time() - dias * 86400,),
            )
        por: dict[str, dict] = {}
        for r in linhas:
            d = por.setdefault(
                r["dispositivo"],
                {
                    "dispositivo": r["dispositivo"],
                    "ips": [],
                    "clientes": [],
                    "fusos": [],
                    "impressoes": [],
                    "pedidos": 0,
                    "fotos_teste": 0,
                },
            )
            campos = (
                ("ips", r["ip"]),
                ("clientes", r["cliente_id"]),
                ("fusos", r["fuso"]),
                ("impressoes", r["impressao"]),
            )
            for campo, valor in campos:
                if valor and valor not in d[campo]:
                    d[campo].append(valor)
            d["pedidos"] += 1
            if r["plano"] == "teste" and r["status"] not in ("aguardando_envio", "recusado"):
                d["fotos_teste"] += int(r["n_fotos"])
            d["ultimo"] = r["criado"]
        donos: dict[str, set] = {}
        for d in por.values():
            for imp in d["impressoes"]:
                donos.setdefault(imp, set()).add(d["dispositivo"])
        for d in por.values():
            outros = set().union(*(donos[i] for i in d["impressoes"])) if d["impressoes"] else set()
            d["mesmo_navegador"] = sorted(outros - {d["dispositivo"]})
        return sorted(por.values(), key=lambda d: d["ultimo"], reverse=True)[:limite]

    def auditoria(self, lote_id: str) -> dict | None:
        """Tudo o que existe sobre um lote: pedido, origem, autorização, fotos (impressões) e eventos."""
        with self.tx() as c:
            lt = c.one("SELECT * FROM lotes WHERE id = ?", (lote_id,))
            if lt is None:
                return None
            return {
                "lote": lt,
                "cliente": c.one(
                    "SELECT id, nome, plano, ativo, criado FROM clientes WHERE id = ?", (lt["cliente_id"],)
                ),
                "autorizacao": c.one("SELECT * FROM consentimentos WHERE lote_id = ?", (lote_id,)),
                "fotos": c.all(
                    "SELECT nome, tipo, bytes, sha256, analise FROM fotos WHERE lote_id = ? ORDER BY nome", (lote_id,)
                ),
                "eventos": c.all(
                    "SELECT quando, tipo, detalhe FROM eventos WHERE lote_id = ? ORDER BY quando", (lote_id,)
                ),
            }

    def autorizacao(self, lote_id: str) -> dict | None:
        with self.tx() as c:
            return c.one(
                "SELECT declarante, quando, ip, dispositivo, texto_versao FROM consentimentos WHERE lote_id = ?",
                (lote_id,),
            )

    def marcar_fotos_apagadas(self, lote_id: str) -> None:
        with self.tx() as c:
            c.run("UPDATE lotes SET fotos_apagadas = ? WHERE id = ?", (time.time(), lote_id))
            self.evento(c, lote_id, "fotos_apagadas", None)

    def cliente(self, cliente_id: str) -> dict | None:
        with self.tx() as c:
            return c.one("SELECT id, nome, plano, ativo FROM clientes WHERE id = ?", (cliente_id,))

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

    def criar_lote(
        self,
        cliente_id: str,
        pedido: dict,
        fotos: list[dict],
        origem: dict | None = None,
        autorizacao: dict | None = None,
    ) -> str:
        """fotos: [{nome, tipo, bytes, chave}] (já validadas pela API).
        origem: {ip, dispositivo, agente, fuso} de quem pediu.
        autorizacao: {declarante, texto_versao, texto} quando a caixa de autorização foi marcada;
        gravada na mesma transação, com a origem e a lista de fotos (auditoria)."""
        lid = novo_id("lote")
        agora = time.time()
        o = origem or {}
        with self.tx() as c:
            cli = c.one("SELECT nome, plano FROM clientes WHERE id = ?", (cliente_id,))
            if cli is None:
                raise BancoError("cliente não encontrado")
            c.run(
                "INSERT INTO lotes (id, cliente_id, status, pedido, n_fotos, prioridade, criado, ip, dispositivo,"
                " agente, fuso, impressao) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    lid,
                    cliente_id,
                    "aguardando_envio",
                    json.dumps(pedido, ensure_ascii=False),
                    len(fotos),
                    PLANOS[cli["plano"]],
                    agora,
                    o.get("ip"),
                    o.get("dispositivo"),
                    o.get("agente"),
                    o.get("fuso"),
                    o.get("impressao"),
                ),
            )
            if autorizacao:
                c.run(
                    "INSERT INTO consentimentos (id, lote_id, cliente_id, cliente_nome, declarante, texto_versao,"
                    " texto_sha256, texto, ip, dispositivo, agente, fuso, fotos, quando, sinais)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        novo_id("aut"),
                        lid,
                        cliente_id,
                        cli["nome"],
                        autorizacao["declarante"],
                        autorizacao["texto_versao"],
                        hashlib.sha256(autorizacao["texto"].encode()).hexdigest(),
                        autorizacao["texto"],
                        o.get("ip"),
                        o.get("dispositivo"),
                        o.get("agente"),
                        o.get("fuso"),
                        json.dumps([{"nome": f["nome"], "bytes": f["bytes"]} for f in fotos], ensure_ascii=False),
                        agora,
                        json.dumps(autorizacao.get("sinais") or {}, ensure_ascii=False),
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
            self.evento(c, lid, "criado", {"fotos": len(fotos), "autorizacao": bool(autorizacao), **o})
        return lid

    # -- suporte --------------------------------------------------------------------------

    def registrar_suporte(self, cliente_id: str, origem: dict, email: str | None, assunto: str, mensagem: str) -> str:
        sid = novo_id("sup")
        with self.tx() as c:
            c.run(
                "INSERT INTO suporte (id, cliente_id, ip, dispositivo, email, assunto, mensagem, quando)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sid, cliente_id, origem.get("ip"), origem.get("dispositivo"), email, assunto, mensagem, time.time()),
            )
        return sid

    def contar_suporte(self, campo: str, valor: str | None, desde_s: float) -> int:
        """Mensagens do mesmo IP, dispositivo ou cliente na janela (limite contra spam)."""
        if campo not in ("ip", "dispositivo", "cliente_id") or not valor:
            return 0
        with self.tx() as c:
            r = c.one(
                f"SELECT COUNT(*) AS n FROM suporte WHERE {campo} = ? AND quando > ?", (valor, time.time() - desde_s)
            )
        return int(r["n"])

    def registrar_cookies(self, origem: dict, versao: str, escolha: dict) -> str:
        cid = novo_id("ck")
        with self.tx() as c:
            c.run(
                "INSERT INTO cookies (id, dispositivo, ip, agente, versao, escolha, quando)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    cid,
                    origem.get("dispositivo"),
                    origem.get("ip"),
                    origem.get("agente"),
                    versao,
                    json.dumps(escolha),
                    time.time(),
                ),
            )
        return cid

    def contar_cookies(self, ip: str, desde_s: float) -> int:
        with self.tx() as c:
            r = c.one("SELECT COUNT(*) AS n FROM cookies WHERE ip = ? AND quando > ?", (ip, time.time() - desde_s))
        return int(r["n"])

    def marcar_suporte(self, sid: str, enviado: bool, erro: str | None = None) -> None:
        with self.tx() as c:
            c.run("UPDATE suporte SET enviado = ?, erro = ? WHERE id = ?", (int(enviado), erro, sid))

    def suporte_recente(self, limite: int = 50) -> list[dict]:
        with self.tx() as c:
            return c.all(
                "SELECT s.*, k.nome AS cliente_nome, k.plano FROM suporte s LEFT JOIN clientes k ON k.id = s.cliente_id"
                " ORDER BY s.quando DESC LIMIT ?",
                (limite,),
            )

    # -- previsão para o cliente ----------------------------------------------------------

    def segundos_por_video(self, n: int = 30) -> float | None:
        """Média dos últimos vídeos prontos (download, geração e envio)."""
        with self.tx() as c:
            linhas = c.all(
                "SELECT segundos FROM jobs WHERE tipo = 'render' AND status = 'ok' AND segundos IS NOT NULL"
                " ORDER BY atualizado DESC LIMIT ?",
                (n,),
            )
        return sum(float(r["segundos"]) for r in linhas) / len(linhas) if linhas else None

    def videos_a_frente(self, lote: dict) -> dict[str, int]:
        """Na ordem da fila (ver `reivindicar`), quantos vídeos saem antes do 1º vídeo pendente deste
        lote ("primeiro") e quantos, contando os do próprio lote, até o último dele ("ultimo").

        Entram os vídeos em andamento (ocupam as GPUs agora), os de prioridade melhor, os pedidos
        anteriores do mesmo cliente e, dos outros clientes da mesma prioridade, a vez intercalada:
        cada um anda até alcançar os vídeos iniciados deste cliente (no empate, quem pediu antes)."""
        with self.tx() as c:
            linhas = c.all(
                "SELECT l.id, l.cliente_id, l.prioridade, l.criado,"
                " SUM(CASE WHEN j.status = 'pendente' THEN 1 ELSE 0 END) AS pendentes,"
                " SUM(CASE WHEN j.status = 'em_andamento' THEN 1 ELSE 0 END) AS andamento,"
                " SUM(CASE WHEN j.status <> 'pendente' THEN 1 ELSE 0 END) AS iniciados"
                " FROM jobs j JOIN lotes l ON l.id = j.lote_id"
                " WHERE j.tipo = 'render' AND l.status IN ('aprovado', 'processando')"
                " GROUP BY l.id, l.cliente_id, l.prioridade, l.criado"
            )
        p, cli, este = lote["prioridade"], lote["cliente_id"], (lote["criado"], lote["id"])
        fixos = meus_antes = meus_pendentes = iniciados_meus = pend_lote = anda_lote = 0
        outros: dict[str, list[float]] = {}  # cliente → [pendentes, iniciados, pedido pendente mais antigo]
        for r in linhas:
            pend, anda, inic = (int(r[k] or 0) for k in ("pendentes", "andamento", "iniciados"))
            if r["id"] == lote["id"]:
                pend_lote, anda_lote, iniciados_meus = pend, anda, iniciados_meus + inic
            elif r["cliente_id"] == cli and r["prioridade"] == p:
                iniciados_meus += inic
                if (r["criado"], r["id"]) < este:
                    meus_antes += pend + anda
                    meus_pendentes += pend
                else:
                    fixos += anda
            elif r["prioridade"] < p:
                fixos += pend + anda
            else:
                fixos += anda
                if r["prioridade"] == p and pend:
                    o = outros.setdefault(r["cliente_id"], [0, 0, float("inf")])
                    o[0] += pend
                    o[1] += inic
                    o[2] = min(o[2], float(r["criado"]))
                elif r["prioridade"] == p:
                    outros.setdefault(r["cliente_id"], [0, 0, float("inf")])[1] += inic

        def vez_dos_outros(alvo: int) -> int:
            return sum(
                int(min(pend, max(0, alvo - inic + (1 if criado < lote["criado"] else 0))))
                for pend, inic, criado in outros.values()
            )

        alvo = iniciados_meus + meus_pendentes  # vídeos deste cliente iniciados quando o lote começar
        primeiro = fixos + meus_antes + vez_dos_outros(alvo)
        ultimo = fixos + meus_antes + pend_lote + anda_lote + vez_dos_outros(alvo + max(0, pend_lote - 1))
        return {"primeiro": primeiro, "ultimo": ultimo}

    # -- geradores (pods do orquestrador) --------------------------------------------------

    def gerador_subindo(self, gpu: str | None = None) -> str:
        gid = novo_id("ger")
        with self.tx() as c:
            c.run(
                "INSERT INTO geradores (id, gpu, estado, criado) VALUES (?, ?, 'subindo', ?)",
                (gid, gpu, time.time()),
            )
        return gid

    def gerador_pronto(self, gid: str, worker: str, gpu: str | None, usd_h: float | None) -> None:
        with self.tx() as c:
            c.run(
                "UPDATE geradores SET estado = 'pronto', worker = ?, gpu = ?, usd_h = ?, pronto_em = ? WHERE id = ?",
                (worker, gpu, usd_h, time.time(), gid),
            )

    def gerador_fim(self, gid: str, motivo: str, falhou: bool = False) -> None:
        with self.tx() as c:
            c.run(
                "UPDATE geradores SET estado = ?, encerrado_em = ?, motivo = ?"
                " WHERE id = ? AND estado IN ('subindo', 'pronto')",
                ("falhou" if falhou else "encerrado", time.time(), motivo[:300], gid),
            )

    def geradores_reiniciar(self, motivo: str = "orquestrador reiniciado") -> int:
        """Na partida do orquestrador: os pods do processo anterior já foram encerrados por ele (ou
        pelo desligamento automático); nenhum continua contando como ligado."""
        with self.tx() as c:
            return c.run(
                "UPDATE geradores SET estado = 'encerrado', encerrado_em = ?, motivo = ?"
                " WHERE estado IN ('subindo', 'pronto')",
                (time.time(), motivo),
            )

    def estado_geradores(self, amostras: int = 10) -> dict[str, Any]:
        """Para a previsão: pods prontos, início das subidas em andamento e a subida típica medida
        (da decisão de subir até o 1º vídeo começar, já com a tradução do pedido; mediana das últimas
        `amostras`, com pelo menos 3). Registro com mais de 2 h 15 min é ignorado: nenhum pod passa de
        2 h (desligamento automático), então é resto de um orquestrador que caiu."""
        agora = time.time()
        with self.tx() as c:
            ativos = c.all(
                "SELECT estado, criado, primeiro_video_em FROM geradores"
                " WHERE estado IN ('subindo', 'pronto') AND criado > ?",
                (agora - 8100,),
            )
            medidas = c.all(
                "SELECT primeiro_video_em - criado AS s FROM geradores WHERE primeiro_video_em IS NOT NULL"
                " ORDER BY criado DESC LIMIT ?",
                (amostras,),
            )
        subidas = [float(m["s"]) for m in medidas]
        # o worker começa antes de os modelos terminarem de baixar: a subida só acaba no 1º vídeo
        gerando = [a for a in ativos if a["estado"] == "pronto" and a["primeiro_video_em"] is not None]
        return {
            "prontos": len(gerando),
            "subindo": sorted(float(a["criado"]) for a in ativos if a not in gerando),
            "subida_s": statistics.median(subidas) if len(subidas) >= 3 else None,
        }

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

    def salvar_analise(self, foto_id: str, analise: dict, sha256: str | None = None) -> None:
        with self.tx() as c:
            c.run(
                "UPDATE fotos SET analise = ?, sha256 = COALESCE(?, sha256) WHERE id = ?",
                (json.dumps(analise, ensure_ascii=False), sha256, foto_id),
            )

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

    def reivindicar(self, worker: str, lease_s: float = 900, tipos: list[str] | None = None) -> dict | None:
        """Próximo job (compilar antes de render; render só com o prompt pronto).

        `tipos`: só esses tipos de job (o pod que ainda baixa os modelos pega só "compilar")."""
        tipos = list(tipos or TIPOS_JOB)
        if not tipos or not set(tipos) <= set(TIPOS_JOB):
            raise BancoError(f"tipos de job inválidos: {tipos}")
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
            # vez por cliente (menos vídeos iniciados primeiro); no mesmo cliente, o pedido mais antigo;
            # no mesmo pedido, a ordem da lista que o cliente vê (arquivo)
            job = c.one(
                "SELECT j.* FROM jobs j JOIN lotes l ON l.id = j.lote_id"
                " WHERE j.status = 'pendente' AND l.status IN ('aprovado', 'processando')"
                " AND (j.tipo = 'compilar' OR l.prompt_en IS NOT NULL)"
                f" AND j.tipo IN ({', '.join('?' * len(tipos))})"
                " ORDER BY CASE j.tipo WHEN 'compilar' THEN 0 ELSE 1 END, l.prioridade,"
                f" ({INICIADOS_DO_CLIENTE}), l.criado, l.id, j.criado, j.arquivo, j.id LIMIT 1" + trava,
                tuple(tipos),
            )
            if job is None:
                return None
            c.run(
                "UPDATE jobs SET status = 'em_andamento', worker = ?, lease_ate = ?, tentativas = tentativas + 1,"
                " atualizado = ? WHERE id = ?",
                (worker, agora + lease_s, agora, job["id"]),
            )
            if job["tipo"] == "render":  # fim da subida deste pod (previsão para o cliente)
                c.run(
                    "UPDATE geradores SET primeiro_video_em = ? WHERE worker = ? AND primeiro_video_em IS NULL",
                    (agora, worker),
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

    # -- limpeza do histórico ---------------------------------------------------------------

    def contar_historico(self, cookies: bool = False) -> dict[str, int]:
        tabelas = HISTORICO + (("cookies",) if cookies else ())
        with self.tx() as c:
            return {t: int(c.one(f"SELECT COUNT(*) AS n FROM {t}")["n"]) for t in tabelas}

    def em_geracao(self) -> int:
        with self.tx() as c:
            return int(c.one("SELECT COUNT(*) AS n FROM lotes WHERE status IN ('aprovado', 'processando')")["n"])

    def apagar_historico(self, cookies: bool = False, forcar: bool = False) -> dict[str, int]:
        """Apaga os pedidos e tudo o que vem deles (trabalhos, fotos, autorizações, eventos), as
        mensagens de suporte e o registro dos pods. Os clientes (códigos de acesso) ficam. Com
        `cookies`, também as escolhas do aviso de cookies. Pedido em geração bloqueia, salvo `forcar`
        (o worker que estiver com um job recebe erro ao concluir)."""
        tabelas = HISTORICO + (("cookies",) if cookies else ())
        with self.tx() as c:
            n = int(c.one("SELECT COUNT(*) AS n FROM lotes WHERE status IN ('aprovado', 'processando')")["n"])
            if n and not forcar:
                raise BancoError(f"{n} pedido(s) em geração: espere terminar (ou use --forcar)")
            apagados = {}
            for t in tabelas:
                apagados[t] = int(c.one(f"SELECT COUNT(*) AS n FROM {t}")["n"])
                c.run(f"DELETE FROM {t}")
        return apagados

    def compactar(self, tabelas: tuple[str, ...] = HISTORICO + ("cookies",)) -> None:
        """Reescreve as tabelas sem as linhas apagadas (o DELETE só marca as linhas como mortas)."""
        if self.pg:
            import psycopg

            with psycopg.connect(self.url, autocommit=True) as con:
                for t in tabelas:
                    con.execute(f"VACUUM FULL {t}")
            return
        with self._lock:
            self._sq.execute("VACUUM")

    def jobs(self, lote_id: str) -> list[dict]:
        with self.tx() as c:
            return c.all("SELECT * FROM jobs WHERE lote_id = ? ORDER BY tipo, arquivo", (lote_id,))

    def trabalho(self) -> list[dict]:
        """Para o orquestrador: renders pendentes e em andamento por lote, com o pedido
        (qualidade e duração decidem o tempo por vídeo) e a prioridade."""
        with self.tx() as c:
            return c.all(
                "SELECT l.id AS lote_id, l.pedido, l.prioridade, MIN(j.criado) AS mais_antigo,"
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
