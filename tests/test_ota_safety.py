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
    (root / "raceflag" / "main.py").write_bytes(main.encode())
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
    (saved / "raceflag" / "main.py").write_bytes(b"NEW\n")
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
