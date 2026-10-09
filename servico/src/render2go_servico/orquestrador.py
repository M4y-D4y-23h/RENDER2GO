"""Orquestrador de pods da fila (ADR 0003, fase 3): lê a fila, sobe e encerra pods.

A cada ciclo (RENDER2GO_CICLO_S, padrão 10 s):
1. lê o trabalho pendente no banco (renders por lote, com qualidade e duração);
2. decide quantos pods a fila pede, com a mesma regra do `lote_nuvem --pods auto`:
   - cada pod com ≥ 10 min de vídeos;
   - ou, com RENDER2GO_PRAZO_MIN, o mínimo de pods para entregar no prazo;
   - até RENDER2GO_MAX_PODS e RENDER2GO_MAX_USD_H;
3. sobe os pods que faltam com a instalação do `lote_nuvem` (CUDA, teste de rede, memória de
   datacenter, download em partes, espelho R2) e inicia neles o worker da fila
   (`render2go_worker.fila`). As GPUs vêm de RENDER2GO_GPUS, em ordem de preferência (padrão:
   `lote_nuvem.GPUS_PADRAO`). Os serviços e o worker sobem assim que o ambiente fica pronto, e o
   pedido é traduzido enquanto os modelos de vídeo terminam de baixar (`subir_pod(cedo=True)`);
4. encerra os pods cujo worker terminou (fila vazia por RENDER2GO_POD_OCIOSO_MIN, padrão 3 min =
   "pod quente" vencido).

Cada pod fica registrado na tabela `geradores` (subindo, pronto, encerrado): a API usa esse estado
e as subidas medidas para a previsão mostrada ao cliente, e o /aprovar mostra o relatório de cada
pod (id no RunPod, GPU, preço, datacenter, horários, vídeos e custo estimado). O id e o preço são
gravados assim que o RunPod cria o pod, mesmo que a instalação falhe depois; o desligamento, quando
o pod é de fato encerrado (o gravador pode levar minutos enviando ao espelho depois do fim da fila).

Testes gratuitos em grupo (docs/08): com só testes na fila e nenhum pod ligado, o pod espera
juntar RENDER2GO_AGRUPAR_TESTE_VIDEOS vídeos (padrão 20) ou o teste mais antigo esperar
RENDER2GO_AGRUPAR_TESTE_MIN minutos (padrão 10). Cada subida custa ~US$ 0,20 (subida + espera do
pod quente) e um vídeo de teste ~US$ 0,015: dez testes de 1 foto em horários diferentes
pagariam dez subidas; juntos, pagam uma. Pedidos pagos sobem na hora.

Proteções:
- cada pod nasce com desligamento automático (≤ 2 h e o que o saldo paga);
- o worker para de pegar jobs 10 min antes;
- o estado fica em disco para `render2go-servico limpar`.

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

from render2go_worker import lote, runpod_pods
from render2go_worker import lote_nuvem as ln

from render2go_servico.db import Banco

FILA_CMD = (
    "set -a; . {raiz}/fila.env; set +a; cd /opt/render2go/worker && "
    "PATH=/opt/venv/bin:$PATH PYTHONPATH=/opt/render2go/worker/src "
    "python -m render2go_worker.fila --api {api} --worker {worker} --gpus {gpus} --ate {ate} "
    "--ocioso {ocioso} --modelos-prontos {raiz}/estado --modelos-ate {modelos_ate}"
)
VIVO = "vivo"  # tocado a cada ciclo bem-sucedido (healthcheck do contêiner)


def _gpus_do_ambiente() -> list[str]:
    """RENDER2GO_GPUS: ids do RunPod separados por vírgula, em ordem de preferência (padrão:
    lote_nuvem.GPUS_PADRAO). Trocar a GPU = editar o .env e reiniciar o orquestrador."""
    gpus = [g.strip() for g in os.environ.get("RENDER2GO_GPUS", "").split(",") if g.strip()]
    return gpus or list(ln.GPUS_PADRAO)


def _log(msg: str) -> None:
    ln._log(f"[orquestrador] {msg}")


@dataclass
class ConfigOrq:
    api_url: str  # URL pública da API (https), que os pods acessam
    worker_token: str
    hosts_permitidos: str  # RENDER2GO_ALLOWED_URL_HOSTS no pod (ex.: o host do R2)
    gpus: list[str] = field(default_factory=lambda: list(ln.GPUS_PADRAO))
    max_pods: int = ln.MAX_PODS_AUTO
    max_usd_h: float = 20.0
    prazo_min: int | None = None
    intervalo_s: float = 10  # até 08/10/2026 eram 30 s: o pedido aprovado esperava até 30 s pelo ciclo
    estado: Path = ln.CACHE / "orquestrador"
    segredo_aprovacao: str | None = None
    fila_cmd: str = FILA_CMD
    agrupar_teste_s: float = 10 * 60
    agrupar_teste_videos: int = 20
    ocioso_s: float = 180  # pod quente: sem jobs por esse tempo, o worker sai e o pod é encerrado

    @classmethod
    def do_ambiente(cls) -> ConfigOrq:
        api = os.environ.get("RENDER2GO_API_URL", "")
        token = next((t for t in os.environ.get("RENDER2GO_WORKER_TOKENS", "").split(",") if len(t) >= 32), "")
        if not api.startswith("https://") or not token:
            raise SystemExit("faltam RENDER2GO_API_URL (https) ou RENDER2GO_WORKER_TOKENS")
        r2 = urlsplit(os.environ.get("RENDER2GO_R2_ENDPOINT", "")).hostname
        if not r2:
            raise SystemExit("falta RENDER2GO_R2_ENDPOINT (o pod só baixa e envia para esse host)")
        prazo = os.environ.get("RENDER2GO_PRAZO_MIN")
        return cls(
            api_url=api.rstrip("/"),
            worker_token=token,
            hosts_permitidos=r2,
            gpus=_gpus_do_ambiente(),
            max_pods=int(os.environ.get("RENDER2GO_MAX_PODS", ln.MAX_PODS_AUTO)),
            max_usd_h=float(os.environ.get("RENDER2GO_MAX_USD_H", 20)),
            prazo_min=int(prazo) if prazo else None,
            intervalo_s=max(5.0, float(os.environ.get("RENDER2GO_CICLO_S", 10))),
            segredo_aprovacao=os.environ.get("RENDER2GO_SEGREDO_APROVACAO") or None,
            agrupar_teste_s=float(os.environ.get("RENDER2GO_AGRUPAR_TESTE_MIN", 10)) * 60,
            agrupar_teste_videos=int(os.environ.get("RENDER2GO_AGRUPAR_TESTE_VIDEOS", 20)),
            ocioso_s=max(60.0, float(os.environ.get("RENDER2GO_POD_OCIOSO_MIN", 3)) * 60),
        )


@dataclass
class PodFila:
    pod: ln.Pod
    ate: float  # epoch em que o worker para de pegar jobs
    gerador: str | None = None  # linha na tabela geradores (previsão para o cliente)


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
        self.encerrando: dict[str, PodFila] = {}  # gravadores terminando o envio ao espelho
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
        """US$/h para o teto por hora e o desligamento automático: a opção mais cara com preço
        agora, porque o pod pode cair em qualquer uma (a mais barata vem primeiro na lista)."""
        precos = [x for o in self.pods_ln.a.opcoes if (x := self.provedor.preco(o.gpu, o.nuvem)) is not None]
        return max(precos) if precos else ln.PRECO_RESERVA_USD_H

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
            "RENDER2GO_WORKER_TOKEN": self.cfg.worker_token,
            "RENDER2GO_SAFETY": "c1-revisao-humana",
            "RENDER2GO_ALLOWED_URL_HOSTS": self.cfg.hosts_permitidos,
        }
        if self.cfg.segredo_aprovacao:
            linhas["RENDER2GO_SEGREDO_APROVACAO"] = self.cfg.segredo_aprovacao
        env.write_text("".join(f"{k}={shlex.quote(v)}\n" for k, v in linhas.items()))
        env.chmod(0o600)
        try:
            ag.put(env, f"{r.raiz}/fila.env")
        finally:
            env.unlink(missing_ok=True)
        vivo_s = time.monotonic() - pod.criado
        ate = time.time() + deadman_s - ln.MARGEM_S - vivo_s
        cmd = self.cfg.fila_cmd.format(
            raiz=r.raiz,
            api=shlex.quote(self.cfg.api_url),
            worker=f"pod-{pod.info.id}",
            gpus=pod.info.gpus,
            ate=int(ate),
            ocioso=int(self.cfg.ocioso_s),
            # o mesmo prazo da instalação inteira, contado da criação do pod
            modelos_ate=int(time.time() + ln.ESPERA_INSTALACAO_S - vivo_s),
        )
        ag.bg(f"{cmd}; echo FIM > {r.raiz}/fila.fim", f"{r.raiz}/fila.log")
        _log(f"pod {pod.nome}: worker da fila iniciado")
        return PodFila(pod, ate)

    def _subir(self, deadman_s: int, gerador: str | None = None) -> None:
        pronto = False
        try:
            with self.lock:
                self.n += 1
                i = self.n
            # serviços já durante o download dos modelos: o pedido é traduzido enquanto ele termina
            pod = self.pods_ln.subir_pod(
                i,
                deadman_s,
                cedo=True,
                ao_criar=lambda info: self._registrar(
                    "gerador_criado", gerador, f"pod-{info.id}", info.gpu, info.usd_h, info.datacentro
                ),
            )
            if pod is None:  # o lote_nuvem já encerrou o pod que não subiu (se chegou a existir)
                return
            try:
                pf = self._iniciar_worker(pod, deadman_s)
            except (ln.NuvemError, OSError) as e:
                _log(f"pod {pod.nome}: worker não iniciou ({e})")
                self.pods_ln.encerrar(pod, "worker não iniciou")
                return
            pf.gerador = gerador
            with self.lock:
                self.ativos[pod.info.id] = pf
            pronto = True
            self._registrar("gerador_pronto", gerador, f"pod-{pod.info.id}", pod.info.gpu, pod.info.usd_h)
        finally:
            if not pronto:  # registra antes de liberar a vaga: quem vê subindo == 0 vê o registro
                self._registrar("gerador_fim", gerador, "não subiu", falhou=True)
                self._registrar("gerador_desligado", gerador)
            with self.lock:
                self.subindo -= 1

    def _registrar(self, acao: str, gerador: str | None, *args, **kw) -> None:
        """Registro do pod na tabela geradores (previsão para o cliente): nunca derruba o fluxo."""
        if not gerador:
            return
        try:
            getattr(self.banco, acao)(gerador, *args, **kw)
        except Exception as e:  # noqa: BLE001 (só a previsão depende disso)
            _log(f"AVISO: registro do gerador ({acao}): {e}")

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
                with self.lock:
                    self.ativos.pop(pid, None)
                # o worker saiu: o pod já não gera vídeos (a previsão não pode contar com ele)
                self._registrar("gerador_fim", pf.gerador, "fila vazia")
                if pf.pod.grava_volume:  # o envio ao espelho pode levar minutos: fora do ciclo
                    with self.lock:
                        self.encerrando[pid] = pf
                    threading.Thread(target=self._encerrar_pod, args=(pf, "fila vazia"), daemon=True).start()
                else:
                    self._encerrar_pod(pf, "fila vazia")

    def _encerrar_pod(self, pf: PodFila, motivo: str) -> None:
        """Encerra o pod; o gravador antes termina o envio ao espelho (com prazo)."""
        try:
            self.pods_ln.encerrar(pf.pod, motivo)
        finally:
            with self.lock:
                self.encerrando.pop(pf.pod.info.id, None)
            self._registrar("gerador_fim", pf.gerador, motivo)
            self._registrar("gerador_desligado", pf.gerador)  # fim da cobrança do pod

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
            try:  # registro para a previsão do cliente; sem ele, o pod sobe do mesmo jeito
                gerador = self.banco.gerador_subindo(self.cfg.gpus[0])
            except Exception as e:  # noqa: BLE001
                _log(f"AVISO: registro do gerador: {e}")
                gerador = None
            with self.lock:
                self.subindo += 1
            gasto_h += usd_h
            subiu += 1
            threading.Thread(target=self._subir, args=(deadman, gerador), daemon=True).start()
        if subiu:
            _log(f"fila pede {querem} pod(s); subindo {subiu}")
        return {"desejados": querem, "ativos": len(self.ativos), "subindo": self.subindo}

    def rodar(self) -> None:
        _log(
            f"rodando; até {self.cfg.max_pods} pods e US$ {self.cfg.max_usd_h:.2f}/h; GPUs: " + ", ".join(self.cfg.gpus)
        )
        try:
            self.banco.geradores_reiniciar()  # pods de um processo anterior não contam mais
        except Exception as e:  # noqa: BLE001 (só a previsão depende disso)
            _log(f"AVISO: registro dos geradores: {e}")
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
        """Encerra tudo na hora, inclusive o gravador no meio do envio (o espelho completa depois)."""
        with self.lock:
            pods = list(self.ativos.values()) + list(self.encerrando.values())
        for pf in pods:
            self.pods_ln.encerrar(pf.pod, motivo, esperar_espelho=False)
            self._registrar("gerador_fim", pf.gerador, motivo)
            self._registrar("gerador_desligado", pf.gerador)
        with self.lock:
            self.ativos.clear()
            self.encerrando.clear()


def novo_token() -> str:
    return secrets.token_urlsafe(32)
