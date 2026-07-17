import pytest
from fastapi.testclient import TestClient
from raceflag.web_server import create_app
from raceflag.state import AppState
from raceflag.config import Config
from raceflag.led_controller import LEDController, MockStrip


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
