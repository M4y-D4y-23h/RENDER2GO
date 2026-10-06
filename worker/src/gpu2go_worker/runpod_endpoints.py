"""Endpoints serverless do RunPod como código (infra/runpod/*.json).

  plan  CONFIG   mostra o template e o endpoint que seriam criados ou atualizados
  apply CONFIG   cria ou atualiza (por nome) o template e o endpoint; imprime os ids
  delete NOME    apaga o endpoint (o template fica)

Regras aplicadas antes de qualquer chamada:
- MODE=serverless, CUDA a partir de 12.8 (driver ≥ 570) e GPU fixada pelo tier;
- fail-closed: sem filtros C1–C2 o endpoint não é criado. Para endpoints de teste e
  medição, `--sfw-tests` liga GPU2GO_SAFETY=disabled-sfw-tests e exige "teste" no nome;
- GPU2GO_ALLOWED_URL_HOSTS (hosts das URLs assinadas) é obrigatório.

A chave vem só de RUNPOD_API_KEY.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

from gpu2go_worker.runpod_pods import RunPodError, _chave, _rest
from gpu2go_worker.safety import SFW_TESTS


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def build(
    config: dict[str, Any],
    imagem: str,
    versao: str,
    hosts: str | None,
    sfw_tests: bool,
    sufixo: str = "",
) -> tuple[dict, dict]:
    """Corpo do template e do endpoint, já validados."""
    tpl = copy.deepcopy(config["template"])
    ep = copy.deepcopy(config["endpoint"])
    if sufixo:
        tpl["name"] += f"-{sufixo}"
        ep["name"] += f"-{sufixo}"
    env = tpl.setdefault("env", {})
    env["GPU2GO_IMAGE_VERSION"] = versao
    if hosts:
        env["GPU2GO_ALLOWED_URL_HOSTS"] = hosts
    if sfw_tests:
        if "teste" not in ep["name"]:
            raise RunPodError(
                "endpoint com filtros desligados precisa de 'teste' no nome (--sufixo teste)"
            )
        env["GPU2GO_SAFETY"] = SFW_TESTS
        env.setdefault("GPU2GO_ALLOW_DISCARD_OUTPUT", "1")  # o bench descarta a saída
    if env.get("MODE") != "serverless":
        raise RunPodError("template.env.MODE deve ser serverless")
    if not env.get("GPU2GO_SAFETY"):
        raise RunPodError(
            "sem filtros C1–C2 (GPU2GO_SAFETY): o endpoint não é criado (fail-closed)"
        )
    if not env.get("GPU2GO_ALLOWED_URL_HOSTS"):
        raise RunPodError(
            "GPU2GO_ALLOWED_URL_HOSTS vazio: informe --hosts (hosts das URLs assinadas)"
        )
    cuda = ep.get("allowedCudaVersions") or []
    if not cuda or min(tuple(map(int, v.split("."))) for v in cuda) < (12, 8):
        raise RunPodError("allowedCudaVersions deve começar em 12.8 (PyTorch +cu128)")
    tpl.update(imageName=imagem, isServerless=True, ports=[], volumeInGb=0)
    return tpl, ep


def _find(kind: str, name: str, chave: str) -> dict | None:
    r = _rest("GET", f"/{kind}", chave)
    if not r.ok:
        raise RunPodError(f"listar {kind}: {r.status_code} {r.text[:300]}")
    return next((x for x in r.json() if x.get("name") == name), None)


def _upsert(kind: str, body: dict, chave: str) -> dict:
    atual = _find(kind, body["name"], chave)
    if atual is None:
        r = _rest("POST", f"/{kind}", chave, body)
        acao = "criado"
    else:
        campos = {k: v for k, v in body.items() if k != "isServerless"}  # não muda no PATCH
        r = _rest("PATCH", f"/{kind}/{atual['id']}", chave, campos)
        acao = "atualizado"
    if not r.ok:
        raise RunPodError(f"{kind} {body['name']}: {r.status_code} {r.text[:400]}")
    out = r.json()
    _log(f"{kind[:-1]} {acao}: {out.get('id')} ({body['name']})")
    return out


def apply(tpl: dict, ep: dict, chave: str) -> dict[str, Any]:
    template = _upsert("templates", tpl, chave)
    endpoint = _upsert("endpoints", {**ep, "templateId": template["id"]}, chave)
    return {"template_id": template["id"], "endpoint_id": endpoint["id"], "name": ep["name"]}


def apply_github(tpl: dict, ep: dict, chave: str) -> dict[str, Any]:
    """Endpoint criado no console pela integração GitHub: ajusta, não cria.

    O RunPod cria o template (e a imagem) a partir do repositório. Aqui só entram a
    configuração do endpoint e as variáveis de ambiente do template dele; a imagem e o
    nome do template ficam como o RunPod definiu.
    """
    atual = _find("endpoints", ep["name"], chave)
    if atual is None:
        raise RunPodError(
            f"endpoint {ep['name']} não existe: crie-o no console (New Endpoint → Import Git "
            "Repository, ver infra/runpod/LEIAME.md) e rode de novo"
        )
    r = _rest("PATCH", f"/endpoints/{atual['id']}", chave, ep)
    if not r.ok:
        raise RunPodError(f"endpoint {ep['name']}: {r.status_code} {r.text[:400]}")
    template_id = atual.get("templateId") or (atual.get("template") or {}).get("id")
    if not template_id:
        raise RunPodError("o endpoint não informou o templateId; ajuste as variáveis no console")
    r = _rest("PATCH", f"/templates/{template_id}", chave, {"env": tpl["env"]})
    if not r.ok:
        raise RunPodError(f"template {template_id}: {r.status_code} {r.text[:400]}")
    _log(f"endpoint ajustado: {atual['id']} ({ep['name']}); variáveis do template {template_id}")
    return {"template_id": template_id, "endpoint_id": atual["id"], "name": ep["name"]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gpu2go-endpoint", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for nome in ("plan", "apply"):
        s = sub.add_parser(nome)
        s.add_argument("config", type=Path)
        s.add_argument("--imagem", help="imagem no registry, com tag fixa")
        s.add_argument(
            "--github",
            action="store_true",
            help="endpoint da integração GitHub (criado no console): só ajusta config e env",
        )
        s.add_argument("--versao", help="GPU2GO_IMAGE_VERSION (padrão: a tag da imagem)")
        s.add_argument("--hosts", help="hosts das URLs assinadas, separados por vírgula")
        s.add_argument(
            "--sfw-tests",
            action="store_true",
            help="filtros desligados, só para testes e medições SFW",
        )
        s.add_argument("--sufixo", default="", help="sufixo do nome (ex.: teste)")
    d = sub.add_parser("delete")
    d.add_argument("nome")
    a = p.parse_args(argv)
    try:
        if a.cmd == "delete":
            chave = _chave()
            ep = _find("endpoints", a.nome, chave)
            if ep is None:
                _log(f"endpoint {a.nome} não existe")
                return 1
            r = _rest("DELETE", f"/endpoints/{ep['id']}", chave)
            if not r.ok:
                raise RunPodError(f"apagar: {r.status_code} {r.text[:300]}")
            _log(f"endpoint {a.nome} apagado")
            return 0
        config = json.loads(a.config.read_text())
        if bool(a.imagem) == bool(a.github):
            raise RunPodError("informe --imagem (registry) ou --github (integração), um dos dois")
        imagem = a.imagem or "github"
        versao = a.versao or (a.imagem.rsplit(":", 1)[-1] if a.imagem else None)
        if not versao:
            raise RunPodError("com --github, informe --versao (a tag do release no GitHub)")
        tpl, ep = build(config, imagem, versao, a.hosts, a.sfw_tests, a.sufixo)
        if a.cmd == "plan":
            print(json.dumps({"template": tpl, "endpoint": ep}, ensure_ascii=False, indent=2))
            return 0
        fn = apply_github if a.github else apply
        print(json.dumps(fn(tpl, ep, _chave()), ensure_ascii=False, indent=2))
        return 0
    except RunPodError as e:
        _log(f"erro: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
