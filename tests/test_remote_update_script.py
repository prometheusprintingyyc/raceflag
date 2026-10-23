"""Runs tools/raceflag-remote-update.sh for real in a temp directory with fake curl, pip,
overlayroot-chroot and reboot (needs bash and GNU tar; on Windows that's Git Bash, never
WSL's System32 bash)."""
import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "tools" / "raceflag-remote-update.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(
    BASH is None or (sys.platform == "win32" and "system32" in BASH.lower()),
    reason="needs bash with GNU tar (Git Bash on Windows)")

REAL_FILES = ["raceflag/__init__.py", "raceflag/ota_archive.py", "raceflag/ota_deps.py",
              "raceflag/ota_install.sh"]


def _release_bytes() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name in REAL_FILES:
            tf.add(ROOT / name, arcname=name)
        for name, text in (("raceflag/main.py", "NEW\n"), ("requirements.txt", "httpx\n")):
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


FAKE_CURL = r'''#!/bin/bash
echo "$@" >> "$CURL_LOG"
[ "${FAKE_CURL_RC:-0}" = 0 ] || exit "$FAKE_CURL_RC"
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then cp "$FAKE_RELEASE" "$2"; fi
  shift
done
'''

FAKE_PIP = r'''#!/bin/bash
echo "$@" >> "$PIP_LOG"
while [ $# -gt 0 ]; do
  case "$1" in
    --report) echo '{"install": []}' > "$2" ;;
    --no-index) exit "${FAKE_PIP_INSTALL_RC:-0}" ;;
  esac
  shift
done
exit "${FAKE_PIP_RC:-0}"
'''

FAKE_CHROOT = '#!/bin/bash\necho "$@" >> "$CHROOT_LOG"\nexec "$@"\n'
FAKE_REBOOT = '#!/bin/bash\necho rebooted >> "$REBOOT_LOG"\nexit "${FAKE_REBOOT_RC:-0}"\n'
FAKE_FALLBACK = '#!/bin/bash\necho rebooted >> "$FALLBACK_LOG"\nexit "${FAKE_FALLBACK_RC:-0}"\n'


def _program(path: Path, text: str) -> str:
    path.write_text(text, newline="\n")
    path.chmod(0o755)
    return path.as_posix()


@pytest.fixture
def box(tmp_path):
    install = tmp_path / "opt" / "raceflag"
    (install / "raceflag").mkdir(parents=True)
    (install / "raceflag" / "main.py").write_text("OLD\n")
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "version.txt").write_text("0.0.1\n")
    release = tmp_path / "release.tar.gz"
    release.write_bytes(_release_bytes())
    mounts = tmp_path / "mounts"
    mounts.write_text("/dev/root / ext4 rw 0 0\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if sys.platform == "win32":
        # Python (native) can't run shebang scripts, so pip gets a .cmd wrapper around bash.
        sh = bin_dir / "fakepip.sh"
        sh.write_text(FAKE_PIP, newline="\n")
        cmd = bin_dir / "fakepip.cmd"
        cmd.write_text(f'@"{BASH}" "{sh.as_posix()}" %*\r\n', newline="")
        pip = cmd.as_posix()
    else:
        pip = _program(bin_dir / "fakepip", FAKE_PIP)
    env = dict(os.environ)
    env.update(
        CURL=_program(bin_dir / "fakecurl", FAKE_CURL),
        PYTHON=Path(sys.executable).as_posix(), PIP=pip,
        OVERLAYROOT_CHROOT=_program(bin_dir / "fakechroot", FAKE_CHROOT),
        REBOOT=_program(bin_dir / "fakereboot", FAKE_REBOOT),
        REBOOT_FALLBACK=_program(bin_dir / "fakefallback", FAKE_FALLBACK),
        DONE_MARKER=(tmp_path / "update.done").as_posix(),
        FALLBACK_LOG=(tmp_path / "fallback.log").as_posix(),
        PROC_MOUNTS=mounts.as_posix(), INSTALL=install.as_posix(),
        BOOT_DIR=boot.as_posix(), WORK_DIR=(tmp_path / "work").as_posix(),
        REQUIRE_ROOT="0", FAKE_RELEASE=release.as_posix(),
        CURL_LOG=(tmp_path / "curl.log").as_posix(), PIP_LOG=(tmp_path / "pip.log").as_posix(),
        CHROOT_LOG=(tmp_path / "chroot.log").as_posix(),
        REBOOT_LOG=(tmp_path / "reboot.log").as_posix(), PYTHONUTF8="1")
    return tmp_path, env


def _run(env, *args):
    return subprocess.run([BASH, SCRIPT.as_posix(), *args], env=env, capture_output=True,
                          text=True, encoding="utf-8")


def _lines(result):
    return result.stdout.splitlines()


def _markers(result):
    return [l[5:] for l in _lines(result) if l.startswith("STEP ")]


def _main(env):
    return (Path(env["INSTALL"]) / "raceflag" / "main.py").read_text().strip()


def _assert_clean(env, result, code):
    assert result.returncode == code, result.stdout + result.stderr
    assert not Path(env["WORK_DIR"]).exists()


def test_success_installs_writes_version_and_reboots(box):
    tmp, env = box
    r = _run(env, "v9.9.9")
    _assert_clean(env, r, 0)
    assert _main(env) == "NEW"
    assert (Path(env["BOOT_DIR"]) / "version.txt").read_text().strip() == "9.9.9"
    assert Path(env["REBOOT_LOG"]).exists()
    assert _markers(r) == ["check", "download", "verify", "packages", "install", "version",
                           "restart"]
    assert _lines(r)[-1] == "DONE"
    assert "releases/download/v9.9.9/raceflag-v9.9.9.tar.gz" in Path(env["CURL_LOG"]).read_text()
    assert not Path(env["CHROOT_LOG"]).exists()


def test_protected_installs_through_overlayroot_chroot(box):
    tmp, env = box
    Path(env["PROC_MOUNTS"]).write_text("overlayroot / overlay rw 0 0\n")
    r = _run(env, "v9.9.9")
    _assert_clean(env, r, 0)
    log = Path(env["CHROOT_LOG"]).read_text()
    assert "/usr/bin/env" in log and "INSTALL=" in log
    assert _main(env) == "NEW"


def test_protected_without_chroot_tool_refuses(box):
    tmp, env = box
    Path(env["PROC_MOUNTS"]).write_text("overlayroot / overlay rw 0 0\n")
    env["OVERLAYROOT_CHROOT"] = "does-not-exist"
    r = _run(env, "v9.9.9")
    _assert_clean(env, r, 1)
    assert "ERROR SD card protection tools are missing." in _lines(r)
    assert _main(env) == "OLD"
    assert not Path(env["CURL_LOG"]).exists()


def test_bad_tag_refused_before_download(box):
    tmp, env = box
    r = _run(env, "latest")
    _assert_clean(env, r, 1)
    assert "ERROR That isn't a RaceFlag release number (expected vX.Y.Z)." in _lines(r)
    assert not Path(env["CURL_LOG"]).exists()


def test_no_install_dir_refused(box):
    tmp, env = box
    env["INSTALL"] = (tmp / "nowhere").as_posix()
    r = _run(env, "v9.9.9")
    _assert_clean(env, r, 1)
    assert "ERROR This doesn't look like a RaceFlag unit (no /opt/raceflag)." in _lines(r)
    assert not Path(env["CURL_LOG"]).exists()


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="running as root")
def test_not_root_refused(box):
    tmp, env = box
    env["REQUIRE_ROOT"] = "1"
    r = _run(env, "v9.9.9")
    _assert_clean(env, r, 1)
    assert "ERROR Run this with sudo." in _lines(r)
    assert not Path(env["CURL_LOG"]).exists()


def test_download_failure_changes_nothing(box):
    tmp, env = box
    env["FAKE_CURL_RC"] = "22"
    r = _run(env, "v9.9.9")
    _assert_clean(env, r, 1)
    assert ("ERROR Could not download the update. Check the unit's internet connection."
            in _lines(r))
    assert _main(env) == "OLD"
    assert (Path(env["BOOT_DIR"]) / "version.txt").read_text().strip() == "0.0.1"
    assert not Path(env["REBOOT_LOG"]).exists()


def test_not_a_release(box):
    tmp, env = box
    Path(env["FAKE_RELEASE"]).write_text("<html>nope</html>")
    r = _run(env, "v9.9.9")
    _assert_clean(env, r, 1)
    assert "ERROR The download is not a RaceFlag release." in _lines(r)
    assert _main(env) == "OLD"
    assert not Path(env["REBOOT_LOG"]).exists()


def test_packages_failure(box):
    tmp, env = box
    env["FAKE_PIP_RC"] = "1"
    r = _run(env, "v9.9.9")
    _assert_clean(env, r, 1)
    assert "ERROR Could not download the packages this update needs." in _lines(r)
    assert _main(env) == "OLD"
    assert not Path(env["REBOOT_LOG"]).exists()


def test_install_failure_reports_step(box):
    tmp, env = box
    env["FAKE_PIP_INSTALL_RC"] = "1"
    r = _run(env, "v9.9.9")
    _assert_clean(env, r, 1)
    assert "ERROR Could not install the update (packages). Nothing was changed." in _lines(r)
    assert any(l.startswith("LOG ") for l in _lines(r))
    assert _main(env) == "OLD"
    assert (Path(env["BOOT_DIR"]) / "version.txt").read_text().strip() == "0.0.1"
    assert not Path(env["REBOOT_LOG"]).exists()


def test_protected_success_creates_done_marker(box):
    tmp, env = box
    Path(env["PROC_MOUNTS"]).write_text("overlayroot / overlay rw 0 0\n")
    _assert_clean(env, _run(env, "v9.9.9"), 0)
    assert Path(env["DONE_MARKER"]).exists()


def test_unprotected_success_has_no_done_marker(box):
    tmp, env = box
    _assert_clean(env, _run(env, "v9.9.9"), 0)
    assert not Path(env["DONE_MARKER"]).exists()


def test_failures_leave_no_done_marker(box):
    tmp, env = box
    Path(env["PROC_MOUNTS"]).write_text("overlayroot / overlay rw 0 0\n")
    env["FAKE_PIP_INSTALL_RC"] = "1"
    _assert_clean(env, _run(env, "v9.9.9"), 1)
    assert not Path(env["DONE_MARKER"]).exists()


def test_reboot_fallback_used_when_reboot_fails(box):
    tmp, env = box
    env["FAKE_REBOOT_RC"] = "1"
    r = _run(env, "v9.9.9")
    _assert_clean(env, r, 0)
    assert _lines(r)[-1] == "DONE"
    assert Path(env["FALLBACK_LOG"]).exists()


def test_both_reboots_failing_is_an_error(box):
    tmp, env = box
    env["FAKE_REBOOT_RC"] = "1"
    env["FAKE_FALLBACK_RC"] = "1"
    r = _run(env, "v9.9.9")
    _assert_clean(env, r, 1)
    assert ("ERROR The update was installed, but RaceFlag couldn't restart itself. "
            "Unplug it, wait 10 seconds and plug it back in.") in _lines(r)
    assert "DONE" not in _lines(r)
    assert (Path(env["BOOT_DIR"]) / "version.txt").read_text().strip() == "9.9.9"


def test_script_body_runs_only_from_main_on_last_line():
    lines = [l for l in SCRIPT.read_text().splitlines() if l.strip()]
    assert lines[-1] == 'main "$@"'
    assert any(l.startswith("main() {") for l in lines)


def test_download_has_a_timeout(box):
    tmp, env = box
    _assert_clean(env, _run(env, "v9.9.9"), 0)
    assert "--max-time" in Path(env["CURL_LOG"]).read_text()


def _snippets():
    import re
    return re.findall(r"-c '\n(.*?)\n[^\n']*'", SCRIPT.read_text(), flags=re.S)


def test_python_snippets_have_no_leading_indentation():
    snippets = _snippets()
    assert len(snippets) == 2
    for src in snippets:
        assert src.splitlines()[0] == src.splitlines()[0].lstrip()
        compile(src + "\n", "snippet", "exec")


def test_runs_with_an_older_python(box):
    tmp, env = box
    try:
        out = subprocess.run(["py", "-3.10", "-c", "import sys;print(sys.executable)"],
                             capture_output=True, text=True)
    except OSError:
        pytest.skip("no py launcher")
    if out.returncode != 0:
        pytest.skip("Python 3.10 not installed")
    env["PYTHON"] = Path(out.stdout.strip()).as_posix()
    Path(env["PROC_MOUNTS"]).write_text("overlayroot / overlay rw 0 0" + chr(10))
    r = _run(env, "v9.9.9")
    _assert_clean(env, r, 0)
    assert _main(env) == "NEW"


def test_missing_boot_dir_is_created_for_the_version(box):
    tmp, env = box
    env["BOOT_DIR"] = (tmp / "newboot" / "raceflag").as_posix()
    _assert_clean(env, _run(env, "v9.9.9"), 0)
    assert (Path(env["BOOT_DIR"]) / "version.txt").read_text().strip() == "9.9.9"


def test_finishes_when_the_client_goes_away(box):
    tmp, env = box
    p = subprocess.Popen([BASH, SCRIPT.as_posix(), "v9.9.9"], env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    p.stdout.readline()
    p.stdout.close()
    assert p.wait(timeout=120) == 0
    assert (Path(env["BOOT_DIR"]) / "version.txt").read_text().strip() == "9.9.9"
    assert Path(env["REBOOT_LOG"]).exists()
