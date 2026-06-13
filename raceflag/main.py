from __future__ import annotations
import asyncio
import logging
import os
from pathlib import Path

import uvicorn

from raceflag.config import load as load_config
from raceflag.state import AppState
from raceflag.f1_listener import F1Listener
from raceflag.api_client import JolpicaClient
from raceflag.web_server import create_app
from raceflag.wifi_manager import WiFiManager
from raceflag.ota import OTAUpdater

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CONFIG_PATH = Path(os.environ.get("RACEFLAG_CONFIG", "/opt/raceflag/config.json"))
EFFECTS_PATH = Path(os.environ.get("RACEFLAG_EFFECTS", "/opt/raceflag/raceflag/effects/effects.json"))
VERSION_FILE = Path(os.environ.get("RACEFLAG_VERSION", "/opt/raceflag/version.txt"))
INSTALL_DIR = Path(os.environ.get("RACEFLAG_DIR", "/opt/raceflag"))
GITHUB_REPO = os.environ.get("RACEFLAG_REPO", "prometheusprintingyyc/raceflag")
DEMO_MODE = os.environ.get("DEMO_MODE", "").lower() in ("1", "true", "yes")


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


async def _refresh_standings_loop(client: JolpicaClient, state: AppState) -> None:
    while True:
        try:
            drivers = await client.fetch_driver_standings()
            constructors = await client.fetch_constructor_standings()
            state.set_standings(drivers, constructors)
            next_race = await client.fetch_next_race()
            state.set_next_race(next_race)
        except Exception as e:
            logger.warning("Standings refresh failed: %s", e)
        await asyncio.sleep(4 * 3600)


async def main() -> None:
    config = load_config(CONFIG_PATH)
    state = AppState()
    state.set_demo_mode(DEMO_MODE)

    strip = _make_strip(config)
    from raceflag.led_controller import LEDController
    led = LEDController(strip=strip, effects_path=EFFECTS_PATH, delay_seconds=config.delay_seconds)
    led.start()

    wifi = WiFiManager(config=config, config_path=CONFIG_PATH)
    ota = OTAUpdater(version_file=VERSION_FILE, install_dir=INSTALL_DIR, github_repo=GITHUB_REPO)

    jolpica = JolpicaClient()

    _IDLE_STATUSES = {"unknown", "break", "finished"}
    _LED_IDLE_STATUSES = {"unknown", "break"}
    _TIMED_EFFECTS = {"track_clear": 30.0, "race_start": 30.0, "checkered": 30.0}
    _RACE_SESSION_TYPES = {"race", "sprint"}
    _race_started = False

    def on_flag_change(status: str) -> None:
        nonlocal _race_started
        delay = config.delay_seconds

        # Reset when a session ends so the next race triggers the animation again
        if status in _IDLE_STATUSES:
            _race_started = False

        # Promote the first track_clear in a Race/Sprint session to race_start
        effective = status
        if (status == "track_clear"
                and not _race_started
                and state.session.is_active
                and state.session.session_type.lower() in _RACE_SESSION_TYPES):
            _race_started = True
            effective = "race_start"

        if effective not in _IDLE_STATUSES and effective not in _TIMED_EFFECTS:
            led.trigger(effective)

        if delay <= 0:
            state.set_display_track_status(status)
            if status in _LED_IDLE_STATUSES:
                led.set_idle(True)
            elif effective in _TIMED_EFFECTS:
                led.trigger_timed(effective, _TIMED_EFFECTS[effective])
        else:
            async def _delayed_ui(s: str = status, e: str = effective, d: float = delay) -> None:
                await asyncio.sleep(d)
                state.set_display_track_status(s)
                if s in _LED_IDLE_STATUSES:
                    led.set_idle(True)
                elif e in _TIMED_EFFECTS:
                    led.trigger_timed(e, _TIMED_EFFECTS[e])
            asyncio.ensure_future(_delayed_ui())

    listener = F1Listener(state=state, on_track_status_change=on_flag_change)

    current_version = VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else ""
    app = create_app(state=state, config=config, led=led, config_path=CONFIG_PATH,
                     wifi_manager=wifi, ota=ota, version=current_version)

    server_config = uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="warning")
    server = uvicorn.Server(server_config)

    await asyncio.gather(
        wifi.start(),
        _refresh_standings_loop(jolpica, state),
        listener.start(),
        server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
