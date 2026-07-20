# Replay Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Replay tab to RaceFlag that lets users select a past F1 race session, sync the LED strip to their TV broadcast at lights-out, and experience the same flag animations as live mode.

**Architecture:** A new `ReplayManager` class fetches and parses F1 livetiming `.jsonStream` files and fires the same `on_flag_change` callback used by `F1Listener`. While replay is active, `F1Listener.suspended` suppresses live callbacks. `AppState` gains four new replay fields returned by the existing `/api/state` poll. The existing LED delay slider is repurposed as a ±30 s "Sync Offset" control when in Replay mode. The frontend adds a Replay tab (3-option toggle) and a context-sensitive selector bar.

**Tech Stack:** Python 3.11, asyncio, httpx (already in requirements), FastAPI/Pydantic (existing), pytest-asyncio (already in dev requirements), vanilla JS/HTML/CSS (no new frontend packages).

## Global Constraints

- Phase 1: Race sessions only (`Type == "Race"`), current year (2025)
- Sync offset: −30 to +30 seconds, default 0.0
- All new API endpoints under `/api/replay/*`
- HTTP client: `httpx.AsyncClient` with `timeout=15.0`
- Toggle pill: 3 equal options, width `calc(33.33% - 2px)`
- Replay bar and REPLAY pill appear only when `replay_mode == True` in `/api/state`
- CHANGELOG.md `[Unreleased]` must be updated with every code change
- Git: `prometheusprintingyyc` as author; always add `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`

---

## File Map

| Action | File | Responsibility |
|---|---|---|
| Create | `raceflag/replay_manager.py` | Session discovery, stream parsing, playback engine |
| Modify | `raceflag/state.py` | Four new `replay_*` fields + `set_replay_state()` |
| Modify | `raceflag/f1_listener.py` | `suspended: bool` flag |
| Modify | `raceflag/web_server.py` | Six new `/api/replay/*` endpoints |
| Modify | `raceflag/main.py` | Instantiate `ReplayManager`, pass to `create_app`, wire callback |
| Modify | `raceflag/frontend/index.html` | 3-option toggle; replay bar (3 layout rows); REPLAY pill |
| Modify | `raceflag/frontend/style.css` | Toggle pill width; 3 pill positions; replay bar styling |
| Modify | `raceflag/frontend/app.js` | Replay tab logic; session fetch; controls; slider dual-mode |
| Create | `tests/test_replay_manager.py` | Data layer + playback tests |
| Modify | `tests/test_web_server.py` | Tests for all 6 new endpoints |
| Modify | `tests/test_state.py` | Tests for new replay fields and setter |
| Modify | `CHANGELOG.md` | `[Unreleased]` section |

---

### Task 1: AppState replay fields

**Files:**
- Modify: `raceflag/state.py`
- Modify: `tests/test_state.py`

**Interfaces:**
- Produces: `AppState.replay_mode: bool`, `AppState.replay_status: str`, `AppState.replay_session_name: str`, `AppState.replay_time_elapsed: str`, `AppState.set_replay_state(mode, status, session_name, elapsed)`
- Produces: `to_dict()` now includes `replay_mode`, `replay_status`, `replay_session_name`, `replay_time_elapsed`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_state.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_state.py::test_replay_fields_default tests/test_state.py::test_set_replay_state_updates_all_fields -v`

Expected: FAIL with `AttributeError: 'AppState' object has no attribute 'replay_mode'`

- [ ] **Step 3: Add replay fields to `AppState` in `raceflag/state.py`**

Add four new fields after `feed_connected: bool = False` (around line 135):

```python
    feed_connected: bool = False
    replay_mode: bool = False
    replay_status: str = "idle"   # "idle" | "loading" | "ready" | "playing" | "paused" | "complete"
    replay_session_name: str = ""
    replay_time_elapsed: str = ""
```

Add setter after `set_feed_connected` (around line 163):

```python
    def set_replay_state(
        self,
        mode: bool,
        status: str,
        session_name: str = "",
        elapsed: str = "",
    ) -> None:
        with self._lock:
            self.replay_mode = mode
            self.replay_status = status
            self.replay_session_name = session_name
            self.replay_time_elapsed = elapsed
```

Add four entries to `to_dict()` return dict (inside the `with self._lock:` block, after `"feed_connected"`):

```python
                "replay_mode": self.replay_mode,
                "replay_status": self.replay_status,
                "replay_session_name": self.replay_session_name,
                "replay_time_elapsed": self.replay_time_elapsed,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_state.py -v`

Expected: All pass.

- [ ] **Step 5: Commit**

```
git add raceflag/state.py tests/test_state.py
git commit -m "feat: add replay_mode/status/session_name/elapsed fields to AppState"
```

---

### Task 2: F1Listener suspended flag

**Files:**
- Modify: `raceflag/f1_listener.py`
- Modify: `tests/test_f1_listener.py`

**Interfaces:**
- Consumes: nothing new — modifies existing `F1Listener` class
- Produces: `F1Listener.suspended: bool = False` — when `True`, `_handle_feed` returns without calling callbacks

- [ ] **Step 1: Write the failing test**

Add to `tests/test_f1_listener.py`:

```python
def test_suspended_flag_suppresses_callbacks():
    received = []
    state = AppState()
    listener = F1Listener(state=state, on_track_status_change=received.append)
    listener.suspended = True
    # Simulate an incoming TrackStatus update
    listener._handle_feed("TrackStatus", {"Status": "2"}, is_snapshot=False)
    assert received == [], "callback must not fire while suspended"


def test_suspended_false_allows_callbacks():
    received = []
    state = AppState()
    listener = F1Listener(state=state, on_track_status_change=received.append)
    listener.suspended = False
    listener._handle_feed("TrackStatus", {"Status": "2"}, is_snapshot=False)
    assert "yellow_flag" in received
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_f1_listener.py::test_suspended_flag_suppresses_callbacks tests/test_f1_listener.py::test_suspended_false_allows_callbacks -v`

Expected: `test_suspended_flag_suppresses_callbacks` FAILS (callback fires even when suspended).

- [ ] **Step 3: Add `suspended` flag to `F1Listener`**

In `raceflag/f1_listener.py`, find the `__init__` method and add after the existing `self.on_track_status_change` assignment:

```python
        self.suspended: bool = False
```

Find the `_handle_feed` method and add a guard at the very start of the method body (before any other logic):

```python
    def _handle_feed(self, topic: str, data, is_snapshot: bool = False) -> None:
        if self.suspended:
            return
        # ... rest of existing body unchanged ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_f1_listener.py -v`

Expected: All pass.

- [ ] **Step 5: Commit**

```
git add raceflag/f1_listener.py tests/test_f1_listener.py
git commit -m "feat: add suspended flag to F1Listener to suppress callbacks during replay"
```

---

### Task 3: ReplayManager — data layer

**Files:**
- Create: `raceflag/replay_manager.py`
- Create: `tests/test_replay_manager.py`

**Interfaces:**
- Produces: `ReplayManager` class with:
  - `async get_sessions(year: int = 2025) -> list[dict]` — each dict has `name`, `path`, `date`, `circuit`
  - `async load_session(path: str, session_name: str = "") -> int` — returns event count
  - `_events: list[tuple[float, str]]` — (race_time_seconds, flag_state) pairs, sorted by race time
  - `_session_name: str` — display name set by `load_session`
- Task 4 will add `play`, `pause`, `resume`, `stop`, `set_sync_offset` to the same class

**F1 livetiming data format:**
- `GET https://livetiming.formula1.com/static/{year}/Index.json` → `{"Meetings": [{"Name": str, "Circuit": {"ShortName": str}, "Sessions": [{"Path": str, "Type": str, "Name": str, "StartDate": str}]}]}`
- `.jsonStream` lines: `HH:MM:SS.MMM{JSON}` — first 12 chars are timestamp, rest is a JSON object
- `TrackStatus.jsonStream` JSON: `{"Status": "1"|"2"|"4"|"5"|"6"|"7"}` — maps to flag states via `TRACK_STATUS_MAP`
- Lights-out detection: scan `RaceControlMessages.jsonStream` for a line whose JSON contains `"RACE STARTED"` anywhere (most reliable); fallback is the first `Status == "1"` in TrackStatus after a ≥ 5 min gap from the previous entry

- [ ] **Step 1: Write the failing tests**

Create `tests/test_replay_manager.py`:

```python
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
    # No "RACE STARTED" in RC messages; formation lap gap ≥ 5 min before second AllClear
    ts_lines = [
        '00:00:00.000{"Status":"1"}',   # warm-up AllClear at T=0
        '00:28:00.000{"Status":"1"}',   # formation lap ends — gap = 28 min ≥ 5 min
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_replay_manager.py -v`

Expected: `ModuleNotFoundError: No module named 'raceflag.replay_manager'`

- [ ] **Step 3: Create `raceflag/replay_manager.py` with data layer**

```python
from __future__ import annotations

import json
import logging
from typing import Callable

import httpx

from raceflag.state import TRACK_STATUS_MAP

logger = logging.getLogger(__name__)

BASE_URL = "https://livetiming.formula1.com/static"


def _parse_ts(ts: str) -> float:
    """Parse HH:MM:SS.mmm into total seconds."""
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _parse_jsonstream_line(line: str) -> tuple[float, dict] | None:
    """Parse one .jsonStream line into (seconds, payload). Returns None on bad input."""
    line = line.strip()
    if not line or len(line) < 13:
        return None
    try:
        ts = _parse_ts(line[:12])
        payload = json.loads(line[12:])
        return ts, payload
    except (ValueError, json.JSONDecodeError):
        return None


class ReplayManager:
    def __init__(self) -> None:
        self._events: list[tuple[float, str]] = []  # (race_time_seconds, flag_state)
        self._session_name: str = ""
        # Playback state (populated by Task 4)
        self._play_wall_origin: float = 0.0
        self._paused: bool = False
        self._pause_wall: float = 0.0
        self._sync_offset: float = 0.0
        self._task = None
        self._on_event: Callable[[str], None] | None = None

    async def get_sessions(self, year: int = 2025) -> list[dict]:
        """Fetch Index.json fresh and return Race sessions only."""
        url = f"{BASE_URL}/{year}/Index.json"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        sessions = []
        for meeting in data.get("Meetings", []):
            meeting_name = meeting.get("Name", "")
            circuit = meeting.get("Circuit", {}).get("ShortName", "")
            for session in meeting.get("Sessions", []):
                if session.get("Type") != "Race":
                    continue
                start = session.get("StartDate", "")
                year_str = start[:4] if start else str(year)
                sessions.append({
                    "name": f"{year_str} {meeting_name}",
                    "path": session.get("Path", ""),
                    "date": start[:10],
                    "circuit": circuit,
                })
        return sessions

    async def load_session(self, path: str, session_name: str = "") -> int:
        """Fetch TrackStatus + RaceControlMessages streams, parse events, return event count."""
        base = f"{BASE_URL}/{path}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            ts_resp = await client.get(base + "TrackStatus.jsonStream")
            rc_resp = await client.get(base + "RaceControlMessages.jsonStream")
            ts_resp.raise_for_status()
            rc_resp.raise_for_status()

        ts_lines = ts_resp.text.strip().splitlines()
        rc_lines = rc_resp.text.strip().splitlines()

        lights_out = self._find_lights_out(ts_lines, rc_lines)
        logger.info("Replay lights-out offset: %.3fs", lights_out)

        events: list[tuple[float, str]] = [(0.0, "race_start")]
        for line in ts_lines:
            parsed = _parse_jsonstream_line(line)
            if parsed is None:
                continue
            abs_ts, payload = parsed
            race_time = abs_ts - lights_out
            if race_time <= 0:
                continue
            flag_state = TRACK_STATUS_MAP.get(str(payload.get("Status", "")))
            if flag_state:
                events.append((race_time, flag_state))

        events.sort(key=lambda x: x[0])
        self._events = events
        self._session_name = session_name or path
        return len(events)

    def _find_lights_out(self, ts_lines: list[str], rc_lines: list[str]) -> float:
        """Return the absolute session timestamp of race lights-out (seconds)."""
        # Primary: "RACE STARTED" anywhere in a RaceControlMessages line
        for line in rc_lines:
            parsed = _parse_jsonstream_line(line)
            if parsed is None:
                continue
            ts, payload = parsed
            if "RACE STARTED" in json.dumps(payload):
                return ts

        # Fallback: first AllClear after a ≥ 5 min gap (end of formation lap)
        prev_ts = 0.0
        for line in ts_lines:
            parsed = _parse_jsonstream_line(line)
            if parsed is None:
                continue
            ts, payload = parsed
            if str(payload.get("Status", "")) == "1" and (ts - prev_ts) >= 300:
                return ts
            prev_ts = ts

        return 0.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_replay_manager.py -v`

Expected: All pass.

- [ ] **Step 5: Commit**

```
git add raceflag/replay_manager.py tests/test_replay_manager.py
git commit -m "feat: add ReplayManager data layer (get_sessions, load_session, lights-out detection)"
```

---

### Task 4: ReplayManager — playback engine

**Files:**
- Modify: `raceflag/replay_manager.py` (add playback methods)
- Modify: `tests/test_replay_manager.py` (add playback tests)

**Interfaces:**
- Consumes: `ReplayManager._events` and `ReplayManager._session_name` from Task 3
- Produces: `async play(on_event)`, `pause()`, `resume()`, `stop()`, `set_sync_offset(seconds)`
- When paused and then resumed, `_play_wall_origin` is shifted forward by the pause duration so the replay clock stays frozen at the same race position during the pause.

**Pause/resume clock math:**
```
_play_wall_origin = wall time at which race-time 0.0 (lights-out) occurred
target_wall_for_event(race_time) = _play_wall_origin + race_time + _sync_offset

On pause():  _pause_wall = time.monotonic()
On resume(): _play_wall_origin += (time.monotonic() - _pause_wall)
             → this shifts the origin forward by the pause duration,
               so (target_wall - now) stays unchanged for upcoming events
```

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_replay_manager.py`:

```python
import asyncio
import time


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_replay_manager.py::test_play_fires_events_in_order tests/test_replay_manager.py::test_stop_clears_events_and_name -v`

Expected: FAIL with `AttributeError` (play/stop not defined yet).

- [ ] **Step 3: Add playback methods to `raceflag/replay_manager.py`**

Add `import asyncio` and `import time` to the top of the file (after `from __future__ import annotations`).

Replace the `# Playback state (populated by Task 4)` placeholder block and add these methods to `ReplayManager`:

```python
    async def play(self, on_event: Callable[[str], None]) -> None:
        """Start playback from the beginning of the loaded events."""
        self._on_event = on_event
        self._paused = False
        self._play_wall_origin = time.monotonic()
        self._task = asyncio.create_task(self._playback_loop())

    async def _playback_loop(self) -> None:
        for race_time, flag_state in self._events:
            while True:
                if self._paused:
                    await asyncio.sleep(0.05)
                    continue
                target = self._play_wall_origin + race_time + self._sync_offset
                remaining = target - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(remaining, 0.05))
            if self._on_event:
                self._on_event(flag_state)

    def pause(self) -> None:
        if not self._paused:
            self._pause_wall = time.monotonic()
            self._paused = True

    def resume(self) -> None:
        if self._paused:
            # Shift wall origin forward by the pause duration so race position stays frozen
            self._play_wall_origin += time.monotonic() - self._pause_wall
            self._paused = False

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        self._paused = False
        self._events = []
        self._session_name = ""

    def set_sync_offset(self, seconds: float) -> None:
        self._sync_offset = max(-30.0, min(30.0, float(seconds)))
```

Also update `__init__` to remove the comment placeholder:

```python
    def __init__(self) -> None:
        self._events: list[tuple[float, str]] = []
        self._session_name: str = ""
        self._play_wall_origin: float = 0.0
        self._paused: bool = False
        self._pause_wall: float = 0.0
        self._sync_offset: float = 0.0
        self._task: asyncio.Task | None = None
        self._on_event: Callable[[str], None] | None = None
```

- [ ] **Step 4: Run all replay manager tests**

Run: `pytest tests/test_replay_manager.py -v`

Expected: All pass.

- [ ] **Step 5: Commit**

```
git add raceflag/replay_manager.py tests/test_replay_manager.py
git commit -m "feat: add ReplayManager playback engine (play, pause, resume, stop, offset)"
```

---

### Task 5: Web server endpoints

**Files:**
- Modify: `raceflag/web_server.py`
- Modify: `tests/test_web_server.py`

**Interfaces:**
- Consumes: `ReplayManager` from Tasks 3+4, `AppState.set_replay_state()` from Task 1, `F1Listener.suspended` from Task 2
- Produces six new endpoints:

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/api/replay/sessions` | — | `[{"name", "path", "date", "circuit"}, …]` |
| `POST` | `/api/replay/load` | `{"session_path": str, "session_name": str}` | `{"status": "ready", "session_name": str, "event_count": int}` |
| `POST` | `/api/replay/play` | — | `{"status": "playing"}` |
| `POST` | `/api/replay/pause` | — | `{"status": "paused"}` |
| `POST` | `/api/replay/stop` | — | `{"status": "idle"}` |
| `POST` | `/api/replay/offset` | `{"seconds": float}` | `{"offset_seconds": float}` |

- `create_app()` gains a new optional param `replay_manager=None`; when not `None`, the 6 endpoints are active.
- `play` sets `listener.suspended = True` (listener is passed to `create_app`)
- `stop` sets `listener.suspended = False`

**Note:** Task 6 (main.py) wires the real listener. For tests, pass `listener=None`; the endpoints still work but won't toggle `.suspended`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_web_server.py`. First check what fixtures exist — the file uses `app_state`, `config`, `led`, and `client` fixtures with `TestClient(create_app(...))`. Add:

```python
from unittest.mock import AsyncMock, MagicMock
from raceflag.replay_manager import ReplayManager


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
    from starlette.testclient import TestClient
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
    # Need events loaded first
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


def test_replay_endpoints_absent_without_replay_manager(client):
    # The bare `client` fixture has no replay_manager
    assert client.get("/api/replay/sessions").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web_server.py::test_get_replay_sessions tests/test_web_server.py::test_post_replay_stop -v`

Expected: FAIL — `create_app` doesn't accept `replay_manager` yet.

- [ ] **Step 3: Add Pydantic models and endpoints to `raceflag/web_server.py`**

Add new Pydantic models after the existing model classes (before `create_app`):

```python
class LoadSessionRequest(BaseModel):
    session_path: str
    session_name: str = ""


class ReplayOffsetRequest(BaseModel):
    seconds: float = Field(ge=-30.0, le=30.0)
```

Add `replay_manager=None` and `listener=None` to `create_app` signature:

```python
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
) -> FastAPI:
```

Add the six replay endpoints inside `create_app`, after the existing `@app.post("/api/led/enabled")` block, gated on `replay_manager is not None`:

```python
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
            await replay_manager.play(on_event=state.set_track_status)
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
```

- [ ] **Step 4: Fix the pause/resume test — the spec uses `/api/replay/pause` to cover both pause and resume via the `_paused` toggle. Update the play test: since `replay_manager.play` is an `AsyncMock`, the `await` in the endpoint works correctly. Verify the test for `test_post_replay_play` passes. If `play` is an `AsyncMock`, calling `await replay_manager.play(...)` in the endpoint returns immediately.**

- [ ] **Step 5: Run all web server tests**

Run: `pytest tests/test_web_server.py -v`

Expected: All pass.

- [ ] **Step 6: Commit**

```
git add raceflag/web_server.py tests/test_web_server.py
git commit -m "feat: add six /api/replay/* endpoints to web server"
```

---

### Task 6: main.py wiring

**Files:**
- Modify: `raceflag/main.py`

**Interfaces:**
- Consumes: `ReplayManager` from Tasks 3+4, `create_app` from Task 5 (updated signature)
- No new tests — this is pure wiring; existing integration is covered by other tests

The `on_flag_change` callback in `main.py` is already the right signature for `replay_manager.play(on_event=on_flag_change)`. The replay playback fires `on_flag_change(status)` for each event, which drives `LEDController` and `AppState.set_display_track_status` through the existing delay path — identical to live mode.

- [ ] **Step 1: Import `ReplayManager` in `raceflag/main.py`**

Add after the existing imports:

```python
from raceflag.replay_manager import ReplayManager
```

- [ ] **Step 2: Instantiate `ReplayManager` and pass it to `create_app`**

In `main()`, after `listener = F1Listener(...)`:

```python
    replay = ReplayManager()
```

Update the `create_app` call:

```python
    app = create_app(
        state=state,
        config=config,
        led=led,
        config_path=CONFIG_PATH,
        wifi_manager=wifi,
        ota=ota,
        version=current_version,
        replay_manager=replay,
        listener=listener,
    )
```

- [ ] **Step 3: Run the existing test suite to verify no regressions**

Run: `pytest -v`

Expected: All tests pass.

- [ ] **Step 4: Commit**

```
git add raceflag/main.py
git commit -m "feat: wire ReplayManager into main.py and create_app"
```

---

### Task 7: Frontend HTML + CSS

**Files:**
- Modify: `raceflag/frontend/index.html`
- Modify: `raceflag/frontend/style.css`

**Interfaces:**
- Consumes: existing toggle HTML (lines 31–36 of index.html), existing CSS classes `view-toggle`, `view-toggle-pill`, `view-btn`
- Produces:
  - 3-option toggle with ids `btn-live`, `btn-replay`, `btn-standings`
  - Replay bar `#replay-bar` with three inner rows (idle, ready, playback)
  - CSS for 3-position pill, replay bar, replay pill badge
  - Slider label element `#delay-label` (currently a div with class `delay-label` — promote to have an id)

No tests for HTML/CSS. Verification is visual + Task 8 JS that references these IDs.

- [ ] **Step 1: Update the view toggle in `index.html`**

Replace lines 31–36 (the `<div class="view-toggle">` block):

**Before:**
```html
  <div class="view-toggle">
    <div class="view-toggle-pill" id="view-toggle-pill"></div>
    <button class="view-btn active" id="btn-live">Live Race</button>
    <button class="view-btn" id="btn-standings">Standings</button>
  </div>
  <div class="auto-label">Switches automatically · Manual override above</div>
```

**After:**
```html
  <div class="view-toggle">
    <div class="view-toggle-pill" id="view-toggle-pill"></div>
    <button class="view-btn active" id="btn-live">Live Race</button>
    <button class="view-btn" id="btn-replay">Replay</button>
    <button class="view-btn" id="btn-standings">Standings</button>
  </div>
  <div class="auto-label" id="auto-label">Switches automatically · Manual override above</div>
```

- [ ] **Step 2: Add the replay selector bar and REPLAY pill in `index.html`**

Add the replay bar directly after the `<div class="auto-label"...>` line and before `<div class="view active" id="view-live">`:

```html
  <!-- Replay selector bar — shown only when Replay tab is active -->
  <div class="replay-bar" id="replay-bar" style="display:none">
    <!-- Row A-idle: shown in idle state -->
    <div class="replay-idle-row" id="replay-idle-row">
      <select class="replay-dropdown" id="replay-dropdown">
        <option value="">Loading races…</option>
      </select>
      <button class="btn-replay-action" id="btn-replay-load" disabled>Load</button>
    </div>
    <!-- Row A-ready: shown when session is loaded, not yet playing -->
    <div class="replay-ready-row" id="replay-ready-row" style="display:none">
      <div class="replay-race-chip" id="replay-race-chip"></div>
      <button class="btn-replay-action" id="btn-replay-play">▶ Play</button>
    </div>
    <!-- Row B: shown when playing or paused -->
    <div class="replay-playback-row" id="replay-playback-row" style="display:none">
      <button class="btn-replay-action flex1" id="btn-replay-pause">⏸ Pause</button>
      <button class="btn-replay-action flex1" id="btn-replay-stop">■ Stop</button>
    </div>
  </div>
```

Add the `REPLAY` pill to the Session section title. Find this line in `index.html`:

```html
    <div class="section-title">Session</div>
```

Replace with:

```html
    <div class="section-title">Session <span class="replay-pill" id="session-replay-pill" style="display:none">REPLAY</span></div>
```

Give the delay label an id so JS can update its text. Find:

```html
      <div><div class="delay-label">LED Delay</div></div>
```

Replace with:

```html
      <div><div class="delay-label" id="delay-label">LED Delay</div></div>
```

- [ ] **Step 3: Update toggle pill CSS in `style.css`**

Find the current toggle pill rules (around line 28–44) and update:

**Before:**
```css
.view-toggle-pill {
  position: absolute; top: 3px; left: 3px;
  width: calc(50% - 3px); height: calc(100% - 6px);
  ...
}
.view-toggle-pill.right { transform: translateX(100%); }
```

**After:**
```css
.view-toggle-pill {
  position: absolute; top: 3px; left: 3px;
  width: calc(33.33% - 2px); height: calc(100% - 6px);
  ...
}
.view-toggle-pill.centre { transform: translateX(calc(100% + 2px)); }
.view-toggle-pill.right  { transform: translateX(calc(200% + 4px)); }
```

(Keep all other properties — `background`, `border-radius`, `transition`, `pointer-events` — unchanged.)

- [ ] **Step 4: Add replay bar and replay pill CSS to `style.css`**

Append at the end of `style.css`:

```css
/* ── Replay bar ───────────────────────────────────────────────────────────── */
.replay-bar { margin: 8px 12px 0; display: flex; flex-direction: column; gap: 8px; }
.replay-idle-row,
.replay-ready-row,
.replay-playback-row { display: flex; gap: 8px; align-items: center; }
.replay-playback-row { display: none; }

.replay-dropdown {
  flex: 1; padding: 8px 10px; border-radius: 6px; font-size: 13px;
  background: #1a1a1a; border: 1px solid #333; color: #eee;
}
.replay-race-chip {
  flex: 1; padding: 8px 12px; border-radius: 6px; font-size: 13px;
  background: #1a1a1a; border: 1px solid #333; color: #eee; overflow: hidden;
  white-space: nowrap; text-overflow: ellipsis;
}
.btn-replay-action {
  padding: 8px 16px; border-radius: 6px; font-size: 12px; font-weight: 600;
  background: #e10600; color: #fff; border: none; cursor: pointer;
  white-space: nowrap;
}
.btn-replay-action:disabled { background: #333; color: #666; cursor: not-allowed; }
.btn-replay-action.flex1 { flex: 1; }
.btn-replay-action.secondary { background: #2a2a2a; color: #aaa; border: 1px solid #444; }

/* ── Replay pill badge (on section titles) ───────────────────────────────── */
.replay-pill {
  font-size: 9px; font-weight: 700; letter-spacing: 1px;
  background: #e10600; color: #fff; border-radius: 3px;
  padding: 2px 5px; vertical-align: middle; margin-left: 6px;
}
```

- [ ] **Step 5: Verify the HTML is valid and IDs are consistent**

Check that every ID referenced by Task 8's JS plan exists in index.html:
- `btn-replay`, `btn-live`, `btn-standings`, `view-toggle-pill`
- `auto-label`, `replay-bar`, `replay-idle-row`, `replay-ready-row`, `replay-playback-row`
- `replay-dropdown`, `btn-replay-load`, `replay-race-chip`, `btn-replay-play`
- `btn-replay-pause`, `btn-replay-stop`
- `session-replay-pill`, `delay-label`

- [ ] **Step 6: Commit**

```
git add raceflag/frontend/index.html raceflag/frontend/style.css
git commit -m "feat: add Replay tab toggle, replay selector bar, and REPLAY pill to frontend HTML/CSS"
```

---

### Task 8: Frontend JS + CHANGELOG

**Files:**
- Modify: `raceflag/frontend/app.js`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: all IDs added in Task 7
- Consumes: `replay_mode`, `replay_status`, `replay_session_name` from `/api/state` poll
- Consumes: `/api/replay/sessions`, `/api/replay/load`, `/api/replay/play`, `/api/replay/pause`, `/api/replay/resume`, `/api/replay/stop`, `/api/replay/offset`

No unit tests for frontend JS (tested visually). Verify by launching `uvicorn raceflag.main:app` and testing all four replay bar states manually.

- [ ] **Step 1: Add `replayMode` state variable and update `manualView` handling**

Near the top of `app.js`, after `let manualView = null;`, add:

```javascript
let replayMode = false;
let _savedDelay = 0;  // persisted LED delay to restore when leaving replay
```

- [ ] **Step 2: Wire the three toggle buttons (replace existing 2-button wiring)**

Find and replace these two lines in `app.js`:

**Before:**
```javascript
document.getElementById('btn-live').addEventListener('click', () => { manualView = 'live'; fetchState(); });
document.getElementById('btn-standings').addEventListener('click', () => { manualView = 'standings'; fetchState(); });
```

**After:**
```javascript
document.getElementById('btn-live').addEventListener('click', () => { manualView = 'live'; fetchState(); });
document.getElementById('btn-replay').addEventListener('click', () => {
  manualView = 'replay';
  fetchState();
  _loadReplaySessions();
});
document.getElementById('btn-standings').addEventListener('click', () => { manualView = 'standings'; fetchState(); });
```

- [ ] **Step 3: Update `updateUI` to handle the 3-option toggle and replay state**

In `updateUI(data)`, replace the toggle section. Find the block starting with:

```javascript
  const targetView = manualView || (isActive ? 'live' : 'standings');
  document.getElementById('view-live').classList.toggle('active', targetView === 'live');
  document.getElementById('view-standings').classList.toggle('active', targetView === 'standings');
  document.getElementById('btn-live').classList.toggle('active', targetView === 'live');
  document.getElementById('btn-standings').classList.toggle('active', targetView === 'standings');
  const _pill = document.getElementById('view-toggle-pill');
  if (_pill) _pill.classList.toggle('right', targetView === 'standings');
```

Replace with:

```javascript
  const targetView = manualView || (isActive ? 'live' : 'standings');
  // Both 'live' and 'replay' render view-live content
  document.getElementById('view-live').classList.toggle('active', targetView === 'live' || targetView === 'replay');
  document.getElementById('view-standings').classList.toggle('active', targetView === 'standings');
  document.getElementById('btn-live').classList.toggle('active', targetView === 'live');
  document.getElementById('btn-replay').classList.toggle('active', targetView === 'replay');
  document.getElementById('btn-standings').classList.toggle('active', targetView === 'standings');
  const _pill = document.getElementById('view-toggle-pill');
  if (_pill) {
    _pill.classList.remove('centre', 'right');
    if (targetView === 'replay') _pill.classList.add('centre');
    else if (targetView === 'standings') _pill.classList.add('right');
  }

  // Replay bar visibility
  const isReplay = targetView === 'replay';
  document.getElementById('replay-bar').style.display = isReplay ? 'flex' : 'none';

  // Auto-label text
  const autoLabel = document.getElementById('auto-label');
  if (autoLabel) {
    const rs = data.replay_status || 'idle';
    const rn = data.replay_session_name || '';
    if (!isReplay) {
      autoLabel.textContent = 'Switches automatically · Manual override above';
    } else if (rs === 'idle') {
      autoLabel.textContent = 'Select a race to begin';
    } else if (rs === 'ready') {
      autoLabel.textContent = 'Press Play at lights out · Use slider to fine-tune sync';
    } else if (rs === 'playing') {
      autoLabel.textContent = `Replaying: ${rn}`;
    } else if (rs === 'paused') {
      autoLabel.textContent = `Paused · ${rn}`;
    }
  }

  // Replay bar row visibility
  const rs = data.replay_status || 'idle';
  document.getElementById('replay-idle-row').style.display =
    (isReplay && (rs === 'idle' || rs === 'loading')) ? 'flex' : 'none';
  document.getElementById('replay-ready-row').style.display =
    (isReplay && rs === 'ready') ? 'flex' : 'none';
  document.getElementById('replay-playback-row').style.display =
    (isReplay && (rs === 'playing' || rs === 'paused')) ? 'flex' : 'none';

  // Pause/resume button label
  const pauseBtn = document.getElementById('btn-replay-pause');
  if (pauseBtn) pauseBtn.textContent = rs === 'paused' ? '▶ Resume' : '⏸ Pause';

  // REPLAY pill on Session section
  const replayPill = document.getElementById('session-replay-pill');
  if (replayPill) replayPill.style.display = data.replay_mode ? 'inline' : 'none';

  // Slider dual-mode: switch between LED Delay and Sync Offset
  const newReplayMode = !!data.replay_mode;
  if (newReplayMode !== replayMode) {
    replayMode = newReplayMode;
    const slider = document.getElementById('delay-slider');
    const label = document.getElementById('delay-label');
    if (replayMode) {
      label.textContent = 'Sync Offset';
      slider.min = -30;
      slider.max = 30;
      slider.value = 0;
      document.getElementById('delay-value').textContent = '0';
    } else {
      label.textContent = 'LED Delay';
      slider.min = 0;
      slider.max = 90;
      slider.value = _savedDelay;
      document.getElementById('delay-value').textContent = _savedDelay;
    }
  }
```

- [ ] **Step 4: Update the slider event listeners for dual-mode**

Find and replace the two slider listeners:

**Before:**
```javascript
document.getElementById('delay-slider').addEventListener('input', function () {
  document.getElementById('delay-value').textContent = this.value;
});
document.getElementById('delay-slider').addEventListener('change', async function () {
  await fetch('/api/config/delay', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ seconds: parseFloat(this.value) }),
  });
});
```

**After:**
```javascript
document.getElementById('delay-slider').addEventListener('input', function () {
  document.getElementById('delay-value').textContent = this.value;
});
document.getElementById('delay-slider').addEventListener('change', async function () {
  const val = parseFloat(this.value);
  if (replayMode) {
    await fetch('/api/replay/offset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seconds: val }),
    });
  } else {
    _savedDelay = val;
    await fetch('/api/config/delay', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seconds: val }),
    });
  }
});
```

- [ ] **Step 5: Add the replay session loading and control functions**

Add these functions before `fetchState()` at the bottom of `app.js`:

```javascript
// ── Replay ──────────────────────────────────────────────────────────────────
async function _loadReplaySessions() {
  const dropdown = document.getElementById('replay-dropdown');
  if (!dropdown) return;
  dropdown.innerHTML = '<option value="">Loading…</option>';
  document.getElementById('btn-replay-load').disabled = true;
  try {
    const resp = await fetch('/api/replay/sessions');
    if (!resp.ok) throw new Error('fetch failed');
    const sessions = await resp.json();
    dropdown.innerHTML = '<option value="">Select a race…</option>' +
      sessions.map(s =>
        `<option value="${s.path}" data-name="${s.name}">${s.name} · ${s.date}</option>`
      ).join('');
  } catch (e) {
    dropdown.innerHTML = '<option value="">Failed to load</option>';
  }
}

document.getElementById('replay-dropdown').addEventListener('change', function () {
  document.getElementById('btn-replay-load').disabled = !this.value;
});

document.getElementById('btn-replay-load').addEventListener('click', async () => {
  const dropdown = document.getElementById('replay-dropdown');
  const path = dropdown.value;
  const name = dropdown.options[dropdown.selectedIndex]?.dataset?.name || '';
  if (!path) return;
  const btn = document.getElementById('btn-replay-load');
  btn.textContent = 'Loading…';
  btn.disabled = true;
  try {
    await fetch('/api/replay/load', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_path: path, session_name: name }),
    });
    document.getElementById('replay-race-chip').textContent = name;
    await fetchState();
  } catch (e) {
    btn.textContent = 'Load';
    btn.disabled = false;
  }
});

document.getElementById('btn-replay-play').addEventListener('click', async () => {
  await fetch('/api/replay/play', { method: 'POST' });
  await fetchState();
});

document.getElementById('btn-replay-pause').addEventListener('click', async () => {
  const isPaused = document.getElementById('btn-replay-pause').textContent.includes('Resume');
  await fetch(isPaused ? '/api/replay/resume' : '/api/replay/pause', { method: 'POST' });
  await fetchState();
});

document.getElementById('btn-replay-stop').addEventListener('click', async () => {
  await fetch('/api/replay/stop', { method: 'POST' });
  await fetchState();
  // Re-load session list so user can pick another race
  _loadReplaySessions();
});
```

- [ ] **Step 6: Restore persisted LED delay on startup**

The bottom of `app.js` already has:

```javascript
(async () => {
  try {
    const resp = await fetch('/api/config');
    if (!resp.ok) return;
    const cfg = await resp.json();
    const slider = document.getElementById('delay-slider');
    slider.value = cfg.delay_seconds ?? 0;
    document.getElementById('delay-value').textContent = slider.value;
  } catch (e) {}
})();
```

Add `_savedDelay = cfg.delay_seconds ?? 0;` inside that block:

```javascript
    const cfg = await resp.json();
    _savedDelay = cfg.delay_seconds ?? 0;
    const slider = document.getElementById('delay-slider');
    slider.value = _savedDelay;
    document.getElementById('delay-value').textContent = slider.value;
```

- [ ] **Step 7: Update CHANGELOG.md**

Add to the `[Unreleased]` → `### Added` section:

```markdown
- Replay Mode — select any completed 2025 F1 race from the F1 livetiming archive, press Play at lights out on your TV, and the LED strip reacts to all flag events identically to live mode
- Sync Offset slider replaces LED Delay when in Replay mode (±30 s range); LED Delay is restored when returning to Live mode
- Pause and Resume replay without losing sync — user pauses both TV broadcast and RaceFlag simultaneously
- REPLAY pill appears on the Session section title while a replay is active
```

- [ ] **Step 8: Run the full test suite**

Run: `pytest -v`

Expected: All tests pass.

- [ ] **Step 9: Commit**

```
git add raceflag/frontend/app.js CHANGELOG.md
git commit -m "feat: add Replay tab JS — session picker, play/pause/resume/stop, sync offset slider"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] 3-option toggle: Task 7
- [x] Session selector fetches fresh on tab open: Task 8 (`_loadReplaySessions` called on Replay tab click)
- [x] Lights-out sync: Task 3 (`_find_lights_out`), Task 4 (`play` starts at T=0 with `race_start` event)
- [x] Sync offset slider ±30 s: Task 1 (`AppState`), Task 5 (`/api/replay/offset`), Task 8 (slider dual-mode)
- [x] Same LED behaviour: Task 6 — `on_flag_change` passed as `on_event` callback
- [x] Pause/resume without wall-clock shift: Task 4 (`pause` + `resume` with `_play_wall_origin` shift)
- [x] Stop restores live mode: Task 5 (`/api/replay/stop` sets `listener.suspended = False`, `replay_mode = False`)
- [x] 4-state selector bar layout: Task 7 (HTML) + Task 8 (JS toggles between rows)
- [x] REPLAY pill: Task 7 (HTML) + Task 8 (JS toggles display)
- [x] Auto-label text per state: Task 8 (`updateUI`)
- [x] F1Listener stays connected during replay: Task 2 (`suspended` flag — SignalR connection stays alive)
- [x] CHANGELOG update: Task 8

**Type consistency:**
- `ReplayManager.play(on_event)` — `on_event` is `Callable[[str], None]`, matches `on_flag_change(status: str)` ✓
- `AppState.set_replay_state(mode, status, session_name, elapsed)` — all str/bool, used consistently ✓
- `create_app(..., replay_manager=None, listener=None)` — optional, absent in tests that don't need it ✓

**Out of scope (not implemented):**
- Sprint race sessions
- Qualifying replay
- Persisting replay selection across page reloads
- Caching fetched .jsonStream files
- Seek/scrub to arbitrary position
