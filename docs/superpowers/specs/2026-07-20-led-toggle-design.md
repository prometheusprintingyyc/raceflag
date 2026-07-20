# LED Toggle Design

## Problem

There is no way to turn the LED strip off without shutting down RaceFlag entirely. Users may want the app to continue running — tracking live timing, serving the web UI — while keeping the LED dark (e.g. watching a stream in a dark room, or just not wanting the light on).

---

## Solution

Add an LED Strip on/off toggle to the Settings panel. Toggling off turns the strip dark immediately; the app continues running normally. Toggling back on resumes whatever animation was active.

---

## Behaviour

- **Toggle OFF:** All LEDs go dark immediately. Flag state, timing feed, and web UI continue uninterrupted. Internal animation state is preserved — if a yellow flag is active, the strip will resume the yellow animation when re-enabled.
- **Toggle ON:** Resumes current animation state immediately (idle, flag animation, or timed effect).
- **Hotspot mode:** The white flashing setup animation always shows regardless of toggle state. The LED toggle only applies during normal operation.
- **Test Effects:** Test effect buttons will appear to do nothing when the LED is off (the run loop overrides with black). No special blocking of the API call is needed — the behaviour is self-evident from the UI state.
- **Persistence:** Toggle state is not saved to `config.json`. The strip always starts enabled on boot, matching the Demo Mode pattern.

---

## Backend — `LEDController` (`raceflag/led_controller.py`)

**New field in `__init__`:**

```python
self._led_enabled: bool = True
```

**New method:**

```python
def set_led_enabled(self, enabled: bool) -> None:
    self._led_enabled = enabled
    if not enabled:
        self._strip.fill(0, 0, 0)
        self._strip.show()
```

When disabling, the strip is blanked immediately (not waiting for the next `_run()` tick).

**`_run()` loop changes:**

Hotspot animation is checked first and always renders. Then, if `_led_enabled` is `False`, fill black and continue — skipping all other animation logic. The queue is still drained so flag events are tracked while the LED is off.

```python
def _run(self) -> None:
    while not self._stop_event.is_set():
        self._maybe_reload_effects()
        self._drain_queue()
        if self._active_animation == "hotspot":
            self._step_hotspot_animation()
            time.sleep(0.05)
            continue
        if not self._led_enabled:
            self._strip.fill(0, 0, 0)
            self._strip.show()
            time.sleep(0.05)
            continue
        now = time.monotonic()
        # ... existing timed_effect / active_animation / idle logic unchanged ...
```

**`_drain_queue()` change:**

Guard `_apply_effect()` with `if self._led_enabled` to prevent a one-frame flash when a static effect fires while the LED is off:

```python
if flag_state in self._CONTINUOUS_ANIMATIONS:
    self._active_animation = flag_state
else:
    self._active_animation = ""
    if self._led_enabled:
        self._apply_effect(flag_state)
```

---

## Backend — `AppState` (`raceflag/state.py`)

**New field:**

```python
led_enabled: bool = True
```

**New setter:**

```python
def set_led_enabled(self, enabled: bool) -> None:
    with self._lock:
        self.led_enabled = enabled
```

**`to_dict()` addition:**

```python
"led_enabled": self.led_enabled,
```

---

## Backend — `web_server.py`

**New request model:**

```python
class LEDEnabledRequest(BaseModel):
    enabled: bool
```

**New endpoint:**

```python
@app.post("/api/led/enabled")
async def set_led_enabled(req: LEDEnabledRequest):
    state.set_led_enabled(req.enabled)
    led.set_led_enabled(req.enabled)
    return {"led_enabled": req.enabled}
```

---

## Frontend — `raceflag/frontend/index.html`

New `settings-row` added between the Demo Mode row and the Test Effects row:

```html
<div class="settings-row">
  <span class="settings-label">LED Strip</span>
  <button class="btn-toggle" id="btn-led-toggle">ON</button>
</div>
```

Uses the existing `btn-toggle` CSS class — no new styles needed.

---

## Frontend — `raceflag/frontend/app.js`

Follows the exact same pattern as `_setDemoMode` / `btn-demo-mode`.

**Helper function:**

```javascript
function _setLedEnabled(enabled) {
  const btn = document.getElementById('btn-led-toggle');
  if (!btn) return;
  btn.textContent = enabled ? 'ON' : 'OFF';
  if (enabled) btn.classList.add('on');
  else btn.classList.remove('on');
}
```

**Initialization (in the state poll handler, alongside `_setDemoMode`):**

```javascript
_setLedEnabled(!!data.led_enabled);
```

**Click handler:**

```javascript
document.getElementById('btn-led-toggle').addEventListener('click', async () => {
  const btn = document.getElementById('btn-led-toggle');
  const enabling = !btn.classList.contains('on');
  await fetch('/api/led/enabled', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: enabling }),
  });
  await fetchState();
});
```

`fetchState()` is called immediately after the POST so the button updates without waiting for the next poll tick — matching the Demo Mode pattern.

---

## Files Changed

| File | Change |
|---|---|
| `raceflag/led_controller.py` | `_led_enabled` field; `set_led_enabled()` method; `_run()` hotspot-first + disabled-blackout logic; `_drain_queue()` guarded `_apply_effect()` |
| `raceflag/state.py` | `led_enabled` field; `set_led_enabled()` method; `to_dict()` addition |
| `raceflag/web_server.py` | `LEDEnabledRequest` model; `POST /api/led/enabled` endpoint |
| `raceflag/frontend/index.html` | New settings row for LED Strip toggle |
| `raceflag/frontend/app.js` | State poll initialization + click handler |
| `tests/test_led_controller.py` | Tests for `set_led_enabled()`, disabled blackout, hotspot override |
| `CHANGELOG.md` | Update `[Unreleased]` section |

---

## Out of Scope

- Persisting toggle state to `config.json`
- Blocking test effect API calls when LED is off
- Any change to the LED brightness or animation parameters
