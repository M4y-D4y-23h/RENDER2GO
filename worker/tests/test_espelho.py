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


class S3ComTextos(S3Falso):
    """S3Falso com objetos pequenos de texto (o .sha256 do ambiente) e envios concluídos."""

    def __init__(self):
        super().__init__()
        self.textos, self.tamanho_esperado = {}, {}

    def ler(self, chave, limite):
        if chave not in self.textos:
            raise esp_mod.ArmazenamentoError(f"GET {chave}: HTTP 404")
        return self.textos[chave]

    def escrever(self, chave, dados, content_type="text/plain"):
        self.textos[chave] = dados
        self.objetos[chave] = len(dados)


def test_environment_key_is_the_same_as_the_bootstrap():
    esperado = subprocess.run(
        "cat requirements.lock Dockerfile | sha256sum | cut -c1-16",
        shell=True,
        cwd=WORKER,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert esp_mod.chave_do_ambiente() == esperado


def test_environment_archive_is_uploaded_once_and_then_served_with_its_sha256(
    modelos_pequenos, monkeypatch
):
    _, entradas = modelos_pequenos
    s3 = S3ComTextos()
    for m in entradas.values():  # modelos já no espelho; falta só o ambiente
        s3.objetos[f"modelos/{m.sha256}"] = m.bytes
    e = esp_mod.Espelho(s3)
    e.verificar()
    assert not e.faltando() and e.ambiente_falta() and e.ambiente_tsv() == ""
    amb = {"sha256": "ab" * 32, "bytes": 2 * esp_mod.PARTE + 7}
    plano = e.plano(amb)
    (arq,) = plano["arquivos"]
    assert arq["caminho"] == esp_mod.AMBIENTE_NO_POD
    assert [p["n"] for p in arq["partes"]] == [1, 2, 3]
    chave = e.chave_ambiente()
    assert chave == f"modelos/ambiente-{esp_mod.chave_do_ambiente()}.tar.zst"
    # envio incompleto: abortado, e o .sha256 nunca é gravado
    assert e.concluir({arq["caminho"]: {"1": "x"}}) == []
    assert s3.abortados == [chave] and chave + ".sha256" not in s3.textos and e.ambiente_falta()
    # envio completo: fecha as partes e grava o .sha256 por último
    plano = e.plano(amb)
    s3.tamanho_esperado[chave] = amb["bytes"]
    feitos = e.concluir({esp_mod.AMBIENTE_NO_POD: {"1": "a", "2": "b", "3": "c"}})
    assert feitos == [chave.rsplit("/", 1)[1]]
    assert s3.textos[chave + ".sha256"] == f"{'ab' * 32} {amb['bytes']}\n".encode()
    # próximo lote: o ambiente está no espelho, com URL, SHA-256 e bytes para o bootstrap
    e2 = esp_mod.Espelho(s3)
    e2.verificar()
    assert e2.ambiente == amb and not e2.ambiente_falta() and e2.plano(amb) == {"arquivos": []}
    url, sha, n = e2.ambiente_tsv().rstrip("\n").split("\t")
    assert chave in url and sha == "ab" * 32 and int(n) == amb["bytes"]
    # arquivo com tamanho diferente do .sha256 (envio interrompido depois): ignorado
    s3.objetos[chave] = 5
    e2.verificar()
    assert e2.ambiente_falta()
