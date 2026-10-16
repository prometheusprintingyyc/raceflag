# Live Delay Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Sync button that sets the live LED delay from the moment the user sees lights-out, and a fix so live lights-out is detected at all.

**Architecture:** The listener records the box's monotonic time when a live Race/Sprint reports `SessionStatus "Started"` and reports `race_start` directly. The flag-routing closure in `main.py` moves into a testable `FlagRouter` class that keeps the delayed race-start cancellable. A pure `delay_sync` module turns (press time, lights-out time, race live?) into a delay or a reason, and `POST /api/delay/sync` saves the delay and asks the router to play the race start now.

**Tech Stack:** Python 3.11+, FastAPI (<0.100) + pydantic v1, asyncio, pytest (`asyncio_mode = auto`), vanilla JS/HTML/CSS frontend.

**Spec:** `docs/superpowers/specs/2026-10-16-live-delay-sync-design.md`

## Global Constraints

- All work on the `beta` branch of the Pi repo. Spec changes go in `../raceflag_specs` (branch `master`) as their own commits naming the feature; stage only your own files there.
- Commit trailer lines on every commit:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01UQ9hM8siPUL1nmzWsUhytk`
- Never stage `Notes.txt`, `Notes.txt.bak`, `.claude/settings.local.json` or any untracked user file.
- Race and Sprint sessions only (`session_type.lower() in {"race", "sprint"}`).
- Sync window: 90 s after lights-out (`SYNC_WINDOW_S = 90.0`). Delay is `round(now − lights_out_at, 1)`, clamped to 0.0–90.0.
- 409 messages, exactly: `"Sync needs a live race or sprint"`, `"The race hasn't started yet"`, `"Sync works within 90 s of the race start"`.
- The synced delay is saved to `config.json` (`delay_seconds`) and persists across races and reboots. Nothing in this feature resets `delay_seconds`.
- `lights_out_at` is runtime only and is cleared when the session ends (`Finished`, `Finalised`, `Ends`, `Inactive`).
- Run tests with `PYTHONUTF8=1 python -m pytest tests -q -p no:warnings`. Six tests fail before this work and are not yours: `test_handle_feed_session_status_finished_sets_break`, `test_handle_feed_session_status_finished_resets_track_status`, `test_play_snapshot_phase_fires_with_is_snapshot_true`, `test_set_delay_rejects_above_60`, `test_test_effect_triggers_led`, `test_check_connectivity_returns_true_when_ping_succeeds`. Any other failure is yours.
- Update `CHANGELOG.md` `[Unreleased]` in the task that changes user-visible behaviour.

## Review Focus

1. Box rebooted or reconnected after lights-out (the feed's snapshot says `Started`) → `lights_out_at` stays unset, Sync answers "The race hasn't started yet" and changes nothing. (Task 2 test `test_started_in_snapshot_is_not_lights_out`.)
2. Delay was already 0 at lights-out (race start played immediately, nothing pending) → Sync still works and plays the race start once more, without errors. (Task 3 test `test_sync_without_pending_race_start_plays_once`.)
3. The 90 s boundary: 90.0 s after lights-out syncs to 90.0; 90.05 s is refused. (Task 1 tests `test_exactly_90_s_is_allowed`, `test_just_over_90_s_is_refused`.)
4. Sync pressed twice → the second press recalculates from the same lights-out, with no double cancel error. (Task 3 test `test_second_sync_recalculates`.)
5. Race ends after a sync → `lights_out_at` cleared, saved delay untouched; a later Sync is refused without changing the delay. (Task 4 test `test_sync_after_race_end_keeps_saved_delay`.)

---

### Task 1: Sync calculation module

**Files:**
- Create: `raceflag/delay_sync.py`
- Test: `tests/test_delay_sync.py`

**Interfaces:**
- Produces:
  - `SYNC_WINDOW_S: float = 90.0`
  - `class SyncError(Exception)` — `str(exc)` is the user-facing message
  - `NO_RACE`, `NOT_STARTED`, `TOO_LATE`: the three message strings
  - `is_live_race(session_type: str, is_active: bool, replay_mode: bool) -> bool`
  - `sync_delay(now: float, lights_out_at: float | None, race_live: bool) -> float` — raises `SyncError`

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from raceflag.delay_sync import (
    NO_RACE, NOT_STARTED, TOO_LATE, SYNC_WINDOW_S, SyncError, is_live_race, sync_delay,
)


def test_messages():
    assert NO_RACE == "Sync needs a live race or sprint"
    assert NOT_STARTED == "The race hasn't started yet"
    assert TOO_LATE == "Sync works within 90 s of the race start"
    assert SYNC_WINDOW_S == 90.0


@pytest.mark.parametrize("session_type,active,replay,expected", [
    ("Race", True, False, True),
    ("Sprint", True, False, True),
    ("race", True, False, True),
    ("Qualifying", True, False, False),
    ("Practice 1", True, False, False),
    ("Race", False, False, False),
    ("Race", True, True, False),
    ("", True, False, False),
])
def test_is_live_race(session_type, active, replay, expected):
    assert is_live_race(session_type, active, replay) is expected


def test_delay_is_time_since_lights_out_rounded():
    assert sync_delay(now=1034.24, lights_out_at=1000.0, race_live=True) == 34.2


def test_rounds_half_up_to_tenth():
    assert sync_delay(now=1012.36, lights_out_at=1000.0, race_live=True) == 12.4


def test_exactly_90_s_is_allowed():
    assert sync_delay(now=1090.0, lights_out_at=1000.0, race_live=True) == 90.0


def test_just_over_90_s_is_refused():
    with pytest.raises(SyncError) as exc:
        sync_delay(now=1090.05, lights_out_at=1000.0, race_live=True)
    assert str(exc.value) == TOO_LATE


def test_no_race_live_is_refused_first():
    with pytest.raises(SyncError) as exc:
        sync_delay(now=1010.0, lights_out_at=1000.0, race_live=False)
    assert str(exc.value) == NO_RACE


def test_no_lights_out_yet_is_refused():
    with pytest.raises(SyncError) as exc:
        sync_delay(now=1010.0, lights_out_at=None, race_live=True)
    assert str(exc.value) == NOT_STARTED


def test_press_at_lights_out_gives_zero():
    assert sync_delay(now=1000.0, lights_out_at=1000.0, race_live=True) == 0.0


def test_clock_before_lights_out_clamps_to_zero():
    assert sync_delay(now=999.5, lights_out_at=1000.0, race_live=True) == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONUTF8=1 python -m pytest tests/test_delay_sync.py -q -p no:warnings`
Expected: FAIL with `ModuleNotFoundError: No module named 'raceflag.delay_sync'`

- [ ] **Step 3: Write the implementation**

```python
"""Live delay Sync (feature 016). Pure functions — no I/O, no clocks."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

SYNC_WINDOW_S = 90.0  # the delay slider's maximum

NO_RACE = "Sync needs a live race or sprint"
NOT_STARTED = "The race hasn't started yet"
TOO_LATE = "Sync works within 90 s of the race start"

_RACE_SESSION_TYPES = frozenset({"race", "sprint"})


class SyncError(Exception):
    """Sync can't set a delay; the message is shown to the user."""


def is_live_race(session_type: str, is_active: bool, replay_mode: bool) -> bool:
    return is_active and not replay_mode and session_type.lower() in _RACE_SESSION_TYPES


def sync_delay(now: float, lights_out_at: float | None, race_live: bool) -> float:
    """Seconds from the feed's lights-out to the user's press, to 0.1 s (half up), 0–90."""
    if not race_live:
        raise SyncError(NO_RACE)
    if lights_out_at is None:
        raise SyncError(NOT_STARTED)
    elapsed = now - lights_out_at
    if elapsed > SYNC_WINDOW_S:
        raise SyncError(TOO_LATE)
    rounded = float(Decimal(repr(elapsed)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    return min(max(rounded, 0.0), SYNC_WINDOW_S)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/test_delay_sync.py -q -p no:warnings`
Expected: all pass. (If `test_rounds_half_up_to_tenth` fails on float noise, that's the reason for `Decimal(repr(...))` — `12.36` must give `12.4`.)

- [ ] **Step 5: Commit**

```bash
git add raceflag/delay_sync.py tests/test_delay_sync.py
git commit -m "feat: live delay Sync calculation"
```

---

### Task 2: Live lights-out detection in the listener

**Files:**
- Modify: `raceflag/state.py` (`AppState`: field + setter)
- Modify: `raceflag/f1_listener.py:192-225` (`_handle_feed`, SessionStatus block) and imports
- Test: `tests/test_f1_listener.py`

**Interfaces:**
- Produces:
  - `AppState.lights_out_at: Optional[float] = None` (box `time.monotonic()` of the live race's lights-out)
  - `AppState.set_lights_out_at(value: Optional[float]) -> None`
  - Listener reports `"race_start"` (not `"track_clear"`) to `on_track_status_change` at a live Race/Sprint `SessionStatus "Started"`, and sets `state.track_status = "track_clear"`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_f1_listener.py`)

```python
# --- Live lights-out (features 004 / 016) -----------------------------------

_RACE_INFO = {"Meeting": {"Name": "Belgian Grand Prix"}, "Type": "Race", "Name": "Race"}


def _race_listener(monkeypatch, now=5000.0):
    state = AppState()
    calls = []
    listener = F1Listener(state=state, on_track_status_change=calls.append)
    monkeypatch.setattr("raceflag.f1_listener.time.monotonic", lambda: now)
    return state, listener, calls


def test_started_after_inactive_snapshot_is_lights_out(monkeypatch):
    """Belgian GP 2024: box connects pre-race (snapshot Inactive), track stays AllClear."""
    state, listener, calls = _race_listener(monkeypatch)
    listener._handle_feed("SessionInfo", _RACE_INFO, is_snapshot=True)
    listener._handle_feed("TrackStatus", {"Status": "1"}, is_snapshot=True)
    listener._handle_feed("SessionStatus", {"Status": "Inactive"}, is_snapshot=True)
    listener._handle_feed("Heartbeat", {})
    listener._handle_feed("SessionStatus", {"Status": "Started"})
    assert calls == ["race_start"]
    assert state.lights_out_at == 5000.0
    assert state.track_status == "track_clear"


def test_pre_race_allclear_is_plain_and_started_is_lights_out(monkeypatch):
    state, listener, calls = _race_listener(monkeypatch)
    listener._handle_feed("SessionInfo", _RACE_INFO, is_snapshot=True)
    listener._handle_feed("SessionStatus", {"Status": "Inactive"}, is_snapshot=True)
    listener._handle_feed("TrackStatus", {"Status": "1"})
    listener._handle_feed("SessionStatus", {"Status": "Started"})
    assert calls == ["track_clear", "race_start"]
    assert state.lights_out_at == 5000.0


def test_started_in_snapshot_is_not_lights_out(monkeypatch):
    """Reconnecting mid-race: the snapshot's Started must not pretend lights-out is now."""
    state, listener, calls = _race_listener(monkeypatch)
    listener._handle_feed("SessionInfo", _RACE_INFO, is_snapshot=True)
    listener._handle_feed("SessionStatus", {"Status": "Started"}, is_snapshot=True)
    assert calls == []
    assert state.lights_out_at is None


def test_started_in_replay_is_not_live_lights_out(monkeypatch):
    state, listener, calls = _race_listener(monkeypatch)
    state.set_replay_state(True, "playing")
    listener._handle_feed("SessionInfo", _RACE_INFO)
    listener._handle_feed("SessionStatus", {"Status": "Started"})
    assert "race_start" not in calls
    assert state.lights_out_at is None


def test_started_in_qualifying_keeps_break_reset(monkeypatch):
    state, listener, calls = _race_listener(monkeypatch)
    listener._handle_feed("SessionInfo", {"Meeting": {"Name": "X"}, "Type": "Qualifying", "Name": "Qualifying"},
                          is_snapshot=True)
    listener._handle_feed("SessionStatus", {"Status": "Ends"})
    listener._handle_feed("SessionStatus", {"Status": "Started"})
    assert calls == ["break", "unknown"]
    assert state.lights_out_at is None


@pytest.mark.parametrize("end_status", ["Finished", "Finalised", "Ends", "Inactive"])
def test_session_end_clears_lights_out(monkeypatch, end_status):
    state, listener, calls = _race_listener(monkeypatch)
    listener._handle_feed("SessionInfo", _RACE_INFO, is_snapshot=True)
    listener._handle_feed("SessionStatus", {"Status": "Started"})
    assert state.lights_out_at == 5000.0
    listener._handle_feed("SessionStatus", {"Status": end_status})
    assert state.lights_out_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONUTF8=1 python -m pytest tests/test_f1_listener.py -q -p no:warnings -k "lights_out or started_in"`
Expected: FAIL — `AppState` has no `lights_out_at`, and `raceflag.f1_listener` has no `time`.

- [ ] **Step 3: Add the state field** in `raceflag/state.py`

Add the field after `replay_time_elapsed: str = ""` in `AppState`:

```python
    lights_out_at: Optional[float] = None  # box monotonic time of the live race's lights-out
```

Add the setter after `set_feed_connected`:

```python
    def set_lights_out_at(self, value: Optional[float]) -> None:
        with self._lock:
            self.lights_out_at = value
```

If `Optional` isn't imported in `state.py`, add `from typing import Optional` (keep the existing `List` import). Do **not** add `lights_out_at` to `to_dict()`.

- [ ] **Step 4: Change the listener**

In `raceflag/f1_listener.py` add `import time` next to the other standard-library imports.

In `_handle_feed`, in the session-ending branch, clear the lights-out time right after the `set_session(...)` line:

```python
                self._state.set_session(_replace(self._state.session, is_active=False, extrapolating=False))
                self._state.set_lights_out_at(None)
```

Replace the whole `if status_msg == "Started":` block (currently `f1_listener.py:210-225`) with:

```python
            if status_msg == "Started":
                is_race = self._state.session.session_type.lower() in ("race", "sprint")
                if is_race and not is_snapshot and not self._state.replay_mode:
                    # Live race lights-out (verified on Belgian GP 2024: Started lands with
                    # GREEN LIGHT - PIT EXIT OPEN, 1:52 before lap 2). Checked before the
                    # break/finished reset: a pre-race "Inactive" leaves track_status
                    # "finished", which must not swallow lights-out.
                    logger.info("Race lights-out (SessionStatus Started) — triggering race_start")
                    self._state.set_lights_out_at(time.monotonic())
                    self._state.set_track_status("track_clear")
                    if self._on_track_status_change:
                        self._on_track_status_change("race_start")
                elif self._state.track_status in ("break", "finished"):
                    self._state.set_track_status("unknown")
                    logger.info("New session segment started — clearing break state")
                    if self._on_track_status_change and not is_snapshot:
                        self._on_track_status_change("unknown")
```

- [ ] **Step 5: Run the listener tests**

Run: `PYTHONUTF8=1 python -m pytest tests/test_f1_listener.py -q -p no:warnings`
Expected: the new tests pass; the only failures are the two baseline ones listed in Global Constraints.

- [ ] **Step 6: Commit**

```bash
git add raceflag/state.py raceflag/f1_listener.py tests/test_f1_listener.py
git commit -m "fix: detect live race lights-out at SessionStatus Started"
```

(`main.py` still promotes `track_clear` and doesn't know `race_start` as a listener status until Task 3; don't run the box between Tasks 2 and 3.)

---

### Task 3: FlagRouter — move flag routing out of main.py, cancellable race start, conformance

**Files:**
- Create: `raceflag/flag_router.py`
- Modify: `raceflag/main.py:206-275` (replace the `on_flag_change` closure and its constants; wire the router)
- Modify: `tests/test_conformance.py:52-75` (`_run_case` uses `FlagRouter.resolve`)
- Modify (specs repo): `../raceflag_specs/conformance/002-race-start-promotion/` and new `../raceflag_specs/conformance/004-lights-out-after-inactive/`
- Test: `tests/test_flag_router.py`

**Interfaces:**
- Consumes: listener reports `"race_start"` at live lights-out (Task 2).
- Produces:
  - `IDLE_STATUSES`, `TIMED_EFFECTS`, `RACE_SESSION_TYPES` module constants
  - `class FlagRouter(state, led, config)`
    - `resolve(status: str) -> tuple[str, str]` — `(display_status, effective)`; updates the race-started latch
    - `on_flag_change(status: str) -> None` — the listener / replay callback
    - `sync(delay: float) -> None` — cancel the pending race start, apply `led.set_delay(delay)`, show `track_clear`, play `race_start` now

- [ ] **Step 1: Write the failing tests** (`tests/test_flag_router.py`)

```python
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from raceflag.flag_router import FlagRouter, TIMED_EFFECTS
from raceflag.state import AppState, SessionInfo


def _router(session_type="Race", delay=0.0, replay=False, active=True):
    state = AppState()
    state.set_session(SessionInfo(session_type=session_type, is_active=active))
    if replay:
        state.set_replay_state(True, "playing")
    led = MagicMock()
    config = SimpleNamespace(delay_seconds=delay)
    return FlagRouter(state=state, led=led, config=config), state, led


def test_live_race_start_shows_track_clear_and_plays_race_start():
    router, state, led = _router()
    router.on_flag_change("race_start")
    assert state.display_track_status == "track_clear"
    led.trigger_timed.assert_called_once_with("race_start", TIMED_EFFECTS["race_start"])


def test_live_first_allclear_is_not_promoted():
    router, _, _ = _router()
    assert router.resolve("track_clear") == ("track_clear", "track_clear")


def test_replay_first_allclear_is_promoted_once():
    router, _, _ = _router(replay=True)
    assert router.resolve("track_clear") == ("track_clear", "race_start")
    assert router.resolve("track_clear") == ("track_clear", "track_clear")


def test_replay_promotion_resets_after_session_end():
    router, _, _ = _router(replay=True)
    router.resolve("track_clear")
    router.resolve("finished")
    assert router.resolve("track_clear") == ("track_clear", "race_start")


def test_live_race_start_blocks_later_replay_style_promotion():
    router, _, _ = _router()
    assert router.resolve("race_start") == ("track_clear", "race_start")
    assert router.resolve("track_clear") == ("track_clear", "track_clear")


async def test_delayed_race_start_waits_for_delay():
    router, state, led = _router(delay=0.05)
    router.on_flag_change("race_start")
    led.trigger_timed.assert_not_called()
    await asyncio.sleep(0.12)
    assert state.display_track_status == "track_clear"
    led.trigger_timed.assert_called_once()
    assert led.trigger_timed.call_args.args[0] == "race_start"


async def test_sync_cancels_pending_race_start_and_plays_once():
    router, state, led = _router(delay=0.05)
    router.on_flag_change("race_start")
    router.sync(12.3)
    led.set_delay.assert_called_once_with(12.3)
    assert state.display_track_status == "track_clear"
    led.trigger_timed.assert_called_once_with("race_start", TIMED_EFFECTS["race_start"])
    await asyncio.sleep(0.12)
    led.trigger_timed.assert_called_once()  # the cancelled original never fires


def test_sync_without_pending_race_start_plays_once():
    router, _, led = _router(delay=0.0)
    router.on_flag_change("race_start")  # delay 0: played immediately, nothing pending
    led.trigger_timed.reset_mock()
    router.sync(0.0)
    led.trigger_timed.assert_called_once_with("race_start", TIMED_EFFECTS["race_start"])


async def test_second_sync_recalculates():
    router, _, led = _router(delay=0.05)
    router.on_flag_change("race_start")
    router.sync(10.0)
    router.sync(11.5)
    assert [c.args[0] for c in led.set_delay.call_args_list] == [10.0, 11.5]
    assert led.trigger_timed.call_count == 2
    await asyncio.sleep(0.12)
    assert led.trigger_timed.call_count == 2


def test_continuous_flag_goes_to_led_queue_live():
    router, _, led = _router()
    router.on_flag_change("yellow_flag")
    led.trigger.assert_called_once_with("yellow_flag")


def test_idle_status_sets_idle():
    router, state, led = _router()
    router.on_flag_change("finished")
    assert state.display_track_status == "finished"
    led.set_idle.assert_called_once_with(True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONUTF8=1 python -m pytest tests/test_flag_router.py -q -p no:warnings`
Expected: FAIL with `ModuleNotFoundError: No module named 'raceflag.flag_router'`

- [ ] **Step 3: Write `raceflag/flag_router.py`**

This is the `on_flag_change` closure from `main.py:206-259`, moved into a class, with three changes: live `race_start` from the listener, promotion only in replay, and a cancellable delayed race start.

```python
"""Routes live and replay flag changes to the LEDs and the displayed status."""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

IDLE_STATUSES = frozenset({"unknown", "break", "finished"})
TIMED_EFFECTS = {"track_clear": 30.0, "race_start": 30.0, "checkered": 30.0}
RACE_SESSION_TYPES = frozenset({"race", "sprint"})


class FlagRouter:
    def __init__(self, state, led, config) -> None:
        self._state = state
        self._led = led
        self._config = config
        self._race_started = False
        self._pending_race_start: asyncio.Future | None = None

    def resolve(self, status: str) -> tuple[str, str]:
        """(status to display, effect to play) for a flag change; updates the race-start latch."""
        if status in IDLE_STATUSES:
            self._race_started = False
        if status == "race_start":
            # Live lights-out, reported by the listener. The page shows All Clear.
            self._race_started = True
            return "track_clear", "race_start"
        if (status == "track_clear"
                and not self._race_started
                and self._state.replay_mode
                and self._state.session.session_type.lower() in RACE_SESSION_TYPES):
            # Replay only: the last pre-race TrackStatus is re-fired at lights-out
            # (feature 006), so the first AllClear is the start.
            self._race_started = True
            return status, "race_start"
        return status, status

    def on_flag_change(self, status: str) -> None:
        state = self._state
        delay = 0.0 if state.replay_mode else self._config.delay_seconds
        logger.info("Flag change received: %s  delay=%.1fs", status, delay)
        status, effective = self.resolve(status)

        if (effective not in IDLE_STATUSES and effective not in TIMED_EFFECTS
                and (state.session.is_active or state.replay_mode)):
            if state.replay_mode:
                self._led.force_trigger(effective)
            else:
                self._led.trigger(effective)

        if delay <= 0:
            state.set_display_track_status(status)
            if status in IDLE_STATUSES:
                self._led.set_idle(True)
            elif effective in TIMED_EFFECTS:
                self._led.trigger_timed(effective, TIMED_EFFECTS[effective])
        else:
            task = asyncio.ensure_future(self._delayed(status, effective, delay))
            if effective == "race_start":
                self._pending_race_start = task

    async def _delayed(self, status: str, effective: str, delay: float) -> None:
        # Record when this status was received so trigger_timed only flushes items that
        # predate it — items queued during the delay window (e.g. a yellow flag that
        # arrived while waiting to fire track_clear) are preserved.
        scheduled_at = time.monotonic()
        await asyncio.sleep(delay)
        self._state.set_display_track_status(status)
        if status in IDLE_STATUSES:
            self._led.set_idle(True)
        elif effective in TIMED_EFFECTS:
            self._led.trigger_timed(effective, TIMED_EFFECTS[effective], flush_before=scheduled_at)

    def sync(self, delay: float) -> None:
        """Live delay Sync (feature 016): apply the delay and play the race start now."""
        if self._pending_race_start is not None:
            self._pending_race_start.cancel()
            self._pending_race_start = None
        self._led.set_delay(delay)
        self._state.set_display_track_status("track_clear")
        self._led.trigger_timed("race_start", TIMED_EFFECTS["race_start"])
```

- [ ] **Step 4: Run the router tests**

Run: `PYTHONUTF8=1 python -m pytest tests/test_flag_router.py -q -p no:warnings`
Expected: all pass.

- [ ] **Step 5: Wire it into `main.py`**

In `raceflag/main.py`, delete the block from `_IDLE_STATUSES = {"unknown", "break", "finished"}` through the end of `on_flag_change` (the line `asyncio.ensure_future(_delayed_ui())`), i.e. the constants `_IDLE_STATUSES`, `_LED_IDLE_STATUSES`, `_TIMED_EFFECTS`, `_RACE_SESSION_TYPES`, `_race_started` and the whole `def on_flag_change`. Replace it with:

```python
    flag_router = FlagRouter(state=state, led=led, config=config)
```

Then change the listener line and the `create_app` call:

```python
    listener = F1Listener(state=state, on_track_status_change=flag_router.on_flag_change)
```

```python
        on_replay_event=flag_router.on_flag_change,
        flag_router=flag_router,
```

Add the import with the other `raceflag` imports:

```python
from raceflag.flag_router import FlagRouter
```

(`create_app` gets its `flag_router` parameter in Task 4. Until then add it now as an accepted, unused keyword so `main.py` runs: in `raceflag/web_server.py`'s `create_app` signature add `flag_router=None,` after `on_replay_event=None,`.)

Check nothing else in `main.py` used the removed names:

Run: `grep -n "_race_started\|_TIMED_EFFECTS\|_IDLE_STATUSES\|_LED_IDLE_STATUSES\|_RACE_SESSION_TYPES\|on_flag_change" raceflag/main.py`
Expected: only `flag_router.on_flag_change` lines.

- [ ] **Step 6: Point the conformance runner at the real routing**

In `tests/test_conformance.py`, replace `_run_case` with:

```python
def _run_case(events):
    """Feed events through F1Listener and FlagRouter.resolve; return the flag decisions."""
    decisions = []
    state = AppState()
    router = FlagRouter(state=state, led=None, config=None)

    def on_flag_change(status):
        _display, effective = router.resolve(status)
        decisions.append(effective)

    listener = F1Listener(state=state, on_track_status_change=on_flag_change)
    for _ts, topic, data in events:
        listener._handle_feed(topic, data, is_snapshot=False)
    return decisions
```

Add `from raceflag.flag_router import FlagRouter` to its imports, and remove the now-unused `_IDLE_STATUSES` / `_RACE_SESSION_TYPES` definitions from the test file if it defines them (check with `grep -n "_IDLE_STATUSES\|_RACE_SESSION_TYPES" tests/test_conformance.py`).

- [ ] **Step 7: Update conformance case 002** in `../raceflag_specs/conformance/002-race-start-promotion/`

Create `input/SessionStatus.jsonStream`:

```
00:01:50.000{"Status":"Started"}
```

Replace `expected.json` with:

```json
[
  {"t_ms": 110000, "event": "race_start"},
  {"t_ms": 120000, "event": "track_clear"},
  {"t_ms": 150000, "event": "yellow_flag"},
  {"t_ms": 240000, "event": "track_clear"}
]
```

Replace `README.md` with:

```
Live race: SessionStatus "Started" is lights-out (race_start); every AllClear, including the first, is a plain track_clear.
```

- [ ] **Step 8: Add conformance case 004** `../raceflag_specs/conformance/004-lights-out-after-inactive/`

`README.md`:

```
Belgian GP 2024 pre-race: SessionStatus "Inactive", a pre-race AllClear, then "Started" — race_start fires at Started only.
```

`input/SessionInfo.jsonStream`:

```
00:01:00.000{"Meeting":{"Name":"Belgian Grand Prix","Location":"Spa","Country":{"Name":"Belgium"},"Circuit":{"ShortName":"Spa"}},"Name":"Race","Type":"Race"}
```

`input/SessionStatus.jsonStream`:

```
00:01:30.000{"Status":"Inactive"}
00:05:00.000{"Status":"Started"}
```

`input/TrackStatus.jsonStream`:

```
00:02:00.000{"Status":"1"}
```

`expected.json`:

```json
[
  {"t_ms": 90000,  "event": "finished"},
  {"t_ms": 120000, "event": "track_clear"},
  {"t_ms": 300000, "event": "race_start"}
]
```

- [ ] **Step 9: Run the conformance and full suites**

Run: `PYTHONUTF8=1 python -m pytest tests/test_conformance.py -q -p no:warnings -v`
Expected: cases 001, 002, 003, 004 pass.

Run: `PYTHONUTF8=1 python -m pytest tests -q -p no:warnings`
Expected: only the six baseline failures.

- [ ] **Step 10: Commit both repos**

```bash
git add raceflag/flag_router.py raceflag/main.py raceflag/web_server.py tests/test_flag_router.py tests/test_conformance.py
git commit -m "refactor: move flag routing into FlagRouter; live race start comes from lights-out"
cd ../raceflag_specs
git add conformance/002-race-start-promotion conformance/004-lights-out-after-inactive
git commit -m "conformance: 004 Race-start — live lights-out at SessionStatus Started (cases 002, 004)"
```

---

### Task 4: `POST /api/delay/sync`

**Files:**
- Modify: `raceflag/web_server.py` (imports, `create_app` route)
- Test: `tests/test_web_server.py`

**Interfaces:**
- Consumes: `sync_delay`, `is_live_race`, `SyncError` (Task 1); `state.lights_out_at` (Task 2); `FlagRouter.sync(delay)` (Task 3); `create_app(..., flag_router=None)` (Task 3).
- Produces: `POST /api/delay/sync` → 200 `{"delay_seconds": float}` / 409 `{"detail": str}`; new `create_app` keyword `clock: Callable[[], float] = time.monotonic` (tests inject a fixed clock instead of patching `time.monotonic`, which the test client's event loop also uses).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_web_server.py`)

```python
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
```

If `MagicMock` isn't already imported at the top of `tests/test_web_server.py`, it is (`from unittest.mock import AsyncMock, MagicMock`); `json` is imported too.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONUTF8=1 python -m pytest tests/test_web_server.py -q -p no:warnings -k sync`
Expected: FAIL — `create_app() got an unexpected keyword argument 'clock'`.

- [ ] **Step 3: Add the route**

In `raceflag/web_server.py` add `import time` with the standard-library imports, `Callable` to the `typing` import (`from typing import Callable, Optional`), and:

```python
from raceflag.delay_sync import SyncError, is_live_race, sync_delay
```

Add `clock: Callable[[], float] = time.monotonic,` to the `create_app` signature after `flag_router=None,`.

Inside `create_app`, after the `@app.post("/api/config/delay")` route:

```python
    @app.post("/api/delay/sync")
    async def delay_sync():
        # Timestamp first: the press is "now", before any work below. Same clock as the
        # listener's lights-out time (time.monotonic) in production.
        now = clock()
        session = state.session
        try:
            delay = sync_delay(
                now,
                state.lights_out_at,
                is_live_race(session.session_type, session.is_active, state.replay_mode),
            )
        except SyncError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        config.delay_seconds = delay
        if config_path:
            save_config(config, config_path)
        if flag_router is not None:
            flag_router.sync(delay)
        else:
            led.set_delay(delay)
        return {"delay_seconds": delay}
```

- [ ] **Step 4: Run tests**

Run: `PYTHONUTF8=1 python -m pytest tests/test_web_server.py -q -p no:warnings`
Expected: the new tests pass; only baseline failures remain (`test_set_delay_rejects_above_60`, `test_test_effect_triggers_led`).

- [ ] **Step 5: Commit**

```bash
git add raceflag/web_server.py tests/test_web_server.py
git commit -m "feat: POST /api/delay/sync sets the live delay from lights-out"
```

---

### Task 5: Sync button on the page

**Files:**
- Modify: `raceflag/frontend/index.html:69-74` (delay bar)
- Modify: `raceflag/frontend/style.css:58-66` (delay bar rules)
- Modify: `raceflag/frontend/app.js` (replay/live slider switch ~line 94-111; new Sync handler after the delay-slider `change` handler ~line 275)

**Interfaces:**
- Consumes: `POST /api/delay/sync` (Task 4).

- [ ] **Step 1: Markup** — replace the delay bar in `index.html` with:

```html
    <div class="delay-bar">
      <div><div class="delay-label" id="delay-label">LED Delay</div></div>
      <div class="delay-value" id="delay-value">0</div>
      <div class="delay-unit">sec</div>
      <input type="range" class="delay-slider" id="delay-slider" min="0" max="90" step="0.1" value="0">
      <button type="button" class="btn-sync" id="btn-delay-sync">Sync</button>
    </div>
    <div class="delay-sync-msg" id="delay-sync-msg" role="status" hidden></div>
```

- [ ] **Step 2: Styles** — append after `.delay-slider { … }` in `style.css`:

```css
.btn-sync {
  background: #2a2a2a; border: 1px solid #555; color: #ddd; border-radius: 6px;
  padding: 0 14px; min-height: 44px; font-size: 12px; font-weight: 700;
  letter-spacing: 1px; text-transform: uppercase; cursor: pointer;
}
.btn-sync:active { transform: translateY(1px); }
.btn-sync:disabled { opacity: 0.4; cursor: default; }
.btn-sync:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }
.btn-sync[hidden] { display: none; }
.delay-sync-msg { margin: -6px 12px 12px; font-size: 12px; color: #aaa; }
.delay-sync-msg.error { color: #ff5a52; }
.delay-sync-msg[hidden] { display: none; }
```

- [ ] **Step 3: Hide Sync in replay, and 0.1 s steps only for the live delay** — in `app.js`, in the `if (newReplayMode !== replayMode)` block, add to the replay branch:

```js
      slider.step = 1;
      document.getElementById('btn-delay-sync').hidden = true;
      document.getElementById('delay-sync-msg').hidden = true;
```

and to the live (`else`) branch:

```js
      slider.step = 0.1;
      document.getElementById('btn-delay-sync').hidden = false;
```

- [ ] **Step 4: Sync handler** — add after the `delay-slider` `change` listener in `app.js`:

```js
let _syncMsgTimer = null;

function _showSyncMessage(text, isError) {
  const el = document.getElementById('delay-sync-msg');
  el.textContent = text;
  el.classList.toggle('error', !!isError);
  el.hidden = false;
  clearTimeout(_syncMsgTimer);
  _syncMsgTimer = setTimeout(() => { el.hidden = true; }, 6000);
}

document.getElementById('btn-delay-sync').addEventListener('click', async function () {
  this.disabled = true;
  try {
    const resp = await fetch('/api/delay/sync', { method: 'POST' });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok) {
      _savedDelay = data.delay_seconds;
      const slider = document.getElementById('delay-slider');
      slider.value = _savedDelay;
      document.getElementById('delay-value').textContent = _savedDelay;
      _showSyncMessage(`Delay set to ${_savedDelay} s`, false);
    } else {
      _showSyncMessage(data.detail || "Couldn't sync. Try again.", true);
    }
  } catch (e) {
    _showSyncMessage("Couldn't reach RaceFlag. Check the connection.", true);
  } finally {
    this.disabled = false;
  }
});
```

- [ ] **Step 5: Check syntax and try it on the dev server**

Run: `node --check raceflag/frontend/app.js`
Expected: no output (OK).

Start the dev server (`$env:PYTHONUTF8='1'; python <scratchpad>\rf_dev_server.py <repo>` → http://localhost:8080) and check, at phone width:
- Sync sits at the right of the delay bar; the bar doesn't overflow at 360 px wide.
- Tapping Sync with no live race shows "Sync needs a live race or sprint" in red under the bar, then hides after 6 s.
- Dragging the slider shows tenths (e.g. `34.2`).

Expected: all three as described.

- [ ] **Step 6: Commit**

```bash
git add raceflag/frontend/index.html raceflag/frontend/style.css raceflag/frontend/app.js
git commit -m "feat: Sync button next to the LED delay slider"
```

---

### Task 6: Specs, PARITY and changelog

**Files:**
- Create: `../raceflag_specs/features/016-live-delay-sync.md`
- Modify: `../raceflag_specs/features/004-race-start.md`, `../raceflag_specs/contracts/api.md`, `../raceflag_specs/PARITY.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Write `features/016-live-delay-sync.md`**

```markdown
# 016 — Live Delay Sync

**Status:** beta on Pi

## Summary

Instead of finding the LED delay by trial and error, the user taps **Sync** the moment the
lights go out on their broadcast. The box sets the delay to the time since the live feed
reported lights-out, saves it, and plays the race-start animation right then.

## Behaviour

- Race and Sprint sessions only. Meant to be used once, at one race start; the saved
  delay usually suits later races too.
- Lights-out is the feed's `SessionStatus "Started"` in a live Race/Sprint (see
  [004](004-race-start.md)). The box records when it arrived, on its own clock.
- When Sync is pressed, the box timestamps the press as the request arrives:

| Situation | Result |
|---|---|
| No live Race/Sprint (not live, other session type, or replay) | Nothing changes. 409 "Sync needs a live race or sprint" |
| No lights-out reported yet | Nothing changes. 409 "The race hasn't started yet" |
| More than 90 s since lights-out | Nothing changes. 409 "Sync works within 90 s of the race start" |
| Otherwise | Delay = time since lights-out, to 0.1 s (half up), 0–90 s |

- On success: the delay is saved (`delay_seconds`) and applied like a slider change; the
  race-start animation still waiting out the old delay is cancelled; the displayed status
  becomes All Clear and the race-start animation (30 s) plays now.
- The delay persists across races and reboots. Only the lights-out time is cleared when
  the session ends.
- Flags already waiting fire on the new delay.

## Triggers / inputs

- UI: **Sync** button next to the LED delay slider (hidden in replay mode).
- API: `POST /api/delay/sync` (no body) → `{"delay_seconds": 34.2}` or 409 `{"detail": "..."}`.

## Settings

| Setting | Default | Range | Notes |
|---|---|---|---|
| `delay_seconds` | 0.0 | 0.0–90.0 | Shared with [003](003-led-delay.md); Sync writes it |

## UI

- Sync button at the right of the delay bar; the result shows under the bar for 6 s
  ("Delay set to 34.2 s", or the 409 reason in red).
- The live delay slider moves in 0.1 s steps.

## Edge cases

- The box restarted or reconnected after lights-out: the feed's snapshot isn't treated as
  lights-out, so Sync answers "The race hasn't started yet".
- Delay already 0 at lights-out: the race start played immediately; Sync plays it once more.
- Pressing Sync again inside the window recalculates from the same lights-out.

## Platform notes

### Pi

- Design: `Raceflag/docs/superpowers/specs/2026-10-16-live-delay-sync-design.md` (beta).
- Lights-out time is `time.monotonic()` in `AppState.lights_out_at` (runtime only).

### ESP32

_To be filled by the ESP32 session._

## Conformance cases

- [`004-lights-out-after-inactive`](../conformance/004-lights-out-after-inactive/) — lights-out
  detection Sync depends on.

## Open questions

- None.
```

- [ ] **Step 2: Add the beta callout to `features/004-race-start.md`** — directly under the `### Live feed path` list (after its step 4), add:

```markdown
> **Beta (Pi):** live lights-out is `SessionStatus "Started"` in a Race/Sprint, whatever
> the current track status — a pre-race `"Inactive"` (which sets `finished`) no longer
> blocks it. The listener records the lights-out time (used by
> [016 Live Delay Sync](016-live-delay-sync.md)) and reports `race_start` directly.
> Promotion of the first `track_clear` now applies **only in replay mode**: a live
> pre-race AllClear is a plain `track_clear`. The lights-out time is cleared when the
> session ends. Verified on Belgian GP 2024: `Started` at 0:55:14.764, with
> `GREEN LIGHT - PIT EXIT OPEN` 0.1 s later and lap 2 1:52.1 later.
```

Also add under `## Conformance cases` in 004:

```markdown
- [`004-lights-out-after-inactive`](../conformance/004-lights-out-after-inactive/) — Inactive,
  pre-race AllClear, then Started: race_start only at Started (beta on Pi)
```

- [ ] **Step 3: Add the route to `contracts/api.md`** — after the `POST /api/config/delay` section:

```markdown
## POST /api/delay/sync

> **Beta (Pi):** new route ([feature 016](../features/016-live-delay-sync.md)).

Sets the live LED delay to the time since the feed reported race lights-out, saves it,
and plays the race-start animation now. The press is timestamped when the request arrives.

**Editions:** Full ✓ · Lite ✓

**Request body:** none

**Response 200:**
```json
{"delay_seconds": 34.2}
```

**409:** nothing changed; `detail` is one of `"Sync needs a live race or sprint"`,
`"The race hasn't started yet"`, `"Sync works within 90 s of the race start"`.

---
```

- [ ] **Step 4: PARITY** — in `PARITY.md` change the 004 row to `| [Race-start detection](features/004-race-start.md) | 🧪 | ❌ | ❌ |` and add after the 015 row:

```markdown
| [Live delay sync](features/016-live-delay-sync.md) | 🧪 | ❌ | ❌ |
```

Read `PARITY.md` first; the ESP32 session edits it too — change only these two rows.

- [ ] **Step 5: CHANGELOG** — under `[Unreleased]` → `### Added`:

```markdown
- **Live delay Sync** (beta): a Sync button next to the LED delay slider. Tap it when the lights go out on your broadcast; the box sets the delay to the time since the feed reported lights-out (to 0.1 s), saves it, and plays the race-start animation right then. Works within 90 s of a race or sprint start. New route `POST /api/delay/sync`. The delay slider now moves in 0.1 s steps
```

and under `### Fixed`:

```markdown
- The race-start animation now plays at live lights-out (beta). It never played live when the box connected before the race (the feed's pre-race "Inactive" status blocked it), and played too early when an All Clear arrived before the start
```

- [ ] **Step 6: Commit both repos**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog for live delay Sync"
cd ../raceflag_specs
git add features/016-live-delay-sync.md features/004-race-start.md contracts/api.md PARITY.md
git commit -m "spec: 016 Live delay sync (beta on Pi); 004 Race-start — live lights-out fix"
```

- [ ] **Step 7: Final full run**

Run: `PYTHONUTF8=1 python -m pytest tests -q -p no:warnings`
Expected: only the six baseline failures.
