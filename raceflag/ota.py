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
from raceflag.ota_deps import DependencyError, fetch_all, fetch_missing
from raceflag.ota_safety import code_hash, tar_tree

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"

# /boot/firmware/raceflag/ is on the FAT32 boot partition — always writable even when
# overlayroot makes the root filesystem read-only. Config and version.txt live there.
BOOT_DIR = Path("/boot/firmware/raceflag")
# Update downloads: RAM, and /run is shared with overlayroot-chroot (unlike /boot/firmware).
UPDATE_DIR = Path("/run/raceflag-update")
# The real SD card's copy of the install, as overlayroot mounts it (read-only).
SAVED_INSTALL_DIR = Path("/media/root-ro/opt/raceflag")
UPDATE_DONE_MARKER = Path("/run/raceflag-update.done")
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


class OTAUpdater:
    def __init__(self, version_file: Path, install_dir: Path, github_repo: str, *,
                 work_dir: Path = UPDATE_DIR, boot_dir: Path = BOOT_DIR,
                 saved_install_dir: Path = SAVED_INSTALL_DIR,
                 done_marker: Path = UPDATE_DONE_MARKER):
        self._version_file = version_file
        self._install_dir = install_dir
        self._github_repo = github_repo
        self._work_dir = work_dir
        self._wheels_dir = work_dir / "wheels"
        self._boot_dir = boot_dir
        self._saved_install_dir = saved_install_dir
        self._done_marker = done_marker
        # One update or safety-net save at a time.
        self._lock = asyncio.Lock()

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
            if overlayroot_active:
                # The SD card changed under the mounted overlay: the safety net must stand
                # down until the reboot gives the new code a clean view.
                try:
                    self._done_marker.touch()
                except OSError as e:
                    logger.error("Could not write the update marker: %s", e)
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
            if self._done_marker.exists():
                logger.info("Update installed — waiting for the reboot")
                return "updated"
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

    def _apply_direct(self, archive_path: Path) -> bool:
        """Apply update by writing directly to the filesystem (overlayroot not yet active).

        Swaps like ota_install.sh: extract to staging, then two renames. The old install is
        never deleted or overwritten by a copy; a failed swap renames it back."""
        backup_dir = self._install_dir.parent / "raceflag.bak"
        staging = self._install_dir.parent / "raceflag-staging"
        try:
            try:
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
            except Exception as e:
                logger.error("Direct apply failed while extracting: %s", e)
                shutil.rmtree(staging, ignore_errors=True)
                return False

            try:
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
                os.rename(self._install_dir, backup_dir)
            except Exception as e:
                logger.error("Direct apply failed while making the backup: %s", e)
                shutil.rmtree(staging, ignore_errors=True)
                return False

            try:
                os.rename(staging, self._install_dir)
            except Exception as e:
                logger.error("Direct apply failed while swapping in the update: %s", e)
                try:
                    os.rename(backup_dir, self._install_dir)
                except Exception as e2:
                    logger.error("Could not put the previous install back: %s", e2)
                shutil.rmtree(staging, ignore_errors=True)
                return False
            return True
        finally:
            archive_path.unlink(missing_ok=True)

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
            # overlayroot already active — pip ran inside the chroot (ota_install.sh), which also
            # fixed the service file on the SD card. The SD card changed under the mounted
            # overlay, so only a reboot gives the new code a clean view.
            logger.info("Update installed — rebooting so the new code runs from the SD card")
            proc = await asyncio.create_subprocess_exec(
                "reboot",
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
            await asyncio.wait_for(initramfs.communicate(), timeout=300)
            logger.info("overlayroot installed and configured — will activate on next boot")
        except asyncio.TimeoutError:
            logger.error("overlayroot install timed out — SD card protection not enabled")
        except Exception as e:
            logger.error("Failed to install overlayroot: %s — SD card protection not enabled", e)
