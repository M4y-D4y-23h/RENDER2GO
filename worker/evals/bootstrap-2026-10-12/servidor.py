"""Servidor HTTP local com Range (o aria2 só abre várias conexões com Range), para o teste do
bootstrap em contêiner. Só biblioteca padrão.

Caminhos (o nome do arquivo vem depois do prefixo):
    /origem/<nome>        o arquivo de verdade (papel do Hugging Face)
    /espelho/<nome>       o arquivo de verdade (papel do espelho no R2)
    /espelho-ruim/<nome>  os mesmos bytes de tamanho, com o 1º byte trocado (espelho corrompido)
    /ausente/<nome>       404

Uso: python3 servidor.py <pasta dos arquivos> <porta>
"""

from __future__ import annotations

import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PASTA = sys.argv[1]
BLOCO = 1 << 20


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _arquivo(self) -> tuple[str, str] | None:
        m = re.fullmatch(r"/(origem|espelho|espelho-ruim|ausente)/([\w.\-]+)", self.path)
        if not m or m.group(1) == "ausente":
            return None
        caminho = os.path.join(PASTA, m.group(2))
        return (m.group(1), caminho) if os.path.isfile(caminho) else None

    def do_HEAD(self):
        self._servir(corpo=False)

    def do_GET(self):
        self._servir(corpo=True)

    def _servir(self, corpo: bool) -> None:
        achado = self._arquivo()
        if achado is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        tipo, caminho = achado
        total = os.path.getsize(caminho)
        ini, fim = 0, total - 1
        m = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if m:
            ini = int(m.group(1))
            fim = min(int(m.group(2)) if m.group(2) else total - 1, total - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {ini}-{fim}/{total}")
        else:
            self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(fim - ini + 1))
        self.end_headers()
        if not corpo:
            return
        with open(caminho, "rb") as f:
            f.seek(ini)
            falta, pos = fim - ini + 1, ini
            while falta > 0:
                b = bytearray(f.read(min(BLOCO, falta)))
                if not b:
                    break
                if tipo == "espelho-ruim" and pos == 0:
                    b[0] ^= 0xFF
                try:
                    self.wfile.write(b)
                except (BrokenPipeError, ConnectionResetError):
                    return
                falta -= len(b)
                pos += len(b)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[2])), H).serve_forever()
