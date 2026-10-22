"""Runs raceflag/ota_install.sh for real in a temp directory (needs bash and GNU tar;
on Windows that's Git Bash, never WSL's System32 bash)."""
import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "raceflag" / "ota_install.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(
    BASH is None or (sys.platform == "win32" and "system32" in BASH.lower()),
    reason="needs bash with GNU tar (Git Bash on Windows)")

RELEASE = {"requirements.txt": "httpx\n", "raceflag/main.py": "NEW\n", "version.txt": "9.9.9\n"}


def _tar_gz(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def box(tmp_path):
    opt = tmp_path / "opt"
    install = opt / "raceflag"
    (install / "raceflag").mkdir(parents=True)
    (install / "raceflag" / "main.py").write_text("OLD\n")
    stale = opt / "raceflag.bak"
    (stale / "raceflag").mkdir(parents=True)
    (stale / "raceflag" / "main.py").write_text("STALE\n")
    fake_pip = tmp_path / "fakepip"
    fake_pip.write_text('#!/bin/bash\necho "$@" >> "$PIP_LOG"\nexit "${FAKE_PIP_RC:-0}"\n')
    env = dict(os.environ)
    env.update(
        INSTALL=install.as_posix(), STAGING=(opt / "raceflag-staging").as_posix(),
        BACKUP=stale.as_posix(), WHEELS=(tmp_path / "wheels").as_posix(),
        PIP=f"bash {fake_pip.as_posix()}", PIP_LOG=(tmp_path / "pip.log").as_posix(),
        SERVICE=(tmp_path / "absent.service").as_posix())
    return tmp_path, install, stale, env


def _run(env, data: bytes):
    return subprocess.run([BASH, SCRIPT.as_posix()], input=data, env=env, capture_output=True)


def _steps(result) -> list[str]:
    return [l[5:] for l in result.stdout.decode().splitlines() if l.startswith("STEP ")]


def test_success_swaps_in_the_release_and_keeps_the_previous_code_as_backup(box):
    tmp, install, backup, env = box
    r = _run(env, _tar_gz(RELEASE))
    assert r.returncode == 0, r.stderr
    assert _steps(r) == ["extract", "check", "packages", "swap", "service", "done"]
    assert (install / "raceflag" / "main.py").read_text() == "NEW\n"
    assert (backup / "raceflag" / "main.py").read_text() == "OLD\n"
    assert not (tmp / "opt" / "raceflag-staging").exists()


def test_packages_install_offline_from_the_wheels_dir(box):
    tmp, _, _, env = box
    _run(env, _tar_gz(RELEASE))
    log = (tmp / "pip.log").read_text()
    assert "install --no-index --find-links" in log
    assert (tmp / "wheels").as_posix() in log
    assert "raceflag-staging/requirements.txt" in log


def test_dot_slash_archive_installs(box):
    _, install, _, env = box
    r = _run(env, _tar_gz({"./" + k: v for k, v in RELEASE.items()}))
    assert r.returncode == 0, r.stderr
    assert (install / "raceflag" / "main.py").read_text() == "NEW\n"


def _assert_untouched(install, backup):
    assert (install / "raceflag" / "main.py").read_text() == "OLD\n"
    assert (backup / "raceflag" / "main.py").read_text() == "STALE\n"


def test_bad_archive_changes_nothing(box):
    _, install, backup, env = box
    r = _run(env, b"<html>Not Found</html>")
    assert r.returncode != 0
    assert _steps(r)[-1] == "extract"
    _assert_untouched(install, backup)


def test_archive_without_main_py_changes_nothing(box):
    _, install, backup, env = box
    r = _run(env, _tar_gz({"requirements.txt": "httpx\n"}))
    assert r.returncode != 0
    assert _steps(r)[-1] == "check"
    _assert_untouched(install, backup)


def test_pip_failure_changes_nothing(box):
    _, install, backup, env = box
    env["FAKE_PIP_RC"] = "1"
    r = _run(env, _tar_gz(RELEASE))
    assert r.returncode != 0
    assert _steps(r)[-1] == "packages"
    _assert_untouched(install, backup)


def test_failed_swap_puts_the_current_code_back(box):
    tmp, install, _, env = box
    shim = tmp / "bin"
    shim.mkdir()
    mv = shim / "mv"
    mv.write_text('#!/bin/bash\nif [ "$1" = "$FAIL_MV_SRC" ]; then exit 1; fi\nexec /usr/bin/mv "$@"\n')
    mv.chmod(0o755)
    env["PATH"] = str(shim) + os.pathsep + env["PATH"]
    env["FAIL_MV_SRC"] = env["STAGING"]
    r = _run(env, _tar_gz(RELEASE))
    assert r.returncode != 0
    assert _steps(r)[-1] == "swap"
    assert (install / "raceflag" / "main.py").read_text() == "OLD\n"


def test_service_file_paths_are_fixed(box):
    tmp, _, _, env = box
    svc = tmp / "raceflag.service"
    svc.write_text("Environment=RACEFLAG_CONFIG=/opt/raceflag/config.json\n"
                   "Environment=RACEFLAG_VERSION=/opt/raceflag/version.txt\n")
    env["SERVICE"] = svc.as_posix()
    assert _run(env, _tar_gz(RELEASE)).returncode == 0
    text = svc.read_text()
    assert "RACEFLAG_CONFIG=/boot/firmware/raceflag/config.json" in text
    assert "RACEFLAG_VERSION=/boot/firmware/raceflag/version.txt" in text
