from __future__ import annotations
import asyncio
import logging
import os
import shutil
from pathlib import Path

import uvicorn

from raceflag.config import load as load_config, save as save_config, Config, fix_led_count
from raceflag.state import AppState
from raceflag.f1_listener import F1Listener
from raceflag.flag_router import FlagRouter
from raceflag.f1_renewal import F1LoginRenewer
from raceflag.api_client import JolpicaClient
from raceflag.web_server import create_app
from raceflag.replay_manager import ReplayManager
from raceflag.wifi_manager import WiFiManager
from raceflag.ota import OTAUpdater
from raceflag.button_manager import ButtonManager
from raceflag.led_modes import lamp_from_config, schedule_from_config, session_live

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CONFIG_PATH = Path(os.environ.get("RACEFLAG_CONFIG", "/boot/firmware/raceflag/config.json"))
EFFECTS_PATH = Path(os.environ.get("RACEFLAG_EFFECTS", "/opt/raceflag/raceflag/effects/effects.json"))
VERSION_FILE = Path(os.environ.get("RACEFLAG_VERSION", "/boot/firmware/raceflag/version.txt"))
INSTALL_DIR = Path(os.environ.get("RACEFLAG_DIR", "/opt/raceflag"))
GITHUB_REPO = os.environ.get("RACEFLAG_REPO", "prometheusprintingyyc/raceflag")
DEMO_MODE = os.environ.get("DEMO_MODE", "").lower() in ("1", "true", "yes")
WIFI_ENABLED = os.environ.get("WIFI_ENABLED", "1").lower() not in ("0", "false", "no")
BUTTON_GPIO = int(os.environ.get("RACEFLAG_BUTTON_GPIO", "21"))


def _migrate_legacy_config() -> None:
    """Migrate config and version from /opt/raceflag/ to /boot/firmware/raceflag/.

    Runs on every startup. Covers three cases:
    - New install / post-OTA: copies files from old location if missing at new location.
    - New location exists but has empty wifi_ssid: overwrites with old config that has credentials.
    - No source at all: creates a default config.json so wifi setup always has somewhere to save.
    """
    boot_dir = CONFIG_PATH.parent
    try:
        boot_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning("Could not create boot config dir: %s", e)
        return

    import json as _json

    for filename in ("config.json", "version.txt"):
        new_path = boot_dir / filename
        old_path = INSTALL_DIR / filename
        if not old_path.exists():
            continue
        should_migrate = not new_path.exists()
        if not should_migrate and filename == "config.json":
            try:
                if not _json.loads(new_path.read_text()).get("wifi_ssid"):
                    should_migrate = True  # new config exists but has no credentials
            except Exception:
                should_migrate = True  # new config is unreadable — overwrite it
        if should_migrate:
            try:
                shutil.copy(old_path, new_path)
                logger.info("Migrated %s → %s", old_path, new_path)
            except Exception as e:
                logger.warning("Failed to migrate %s: %s", filename, e)

    # Always ensure config.json exists — wifi setup writes credentials here.
    # If migration had nothing to copy, create a default so save() has a target.
    if not CONFIG_PATH.exists():
        try:
            save_config(Config(), CONFIG_PATH)
            logger.info("Created default config at %s", CONFIG_PATH)
        except Exception as e:
            logger.warning("Could not create default config: %s", e)


def _make_strip(config):
    try:
        from rpi_ws281x import PixelStrip, Color

        class RpiStrip:
            def __init__(self):
                self._strip = PixelStrip(config.led_count, config.led_gpio_pin, brightness=config.led_brightness)
                self._Color = Color

            def begin(self): self._strip.begin()
            def set_pixel(self, n, r, g, b): self._strip.setPixelColor(n, self._Color(r, g, b))
            def show(self): self._strip.show()
            def num_pixels(self): return self._strip.numPixels()
            def get_pixels(self):
                colors = (self._strip.getPixelColor(i) for i in range(self._strip.numPixels()))
                return [((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF) for c in colors]
            def fill(self, r, g, b):
                for i in range(self._strip.numPixels()):
                    self._strip.setPixelColor(i, self._Color(r, g, b))
                self._strip.show()

        strip = RpiStrip()
        strip.begin()
        return strip
    except (ImportError, RuntimeError) as e:
        logger.warning("rpi_ws281x unavailable (%s) — using mock strip", e)
        from raceflag.led_controller import MockStrip
        return MockStrip(config.led_count)


async def _ensure_overlayroot(ota: OTAUpdater) -> None:
    """Set up overlayroot SD card protection if not yet configured.

    Runs once per boot as a background task. Handles units that received new
    code via the old v0.2.21 OTA path, which didn't call _setup_overlayroot.
    Does nothing on units where overlayroot is already active or configured.
    """
    # Already active — nothing to do.
    try:
        proc = await asyncio.create_subprocess_exec(
            "grep", "-q", "overlayroot", "/proc/mounts",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if proc.returncode == 0:
            logger.info("overlayroot active — SD card protection OK")
            return
    except Exception:
        pass

    # Already configured (will activate on next reboot) — nothing to do.
    if Path("/etc/overlayroot.local.conf").exists():
        logger.info("overlayroot configured — SD card protection active on next reboot")
        return

    # Not configured. Wait for WiFi to be ready (apt-get needs network).
    logger.info("overlayroot not configured — will set up SD card protection in 60 s")
    await asyncio.sleep(60)

    await ota._setup_overlayroot()

    logger.info("overlayroot setup complete — rebooting to activate SD card protection")
    proc = await asyncio.create_subprocess_exec(
        "reboot",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


async def _refresh_standings_loop(client: JolpicaClient, state: AppState) -> None:
    while True:
        try:
            drivers = await client.fetch_driver_standings()
            constructors = await client.fetch_constructor_standings()
            next_race = await client.fetch_next_race()
        except Exception as e:
            logger.warning("Standings refresh failed: %s", e)
            await asyncio.sleep(60)
            continue
        if drivers or constructors:
            state.set_standings(drivers, constructors)
            state.set_next_race(next_race)
        # Retry in 60 s if either standings fetch returned empty (e.g. network
        # not ready at startup); only wait the full 4-hour cycle when both succeeded.
        if drivers and constructors:
            await asyncio.sleep(4 * 3600)
        else:
            await asyncio.sleep(60)


async def main() -> None:
    _migrate_legacy_config()
    config = load_config(CONFIG_PATH)
    if fix_led_count(config):
        logger.info("led_count was the old installer's 60; corrected to %d", config.led_count)
        try:
            save_config(config, CONFIG_PATH)
        except OSError as e:
            logger.warning("Could not save corrected led_count: %s", e)
    state = AppState()
    state.set_demo_mode(DEMO_MODE)

    strip = _make_strip(config)
    from raceflag.led_controller import LEDController
    led = LEDController(
        strip=strip,
        effects_path=EFFECTS_PATH,
        delay_seconds=config.delay_seconds,
        is_session_live=lambda: session_live(
            state.session.is_active, state.replay_mode, state.track_status),
    )
    # Mode always boots as "on"; schedule, lamp and timezone come from config.json.
    # Invalid stored values fall back to defaults so a bad file can't stop the unit booting.
    led.set_schedule(*schedule_from_config(config.schedule_on, config.schedule_off))
    led.set_lamp(*lamp_from_config(config.lamp_color, config.lamp_brightness))
    led.set_lamp_use_schedule(config.lamp_use_schedule is True)
    led.set_timezone(config.timezone)
    led.start()

    wifi = WiFiManager(config=config, config_path=CONFIG_PATH, on_hotspot_change=led.set_hotspot_mode)
    button = ButtonManager(gpio_pin=BUTTON_GPIO, wifi_manager=wifi, led=led)
    ota = OTAUpdater(version_file=VERSION_FILE, install_dir=INSTALL_DIR, github_repo=GITHUB_REPO)

    jolpica = JolpicaClient()

    flag_router = FlagRouter(state=state, led=led, config=config)

    listener = F1Listener(state=state, on_track_status_change=flag_router.on_flag_change)
    replay = ReplayManager(on_feed=listener.process_replay_event, get_token=lambda: config.f1_token)
    f1_renewer = F1LoginRenewer(config, CONFIG_PATH)

    current_version = VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else ""
    app = create_app(
        state=state,
        config=config,
        led=led,
        config_path=CONFIG_PATH,
        wifi_manager=wifi,
        ota=ota,
        version=current_version,
        replay_manager=replay,
        listener=listener,
        on_replay_event=flag_router.on_flag_change,
        flag_router=flag_router,
        f1_renewer=f1_renewer,
    )

    server_config = uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="warning")
    server = uvicorn.Server(server_config)

    tasks = [_refresh_standings_loop(jolpica, state), listener.start(), server.serve(), button.start(), _ensure_overlayroot(ota), f1_renewer.run()]
    if WIFI_ENABLED:
        tasks.append(wifi.start())
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
