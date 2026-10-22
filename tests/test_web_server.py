import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from raceflag.web_server import create_app
from raceflag.state import AppState
from raceflag.config import Config
from raceflag.led_controller import LEDController, MockStrip
from raceflag.replay_manager import ReplayManager


@pytest.fixture
def app_state():
    return AppState()


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def led(tmp_path):
    effects = tmp_path / "effects.json"
    effects.write_text('{"track_clear": {"segments": [{"start":0,"end":9,"color":"#00FF00","pattern":"solid"}],"transition":"instant","transition_ms":0}}')
    return LEDController(strip=MockStrip(10), effects_path=effects, delay_seconds=0.0)


@pytest.fixture
def client(app_state, config, led):
    app = create_app(state=app_state, config=config, led=led)
    return TestClient(app)


def test_get_state_returns_200(client):
    resp = client.get("/api/state")
    assert resp.status_code == 200
    data = resp.json()
    assert "track_status" in data
    assert "session" in data
    assert "weather" in data


def test_get_state_reflects_current_track_status(client, app_state):
    app_state.set_track_status("red_flag")
    app_state.set_display_track_status("red_flag")
    resp = client.get("/api/state")
    assert resp.json()["track_status"] == "red_flag"


def test_set_delay_updates_led_controller(client, led):
    resp = client.post("/api/config/delay", json={"seconds": 20.0})
    assert resp.status_code == 200
    assert led._delay_seconds == 20.0


def test_set_delay_rejects_negative(client):
    resp = client.post("/api/config/delay", json={"seconds": -1.0})
    assert resp.status_code == 422


def test_set_delay_rejects_above_60(client):
    resp = client.post("/api/config/delay", json={"seconds": 61.0})
    assert resp.status_code == 422


def test_test_effect_triggers_led(client, led):
    resp = client.post("/api/test-effect", json={"flag_state": "red_flag"})
    assert resp.status_code == 200
    assert not led._queue.empty()


def test_test_effect_rejects_unknown_state(client):
    resp = client.post("/api/test-effect", json={"flag_state": "nonsense"})
    assert resp.status_code == 422


def test_get_config_returns_current_values(client, config):
    config.led_count = 120
    resp = client.get("/api/config")
    assert resp.json()["led_count"] == 120


def test_get_led_state_returns_pixels_for_mock_strip(client):
    resp = client.get("/api/led-state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert isinstance(data["pixels"], list)
    assert len(data["pixels"]) == 10
    assert data["pixels"][0] == [0, 0, 0]


def test_test_idle_cancels_running_checkered(client, led):
    led.trigger_timed("checkered", 30.0)
    resp = client.post("/api/test-idle")
    assert resp.status_code == 200
    assert led._timed_effect == ""
    assert led._idle_active is True


def test_set_demo_mode_on(client, app_state):
    resp = client.post("/api/config/demo-mode", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["demo_mode"] is True
    assert app_state.demo_mode is True


def test_set_demo_mode_off(client, app_state):
    app_state.set_demo_mode(True)
    resp = client.post("/api/config/demo-mode", json={"enabled": False})
    assert resp.status_code == 200
    assert app_state.demo_mode is False


def test_state_includes_demo_mode(client, app_state):
    app_state.set_demo_mode(True)
    resp = client.get("/api/state")
    assert resp.json()["demo_mode"] is True


def test_logs_returns_lines_and_timestamp(client, mocker):
    mocker.patch(
        "raceflag.web_server._fetch_logs",
        return_value="INFO starting\nINFO feed connected\n",
    )
    resp = client.get("/api/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["lines"] == "INFO starting\nINFO feed connected\n"
    assert "timestamp" in data


def test_logs_returns_fallback_when_journalctl_unavailable(client, mocker):
    mocker.patch(
        "raceflag.web_server._fetch_logs",
        return_value="journalctl not available — unit may be running in Docker or a non-systemd environment.",
    )
    resp = client.get("/api/logs")
    assert resp.status_code == 200
    assert "not available" in resp.json()["lines"]


@pytest.mark.asyncio
async def test_fetch_logs_returns_stdout_on_success(mocker):
    mock_proc = mocker.MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = mocker.AsyncMock(return_value=(b"INFO starting\nINFO feed connected\n", b""))
    mocker.patch(
        "raceflag.web_server.asyncio.create_subprocess_exec",
        new=mocker.AsyncMock(return_value=mock_proc),
    )
    from raceflag.web_server import _fetch_logs
    result = await _fetch_logs()
    assert result == "INFO starting\nINFO feed connected\n"


@pytest.mark.asyncio
async def test_fetch_logs_returns_fallback_on_nonzero_exit(mocker):
    mock_proc = mocker.MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = mocker.AsyncMock(return_value=(b"", b""))
    mocker.patch(
        "raceflag.web_server.asyncio.create_subprocess_exec",
        new=mocker.AsyncMock(return_value=mock_proc),
    )
    from raceflag.web_server import _fetch_logs, _LOGS_UNAVAILABLE
    result = await _fetch_logs()
    assert result == _LOGS_UNAVAILABLE


@pytest.mark.asyncio
async def test_fetch_logs_returns_fallback_when_journalctl_missing(mocker):
    mocker.patch(
        "raceflag.web_server.asyncio.create_subprocess_exec",
        side_effect=FileNotFoundError,
    )
    from raceflag.web_server import _fetch_logs, _LOGS_UNAVAILABLE
    result = await _fetch_logs()
    assert result == _LOGS_UNAVAILABLE


# ---------------------------------------------------------------------------
# LED mode and lamp (feature 014)
# ---------------------------------------------------------------------------

def test_set_led_mode_returns_mode(client):
    resp = client.post("/api/led/mode", json={"mode": "lamp"})
    assert resp.status_code == 200
    assert resp.json() == {"led_mode": "lamp"}


def test_set_led_mode_updates_state_and_controller(client, app_state, led):
    client.post("/api/led/mode", json={"mode": "auto"})
    assert app_state.led_mode == "auto"
    assert led._mode == "auto"


def test_set_led_mode_rejects_unknown(client, led):
    resp = client.post("/api/led/mode", json={"mode": "disco"})
    assert resp.status_code == 422
    assert led._mode == "on"


def test_set_led_mode_ends_preview(client, led):
    led.start_preview("#00FF00", 100)
    client.post("/api/led/mode", json={"mode": "on"})
    assert led._preview is None


def test_led_enabled_route_removed(client):
    resp = client.post("/api/led/enabled", json={"enabled": False})
    assert resp.status_code in (404, 405)


def test_get_state_includes_led_mode(client):
    data = client.get("/api/state").json()
    assert data["led_mode"] == "on"
    assert "led_enabled" not in data


def test_lamp_preview_starts_preview(client, led):
    resp = client.post("/api/led/lamp/preview", json={"color": "#00ff00", "brightness": 40})
    assert resp.status_code == 200
    assert resp.json() == {"preview": True}
    assert led._preview == ("#00FF00", 40)


@pytest.mark.parametrize("body", [
    {"color": "green", "brightness": 40},
    {"color": "#00FF00", "brightness": 101},
    {"color": "#00FF00", "brightness": -1},
])
def test_lamp_preview_rejects_bad_input(client, led, body):
    resp = client.post("/api/led/lamp/preview", json=body)
    assert resp.status_code == 422
    assert led._preview is None


def test_delete_lamp_preview_ends_preview(client, led):
    led.start_preview("#00FF00", 100)
    resp = client.delete("/api/led/lamp/preview")
    assert resp.status_code == 200
    assert resp.json() == {"preview": False}
    assert led._preview is None


def test_save_lamp_persists_and_applies(tmp_path, app_state, config, led):
    path = tmp_path / "config.json"
    c = TestClient(create_app(state=app_state, config=config, led=led, config_path=path))
    led.start_preview("#00FF00", 100)
    resp = c.post("/api/config/lamp", json={"color": "#ffd700", "brightness": 80})
    assert resp.status_code == 200
    assert resp.json() == {"lamp_color": "#FFD700", "lamp_brightness": 80, "lamp_use_schedule": False}
    assert (config.lamp_color, config.lamp_brightness) == ("#FFD700", 80)
    saved = json.loads(path.read_text())
    assert (saved["lamp_color"], saved["lamp_brightness"]) == ("#FFD700", 80)
    assert led._lamp == ("#FFD700", 80)
    assert led._preview is None


def test_save_lamp_use_schedule_persists_and_applies(tmp_path, app_state, config, led):
    path = tmp_path / "config.json"
    c = TestClient(create_app(state=app_state, config=config, led=led, config_path=path))
    resp = c.post("/api/config/lamp", json={"color": "#FFD700", "brightness": 80, "use_schedule": True})
    assert resp.status_code == 200
    assert resp.json()["lamp_use_schedule"] is True
    assert config.lamp_use_schedule is True
    assert json.loads(path.read_text())["lamp_use_schedule"] is True
    assert led._lamp_use_schedule is True


def test_save_lamp_without_use_schedule_keeps_saved_value(client, config, led):
    config.lamp_use_schedule = True
    led.set_lamp_use_schedule(True)
    resp = client.post("/api/config/lamp", json={"color": "#FFD700", "brightness": 80})
    assert resp.json()["lamp_use_schedule"] is True
    assert led._lamp_use_schedule is True


def test_save_lamp_rejects_bad_colour(client, config, led):
    resp = client.post("/api/config/lamp", json={"color": "#FFF", "brightness": 80})
    assert resp.status_code == 422
    assert config.lamp_color == "#FFFFFF"
    assert led._lamp == ("#FFFFFF", 100)


def test_time_sync_updates_led_timezone(client, led, mocker):
    mocker.patch("raceflag.clock._set_system_clock")
    client.post("/api/time/sync",
                json={"timezone": "America/Edmonton", "epoch_ms": int(time.time() * 1000)})
    assert led._timezone == "America/Edmonton"


def test_set_schedule_persists_and_applies(tmp_path, app_state, config, led):
    path = tmp_path / "config.json"
    c = TestClient(create_app(state=app_state, config=config, led=led, config_path=path))
    resp = c.post("/api/config/schedule", json={"on": "18:00", "off": "01:00"})
    assert resp.status_code == 200
    assert resp.json() == {"schedule_on": "18:00", "schedule_off": "01:00"}
    assert (config.schedule_on, config.schedule_off) == ("18:00", "01:00")
    saved = json.loads(path.read_text())
    assert (saved["schedule_on"], saved["schedule_off"]) == ("18:00", "01:00")
    assert led._schedule == (1080, 60)


def test_set_schedule_rejects_equal_times(client, config, led):
    resp = client.post("/api/config/schedule", json={"on": "09:00", "off": "09:00"})
    assert resp.status_code == 422
    assert (config.schedule_on, config.schedule_off) == ("08:00", "23:00")
    assert led._schedule == (480, 1380)


@pytest.mark.parametrize("on,off", [
    ("8:00", "23:00"), ("24:00", "23:00"), ("08:60", "23:00"), ("", "23:00"), ("08:00:00", "23:00"),
])
def test_set_schedule_rejects_bad_format(client, config, on, off):
    resp = client.post("/api/config/schedule", json={"on": on, "off": off})
    assert resp.status_code == 422
    assert config.schedule_on == "08:00"


# ---------------------------------------------------------------------------
# Time sync (feature 014)
# ---------------------------------------------------------------------------

def test_time_sync_saves_timezone(tmp_path, app_state, config, led, mocker):
    setter = mocker.patch("raceflag.clock._set_system_clock")
    path = tmp_path / "config.json"
    c = TestClient(create_app(state=app_state, config=config, led=led, config_path=path))
    resp = c.post("/api/time/sync",
                  json={"timezone": "America/Edmonton", "epoch_ms": int(time.time() * 1000)})
    assert resp.status_code == 200
    assert resp.json() == {"timezone": "America/Edmonton", "clock_adjusted": False}
    assert config.timezone == "America/Edmonton"
    assert json.loads(path.read_text())["timezone"] == "America/Edmonton"
    setter.assert_not_called()


def test_time_sync_sets_clock_when_far_off(client, mocker):
    setter = mocker.patch("raceflag.clock._set_system_clock")
    resp = client.post("/api/time/sync",
                       json={"timezone": "UTC", "epoch_ms": int((time.time() + 3600) * 1000)})
    assert resp.status_code == 200
    assert resp.json()["clock_adjusted"] is True
    setter.assert_called_once()


def test_time_sync_rejects_unknown_timezone(client, config, mocker):
    setter = mocker.patch("raceflag.clock._set_system_clock")
    resp = client.post("/api/time/sync",
                       json={"timezone": "Mars/Olympus_Mons", "epoch_ms": int(time.time() * 1000)})
    assert resp.status_code == 422
    assert config.timezone == ""
    setter.assert_not_called()


def test_time_sync_rejects_implausible_epoch(client, config, mocker):
    setter = mocker.patch("raceflag.clock._set_system_clock")
    resp = client.post("/api/time/sync",
                       json={"timezone": "America/Edmonton", "epoch_ms": 1_000_000_000_000})
    assert resp.status_code == 422
    assert config.timezone == ""
    setter.assert_not_called()


def test_time_sync_accepts_empty_timezone(client, config, mocker):
    mocker.patch("raceflag.clock._set_system_clock")
    config.timezone = "America/Edmonton"
    resp = client.post("/api/time/sync", json={"timezone": "", "epoch_ms": int(time.time() * 1000)})
    assert resp.status_code == 200
    assert resp.json()["timezone"] == "America/Edmonton"
    assert config.timezone == "America/Edmonton"


def test_get_time_reports_box_local_time(client, config, mocker):
    config.timezone = "America/Edmonton"
    mocker.patch("raceflag.clock.local_minutes", return_value=11 * 60 + 9)
    resp = client.get("/api/time")
    assert resp.status_code == 200
    assert resp.json() == {"timezone": "America/Edmonton", "local_time": "11:09"}


def test_get_time_without_timezone_reports_utc(client):
    assert client.get("/api/time").json()["timezone"] == "UTC"


def test_get_config_includes_led_mode_settings(client):
    data = client.get("/api/config").json()
    for key in ("schedule_on", "schedule_off", "lamp_color", "lamp_brightness", "lamp_use_schedule", "timezone"):
        assert key in data


# ---------------------------------------------------------------------------
# Replay endpoint tests
# ---------------------------------------------------------------------------

@pytest.fixture
def replay_manager():
    rm = ReplayManager()
    rm.get_sessions = AsyncMock(return_value=[
        {"name": "2025 British GP", "path": "2025/brit/", "date": "2025-07-06", "circuit": "Silverstone"}
    ])
    rm.load_session = AsyncMock(return_value=42)
    rm.play = AsyncMock()
    rm.pause = MagicMock()
    rm.resume = MagicMock()
    rm.stop = MagicMock()
    rm.set_sync_offset = MagicMock()
    rm._paused = False
    return rm


@pytest.fixture
def client_with_replay(app_state, config, led, replay_manager):
    app = create_app(state=app_state, config=config, led=led, replay_manager=replay_manager)
    return TestClient(app)


def test_get_replay_sessions(client_with_replay):
    resp = client_with_replay.get("/api/replay/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["name"] == "2025 British GP"


def test_post_replay_load(client_with_replay, app_state):
    resp = client_with_replay.post(
        "/api/replay/load",
        json={"session_path": "2025/brit/", "session_name": "2025 British GP"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["event_count"] == 42
    assert app_state.replay_status == "ready"


def test_post_replay_play(client_with_replay, app_state):
    client_with_replay.post("/api/replay/load",
                            json={"session_path": "2025/brit/", "session_name": "2025 British GP"})
    resp = client_with_replay.post("/api/replay/play")
    assert resp.status_code == 200
    assert resp.json()["status"] == "playing"
    assert app_state.replay_status == "playing"


def test_post_replay_pause(client_with_replay, app_state):
    client_with_replay.post("/api/replay/load",
                            json={"session_path": "2025/brit/", "session_name": "2025 British GP"})
    client_with_replay.post("/api/replay/play")
    resp = client_with_replay.post("/api/replay/pause")
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"
    assert app_state.replay_status == "paused"


def test_post_replay_stop(client_with_replay, app_state):
    client_with_replay.post("/api/replay/load",
                            json={"session_path": "2025/brit/", "session_name": "2025 British GP"})
    client_with_replay.post("/api/replay/play")
    resp = client_with_replay.post("/api/replay/stop")
    assert resp.status_code == 200
    assert resp.json()["status"] == "idle"
    assert app_state.replay_mode is False
    assert app_state.replay_status == "idle"


def test_post_replay_offset(client_with_replay, replay_manager):
    resp = client_with_replay.post("/api/replay/offset", json={"seconds": 5.0})
    assert resp.status_code == 200
    assert resp.json()["offset_seconds"] == 5.0
    replay_manager.set_sync_offset.assert_called_once_with(5.0)


def test_replay_load_resets_state_on_error(client_with_replay, app_state, replay_manager):
    """If load_session raises, the endpoint returns 502 and resets replay state to idle."""
    replay_manager.load_session.side_effect = Exception("network error")
    resp = client_with_replay.post(
        "/api/replay/load",
        json={"session_path": "2025/brit/", "session_name": "2025 British GP"},
    )
    assert resp.status_code == 502
    assert "network error" in resp.json()["detail"]
    assert app_state.replay_mode is False
    assert app_state.replay_status == "idle"


def test_replay_endpoints_absent_without_replay_manager(client):
    assert client.get("/api/replay/sessions").status_code == 404


def test_replay_sessions_login_refused_returns_401(client_with_replay, replay_manager):
    from raceflag.replay_manager import F1LoginRequired
    replay_manager.get_sessions = AsyncMock(side_effect=F1LoginRequired())
    resp = client_with_replay.get("/api/replay/sessions")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "F1 login needed or expired. Sign in again."}


def test_replay_load_login_refused_returns_401_and_stays_idle(client_with_replay, replay_manager, app_state):
    from raceflag.replay_manager import F1LoginRequired
    replay_manager.load_session = AsyncMock(side_effect=F1LoginRequired())
    resp = client_with_replay.post("/api/replay/load", json={"session_path": "p/", "session_name": "X"})
    assert resp.status_code == 401
    assert "F1 login needed" in resp.json()["detail"]
    assert app_state.replay_mode is False


# ── Live feed paused from the start of a load (feature 006, ESP32 difference 1) ─

@pytest.fixture
def replay_env(app_state, config, led, replay_manager):
    from raceflag.f1_listener import F1Listener
    listener = F1Listener(state=app_state)
    app = create_app(state=app_state, config=config, led=led,
                     replay_manager=replay_manager, listener=listener)
    return TestClient(app), listener


def test_replay_load_pauses_live_feed_while_downloading(replay_env, replay_manager):
    c, listener = replay_env
    seen = {}

    async def load(path, session_name=""):
        seen["suspended"] = listener.suspended
        return 42
    replay_manager.load_session = AsyncMock(side_effect=load)
    c.post("/api/replay/load", json={"session_path": "p/", "session_name": "X"})
    assert seen["suspended"] is True


def test_replay_load_keeps_live_feed_paused_until_play(replay_env, app_state):
    c, listener = replay_env
    c.post("/api/replay/load", json={"session_path": "p/", "session_name": "X"})
    assert app_state.replay_status == "ready"
    assert listener.suspended is True
    # A live message arriving before Play no longer overwrites the loaded race.
    listener._handle_feed("TrackStatus", {"Status": "5"})
    assert app_state.track_status != "red_flag"


def test_replay_load_starts_from_empty_timing_data(replay_env, app_state, replay_manager):
    c, listener = replay_env
    listener._timing_lines = {"44": {"Position": "1"}}
    app_state.set_driver_positions([{"number": "44"}])
    seen = {}

    async def load(path, session_name=""):
        seen["lines"] = dict(listener._timing_lines)
        seen["positions"] = list(app_state.driver_positions)
        return 42
    replay_manager.load_session = AsyncMock(side_effect=load)
    c.post("/api/replay/load", json={"session_path": "p/", "session_name": "X"})
    assert seen == {"lines": {}, "positions": []}


@pytest.mark.parametrize("error", ["login", "network"])
def test_replay_load_failure_resumes_live_feed(replay_env, replay_manager, error):
    from raceflag.replay_manager import F1LoginRequired
    c, listener = replay_env
    replay_manager.load_session = AsyncMock(
        side_effect=F1LoginRequired() if error == "login" else Exception("network error"))
    c.post("/api/replay/load", json={"session_path": "p/", "session_name": "X"})
    assert listener.suspended is False


def test_replay_load_starts_a_new_race_for_the_flag_router(app_state, config, led, replay_manager):
    router = MagicMock()
    app = create_app(state=app_state, config=config, led=led,
                     replay_manager=replay_manager, flag_router=router)
    TestClient(app).post("/api/replay/load", json={"session_path": "p/", "session_name": "X"})
    router.new_race.assert_called_once_with()


def test_replay_stop_after_load_resumes_live_feed(replay_env):
    c, listener = replay_env
    c.post("/api/replay/load", json={"session_path": "p/", "session_name": "X"})
    c.post("/api/replay/stop")
    assert listener.suspended is False


# ── GET /api/replay/progress ─────────────────────────────────────────────────

_LOAD = {"session_path": "2025/brit/", "session_name": "2025 British GP"}


def test_replay_progress_when_idle(client_with_replay):
    resp = client_with_replay.get("/api/replay/progress")
    assert resp.status_code == 200
    assert resp.json() == {"status": "idle", "percent": 0, "stream": None, "event_count": 0,
                           "session_name": "", "detail": None, "login_needed": False}


def test_replay_progress_while_loading_reports_streams_finished(client_with_replay, replay_manager, app_state):
    app_state.set_replay_state(mode=True, status="loading", session_name="2025 British GP")
    replay_manager._streams_done = 3
    body = client_with_replay.get("/api/replay/progress").json()
    assert body["status"] == "loading"
    assert body["percent"] == 30
    assert body["session_name"] == "2025 British GP"
    assert body["event_count"] == 0


def test_replay_progress_after_load_is_ready_at_100(client_with_replay):
    client_with_replay.post("/api/replay/load", json=_LOAD)
    body = client_with_replay.get("/api/replay/progress").json()
    assert body == {"status": "ready", "percent": 100, "stream": None, "event_count": 42,
                    "session_name": "2025 British GP", "detail": None, "login_needed": False}


def test_replay_progress_after_login_refused(client_with_replay, replay_manager):
    from raceflag.replay_manager import F1LoginRequired
    replay_manager.load_session = AsyncMock(side_effect=F1LoginRequired())
    client_with_replay.post("/api/replay/load", json=_LOAD)
    body = client_with_replay.get("/api/replay/progress").json()
    assert body["status"] == "idle"
    assert body["percent"] == 0
    assert body["detail"] == "F1 login needed or expired. Sign in again."
    assert body["login_needed"] is True


def test_replay_progress_after_failed_load(client_with_replay, replay_manager):
    replay_manager.load_session.side_effect = Exception("network error")
    client_with_replay.post("/api/replay/load", json=_LOAD)
    body = client_with_replay.get("/api/replay/progress").json()
    assert body["status"] == "idle"
    assert "network error" in body["detail"]
    assert body["login_needed"] is False


def test_replay_progress_failure_cleared_by_next_load(client_with_replay, replay_manager):
    replay_manager.load_session.side_effect = Exception("network error")
    client_with_replay.post("/api/replay/load", json=_LOAD)
    replay_manager.load_session.side_effect = None
    client_with_replay.post("/api/replay/load", json=_LOAD)
    assert client_with_replay.get("/api/replay/progress").json()["detail"] is None


def test_replay_progress_failure_cleared_by_stop(client_with_replay, replay_manager):
    replay_manager.load_session.side_effect = Exception("network error")
    client_with_replay.post("/api/replay/load", json=_LOAD)
    client_with_replay.post("/api/replay/stop")
    body = client_with_replay.get("/api/replay/progress").json()
    assert body["detail"] is None
    assert body["login_needed"] is False


def test_replay_progress_event_count_cleared_by_stop(client_with_replay):
    client_with_replay.post("/api/replay/load", json=_LOAD)
    client_with_replay.post("/api/replay/stop")
    body = client_with_replay.get("/api/replay/progress").json()
    assert body["status"] == "idle"
    assert body["event_count"] == 0
    assert body["percent"] == 0


# ---------------------------------------------------------------------------
# F1 account (feature 017)
# ---------------------------------------------------------------------------

def _account_token(exp_offset: float = 3600.0) -> str:
    import base64
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'x'})}.{part({'exp': int(time.time() + exp_offset)})}.sig"


@pytest.fixture
def account_env(tmp_path, app_state, config, led):
    path = tmp_path / "config.json"
    return TestClient(create_app(state=app_state, config=config, led=led, config_path=path)), path


def test_account_starts_signed_out(account_env):
    c, _ = account_env
    assert c.get("/api/f1/account").json() == {"status": "signed_out", "signed_in": False, "expires_at": None}


def test_account_paste_cookie_saves_token(account_env, config):
    from urllib.parse import quote
    c, path = account_env
    token = _account_token()
    cookie = quote(json.dumps({"data": {"subscriptionToken": token}}))
    resp = c.post("/api/f1/account", json={"login": cookie})
    assert resp.status_code == 200
    assert resp.json()["signed_in"] is True
    assert config.f1_token == token
    assert json.loads(path.read_text())["f1_token"] == token
    assert c.get("/api/f1/account").json()["signed_in"] is True


def test_account_paste_rejects_garbage_and_keeps_old_token(account_env, config):
    c, path = account_env
    config.f1_token = "previous"
    resp = c.post("/api/f1/account", json={"login": "not a login"})
    assert resp.status_code == 422
    assert "isn't an F1 login" in resp.json()["detail"]
    assert config.f1_token == "previous"
    assert not path.exists()


def test_account_paste_rejects_expired(account_env):
    c, _ = account_env
    resp = c.post("/api/f1/account", json={"login": _account_token(-60)})
    assert resp.status_code == 422
    assert "expired" in resp.json()["detail"]


def test_account_sign_out_clears_token(account_env, config):
    c, path = account_env
    c.post("/api/f1/account", json={"login": _account_token()})
    resp = c.delete("/api/f1/account")
    assert resp.status_code == 200
    assert resp.json() == {"status": "signed_out", "signed_in": False, "expires_at": None}
    assert config.f1_token == ""
    assert json.loads(path.read_text())["f1_token"] == ""


@pytest.fixture
def renewer_env(tmp_path, app_state, config, led):
    renewer = MagicMock()
    renewer.rejected = False
    path = tmp_path / "config.json"
    c = TestClient(create_app(state=app_state, config=config, led=led, config_path=path, f1_renewer=renewer))
    return c, renewer


def test_account_reports_sign_in_again_when_renewal_refused(renewer_env, config):
    c, renewer = renewer_env
    config.f1_token = _account_token()
    renewer.rejected = True
    data = c.get("/api/f1/account").json()
    assert data["status"] == "sign_in_again"
    assert data["signed_in"] is False


def test_saving_a_login_clears_the_refusal(renewer_env):
    c, renewer = renewer_env
    resp = c.post("/api/f1/account", json={"login": _account_token()})
    assert resp.status_code == 200
    renewer.login_saved.assert_called_once_with()


def test_sign_out_clears_the_refusal(renewer_env):
    c, renewer = renewer_env
    c.delete("/api/f1/account")
    renewer.login_saved.assert_called_once_with()


def test_rejected_login_does_not_call_login_saved(renewer_env):
    c, renewer = renewer_env
    c.post("/api/f1/account", json={"login": "junk"})
    renewer.login_saved.assert_not_called()


def test_get_config_never_returns_the_f1_token(account_env, config):
    c, _ = account_env
    config.f1_token = _account_token()
    data = c.get("/api/config").json()
    assert "f1_token" not in data


# ---------------------------------------------------------------------------
# Live delay Sync (feature 016)
# ---------------------------------------------------------------------------

from raceflag.state import SessionInfo  # noqa: E402


@pytest.fixture
def sync_env(tmp_path, app_state, config, led):
    router = MagicMock()
    path = tmp_path / "config.json"
    c = TestClient(create_app(state=app_state, config=config, led=led, config_path=path,
                              flag_router=router, clock=lambda: 1034.24))
    app_state.set_session(SessionInfo(session_type="Race", is_active=True))
    return c, router, path


def test_sync_sets_saves_and_plays_race_start(sync_env, app_state, config):
    c, router, path = sync_env
    app_state.set_lights_out_at(1000.0)
    resp = c.post("/api/delay/sync")
    assert resp.status_code == 200
    assert resp.json() == {"delay_seconds": 34.2}
    assert config.delay_seconds == 34.2
    assert json.loads(path.read_text())["delay_seconds"] == 34.2
    router.sync.assert_called_once_with(34.2)


@pytest.mark.parametrize("setup,message", [
    ("not_race", "Sync needs a live race or sprint"),
    ("replay", "Sync needs a live race or sprint"),
    ("no_lights_out", "The race hasn't started yet"),
    ("too_late", "Sync works within 90 s of the race start"),
])
def test_sync_refusals_change_nothing(sync_env, app_state, config, setup, message):
    c, router, path = sync_env
    config.delay_seconds = 7.0
    app_state.set_lights_out_at(1000.0)
    if setup == "not_race":
        app_state.set_session(SessionInfo(session_type="Qualifying", is_active=True))
    elif setup == "replay":
        app_state.set_replay_state(True, "playing")
    elif setup == "no_lights_out":
        app_state.set_lights_out_at(None)
    elif setup == "too_late":
        app_state.set_lights_out_at(900.0)
    resp = c.post("/api/delay/sync")
    assert resp.status_code == 409
    assert resp.json() == {"detail": message}
    assert config.delay_seconds == 7.0
    assert not path.exists()
    router.sync.assert_not_called()


def test_sync_after_race_end_keeps_saved_delay(sync_env, app_state, config):
    from raceflag.f1_listener import F1Listener
    c, router, path = sync_env
    app_state.set_lights_out_at(1000.0)
    assert c.post("/api/delay/sync").status_code == 200
    F1Listener(state=app_state)._handle_feed("SessionStatus", {"Status": "Finished"})
    assert app_state.lights_out_at is None
    resp = c.post("/api/delay/sync")
    assert resp.status_code == 409
    assert config.delay_seconds == 34.2
    assert json.loads(path.read_text())["delay_seconds"] == 34.2


def test_sync_applies_before_saving(sync_env, app_state, monkeypatch):
    # An SD write error must not stop the delay applying and the race start playing.
    c, router, path = sync_env
    app_state.set_lights_out_at(1000.0)

    def failing_save(*args, **kwargs):
        raise OSError("SD card write failed")

    monkeypatch.setattr("raceflag.web_server.save_config", failing_save)
    with pytest.raises(OSError):
        c.post("/api/delay/sync")
    router.sync.assert_called_once_with(34.2)
