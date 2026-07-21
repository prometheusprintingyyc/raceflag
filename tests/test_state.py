import threading
from raceflag.state import (
    AppState, SessionInfo, WeatherInfo, DriverPosition,
    RaceControlMessage, DriverStanding, ConstructorStanding, NextRace,
    TEAM_COLORS, COUNTRY_FLAGS, FLAG_COLORS,
)


def test_initial_track_status_is_unknown():
    s = AppState()
    assert s.track_status == "unknown"


def test_set_track_status():
    s = AppState()
    s.set_track_status("yellow_flag")
    assert s.track_status == "yellow_flag"


def test_set_session():
    s = AppState()
    session = SessionInfo(name="Monaco GP", is_active=True, current_lap=5, total_laps=78)
    s.set_session(session)
    assert s.session.name == "Monaco GP"
    assert s.session.is_active is True


def test_add_race_control_message_prepends():
    s = AppState()
    s.add_race_control_message(RaceControlMessage(time="12:00", message="TRACK CLEAR"))
    s.add_race_control_message(RaceControlMessage(time="12:01", message="YELLOW FLAG"))
    assert s.race_control_messages[0].message == "YELLOW FLAG"
    assert s.race_control_messages[1].message == "TRACK CLEAR"


def test_race_control_messages_capped_at_50():
    s = AppState()
    for i in range(60):
        s.add_race_control_message(RaceControlMessage(time=str(i), message=f"msg {i}"))
    assert len(s.race_control_messages) == 50


def test_to_dict_is_serialisable():
    import json
    s = AppState()
    s.set_track_status("red_flag")
    s.set_display_track_status("red_flag")
    d = s.to_dict()
    assert d["track_status"] == "red_flag"
    assert d["flag_color"] == FLAG_COLORS["red_flag"]
    json.dumps(d)  # must not raise


def test_thread_safety():
    s = AppState()
    errors = []

    def writer():
        try:
            for i in range(100):
                s.set_track_status("yellow_flag" if i % 2 else "track_clear")
        except Exception as e:
            errors.append(e)

    def reader():
        try:
            for _ in range(100):
                s.to_dict()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_team_colors_has_expected_teams():
    assert "Ferrari" in TEAM_COLORS
    assert "McLaren" in TEAM_COLORS
    assert "Mercedes" in TEAM_COLORS


def test_flag_colors_covers_all_states():
    for state in ("track_clear", "yellow_flag", "safety_car", "virtual_sc", "red_flag", "checkered"):
        assert state in FLAG_COLORS


def test_led_enabled_defaults_to_true():
    s = AppState()
    assert s.led_enabled is True


def test_set_led_enabled_updates_state():
    s = AppState()
    s.set_led_enabled(False)
    assert s.led_enabled is False


def test_to_dict_includes_led_enabled():
    s = AppState()
    d = s.to_dict()
    assert "led_enabled" in d
    assert d["led_enabled"] is True


def test_replay_fields_default():
    s = AppState()
    assert s.replay_mode is False
    assert s.replay_status == "idle"
    assert s.replay_session_name == ""
    assert s.replay_time_elapsed == ""


def test_set_replay_state_updates_all_fields():
    s = AppState()
    s.set_replay_state(mode=True, status="playing", session_name="2025 British GP", elapsed="00:32:00")
    assert s.replay_mode is True
    assert s.replay_status == "playing"
    assert s.replay_session_name == "2025 British GP"
    assert s.replay_time_elapsed == "00:32:00"


def test_set_replay_state_defaults_optional_args():
    s = AppState()
    s.set_replay_state(mode=False, status="idle")
    assert s.replay_session_name == ""
    assert s.replay_time_elapsed == ""


def test_to_dict_includes_replay_fields():
    s = AppState()
    s.set_replay_state(mode=True, status="ready", session_name="Test GP", elapsed="")
    d = s.to_dict()
    assert d["replay_mode"] is True
    assert d["replay_status"] == "ready"
    assert d["replay_session_name"] == "Test GP"
    assert d["replay_time_elapsed"] == ""
