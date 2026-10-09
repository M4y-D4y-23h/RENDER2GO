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

Saldo pré-pago (`movimentos`, 09/10/2026): o saldo do cliente é a soma dos movimentos (recargas e
ajustes do dono, cobranças de pedidos e estornos). O pedido do plano pago é cobrado inteiro quando as
fotos terminam de chegar (`confirmar_envio`), com o preço por vídeo gravado no lote; o pedido
recusado volta inteiro, e cada vídeo que termina em erro ou cancelado volta sozinho (`_fechar_lote`).
Cada movimento tem uma referência única (ex.: `pedido:<lote>`, `estorno:<job>`): nada é cobrado ou
devolvido duas vezes. O extrato é registro financeiro: a limpeza do histórico não o apaga.

Contas por e-mail (ADR 0007, 09/10/2026): a conta nasce no 1º acesso com o código que chega por
e-mail (`codigos`), já no teste gratuito; a sessão fica num cookie HttpOnly e no banco só o SHA-256
(`sessoes`, também as do painel). O aceite dos termos (`aceites`) e os registros de acesso
(`acessos`, Marco Civil da Internet: 6 meses) não saem com a limpeza do histórico.

Pagamentos (ADR 0008): cada Pix de recarga é uma linha em `pagamentos`; o crédito entra no saldo uma
vez só (referência `pagamento:<provedor>:<id>`). Painel → orquestrador: `comandos` (desligar um pod,
parada de emergência) e `estado` (pausa e sinal de vida do orquestrador). Cada pod recebe um token de
worker próprio (`tokens_worker`), que vence com o pod e é revogado quando ele é encerrado.

Postgres: conexões num pool (RENDER2GO_DB_POOL, padrão 10 por processo); antes, cada transação abria
uma conexão nova.
"""

from __future__ import annotations

import hashlib
import json
import os
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
        similaridade DOUBLE PRECISION, bytes_video BIGINT, deriva_cor DOUBLE PRECISION, salto_cor DOUBLE PRECISION,
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
        encerrado_em DOUBLE PRECISION, motivo TEXT, datacentro TEXT, pod_criado_em DOUBLE PRECISION,
        desligado_em DOUBLE PRECISION)""",
    # saldo pré-pago: um movimento por recarga, ajuste, cobrança de pedido ou estorno (centavos de real).
    # Sem chave estrangeira para lotes e jobs: o extrato fica depois da limpeza do histórico.
    """CREATE TABLE IF NOT EXISTS movimentos (
        id TEXT PRIMARY KEY, cliente_id TEXT NOT NULL REFERENCES clientes(id), quando DOUBLE PRECISION NOT NULL,
        tipo TEXT NOT NULL, valor BIGINT NOT NULL, referencia TEXT NOT NULL UNIQUE, lote_id TEXT, job_id TEXT,
        descricao TEXT, por TEXT)""",
    "CREATE INDEX IF NOT EXISTS movimentos_cliente ON movimentos (cliente_id, quando)",
    # "Tenho interesse" numa finalidade ainda em breve (render2go_servico.modelos): um por cliente e finalidade
    """CREATE TABLE IF NOT EXISTS interesses (
        cliente_id TEXT NOT NULL REFERENCES clientes(id), finalidade TEXT NOT NULL,
        quando DOUBLE PRECISION NOT NULL, PRIMARY KEY (cliente_id, finalidade))""",
    "CREATE INDEX IF NOT EXISTS movimentos_lote ON movimentos (lote_id)",
    # contas por e-mail (ADR 0007): sessões (cliente e painel), códigos de entrada, aceites e acessos
    """CREATE TABLE IF NOT EXISTS sessoes (
        id TEXT PRIMARY KEY, tipo TEXT NOT NULL, cliente_id TEXT, admin TEXT, criado DOUBLE PRECISION NOT NULL,
        expira DOUBLE PRECISION NOT NULL, ultimo_uso DOUBLE PRECISION, ip TEXT, agente TEXT,
        revogada_em DOUBLE PRECISION)""",
    """CREATE TABLE IF NOT EXISTS codigos (
        id TEXT PRIMARY KEY, email_norm TEXT NOT NULL, email TEXT NOT NULL, codigo TEXT NOT NULL,
        criado DOUBLE PRECISION NOT NULL, expira DOUBLE PRECISION NOT NULL, tentativas INTEGER NOT NULL DEFAULT 0,
        usado_em DOUBLE PRECISION, ip TEXT, dispositivo TEXT)""",
    # limites de requisições compartilhados entre processos e servidores (contagem por janela de tempo)
    """CREATE UNLOGGED TABLE IF NOT EXISTS limites (
        chave TEXT NOT NULL, janela BIGINT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (chave, janela))""",
    # aceite dos termos e da política de privacidade (versão, quando, de onde): só inserção
    """CREATE TABLE IF NOT EXISTS aceites (
        id TEXT PRIMARY KEY, cliente_id TEXT NOT NULL, documento TEXT NOT NULL, versao TEXT NOT NULL,
        quando DOUBLE PRECISION NOT NULL, ip TEXT, agente TEXT)""",
    # registros de acesso à aplicação (Marco Civil da Internet, art. 15: 6 meses): entrada, saída, pedido...
    """CREATE TABLE IF NOT EXISTS acessos (
        cliente_id TEXT, evento TEXT NOT NULL, quando DOUBLE PRECISION NOT NULL, ip TEXT, porta INTEGER,
        agente TEXT, detalhe TEXT)""",
    # painel → orquestrador (desligar um pod, parada de emergência) e estado compartilhado
    """CREATE TABLE IF NOT EXISTS comandos (
        id TEXT PRIMARY KEY, tipo TEXT NOT NULL, alvo TEXT, criado DOUBLE PRECISION NOT NULL, por TEXT,
        executado_em DOUBLE PRECISION, resultado TEXT)""",
    """CREATE TABLE IF NOT EXISTS estado (chave TEXT PRIMARY KEY, valor TEXT, atualizado DOUBLE PRECISION NOT NULL)""",
    # um token de worker por pod: vence com o pod e é revogado quando ele é encerrado
    """CREATE TABLE IF NOT EXISTS tokens_worker (
        sha TEXT PRIMARY KEY, worker TEXT NOT NULL, gerador TEXT, criado DOUBLE PRECISION NOT NULL,
        expira DOUBLE PRECISION NOT NULL, revogado_em DOUBLE PRECISION)""",
    # recargas por Pix (ADR 0008); registro financeiro: a limpeza do histórico não apaga
    """CREATE TABLE IF NOT EXISTS pagamentos (
        id TEXT PRIMARY KEY, cliente_id TEXT NOT NULL REFERENCES clientes(id), valor BIGINT NOT NULL,
        provedor TEXT NOT NULL, provedor_id TEXT UNIQUE, status TEXT NOT NULL, criado DOUBLE PRECISION NOT NULL,
        atualizado DOUBLE PRECISION NOT NULL, expira DOUBLE PRECISION, copia_cola TEXT, qr_base64 TEXT,
        documento TEXT, creditado_em DOUBLE PRECISION, conferido_em DOUBLE PRECISION, detalhe TEXT)""",
    # o que o painel fez (quem, o quê, de onde): saldo, plano, contas, pods; só inserção
    """CREATE TABLE IF NOT EXISTS auditoria_admin (
        quando DOUBLE PRECISION NOT NULL, admin TEXT NOT NULL, acao TEXT NOT NULL, alvo TEXT, detalhe TEXT,
        ip TEXT)""",
]
# índices: depois das colunas novas (um banco antigo ganha a coluna antes do índice que a usa)
INDICES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS clientes_email ON clientes (email_norm)",
    "CREATE INDEX IF NOT EXISTS clientes_email_resumo ON clientes (email_resumo)",
    "CREATE INDEX IF NOT EXISTS lotes_cliente ON lotes (cliente_id, criado)",
    "CREATE INDEX IF NOT EXISTS lotes_status ON lotes (status, criado)",
    "CREATE INDEX IF NOT EXISTS lotes_ip ON lotes (ip)",
    "CREATE INDEX IF NOT EXISTS lotes_rede ON lotes (rede)",
    "CREATE INDEX IF NOT EXISTS lotes_dispositivo ON lotes (dispositivo)",
    "CREATE INDEX IF NOT EXISTS jobs_lote ON jobs (lote_id, tipo)",
    "CREATE INDEX IF NOT EXISTS jobs_worker ON jobs (worker)",
    "CREATE INDEX IF NOT EXISTS eventos_lote ON eventos (lote_id, quando)",
    "CREATE INDEX IF NOT EXISTS consentimentos_lote ON consentimentos (lote_id)",
    "CREATE INDEX IF NOT EXISTS suporte_ip ON suporte (ip, quando)",
    "CREATE INDEX IF NOT EXISTS suporte_dispositivo ON suporte (dispositivo, quando)",
    "CREATE INDEX IF NOT EXISTS suporte_cliente ON suporte (cliente_id, quando)",
    "CREATE INDEX IF NOT EXISTS cookies_ip ON cookies (ip, quando)",
    "CREATE INDEX IF NOT EXISTS geradores_estado ON geradores (estado, criado)",
    "CREATE INDEX IF NOT EXISTS sessoes_cliente ON sessoes (cliente_id)",
    "CREATE INDEX IF NOT EXISTS codigos_email ON codigos (email_norm, criado)",
    "CREATE INDEX IF NOT EXISTS aceites_cliente ON aceites (cliente_id)",
    "CREATE INDEX IF NOT EXISTS acessos_cliente ON acessos (cliente_id, quando)",
    "CREATE INDEX IF NOT EXISTS acessos_quando ON acessos (quando)",
    "CREATE INDEX IF NOT EXISTS pagamentos_cliente ON pagamentos (cliente_id, criado)",
    "CREATE INDEX IF NOT EXISTS pagamentos_status ON pagamentos (status, criado)",
    "CREATE INDEX IF NOT EXISTS comandos_pendentes ON comandos (executado_em, criado)",
    "CREATE INDEX IF NOT EXISTS tokens_worker_gerador ON tokens_worker (gerador)",
    "CREATE INDEX IF NOT EXISTS auditoria_admin_quando ON auditoria_admin (quando)",
]
# vídeos já iniciados (ou prontos) de cada cliente nos pedidos em andamento: a vez na fila é por cliente
INICIADOS_POR_CLIENTE = (
    "SELECT m.cliente_id, COUNT(*) AS n FROM jobs k JOIN lotes m ON m.id = k.lote_id"
    " WHERE m.status IN ('aprovado', 'processando') AND k.tipo = 'render' AND k.status <> 'pendente'"
    " GROUP BY m.cliente_id"
)
TIPOS_JOB = ("compilar", "render")
# dois CREATE TABLE IF NOT EXISTS ao mesmo tempo podem falhar no Postgres (pg_type duplicado)
TRAVA_ESQUEMA = 20261008
# tabelas do histórico (pedidos e tudo o que vem deles), na ordem de apagar (chaves estrangeiras).
# Ficam de fora, de propósito: clientes, sessões, aceites dos termos, registros de acesso (Marco
# Civil: 6 meses), movimentos e pagamentos (registro financeiro) e a auditoria do painel.
HISTORICO = (
    "jobs",
    "consentimentos",
    "fotos",
    "eventos",
    "lotes",
    "suporte",
    "geradores",
    "codigos",
    "comandos",
    "tokens_worker",
    "limites",
)
# colunas que entraram depois da primeira implantação: (tabela, coluna, tipo)
COLUNAS_NOVAS = [
    ("fotos", "analise", "TEXT"),  # aviso de pessoas no painel (render2go_servico.pessoas)
    ("lotes", "ip", "TEXT"),  # IP de quem criou o pedido (limite do teste gratuito por IP)
    ("lotes", "fotos_apagadas", "DOUBLE PRECISION"),  # fotos de entrada apagadas ao fechar o lote
    ("lotes", "dispositivo", "TEXT"),  # identificador do navegador (limite do teste por dispositivo)
    ("lotes", "agente", "TEXT"),  # User-Agent do navegador
    ("lotes", "fuso", "TEXT"),  # fuso horário informado pelo navegador (sinal de VPN)
    ("fotos", "sha256", "TEXT"),  # impressão da foto, que fica depois que a foto é apagada
    ("lotes", "impressao", "TEXT"),  # resumo dos sinais do navegador (acha o mesmo navegador sem o identificador)
    ("consentimentos", "sinais", "TEXT"),  # sinais do navegador no momento da declaração (JSON)
    ("lotes", "preco_video", "BIGINT"),  # centavos por vídeo do pré-pago (NULL: teste ou interno)
    ("jobs", "deriva_cor", "DOUBLE PRECISION"),  # continuidade do vídeo (render2go_worker.conferencia)
    ("jobs", "salto_cor", "DOUBLE PRECISION"),
    ("geradores", "datacentro", "TEXT"),  # relatório de GPUs no painel
    ("geradores", "pod_criado_em", "DOUBLE PRECISION"),  # o RunPod criou o pod (começa a cobrança)
    ("geradores", "desligado_em", "DOUBLE PRECISION"),  # o pod foi encerrado (fim da cobrança)
    ("clientes", "email", "TEXT"),  # conta por e-mail (ADR 0007): o endereço para os códigos
    ("clientes", "email_norm", "TEXT"),  # o mesmo, normalizado (sem pontos/+ no Gmail): uma conta por caixa
    ("clientes", "termos_versao", "TEXT"),  # versão dos termos aceitos por último
    ("clientes", "ultimo_acesso", "DOUBLE PRECISION"),
    ("clientes", "excluido_em", "DOUBLE PRECISION"),  # conta excluída a pedido do titular (LGPD)
    # resumo irreversível (HMAC) do e-mail de uma conta excluída: o mesmo e-mail não ganha outro teste grátis
    ("clientes", "email_resumo", "TEXT"),
    ("lotes", "rede", "TEXT"),  # IPv4 inteiro ou IPv6 /64: limite do teste por rede (troca de IPv6 não escapa)
    ("lotes", "modelo", "TEXT"),  # modelo de geração (o pod só pega jobs dos modelos que roda)
    ("lotes", "porta", "INTEGER"),  # porta de origem da conexão (Marco Civil, com o IP e a hora)
]
MODELO_PADRAO = "wan2.2-i2v"
SESSAO_TIPOS = ("cliente", "admin")
COMANDOS = ("desligar_pod", "emergencia", "retomar")
TIPOS_MOVIMENTO = ("recarga", "ajuste", "pedido", "estorno")
RESOLUCAO = {"rapida": "480p", "alta": "720p"}  # qualidade do pedido → resolução (descrição no extrato)
# lote que já passou do envio e não foi recusado: conta nos limites do teste gratuito
ATIVO = "status NOT IN ('aguardando_envio', 'recusado')"
ATIVO_L = "l." + ATIVO  # o mesmo, com a tabela lotes como "l"
APROVADOS = ("aprovado", "processando", "concluido", "concluido_com_falhas")
ORIGEM_EVENTO = ("ip", "dispositivo", "agente", "fuso", "impressao", "porta")  # gravada no evento "criado"


class BancoError(Exception):
    pass


class SaldoInsuficiente(BancoError):
    def __init__(self, custo: int, saldo: int):
        super().__init__(f"saldo insuficiente: o pedido custa {custo} centavos e o saldo é {saldo}")
        self.custo, self.saldo = custo, saldo


def sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def novo_id(prefixo: str) -> str:
    return f"{prefixo}_{secrets.token_hex(8)}"


class Banco:
    """`url`: postgresql://... ou sqlite:///caminho (ou sqlite:///:memory:)."""

    def __init__(self, url: str, pool: int | None = None):
        self.url = url
        self.pg = url.startswith(("postgresql://", "postgres://"))
        self._lock = threading.RLock()
        if self.pg:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            # um pool por processo (API: um por worker do uvicorn); o Postgres atende ~100 conexões
            tamanho = pool or max(2, int(os.environ.get("RENDER2GO_DB_POOL", 10)))
            self._pool = ConnectionPool(
                url,
                min_size=1,
                max_size=tamanho,
                kwargs={"row_factory": dict_row, "autocommit": False},
                timeout=15,
                max_idle=300,
                name="render2go",
                open=True,
            )
        else:
            caminho = url.removeprefix("sqlite:///")
            self._sq = sqlite3.connect(caminho, check_same_thread=False, isolation_level=None)
            self._sq.row_factory = sqlite3.Row
            self._sq.execute("PRAGMA foreign_keys=ON")
            self._sq.execute("PRAGMA busy_timeout=5000")

    def trava_exclusiva(self, chave: int) -> Any | None:
        """Trava de sessão do Postgres numa conexão própria (fora do pool): enquanto a conexão viver, só
        este processo a tem. Devolve a conexão (quem chama fecha no fim) ou None se outro processo tem a
        trava. No SQLite (um processo só), sempre consegue."""
        if not self.pg:
            return sqlite3  # qualquer objeto verdadeiro: não há outro processo
        import psycopg

        con = psycopg.connect(self.url, autocommit=True)
        if con.execute("SELECT pg_try_advisory_lock(%s)", (chave,)).fetchone()[0]:
            return con
        con.close()
        return None

    def lotes_para_analisar(self, limite: int = 20) -> list[str]:
        """Lotes com foto enviada e ainda sem a análise de pessoas (serviço `render2go-servico analisar`)."""
        with self.tx() as c:
            return [
                r["lote_id"]
                for r in c.all(
                    "SELECT f.lote_id, MIN(l.criado) AS criado FROM fotos f JOIN lotes l ON l.id = f.lote_id"
                    " WHERE f.enviada = 1 AND f.analise IS NULL"
                    " AND l.status IN ('aguardando_aprovacao', 'aprovado', 'processando')"
                    " GROUP BY f.lote_id ORDER BY criado LIMIT ?",
                    (limite,),
                )
            ]

    def fechar(self) -> None:
        """Fecha as conexões (fim do processo e testes)."""
        if self.pg:
            self._pool.close()
        else:
            self._sq.close()

    # -- conexão e transação --------------------------------------------------------------

    @contextmanager
    def tx(self) -> Iterator[Any]:
        """Transação; no SQLite, BEGIN IMMEDIATE (um escritor por vez). No Postgres, a conexão vem do
        pool e volta para ele no fim (commit no sucesso, rollback na exceção)."""
        if self.pg:
            with self._pool.connection() as con, con.cursor() as cur:
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

    def _sql_esquema(self, sql: str) -> str:
        if self.pg:
            return sql
        return sql.replace("DOUBLE PRECISION", "REAL").replace("BIGINT", "INTEGER").replace("UNLOGGED ", "")

    def criar_esquema(self) -> None:
        with self.tx() as c:
            if self.pg:  # a API e o orquestrador sobem juntos: um cria as tabelas novas, o outro espera
                c.run("SELECT pg_advisory_xact_lock(?)", (TRAVA_ESQUEMA,))
            for sql in ESQUEMA:
                c.run(self._sql_esquema(sql))
            for tabela, coluna, tipo in COLUNAS_NOVAS:
                tipo = tipo if self.pg else tipo.replace("DOUBLE PRECISION", "REAL")
                if self.pg:  # IF NOT EXISTS: a API e o orquestrador sobem juntos
                    c.run(f"ALTER TABLE {tabela} ADD COLUMN IF NOT EXISTS {coluna} {tipo}")
                elif not any(r["name"] == coluna for r in c.all(f"PRAGMA table_info({tabela})")):
                    c.run(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")
            for sql in INDICES:
                c.run(sql)

    def evento(self, c, lote_id: str | None, tipo: str, detalhe: Any = None) -> None:
        c.run(
            "INSERT INTO eventos (lote_id, quando, tipo, detalhe) VALUES (?, ?, ?, ?)",
            (lote_id, time.time(), tipo, json.dumps(detalhe, ensure_ascii=False)),
        )

    # -- clientes -------------------------------------------------------------------------

    def criar_cliente(
        self,
        nome: str,
        cota_fotos_dia: int = 500,
        plano: str = "pago",
        email: str | None = None,
        email_norm: str | None = None,
    ) -> tuple[str, str]:
        """Devolve (id, token). O token só existe aqui; o banco guarda o SHA-256. Conta por e-mail:
        o token não é mostrado a ninguém (a pessoa entra com o código que chega no e-mail)."""
        if plano not in PLANOS:
            raise BancoError(f"plano desconhecido: {plano}")
        cid, token = novo_id("cli"), secrets.token_urlsafe(32)
        with self.tx() as c:
            if email_norm and c.one("SELECT id FROM clientes WHERE email_norm = ?", (email_norm,)):
                raise BancoError("já existe uma conta com este e-mail")
            c.run(
                "INSERT INTO clientes (id, nome, token_sha, cota_fotos_dia, plano, criado, email, email_norm)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, nome, sha(token), cota_fotos_dia, plano, time.time(), email, email_norm),
            )
        return cid, token

    # -- contas por e-mail (ADR 0007) -----------------------------------------------------------

    def conta_por_email(self, email_norm: str) -> dict | None:
        with self.tx() as c:
            return c.one("SELECT * FROM clientes WHERE email_norm = ?", (email_norm,))

    def conta_do_email(
        self, email: str, email_norm: str, plano: str = "teste", cota: int = 500, resumo: str | None = None
    ) -> tuple[dict, bool]:
        """A conta deste e-mail; sem conta, cria (no 1º acesso, já no teste gratuito). (conta, nova).
        `resumo`: HMAC do e-mail; se uma conta excluída tinha esse e-mail, a nova nasce sem o teste grátis."""
        if plano not in PLANOS:
            raise BancoError(f"plano desconhecido: {plano}")
        agora = time.time()
        with self.tx() as c:
            atual = c.one("SELECT * FROM clientes WHERE email_norm = ?", (email_norm,))
            if atual:
                return atual, False
            if plano == "teste" and resumo and c.one("SELECT id FROM clientes WHERE email_resumo = ?", (resumo,)):
                plano = "pago"  # o teste grátis desse e-mail já foi usado por uma conta excluída
            cid = novo_id("cli")
            n = c.run(
                "INSERT INTO clientes (id, nome, token_sha, cota_fotos_dia, plano, criado, email, email_norm)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (email_norm) DO NOTHING",
                (cid, email, sha(secrets.token_urlsafe(32)), cota, plano, agora, email, email_norm),
            )
            conta = c.one("SELECT * FROM clientes WHERE email_norm = ?", (email_norm,))
        return conta, bool(n)

    def definir_email(self, cliente_id: str, email: str, email_norm: str) -> None:
        """Liga um e-mail a uma conta antiga (código de acesso): a pessoa passa a entrar pelo e-mail."""
        with self.tx() as c:
            dono = c.one("SELECT id FROM clientes WHERE email_norm = ?", (email_norm,))
            if dono and dono["id"] != cliente_id:
                raise BancoError("este e-mail já é de outra conta")
            if not c.run("UPDATE clientes SET email = ?, email_norm = ? WHERE id = ?", (email, email_norm, cliente_id)):
                raise BancoError("cliente não encontrado")

    def mudar_plano(self, cliente_id: str, plano: str) -> None:
        if plano not in PLANOS:
            raise BancoError(f"plano desconhecido: {plano}")
        with self.tx() as c:
            if not c.run("UPDATE clientes SET plano = ? WHERE id = ?", (plano, cliente_id)):
                raise BancoError("cliente não encontrado")

    def reativar_cliente(self, cliente_id: str) -> bool:
        with self.tx() as c:
            return bool(
                c.run("UPDATE clientes SET ativo = 1 WHERE id = ? AND ativo = 0 AND excluido_em IS NULL", (cliente_id,))
            )

    def registrar_aceite(self, cliente_id: str, documentos: dict[str, str], origem: dict) -> int:
        """Aceite dos documentos (ex.: {"termos": "termos-v1", "privacidade": "privacidade-v1"}) que a
        conta ainda não aceitou nessa versão; devolve quantos foram gravados."""
        agora, novos = time.time(), 0
        with self.tx() as c:
            for doc, versao in documentos.items():
                if c.one(
                    "SELECT id FROM aceites WHERE cliente_id = ? AND documento = ? AND versao = ?",
                    (cliente_id, doc, versao),
                ):
                    continue
                c.run(
                    "INSERT INTO aceites (id, cliente_id, documento, versao, quando, ip, agente)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (novo_id("ace"), cliente_id, doc, versao, agora, origem.get("ip"), origem.get("agente")),
                )
                novos += 1
            if "termos" in documentos:
                c.run("UPDATE clientes SET termos_versao = ? WHERE id = ?", (documentos["termos"], cliente_id))
        return novos

    def registrar_acesso(self, cliente_id: str | None, evento: str, origem: dict, detalhe: Any = None) -> None:
        """Registro de acesso (Marco Civil, art. 15): conta, evento, IP, porta de origem e hora (UTC)."""
        with self.tx() as c:
            c.run(
                "INSERT INTO acessos (cliente_id, evento, quando, ip, porta, agente, detalhe)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    cliente_id,
                    evento,
                    time.time(),
                    origem.get("ip"),
                    origem.get("porta"),
                    (origem.get("agente") or "")[:300] or None,
                    json.dumps(detalhe, ensure_ascii=False) if detalhe is not None else None,
                ),
            )
            if cliente_id and evento == "entrada":
                c.run("UPDATE clientes SET ultimo_acesso = ? WHERE id = ?", (time.time(), cliente_id))

    def purgar(self, acessos_dias: int = 190) -> dict[str, int]:
        """Limpeza periódica: registros de acesso vencidos (o Marco Civil pede 6 meses), códigos e
        limites antigos, sessões e tokens de worker vencidos."""
        agora = time.time()
        with self.tx() as c:
            return {
                "acessos": c.run("DELETE FROM acessos WHERE quando < ?", (agora - max(183, acessos_dias) * 86400,)),
                "codigos": c.run("DELETE FROM codigos WHERE criado < ?", (agora - 2 * 86400,)),
                "limites": c.run("DELETE FROM limites WHERE janela < ?", (int(agora) - 2 * 86400,)),
                "sessoes": c.run("DELETE FROM sessoes WHERE expira < ?", (agora - 30 * 86400,)),
                "tokens_worker": c.run("DELETE FROM tokens_worker WHERE expira < ?", (agora - 86400,)),
            }

    def dados_da_conta(self, cliente_id: str) -> dict[str, Any]:
        """Tudo o que o serviço guarda sobre a conta (LGPD, art. 18: acesso e portabilidade)."""
        with self.tx() as c:
            conta = c.one(
                "SELECT id, nome, email, plano, ativo, criado, termos_versao, ultimo_acesso FROM clientes WHERE id = ?",
                (cliente_id,),
            )
            if conta is None:
                raise BancoError("cliente não encontrado")
            return {
                "conta": conta,
                "aceites": c.all(
                    "SELECT documento, versao, quando, ip FROM aceites WHERE cliente_id = ? ORDER BY quando",
                    (cliente_id,),
                ),
                "acessos": c.all(
                    "SELECT evento, quando, ip, porta, agente FROM acessos WHERE cliente_id = ? ORDER BY quando DESC"
                    " LIMIT 1000",
                    (cliente_id,),
                ),
                "sessoes": c.all(
                    "SELECT criado, expira, ultimo_uso, ip, agente, revogada_em FROM sessoes"
                    " WHERE cliente_id = ? AND tipo = 'cliente' ORDER BY criado DESC",
                    (cliente_id,),
                ),
                "pedidos": c.all(
                    "SELECT id, status, pedido, n_fotos, criado, concluido_em, motivo, ip, dispositivo, agente, fuso"
                    " FROM lotes WHERE cliente_id = ? ORDER BY criado DESC",
                    (cliente_id,),
                ),
                "autorizacoes": c.all(
                    "SELECT lote_id, declarante, texto_versao, texto, quando, ip, dispositivo FROM consentimentos"
                    " WHERE cliente_id = ? ORDER BY quando",
                    (cliente_id,),
                ),
                "movimentos": c.all(
                    "SELECT quando, tipo, valor, lote_id, descricao FROM movimentos WHERE cliente_id = ?"
                    " ORDER BY quando",
                    (cliente_id,),
                ),
                "pagamentos": c.all(
                    "SELECT id, valor, provedor, status, criado, creditado_em, documento FROM pagamentos"
                    " WHERE cliente_id = ? ORDER BY criado",
                    (cliente_id,),
                ),
                "suporte": c.all(
                    "SELECT assunto, mensagem, email, quando FROM suporte WHERE cliente_id = ? ORDER BY quando",
                    (cliente_id,),
                ),
                "interesses": c.all(
                    "SELECT finalidade, quando FROM interesses WHERE cliente_id = ? ORDER BY quando", (cliente_id,)
                ),
            }

    def excluir_conta(self, cliente_id: str, resumo: str | None = None) -> None:
        """Exclusão a pedido do titular (LGPD, art. 18, VI): a conta deixa de existir para o login, o
        e-mail é apagado e as sessões caem. Ficam, pelo tempo que a lei pede, o extrato e os pagamentos
        (registro financeiro) e os registros de acesso (Marco Civil). Saldo positivo bloqueia: o
        reembolso é pedido pelo suporte antes."""
        agora = time.time()
        with self.tx() as c:
            self._travar_cliente(c, cliente_id)
            if self._saldo(c, cliente_id) > 0:
                raise BancoError("a conta ainda tem saldo: peça o reembolso pelo suporte antes de excluir")
            em_curso = c.one(
                "SELECT COUNT(*) AS n FROM lotes WHERE cliente_id = ?"
                " AND status IN ('aguardando_aprovacao', 'aprovado', 'processando')",
                (cliente_id,),
            )
            if int(em_curso["n"]):
                raise BancoError("há pedidos em andamento: espere terminar para excluir a conta")
            n = c.run(
                "UPDATE clientes SET ativo = 0, excluido_em = ?, email = NULL, email_norm = NULL, nome = ?,"
                " token_sha = ?, email_resumo = ? WHERE id = ? AND excluido_em IS NULL",
                (agora, "conta excluída", sha(secrets.token_urlsafe(32)), resumo, cliente_id),
            )
            if not n:
                raise BancoError("cliente não encontrado")
            c.run(
                "UPDATE sessoes SET revogada_em = ? WHERE cliente_id = ? AND revogada_em IS NULL", (agora, cliente_id)
            )
            c.run("DELETE FROM interesses WHERE cliente_id = ?", (cliente_id,))
            c.run("UPDATE suporte SET email = NULL WHERE cliente_id = ?", (cliente_id,))

    # -- códigos de entrada por e-mail ----------------------------------------------------------

    def criar_codigo(self, email_norm: str, email: str, codigo_hash: str, validade_s: float, origem: dict) -> str:
        """Novo código para o e-mail; os anteriores ainda não usados deixam de valer."""
        agora = time.time()
        cid = novo_id("cod")
        with self.tx() as c:
            c.run(
                "UPDATE codigos SET usado_em = ? WHERE email_norm = ? AND usado_em IS NULL",
                (agora, email_norm),
            )
            c.run(
                "INSERT INTO codigos (id, email_norm, email, codigo, criado, expira, ip, dispositivo)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cid,
                    email_norm,
                    email,
                    codigo_hash,
                    agora,
                    agora + validade_s,
                    origem.get("ip"),
                    origem.get("dispositivo"),
                ),
            )
        return cid

    def conferir_codigo(self, email_norm: str, codigo_hash: str, max_tentativas: int = 5) -> bool:
        """Confere o código mais recente do e-mail (vale uma vez, dentro do prazo e de `max_tentativas`)."""
        import hmac as _hmac

        agora = time.time()
        trava = " FOR UPDATE" if self.pg else ""
        with self.tx() as c:
            cod = c.one(
                "SELECT id, codigo, tentativas FROM codigos WHERE email_norm = ? AND usado_em IS NULL AND expira > ?"
                " ORDER BY criado DESC LIMIT 1" + trava,
                (email_norm, agora),
            )
            if cod is None:
                return False
            if _hmac.compare_digest(cod["codigo"], codigo_hash):
                c.run("UPDATE codigos SET usado_em = ? WHERE id = ?", (agora, cod["id"]))
                return True
            tentativas = int(cod["tentativas"]) + 1
            c.run(
                "UPDATE codigos SET tentativas = ?, usado_em = ? WHERE id = ?",
                (tentativas, agora if tentativas >= max_tentativas else None, cod["id"]),
            )
            return False

    # -- sessões (cliente e painel) -------------------------------------------------------------

    def criar_sessao(
        self, tipo: str, validade_s: float, origem: dict, cliente_id: str | None = None, admin: str | None = None
    ) -> str:
        """Devolve o token da sessão (vai só para o cookie); o banco guarda o SHA-256."""
        if tipo not in SESSAO_TIPOS:
            raise BancoError(f"tipo de sessão desconhecido: {tipo}")
        token, agora = secrets.token_urlsafe(32), time.time()
        with self.tx() as c:
            c.run(
                "INSERT INTO sessoes (id, tipo, cliente_id, admin, criado, expira, ultimo_uso, ip, agente)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sha(token),
                    tipo,
                    cliente_id,
                    admin,
                    agora,
                    agora + validade_s,
                    agora,
                    origem.get("ip"),
                    (origem.get("agente") or "")[:300] or None,
                ),
            )
        return token

    def sessao(self, token: str, tipo: str) -> dict | None:
        """Sessão válida (não vencida nem revogada); a de cliente vem com a conta (ativa)."""
        agora = time.time()
        with self.tx() as c:
            s = c.one(
                "SELECT * FROM sessoes WHERE id = ? AND tipo = ? AND revogada_em IS NULL AND expira > ?",
                (sha(token), tipo, agora),
            )
            if s is None:
                return None
            if tipo == "cliente":
                cli = c.one("SELECT * FROM clientes WHERE id = ? AND ativo = 1", (s["cliente_id"],))
                if cli is None:
                    return None
                s["cliente"] = cli
            if agora - float(s["ultimo_uso"] or 0) > 300:  # sem escrever a cada requisição
                c.run("UPDATE sessoes SET ultimo_uso = ? WHERE id = ?", (agora, s["id"]))
            return s

    def revogar_sessao(self, token: str) -> None:
        with self.tx() as c:
            c.run("UPDATE sessoes SET revogada_em = ? WHERE id = ? AND revogada_em IS NULL", (time.time(), sha(token)))

    def revogar_sessoes(self, cliente_id: str | None = None, tipo: str = "cliente") -> int:
        """Todas as sessões da conta (sair de todos os aparelhos); sem conta, todas as do tipo."""
        with self.tx() as c:
            if cliente_id:
                return c.run(
                    "UPDATE sessoes SET revogada_em = ? WHERE cliente_id = ? AND tipo = ? AND revogada_em IS NULL",
                    (time.time(), cliente_id, tipo),
                )
            return c.run(
                "UPDATE sessoes SET revogada_em = ? WHERE tipo = ? AND revogada_em IS NULL", (time.time(), tipo)
            )

    # -- limites de requisições (compartilhados entre processos) --------------------------------

    def contar(self, chave: str, janela_s: int, somar: bool = True) -> int:
        """Conta na janela fixa de `janela_s` segundos que contém agora; `somar` acrescenta 1 antes.
        Postgres: tabela UNLOGGED (sem WAL), uma linha por chave e janela."""
        janela = int(time.time() // janela_s) * janela_s
        chave = f"{janela_s}:{chave}"[:300]
        with self.tx() as c:
            if not somar:
                r = c.one("SELECT n FROM limites WHERE chave = ? AND janela = ?", (chave, janela))
                return int(r["n"]) if r else 0
            r = c.one(
                "INSERT INTO limites (chave, janela, n) VALUES (?, ?, 1)"
                " ON CONFLICT (chave, janela) DO UPDATE SET n = limites.n + 1 RETURNING n",
                (chave, janela),
            )
            return int(r["n"])

    # -- painel: auditoria, comandos para o orquestrador e estado ---------------------------------

    def auditar(
        self, admin: str, acao: str, alvo: str | None = None, detalhe: Any = None, ip: str | None = None
    ) -> None:
        with self.tx() as c:
            c.run(
                "INSERT INTO auditoria_admin (quando, admin, acao, alvo, detalhe, ip) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), admin, acao, alvo, json.dumps(detalhe, ensure_ascii=False) if detalhe else None, ip),
            )

    def auditoria_admin(self, limite: int = 100) -> list[dict]:
        with self.tx() as c:
            return c.all(
                "SELECT quando, admin, acao, alvo, detalhe, ip FROM auditoria_admin ORDER BY quando DESC LIMIT ?",
                (limite,),
            )

    def definir_estado(self, chave: str, valor: str | None) -> None:
        with self.tx() as c:
            c.run(
                "INSERT INTO estado (chave, valor, atualizado) VALUES (?, ?, ?)"
                " ON CONFLICT (chave) DO UPDATE SET valor = excluded.valor, atualizado = excluded.atualizado",
                (chave, valor, time.time()),
            )

    def ler_estado(self, chave: str) -> dict | None:
        with self.tx() as c:
            return c.one("SELECT valor, atualizado FROM estado WHERE chave = ?", (chave,))

    def comando(self, tipo: str, por: str, alvo: str | None = None) -> str:
        """Pedido do painel ao orquestrador (ele executa no próximo ciclo, em até ~10 s)."""
        if tipo not in COMANDOS:
            raise BancoError(f"comando desconhecido: {tipo}")
        cid = novo_id("cmd")
        with self.tx() as c:
            c.run(
                "INSERT INTO comandos (id, tipo, alvo, criado, por) VALUES (?, ?, ?, ?, ?)",
                (cid, tipo, alvo, time.time(), por),
            )
        return cid

    def comandos_pendentes(self) -> list[dict]:
        with self.tx() as c:
            return c.all("SELECT * FROM comandos WHERE executado_em IS NULL ORDER BY criado")

    def comando_executado(self, cid: str, resultado: str) -> None:
        with self.tx() as c:
            c.run(
                "UPDATE comandos SET executado_em = ?, resultado = ? WHERE id = ?", (time.time(), resultado[:500], cid)
            )

    def comandos_recentes(self, limite: int = 20) -> list[dict]:
        with self.tx() as c:
            return c.all("SELECT * FROM comandos ORDER BY criado DESC LIMIT ?", (limite,))

    def geradores_ativos(self) -> list[dict]:
        """Pods ligados agora (subindo ou prontos), para o painel desligar um deles."""
        with self.tx() as c:
            return c.all(
                "SELECT g.id, g.worker, g.gpu, g.usd_h, g.estado, g.criado, g.pronto_em, g.datacentro,"
                " (SELECT COUNT(*) FROM jobs j WHERE j.worker = g.worker AND g.worker IS NOT NULL"
                "  AND j.status = 'em_andamento') AS em_andamento"
                " FROM geradores g WHERE g.estado IN ('subindo', 'pronto') ORDER BY g.criado"
            )

    # -- tokens de worker por pod -----------------------------------------------------------------

    def criar_token_worker(self, worker: str, gerador: str | None, validade_s: float) -> str:
        token, agora = secrets.token_urlsafe(32), time.time()
        with self.tx() as c:
            c.run(
                "INSERT INTO tokens_worker (sha, worker, gerador, criado, expira) VALUES (?, ?, ?, ?, ?)",
                (sha(token), worker, gerador, agora, agora + validade_s),
            )
        return token

    def worker_do_token(self, token: str) -> str | None:
        """Nome do worker dono do token (válido e não revogado), ou None."""
        with self.tx() as c:
            r = c.one(
                "SELECT worker FROM tokens_worker WHERE sha = ? AND revogado_em IS NULL AND expira > ?",
                (sha(token), time.time()),
            )
        return r["worker"] if r else None

    def revogar_tokens_worker(self, worker: str | None = None, gerador: str | None = None) -> int:
        with self.tx() as c:
            if gerador:
                return c.run(
                    "UPDATE tokens_worker SET revogado_em = ? WHERE gerador = ? AND revogado_em IS NULL",
                    (time.time(), gerador),
                )
            if worker:
                return c.run(
                    "UPDATE tokens_worker SET revogado_em = ? WHERE worker = ? AND revogado_em IS NULL",
                    (time.time(), worker),
                )
            return c.run("UPDATE tokens_worker SET revogado_em = ? WHERE revogado_em IS NULL", (time.time(),))

    # -- pagamentos (ADR 0008) --------------------------------------------------------------------

    def criar_pagamento(self, cliente_id: str, valor: int, provedor: str, documento: str | None) -> str:
        pid, agora = novo_id("pag"), time.time()
        with self.tx() as c:
            c.run(
                "INSERT INTO pagamentos (id, cliente_id, valor, provedor, status, criado, atualizado, documento)"
                " VALUES (?, ?, ?, ?, 'criando', ?, ?, ?)",
                (pid, cliente_id, valor, provedor, agora, agora, documento),
            )
        return pid

    def pagamento_criado(
        self, pid: str, provedor_id: str, status: str, expira: float, copia_cola: str | None, qr_base64: str | None
    ) -> None:
        with self.tx() as c:
            c.run(
                "UPDATE pagamentos SET provedor_id = ?, status = ?, expira = ?, copia_cola = ?, qr_base64 = ?,"
                " atualizado = ? WHERE id = ?",
                (provedor_id, status, expira, copia_cola, qr_base64, time.time(), pid),
            )

    def pagamento(self, pid: str, cliente_id: str | None = None) -> dict | None:
        with self.tx() as c:
            if cliente_id:
                return c.one("SELECT * FROM pagamentos WHERE id = ? AND cliente_id = ?", (pid, cliente_id))
            return c.one("SELECT * FROM pagamentos WHERE id = ?", (pid,))

    def pagamentos(self, cliente_id: str | None = None, limite: int = 20) -> list[dict]:
        campos = (
            "p.id, p.cliente_id, p.valor, p.provedor, p.provedor_id, p.status, p.criado, p.atualizado, p.expira,"
            " p.creditado_em, p.documento, k.email"
        )
        with self.tx() as c:
            if cliente_id:
                return c.all(
                    f"SELECT {campos} FROM pagamentos p JOIN clientes k ON k.id = p.cliente_id"
                    " WHERE p.cliente_id = ? ORDER BY p.criado DESC LIMIT ?",
                    (cliente_id, limite),
                )
            return c.all(
                f"SELECT {campos} FROM pagamentos p JOIN clientes k ON k.id = p.cliente_id"
                " ORDER BY p.criado DESC LIMIT ?",
                (limite,),
            )

    def pagamentos_pendentes(self, cliente_id: str) -> int:
        with self.tx() as c:
            r = c.one(
                "SELECT COUNT(*) AS n FROM pagamentos WHERE cliente_id = ? AND status IN ('criando', 'pendente')"
                " AND (expira IS NULL OR expira > ?)",
                (cliente_id, time.time()),
            )
        return int(r["n"])

    def atualizar_pagamento(self, pid: str, status: str, detalhe: Any = None) -> None:
        """Situação nova vinda do provedor (pendente, recusado, cancelado, expirado...). O aprovado
        passa por `creditar_pagamento`."""
        with self.tx() as c:
            c.run(
                "UPDATE pagamentos SET status = ?, detalhe = ?, atualizado = ?, conferido_em = ?"
                " WHERE id = ? AND status <> 'aprovado'",
                (status, json.dumps(detalhe, ensure_ascii=False) if detalhe else None, time.time(), time.time(), pid),
            )

    def marcar_conferido(self, pid: str) -> None:
        with self.tx() as c:
            c.run("UPDATE pagamentos SET conferido_em = ? WHERE id = ?", (time.time(), pid))

    def creditar_pagamento(self, pid: str, valor_pago: int, provedor_id: str, detalhe: Any = None) -> bool:
        """Pagamento aprovado pelo provedor: o valor entra no saldo uma vez só (mesma transação). A conta
        do teste gratuito passa para o plano pago. False se já tinha entrado."""
        agora = time.time()
        trava = " FOR UPDATE" if self.pg else ""
        with self.tx() as c:
            p = c.one("SELECT * FROM pagamentos WHERE id = ?" + trava, (pid,))
            if p is None:
                raise BancoError("pagamento não encontrado")
            if p["provedor_id"] and str(p["provedor_id"]) != str(provedor_id):
                raise BancoError("o pagamento do provedor não é deste pedido de recarga")
            if int(valor_pago) != int(p["valor"]):
                raise BancoError(f"valor pago ({valor_pago}) diferente do pedido ({p['valor']})")
            if p["creditado_em"] is not None:
                return False
            self._travar_cliente(c, p["cliente_id"])
            ref = f"pagamento:{p['provedor']}:{provedor_id}"
            novo = self._movimento(
                c, p["cliente_id"], "recarga", int(p["valor"]), ref, descricao="Recarga por Pix", por=p["provedor"]
            )
            c.run(
                "UPDATE pagamentos SET status = 'aprovado', creditado_em = ?, atualizado = ?, conferido_em = ?,"
                " provedor_id = ?, detalhe = ? WHERE id = ?",
                (agora, agora, agora, provedor_id, json.dumps(detalhe, ensure_ascii=False) if detalhe else None, pid),
            )
            c.run("UPDATE clientes SET plano = 'pago' WHERE id = ? AND plano = 'teste'", (p["cliente_id"],))
            return novo

    def estornar_pagamento(self, pid: str, status: str, detalhe: Any = None) -> int:
        """Pagamento creditado e depois devolvido pelo provedor (estorno, contestação): desconta do saldo
        o que ainda houver (devolve quanto descontou); o resto fica para o painel resolver."""
        agora = time.time()
        with self.tx() as c:
            p = c.one("SELECT * FROM pagamentos WHERE id = ?", (pid,))
            if p is None or p["creditado_em"] is None or p["status"] == status:
                return 0
            self._travar_cliente(c, p["cliente_id"])
            desconto = min(int(p["valor"]), max(0, self._saldo(c, p["cliente_id"])))
            if desconto:
                self._movimento(
                    c,
                    p["cliente_id"],
                    "ajuste",
                    -desconto,
                    f"estorno-pagamento:{p['provedor']}:{p['provedor_id']}",
                    descricao=f"Recarga devolvida pelo banco ({status})",
                    por=p["provedor"],
                )
            c.run(
                "UPDATE pagamentos SET status = ?, atualizado = ?, detalhe = ? WHERE id = ?",
                (status, agora, json.dumps(detalhe, ensure_ascii=False) if detalhe else None, pid),
            )
        return desconto

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

    def fotos_teste_ip(self, ip: str, exceto: str | None = None, rede: str | None = None) -> int:
        """Fotos de teste gratuito pedidas a partir deste IP (ou da mesma rede: IPv6 conta pelo /64, já
        que quem tem um endereço IPv6 costuma ter o /64 inteiro), somando todos os clientes de teste."""
        with self.tx() as c:
            r = c.one(
                "SELECT COALESCE(SUM(l.n_fotos), 0) AS n FROM lotes l JOIN clientes k ON k.id = l.cliente_id"
                f" WHERE (l.ip = ? OR l.rede = ?) AND k.plano = 'teste' AND {ATIVO_L}"
                " AND l.id <> ?",
                (ip, rede or ip, exceto or ""),
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
        """Lotes mais recentes com o cliente e o andamento, para o painel."""
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

    def uso_por_ip(self, limite: int = 50, dias: int = 90) -> list[dict]:
        """Fotos e pedidos por IP nos últimos `dias` (só lotes que passaram do envio e não foram recusados)."""
        with self.tx() as c:
            return c.all(
                "SELECT l.ip, COUNT(*) AS pedidos, COALESCE(SUM(l.n_fotos), 0) AS fotos,"
                " COALESCE(SUM(CASE WHEN k.plano = 'teste' THEN l.n_fotos ELSE 0 END), 0) AS fotos_teste,"
                " COUNT(DISTINCT l.cliente_id) AS clientes, COUNT(DISTINCT l.dispositivo) AS dispositivos,"
                " MAX(l.criado) AS ultimo FROM lotes l JOIN clientes k ON k.id = l.cliente_id"
                f" WHERE l.ip IS NOT NULL AND l.criado > ? AND {ATIVO_L}"
                " GROUP BY l.ip ORDER BY ultimo DESC LIMIT ?",
                (time.time() - dias * 86400, limite),
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
            return c.one(
                "SELECT id, nome, plano, ativo, email, criado, excluido_em FROM clientes WHERE id = ?", (cliente_id,)
            )

    def cliente_por_token(self, token: str) -> dict | None:
        with self.tx() as c:
            return c.one("SELECT * FROM clientes WHERE token_sha = ? AND ativo = 1", (sha(token),))

    def clientes(self, busca: str | None = None, limite: int = 200) -> list[dict]:
        """Clientes com o saldo (para o painel), os mais recentes primeiro; `busca` filtra por e-mail,
        nome ou id (com milhares de contas, a lista não vem inteira)."""
        onde, args = "", ()
        if busca:
            termo = f"%{busca.strip().lower()[:100]}%"
            onde, args = (
                " WHERE LOWER(COALESCE(k.email, '')) LIKE ? OR LOWER(k.nome) LIKE ? OR k.id LIKE ?",
                (
                    termo,
                    termo,
                    termo,
                ),
            )
        with self.tx() as c:
            return c.all(
                "SELECT k.id, k.nome, k.email, k.plano, k.ativo, k.criado, k.ultimo_acesso, k.excluido_em,"
                " COALESCE((SELECT SUM(m.valor) FROM movimentos m WHERE m.cliente_id = k.id), 0) AS saldo"
                f" FROM clientes k{onde} ORDER BY k.criado DESC LIMIT ?",
                (*args, limite),
            )

    def contar_clientes(self) -> dict[str, int]:
        with self.tx() as c:
            r = c.one(
                "SELECT COUNT(*) AS total, SUM(CASE WHEN plano = 'teste' THEN 1 ELSE 0 END) AS teste,"
                " SUM(CASE WHEN plano = 'pago' THEN 1 ELSE 0 END) AS pago,"
                " SUM(CASE WHEN email IS NOT NULL THEN 1 ELSE 0 END) AS com_email,"
                " SUM(CASE WHEN criado > ? THEN 1 ELSE 0 END) AS novos_24h FROM clientes WHERE excluido_em IS NULL",
                (time.time() - 86400,),
            )
        return {k: int(r[k] or 0) for k in ("total", "teste", "pago", "com_email", "novos_24h")}

    # -- saldo pré-pago ---------------------------------------------------------------------

    def _travar_cliente(self, c, cliente_id: str) -> None:
        """Um movimento por vez no saldo do cliente (no SQLite, o BEGIN IMMEDIATE já serializa)."""
        if self.pg:
            c.one("SELECT id FROM clientes WHERE id = ? FOR UPDATE", (cliente_id,))

    def _saldo(self, c, cliente_id: str) -> int:
        r = c.one("SELECT COALESCE(SUM(valor), 0) AS s FROM movimentos WHERE cliente_id = ?", (cliente_id,))
        return int(r["s"])

    def _movimento(
        self,
        c,
        cliente_id: str,
        tipo: str,
        valor: int,
        referencia: str,
        lote_id: str | None = None,
        job_id: str | None = None,
        descricao: str | None = None,
        por: str | None = None,
    ) -> bool:
        """Grava um movimento; False se a referência já existe (nada é cobrado ou devolvido duas vezes)."""
        if tipo not in TIPOS_MOVIMENTO:
            raise BancoError(f"tipo de movimento desconhecido: {tipo}")
        n = c.run(
            "INSERT INTO movimentos (id, cliente_id, quando, tipo, valor, referencia, lote_id, job_id, descricao, por)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (referencia) DO NOTHING",
            (novo_id("mov"), cliente_id, time.time(), tipo, valor, referencia, lote_id, job_id, descricao, por),
        )
        return n > 0

    def saldo(self, cliente_id: str) -> int:
        """Saldo do cliente em centavos."""
        with self.tx() as c:
            return self._saldo(c, cliente_id)

    def movimentar(self, cliente_id: str, valor: int, descricao: str, por: str, referencia: str | None = None) -> int:
        """Recarga (valor > 0) ou ajuste (valor < 0) feito pelo dono do serviço; devolve o saldo novo.
        `referencia`: identificador único do pagamento (o mesmo pagamento nunca entra duas vezes)."""
        if not valor:
            raise BancoError("valor zero")
        tipo = "recarga" if valor > 0 else "ajuste"
        with self.tx() as c:
            if c.one("SELECT id FROM clientes WHERE id = ?", (cliente_id,)) is None:
                raise BancoError("cliente não encontrado")
            self._travar_cliente(c, cliente_id)
            saldo = self._saldo(c, cliente_id) + valor
            if saldo < 0:
                raise BancoError("o saldo ficaria negativo")
            ref = referencia or f"{tipo}:{novo_id('mov')}"
            if not self._movimento(c, cliente_id, tipo, valor, ref, descricao=descricao, por=por):
                raise BancoError(f"movimento já registrado: {ref}")
        return saldo

    def extrato(self, cliente_id: str, limite: int = 50) -> list[dict]:
        """Movimentos do saldo, do mais recente para o mais antigo."""
        with self.tx() as c:
            return c.all(
                "SELECT quando, tipo, valor, lote_id, descricao FROM movimentos WHERE cliente_id = ?"
                " ORDER BY quando DESC LIMIT ?",
                (cliente_id, limite),
            )

    def cobranca(self, lote_id: str) -> dict[str, int]:
        """Quanto o pedido custou e quanto já voltou para o saldo (vídeos que não saíram, recusa)."""
        with self.tx() as c:
            r = c.one(
                "SELECT COALESCE(SUM(CASE WHEN tipo = 'pedido' THEN -valor ELSE 0 END), 0) AS cobrado,"
                " COALESCE(SUM(CASE WHEN tipo = 'estorno' THEN valor ELSE 0 END), 0) AS devolvido"
                " FROM movimentos WHERE lote_id = ?",
                (lote_id,),
            )
        return {"cobrado": int(r["cobrado"]), "devolvido": int(r["devolvido"])}

    def _cobrar(self, c, lote: dict) -> None:
        """Cobra o pedido inteiro (fotos × variações × preço por vídeo) ou levanta SaldoInsuficiente."""
        pedido = json.loads(lote["pedido"])
        videos = int(lote["n_fotos"]) * int(pedido.get("variacoes", 1))
        custo = videos * int(lote["preco_video"])
        self._travar_cliente(c, lote["cliente_id"])
        saldo = self._saldo(c, lote["cliente_id"])
        if saldo < custo:
            raise SaldoInsuficiente(custo, saldo)
        resolucao = RESOLUCAO.get(pedido.get("qualidade"), pedido.get("qualidade"))
        self._movimento(
            c,
            lote["cliente_id"],
            "pedido",
            -custo,
            f"pedido:{lote['id']}",
            lote_id=lote["id"],
            descricao=f"{videos} vídeo(s) de {pedido.get('duracao')} s em {resolucao}",
        )

    def _estornar_lote(self, c, lote_id: str, descricao: str) -> None:
        """Pedido recusado depois de cobrado: volta o valor inteiro (ainda não há vídeos)."""
        cobr = c.one("SELECT cliente_id, valor FROM movimentos WHERE referencia = ?", (f"pedido:{lote_id}",))
        if cobr:
            ref = f"estorno-lote:{lote_id}"
            self._movimento(c, cobr["cliente_id"], "estorno", -int(cobr["valor"]), ref, lote_id, None, descricao)

    def _estornar_videos(self, c, lote_id: str) -> None:
        """Cada vídeo que terminou em erro ou foi cancelado volta para o saldo (uma vez só)."""
        cobr = c.one(
            "SELECT m.cliente_id, l.preco_video FROM movimentos m JOIN lotes l ON l.id = m.lote_id"
            " WHERE m.referencia = ?",
            (f"pedido:{lote_id}",),
        )
        if not cobr or not cobr["preco_video"]:
            return
        falhos = c.all(
            "SELECT id, arquivo FROM jobs WHERE lote_id = ? AND tipo = 'render' AND status IN ('erro', 'cancelado')",
            (lote_id,),
        )
        for j in falhos:
            self._movimento(
                c,
                cobr["cliente_id"],
                "estorno",
                int(cobr["preco_video"]),
                f"estorno:{j['id']}",
                lote_id,
                j["id"],
                f"{j['arquivo']}: o vídeo não saiu",
            )

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
        preco_video: int | None = None,
    ) -> str:
        """fotos: [{nome, tipo, bytes, chave}] (já validadas pela API).
        origem: {ip, dispositivo, agente, fuso} de quem pediu.
        autorizacao: {declarante, texto_versao, texto} quando a caixa de autorização foi marcada;
        gravada na mesma transação, com a origem e a lista de fotos (auditoria).
        preco_video: centavos por vídeo do pré-pago (cobrado em `confirmar_envio`); None não cobra."""
        lid = novo_id("lote")
        agora = time.time()
        o = origem or {}
        with self.tx() as c:
            cli = c.one("SELECT nome, plano FROM clientes WHERE id = ?", (cliente_id,))
            if cli is None:
                raise BancoError("cliente não encontrado")
            c.run(
                "INSERT INTO lotes (id, cliente_id, status, pedido, n_fotos, prioridade, criado, ip, dispositivo,"
                " agente, fuso, impressao, preco_video, rede, modelo, porta)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    preco_video,
                    o.get("rede") or o.get("ip"),
                    pedido.get("modelo") or MODELO_PADRAO,
                    o.get("porta"),
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
            self.evento(
                c,
                lid,
                "criado",
                {"fotos": len(fotos), "autorizacao": bool(autorizacao), **{k: o.get(k) for k in ORIGEM_EVENTO}},
            )
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

    def linhas_fila(self) -> list[dict]:
        """Retrato da fila (vídeos por lote em andamento) para `videos_a_frente`: a API guarda por
        alguns segundos e usa o mesmo retrato em todas as previsões desse intervalo."""
        with self.tx() as c:
            return c.all(
                "SELECT l.id, l.cliente_id, l.prioridade, l.criado,"
                " SUM(CASE WHEN j.status = 'pendente' THEN 1 ELSE 0 END) AS pendentes,"
                " SUM(CASE WHEN j.status = 'em_andamento' THEN 1 ELSE 0 END) AS andamento,"
                " SUM(CASE WHEN j.status <> 'pendente' THEN 1 ELSE 0 END) AS iniciados"
                " FROM jobs j JOIN lotes l ON l.id = j.lote_id"
                " WHERE j.tipo = 'render' AND l.status IN ('aprovado', 'processando')"
                " GROUP BY l.id, l.cliente_id, l.prioridade, l.criado"
            )

    def videos_a_frente(self, lote: dict, linhas: list[dict] | None = None) -> dict[str, int]:
        """Na ordem da fila (ver `reivindicar`), quantos vídeos saem antes do 1º vídeo pendente deste
        lote ("primeiro") e quantos, contando os do próprio lote, até o último dele ("ultimo").

        Entram os vídeos em andamento (ocupam as GPUs agora), os de prioridade melhor, os pedidos
        anteriores do mesmo cliente e, dos outros clientes da mesma prioridade, a vez intercalada:
        cada um anda até alcançar os vídeos iniciados deste cliente (no empate, quem pediu antes).
        `linhas`: retrato da fila já lido (`linhas_fila`); sem ele, lê agora."""
        if linhas is None:
            linhas = self.linhas_fila()
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

    def gerador_criado(
        self, gid: str, worker: str, gpu: str | None, usd_h: float | None, datacentro: str | None = None
    ) -> None:
        """O RunPod criou o pod: id, GPU, preço e datacenter, antes da instalação (que pode falhar)."""
        with self.tx() as c:
            c.run(
                "UPDATE geradores SET worker = ?, gpu = ?, usd_h = ?, datacentro = ?, pod_criado_em = ? WHERE id = ?",
                (worker, gpu, usd_h, datacentro, time.time(), gid),
            )

    def gerador_desligado(self, gid: str) -> None:
        """O pod foi encerrado no RunPod (fim da cobrança)."""
        with self.tx() as c:
            c.run(
                "UPDATE geradores SET desligado_em = ? WHERE id = ? AND desligado_em IS NULL",
                (time.time(), gid),
            )

    # -- interesse nas finalidades em breve ---------------------------------------------------

    def registrar_interesse(self, cliente_id: str, finalidade: str) -> bool:
        """True se é novo (o mesmo cliente clicando de novo não conta duas vezes)."""
        with self.tx() as c:
            return bool(
                c.run(
                    "INSERT INTO interesses (cliente_id, finalidade, quando) VALUES (?, ?, ?)"
                    " ON CONFLICT (cliente_id, finalidade) DO NOTHING",
                    (cliente_id, finalidade, time.time()),
                )
            )

    def interesses_do_cliente(self, cliente_id: str) -> list[str]:
        with self.tx() as c:
            return [
                r["finalidade"]
                for r in c.all(
                    "SELECT finalidade FROM interesses WHERE cliente_id = ? ORDER BY quando, finalidade", (cliente_id,)
                )
            ]

    def interesses(self) -> list[dict]:
        """Por finalidade: quantos clientes pediram (e de que planos) e o pedido mais recente."""
        with self.tx() as c:
            return c.all(
                "SELECT i.finalidade, COUNT(*) AS clientes,"
                " SUM(CASE WHEN k.plano = 'teste' THEN 1 ELSE 0 END) AS clientes_teste, MAX(i.quando) AS ultimo"
                " FROM interesses i JOIN clientes k ON k.id = i.cliente_id"
                " GROUP BY i.finalidade ORDER BY clientes DESC, ultimo DESC"
            )

    def interesses_detalhados(self, limite: int = 200) -> list[dict]:
        """Cada clique em "Tenho interesse": quem (conta e e-mail, para avisar quando a opção sair), plano e
        quando, do mais recente para o mais antigo."""
        with self.tx() as c:
            return c.all(
                "SELECT i.finalidade, i.quando, k.id AS cliente_id, k.nome, k.email, k.plano"
                " FROM interesses i JOIN clientes k ON k.id = i.cliente_id ORDER BY i.quando DESC LIMIT ?",
                (limite,),
            )

    def relatorio_geradores(self, desde: float, limite: int = 200) -> dict[str, Any]:
        """Relatório de cada pod para o painel: identificação, horários, vídeos e custo estimado.

        Tempo ligado: da criação do pod (ou da decisão de subir, nos registros antigos) até o
        desligamento; sem o desligamento registrado (orquestrador que caiu), até o fim da fila,
        marcado como estimado. Custo = preço por hora × tempo ligado (o RunPod cobra por tempo)."""
        agora = time.time()
        por_worker = (
            "SELECT {expr} FROM jobs j WHERE j.worker = g.worker AND g.worker IS NOT NULL AND j.tipo = '{tipo}'{extra}"
        )
        campos = {
            "videos_ok": por_worker.format(expr="COUNT(*)", tipo="render", extra=" AND j.status = 'ok'"),
            "videos_erro": por_worker.format(expr="COUNT(*)", tipo="render", extra=" AND j.status = 'erro'"),
            "videos_andamento": por_worker.format(
                expr="COUNT(*)", tipo="render", extra=" AND j.status = 'em_andamento'"
            ),
            "traducoes": por_worker.format(expr="COUNT(*)", tipo="compilar", extra=" AND j.status = 'ok'"),
            "lotes": por_worker.format(expr="COUNT(DISTINCT j.lote_id)", tipo="render", extra=""),
            "segundos_render": por_worker.format(
                expr="COALESCE(SUM(j.segundos), 0)", tipo="render", extra=" AND j.status = 'ok'"
            ),
        }
        sub = ", ".join(f"({q}) AS {nome}" for nome, q in campos.items())
        with self.tx() as c:
            linhas = c.all(
                f"SELECT g.*, {sub} FROM geradores g WHERE g.criado >= ? ORDER BY g.criado DESC LIMIT ?",
                (desde, limite),
            )
        geradores = []
        for g in linhas:
            inicio = float(g["pod_criado_em"] or g["criado"])
            ativo = g["estado"] in ("subindo", "pronto")
            fim = g["desligado_em"] or g["encerrado_em"]
            estimado = not ativo and g["desligado_em"] is None
            segundos = max(0.0, (agora if ativo or fim is None else float(fim)) - inicio)
            sem_pod = g["pod_criado_em"] is None and g["pronto_em"] is None  # o RunPod nem criou o pod
            custo = None if g["usd_h"] is None else round(float(g["usd_h"]) * segundos / 3600, 4)
            subida = None if g["primeiro_video_em"] is None else round(float(g["primeiro_video_em"]) - inicio, 1)
            geradores.append(
                {
                    "id": g["id"],
                    "pod": g["worker"],
                    "gpu": g["gpu"],
                    "datacentro": g["datacentro"],
                    "usd_h": None if g["usd_h"] is None else float(g["usd_h"]),
                    "estado": g["estado"],
                    "motivo": g["motivo"],
                    "inicio": inicio,
                    "pronto_em": g["pronto_em"],
                    "primeiro_video_em": g["primeiro_video_em"],
                    "fim_fila": g["encerrado_em"],
                    "desligado_em": g["desligado_em"],
                    "ligado_s": 0.0 if sem_pod else round(segundos, 1),
                    "ligado_estimado": estimado,
                    "subida_s": subida,
                    "custo_usd": 0.0 if sem_pod else custo,
                    "videos_ok": int(g["videos_ok"]),
                    "videos_erro": int(g["videos_erro"]),
                    "videos_andamento": int(g["videos_andamento"]),
                    "traducoes": int(g["traducoes"]),
                    "lotes": int(g["lotes"]),
                    "segundos_render": round(float(g["segundos_render"]), 1),
                }
            )
        custos = [x["custo_usd"] for x in geradores if x["custo_usd"] is not None]
        videos = sum(x["videos_ok"] for x in geradores)
        custo = round(sum(custos), 4)
        return {
            "desde": desde,
            "geradores": geradores,
            "totais": {
                "pods": sum(1 for x in geradores if x["pod"]),
                "falharam": sum(1 for x in geradores if x["estado"] == "falhou"),
                "ligados_agora": sum(1 for x in geradores if x["estado"] in ("subindo", "pronto")),
                "horas": round(sum(x["ligado_s"] for x in geradores) / 3600, 3),
                "custo_usd": custo,
                "sem_preco": len(geradores) - len(custos),
                "videos_ok": videos,
                "videos_erro": sum(x["videos_erro"] for x in geradores),
                "custo_por_video_usd": round(custo / videos, 4) if videos else None,
            },
        }

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
        """tamanhos: chave → bytes no armazenamento (None = ausente). Devolve os problemas.
        Pedido do pré-pago: cobra o pedido inteiro na mesma transação (SaldoInsuficiente: nada muda)."""
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
                if lote["preco_video"] is not None:
                    self._cobrar(c, lote)
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
            self._estornar_lote(c, lote_id, f"pedido recusado: {motivo}"[:200])
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

    def reivindicar(
        self, worker: str, lease_s: float = 900, tipos: list[str] | None = None, modelos: list[str] | None = None
    ) -> dict | None:
        """Próximo job (compilar antes de render; render só com o prompt pronto).

        `tipos`: só esses tipos de job (o pod que ainda baixa os modelos pega só "compilar").
        `modelos`: só pedidos desses modelos de geração (cada pod roda os seus; sem a lista, qualquer um)."""
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
            # no mesmo pedido, a ordem da lista que o cliente vê (arquivo). A contagem por cliente sai uma
            # vez só (CTE), e não uma vez por job candidato: com milhares de jobs na fila, a consulta
            # continua rápida para dezenas de pods perguntando ao mesmo tempo
            filtro_modelo, args_modelo = "", ()
            if modelos:
                filtro_modelo = f" AND COALESCE(l.modelo, ?) IN ({', '.join('?' * len(modelos))})"
                args_modelo = (MODELO_PADRAO, *modelos)
            job = c.one(
                f"WITH inic AS ({INICIADOS_POR_CLIENTE})"
                " SELECT j.* FROM jobs j JOIN lotes l ON l.id = j.lote_id"
                " LEFT JOIN inic ON inic.cliente_id = l.cliente_id"
                " WHERE j.status = 'pendente' AND l.status IN ('aprovado', 'processando')"
                " AND (j.tipo = 'compilar' OR l.prompt_en IS NOT NULL)"
                f" AND j.tipo IN ({', '.join('?' * len(tipos))}){filtro_modelo}"
                " ORDER BY CASE j.tipo WHEN 'compilar' THEN 0 ELSE 1 END, l.prioridade,"
                " COALESCE(inic.n, 0), l.criado, l.id, j.criado, j.arquivo, j.id LIMIT 1" + trava,
                (*tipos, *args_modelo),
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
            # job devolvido à fila com `seed`: a próxima tentativa sai de outra seed
            seed = campos.get("seed") if repetir and campos.get("seed") is not None else job["seed"]
            c.run(
                "UPDATE jobs SET status = ?, segundos = ?, aviso = ?, erro = ?, similaridade = ?, bytes_video = ?,"
                " deriva_cor = ?, salto_cor = ?, seed = ?, atualizado = ?, lease_ate = NULL WHERE id = ?",
                (
                    novo,
                    campos.get("segundos"),
                    campos.get("aviso"),
                    campos.get("erro"),
                    campos.get("similaridade"),
                    campos.get("bytes_video"),
                    campos.get("deriva_cor"),
                    campos.get("salto_cor"),
                    seed,
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
        self._estornar_videos(c, lote_id)
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

    def jobs_de(self, lotes: list[str]) -> dict[str, list[dict]]:
        """Os jobs de render de vários lotes numa consulta só (lista de pedidos do cliente)."""
        if not lotes:
            return {}
        with self.tx() as c:
            linhas = c.all(
                "SELECT id, lote_id, tipo, arquivo, status, aviso FROM jobs"
                f" WHERE lote_id IN ({', '.join('?' * len(lotes))}) AND tipo = 'render' ORDER BY arquivo",
                tuple(lotes),
            )
        por: dict[str, list[dict]] = {lid: [] for lid in lotes}
        for j in linhas:
            por[j["lote_id"]].append(j)
        return por

    def cobrancas(self, lotes: list[str]) -> dict[str, dict[str, int]]:
        """`cobranca` de vários lotes numa consulta só."""
        if not lotes:
            return {}
        with self.tx() as c:
            linhas = c.all(
                "SELECT lote_id, COALESCE(SUM(CASE WHEN tipo = 'pedido' THEN -valor ELSE 0 END), 0) AS cobrado,"
                " COALESCE(SUM(CASE WHEN tipo = 'estorno' THEN valor ELSE 0 END), 0) AS devolvido"
                f" FROM movimentos WHERE lote_id IN ({', '.join('?' * len(lotes))}) GROUP BY lote_id",
                tuple(lotes),
            )
        out = {lid: {"cobrado": 0, "devolvido": 0} for lid in lotes}
        for r in linhas:
            out[r["lote_id"]] = {"cobrado": int(r["cobrado"]), "devolvido": int(r["devolvido"])}
        return out

    def trabalho(self) -> list[dict]:
        """Para o orquestrador: renders pendentes e em andamento por lote, com o pedido
        (qualidade e duração decidem o tempo por vídeo) e a prioridade."""
        with self.tx() as c:
            return c.all(
                "SELECT l.id AS lote_id, l.pedido, l.prioridade, l.modelo, MIN(j.criado) AS mais_antigo,"
                " SUM(CASE WHEN j.status = 'pendente' THEN 1 ELSE 0 END) AS pendentes,"
                " SUM(CASE WHEN j.status = 'em_andamento' THEN 1 ELSE 0 END) AS em_andamento"
                " FROM jobs j JOIN lotes l ON l.id = j.lote_id"
                " WHERE l.status IN ('aprovado', 'processando') AND j.status IN ('pendente', 'em_andamento')"
                " GROUP BY l.id, l.pedido, l.prioridade, l.modelo ORDER BY l.prioridade, MIN(j.criado)"
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

    @staticmethod
    def _args(args) -> tuple:
        """O caractere NUL (\\x00) sai de todo texto: o Postgres recusa (a API responderia 500 a uma busca
        ou mensagem com ele), e nenhum dado legítimo tem esse caractere."""
        return tuple(a.replace("\x00", "") if isinstance(a, str) else a for a in args)

    def run(self, sql: str, args: tuple = ()) -> int:
        self.cur.execute(self._sql(sql), self._args(args))
        return self.cur.rowcount

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        self.cur.execute(self._sql(sql), self._args(args))
        r = self.cur.fetchone()
        return dict(r) if r is not None else None

    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        self.cur.execute(self._sql(sql), self._args(args))
        return [dict(r) for r in self.cur.fetchall()]
