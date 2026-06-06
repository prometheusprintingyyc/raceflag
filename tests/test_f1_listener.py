import json
import pytest
from raceflag.f1_listener import F1Listener, parse_track_status, parse_weather, parse_race_control, parse_session_info
from raceflag.state import AppState


def test_parse_track_status_maps_1_to_track_clear():
    assert parse_track_status({"Status": "1"}) == "track_clear"


def test_parse_track_status_maps_5_to_red_flag():
    assert parse_track_status({"Status": "5"}) == "red_flag"


def test_parse_track_status_returns_unknown_for_unexpected():
    assert parse_track_status({"Status": "99"}) == "unknown"


def test_parse_weather_extracts_fields():
    data = {
        "AirTemp": "24.5", "TrackTemp": "38.1",
        "Humidity": "62", "WindSpeed": "14.2",
        "WindDirection": "NW", "Rainfall": "0",
    }
    w = parse_weather(data)
    assert w.air_temp == 24.5
    assert w.track_temp == 38.1
    assert w.humidity == 62.0
    assert w.wind_speed == 14.2
    assert w.wind_direction == "NW"
    assert w.rain is False


def test_parse_weather_sets_rain_true_when_nonzero():
    data = {
        "AirTemp": "20", "TrackTemp": "30", "Humidity": "80",
        "WindSpeed": "5", "WindDirection": "N", "Rainfall": "1",
    }
    w = parse_weather(data)
    assert w.rain is True


def test_parse_race_control_extracts_message():
    data = {"Utc": "2025-06-01T14:32:01", "Message": "TRACK CLEAR", "Flag": "GREEN"}
    msg = parse_race_control(data)
    assert msg.message == "TRACK CLEAR"
    assert "14:32:01" in msg.time


def test_parse_race_control_sets_flag_color_for_yellow():
    data = {"Utc": "2025-06-01T14:28:44", "Message": "YELLOW FLAG", "Flag": "YELLOW"}
    msg = parse_race_control(data)
    assert msg.flag_color == "#FFD600"


def test_parse_session_info_sets_is_active():
    data = {
        "Meeting": {
            "Name": "Monaco Grand Prix",
            "Location": "Monte Carlo",
            "Country": {"Name": "Monaco"},
            "Circuit": {"ShortName": "Monaco"},
        },
        "Name": "Practice 1",
        "Type": "Practice",
    }
    session = parse_session_info(data)
    assert session.is_active is True
    assert session.name == "Monaco Grand Prix"
    assert session.session_type == "Practice 1"


def test_f1_listener_updates_state_on_track_status():
    state = AppState()
    listener = F1Listener(state=state)
    listener._handle_feed("TrackStatus", {"Status": "2"})
    assert state.track_status == "yellow_flag"


def test_f1_listener_updates_state_on_weather():
    state = AppState()
    listener = F1Listener(state=state)
    listener._handle_feed("WeatherData", {
        "AirTemp": "22", "TrackTemp": "35", "Humidity": "70",
        "WindSpeed": "10", "WindDirection": "S", "Rainfall": "0",
    })
    assert state.weather.air_temp == 22.0


def test_f1_listener_marks_session_active_on_any_feed():
    state = AppState()
    listener = F1Listener(state=state)
    assert state.session.is_active is False
    listener._handle_feed("WeatherData", {
        "AirTemp": "22", "TrackTemp": "35", "Humidity": "70",
        "WindSpeed": "10", "WindDirection": "S", "Rainfall": "0",
    })
    assert state.session.is_active is True


def test_connection_data_uses_capital_streaming():
    from raceflag.f1_listener import CONNECTION_DATA
    data = json.loads(CONNECTION_DATA)
    assert data[0]["name"] == "Streaming"


def test_topics_include_session_status_and_heartbeat():
    from raceflag.f1_listener import TOPICS
    assert "SessionStatus" in TOPICS
    assert "Heartbeat" in TOPICS


def test_process_message_handles_type1_feed():
    from raceflag.f1_listener import RECORD_SEP
    state = AppState()
    listener = F1Listener(state=state)
    raw = json.dumps({
        "type": 1,
        "target": "feed",
        "arguments": ["TrackStatus", {"Status": "5"}],
    }) + RECORD_SEP
    listener._process_message(raw)
    assert state.track_status == "red_flag"


def test_process_message_handles_type3_snapshot():
    from raceflag.f1_listener import RECORD_SEP
    state = AppState()
    listener = F1Listener(state=state)
    raw = json.dumps({
        "type": 3,
        "result": {
            "TrackStatus": {"Status": "2"},
            "WeatherData": {
                "AirTemp": "25", "TrackTemp": "40", "Humidity": "55",
                "WindSpeed": "8", "WindDirection": "W", "Rainfall": "0",
            },
        },
    }) + RECORD_SEP
    listener._process_message(raw)
    assert state.track_status == "yellow_flag"
    assert state.weather.air_temp == 25.0


def test_process_message_type3_ignores_non_dict_values():
    from raceflag.f1_listener import RECORD_SEP
    state = AppState()
    listener = F1Listener(state=state)
    raw = json.dumps({
        "type": 3,
        "result": {"SomeStream": None, "TrackStatus": {"Status": "1"}},
    }) + RECORD_SEP
    listener._process_message(raw)
    assert state.track_status == "track_clear"


def test_process_message_responds_to_ping():
    from raceflag.f1_listener import RECORD_SEP
    state = AppState()
    listener = F1Listener(state=state)
    raw = json.dumps({"type": 6}) + RECORD_SEP
    responses = listener._process_message(raw)
    assert len(responses) == 1
    assert json.loads(responses[0].replace(RECORD_SEP, "")) == {"type": 6}


def test_handle_feed_session_status_started_marks_active():
    state = AppState()
    listener = F1Listener(state=state)
    assert state.session.is_active is False
    listener._handle_feed("SessionStatus", {"Status": "Started"})
    assert state.session.is_active is True


def test_handle_feed_session_status_finished_marks_inactive():
    state = AppState()
    listener = F1Listener(state=state)
    listener._handle_feed("WeatherData", {
        "AirTemp": "22", "TrackTemp": "35", "Humidity": "70",
        "WindSpeed": "10", "WindDirection": "S", "Rainfall": "0",
    })
    assert state.session.is_active is True
    listener._handle_feed("SessionStatus", {"Status": "Finished"})
    assert state.session.is_active is False


def test_handle_feed_session_status_ends_sets_break():
    state = AppState()
    listener = F1Listener(state=state)
    listener._handle_feed("WeatherData", {
        "AirTemp": "22", "TrackTemp": "35", "Humidity": "70",
        "WindSpeed": "10", "WindDirection": "S", "Rainfall": "0",
    })
    listener._handle_feed("SessionStatus", {"Status": "Ends"})
    assert state.session.is_active is False
    assert state.track_status == "break"


def test_handle_feed_session_status_inactive_does_not_reactivate():
    """SessionStatus Inactive (Q1→Q2 break) must not re-mark the session active."""
    state = AppState()
    listener = F1Listener(state=state)
    listener._handle_feed("WeatherData", {
        "AirTemp": "22", "TrackTemp": "35", "Humidity": "70",
        "WindSpeed": "10", "WindDirection": "S", "Rainfall": "0",
    })
    listener._handle_feed("SessionStatus", {"Status": "Ends"})
    listener._handle_feed("SessionStatus", {"Status": "Inactive"})
    assert state.session.is_active is False
    assert state.track_status == "break"


def test_handle_feed_session_status_finished_sets_finished():
    state = AppState()
    listener = F1Listener(state=state)
    listener._handle_feed("WeatherData", {
        "AirTemp": "22", "TrackTemp": "35", "Humidity": "70",
        "WindSpeed": "10", "WindDirection": "S", "Rainfall": "0",
    })
    listener._handle_feed("SessionStatus", {"Status": "Finished"})
    assert state.session.is_active is False
    assert state.track_status == "finished"


def test_handle_feed_session_status_finished_resets_track_status():
    state = AppState()
    listener = F1Listener(state=state)
    listener._handle_feed("TrackStatus", {"Status": "2"})
    assert state.track_status == "yellow_flag"
    listener._handle_feed("SessionStatus", {"Status": "Finished"})
    assert state.track_status == "finished"


def test_timing_data_incremental_updates_accumulate():
    """Incremental TimingData must merge — not replace — so all 20 drivers remain visible."""
    state = AppState()
    listener = F1Listener(state=state)
    listener._driver_list = {
        "1": {"Tla": "VER", "FullName": "Max Verstappen", "TeamName": "Red Bull Racing", "TeamColour": "3671C6"},
        "44": {"Tla": "HAM", "FullName": "Lewis Hamilton", "TeamName": "Mercedes", "TeamColour": "27F4D2"},
    }
    listener._handle_feed("TimingData", {"Lines": {
        "1": {"Position": "1", "GapToLeader": ""},
        "44": {"Position": "2", "GapToLeader": "+1.234"},
    }})
    assert len(state.driver_positions) == 2

    # Second update only touches driver 44 — driver 1 must stay in the table
    listener._handle_feed("TimingData", {"Lines": {
        "44": {"GapToLeader": "+2.100"},
    }})
    assert len(state.driver_positions) == 2
    ver = next(p for p in state.driver_positions if p.code == "VER")
    ham = next(p for p in state.driver_positions if p.code == "HAM")
    assert ver.position == 1
    assert ham.gap == "+2.100"


def test_timing_data_gap_dict_format():
    """GapToLeader can be a dict {Value: '...'} — extract the value string."""
    state = AppState()
    listener = F1Listener(state=state)
    listener._driver_list = {
        "33": {"Tla": "VER", "FullName": "Max Verstappen", "TeamName": "Red Bull Racing", "TeamColour": "3671C6"},
    }
    listener._handle_feed("TimingData", {"Lines": {
        "33": {"Position": "1", "GapToLeader": {"Value": "+0.000"}},
    }})
    assert state.driver_positions[0].gap == "+0.000"


def test_timing_app_data_provides_tyre():
    """TimingAppData stints should populate the tyre field."""
    state = AppState()
    listener = F1Listener(state=state)
    listener._driver_list = {
        "1": {"Tla": "VER", "FullName": "Max Verstappen", "TeamName": "Red Bull Racing", "TeamColour": "3671C6"},
    }
    listener._handle_feed("TimingData", {"Lines": {"1": {"Position": "1"}}})
    listener._handle_feed("TimingAppData", {"Lines": {
        "1": {"Stints": {"0": {"Compound": "SOFT", "TotalLaps": 10}}},
    }})
    assert state.driver_positions[0].tyre == "S"


def test_timing_data_last_lap_time_string():
    state = AppState()
    listener = F1Listener(state=state)
    listener._driver_list = {
        "1": {"Tla": "VER", "FullName": "Max Verstappen", "TeamName": "Red Bull Racing", "TeamColour": "3671C6"},
    }
    listener._handle_feed("TimingData", {"Lines": {
        "1": {"Position": "1", "LastLapTime": {"Value": "1:18.234"}},
    }})
    assert state.driver_positions[0].last_lap_time == "1:18.234"


def test_driver_list_merges_on_update():
    """DriverList updates are incremental — merge, don't replace."""
    state = AppState()
    listener = F1Listener(state=state)
    listener._handle_feed("DriverList", {
        "1": {"Tla": "VER", "FullName": "Max Verstappen", "TeamName": "Red Bull Racing", "TeamColour": "3671C6"},
    })
    listener._handle_feed("DriverList", {
        "44": {"Tla": "HAM", "FullName": "Lewis Hamilton", "TeamName": "Mercedes", "TeamColour": "27F4D2"},
    })
    assert "1" in listener._driver_list
    assert "44" in listener._driver_list
