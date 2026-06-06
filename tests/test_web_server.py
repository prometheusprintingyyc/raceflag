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
