# WiFi Resilience Design

## Problem

Three independent bugs in `raceflag/wifi_manager.py` and `raceflag/frontend/setup.html` make the WiFi setup and recovery experience unreliable:

1. **Wrong-password dark period** — when the user enters wrong credentials, `connect()` disables the hotspot and calls nmcli, which can hang for 60+ seconds before failing. The hotspot is then not re-enabled for another 60 seconds (two ping failures in the monitor loop). Total dark period: 2+ minutes. During this time the LED is not flashing white and the user has no feedback.

2. **Over-aggressive hotspot trigger** — the monitor loop enables the hotspot after just 2 consecutive ping failures (60 seconds). A momentary network blip or a router reboot causes the Pi to broadcast RaceFlag-Setup unnecessarily.

3. **Wrong-password retry loop** — while the hotspot is active, the monitor loop scans for the configured SSID every 120 seconds. If found, it disables the hotspot and retries nmcli with the same wrong credentials. This repeats indefinitely: hotspot → find SSID → try wrong password → fail → ping fails → re-enable hotspot → repeat.

4. **False "Connected!" on setup page** — `setup.html` currently treats any network error from the `fetch('/api/wifi/connect')` call as a success (the fetch always fails because the hotspot is torn down before the response arrives). The page shows "Connected!" regardless of whether the connection actually worked.

5. **No password visibility toggle** — users cannot reveal what they have typed in the password field to check for typos.

---

## Solution

### Fix 1 — Immediate hotspot re-enable + setup page countdown

**Backend:** `connect()` currently calls `disable_hotspot()` then `_connect_to_configured()` and returns, leaving the hotspot off on failure. Fix: make `_connect_to_configured()` return `bool`, and if it returns `False`, call `enable_hotspot()` immediately inside `connect()`. Also wrap the `proc.communicate()` call in `asyncio.wait_for(timeout=CONNECT_TIMEOUT)` so a hung nmcli is killed after 30 seconds:

```python
try:
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=CONNECT_TIMEOUT)
except asyncio.TimeoutError:
    proc.kill()
    return False
```

No change to `_monitor_loop()` is needed for this fix — the monitor loop's hotspot-enable path remains as a safety net.

**Frontend:** Replace the current flow (fetch → success/failure on HTTP response) with a countdown-based flow:
- User submits credentials → page immediately shows "RaceFlag is attempting to connect…" with a countdown bar (duration: `CONNECT_COUNTDOWN_SEC = 45`)
- Below the bar: "If the LED strip is still flashing white after the countdown, the connection was unsuccessful. Reconnect to RaceFlag-Setup and try again."
- The `fetch('/api/wifi/connect', …)` is fire-and-forget (errors silently ignored) — the page does not wait for a response
- At end of countdown: `window.location.href = REDIRECT_URL` (`http://raceflag.local:8080`) — this redirect succeeds if the Pi joined home WiFi (phone is on home network too), and fails silently if not
- The LED strip is the authoritative success/failure indicator: flashing white = failed, solid/off = connected

The `s-success` HTML element and `showSuccess()` JS function are removed entirely — they are replaced by the countdown connecting state.

**Password visibility toggle:** Add a "Show / Hide" text button inline with each password field (`#pw-input` in `s-password`, `#manual-pw` in `s-manual`). Clicking it toggles the input's `type` attribute between `password` and `text`. No new CSS class needed.

---

### Fix 2 — Raise outage threshold for previously-connected devices

Add `_ever_connected: bool = False` to `WiFiManager.__init__`. Set it to `True` the first time `_check_connectivity()` returns `True` in the monitor loop.

In `_monitor_loop()`, replace the hardcoded `fail_count >= 2` with:

```python
threshold = RECONNECT_FAIL_THRESHOLD if self._ever_connected else CONNECT_FAIL_THRESHOLD
if fail_count >= threshold:
    ...
```

Constants (module-level):

```python
CONNECT_FAIL_THRESHOLD = 2      # 60 s — fast hotspot during initial setup
RECONNECT_FAIL_THRESHOLD = 10   # 300 s — tolerates a router reboot
```

---

### Fix 3 — Break wrong-password retry loop

Add `_hotspot_attempt_count: int = 0` to `WiFiManager.__init__`. This counter tracks consecutive auto-retry failures initiated by `_monitor_loop()` (not user-initiated `connect()` calls).

In `_monitor_loop()`, the hotspot branch:

```python
if self._hotspot_active:
    if await self._check_configured_available():
        await self.disable_hotspot()
        success = await self._connect_to_configured()
        if not success:
            self._hotspot_attempt_count += 1
            await self.enable_hotspot()
            if self._hotspot_attempt_count >= MAX_HOTSPOT_ATTEMPTS:
                logger.warning(
                    "WiFi connect failed %d times — clearing credentials",
                    self._hotspot_attempt_count,
                )
                self._config.wifi_ssid = ""
                self._config.wifi_password = ""
                if self._config_path:
                    save_config(self._config, self._config_path)
                self._hotspot_attempt_count = 0
        else:
            self._hotspot_attempt_count = 0
    await asyncio.sleep(120)
```

In `connect()` (user-initiated), reset the counter so a fresh credential attempt starts clean:

```python
self._hotspot_attempt_count = 0
```

Constant (module-level):

```python
MAX_HOTSPOT_ATTEMPTS = 3
```

---

## Constants Summary

New module-level constants in `wifi_manager.py` (Python):

| Constant | Value | Meaning |
|---|---|---|
| `CONNECT_TIMEOUT` | `30` | seconds to wait for nmcli before giving up |
| `CONNECT_FAIL_THRESHOLD` | `2` | ping failures before hotspot (never connected) |
| `RECONNECT_FAIL_THRESHOLD` | `10` | ping failures before hotspot (previously connected) |
| `MAX_HOTSPOT_ATTEMPTS` | `3` | auto-retries before credentials are cleared |

New JS constant in `setup.html`:

| Constant | Value | Meaning |
|---|---|---|
| `CONNECT_COUNTDOWN_SEC` | `45` | countdown bar duration; covers 30s nmcli timeout + hotspot restart time |

---

## Files Changed

| File | Change |
|---|---|
| `raceflag/wifi_manager.py` | `_connect_to_configured()` returns bool; `connect()` re-enables hotspot on failure; constants; Fix 2 threshold logic; Fix 3 retry counter |
| `raceflag/frontend/setup.html` | Countdown connecting state; fire-and-forget fetch; password show/hide toggle |
| `tests/test_wifi_manager.py` | New tests for timeout, immediate hotspot re-enable, threshold logic, retry loop clearing |
| `CHANGELOG.md` | Update `[Unreleased]` section |

---

## Out of Scope

- UI error message on the setup page for failed connections (physical LED is the feedback)
- Any change to `web_server.py` (the `/api/wifi/connect` endpoint signature is unchanged)
- Real-time status polling during the connection attempt
