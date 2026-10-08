"""Baixa ou copia os modelos listados em models.lock.json e confere bytes e SHA-256.

Origens:
- Hugging Face, na revisão fixada no lock (download com retomada);
- um diretório local, como o volume `/workspace/models` de um pod (só leitura sequencial
  na origem; nada de rename/chmod no volume, que não é POSIX).

O destino é sempre o disco local: nunca carregar modelos direto do volume.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from render2go_worker import WORKER_DIR

DEFAULT_LOCK = WORKER_DIR / "models.lock.json"
CHUNK = 16 * 1024 * 1024


class ModelError(Exception):
    pass


@dataclass(frozen=True)
class ModelEntry:
    name: str
    repo: str
    revision: str
    path: str
    dest: str
    bytes: int
    sha256: str

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{self.path}"


def load_lock(lock_path: Path = DEFAULT_LOCK) -> tuple[dict[str, list[str]], dict[str, ModelEntry]]:
    data = json.loads(Path(lock_path).read_text())
    models = {
        name: ModelEntry(
            name=name,
            repo=m["repo"],
            revision=m["revision"],
            path=m["path"],
            dest=m["dest"],
            bytes=int(m["bytes"]),
            sha256=m["sha256"].lower(),
        )
        for name, m in data["models"].items()
    }
    sets = data.get("sets", {})
    for set_name, names in sets.items():
        missing = [n for n in names if n not in models]
        if missing:
            raise ModelError(f"conjunto {set_name!r} cita modelos inexistentes: {missing}")
    return sets, models


def resolve(selection: list[str], sets: dict[str, list[str]], models: dict[str, ModelEntry]):
    out: list[ModelEntry] = []
    for item in selection:
        names = sets.get(item, [item])
        for n in names:
            if n not in models:
                raise ModelError(f"modelo ou conjunto desconhecido: {n!r}")
            if models[n] not in out:
                out.append(models[n])
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def verify(entry: ModelEntry, path: Path, check_sha: bool) -> None:
    if not path.is_file():
        raise ModelError(f"{entry.name}: arquivo ausente em {path}")
    size = path.stat().st_size
    if size != entry.bytes:
        raise ModelError(f"{entry.name}: {size} bytes, esperado {entry.bytes} ({path})")
    if check_sha:
        digest = sha256_file(path)
        if digest != entry.sha256:
            raise ModelError(f"{entry.name}: sha256 {digest}, esperado {entry.sha256}")


def _is_ok(entry: ModelEntry, path: Path, check_sha: bool) -> bool:
    try:
        verify(entry, path, check_sha)
        return True
    except ModelError:
        return False


def download_hf(entry: ModelEntry, dest: Path, retries: int = 5, token: str | None = None) -> None:
    """Baixa para `<dest>.part` com retomada (Range) e só renomeia no fim, no disco local."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    headers_base = {"Authorization": f"Bearer {token}"} if token else {}
    for attempt in range(1, retries + 1):
        have = part.stat().st_size if part.exists() else 0
        if have > entry.bytes:
            part.unlink()
            have = 0
        if have == entry.bytes:
            break
        headers = dict(headers_base)
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            with requests.get(entry.url, headers=headers, stream=True, timeout=60) as r:
                if have and r.status_code != 206:
                    have = 0  # servidor ignorou o Range: recomeça
                    mode = "wb"
                else:
                    mode = "ab" if have else "wb"
                r.raise_for_status()
                with open(part, mode) as f:
                    for chunk in r.iter_content(chunk_size=CHUNK):
                        f.write(chunk)
        except (requests.RequestException, OSError) as e:
            print(f"  {entry.name}: tentativa {attempt} falhou ({e}); retomando", file=sys.stderr)
            time.sleep(min(2**attempt, 30))
    if not part.exists() or part.stat().st_size != entry.bytes:
        raise ModelError(f"{entry.name}: download incompleto após {retries} tentativas")
    os.replace(part, dest)


def copy_from_dir(entry: ModelEntry, source_dir: Path, dest: Path) -> None:
    """Copia de um volume por leitura sequencial, escrevendo direto no destino local."""
    src = source_dir / entry.dest
    if not src.is_file():
        raise ModelError(f"{entry.name}: não encontrado na origem {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as fin, open(dest, "wb") as fout:
        shutil.copyfileobj(fin, fout, length=CHUNK)


def fetch(
    entries: list[ModelEntry],
    models_dir: Path,
    from_dir: Path | None = None,
    check_sha: bool = True,
    check_only: bool = False,
    token: str | None = None,
) -> None:
    for entry in entries:
        dest = models_dir / entry.dest
        t0 = time.monotonic()
        if _is_ok(entry, dest, check_sha):
            print(f"ok      {entry.dest}")
            continue
        if check_only:
            verify(entry, dest, check_sha)  # levanta com a mensagem certa
        print(f"obtendo {entry.dest} ({entry.bytes / 1e9:.2f} GB)", flush=True)
        if from_dir is not None:
            copy_from_dir(entry, from_dir, dest)
        else:
            download_hf(entry, dest, token=token)
        verify(entry, dest, check_sha)
        print(f"ok      {entry.dest} em {time.monotonic() - t0:.0f} s", flush=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("selection", nargs="+", help="conjuntos ou modelos do lock (ex.: i2v_r1v2)")
    p.add_argument("--models-dir", type=Path, required=True, help="pasta models/ do ComfyUI")
    p.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    p.add_argument("--from-dir", type=Path, help="copiar de um volume em vez de baixar")
    p.add_argument("--no-sha", action="store_true", help="conferir só os bytes (mais rápido)")
    p.add_argument("--check-only", action="store_true", help="só conferir, sem baixar")
    a = p.parse_args(argv)
    try:
        sets, models = load_lock(a.lock)
        entries = resolve(a.selection, sets, models)
        fetch(
            entries,
            a.models_dir,
            from_dir=a.from_dir,
            check_sha=not a.no_sha,
            check_only=a.check_only,
            token=os.environ.get("HF_TOKEN"),
        )
    except ModelError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
