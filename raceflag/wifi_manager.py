from __future__ import annotations
import asyncio
import logging
from pathlib import Path

from raceflag.config import Config, save as save_config

logger = logging.getLogger(__name__)

HOTSPOT_SSID = "RaceFlag-Setup"
HOTSPOT_IP = "192.168.4.1"

HOSTAPD_CONF = "/etc/hostapd/hostapd.conf"
DNSMASQ_CONF = "/etc/dnsmasq.d/raceflag.conf"

HOSTAPD_CONF_CONTENT = f"""interface=wlan0
driver=nl80211
ssid={HOTSPOT_SSID}
hw_mode=g
channel=7
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
"""

DNSMASQ_CONF_CONTENT = f"""interface=wlan0
dhcp-range=192.168.4.2,192.168.4.20,255.255.255.0,24h
address=/#/{HOTSPOT_IP}
"""

CONNECT_TIMEOUT = 30
CONNECT_FAIL_THRESHOLD = 2
RECONNECT_FAIL_THRESHOLD = 10
MAX_HOTSPOT_ATTEMPTS = 3


class WiFiManager:
    def __init__(self, config: Config, config_path: Path | None = None, on_hotspot_change=None):
        self._config = config
        self._config_path = config_path
        self._on_hotspot_change = on_hotspot_change
        self._connected = False
        self._current_ssid = ""
        self._hotspot_active = False
        self._running = False
        self._task: asyncio.Task | None = None
        self._ever_connected = False
        self._hotspot_attempt_count = 0

    def is_connected(self) -> bool:
        return self._connected

    def get_ssid(self) -> str:
        return self._current_ssid

    def is_hotspot_active(self) -> bool:
        return self._hotspot_active

    async def _sync_nm_wifi_to_config(self) -> bool:
        """Read the active wlan0 WiFi profile from NM and save SSID+password to config.json.

        Returns True if credentials were found and saved. This makes config self-healing:
        after one restart the normal configured-SSID path takes over without needing
        any detection logic.
        """
        try:
            # Step 1: get the active connection profile name for wlan0
            proc = await asyncio.create_subprocess_exec(
                "nmcli", "-g", "GENERAL.CONNECTION", "device", "show", "wlan0",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            profile = stdout.decode().strip()
            if not profile or profile == "--":
                return False

            # Step 2: get the actual SSID from that profile
            proc = await asyncio.create_subprocess_exec(
                "nmcli", "-g", "802-11-wireless.ssid", "connection", "show", profile,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            ssid = stdout.decode().strip()
            if not ssid:
                return False

            # Step 3: get the password (may be empty for open networks)
            proc = await asyncio.create_subprocess_exec(
                "nmcli", "-g", "802-11-wireless-security.psk", "connection", "show", profile,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            password = stdout.decode().strip()

            self._config.wifi_ssid = ssid
            self._config.wifi_password = password
            self._current_ssid = ssid
            if self._config_path:
                save_config(self._config, self._config_path)
            logger.info("Synced WiFi credentials from NM profile %r: ssid=%r", profile, ssid)
            return True
        except Exception as e:
            logger.warning("Failed to sync NM credentials to config: %s", e)
            return False

    async def _has_network_address(self) -> bool:
        """Return True if any interface has a routable non-hotspot IP."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ip", "-4", "addr",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            for line in stdout.decode().splitlines():
                line = line.strip()
                if not line.startswith("inet "):
                    continue
                ip = line.split()[1].split("/")[0]
                if (not ip.startswith("127.")
                        and not ip.startswith("169.254.")
                        and ip != HOTSPOT_IP):
                    return True
        except Exception:
            pass
        return False

    async def start(self) -> None:
        logger.info("WiFiManager starting (configured_ssid=%r)", self._config.wifi_ssid or "")
        self._running = True

        if not self._config.wifi_ssid:
            # Before deciding to hotspot, ask NM if wlan0 already has an active connection.
            # This handles devices where NM has cached credentials but config.json is empty
            # (first boot at a new location, or config was reset while NM kept credentials).
            if await self._sync_nm_wifi_to_config():
                self._connected = True
                self._ever_connected = True
                logger.info("Adopted existing NM connection — skipping hotspot")

        if self._connected:
            pass  # Already connected via NM adoption above
        elif self._config.wifi_ssid:
            success = await self._connect_to_configured()
            if not success:
                await self.enable_hotspot()
        else:
            # No SSID in config and NM has no WiFi profile.
            # Still skip hotspot if a non-WiFi routable IP exists (e.g. Ethernet).
            if await self._has_network_address():
                self._connected = True
                self._ever_connected = True
                logger.info("No WiFi config but routable IP found — skipping hotspot")
            else:
                await self.enable_hotspot()

        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _monitor_loop(self) -> None:
        fail_count = 0
        while self._running:
            if self._hotspot_active:
                if not self._config.wifi_ssid and await self._has_network_address():
                    # No configured SSID but a routable IP appeared — NM reconnected.
                    logger.info("Network address appeared while in hotspot — disabling hotspot")
                    await self.disable_hotspot()
                    self._connected = True
                    self._ever_connected = True
                    await self._sync_nm_wifi_to_config()
                elif await self._check_configured_available():
                    await self.disable_hotspot()
                    success = await self._connect_to_configured()
                    if not success:
                        self._hotspot_attempt_count += 1
                        await self.enable_hotspot()
                        if self._hotspot_attempt_count >= MAX_HOTSPOT_ATTEMPTS:
                            logger.warning(
                                "WiFi connect failed %d times — clearing credentials",
                                self._hotspot_attempt_count,
                            )
                            self._config.wifi_ssid = ""
                            self._config.wifi_password = ""
                            if self._config_path:
                                save_config(self._config, self._config_path)
                            self._hotspot_attempt_count = 0
                    else:
                        self._hotspot_attempt_count = 0
                await asyncio.sleep(120)
            else:
                ok = await self._check_connectivity()
                if ok:
                    fail_count = 0
                    self._connected = True
                    self._ever_connected = True
                else:
                    fail_count += 1
                    self._connected = False
                    threshold = RECONNECT_FAIL_THRESHOLD if self._ever_connected else CONNECT_FAIL_THRESHOLD
                    if fail_count >= threshold:
                        logger.warning("WiFi unreachable — starting hotspot")
                        await self.enable_hotspot()
                        fail_count = 0
                await asyncio.sleep(30)

    async def _check_connectivity(self) -> bool:
        """Return True if a routable non-hotspot IP is present on any interface.

        Uses IP address detection instead of ICMP ping so corporate firewalls
        that block outbound ping don't cause false connectivity failures.
        """
        return await self._has_network_address()

    async def _check_configured_available(self) -> bool:
        if not self._config.wifi_ssid:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "iwlist", "wlan0", "scan",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            return self._config.wifi_ssid.encode() in stdout
        except Exception:
            return False

    async def _connect_to_configured(self) -> bool:
        ssid = self._config.wifi_ssid
        password = self._config.wifi_password
        if not ssid:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "nmcli", "device", "wifi", "connect", ssid, "password", password,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=CONNECT_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning("WiFi connect timed out after %ds", CONNECT_TIMEOUT)
                return False
            self._connected = proc.returncode == 0
            self._current_ssid = ssid if self._connected else ""
            return self._connected
        except Exception as e:
            logger.error("WiFi connect failed: %s", e)
            return False

    async def enable_hotspot(self) -> None:
        self._hotspot_active = True
        self._connected = False
        self._current_ssid = ""
        if self._on_hotspot_change:
            self._on_hotspot_change(True)
        try:
            Path(HOSTAPD_CONF).write_text(HOSTAPD_CONF_CONTENT)
            Path(DNSMASQ_CONF).write_text(DNSMASQ_CONF_CONTENT)
            for cmd in [
                ["ip", "addr", "add", f"{HOTSPOT_IP}/24", "dev", "wlan0"],
                ["systemctl", "restart", "hostapd"],
                ["systemctl", "restart", "dnsmasq"],
            ]:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
        except Exception as e:
            logger.error("Failed to enable hotspot: %s", e)

    async def disable_hotspot(self) -> None:
        self._hotspot_active = False
        if self._on_hotspot_change:
            self._on_hotspot_change(False)
        try:
            for cmd in [
                ["systemctl", "stop", "hostapd"],
                ["systemctl", "stop", "dnsmasq"],
                ["ip", "addr", "del", f"{HOTSPOT_IP}/24", "dev", "wlan0"],
            ]:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
        except Exception as e:
            logger.error("Failed to disable hotspot: %s", e)

    async def scan(self) -> list[str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "nmcli", "-t", "-f", "SSID", "device", "wifi", "list",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            return [s.strip() for s in stdout.decode().splitlines() if s.strip()]
        except Exception:
            return []

    async def connect(self, ssid: str, password: str) -> None:
        self._config.wifi_ssid = ssid
        self._config.wifi_password = password
        if self._config_path:
            save_config(self._config, self._config_path)
        self._hotspot_attempt_count = 0
        await self.disable_hotspot()
        success = await self._connect_to_configured()
        if not success:
            await self.enable_hotspot()
