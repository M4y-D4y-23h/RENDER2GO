"""API do serviço (ADR 0003, fase 3). Os bytes nunca passam por aqui: só URLs assinadas.

Cliente (Authorization: Bearer <token do cliente>):
    GET  /api/receitas                 receitas, formatos e limites
    POST /api/lotes                    cria o lote; devolve uma URL de envio por foto
    POST /api/lotes/{id}/enviado       confere no armazenamento e manda para aprovação
    GET  /api/lotes  /api/lotes/{id}   andamento e links dos vídeos

Aprovação (Bearer <token de administração>), ADR 0004:
    GET  /api/admin/lotes              lotes aguardando aprovação, com miniaturas
    POST /api/admin/lotes/{id}/aprovar | /recusar
    POST /api/admin/clientes           cadastra um cliente (plano pago ou teste); o token aparece uma vez

Workers (Bearer <token de worker>), sem porta de entrada nos pods (eles puxam):
    POST /api/worker/reivindicar       próximo job, com URLs assinadas e carimbo de aprovação
    POST /api/worker/jobs/{id}/prompt  prompt compilado (passa pelo C1 aqui também)
    POST /api/worker/jobs/{id}/concluir  "ok" de render só com o vídeo no armazenamento e a
                                       conferência do 1º quadro acima do limiar (docs/08)

Páginas: / (cliente) e /aprovar (administração).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
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
from gpu2go_worker import conferencia, filtros, lote, presets
from pydantic import BaseModel, Field

from gpu2go_servico import SERVICO_DIR, pessoas
from gpu2go_servico.armazenamento import Armazenamento, chave_entrada, chave_saida
from gpu2go_servico.db import Banco, BancoError

TIPOS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
MAX_FOTOS_LOTE = 1000
MAX_BYTES_FOTO = 50 * 1024**2
URL_ENVIO_S = 3600
URL_DOWNLOAD_S = 24 * 3600
# teste gratuito (docs/08): qualidade rápida, vídeos de 3 s, uma variação, até 10 fotos no total
TESTE = {"qualidade": "rapida", "duracao": 3, "variacoes": 1, "fotos": 10}
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
            try:
                a = self.analisar(self.armazenamento.ler(f["chave"], MAX_BYTES_FOTO))
            except Exception as e:  # noqa: BLE001 (foto ilegível ou armazenamento fora: o revisor vê o erro)
                a = {"versao": pessoas.VERSAO, "erro": str(e)[:200]}
            self.banco.salvar_analise(f["id"], a)


@dataclass
class Config:
    banco: Banco
    armazenamento: Any  # Armazenamento ou um equivalente nos testes
    admin_tokens: list[str]
    worker_tokens: list[str]
    segredo_aprovacao: str
    limite_por_minuto: int = 120
    analisar: Callable[[bytes], dict] | None = None  # None: sem aviso de pessoas
    _janelas: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _analises: AnaliseFotos | None = None

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

        segredo = os.environ.get("GPU2GO_SEGREDO_APROVACAO", "")
        if len(segredo) < 32:
            raise SystemExit("GPU2GO_SEGREDO_APROVACAO ausente ou curto (mínimo 32 caracteres)")
        armazenamento = Armazenamento.do_ambiente()  # GPU2GO_R2_* (mesmas do lote_nuvem)
        if armazenamento is None:
            raise SystemExit("faltam GPU2GO_R2_ENDPOINT, _BUCKET, _ACCESS_KEY_ID ou _SECRET_ACCESS_KEY")
        banco = Banco(os.environ["GPU2GO_DB_URL"])
        banco.criar_esquema()
        return cls(
            banco=banco,
            armazenamento=armazenamento,
            admin_tokens=lista("GPU2GO_ADMIN_TOKENS"),
            worker_tokens=lista("GPU2GO_WORKER_TOKENS"),
            segredo_aprovacao=segredo,
            analisar=pessoas.analisador(),
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


class FotoIn(BaseModel):
    nome: str = Field(min_length=1, max_length=200)
    tipo: str
    bytes: int = Field(gt=0)


class LoteIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=600)
    receita: str = "produto-close"
    formato: str = "auto"
    qualidade: str = "rapida"
    duracao: int | None = None
    variacoes: int = Field(1, ge=1, le=lote.MAX_VARIACOES)
    consentimento: bool = False
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
    similaridade: float | None = None  # 1º quadro × foto (gpu2go_worker.conferencia)
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
    app = FastAPI(title="GPU2GO", docs_url=None, redoc_url=None)
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
    def receitas(_: dict = Depends(cliente)):
        r = lote.load_receitas()
        return {
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
    def criar_lote(body: LoteIn, cli: dict = Depends(cliente), c: Config = Depends(config)):
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
                raise HTTPException(422, "teste gratuito: qualidade rápida, vídeos de 3 s, 1 variação")
            if c.banco.fotos_total(cli["id"]) + len(body.fotos) > TESTE["fotos"]:
                raise HTTPException(429, f"o teste gratuito vale para até {TESTE['fotos']} fotos")
            body.duracao = TESTE["duracao"]
        if c.banco.fotos_hoje(cli["id"]) + len(body.fotos) > cli["cota_fotos_dia"]:
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
        pedido = body.model_dump(exclude={"fotos"})
        lid = c.banco.criar_lote(cli["id"], pedido, fotos)
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
        if c.banco.lote(lote_id, cli["id"]) is None:
            raise HTTPException(404, "lote não encontrado")
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
        return {"lote": lote_id, "status": "aguardando_aprovacao"}

    def _resumo(lt: dict, c: Config, cliente_id: str) -> dict:
        jobs = [j for j in c.banco.jobs(lt["id"]) if j["tipo"] == "render"]
        videos = []
        for j in jobs:
            item = {"arquivo": j["arquivo"], "status": j["status"], "aviso": j["aviso"]}
            if j["status"] == "ok":
                item["url"] = c.armazenamento.url(
                    "GET", chave_saida(cliente_id, lt["id"], j["arquivo"]), URL_DOWNLOAD_S
                )
            videos.append(item)
        return {
            "lote": lt["id"],
            "status": lt["status"],
            "criado": lt["criado"],
            "fotos": lt["n_fotos"],
            "motivo": lt["motivo"],
            "pedido": json.loads(lt["pedido"]),
            "prontos": sum(1 for v in videos if v["status"] == "ok"),
            "total": len(videos),
            "videos": videos,
        }

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
            out.append(
                {
                    "lote": lt["id"],
                    "cliente": lt["cliente_id"],
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
            cid, token = c.banco.criar_cliente(body.nome, body.cota_fotos_dia, body.plano)
        except BancoError as e:
            raise HTTPException(422, str(e)) from e
        return {"cliente": cid, "token": token, "aviso": "guarde o token: ele não aparece de novo"}

    @app.post("/api/admin/lotes/{lote_id}/recusar")
    def recusar(lote_id: str, body: RecusaIn, quem: str = Depends(admin), c: Config = Depends(config)):
        try:
            c.banco.recusar(lote_id, quem, body.motivo)
        except BancoError as e:
            raise HTTPException(409, str(e)) from e
        return {"lote": lote_id, "status": "recusado"}

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
