"""Espelho dos modelos no R2: reserva do Hugging Face para a subida dos pods (lote_nuvem).

- Download: o pod recebe uma URL assinada (GET, 6 h) por modelo presente no espelho e o aria2
  baixa das duas origens ao mesmo tempo; se uma cair, segue pela outra. O SHA-256 do
  models.lock continua decidindo.
- Envio: o que falta é enviado pelo 1º pod do lote, a partir do disco local já conferido, em
  partes de 512 MB com uma URL de PUT por parte (`scripts/espelhar.py`). Início e fim do envio
  são chamadas assinadas daqui: as chaves nunca vão para o pod.

Objetos: modelos/<sha256> (endereçados pelo conteúdo; o nome do arquivo não importa).
"""

from __future__ import annotations

from pathlib import PurePosixPath

from gpu2go_worker import models
from gpu2go_worker.armazenamento import Armazenamento, ArmazenamentoError

PARTE = 512 * 1024**2
URL_S = 6 * 3600  # o pod vive no máximo 2 h
COMFY_MODELOS = "/opt/comfyui/models"


class Espelho:
    def __init__(self, s3: Armazenamento, conjunto: str = "i2v_r1v2"):
        sets, entradas = models.load_lock()
        self.s3 = s3
        self.modelos = [entradas[n] for n in sorted(set(sets[conjunto]))]
        self.presentes: set[str] = set()
        self.envios: dict[str, tuple[models.ModelEntry, str]] = {}
        self.erro: str | None = None  # último erro ao concluir um envio (para o log)

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
        """Quais modelos já estão no espelho com os bytes certos (HEAD em cada um)."""
        self.presentes = {
            m.sha256 for m in self.modelos if self.s3.tamanho(self.chave(m)) == m.bytes
        }

    def faltando(self) -> list[models.ModelEntry]:
        return [m for m in self.modelos if m.sha256 not in self.presentes]

    def tsv(self) -> str:
        """Para o bootstrap: <arquivo>\\t<URL assinada> dos modelos presentes."""
        return "".join(
            f"{PurePosixPath(m.dest).name}\t{self.s3.url('GET', self.chave(m), URL_S)}\n"
            for m in self.modelos
            if m.sha256 in self.presentes
        )

    def url(self, m: models.ModelEntry) -> str | None:
        return self.s3.url("GET", self.chave(m), URL_S) if m.sha256 in self.presentes else None

    def plano(self) -> dict:
        """Abre um envio em partes por modelo que falta e devolve o plano para o pod."""
        arquivos = []
        for m in self.faltando():
            uid = self.s3.iniciar_partes(self.chave(m))
            self.envios[self.caminho(m)] = (m, uid)
            partes = [
                {
                    "n": n,
                    "ini": ini,
                    "tam": min(PARTE, m.bytes - ini),
                    "url": self.s3.url_parte(self.chave(m), uid, n, URL_S),
                }
                for n, ini in enumerate(range(0, m.bytes, PARTE), 1)
            ]
            arquivos.append({"caminho": self.caminho(m), "partes": partes})
        return {"arquivos": arquivos}

    def concluir(self, resultado: dict[str, dict[str, str]]) -> list[str]:
        """Fecha os envios completos e aborta os outros; devolve os arquivos espelhados."""
        feitos = []
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
