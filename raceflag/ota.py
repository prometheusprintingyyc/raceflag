from __future__ import annotations
import asyncio
import logging
import shutil
import tarfile
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"


class OTAUpdater:
    def __init__(self, version_file: Path, install_dir: Path, github_repo: str):
        self._version_file = version_file
        self._install_dir = install_dir
        self._github_repo = github_repo

    def _current_version(self) -> str:
        try:
            return self._version_file.read_text().strip()
        except FileNotFoundError:
            return "0.0.0"

    def _is_newer(self, tag: str, current: str) -> bool:
        def parse(v: str) -> tuple[int, ...]:
            return tuple(int(x) for x in v.lstrip("v").split("."))
        try:
            return parse(tag) > parse(current)
        except ValueError:
            return False

    async def _fetch_latest_release(self) -> dict:
        url = GITHUB_API.format(repo=self._github_repo)
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"Accept": "application/vnd.github+json"})
            resp.raise_for_status()
            return resp.json()

    async def check(self) -> dict:
        current = self._current_version()
        try:
            release = await self._fetch_latest_release()
            tag = release.get("tag_name", "")
            available = self._is_newer(tag, current)
            return {"current": current, "latest": tag, "update_available": available}
        except Exception as e:
            logger.warning("OTA check failed: %s", e)
            return {"current": current, "latest": None, "update_available": False}

    async def apply(self) -> bool:
        current = self._current_version()
        try:
            release = await self._fetch_latest_release()
            tag = release.get("tag_name", "")
            if not self._is_newer(tag, current):
                return False

            asset = next(
                (a for a in release.get("assets", []) if a["name"].endswith(".tar.gz")),
                None,
            )
            if not asset:
                logger.error("No .tar.gz asset found in release %s", tag)
                return False

            archive_path = self._install_dir.parent / f"raceflag-{tag}.tar.gz"
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream("GET", asset["browser_download_url"]) as resp:
                    resp.raise_for_status()
                    with open(archive_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(8192):
                            f.write(chunk)

            backup_dir = self._install_dir.parent / "raceflag.bak"
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
            shutil.copytree(self._install_dir, backup_dir)

            staging = self._install_dir.parent / "raceflag-staging"
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir()
            with tarfile.open(archive_path) as tf:
                tf.extractall(staging)

            shutil.rmtree(self._install_dir)
            shutil.move(str(staging), str(self._install_dir))
            archive_path.unlink(missing_ok=True)

            self._version_file.write_text(tag.lstrip("v"))

            proc = await asyncio.create_subprocess_exec(
                "systemctl", "restart", "raceflag",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            return True

        except Exception as e:
            logger.error("OTA update failed: %s — rolling back", e)
            backup_dir = self._install_dir.parent / "raceflag.bak"
            if backup_dir.exists():
                if self._install_dir.exists():
                    shutil.rmtree(self._install_dir)
                shutil.copytree(backup_dir, self._install_dir)
            return False
