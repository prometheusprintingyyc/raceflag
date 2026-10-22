# raceflag/ota_archive.py
"""Checks a downloaded release before anything is installed (feature 011)."""
from __future__ import annotations

import posixpath
import tarfile
import zlib
from pathlib import Path

REQUIRED = ("requirements.txt", "raceflag/main.py")


class ReleaseArchiveError(ValueError):
    """The download is not a usable RaceFlag release."""


def _inside(path: str) -> bool:
    norm = posixpath.normpath(path)
    return not (path.startswith("/") or norm == ".." or norm.startswith("../"))


def check_archive(path: Path) -> None:
    """Raise ReleaseArchiveError unless `path` is a gzip tar whose members all stay inside the
    extraction directory and which holds requirements.txt and raceflag/main.py."""
    try:
        with tarfile.open(path, "r:gz") as tf:
            members = tf.getmembers()
            for m in members:  # reading to the end checks the gzip CRC
                if m.isfile():
                    f = tf.extractfile(m)
                    while f is not None and f.read(1 << 16):
                        pass
            # tarfile stops at the end-of-archive blocks; read on so the gzip trailer is checked.
            while tf.fileobj.read(1 << 16):
                pass
    except (tarfile.TarError, OSError, EOFError, zlib.error) as e:
        raise ReleaseArchiveError(f"not a gzip tar archive: {e}") from e
    names = set()
    for m in members:
        if not _inside(m.name):
            raise ReleaseArchiveError(f"member outside the archive: {m.name}")
        if m.issym() or m.islnk():
            raise ReleaseArchiveError(f"links are not allowed: {m.name}")
        if not (m.isfile() or m.isdir()):
            raise ReleaseArchiveError(f"unsupported member type: {m.name}")
        names.add(posixpath.normpath(m.name))
    for required in REQUIRED:
        if required not in names:
            raise ReleaseArchiveError(f"missing {required}")


def read_member(path: Path, name: str) -> bytes:
    """Contents of `name` (also found as ./name). Raises ReleaseArchiveError if absent."""
    with tarfile.open(path, "r:gz") as tf:
        for m in tf.getmembers():
            if m.isfile() and posixpath.normpath(m.name) == name:
                f = tf.extractfile(m)
                if f is not None:
                    return f.read()
    raise ReleaseArchiveError(f"missing {name}")
