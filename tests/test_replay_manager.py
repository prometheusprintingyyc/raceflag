import asyncio
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from raceflag.replay_manager import ReplayManager, _parse_ts, _parse_jsonstream_line


# ── Pure helper tests ────────────────────────────────────────────────────────

def test_parse_ts_converts_hhmmss():
    assert _parse_ts("00:00:00.000") == pytest.approx(0.0)
    assert _parse_ts("00:01:00.000") == pytest.approx(60.0)
    assert _parse_ts("01:00:00.000") == pytest.approx(3600.0)
    assert _parse_ts("00:32:55.416") == pytest.approx(1975.416, abs=0.001)


def test_parse_jsonstream_line_extracts_ts_and_payload():
    line = '00:32:55.416{"Status":"2"}'
    result = _parse_jsonstream_line(line)
    assert result is not None
    ts, payload = result
    assert ts == pytest.approx(1975.416, abs=0.001)
    assert payload == {"Status": "2"}


def test_parse_jsonstream_line_returns_none_for_blank():
    assert _parse_jsonstream_line("") is None
    assert _parse_jsonstream_line("  ") is None


def test_parse_jsonstream_line_returns_none_for_short():
    assert _parse_jsonstream_line("00:00{}") is None


# ── Lights-out detection (new API: takes list of (abs_ts, topic, dict)) ─────

def _ts_event(ts_str: str, status: str) -> tuple:
    return (_parse_ts(ts_str), "TrackStatus", {"Status": status})


def _rc_event(ts_str: str, message: str) -> tuple:
    return (_parse_ts(ts_str), "RaceControlMessages",
            {"Messages": {"1": {"Message": message, "Flag": "GREEN"}}})


def test_find_lights_out_uses_race_started_message():
    events = [
        _ts_event("00:10:00.000", "1"),   # formation-lap AllClear
        _ts_event("00:28:00.000", "2"),   # yellow during formation lap
        _rc_event("00:32:55.000", "RACE STARTED"),
        _ts_event("00:33:00.000", "1"),   # lights out AllClear
    ]
    rm = ReplayManager()
    result = rm._find_lights_out(events)
    assert result == pytest.approx(1975.0, abs=0.1)


def test_find_lights_out_fallback_to_first_allclear_after_non_clear():
    # No "RACE STARTED" RC message; secondary detection from TrackStatus transitions
    events = [
        _ts_event("00:10:00.000", "1"),   # pre-race AllClear
        _ts_event("00:28:00.000", "2"),   # yellow flag during formation lap
        _ts_event("00:33:00.000", "1"),   # lights-out AllClear (first after non-clear)
        _ts_event("01:05:00.000", "4"),   # SC deployed mid-race
        _ts_event("01:12:00.000", "1"),   # SC end — should NOT be chosen
    ]
    rm = ReplayManager()
    result = rm._find_lights_out(events)
    assert result == pytest.approx(33 * 60, abs=0.1)


def test_find_lights_out_uses_session_status_started():
    # No RC message, no TrackStatus non-clear; SessionStatus "Started" fires at lights-out
    events = [
        _ts_event("00:10:00.000", "1"),
        (_parse_ts("00:32:55.000"), "SessionStatus", {"Status": "Started"}),
        _ts_event("00:33:00.000", "1"),
    ]
    rm = ReplayManager()
    result = rm._find_lights_out(events)
    assert result == pytest.approx(32 * 60 + 55, abs=0.1)


def test_find_lights_out_uses_lapcount_current_lap_1():
    # No RC, no SessionStatus — LapCount CurrentLap=1 fires at lights-out
    events = [
        _ts_event("00:10:00.000", "1"),
        (_parse_ts("00:33:00.000"), "LapCount", {"CurrentLap": 1, "TotalLaps": 44}),
    ]
    rm = ReplayManager()
    result = rm._find_lights_out(events)
    assert result == pytest.approx(33 * 60, abs=0.1)


def test_find_lights_out_non_clear_required_before_allclear():
    # Only AllClear events present — saw_non_clear never set, quaternary fails, returns 0.0
    events = [
        _ts_event("00:10:00.000", "1"),
        _ts_event("00:33:00.000", "1"),
    ]
    rm = ReplayManager()
    result = rm._find_lights_out(events)
    assert result == pytest.approx(0.0)


def test_find_lights_out_returns_zero_when_empty():
    rm = ReplayManager()
    assert rm._find_lights_out([]) == pytest.approx(0.0)


# ── get_sessions (mocked HTTP) ───────────────────────────────────────────────

INDEX_JSON = {
    "Meetings": [
        {
            "Name": "British Grand Prix",
            "Circuit": {"ShortName": "Silverstone"},
            "Sessions": [
                {
                    "Path": "2025/2025-07-04_British_Grand_Prix/2025-07-06_Race/",
                    "Type": "Race",
                    "Name": "Race",
                    "StartDate": "2025-07-06T14:00:00",
                },
                {
                    "Path": "2025/2025-07-04_British_Grand_Prix/2025-07-06_Sprint/",
                    "Type": "Race",
                    "Name": "Sprint",
                    "StartDate": "2025-07-06T11:00:00",
                },
                {
                    "Path": "2025/2025-07-04_British_Grand_Prix/2025-07-05_Qualifying/",
                    "Type": "Qualifying",
                    "Name": "Qualifying",
                    "StartDate": "2025-07-05T14:00:00",
                },
            ],
        }
    ]
}


def _make_index_client(json_data):
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=json_data)
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


@pytest.mark.asyncio
async def test_get_sessions_returns_race_and_sprint():
    with patch("raceflag.replay_manager.httpx.AsyncClient",
               return_value=_make_index_client(INDEX_JSON)):
        sessions = await ReplayManager().get_sessions(year=2025)

    assert len(sessions) == 2
    names = [s["name"] for s in sessions]
    assert "2025 British Grand Prix" in names
    assert "2025 British Grand Prix (Sprint)" in names


@pytest.mark.asyncio
async def test_get_sessions_excludes_qualifying():
    with patch("raceflag.replay_manager.httpx.AsyncClient",
               return_value=_make_index_client(INDEX_JSON)):
        sessions = await ReplayManager().get_sessions(year=2025)

    assert all("Qualifying" not in s["name"] for s in sessions)


@pytest.mark.asyncio
async def test_get_sessions_returns_empty_for_no_race_sessions():
    data = {"Meetings": [{"Name": "Test GP", "Circuit": {"ShortName": "X"},
                          "Sessions": [{"Path": "p/", "Type": "Qualifying",
                                        "Name": "Q", "StartDate": "2025-01-01T00:00:00"}]}]}
    with patch("raceflag.replay_manager.httpx.AsyncClient",
               return_value=_make_index_client(data)):
        sessions = await ReplayManager().get_sessions(year=2025)
    assert sessions == []


# ── load_session (mocked HTTP) ───────────────────────────────────────────────

TS_STREAM = "\n".join([
    '00:10:00.000{"Status":"1"}',   # pre-race AllClear
    '00:28:00.000{"Status":"2"}',   # yellow during formation
    '00:33:00.000{"Status":"1"}',   # lights-out AllClear
    '00:40:00.000{"Status":"4"}',   # safety car
    '00:45:00.000{"Status":"1"}',   # SC end
])

RC_STREAM = '00:32:55.000{"Messages":{"1":{"Message":"RACE STARTED","Flag":"GREEN"}}}\n'


def _make_stream_client(stream_data: dict[str, str]):
    """Return a mock client that serves different text per topic URL substring."""
    async def side_effect(url, **_):
        mock_resp = MagicMock()
        for key, text in stream_data.items():
            if key in url:
                mock_resp.status_code = 200
                mock_resp.text = text
                return mock_resp
        mock_resp.status_code = 404
        mock_resp.text = ""
        return mock_resp

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=side_effect)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


@pytest.mark.asyncio
async def test_load_session_counts_realtime_events():
    mock_ctx = _make_stream_client({
        "TrackStatus": TS_STREAM,
        "RaceControlMessages": RC_STREAM,
    })
    with patch("raceflag.replay_manager.httpx.AsyncClient", return_value=mock_ctx):
        rm = ReplayManager()
        count = await rm.load_session("2025/path/", session_name="2025 British GP")

    assert count > 0
    assert rm._session_name == "2025 British GP"


@pytest.mark.asyncio
async def test_load_session_anchors_events_to_lights_out():
    mock_ctx = _make_stream_client({
        "TrackStatus": TS_STREAM,
        "RaceControlMessages": RC_STREAM,
    })
    with patch("raceflag.replay_manager.httpx.AsyncClient", return_value=mock_ctx):
        rm = ReplayManager()
        await rm.load_session("2025/path/")

    # Lights-out detected from "RACE STARTED" at 00:32:55 = 1975s
    # All real-time events must have race_time >= 0
    realtime = [(rt, t, d) for rt, t, d in rm._events if rt >= 0]
    assert len(realtime) > 0
    assert all(rt >= 0 for rt, _, _ in realtime)

    # Pre-race events must have race_time < 0
    pre_race = [(rt, t, d) for rt, t, d in rm._events if rt < 0]
    assert len(pre_race) > 0


@pytest.mark.asyncio
async def test_load_session_events_sorted_chronologically():
    mock_ctx = _make_stream_client({
        "TrackStatus": TS_STREAM,
        "RaceControlMessages": RC_STREAM,
    })
    with patch("raceflag.replay_manager.httpx.AsyncClient", return_value=mock_ctx):
        rm = ReplayManager()
        await rm.load_session("2025/path/")

    times = [rt for rt, _, _ in rm._events]
    assert times == sorted(times)


# ── Playback engine (uses on_feed for new API, on_event for legacy) ──────────

@pytest.mark.asyncio
async def test_play_fires_on_feed_in_order():
    received = []
    rm = ReplayManager(on_feed=lambda t, d, s: received.append((t, s)))
    rm._events = [
        (0.0, "TrackStatus", {"Status": "1"}),
        (0.05, "WeatherData", {"AirTemp": "25"}),
        (0.1, "TrackStatus", {"Status": "2"}),
    ]
    await rm.play()
    await asyncio.sleep(0.3)
    rm.stop()
    assert received == [
        ("TrackStatus", False),
        ("WeatherData", False),
        ("TrackStatus", False),
    ]


@pytest.mark.asyncio
async def test_play_snapshot_phase_fires_with_is_snapshot_true():
    received = []
    rm = ReplayManager(on_feed=lambda t, d, s: received.append(s))
    rm._events = [
        (-10.0, "TrackStatus", {"Status": "1"}),  # pre-race (snapshot)
        (0.0, "TrackStatus", {"Status": "1"}),     # at lights-out (real-time)
        (0.05, "TrackStatus", {"Status": "2"}),    # after lights-out (real-time)
    ]
    await rm.play()
    await asyncio.sleep(0.3)
    rm.stop()
    # First event is snapshot (True), rest are real-time (False)
    assert received[0] is True
    assert received[1] is False
    assert received[2] is False


@pytest.mark.asyncio
async def test_play_legacy_on_event_fires_for_track_status_only():
    received = []
    rm = ReplayManager()  # no on_feed
    rm._events = [
        (0.0, "TrackStatus", {"Status": "2"}),     # yellow_flag → on_event fires
        (0.05, "WeatherData", {"AirTemp": "25"}),  # no on_event
    ]
    await rm.play(on_event=received.append)
    await asyncio.sleep(0.3)
    rm.stop()
    assert received == ["yellow_flag"]


@pytest.mark.asyncio
async def test_pause_stops_event_delivery():
    received = []
    rm = ReplayManager(on_feed=lambda t, d, s: received.append(t))
    rm._events = [
        (0.0, "TrackStatus", {"Status": "1"}),
        (0.15, "TrackStatus", {"Status": "2"}),
    ]
    await rm.play()
    await asyncio.sleep(0.02)
    rm.pause()
    await asyncio.sleep(0.3)
    rm.stop()
    assert received == ["TrackStatus"]  # only first event fired


@pytest.mark.asyncio
async def test_resume_continues_from_paused_position():
    received = []
    rm = ReplayManager(on_feed=lambda t, d, s: received.append(t))
    rm._events = [
        (0.0, "TrackStatus", {"Status": "1"}),
        (0.15, "TrackStatus", {"Status": "2"}),
    ]
    await rm.play()
    await asyncio.sleep(0.02)
    rm.pause()
    await asyncio.sleep(0.1)
    rm.resume()
    await asyncio.sleep(0.3)
    rm.stop()
    assert received == ["TrackStatus", "TrackStatus"]


@pytest.mark.asyncio
async def test_stop_cancels_playback():
    received = []
    rm = ReplayManager(on_feed=lambda t, d, s: received.append(t))
    rm._events = [
        (0.0, "TrackStatus", {"Status": "1"}),
        (10.0, "TrackStatus", {"Status": "2"}),
    ]
    await rm.play()
    await asyncio.sleep(0.02)
    rm.stop()
    await asyncio.sleep(0.05)
    assert received == ["TrackStatus"]
    assert rm._task is None


def test_set_sync_offset_clamps_to_range():
    rm = ReplayManager()
    rm.set_sync_offset(50.0)
    assert rm._sync_offset == 30.0
    rm.set_sync_offset(-50.0)
    assert rm._sync_offset == -30.0
    rm.set_sync_offset(10.0)
    assert rm._sync_offset == 10.0


def test_stop_clears_events_and_name():
    rm = ReplayManager()
    rm._events = [(0.0, "TrackStatus", {"Status": "1"})]
    rm._session_name = "Test GP"
    rm.stop()
    assert rm._events == []
    assert rm._session_name == ""


def test_stop_resets_sync_offset():
    rm = ReplayManager()
    rm.set_sync_offset(15.0)
    assert rm._sync_offset == 15.0
    rm.stop()
    assert rm._sync_offset == 0.0
