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
- cada pod nasce com desligamento automático (≤ 2 h e o que o saldo paga);
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
    "--ocioso {ocioso} --modelos-prontos {raiz}/estado --modelos-ate {modelos_ate}"
)
VIVO = "vivo"  # tocado a cada ciclo bem-sucedido (healthcheck do contêiner)
TRAVA_ORQUESTRADOR = 20261009  # trava do Postgres: um orquestrador por vez manda nos pods
PREFIXO_PODS = "render2go-"  # nome dos pods que o orquestrador cria (a parada de emergência procura por ele)
LIMPEZA_S = 3600  # a cada hora: registros de acesso vencidos, códigos e limites antigos (Banco.purgar)


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
        self._revisor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="revisar")

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

    def _iniciar_worker(self, pod: ln.Pod, deadman_s: int, gerador: str | None = None) -> PodFila:
        r, ag = self.remoto, pod.agente
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
            with self.lock:
                cancelado = gerador in self.cancelados or self.pausado
            if cancelado:  # desligado pelo painel (ou parada de emergência) enquanto subia
                _log(f"pod {pod.nome}: desligado pelo painel durante a subida")
                self.pods_ln.encerrar(pod, "desligado pelo painel", esperar_espelho=False)
                return
            try:
                pf = self._iniciar_worker(pod, deadman_s, gerador)
            except (ln.NuvemError, OSError, BancoError) as e:
                _log(f"pod {pod.nome}: worker não iniciou ({e})")
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

    def _registrar(self, acao: str, gerador: str | None, *args, **kw) -> None:
        """Registro do pod na tabela geradores (previsão para o cliente): nunca derruba o fluxo."""
        if not gerador:
            return
        try:
            getattr(self.banco, acao)(gerador, *args, **kw)
        except Exception as e:  # noqa: BLE001 (só a previsão depende disso)
            _log(f"AVISO: registro do gerador ({acao}): {e}")

    def _terminou(self, pf: PodFila) -> bool:
        try:
            _, out = pf.pod.agente.exec(f"cat {self.remoto.raiz}/fila.fim 2>/dev/null", 30)
            return "FIM" in out
        except (ln.NuvemError, OSError) as e:
            _log(f"pod {pf.pod.nome}: agente inacessível ({e})")
            return True

    def _revisar(self) -> None:
        """Encerra pods cujo worker terminou ou cujo agente sumiu. Os agentes são consultados ao mesmo
        tempo: com dezenas de pods, um agente lento (até 30 s) não atrasa o ciclo inteiro."""
        pods = list(self.ativos.items())
        terminados = list(self._revisor.map(lambda item: self._terminou(item[1]), pods)) if pods else []
        for (pid, pf), fim in zip(pods, terminados, strict=True):
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

    def _encerrar_pod(self, pf: PodFila, motivo: str, esperar_espelho: bool = True) -> None:
        """Encerra o pod; o gravador antes termina o envio ao espelho (com prazo)."""
        self._registrar_token(pf)
        try:
            self.pods_ln.encerrar(pf.pod, motivo, esperar_espelho=esperar_espelho)
        finally:
            with self.lock:
                self.encerrando.pop(pf.pod.info.id, None)
            self._registrar("gerador_fim", pf.gerador, motivo)
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
        if pf:
            self._encerrar_pod(pf, "desligado pelo painel", esperar_espelho=False)
            return f"pod {pf.pod.info.id} encerrado"
        try:  # subindo: se o RunPod já criou o pod, encerra agora mesmo pelo id registrado
            with self.banco.tx() as c:
                g = c.one("SELECT worker FROM geradores WHERE id = ?", (gerador,))
            if g and g["worker"]:
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
                    self.encerrar_todos("parada de emergência")
                    sobras = self._sobras_runpod()
                    resultado = f"{n} pod(s) encerrados" + (f" e {sobras} sobra(s) no RunPod" if sobras else "")
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
        if time.time() - self._limpeza > LIMPEZA_S:
            self._limpeza = time.time()
            n = self.banco.purgar()
            if any(n.values()):
                _log("limpeza: " + ", ".join(f"{k} {v}" for k, v in n.items() if v))

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

    def _sobras_do_disco(self) -> None:
        """Pods que um processo anterior deixou ligados (caiu sem encerrá-los): encerrados agora, em vez
        de esperar o desligamento automático (até 2 h pagas à toa)."""
        est = self.cfg.estado / ln.ESTADO / "pods.json"
        try:
            sobras = json.loads(est.read_text()) if est.is_file() else []
        except (OSError, ValueError):
            sobras = []
        for p in sobras:
            try:
                self.provedor.encerrar(p["id"])
                _log(f"pod {p['id']} de um processo anterior encerrado")
            except Exception as e:  # noqa: BLE001 (o desligamento automático cobre)
                _log(f"AVISO: encerrar a sobra {p.get('id')}: {e}")
        if sobras:
            est.write_text("[]")

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
            self.encerrar_todos("orquestrador parou")

    def encerrar_todos(self, motivo: str) -> None:
        """Encerra tudo na hora, inclusive o gravador no meio do envio (o espelho completa depois) e os
        pods que ainda estão subindo (o RunPod já os criou: estão em `pods_ln.pods`)."""
        with self.lock:
            pods = list(self.ativos.values()) + list(self.encerrando.values())
            conhecidos = {p.pod.info.id for p in pods}
        for pf in pods:
            self._registrar_token(pf)
            self.pods_ln.encerrar(pf.pod, motivo, esperar_espelho=False)
            self._registrar("gerador_fim", pf.gerador, motivo)
            self._registrar("gerador_desligado", pf.gerador)
        with self.pods_ln.lock:
            subindo = [p for p in self.pods_ln.pods if p.vivo and p.info.id not in conhecidos]
        for pod in subindo:  # a subida em andamento vê o pod encerrado e desiste
            self.pods_ln.encerrar(pod, motivo, esperar_espelho=False)
        with self.lock:
            self.ativos.clear()
            self.encerrando.clear()


def novo_token() -> str:
    return secrets.token_urlsafe(32)
