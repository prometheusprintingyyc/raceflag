import pytest
from pathlib import Path
from pytest_mock import MockerFixture
from raceflag.ota import OTAUpdater


LATEST_RELEASE = {
    "tag_name": "v0.2.0",
    "assets": [
        {"name": "raceflag-v0.2.0.tar.gz", "browser_download_url": "https://example.com/raceflag.tar.gz"}
    ],
}


@pytest.fixture
def updater(tmp_path):
    version_file = tmp_path / "version.txt"
    version_file.write_text("0.1.0")
    install_dir = tmp_path / "raceflag"
    install_dir.mkdir()
    return OTAUpdater(
        version_file=version_file,
        install_dir=install_dir,
        github_repo="prometheusprintingyyc/raceflag",
    )


@pytest.mark.asyncio
async def test_check_returns_no_update_when_current(updater, mocker: MockerFixture):
    mocker.patch.object(updater, "_fetch_latest_release", return_value={"tag_name": "v0.1.0", "assets": []})
    result = await updater.check()
    assert result["update_available"] is False
    assert result["current"] == "0.1.0"


@pytest.mark.asyncio
async def test_check_returns_update_available_when_newer(updater, mocker: MockerFixture):
    mocker.patch.object(updater, "_fetch_latest_release", return_value=LATEST_RELEASE)
    result = await updater.check()
    assert result["update_available"] is True
    assert result["latest"] == "v0.2.0"


@pytest.mark.asyncio
async def test_check_returns_no_update_on_api_error(updater, mocker: MockerFixture):
    mocker.patch.object(updater, "_fetch_latest_release", side_effect=Exception("network error"))
    result = await updater.check()
    assert result["update_available"] is False


def test_is_newer_version_detects_newer(updater):
    assert updater._is_newer("v0.2.0", "0.1.0") is True
    assert updater._is_newer("v1.0.0", "0.9.9") is True


def test_is_newer_version_same_is_not_newer(updater):
    assert updater._is_newer("v0.1.0", "0.1.0") is False


def test_is_newer_version_older_is_not_newer(updater):
    assert updater._is_newer("v0.0.9", "0.1.0") is False


@pytest.mark.asyncio
async def test_is_overlayroot_active_returns_false_when_not_active(mocker: MockerFixture):
    mock_proc = mocker.AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate = mocker.AsyncMock(return_value=(b"", b""))
    mocker.patch("asyncio.create_subprocess_exec", return_value=mock_proc)
    assert await OTAUpdater._is_overlayroot_active() is False


@pytest.mark.asyncio
async def test_is_overlayroot_active_returns_true_when_active(mocker: MockerFixture):
    mock_proc = mocker.AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = mocker.AsyncMock(return_value=(b"", b""))
    mocker.patch("asyncio.create_subprocess_exec", return_value=mock_proc)
    assert await OTAUpdater._is_overlayroot_active() is True


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


# ── _apply_direct: swap like ota_install.sh, never copy a backup over a live install ──
def _direct_archive(up):
    up._work_dir.mkdir(parents=True, exist_ok=True)
    path = up._work_dir / "r.tar.gz"
    path.write_bytes(_release_bytes())
    return path


def test_direct_backup_failure_leaves_the_install_untouched(box, mocker):
    up, _ = box
    stale = up._install_dir.parent / "raceflag.bak"
    (stale / "raceflag").mkdir(parents=True)
    (stale / "raceflag" / "main.py").write_text("STALE")
    real_rmtree = ota_module.shutil.rmtree

    def rmtree(path, *a, **k):
        if Path(path) == stale:
            raise OSError("I/O error")
        return real_rmtree(path, *a, **k)
    mocker.patch("raceflag.ota.shutil.rmtree", side_effect=rmtree)
    assert up._apply_direct(_direct_archive(up)) is False
    assert (up._install_dir / "raceflag" / "main.py").read_text() == "OLD\n"


def test_direct_failed_second_rename_puts_the_install_back(box, mocker):
    up, _ = box
    real_rename = ota_module.os.rename

    def rename(src, dst):
        if Path(dst) == up._install_dir and Path(src).name == "raceflag-staging":
            raise OSError("boom")
        return real_rename(src, dst)
    mocker.patch("raceflag.ota.os.rename", side_effect=rename)
    assert up._apply_direct(_direct_archive(up)) is False
    assert (up._install_dir / "raceflag" / "main.py").read_text() == "OLD\n"


def test_direct_success_keeps_previous_code_as_backup(box):
    up, _ = box
    assert up._apply_direct(_direct_archive(up)) is True
    assert (up._install_dir / "raceflag" / "main.py").read_text() == "NEW\n"
    backup = up._install_dir.parent / "raceflag.bak"
    assert (backup / "raceflag" / "main.py").read_text() == "OLD\n"
