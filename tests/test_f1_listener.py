import pytest
from raceflag.f1_listener import F1Listener, parse_track_status, parse_weather, parse_race_control
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


def test_f1_listener_updates_state_on_track_status(mocker):
    state = AppState()
    listener = F1Listener(state=state)
    listener._handle_feed("TrackStatus", {"Status": "2"})
    assert state.track_status == "yellow_flag"


def test_f1_listener_updates_state_on_weather(mocker):
    state = AppState()
    listener = F1Listener(state=state)
    listener._handle_feed("WeatherData", {
        "AirTemp": "22", "TrackTemp": "35", "Humidity": "70",
        "WindSpeed": "10", "WindDirection": "S", "Rainfall": "0",
    })
    assert state.weather.air_temp == 22.0
