"""Legenda de cada foto (ADR 0014, proposto; a combinação D da rodada de 13/10/2026).

O Qwen2.5-VL-7B-Instruct (Apache 2.0; o 3B não serve, licença não comercial, `docs/12`) olha a foto
como o vídeo vai mostrá-la (o 1º quadro, já no formato do vídeo) e devolve, num JSON de esquema fixo
(`format` do Ollama):
- uma descrição curta e objetiva, em inglês (vai para o tradutor e para o painel, nunca para o
  cliente);
- a categoria da foto, uma das receitas v4 (comida, produto, imovel, roupa, carro, pessoa, geral);
- o assunto principal em inglês ("the chocolate cake"), que o movimento padrão usa;
- se aparece uma pessoa e se há rótulo ou texto.

Roda num 2º Ollama, só em CPU (`OLLAMA_CPU`, 127.0.0.1:11435, e `num_gpu` 0): zero VRAM por
construção, em paralelo com a geração do vídeo anterior.

O código confere tudo o que volta (`conferir`). Fora do esquema, incoerente (pessoa no texto e
"people" falso, categoria "pessoa" sem pessoa) ou com caracteres que não cabem num prompt: a foto
segue sem legenda (a categoria do estilo, como antes). Nada bloqueia o pedido por isso, e a legenda
nunca é camada de segurança: o filtro das fotos, o C1 e as conferências do vídeo continuam iguais. A
regra da pessoa inventada só fica mais rígida com ela (vale se a análise do servidor **ou** a
legenda diz que não há pessoa).
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from render2go_worker import compiler, preparo, traducao

VERSAO = 1  # worker/prompts/legenda.v<N>.txt
MODELO = "qwen2.5vl:7b"  # digest da camada de pesos: LEGENDA_MODEL_DIGEST no worker/Dockerfile
OLLAMA_CPU = "http://127.0.0.1:11435"  # o 2º Ollama do pod, sem GPU (entrypoint.sh)
# as categorias das receitas v4
CATEGORIAS = ("comida", "produto", "imovel", "roupa", "carro", "pessoa", "geral")
LADO_MAX = 512  # a foto vai reduzida: categoria, assunto e pessoa não pedem mais, e a CPU agradece
MAX_PALAVRAS_DESCRICAO = 40
MAX_CARACTERES_DESCRICAO = 300
MAX_PALAVRAS_ASSUNTO = 7  # "the" + até 6
ESQUEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "category": {"type": "string", "enum": list(CATEGORIAS)},
        "subject": {"type": "string"},
        "people": {"type": "boolean"},
        "text": {"type": "boolean"},
    },
    "required": ["description", "category", "subject", "people", "text"],
}
# o assunto entra no prompt do vídeo sem passar pelo tradutor (movimento padrão): só letras sem
# acento, números, espaço, hífen e apóstrofo, nada que pareça uma instrução ou um texto entre aspas
ASSUNTO = re.compile(r"^the [a-z0-9][a-z0-9' -]*[a-z0-9]$")
# a descrição vai para o tradutor numa linha própria: sem aspas (o tradutor copia o texto entre
# aspas ao pé da letra), sem quebra de linha e sem caracteres de controle
ASPAS = re.compile(r"[\"“”„‟«»`]")
CONTROLE = re.compile(r"[\x00-\x1f\x7f]")


class LegendaError(Exception):
    pass


@dataclass(frozen=True)
class Legenda:
    descricao: str
    categoria: str
    assunto: str
    pessoas: bool
    texto: bool
    modelo: str = MODELO
    versao: int = VERSAO
    segundos: float = 0.0

    def como_dict(self) -> dict[str, Any]:
        """Para o registro do pedido (API): o que o painel e a auditoria mostram."""
        return asdict(self)


def system_prompt(versao: int = VERSAO) -> str:
    path = compiler.PROMPTS_DIR / f"legenda.v{versao}.txt"
    if not path.is_file():
        raise LegendaError(f"prompt inexistente: {path.name}")
    return path.read_text().strip()


def imagem_para_o_modelo(foto: Path, lado_max: int = LADO_MAX) -> bytes:
    """A foto em JPEG, no máximo `lado_max` no lado maior, com a rotação do EXIF e a transparência
    como o vídeo as vê (`preparo.rgb`). Só os leitores de JPEG, PNG e WebP do Pillow, e o tamanho
    conferido no cabeçalho antes de decodificar (a mesma regra do `media.check_image`)."""
    try:
        with Image.open(foto, formats=("JPEG", "PNG", "WEBP")) as im:
            if im.width * im.height > preparo.MAX_PIXELS:
                raise LegendaError(f"foto grande demais ({im.width}x{im.height})")
            im = preparo.rgb(im)
            im.thumbnail((lado_max, lado_max), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            im.save(out, "JPEG", quality=90)
    except (OSError, Image.DecompressionBombError) as e:
        raise LegendaError(f"foto ilegível: {e}") from e
    return out.getvalue()


def _assunto(valor: Any) -> str:
    if not isinstance(valor, str) or CONTROLE.search(valor):  # quebra de linha: não é um assunto
        raise LegendaError("assunto fora do esquema")
    s = re.sub(r"\s+", " ", valor).strip().strip(".,;:!?").strip().lower()
    s = re.sub(r"^(?:an?|the)\s+", "", s)
    s = f"the {s}"
    if not ASSUNTO.match(s) or len(s.split()) > MAX_PALAVRAS_ASSUNTO or len(s) > 60:
        raise LegendaError(f"assunto fora do formato: {valor[:80]!r}")
    return s


def _descricao(valor: Any) -> str:
    if not isinstance(valor, str):
        raise LegendaError("descrição fora do esquema")
    d = re.sub(r"\s+", " ", ASPAS.sub("", CONTROLE.sub(" ", valor))).strip()
    if len(d) < 3 or len(d) > MAX_CARACTERES_DESCRICAO or len(d.split()) > MAX_PALAVRAS_DESCRICAO:
        raise LegendaError(f"descrição de {len(d)} caracteres e {len(d.split())} palavras")
    if compiler.NON_ENGLISH.search(d):
        raise LegendaError("descrição fora do inglês")
    return d


def conferir(bruto: Any, *, modelo: str = MODELO, segundos: float = 0.0) -> Legenda:
    """A resposta do modelo, conferida campo a campo. Qualquer dúvida é LegendaError: a foto segue
    sem legenda (nunca uma legenda pela metade)."""
    if not isinstance(bruto, dict) or set(bruto) != set(ESQUEMA["required"]):
        raise LegendaError("resposta fora do esquema")
    categoria = bruto["category"]
    if categoria not in CATEGORIAS:
        raise LegendaError(f"categoria desconhecida: {str(categoria)[:40]!r}")
    if not isinstance(bruto["people"], bool) or not isinstance(bruto["text"], bool):
        raise LegendaError("people e text precisam ser verdadeiro ou falso")
    leg = Legenda(
        descricao=_descricao(bruto["description"]),
        categoria=categoria,
        assunto=_assunto(bruto["subject"]),
        pessoas=bruto["people"],
        texto=bruto["text"],
        modelo=modelo,
        segundos=round(segundos, 2),
    )
    citadas = traducao.prompt_cita_pessoa(f"{leg.descricao} {leg.assunto}")
    if leg.categoria == "pessoa" and not leg.pessoas:
        raise LegendaError("categoria pessoa sem pessoa na foto")
    if citadas and not leg.pessoas:
        raise LegendaError(f"a descrição cita pessoa ({', '.join(citadas)}) e people é falso")
    return leg


def de_dict(d: Any) -> Legenda:
    """A legenda como o pod a manda à API e a API a guarda (`Legenda.como_dict`), conferida de
    novo com as mesmas regras de `conferir` (a API não confia no que chega). Fora do formato:
    LegendaError."""
    if not isinstance(d, dict):
        raise LegendaError("legenda fora do formato")
    bruto = {
        "description": d.get("descricao"),
        "category": d.get("categoria"),
        "subject": d.get("assunto"),
        "people": d.get("pessoas"),
        "text": d.get("texto"),
    }
    modelo = d.get("modelo") if isinstance(d.get("modelo"), str) else MODELO
    segundos = d.get("segundos") if isinstance(d.get("segundos"), int | float) else 0.0
    leg = conferir(bruto, modelo=modelo[:80], segundos=max(0.0, min(float(segundos), 3600.0)))
    versao = d.get("versao")
    if isinstance(versao, bool) or not isinstance(versao, int) or not 1 <= versao <= 99:
        raise LegendaError("versão do prompt da legenda fora do formato")
    return Legenda(**{**leg.como_dict(), "versao": versao})


def legendar(
    foto: Path,
    *,
    ollama_url: str = OLLAMA_CPU,
    model: str = MODELO,
    timeout: float = 180.0,
    seed: int = 0,
    keep_alive: str | int = -1,
    lado_max: int = LADO_MAX,
    threads: int | None = None,
) -> Legenda:
    """A legenda de uma foto (já baixada e conferida). Erro de rede, do Ollama ou fora do esquema:
    LegendaError. `threads`: as da CPU que o modelo usa (None: o Ollama escolhe)."""
    imagem = imagem_para_o_modelo(foto, lado_max)
    payload = {
        "model": model,
        "system": system_prompt(),
        "prompt": "Describe this photo.",
        "images": [base64.b64encode(imagem).decode()],
        "format": ESQUEMA,
        "stream": False,
        # num_gpu 0: só CPU, mesmo que o Ollama de CPU um dia enxergue uma GPU
        "options": {
            "temperature": 0,
            "seed": seed,
            "num_ctx": 4096,
            "num_predict": 256,
            "num_gpu": 0,
        },
        "keep_alive": keep_alive,
    }
    if threads:
        payload["options"]["num_thread"] = int(threads)
    t0 = time.monotonic()
    try:
        r = requests.post(f"{ollama_url.rstrip('/')}/api/generate", json=payload, timeout=timeout)
        r.raise_for_status()
        bruto = json.loads(r.json().get("response") or "null")
    except (requests.RequestException, ValueError) as e:
        raise LegendaError(
            f"Ollama de CPU inacessível ou resposta ilegível em {ollama_url}: {e}"
        ) from e
    return conferir(bruto, modelo=model, segundos=time.monotonic() - t0)


def tentar(foto: Path, **kw: Any) -> Legenda | None:
    """`legendar` sem nunca falhar o pedido: sem legenda, a foto segue com a categoria do estilo."""
    try:
        return legendar(foto, **kw)
    except LegendaError as e:
        print(f"[legenda] AVISO: a foto segue sem legenda: {e}", file=sys.stderr, flush=True)
        return None


def cpus_do_conteiner(cgroup: Path = Path("/sys/fs/cgroup")) -> int:
    """As CPUs que este contêiner usa de fato: o menor entre a afinidade do processo e a cota do
    cgroup (v2 `cpu.max`; v1 `cpu.cfs_quota_us` e `cpu.cfs_period_us`). No pod do RunPod,
    `os.cpu_count()` dá as do host (128 num L40S, em 12/10/2026): com ele, o Ollama de CPU abriria
    dezenas de threads numa cota bem menor e disputaria a CPU com o ComfyUI e a conferência."""
    n = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 1
    cotas = []
    try:
        cota, periodo = (cgroup / "cpu.max").read_text().split()[:2]
        if cota != "max":
            cotas.append((int(cota), int(periodo)))
    except (OSError, ValueError):
        pass
    for pasta in (cgroup / "cpu", cgroup / "cpu,cpuacct"):
        try:
            cota_v1 = int((pasta / "cpu.cfs_quota_us").read_text())
            cotas.append((cota_v1, int((pasta / "cpu.cfs_period_us").read_text())))
        except (OSError, ValueError):
            pass
    for cota, periodo in cotas:
        if cota > 0 and periodo > 0:
            n = min(n, cota // periodo)
    return max(1, n)


def threads_da_legenda(cgroup: Path = Path("/sys/fs/cgroup")) -> int:
    """Metade das CPUs do contêiner (no mínimo 2): a outra metade fica com o ComfyUI e com a
    conferência e o envio do vídeo anterior, que correm ao mesmo tempo."""
    return max(2, cpus_do_conteiner(cgroup) // 2)
