# LED Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an LED Strip on/off toggle to the Settings panel that darkens the LEDs immediately while keeping RaceFlag and the web UI fully active.

**Architecture:** `LEDController` gains an `_led_enabled` flag — the `_run()` loop skips all animation logic when disabled (filling black instead), except the hotspot animation which always renders. `AppState` tracks the toggle state and exposes it via `/api/state`. A new `POST /api/led/enabled` endpoint wires them together. The frontend follows the exact Demo Mode pattern for the UI element and click handler.

**Tech Stack:** Python (asyncio/dataclasses/threading), FastAPI, vanilla JS/HTML, pytest

## Global Constraints

- Project root: `raceflag/` Python package; tests in `tests/`
- Test runner: `pytest` (no special flags needed; `asyncio_mode = auto` in `pytest.ini`)
- No new dependencies — use only the stdlib and packages already in `requirements.txt`
- No persistence to `config.json` — the toggle always starts ON after a reboot (matches Demo Mode)
- Hotspot animation (`_active_animation == "hotspot"`) must render regardless of `_led_enabled`
- Button label is `"ON"` / `"OFF"` (uppercase); button id is `btn-led-toggle`; CSS class `btn-toggle` with `.on` modifier — exact same pattern as `btn-demo-mode`
- New API endpoint: `POST /api/led/enabled` with body `{"enabled": bool}`, returns `{"led_enabled": bool}`
- `AppState.to_dict()` must include `"led_enabled"` so the frontend can read it from the existing `/api/state` poll
- Always update `CHANGELOG.md` `[Unreleased]` section after code changes

---

### Task 1: LEDController — `_led_enabled` toggle

**Files:**
- Modify: `raceflag/led_controller.py`
- Test: `tests/test_led_controller.py`

**Interfaces:**
- Produces: `LEDController.set_led_enabled(enabled: bool) -> None` and `LEDController._led_enabled: bool` (consumed by Task 2's endpoint)

---

- [ ] **Step 1: Write the failing tests**

Add these tests at the bottom of `tests/test_led_controller.py`:

```python
def test_led_enabled_defaults_to_true(controller):
    assert controller._led_enabled is True


def test_set_led_enabled_false_blanks_strip(controller):
    controller._strip.set_pixel(0, 255, 0, 0)
    controller.set_led_enabled(False)
    assert all(p == (0, 0, 0) for p in controller._strip.pixels)


def test_set_led_enabled_false_calls_show(controller):
    show_before = controller._strip.show_calls
    controller.set_led_enabled(False)
    assert controller._strip.show_calls == show_before + 1


def test_set_led_enabled_true_does_not_blank_strip(controller):
    controller._strip.set_pixel(0, 255, 0, 0)
    controller.set_led_enabled(True)
    assert controller._strip.pixels[0] == (255, 0, 0)


def test_run_blanks_strip_when_led_disabled(controller):
    controller._effects = controller._load_effects()
    controller.set_led_enabled(False)
    controller.start()
    time.sleep(0.15)
    controller.stop()
    assert all(p == (0, 0, 0) for p in controller._strip.pixels)


def test_run_hotspot_animation_runs_when_led_disabled(controller):
    from unittest.mock import patch
    controller._effects = controller._load_effects()
    controller.set_led_enabled(False)
    controller.set_hotspot_mode(True)
    with patch.object(controller, '_step_hotspot_animation') as mock_anim:
        controller.start()
        time.sleep(0.15)
        controller.stop()
    assert mock_anim.call_count > 0


def test_drain_queue_skips_apply_effect_when_led_disabled(controller):
    controller._effects = controller._load_effects()
    controller.set_led_enabled(False)
    controller.trigger("track_clear")
    controller._drain_queue()
    assert all(p == (0, 0, 0) for p in controller._strip.pixels)


def test_drain_queue_tracks_continuous_animation_when_led_disabled(controller):
    controller._effects = controller._load_effects()
    controller.set_led_enabled(False)
    controller.trigger("yellow_flag")
    controller._drain_queue()
    assert controller._active_animation == "yellow_flag"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_led_controller.py::test_led_enabled_defaults_to_true tests/test_led_controller.py::test_set_led_enabled_false_blanks_strip tests/test_led_controller.py::test_set_led_enabled_false_calls_show tests/test_led_controller.py::test_set_led_enabled_true_does_not_blank_strip tests/test_led_controller.py::test_run_blanks_strip_when_led_disabled tests/test_led_controller.py::test_run_hotspot_animation_runs_when_led_disabled tests/test_led_controller.py::test_drain_queue_skips_apply_effect_when_led_disabled tests/test_led_controller.py::test_drain_queue_tracks_continuous_animation_when_led_disabled -v
```

Expected: all FAIL with `AttributeError: 'LEDController' object has no attribute '_led_enabled'`

- [ ] **Step 3: Add `_led_enabled` to `__init__`**

In `raceflag/led_controller.py`, inside `LEDController.__init__` (after `self._active_animation`), add:

```python
self._led_enabled: bool = True
```

- [ ] **Step 4: Add `set_led_enabled` method**

Add this method to `LEDController` (after `set_hotspot_mode`):

```python
def set_led_enabled(self, enabled: bool) -> None:
    self._led_enabled = enabled
    if not enabled:
        self._strip.fill(0, 0, 0)
        self._strip.show()
```

- [ ] **Step 5: Update `_run()` — hotspot-first + disabled-blackout**

Replace the entire `_run` method with:

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
        if self._timed_effect:
            if now >= self._timed_effect_expiry:
                self._timed_effect = ""
                self._idle_active = True
            else:
                if self._timed_effect == "race_start":
                    self._step_race_start_animation()
                elif self._timed_effect == "checkered":
                    self._step_checkered_animation()
                else:
                    self._step_track_clear_animation()
        elif self._active_animation:
            if self._active_animation == "red_flag":
                self._step_red_flag_animation()
            elif self._active_animation == "yellow_flag":
                self._step_yellow_flag_animation()
            elif self._active_animation == "safety_car":
                self._step_safety_car_animation()
            elif self._active_animation == "virtual_sc":
                self._step_virtual_sc_animation()
        elif self._idle_active:
            self._step_idle_animation()
        time.sleep(0.05)
```

Note: `elif self._active_animation == "hotspot": self._step_hotspot_animation()` is removed from the elif chain — hotspot is now handled at the top before the `_led_enabled` check.

- [ ] **Step 6: Update `_drain_queue()` — guard `_apply_effect`**

In `_drain_queue`, find this block (inside the `if waited >= self._delay_seconds:` branch):

```python
            if flag_state in self._CONTINUOUS_ANIMATIONS:
                self._active_animation = flag_state
            else:
                self._active_animation = ""
                self._apply_effect(flag_state)
```

Replace with:

```python
            if flag_state in self._CONTINUOUS_ANIMATIONS:
                self._active_animation = flag_state
            else:
                self._active_animation = ""
                if self._led_enabled:
                    self._apply_effect(flag_state)
```

- [ ] **Step 7: Run all new tests**

```
pytest tests/test_led_controller.py -v
```

Expected: all PASS. The existing test `test_run_dispatches_race_start_animation` should still pass — the timed effect path is unchanged.

- [ ] **Step 8: Commit**

```bash
git add raceflag/led_controller.py tests/test_led_controller.py
git commit -m "feat: add LED enabled toggle to LEDController"
```

---

### Task 2: AppState + web_server API

**Files:**
- Modify: `raceflag/state.py`
- Modify: `raceflag/web_server.py`
- Test: `tests/test_state.py`
- Test: `tests/test_web_server.py`

**Interfaces:**
- Consumes: `LEDController.set_led_enabled(enabled: bool) -> None` (from Task 1)
- Produces: `AppState.led_enabled: bool`, `AppState.set_led_enabled(enabled: bool) -> None`, `POST /api/led/enabled`

---

- [ ] **Step 1: Write the failing tests for `AppState`**

Add to the bottom of `tests/test_state.py`:

```python
def test_led_enabled_defaults_to_true():
    s = AppState()
    assert s.led_enabled is True


def test_set_led_enabled_updates_state():
    s = AppState()
    s.set_led_enabled(False)
    assert s.led_enabled is False


def test_to_dict_includes_led_enabled():
    s = AppState()
    d = s.to_dict()
    assert "led_enabled" in d
    assert d["led_enabled"] is True
```

- [ ] **Step 2: Write the failing tests for the web server**

Add to the bottom of `tests/test_web_server.py`:

```python
def test_set_led_enabled_returns_200(client):
    resp = client.post("/api/led/enabled", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["led_enabled"] is False


def test_set_led_enabled_updates_app_state(client, app_state):
    client.post("/api/led/enabled", json={"enabled": False})
    assert app_state.led_enabled is False


def test_set_led_enabled_updates_led_controller(client, led):
    client.post("/api/led/enabled", json={"enabled": False})
    assert led._led_enabled is False


def test_get_state_includes_led_enabled(client):
    resp = client.get("/api/state")
    assert "led_enabled" in resp.json()
```

- [ ] **Step 3: Run tests to verify they fail**

```
pytest tests/test_state.py::test_led_enabled_defaults_to_true tests/test_state.py::test_set_led_enabled_updates_state tests/test_state.py::test_to_dict_includes_led_enabled tests/test_web_server.py::test_set_led_enabled_returns_200 tests/test_web_server.py::test_set_led_enabled_updates_app_state tests/test_web_server.py::test_set_led_enabled_updates_led_controller tests/test_web_server.py::test_get_state_includes_led_enabled -v
```

Expected: FAIL — `AttributeError` on `AppState` and 404 on the endpoint.

- [ ] **Step 4: Add `led_enabled` field to `AppState`**

In `raceflag/state.py`, in the `AppState` dataclass, add the field after `demo_mode`:

```python
led_enabled: bool = True
```

- [ ] **Step 5: Add `set_led_enabled` method to `AppState`**

In `raceflag/state.py`, add this method to `AppState` after `set_demo_mode`:

```python
def set_led_enabled(self, enabled: bool) -> None:
    with self._lock:
        self.led_enabled = enabled
```

- [ ] **Step 6: Add `led_enabled` to `AppState.to_dict()`**

In `raceflag/state.py`, in `to_dict()`, add after `"demo_mode": self.demo_mode,`:

```python
"led_enabled": self.led_enabled,
```

- [ ] **Step 7: Add `LEDEnabledRequest` model to `web_server.py`**

In `raceflag/web_server.py`, add this Pydantic model after `DemoModeRequest`:

```python
class LEDEnabledRequest(BaseModel):
    enabled: bool
```

- [ ] **Step 8: Add `POST /api/led/enabled` endpoint**

In `raceflag/web_server.py`, inside `create_app`, add this endpoint after the `set_demo_mode` endpoint:

```python
@app.post("/api/led/enabled")
async def set_led_enabled(req: LEDEnabledRequest):
    state.set_led_enabled(req.enabled)
    led.set_led_enabled(req.enabled)
    return {"led_enabled": req.enabled}
```

- [ ] **Step 9: Run all new tests**

```
pytest tests/test_state.py tests/test_web_server.py -v
```

Expected: all PASS. Existing tests must not regress.

- [ ] **Step 10: Run the full test suite**

```
pytest -v
```

Expected: all PASS.

- [ ] **Step 11: Commit**

```bash
git add raceflag/state.py raceflag/web_server.py tests/test_state.py tests/test_web_server.py
git commit -m "feat: expose LED enabled state via AppState and POST /api/led/enabled"
```

---

### Task 3: Frontend + CHANGELOG

**Files:**
- Modify: `raceflag/frontend/index.html`
- Modify: `raceflag/frontend/app.js`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `POST /api/led/enabled` (from Task 2); `data.led_enabled` in `/api/state` response (from Task 2)

---

- [ ] **Step 1: Add the settings row to `index.html`**

In `raceflag/frontend/index.html`, find the Demo Mode settings row:

```html
      <div class="settings-row">
        <span class="settings-label">Demo Mode</span>
        <button class="btn-toggle" id="btn-demo-mode">OFF</button>
      </div>
```

Add the LED Strip row immediately after it (before the Test Effects row):

```html
      <div class="settings-row">
        <span class="settings-label">LED Strip</span>
        <button class="btn-toggle" id="btn-led-toggle">ON</button>
      </div>
```

The button starts as `ON` (the default state on load before the first poll).

- [ ] **Step 2: Add `_setLedEnabled` helper to `app.js`**

In `raceflag/frontend/app.js`, find the `_setDemoMode` function:

```javascript
function _setDemoMode(enabled) {
```

Add this new function immediately after the closing `}` of `_setDemoMode`:

```javascript
function _setLedEnabled(enabled) {
  const btn = document.getElementById('btn-led-toggle');
  if (!btn) return;
  btn.textContent = enabled ? 'ON' : 'OFF';
  if (enabled) btn.classList.add('on');
  else btn.classList.remove('on');
}
```

- [ ] **Step 3: Call `_setLedEnabled` in the state poll handler**

In `app.js`, find the line that calls `_setDemoMode`:

```javascript
  _setDemoMode(!!data.demo_mode);
```

Add the `_setLedEnabled` call immediately after it:

```javascript
  _setLedEnabled(!!data.led_enabled);
```

- [ ] **Step 4: Add the click handler**

In `app.js`, find the click handler for `btn-demo-mode`:

```javascript
document.getElementById('btn-demo-mode').addEventListener('click', async () => {
```

Add the LED toggle click handler immediately after the closing `});` of that handler:

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

- [ ] **Step 5: Update CHANGELOG.md**

Add to the `[Unreleased]` → `### Added` section in `CHANGELOG.md`:

```
- LED Strip on/off toggle in Settings — darkens the LED strip immediately while keeping the app and web UI active; hotspot setup mode always shows regardless of toggle state
```

- [ ] **Step 6: Run the full test suite**

```
pytest -v
```

Expected: all PASS. (Frontend changes have no automated tests — they are verified manually.)

- [ ] **Step 7: Commit**

```bash
git add raceflag/frontend/index.html raceflag/frontend/app.js CHANGELOG.md
git commit -m "feat: add LED strip on/off toggle to Settings UI"
```
