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
