# Live Delay Sync — Design

**Date:** 2026-10-16
**Branch:** `beta`
**Shared spec:** new feature page `016-live-delay-sync.md`; changes to `004-race-start.md`

## Goal

Setting the LED delay for a live race is currently trial and error. With **Sync**, the
user taps a button the moment the lights go out on their broadcast. The box works out the
delay from when the live feed reported lights-out, saves it, shows it on the delay slider
and plays the race-start animation right then, as replay mode does.

Sync is meant to be used once, at the start of one race. The delay it sets is usually close
enough for later races too, so people rarely adjust it again.

## Decisions (agreed with the user)

- Race and Sprint sessions only.
- The broadcast is always behind the feed. There is no "pressed before the feed reported
  lights-out" path that sets a delay; Sync just says the race hasn't started yet.
- Only the race's single lights-out matters. No handling for aborted starts.
- The box timestamps the press when the request arrives (not the phone's clock).
- Sync works within 90 s of lights-out (the delay slider's maximum).
- The synced delay is saved like a manual slider change (`delay_seconds` in
  `config.json`). It persists across races and reboots until the user syncs again or
  moves the slider; nothing in this feature ever resets it.
- The live lights-out bug (below) is fixed as part of this feature.

## Background: live lights-out is broken today

In a live Race/Sprint, `SessionStatus "Started"` is lights-out. Verified against the real
Belgian GP 2024 recording: `Started` at 0:55:14.764, `GREEN LIGHT - PIT EXIT OPEN` 0.1 s
later, and lap 2 began 1:52.1 after `Started` (a standing-start lap 1 at Spa).

The listener's handling of it doesn't work:

1. **Box connected before the race (normal case).** The feed's initial snapshot reports
   `SessionStatus "Inactive"`, which `f1_listener.py:201-205` turns into track status
   `finished` for a race. At `Started`, the `track_status in ("break", "finished")` branch
   (`f1_listener.py:211`) runs instead of the lights-out branch: it reports `unknown`, and
   the race-start animation never plays.
2. **A TrackStatus AllClear arrives before the start** (e.g. during the formation lap).
   `main.py:228-233` promotes that first AllClear to `race_start`, so the animation plays
   before the start, and `Started` then reports a plain `track_clear`.

## Behaviour

### Lights-out (live)

- In a live Race or Sprint session, `SessionStatus "Started"` is lights-out, whatever the
  current track status is. The listener:
  - records `state.lights_out_at = time.monotonic()` (runtime only, never saved);
  - sets track status `track_clear`;
  - reports `race_start` to `on_flag_change` (instead of `track_clear` for promotion).
- `on_flag_change` handles `race_start` like any timed effect: after the LED delay it
  updates the displayed status and plays the 30 s race-start animation.
- The track_clear → race_start promotion in `main.py` applies **only in replay mode**
  (replay still relies on it). A live AllClear is always a plain `track_clear`.
- `Started` in a non-race session keeps today's behaviour (clears a `break`/`finished`
  status to `unknown`).
- The lights-out **timestamp** (`lights_out_at`) is cleared when the session ends
  (`finished`/`break`), so a Sync pressed before the next race can't measure from the
  previous race's lights-out. This does **not** touch the delay.

### Sync

The user presses **Sync**. With `now = time.monotonic()` when the request arrives:

| Situation | Result |
|---|---|
| No live Race/Sprint (session not live, not a race/sprint, or replay mode) | Nothing changes. 409 `"Sync needs a live race or sprint"` |
| Race/Sprint live, `lights_out_at` not set | Nothing changes. 409 `"The race hasn't started yet"` |
| `now − lights_out_at > 90` s | Nothing changes. 409 `"Sync works within 90 s of the race start"` |
| Otherwise | Delay = `round(now − lights_out_at, 1)`, clamped to 0–90 |

On success:
1. `config.delay_seconds` is set and saved, and `led.set_delay()` applied — the same as
   `POST /api/config/delay`.
2. The pending delayed race-start (scheduled at lights-out with the old delay) is
   cancelled, so the animation doesn't play twice.
3. The displayed track status is set to `track_clear`, and the race-start animation plays
   now (`led.trigger_timed("race_start", 30.0)`).

Pressing Sync again inside the window recalculates from the same lights-out (no special
handling; not a designed use).

Flag changes the feed sent after lights-out are re-timed onto the new delay, LEDs and
displayed status, as if the new delay had been set before the start: each takes effect at
`received_at + new_delay`. FlagRouter logs every live (non-replay) flag change since the
last live `race_start` (cleared by an idle status) and tracks every pending delayed task.
Sync cancels all pending delayed tasks, sets the delay, shows `track_clear`, plays
`race_start` with a full LED-queue flush, then for each logged change re-queues continuous
flags with their original arrival time (`led.trigger_at`) and reschedules the display /
timed-effect update for `max(0, received_at + new_delay − now)`. Since the press is at
`lights_out_at + new_delay` and every logged change arrived after lights-out, none is
overdue.

## API

`POST /api/delay/sync` — no body.

- **200:** `{"delay_seconds": 34.2}`
- **409:** `{"detail": "<message from the table above>"}`

`POST /api/config/delay` is unchanged (0.0–90.0, float).

## UI

- A **Sync** button next to the LED delay slider, hidden in replay mode (where the slider
  is the replay Sync Offset).
- On success the slider and number show the new delay (e.g. `34.2`) and a short message
  confirms it ("Delay set to 34.2 s"). On 409 the message shows the reason.
- The live delay slider moves in 0.1 s steps so it can show the synced value.

## Components

| File | Change |
|---|---|
| `raceflag/state.py` | `lights_out_at: float | None` |
| `raceflag/f1_listener.py` | Live race `Started` → record lights-out, report `race_start`; clear `lights_out_at` on session end |
| `raceflag/main.py` | Promotion only in replay; keep the delayed race-start task cancellable; provide the "race start now" action to the web server |
| `raceflag/delay_sync.py` (new) | Pure function: `(now, lights_out_at, race_live) → delay or reason` |
| `raceflag/web_server.py` | `POST /api/delay/sync` |
| `raceflag/frontend/*` | Sync button, message, 0.1 s slider step |

## Testing

- Listener: both Belgian GP sequences above (Inactive snapshot, then `Started`; live AllClear,
  then `Started`) report `race_start` exactly once, at `Started`, and record `lights_out_at`.
  Non-race `Started` unchanged. Session end clears `lights_out_at` and leaves
  `delay_seconds` unchanged (in memory and in `config.json`).
- `delay_sync`: each row of the table, the 0.1 s rounding, and the 90 s boundary.
- Route: success saves and applies the delay, cancels the pending race-start, and plays it
  once; each 409 changes nothing.
- Conformance: case `002-race-start-promotion` changes (a live AllClear no longer promotes);
  it gets a `SessionStatus.jsonStream` with `Started`, and a new case covers lights-out
  after an `Inactive` status. The Pi conformance runner's promotion logic is updated to
  match `main.py`.

## Specs to update (raceflag_specs)

- New `features/016-live-delay-sync.md` (`**Status:** beta on Pi`).
- `features/004-race-start.md`: `> **Beta (Pi):**` callout for the live lights-out change.
- `contracts/api.md`: `POST /api/delay/sync`.
- `conformance/`: case 002 updated, new lights-out-after-Inactive case.
- `PARITY.md`: 016 row `🧪 | ❌ | ❌`; 004 Pi → 🧪, ESP32 columns ❌.

## Out of scope

- Practice and qualifying session starts.
- Aborted starts and red-flag restarts.
- Replay data access (the archive now needs F1TV authentication) — a separate piece of work.
