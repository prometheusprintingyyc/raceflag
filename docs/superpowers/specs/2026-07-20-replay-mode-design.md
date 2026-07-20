# Replay Mode Design

## Problem

RaceFlag only reacts to live F1 timing data. Users who want to re-watch a past race on TV get no LED feedback. A replay mode lets users select any completed race from the current season, sync the LED strip to their broadcast, and experience the same flag animations they would get during a live race.

---

## Solution

Add a Replay mode that feeds historical F1 livetiming data through the exact same LED and state pipeline used in live mode. The frontend gains a third tab ("Replay") and a session selector. The LED behaviour, animations, and web UI panels are identical to live mode — the only differences are the data source and a sync offset control.

---

## Data Source

**F1 Livetiming static files** — `https://livetiming.formula1.com/static/`

Session discovery:
```
GET https://livetiming.formula1.com/static/{Year}/Index.json
```
Returns `Meetings[].Sessions[]` with `Path`, `Type`, `Name`, `StartDate`. The `Path` field is the directory prefix for all session data files.

Session data files (fetched per session):
- `TrackStatus.jsonStream` — flag state changes (AllClear, Yellow, Red, SC, VSC)
- `RaceControlMessages.jsonStream` — race control events (RACE STARTED, YELLOW FLAG messages, etc.)

File format: one entry per line, `HH:MM:SS.MMM{JSON}` — no separator between timestamp and JSON object. Timestamps are relative to session start on F1's servers.

Phase 1 scope: Race sessions only (`Type == "Race"`, not Sprint). Sprint races can be added later.

---

## Architecture

Replay acts as a drop-in replacement data source feeding the same callbacks used by the live `F1Listener`:

```
Live mode:
  F1Listener (SignalR) ──► on_flag_change() ──► LEDController.trigger()
                       ──► AppState.update_track_status()

Replay mode:
  ReplayManager (.jsonStream) ──► on_flag_change() ──► LEDController.trigger()
                               ──► AppState.update_track_status()
```

When replay starts, `F1Listener` processing is suspended (SignalR stays connected, callbacks suppressed via a flag). When replay stops, the listener resumes. `AppState` tracks `replay_mode: bool` so the frontend renders correctly.

---

## ReplayManager

New class: `raceflag/replay_manager.py`

**Responsibilities:**
- Fetch and parse `Index.json` to list available race sessions
- Fetch and parse `TrackStatus.jsonStream` + `RaceControlMessages.jsonStream` for a selected session
- Merge both streams into a single chronological event list
- Detect the lights-out offset (effective T=0 for the race start)
- Play back events in real time using `asyncio.sleep()`
- Expose play/stop controls and sync offset adjustment

**Lights-out detection algorithm:**
1. Parse `RaceControlMessages` for a message matching `"RACE STARTED"` — this is the primary marker
2. Fallback: find the first `AllClear` (`Status == "1"`) in `TrackStatus` that occurs after a gap of ≥ 5 minutes from the previous event (the formation lap gap)
3. Record this timestamp as `_lights_out_offset` — all events before it are discarded during playback

**Sync offset:**
- Stored as `_sync_offset_seconds: float` (default `0.0`)
- Range: `-30` to `+30` seconds
- Applied to the wall-clock baseline at playback time: `event_fires_at = t_play + (event_timestamp - lights_out_offset) + sync_offset`
- Can be adjusted mid-playback without restarting — takes effect on the next event cycle

**Key methods:**
```python
async def get_sessions(year: int) -> list[dict]
    # Fetches Index.json fresh each call. Returns [{"name", "path", "date", "circuit"}, ...]

async def load_session(path: str) -> None
    # Fetches both .jsonStream files, parses and merges events, detects lights-out offset

async def play(on_event: Callable) -> None
    # Starts asyncio task walking events in real time, calling on_event(flag_state) for each

def pause() -> None
    # Sets _paused = True
    # The playback task checks _paused each tick and sleeps without firing events

def resume() -> None
    # Sets _paused = False; playback continues from the same position
    # No clock adjustment needed — the user pauses and resumes both TV and RaceFlag together

def stop() -> None
    # Cancels playback task, resets all state including _paused

def set_sync_offset(seconds: float) -> None
    # Adjusts _sync_offset_seconds, clamped to [-30, +30]
```

**Pause implementation detail:** The playback asyncio task loops over the event list. On each iteration it checks `_paused`; if `True` it `await asyncio.sleep(0.1)` without advancing. The user is expected to pause and resume their TV broadcast simultaneously with RaceFlag, so no wall-clock compensation is applied on resume.

---

## AppState Changes

New fields added to `AppState` dataclass (`raceflag/state.py`):

```python
replay_mode: bool = False
replay_status: str = "idle"   # "idle" | "loading" | "ready" | "playing" | "paused" | "complete"
replay_session_name: str = ""  # e.g. "2025 British Grand Prix"
replay_time_elapsed: str = ""  # HH:MM:SS display of current replay position
```

Added to `to_dict()` so the frontend reads them via the existing `/api/state` poll.

New setter:
```python
def set_replay_state(self, mode: bool, status: str, session_name: str = "", elapsed: str = "") -> None
```

---

## API Endpoints

New endpoints added to `raceflag/web_server.py`:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/replay/sessions` | Fetch fresh session list from Index.json |
| `POST` | `/api/replay/load` | Load a session; body `{"session_path": str}` |
| `POST` | `/api/replay/play` | Start or resume playback |
| `POST` | `/api/replay/pause` | Pause playback mid-session |
| `POST` | `/api/replay/stop` | Stop playback entirely, resume live mode |
| `POST` | `/api/replay/offset` | Adjust sync offset; body `{"seconds": float}` |

`GET /api/replay/sessions` always fetches fresh — no caching — so newly available replays appear automatically each time the Replay tab is opened.

`POST /api/replay/load` response:
```json
{ "status": "ready", "session_name": "2025 British Grand Prix", "event_count": 84 }
```

---

## LED Delay Slider — Dual Mode

The existing LED delay slider (`#delay-slider`, `POST /api/config/delay`) is repurposed in Replay mode to control the sync offset instead of the LED broadcast delay.

| Mode | Label | Range | Value meaning |
|---|---|---|---|
| Live | "LED Delay" | 0 – 90 s | Delay before LED reacts to flag events |
| Replay | "Sync Offset" | −30 – +30 s | Shift replay timeline relative to TV |

The slider thumb sits at centre (value = 0) when entering Replay mode. Moving left applies a negative offset (replay fires events earlier — user pressed Play slightly late). Moving right applies a positive offset (user pressed slightly early).

Implementation: the frontend detects `replay_mode` from `/api/state` and switches the slider's `min`, `max`, `value`, and label. On change, it POSTs to `/api/replay/offset` instead of `/api/config/delay`. On leaving Replay mode, the slider resets to the persisted LED delay value from `/api/config`.

---

## F1Listener Interaction

`F1Listener` gains a `suspended: bool` flag (default `False`). When `True`, incoming SignalR events are received but not processed — callbacks are not called, `AppState` is not updated.

`ReplayManager.play()` sets `listener.suspended = True` before starting.
`ReplayManager.stop()` sets `listener.suspended = False` after stopping, then calls `AppState.reset_to_idle()` to clear replay state.

This means the live feed reconnects automatically if it dropped during replay — `F1Listener` keeps its SignalR connection alive even while suspended.

---

## Frontend Changes

### View toggle — 3 options

The existing 2-option pill toggle (`Live Race` | `Standings`) gains a third centre option:

```
[ Live Race ]  [ Replay ]  [ Standings ]
```

Pill width: `calc(33.33% - 2px)`. Positions: left, centre (`translateX(calc(100% + 3px))`), right (`translateX(calc(200% + 6px))`).

### Replay session selector bar

Appears below the toggle only when Replay tab is active. The layout changes across states:

**Idle** — full-width dropdown + Load button:
```
[ Dropdown: Select a race…  ▼              ]  [ Load ]
```

**Loaded** — dropdown replaced by a compact race name chip + Play button:
```
[ 🇬🇧 British Grand Prix · 6 Jul 2025 ]  [ ▶ Play ]
```
The chip is a non-interactive label. A small "×" or "Change" affordance beside it lets the user go back to the dropdown if they want a different race.

**Playing** — dropdown/chip hidden entirely, two equal buttons fill the row:
```
[ ⏸  Pause          ]  [ ■  Stop           ]
```

**Paused** — same two-button layout, left button switches to Resume:
```
[ ▶  Resume         ]  [ ■  Stop           ]
```

Pressing Stop cancels the replay, clears state, and restores the full dropdown so the user can select a different race. Pause/Resume toggle without losing the current replay position.

### Auto-label (below toggle)

| State | Text |
|---|---|
| Live tab | "Switches automatically · Manual override above" |
| Replay idle | "Select a race to begin" |
| Replay loaded | "Press Play at lights out · Use slider to fine-tune sync" |
| Replay playing | "Replaying: 2025 British Grand Prix" |
| Replay paused | "Paused · 2025 British Grand Prix" |

### Slider label and range

Set dynamically from `replay_mode` in the state poll handler:

- If `replay_mode == false`: label = "LED Delay", min=0, max=90, restore persisted value, POST target = `/api/config/delay`
- If `replay_mode == true`: label = "Sync Offset", min=-30, max=30, value=0, POST target = `/api/replay/offset`

### Session section title

The "Session" section title gains a `REPLAY` pill (same style as the existing `LIVE` pill on Race Positions) when `replay_mode == true`, making the data source unambiguous:

```
Session  [REPLAY]
```

### Everything else

Flag banner, LED strip panel, weather, race positions, race control messages, and the Standings tab are all unchanged. They render from the same `/api/state` poll as in live mode.

---

## Sync Flow (User's Perspective)

1. Tap **Replay** tab → session list loads fresh from Index.json
2. Select a race from the dropdown → **Load** button activates
3. Tap **Load** → session data fetches; button becomes **▶ Play**; flag banner shows "Ready — Press Play at lights out on your TV"; slider shows "Sync Offset" at 0
4. Start the race broadcast on TV (e.g. F1 TV, YouTube, etc.)
5. At the moment of lights out, tap **▶ Play** → LED goes green immediately (race start animation)
6. If LEDs are slightly out of sync, adjust the **Sync Offset** slider left or right until aligned
7. Watch the race — LEDs react to all flag events identically to live mode
8. Tap **⏸ Pause** to freeze the replay (e.g. pausing the broadcast); tap **▶ Resume** to continue from the same position
9. Tap **■ Stop** at any point to end the replay entirely and return to live mode

---

## Files Changed

| File | Change |
|---|---|
| `raceflag/replay_manager.py` | New — `ReplayManager` class |
| `raceflag/state.py` | Add `replay_mode`, `replay_status`, `replay_session_name`, `replay_time_elapsed` fields and setter |
| `raceflag/web_server.py` | Add 5 new `/api/replay/*` endpoints; add `ReplayManager` to `create_app` dependencies |
| `raceflag/f1_listener.py` | Add `suspended: bool` flag; guard callbacks with `if not self.suspended` |
| `raceflag/frontend/index.html` | 3-option toggle; replay selector bar; REPLAY pill on section titles |
| `raceflag/frontend/app.js` | Replay tab handler; session fetch; load/play/stop click handlers; slider dual-mode logic |
| `tests/test_replay_manager.py` | New — unit tests for session parsing, lights-out detection, event playback |
| `tests/test_web_server.py` | Tests for all 5 new replay endpoints |
| `tests/test_state.py` | Tests for new `replay_*` fields and setter |
| `CHANGELOG.md` | Update `[Unreleased]` section |

---

## Out of Scope (Phase 1)

- Sprint race sessions
- Qualifying replay
- Persisting selected race across page reloads
- Caching fetched session data on disk
- Scrubbing / seeking to an arbitrary position within a replay
