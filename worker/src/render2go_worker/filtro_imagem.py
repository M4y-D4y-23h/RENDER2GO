"""Filtro de imagem nos quadros do vídeo gerado (ADR 0005, 2ª porta): a última barreira da entrega.

O C1 olha só o texto, e um texto inocente com outra intenção ("tira o casaco devagar") ou uma foto
sugestiva podem gerar nudez (visto pelo dono em 11/10/2026, nos testes do plano interno). Antes de
enviar o vídeo, o worker passa 1 a cada 4 quadros (a cada 0,25 s, incluindo o 1º, que é a própria
foto, e o último) por um classificador de imagem e **não envia** o vídeo em que algum quadro passa
do limiar. Classificador ausente ou com erro: nada é enviado (fail-closed), e a API recusa "ok" sem
a nota (`render2go_servico.api`, concluir).

Classificador: `Freepik/nsfw_image_detector` (EVA02-base 448 px, licença MIT, base
`timm/eva02_base_patch14_448`, MIT), 4 níveis: neutral, low (sugestivo), medium, high. Escolhido em
11/10/2026 por acertar mais em imagem gerada por IA que os outros candidatos do ADR 0005, segundo
o cartão do modelo (medium 96%, high 100%, neutral 100%; o Falconsai erra 31% do "medium" em IA).
Arquivos fixados por commit e SHA-256 no `models.lock.json` (`nsfw_freepik_*`). Nas 4 fotos SFW do
repositório (fixtures/sfw), a maior nota foi 0,013; em vídeos de teste feitos com elas, 0,017.

A regra (`barrado`), desde 12/10/2026, a pedido do dono ("muitos vídeos bloqueados por motivos
bestas"):
- **nota** = a maior média de P(medium) + P(high) em JANELA quadros amostrados seguidos (0,25 s de
  vídeo): barra com nota ≥ LIMIAR (0,5, o limiar do cartão do modelo para o nível "medium");
- **nota_alta** = o maior P(high) (explícito) num quadro só: barra com nota_alta ≥ LIMIAR_ALTO
  (0,35), mesmo num quadro isolado.
Até 12/10, bastava um quadro com P(medium) + P(high) ≥ 0,35 (abaixo dos 0,5 do cartão): com 13 a 29
quadros por vídeo, um quadro de transição (borrão, mão, comida, tecido) já barrava o vídeo. Os dois
limiares podem mudar no .env (RENDER2GO_FILTRO_LIMIAR e RENDER2GO_FILTRO_LIMIAR_ALTO), só dentro de
LIMITES: o filtro nunca é desligado por configuração. A mesma regra vale para a foto de entrada,
avaliada no servidor antes de ir a um pod (render2go_servico.filtro_fotos): a foto é o 1º quadro do
vídeo, e uma foto parada é a mesma imagem em todos os quadros da janela.

Regra de teste (projeto e ADR 0005): nada explícito é buscado, gerado ou guardado para testar; o
caminho de bloqueio é testado com um classificador falso, e a taxa de acerto vem do cartão.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

LIMIAR = 0.5  # P(medium) + P(high), na média de JANELA quadros seguidos: o vídeo não é entregue
LIMIAR_ALTO = 0.35  # P(high) (explícito) num quadro só: não é entregue
JANELA = 2  # quadros amostrados seguidos (0,25 s de vídeo a 1 em cada 4 quadros, 16 fps)
# o que o .env pode escolher: mais rígido ou um pouco mais solto, nunca desligado
LIMITES = {"RENDER2GO_FILTRO_LIMIAR": (0.3, 0.8), "RENDER2GO_FILTRO_LIMIAR_ALTO": (0.2, 0.6)}
LADO = 448  # entrada fixa do EVA02 (o cartão: redimensiona sem cortar, bicúbico)
PASSO = 4  # 1 quadro a cada 4 (16 fps → a cada 0,25 s); 16·d é múltiplo de 4: o último entra
ARQUITETURA = "eva02_base_patch14_448"
PASTA = "classificadores/nsfw_image_detector"  # em /opt/comfyui/models (models.lock.json)
MEDIA = (0.48145466, 0.4578275, 0.40821073)
DESVIO = (0.26862954, 0.26130258, 0.27577711)


class FiltroError(Exception):
    """O filtro não conseguiu avaliar o vídeo (fail-closed: nada é enviado)."""


def _limiar(nome: str, padrao: float) -> float:
    """O limiar do .env, dentro de LIMITES (fora deles, o mais próximo, com aviso no log)."""
    bruto = os.environ.get(nome, "").strip().replace(",", ".")
    if not bruto:
        return padrao
    minimo, maximo = LIMITES[nome]
    try:
        valor = float(bruto)
    except ValueError:
        print(f"AVISO: {nome}={bruto!r} não é número: usando {padrao}", file=sys.stderr)
        return padrao
    if not minimo <= valor <= maximo:
        valor = min(max(valor, minimo), maximo)
        print(f"AVISO: {nome}={bruto} fora de {minimo}–{maximo}: usando {valor}", file=sys.stderr)
    return valor


def limiares() -> tuple[float, float]:
    """(LIMIAR, LIMIAR_ALTO) que valem agora: os do .env (dentro de LIMITES) ou os padrões. A API, o
    pod (recebe do orquestrador no fila.env) e o filtro das fotos leem o mesmo .env."""
    return (
        _limiar("RENDER2GO_FILTRO_LIMIAR", LIMIAR),
        _limiar("RENDER2GO_FILTRO_LIMIAR_ALTO", LIMIAR_ALTO),
    )


def barrado(nota: float, nota_alta: float) -> bool:
    """A regra do filtro (vídeo e foto): nota da janela ≥ LIMIAR ou nota explícita ≥ LIMIAR_ALTO."""
    limiar, limiar_alto = limiares()
    return nota >= limiar or nota_alta >= limiar_alto


@dataclass(frozen=True)
class Resultado:
    nota: float  # maior média de P(medium) + P(high) em JANELA quadros seguidos
    nota_alta: float  # maior P(high) (explícito) num quadro só
    nota_quadro: float  # maior P(medium) + P(high) num quadro só (a nota de antes de 12/10/2026)
    nota_baixa: (
        float  # maior P(low) + P(medium) + P(high) (sugestivo; só informação para o revisor)
    )
    quadros: int
    pior_quadro: int  # índice (no vídeo) do quadro com a maior P(medium) + P(high)

    @property
    def barrado(self) -> bool:
        return barrado(self.nota, self.nota_alta)


def notas(probs: list[tuple[float, float, float, float]]) -> tuple[float, float]:
    """(nota, nota_alta) de uma sequência de quadros: a janela de P(medium) + P(high) e o P(high)
    máximo. Um quadro só (foto) vale como uma foto parada: a janela é ele mesmo."""
    m = [p[2] + p[3] for p in probs]
    janela = [sum(m[i : i + JANELA]) / JANELA for i in range(len(m) - JANELA + 1)] or m
    return max(janela), max(p[3] for p in probs)


def notas_foto(probs: list[tuple[float, float, float, float]]) -> tuple[float, float]:
    """(nota, nota_alta) de uma foto avaliada em mais de uma versão (inteira e no recorte do vídeo):
    cada versão é uma foto parada (a janela é ela mesma), e vale a pior."""
    return max(p[2] + p[3] for p in probs), max(p[3] for p in probs)


class Classificador(Protocol):
    def probabilidades(self, quadros: list[bytes]) -> list[tuple[float, float, float, float]]:
        """(neutral, low, medium, high) de cada quadro RGB LADO×LADO (bytes rgb24)."""
        ...


def quadros(video: Path, passo: int = PASSO, lado: int = LADO) -> list[tuple[int, bytes]]:
    """(índice no vídeo, RGB lado×lado) de 1 a cada `passo` quadros, redimensionados sem corte."""
    filtro = f"select='not(mod(n\\,{passo}))',scale={lado}:{lado}:flags=bicubic"
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(video), "-vf", filtro]
        + ["-fps_mode", "vfr", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
        timeout=300,
    )
    n = lado * lado * 3
    if r.returncode != 0 or not r.stdout or len(r.stdout) % n:
        raise FiltroError(f"não leu os quadros de {video.name}: {r.stderr.decode()[-200:]}")
    return [(i * passo, r.stdout[i * n : (i + 1) * n]) for i in range(len(r.stdout) // n)]


def avaliar(video: Path, classificador: Classificador) -> Resultado:
    qs = quadros(video)
    try:
        probs = classificador.probabilidades([q for _, q in qs])
    except FiltroError:
        raise
    except Exception as e:  # noqa: BLE001 (qualquer falha do classificador = não avaliado)
        raise FiltroError(f"classificador falhou: {e}") from e
    if len(probs) != len(qs) or not probs:
        raise FiltroError("classificador sem resposta para os quadros")
    m = [p[2] + p[3] for p in probs]
    pior = max(range(len(m)), key=m.__getitem__)
    nota, nota_alta = notas(probs)
    return Resultado(
        nota=round(nota, 4),
        nota_alta=round(nota_alta, 4),
        nota_quadro=round(max(m), 4),
        nota_baixa=round(max(p[1] + p[2] + p[3] for p in probs), 4),
        quadros=len(qs),
        pior_quadro=qs[pior][0],
    )


class Freepik:
    """O classificador de verdade (PyTorch + timm), carregado na 1ª chamada. Na GPU (pods), em bf16
    (como foi treinado), milissegundos por quadro; na CPU, em fp32: ~2,1 s por imagem com 1 thread e
    ~1,15 s com 2, e ~870 MB de memória (medido em 12/10/2026). Na CPU, é o filtro das fotos no
    servidor (render2go_servico.filtro_fotos), com uma ou duas imagens por foto."""

    def __init__(self, pasta: Path, dispositivo: str = "auto"):
        self.pasta, self.dispositivo = Path(pasta), dispositivo
        self._modelo = None
        self._lock = threading.Lock()

    def _carregar(self):
        try:
            import timm
            import torch
            from safetensors.torch import load_file
        except ImportError as e:
            raise FiltroError(f"dependência do classificador ausente: {e}") from e
        pesos = self.pasta / "model.safetensors"
        if not pesos.is_file():
            raise FiltroError(f"classificador ausente: {pesos}")
        disp = self.dispositivo
        if disp == "auto":
            disp = "cuda" if torch.cuda.is_available() else "cpu"
        tipo = torch.bfloat16 if disp.startswith("cuda") else torch.float32
        m = timm.create_model(ARQUITETURA, pretrained=False, num_classes=4, global_pool="avg")
        m.load_state_dict(load_file(str(pesos)))  # strict: arquivo errado não carrega
        self._modelo = (m.eval().to(disp, tipo), torch, disp, tipo)

    def carregar(self) -> None:
        """Carrega já (o worker chama em segundo plano no PRONTO: na 1ª chamada, importar o PyTorch
        e carregar o modelo levou 11–17 s em 11/10/2026, e o 1º vídeo esperava)."""
        with self._lock:
            if self._modelo is None:
                self._carregar()

    def probabilidades(self, quadros: list[bytes]) -> list[tuple[float, float, float, float]]:
        with self._lock:  # um ComfyUI por GPU, mas um classificador só no processo
            if self._modelo is None:
                self._carregar()
            m, torch, disp, tipo = self._modelo
            media = torch.tensor(MEDIA).view(1, 3, 1, 1)
            desvio = torch.tensor(DESVIO).view(1, 3, 1, 1)
            x = torch.frombuffer(bytearray(b"".join(quadros)), dtype=torch.uint8)
            x = x.view(len(quadros), LADO, LADO, 3).permute(0, 3, 1, 2).float() / 255
            x = ((x - media) / desvio).to(disp, tipo)
            with torch.inference_mode():
                p = torch.softmax(m(x).float(), dim=-1).cpu().tolist()
            if disp.startswith("cuda"):
                torch.cuda.empty_cache()  # devolve a memória ao ComfyUI e ao Ollama
            return [tuple(q) for q in p]
