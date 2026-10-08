"""Templates de workflow (formato API do ComfyUI) e seus contratos de parâmetros.

Cada template `<nome>.vN.api.json` tem ao lado um `<nome>.vN.contract.json` que diz:
- quais parâmetros existem, em que nó/entrada caem e quais são os limites;
- quais valores são fixos (modelos, turbo, passos) e não podem mudar;
- quais classes de nó são proibidas (ex.: os nós do Ollama, que saíram do grafo).

O código nunca edita o JSON do workflow "na mão": tudo passa por `apply()`.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from render2go_worker import WORKER_DIR

WORKFLOWS_DIR = WORKER_DIR / "workflows"
FPS = 16


class TemplateError(Exception):
    pass


def frames_for(duration_s: int, fps: int = FPS) -> int:
    """Número de frames gerados pelo template: floor(d * fps + 1); com d inteiro dá 4k + 1."""
    return duration_s * fps + 1


def _is_link(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], int)
    )


@dataclass
class Template:
    name: str
    version: int
    comfyui: str
    workflow: dict[str, Any]
    contract: dict[str, Any]
    path: Path = field(default=Path())

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def apply(self, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Devolve (workflow preenchido, parâmetros efetivos). Levanta TemplateError."""
        spec = self.contract["params"]
        unknown = sorted(set(params) - set(spec))
        if unknown:
            raise TemplateError(f"parâmetros desconhecidos: {unknown}")

        resolved: dict[str, Any] = {}
        for name, p in spec.items():
            value = params.get(name, p.get("default"))
            if value is None:
                if p.get("required"):
                    raise TemplateError(f"parâmetro obrigatório ausente: {name}")
                continue
            resolved[name] = _check_param(name, value, p)

        max_area = self.contract.get("constraints", {}).get("max_area")
        if max_area and "width" in resolved and "height" in resolved:
            area = resolved["width"] * resolved["height"]
            if area > max_area:
                raise TemplateError(
                    f"resolução {resolved['width']}x{resolved['height']} ({area} px) "
                    f"acima do limite de {max_area} px"
                )

        wf = copy.deepcopy(self.workflow)
        for name, value in resolved.items():
            p = spec[name]
            node = wf.get(p["node"])
            if node is None or p["input"] not in node["inputs"]:
                raise TemplateError(f"{name}: entrada {p['node']}.{p['input']} não existe")
            node["inputs"][p["input"]] = float(value) if p.get("as_float") else value
        validate_structure(wf, self.contract)
        return wf, resolved


def _check_param(name: str, value: Any, p: dict[str, Any]) -> Any:
    kind = p["type"]
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise TemplateError(f"{name}: esperado inteiro, recebido {value!r}")
        if "min" in p and value < p["min"]:
            raise TemplateError(f"{name}: {value} abaixo do mínimo {p['min']}")
        if "max" in p and value > p["max"]:
            raise TemplateError(f"{name}: {value} acima do máximo {p['max']}")
        if "multiple_of" in p and value % p["multiple_of"]:
            raise TemplateError(f"{name}: {value} não é múltiplo de {p['multiple_of']}")
    elif kind == "str":
        if not isinstance(value, str):
            raise TemplateError(f"{name}: esperado texto, recebido {value!r}")
        if p.get("required") and not value.strip():
            raise TemplateError(f"{name}: não pode ser vazio")
        if "max_len" in p and len(value) > p["max_len"]:
            raise TemplateError(f"{name}: texto com mais de {p['max_len']} caracteres")
    else:
        raise TemplateError(f"{name}: tipo de contrato desconhecido {kind!r}")
    return value


def validate_structure(wf: dict[str, Any], contract: dict[str, Any]) -> None:
    """Links resolvem, nenhuma classe proibida, valores fixos intactos."""
    forbidden = set(contract.get("forbidden_classes", []))
    for node_id, node in wf.items():
        cls = node.get("class_type")
        if cls in forbidden:
            raise TemplateError(f"nó {node_id} usa a classe proibida {cls}")
        for key, value in node.get("inputs", {}).items():
            if _is_link(value) and value[0] not in wf:
                raise TemplateError(f"nó {node_id}.{key} aponta para nó inexistente {value[0]}")
    for ref, expected in contract.get("fixed", {}).items():
        node_id, _, input_name = ref.rpartition(".")
        actual = wf.get(node_id, {}).get("inputs", {}).get(input_name, "<ausente>")
        if actual != expected:
            raise TemplateError(f"valor fixo alterado: {ref} = {actual!r}, esperado {expected!r}")


def validate_against_object_info(wf: dict[str, Any], object_info: dict[str, Any]) -> list[str]:
    """Confere classes e entradas do workflow contra o /object_info de um ComfyUI rodando.

    Entradas dinâmicas aparecem no formato API como `prefixo.sub` (ex.: `values.a` do
    ComfyMathExpression, `format.codec` do SaveVideo); basta o prefixo existir.
    """
    problems: list[str] = []
    for node_id, node in wf.items():
        cls = node["class_type"]
        info = object_info.get(cls)
        if info is None:
            problems.append(f"{node_id}: classe {cls} não existe nesta versão do ComfyUI")
            continue
        declared = info.get("input", {})
        known = set(declared.get("required", {})) | set(declared.get("optional", {}))
        for key in node.get("inputs", {}):
            if key not in known and key.split(".", 1)[0] not in known:
                problems.append(f"{node_id} ({cls}): entrada {key!r} não existe")
    return problems


def load(path: str | Path) -> Template:
    path = Path(path)
    if not path.name.endswith(".api.json"):
        raise TemplateError(f"template deve terminar em .api.json: {path}")
    contract_path = path.with_name(path.name.replace(".api.json", ".contract.json"))
    workflow = json.loads(path.read_text())
    contract = json.loads(contract_path.read_text())
    tpl = Template(
        name=contract["template"],
        version=int(contract["version"]),
        comfyui=contract["comfyui"],
        workflow=workflow,
        contract=contract,
        path=path,
    )
    validate_structure(tpl.workflow, tpl.contract)
    return tpl


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Valida templates contra o /object_info de um ComfyUI em execução."
    )
    p.add_argument("templates", nargs="+", type=Path)
    p.add_argument("--object-info", type=Path, help="JSON salvo de /object_info")
    p.add_argument("--comfy", help="URL do ComfyUI (ex.: http://127.0.0.1:8188)")
    a = p.parse_args(argv)
    if a.object_info:
        info = json.loads(a.object_info.read_text())
    elif a.comfy:
        import requests

        info = requests.get(f"{a.comfy.rstrip('/')}/object_info", timeout=60).json()
    else:
        p.error("informe --object-info ou --comfy")
    failed = False
    for t in a.templates:
        try:
            tpl = load(t)
        except (TemplateError, OSError, json.JSONDecodeError) as e:
            print(f"ERRO {t}: {e}")
            failed = True
            continue
        problems = validate_against_object_info(tpl.workflow, info)
        for prob in problems:
            print(f"ERRO {tpl.ref}: {prob}")
        if problems:
            failed = True
        else:
            print(f"ok   {tpl.ref} ({len(tpl.workflow)} nós)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
