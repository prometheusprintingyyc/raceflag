from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from raceflag.config import Config, save as save_config
from raceflag.led_controller import LEDController
from raceflag.state import AppState

VALID_FLAG_STATES = {
    "track_clear", "yellow_flag", "safety_car", "virtual_sc", "red_flag", "checkered",
}

# These use trigger_timed rather than trigger so the animated version fires
_TIMED_TEST_EFFECTS: dict[str, float] = {
    "track_clear": 30.0,
    "checkered": 30.0,
}

FRONTEND_DIR = Path(__file__).parent / "frontend"


class DelayRequest(BaseModel):
    seconds: float = Field(ge=0.0, le=60.0)


class TestEffectRequest(BaseModel):
    flag_state: str


class WiFiRequest(BaseModel):
    ssid: str
    password: str


class DemoModeRequest(BaseModel):
    enabled: bool


def create_app(
    state: AppState,
    config: Config,
    led: LEDController,
    config_path=None,
    wifi_manager=None,
    ota=None,
    version: str = "",
) -> FastAPI:
    app = FastAPI(title="RaceFlag")

    @app.get("/api/state")
    async def get_state():
        return state.to_dict()

    @app.get("/api/config")
    async def get_config():
        return asdict(config)

    @app.post("/api/config/delay")
    async def set_delay(req: DelayRequest):
        led.set_delay(req.seconds)
        config.delay_seconds = req.seconds
        if config_path:
            save_config(config, config_path)
        return {"delay_seconds": req.seconds}

    @app.post("/api/test-effect")
    async def test_effect(req: TestEffectRequest):
        if req.flag_state not in VALID_FLAG_STATES:
            raise HTTPException(status_code=422, detail=f"flag_state must be one of {VALID_FLAG_STATES}")
        if req.flag_state in _TIMED_TEST_EFFECTS:
            led.trigger_timed(req.flag_state, _TIMED_TEST_EFFECTS[req.flag_state])
        else:
            led.trigger(req.flag_state)
        return {"triggered": req.flag_state}

    @app.get("/api/led-state")
    async def get_led_state():
        pixels = led.get_pixel_state()
        if pixels is None:
            return {"available": False, "pixels": []}
        return {"available": True, "pixels": [[r, g, b] for r, g, b in pixels]}

    @app.post("/api/config/demo-mode")
    async def set_demo_mode(req: DemoModeRequest):
        state.set_demo_mode(req.enabled)
        return {"demo_mode": req.enabled}

    @app.post("/api/test-idle")
    async def test_idle():
        led.set_idle(True)
        return {"triggered": "idle"}

    @app.post("/api/test-race-start")
    async def test_race_start():
        led.trigger_timed("race_start", 30.0)
        return {"triggered": "race_start"}

    @app.get("/api/update/check")
    async def check_update():
        if ota is None:
            return {"current": "unknown", "latest": None, "update_available": False}
        return await ota.check()

    @app.post("/api/update/apply")
    async def apply_update():
        if ota is None:
            raise HTTPException(503, "OTA not available")
        success = await ota.apply()
        return {"success": success}

    @app.get("/api/wifi/status")
    async def wifi_status():
        if wifi_manager is None:
            return {"connected": False, "ssid": ""}
        return {"connected": wifi_manager.is_connected(), "ssid": wifi_manager.get_ssid()}

    @app.get("/api/wifi/scan")
    async def wifi_scan():
        if wifi_manager is None:
            return {"networks": []}
        return {"networks": await wifi_manager.scan()}

    @app.post("/api/wifi/connect")
    async def wifi_connect(req: WiFiRequest):
        if wifi_manager is None:
            raise HTTPException(503, "WiFi manager not available")
        await wifi_manager.connect(req.ssid, req.password)
        return {"ok": True}

    @app.get("/", include_in_schema=False)
    async def index():
        html = (FRONTEND_DIR / "index.html").read_text()
        if version:
            html = html.replace('href="/style.css"', f'href="/style.css?v={version}"')
            html = html.replace('src="/app.js"', f'src="/app.js?v={version}"')
        return Response(content=html, media_type="text/html",
                        headers={"Cache-Control": "no-cache"})

    @app.get("/setup", include_in_schema=False)
    async def setup_page():
        return FileResponse(FRONTEND_DIR / "index.html")

    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    return app
