"""Capacidade da fila (14/10/2026): quanto trabalho de GPU a fila tem, quantos pods ela pede e por quanto tempo
um pod sem pedidos espera antes de sair. Funções puras: o orquestrador (render2go_servico.orquestrador) e a
simulação (render2go_servico.simulacao) usam as mesmas.

O que mudou em relação à regra de antes (`Orquestrador.desejados` até 14/10/2026):
- a tradução de um pedido não conta mais como um vídeo inteiro (contava: `Banco.trabalho` somava o job
  "compilar" nos pendentes); ela conta TRADUCAO_S, e um pedido que só espera a tradução ainda pede um pod;
- um vídeo em andamento conta o que falta dele (pelo tempo desde que começou), não um vídeo inteiro, e nunca
  menos que FRACAO_MIN_ANDAMENTO (pode estar travado ou no fim);
- o tempo por vídeo vem do histórico de cada GPU, qualidade e duração quando há amostras (mediana, pelo menos
  MIN_AMOSTRAS), sempre dentro de LIMITES_HISTORICO da tabela medida (`lote_nuvem.MEDIDO_S`); sem amostras, a
  tabela ou o pior caso (GPU sem medida);
- os pods ligados contam pela velocidade da GPU deles em relação à 1ª GPU da lista (a referência): uma RTX PRO
  6000 vale ~2 L40S.

Nada disso muda a ordem da fila, a justiça entre clientes, o saldo, o filtro das fotos ou a aprovação: só o número
de pods que a fila pede.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field

from render2go_worker import lote
from render2go_worker import lote_nuvem as ln

TRADUCAO_S = 60.0  # 1ª tradução de um pedido no pod: o tradutor carrega (57,5 s medidos em 13/10); depois, ~5 s
FRACAO_MIN_ANDAMENTO = 0.25
MIN_AMOSTRAS = 5
LIMITES_HISTORICO = (0.5, 2.0)  # o histórico só move o tempo da tabela dentro desta faixa


@dataclass
class Tempos:
    """Segundos de pod por vídeo, por GPU, qualidade e duração. `historico`: (gpu, qualidade, duracao) → lista de
    medidas (jobs.segundos dos vídeos entregues)."""

    historico: dict[tuple[str, str, int], list[float]] = field(default_factory=dict)

    def tabela(self, gpu: str, qualidade: str, duracao: int) -> float:
        return ln.segundos_por_video(gpu, qualidade, duracao)

    def video(self, gpu: str, qualidade: str, duracao: int) -> float:
        base = self.tabela(gpu, qualidade, duracao)
        amostras = self.historico.get((gpu, qualidade, int(duracao))) or []
        if len(amostras) < MIN_AMOSTRAS:
            return base
        baixo, alto = LIMITES_HISTORICO
        return min(max(statistics.median(amostras), baixo * base), alto * base)

    def amostras(self, gpu: str, qualidade: str, duracao: int) -> int:
        return len(self.historico.get((gpu, qualidade, int(duracao))) or [])


@dataclass
class Trabalho:
    """A fila em GPU-segundos da GPU de referência, e as partes."""

    renders: int = 0  # vídeos esperando (com o prompt pronto ou esperando a tradução)
    andamento: int = 0  # vídeos num pod agora
    traducoes: int = 0  # traduções de pedido (ou de um texto próprio) esperando ou em andamento
    segundos: float = 0.0  # tudo: o que espera, o que falta dos em andamento e as traduções

    @property
    def videos(self) -> int:
        return self.renders + self.andamento

    @property
    def vazio(self) -> bool:
        return not (self.renders or self.andamento or self.traducoes)


def _pedido(t: dict) -> dict:
    p = t.get("pedido") or {}
    return json.loads(p) if isinstance(p, str) else p


def trabalho(linhas: Iterable[dict], gpu_ref: str, tempos: Tempos, receitas: dict | None = None) -> Trabalho:
    """A fila de `Banco.trabalho` (por pedido: `pendentes` e `em_andamento` são vídeos; `traducoes`, `andamento_s`
    o tempo já gasto nos em andamento). Linhas sem `traducoes` (o formato de antes) contam só os vídeos."""
    receitas = receitas or lote.load_receitas()
    r = Trabalho()
    for t in linhas:
        p = _pedido(t)
        dur = int(p.get("duracao") or receitas["receitas"][p.get("receita", "produto-close")]["duracao"])
        s = tempos.video(gpu_ref, p.get("qualidade", "rapida"), dur)
        pend, anda = int(t.get("pendentes") or 0), int(t.get("em_andamento") or 0)
        trad = int(t.get("traducoes") or 0)
        gasto = float(t.get("andamento_s") or 0.0)
        falta = max(anda * s * FRACAO_MIN_ANDAMENTO, anda * s - gasto) if anda else 0.0
        r.renders += pend
        r.andamento += anda
        r.traducoes += trad
        r.segundos += pend * s + falta + trad * TRADUCAO_S
    return r


def pods_desejados(t: Trabalho, prazo_min: int | None, max_pods: int) -> int:
    """Quantos pods a fila pede: a regra do `lote_nuvem --pods auto` (cada pod com pelo menos 10 min de trabalho
    ou, com prazo, o mínimo que cumpre o prazo), sobre o trabalho em GPU-segundos; nunca mais pods que vídeos; sem
    vídeo, uma tradução pendente ainda pede um pod (a tradução nunca fica sem capacidade)."""
    if t.vazio:
        return 0
    pods, _ = ln.escolher_pods(1, max(1, t.videos), 1, t.segundos, prazo_min, max_pods=max_pods)
    return min(pods, max_pods)


def equivalentes(gpus_ligadas: Iterable[str], subindo: int, gpu_ref: str, tempos: Tempos) -> float:
    """Capacidade dos pods que pegam vídeos, em pods da GPU de referência: cada pod ligado pela velocidade da GPU
    dele (480p, 5 s, a medida mais comum); os que sobem, como a referência (é a 1ª GPU tentada)."""
    ref = tempos.video(gpu_ref, "rapida", 5)
    total = float(subindo)
    for g in gpus_ligadas:
        total += ref / tempos.video(g or gpu_ref, "rapida", 5)
    return total


@dataclass
class PoliticaOciosidade:
    """Quanto tempo o worker de um pod espera sem pedidos antes de sair (o "pod quente", docs/08).

    Fixa (o padrão, como sempre: `curto_s`, RENDER2GO_POD_OCIOSO_MIN = 3 min) ou com histerese (`longo_s`, desligada
    por padrão; RENDER2GO_POD_OCIOSO_LONGO_MIN): se a demanda voltar até `janela_s` depois de um pod ter saído por
    falta de pedidos, os próximos `episodios` episódios de demanda usam `longo_s` (a volta rápida mostrou que valia
    esperar); sem nova volta rápida, volta para `curto_s`. Assim, `curto_s` pode ser menor (ex.: 60 s) sem ligar e
    desligar pods a cada pedido de um cliente que manda vários seguidos. A escolha entre 60 e 180 s depende do
    custo da subida e da ociosidade (simulação: `render2go_servico.simulacao`; docs/15, seção 3)."""

    curto_s: float = 180.0
    longo_s: float | None = None
    janela_s: float = 600.0
    episodios: int = 3
    _desligou_em: float | None = None
    _longos: int = 0

    def ocioso_s(self) -> float:
        return float(self.longo_s) if self.longo_s and self._longos > 0 else float(self.curto_s)

    def desligou_ocioso(self, agora: float) -> None:
        self._desligou_em = agora

    def nova_demanda(self, agora: float) -> None:
        if not self.longo_s:
            return
        rapida = self._desligou_em is not None and agora - self._desligou_em <= self.janela_s
        if rapida:
            self._longos = self.episodios
        elif self._longos > 0:
            self._longos -= 1
        self._desligou_em = None

    @property
    def descricao(self) -> str:
        if not self.longo_s:
            return f"fixa ({self.curto_s:.0f} s)"
        return (
            f"histerese ({self.curto_s:.0f} s; {self.longo_s:.0f} s por {self.episodios} episódio(s) depois de a "
            f"demanda voltar em até {self.janela_s:.0f} s)"
        )
