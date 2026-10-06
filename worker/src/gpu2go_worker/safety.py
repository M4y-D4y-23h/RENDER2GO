"""Ponto de entrada dos filtros de texto C1–C2 no worker (doc 03, seção 5).

Os filtros chegam na Etapa 4. Até lá, a regra é fail-closed: sem filtros configurados,
o worker não processa job nenhum. A única exceção é GPU2GO_SAFETY=disabled-sfw-tests,
para testes e medições só com conteúdo SFW (Etapa 3); todo resultado sai marcado com
esse modo, e o backend deve recusar entregar resultados marcados assim em produção.

O compilador (modelo abliterado) nunca é camada de segurança.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

MODE_ENV = "GPU2GO_SAFETY"
SFW_TESTS = "disabled-sfw-tests"


class SafetyNotConfigured(Exception):
    pass


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


class Filters:
    """Interface dos filtros C1 (regras/listas) e C2 (classificador) sobre o texto."""

    mode: str = "abstract"

    def check_request(self, text_pt: str) -> Decision:  # antes do compilador
        raise NotImplementedError

    def check_prompt(self, prompt_en: str) -> Decision:  # depois do compilador
        raise NotImplementedError


class DisabledForSfwTests(Filters):
    mode = SFW_TESTS

    def check_request(self, text_pt: str) -> Decision:
        return Decision(True, "filtros desligados: só testes SFW")

    def check_prompt(self, prompt_en: str) -> Decision:
        return Decision(True, "filtros desligados: só testes SFW")


def load_filters() -> Filters:
    mode = os.environ.get(MODE_ENV, "").strip()
    if mode == SFW_TESTS:
        return DisabledForSfwTests()
    raise SafetyNotConfigured(
        f"filtros C1–C2 não configurados ({MODE_ENV}={mode or '<vazio>'}); "
        f"o worker não processa jobs sem eles (Etapa 4). Para testes SFW: {MODE_ENV}={SFW_TESTS}"
    )
