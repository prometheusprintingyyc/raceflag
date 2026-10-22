# OTA on overlayroot units — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A unit running the fixed updater installs later releases through the Update button with overlayroot active, or stays exactly as it was and shows why; code that only lives in RAM saves itself to the SD card.

**Architecture:** The release is downloaded to `/run/raceflag-update/` (RAM, shared with the chroot), checked in Python, its missing packages downloaded outside the chroot, then installed by one bash script (`raceflag/ota_install.sh`) run inside `overlayroot-chroot` with the archive on stdin; the script swaps by rename so the backup always comes from the same attempt. A start-up safety net compares the running code with `/media/root-ro/opt/raceflag` and, if they differ, feeds the running code through the same script.

**Tech Stack:** Python 3.11 asyncio, httpx, tarfile, hashlib; bash + GNU tar inside `overlayroot-chroot`; pip 23 (`--dry-run --report`, `download`, `--no-index --find-links`); FastAPI route; vanilla JS page; pytest + pytest-asyncio (`asyncio_mode=auto`) + pytest-mock.

**Spec:** `docs/superpowers/specs/2026-10-22-ota-overlayroot-fix-design.md`

## Global Constraints

- **Top rule (CLAUDE.md): never break updates for deployed units.** The release format does not change: numeric `vX.Y.Z` tags, one `.tar.gz` asset whose top level holds `raceflag/`, `requirements.txt`, `version.txt`, `install.sh`, `raceflag.service`; GitHub "latest release". Do not touch `.github/workflows/release.yml` or `raceflag.service`.
- `POST /api/update/apply` keeps `success` (bool); `detail` (string or null) is added.
- New code must start without any newly added package (only the standard library and packages already in `requirements.txt`).
- Nothing is written to the boot partition by an update attempt; downloads go to `/run/raceflag-update/` and that directory is removed when the attempt ends.
- Reason strings, verbatim: "Already up to date." · "The release has no update file." · "Could not download the update. Check the connection." · "The download is not a RaceFlag release." · "Could not download the packages this update needs." · "Could not install the update (<step>). Nothing was changed." · "The update took too long and was cancelled." · "SD card protection tools are missing." · plus (ruling, see Task 4) "The update failed. Nothing was changed." for unexpected errors.
- Install script step names, in order: `extract`, `check`, `packages`, `swap`, `service`, `done`.
- Chroot timeout 300 s (unchanged). Safety net: first check 60 s after start, retry every 3600 s after a failure.
- Paths: install `/opt/raceflag`, staging `/opt/raceflag-staging`, backup `/opt/raceflag.bak`, work dir `/run/raceflag-update`, wheels `/run/raceflag-update/wheels`, saved copy `/media/root-ro/opt/raceflag`, piwheels `https://www.piwheels.org/simple/`.
- Tests: `PYTHONUTF8=1 python -m pytest tests -q -p no:warnings`. Six failures are pre-existing and not ours: `test_handle_feed_session_status_finished_sets_break`, `test_handle_feed_session_status_finished_resets_track_status`, `test_play_snapshot_phase_fires_with_is_snapshot_true`, `test_set_delay_rejects_above_60`, `test_test_effect_triggers_led`, `test_check_connectivity_returns_true_when_ping_succeeds`.
- Commits on `beta`; never stage `Notes.txt`, `Notes.txt.bak`, `.claude/settings.local.json` or untracked user files. Trailers:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01UQ9hM8siPUL1nmzWsUhytk`.

## Review Focus

1. A safety-net tarball has `./`-prefixed member names (`tar -C dir .` style) — the archive check and the script's `test -f` must accept them (Task 1 test `test_check_archive_accepts_dot_slash_names`).
2. A timed-out install leaves pip/tar running as orphans if only the chroot wrapper is killed — the whole process group must be signalled (Task 4 test `test_timeout_signals_the_process_group_and_recovers`).
3. `/run` (tmpfs) filling up or a dropped connection mid-download — must end as "Could not download the update…", not a 500 (Task 4 test `test_download_error_reports_download_failed`).
4. The safety net and an Update tap at the same moment — they must not run the install script concurrently (Task 5 test `test_safety_net_waits_for_a_running_update`).
5. A requirement whose version spec changed (an upgrade, not a new package) — the report lists it and it must be downloaded (Task 3 test `test_to_install_lists_upgrades_too`).

---

### Task 1: Release archive check

**Files:**
- Create: `raceflag/ota_archive.py`
- Test: `tests/test_ota_archive.py`

**Interfaces:**
- Produces: `class ReleaseArchiveError(ValueError)`; `check_archive(path: Path) -> None` (raises `ReleaseArchiveError`); `read_member(path: Path, name: str) -> bytes` (accepts `name` or `./name`; raises `ReleaseArchiveError` if absent).

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 python -m pytest tests/test_ota_archive.py -q -p no:warnings`
Expected: ERROR — `ModuleNotFoundError: No module named 'raceflag.ota_archive'`

- [ ] **Step 3: Implement**

```python
# raceflag/ota_archive.py
"""Checks a downloaded release before anything is installed (feature 011)."""
from __future__ import annotations

import posixpath
import tarfile
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
    except (tarfile.TarError, OSError, EOFError) as e:
        raise ReleaseArchiveError(f"not a gzip tar archive: {e}") from e
    names = set()
    for m in members:
        if not _inside(m.name):
            raise ReleaseArchiveError(f"member outside the archive: {m.name}")
        if m.issym() and (m.linkname.startswith("/") or not _inside(
                posixpath.join(posixpath.dirname(posixpath.normpath(m.name)), m.linkname))):
            raise ReleaseArchiveError(f"link outside the archive: {m.name}")
        if m.islnk() and not _inside(m.linkname):
            raise ReleaseArchiveError(f"link outside the archive: {m.name}")
        if not (m.isfile() or m.isdir() or m.issym() or m.islnk()):
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/test_ota_archive.py -q -p no:warnings`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add raceflag/ota_archive.py tests/test_ota_archive.py
git commit -m "feat: check a downloaded release before installing it" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQ9hM8siPUL1nmzWsUhytk"
```

---

### Task 2: The install script

**Files:**
- Create: `raceflag/ota_install.sh`, `.gitattributes`
- Test: `tests/test_ota_install_script.py`

**Interfaces:**
- Produces: `raceflag/ota_install.sh` — reads a release `.tar.gz` on stdin; parameters by environment: `INSTALL`, `STAGING`, `BACKUP`, `WHEELS`, `PIP` (may contain spaces, e.g. `bash /path/fakepip`), `SERVICE`; prints `STEP <name>` before each step (`extract`, `check`, `packages`, `swap`, `service`, `done`); exit 0 only when `STEP done` was printed. Python (Task 4) runs it as `overlayroot-chroot /usr/bin/env INSTALL=… STAGING=… BACKUP=… WHEELS=… /bin/bash -c "<script text>"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ota_install_script.py
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 python -m pytest tests/test_ota_install_script.py -q -p no:warnings`
Expected: FAIL — bash reports `No such file or directory` for `ota_install.sh` (non-zero return codes, assertions fail).

- [ ] **Step 3: Implement**

First create `.gitattributes` (this machine has `core.autocrlf=true`; a CRLF bash script fails
with `$'': command not found`, locally and if ever checked out that way):

```gitattributes
*.sh text eol=lf
```

Then `raceflag/ota_install.sh`:

```bash
#!/bin/bash
# Installs a RaceFlag release from the .tar.gz on stdin (feature 011). Run inside
# overlayroot-chroot so it writes the real SD card; the chroot can't see /boot/firmware,
# so the archive comes on stdin and package files come from $WHEELS under /run.
# Prints "STEP <name>" before each step; the updater reports the last one on failure.
# A failure before "swap" changes nothing; a failed swap puts the current code back.
set -euo pipefail

INSTALL="${INSTALL:-/opt/raceflag}"
STAGING="${STAGING:-/opt/raceflag-staging}"
BACKUP="${BACKUP:-/opt/raceflag.bak}"
WHEELS="${WHEELS:-/run/raceflag-update/wheels}"
PIP="${PIP:-pip3}"
SERVICE="${SERVICE:-/etc/systemd/system/raceflag.service}"

echo "STEP extract"
rm -rf "$STAGING"
mkdir -p "$STAGING"
tar xzf - -C "$STAGING"

echo "STEP check"
test -f "$STAGING/requirements.txt"
test -f "$STAGING/raceflag/main.py"

echo "STEP packages"
mkdir -p "$WHEELS"
# Offline: requirements already installed are satisfied; new ones come from $WHEELS.
$PIP install --no-index --find-links "$WHEELS" -r "$STAGING/requirements.txt" \
  --break-system-packages -q

echo "STEP swap"
# The backup is always this attempt's: the code that is running now.
rm -rf "$BACKUP"
mv "$INSTALL" "$BACKUP"
if ! mv "$STAGING" "$INSTALL"; then
  mv "$BACKUP" "$INSTALL"
  exit 1
fi

echo "STEP service"
if [ -f "$SERVICE" ]; then
  sed -i \
    's|RACEFLAG_CONFIG=/opt/raceflag/config.json|RACEFLAG_CONFIG=/boot/firmware/raceflag/config.json|g; s|RACEFLAG_VERSION=/opt/raceflag/version.txt|RACEFLAG_VERSION=/boot/firmware/raceflag/version.txt|g' \
    "$SERVICE" || true
fi
sync || true

echo "STEP done"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/test_ota_install_script.py -q -p no:warnings`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add .gitattributes raceflag/ota_install.sh tests/test_ota_install_script.py
git commit -m "feat: install script for updates inside overlayroot-chroot" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQ9hM8siPUL1nmzWsUhytk"
```

---

### Task 3: Package download outside the chroot

**Files:**
- Create: `raceflag/ota_deps.py`
- Test: `tests/test_ota_deps.py`

**Interfaces:**
- Produces: `class DependencyError(Exception)`; `to_install(report: dict) -> list[str]` (`"name==version"` items); `async fetch_missing(requirements: Path, wheels: Path, *, pip: str = "pip3", timeout: float = 240.0) -> list[str]`; `async fetch_all(requirements: Path, wheels: Path, *, pip: str = "pip3", timeout: float = 240.0) -> None`; `PIWHEELS = "https://www.piwheels.org/simple/"`. Both async functions raise `DependencyError` on any pip failure or timeout.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ota_deps.py
import json
from pathlib import Path

import pytest

from raceflag import ota_deps
from raceflag.ota_deps import DependencyError, fetch_all, fetch_missing, to_install


def test_to_install_reads_names_and_versions():
    report = {"install": [{"metadata": {"name": "tzdata", "version": "2024.2"}}]}
    assert to_install(report) == ["tzdata==2024.2"]


def test_to_install_empty_when_nothing_needed():
    assert to_install({"install": []}) == []
    assert to_install({}) == []


def test_to_install_lists_upgrades_too():
    report = {"install": [{"metadata": {"name": "httpx", "version": "0.28.1"}},
                          {"metadata": {"name": "anyio", "version": "4.6.0"}}]}
    assert to_install(report) == ["httpx==0.28.1", "anyio==4.6.0"]


def test_to_install_rejects_items_without_version():
    with pytest.raises(DependencyError):
        to_install({"install": [{"metadata": {"name": "x"}}]})


class _Pip:
    """Fake pip: records calls; writes the --report file; returns per-subcommand codes."""
    def __init__(self, report=None, codes=None):
        self.calls, self.report, self.codes = [], report or {"install": []}, codes or {}

    async def __call__(self, *args, timeout):
        self.calls.append(args)
        if "--report" in args:
            Path(args[args.index("--report") + 1]).write_text(json.dumps(self.report))
        sub = args[1]
        return self.codes.get(sub, 0), b"", b"pip said no"


async def test_fetch_missing_downloads_only_what_the_report_lists(tmp_path, monkeypatch):
    pip = _Pip(report={"install": [{"metadata": {"name": "tzdata", "version": "2024.2"}}]})
    monkeypatch.setattr(ota_deps, "_run", pip)
    req = tmp_path / "requirements.txt"
    req.write_text("tzdata\n")
    assert await fetch_missing(req, tmp_path / "wheels") == ["tzdata==2024.2"]
    install, download = pip.calls
    assert install[:3] == ("pip3", "install", "--dry-run")
    assert download[:2] == ("pip3", "download")
    assert "--no-deps" in download and "tzdata==2024.2" in download
    assert str(tmp_path / "wheels") in download
    assert ota_deps.PIWHEELS in download


async def test_fetch_missing_skips_download_when_nothing_needed(tmp_path, monkeypatch):
    pip = _Pip()
    monkeypatch.setattr(ota_deps, "_run", pip)
    req = tmp_path / "requirements.txt"
    req.write_text("httpx\n")
    assert await fetch_missing(req, tmp_path / "wheels") == []
    assert len(pip.calls) == 1
    assert (tmp_path / "wheels").is_dir()


@pytest.mark.parametrize("failing", ["install", "download"])
async def test_fetch_missing_raises_when_pip_fails(tmp_path, monkeypatch, failing):
    pip = _Pip(report={"install": [{"metadata": {"name": "a", "version": "1"}}]}, codes={failing: 1})
    monkeypatch.setattr(ota_deps, "_run", pip)
    req = tmp_path / "requirements.txt"
    req.write_text("a\n")
    with pytest.raises(DependencyError):
        await fetch_missing(req, tmp_path / "wheels")


async def test_fetch_all_downloads_the_whole_requirements_file(tmp_path, monkeypatch):
    pip = _Pip()
    monkeypatch.setattr(ota_deps, "_run", pip)
    req = tmp_path / "requirements.txt"
    req.write_text("httpx\n")
    await fetch_all(req, tmp_path / "wheels")
    (download,) = pip.calls
    assert download[:2] == ("pip3", "download")
    assert "-r" in download and str(req) in download


async def test_run_timeout_raises_dependency_error(monkeypatch):
    import asyncio

    class Slow:
        pid = 1
        returncode = None
        async def communicate(self):
            await asyncio.sleep(10)
        def kill(self):
            self.returncode = -9
        async def wait(self):
            return -9

    async def fake_exec(*a, **k):
        return Slow()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(DependencyError):
        await ota_deps._run("pip3", "download", timeout=0.01)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 python -m pytest tests/test_ota_deps.py -q -p no:warnings`
Expected: ERROR — `ModuleNotFoundError: No module named 'raceflag.ota_deps'`

- [ ] **Step 3: Implement**

```python
# raceflag/ota_deps.py
"""Downloads the packages an update needs, outside overlayroot-chroot (feature 011).

The chroot has no DNS, so pip there installs offline from these files (ota_install.sh).
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PIWHEELS = "https://www.piwheels.org/simple/"


class DependencyError(Exception):
    """pip could not work out or download the packages an update needs."""


def to_install(report: dict) -> list[str]:
    """'name==version' for each package pip's --report says it would install (new or upgraded)."""
    out = []
    for item in report.get("install", []):
        meta = item.get("metadata") or {}
        name, version = meta.get("name"), meta.get("version")
        if not name or not version:
            raise DependencyError("pip report item without a name or version")
        out.append(f"{name}=={version}")
    return out


async def _run(*args: str, timeout: float) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as e:
        proc.kill()
        await proc.wait()
        raise DependencyError(f"{args[0]} {args[1]} timed out") from e
    return proc.returncode, out, err


async def fetch_missing(requirements: Path, wheels: Path, *, pip: str = "pip3",
                        timeout: float = 240.0) -> list[str]:
    """Download (into `wheels`) only what installing `requirements` would add or upgrade.

    The running system has the same packages as the SD card, so a dry run here tells what the
    chroot will be missing. Returns the downloaded 'name==version' list.
    """
    wheels.mkdir(parents=True, exist_ok=True)
    report_path = wheels.parent / "pip-report.json"
    rc, _, err = await _run(
        pip, "install", "--dry-run", "--quiet", "--report", str(report_path),
        "-r", str(requirements), "--break-system-packages", "--extra-index-url", PIWHEELS,
        timeout=timeout)
    if rc != 0:
        raise DependencyError(f"pip dry run failed: {err.decode(errors='replace')[-500:]}")
    try:
        needed = to_install(json.loads(report_path.read_text()))
    except (OSError, ValueError) as e:
        raise DependencyError(f"unreadable pip report: {e}") from e
    if needed:
        rc, _, err = await _run(
            pip, "download", "--no-deps", "-d", str(wheels), "--extra-index-url", PIWHEELS,
            *needed, timeout=timeout)
        if rc != 0:
            raise DependencyError(f"pip download failed: {err.decode(errors='replace')[-500:]}")
    logger.info("Update packages to install: %s", ", ".join(needed) or "none")
    return needed


async def fetch_all(requirements: Path, wheels: Path, *, pip: str = "pip3",
                    timeout: float = 240.0) -> None:
    """Download every package in `requirements` (and dependencies) into `wheels`.

    Used by the safety net: packages that only live in RAM can't be told apart from the SD card's.
    """
    wheels.mkdir(parents=True, exist_ok=True)
    rc, _, err = await _run(
        pip, "download", "-d", str(wheels), "-r", str(requirements),
        "--extra-index-url", PIWHEELS, timeout=timeout)
    if rc != 0:
        raise DependencyError(f"pip download failed: {err.decode(errors='replace')[-500:]}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/test_ota_deps.py -q -p no:warnings`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add raceflag/ota_deps.py tests/test_ota_deps.py
git commit -m "feat: download update packages outside the chroot" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQ9hM8siPUL1nmzWsUhytk"
```

---

### Task 4: Updater — new apply flow with reasons

**Files:**
- Modify: `raceflag/ota.py` (imports and constants at the top; `__init__`; replace `apply()`, `_apply_with_chroot()`, `_rollback_with_chroot()`; keep `_apply_direct`, `_deferred_restart`, `_setup_overlayroot`, `check`, `_is_newer`, `_fetch_latest_release`, `_is_overlayroot_active`)
- Test: `tests/test_ota.py` (append)

**Interfaces:**
- Consumes: `check_archive`, `read_member`, `ReleaseArchiveError` (Task 1); `raceflag/ota_install.sh` (Task 2); `fetch_missing`, `DependencyError` (Task 3).
- Produces: `OTAUpdater(version_file, install_dir, github_repo, *, work_dir=UPDATE_DIR, boot_dir=BOOT_DIR, saved_install_dir=SAVED_INSTALL_DIR)`; `async apply() -> tuple[bool, str | None]`; `async _run_install_script(archive_bytes: bytes) -> None` (raises `UpdateError`); `self._lock: asyncio.Lock` (shared with Task 5); `self._work_dir`, `self._wheels_dir`; module names `UpdateError`, `last_step(output: str) -> str`, `CHROOT_TIMEOUT_S`, `INSTALL_SCRIPT`, `RECOVER_SCRIPT`, reason constants `UP_TO_DATE`, `NO_ASSET`, `DOWNLOAD_FAILED`, `NOT_A_RELEASE`, `PACKAGES_FAILED`, `TIMED_OUT`, `NO_CHROOT`, `UNEXPECTED`, `install_failed(step: str) -> str`.

Ruling recorded in the plan: an unexpected exception (a bug, or `version.txt` not writable) needs a reason too; the spec's list has none, so use "The update failed. Nothing was changed." — cost if wrong: one string.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_ota.py`)

```python
# ── apply(): download, check, install, reasons (feature 011, overlayroot fix) ──
import asyncio
import io
import tarfile

from raceflag import ota as ota_module
from raceflag.ota import (
    DOWNLOAD_FAILED, NO_ASSET, NO_CHROOT, NOT_A_RELEASE, PACKAGES_FAILED, TIMED_OUT,
    UNEXPECTED, UP_TO_DATE, UpdateError, install_failed, last_step,
)
from raceflag.ota_deps import DependencyError


def _release_bytes(files=None) -> bytes:
    files = files or {"requirements.txt": b"httpx\n", "raceflag/main.py": b"NEW\n"}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def box(tmp_path, mocker):
    version_file = tmp_path / "version.txt"
    version_file.write_text("0.1.0")
    install_dir = tmp_path / "opt" / "raceflag"
    (install_dir / "raceflag").mkdir(parents=True)
    (install_dir / "raceflag" / "main.py").write_text("OLD\n")
    up = OTAUpdater(version_file=version_file, install_dir=install_dir,
                    github_repo="prometheusprintingyyc/raceflag",
                    work_dir=tmp_path / "run" / "raceflag-update", boot_dir=tmp_path / "boot",
                    saved_install_dir=tmp_path / "media" / "root-ro" / "opt" / "raceflag")
    mocker.patch.object(up, "_fetch_latest_release", return_value=LATEST_RELEASE)

    async def download(url, tag):
        up._work_dir.mkdir(parents=True, exist_ok=True)
        path = up._work_dir / f"raceflag-{tag}.tar.gz"
        path.write_bytes(_release_bytes())
        return path
    mocker.patch.object(up, "_download", side_effect=download)
    mocker.patch.object(up, "_deferred_restart", new=mocker.AsyncMock())
    return up, version_file


class _Proc:
    def __init__(self, rc=0, out=b"STEP extract\nSTEP done\n", err=b"", hang=False):
        self.returncode, self._out, self._err, self._hang, self.pid = None, out, err, hang, 4242
        self._rc = rc
        self.stdin_data = None
    async def communicate(self, data=None):
        self.stdin_data = data
        if self._hang:
            await asyncio.sleep(10)
        self.returncode = self._rc
        return self._out, self._err
    async def wait(self):
        self.returncode = -15 if self.returncode is None else self.returncode
        return self.returncode


def _protected(mocker, up, procs):
    mocker.patch.object(OTAUpdater, "_is_overlayroot_active", return_value=True)
    mocker.patch("raceflag.ota.fetch_missing", return_value=[])
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return procs.pop(0)
    mocker.patch("raceflag.ota.asyncio.create_subprocess_exec", side_effect=fake_exec)
    return calls


def test_last_step_reads_the_last_marker():
    assert last_step("STEP extract\nSTEP check\nnoise\n") == "check"
    assert last_step("") == "start"


async def test_protected_success_feeds_the_archive_on_stdin_and_writes_version(box, mocker):
    up, version_file = box
    proc = _Proc()
    calls = _protected(mocker, up, [proc])
    assert await up.apply() == (True, None)
    args, kwargs = calls[0]
    assert args[0] == "overlayroot-chroot" and args[1] == "/usr/bin/env"
    assert f"INSTALL={up._install_dir}" in args
    assert f"WHEELS={up._wheels_dir}" in args
    assert args[-3:-1] == ("/bin/bash", "-c")
    assert "STEP extract" in args[-1]
    assert kwargs.get("start_new_session") is True
    assert proc.stdin_data == _release_bytes()
    assert version_file.read_text() == "0.2.0"
    up._deferred_restart.assert_called_once_with(enable_overlayroot=False)
    assert not up._work_dir.exists()


async def test_protected_never_reads_the_boot_partition(box, mocker):
    up, _ = box
    _protected(mocker, up, [_Proc()])
    await up.apply()
    assert not up._boot_dir.exists()


async def test_script_failure_reports_the_step_and_keeps_version(box, mocker):
    up, version_file = box
    _protected(mocker, up, [_Proc(rc=1, out=b"STEP extract\nSTEP check\nSTEP packages\n")])
    assert await up.apply() == (False, install_failed("packages"))
    assert version_file.read_text() == "0.1.0"
    up._deferred_restart.assert_not_called()
    assert not up._work_dir.exists()


async def test_missing_overlayroot_chroot_reports_tools_missing(box, mocker):
    up, _ = box
    mocker.patch.object(OTAUpdater, "_is_overlayroot_active", return_value=True)
    mocker.patch("raceflag.ota.fetch_missing", return_value=[])
    mocker.patch("raceflag.ota.asyncio.create_subprocess_exec", side_effect=FileNotFoundError())
    assert await up.apply() == (False, NO_CHROOT)


async def test_package_download_failure_stops_before_the_chroot(box, mocker):
    up, version_file = box
    mocker.patch.object(OTAUpdater, "_is_overlayroot_active", return_value=True)
    mocker.patch("raceflag.ota.fetch_missing", side_effect=DependencyError("no network"))
    exec_ = mocker.patch("raceflag.ota.asyncio.create_subprocess_exec")
    assert await up.apply() == (False, PACKAGES_FAILED)
    exec_.assert_not_called()
    assert version_file.read_text() == "0.1.0"


async def test_packages_are_planned_from_the_releases_requirements(box, mocker):
    up, _ = box
    _protected(mocker, up, [_Proc()])
    fm = mocker.patch("raceflag.ota.fetch_missing", return_value=[])
    seen = {}

    async def capture(req, wheels):
        seen["req"] = req.read_bytes()
        seen["wheels"] = wheels
        return []
    fm.side_effect = capture
    await up.apply()
    assert seen == {"req": b"httpx\n", "wheels": up._wheels_dir}


async def test_not_a_release_is_refused_before_anything_runs(box, mocker):
    up, _ = box
    mocker.patch.object(OTAUpdater, "_is_overlayroot_active", return_value=True)

    async def bad_download(url, tag):
        up._work_dir.mkdir(parents=True, exist_ok=True)
        p = up._work_dir / "x.tar.gz"
        p.write_bytes(b"<html>")
        return p
    up._download.side_effect = bad_download
    exec_ = mocker.patch("raceflag.ota.asyncio.create_subprocess_exec")
    assert await up.apply() == (False, NOT_A_RELEASE)
    exec_.assert_not_called()


async def test_download_error_reports_download_failed(box, mocker):
    up, _ = box
    mocker.patch.object(OTAUpdater, "_is_overlayroot_active", return_value=True)
    up._download.side_effect = UpdateError(DOWNLOAD_FAILED)
    assert await up.apply() == (False, DOWNLOAD_FAILED)


async def test_real_download_maps_disk_full_to_download_failed(tmp_path, mocker):
    up = OTAUpdater(version_file=tmp_path / "v", install_dir=tmp_path / "i", github_repo="x/y",
                    work_dir=tmp_path / "w")
    mocker.patch("raceflag.ota.open", side_effect=OSError(28, "No space left on device"), create=True)

    class Resp:
        def raise_for_status(self): pass
        async def aiter_bytes(self, n):
            yield b"x"

    class Stream:
        async def __aenter__(self): return Resp()
        async def __aexit__(self, *a): return False

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def stream(self, *a, **k): return Stream()
    mocker.patch("raceflag.ota.httpx.AsyncClient", Client)
    with pytest.raises(UpdateError) as e:
        await up._download("https://example.com/x.tar.gz", "v0.2.0")
    assert e.value.reason == DOWNLOAD_FAILED


async def test_up_to_date_and_no_asset_reasons(box, mocker):
    up, _ = box
    up._fetch_latest_release.return_value = {"tag_name": "v0.1.0", "assets": []}
    assert await up.apply() == (False, UP_TO_DATE)
    up._fetch_latest_release.return_value = {"tag_name": "v0.3.0", "assets": []}
    assert await up.apply() == (False, NO_ASSET)


async def test_release_lookup_failure_reports_download_failed(box, mocker):
    up, _ = box
    up._fetch_latest_release.side_effect = Exception("github down")
    assert await up.apply() == (False, DOWNLOAD_FAILED)


async def test_unexpected_error_reports_generic_reason(box, mocker):
    up, version_file = box
    mocker.patch.object(OTAUpdater, "_is_overlayroot_active", return_value=True)
    mocker.patch("raceflag.ota.fetch_missing", side_effect=RuntimeError("bug"))
    assert await up.apply() == (False, UNEXPECTED)
    assert version_file.read_text() == "0.1.0"


async def test_timeout_signals_the_process_group_and_recovers(box, mocker):
    up, version_file = box
    mocker.patch("raceflag.ota.CHROOT_TIMEOUT_S", 0.01)
    recover = _Proc()
    calls = _protected(mocker, up, [_Proc(hang=True), recover])
    killed = []
    mocker.patch("raceflag.ota._kill_group", side_effect=lambda pid, sig: killed.append(pid))
    assert await up.apply() == (False, TIMED_OUT)
    assert killed and killed[0] == 4242
    recover_args = calls[1][0]
    assert recover_args[-1] == ota_module.RECOVER_SCRIPT
    assert version_file.read_text() == "0.1.0"


async def test_unprotected_uses_the_direct_path(box, mocker):
    up, version_file = box
    mocker.patch.object(OTAUpdater, "_is_overlayroot_active", return_value=False)
    exec_ = mocker.patch("raceflag.ota.asyncio.create_subprocess_exec")
    assert await up.apply() == (True, None)
    exec_.assert_not_called()
    assert (up._install_dir / "raceflag" / "main.py").read_text() == "NEW\n"
    assert version_file.read_text() == "0.2.0"
    up._deferred_restart.assert_called_once_with(enable_overlayroot=True)


async def test_unprotected_failure_reports_files_step(box, mocker):
    up, version_file = box
    mocker.patch.object(OTAUpdater, "_is_overlayroot_active", return_value=False)
    mocker.patch.object(up, "_apply_direct", return_value=False)
    assert await up.apply() == (False, install_failed("files"))
    assert version_file.read_text() == "0.1.0"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 python -m pytest tests/test_ota.py -q -p no:warnings`
Expected: ERROR at import — `ImportError: cannot import name 'DOWNLOAD_FAILED' from 'raceflag.ota'`

- [ ] **Step 3: Implement** — in `raceflag/ota.py`:

Replace the imports and constants block at the top (lines 1–18) with:

```python
from __future__ import annotations
import asyncio
import logging
import os
import shutil
import signal
import tarfile
from pathlib import Path

import httpx

from raceflag.ota_archive import ReleaseArchiveError, check_archive, read_member
from raceflag.ota_deps import DependencyError, fetch_missing

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"

# /boot/firmware/raceflag/ is on the FAT32 boot partition — always writable even when
# overlayroot makes the root filesystem read-only. Config and version.txt live there.
BOOT_DIR = Path("/boot/firmware/raceflag")
# Update downloads: RAM, and /run is shared with overlayroot-chroot (unlike /boot/firmware).
UPDATE_DIR = Path("/run/raceflag-update")
# The real SD card's copy of the install, as overlayroot mounts it (read-only).
SAVED_INSTALL_DIR = Path("/media/root-ro/opt/raceflag")
INSTALL_SCRIPT = Path(__file__).with_name("ota_install.sh")
CHROOT_TIMEOUT_S = 300.0
# After a timeout: put the install back only if the kill landed between the script's two renames.
RECOVER_SCRIPT = '[ -d "$INSTALL" ] || { [ -d "$BACKUP" ] && mv "$BACKUP" "$INSTALL"; }'
_OVERLAYROOT_CONF = Path("/etc/overlayroot.local.conf")
_NM_CONN_SRC = Path("/etc/NetworkManager/system-connections")

# Shown on the page when an update doesn't install (POST /api/update/apply "detail").
UP_TO_DATE = "Already up to date."
NO_ASSET = "The release has no update file."
DOWNLOAD_FAILED = "Could not download the update. Check the connection."
NOT_A_RELEASE = "The download is not a RaceFlag release."
PACKAGES_FAILED = "Could not download the packages this update needs."
TIMED_OUT = "The update took too long and was cancelled."
NO_CHROOT = "SD card protection tools are missing."
UNEXPECTED = "The update failed. Nothing was changed."


def install_failed(step: str) -> str:
    return f"Could not install the update ({step}). Nothing was changed."


class UpdateError(Exception):
    """An update attempt failed; `reason` is shown on the page."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def last_step(output: str) -> str:
    """The last 'STEP <name>' the install script printed ('start' if none)."""
    steps = [line[5:].strip() for line in output.splitlines() if line.startswith("STEP ")]
    return steps[-1] if steps else "start"


def _kill_group(pid: int, sig: int) -> None:
    os.killpg(pid, sig)


async def _stop_process_group(proc) -> None:
    """End the chroot and everything it started (tar, pip). TERM first so overlayroot-chroot
    can unmount and make the SD card read-only again; KILL if it doesn't stop."""
    for sig, wait_s in ((signal.SIGTERM, 10.0), (getattr(signal, "SIGKILL", signal.SIGTERM), 5.0)):
        try:
            _kill_group(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=wait_s)
            return
        except asyncio.TimeoutError:
            continue
```

Replace `__init__` with:

```python
    def __init__(self, version_file: Path, install_dir: Path, github_repo: str, *,
                 work_dir: Path = UPDATE_DIR, boot_dir: Path = BOOT_DIR,
                 saved_install_dir: Path = SAVED_INSTALL_DIR):
        self._version_file = version_file
        self._install_dir = install_dir
        self._github_repo = github_repo
        self._work_dir = work_dir
        self._wheels_dir = work_dir / "wheels"
        self._boot_dir = boot_dir
        self._saved_install_dir = saved_install_dir
        # One update or safety-net save at a time.
        self._lock = asyncio.Lock()
```

Replace `apply()` (whole method, lines 73–133) with:

```python
    async def apply(self) -> tuple[bool, str | None]:
        """Install the latest release: (True, None), or (False, reason to show on the page)."""
        async with self._lock:
            return await self._apply()

    async def _apply(self) -> tuple[bool, str | None]:
        current = self._current_version()
        overlayroot_active = await self._is_overlayroot_active()
        try:
            release = await self._fetch_latest_release()
        except Exception as e:
            logger.error("OTA: could not get the latest release: %s", e)
            return False, DOWNLOAD_FAILED
        tag = release.get("tag_name", "")
        if not self._is_newer(tag, current):
            return False, UP_TO_DATE
        asset = next(
            (a for a in release.get("assets", []) if a.get("name", "").endswith(".tar.gz")),
            None,
        )
        if not asset:
            logger.error("No .tar.gz asset found in release %s", tag)
            return False, NO_ASSET
        try:
            archive = await self._download(asset["browser_download_url"], tag)
            try:
                check_archive(archive)
            except ReleaseArchiveError as e:
                logger.error("OTA: %s", e)
                raise UpdateError(NOT_A_RELEASE) from e
            if overlayroot_active:
                await self._install_protected(archive)
            elif not self._apply_direct(archive):
                raise UpdateError(install_failed("files"))
            self._version_file.write_text(tag.lstrip("v"))
        except UpdateError as e:
            logger.error("OTA update to %s failed: %s", tag, e.reason)
            return False, e.reason
        except Exception:
            logger.exception("OTA update to %s failed", tag)
            return False, UNEXPECTED
        finally:
            shutil.rmtree(self._work_dir, ignore_errors=True)

        # If overlayroot is not yet active, _deferred_restart sets it up and reboots.
        asyncio.create_task(self._deferred_restart(enable_overlayroot=not overlayroot_active))
        return True, None

    async def _download(self, url: str, tag: str) -> Path:
        shutil.rmtree(self._work_dir, ignore_errors=True)
        self._work_dir.mkdir(parents=True)
        path = self._work_dir / f"raceflag-{tag}.tar.gz"
        try:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(path, "wb") as f:
                        async for chunk in resp.aiter_bytes(8192):
                            f.write(chunk)
        except (httpx.HTTPError, OSError) as e:
            logger.error("OTA download failed: %s", e)
            raise UpdateError(DOWNLOAD_FAILED) from e
        return path

    async def _install_protected(self, archive: Path) -> None:
        """overlayroot active: packages first (outside the chroot), then the install script."""
        requirements = self._work_dir / "requirements.txt"
        try:
            requirements.write_bytes(read_member(archive, "requirements.txt"))
            await fetch_missing(requirements, self._wheels_dir)
        except DependencyError as e:
            logger.error("OTA packages: %s", e)
            raise UpdateError(PACKAGES_FAILED) from e
        await self._run_install_script(archive.read_bytes())

    def _script_env(self) -> list[str]:
        parent = self._install_dir.parent
        return [f"INSTALL={self._install_dir}", f"STAGING={parent / 'raceflag-staging'}",
                f"BACKUP={parent / 'raceflag.bak'}", f"WHEELS={self._wheels_dir}"]

    async def _run_install_script(self, archive_bytes: bytes) -> None:
        """Run ota_install.sh in overlayroot-chroot with the archive on stdin. Raises UpdateError."""
        env = self._script_env()
        try:
            proc = await asyncio.create_subprocess_exec(
                "overlayroot-chroot", "/usr/bin/env", *env, "/bin/bash", "-c",
                INSTALL_SCRIPT.read_text(),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, start_new_session=True,
            )
        except FileNotFoundError as e:
            raise UpdateError(NO_CHROOT) from e
        try:
            out, err = await asyncio.wait_for(proc.communicate(archive_bytes),
                                              timeout=CHROOT_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.error("Install script timed out after %.0f s", CHROOT_TIMEOUT_S)
            await _stop_process_group(proc)
            await self._recover_after_timeout(env)
            raise UpdateError(TIMED_OUT)
        if proc.returncode != 0:
            step = last_step(out.decode(errors="replace"))
            logger.error("Install script failed at %s: %s", step,
                         err.decode(errors="replace")[-2000:])
            raise UpdateError(install_failed(step))

    async def _recover_after_timeout(self, env: list[str]) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "overlayroot-chroot", "/usr/bin/env", *env, "/bin/bash", "-c", RECOVER_SCRIPT,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            _, err = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode:
                logger.error("Recovery after a timed-out update failed: %s",
                             err.decode(errors="replace"))
        except Exception as e:
            logger.error("Recovery after a timed-out update failed: %s", e)
```

Delete `_apply_with_chroot()` and `_rollback_with_chroot()` (lines 171–236). Keep `_apply_direct()` as it is (it extracts with tarfile, keeps the backup made by the same attempt and restores it on failure).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/test_ota.py tests/test_ota_archive.py tests/test_ota_deps.py tests/test_ota_install_script.py -q -p no:warnings`
Expected: all pass.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONUTF8=1 python -m pytest tests -q -p no:warnings`
Expected: only the six pre-existing failures (the route still returns `success` from a tuple — that's fixed in Task 6; if a web-server test fails because `ota.apply()` now returns a tuple, note it and fix it in Task 6).

- [ ] **Step 6: Commit**

```bash
git add raceflag/ota.py tests/test_ota.py
git commit -m "fix: updates install on overlayroot units — archive on stdin, offline packages, same-attempt backup" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQ9hM8siPUL1nmzWsUhytk"
```

---

### Task 5: Start-up clean-up and the safety net

**Files:**
- Create: `raceflag/ota_safety.py`
- Modify: `raceflag/ota.py` (add methods to `OTAUpdater`), `raceflag/main.py:205` and the `tasks` list (`main.py:234`)
- Test: `tests/test_ota_safety.py`

**Interfaces:**
- Consumes: `OTAUpdater._lock`, `_work_dir`, `_wheels_dir`, `_boot_dir`, `_saved_install_dir`, `_install_dir`, `_run_install_script`, `UpdateError`, `_is_overlayroot_active` (Task 4); `fetch_all`, `DependencyError` (Task 3).
- Produces: `code_hash(root: Path) -> str | None`; `tar_tree(root: Path) -> bytes`; `OTAUpdater.clean_leftovers() -> None`; `async OTAUpdater.persist_if_ram_only() -> str` (`"not_protected" | "same" | "saved" | "failed"`); `async OTAUpdater.run_safety_net(first_delay: float = 60.0, retry: float = 3600.0) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ota_safety.py
import asyncio
import io
import tarfile
from pathlib import Path

import pytest

from raceflag.ota import OTAUpdater, UpdateError
from raceflag.ota_deps import DependencyError
from raceflag.ota_safety import code_hash, tar_tree


def _tree(root: Path, main="A\n", extra=None):
    (root / "raceflag").mkdir(parents=True, exist_ok=True)
    (root / "raceflag" / "main.py").write_text(main)
    (root / "requirements.txt").write_text("httpx\n")
    for rel, text in (extra or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def test_code_hash_equal_for_equal_trees(tmp_path):
    assert code_hash(_tree(tmp_path / "a")) == code_hash(_tree(tmp_path / "b"))


def test_code_hash_changes_with_content_or_requirements(tmp_path):
    a = code_hash(_tree(tmp_path / "a"))
    assert code_hash(_tree(tmp_path / "b", main="B\n")) != a
    c = _tree(tmp_path / "c")
    (c / "requirements.txt").write_text("httpx\ntzdata\n")
    assert code_hash(c) != a


def test_code_hash_ignores_pycache(tmp_path):
    a = code_hash(_tree(tmp_path / "a"))
    b = code_hash(_tree(tmp_path / "b", extra={"raceflag/__pycache__/main.cpython-311.pyc": "x",
                                                 "raceflag/x.pyc": "y"}))
    assert a == b


def test_code_hash_none_without_package(tmp_path):
    assert code_hash(tmp_path / "missing") is None


def test_tar_tree_has_relative_names_and_skips_pycache(tmp_path):
    root = _tree(tmp_path / "r", extra={"raceflag/__pycache__/m.pyc": "x"})
    with tarfile.open(fileobj=io.BytesIO(tar_tree(root)), mode="r:gz") as tf:
        names = tf.getnames()
    assert "raceflag/main.py" in names and "requirements.txt" in names
    assert not any("__pycache__" in n for n in names)
    assert not any(n.startswith("/") for n in names)


@pytest.fixture
def unit(tmp_path, mocker):
    running = _tree(tmp_path / "opt" / "raceflag", main="NEW\n")
    saved = _tree(tmp_path / "media" / "root-ro" / "opt" / "raceflag", main="OLD\n")
    up = OTAUpdater(version_file=tmp_path / "version.txt", install_dir=running, github_repo="x/y",
                    work_dir=tmp_path / "run" / "raceflag-update", boot_dir=tmp_path / "boot",
                    saved_install_dir=saved)
    mocker.patch.object(OTAUpdater, "_is_overlayroot_active", return_value=True)
    fetch = mocker.patch("raceflag.ota.fetch_all", return_value=None)
    script = mocker.patch.object(up, "_run_install_script", new=mocker.AsyncMock())
    return up, running, saved, fetch, script


async def test_same_code_does_nothing(unit):
    up, running, saved, fetch, script = unit
    (saved / "raceflag" / "main.py").write_text("NEW\n")
    assert await up.persist_if_ram_only() == "same"
    fetch.assert_not_called()
    script.assert_not_called()


async def test_not_protected_does_nothing(unit, mocker):
    up, *_, script = unit
    mocker.patch.object(OTAUpdater, "_is_overlayroot_active", return_value=False)
    assert await up.persist_if_ram_only() == "not_protected"
    script.assert_not_called()


async def test_no_saved_copy_mounted_does_nothing(unit, tmp_path):
    up, *_, script = unit
    up._saved_install_dir = tmp_path / "nowhere"
    assert await up.persist_if_ram_only() == "not_protected"
    script.assert_not_called()


async def test_ram_only_code_is_saved_through_the_install_script(unit):
    up, running, saved, fetch, script = unit
    assert await up.persist_if_ram_only() == "saved"
    fetch.assert_called_once_with(running / "requirements.txt", up._wheels_dir)
    (data,), _ = script.call_args
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        assert tf.extractfile("raceflag/main.py").read() == b"NEW\n"
    assert not up._work_dir.exists()


@pytest.mark.parametrize("error", [DependencyError("no network"), UpdateError("x")])
async def test_save_failure_reports_failed(unit, error):
    up, _, _, fetch, script = unit
    fetch.side_effect = error if isinstance(error, DependencyError) else None
    script.side_effect = error if isinstance(error, UpdateError) else None
    assert await up.persist_if_ram_only() == "failed"
    assert not up._work_dir.exists()


async def test_safety_net_waits_for_a_running_update(unit):
    up, *_ = unit
    await up._lock.acquire()
    task = asyncio.create_task(up.persist_if_ram_only())
    await asyncio.sleep(0.05)
    assert not task.done()
    up._lock.release()
    assert await task == "saved"


async def test_run_safety_net_retries_until_saved(unit, mocker):
    up, *_ = unit
    results = iter(["failed", "failed", "saved"])
    mocker.patch.object(up, "persist_if_ram_only", side_effect=lambda: next(results))
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)
    mocker.patch("raceflag.ota.asyncio.sleep", side_effect=fake_sleep)
    await up.run_safety_net(first_delay=60.0, retry=3600.0)
    assert sleeps == [60.0, 3600.0, 3600.0]


async def test_run_safety_net_survives_unexpected_errors(unit, mocker):
    up, *_ = unit
    calls = iter([RuntimeError("bug"), "same"])

    async def flaky():
        r = next(calls)
        if isinstance(r, Exception):
            raise r
        return r
    mocker.patch.object(up, "persist_if_ram_only", side_effect=flaky)
    mocker.patch("raceflag.ota.asyncio.sleep", new=mocker.AsyncMock())
    await up.run_safety_net()


def test_clean_leftovers_removes_old_downloads_and_work_dir(tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "raceflag-v0.2.35.tar.gz").write_bytes(b"x")
    (boot / "config.json").write_text("{}")
    work = tmp_path / "run" / "raceflag-update"
    work.mkdir(parents=True)
    up = OTAUpdater(version_file=tmp_path / "v", install_dir=tmp_path / "i", github_repo="x/y",
                    work_dir=work, boot_dir=boot)
    up.clean_leftovers()
    assert not (boot / "raceflag-v0.2.35.tar.gz").exists()
    assert (boot / "config.json").exists()
    assert not work.exists()


def test_clean_leftovers_without_boot_dir_is_fine(tmp_path):
    up = OTAUpdater(version_file=tmp_path / "v", install_dir=tmp_path / "i", github_repo="x/y",
                    work_dir=tmp_path / "w", boot_dir=tmp_path / "absent")
    up.clean_leftovers()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 python -m pytest tests/test_ota_safety.py -q -p no:warnings`
Expected: ERROR — `ModuleNotFoundError: No module named 'raceflag.ota_safety'`

- [ ] **Step 3: Implement**

```python
# raceflag/ota_safety.py
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
```

In `raceflag/ota.py`, add to the imports:

```python
from raceflag.ota_deps import DependencyError, fetch_all, fetch_missing
from raceflag.ota_safety import code_hash, tar_tree
```

(replacing the Task 4 line `from raceflag.ota_deps import DependencyError, fetch_missing`), and add these methods to `OTAUpdater` (after `_recover_after_timeout`):

```python
    def clean_leftovers(self) -> None:
        """At start: remove downloads the old updater left on the boot partition, and any
        unfinished work directory. Errors are logged and ignored."""
        try:
            for path in self._boot_dir.glob("raceflag-*.tar.gz"):
                path.unlink()
                logger.info("Removed leftover update download %s", path.name)
        except OSError as e:
            logger.warning("Could not remove leftover update downloads: %s", e)
        shutil.rmtree(self._work_dir, ignore_errors=True)

    async def persist_if_ram_only(self) -> str:
        """Safety net: if overlayroot is active and the running code differs from the SD card's
        saved copy (it only lives in RAM), save it to the SD card through the install script.
        Returns 'not_protected', 'same', 'saved' or 'failed'."""
        if not await self._is_overlayroot_active() or not self._saved_install_dir.is_dir():
            return "not_protected"
        async with self._lock:
            running = await asyncio.to_thread(code_hash, self._install_dir)
            saved = await asyncio.to_thread(code_hash, self._saved_install_dir)
            if running is None or running == saved:
                return "same"
            logger.warning("Running code is only in RAM — saving it to the SD card")
            try:
                shutil.rmtree(self._work_dir, ignore_errors=True)
                self._work_dir.mkdir(parents=True)
                await fetch_all(self._install_dir / "requirements.txt", self._wheels_dir)
                data = await asyncio.to_thread(tar_tree, self._install_dir)
                await self._run_install_script(data)
            except (DependencyError, UpdateError) as e:
                logger.error("Could not save the running code to the SD card: %s", e)
                return "failed"
            finally:
                shutil.rmtree(self._work_dir, ignore_errors=True)
            logger.info("Running code saved to the SD card")
            return "saved"

    async def run_safety_net(self, first_delay: float = 60.0, retry: float = 3600.0) -> None:
        """Start-up task: check once after `first_delay` (only code that has run that long is
        saved), then retry every `retry` seconds until nothing is left to do."""
        await asyncio.sleep(first_delay)
        while True:
            try:
                result = await self.persist_if_ram_only()
            except Exception:
                logger.exception("Safety net check failed")
                result = "failed"
            if result != "failed":
                return
            await asyncio.sleep(retry)
```

In `raceflag/main.py`, after `ota = OTAUpdater(...)` (line 205) add:

```python
    ota.clean_leftovers()
```

and append the safety net to the tasks list (line 234):

```python
    tasks = [_refresh_standings_loop(jolpica, state), listener.start(), server.serve(), button.start(), _ensure_overlayroot(ota), f1_renewer.run(), ota.run_safety_net()]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/test_ota_safety.py tests/test_ota.py -q -p no:warnings`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add raceflag/ota_safety.py raceflag/ota.py raceflag/main.py tests/test_ota_safety.py
git commit -m "feat: safety net saves RAM-only code to the SD card; clean old update downloads" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQ9hM8siPUL1nmzWsUhytk"
```

---

### Task 6: Route, page, changelog and specs

**Files:**
- Modify: `raceflag/web_server.py:309-314`, `raceflag/frontend/index.html:191-194`, `raceflag/frontend/style.css` (after line 263), `raceflag/frontend/app.js` (the `btn-update` click handler), `CHANGELOG.md`
- Modify (specs repo `../raceflag_specs`): `features/011-ota-updates.md`, `contracts/api.md` (`POST /api/update/apply`), `PARITY.md`
- Test: `tests/test_web_server.py` (append)

**Interfaces:**
- Consumes: `OTAUpdater.apply() -> tuple[bool, str | None]` (Task 4).
- Produces: `POST /api/update/apply` → `{"success": bool, "detail": str | null}`.

Ruling recorded in the plan: PARITY's ESP32 OTA columns stay ✅ (released, and the added `detail` field is Pi-only and optional); a PARITY note line tells the ESP32 session instead of flipping its columns — cost if wrong: one line.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_web_server.py`)

```python
# ── POST /api/update/apply reports why an update didn't install (feature 011) ──

def _ota_client(app_state, config, led, result):
    ota = MagicMock()
    ota.apply = AsyncMock(return_value=result)
    return TestClient(create_app(state=app_state, config=config, led=led, ota=ota))


def test_update_apply_success_has_no_detail(app_state, config, led):
    resp = _ota_client(app_state, config, led, (True, None)).post("/api/update/apply")
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "detail": None}


def test_update_apply_failure_returns_the_reason(app_state, config, led):
    reason = "Could not install the update (packages). Nothing was changed."
    resp = _ota_client(app_state, config, led, (False, reason)).post("/api/update/apply")
    assert resp.json() == {"success": False, "detail": reason}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 python -m pytest tests/test_web_server.py -q -p no:warnings -k update_apply`
Expected: FAIL — the route returns `{"success": [true, null]}`.

- [ ] **Step 3: Implement the route** (`raceflag/web_server.py`)

```python
    @app.post("/api/update/apply")
    async def apply_update():
        if ota is None:
            raise HTTPException(503, "OTA not available")
        success, detail = await ota.apply()
        return {"success": success, "detail": detail}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/test_web_server.py -q -p no:warnings -k update_apply`
Expected: 2 passed.

- [ ] **Step 5: The page**

`raceflag/frontend/index.html` — replace the "Check for updates" row (lines 191–194) with:

```html
      <div class="settings-row">
        <span class="settings-label">Check for updates</span>
        <button class="btn-update" id="btn-update" disabled>Up to date</button>
      </div>
      <span class="update-msg" id="update-msg" role="status" hidden></span>
```

`raceflag/frontend/style.css` — after the `.f1-account-msg[hidden]` rule (line 263) add:

```css
.update-msg { display: block; font-size: 12px; color: #ff5a52; line-height: 1.4; }
.update-msg[hidden] { display: none; }
```

`raceflag/frontend/app.js` — replace the start of the `btn-update` click handler, from `document.getElementById('btn-update').addEventListener('click', async () => {` through the line `try { await fetch('/api/update/apply', { method: 'POST' }); } catch (e) {}`, with:

```javascript
document.getElementById('btn-update').addEventListener('click', async () => {
  const btn = document.getElementById('btn-update');
  btn.textContent = 'Updating…';
  btn.disabled = true;
  _showMsg('update-msg', '', false);
  let result = null;
  try {
    const resp = await fetch('/api/update/apply', { method: 'POST' });
    result = await resp.json().catch(() => null);
  } catch (e) { /* the unit may restart before answering; keep polling below */ }
  if (result && result.success === false) {
    _showMsg('update-msg', result.detail || "The update didn't install. Nothing was changed.", true);
    loadSettings();  // puts the button back ("Update to vX" or "Up to date")
    return;
  }
```

(the rest of the handler — the 5 s wait and the polling loop — stays as it is).

Run: `node --check raceflag/frontend/app.js`
Expected: no output (syntax OK).

- [ ] **Step 6: Full suite**

Run: `PYTHONUTF8=1 python -m pytest tests -q -p no:warnings`
Expected: only the six pre-existing failures.

- [ ] **Step 7: Changelog** — in `CHANGELOG.md` `[Unreleased]` → `### Fixed`, add as the last bullet:

```markdown
- **Updates on units with SD card protection** (beta): the updater couldn't see its own download inside `overlayroot-chroot`, so every update failed on protected units, and the failure could put back an older backup. The release now goes to RAM (`/run/raceflag-update/`) and is fed into the chroot on stdin; packages are downloaded outside the chroot and installed offline; the swap keeps a backup from the same attempt, so a failure never installs older code; the page shows why an update didn't install (`POST /api/update/apply` adds `detail`). Leftover downloads on the boot partition are removed at start. A safety net saves code that only lives in RAM to the SD card a minute after start. Units already protected need one SD card re-flash to get this updater
```

- [ ] **Step 8: Specs** (`../raceflag_specs`; stage only these files)

`features/011-ota-updates.md` — after the Path B bullets and the line "On failure, the backup at `/opt/raceflag.bak` is restored." add:

```markdown
> **Beta (Pi):** Path B fixed (2026-10-22). `overlayroot-chroot` bind-mounts only `/proc`, `/run`
> and `/sys`, so the old Path B could not see its download on `/boot/firmware` and every update
> failed on protected units (and the failure could restore an older `/opt/raceflag.bak`). Now:
> - The release downloads to `/run/raceflag-update/` (RAM; removed after the attempt; nothing is
>   written to the boot partition) and is checked (gzip tar, nothing outside its directory,
>   `requirements.txt` and `raceflag/main.py` present).
> - Packages: outside the chroot, `pip3 install --dry-run --report` lists what the release would
>   add or upgrade, `pip3 download --no-deps` fetches only those into `/run/raceflag-update/wheels`;
>   inside, pip installs offline from there. A failure stops the update before the code changes.
> - `raceflag/ota_install.sh` runs in the chroot with the archive on stdin: extract to
>   `/opt/raceflag-staging`, check, packages, swap by rename (the running code becomes
>   `/opt/raceflag.bak`, made by this attempt; a failed swap puts it back), service-file path fix.
>   A failure before the swap changes nothing. Timeout 300 s: the process group is stopped and
>   the install is put back if the stop landed between the two renames.
> - `POST /api/update/apply` adds `detail`, the reason shown on the page: "Already up to date.",
>   "The release has no update file.", "Could not download the update. Check the connection.",
>   "The download is not a RaceFlag release.", "Could not download the packages this update
>   needs.", "Could not install the update (<step>). Nothing was changed.", "The update took too
>   long and was cancelled.", "SD card protection tools are missing.", "The update failed.
>   Nothing was changed."
> - At start: `raceflag-*.tar.gz` left on the boot partition are deleted. A minute after start,
>   with overlayroot active, the running code is compared (SHA-256 of `raceflag/` and
>   `requirements.txt`, caches skipped) with `/media/root-ro/opt/raceflag`; if it differs (code
>   only in RAM), it is saved through the same script after `pip3 download -r requirements.txt`;
>   retried hourly on failure.
> - Units already protected can't receive this by OTA (their installed updater fails first);
>   they are re-flashed once. Every release is OTA-tested on a protected unit before it is
>   published (Pi repo CLAUDE.md, release gate).
```

`contracts/api.md` — in `POST /api/update/apply`, after its response example add:

```markdown
> **Beta (Pi):** also returns `detail`: `null` on success, otherwise the reason shown on the page
> (see [011](../features/011-ota-updates.md)), e.g.
> `{"success": false, "detail": "Could not install the update (packages). Nothing was changed."}`.
```

`PARITY.md` — set the OTA row's Pi column to 🧪 (ESP32 columns unchanged), and add a note line above the table:

```markdown
Pi 🧪 on OTA updates: Path B (overlayroot) rewritten — archive on stdin, offline packages, same-attempt backup, `detail` reason in `POST /api/update/apply` (Pi-only, optional), safety net for RAM-only code (feature 011). Nothing for the ESP32 to change.
```

Commit the specs (in `../raceflag_specs`):

```bash
git add features/011-ota-updates.md contracts/api.md PARITY.md
git commit -m "spec: OTA updates — Pi Path B fixed for overlayroot (beta)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQ9hM8siPUL1nmzWsUhytk"
```

- [ ] **Step 9: Commit the Pi repo**

```bash
git add raceflag/web_server.py raceflag/frontend/index.html raceflag/frontend/style.css raceflag/frontend/app.js tests/test_web_server.py CHANGELOG.md
git commit -m "feat: update failures show their reason on the page" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UQ9hM8siPUL1nmzWsUhytk"
```

---

### Task 7 (owner, on hardware): the release gate

Not code. After the branch is reviewed, pushed to `beta` (with the owner's go-ahead) and installed on the test unit, the owner runs these; no real release is tagged until all pass.

1. Make a public repo `prometheusprintingyyc/raceflag-ota-test`. Point the test unit at it permanently:
   `sudo overlayroot-chroot sh -c 'mkdir -p /etc/systemd/system/raceflag.service.d && printf "[Service]\nEnvironment=RACEFLAG_REPO=prometheusprintingyyc/raceflag-ota-test\n" > /etc/systemd/system/raceflag.service.d/ota-test.conf'` then `sudo reboot`.
2. Confirm protection: `grep overlayroot /proc/mounts` prints a line. Confirm `pip3 --version` is 22.2 or later.
3. Release `v0.2.90` in the test repo: the `beta` code with `version.txt` = `0.2.90` and one small new package added to `requirements.txt`; asset built exactly like `release.yml` (`tar -czf raceflag-v0.2.90.tar.gz raceflag/ requirements.txt version.txt install.sh raceflag.service`).
4. Update button → the page shows v0.2.90. `sudo reboot` → still v0.2.90; `journalctl -u raceflag | grep -i "update packages"` names the new package; `python3 -c "import <package>"` works.
5. Release `v0.2.91` whose asset is not a tar.gz → the page shows "The download is not a RaceFlag release."; after a reboot still v0.2.90.
6. Safety net: copy a different build into `/opt/raceflag` without the chroot (RAM only), `sudo systemctl restart raceflag`, wait 70 s → log "Running code saved to the SD card"; reboot → that build runs.
7. Once with protection off (`overlayroot=disabled` in `cmdline.txt`), update to a higher test release → direct path works, protection turns on again after its reboot (remove `overlayroot=disabled` first).
8. If a unit or SD card with v0.2.21 is available: update it to the test release → direct path, then protection turns on.
9. Remove the drop-in (`sudo overlayroot-chroot rm /etc/systemd/system/raceflag.service.d/ota-test.conf`, reboot) before using the unit normally.
