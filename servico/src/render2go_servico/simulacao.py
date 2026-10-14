"""Simulação da fila e dos pods (14/10/2026; docs/15, seção 3): compara políticas sob as mesmas premissas.

    python -m render2go_servico.simulacao --sementes 30 [--json saida.json]

**Não é medição.** Os tempos vêm das medições registradas (worker/evals: subida fria até pronto 195–221 s em 13/10 e
4,2–5,1 min até o 1º vídeo em 08/10; vídeo de 5 s na L40S 52 s em 480p e 173 s em 720p; volta da hibernação 64–67 s;
1ª tradução 57,5 s), com uma variação aleatória por semente. Os números só servem para comparar políticas entre si
(ociosidade de 60 × 180 s, histerese, entrega que repete o mesmo vídeo × a de antes) e para ver o efeito das regras
de capacidade; não prometem tempos de produção.

As decisões são as do código: `capacidade.trabalho`, `capacidade.pods_desejados`, `capacidade.equivalentes` e
`capacidade.PoliticaOciosidade`, a cada ciclo do orquestrador (10 s), com o teto por hora contando tudo o que pode
cobrar (ADR 0016) e a fila intercalada por cliente (o cliente com menos vídeos começados primeiro; no mesmo cliente,
o pedido mais antigo).
"""

from __future__ import annotations

import argparse
import heapq
import itertools
import json
import random
import statistics
import sys
from dataclasses import dataclass, field

from render2go_servico import capacidade as cap

CICLO_S = 10.0
CONSULTA_WORKER_S = 5.0  # o worker sem trabalho pergunta à API a cada 5 s (fila.ESPERA_S)
TRADUCAO_1_S, TRADUCAO_S = 60.0, 5.0  # 1ª tradução no pod (o tradutor carrega) e as seguintes
CONFERE_S = 4.0  # conferência e filtro de imagem (CPU; com `adiantar`, em paralelo com o próximo vídeo)
ENVIO_S = 1.5
CONFIRMACAO_S = (5.0, 20.0)  # do DELETE aceito ao fim observado (a cobrança vai até ele)
SUBIDA_FRIA_S = (195.0, 260.0)  # da criação ao PRONTO (13/10: 195–221 s; 08/10: 4,2–5,1 min até o 1º vídeo)
AMBIENTE_ANTES_S = 120.0  # os serviços sobem ~2 min antes do PRONTO: o pedido já é traduzido (subir_pod(cedo=True))
RETOMADA_S = (64.0, 67.0)
RUIDO = 0.08  # variação do tempo de cada vídeo (lognormal)
MAX_POD_S = 2 * 3600 - 600  # o worker para de pegar vídeos 10 min antes das 2 h


@dataclass
class Gpu:
    nome: str
    usd_h: float
    vaga: float  # chance de ter vaga em cada tentativa


L40S = Gpu("NVIDIA L40S", 1.09, 0.9)
PRO6000 = Gpu("NVIDIA RTX PRO 6000 Blackwell Server Edition", 2.49, 0.9)


@dataclass
class Pedido:
    id: int
    cliente: str
    chegada: float
    n: int
    qualidade: str = "rapida"
    duracao: int = 5
    pagos: int | None = None  # vídeos que o saldo paga (None: todos)
    bloqueados: int = 0  # fotos recusadas pelo filtro das fotos: nunca viram vídeo
    cancelado_em: float | None = None
    traduzido: bool = False
    traduzindo: bool = False
    iniciados: int = 0
    prontos: int = 0
    primeiro: float | None = None
    ultimo: float | None = None

    @property
    def elegiveis(self) -> int:
        """Vídeos que podem ir para um pod: sem os bloqueados, até o que o saldo paga."""
        n = self.n - self.bloqueados
        return min(n, self.pagos) if self.pagos is not None else n

    def pendentes(self, agora: float) -> int:
        if self.cancelado_em is not None and agora >= self.cancelado_em:
            return 0
        return max(0, self.elegiveis - self.iniciados)


@dataclass
class PodSim:
    id: int
    gpu: Gpu
    criado: float
    ocioso_s: float
    gravador: bool = False
    estado: str = "subindo"  # subindo, ativo, drenando, fechando, fechado
    pronto_em: float | None = None
    livre: bool = True
    ocioso_desde: float | None = None
    ocupado_s: float = 0.0
    traducoes: int = 0
    andamento: dict[int, float] = field(default_factory=dict)  # pedido → início do vídeo na GPU
    fim_cobranca: float | None = None
    so_traducao: bool = False  # serviços no ar, modelos baixando


@dataclass
class Cenario:
    nome: str
    pedidos: list[Pedido]
    gpus: list[Gpu] = field(default_factory=lambda: [L40S])
    max_pods: int = 8
    max_usd_h: float = 20.0
    falha_envio: float = 0.0  # chance de um envio falhar (rede); a entrega nova repete o mesmo vídeo
    falha_conteudo: float = 0.0  # chance de a conferência reprovar (refeito com outra seed, nas duas entregas)
    cache_s: float = 0.0  # o 1º pod grava o cache (volume/espelho) por isto antes de fechar
    horizonte_s: float = 6 * 3600
    compara_capacidade: bool = False  # também com a regra de capacidade de antes de 14/10/2026


@dataclass
class Resultado:
    cenario: str
    politica: str
    entrega: str
    primeiro_s: list[float] = field(default_factory=list)
    lote_s: list[float] = field(default_factory=list)
    videos: int = 0
    nao_entregues: int = 0
    ociosidade_s: float = 0.0
    desperdicio_s: float = 0.0
    custo_usd: float = 0.0
    pods: int = 0

    def resumo(self) -> dict:
        def pct(v: list[float]) -> dict:
            if not v:
                return {"n": 0}
            s = sorted(v)
            r = {"n": len(s), "p50": round(statistics.median(s), 1)}
            if len(s) >= 20:  # com menos, o p95 não diz nada
                r["p95"] = round(s[min(len(s) - 1, int(0.95 * len(s)))], 1)
            return r

        return {
            "cenario": self.cenario,
            "politica": self.politica,
            "entrega": self.entrega,
            "primeiro_video_s": pct(self.primeiro_s),
            "lote_s": pct(self.lote_s),
            "videos": self.videos,
            "nao_entregues": self.nao_entregues,
            "gpu_ociosa_s": round(self.ociosidade_s),
            "render_desperdicado_s": round(self.desperdicio_s),
            "custo_usd": round(self.custo_usd, 3),
            "custo_por_video_usd": round(self.custo_usd / self.videos, 4) if self.videos else None,
            "pods": self.pods,
        }


class Sim:
    def __init__(
        self, c: Cenario, politica: cap.PoliticaOciosidade, entrega_nova: bool, semente: int, regra: str = "nova"
    ):
        self.c, self.politica, self.nova, self.regra = c, politica, entrega_nova, regra
        self.rng = random.Random(semente)
        self.tempos = cap.Tempos()
        self.pedidos = [Pedido(**{k: getattr(p, k) for k in Pedido.__dataclass_fields__}) for p in c.pedidos]
        self.pods: list[PodSim] = []
        self.eventos: list = []
        self.seq = itertools.count()
        self.com_demanda = False
        self.desperdicio_s = 0.0
        self.agora = 0.0

    # -- eventos ------------------------------------------------------------------------------------------------
    def depois(self, t: float, f, *args) -> None:
        heapq.heappush(self.eventos, (t, next(self.seq), f, args))

    def rodar(self) -> None:
        self.depois(0.0, self.ciclo)
        while self.eventos:
            t, _, f, args = heapq.heappop(self.eventos)
            if t > self.c.horizonte_s:
                break
            self.agora = t
            f(*args)
        for p in self.pods:  # o que ficou ligado no fim do horizonte cobra até ele
            if p.fim_cobranca is None:
                p.fim_cobranca = self.c.horizonte_s

    # -- orquestrador -------------------------------------------------------------------------------------------
    def _linhas(self) -> list[dict]:
        out = []
        for p in self.pedidos:
            if p.chegada > self.agora:
                continue
            pend = p.pendentes(self.agora)
            anda = [ini for pod in self.pods for pid, ini in pod.andamento.items() if pid == p.id]
            trad = int(not p.traduzido and (pend > 0 or p.traduzindo))
            if pend or anda or trad:
                out.append({
                    "pedido": {"qualidade": p.qualidade, "duracao": p.duracao}, "pendentes": pend,
                    "em_andamento": len(anda), "traducoes": trad,
                    "andamento_s": sum(self.agora - ini for ini in anda),
                })  # fmt: skip
        return out

    def ciclo(self) -> None:
        ativos = [p for p in self.pods if p.estado == "ativo"]
        subindo = [p for p in self.pods if p.estado == "subindo"]
        existentes = len(ativos) + len(subindo)
        querem = self._querem(self._linhas())
        if querem and not existentes and not self.com_demanda:
            self.com_demanda = True
            self.politica.nova_demanda(self.agora)
        elif not querem and not existentes:
            self.com_demanda = False
        if self.regra == "nova":
            ligada = cap.equivalentes([p.gpu.nome for p in ativos], len(subindo), self.c.gpus[0].nome, self.tempos)
        else:  # antes de 14/10/2026: cada pod contava 1, qualquer GPU
            ligada = float(existentes)
        faltam = max(0, min(querem - int(ligada + 0.5), self.c.max_pods - existentes))
        faturaveis = sum(p.gpu.usd_h for p in self.pods if p.estado != "fechado")
        for _ in range(faltam):
            gpu = next((g for g in self.c.gpus if self.rng.random() < g.vaga), None)
            if gpu is None or faturaveis + gpu.usd_h > self.c.max_usd_h:
                break
            faturaveis += gpu.usd_h
            pod = PodSim(len(self.pods), gpu, self.agora, self.politica.ocioso_s())
            pod.gravador = self.c.cache_s > 0 and not any(x.gravador for x in self.pods)
            self.pods.append(pod)
            subida = self.rng.uniform(*SUBIDA_FRIA_S)
            self.depois(self.agora + max(0.0, subida - AMBIENTE_ANTES_S), self.ambiente, pod)
            self.depois(self.agora + subida, self.pronto, pod)
        if self.agora + CICLO_S <= self.c.horizonte_s:
            self.depois(self.agora + CICLO_S, self.ciclo)

    def _querem(self, linhas: list[dict]) -> int:
        if self.regra == "nova":
            t = cap.trabalho(linhas, self.c.gpus[0].nome, self.tempos)
            return cap.pods_desejados(t, None, self.c.max_pods)
        # a regra de antes de 14/10/2026: a tradução contava como um vídeo e o vídeo em andamento, inteiro
        from render2go_worker import lote_nuvem as ln

        segundos, videos = 0.0, 0
        for x in linhas:
            n = x["pendentes"] + x["em_andamento"] + x["traducoes"]
            q, d = x["pedido"]["qualidade"], x["pedido"]["duracao"]
            segundos += n * ln.segundos_por_video(self.c.gpus[0].nome, q, d)
            videos += n
        if not videos:
            return 0
        pods, _ = ln.escolher_pods(videos, videos, 1, segundos / videos, None, max_pods=self.c.max_pods)
        return min(pods, self.c.max_pods)

    # -- pod e worker -------------------------------------------------------------------------------------------
    def ambiente(self, pod: PodSim) -> None:
        """Serviços no ar com os modelos ainda baixando: o pod só traduz (fila.estado_modelos "esperando")."""
        if pod.estado == "subindo":
            pod.so_traducao = True
            self.trabalhar(pod)

    def pronto(self, pod: PodSim) -> None:
        pod.estado, pod.pronto_em, pod.ocioso_desde, pod.so_traducao = "ativo", self.agora, self.agora, False
        self.trabalhar(pod)

    def _proximo(self) -> tuple[str, Pedido] | None:
        abertos = [p for p in self.pedidos if p.chegada <= self.agora and p.pendentes(self.agora) > 0]
        for p in abertos:  # a tradução do pedido passa na frente
            if not p.traduzido and not p.traduzindo:
                return "traduzir", p
        prontos = [p for p in abertos if p.traduzido]
        if not prontos:
            return None
        por_cliente: dict[str, int] = {}
        for p in self.pedidos:
            por_cliente[p.cliente] = por_cliente.get(p.cliente, 0) + p.iniciados
        return "render", min(prontos, key=lambda p: (por_cliente[p.cliente], p.chegada, p.id))

    def trabalhar(self, pod: PodSim) -> None:
        if not pod.livre or not (pod.estado == "ativo" or pod.so_traducao):
            return
        if self.agora - pod.criado > MAX_POD_S:
            return self.sair(pod)
        item = self._proximo()
        if pod.so_traducao and (item is None or item[0] != "traduzir"):
            return  # só traduz até o PRONTO (o PRONTO chama de novo)
        if item is None:
            if pod.ocioso_desde is None:
                pod.ocioso_desde = self.agora
            if self.agora - pod.ocioso_desde >= pod.ocioso_s:
                return self.sair(pod)
            self.depois(self.agora + CONSULTA_WORKER_S, self.trabalhar, pod)
            return
        tipo, p = item
        pod.livre, pod.ocioso_desde = False, None
        if tipo == "traduzir":
            p.traduzindo = True
            s = TRADUCAO_1_S if pod.traducoes == 0 else TRADUCAO_S
            pod.traducoes += 1
            self.depois(self.agora + s, self.traduzido, pod, p, s)
            return
        p.iniciados += 1
        s = self.tempos.video(pod.gpu.nome, p.qualidade, p.duracao) * self.rng.lognormvariate(0.0, RUIDO)
        pod.andamento[p.id] = self.agora
        self.depois(self.agora + s, self.renderizado, pod, p, s)

    def traduzido(self, pod: PodSim, p: Pedido, s: float) -> None:
        p.traduzido, p.traduzindo = True, False
        pod.ocupado_s += s
        pod.livre = True
        self.trabalhar(pod)

    def renderizado(self, pod: PodSim, p: Pedido, s: float) -> None:
        pod.ocupado_s += s
        pod.andamento.pop(p.id, None)
        pod.livre = True
        if self.rng.random() < self.c.falha_conteudo:  # conferência reprovada: refeito com outra seed
            self.desperdicio_s += s
            p.iniciados -= 1
        else:
            self.entregar(p, s, CONFERE_S)
        self.trabalhar(pod)  # a GPU não espera a conferência nem o envio (`adiantar`)

    def entregar(self, p: Pedido, s_render: float, ja: float) -> None:
        t = ja + ENVIO_S
        while self.rng.random() < self.c.falha_envio:
            if not self.nova:  # antes de 14/10/2026: a falha final do envio refazia o vídeo na GPU
                self.desperdicio_s += s_render
                self.depois(self.agora + t, self._refazer, p)
                return
            t += min(32.0, 2.0 * (1 + t / 10)) + ENVIO_S  # a mesma entrega, de novo (o mesmo vídeo)
        self.depois(self.agora + t, self.entregue, p)

    def _refazer(self, p: Pedido) -> None:
        p.iniciados -= 1  # volta para a fila; um pod livre pega na próxima consulta

    def entregue(self, p: Pedido) -> None:
        p.prontos += 1
        p.primeiro = p.primeiro if p.primeiro is not None else self.agora
        p.ultimo = self.agora

    def sair(self, pod: PodSim) -> None:
        pod.estado = "drenando"
        if pod.ocioso_desde is not None:
            self.politica.desligou_ocioso(self.agora)
        espera = 0.0
        if pod.gravador:  # o cache é opcional, com o orçamento CACHE_MAX_USD (ADR 0016)
            from render2go_worker import lote_nuvem as ln

            espera = min(self.c.cache_s, ln.CACHE_MAX_USD / pod.gpu.usd_h * 3600, ln.ESPERA_GUARDAR_S)
        self.depois(self.agora + espera, self.fechar, pod)

    def fechar(self, pod: PodSim) -> None:
        pod.estado = "fechando"
        self.depois(self.agora + self.rng.uniform(*CONFIRMACAO_S), self.fechado, pod)

    def fechado(self, pod: PodSim) -> None:
        pod.estado, pod.fim_cobranca = "fechado", self.agora

    # -- resultado ----------------------------------------------------------------------------------------------
    def resultado(self, r: Resultado) -> None:
        for p in self.pedidos:
            if p.primeiro is not None:
                r.primeiro_s.append(p.primeiro - p.chegada)
            alvo = p.elegiveis if p.cancelado_em is None else p.iniciados
            if alvo and p.prontos >= alvo and p.ultimo is not None:
                r.lote_s.append(p.ultimo - p.chegada)
            r.videos += p.prontos
            r.nao_entregues += max(0, p.elegiveis - p.prontos) if p.cancelado_em is None else 0
        for pod in self.pods:
            fim = pod.fim_cobranca if pod.fim_cobranca is not None else self.c.horizonte_s
            ligado = fim - pod.criado
            r.custo_usd += pod.gpu.usd_h * ligado / 3600
            r.ociosidade_s += max(0.0, ligado - pod.ocupado_s)
        r.desperdicio_s += self.desperdicio_s
        r.pods += len(self.pods)


def politicas() -> dict[str, cap.PoliticaOciosidade]:
    return {
        "fixa 180 s (padrão)": cap.PoliticaOciosidade(curto_s=180),
        "fixa 60 s": cap.PoliticaOciosidade(curto_s=60),
        "histerese 60/180 s": cap.PoliticaOciosidade(curto_s=60, longo_s=180),
    }


def cenarios(rng: random.Random) -> list[Cenario]:
    """Os cenários pedidos na revisão de 14/10/2026 (docs/15, seção 3)."""

    def p(i, cliente, t, n, **kw):
        return Pedido(i, cliente, t, n, **kw)

    intermitente, t = [], 0.0
    for i in range(24):
        t += rng.expovariate(1 / 900)  # um pedido a cada ~15 min
        intermitente.append(p(i, f"c{i % 5}", t, rng.randint(3, 10)))
    rajada = [p(i, f"c{i % 3}", i * 12.0, 20) for i in range(10)]
    return [
        Cenario("fila pequena (1 pedido de 5 vídeos)", [p(0, "c0", 0, 5)], compara_capacidade=True),
        Cenario("rajada (10 pedidos de 20 em 2 min)", rajada, compara_capacidade=True),
        Cenario("chegada intermitente (24 pedidos de 3 a 10, ~15 min)", intermitente),
        Cenario(
            "GPUs heterogêneas (L40S ou RTX PRO 6000)",
            [p(i, f"c{i % 3}", i * 12.0, 20) for i in range(10)],
            gpus=[Gpu(L40S.nome, L40S.usd_h, 0.5), PRO6000],
            compara_capacidade=True,
        ),
        Cenario("foto bloqueada (metade recusada)", [p(0, "c0", 0, 10, bloqueados=5), p(1, "c1", 30, 6)]),
        Cenario(
            "tradução pendente (720p, 4 pedidos ao mesmo tempo)",
            [p(i, f"c{i}", 0, 4, qualidade="alta") for i in range(4)],
            compara_capacidade=True,
        ),
        Cenario("saldo insuficiente (paga 3 de 12)", [p(0, "c0", 0, 12, pagos=3), p(1, "c1", 60, 4)]),
        Cenario("cancelamento durante a subida", [p(0, "c0", 0, 30, cancelado_em=90.0), p(1, "c1", 120, 3)]),
        Cenario("envio instável (20% de falha)", [p(i, f"c{i % 2}", i * 30.0, 10) for i in range(4)], falha_envio=0.2),
        Cenario("cache lento (o 1º pod grava 15 min)", [p(0, "c0", 0, 8)], cache_s=900),
    ]


def simular(sementes: int = 30) -> list[dict]:
    out = []
    base = cenarios(random.Random(14102026))
    for c in base:
        for nome, _ in politicas().items():
            variantes = [(True, "nova")]
            if c.falha_envio:
                variantes.append((False, "nova"))
            if nome.startswith("fixa 180") and c.compara_capacidade:
                variantes.append((True, "antiga"))
            for nova, regra in variantes:
                entrega = "nova (repete o mesmo vídeo)" if nova else "antiga (refaz na GPU)"
                r = Resultado(c.nome, nome + ("" if regra == "nova" else "; capacidade antiga"), entrega)
                for s in range(sementes):
                    pol = politicas()[nome]  # estado novo a cada semente
                    sim = Sim(c, pol, nova, s, regra)
                    sim.rodar()
                    sim.resultado(r)
                out.append(r.resumo())
    return out


def tabela(linhas: list[dict]) -> str:
    def fmt(d: dict) -> str:
        if not d.get("n"):
            return "—"
        return f"{d['p50']:.0f}" + (f" / {d['p95']:.0f}" if "p95" in d else " / (n<20)") + f" (n={d['n']})"

    cab = (
        "| Cenário | Política | Entrega | 1º vídeo p50/p95 (s) | Pedido p50/p95 (s) | GPU ociosa (s) "
        "| Render desperdiçado (s) | Custo (US$) | US$/vídeo | Pods |\n|---|---|---|---|---|---|---|---|---|---|\n"
    )
    return cab + "".join(
        f"| {x['cenario']} | {x['politica']} | {x['entrega']} | {fmt(x['primeiro_video_s'])} | {fmt(x['lote_s'])} "
        f"| {x['gpu_ociosa_s']} | {x['render_desperdicado_s']} | {x['custo_usd']:.2f} | "
        f"{x['custo_por_video_usd'] if x['custo_por_video_usd'] is not None else '—'} | {x['pods']} |\n"
        for x in linhas
    )


def main(argv: list[str] | None = None) -> int:
    a = argparse.ArgumentParser(prog="render2go-simulacao", description=__doc__.split("\n")[0])
    a.add_argument("--sementes", type=int, default=30, help="repetições de cada cenário (somadas)")
    a.add_argument("--json", help="grava o resultado em JSON")
    args = a.parse_args(argv)
    linhas = simular(args.sementes)
    sys.stdout.write(tabela(linhas))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"sementes": args.sementes, "linhas": linhas}, f, indent=1, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
