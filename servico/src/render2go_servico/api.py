"""API do serviço (ADR 0003, fase 3). Os bytes nunca passam por aqui: só URLs assinadas.

Cliente (Authorization: Bearer <token do cliente>):
    GET  /api/receitas                 receitas, formatos e limites
    POST /api/lotes                    cria o lote; devolve uma URL de envio por foto
    POST /api/lotes/{id}/enviado       confere no armazenamento e manda para aprovação
    GET  /api/lotes  /api/lotes/{id}   andamento, previsão e links dos vídeos (abrir e baixar)
    POST /api/suporte                  mensagem para o suporte (e-mail), com limite contra spam; sem código
                                       de acesso, exige e-mail e tem limites menores (página /suporte)
    POST /api/cookies                  escolha do aviso de cookies (prova do consentimento)

Aprovação (Bearer <token de administração>), ADR 0004:
    GET  /api/admin/lotes              lotes aguardando aprovação, com miniaturas e aviso de pessoas
    POST /api/admin/lotes/{id}/aprovar | /recusar
    GET  /api/admin/historico          pedidos feitos, aprovados e recusados; uso por IP e por dispositivo
    GET  /api/admin/lotes/{id}/auditoria  registro completo de um lote (origem, autorização, fotos, eventos)
    GET  /api/admin/suporte            últimas mensagens de suporte
    POST /api/admin/clientes           cadastra um cliente (plano pago ou teste); o token aparece uma vez
    POST /api/admin/clientes/{id}/desativar   o código de acesso do cliente deixa de valer

Teste gratuito (docs/08): um pedido só, até 10 fotos, vídeos de 3 s em 480p; no máximo 10 fotos de
teste por dispositivo (navegador) e 50 por IP (uma empresa com várias pessoas na mesma rede).
Plano interno (o dono do serviço): sem limites de teste e com aprovação automática; o C1 continua.
As fotos de entrada são apagadas quando o lote fecha (pronto, com falhas ou recusado); os vídeos
ficam até a regra de retenção do bucket. A caixa de autorização gera um registro de auditoria
(quem declarou, texto e versão, IP, dispositivo, fotos e suas impressões SHA-256).

Workers (Bearer <token de worker>), sem porta de entrada nos pods (eles puxam):
    POST /api/worker/reivindicar       próximo job, com URLs assinadas e carimbo de aprovação
    POST /api/worker/jobs/{id}/prompt  prompt compilado (passa pelo C1 aqui também)
    POST /api/worker/jobs/{id}/concluir  "ok" de render só com o vídeo no armazenamento e a
                                       conferência do 1º quadro acima do limiar (docs/08)

Páginas: / (cliente), /suporte (suporte) e /aprovar (administração).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import math
import os
import queue
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from render2go_worker import conferencia, filtros, lote, presets

from render2go_servico import SERVICO_DIR, modelos, pessoas, suporte
from render2go_servico.armazenamento import Armazenamento, chave_entrada, chave_saida
from render2go_servico.db import Banco, BancoError

TIPOS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
MAX_FOTOS_LOTE = 1000
MAX_BYTES_FOTO = 50 * 1024**2
URL_ENVIO_S = 3600
URL_DOWNLOAD_S = 24 * 3600
# teste gratuito (docs/08): um pedido só, qualidade rápida (480p), vídeos de 3 s, uma variação,
# até 10 fotos; e no máximo 10 fotos de teste por IP, somando todos os clientes de teste
TESTE = {
    "qualidade": "rapida",
    "duracao": 3,
    "variacoes": 1,
    "fotos": 10,
    "fotos_dispositivo": 10,
    "fotos_ip": int(os.environ.get("RENDER2GO_TESTE_FOTOS_IP", 50)),
}
# texto da caixa de autorização: a página mostra este, e o registro guarda o texto e o SHA-256.
# Mudou o texto = versão nova (o registro antigo continua apontando para o texto que a pessoa viu).
AUTORIZACAO = {
    "versao": "autorizacao-v1",
    "texto": (
        "Declaro que tenho autorização por escrito de todas as pessoas que aparecem nestas fotos "
        "(ou de seus responsáveis legais) para criar e usar os vídeos, e que posso apresentá-la se for pedida."
    ),
}
# previsão mostrada ao cliente (sem detalhar as etapas internas)
PRAZO_ANALISE_S = int(os.environ.get("RENDER2GO_PRAZO_REVISAO_MIN", 60)) * 60  # tempo típico da revisão
PREPARO_S = 4 * 60  # medido em 08/10: gerador pronto em ~2 min 40 s, mais a tradução do pedido
VIDEO_PADRAO_S = 30.0  # sem histórico: ~25–30 s por vídeo de 3 s (medido em 08/10)
# os mesmos padrões do orquestrador (testes gratuitos esperam juntar vídeos antes de começar)
AGRUPAR_TESTE_S = float(os.environ.get("RENDER2GO_AGRUPAR_TESTE_MIN", 10)) * 60
AGRUPAR_TESTE_VIDEOS = int(os.environ.get("RENDER2GO_AGRUPAR_TESTE_VIDEOS", 20))
DISPOSITIVO = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
FUSO = re.compile(r"^[A-Za-z0-9_+\-/]{1,64}$")
LOTE_FECHADO = ("concluido", "concluido_com_falhas", "recusado")
NOME = re.compile(r"[^A-Za-z0-9_-]+")
WEB = SERVICO_DIR / "web"


class AnaliseFotos:
    """Análise das fotos em segundo plano (aviso de pessoas no /aprovar): um thread, fila por lote.

    Agendada no envio e de novo quando o /aprovar lista um lote com foto ainda sem análise
    (assim uma reinicialização da API no meio não deixa foto para trás)."""

    def __init__(self, banco: Banco, armazenamento: Any, analisar: Callable[[bytes], dict]):
        self.banco, self.armazenamento, self.analisar = banco, armazenamento, analisar
        self.fila: queue.Queue[str] = queue.Queue()
        self.agendados: set[str] = set()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None

    def agendar(self, lote_id: str) -> None:
        with self.lock:
            if lote_id in self.agendados:
                return
            self.agendados.add(lote_id)
            if self.thread is None:
                self.thread = threading.Thread(target=self._rodar, name="analise-fotos", daemon=True)
                self.thread.start()
        self.fila.put(lote_id)

    def esperar(self) -> None:
        self.fila.join()

    def _rodar(self) -> None:
        while True:
            lote_id = self.fila.get()
            try:
                self._lote(lote_id)
            except Exception as e:  # noqa: BLE001 (o thread não pode morrer; a próxima listagem reagenda)
                print(f"[analise] AVISO: lote {lote_id}: {e}", flush=True)
            finally:
                with self.lock:
                    self.agendados.discard(lote_id)
                self.fila.task_done()

    def _lote(self, lote_id: str) -> None:
        for f in self.banco.fotos(lote_id):
            if f["analise"] is not None or not f["enviada"]:
                continue
            impressao = None
            try:
                dados = self.armazenamento.ler(f["chave"], MAX_BYTES_FOTO)
                impressao = hashlib.sha256(dados).hexdigest()  # fica no banco depois que a foto é apagada
                a = self.analisar(dados)
            except Exception as e:  # noqa: BLE001 (foto ilegível ou armazenamento fora: o revisor vê o erro)
                a = {"versao": pessoas.VERSAO, "erro": str(e)[:200]}
            self.banco.salvar_analise(f["id"], a, impressao)


@dataclass
class Config:
    banco: Banco
    armazenamento: Any  # Armazenamento ou um equivalente nos testes
    admin_tokens: list[str]
    worker_tokens: list[str]
    segredo_aprovacao: str
    limite_por_minuto: int = 120
    analisar: Callable[[bytes], dict] | None = None  # None: sem aviso de pessoas
    email: suporte.ConfigEmail | None = None  # None: mensagens de suporte só no banco
    enviar_email: Callable = suporte.enviar
    _janelas: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _analises: AnaliseFotos | None = None
    _fundo: ThreadPoolExecutor | None = None
    _pendentes_fundo: list = field(default_factory=list)

    def em_segundo_plano(self, fn: Callable, *args) -> None:
        with self._lock:
            if self._fundo is None:
                self._fundo = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fundo")
            self._pendentes_fundo = [f for f in self._pendentes_fundo if not f.done()]
            self._pendentes_fundo.append(self._fundo.submit(fn, *args))

    def esperar_fundo(self) -> None:  # testes
        with self._lock:
            pendentes = list(self._pendentes_fundo)
        for f in pendentes:
            f.result()

    @property
    def analises(self) -> AnaliseFotos | None:
        if self.analisar is None:
            return None
        with self._lock:
            if self._analises is None:
                self._analises = AnaliseFotos(self.banco, self.armazenamento, self.analisar)
            return self._analises

    @classmethod
    def do_ambiente(cls) -> Config:
        def lista(nome: str) -> list[str]:
            return [t for t in os.environ.get(nome, "").split(",") if len(t) >= 32]

        segredo = os.environ.get("RENDER2GO_SEGREDO_APROVACAO", "")
        if len(segredo) < 32:
            raise SystemExit("RENDER2GO_SEGREDO_APROVACAO ausente ou curto (mínimo 32 caracteres)")
        armazenamento = Armazenamento.do_ambiente()  # RENDER2GO_R2_* (mesmas do lote_nuvem)
        if armazenamento is None:
            raise SystemExit("faltam RENDER2GO_R2_ENDPOINT, _BUCKET, _ACCESS_KEY_ID ou _SECRET_ACCESS_KEY")
        banco = Banco(os.environ["RENDER2GO_DB_URL"])
        banco.criar_esquema()
        return cls(
            banco=banco,
            armazenamento=armazenamento,
            admin_tokens=lista("RENDER2GO_ADMIN_TOKENS"),
            worker_tokens=lista("RENDER2GO_WORKER_TOKENS"),
            segredo_aprovacao=segredo,
            analisar=pessoas.analisador(),
            email=suporte.ConfigEmail.do_ambiente(),
        )

    def limitar(self, chave: str) -> None:
        """Limite simples de requisições por minuto, por token (uma instância da API)."""
        agora = time.monotonic()
        with self._lock:
            janela = [t for t in self._janelas.get(chave, []) if agora - t < 60]
            if len(janela) >= self.limite_por_minuto:
                raise HTTPException(429, "muitas requisições; tente em um minuto")
            janela.append(agora)
            self._janelas[chave] = janela


def carimbo(segredo: str, lote_id: str, aprovado_em: float) -> str:
    """Carimbo de aprovação (HMAC) que o worker confere antes de renderizar (ADR 0004)."""
    return hmac.new(segredo.encode(), f"{lote_id}:{aprovado_em:.6f}".encode(), hashlib.sha256).hexdigest()


def _token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "token ausente")
    return authorization.removeprefix("Bearer ").strip()


def _um_de(token: str, validos: list[str]) -> bool:
    return any(hmac.compare_digest(token, v) for v in validos)


def ip_de(request: Request) -> str:
    """IP de quem fez a requisição. A API só é alcançável pelo Caddy, que troca o
    X-Forwarded-For recebido de fora pelo IP real da conexão; vale o último da lista."""
    xff = request.headers.get("x-forwarded-for", "")
    ip = xff.split(",")[-1].strip() if xff else ""
    return ip or (request.client.host if request.client else "desconhecido")


def origem_de(request: Request) -> dict:
    """De onde veio o pedido: IP (do Caddy), dispositivo (identificador que a página guarda no
    navegador), User-Agent e fuso horário do navegador. Valores fora do formato viram None."""
    disp = request.headers.get("x-dispositivo", "").strip().lower()
    fuso = request.headers.get("x-fuso", "").strip()
    return {
        "ip": ip_de(request),
        "dispositivo": disp if DISPOSITIVO.match(disp) else None,
        "agente": request.headers.get("user-agent", "")[:300] or None,
        "fuso": fuso if FUSO.match(fuso) else None,
    }


def impressao_navegador(origem: dict, nav: NavegadorIn | None) -> str | None:
    """Resumo (SHA-256) dos sinais do navegador: acha o mesmo navegador quando o identificador muda
    (janela anônima, dados apagados). Aparelhos iguais podem ter a mesma impressão: é sinal, não prova."""
    if nav is None:
        return None
    partes = [
        origem.get("agente") or "",
        nav.idioma or "",
        ",".join(nav.idiomas),
        nav.tela or "",
        nav.plataforma or "",
        str(nav.nucleos),
        str(nav.memoria),
        str(nav.toque),
        origem.get("fuso") or "",
    ]
    return hashlib.sha256("|".join(partes).encode()).hexdigest()[:24]


def _apagar_fotos(c: Config, lote_id: str) -> None:
    """Fotos de entrada de um lote fechado: apagadas do armazenamento (minimiza o que guardamos)."""
    falhas = 0
    for f in c.banco.fotos(lote_id):
        try:
            c.armazenamento.apagar(f["chave"])
        except Exception as e:  # noqa: BLE001 (a regra de ciclo de vida do bucket apaga depois)
            falhas += 1
            print(f"[fotos] AVISO: {f['chave']}: {e}", flush=True)
    if not falhas:
        c.banco.marcar_fotos_apagadas(lote_id)


def _enviar_suporte(c: Config, sid: str, dados: dict) -> None:
    try:
        c.enviar_email(c.email, suporte.montar(c.email, dados))
        c.banco.marcar_suporte(sid, True)
    except Exception as e:  # noqa: BLE001 (a mensagem fica no banco; o /aprovar mostra o erro)
        c.banco.marcar_suporte(sid, False, str(e)[:300])
        print(f"[suporte] AVISO: e-mail {sid} não saiu: {e}", flush=True)


def _se_fechou(c: Config, lote_id: str) -> None:
    lt = c.banco.lote(lote_id)
    if lt and lt["status"] in LOTE_FECHADO and not lt["fotos_apagadas"]:
        c.em_segundo_plano(_apagar_fotos, c, lote_id)


def _limites_teste(c: Config, cli: dict, origem: dict, n_fotos: int, exceto: str | None = None) -> None:
    """Teste gratuito: um pedido só (todas as fotos de uma vez), até 10 fotos por dispositivo e 50
    por IP (várias pessoas de uma empresa dividem o IP; o teto por IP segura quem troca de navegador)."""
    if c.banco.pedidos_ativos(cli["id"], exceto) > 0:
        raise HTTPException(
            429, "o teste gratuito é um pedido só, com até 10 fotos; para mais vídeos, fale com a gente"
        )
    disp = origem.get("dispositivo")
    if disp and c.banco.fotos_teste_dispositivo(disp, exceto) + n_fotos > TESTE["fotos_dispositivo"]:
        raise HTTPException(429, f"este dispositivo já usou o teste gratuito ({TESTE['fotos_dispositivo']} fotos)")
    if c.banco.fotos_teste_ip(origem["ip"], exceto) + n_fotos > TESTE["fotos_ip"]:
        raise HTTPException(429, "esta rede já usou todos os testes gratuitos disponíveis; fale com a gente")


class FotoIn(BaseModel):
    nome: str = Field(min_length=1, max_length=200)
    tipo: str
    bytes: int = Field(gt=0)


class NavegadorIn(BaseModel):
    """Sinais do navegador enviados com o pedido (registro de auditoria e impressão do navegador)."""

    idioma: str | None = Field(None, max_length=35)
    idiomas: list[str] = Field(default_factory=list, max_length=10)
    tela: str | None = Field(None, max_length=40)
    plataforma: str | None = Field(None, max_length=60)
    nucleos: int | None = Field(None, ge=0, le=1024)
    memoria: float | None = Field(None, ge=0, le=4096)
    toque: int | None = Field(None, ge=0, le=64)
    marcado_em: str | None = Field(None, max_length=40)  # quando a caixa de autorização foi marcada


class CookiesIn(BaseModel):
    versao: str = Field(min_length=1, max_length=20)
    estatisticas: bool = False
    marketing: bool = False


class SuporteIn(BaseModel):
    assunto: str = Field("", max_length=120)
    mensagem: str = Field(min_length=10, max_length=2000)
    email: str | None = Field(None, max_length=200)


class LoteIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=600)
    receita: str = "produto-close"
    formato: str = "auto"
    qualidade: str = "rapida"
    duracao: int | None = None
    variacoes: int = Field(1, ge=1, le=lote.MAX_VARIACOES)
    consentimento: bool = False
    declarante: str | None = Field(None, max_length=120)  # opcional; a página não pede mais
    modelo: str = modelos.PADRAO
    navegador: NavegadorIn | None = None
    fotos: list[FotoIn]


class PromptIn(BaseModel):
    worker: str
    prompt_en: str = Field(min_length=3, max_length=2000)
    compilador: str


class ConcluirIn(BaseModel):
    worker: str
    status: str
    segundos: float | None = None
    aviso: str | None = None
    erro: str | None = Field(None, max_length=1000)
    repetir: bool = False
    similaridade: float | None = None  # 1º quadro × foto (render2go_worker.conferencia)
    bytes_video: int | None = None


class RecusaIn(BaseModel):
    motivo: str = Field(min_length=3, max_length=500)


class ReivindicarIn(BaseModel):
    worker: str = Field(min_length=3, max_length=100)


class ClienteIn(BaseModel):
    nome: str = Field(min_length=2, max_length=120)
    plano: str = "pago"
    cota_fotos_dia: int = Field(500, ge=1, le=100_000)


def criar_app(cfg: Config | None = None) -> FastAPI:
    app = FastAPI(title="Render2Go", docs_url=None, redoc_url=None)
    app.state.cfg = cfg

    def config() -> Config:
        if app.state.cfg is None:
            app.state.cfg = Config.do_ambiente()
        return app.state.cfg

    def cliente(authorization: str | None = Header(None), c: Config = Depends(config)) -> dict:
        tok = _token(authorization)
        cli = c.banco.cliente_por_token(tok)
        if cli is None:
            raise HTTPException(401, "token inválido")
        c.limitar(cli["id"])
        return cli

    def cliente_opcional(authorization: str | None = Header(None), c: Config = Depends(config)) -> dict | None:
        """Cliente logado, ou None sem código de acesso (página /suporte)."""
        if not authorization:
            return None
        return cliente(authorization, c)

    def admin(authorization: str | None = Header(None), c: Config = Depends(config)) -> str:
        tok = _token(authorization)
        if not _um_de(tok, c.admin_tokens):
            raise HTTPException(401, "token inválido")
        return "admin-" + hashlib.sha256(tok.encode()).hexdigest()[:8]

    def worker_auth(authorization: str | None = Header(None), c: Config = Depends(config)) -> None:
        if not _um_de(_token(authorization), c.worker_tokens):
            raise HTTPException(401, "token inválido")

    # -- páginas ------------------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def pagina_cliente():
        return FileResponse(WEB / "index.html")

    @app.get("/suporte", include_in_schema=False)
    def pagina_suporte():
        return FileResponse(WEB / "suporte.html", media_type="text/html")

    @app.get("/aprovar", include_in_schema=False)
    def pagina_aprovar():
        return FileResponse(WEB / "aprovar.html")

    @app.get("/app.js", include_in_schema=False)
    def js():
        return FileResponse(WEB / "app.js", media_type="text/javascript")

    @app.get("/estilo.css", include_in_schema=False)
    def css():
        return FileResponse(WEB / "estilo.css", media_type="text/css")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    @app.get("/saude")
    def saude():
        return {"ok": True}

    # -- cliente ------------------------------------------------------------------------

    @app.get("/api/receitas")
    def receitas(request: Request, cli: dict = Depends(cliente), c: Config = Depends(config)):
        r = lote.load_receitas()
        plano: dict[str, Any] = {"nome": cli["plano"]}
        if cli["plano"] == "teste":
            motivo = None
            try:
                _limites_teste(c, cli, origem_de(request), 1)
            except HTTPException as e:
                motivo = e.detail
            plano.update(
                qualidades=[TESTE["qualidade"]],
                duracoes=[TESTE["duracao"]],
                max_fotos=TESTE["fotos"],
                disponivel=motivo is None,
                motivo=motivo,
            )
        return {
            "plano": plano,
            "autorizacao": AUTORIZACAO,
            "modelos": modelos.disponiveis(),
            "modelo_padrao": modelos.PADRAO,
            "receitas": [
                {
                    "id": k,
                    "label": v["label"],
                    "nicho": v["nicho"],
                    "acao": v["acao"],
                    "exige_consentimento": bool(v.get("exige_consentimento")),
                }
                for k, v in r["receitas"].items()
            ],
            "formatos": ["auto", *r["formatos"]],
            "qualidades": list(lote.QUALIDADES),
            "limites": {
                "fotos": MAX_FOTOS_LOTE,
                "bytes_foto": MAX_BYTES_FOTO,
                "tipos": list(TIPOS),
            },
        }

    @app.post("/api/lotes")
    def criar_lote(body: LoteIn, request: Request, cli: dict = Depends(cliente), c: Config = Depends(config)):
        origem = origem_de(request)
        origem["impressao"] = impressao_navegador(origem, body.navegador)
        if body.modelo not in {m["id"] for m in modelos.disponiveis()}:
            raise HTTPException(422, "modelo de geração indisponível")
        d = filtros.avaliar(body.prompt)
        if not d.allowed:  # C1 já na entrada (ADR 0004)
            raise HTTPException(422, f"pedido recusado pelo filtro de conteúdo: {d.reason}")
        rec = lote.load_receitas()
        r = rec["receitas"].get(body.receita)
        if r is None:
            raise HTTPException(422, "receita desconhecida")
        if r.get("exige_consentimento") and not body.consentimento:
            raise HTTPException(422, "esta receita exige a autorização por escrito das pessoas das fotos")
        if body.formato not in ("auto", *rec["formatos"]) or body.qualidade not in lote.QUALIDADES:
            raise HTTPException(422, "formato ou qualidade inválidos")
        if body.duracao is not None and body.duracao not in lote.DURACOES:
            raise HTTPException(422, "duração de 3 a 7 s")
        try:
            presets.compose("i2v", body.prompt, r["presets"])
        except presets.PresetError as e:
            raise HTTPException(422, str(e)) from e
        if not 1 <= len(body.fotos) <= MAX_FOTOS_LOTE:
            raise HTTPException(422, f"de 1 a {MAX_FOTOS_LOTE} fotos por lote")
        if cli["plano"] == "teste":
            pedido_teste = (body.qualidade, body.duracao or TESTE["duracao"], body.variacoes)
            if pedido_teste != (TESTE["qualidade"], TESTE["duracao"], TESTE["variacoes"]):
                raise HTTPException(422, "teste gratuito: qualidade rápida (480p), vídeos de 3 s, 1 variação")
            if len(body.fotos) > TESTE["fotos"]:
                raise HTTPException(429, f"o teste gratuito vale para até {TESTE['fotos']} fotos, num pedido só")
            _limites_teste(c, cli, origem, len(body.fotos))
            body.duracao = TESTE["duracao"]
        if cli["plano"] != "interno" and c.banco.fotos_hoje(cli["id"]) + len(body.fotos) > cli["cota_fotos_dia"]:
            raise HTTPException(429, f"cota diária de {cli['cota_fotos_dia']} fotos atingida")
        fotos, usados = [], set()
        for f in body.fotos:
            if f.tipo not in TIPOS:
                raise HTTPException(422, f"{f.nome}: use JPG, PNG ou WebP")
            if f.bytes > MAX_BYTES_FOTO:
                raise HTTPException(422, f"{f.nome}: acima de {MAX_BYTES_FOTO // 1024**2} MB")
            base = NOME.sub("-", Path(f.nome).stem).strip("-")[:80] or "foto"
            nome, n = base, 2
            while nome in usados:
                nome, n = f"{base}-{n}", n + 1
            usados.add(nome)
            arq = f"{nome}.{TIPOS[f.tipo]}"
            fotos.append(
                {
                    "nome": nome,
                    "tipo": f.tipo,
                    "bytes": f.bytes,
                    "chave": chave_entrada(cli["id"], "{lote}", arq),
                    "original": f.nome,
                }
            )
        pedido = body.model_dump(exclude={"fotos", "declarante", "navegador"})
        autorizacao = None
        if body.consentimento:  # registro de auditoria: conta, de onde, qual texto, quais fotos, sinais
            sinais = body.navegador.model_dump() if body.navegador else {}
            sinais["accept_language"] = request.headers.get("accept-language", "")[:200] or None
            sinais["impressao"] = origem["impressao"]
            autorizacao = {
                "declarante": (body.declarante or "").strip(),
                "texto_versao": AUTORIZACAO["versao"],
                "texto": AUTORIZACAO["texto"],
                "sinais": sinais,
            }
        if cli["plano"] == "teste":  # envio anterior que não terminou não segura o teste
            c.banco.substituir_envios(cli["id"])
        lid = c.banco.criar_lote(cli["id"], pedido, fotos, origem=origem, autorizacao=autorizacao)
        envios = []
        for f in c.banco.fotos(lid):
            original = next(x["original"] for x in fotos if x["nome"] == f["nome"])
            envios.append(
                {
                    "original": original,
                    "url": c.armazenamento.url("PUT", f["chave"], URL_ENVIO_S, content_type=f["tipo"]),
                    "tipo": f["tipo"],
                }
            )
        return {"lote": lid, "envios": envios, "expira_s": URL_ENVIO_S}

    @app.post("/api/lotes/{lote_id}/enviado")
    def enviado(lote_id: str, cli: dict = Depends(cliente), c: Config = Depends(config)):
        lt = c.banco.lote(lote_id, cli["id"])
        if lt is None:
            raise HTTPException(404, "lote não encontrado")
        if cli["plano"] == "teste":  # de novo aqui: dois pedidos enviados ao mesmo tempo
            origem = {"ip": lt["ip"] or "desconhecido", "dispositivo": lt["dispositivo"]}
            _limites_teste(c, cli, origem, lt["n_fotos"], exceto=lote_id)
        fotos = c.banco.fotos(lote_id)
        with ThreadPoolExecutor(max_workers=16) as ex:
            tamanhos = dict(
                zip(
                    [f["chave"] for f in fotos],
                    ex.map(lambda f: c.armazenamento.tamanho(f["chave"]), fotos),
                    strict=False,
                )
            )
        try:
            problemas = c.banco.confirmar_envio(lote_id, tamanhos)
        except BancoError as e:
            raise HTTPException(409, str(e)) from e
        if problemas:
            raise HTTPException(422, {"problemas": problemas})
        if c.analises is not None:  # aviso de pessoas para o revisor, em segundo plano
            c.analises.agendar(lote_id)
        if cli["plano"] == "interno":  # o dono do serviço revisa o próprio pedido ao fazê-lo (ADR 0004)
            r = _aprovar(c, lote_id, "auto:interno")
            return {"lote": lote_id, "status": "aprovado", "videos": r["videos"]}
        return {"lote": lote_id, "status": "aguardando_aprovacao"}

    def _resumo(lt: dict, c: Config, cliente_id: str) -> dict:
        jobs = [j for j in c.banco.jobs(lt["id"]) if j["tipo"] == "render"]
        videos = []
        for j in jobs:
            item = {"arquivo": j["arquivo"], "status": j["status"], "aviso": j["aviso"]}
            if j["status"] == "ok":
                chave = chave_saida(cliente_id, lt["id"], j["arquivo"])
                item["url"] = c.armazenamento.url("GET", chave, URL_DOWNLOAD_S)  # abre e toca no navegador
                item["baixar"] = c.armazenamento.url(  # o mesmo vídeo, salvo como arquivo
                    "GET",
                    chave,
                    URL_DOWNLOAD_S,
                    extra={"response-content-disposition": f'attachment; filename="{j["arquivo"]}"'},
                )
            videos.append(item)
        pedido = json.loads(lt["pedido"])
        return {
            "lote": lt["id"],
            "status": lt["status"],
            "criado": lt["criado"],
            "fotos": lt["n_fotos"],
            "motivo": lt["motivo"],
            "pedido": pedido,
            "modelo": modelos.nome(pedido.get("modelo")),
            "prontos": sum(1 for v in videos if v["status"] == "ok"),
            "total": len(videos),
            "videos": videos,
            "previsao": _previsao(c, lt, jobs),
        }

    def _previsao(c: Config, lt: dict, jobs: list[dict]) -> dict | None:
        """Quanto falta para começar e para terminar, em segundos (o cliente vê só o tempo)."""
        if lt["status"] == "aguardando_aprovacao":
            return {"fase": "analise", "inicio_max_s": PRAZO_ANALISE_S}
        if lt["status"] not in ("aprovado", "processando"):
            return None
        restantes = sum(j["status"] in ("pendente", "em_andamento") for j in jobs)
        por_video = c.banco.segundos_por_video() or VIDEO_PADRAO_S
        ativos = c.banco.geradores_ativos()
        vazao = por_video / max(1, ativos)
        if any(j["status"] in ("em_andamento", "ok") for j in jobs):
            return {"fase": "gerando", "fim_s": math.ceil(restantes * vazao)}
        espera = 0.0 if ativos else PREPARO_S
        if lt["prompt_en"] is None and ativos:
            espera += 60  # a tradução do pedido roda antes dos vídeos
        if not ativos and lt["prioridade"] > 0:  # teste gratuito: espera juntar com outros testes
            trabalho = c.banco.trabalho()
            if trabalho and not any(t["prioridade"] == 0 for t in trabalho):
                videos = sum(int(t["pendentes"] or 0) + int(t["em_andamento"] or 0) for t in trabalho)
                idade = time.time() - min(float(t["mais_antigo"]) for t in trabalho)
                if videos < AGRUPAR_TESTE_VIDEOS:
                    espera += max(0.0, AGRUPAR_TESTE_S - idade)
        inicio = espera + c.banco.videos_a_frente(lt) * vazao
        return {"fase": "fila", "inicio_s": math.ceil(inicio), "fim_s": math.ceil(inicio + restantes * vazao)}

    @app.post("/api/suporte")
    def mensagem_suporte(
        body: SuporteIn,
        request: Request,
        cli: dict | None = Depends(cliente_opcional),
        c: Config = Depends(config),
    ):
        origem = origem_de(request)
        email = (body.email or "").strip() or None
        if email and not suporte.EMAIL.match(email):
            raise HTTPException(422, "e-mail para resposta inválido")
        if cli is None and not email:
            raise HTTPException(422, "informe o seu e-mail para podermos responder")
        cliente_id = cli["id"] if cli else ""
        valores = {"ip": origem["ip"], "dispositivo": origem["dispositivo"], "cliente_id": cliente_id}
        for campo, janela, maximo in suporte.LIMITES if cli else suporte.LIMITES_ANONIMO:
            if c.banco.contar_suporte(campo, valores[campo], janela) >= maximo:
                espera = "uma hora" if janela <= 3600 else "amanhã"
                raise HTTPException(429, f"muitas mensagens seguidas; tente de novo em {espera}")
        mensagem = body.mensagem.strip()
        sid = c.banco.registrar_suporte(cliente_id, origem, email, body.assunto.strip(), mensagem)
        if c.email is not None:
            dados = {
                "id": sid,
                "cliente_id": cliente_id,
                "cliente_nome": cli["nome"] if cli else None,
                "plano": cli["plano"] if cli else None,
                "email": email,
                "assunto": body.assunto.strip(),
                "mensagem": mensagem,
                "ip": origem["ip"],
                "dispositivo": origem["dispositivo"],
                "quando": time.time(),
            }
            c.em_segundo_plano(_enviar_suporte, c, sid, dados)
        return {"ok": True, "id": sid}

    @app.post("/api/cookies")
    def escolha_cookies(body: CookiesIn, request: Request, c: Config = Depends(config)):
        """Guarda a escolha do aviso de cookies (sem login). Os necessários não têm escolha."""
        origem = origem_de(request)
        if c.banco.contar_cookies(origem["ip"], 3600) >= 60:  # contra quem tenta encher o banco
            raise HTTPException(429, "muitas escolhas seguidas")
        escolha = {"necessarios": True, "estatisticas": body.estatisticas, "marketing": body.marketing}
        return {"ok": True, "id": c.banco.registrar_cookies(origem, body.versao, escolha)}

    @app.get("/api/lotes")
    def meus_lotes(cli: dict = Depends(cliente), c: Config = Depends(config)):
        return [_resumo(lt, c, cli["id"]) for lt in c.banco.lotes(cliente_id=cli["id"], limite=20)]

    @app.get("/api/lotes/{lote_id}")
    def ver_lote(lote_id: str, cli: dict = Depends(cliente), c: Config = Depends(config)):
        lt = c.banco.lote(lote_id, cli["id"])
        if lt is None:
            raise HTTPException(404, "lote não encontrado")
        return _resumo(lt, c, cli["id"])

    # -- aprovação (ADR 0004) -------------------------------------------------------------

    @app.get("/api/admin/lotes")
    def pendentes(status: str = "aguardando_aprovacao", _: str = Depends(admin), c: Config = Depends(config)):
        out = []
        for lt in c.banco.lotes(status=status, limite=50):
            fotos = c.banco.fotos(lt["id"])
            if c.analises is not None and any(f["analise"] is None and f["enviada"] for f in fotos):
                c.analises.agendar(lt["id"])  # a API pode ter reiniciado no meio da análise
            cli = c.banco.cliente(lt["cliente_id"]) or {}
            out.append(
                {
                    "lote": lt["id"],
                    "cliente": lt["cliente_id"],
                    "cliente_nome": cli.get("nome"),
                    "plano": cli.get("plano"),
                    "ip": lt["ip"],
                    "dispositivo": lt["dispositivo"],
                    "autorizacao": c.banco.autorizacao(lt["id"]),
                    "modelo": modelos.nome(json.loads(lt["pedido"]).get("modelo")),
                    "criado": lt["criado"],
                    "pedido": json.loads(lt["pedido"]),
                    "filtro_c1": filtros.avaliar(json.loads(lt["pedido"])["prompt"]).reason,
                    "analise_ligada": c.analises is not None,
                    "fotos": [
                        {
                            "nome": f["nome"],
                            "url": c.armazenamento.url("GET", f["chave"], 3600),
                            "analise": json.loads(f["analise"]) if f["analise"] else None,
                        }
                        for f in fotos
                    ],
                }
            )
        return out

    @app.post("/api/admin/lotes/{lote_id}/aprovar")
    def aprovar(lote_id: str, quem: str = Depends(admin), c: Config = Depends(config)):
        return _aprovar(c, lote_id, quem)

    def _aprovar(c: Config, lote_id: str, quem: str) -> dict:
        lt = c.banco.lote(lote_id)
        if lt is None:
            raise HTTPException(404, "lote não encontrado")
        pedido = json.loads(lt["pedido"])
        tarefas = [
            {
                "foto_id": f["id"],
                "variacao": k,
                "seed": lote.seed_de(f["nome"], k),
                "arquivo": f"{f['nome']}-v{k}.mp4",
            }
            for f in c.banco.fotos(lote_id)
            for k in range(1, pedido["variacoes"] + 1)
        ]
        try:
            c.banco.aprovar(lote_id, quem, tarefas)
        except BancoError as e:
            raise HTTPException(409, str(e)) from e
        return {"lote": lote_id, "status": "aprovado", "videos": len(tarefas)}

    @app.post("/api/admin/clientes")
    def novo_cliente(body: ClienteIn, quem: str = Depends(admin), c: Config = Depends(config)):
        try:
            cota = 100_000 if body.plano == "interno" else body.cota_fotos_dia
            cid, token = c.banco.criar_cliente(body.nome, cota, body.plano)
        except BancoError as e:
            raise HTTPException(422, str(e)) from e
        return {"cliente": cid, "token": token, "aviso": "guarde o token: ele não aparece de novo"}

    @app.post("/api/admin/lotes/{lote_id}/recusar")
    def recusar(lote_id: str, body: RecusaIn, quem: str = Depends(admin), c: Config = Depends(config)):
        try:
            c.banco.recusar(lote_id, quem, body.motivo)
        except BancoError as e:
            raise HTTPException(409, str(e)) from e
        _se_fechou(c, lote_id)
        return {"lote": lote_id, "status": "recusado"}

    @app.get("/api/admin/historico")
    def historico(grupo: str = "todos", _: str = Depends(admin), c: Config = Depends(config)):
        try:
            lotes = c.banco.historico(grupo, limite=100)
        except BancoError as e:
            raise HTTPException(422, str(e)) from e
        return {
            "lotes": [
                {
                    "lote": lt["id"],
                    "cliente": lt["cliente_id"],
                    "cliente_nome": lt["cliente_nome"],
                    "plano": lt["plano"],
                    "cliente_ativo": bool(lt["cliente_ativo"]),
                    "status": lt["status"],
                    "prompt": json.loads(lt["pedido"]).get("prompt", ""),
                    "fotos": lt["n_fotos"],
                    "ip": lt["ip"],
                    "criado": lt["criado"],
                    "aprovado_em": lt["aprovado_em"],
                    "aprovado_por": lt["aprovado_por"],
                    "concluido_em": lt["concluido_em"],
                    "motivo": lt["motivo"],
                    "fotos_apagadas": lt["fotos_apagadas"],
                    "dispositivo": lt["dispositivo"],
                    "fuso": lt["fuso"],
                    "declarante": lt["declarante"],
                    "modelo": modelos.nome(json.loads(lt["pedido"]).get("modelo")),
                    "autorizado_em": lt["autorizado_em"],
                    "prontos": lt["prontos"],
                    "total": lt["total"],
                }
                for lt in lotes
            ],
            "ips": c.banco.uso_por_ip(),
            "dispositivos": c.banco.uso_por_dispositivo(),
        }

    @app.get("/api/admin/suporte")
    def ver_suporte(_: str = Depends(admin), c: Config = Depends(config)):
        return {"email_configurado": c.email is not None, "mensagens": c.banco.suporte_recente()}

    @app.get("/api/admin/lotes/{lote_id}/auditoria")
    def auditoria(lote_id: str, _: str = Depends(admin), c: Config = Depends(config)):
        a = c.banco.auditoria(lote_id)
        if a is None:
            raise HTTPException(404, "lote não encontrado")

        def js(v):
            return json.loads(v) if isinstance(v, str) and v[:1] in "[{" else v

        a["lote"]["pedido"] = js(a["lote"]["pedido"])
        if a["autorizacao"]:
            a["autorizacao"]["fotos"] = js(a["autorizacao"]["fotos"])
            a["autorizacao"]["sinais"] = js(a["autorizacao"].get("sinais"))
        for f in a["fotos"]:
            f["analise"] = js(f["analise"])
        for e in a["eventos"]:
            e["detalhe"] = js(e["detalhe"])
        return a

    @app.post("/api/admin/clientes/{cliente_id}/desativar")
    def desativar(cliente_id: str, quem: str = Depends(admin), c: Config = Depends(config)):
        if not c.banco.desativar_cliente(cliente_id, quem):
            raise HTTPException(404, "cliente não encontrado ou já desativado")
        return {"cliente": cliente_id, "ativo": False}

    # -- workers --------------------------------------------------------------------------

    @app.post("/api/worker/reivindicar", dependencies=[Depends(worker_auth)])
    def reivindicar(body: ReivindicarIn, c: Config = Depends(config)):
        job = c.banco.reivindicar(body.worker)
        if job is None:
            return {"job": None, "fila": c.banco.fila()}
        lt = c.banco.lote(job["lote_id"])
        pedido = json.loads(lt["pedido"])
        out: dict[str, Any] = {
            "job_id": job["id"],
            "tipo": job["tipo"],
            "lote": lt["id"],
            "pedido": pedido,
            "aprovacao": carimbo(c.segredo_aprovacao, lt["id"], lt["aprovado_em"]),
            "aprovado_em": lt["aprovado_em"],
        }
        if job["tipo"] == "render":
            foto = next(f for f in c.banco.fotos(lt["id"]) if f["id"] == job["foto_id"])
            stem = Path(job["arquivo"]).stem
            out |= {
                "prompt_en": lt["prompt_en"],
                "arquivo": job["arquivo"],
                "seed": job["seed"],
                "foto_url": c.armazenamento.url("GET", foto["chave"], 3600),
                "foto_ext": TIPOS[foto["tipo"]],
                "video_url": c.armazenamento.url(
                    "PUT",
                    chave_saida(lt["cliente_id"], lt["id"], job["arquivo"]),
                    3600,
                    content_type="video/mp4",
                ),
                "folha_url": c.armazenamento.url(
                    "PUT",
                    chave_saida(lt["cliente_id"], lt["id"], f"conferencia/{stem}.jpg"),
                    3600,
                    content_type="image/jpeg",
                ),
            }
        return {"job": out}

    def _do_worker(job_id: str, worker: str, c: Config) -> dict:
        with c.banco.tx() as cur:
            job = cur.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if job is None or job["worker"] != worker or job["status"] != "em_andamento":
            raise HTTPException(409, "job não está com este worker")
        return job

    @app.post("/api/worker/jobs/{job_id}/prompt", dependencies=[Depends(worker_auth)])
    def prompt(job_id: str, body: PromptIn, c: Config = Depends(config)):
        job = _do_worker(job_id, body.worker, c)
        d = filtros.avaliar(body.prompt_en)
        if not d.allowed:  # C1 também no prompt compilado
            c.banco.concluir(job_id, body.worker, "erro", erro=d.reason)
            with contextlib.suppress(BancoError):  # já recusado ou já em andamento
                c.banco.recusar(job["lote_id"], "filtro-c1", d.reason)
            _se_fechou(c, job["lote_id"])
            raise HTTPException(422, d.reason)
        c.banco.definir_prompt(job["lote_id"], body.prompt_en, body.compilador)
        return {"ok": True}

    @app.post("/api/worker/jobs/{job_id}/concluir", dependencies=[Depends(worker_auth)])
    def concluir(job_id: str, body: ConcluirIn, c: Config = Depends(config)):
        job = _do_worker(job_id, body.worker, c)
        status, erro, repetir = body.status, body.erro, body.repetir
        if job["tipo"] == "render" and status == "ok":
            # fail-closed: o vídeo precisa estar no armazenamento, com o tamanho informado, e a
            # conferência do 1º quadro precisa dizer que ele é dessa foto
            lt = c.banco.lote(job["lote_id"])
            chave = chave_saida(lt["cliente_id"], lt["id"], job["arquivo"])
            tamanho = c.armazenamento.tamanho(chave)
            problema = None
            if body.similaridade is None or body.similaridade < conferencia.LIMIAR:
                problema = f"conferência do 1º quadro reprovada ({body.similaridade})"
            elif not body.bytes_video or tamanho != body.bytes_video:
                problema = f"vídeo ausente ou incompleto ({tamanho} de {body.bytes_video} bytes)"
            if problema:
                c.armazenamento.apagar(chave)  # nada que não passou chega ao cliente
                status, erro, repetir = "erro", problema, True
        try:
            r = c.banco.concluir(
                job_id,
                body.worker,
                status,
                segundos=body.segundos,
                aviso=body.aviso,
                erro=erro,
                repetir=repetir,
                similaridade=body.similaridade,
                bytes_video=body.bytes_video,
            )
        except BancoError as e:
            raise HTTPException(409, str(e)) from e
        _se_fechou(c, job["lote_id"])
        return {"status": r["status"]}

    def origem_armazenamento() -> str:
        """Só o armazenamento (R2) além da própria API: fotos sobem e vídeos descem de lá."""
        try:
            arm = config().armazenamento  # carrega a configuração na 1ª requisição, se preciso
        except (Exception, SystemExit):  # noqa: BLE001 (sem configuração: CSP só com 'self')
            return ""
        u = urlsplit(getattr(arm, "endpoint", None) or getattr(arm, "base", ""))
        return f"{u.scheme}://{u.netloc}" if u.netloc else ""

    @app.middleware("http")
    async def cabecalhos(request: Request, call_next):
        resp = await call_next(request)
        arm = origem_armazenamento()
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Content-Security-Policy"] = (
            f"default-src 'self'; img-src 'self' {arm}; media-src {arm}; connect-src 'self' {arm}; "
            "style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return resp

    return app
