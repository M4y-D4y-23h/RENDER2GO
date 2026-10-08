"""Orquestrador de pods da fila (ADR 0003, fase 3): lê a fila, sobe e encerra pods.

A cada ciclo (30 s):
1. lê o trabalho pendente no banco (renders por lote, com qualidade e duração);
2. decide quantos pods a fila pede, com a mesma regra do `lote_nuvem --pods auto`:
   - cada pod com ≥ 10 min de vídeos;
   - ou, com GPU2GO_PRAZO_MIN, o mínimo de pods para entregar no prazo;
   - até GPU2GO_MAX_PODS e GPU2GO_MAX_USD_H;
3. sobe os pods que faltam com a instalação do `lote_nuvem` (CUDA, teste de rede, memória de
   datacenter, download em partes, espelho R2) e inicia neles o worker da fila
   (`gpu2go_worker.fila`);
4. encerra os pods cujo worker terminou (fila vazia por 3 min = "pod quente" vencido).

Testes gratuitos em grupo (docs/08): com só testes na fila e nenhum pod ligado, o pod espera
juntar GPU2GO_AGRUPAR_TESTE_VIDEOS vídeos (padrão 20) ou o teste mais antigo esperar
GPU2GO_AGRUPAR_TESTE_MIN minutos (padrão 10). Cada subida custa ~US$ 0,20 (subida + espera do
pod quente) e um vídeo de teste ~US$ 0,015: dez testes de 1 foto em horários diferentes
pagariam dez subidas; juntos, pagam uma. Pedidos pagos sobem na hora.

Proteções:
- cada pod nasce com desligamento automático (≤ 2 h e o que o saldo paga);
- o worker para de pegar jobs 10 min antes;
- o estado fica em disco para `gpu2go-servico limpar`.

O pod recebe só a URL da API, o token de worker e os hosts permitidos (nunca as chaves do R2
ou do RunPod).
"""

from __future__ import annotations

import json
import os
import secrets
import shlex
import threading
import time
from argparse import Namespace
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from gpu2go_worker import lote, runpod_pods
from gpu2go_worker import lote_nuvem as ln

from gpu2go_servico.db import Banco

FILA_CMD = (
    "set -a; . {raiz}/fila.env; set +a; cd /opt/gpu2go/worker && "
    "PATH=/opt/venv/bin:$PATH PYTHONPATH=/opt/gpu2go/worker/src "
    "python -m gpu2go_worker.fila --api {api} --worker {worker} --gpus {gpus} --ate {ate}"
)
VIVO = "vivo"  # tocado a cada ciclo bem-sucedido (healthcheck do contêiner)


def _log(msg: str) -> None:
    ln._log(f"[orquestrador] {msg}")


@dataclass
class ConfigOrq:
    api_url: str  # URL pública da API (https), que os pods acessam
    worker_token: str
    hosts_permitidos: str  # GPU2GO_ALLOWED_URL_HOSTS no pod (ex.: o host do R2)
    gpus: list[str] = field(default_factory=lambda: list(ln.GPUS_PADRAO))
    max_pods: int = ln.MAX_PODS_AUTO
    max_usd_h: float = 20.0
    prazo_min: int | None = None
    intervalo_s: float = 30
    estado: Path = ln.CACHE / "orquestrador"
    segredo_aprovacao: str | None = None
    fila_cmd: str = FILA_CMD
    agrupar_teste_s: float = 10 * 60
    agrupar_teste_videos: int = 20

    @classmethod
    def do_ambiente(cls) -> ConfigOrq:
        api = os.environ.get("GPU2GO_API_URL", "")
        token = next((t for t in os.environ.get("GPU2GO_WORKER_TOKENS", "").split(",") if len(t) >= 32), "")
        if not api.startswith("https://") or not token:
            raise SystemExit("faltam GPU2GO_API_URL (https) ou GPU2GO_WORKER_TOKENS")
        r2 = urlsplit(os.environ.get("GPU2GO_R2_ENDPOINT", "")).hostname
        if not r2:
            raise SystemExit("falta GPU2GO_R2_ENDPOINT (o pod só baixa e envia para esse host)")
        prazo = os.environ.get("GPU2GO_PRAZO_MIN")
        return cls(
            api_url=api.rstrip("/"),
            worker_token=token,
            hosts_permitidos=r2,
            max_pods=int(os.environ.get("GPU2GO_MAX_PODS", ln.MAX_PODS_AUTO)),
            max_usd_h=float(os.environ.get("GPU2GO_MAX_USD_H", 20)),
            prazo_min=int(prazo) if prazo else None,
            segredo_aprovacao=os.environ.get("GPU2GO_SEGREDO_APROVACAO") or None,
            agrupar_teste_s=float(os.environ.get("GPU2GO_AGRUPAR_TESTE_MIN", 10)) * 60,
            agrupar_teste_videos=int(os.environ.get("GPU2GO_AGRUPAR_TESTE_VIDEOS", 20)),
        )


@dataclass
class PodFila:
    pod: ln.Pod
    ate: float  # epoch em que o worker para de pegar jobs


class Orquestrador:
    def __init__(
        self,
        banco: Banco,
        cfg: ConfigOrq,
        provedor: ln.Provedor | None = None,
        remoto: ln.Remoto | None = None,
        repo_tgz: Path | None = None,
        espelho=None,
    ):
        self.banco, self.cfg = banco, cfg
        cfg.estado.mkdir(parents=True, exist_ok=True)
        self.provedor = provedor or ln.RunPod()
        a = Namespace(
            opcoes=[runpod_pods.Opcao(g, "secure") for g in cfg.gpus],  # só secure (regra do projeto)
            gpus_por_pod=1,
            comfy_por_gpu=1,
            volume=None,
            espelho=espelho,
            repo_tgz=repo_tgz or ln.empacotar_repo(cfg.estado / ln.ESTADO / "repo.tgz"),
            saida=cfg.estado,
            qualidade="rapida",
            orcamento_usd=cfg.max_usd_h * 2,
        )
        self.pods_ln = ln.Orquestrador(a, [], self.provedor, remoto or ln.Remoto())
        self.remoto = self.pods_ln.remoto
        self.ativos: dict[str, PodFila] = {}
        self.subindo = 0
        self.lock = threading.Lock()
        self.parar = threading.Event()
        self.n = 0
        self._segurando: int | None = None

    # -- decisão --------------------------------------------------------------------------

    def segurar_testes(self, trabalho: list[dict], existentes: int) -> bool:
        """Só testes na fila e nenhum pod ligado: espera juntar mais antes de pagar uma subida."""
        if existentes or not trabalho or any(t["prioridade"] == 0 for t in trabalho):
            self._segurando = None
            return False
        videos = sum(int(t["pendentes"] or 0) + int(t["em_andamento"] or 0) for t in trabalho)
        espera = time.time() - min(float(t["mais_antigo"]) for t in trabalho)
        if videos >= self.cfg.agrupar_teste_videos or espera >= self.cfg.agrupar_teste_s:
            self._segurando = None
            return False
        if self._segurando != videos:  # registra só quando muda
            self._segurando = videos
            falta = (self.cfg.agrupar_teste_s - espera) / 60
            alvo = self.cfg.agrupar_teste_videos
            _log(f"juntando testes gratuitos: {videos} vídeo(s); sobe com {alvo} ou em {falta:.0f} min")
        return True

    def desejados(self, trabalho: list[dict]) -> int:
        """Pods que a fila pede agora (0 sem trabalho)."""
        gpu = self.cfg.gpus[0]
        receitas = lote.load_receitas()
        segundos, videos = 0.0, 0
        for t in trabalho:
            p = json.loads(t["pedido"]) if isinstance(t["pedido"], str) else t["pedido"]
            n = int(t["pendentes"] or 0) + int(t["em_andamento"] or 0)
            dur = p.get("duracao") or receitas["receitas"][p.get("receita", "produto-close")]["duracao"]
            segundos += n * ln.segundos_por_video(gpu, p.get("qualidade", "rapida"), dur)
            videos += n
        if not videos:
            return 0
        s_medio = segundos / videos
        pods, _ = ln.escolher_pods(videos, videos, 1, s_medio, self.cfg.prazo_min)
        return min(pods, self.cfg.max_pods)

    def _preco(self) -> float:
        for o in self.pods_ln.a.opcoes:
            if (x := self.provedor.preco(o.gpu, o.nuvem)) is not None:
                return x
        return 2.09

    def _deadman(self, usd_h: float) -> int | None:
        """Prazo do desligamento automático do próximo pod; None se o saldo não paga 15 min."""
        limite = ln.MAX_POD_S
        saldo = self.provedor.saldo() if hasattr(self.provedor, "saldo") else None
        if saldo is not None:
            disponivel = saldo - max(ln.RESERVA_SALDO_USD, 0.1 * saldo)
            disponivel -= sum(p.pod.info.usd_h for p in self.ativos.values()) * 2  # já comprometido
            limite = min(limite, max(0.0, disponivel) / usd_h * 3600)
        return int(limite) if limite >= ln.MIN_POD_S else None

    # -- pods -----------------------------------------------------------------------------

    def _iniciar_worker(self, pod: ln.Pod, deadman_s: int) -> PodFila:
        r, ag = self.remoto, pod.agente
        env = self.cfg.estado / f"fila-{pod.info.id}.env"
        linhas = {
            "GPU2GO_WORKER_TOKEN": self.cfg.worker_token,
            "GPU2GO_SAFETY": "c1-revisao-humana",
            "GPU2GO_ALLOWED_URL_HOSTS": self.cfg.hosts_permitidos,
        }
        if self.cfg.segredo_aprovacao:
            linhas["GPU2GO_SEGREDO_APROVACAO"] = self.cfg.segredo_aprovacao
        env.write_text("".join(f"{k}={shlex.quote(v)}\n" for k, v in linhas.items()))
        env.chmod(0o600)
        try:
            ag.put(env, f"{r.raiz}/fila.env")
        finally:
            env.unlink(missing_ok=True)
        ate = time.time() + deadman_s - ln.MARGEM_S - (time.monotonic() - pod.criado)
        cmd = self.cfg.fila_cmd.format(
            raiz=r.raiz,
            api=shlex.quote(self.cfg.api_url),
            worker=f"pod-{pod.info.id}",
            gpus=pod.info.gpus,
            ate=int(ate),
        )
        ag.bg(f"{cmd}; echo FIM > {r.raiz}/fila.fim", f"{r.raiz}/fila.log")
        _log(f"pod {pod.nome}: worker da fila iniciado")
        return PodFila(pod, ate)

    def _subir(self, deadman_s: int) -> None:
        try:
            with self.lock:
                self.n += 1
                i = self.n
            pod = self.pods_ln.subir_pod(i, deadman_s)
            if pod is None:
                return
            try:
                pf = self._iniciar_worker(pod, deadman_s)
            except (ln.NuvemError, OSError) as e:
                _log(f"pod {pod.nome}: worker não iniciou ({e})")
                self.pods_ln.encerrar(pod, "worker não iniciou")
                return
            with self.lock:
                self.ativos[pod.info.id] = pf
        finally:
            with self.lock:
                self.subindo -= 1

    def _revisar(self) -> None:
        """Encerra pods cujo worker terminou ou cujo agente sumiu."""
        for pid, pf in list(self.ativos.items()):
            try:
                _, out = pf.pod.agente.exec(f"cat {self.remoto.raiz}/fila.fim 2>/dev/null", 30)
                fim = "FIM" in out
            except (ln.NuvemError, OSError) as e:
                _log(f"pod {pf.pod.nome}: agente inacessível ({e})")
                fim = True
            if fim:
                self.pods_ln.encerrar(pf.pod, "fila vazia")
                with self.lock:
                    self.ativos.pop(pid, None)

    def ciclo(self) -> dict:
        self._revisar()
        trabalho = self.banco.trabalho()
        with self.lock:
            existentes = len(self.ativos) + self.subindo
        querem = 0 if self.segurar_testes(trabalho, existentes) else self.desejados(trabalho)
        faltam = max(0, querem - existentes)
        subiu = 0
        if not faltam:  # fila vazia ou já atendida: sem consulta de preço a cada 30 s
            return {"desejados": querem, "ativos": len(self.ativos), "subindo": self.subindo}
        usd_h = self._preco()
        gasto_h = sum(p.pod.info.usd_h for p in self.ativos.values()) + self.subindo * usd_h
        for _ in range(faltam):
            if gasto_h + usd_h > self.cfg.max_usd_h:
                _log(f"teto de US$ {self.cfg.max_usd_h:.2f}/h: não sobe mais pods agora")
                break
            deadman = self._deadman(usd_h)
            if deadman is None:
                _log("saldo da conta não paga mais um pod: não sobe")
                break
            with self.lock:
                self.subindo += 1
            gasto_h += usd_h
            subiu += 1
            threading.Thread(target=self._subir, args=(deadman,), daemon=True).start()
        if subiu:
            _log(f"fila pede {querem} pod(s); subindo {subiu}")
        return {"desejados": querem, "ativos": len(self.ativos), "subindo": self.subindo}

    def rodar(self) -> None:
        _log(f"rodando; até {self.cfg.max_pods} pods e US$ {self.cfg.max_usd_h:.2f}/h")
        try:
            while not self.parar.is_set():
                try:
                    self.ciclo()
                    (self.cfg.estado / VIVO).touch()  # o healthcheck do compose confere a idade deste arquivo
                except Exception as e:  # noqa: BLE001 (o laço não pode morrer; os pods têm desligamento próprio)
                    _log(f"AVISO: ciclo falhou: {e}")
                self.parar.wait(self.cfg.intervalo_s)
        finally:
            self.encerrar_todos("orquestrador parou")

    def encerrar_todos(self, motivo: str) -> None:
        for pf in list(self.ativos.values()):
            self.pods_ln.encerrar(pf.pod, motivo)
        self.ativos.clear()


def novo_token() -> str:
    return secrets.token_urlsafe(32)
