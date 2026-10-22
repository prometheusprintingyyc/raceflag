# tests/test_ota_archive.py
import io
import tarfile
from pathlib import Path

import pytest

from raceflag.ota_archive import ReleaseArchiveError, check_archive, read_member


def _write(path: Path, members: list[tarfile.TarInfo | tuple[str, bytes]]) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for m in members:
            if isinstance(m, tarfile.TarInfo):
                tf.addfile(m)
            else:
                name, data = m
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
    return path


GOOD = [("requirements.txt", b"httpx\n"), ("raceflag/main.py", b"print()\n"), ("version.txt", b"0.3.0\n")]


def test_check_archive_accepts_a_release(tmp_path):
    check_archive(_write(tmp_path / "r.tar.gz", GOOD))


def test_check_archive_accepts_dot_slash_names(tmp_path):
    check_archive(_write(tmp_path / "r.tar.gz", [("./" + n, d) for n, d in GOOD]))


def test_check_archive_rejects_non_gzip(tmp_path):
    p = tmp_path / "r.tar.gz"
    p.write_bytes(b"<html>Not Found</html>")
    with pytest.raises(ReleaseArchiveError):
        check_archive(p)


@pytest.mark.parametrize("missing", ["requirements.txt", "raceflag/main.py"])
def test_check_archive_rejects_missing_files(tmp_path, missing):
    with pytest.raises(ReleaseArchiveError, match=missing):
        check_archive(_write(tmp_path / "r.tar.gz", [m for m in GOOD if m[0] != missing]))


@pytest.mark.parametrize("name", ["/etc/passwd", "../x", "raceflag/../../x"])
def test_check_archive_rejects_paths_outside(tmp_path, name):
    with pytest.raises(ReleaseArchiveError):
        check_archive(_write(tmp_path / "r.tar.gz", GOOD + [(name, b"x")]))


@pytest.mark.parametrize("target", ["/etc/shadow", "../../outside"])
def test_check_archive_rejects_links_outside(tmp_path, target):
    link = tarfile.TarInfo("raceflag/link")
    link.type = tarfile.SYMTYPE
    link.linkname = target
    with pytest.raises(ReleaseArchiveError):
        check_archive(_write(tmp_path / "r.tar.gz", GOOD + [link]))


def test_check_archive_rejects_device_files(tmp_path):
    dev = tarfile.TarInfo("raceflag/dev")
    dev.type = tarfile.CHRTYPE
    with pytest.raises(ReleaseArchiveError):
        check_archive(_write(tmp_path / "r.tar.gz", GOOD + [dev]))


def test_read_member_returns_contents_with_or_without_dot_slash(tmp_path):
    plain = _write(tmp_path / "a.tar.gz", GOOD)
    dotted = _write(tmp_path / "b.tar.gz", [("./" + n, d) for n, d in GOOD])
    assert read_member(plain, "requirements.txt") == b"httpx\n"
    assert read_member(dotted, "requirements.txt") == b"httpx\n"


def test_read_member_missing_raises(tmp_path):
    with pytest.raises(ReleaseArchiveError):
        read_member(_write(tmp_path / "r.tar.gz", GOOD), "nope.txt")


def test_check_archive_rejects_symlink_inside_archive(tmp_path):
    link = tarfile.TarInfo("raceflag/link")
    link.type = tarfile.SYMTYPE
    link.linkname = "main.py"
    with pytest.raises(ReleaseArchiveError, match="raceflag/link"):
        check_archive(_write(tmp_path / "r.tar.gz", GOOD + [link]))


def test_check_archive_rejects_hardlink_member(tmp_path):
    link = tarfile.TarInfo("raceflag/copy.py")
    link.type = tarfile.LNKTYPE
    link.linkname = "raceflag/main.py"
    with pytest.raises(ReleaseArchiveError, match="raceflag/copy.py"):
        check_archive(_write(tmp_path / "r.tar.gz", GOOD + [link]))


def test_check_archive_rejects_chained_symlinks(tmp_path):
    # a/l1 -> .. passes a per-link lexical check, and l2 -> a/l1/.. then escapes
    # the extraction directory once l1 is resolved on disk.
    l1 = tarfile.TarInfo("raceflag/a/l1")
    l1.type = tarfile.SYMTYPE
    l1.linkname = ".."
    l2 = tarfile.TarInfo("raceflag/l2")
    l2.type = tarfile.SYMTYPE
    l2.linkname = "a/l1/.."
    with pytest.raises(ReleaseArchiveError):
        check_archive(_write(tmp_path / "r.tar.gz", GOOD + [l1, l2]))


def test_check_archive_rejects_corrupt_compressed_data(tmp_path):
    import os
    big = os.urandom(200_000)  # incompressible, so the corruption lands inside the deflate data
    p = _write(tmp_path / "r.tar.gz", [("requirements.txt", b"httpx\n"), ("raceflag/main.py", big)])
    raw = bytearray(p.read_bytes())
    mid = len(raw) // 2
    for i in range(mid, mid + 8):
        raw[i] ^= 0xFF
    p.write_bytes(bytes(raw))
    with pytest.raises(ReleaseArchiveError):
        check_archive(p)
