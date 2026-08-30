from __future__ import annotations
import asyncio
import logging
import shutil
import tarfile
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"

# /boot/firmware/raceflag/ is on the FAT32 boot partition — always writable even when
# overlayroot makes the root filesystem read-only.
BOOT_DIR = Path("/boot/firmware/raceflag")
_OVERLAYROOT_CONF = Path("/etc/overlayroot.local.conf")
_NM_CONN_SRC = Path("/etc/NetworkManager/system-connections")


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

    @staticmethod
    async def _is_overlayroot_active() -> bool:
        """Return True if the root filesystem is currently an overlayroot tmpfs."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "grep", "-q", "overlayroot", "/proc/mounts",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception:
            return False

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
        overlayroot_active = await self._is_overlayroot_active()

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

            # Ensure the boot dir exists before writing to it (created by install.sh
            # on new installs; may not exist on units upgrading from older versions).
            BOOT_DIR.mkdir(parents=True, exist_ok=True)

            # Download to /boot/firmware/raceflag/ — writable under both normal and overlayroot
            # operation, and accessible inside overlayroot-chroot via the /boot bind-mount.
            archive_path = BOOT_DIR / f"raceflag-{tag}.tar.gz"

            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                async with client.stream("GET", asset["browser_download_url"]) as resp:
                    resp.raise_for_status()
                    with open(archive_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(8192):
                            f.write(chunk)

            if overlayroot_active:
                success = await self._apply_with_chroot(archive_path)
            else:
                success = self._apply_direct(archive_path)

            if not success:
                return False

            # Write version to /boot/firmware/raceflag/ — always writable regardless of overlayroot.
            self._version_file.write_text(tag.lstrip("v"))

            # Schedule restart. If overlayroot is not yet active, _deferred_restart will
            # set it up and issue a full reboot (30–60 s on Zero W) instead of a quick
            # service restart, so the protection is in place before the next power cycle.
            asyncio.create_task(
                self._deferred_restart(enable_overlayroot=not overlayroot_active)
            )
            return True

        except Exception as e:
            logger.error("OTA update failed: %s — rolling back", e)
            if not overlayroot_active:
                backup_dir = self._install_dir.parent / "raceflag.bak"
                if backup_dir.exists():
                    if self._install_dir.exists():
                        shutil.rmtree(self._install_dir)
                    shutil.copytree(backup_dir, self._install_dir)
            return False

    def _apply_direct(self, archive_path: Path) -> bool:
        """Apply update by writing directly to the filesystem (overlayroot not yet active)."""
        backup_dir = self._install_dir.parent / "raceflag.bak"
        staging = self._install_dir.parent / "raceflag-staging"
        try:
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
            shutil.copytree(self._install_dir, backup_dir)

            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir()
            with tarfile.open(archive_path) as tf:
                # filter="data" (Python 3.12+) blocks path traversal; fall back to manual check on 3.11
                try:
                    tf.extractall(staging, filter="data")
                except TypeError:
                    for member in tf.getmembers():
                        dest = (staging / member.name).resolve()
                        if not str(dest).startswith(str(staging.resolve())):
                            raise ValueError(f"Unsafe tar member: {member.name}")
                    tf.extractall(staging)

            shutil.rmtree(self._install_dir)
            shutil.move(str(staging), str(self._install_dir))
            archive_path.unlink(missing_ok=True)
            return True
        except Exception as e:
            logger.error("Direct apply failed: %s", e)
            if backup_dir.exists():
                if self._install_dir.exists():
                    shutil.rmtree(self._install_dir)
                shutil.copytree(backup_dir, self._install_dir)
            archive_path.unlink(missing_ok=True)
            return False

    async def _apply_with_chroot(self, archive_path: Path) -> bool:
        """Apply update via overlayroot-chroot so writes reach the real underlying filesystem."""
        install = self._install_dir
        backup = install.parent / "raceflag.bak"
        staging = install.parent / "raceflag-staging"

        # Extract to staging first (safe — doesn't touch live install until tar succeeds).
        # pip3 install runs inside the chroot so packages land on the real disk, not the overlay.
        script = (
            "set -e\n"
            f"rm -rf {staging} && mkdir -p {staging}\n"
            f"tar xzf {archive_path} -C {staging}\n"
            f"rm -rf {backup}\n"
            f"cp -r {install} {backup}\n"
            f"rm -rf {install}\n"
            f"mv {staging} {install}\n"
            f"rm -f {archive_path}\n"
            f"pip3 install -r {install}/requirements.txt"
            f" --extra-index-url https://www.piwheels.org/simple/"
            f" --break-system-packages -q 2>/dev/null || true\n"
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                "overlayroot-chroot", "/bin/bash", "-c", script,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            if proc.returncode != 0:
                logger.error("overlayroot-chroot apply failed: %s", stderr.decode())
                await self._rollback_with_chroot()
                return False
            return True
        except asyncio.TimeoutError:
            logger.error("overlayroot-chroot apply timed out after 300 s")
            return False
        except FileNotFoundError:
            logger.error("overlayroot-chroot not found — cannot apply update safely with overlayroot active")
            return False

    async def _rollback_with_chroot(self) -> None:
        backup = self._install_dir.parent / "raceflag.bak"
        script = (
            f"if [ -d {backup} ]; then "
            f"rm -rf {self._install_dir} && "
            f"cp -r {backup} {self._install_dir} && "
            f"echo 'rolled back from backup'; fi"
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                "overlayroot-chroot", "/bin/bash", "-c", script,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=60)
        except Exception as e:
            logger.error("Rollback via overlayroot-chroot failed: %s", e)

    async def _deferred_restart(self, *, enable_overlayroot: bool) -> None:
        await asyncio.sleep(2)

        if enable_overlayroot:
            # overlayroot not yet active — pip writes land on the real disk normally.
            pip = await asyncio.create_subprocess_exec(
                "pip3", "install", "-r",
                str(self._install_dir / "requirements.txt"),
                "--extra-index-url", "https://www.piwheels.org/simple/",
                "--break-system-packages",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await pip.wait()
            await self._setup_overlayroot()
            # Full reboot required to activate overlayroot for the first time.
            # Takes 30–60 s on Pi Zero W — longer than a normal service restart.
            logger.info("OTA complete — rebooting to activate overlayroot SD card protection")
            proc = await asyncio.create_subprocess_exec(
                "reboot",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        else:
            # overlayroot already active — pip ran inside the chroot in _apply_with_chroot.
            # Just restart the service to pick up the new code.
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "restart", "raceflag",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()

    async def _setup_overlayroot(self) -> None:
        """Configure overlayroot SD card protection. Idempotent — safe to call multiple times."""

        # 1. Ensure /boot/firmware/raceflag/ exists.
        BOOT_DIR.mkdir(parents=True, exist_ok=True)

        # 2. Migrate config.json and version.txt from old /opt/raceflag/ location if needed.
        for filename in ("config.json", "version.txt"):
            old_path = self._install_dir / filename
            new_path = BOOT_DIR / filename
            if old_path.exists() and not new_path.exists():
                shutil.copy(old_path, new_path)
                logger.info("Migrated %s → %s", old_path, new_path)

        # 3. Ensure NM connections directory exists and remove any legacy symlink
        #    from the previous migration approach.
        if _NM_CONN_SRC.is_symlink():
            _NM_CONN_SRC.unlink()
            logger.info("Removed legacy NM connections symlink")
        _NM_CONN_SRC.mkdir(parents=True, exist_ok=True)
        _NM_CONN_SRC.chmod(0o700)

        # 4. Update service file if it still points config/version to the old /opt/raceflag/ paths.
        #    Units installed before v0.2.26 have these hardcoded; update them so the service reads
        #    and writes to the boot partition after the next reboot.
        service_file = Path("/etc/systemd/system/raceflag.service")
        if service_file.exists():
            content = service_file.read_text()
            updated = content.replace(
                "Environment=RACEFLAG_CONFIG=/opt/raceflag/config.json",
                "Environment=RACEFLAG_CONFIG=/boot/firmware/raceflag/config.json",
            ).replace(
                "Environment=RACEFLAG_VERSION=/opt/raceflag/version.txt",
                "Environment=RACEFLAG_VERSION=/boot/firmware/raceflag/version.txt",
            )
            if updated != content:
                service_file.write_text(updated)
                proc = await asyncio.create_subprocess_exec(
                    "systemctl", "daemon-reload",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
                logger.info("Updated service file paths to boot partition")

        # 5. Move journald logs to tmpfs to reduce SD card writes.
        journald_dir = Path("/etc/systemd/journald.conf.d")
        journald_dir.mkdir(parents=True, exist_ok=True)
        journald_conf = journald_dir / "raceflag-volatile.conf"
        if not journald_conf.exists():
            journald_conf.write_text("[Journal]\nStorage=volatile\n")
            logger.info("Configured journald to use volatile (RAM) storage")

        # 6. Install and enable overlayroot (skipped if already configured).
        if _OVERLAYROOT_CONF.exists():
            logger.info("overlayroot already configured — skipping install")
            return
        try:
            logger.info("Installing overlayroot (may take a few minutes on Pi Zero W)...")
            proc = await asyncio.create_subprocess_exec(
                "apt-get", "install", "-y", "-qq", "overlayroot",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=300)
            _OVERLAYROOT_CONF.write_text('overlayroot="tmpfs"\n')
            initramfs = await asyncio.create_subprocess_exec(
                "update-initramfs", "-u",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(initramfs.communicate(), timeout=120)
            logger.info("overlayroot installed and configured — will activate on next boot")
        except asyncio.TimeoutError:
            logger.error("overlayroot install timed out — SD card protection not enabled")
        except Exception as e:
            logger.error("Failed to install overlayroot: %s — SD card protection not enabled", e)
