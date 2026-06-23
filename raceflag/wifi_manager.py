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


class WiFiManager:
    def __init__(self, config: Config, config_path: Path | None = None):
        self._config = config
        self._config_path = config_path
        self._connected = False
        self._current_ssid = ""
        self._hotspot_active = False
        self._running = False
        self._task: asyncio.Task | None = None

    def is_connected(self) -> bool:
        return self._connected

    def get_ssid(self) -> str:
        return self._current_ssid

    def is_hotspot_active(self) -> bool:
        return self._hotspot_active

    async def start(self) -> None:
        self._running = True
        if self._config.wifi_ssid:
            await self._connect_to_configured()
        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _monitor_loop(self) -> None:
        fail_count = 0
        while self._running:
            if self._hotspot_active:
                if await self._check_configured_available():
                    await self.disable_hotspot()
                    await self._connect_to_configured()
                await asyncio.sleep(120)
            else:
                ok = await self._check_connectivity()
                if ok:
                    fail_count = 0
                    self._connected = True
                else:
                    fail_count += 1
                    self._connected = False
                    if fail_count >= 2:
                        logger.warning("WiFi unreachable — starting hotspot")
                        await self.enable_hotspot()
                        fail_count = 0
                await asyncio.sleep(30)

    async def _check_connectivity(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ping", "-c", "1", "-W", "3", "8.8.8.8",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            return proc.returncode == 0
        except Exception:
            return False

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

    async def _connect_to_configured(self) -> None:
        ssid = self._config.wifi_ssid
        password = self._config.wifi_password
        if not ssid:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "nmcli", "device", "wifi", "connect", ssid, "password", password,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            self._connected = proc.returncode == 0
            self._current_ssid = ssid if self._connected else ""
        except Exception as e:
            logger.error("WiFi connect failed: %s", e)

    async def enable_hotspot(self) -> None:
        self._hotspot_active = True
        self._connected = False
        self._current_ssid = ""
        try:
            Path(HOSTAPD_CONF).write_text(HOSTAPD_CONF_CONTENT)
            Path(DNSMASQ_CONF).write_text(DNSMASQ_CONF_CONTENT)
            for cmd in [
                ["ip", "addr", "add", f"{HOTSPOT_IP}/24", "dev", "wlan0"],
                ["systemctl", "start", "hostapd"],
                ["systemctl", "start", "dnsmasq"],
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
        await self.disable_hotspot()
        await self._connect_to_configured()
