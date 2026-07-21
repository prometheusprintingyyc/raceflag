from __future__ import annotations
import asyncio
from dataclasses import asdict
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, Response
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

_LOGS_UNAVAILABLE = (
    "journalctl not available — unit may be running in Docker or a non-systemd environment."
)


async def _fetch_logs() -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "journalctl", "-u", "raceflag", "-n", "150", "--no-pager",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0 and stdout:
            return stdout.decode("utf-8", errors="replace")
        return _LOGS_UNAVAILABLE
    except FileNotFoundError:
        return _LOGS_UNAVAILABLE


class DelayRequest(BaseModel):
    seconds: float = Field(ge=0.0, le=90.0)


class TestEffectRequest(BaseModel):
    flag_state: str


class WiFiRequest(BaseModel):
    ssid: str
    password: str


class DemoModeRequest(BaseModel):
    enabled: bool


class LEDEnabledRequest(BaseModel):
    enabled: bool


class LoadSessionRequest(BaseModel):
    session_path: str
    session_name: str = ""


class ReplayOffsetRequest(BaseModel):
    seconds: float = Field(ge=-30.0, le=30.0)


def create_app(
    state: AppState,
    config: Config,
    led: LEDController,
    config_path=None,
    wifi_manager=None,
    ota=None,
    version: str = "",
    replay_manager=None,
    listener=None,
    on_replay_event=None,
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
            led.force_trigger(req.flag_state)
        return {"triggered": req.flag_state}

    @app.get("/api/led-state")
    async def get_led_state():
        pixels = led.get_pixel_state()
        if pixels is None:
            return {"available": False, "pixels": [], "segment_breaks": []}
        return {
            "available": True,
            "pixels": [[r, g, b] for r, g, b in pixels],
            "segment_breaks": led.get_segment_breaks(),
        }

    @app.post("/api/config/demo-mode")
    async def set_demo_mode(req: DemoModeRequest):
        state.set_demo_mode(req.enabled)
        return {"demo_mode": req.enabled}

    @app.post("/api/led/enabled")
    async def set_led_enabled(req: LEDEnabledRequest):
        state.set_led_enabled(req.enabled)
        led.set_led_enabled(req.enabled)
        return {"led_enabled": req.enabled}

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
            return {"connected": False, "ssid": "", "hotspot_active": False}
        return {
            "connected": wifi_manager.is_connected(),
            "ssid": wifi_manager.get_ssid(),
            "hotspot_active": wifi_manager.is_hotspot_active(),
        }

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

    @app.post("/api/shutdown")
    async def shutdown():
        async def _deferred_shutdown() -> None:
            await asyncio.sleep(1)
            proc = await asyncio.create_subprocess_exec(
                "shutdown", "-h", "now",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        asyncio.create_task(_deferred_shutdown())
        return {"ok": True}

    @app.get("/api/logs")
    async def get_logs():
        from datetime import datetime
        lines = await _fetch_logs()
        return {"lines": lines, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")}

    if replay_manager is not None:
        @app.get("/api/replay/sessions")
        async def get_replay_sessions():
            import datetime
            year = datetime.datetime.now().year
            return await replay_manager.get_sessions(year=year)

        @app.post("/api/replay/load")
        async def load_replay_session(req: LoadSessionRequest):
            state.set_replay_state(mode=True, status="loading",
                                   session_name=req.session_name)
            event_count = await replay_manager.load_session(
                req.session_path, session_name=req.session_name
            )
            state.set_replay_state(mode=True, status="ready",
                                   session_name=req.session_name)
            return {"status": "ready", "session_name": req.session_name,
                    "event_count": event_count}

        @app.post("/api/replay/play")
        async def play_replay():
            if listener is not None:
                listener.suspended = True
            state.set_replay_state(mode=True, status="playing",
                                   session_name=replay_manager._session_name)
            await replay_manager.play(on_event=on_replay_event or state.set_track_status)
            return {"status": "playing"}

        @app.post("/api/replay/pause")
        async def pause_replay():
            replay_manager.pause()
            state.set_replay_state(mode=True, status="paused",
                                   session_name=replay_manager._session_name)
            return {"status": "paused"}

        @app.post("/api/replay/resume")
        async def resume_replay():
            replay_manager.resume()
            state.set_replay_state(mode=True, status="playing",
                                   session_name=replay_manager._session_name)
            return {"status": "playing"}

        @app.post("/api/replay/stop")
        async def stop_replay():
            replay_manager.stop()
            if listener is not None:
                listener.suspended = False
            state.set_replay_state(mode=False, status="idle")
            return {"status": "idle"}

        @app.post("/api/replay/offset")
        async def set_replay_offset(req: ReplayOffsetRequest):
            replay_manager.set_sync_offset(req.seconds)
            return {"offset_seconds": req.seconds}

    @app.get("/", include_in_schema=False)
    async def index():
        if wifi_manager and wifi_manager.is_hotspot_active():
            return RedirectResponse(url="/setup")
        html = (FRONTEND_DIR / "index.html").read_text()
        if version:
            html = html.replace('href="/style.css"', f'href="/style.css?v={version}"')
            html = html.replace('src="/app.js"', f'src="/app.js?v={version}"')
        return Response(content=html, media_type="text/html",
                        headers={"Cache-Control": "no-cache"})

    @app.get("/setup", include_in_schema=False)
    async def setup_page():
        return FileResponse(FRONTEND_DIR / "setup.html")

    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    return app
