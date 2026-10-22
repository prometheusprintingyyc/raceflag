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
