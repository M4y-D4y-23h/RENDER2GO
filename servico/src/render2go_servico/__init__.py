"""Serviço do Render2Go (ADR 0003, fase 3): API, banco, armazenamento, fila e orquestrador."""

from pathlib import Path

import render2go_worker  # noqa: F401 (aceita as variáveis com o nome antigo do projeto antes de lê-las)

SERVICO_DIR = Path(__file__).resolve().parents[2]
