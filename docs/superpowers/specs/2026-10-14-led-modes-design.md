# LED Modes, Lamp & Schedule Design

## Problem

When no session is live, the LED only knows two states: the idle chase, or off (the LED
Strip toggle). Owners want to use the lightbox as a lamp, have it switch itself on and off
on a daily schedule, and still have it light up for every live session — without the
schedule hiding a session that happens during off hours.

---

## Solution

Replace the LED Strip on/off toggle with an **LED mode** selector — **On · Auto · Lamp ·
Off** — plus two popups: a **Lamp** popup (colour + brightness with live preview) and a
**Schedule** popup (one on time and one off time, same every day). The Pi keeps its local
time correct by syncing the timezone and clock from the browser on every page load.

Shared behaviour spec: `../raceflag_specs/features/014-led-modes.md`.
UI mockup: https://claude.ai/artifact/U6S9D8Eft1134wSgmmTMRe

---

## Behaviour

### Modes

| Mode | No session | Live session or replay | Session ends |
|---|---|---|---|
| **On** | Idle chase, 24/7 | Flag effects; chase between flags | Timed effect finishes, back to chase |
| **Auto** | Chase between `schedule_on` and `schedule_off`, dark outside | Flag effects; chase between flags — even in the off window | Timed effect finishes, then chase or dark per the schedule |
| **Lamp** | Solid `lamp_color` at `lamp_brightness` | Same as On: flag effects, chase between flags | Timed effect finishes, back to the lamp colour |
| **Off** | Dark | Dark | Dark |

- **Boot:** the mode always starts as **On**. The mode is not saved — the chase at boot is
  the owner's sign that the unit started correctly. Schedule times and lamp settings *are*
  saved, so returning to Auto or Lamp is one tap.
- **Hotspot animation** shows in every mode, including Off (unchanged).
- **Test effects** show in On, Auto and Lamp — including Auto's off window, because an
  active effect counts as "something to show". In Off they stay dark, as they do today
  with the LED toggle off.
- **"Session live"** = `state.session.is_active or state.replay_mode`.
- **Schedule window** is half-open `[on, off)` in local time. `off < on` crosses midnight.
  `on == off` is rejected by the API and the UI.
- **Lamp brightness** 0–100 % scales the RGB values; the existing hardware cap
  (`led_brightness: 128`) still applies on top, so 100 % lamp = 50 % physical.

### Lamp preview

While the Lamp popup is open the strip previews the draft colour/brightness without saving.
Save stores it; Cancel ends the preview; a preview with no update for **60 s** ends on its
own (browser tab closed mid-edit). During a live session the session wins and the preview
is not visible — the popup says "Live session – preview paused".

### Time sync

On every page load the UI posts the browser's timezone
(`Intl.DateTimeFormat().resolvedOptions().timeZone`) and `Date.now()`:
- The timezone is saved to `config.json` if it differs from the stored one.
- The system clock is set (`date -u -s @<epoch>`) only if it is more than **60 s** off.
  NTP (`systemd-timesyncd`) keeps correcting it as normal.
- RaceFlag computes local time itself with `zoneinfo` from the saved timezone; the system
  timezone is never changed (overlayroot would discard it at reboot anyway).
- With no timezone saved yet, Auto uses UTC until the first page load.

---

## Backend — new module `raceflag/led_modes.py`

Pure functions, no I/O, so every rule in the table is unit-testable.

```python
MODES = ("on", "auto", "lamp", "off")

def in_window(now_min: int, on_min: int, off_min: int) -> bool:
    if on_min < off_min:
        return on_min <= now_min < off_min
    return now_min >= on_min or now_min < off_min

def decide_output(mode: str, session_live: bool, effect_active: bool,
                  scheduled_on: bool, preview_active: bool) -> str:
    """Returns "normal" (existing flag/timed/chase logic), "lamp_preview", "lamp" or "dark"."""
    if mode == "off":
        return "dark"
    if session_live or effect_active:
        return "normal"
    if preview_active:
        return "lamp_preview"
    if mode == "on":
        return "normal"
    if mode == "auto":
        return "normal" if scheduled_on else "dark"
    return "lamp"

def parse_hhmm(value: str) -> int: ...      # "21:30" -> 1290, ValueError if invalid
def scale_rgb(hex_color: str, brightness: int) -> tuple[int, int, int]: ...
```

`effect_active` = a timed effect is running or a continuous flag animation is set
(`_timed_effect or _active_animation`).

---

## Backend — `raceflag/clock.py` (new)

```python
def local_minutes(tz_name: str, now: datetime | None = None) -> int: ...
def sync(tz_name: str, epoch_ms: int, cfg: Config, cfg_path: Path,
         set_clock=_set_system_clock) -> dict: ...
```

`sync` validates the timezone with `zoneinfo.ZoneInfo` (unknown → `ValueError`), saves it
when changed, and calls `set_clock(epoch_s)` when `abs(time.time() - epoch_s) > 60`.
`_set_system_clock` runs `date -u -s @<epoch>` via `subprocess.run`; tests inject a fake.

---

## Backend — `LEDController` (`raceflag/led_controller.py`)

- **Replace** `_led_enabled` / `set_led_enabled()` with `_mode` (default `"on"`) and
  `set_mode(mode)`.
- **New state:** `_schedule = (on_min, off_min)`, `_timezone`, `_lamp = (hex, brightness)`,
  `_preview = (hex, brightness) | None`, `_preview_expiry`.
- **New methods:** `set_schedule(on, off)`, `set_timezone(name)`, `set_lamp(hex, b)`,
  `start_preview(hex, b)` (refreshes the 60 s expiry), `end_preview()`.
- **Constructor** gains `is_session_live: Callable[[], bool]`, wired in `main.py` to
  `lambda: state.session.is_active or state.replay_mode`.
- **`_run()`:** hotspot first (unchanged). Then compute
  `decide_output(...)` once per tick and branch:
  `"dark"` → fill black; `"lamp"` / `"lamp_preview"` → fill `scale_rgb(...)`;
  `"normal"` → the existing timed-effect / active-animation / idle-chase logic, unchanged.
  The queue is still drained every tick in every mode, so flag state stays current.
- **`_drain_queue()`:** the `if self._led_enabled` guard on `_apply_effect()` becomes
  `if self._mode != "off"`.
- Local minutes are recomputed at most once per second (cheap, and the schedule has
  minute resolution).

---

## Backend — `Config` (`raceflag/config.py`)

```python
schedule_on: str = "08:00"
schedule_off: str = "23:00"
lamp_color: str = "#FFFFFF"
lamp_brightness: int = 100
timezone: str = ""
```

Existing `config.json` files without these keys load with the defaults (the loader already
ignores unknown keys and defaults missing ones).

## Backend — `AppState` (`raceflag/state.py`)

`led_enabled` → `led_mode: str = "on"`; `set_led_enabled()` → `set_led_mode()`;
`to_dict()` emits `"led_mode"` instead of `"led_enabled"`.

---

## Backend — `web_server.py`

| Route | Body | Response | Notes |
|---|---|---|---|
| `POST /api/led/mode` | `{"mode": "on"\|"auto"\|"lamp"\|"off"}` | `{"led_mode": ...}` | Replaces `POST /api/led/enabled` (removed). 422 on unknown mode. Not persisted. |
| `POST /api/config/schedule` | `{"on": "HH:MM", "off": "HH:MM"}` | `{"schedule_on", "schedule_off"}` | 422 on bad format or `on == off`. Saves and applies. |
| `POST /api/led/lamp/preview` | `{"color": "#RRGGBB", "brightness": 0–100}` | `{"preview": true}` | Not saved. 60 s expiry, refreshed by each call. |
| `DELETE /api/led/lamp/preview` | — | `{"preview": false}` | Cancel. |
| `POST /api/config/lamp` | `{"color", "brightness"}` | `{"lamp_color", "lamp_brightness"}` | Saves, applies, ends any preview. |
| `POST /api/time/sync` | `{"timezone": "...", "epoch_ms": int}` | `{"timezone", "clock_adjusted": bool}` | 422 on unknown timezone. |

`GET /api/config` gains the five new keys. `GET /api/state` swaps `led_enabled` for
`led_mode`. These routes have the same trust model as the rest of the API (local network,
no auth); setting the clock is bounded to what the browser reports.

---

## Frontend — `index.html` / `app.js` / `style.css`

- **Settings row:** "LED Strip" toggle → "LED Mode" row: a four-segment pill
  (`On · Auto · Lamp · Off`, same look as `.seg-toggle`; Off highlights red, the others
  green), a short hint line (e.g. "On 8:00 AM, off 11:00 PM", "White · 80%"), and a ⚙
  button shown only in Auto and Lamp. All other rows (version, feed, updates, demo mode,
  test effects, shut down / manual / logs) are unchanged.
- **Tapping Auto or Lamp** posts the mode and opens that mode's popup. ⚙ reopens it.
- **Lamp popup** (`.confirm-dialog` style): swatches red `#FF0000`, yellow `#FFD700`,
  green `#00FF00`, white `#FFFFFF`, and a Custom swatch wrapping `<input type="color">`;
  a 0–100 % brightness slider; Cancel / Save. Changes post to the preview route,
  throttled to one request per 150 ms. Shows "Live session – preview paused" when the
  state says a session or replay is live.
- **Schedule popup:** two `<input type="time">` (device 12/24 h display), a "Lit for N h a
  day[, crossing midnight]" line, Save disabled with an inline error when the times match.
- **Time sync:** on `DOMContentLoaded`, post `/api/time/sync` once. Failures are ignored.
- Times are formatted for the hint line with `toLocaleTimeString`.

---

## Testing

- `tests/test_led_modes.py` — `decide_output` for every row of the mode table;
  `in_window` incl. midnight crossing and boundaries; `parse_hhmm`; `scale_rgb`.
- `tests/test_clock.py` — timezone saved only when changed; clock set only when > 60 s
  off (fake setter); unknown timezone rejected; UTC fallback.
- `tests/test_led_controller.py` — mode switching; Off stays dark during a flag; Auto
  off-window dark, lit during session, dark after timed effect expires; lamp fill; preview
  expiry; hotspot overrides Off.
- `tests/test_web_server.py` — each new route incl. 422 cases; `/api/led/enabled` gone;
  `/api/state` has `led_mode`.
- `tests/test_config.py` — old config without new keys loads defaults.
- Conformance cases: unchanged (modes change output, not flag decisions).
- Hardware (beta unit): boot shows chase; each mode from a phone; Auto boundary crossing
  (off time 2 min ahead); replay during off window lights then darkens after chequered.

---

## Build order (all on `beta`)

1. **Settings & time sync** — config keys, `clock.py`, `/api/time/sync`, UI sync on load.
2. **Modes & Lamp** — `led_modes.py`, controller modes, `/api/led/mode`, lamp routes,
   mode pill, ⚙, Lamp popup. Removes the LED Strip toggle and `/api/led/enabled`.
3. **Auto** — schedule routes, schedule popup, session override in the off window.

Each phase updates `CHANGELOG.md` `[Unreleased]` and the matching spec pages in
`raceflag_specs` (014 status, beta callouts on 008/010 and `contracts/api.md`, 🧪 in
`PARITY.md`).

---

## Files Changed

| File | Change |
|---|---|
| `raceflag/led_modes.py` | New: pure mode/schedule/colour logic |
| `raceflag/clock.py` | New: local time + browser time sync |
| `raceflag/led_controller.py` | Modes replace `_led_enabled`; lamp/preview/schedule state; `_run()` decision branch |
| `raceflag/config.py` | Five new fields |
| `raceflag/state.py` | `led_enabled` → `led_mode` |
| `raceflag/web_server.py` | Six routes; remove `/api/led/enabled` |
| `raceflag/main.py` | Pass `is_session_live`, initial schedule/lamp/timezone to the controller |
| `raceflag/frontend/index.html`, `app.js`, `style.css` | Mode row, two popups, time sync |
| `tests/…` | As listed under Testing |
| `CHANGELOG.md` | `[Unreleased]` per phase |

---

## Out of Scope

- Per-day or multiple schedules
- A warm-white preset (RGB strip has no white channel; Custom covers it)
- Saving the mode across reboots
- Manual time entry or a clock-sync status display
- Raising or enforcing the hardware brightness cap
- ESP32 implementation (tracked in `raceflag_specs/PARITY.md`)
