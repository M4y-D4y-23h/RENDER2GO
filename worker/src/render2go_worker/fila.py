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

Um laço por ComfyUI (uma GPU cada). Sem jobs por `--ocioso` segundos (o "pod quente", docs/08),
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
import dataclasses
import hashlib
import hmac
import os
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests

from render2go_worker import (
    cli_submit,
    compiler,
    conferencia,
    filtro_imagem,
    lote,
    media,
    preparo,
    presets,
    safety,
)

MAX_BYTES_FOTO = 50 * 1024**2
# modelos de geração que este worker sabe rodar (render2go_servico/modelos.py lista os oferecidos);
# pedido de outro modelo é recusado sem repetir: fail-closed
MODELOS = {"wan2.2-i2v"}
ESPERA_S = 5  # fila vazia: tempo entre consultas
ESPERA_MODELOS_S = 2  # modelos baixando: confere o estado de novo logo
OCIOSO_S = 180  # ~ o tempo de uma subida de pod: depois disso, sair custa menos que esperar
# por que o worker saiu (o mais grave entre os laços vale para o pod)
MOTIVOS = {
    "modelos": "a instalação dos modelos falhou ou passou do prazo",
    "filtro": "o filtro de imagem não carregou (nenhum vídeo sai sem ele)",
    "gpu": "a GPU ficou inacessível",
    "limite": "limite de tempo do pod (2 h ou o que o saldo do RunPod paga)",
    "ocioso": "sem pedidos (pod quente vencido)",
}
TIPOS = {"jpg": ".jpg", "png": ".png", "webp": ".webp"}


class FilaError(Exception):
    pass


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

    def prompt(self, job_id: str, prompt_en: str, compilador: str) -> None:
        r = self._post(
            f"/api/worker/jobs/{job_id}/prompt",
            {"worker": self.worker, "prompt_en": prompt_en, "compilador": compilador},
        )
        if r.status_code != 200:
            raise FilaError(f"prompt recusado: HTTP {r.status_code} {r.text[:200]}")

    def concluir(self, job_id: str, status: str, **campos: Any) -> str:
        corpo = {"worker": self.worker, "status": status, **campos}
        r = self._post(f"/api/worker/jobs/{job_id}/concluir", corpo)
        if r.status_code != 200:
            raise FilaError(f"concluir: HTTP {r.status_code} {r.text[:200]}")
        return r.json()["status"]


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
        pedido = job["pedido"]
        d = self.filtros.check_request(pedido["prompt"])
        if not d.allowed:
            self.api.concluir(job["job_id"], "erro", erro=f"filtro: {d.reason}")
            return
        r = self.receitas["receitas"][pedido["receita"]]
        composed = presets.compose("i2v", pedido["prompt"], r["presets"])
        try:
            comp = self.compile_fn(
                composed.text,
                profile="i2v",
                ollama_url=self.cfg.ollama,
                model=self.cfg.model,
                keep_alive=0,  # libera a VRAM antes dos renders
                suffix=composed.suffix,
                camera_preset=composed.camera,
            )
        except compiler.CompilerRefused as e:
            self.api.concluir(job["job_id"], "erro", erro=f"compilador recusou: {e}")
            return
        except compiler.CompilerError as e:
            self.api.concluir(job["job_id"], "erro", erro=str(e)[:500], repetir=True)
            return
        d = self.filtros.check_prompt(comp.prompt)
        if not d.allowed:
            self.api.concluir(job["job_id"], "erro", erro=f"filtro no prompt: {d.reason}")
            return
        self.api.prompt(job["job_id"], comp.prompt, comp.ref)  # a API passa o C1 de novo
        self.api.concluir(job["job_id"], "ok")
        _log(f"lote {job['lote']}: prompt compilado ({comp.word_count} palavras)")

    def _tarefa(self, job: dict, foto: Path) -> lote.Tarefa:
        p = job["pedido"]
        nome = Path(job["arquivo"]).stem.rsplit("-v", 1)[0]
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
        t = next((t for t in tarefas if t.arquivo == job["arquivo"]), None)
        if t is None:
            raise FilaError(f"arquivo {job['arquivo']} fora do pedido")
        return dataclasses.replace(t, seed=int(job["seed"]))

    def renderizar(self, job: dict, comfy: str) -> bool:
        """Devolve False se a GPU ficou inacessível (o laço dela para)."""
        jid, t0 = job["job_id"], time.monotonic()
        with tempfile.TemporaryDirectory(prefix="render2go-job-") as d:
            pasta = Path(d)
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
                repetir = isinstance(e, media.MediaError) and getattr(e, "retryable", False)
                self.api.concluir(jid, "erro", erro=str(e)[:500], repetir=repetir)
                return True
            video = pasta / "video.mp4"
            args = lote._args(t, job["prompt_en"], video, comfy, self.cfg, prep.path)
            code, res = self.submit(args)
            if code == cli_submit.EXIT_UNREACHABLE:
                self.api.concluir(
                    jid, "erro", erro=f"GPU inacessível: {res.get('error')}", repetir=True
                )
                return False
            if code != cli_submit.EXIT_OK or not video.is_file():
                repetir = code in (cli_submit.EXIT_COMFY,)
                self.api.concluir(
                    jid, "erro", erro=str(res.get("error", code))[:500], repetir=repetir
                )
                return True
            try:  # o vídeo é dessa foto e continua nela até o fim? só envia se for
                sim = conferencia.confere(prep.path, video)
                cont = conferencia.continuidade(video)
            except conferencia.ConferenciaError as e:
                self.api.concluir(jid, "erro", erro=str(e)[:500], repetir=True)
                return True
            problema = conferencia.problema_continuidade(**cont)
            if problema:  # refeito com outra seed (a API troca a seed ao devolver o job)
                self.api.concluir(jid, "erro", erro=problema, repetir=True, **cont)
                _log(f"{job['arquivo']} (lote {job['lote']}): {problema}; refazendo")
                return True
            try:  # 2ª porta (ADR 0005): nudez em algum quadro = o vídeo não sai do pod
                filtro = filtro_imagem.avaliar(video, self.classificador)
            except filtro_imagem.FiltroError as e:
                self.api.concluir(jid, "erro", erro=f"filtro de imagem: {e}"[:500], repetir=True)
                _log(f"{job['arquivo']}: filtro de imagem indisponível ({e}); este laço para")
                self.filtro_falhou = True
                return False
            if filtro.barrado:  # não vai ao cliente nem é refeito; vai à quarentena (ADR 0005)
                self.api.concluir(
                    jid,
                    "erro",
                    erro=f"barrado pelo filtro de imagem (nota {filtro.nota:.2f})",
                    barrado=True,
                    quarentena=self._quarentena(job, prep.path, video, pasta, t),
                    nsfw=filtro.nota,
                    nsfw_quadros=filtro.quadros,
                    **cont,
                )
                with self.lock:
                    self.falhas += 1
                _log(
                    f"{job['arquivo']} (lote {job['lote']}): barrado pelo filtro de imagem "
                    f"(nota {filtro.nota:.2f} no quadro {filtro.pior_quadro})"
                )
                return True
            aviso = prep.aviso
            try:
                folha = preparo.folha(prep.path, video, pasta / "folha.jpg", 16 * t.duracao + 1)
                media.upload(job["folha_url"], folha, "image/jpeg")
            except Exception as e:  # noqa: BLE001 (a folha é ajuda ao revisor, não bloqueia)
                aviso = f"{aviso}; sem folha de conferência ({e})".strip("; ")
            try:
                media.upload(job["video_url"], video, "video/mp4")
            except (media.MediaError, media.UnsafeUrl) as e:
                self.api.concluir(jid, "erro", erro=str(e)[:500], repetir=True)
                return True
            status = self.api.concluir(
                jid,
                "ok",
                segundos=round(time.monotonic() - t0, 1),
                similaridade=round(sim, 4),
                bytes_video=video.stat().st_size,
                aviso=aviso or None,
                nsfw=filtro.nota,
                nsfw_quadros=filtro.quadros,
                **cont,
            )
        with self.lock:
            self.feitos += status == "ok"
            self.falhas += status != "ok"
        _log(
            f"{job['arquivo']} (lote {job['lote']}): {status}, similaridade {sim:.3f}, "
            f"{time.monotonic() - t0:.0f} s"
        )
        return True

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

    def processar(self, job: dict, comfy: str) -> bool:
        if not carimbo_ok(self.cfg.segredo, job):
            self.api.concluir(job["job_id"], "erro", erro="carimbo de aprovação inválido")
            return True
        modelo = (job.get("pedido") or {}).get("modelo") or "wan2.2-i2v"
        if modelo not in MODELOS:
            erro = f"modelo {modelo} não disponível neste gerador"
            self.api.concluir(job["job_id"], "erro", erro=erro)
            return True
        if job["tipo"] == "compilar":
            self.compilar(job)
            return True
        return self.renderizar(job, comfy)

    # -- laço -----------------------------------------------------------------------------

    def _laco(self, comfy: str) -> None:
        motivo = self._rodar_laco(comfy)
        with self.lock:
            self.motivos.append(motivo)

    def motivo(self) -> str:
        """O motivo mais grave entre os laços (a ordem de MOTIVOS)."""
        return next((m for m in MOTIVOS if m in self.motivos), "ocioso")

    def _rodar_laco(self, comfy: str) -> str:
        while True:
            if self.cfg.ate and time.time() > self.cfg.ate:
                _log("perto do limite de tempo do pod: não pega mais jobs")
                return "limite"
            modelos = self.estado_modelos()
            if modelos == "falhou":
                return "modelos"
            try:  # modelos ainda baixando: só traduz pedidos
                job = self.api.reivindicar(None if modelos == "ok" else ["compilar"])
            except FilaError as e:
                _log(f"AVISO: {e}")
                job = None
            if job is None:
                if modelos == "esperando":  # o tempo de pod quente só conta com os modelos prontos
                    time.sleep(ESPERA_MODELOS_S)
                    continue
                with self.lock:
                    ocioso = time.monotonic() - self.ultimo_job
                if ocioso > self.cfg.ocioso_s:
                    return "ocioso"
                time.sleep(ESPERA_S)
                continue
            with self.lock:
                self.ultimo_job = time.monotonic()
            try:
                if not self.processar(job, comfy):
                    if self.filtro_falhou:
                        return "filtro"
                    _log(f"GPU {comfy} inacessível; este laço para")
                    return "gpu"
            except FilaError as e:  # a API caiu no meio: o prazo do job o devolve à fila
                _log(f"AVISO: {e}")
            with self.lock:
                self.ultimo_job = time.monotonic()

    def rodar(self) -> int:
        _log(
            f"{len(self.cfg.comfy)} GPU(s); filtros {self.filtros.mode}; sai após "
            f"{self.cfg.ocioso_s:.0f} s sem jobs"
        )
        if self.modelos_ok:  # sem o estado da instalação: os modelos já estão no disco
            self._precarregar_filtro()
        ts = [threading.Thread(target=self._laco, args=(c,), daemon=True) for c in self.cfg.comfy]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
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
    )
    return WorkerFila(ApiFila(a.api, token, a.worker), cfg).rodar()


if __name__ == "__main__":
    sys.exit(main())
