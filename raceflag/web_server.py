from __future__ import annotations
import asyncio
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from raceflag import clock as clock_module
from raceflag.delay_sync import SyncError, is_live_race, sync_delay
from raceflag.config import Config, save as save_config
from raceflag.f1_account import LoginError, account_status, parse_login
from raceflag.replay_manager import F1LoginRequired
from raceflag.led_controller import LEDController
from raceflag.led_modes import MODES, is_hex_color, parse_hhmm
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


class LEDModeRequest(BaseModel):
    mode: str


class LampRequest(BaseModel):
    color: str
    brightness: int = Field(ge=0, le=100)
    use_schedule: Optional[bool] = None  # /api/config/lamp only; omitted = keep the saved value


class ScheduleRequest(BaseModel):
    on: str
    off: str


class LoadSessionRequest(BaseModel):
    session_path: str
    session_name: str = ""


class ReplayOffsetRequest(BaseModel):
    seconds: float = Field(ge=-30.0, le=30.0)


class TimeSyncRequest(BaseModel):
    timezone: str
    epoch_ms: int


class F1LoginRequest(BaseModel):
    login: str = Field(max_length=20000)  # login-session cookie or token


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
    flag_router=None,
    f1_renewer=None,
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    app = FastAPI(title="RaceFlag")

    @app.get("/api/state")
    async def get_state():
        return state.to_dict()

    @app.get("/api/config")
    async def get_config():
        data = asdict(config)
        data.pop("f1_token", None)  # a login credential: never sent back to the page
        return data

    # F1 account (feature 017). The token's expiry is wall-clock epoch time.
    def _f1_status(token: str) -> dict:
        rejected = bool(f1_renewer and f1_renewer.rejected)
        return account_status(token, time.time(), rejected=rejected)

    @app.get("/api/f1/account")
    async def get_f1_account():
        return _f1_status(config.f1_token)

    @app.post("/api/f1/account")
    async def set_f1_account(req: F1LoginRequest):
        try:
            token = parse_login(req.login, time.time())
        except LoginError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        config.f1_token = token
        if config_path:
            save_config(config, config_path)
        if f1_renewer is not None:
            f1_renewer.login_saved()
        return _f1_status(token)

    @app.delete("/api/f1/account")
    async def clear_f1_account():
        config.f1_token = ""
        if config_path:
            save_config(config, config_path)
        if f1_renewer is not None:
            f1_renewer.login_saved()
        return _f1_status("")

    @app.post("/api/config/delay")
    async def set_delay(req: DelayRequest):
        led.set_delay(req.seconds)
        config.delay_seconds = req.seconds
        if config_path:
            save_config(config, config_path)
        return {"delay_seconds": req.seconds}

    @app.post("/api/delay/sync")
    async def delay_sync():
        # Timestamp first: the press is "now", before any work below. Same clock as the
        # listener's lights-out time (time.monotonic) in production.
        now = clock()
        session = state.session
        try:
            delay = sync_delay(
                now,
                state.lights_out_at,
                is_live_race(session.session_type, session.is_active, state.replay_mode),
            )
        except SyncError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        config.delay_seconds = delay
        # Apply first, so an SD write error still applies the delay and plays the start.
        if flag_router is not None:
            flag_router.sync(delay)
        else:
            led.set_delay(delay)
        if config_path:
            save_config(config, config_path)
        return {"delay_seconds": delay}

    @app.post("/api/time/sync")
    async def time_sync(req: TimeSyncRequest):
        try:
            result = clock_module.sync(req.timezone, req.epoch_ms, config, config_path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        led.set_timezone(config.timezone)
        return result

    @app.get("/api/time")
    async def get_time():
        minutes = clock_module.local_minutes(config.timezone)
        return {
            "timezone": clock_module.effective_timezone(config.timezone),
            "local_time": f"{minutes // 60:02d}:{minutes % 60:02d}",
        }

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

    @app.post("/api/led/mode")
    async def set_led_mode(req: LEDModeRequest):
        if req.mode not in MODES:
            raise HTTPException(status_code=422, detail=f"mode must be one of {list(MODES)}")
        led.end_preview()
        led.set_mode(req.mode)
        state.set_led_mode(req.mode)
        return {"led_mode": req.mode}

    def _lamp_color(req: LampRequest) -> str:
        if not is_hex_color(req.color):
            raise HTTPException(status_code=422, detail="color must be #RRGGBB")
        return req.color.upper()

    @app.post("/api/led/lamp/preview")
    async def start_lamp_preview(req: LampRequest):
        led.start_preview(_lamp_color(req), req.brightness)
        return {"preview": True}

    @app.delete("/api/led/lamp/preview")
    async def end_lamp_preview():
        led.end_preview()
        return {"preview": False}

    @app.post("/api/config/lamp")
    async def set_lamp(req: LampRequest):
        color = _lamp_color(req)
        config.lamp_color = color
        config.lamp_brightness = req.brightness
        if req.use_schedule is not None:
            config.lamp_use_schedule = req.use_schedule
        if config_path:
            save_config(config, config_path)
        led.set_lamp(color, req.brightness)
        led.set_lamp_use_schedule(config.lamp_use_schedule)
        led.end_preview()
        return {
            "lamp_color": color,
            "lamp_brightness": req.brightness,
            "lamp_use_schedule": config.lamp_use_schedule,
        }

    @app.post("/api/config/schedule")
    async def set_schedule(req: ScheduleRequest):
        try:
            on_min, off_min = parse_hhmm(req.on), parse_hhmm(req.off)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if on_min == off_min:
            raise HTTPException(status_code=422, detail="On and off times must be different")
        config.schedule_on = req.on
        config.schedule_off = req.off
        if config_path:
            save_config(config, config_path)
        led.set_schedule(on_min, off_min)
        return {"schedule_on": req.on, "schedule_off": req.off}

    @app.post("/api/test-idle")
    async def test_idle():
        led.force_idle()
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
        _pre_pause_extrapolating: bool = False

        @app.get("/api/replay/sessions")
        async def get_replay_sessions():
            import datetime
            year = datetime.datetime.now().year
            try:
                return await replay_manager.get_sessions(year=year)
            except F1LoginRequired as exc:
                raise HTTPException(status_code=401, detail=str(exc))

        # Outcome of the last load, for GET /api/replay/progress. Cleared by a new load or Stop.
        _load_result: dict = {"event_count": 0, "detail": None, "login_needed": False}

        def _clear_load_result() -> None:
            _load_result.update(event_count=0, detail=None, login_needed=False)

        def _resume_live_feed() -> None:
            if listener is not None:
                listener.suspended = False

        @app.post("/api/replay/load")
        async def load_replay_session(req: LoadSessionRequest):
            _clear_load_result()
            if listener is not None:
                # Pause the live feed from the start of loading (as on the ESP32) so a live
                # message can't overwrite the loaded race before Play; start from empty timing.
                listener.suspended = True
                listener.reset_timing_state()
            state.set_replay_state(mode=True, status="loading",
                                   session_name=req.session_name)
            try:
                event_count = await replay_manager.load_session(
                    req.session_path, session_name=req.session_name
                )
            except F1LoginRequired as exc:
                _resume_live_feed()
                state.set_replay_state(mode=False, status="idle")
                _load_result.update(detail=str(exc), login_needed=True)
                raise HTTPException(status_code=401, detail=str(exc))
            except Exception as exc:
                _resume_live_feed()
                state.set_replay_state(mode=False, status="idle")
                detail = f"Failed to load session: {exc}"
                _load_result.update(detail=detail)
                raise HTTPException(status_code=502, detail=detail)
            _load_result.update(event_count=event_count)
            state.set_replay_state(mode=True, status="ready",
                                   session_name=req.session_name)
            return {"status": "ready", "session_name": req.session_name,
                    "event_count": event_count}

        @app.get("/api/replay/progress")
        async def get_replay_progress():
            # Streams download in parallel, so there is no single stream in progress.
            status = state.replay_status
            loaded = status in ("ready", "playing", "paused")
            if status == "loading":
                percent = replay_manager.load_percent
            else:
                percent = 100 if loaded else 0
            return {
                "status": status,
                "percent": percent,
                "stream": None,
                "event_count": _load_result["event_count"] if loaded else 0,
                "session_name": state.replay_session_name,
                "detail": _load_result["detail"],
                "login_needed": _load_result["login_needed"],
            }

        @app.post("/api/replay/play")
        async def play_replay():
            if listener is not None:
                listener.suspended = True
            state.set_replay_state(mode=True, status="playing",
                                   session_name=replay_manager._session_name)
            await replay_manager.play(on_event=on_replay_event or state.set_track_status)

            def _on_complete(task: asyncio.Task) -> None:
                if not task.cancelled():
                    if listener is not None:
                        listener.suspended = False
                        listener.reset_timing_state()
                    state.set_replay_state(mode=False, status="idle")
                    state.set_track_status("unknown")
                    state.set_display_track_status("unknown")
                    state.clear_time_remaining()
                    led.set_idle(True)

            if replay_manager._task is not None:
                replay_manager._task.add_done_callback(_on_complete)

            return {"status": "playing"}

        @app.post("/api/replay/pause")
        async def pause_replay():
            nonlocal _pre_pause_extrapolating
            replay_manager.pause()
            _pre_pause_extrapolating = state.session.extrapolating
            state.freeze_countdown()
            state.set_replay_state(mode=True, status="paused",
                                   session_name=replay_manager._session_name)
            return {"status": "paused"}

        @app.post("/api/replay/resume")
        async def resume_replay():
            replay_manager.resume()
            state.unfreeze_countdown(extrapolating=_pre_pause_extrapolating)
            state.set_replay_state(mode=True, status="playing",
                                   session_name=replay_manager._session_name)
            return {"status": "playing"}

        @app.post("/api/replay/stop")
        async def stop_replay():
            replay_manager.stop()
            _clear_load_result()
            if listener is not None:
                listener.suspended = False
                listener.reset_timing_state()
            state.set_replay_state(mode=False, status="idle")
            state.set_track_status("unknown")
            state.set_display_track_status("unknown")
            state.clear_time_remaining()
            led.set_idle(True)
            return {"status": "idle"}

        @app.post("/api/replay/offset")
        async def set_replay_offset(req: ReplayOffsetRequest):
            replay_manager.set_sync_offset(req.seconds)
            return {"offset_seconds": req.seconds}

    def _asset_tag(filename: str) -> str:
        """Return a cache-busting query param based on file mtime (changes on git pull)."""
        try:
            return str(int((FRONTEND_DIR / filename).stat().st_mtime))
        except Exception:
            return version or "0"

    @app.get("/", include_in_schema=False)
    async def index():
        if wifi_manager and wifi_manager.is_hotspot_active():
            return RedirectResponse(url="/setup")
        html = (FRONTEND_DIR / "index.html").read_text()
        html = html.replace('href="/style.css"', f'href="/style.css?v={_asset_tag("style.css")}"')
        html = html.replace('src="/app.js"', f'src="/app.js?v={_asset_tag("app.js")}"')
        return Response(content=html, media_type="text/html",
                        headers={"Cache-Control": "no-cache"})

    @app.get("/setup", include_in_schema=False)
    async def setup_page():
        return FileResponse(FRONTEND_DIR / "setup.html")

    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    return app
