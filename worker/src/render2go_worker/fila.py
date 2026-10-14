"""Worker da fila (ADR 0003, fase 3): o pod puxa jobs da API, renderiza e envia o resultado.

Nenhuma porta de entrada no pod: ele só faz chamadas de saída para a API (token de worker) e
para o armazenamento (URLs assinadas, uma por arquivo). Por job:

- `compilar`: o pedido do lote vira prompt uma vez (presets + compilador) e vai para a API,
  que passa o filtro C1 de novo antes de liberar os renders;
- `render`: baixa a foto (só https e hosts de RENDER2GO_ALLOWED_URL_HOSTS), prepara no tamanho do
  vídeo, renderiza no ComfyUI, **confere o 1º quadro contra a foto e a continuidade até o fim**
  (render2go_worker.conferencia), **passa os quadros pelo filtro de imagem** (nudez:
  render2go_worker.filtro_imagem, ADR 0005) e só então envia o vídeo e a folha de conferência. A API
  recusa "ok" sem essas medidas; o vídeo que troca de produto no meio é refeito com outra seed; o
  vídeo barrado pelo filtro não vai ao cliente e não é refeito: ele e a folha vão para a quarentena,
  que só o dono vê pelo painel, por 7 dias (12/10/2026).

Um laço por ComfyUI (uma GPU cada), e **a GPU não espera** (12/10/2026): enquanto ela gera um
vídeo, o próximo já é pego e preparado (foto baixada, conferida e no tamanho do vídeo) e o anterior
é conferido, filtrado e enviado, em segundo plano. Antes de ir para a GPU, o vídeo adiantado passa
pela API (`iniciar`): se o pedido foi parado nesse meio-tempo, ele não é gerado (cancelado, sem
custo). Se o laço para com um vídeo adiantado na mão, ele volta para a fila sem contar tentativa
(`devolver`). Com `--sem-adiantar`, um vídeo de cada vez, do começo ao fim (como antes).

Sem pedido na fila logo que os modelos ficam prontos, o worker **aquece** a GPU com um vídeo
pequeno e descartado (os modelos vão para a memória e o 1º pedido não paga a carga); com fila, não
aquece.

Sem jobs por `--ocioso` segundos (o "pod quente", docs/08),
o worker sai e o pod se desliga (desligamento automático do lote_nuvem / orquestrador). Com
`--motivo ARQUIVO`, o worker escreve ali por que saiu (MOTIVOS), e o orquestrador registra no
relatório de GPUs do painel: "sem pedidos" não é o mesmo que "limite de tempo do pod".

Com `--modelos-prontos ESTADO` (o arquivo de estado do bootstrap_pod.sh), o worker começa antes
de os modelos de vídeo terminarem de baixar: até o estado dizer PRONTO, ele só pega jobs
`compilar` (traduz o pedido enquanto o download termina); FALHOU, ou passar de `--modelos-ate`,
e ele sai (o orquestrador encerra o pod).

Uso no pod: python -m render2go_worker.fila --api https://... --worker pod-<id> [--gpus N]
Token em RENDER2GO_WORKER_TOKEN. Filtros: RENDER2GO_SAFETY=c1-revisao-humana (ADR 0004).
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import hmac
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from render2go_worker import (
    cli_submit,
    comfy_client,
    compiler,
    conferencia,
    filtro_imagem,
    legenda,
    lote,
    media,
    preparo,
    presets,
    safety,
    traducao,
)

MAX_BYTES_FOTO = 50 * 1024**2
# modelos de geração que este worker sabe rodar (render2go_servico/modelos.py lista os oferecidos);
# pedido de outro modelo é recusado sem repetir: fail-closed
MODELOS = {"wan2.2-i2v"}
ESPERA_S = 5  # fila vazia: tempo entre consultas
ESPERA_MODELOS_S = 2  # modelos baixando: confere o estado de novo logo
OCIOSO_S = 180  # ~ o tempo de uma subida de pod: depois disso, sair custa menos que esperar
# antes do 1º vídeo, quanto a GPU pode levar num vídeo (720p, 7 s: ~250 s na L40S): perto do
# limite de tempo do pod, o próximo não é adiantado
GPU_PADRAO_S = 300
# aquecimento: o mesmo template, no menor tamanho e duração que ele aceita, com uma imagem cinza
AQUECER_LADO, AQUECER_DURACAO = 256, 3
AQUECER_PROMPT = "A plain gray background. The camera stays still."
TRADUCOES_EM_CACHE = 64  # resultados do compilador guardados no pod (WorkerFila._compilar_em_cache)
# por que o worker saiu (o mais grave entre os laços vale para o pod)
MOTIVOS = {
    "modelos": "a instalação dos modelos falhou ou passou do prazo",
    "filtro": "o filtro de imagem não carregou (nenhum vídeo sai sem ele)",
    "gpu": "a GPU ficou inacessível",
    "limite": "limite de tempo do pod (2 h ou o que o saldo do RunPod paga)",
    "ocioso": "sem pedidos (pod quente vencido)",
}
TIPOS = {"jpg": ".jpg", "png": ".png", "webp": ".webp"}
OLLAMA_CPU = legenda.OLLAMA_CPU  # o 2º Ollama do pod, só em CPU (legenda por foto, ADR 0014)
# Entrega (14/10/2026): o vídeo aprovado (conferência e filtro) fica no disco do pod, com o SHA-256,
# até a API confirmar; a falha de envio ou de confirmação repete o MESMO vídeo, sem voltar à GPU.
# Só depois de ENTREGA_MAX_S (e nunca depois do limite do pod) ele volta para a fila, com a mesma
# seed (falha de transporte). Some com o disco do pod: não há recuperação depois que o pod acaba.
ENTREGA_MAX_S = 600
ENTREGA_ALEM_ATE_S = 300  # a entrega pode passar do --ate (que fica 10 min antes do desligamento)
ENTREGAS_MAX = 8  # vídeos esperando confirmação no disco, no máximo (o resto segue sem retenção)
ENTREGAS_MAX_BYTES = 2 * 1024**3
ENTREGA_VELHA_S = 3 * 3600  # na partida, manifestos mais velhos que o pod (2 h) saem sem envio
MANIFESTO = "entrega.json"
BATIMENTO_S = 60  # renovação do prazo do job durante o vídeo, com o progresso observado


class FilaError(Exception):
    pass


class Perdida(FilaError):
    """O job não está mais com esta tentativa (prazo vencido, pedido parado, outro pod): o que o
    pod tem dele não é enviado nem confirmado."""


def _log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} [fila] {msg}", file=sys.stderr, flush=True)


class ApiFila:
    """Cliente da API para workers (Bearer RENDER2GO_WORKER_TOKEN)."""

    def __init__(self, url: str, token: str, worker: str, timeout: float = 30):
        if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise FilaError("a API precisa ser https")
        self.url, self.worker, self.timeout = url.rstrip("/"), worker, timeout
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"

    @staticmethod
    def _com(corpo: dict, tentativa: str | None) -> dict:
        """A tentativa vai junto (14/10/2026) só quando a API a deu (a de antes não a conhece)."""
        return {**corpo, "tentativa": tentativa} if tentativa else corpo

    def _post(self, rota: str, corpo: dict, tentativas: int = 5) -> requests.Response:
        erro = ""
        for i in range(tentativas):
            try:
                r = self.s.post(self.url + rota, json=corpo, timeout=self.timeout)
                if r.status_code < 500:
                    return r
                erro = f"HTTP {r.status_code}"
            except requests.RequestException as e:
                erro = str(e)
            time.sleep(min(30, 2**i))
        raise FilaError(f"API inacessível ({rota}): {erro}")

    def reivindicar(self, tipos: list[str] | None = None) -> dict | None:
        # só pedidos dos modelos que este pod roda (a API filtra; o pod confere de novo)
        corpo: dict[str, Any] = {"worker": self.worker, "modelos": sorted(MODELOS)}
        if tipos:
            corpo["tipos"] = tipos
        r = self._post("/api/worker/reivindicar", corpo)
        if r.status_code != 200:
            raise FilaError(f"reivindicar: HTTP {r.status_code} {r.text[:200]}")
        return r.json().get("job")

    def prompt(
        self, job_id: str, prompt_en: str, compilador: str, tentativa: str | None = None, **detalhe
    ) -> None:
        """`detalhe`: como a tradução saiu (vago, padrao, pessoas_recusadas...), para o registro do
        pedido; a API de antes ignora."""
        r = self._post(
            f"/api/worker/jobs/{job_id}/prompt",
            self._com(
                {
                    "worker": self.worker,
                    "prompt_en": prompt_en,
                    "compilador": compilador,
                    **detalhe,
                },
                tentativa,
            ),
        )
        if r.status_code != 200:
            raise FilaError(f"prompt recusado: HTTP {r.status_code} {r.text[:200]}")

    def concluir(self, job_id: str, status: str, tentativa: str | None = None, **campos) -> str:
        """Conclui a tentativa. A mesma tentativa repetindo (a resposta se perdeu) recebe a mesma
        resposta da API, sem cobrar de novo; 409 (o job é de outra tentativa) levanta Perdida; 503
        (a publicação falhou) e a API fora do ar levantam FilaError (repetir mais tarde)."""
        corpo = self._com({"worker": self.worker, "status": status, **campos}, tentativa)
        r = self._post(f"/api/worker/jobs/{job_id}/concluir", corpo)
        if r.status_code == 409:
            raise Perdida(f"concluir: {r.text[:200]}")
        if r.status_code != 200:
            raise FilaError(f"concluir: HTTP {r.status_code} {r.text[:200]}")
        return r.json()["status"]

    def iniciar(self, job_id: str, tentativa: str | None = None) -> float | bool:
        """O vídeo pego adiantado pode ir para a GPU? False: o pedido foi parado (a API cancelou o
        vídeo, sem custo) ou o job não está mais com este pod. Com a API de 14/10/2026, devolve o
        prazo absoluto da tentativa (s a partir de agora), que recomeça aqui."""
        r = self._post(
            f"/api/worker/jobs/{job_id}/iniciar", self._com({"worker": self.worker}, tentativa)
        )
        if r.status_code == 404:  # API de antes desta rota: segue, como antes
            return True
        if r.status_code == 409:
            return False
        if r.status_code != 200:
            raise FilaError(f"iniciar: HTTP {r.status_code} {r.text[:200]}")
        d = r.json()
        if not d.get("seguir"):
            return False
        return float(d["prazo_s"]) if d.get("prazo_s") else True

    def devolver(self, job_id: str, tentativa: str | None = None) -> None:
        """Vídeo pego adiantado que este pod não vai gerar: volta para a fila sem contar
        tentativa."""
        r = self._post(
            f"/api/worker/jobs/{job_id}/devolver", self._com({"worker": self.worker}, tentativa)
        )
        if r.status_code == 404:  # API de antes desta rota: volta como um erro que se refaz
            self.concluir(job_id, "erro", erro="devolvido sem ir para a GPU", repetir=True)
        elif r.status_code not in (200, 409):
            raise FilaError(f"devolver: HTTP {r.status_code} {r.text[:200]}")

    def renovar(self, job_id: str, tentativa: str, etapa: str, progresso: int) -> bool:
        """Renova o prazo do job com o progresso observado (14/10/2026). False: a API de antes não
        tem a rota (nada a fazer). Perdida: o job não está mais com esta tentativa, o prazo absoluto
        venceu ou não houve progresso (a API recusa o batimento sozinho)."""
        corpo = self._com(
            {"worker": self.worker, "etapa": etapa, "progresso": int(progresso)}, tentativa
        )
        r = self._post(f"/api/worker/jobs/{job_id}/renovar", corpo, tentativas=2)
        if r.status_code == 404:
            return False
        if r.status_code == 409:
            raise Perdida(f"renovar: {r.text[:200]}")
        if r.status_code != 200:
            raise FilaError(f"renovar: HTTP {r.status_code} {r.text[:200]}")
        return True

    def urls(self, job_id: str, tentativa: str | None) -> dict:
        """URLs de envio novas para a mesma tentativa (a de antes venceu: 1 h)."""
        r = self._post(
            f"/api/worker/jobs/{job_id}/urls", self._com({"worker": self.worker}, tentativa)
        )
        if r.status_code == 409:
            raise Perdida(f"urls: {r.text[:200]}")
        if r.status_code != 200:
            raise FilaError(f"urls: HTTP {r.status_code} {r.text[:200]}")
        return r.json()


def semente_do_pedido(lote_id: str) -> int:
    """A seed do tradutor, fixa por pedido: a mesma tradução para todas as fotos do pedido (a do
    pedido e, com a legenda por foto, a de cada foto) e em qualquer pod; outra em cada pedido.
    Antes de 13/10/2026, sem seed (uma aleatória a cada chamada)."""
    return (
        int.from_bytes(hashlib.sha256(f"traducao:{lote_id}".encode()).digest()[:4], "big") % 10**9
    )


def carimbo_ok(segredo: str | None, job: dict) -> bool:
    """Confere o carimbo de aprovação (HMAC, ADR 0004) quando o segredo foi dado ao worker."""
    if not segredo:
        return True
    esperado = hmac.new(
        segredo.encode(), f"{job['lote']}:{job['aprovado_em']:.6f}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(esperado, str(job.get("aprovacao", "")))


@dataclasses.dataclass
class Config:
    comfy: list[str]
    ollama: str = "http://127.0.0.1:11434"
    model: str = compiler.DEFAULT_MODEL
    template: Path = lote.I2V_TEMPLATE
    timeout: float = 1800.0
    ocioso_s: float = OCIOSO_S
    segredo: str | None = None
    ate: float | None = None  # epoch: não pega job novo depois disso (limite de 2 h do pod)
    modelos: Path | None = None  # estado do bootstrap: renderiza só depois de PRONTO
    modelos_ate: float | None = None  # epoch: modelos não ficaram prontos até aqui = sai
    motivo_em: Path | None = None  # arquivo onde o worker escreve por que saiu (MOTIVOS)
    adiantar: bool = True  # a GPU não espera: o próximo é preparado e o anterior enviado durante
    aquecer: bool = False  # sem pedido depois do PRONTO: um vídeo descartado carrega os modelos
    legenda: bool = False  # legenda por foto (ADR 0014): o modelo de visão no Ollama de CPU
    ollama_cpu: str = OLLAMA_CPU
    # vídeos aprovados esperando a confirmação da API (14/10/2026): no disco do pod, um por pasta
    entregas: Path = dataclasses.field(
        default_factory=lambda: Path(tempfile.gettempdir()) / "render2go-entregas"
    )


@dataclasses.dataclass
class Preparado:
    """Um vídeo pronto para a GPU: a foto baixada, conferida e preparada no tamanho do vídeo."""

    job: dict
    pasta: Path  # temporária, só deste vídeo (apagada quando ele termina, de qualquer jeito)
    tarefa: lote.Tarefa
    prep: preparo.Preparada
    t0: float  # quando o job foi pego (monotonic)
    etapas: dict[str, float]  # segundos por etapa: preparo, espera_gpu, gpu, render, envio...
    adiantado: bool = False  # pego enquanto a GPU gerava o anterior: passa pela API antes de ir
    ciclo_s: float | None = None  # tempo de GPU do vídeo, com a espera dela (None: do pego ao fim)
    prazo: float | None = None  # prazo absoluto da tentativa (monotonic); None: API de antes
    batimento: Batimento | None = None  # renova o prazo do job com o progresso (14/10/2026)


class Batimento:
    """Renova o prazo (lease) do job a cada BATIMENTO_S com a etapa e um contador de progresso de
    verdade (passos do ComfyUI, tentativas de envio): a API recusa a renovação sem progresso e
    depois do prazo absoluto da tentativa. Se a API recusar (Perdida), `perdida` fica marcada e
    `ao_perder` é chamado (ex.: interromper o ComfyUI): o vídeo dessa tentativa não é enviado. Só
    com a API de 14/10/2026 (o job traz a `tentativa`); com a de antes, não faz nada."""

    def __init__(self, api: ApiFila, job: dict, ao_perder: Callable[[], None] | None = None):
        self.api, self.job, self.ao_perder = api, job, ao_perder
        self.etapa, self.progresso = "gpu", 0
        self.perdida = threading.Event()
        self._fim = threading.Event()
        self._t: threading.Thread | None = None
        if job.get("tentativa"):
            self._t = threading.Thread(target=self._rodar, daemon=True, name="batimento")
            self._t.start()

    def marcar(self, etapa: str | None = None, mais: int = 1) -> None:
        if etapa:
            self.etapa = etapa
        self.progresso += mais

    def renovar(self) -> None:
        """Uma renovação agora (também chamada pela entrega entre duas tentativas de envio)."""
        if not self.job.get("tentativa") or self.perdida.is_set():
            return
        try:
            if not self.api.renovar(
                self.job["job_id"], self.job["tentativa"], self.etapa, self.progresso
            ):
                self._fim.set()  # API sem a rota
        except Perdida as e:
            _log(f"{self.job.get('arquivo') or self.job['job_id']}: {e}")
            self.perdida.set()
            if self.ao_perder:
                self.ao_perder()
        except FilaError as e:  # API fora do ar: o prazo atual vale; a conclusão confere depois
            _log(f"AVISO: renovação do prazo: {e}")

    def _rodar(self) -> None:
        while not self._fim.wait(BATIMENTO_S):
            self.renovar()
            if self.perdida.is_set():
                return

    def parar(self) -> None:
        self._fim.set()


@dataclasses.dataclass
class Entrega:
    """Um vídeo aprovado esperando a confirmação: a pasta (vídeo e folha) no disco do pod, o SHA-256
    e o que vai na conclusão. O manifesto (sem as URLs assinadas) deixa um worker que reiniciou no
    mesmo pod continuar a entrega, se a tentativa ainda for dele."""

    pasta: Path
    job: dict
    sha256: str
    bytes: int
    campos: dict
    enviado: bool = False

    @property
    def video(self) -> Path:
        return self.pasta / "video.mp4"

    @property
    def folha(self) -> Path:
        return self.pasta / "folha.jpg"

    def integra(self) -> bool:
        try:
            return self.video.stat().st_size == self.bytes and _sha256(self.video) == self.sha256
        except OSError:
            return False

    def gravar(self) -> None:
        dados = {
            "job": {k: self.job.get(k) for k in ("job_id", "tentativa", "lote", "arquivo")},
            "sha256": self.sha256,
            "bytes": self.bytes,
            "campos": self.campos,
            "criada_em": time.time(),
        }
        tmp = self.pasta / (MANIFESTO + ".tmp")
        tmp.write_text(json.dumps(dados))
        tmp.replace(self.pasta / MANIFESTO)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


FEITO = "feito"  # o job terminou antes da GPU (erro já informado à API)


@dataclasses.dataclass
class _Laco:
    """Estado do laço de uma GPU."""

    comfy: str
    fundo: ThreadPoolExecutor
    adiantado: Future | None = None  # o próximo job, pego e preparado durante o vídeo atual
    envio: Future | None = None  # o vídeo anterior, conferido e enviado durante o atual
    fim_gpu: float | None = None  # quando a GPU terminou o último vídeo (monotonic)
    gpu_s: float = GPU_PADRAO_S  # quanto o último vídeo levou na GPU
    aquecido: bool = False


class WorkerFila:
    def __init__(
        self,
        api: ApiFila,
        cfg: Config,
        submit: Callable = cli_submit.run,
        compile_fn: Callable = compiler.compile_prompt,
        classificador: filtro_imagem.Classificador | None = None,
    ):
        self.api, self.cfg, self.submit, self.compile_fn = api, cfg, submit, compile_fn
        self.filtros = safety.load_filters()  # fail-closed: sem filtros, nada roda
        # 2ª porta (ADR 0005): carregado no 1º vídeo; sem ele, nenhum vídeo é enviado
        modelos = Path(os.environ.get("COMFY_DIR", "/opt/comfyui")) / "models"
        self.classificador = classificador or filtro_imagem.Freepik(modelos / filtro_imagem.PASTA)
        self.filtro_falhou = False
        self.receitas = lote.load_receitas()
        self.ultimo_job = time.monotonic()
        self.lock = threading.Lock()
        self.feitos = self.falhas = 0
        self.modelos_ok = cfg.modelos is None
        self.motivos: list[str] = []  # um por laço que terminou (chaves de MOTIVOS)
        self.medidas: list[dict] = []  # etapas de cada vídeo que terminou (log e medições em GPU)
        self._traducoes: dict[tuple, compiler.CompileResult] = {}  # _compilar_em_cache

    def estado_modelos(self) -> str:
        """Estado dos modelos: "ok" (renderiza), "esperando" (só traduz) ou "falhou" (sai). Lido a
        cada job, mesmo depois do PRONTO: na volta da hibernação, o SHA-256 dos modelos termina
        depois dele e pode virar FALHOU (ADR 0011)."""
        if self.cfg.modelos is None:
            return "ok"
        try:
            estado = self.cfg.modelos.read_text().strip()
        except OSError:
            estado = ""
        if estado.startswith("FALHOU"):
            _log(f"a instalação falhou ({estado[:200]}): o worker sai")
            return "falhou"
        if self.modelos_ok:
            return "ok"
        if estado.startswith("PRONTO"):
            with self.lock:
                if not self.modelos_ok:
                    self.modelos_ok = True
                    self.ultimo_job = time.monotonic()  # o pod quente conta a partir daqui
                    _log("modelos prontos: renderizando")
                    self._precarregar_filtro()
            return "ok"
        if self.cfg.modelos_ate and time.time() > self.cfg.modelos_ate:
            _log("os modelos não ficaram prontos no prazo: o worker sai")
            return "falhou"
        return "esperando"

    def _precarregar_filtro(self) -> None:
        """Carrega o filtro de imagem em segundo plano, para o 1º vídeo não esperar o PyTorch. Se
        falhar, o 1º vídeo tenta de novo e, sem filtro, não sai (fail-closed)."""
        carregar = getattr(self.classificador, "carregar", None)
        if carregar is None:
            return

        def rodar() -> None:
            try:
                carregar()
                _log("filtro de imagem carregado")
            except Exception as e:  # noqa: BLE001 (o 1º vídeo tenta de novo)
                _log(f"AVISO: filtro de imagem não carregou ainda: {e}")

        threading.Thread(target=rodar, daemon=True, name="filtro").start()

    # -- jobs -----------------------------------------------------------------------------

    def compilar(self, job: dict) -> None:
        """O pedido vira prompt com as regras do serviço (render2go_worker.traducao, 13/10/2026):
        pedido vago e pessoa inventada viram o movimento padrão. `regras` vem da API: a categoria da
        foto e se a regra da pessoa inventada vale (sem ela, API de antes: na dúvida, vale)."""
        pedido = job["pedido"]
        d = self.filtros.check_request(pedido["prompt"])
        if not d.allowed:
            self._concluir(job, "erro", erro=f"filtro: {d.reason}")
            return
        regras = job.get("regras") or {}
        # tradução por foto (legenda por foto, ADR 0014): a legenda feita durante o vídeo anterior
        # ou, sem ela, agora
        if "_legenda" in job:
            leg = job["_legenda"]
        else:
            leg = self._legendar(job) if job.get("legenda") else None
        try:
            tr = traducao.traduzir(
                pedido["prompt"],
                pedido["receita"],
                receitas=self.receitas,
                categoria=regras.get("categoria"),
                sem_pessoas=regras.get("sem_pessoas"),
                assunto=regras.get("assunto") or "",
                legenda=leg,
                compile_fn=self._compilar_em_cache,
                seed=semente_do_pedido(job["lote"]),
                ollama_url=self.cfg.ollama,
                model=self.cfg.model,
                keep_alive=0,  # libera a VRAM antes dos renders
            )
        except compiler.CompilerRefused as e:
            self._concluir(job, "erro", erro=f"compilador recusou: {e}")
            return
        except compiler.CompilerError as e:
            self._concluir(job, "erro", erro=str(e)[:500], repetir=True)
            return
        except (traducao.TraducaoError, presets.PresetError) as e:
            self._concluir(job, "erro", erro=f"pedido inválido: {e}"[:500])
            return
        d = self.filtros.check_prompt(tr.prompt)
        if not d.allowed:
            self._concluir(job, "erro", erro=f"filtro no prompt: {d.reason}")
            return
        self.api.prompt(  # a API passa o C1 de novo e confere a pessoa inventada
            job["job_id"],
            tr.prompt,
            tr.ref,
            tentativa=job.get("tentativa"),
            vago=tr.vago,
            padrao=tr.padrao,
            tentativas=tr.tentativas,
            pessoas_recusadas=list(tr.pessoas_recusadas),
            legenda=leg.como_dict() if leg is not None else None,
        )
        self._concluir(job, "ok")
        como = " (movimento padrão)" if tr.padrao else ""
        _log(f"lote {job['lote']}: prompt compilado ({tr.word_count} palavras{como})")

    def _concluir(self, job: dict, status: str, **campos: Any) -> str:
        """Conclui com a tentativa do job (API de 14/10/2026); sem ela, como antes."""
        if job.get("tentativa"):
            campos["tentativa"] = job["tentativa"]
        return self.api.concluir(job["job_id"], status, **campos)

    def _compilar_em_cache(self, texto: str, **kw: Any) -> compiler.CompileResult:
        """O compilador, com o resultado guardado pela entrada inteira (o texto composto, que traz o
        pedido, os termos do estilo e, do v7 em diante, as linhas da foto; a versão; a seed; o
        sufixo; a câmera e o modelo). Na legenda por foto (ADR 0014), cada foto do pedido ganha a
        sua tradução, mas o prompt de produção não vê a legenda: a entrada é a mesma em todas as
        fotos, e sem o cache cada uma recarregaria o tradutor na GPU (keep_alive 0) entre dois
        vídeos para repetir o mesmo texto. Recusa e erro não ficam guardados (a próxima foto
        pergunta de novo); a seed da nova tentativa da pessoa inventada muda a chave."""
        chave = (
            texto, kw.get("profile"), kw.get("version"), kw.get("seed"), kw.get("suffix"),
            kw.get("camera_preset"), kw.get("model"),
        )  # fmt: skip
        with self.lock:
            guardado = self._traducoes.get(chave)
        if guardado is not None:
            return guardado
        r = self.compile_fn(texto, **kw)
        with self.lock:
            self._traducoes[chave] = r
            while len(self._traducoes) > TRADUCOES_EM_CACHE:  # o mais antigo sai
                self._traducoes.pop(next(iter(self._traducoes)))
        return r

    def _legendar(self, job: dict) -> legenda.Legenda | None:
        """A legenda da foto de uma tradução por foto (ADR 0014): a foto baixada, conferida e no
        formato do vídeo (o 1º quadro que ele vai ter), no Ollama de CPU. Com a legenda desligada
        neste pod ou com qualquer erro: None (a foto segue sem legenda; nunca bloqueia o pedido)."""
        if not self.cfg.legenda:
            return None
        t0 = time.monotonic()
        pasta = Path(tempfile.mkdtemp(prefix="render2go-legenda-"))
        try:
            foto = pasta / f"foto{TIPOS.get(job.get('foto_ext', ''), '.jpg')}"
            media.download(job["foto_url"], foto, MAX_BYTES_FOTO)
            media.check_image(foto, max_pixels=preparo.MAX_PIXELS)
            t = self._tarefas(job, foto, job["arquivo"].rsplit("-v", 1)[0])[0]
            prep = preparo.preparar(foto, t.width, t.height, pasta / "preparada.png")
            # metade das CPUs do contêiner (a cota, não as do host); a outra metade fica com o
            # ComfyUI e com a conferência do vídeo anterior
            leg = legenda.tentar(
                prep.path, ollama_url=self.cfg.ollama_cpu, threads=legenda.threads_da_legenda()
            )
        except Exception as e:  # noqa: BLE001 (a legenda nunca bloqueia o pedido nem o laço da GPU)
            _log(f"AVISO: {job.get('arquivo')}: a foto segue sem legenda ({str(e)[:200]})")
            return None
        finally:
            shutil.rmtree(pasta, ignore_errors=True)
        if leg is not None:
            _log(
                f"{job['arquivo']}: legenda em {time.monotonic() - t0:.1f} s "
                f"({leg.categoria}: {leg.assunto})"
            )
        return leg

    def _tarefas(self, job: dict, foto: Path, nome: str) -> list[lote.Tarefa]:
        p = job["pedido"]
        pedido = lote.Pedido(
            receita=p["receita"],
            acao=p["prompt"],
            formato=p.get("formato") or "auto",
            duracao=p.get("duracao") or 0,
            variacoes=p.get("variacoes") or 1,
            consentimento=bool(p.get("consentimento")),
        )
        tarefas, erros = lote._tarefas(foto, nome, pedido, p["qualidade"], self.receitas)
        if erros:
            raise FilaError("; ".join(erros))
        return tarefas

    def _tarefa(self, job: dict, foto: Path) -> lote.Tarefa:
        tarefas = self._tarefas(job, foto, Path(job["arquivo"]).stem.rsplit("-v", 1)[0])
        t = next((t for t in tarefas if t.arquivo == job["arquivo"]), None)
        if t is None:
            raise FilaError(f"arquivo {job['arquivo']} fora do pedido")
        return dataclasses.replace(t, seed=int(job["seed"]))

    def renderizar(self, job: dict, comfy: str) -> bool:
        """Um vídeo do começo ao fim, sem sobrepor nada. Devolve False se a GPU ficou inacessível
        ou o filtro de imagem falhou (o laço dela para)."""
        p = self._preparar(job)
        if p is None:
            return True
        r = self._gerar(p, comfy)
        if r != "ok":
            return r != "gpu"
        return self._finalizar(p)

    def _preparar(self, job: dict) -> Preparado | None:
        """Baixa a foto, confere e prepara no tamanho do vídeo (rede e CPU: com `adiantar`, roda
        enquanto a GPU gera o vídeo anterior). None: o job terminou aqui, com o erro na API."""
        t0 = time.monotonic()
        pasta = Path(tempfile.mkdtemp(prefix="render2go-job-"))
        try:
            foto = pasta / f"foto{TIPOS.get(job.get('foto_ext', ''), '.jpg')}"
            media.download(job["foto_url"], foto, MAX_BYTES_FOTO)
            # o mesmo teto do preparo (50 MP); o padrão de check_image é o do endpoint (jobs.py)
            media.check_image(foto, max_pixels=preparo.MAX_PIXELS)
            t = self._tarefa(job, foto)
            prep = preparo.preparar(foto, t.width, t.height, pasta / "preparada.png")
        except (
            media.MediaError,
            media.UnsafeUrl,
            preparo.PreparoError,
            FilaError,
            lote.LoteError,
        ) as e:
            shutil.rmtree(pasta, ignore_errors=True)
            repetir = isinstance(e, media.MediaError) and getattr(e, "retryable", False)
            self._concluir(job, "erro", erro=str(e)[:500], repetir=repetir)
            return None
        except BaseException:
            shutil.rmtree(pasta, ignore_errors=True)
            raise
        prazo = t0 + float(job["prazo_s"]) if job.get("prazo_s") else None  # API de 14/10/2026
        return Preparado(job, pasta, t, prep, t0, {"preparo": time.monotonic() - t0}, prazo=prazo)

    def _gerar(self, p: Preparado, comfy: str) -> str:
        """O vídeo na GPU (ComfyUI). "ok"; "erro" (o job terminou, com o erro na API); "gpu" (a
        GPU ficou inacessível: o job volta para a fila e o laço para)."""
        video = p.pasta / "video.mp4"
        args = lote._args(p.tarefa, p.job["prompt_en"], video, comfy, self.cfg, p.prep.path)
        if p.prazo is not None:  # um prazo só: o do ComfyUI nunca passa o da tentativa (com folga)
            args.timeout = max(60.0, min(args.timeout, p.prazo - time.monotonic() - 60))
        if p.batimento is None:
            p.batimento = Batimento(
                self.api, p.job, ao_perder=lambda: comfy_client.ComfyClient(comfy).interrupt()
            )
        args.on_progress = lambda msg: p.batimento.marcar("gpu")  # passos de verdade do ComfyUI
        t = time.monotonic()
        code, res = self.submit(args)
        p.etapas["gpu"] = time.monotonic() - t
        if p.batimento.perdida.is_set():  # a API tirou o job desta tentativa: o vídeo não vai
            _log(f"{p.job['arquivo']}: a tentativa perdeu o job durante a geração; descartado")
            p.batimento.parar()
            shutil.rmtree(p.pasta, ignore_errors=True)
            return "perdida"
        if code == cli_submit.EXIT_OK and video.is_file():
            render = (res.get("timings") or {}).get("render_s")
            if render is not None:  # só o ComfyUI, sem o envio da foto e a cópia do vídeo
                p.etapas["render"] = float(render)
            return "ok"
        p.batimento.parar()
        try:
            if code == cli_submit.EXIT_UNREACHABLE:
                erro = f"GPU inacessível: {res.get('error')}"
                self._concluir(p.job, "erro", erro=erro, repetir=True)
                return "gpu"
            repetir = code in (cli_submit.EXIT_COMFY,)
            erro = str(res.get("error", code))[:500]
            self._concluir(p.job, "erro", erro=erro, repetir=repetir)
            return "erro"
        finally:
            shutil.rmtree(p.pasta, ignore_errors=True)

    def _finalizar(self, p: Preparado) -> bool:
        """Confere, filtra e envia o vídeo (CPU e rede: com `adiantar`, roda enquanto a GPU gera o
        próximo). False: o filtro de imagem falhou (o laço para; nenhum vídeo sai sem ele)."""
        try:
            return self._conferir_e_enviar(p)
        finally:
            shutil.rmtree(p.pasta, ignore_errors=True)
            if p.batimento is not None:
                p.batimento.parar()

    def _conferir_e_enviar(self, p: Preparado) -> bool:
        job, pasta, t, prep = p.job, p.pasta, p.tarefa, p.prep
        video = pasta / "video.mp4"
        bat = p.batimento
        if bat is not None:
            bat.marcar("conferencia")
        etapa = time.monotonic()
        try:  # o vídeo é dessa foto e continua nela até o fim? só envia se for
            sim = conferencia.confere(prep.path, video)
            cont = conferencia.continuidade(video)
        except conferencia.ConferenciaError as e:
            self._concluir(p.job, "erro", erro=str(e)[:500], repetir=True)
            return True
        p.etapas["conferencia"] = time.monotonic() - etapa
        problema = conferencia.problema_continuidade(**cont)
        if problema:  # refeito com outra seed (a API troca a seed ao devolver o job)
            self._concluir(p.job, "erro", erro=problema, repetir=True, **cont)
            _log(f"{job['arquivo']} (lote {job['lote']}): {problema}; refazendo")
            return True
        if bat is not None:
            bat.marcar("filtro")
        etapa = time.monotonic()
        try:  # 2ª porta (ADR 0005): nudez em algum quadro = o vídeo não sai do pod
            filtro = filtro_imagem.avaliar(video, self.classificador)
        except filtro_imagem.FiltroError as e:
            self._concluir(p.job, "erro", erro=f"filtro de imagem: {e}"[:500], repetir=True)
            _log(f"{job['arquivo']}: filtro de imagem indisponível ({e}); o worker para")
            self.filtro_falhou = True
            return False
        p.etapas["filtro"] = time.monotonic() - etapa
        if filtro.barrado:  # não vai ao cliente nem é refeito; vai à quarentena (ADR 0005)
            self._concluir(
                job,
                "erro",
                erro=f"barrado pelo filtro de imagem (nota {filtro.nota:.2f}, "
                f"explícita {filtro.nota_alta:.2f})",
                barrado=True,
                quarentena=self._quarentena(job, prep.path, video, pasta, t),
                nsfw=filtro.nota,
                nsfw_alto=filtro.nota_alta,
                nsfw_quadros=filtro.quadros,
                **cont,
            )
            with self.lock:
                self.falhas += 1
            _log(
                f"{job['arquivo']} (lote {job['lote']}): barrado pelo filtro de imagem (nota "
                f"{filtro.nota:.2f}, explícita {filtro.nota_alta:.2f}, "
                f"pior quadro {filtro.pior_quadro})"
            )
            return True
        if bat is not None and bat.perdida.is_set():
            _log(f"{job['arquivo']}: a tentativa perdeu o job; o vídeo não é enviado")
            return True
        etapa = time.monotonic()
        aviso = prep.aviso
        try:
            preparo.folha(prep.path, video, pasta / "folha.jpg", 16 * t.duracao + 1)
        except Exception as e:  # noqa: BLE001 (a folha é ajuda ao revisor, não bloqueia)
            aviso = f"{aviso}; sem folha de conferência ({e})".strip("; ")
        # tempo de pod do vídeo (previsão para o cliente e relatório de GPUs): com `adiantar`, o
        # da GPU com a espera dela; sem, do pego ao enviado (o preparo e o envio também param a GPU)
        total = time.monotonic() - p.t0
        campos = {
            "segundos": round(p.ciclo_s if p.ciclo_s is not None else total, 1),
            "similaridade": round(sim, 4),
            "bytes_video": video.stat().st_size,
            "aviso": aviso or None,
            "nsfw": filtro.nota,
            "nsfw_alto": filtro.nota_alta,
            "nsfw_quadros": filtro.quadros,
            **cont,
        }
        e = self._reter(p, campos)
        status = self._entregar(e, bat)
        p.etapas["envio"] = time.monotonic() - etapa
        p.etapas["total"] = time.monotonic() - p.t0
        with self.lock:
            self.feitos += status == "ok"
            self.falhas += status != "ok"
            self.medidas.append({"job": job["job_id"], "arquivo": job["arquivo"], **p.etapas})
        _log(
            f"{job['arquivo']} (lote {job['lote']}): {status}, similaridade {sim:.3f}; "
            + ", ".join(f"{k} {v:.1f} s" for k, v in p.etapas.items())
        )
        return True

    # -- entrega (14/10/2026): o mesmo vídeo aprovado até a confirmação ------------------------

    def _reter(self, p: Preparado, campos: dict) -> Entrega:
        """O vídeo aprovado passa para a pasta de entregas (um rename, no mesmo disco), com o
        SHA-256 e o manifesto. Acima do limite de entregas, ou com o disco cheio, fica na pasta do
        job e segue sem o manifesto (sem retomada depois de um reinício)."""
        video = p.pasta / "video.mp4"
        sha, n = _sha256(video), video.stat().st_size
        destino = self.cfg.entregas / f"{p.job['job_id']}-{p.job.get('tentativa') or 'sem'}"
        try:
            self.cfg.entregas.mkdir(parents=True, exist_ok=True)
            atuais = [d for d in self.cfg.entregas.iterdir() if (d / MANIFESTO).is_file()]
            ocupado = sum(
                (d / "video.mp4").stat().st_size for d in atuais if (d / "video.mp4").is_file()
            )
            if len(atuais) >= ENTREGAS_MAX or ocupado + n > ENTREGAS_MAX_BYTES:
                raise OSError("limite de entregas no disco")
            for nome in ("preparada.png",):  # o que a entrega não usa sai antes
                (p.pasta / nome).unlink(missing_ok=True)
            p.pasta.rename(destino)
            e = Entrega(destino, p.job, sha, n, campos)
            e.gravar()
            return e
        except OSError as erro:
            _log(f"AVISO: {p.job['arquivo']}: entrega sem retenção no disco ({erro})")
            pasta = destino if destino.is_dir() else p.pasta
            return Entrega(pasta, p.job, sha, n, campos)

    def _entregar(self, e: Entrega, bat: Batimento | None = None) -> str:
        """Envia o vídeo e confirma na API, repetindo o MESMO vídeo (conferido pelo SHA-256 antes de
        cada envio) quando o envio ou a confirmação falham: URL vencida pede URLs novas; API fora do
        ar repete a confirmação (a API responde de novo a mesma coisa à mesma tentativa, sem cobrar
        duas vezes). Fim: "ok"/"erro" da API, "perdida" (o job não é mais desta tentativa: nada sai)
        ou "falhou" (o prazo da entrega acabou: o job volta para a fila com a mesma seed). A pasta
        da entrega sai em qualquer fim."""
        job = e.job
        prazo = time.monotonic() + ENTREGA_MAX_S
        if self.cfg.ate:  # nunca depois do limite do pod (o desligamento automático vem depois)
            prazo = min(prazo, time.monotonic() + self.cfg.ate + ENTREGA_ALEM_ATE_S - time.time())
        urls = {k: job[k] for k in ("video_url", "folha_url") if job.get(k)}
        falhas, ultimo = 0, ""
        try:
            while True:
                if bat is not None and bat.perdida.is_set():
                    return "perdida"
                try:
                    if not e.enviado:
                        if not e.integra():  # o arquivo no disco mudou: não é mais o vídeo aprovado
                            return self._concluir(
                                job, "erro", erro="vídeo no pod corrompido", repetir=True
                            )
                        if not urls.get("video_url"):
                            urls = self.api.urls(job["job_id"], job.get("tentativa"))
                        media.upload(urls["video_url"], e.video, "video/mp4", attempts=2)
                        e.enviado = True
                        if urls.get("folha_url") and e.folha.is_file():
                            try:  # a folha ajuda o revisor; não segura a entrega
                                media.upload(urls["folha_url"], e.folha, "image/jpeg", attempts=1)
                            except (media.MediaError, OSError) as erro:
                                _log(f"{job['arquivo']}: folha de conferência não foi ({erro})")
                    if bat is not None:
                        bat.marcar("confirmacao")
                    return self._concluir(job, "ok", sha256=e.sha256, **e.campos)
                except Perdida as erro:
                    _log(f"{job['arquivo']}: {erro}; o vídeo desta tentativa não sai")
                    return "perdida"
                except media.UnsafeUrl as erro:  # host fora da lista: nunca vai dar certo
                    ultimo = str(erro)
                    break
                except (media.MediaError, FilaError, OSError) as erro:
                    falhas += 1
                    ultimo = str(erro)[:300]
                    if isinstance(erro, media.MediaError) and erro.status in (400, 401, 403):
                        urls = {}  # URL vencida ou recusada: URLs novas, a mesma tentativa
                    _log(f"AVISO: {job['arquivo']}: envio/confirmação falhou ({falhas}): {ultimo}")
                resta = prazo - time.monotonic()
                if resta <= 0:
                    break
                if bat is not None:  # o prazo do job acompanha as tentativas (progresso: cada uma)
                    bat.marcar("envio")
                    bat.renovar()
                time.sleep(min(resta, 2 ** min(falhas, 5)))
            # o prazo acabou: o vídeo estava certo; volta para a fila com a mesma seed (transporte)
            try:
                return self._concluir(
                    job,
                    "erro",
                    erro=f"envio do vídeo falhou: {ultimo}"[:500],
                    repetir=True,
                    transporte=True,
                )
            except FilaError as erro:  # API fora: o prazo do job o devolve à fila
                _log(f"AVISO: {job['arquivo']}: {erro}")
            return "falhou"
        finally:
            shutil.rmtree(e.pasta, ignore_errors=True)

    def retomar_entregas(self) -> int:
        """Na partida: os vídeos aprovados que um worker anterior deste pod não conseguiu confirmar
        (o processo caiu) vão de novo, se a tentativa ainda for deste worker (a API decide); os
        velhos demais saem sem envio. Devolve quantos foram retomados."""
        n = 0
        if not self.cfg.entregas.is_dir():
            return 0
        for d in sorted(self.cfg.entregas.iterdir()):
            try:
                m = json.loads((d / MANIFESTO).read_text())
            except (OSError, ValueError):
                shutil.rmtree(d, ignore_errors=True)  # sem manifesto: não há como entregar
                continue
            if time.time() - float(m.get("criada_em") or 0) > ENTREGA_VELHA_S:
                shutil.rmtree(d, ignore_errors=True)
                continue
            e = Entrega(d, dict(m["job"]), m["sha256"], int(m["bytes"]), dict(m["campos"]))
            _log(f"{e.job.get('arquivo')}: retomando a entrega de antes do reinício")
            status = self._entregar(e, Batimento(self.api, e.job))
            with self.lock:
                self.feitos += status == "ok"
            n += 1
        return n

    def _quarentena(self, job: dict, foto: Path, video: Path, pasta: Path, t: lote.Tarefa) -> bool:
        """Vídeo barrado e a folha dele para a quarentena (ADR 0005, 12/10/2026): só o dono vê,
        pelo painel, por 7 dias. Devolve se o vídeo foi; API antiga, sem os endereços: nada vai."""
        url = job.get("quarentena_video_url")
        if not url:
            return False
        if job.get("quarentena_folha_url"):
            try:
                folha = preparo.folha(foto, video, pasta / "folha.jpg", 16 * t.duracao + 1)
                media.upload(job["quarentena_folha_url"], folha, "image/jpeg")
            except Exception as e:  # noqa: BLE001 (a folha ajuda; o vídeo é o que importa)
                _log(f"{job['arquivo']}: folha da quarentena não enviada ({e})")
        try:
            media.upload(url, video, "video/mp4")
        except (media.MediaError, media.UnsafeUrl) as e:
            _log(f"{job['arquivo']}: o vídeo barrado não foi para a quarentena ({e})")
            return False
        return True

    def _aceito(self, job: dict) -> bool:
        """Carimbo de aprovação e modelo: o job que não passa termina aqui (erro, sem repetir)."""
        if not carimbo_ok(self.cfg.segredo, job):
            self._concluir(job, "erro", erro="carimbo de aprovação inválido")
            return False
        modelo = (job.get("pedido") or {}).get("modelo") or "wan2.2-i2v"
        if modelo not in MODELOS:
            erro = f"modelo {modelo} não disponível neste gerador"
            self._concluir(job, "erro", erro=erro)
            return False
        return True

    def processar(self, job: dict, comfy: str) -> bool:
        """Um job do começo ao fim (sem `adiantar`). False: o laço desta GPU para."""
        if not self._aceito(job):
            return True
        if job["tipo"] == "compilar":
            self.compilar(job)
            return True
        return self.renderizar(job, comfy)

    def aquecer(self, comfy: str) -> float | None:
        """Um vídeo pequeno (256×256, 3 s), de uma imagem cinza, descartado: o ComfyUI carrega os
        modelos agora, e o 1º pedido não paga a carga. Nada sai do pod; se falhar, nada muda (o 1º
        vídeo carrega os modelos, como antes). Devolve os segundos (None se falhou)."""
        t0 = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="render2go-aquecer-") as d:
                img = Path(d) / "cinza.png"
                Image.new("RGB", (AQUECER_LADO, AQUECER_LADO), (128, 128, 128)).save(img)
                lado = str(AQUECER_LADO)
                args = cli_submit.build_parser().parse_args(
                    ["--comfy", comfy, "--template", str(self.cfg.template), "--image", str(img),
                     "--prompt-en", AQUECER_PROMPT, "--width", lado, "--height", lado,
                     "--duration", str(AQUECER_DURACAO), "--seed", "0",
                     "--out", str(Path(d) / "aquecer.mp4"), "--timeout", "900"]
                )  # fmt: skip
                code, res = self.submit(args)
        except Exception as e:  # noqa: BLE001 (aquecer é só ganho de tempo)
            code, res = None, {"error": str(e)}
        if code != cli_submit.EXIT_OK:
            _log(f"AVISO: a GPU {comfy} não aqueceu ({str(res.get('error', code))[:200]})")
            return None
        s = time.monotonic() - t0
        _log(f"GPU {comfy} aquecida em {s:.1f} s: os modelos já estão na memória")
        return s

    # -- laço -----------------------------------------------------------------------------

    def _laco(self, comfy: str) -> None:
        motivo = self._rodar_laco(comfy)
        with self.lock:
            self.motivos.append(motivo)

    def motivo(self) -> str:
        """O motivo mais grave entre os laços (a ordem de MOTIVOS)."""
        return next((m for m in MOTIVOS if m in self.motivos), "ocioso")

    def _rodar_laco(self, comfy: str) -> str:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="fila") as fundo:
            lc = _Laco(comfy, fundo, aquecido=not self.cfg.aquecer)
            try:
                return self._girar(lc)
            finally:  # nada fica para trás: o adiantado volta à fila e o anterior termina de ir
                self._largar_adiantado(lc)
                self._esperar_envio(lc)

    def _girar(self, lc: _Laco) -> str:
        while True:
            if self.cfg.ate and time.time() > self.cfg.ate:
                _log("perto do limite de tempo do pod: não pega mais jobs")
                return "limite"
            if self.filtro_falhou:  # o classificador é um só para todas as GPUs
                return "filtro"
            modelos = self.estado_modelos()
            if modelos == "falhou":
                return "modelos"
            item = self._pegar(lc, modelos)  # modelos ainda baixando: só traduz pedidos
            if item is None:  # fila vazia
                if lc.envio is not None:  # o último vídeo ainda vai; se for refeito, volta à fila
                    self._esperar_envio(lc)
                    continue
                if modelos == "esperando":  # o tempo de pod quente só conta com os modelos prontos
                    time.sleep(ESPERA_MODELOS_S)
                    continue
                if not lc.aquecido:  # sem pedido: a GPU aquece enquanto espera
                    lc.aquecido = True
                    self.aquecer(lc.comfy)
                    continue
                with self.lock:
                    ocioso = time.monotonic() - self.ultimo_job
                if ocioso > self.cfg.ocioso_s:
                    return "ocioso"
                time.sleep(ESPERA_S)
                continue
            if item is FEITO:
                continue
            try:
                if isinstance(item, dict):  # tradução: o Ollama usa a GPU, nunca junto de um vídeo
                    self.compilar(item)
                elif not self._vez_da_gpu(lc, item):
                    return "gpu"
            except FilaError as e:  # a API caiu no meio: o prazo do job o devolve à fila
                _log(f"AVISO: {e}")
            self._marcar_atividade()

    def _vez_da_gpu(self, lc: _Laco, p: Preparado) -> bool:
        """Um vídeo na GPU. Com `adiantar`, o próximo é pego e preparado enquanto isso, e este é
        conferido e enviado em segundo plano. False: a GPU ficou inacessível."""
        if p.adiantado:  # pego durante o vídeo anterior: o pedido pode ter sido parado
            try:
                seguir = self.api.iniciar(p.job["job_id"], p.job.get("tentativa"))
            except FilaError as e:  # API fora: o prazo do pod devolve o job à fila
                _log(f"AVISO: {e}")
                seguir = False
            if seguir is not True and seguir:  # o prazo da tentativa recomeça quando a GPU começa
                p.prazo = time.monotonic() + float(seguir)
            if not seguir:
                shutil.rmtree(p.pasta, ignore_errors=True)
                _log(f"{p.job['arquivo']} (lote {p.job['lote']}): não vai para a GPU (parado)")
                return True
        # o próximo, só se houver tempo para ele antes do limite do pod
        if self.cfg.adiantar and (not self.cfg.ate or time.time() + lc.gpu_s < self.cfg.ate):
            lc.adiantado = lc.fundo.submit(self._proximo, "ok")
        inicio = time.monotonic()
        desde = max(lc.fim_gpu or p.t0, p.t0)  # a GPU livre (ou o job pego, se foi depois)
        p.etapas["espera_gpu"] = inicio - desde
        if lc.fim_gpu is not None:  # do fim do vídeo anterior até este (medições em GPU)
            p.etapas["gpu_parada"] = inicio - lc.fim_gpu
        r = self._gerar(p, lc.comfy)
        lc.fim_gpu = time.monotonic()
        lc.gpu_s, lc.aquecido = lc.fim_gpu - inicio, True
        self._marcar_atividade()
        if r == "gpu":
            _log(f"GPU {lc.comfy} inacessível; este laço para")
            return False
        if r != "ok":
            return True
        if not self.cfg.adiantar:
            self._finalizar(p)  # filtro que falhou para o laço na volta (filtro_falhou)
            return True
        p.ciclo_s = lc.fim_gpu - desde
        self._esperar_envio(lc)  # um vídeo em envio por vez (o anterior já foi, quase sempre)
        lc.envio = lc.fundo.submit(self._finalizar, p)
        return True

    def _proximo(self, modelos: str) -> Preparado | dict | str | None:
        """Pega o próximo job; se for um vídeo, já baixa e prepara a foto. None: fila vazia; FEITO:
        o job terminou antes da GPU; dict: tradução; Preparado: vídeo pronto para a GPU."""
        job = self.api.reivindicar(None if modelos == "ok" else ["compilar"])
        if job is None:
            return None
        self._marcar_atividade()
        if not self._aceito(job):
            return FEITO
        if job["tipo"] == "compilar":
            if job.get("legenda"):  # a legenda corre aqui, durante o vídeo atual na GPU (ADR 0014)
                job["_legenda"] = self._legendar(job)
            return job
        return self._preparar(job) or FEITO

    def _pegar(self, lc: _Laco, modelos: str) -> Preparado | dict | str | None:
        """O próximo job: o adiantado (pego durante o vídeo anterior) ou, sem ele, um pego agora."""
        if lc.adiantado is not None:
            fut, lc.adiantado = lc.adiantado, None
            try:
                item = fut.result()
            except FilaError as e:
                _log(f"AVISO: {e}")
                item = None
            if isinstance(item, Preparado):
                item.adiantado = True
            if item is not None:
                return item
        try:
            return self._proximo(modelos)
        except FilaError as e:
            _log(f"AVISO: {e}")
            return None

    def _esperar_envio(self, lc: _Laco) -> None:
        """Espera o vídeo anterior terminar de ser conferido e enviado."""
        if lc.envio is None:
            return
        fut, lc.envio = lc.envio, None
        try:
            fut.result()  # False (o filtro falhou) fica em self.filtro_falhou
        except FilaError as e:  # a API caiu no meio: o prazo do job o devolve à fila
            _log(f"AVISO: {e}")
        except Exception as e:  # noqa: BLE001 (um erro no envio não derruba o laço da GPU; o prazo devolve o job)
            _log(f"AVISO: envio do vídeo anterior: {type(e).__name__}: {e}")
        self._marcar_atividade()

    def _largar_adiantado(self, lc: _Laco) -> None:
        """O job pego adiantado que não vai mais para a GPU volta para a fila, sem contar tentativa
        (se a API não responder, o prazo do pod o devolve)."""
        if lc.adiantado is None:
            return
        fut, lc.adiantado = lc.adiantado, None
        try:
            item = fut.result()
            if isinstance(item, Preparado):
                shutil.rmtree(item.pasta, ignore_errors=True)
                item = item.job
            if isinstance(item, dict):
                self.api.devolver(item["job_id"], item.get("tentativa"))
                _log(f"job {item['job_id']} devolvido à fila (não foi para a GPU)")
        except Exception as e:  # noqa: BLE001 (limpeza na saída do laço: o resto ainda precisa rodar)
            _log(f"AVISO: o job adiantado não foi devolvido ({e}); o prazo do pod o devolve")

    def _marcar_atividade(self) -> None:
        with self.lock:
            self.ultimo_job = time.monotonic()

    def rodar(self) -> int:
        _log(
            f"{len(self.cfg.comfy)} GPU(s); filtros {self.filtros.mode}; sai após "
            f"{self.cfg.ocioso_s:.0f} s sem jobs; adiantar: {self.cfg.adiantar}; "
            f"aquecer: {self.cfg.aquecer}"
        )
        if self.modelos_ok:  # sem o estado da instalação: os modelos já estão no disco
            self._precarregar_filtro()
        # vídeos aprovados de um worker anterior deste pod (o processo caiu antes da confirmação)
        retomada = threading.Thread(target=self.retomar_entregas, daemon=True, name="entregas")
        retomada.start()
        ts = [threading.Thread(target=self._laco, args=(c,), daemon=True) for c in self.cfg.comfy]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        retomada.join(ENTREGA_MAX_S + 60)  # a drenagem espera as entregas, com prazo
        with contextlib.suppress(OSError):  # nada pendente: a pasta das entregas sai (só se vazia)
            self.cfg.entregas.rmdir()
        _log(f"fim: {self.feitos} vídeo(s) ok, {self.falhas} com falha; {MOTIVOS[self.motivo()]}")
        if self.cfg.motivo_em:
            try:
                self.cfg.motivo_em.write_text(self.motivo() + "\n")
            except OSError as e:
                _log(f"AVISO: motivo da saída não gravado: {e}")
        return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="render2go-fila", description=__doc__.split("\n")[0])
    p.add_argument("--api", required=True, help="URL da API (https)")
    p.add_argument("--worker", required=True, help="nome deste worker (ex.: pod-<id>)")
    p.add_argument("--gpus", type=int, default=1, help="ComfyUI em 8188, 8189, ...")
    p.add_argument("--ollama", default="http://127.0.0.1:11434")
    p.add_argument("--ocioso", type=float, default=OCIOSO_S, help="sai após N s sem jobs")
    p.add_argument("--ate", type=float, help="epoch: não pega jobs novos depois disso")
    p.add_argument(
        "--modelos-prontos",
        type=Path,
        help="estado da instalação (bootstrap_pod.sh): até PRONTO, só traduz pedidos",
    )
    p.add_argument("--modelos-ate", type=float, help="epoch: sem PRONTO até aqui, o worker sai")
    p.add_argument("--motivo", type=Path, help="arquivo onde o worker escreve por que saiu")
    p.add_argument(
        "--sem-adiantar",
        action="store_true",
        help="um vídeo de cada vez (sem preparar o próximo e enviar o anterior durante a geração)",
    )
    p.add_argument(
        "--sem-aquecer", action="store_true", help="não aquece a GPU quando não há pedido"
    )
    a = p.parse_args(argv)
    token = os.environ.get("RENDER2GO_WORKER_TOKEN", "")
    if len(token) < 32:
        p.error("RENDER2GO_WORKER_TOKEN ausente ou curto")
    try:
        if safety.load_filters().mode != "c1-revisao-humana":
            p.error("a fila exige RENDER2GO_SAFETY=c1-revisao-humana (ADR 0004)")
    except safety.SafetyNotConfigured as e:
        p.error(str(e))
    cfg = Config(
        comfy=[f"http://127.0.0.1:{8188 + i}" for i in range(a.gpus)],
        ollama=a.ollama,
        ocioso_s=a.ocioso,
        segredo=os.environ.get("RENDER2GO_SEGREDO_APROVACAO") or None,
        ate=a.ate,
        modelos=a.modelos_prontos,
        modelos_ate=a.modelos_ate,
        motivo_em=a.motivo,
        adiantar=not a.sem_adiantar,
        aquecer=not a.sem_aquecer,
        # legenda por foto (ADR 0014): o orquestrador põe a chave no fila.env com o Ollama de CPU
        legenda=os.environ.get("RENDER2GO_LEGENDA_POR_FOTO", "").strip() == "1",
    )
    return WorkerFila(ApiFila(a.api, token, a.worker), cfg).rodar()


if __name__ == "__main__":
    sys.exit(main())
