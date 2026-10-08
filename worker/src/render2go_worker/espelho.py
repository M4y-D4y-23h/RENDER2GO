"""Espelho dos modelos no R2: reserva do Hugging Face para a subida dos pods (lote_nuvem).

- Download: o pod recebe uma URL assinada (GET, 6 h) por modelo presente no espelho e o aria2
  baixa das duas origens ao mesmo tempo; se uma cair, segue pela outra. O SHA-256 do
  models.lock continua decidindo.
- Envio: o que falta é enviado pelo 1º pod do lote, a partir do disco local já conferido, em
  partes de 512 MB com uma URL de PUT por parte (`scripts/espelhar.py`). Início e fim do envio
  são chamadas assinadas daqui: as chaves nunca vão para o pod.

Objetos: modelos/<sha256> (endereçados pelo conteúdo; o nome do arquivo não importa).

Ambiente (08/10/2026): o venv, o ComfyUI e o Ollama empacotados num tar.zst
(modelos/ambiente-<chave>.tar.zst), com a mesma chave do bootstrap_pod.sh (SHA-256 do
requirements.lock e do Dockerfile). Num host com PyPI e GitHub lentos, instalar o ambiente
levou 189 s; do R2, o pod baixa, confere o SHA-256 e extrai. O .sha256 ao lado (<sha256>
<bytes>) é gravado por último: sem ele, o arquivo é ignorado. O 1º pod sem o ambiente no espelho
empacota o dele (RENDER2GO_AMBIENTE_GRAVA=1) e envia depois do PRONTO, como os modelos.
"""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from render2go_worker import WORKER_DIR, models
from render2go_worker.armazenamento import Armazenamento, ArmazenamentoError

PARTE = 512 * 1024**2
URL_S = 6 * 3600  # o pod vive no máximo 2 h
COMFY_MODELOS = "/opt/comfyui/models"
AMBIENTE_NO_POD = "/root/render2go/ambiente.tar.zst"  # onde o bootstrap empacota o ambiente


def chave_do_ambiente(worker_dir: Path = WORKER_DIR) -> str:
    """A mesma chave do bootstrap_pod.sh: `cat requirements.lock Dockerfile | sha256sum`, 16 hex."""
    h = hashlib.sha256()
    for nome in ("requirements.lock", "Dockerfile"):
        h.update((worker_dir / nome).read_bytes())
    return h.hexdigest()[:16]


class Espelho:
    def __init__(self, s3: Armazenamento, conjunto: str = "i2v_r1v2"):
        sets, entradas = models.load_lock()
        self.s3 = s3
        self.modelos = [entradas[n] for n in sorted(set(sets[conjunto]))]
        self.presentes: set[str] = set()
        self.envios: dict[str, tuple[models.ModelEntry, str]] = {}
        self.erro: str | None = None  # último erro ao concluir um envio (para o log)
        self.chave_amb = chave_do_ambiente()
        self.ambiente: dict | None = None  # {"sha256", "bytes"} do ambiente no espelho
        self.envio_ambiente: tuple[dict, str] | None = None

    @classmethod
    def do_ambiente(cls, conjunto: str = "i2v_r1v2") -> Espelho | None:
        s3 = Armazenamento.do_ambiente()
        return cls(s3, conjunto) if s3 else None

    @staticmethod
    def chave(m: models.ModelEntry) -> str:
        return f"modelos/{m.sha256}"

    @staticmethod
    def caminho(m: models.ModelEntry) -> str:
        return f"{COMFY_MODELOS}/{m.dest}"

    def verificar(self) -> None:
        """Quais modelos já estão no espelho com os bytes certos (HEAD em cada um), e o ambiente."""
        self.presentes = {
            m.sha256 for m in self.modelos if self.s3.tamanho(self.chave(m)) == m.bytes
        }
        self.ambiente = self._ambiente_no_espelho()

    def faltando(self) -> list[models.ModelEntry]:
        return [m for m in self.modelos if m.sha256 not in self.presentes]

    # -- ambiente (venv, ComfyUI e Ollama num tar.zst) -----------------------------------

    def chave_ambiente(self) -> str:
        return f"modelos/ambiente-{self.chave_amb}.tar.zst"

    def _ambiente_no_espelho(self) -> dict | None:
        """O .sha256 é gravado por último: sem ele, ou com o tamanho diferente, não há ambiente.
        Falha aqui nunca derruba o espelho dos modelos: o pod instala o ambiente da origem."""
        chave = self.chave_ambiente()
        try:
            sha, n = self.s3.ler(chave + ".sha256", 200).decode().split()[:2]
            if len(sha) != 64 or int(n) <= 0 or self.s3.tamanho(chave) != int(n):
                return None
            int(sha, 16)
            return {"sha256": sha, "bytes": int(n)}
        except Exception:  # noqa: BLE001 (sem ambiente no espelho, o pod instala da origem)
            return None

    def ambiente_falta(self) -> bool:
        return self.ambiente is None

    def ambiente_tsv(self) -> str:
        """Para o bootstrap: <URL assinada>\\t<sha256>\\t<bytes> do ambiente no espelho."""
        if not self.ambiente:
            return ""
        url = self.s3.url("GET", self.chave_ambiente(), URL_S)
        return f"{url}\t{self.ambiente['sha256']}\t{self.ambiente['bytes']}\n"

    def tsv(self) -> str:
        """Para o bootstrap: <arquivo>\\t<URL assinada> dos modelos presentes."""
        return "".join(
            f"{PurePosixPath(m.dest).name}\t{self.s3.url('GET', self.chave(m), URL_S)}\n"
            for m in self.modelos
            if m.sha256 in self.presentes
        )

    def url(self, m: models.ModelEntry) -> str | None:
        return self.s3.url("GET", self.chave(m), URL_S) if m.sha256 in self.presentes else None

    def _partes(self, chave: str, uid: str, total: int) -> list[dict]:
        return [
            {
                "n": n,
                "ini": ini,
                "tam": min(PARTE, total - ini),
                "url": self.s3.url_parte(chave, uid, n, URL_S),
            }
            for n, ini in enumerate(range(0, total, PARTE), 1)
        ]

    def plano(self, ambiente: dict | None = None) -> dict:
        """Abre um envio em partes por modelo que falta e devolve o plano para o pod.

        `ambiente`: {"sha256", "bytes"} do tar.zst que o pod empacotou, se o espelho não tem."""
        arquivos = []
        for m in self.faltando():
            uid = self.s3.iniciar_partes(self.chave(m))
            self.envios[self.caminho(m)] = (m, uid)
            partes = self._partes(self.chave(m), uid, m.bytes)
            arquivos.append({"caminho": self.caminho(m), "partes": partes})
        if ambiente and self.ambiente is None and ambiente.get("bytes", 0) > 0:
            chave = self.chave_ambiente()
            uid = self.s3.iniciar_partes(chave)
            self.envio_ambiente = (dict(ambiente), uid)
            partes = self._partes(chave, uid, ambiente["bytes"])
            arquivos.append({"caminho": AMBIENTE_NO_POD, "partes": partes})
        return {"arquivos": arquivos}

    def _concluir_ambiente(self, resultado: dict[str, dict[str, str]]) -> str | None:
        """Fecha o envio do ambiente e grava o .sha256 por último; devolve o nome, se deu certo."""
        if not self.envio_ambiente:
            return None
        amb, uid = self.envio_ambiente
        self.envio_ambiente = None
        chave = self.chave_ambiente()
        etags = {int(n): e for n, e in (resultado.get(AMBIENTE_NO_POD) or {}).items()}
        if len(etags) == -(-amb["bytes"] // PARTE):
            try:
                self.s3.concluir_partes(chave, uid, etags)
                if self.s3.tamanho(chave) == amb["bytes"]:
                    sidecar = f"{amb['sha256']} {amb['bytes']}\n".encode()
                    self.s3.escrever(chave + ".sha256", sidecar)
                    self.ambiente = {"sha256": amb["sha256"], "bytes": amb["bytes"]}
                    return PurePosixPath(chave).name
            except ArmazenamentoError as e:
                self.erro = str(e)
        self.s3.abortar_partes(chave, uid)
        return None

    def concluir(self, resultado: dict[str, dict[str, str]]) -> list[str]:
        """Fecha os envios completos e aborta os outros; devolve os arquivos espelhados."""
        feitos = [x for x in [self._concluir_ambiente(resultado)] if x]
        for caminho, (m, uid) in list(self.envios.items()):
            etags = {int(n): e for n, e in (resultado.get(caminho) or {}).items()}
            if len(etags) == -(-m.bytes // PARTE):
                try:
                    self.s3.concluir_partes(self.chave(m), uid, etags)
                    if self.s3.tamanho(self.chave(m)) == m.bytes:
                        self.presentes.add(m.sha256)
                        feitos.append(PurePosixPath(m.dest).name)
                        del self.envios[caminho]
                        continue
                except ArmazenamentoError as e:
                    self.erro = str(e)
            self.s3.abortar_partes(self.chave(m), uid)
            del self.envios[caminho]
        return feitos

    def abortar(self) -> None:
        for _, (m, uid) in list(self.envios.items()):
            self.s3.abortar_partes(self.chave(m), uid)
        self.envios.clear()
        if self.envio_ambiente:
            self.s3.abortar_partes(self.chave_ambiente(), self.envio_ambiente[1])
            self.envio_ambiente = None
