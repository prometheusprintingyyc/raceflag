"""Safety net helpers (feature 011): tell whether the running code is the SD card's code."""
from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path


def _skipped(rel: Path) -> bool:
    return "__pycache__" in rel.parts or rel.suffix == ".pyc"


def code_hash(root: Path) -> str | None:
    """SHA-256 over raceflag/ (minus caches) and requirements.txt; None if root has no package."""
    pkg = root / "raceflag"
    if not pkg.is_dir():
        return None
    files = [p for p in pkg.rglob("*") if p.is_file() and not _skipped(p.relative_to(root))]
    req = root / "requirements.txt"
    if req.is_file():
        files.append(req)
    h = hashlib.sha256()
    for p in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        data = p.read_bytes()
        h.update(f"{p.relative_to(root).as_posix()}\0{len(data)}\0".encode())
        h.update(data)
    return h.hexdigest()


def tar_tree(root: Path) -> bytes:
    """The install directory as a .tar.gz with relative names, caches left out."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if _skipped(rel):
                continue
            tf.add(p, arcname=rel.as_posix(), recursive=False)
    return buf.getvalue()
