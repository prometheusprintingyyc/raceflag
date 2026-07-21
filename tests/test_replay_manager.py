import asyncio
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from raceflag.replay_manager import ReplayManager, _parse_ts, _parse_jsonstream_line


# ── Pure helper tests (no HTTP) ───────────────────────────────────────────────

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


# ── Lights-out detection ──────────────────────────────────────────────────────

def test_find_lights_out_uses_race_started_message():
    ts_lines = [
        '00:10:00.000{"Status":"1"}',
        '00:35:00.000{"Status":"1"}',
    ]
    rc_lines = [
        '00:32:55.000{"Messages":{"1":{"Message":"RACE STARTED","Flag":"GREEN"}}}',
    ]
    rm = ReplayManager()
    result = rm._find_lights_out(ts_lines, rc_lines)
    assert result == pytest.approx(1975.0, abs=0.1)


def test_find_lights_out_fallback_to_first_allclear_after_gap():
    # No "RACE STARTED" in RC messages; formation lap gap >= 5 min before second AllClear
    ts_lines = [
        '00:00:00.000{"Status":"1"}',   # warm-up AllClear at T=0
        '00:28:00.000{"Status":"1"}',   # formation lap ends — gap = 28 min >= 5 min
    ]
    rc_lines = []  # no RACE STARTED
    rm = ReplayManager()
    result = rm._find_lights_out(ts_lines, rc_lines)
    assert result == pytest.approx(28 * 60, abs=0.1)


def test_find_lights_out_returns_zero_when_no_marker():
    rm = ReplayManager()
    result = rm._find_lights_out([], [])
    assert result == pytest.approx(0.0)


# ── get_sessions (mocked HTTP) ────────────────────────────────────────────────

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
                    "Path": "2025/2025-07-04_British_Grand_Prix/2025-07-05_Qualifying/",
                    "Type": "Qualifying",
                    "Name": "Qualifying",
                    "StartDate": "2025-07-05T14:00:00",
                },
            ],
        }
    ]
}


def _make_mock_client(json_data=None, text_data=None):
    """Return a mock httpx.AsyncClient context manager."""
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    if json_data is not None:
        mock_resp.json = MagicMock(return_value=json_data)
    if text_data is not None:
        mock_resp.text = text_data
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


@pytest.mark.asyncio
async def test_get_sessions_returns_race_sessions_only():
    with patch("raceflag.replay_manager.httpx.AsyncClient", return_value=_make_mock_client(json_data=INDEX_JSON)):
        rm = ReplayManager()
        sessions = await rm.get_sessions(year=2025)

    assert len(sessions) == 1  # only Race, not Qualifying
    s = sessions[0]
    assert s["name"] == "2025 British Grand Prix"
    assert s["path"] == "2025/2025-07-04_British_Grand_Prix/2025-07-06_Race/"
    assert s["date"] == "2025-07-06"
    assert s["circuit"] == "Silverstone"


@pytest.mark.asyncio
async def test_get_sessions_returns_empty_for_no_race_sessions():
    data = {"Meetings": [{"Name": "Test GP", "Circuit": {"ShortName": "X"},
                           "Sessions": [{"Path": "p/", "Type": "Qualifying",
                                         "Name": "Q", "StartDate": "2025-01-01T00:00:00"}]}]}
    with patch("raceflag.replay_manager.httpx.AsyncClient", return_value=_make_mock_client(json_data=data)):
        rm = ReplayManager()
        sessions = await rm.get_sessions(year=2025)
    assert sessions == []


# ── load_session (mocked HTTP) ───────────────────────────────────────────────

TS_STREAM = "\n".join([
    '00:29:00.000{"Status":"1"}',
    '00:32:55.000{"Status":"2"}',
    '00:35:00.000{"Status":"4"}',
    '00:40:00.000{"Status":"1"}',
])

RC_STREAM = '00:32:55.000{"Messages":{"1":{"Message":"RACE STARTED","Flag":"GREEN"}}}\n'


@pytest.mark.asyncio
async def test_load_session_parses_events_after_lights_out():
    def side_effect(url, timeout=None):
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        if "TrackStatus" in url:
            mock_resp.text = TS_STREAM
        else:
            mock_resp.text = RC_STREAM
        return mock_resp

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=side_effect)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("raceflag.replay_manager.httpx.AsyncClient", return_value=mock_ctx):
        rm = ReplayManager()
        count = await rm.load_session("2025/some_path/", session_name="2025 British GP")

    # race_start at T=0, then yellow_flag at ~0s after lights out,
    # then safety_car at ~125s, then track_clear at ~425s
    assert count > 0
    assert rm._session_name == "2025 British GP"
    assert rm._events[0] == pytest.approx((0.0, "race_start"), abs=0.1)
    # all events at t >= 0
    assert all(t >= 0 for t, _ in rm._events)


# ── Playback engine ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_play_fires_events_in_order():
    rm = ReplayManager()
    rm._events = [(0.0, "race_start"), (0.05, "yellow_flag"), (0.1, "track_clear")]
    received = []
    await rm.play(on_event=received.append)
    await asyncio.sleep(0.3)
    rm.stop()
    assert received == ["race_start", "yellow_flag", "track_clear"]


@pytest.mark.asyncio
async def test_pause_stops_event_delivery():
    rm = ReplayManager()
    rm._events = [(0.0, "race_start"), (0.15, "yellow_flag")]
    received = []
    await rm.play(on_event=received.append)
    await asyncio.sleep(0.02)
    rm.pause()
    await asyncio.sleep(0.3)  # yellow_flag would fire here if not paused
    rm.stop()
    assert received == ["race_start"]
    assert "yellow_flag" not in received


@pytest.mark.asyncio
async def test_resume_continues_from_same_position():
    rm = ReplayManager()
    rm._events = [(0.0, "race_start"), (0.15, "yellow_flag")]
    received = []
    await rm.play(on_event=received.append)
    await asyncio.sleep(0.02)
    rm.pause()
    await asyncio.sleep(0.1)
    rm.resume()
    await asyncio.sleep(0.3)
    rm.stop()
    assert "race_start" in received
    assert "yellow_flag" in received


@pytest.mark.asyncio
async def test_stop_cancels_playback():
    rm = ReplayManager()
    rm._events = [(0.0, "race_start"), (10.0, "yellow_flag")]
    received = []
    await rm.play(on_event=received.append)
    await asyncio.sleep(0.02)
    rm.stop()
    await asyncio.sleep(0.05)
    assert received == ["race_start"]
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
    rm._events = [(0.0, "race_start")]
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
