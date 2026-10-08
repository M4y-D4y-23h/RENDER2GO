"""Espelho dos modelos no R2. Os testes de integração precisam de um S3 local (ex.: SeaweedFS):
RENDER2GO_TESTE_S3=http://127.0.0.1:8333 RENDER2GO_TESTE_S3_BUCKET=gpu2go
RENDER2GO_TESTE_S3_CHAVE=... RENDER2GO_TESTE_S3_SEGREDO=...
Sem isso, só os testes de unidade rodam.
"""

import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from render2go_worker import espelho as esp_mod
from render2go_worker import models
from render2go_worker.armazenamento import Armazenamento

WORKER = Path(__file__).resolve().parents[1]
S3 = os.environ.get("RENDER2GO_TESTE_S3")
s3_local = pytest.mark.skipif(not S3, reason="sem S3 local (RENDER2GO_TESTE_S3)")


def _s3() -> Armazenamento:
    return Armazenamento(
        S3,
        os.environ.get("RENDER2GO_TESTE_S3_BUCKET", "gpu2go"),
        os.environ["RENDER2GO_TESTE_S3_CHAVE"],
        os.environ["RENDER2GO_TESTE_S3_SEGREDO"],
        "us-east-1",
    )


@pytest.fixture
def modelos_pequenos(tmp_path, monkeypatch):
    """Dois "modelos" pequenos no lugar do lock real; partes de 5 MB (o mínimo do S3)."""
    pasta = tmp_path / "models"
    entradas = {}
    for nome, tam in (("a", 11 * 1024**2), ("b", 100 * 1024)):
        dados = os.urandom(tam) + nome.encode()
        dest = f"diffusion_models/{nome}-{hashlib.sha256(dados).hexdigest()[:8]}.safetensors"
        (pasta / dest).parent.mkdir(parents=True, exist_ok=True)
        (pasta / dest).write_bytes(dados)
        entradas[nome] = models.ModelEntry(
            name=nome,
            repo="x/y",
            revision="r",
            path=dest,
            dest=dest,
            bytes=len(dados),
            sha256=hashlib.sha256(dados).hexdigest(),
        )
    monkeypatch.setattr(esp_mod.models, "load_lock", lambda: ({"i2v_r1v2": ["a", "b"]}, entradas))
    monkeypatch.setattr(esp_mod, "COMFY_MODELOS", str(pasta))
    monkeypatch.setattr(esp_mod, "PARTE", 5 * 1024**2)
    return pasta, entradas


class S3Falso:
    def __init__(self):
        self.objetos, self.abertos, self.abortados = {}, {}, []

    def tamanho(self, chave):
        return self.objetos.get(chave)

    def url(self, metodo, chave, expira_s=900, **kw):
        return f"https://s3/{chave}?{metodo}"

    def iniciar_partes(self, chave):
        self.abertos[chave] = f"up-{len(self.abertos)}"
        return self.abertos[chave]

    def url_parte(self, chave, uid, n, expira_s=0):
        return f"https://s3/{chave}?uploadId={uid}&partNumber={n}"

    def concluir_partes(self, chave, uid, etags):
        self.objetos[chave] = self.tamanho_esperado[chave]

    def abortar_partes(self, chave, uid):
        self.abortados.append(chave)


def test_plan_has_one_signed_url_per_part_and_only_missing_models(modelos_pequenos):
    _, entradas = modelos_pequenos
    s3 = S3Falso()
    e = esp_mod.Espelho(s3)
    s3.objetos[e.chave(entradas["b"])] = entradas["b"].bytes  # b já está no espelho
    e.verificar()
    assert [m.name for m in e.faltando()] == ["a"]
    assert "b-" in e.tsv() and "a-" not in e.tsv()
    plano = e.plano()
    (arq,) = plano["arquivos"]
    partes = arq["partes"]
    assert [p["n"] for p in partes] == [1, 2, 3]  # 11 MB + 1 byte em partes de 5 MB
    assert sum(p["tam"] for p in partes) == entradas["a"].bytes
    assert all("uploadId=" in p["url"] for p in partes)
    # resultado incompleto: o envio é abortado, nada entra como presente
    s3.tamanho_esperado = {e.chave(entradas["a"]): entradas["a"].bytes}
    assert e.concluir({arq["caminho"]: {"1": "x"}}) == []
    assert s3.abortados == [e.chave(entradas["a"])] and e.faltando()


@s3_local
def test_pod_uploader_fills_the_mirror_and_signed_urls_download_it(modelos_pequenos, tmp_path):
    pasta, entradas = modelos_pequenos
    s3 = _s3()
    for m in entradas.values():  # começa vazio
        s3.apagar(f"modelos/{m.sha256}")
    e = esp_mod.Espelho(s3)
    e.verificar()
    assert len(e.faltando()) == 2
    plano = tmp_path / "plano.json"
    plano.write_text(json.dumps(e.plano()))
    res = tmp_path / "res.json"
    out = subprocess.run(
        [sys.executable, str(WORKER / "scripts" / "espelhar.py"), str(plano), str(res)],
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout
    assert "ESPELHADO" in out
    assert sorted(e.concluir(json.loads(res.read_text()))) == sorted(
        Path(m.dest).name for m in entradas.values()
    )
    e2 = esp_mod.Espelho(s3)
    e2.verificar()
    assert not e2.faltando()
    for linha in e2.tsv().splitlines():
        nome, url = linha.split("\t")
        m = next(m for m in entradas.values() if Path(m.dest).name == nome)
        assert hashlib.sha256(urllib.request.urlopen(url).read()).hexdigest() == m.sha256
