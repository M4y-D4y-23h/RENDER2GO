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
   "pod quente" vencido) ou, com RENDER2GO_PODS_HIBERNADOS > 0, para até esse número deles em vez de
   encerrar (ADR 0011): o pod hibernado guarda ambiente e modelos no disco de volume próprio e volta em
   ~1 min, sem download. Quando a fila pede pods, os hibernados são ligados primeiro; o host sem GPU
   livre faz o orquestrador tentar o próximo e, no fim, criar um pod novo.

O desligamento automático de cada pod é recalculado assim que o agente responde, com o preço real da
GPU (na criação ainda não se sabe qual GPU veio, e vale o preço da mais cara da lista) e com o que os
outros pods ligados ainda podem gastar até o prazo deles. O worker escreve por que saiu (sem pedidos,
limite de tempo do pod, modelos, GPU) e o motivo vai para o relatório de GPUs do painel. O saldo do
RunPod e os pods hibernados ficam na tabela `estado` ("runpod"), para o painel.

Cada pod fica registrado na tabela `geradores` (subindo, pronto, encerrado): a API usa esse estado
e as subidas medidas para a previsão mostrada ao cliente, e o painel do dono mostra o relatório de cada
pod (id no RunPod, GPU, preço, datacenter, horários, vídeos e custo estimado). O id e o preço são
gravados assim que o RunPod cria o pod, mesmo que a instalação falhe depois; o desligamento, quando
o pod é de fato encerrado (o gravador pode levar minutos enviando ao espelho depois do fim da fila).

Testes gratuitos em grupo (docs/08): com só testes na fila e nenhum pod ligado, o pod espera
juntar RENDER2GO_AGRUPAR_TESTE_VIDEOS vídeos (padrão 20) ou o teste mais antigo esperar
RENDER2GO_AGRUPAR_TESTE_MIN minutos (padrão 10). Cada subida custa ~US$ 0,20 (subida + espera do
pod quente) e um vídeo de teste ~US$ 0,015: dez testes de 1 foto em horários diferentes
pagariam dez subidas; juntos, pagam uma. Pedidos pagos sobem na hora.

Proteções:
- cada pod nasce com desligamento automático (≤ 2 h por subida e o que o saldo paga; o pod que hiberna
  nasce com 20 min e recebe o prazo real quando o agente responde; na hora, ele para em vez de encerrar);
- o worker para de pegar jobs 10 min antes;
- o estado fica em disco para `render2go-servico limpar`; na partida, os pods que um processo anterior
  deixou ligados (ele caiu sem encerrá-los) são encerrados;
- só um orquestrador manda nos pods (trava no Postgres): um segundo processo espera a vez;
- cada pod recebe um token de worker próprio (tabela `tokens_worker`), que vence com o pod e é revogado
  quando ele é encerrado: um token que vaze de um pod não pega jobs de outro nem depois do fim.

Painel → orquestrador (tabela `comandos`, executados no começo de cada ciclo):
- `desligar_pod`: encerra aquele pod (e cancela a subida, se ele ainda estiver subindo);
- `emergencia`: encerra todos os pods, inclusive os que o RunPod listar com o nome render2go-* (sobras de
  processos anteriores), e pausa: nenhum pod novo sobe até o `retomar`;
- `retomar`: tira a pausa. A pausa fica na tabela `estado` ("pausado"), e o sinal de vida do orquestrador
  ("orquestrador_vivo") também, para o painel mostrar se os comandos vão ser executados.

O pod recebe só a URL da API, o token de worker dele e os hosts permitidos (nunca as chaves do R2
ou do RunPod).
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shlex
import threading
import time
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from render2go_worker import lote, runpod_pods
from render2go_worker import lote_nuvem as ln

from render2go_servico.db import Banco, BancoError

FILA_CMD = (
    "set -a; . {raiz}/fila.env; set +a; cd /opt/render2go/worker && "
    "PATH=/opt/venv/bin:$PATH PYTHONPATH=/opt/render2go/worker/src "
    "python -m render2go_worker.fila --api {api} --worker {worker} --gpus {gpus} --ate {ate} "
    "--ocioso {ocioso} --modelos-prontos {raiz}/estado --modelos-ate {modelos_ate} "
    "--motivo {raiz}/fila.motivo"
)
VIVO = "vivo"  # tocado a cada ciclo bem-sucedido (healthcheck do contêiner)
TRAVA_ORQUESTRADOR = 20261009  # trava do Postgres: um orquestrador por vez manda nos pods
PREFIXO_PODS = "render2go-"  # nome dos pods que o orquestrador cria (a parada de emergência procura por ele)
LIMPEZA_S = 3600  # a cada hora: registros de acesso vencidos, códigos e limites antigos (Banco.purgar)
SALDO_S = 300  # a cada 5 min: saldo do RunPod para o painel (estado "runpod")
HIBERNADOS = "hibernados.json"  # pods parados com ambiente e modelos (ADR 0011); tem os tokens dos agentes
FALHAS_RETOMADA = 3  # o host ficou sem GPU livre tantas vezes seguidas: o pod hibernado é encerrado
# por que o worker saiu (fila.motivo, render2go_worker.fila.MOTIVOS), para o relatório do painel
MOTIVOS = {
    "ocioso": "fila vazia",
    "limite": "limite de tempo do pod",
    "modelos": "modelos não ficaram prontos",
    "filtro": "filtro de imagem não carregou",
    "gpu": "GPU inacessível",
}


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
    worker_token: str  # não vai mais para os pods (cada pod recebe um token próprio); fica para compatibilidade
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
    hibernados: int = 0  # pods parados com tudo instalado, prontos para voltar em ~1 min (ADR 0011)
    volume_gb: int = 70  # disco de volume de cada pod que pode hibernar (cobrado parado: US$ 0,20/GB/mês)

    @classmethod
    def do_ambiente(cls) -> ConfigOrq:
        api = os.environ.get("RENDER2GO_API_URL", "")
        token = next((t for t in os.environ.get("RENDER2GO_WORKER_TOKENS", "").split(",") if len(t) >= 32), "")
        if not api.startswith("https://"):
            raise SystemExit("falta RENDER2GO_API_URL (https)")
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
            hibernados=max(0, int(os.environ.get("RENDER2GO_PODS_HIBERNADOS", 0))),
            volume_gb=max(40, int(os.environ.get("RENDER2GO_POD_VOLUME_GB", 70))),
        )


@dataclass
class PodFila:
    pod: ln.Pod
    ate: float  # epoch em que o worker para de pegar jobs
    gerador: str | None = None  # linha na tabela geradores (previsão para o cliente)
    worker: str = ""  # nome do worker (pod-<id>): o token do pod é revogado por ele


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
        self.cancelados: set[str] = set()  # geradores desligados pelo painel enquanto subiam
        self.pausado = False
        self._limpeza = 0.0
        self._saldo_em = 0.0
        self._saldo: float | None = None  # saldo do RunPod (consultado a cada SALDO_S, para o painel)
        self._prazo_novo: int | None = None
        self._revisor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="revisar")
        # pods hibernados (ADR 0011): id → PodInfo + versão, voltas e falhas; em disco (0600) porque
        # guarda o token de cada agente, sem o qual o pod parado não serve para nada
        self.hib_arquivo = cfg.estado / HIBERNADOS
        self.hib: dict[str, dict] = self._ler_hibernados()
        # nome dos pods deste serviço: o hash da API separa ambientes que usem a mesma conta do RunPod
        self.prefixo = "render2go-fila-" + hashlib.sha256(cfg.api_url.encode()).hexdigest()[:6]

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

    def _comprometido(self, excluir: str | None = None) -> float:
        """US$ que os pods ligados (e os que estão subindo) ainda podem gastar até o desligamento
        automático de cada um: o pior caso, que o saldo precisa cobrir antes de um pod novo."""
        agora = time.monotonic()
        with self.pods_ln.lock:
            pods = [p for p in self.pods_ln.pods if p.vivo and p.info.id != excluir]
        return sum(p.info.usd_h * max(0.0, (p.deadman_s or ln.MAX_POD_S) - (agora - p.criado)) / 3600 for p in pods)

    def _deadman(
        self, usd_h: float, reservado: float = 0.0, excluir: str | None = None, saldo: float | None = None
    ) -> int | None:
        """Prazo (s a partir de agora) do desligamento automático de um pod que custa `usd_h`; None se
        o saldo não paga 15 min. `reservado`: US$ já prometidos a pods deste mesmo ciclo."""
        limite = ln.MAX_POD_S
        if saldo is None:
            saldo = self.provedor.saldo() if hasattr(self.provedor, "saldo") else None
        if saldo is not None:
            disponivel = saldo - max(ln.RESERVA_SALDO_USD, 0.1 * saldo) - reservado
            disponivel -= self._comprometido(excluir)
            limite = min(limite, max(0.0, disponivel) / max(usd_h, 0.01) * 3600)
        return int(limite) if limite >= ln.MIN_POD_S else None

    def _ajustar_deadman(self, pod: ln.Pod) -> None:
        """Assim que o agente responde: o prazo com o preço real da GPU (na criação valeu o da mais
        cara da lista; até 08/10/2026 ficava esse, e com saldo baixo o pod durava ~40 min)."""
        vivo_s = time.monotonic() - pod.criado
        total = self._deadman(pod.info.usd_h or self._preco(), excluir=pod.info.id)
        if total is None:  # o saldo não paga nem 15 min: fica o prazo com que o pod nasceu
            _log(f"pod {pod.nome}: saldo baixo; desligamento automático mantido em {pod.deadman_s // 60} min")
            return
        em = pod.agente.deadman(min(total, ln.MAX_POD_S - vivo_s))
        pod.deadman_s = int(vivo_s + em)
        _log(f"pod {pod.nome}: desligamento automático em {em / 60:.0f} min (US$ {pod.info.usd_h:.2f}/h)")

    # -- pods -----------------------------------------------------------------------------

    def _iniciar_worker(self, pod: ln.Pod, gerador: str | None = None) -> PodFila:
        r, ag = self.remoto, pod.agente
        deadman_s = pod.deadman_s  # já com o preço real (_ajustar_deadman)
        env = self.cfg.estado / f"fila-{pod.info.id}.env"
        worker = f"pod-{pod.info.id}"
        # token só deste pod, válido até o desligamento automático (com folga); revogado no fim
        token = self.banco.criar_token_worker(worker, gerador, deadman_s + 900)
        linhas = {
            "RENDER2GO_WORKER_TOKEN": token,
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
            worker=worker,
            gpus=pod.info.gpus,
            ate=int(ate),
            ocioso=int(self.cfg.ocioso_s),
            # o mesmo prazo da instalação inteira, contado da criação do pod
            modelos_ate=int(time.time() + ln.ESPERA_INSTALACAO_S - vivo_s),
        )
        ag.bg(f"{cmd}; echo FIM > {r.raiz}/fila.fim", f"{r.raiz}/fila.log")
        _log(f"pod {pod.nome}: worker da fila iniciado")
        return PodFila(pod, ate, gerador, worker)

    def _subir(self, deadman_s: int, gerador: str | None = None) -> None:
        pronto = False
        try:
            with self.lock:
                self.n += 1
                i = self.n

            def criado(info: ln.PodInfo) -> None:  # o pod passa a custar: id, GPU e preço no relatório
                self._registrar("gerador_criado", gerador, f"pod-{info.id}", info.gpu, info.usd_h, info.datacentro)

            # 1º os pods hibernados (~1 min, nada a baixar); sem nenhum que volte, um pod novo
            pod = self._retomar_algum(criado)
            if pod is None:
                # serviços já durante o download dos modelos: o pedido é traduzido enquanto ele termina
                pod = self.pods_ln.subir_pod(
                    i,
                    deadman_s,
                    cedo=True,
                    ao_criar=criado,
                    ao_agente=self._ajustar_deadman,
                    volume_gb=self.cfg.volume_gb if self.cfg.hibernados else 0,
                    prefixo=self.prefixo,
                )
            if pod is None:  # o lote_nuvem já encerrou o pod que não subiu (se chegou a existir)
                return
            with self.lock:
                cancelado = gerador in self.cancelados or self.pausado
            if cancelado:  # desligado pelo painel (ou parada de emergência) enquanto subia
                _log(f"pod {pod.nome}: desligado pelo painel durante a subida")
                self._tirar_hibernado(pod.info.id)
                self.pods_ln.encerrar(pod, "desligado pelo painel", esperar_espelho=False)
                return
            try:
                pf = self._iniciar_worker(pod, gerador)
            except (ln.NuvemError, OSError, BancoError) as e:
                _log(f"pod {pod.nome}: worker não iniciou ({e})")
                self._tirar_hibernado(pod.info.id)
                self.pods_ln.encerrar(pod, "worker não iniciou")
                return
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

    # -- pods hibernados (ADR 0011) --------------------------------------------------------------

    def _ler_hibernados(self) -> dict[str, dict]:
        try:
            dados = json.loads(self.hib_arquivo.read_text()) if self.hib_arquivo.is_file() else {}
        except (OSError, ValueError):
            dados = {}
        for h in dados.values():
            h["em_uso"] = False  # nenhum está em uso quando o processo começa
        return dados

    def _salvar_hibernados(self) -> None:
        """Grava o conjunto (0600: tem os tokens dos agentes) e o resumo sem tokens para o painel.
        Chamar com self.lock."""
        tmp = self.hib_arquivo.with_name(self.hib_arquivo.name + ".tmp")
        tmp.write_text(json.dumps(self.hib, indent=1))
        tmp.chmod(0o600)
        tmp.replace(self.hib_arquivo)

    def _tirar_hibernado(self, pod_id: str) -> None:
        with self.lock:
            if self.hib.pop(pod_id, None) is not None:
                self._salvar_hibernados()

    def _pode_hibernar(self, pod: ln.Pod, motivo: str) -> bool:
        if not (self.cfg.hibernados and pod.hibernavel and motivo in ("ocioso", "limite")) or self.pausado:
            return False
        versao = ln.versao_hibernacao()
        with self.lock:
            if (self.hib.get(pod.info.id) or {}).get("versao", versao) != versao:
                return False  # o disco do pod é de uma versão anterior (dependências, modelos, agente)
            return sum(1 for k in self.hib if k != pod.info.id) < self.cfg.hibernados

    def _retomar_algum(self, ao_criar) -> ln.Pod | None:
        """Liga o primeiro pod hibernado que conseguir (GPU na ordem de RENDER2GO_GPUS, menos falhas
        antes); None se não há nenhum ou todos estão sem GPU livre no host agora."""
        if not self.cfg.hibernados:
            return None
        versao = ln.versao_hibernacao()
        ordem = {g: n for n, g in enumerate(self.cfg.gpus)}
        while True:
            with self.lock:
                livres = [h for h in self.hib.values() if not h.get("em_uso") and h.get("versao") == versao]
                if not livres:
                    return None
                h = min(livres, key=lambda x: (ordem.get(x.get("gpu"), 99), x.get("falhas", 0)))
                h["em_uso"] = True
            info = ln.PodInfo(
                h["id"], h["url"], h["token"], float(h["usd_h"]), h.get("descricao", ""),
                int(h.get("gpus", 1)), h.get("gpu", ""), h.get("datacentro"),
            )  # fmt: skip
            try:
                pod = self.pods_ln.retomar_pod(info, cedo=True, ao_agente=self._ajustar_deadman, ao_criar=ao_criar)
            except runpod_pods.SemVaga as e:  # o host está sem GPU livre: o pod continua parado
                with self.lock:
                    h["em_uso"] = False
                    h["falhas"] = h.get("falhas", 0) + 1
                    desiste = h["falhas"] >= FALHAS_RETOMADA
                    self._salvar_hibernados()
                _log(f"pod hibernado {info.id}: {e}")
                if desiste:  # host cheio demais: o disco sai junto, e um pod novo ocupa a vaga depois
                    self._descartar_hibernado(info.id, f"{FALHAS_RETOMADA} vezes sem GPU livre no host")
                continue
            except (ln.NuvemError, runpod_pods.RunPodError, OSError) as e:
                _log(f"pod hibernado {info.id} não voltou: {e}")
                self._descartar_hibernado(info.id, "não voltou da hibernação")
                continue
            with self.lock:
                h["falhas"] = 0
                h["voltas"] = h.get("voltas", 0) + 1
                self._salvar_hibernados()
            return pod

    def _descartar_hibernado(self, pod_id: str, motivo: str) -> None:
        """Encerra de vez um pod do conjunto (o disco de volume vai junto)."""
        self._tirar_hibernado(pod_id)
        with self.pods_ln.lock:
            pod = next((p for p in self.pods_ln.pods if p.info.id == pod_id and p.vivo), None)
        try:
            if pod:
                self.pods_ln.encerrar(pod, motivo, esperar_espelho=False)
            else:
                self.provedor.encerrar(pod_id)
            _log(f"pod hibernado {pod_id} encerrado ({motivo})")
        except Exception as e:  # noqa: BLE001 (parado, o pod não cobra GPU; a limpeza tenta de novo)
            _log(f"AVISO: encerrar o pod hibernado {pod_id}: {e}")

    def _registrar(self, acao: str, gerador: str | None, *args, **kw) -> None:
        """Registro do pod na tabela geradores (previsão para o cliente): nunca derruba o fluxo."""
        if not gerador:
            return
        try:
            getattr(self.banco, acao)(gerador, *args, **kw)
        except Exception as e:  # noqa: BLE001 (só a previsão depende disso)
            _log(f"AVISO: registro do gerador ({acao}): {e}")

    def _terminou(self, pf: PodFila) -> str | None:
        """Por que o worker saiu (chave de MOTIVOS ou "agente"), ou None se ele ainda roda."""
        r = self.remoto.raiz
        try:
            _, out = pf.pod.agente.exec(f"cat {r}/fila.fim {r}/fila.motivo 2>/dev/null", 30)
        except (ln.NuvemError, OSError) as e:
            _log(f"pod {pf.pod.nome}: agente inacessível ({e})")
            return "agente"
        if "FIM" not in out:
            return None
        return next((m for m in MOTIVOS if m in out.split()), "ocioso")

    def _revisar(self) -> None:
        """Hiberna ou encerra pods cujo worker terminou ou cujo agente sumiu. Os agentes são consultados
        ao mesmo tempo: com dezenas de pods, um agente lento (até 30 s) não atrasa o ciclo inteiro."""
        pods = list(self.ativos.items())
        terminados = list(self._revisor.map(lambda item: self._terminou(item[1]), pods)) if pods else []
        for (pid, pf), motivo in zip(pods, terminados, strict=True):
            if motivo is None:
                continue
            with self.lock:
                self.ativos.pop(pid, None)
            texto = MOTIVOS.get(motivo, "agente inacessível")
            # o worker saiu: o pod já não gera vídeos (a previsão não pode contar com ele)
            self._registrar("gerador_fim", pf.gerador, texto)
            if motivo == "agente" and self._parado_pelo_prazo(pf):
                with self.pods_ln.lock:  # o próprio pod já se parou: nada a parar nem encerrar
                    pf.pod.vivo, pf.pod.fim = False, time.monotonic()
                    self.pods_ln._salvar_estado()
                motivo, texto = "limite", "limite de tempo do pod (parado pelo desligamento automático)"
            # o envio ao espelho e a 1ª cópia para o disco de volume (ADR 0011) podem levar minutos
            if pf.pod.grava_volume or pf.pod.espelhando or (pf.pod.hibernavel and not pf.pod.retomado):
                with self.lock:
                    self.encerrando[pid] = pf
                threading.Thread(target=self._fim_do_pod, args=(pf, motivo, texto), daemon=True).start()
            else:
                self._fim_do_pod(pf, motivo, texto)

    def _parado_pelo_prazo(self, pf: PodFila) -> bool:
        """O agente sumiu porque o desligamento automático parou o pod (que hiberna)?"""
        if not (pf.pod.hibernavel and hasattr(self.provedor, "situacao")):
            return False
        try:
            return self.provedor.situacao(pf.pod.info.id) == "EXITED"
        except Exception:  # noqa: BLE001
            return False

    def _volume_gravado(self, pod: ln.Pod, esperar: bool) -> bool:
        """O disco de volume do pod tem tudo desta versão (ADR 0011)? Na 1ª subida, a cópia termina
        depois do PRONTO, com o pod no ar; `esperar`: espera o GUARDADO (com prazo) antes de olhar."""
        if pod.retomado:
            return True  # voltou da hibernação com o marcador desta versão (o bootstrap conferiu)
        if esperar:
            self.pods_ln._esperar_guardar(pod)
        try:
            _, out = pod.agente.exec(f"cat {self.remoto.local_hib}/instalado 2>/dev/null", 30)
        except (ln.NuvemError, OSError):
            return False
        return out.split()[:1] == [ln.versao_hibernacao()]

    def _fim_do_pod(self, pf: PodFila, motivo: str, texto: str, esperar: bool = True) -> None:
        """Hiberna (ADR 0011) se pode e se o disco de volume está completo; senão encerra."""
        if not self._pode_hibernar(pf.pod, motivo) or (pf.pod.vivo and not self._volume_gravado(pf.pod, esperar)):
            self._tirar_hibernado(pf.pod.info.id)
            if self._pode_hibernar(pf.pod, motivo):
                texto = f"{texto}; disco de volume incompleto"
            self._encerrar_pod(pf, texto, esperar_espelho=esperar)
            return
        self._registrar_token(pf)
        try:
            # o pod que o desligamento automático já parou (vivo=False em _revisar) só entra no conjunto
            ok = self.pods_ln.hibernar(pf.pod, texto) if pf.pod.vivo else True
            if ok:
                i = pf.pod.info
                with self.lock:
                    h = self.hib.setdefault(i.id, {"criado": time.time(), "voltas": 0, "falhas": 0})
                    h |= {
                        "id": i.id, "url": i.url, "token": i.token, "usd_h": i.usd_h, "descricao": i.descricao,
                        "gpus": i.gpus, "gpu": i.gpu, "datacentro": i.datacentro, "em_uso": False,
                        "versao": ln.versao_hibernacao(), "parado_em": time.time(),
                    }  # fmt: skip
                    self._salvar_hibernados()
                texto = f"hibernado ({texto})"
            else:
                self._tirar_hibernado(pf.pod.info.id)
        finally:
            with self.lock:
                self.encerrando.pop(pf.pod.info.id, None)
            self._registrar("gerador_fim", pf.gerador, texto)
            self._registrar("gerador_motivo", pf.gerador, texto)  # o _revisar já tirou da previsão
            self._registrar("gerador_desligado", pf.gerador)  # fim da cobrança da GPU

    def _encerrar_pod(self, pf: PodFila, motivo: str, esperar_espelho: bool = True) -> None:
        """Encerra o pod; o gravador antes termina o envio ao espelho (com prazo)."""
        self._registrar_token(pf)
        try:
            self.pods_ln.encerrar(pf.pod, motivo, esperar_espelho=esperar_espelho)
        finally:
            with self.lock:
                self.encerrando.pop(pf.pod.info.id, None)
            self._registrar("gerador_fim", pf.gerador, motivo)
            self._registrar("gerador_motivo", pf.gerador, motivo)  # o _revisar pode ter gravado um antes
            self._registrar("gerador_desligado", pf.gerador)  # fim da cobrança do pod

    def _registrar_token(self, pf: PodFila) -> None:
        """O pod que sai perde o token na hora (nunca derruba o encerramento)."""
        try:
            self.banco.revogar_tokens_worker(worker=pf.worker or f"pod-{pf.pod.info.id}")
        except Exception as e:  # noqa: BLE001 (o token vence sozinho com o desligamento automático)
            _log(f"AVISO: revogar o token de {pf.pod.nome}: {e}")

    # -- painel: comandos, pausa e sinal de vida -------------------------------------------------

    def _desligar_gerador(self, gerador: str) -> str:
        with self.lock:
            pf = next((p for p in self.ativos.values() if p.gerador == gerador), None)
            if pf:
                self.ativos.pop(pf.pod.info.id, None)
            else:
                self.cancelados.add(gerador)  # ainda subindo: encerrado assim que o RunPod devolver o pod
        if pf:  # pedido explícito do dono: encerra de vez, mesmo se o pod pudesse hibernar
            self._tirar_hibernado(pf.pod.info.id)
            self._encerrar_pod(pf, "desligado pelo painel", esperar_espelho=False)
            return f"pod {pf.pod.info.id} encerrado"
        try:  # subindo: se o RunPod já criou o pod, encerra agora mesmo pelo id registrado
            with self.banco.tx() as c:
                g = c.one("SELECT worker FROM geradores WHERE id = ?", (gerador,))
            if g and g["worker"]:
                self._tirar_hibernado(g["worker"].removeprefix("pod-"))
                self.provedor.encerrar(g["worker"].removeprefix("pod-"))
                self._registrar("gerador_fim", gerador, "desligado pelo painel")
                self._registrar("gerador_desligado", gerador)
                return f"pod {g['worker']} encerrado durante a subida"
        except Exception as e:  # noqa: BLE001 (a subida vê o cancelamento e encerra)
            _log(f"AVISO: desligar {gerador}: {e}")
        return "subida cancelada"

    def _sobras_runpod(self) -> int:
        """Pods render2go-* que o RunPod lista e que este processo não conhece (sobras de um processo
        anterior ou de um teste): encerrados na parada de emergência."""
        chave = getattr(self.provedor, "chave", None)
        if not chave:
            return 0
        try:
            r = runpod_pods._rest("GET", "/pods", chave)
            pods = r.json() if r.ok else []
        except Exception as e:  # noqa: BLE001
            _log(f"AVISO: listar os pods do RunPod: {e}")
            return 0
        with self.lock:
            conhecidos = {p.pod.info.id for p in self.ativos.values()}
        n = 0
        for p in pods if isinstance(pods, list) else []:
            if str(p.get("name", "")).startswith(PREFIXO_PODS) and p.get("id") not in conhecidos:
                try:
                    self.provedor.encerrar(p["id"])
                    n += 1
                except Exception as e:  # noqa: BLE001
                    _log(f"AVISO: encerrar a sobra {p.get('id')}: {e}")
        return n

    def comandos(self) -> None:
        """Executa os pedidos do painel (desligar um pod, parada de emergência, retomar)."""
        for cmd in self.banco.comandos_pendentes():
            try:
                if cmd["tipo"] == "desligar_pod" and cmd["alvo"]:
                    resultado = self._desligar_gerador(cmd["alvo"])
                elif cmd["tipo"] == "emergencia":
                    self.pausado = True
                    with self.lock:
                        n = len(self.ativos) + len(self.encerrando) + self.subindo
                        hib = list(self.hib)
                    self.encerrar_todos("parada de emergência")
                    for pid in hib:  # parada de emergência: os hibernados também saem (com o disco)
                        self._descartar_hibernado(pid, "parada de emergência")
                    sobras = self._sobras_runpod()
                    resultado = f"{n} pod(s) encerrados" + (f" e {sobras} sobra(s) no RunPod" if sobras else "")
                    if hib:
                        resultado += f"; {len(hib)} hibernado(s) encerrado(s)"
                else:  # retomar: a pausa sai do estado (a API já gravou)
                    resultado = "pods liberados"
                _log(f"painel: {cmd['tipo']} ({cmd['por']}): {resultado}")
            except Exception as e:  # noqa: BLE001 (o comando fica com o erro; o painel mostra)
                resultado = f"erro: {e}"
            self.banco.comando_executado(cmd["id"], resultado)

    def _estado_do_painel(self) -> None:
        pausa = self.banco.ler_estado("pausado")
        pausado = bool(pausa and pausa["valor"] == "1")
        if pausado != self.pausado:
            _log("PAUSADO pelo painel: nenhum pod novo sobe" if pausado else "pausa retirada pelo painel")
        self.pausado = pausado
        if not pausado:
            with self.lock:
                self.cancelados.clear()
        self.banco.definir_estado("orquestrador_vivo", str(time.time()))
        self._estado_runpod()
        if time.time() - self._limpeza > LIMPEZA_S:
            self._limpeza = time.time()
            n = self.banco.purgar()
            if any(n.values()):
                _log("limpeza: " + ", ".join(f"{k} {v}" for k, v in n.items() if v))

    def _estado_runpod(self) -> None:
        """Para o painel: saldo do RunPod (a cada 5 min), o prazo que um pod novo teria por ele e os
        pods hibernados, sem os tokens. Também encerra os hibernados de uma versão anterior e os que
        passam de RENDER2GO_PODS_HIBERNADOS (o dono diminuiu o número ou voltou a 0)."""
        agora = time.time()
        if agora - self._saldo_em >= SALDO_S:
            self._saldo_em = agora
            self._saldo = self.provedor.saldo() if hasattr(self.provedor, "saldo") else None
            self._prazo_novo = None
            if self._saldo is not None:
                try:
                    self._prazo_novo = self._deadman(self._preco(), saldo=self._saldo) or 0
                except Exception as e:  # noqa: BLE001 (só o painel depende disso)
                    _log(f"AVISO: prazo de um pod novo: {e}")
        versao = ln.versao_hibernacao()
        with self.lock:
            velhos = [k for k, h in self.hib.items() if h.get("versao") != versao and not h.get("em_uso")]
        for pid in velhos:
            self._descartar_hibernado(pid, "versão anterior do ambiente ou dos modelos")
        # parados a mais saem, os parados há mais tempo primeiro; o que está ligado agora é encerrado
        # (em vez de hibernado) quando a fila esvaziar, porque o conjunto já está cheio
        with self.lock:
            parados = sorted((h.get("parado_em") or 0, k) for k, h in self.hib.items() if not h.get("em_uso"))
            sobra = len(self.hib) - self.cfg.hibernados
        for _, pid in parados[: max(0, sobra)]:
            self._descartar_hibernado(pid, "acima de RENDER2GO_PODS_HIBERNADOS")
        with self.lock:
            campos = ("id", "gpu", "usd_h", "datacentro", "criado", "parado_em", "voltas", "falhas", "em_uso")
            hib = [{k: h.get(k) for k in campos} for h in self.hib.values()]
        self.banco.definir_estado(
            "runpod",
            json.dumps(
                {
                    "saldo_usd": self._saldo,
                    "saldo_em": self._saldo_em or None,
                    "prazo_novo_pod_s": self._prazo_novo,
                    "margem_s": ln.MARGEM_S,
                    "hibernados": hib,
                    "hibernados_max": self.cfg.hibernados,
                    "volume_gb": self.cfg.volume_gb,
                }
            ),
        )

    def ciclo(self) -> dict:
        self._estado_do_painel()
        self.comandos()
        self._revisar()
        if self.pausado:  # parada de emergência: nada sobe até o painel retomar
            return {"desejados": 0, "ativos": len(self.ativos), "subindo": self.subindo, "pausado": True}
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
        reservado = 0.0  # o pior caso dos pods que este ciclo já mandou subir
        for _ in range(faltam):
            if gasto_h + usd_h > self.cfg.max_usd_h:
                _log(f"teto de US$ {self.cfg.max_usd_h:.2f}/h: não sobe mais pods agora")
                break
            deadman = self._deadman(usd_h, reservado)
            if deadman is None:
                _log("saldo da conta não paga mais um pod: não sobe")
                break
            reservado += usd_h * deadman / 3600
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

    def _sobras_do_disco(self) -> None:
        """Pods que um processo anterior deixou ligados (caiu sem encerrá-los): encerrados agora, em vez
        de esperar o desligamento automático (até 2 h pagas à toa)."""
        est = self.cfg.estado / ln.ESTADO / "pods.json"
        try:
            sobras = json.loads(est.read_text()) if est.is_file() else []
        except (OSError, ValueError):
            sobras = []
        for p in sobras:
            self._tirar_hibernado(p["id"])  # estava ligado quando o processo caiu: sai do conjunto
            try:
                self.provedor.encerrar(p["id"])
                _log(f"pod {p['id']} de um processo anterior encerrado")
            except Exception as e:  # noqa: BLE001 (o desligamento automático cobre)
                _log(f"AVISO: encerrar a sobra {p.get('id')}: {e}")
        if sobras:
            est.write_text("[]")
        self._conferir_hibernados()

    def _conferir_hibernados(self) -> None:
        """Na partida: tira do conjunto o pod que o RunPod já não tem e encerra os pods parados deste
        serviço que não estão no conjunto (o arquivo se perdeu: sem o token do agente, não servem)."""
        chave = getattr(self.provedor, "chave", None)
        if chave is None or not (self.hib or self.cfg.hibernados):
            return
        try:
            r = runpod_pods._rest("GET", "/pods", chave)
            pods = r.json() if r.ok else None
        except Exception as e:  # noqa: BLE001
            _log(f"AVISO: listar os pods do RunPod: {e}")
            return
        if not isinstance(pods, list):
            return
        existem = {p.get("id"): p for p in pods}
        with self.lock:
            sumiram = [k for k in self.hib if existem.get(k, {}).get("desiredStatus") in (None, "TERMINATED")]
        for pid in sumiram:
            self._tirar_hibernado(pid)
            _log(f"pod hibernado {pid} não existe mais no RunPod: saiu do conjunto")
        for pid, p in existem.items():
            nome = str(p.get("name", ""))
            if nome.startswith(self.prefixo) and p.get("desiredStatus") == "EXITED" and pid not in self.hib:
                try:
                    self.provedor.encerrar(pid)
                    _log(f"pod parado {pid} fora do conjunto de hibernados: encerrado")
                except Exception as e:  # noqa: BLE001
                    _log(f"AVISO: encerrar o pod parado {pid}: {e}")

    def rodar(self) -> None:
        trava = None
        while not self.parar.is_set():  # só um orquestrador manda nos pods; outro processo espera a vez
            trava = self.banco.trava_exclusiva(TRAVA_ORQUESTRADOR)
            if trava:
                break
            _log("outro orquestrador está ativo: esperando a vez (nova tentativa em 30 s)")
            self.parar.wait(30)
        if trava is None:
            return
        try:
            self._rodar()
        finally:
            if hasattr(trava, "close"):
                trava.close()

    def _rodar(self) -> None:
        _log(
            f"rodando; até {self.cfg.max_pods} pods e US$ {self.cfg.max_usd_h:.2f}/h; GPUs: " + ", ".join(self.cfg.gpus)
        )
        self._sobras_do_disco()
        try:
            self.banco.geradores_reiniciar()  # pods de um processo anterior não contam mais
            self.banco.revogar_tokens_worker()  # e os tokens deles também não
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
            self.encerrar_todos("orquestrador parou", hibernar=True)

    def encerrar_todos(self, motivo: str, hibernar: bool = False) -> None:
        """Encerra tudo na hora, inclusive o gravador no meio do envio (o espelho completa depois) e os
        pods que ainda estão subindo (o RunPod já os criou: estão em `pods_ln.pods`). Com `hibernar`
        (o orquestrador parou, ex.: atualização), os pods ativos que cabem no conjunto hibernam: os
        vídeos em andamento voltam para a fila do mesmo jeito, e a próxima subida leva ~1 min."""
        with self.lock:
            pods = list(self.ativos.values()) + list(self.encerrando.values())
            conhecidos = {p.pod.info.id for p in pods}
        for pf in pods:
            if hibernar and not pf.pod.grava_volume and self._pode_hibernar(pf.pod, "ocioso"):
                self._fim_do_pod(pf, "ocioso", motivo, esperar=False)  # sem esperar cópia nenhuma
                continue
            self._tirar_hibernado(pf.pod.info.id)
            self._registrar_token(pf)
            self.pods_ln.encerrar(pf.pod, motivo, esperar_espelho=False)
            self._registrar("gerador_fim", pf.gerador, motivo)
            self._registrar("gerador_desligado", pf.gerador)
        with self.pods_ln.lock:
            subindo = [p for p in self.pods_ln.pods if p.vivo and p.info.id not in conhecidos]
        for pod in subindo:  # a subida em andamento vê o pod encerrado e desiste
            self._tirar_hibernado(pod.info.id)
            self.pods_ln.encerrar(pod, motivo, esperar_espelho=False)
        with self.lock:
            self.ativos.clear()
            self.encerrando.clear()


def novo_token() -> str:
    return secrets.token_urlsafe(32)
