"""Envia ao espelho (R2) os modelos já conferidos no disco do pod, em partes, por URLs assinadas.

Roda no pod depois do PRONTO (lote_nuvem). O pod nunca recebe chaves: só uma URL de PUT por
parte, válida para aquele objeto e aquele número de parte. Só biblioteca padrão.

Uso: espelhar.py <plano.json> <resultado.json>
    plano: {"arquivos": [{"caminho": ..., "partes": [{"n": 1, "ini": 0, "tam": ..., "url": ...}]}]}
    resultado: {"<caminho>": {"<n>": "<etag>"}, ...}; ao terminar, imprime ESPELHADO ou FALHOU.
"""

import hashlib
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor


class Trecho:
    """Lê só `tam` bytes do arquivo a partir de `ini` (o corpo do PUT, sem carregar na memória)."""

    def __init__(self, caminho, ini, tam):
        self.f = open(caminho, "rb")  # noqa: SIM115 (fechado em fechar())
        self.f.seek(ini)
        self.resta = tam
        self.md5 = hashlib.md5()  # noqa: S324 (confere o ETag da parte, não é segurança)

    def read(self, n=-1):
        if self.resta <= 0:
            return b""
        n = self.resta if n is None or n < 0 else min(n, self.resta)
        b = self.f.read(n)
        self.resta -= len(b)
        self.md5.update(b)
        return b

    def fechar(self):
        self.f.close()


def enviar(caminho, parte):
    for tentativa in range(4):
        corpo = Trecho(caminho, parte["ini"], parte["tam"])
        try:
            req = urllib.request.Request(parte["url"], data=corpo, method="PUT")
            req.add_header("Content-Length", str(parte["tam"]))
            with urllib.request.urlopen(req, timeout=600) as r:
                etag = r.headers["ETag"]
            limpo = (etag or "").strip('"').lower()
            # o ETag de uma parte é o MD5 dela: confere que chegou inteira (outro formato: aceita)
            if re.fullmatch(r"[0-9a-f]{32}", limpo) and limpo != corpo.md5.hexdigest():
                raise OSError(f"ETag {limpo} não confere com o MD5 enviado")
            return caminho, parte["n"], etag
        except OSError as e:
            if tentativa == 3:
                raise RuntimeError(f"{caminho} parte {parte['n']}: {e}") from e
            time.sleep(5 * (tentativa + 1))
        finally:
            corpo.fechar()
    raise AssertionError


def main(plano_path, resultado_path):
    with open(plano_path) as f:
        plano = json.load(f)
    resultado = {}
    tarefas = [(a["caminho"], p) for a in plano["arquivos"] for p in a["partes"]]
    try:
        with ThreadPoolExecutor(max_workers=4) as ex:
            for caminho, n, etag in ex.map(lambda t: enviar(*t), tarefas):
                resultado.setdefault(caminho, {})[str(n)] = etag
    except Exception as e:  # noqa: BLE001
        print(f"FALHOU {e}")
        return 1
    with open(resultado_path, "w") as f:
        json.dump(resultado, f)
    print("ESPELHADO")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
