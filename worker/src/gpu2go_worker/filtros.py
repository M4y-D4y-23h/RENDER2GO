"""Filtro C1 do texto (regras e listas; doc 03, seção 5) para o serviço SFW (ADR 0004).

Normaliza o texto (minúsculas, sem acento, troca de números e símbolos usados para
disfarçar letras, pontuação vira espaço) e procura termos e expressões inteiras de
`filtros/c1.v1.json`. Bloqueia na dúvida (fail-closed). Nunca usa o compilador, que é um
modelo abliterado.

O C2 (classificador) ainda não existe. No modo `c1-revisao-humana` (ADR 0004), o C1 roda
na API e no worker, e o lote só chega à GPU depois da aprovação humana registrada no
banco. O worker exige o carimbo de aprovação no job.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from gpu2go_worker import WORKER_DIR
from gpu2go_worker.safety import Decision, Filters

ARQUIVO = WORKER_DIR / "filtros" / "c1.v1.json"
MODO = "c1-revisao-humana"
DISFARCE = str.maketrans(
    {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}
)


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).translate(DISFARCE)
    t = re.sub(r"[^a-z\s-]", " ", t).replace("-", " ")
    return " " + re.sub(r"\s+", " ", t).strip() + " "


@lru_cache(maxsize=2)
def _termos(arquivo: str | Path = ARQUIVO) -> dict[str, list[str]]:
    dados = json.loads(Path(arquivo).read_text())
    return {
        cat: [normalizar(t).strip() for t in termos] for cat, termos in dados["categorias"].items()
    }


def avaliar(texto: str) -> Decision:
    alvo = normalizar(texto)
    # versão "colada" pega "p e l a d o" e "p.e.l.a.d.o"
    colado = " " + re.sub(r"(?<=\b\w) (?=\w\b)", "", alvo.strip()) + " "
    for cat, termos in _termos().items():
        for termo in termos:
            if f" {termo} " in alvo or f" {termo} " in colado:
                return Decision(False, f"C1: termo bloqueado ({cat})")
    return Decision(True, "C1: ok")


class FiltrosC1(Filters):
    """C1 nos dois pontos (pedido e prompt); a aprovação humana substitui o C2 (ADR 0004)."""

    mode = MODO
    exige_aprovacao = True

    def check_request(self, text_pt: str) -> Decision:
        return avaliar(text_pt)

    def check_prompt(self, prompt_en: str) -> Decision:
        return avaliar(prompt_en)
