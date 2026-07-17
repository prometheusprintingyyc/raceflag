# WiFi Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three WiFi bugs (wrong-password dark period, over-aggressive hotspot trigger, infinite retry loop) and update the setup page to show a countdown instead of a false "Connected!" message.

**Architecture:** All backend fixes are in `raceflag/wifi_manager.py`. `_connect_to_configured()` is changed to return `bool` so callers can act on success/failure. The frontend change replaces the current fire-and-hope fetch pattern with a fire-and-forget fetch plus a 45-second countdown that attempts a redirect at the end — the LED strip is the authoritative signal of success or failure.

**Tech Stack:** Python 3 / asyncio (backend); vanilla JS / HTML (frontend); pytest + pytest-mock + pytest-asyncio (tests, `asyncio_mode = auto` in `pytest.ini` so all `async def` tests run automatically — no `@pytest.mark.asyncio` decorator required, but it is harmless if present)

## Global Constraints

- `CONNECT_TIMEOUT = 30` — seconds before nmcli is killed
- `CONNECT_FAIL_THRESHOLD = 2` — ping failures before hotspot when never connected (60 s)
- `RECONNECT_FAIL_THRESHOLD = 10` — ping failures before hotspot after first connection (300 s)
- `MAX_HOTSPOT_ATTEMPTS = 3` — auto-reconnect failures before credentials are cleared
- `CONNECT_COUNTDOWN_SEC = 45` — JS countdown bar duration in seconds
- `REDIRECT_URL = 'http://raceflag.local:8080'` — existing constant, unchanged
- Fallback message text below countdown bar (verbatim): `"If the LED strip is still flashing white after the countdown, the connection was unsuccessful. Reconnect to RaceFlag-Setup and try again."`
- Run backend tests with: `pytest tests/test_wifi_manager.py -v`

---

## File Map

| File | Change |
|---|---|
| `raceflag/wifi_manager.py` | Add 4 constants; add 2 fields to `__init__`; `_connect_to_configured()` returns `bool`, uses `asyncio.wait_for`; `connect()` re-enables hotspot on failure; `_monitor_loop()` sets `_ever_connected`, uses threshold constants, tracks retry count, clears credentials |
| `tests/test_wifi_manager.py` | Update existing `test_connect_updates_config`; add 10 new tests across Tasks 1–3 |
| `raceflag/frontend/setup.html` | Replace connecting spinner with countdown state; remove `s-success` block and `showSuccess()`; add password show/hide toggle to both password fields |
| `CHANGELOG.md` | Update `[Unreleased]` section |

---

## Task 1: Backend Fix 1 — timeout + immediate hotspot re-enable

**Files:**
- Modify: `raceflag/wifi_manager.py`
- Modify: `tests/test_wifi_manager.py`

**Interfaces:**
- Produces: `_connect_to_configured() -> bool` — used by `connect()` (this task) and `_monitor_loop()` (Tasks 2 & 3)
- Produces: `WiFiManager._ever_connected: bool` — used by Task 2
- Produces: `WiFiManager._hotspot_attempt_count: int` — used by Task 3

---

- [ ] **Step 1: Write four failing tests**

Append to `tests/test_wifi_manager.py`. Update the import line at the top of the file first:

```python
from raceflag.wifi_manager import WiFiManager, MAX_HOTSPOT_ATTEMPTS
```

Then append these four tests:

```python
@pytest.mark.asyncio
async def test_connect_to_configured_returns_true_on_success(manager, mocker):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(None, None))
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock(return_value=None)
    mocker.patch("raceflag.wifi_manager.asyncio.create_subprocess_exec", return_value=mock_proc)
    result = await manager._connect_to_configured()
    assert result is True


@pytest.mark.asyncio
async def test_connect_to_configured_returns_false_on_nonzero_exit(manager, mocker):
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(None, None))
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock(return_value=None)
    mocker.patch("raceflag.wifi_manager.asyncio.create_subprocess_exec", return_value=mock_proc)
    result = await manager._connect_to_configured()
    assert result is False


@pytest.mark.asyncio
async def test_connect_to_configured_returns_false_on_timeout(manager, mocker):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(None, None))
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock(return_value=None)
    mocker.patch("raceflag.wifi_manager.asyncio.create_subprocess_exec", return_value=mock_proc)
    mocker.patch("raceflag.wifi_manager.asyncio.wait_for", side_effect=asyncio.TimeoutError)
    result = await manager._connect_to_configured()
    assert result is False
    mock_proc.kill.assert_called_once()
    mock_proc.wait.assert_called()


@pytest.mark.asyncio
async def test_connect_reenables_hotspot_on_failure(manager, mocker):
    mocker.patch.object(manager, "disable_hotspot", new=AsyncMock())
    mocker.patch.object(manager, "enable_hotspot", new=AsyncMock())
    mocker.patch.object(manager, "_connect_to_configured", new=AsyncMock(return_value=False))
    await manager.connect("BadNet", "wrongpass")
    manager.enable_hotspot.assert_called_once()


@pytest.mark.asyncio
async def test_connect_does_not_reenable_hotspot_on_success(manager, mocker):
    mocker.patch.object(manager, "disable_hotspot", new=AsyncMock())
    mocker.patch.object(manager, "enable_hotspot", new=AsyncMock())
    mocker.patch.object(manager, "_connect_to_configured", new=AsyncMock(return_value=True))
    await manager.connect("GoodNet", "rightpass")
    manager.enable_hotspot.assert_not_called()
```

- [ ] **Step 2: Run new tests to verify they fail**

```
pytest tests/test_wifi_manager.py::test_connect_to_configured_returns_true_on_success tests/test_wifi_manager.py::test_connect_to_configured_returns_false_on_nonzero_exit tests/test_wifi_manager.py::test_connect_to_configured_returns_false_on_timeout tests/test_wifi_manager.py::test_connect_reenables_hotspot_on_failure tests/test_wifi_manager.py::test_connect_does_not_reenable_hotspot_on_success -v
```

Expected: all five FAIL (return type is None, not bool; no hotspot re-enable logic yet).

- [ ] **Step 3: Add module-level constants and new `__init__` fields to `wifi_manager.py`**

After the existing `DNSMASQ_CONF_CONTENT` block (around line 30) and before `class WiFiManager:`, add:

```python
CONNECT_TIMEOUT = 30
CONNECT_FAIL_THRESHOLD = 2
RECONNECT_FAIL_THRESHOLD = 10
MAX_HOTSPOT_ATTEMPTS = 3
```

In `WiFiManager.__init__`, after `self._task: asyncio.Task | None = None`, add:

```python
self._ever_connected = False
self._hotspot_attempt_count = 0
```

- [ ] **Step 4: Update `_connect_to_configured()` to return `bool` and use `asyncio.wait_for`**

Replace the entire `_connect_to_configured` method with:

```python
async def _connect_to_configured(self) -> bool:
    ssid = self._config.wifi_ssid
    password = self._config.wifi_password
    if not ssid:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "nmcli", "device", "wifi", "connect", ssid, "password", password,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=CONNECT_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("WiFi connect timed out after %ds", CONNECT_TIMEOUT)
            return False
        self._connected = proc.returncode == 0
        self._current_ssid = ssid if self._connected else ""
        return self._connected
    except Exception as e:
        logger.error("WiFi connect failed: %s", e)
        return False
```

- [ ] **Step 5: Update `connect()` to re-enable hotspot on failure**

Replace the entire `connect` method with:

```python
async def connect(self, ssid: str, password: str) -> None:
    self._config.wifi_ssid = ssid
    self._config.wifi_password = password
    if self._config_path:
        save_config(self._config, self._config_path)
    self._hotspot_attempt_count = 0
    await self.disable_hotspot()
    success = await self._connect_to_configured()
    if not success:
        await self.enable_hotspot()
```

- [ ] **Step 6: Update the existing `test_connect_updates_config` test**

`_connect_to_configured()` now calls `proc.communicate()` instead of `proc.wait()`. The existing test mock only sets `mock_proc.wait`, so it will break. Add `mock_proc.communicate`:

Find this test in `tests/test_wifi_manager.py`:

```python
async def test_connect_updates_config(manager, config, tmp_path, mocker):
    config_path = tmp_path / "config.json"
    manager._config_path = config_path
    mock_proc = MagicMock()
    mock_proc.wait = AsyncMock(return_value=0)
    mock_proc.returncode = 0
    mocker.patch("raceflag.wifi_manager.asyncio.create_subprocess_exec", return_value=mock_proc)
    await manager.connect("NewNet", "newpass")
    assert manager._config.wifi_ssid == "NewNet"
    assert manager._config.wifi_password == "newpass"
```

Add `mock_proc.communicate = AsyncMock(return_value=(None, None))` after `mock_proc.wait = ...`:

```python
async def test_connect_updates_config(manager, config, tmp_path, mocker):
    config_path = tmp_path / "config.json"
    manager._config_path = config_path
    mock_proc = MagicMock()
    mock_proc.wait = AsyncMock(return_value=0)
    mock_proc.communicate = AsyncMock(return_value=(None, None))
    mock_proc.returncode = 0
    mocker.patch("raceflag.wifi_manager.asyncio.create_subprocess_exec", return_value=mock_proc)
    await manager.connect("NewNet", "newpass")
    assert manager._config.wifi_ssid == "NewNet"
    assert manager._config.wifi_password == "newpass"
```

- [ ] **Step 7: Run the full wifi_manager test suite**

```
pytest tests/test_wifi_manager.py -v
```

Expected: all tests PASS including the five new ones and the updated existing one.

- [ ] **Step 8: Commit**

```
git add raceflag/wifi_manager.py tests/test_wifi_manager.py
git commit -m "fix: nmcli timeout + immediate hotspot re-enable on wrong password"
```

---

## Task 2: Backend Fix 2 — raise outage threshold for previously-connected devices

**Files:**
- Modify: `raceflag/wifi_manager.py` (`_monitor_loop` only)
- Modify: `tests/test_wifi_manager.py`

**Interfaces:**
- Consumes: `WiFiManager._ever_connected: bool` (added in Task 1)
- Consumes: `CONNECT_FAIL_THRESHOLD = 2`, `RECONNECT_FAIL_THRESHOLD = 10` (added in Task 1)

---

- [ ] **Step 1: Write three failing tests**

Append to `tests/test_wifi_manager.py`:

```python
@pytest.mark.asyncio
async def test_monitor_loop_sets_ever_connected_on_first_ping_success(manager, mocker):
    call_count = 0
    async def mock_check():
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            manager._running = False
        return True
    mocker.patch.object(manager, "_check_connectivity", side_effect=mock_check)
    mocker.patch("raceflag.wifi_manager.asyncio.sleep", new=AsyncMock())
    manager._running = True
    manager._hotspot_active = False
    await manager._monitor_loop()
    assert manager._ever_connected is True


@pytest.mark.asyncio
async def test_monitor_loop_enables_hotspot_after_2_failures_when_never_connected(manager, mocker):
    fail_calls = 0
    async def mock_check():
        nonlocal fail_calls
        fail_calls += 1
        if fail_calls > 2:
            manager._running = False
        return False
    mocker.patch.object(manager, "_check_connectivity", side_effect=mock_check)
    mocker.patch.object(manager, "enable_hotspot", new=AsyncMock())
    mocker.patch("raceflag.wifi_manager.asyncio.sleep", new=AsyncMock())
    manager._running = True
    manager._hotspot_active = False
    manager._ever_connected = False
    await manager._monitor_loop()
    manager.enable_hotspot.assert_called_once()


@pytest.mark.asyncio
async def test_monitor_loop_enables_hotspot_after_10_failures_when_previously_connected(manager, mocker):
    fail_calls = 0
    async def mock_check():
        nonlocal fail_calls
        fail_calls += 1
        if fail_calls > 10:
            manager._running = False
        return False
    mocker.patch.object(manager, "_check_connectivity", side_effect=mock_check)
    mocker.patch.object(manager, "enable_hotspot", new=AsyncMock())
    mocker.patch("raceflag.wifi_manager.asyncio.sleep", new=AsyncMock())
    manager._running = True
    manager._hotspot_active = False
    manager._ever_connected = True
    await manager._monitor_loop()
    manager.enable_hotspot.assert_called_once()
```

- [ ] **Step 2: Run new tests to verify they fail**

```
pytest tests/test_wifi_manager.py::test_monitor_loop_sets_ever_connected_on_first_ping_success tests/test_wifi_manager.py::test_monitor_loop_enables_hotspot_after_2_failures_when_never_connected tests/test_wifi_manager.py::test_monitor_loop_enables_hotspot_after_10_failures_when_previously_connected -v
```

Expected: all three FAIL (`_ever_connected` is never set; threshold is still hardcoded to 2).

- [ ] **Step 3: Update `_monitor_loop()` non-hotspot branch**

Replace the `else:` branch inside `_monitor_loop` (keep the hotspot branch unchanged — Task 3 handles that). The full updated method:

```python
async def _monitor_loop(self) -> None:
    fail_count = 0
    while self._running:
        if self._hotspot_active:
            if await self._check_configured_available():
                await self.disable_hotspot()
                await self._connect_to_configured()
            await asyncio.sleep(120)
        else:
            ok = await self._check_connectivity()
            if ok:
                fail_count = 0
                self._connected = True
                self._ever_connected = True
            else:
                fail_count += 1
                self._connected = False
                threshold = RECONNECT_FAIL_THRESHOLD if self._ever_connected else CONNECT_FAIL_THRESHOLD
                if fail_count >= threshold:
                    logger.warning("WiFi unreachable — starting hotspot")
                    await self.enable_hotspot()
                    fail_count = 0
            await asyncio.sleep(30)
```

- [ ] **Step 4: Run the full test suite**

```
pytest tests/test_wifi_manager.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```
git add raceflag/wifi_manager.py tests/test_wifi_manager.py
git commit -m "fix: raise hotspot trigger threshold to 10 failures for previously-connected devices"
```

---

## Task 3: Backend Fix 3 — break wrong-password retry loop

**Files:**
- Modify: `raceflag/wifi_manager.py` (hotspot branch of `_monitor_loop`)
- Modify: `tests/test_wifi_manager.py`

**Interfaces:**
- Consumes: `_connect_to_configured() -> bool` (Task 1)
- Consumes: `WiFiManager._hotspot_attempt_count: int` (Task 1)
- Consumes: `MAX_HOTSPOT_ATTEMPTS = 3` (Task 1)
- Consumes: full `_monitor_loop` from Task 2 (both branches must be present in the final version)

---

- [ ] **Step 1: Write three failing tests**

Append to `tests/test_wifi_manager.py`:

```python
@pytest.mark.asyncio
async def test_monitor_loop_reenables_hotspot_immediately_on_failed_auto_reconnect(manager, mocker):
    call_count = 0
    async def mock_connect():
        nonlocal call_count
        call_count += 1
        manager._running = False
        return False
    mocker.patch.object(manager, "_check_configured_available", new=AsyncMock(return_value=True))
    mocker.patch.object(manager, "_connect_to_configured", side_effect=mock_connect)
    mocker.patch.object(manager, "disable_hotspot", new=AsyncMock())
    mocker.patch.object(manager, "enable_hotspot", new=AsyncMock())
    mocker.patch("raceflag.wifi_manager.asyncio.sleep", new=AsyncMock())
    manager._running = True
    manager._hotspot_active = True
    await manager._monitor_loop()
    manager.enable_hotspot.assert_called_once()
    assert manager._hotspot_attempt_count == 1


@pytest.mark.asyncio
async def test_monitor_loop_clears_credentials_after_max_hotspot_attempts(manager, mocker, tmp_path):
    manager._config_path = tmp_path / "config.json"
    call_count = 0
    async def mock_connect():
        nonlocal call_count
        call_count += 1
        if call_count >= MAX_HOTSPOT_ATTEMPTS:
            manager._running = False
        return False
    mocker.patch.object(manager, "_check_configured_available", new=AsyncMock(return_value=True))
    mocker.patch.object(manager, "_connect_to_configured", side_effect=mock_connect)
    mocker.patch.object(manager, "disable_hotspot", new=AsyncMock())
    mocker.patch.object(manager, "enable_hotspot", new=AsyncMock())
    mocker.patch("raceflag.wifi_manager.asyncio.sleep", new=AsyncMock())
    manager._running = True
    manager._hotspot_active = True
    await manager._monitor_loop()
    assert manager._config.wifi_ssid == ""
    assert manager._config.wifi_password == ""
    assert manager._hotspot_attempt_count == 0


@pytest.mark.asyncio
async def test_monitor_loop_resets_attempt_count_on_successful_reconnect(manager, mocker):
    async def mock_connect():
        manager._running = False
        return True
    mocker.patch.object(manager, "_check_configured_available", new=AsyncMock(return_value=True))
    mocker.patch.object(manager, "_connect_to_configured", side_effect=mock_connect)
    mocker.patch.object(manager, "disable_hotspot", new=AsyncMock())
    mocker.patch.object(manager, "enable_hotspot", new=AsyncMock())
    mocker.patch("raceflag.wifi_manager.asyncio.sleep", new=AsyncMock())
    manager._running = True
    manager._hotspot_active = True
    manager._hotspot_attempt_count = 2
    await manager._monitor_loop()
    assert manager._hotspot_attempt_count == 0
```

- [ ] **Step 2: Run new tests to verify they fail**

```
pytest tests/test_wifi_manager.py::test_monitor_loop_reenables_hotspot_immediately_on_failed_auto_reconnect tests/test_wifi_manager.py::test_monitor_loop_clears_credentials_after_max_hotspot_attempts tests/test_wifi_manager.py::test_monitor_loop_resets_attempt_count_on_successful_reconnect -v
```

Expected: all three FAIL (hotspot branch doesn't track failures or clear credentials).

- [ ] **Step 3: Update `_monitor_loop()` hotspot branch**

Replace the entire `_monitor_loop` with the final version covering both branches:

```python
async def _monitor_loop(self) -> None:
    fail_count = 0
    while self._running:
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
        else:
            ok = await self._check_connectivity()
            if ok:
                fail_count = 0
                self._connected = True
                self._ever_connected = True
            else:
                fail_count += 1
                self._connected = False
                threshold = RECONNECT_FAIL_THRESHOLD if self._ever_connected else CONNECT_FAIL_THRESHOLD
                if fail_count >= threshold:
                    logger.warning("WiFi unreachable — starting hotspot")
                    await self.enable_hotspot()
                    fail_count = 0
            await asyncio.sleep(30)
```

- [ ] **Step 4: Run the full test suite**

```
pytest tests/test_wifi_manager.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```
git add raceflag/wifi_manager.py tests/test_wifi_manager.py
git commit -m "fix: clear credentials after 3 failed auto-reconnect attempts to break retry loop"
```

---

## Task 4: Frontend — countdown connecting state + password show/hide + CHANGELOG

**Files:**
- Modify: `raceflag/frontend/setup.html`
- Modify: `CHANGELOG.md`

**Interfaces:**
- No dependency on Tasks 1–3 (frontend change is independent)

---

- [ ] **Step 1: Update the JS constants block**

Find:
```javascript
  const REDIRECT_URL = 'http://raceflag.local:8080';
  const COUNTDOWN_SEC = 30;

  let _selectedSsid = '';
  let _networks = [];
  let _countdownTimer = null;
```

Replace with:
```javascript
  const REDIRECT_URL = 'http://raceflag.local:8080';
  const CONNECT_COUNTDOWN_SEC = 45;

  let _selectedSsid = '';
  let _networks = [];
  let _connectTimer = null;
```

- [ ] **Step 2: Remove `s-success` from the `show()` helper**

Find:
```javascript
  function show(id) {
    ['s-idle','s-scanning','s-networks','s-password','s-manual','s-connecting','s-success','s-error']
      .forEach(s => document.getElementById(s).classList.toggle('hidden', s !== id));
  }
```

Replace with:
```javascript
  function show(id) {
    ['s-idle','s-scanning','s-networks','s-password','s-manual','s-connecting','s-error']
      .forEach(s => document.getElementById(s).classList.toggle('hidden', s !== id));
  }
```

- [ ] **Step 3: Replace `connect()` with fire-and-forget version**

Find and replace the entire `connect` function:

```javascript
  async function connect() {
    const password = document.getElementById('pw-input').value;
    document.getElementById('connecting-label').textContent = `Connecting to ${_selectedSsid}…`;
    show('s-connecting');
    try {
      const resp = await fetch('/api/wifi/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ssid: _selectedSsid, password }),
      });
      if (resp.ok) {
        showSuccess();
      } else {
        showError('Check the password and try again.');
      }
    } catch (_) {
      // Network error likely means the hotspot shut down — connection succeeded.
      showSuccess();
    }
  }
```

Replace with:

```javascript
  async function connect() {
    const password = document.getElementById('pw-input').value;
    document.getElementById('connecting-label').textContent = `Connecting to ${_selectedSsid}…`;
    show('s-connecting');
    fetch('/api/wifi/connect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ssid: _selectedSsid, password }),
    }).catch(() => {});
    startConnectingCountdown();
  }
```

- [ ] **Step 4: Replace `connectManual()` with fire-and-forget version**

Find and replace the entire `connectManual` function:

```javascript
  async function connectManual() {
    const ssid = document.getElementById('manual-ssid').value.trim();
    if (!ssid) return;
    _selectedSsid = ssid;
    const password = document.getElementById('manual-pw').value;
    document.getElementById('connecting-label').textContent = `Connecting to ${_selectedSsid}…`;
    show('s-connecting');
    try {
      const resp = await fetch('/api/wifi/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ssid: _selectedSsid, password }),
      });
      if (resp.ok) { showSuccess(); } else { showError('Check the network name and password and try again.'); }
    } catch (_) { showSuccess(); }
  }
```

Replace with:

```javascript
  async function connectManual() {
    const ssid = document.getElementById('manual-ssid').value.trim();
    if (!ssid) return;
    _selectedSsid = ssid;
    const password = document.getElementById('manual-pw').value;
    document.getElementById('connecting-label').textContent = `Connecting to ${_selectedSsid}…`;
    show('s-connecting');
    fetch('/api/wifi/connect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ssid: _selectedSsid, password }),
    }).catch(() => {});
    startConnectingCountdown();
  }
```

- [ ] **Step 5: Replace `showSuccess()` with `startConnectingCountdown()`**

Find and delete the entire `showSuccess` function:

```javascript
  function showSuccess() {
    document.getElementById('success-ssid').textContent = _selectedSsid;
    show('s-success');
    let remaining = COUNTDOWN_SEC;
    const bar = document.getElementById('countdown-bar');
    const label = document.getElementById('countdown-label');
    bar.style.width = '100%';
    label.textContent = `Redirecting to RaceFlag in ${remaining} seconds…`;
    _countdownTimer = setInterval(() => {
      remaining--;
      bar.style.width = (remaining / COUNTDOWN_SEC * 100) + '%';
      label.textContent = remaining > 0
        ? `Redirecting to RaceFlag in ${remaining} seconds…`
        : 'Redirecting…';
      if (remaining <= 0) {
        clearInterval(_countdownTimer);
        window.location.href = REDIRECT_URL;
      }
    }, 1000);
  }
```

Replace it with:

```javascript
  function startConnectingCountdown() {
    if (_connectTimer) clearInterval(_connectTimer);
    let remaining = CONNECT_COUNTDOWN_SEC;
    const bar = document.getElementById('connect-countdown-bar');
    const label = document.getElementById('connect-countdown-label');
    bar.style.width = '100%';
    label.textContent = `${remaining}s`;
    _connectTimer = setInterval(() => {
      remaining--;
      bar.style.width = (remaining / CONNECT_COUNTDOWN_SEC * 100) + '%';
      label.textContent = remaining > 0 ? `${remaining}s` : '';
      if (remaining <= 0) {
        clearInterval(_connectTimer);
        window.location.href = REDIRECT_URL;
      }
    }, 1000);
  }
```

- [ ] **Step 6: Add `togglePw()` helper function**

Append this function after `startConnectingCountdown()`:

```javascript
  function togglePw(inputId, btn) {
    const input = document.getElementById(inputId);
    const showing = input.type === 'text';
    input.type = showing ? 'password' : 'text';
    btn.textContent = showing ? 'Show' : 'Hide';
  }
```

- [ ] **Step 7: Replace the `s-connecting` HTML block**

Find:
```html
  <!-- CONNECTING -->
  <div id="s-connecting" class="hidden">
    <div class="card">
      <div class="card-heading">Connecting…</div>
      <div class="card-sub" id="connecting-label"></div>
      <div class="spinner-row"><div class="spinner"></div><span>Please wait</span></div>
    </div>
  </div>
```

Replace with:
```html
  <!-- CONNECTING -->
  <div id="s-connecting" class="hidden">
    <div class="card">
      <div class="card-heading">RaceFlag is attempting to connect…</div>
      <div class="card-sub" id="connecting-label"></div>
      <div class="countdown-wrap" style="margin-top:16px"><div class="countdown-bar" id="connect-countdown-bar"></div></div>
      <div class="countdown-label" id="connect-countdown-label"></div>
      <div style="margin-top:16px;font-size:12px;color:#555;text-align:center;line-height:1.5">If the LED strip is still flashing white after the countdown, the connection was unsuccessful. Reconnect to RaceFlag-Setup and try again.</div>
    </div>
  </div>
```

- [ ] **Step 8: Delete the `s-success` HTML block**

Find and delete this entire block:
```html
  <!-- SUCCESS -->
  <div id="s-success" class="hidden">
    <div class="card">
      <div class="center-icon icon-success">✓</div>
      <div class="center-heading">Connected!</div>
      <div class="center-ssid" id="success-ssid" style="color:#00C853"></div>
      <div class="center-sub">
        RaceFlag is now online. This setup hotspot will shut down shortly
        — your device will reconnect to your home network automatically.
      </div>
      <div class="countdown-wrap"><div class="countdown-bar" id="countdown-bar"></div></div>
      <div class="countdown-label" id="countdown-label"></div>
    </div>
  </div>
```

- [ ] **Step 9: Add password show/hide toggle to `s-password`**

Find the password input in `s-password`:
```html
        <input id="pw-input" class="pw-input" type="password" placeholder="Enter WiFi password"
               onkeydown="if(event.key==='Enter') connect()">
```

Replace with a wrapper div containing both the input and the toggle button:
```html
        <div style="position:relative">
          <input id="pw-input" class="pw-input" type="password" placeholder="Enter WiFi password"
                 onkeydown="if(event.key==='Enter') connect()"
                 style="padding-right:60px">
          <button type="button" onclick="togglePw('pw-input',this)"
                  style="position:absolute;right:10px;top:50%;transform:translateY(-50%);background:none;border:none;color:#555;font-size:11px;cursor:pointer;padding:4px;font-family:inherit">Show</button>
        </div>
```

- [ ] **Step 10: Add password show/hide toggle to `s-manual`**

Find the password input in `s-manual`:
```html
        <input id="manual-pw" class="pw-input" type="password" placeholder="Enter WiFi password"
               onkeydown="if(event.key==='Enter') connectManual()">
```

Replace with:
```html
        <div style="position:relative">
          <input id="manual-pw" class="pw-input" type="password" placeholder="Enter WiFi password"
                 onkeydown="if(event.key==='Enter') connectManual()"
                 style="padding-right:60px">
          <button type="button" onclick="togglePw('manual-pw',this)"
                  style="position:absolute;right:10px;top:50%;transform:translateY(-50%);background:none;border:none;color:#555;font-size:11px;cursor:pointer;padding:4px;font-family:inherit">Show</button>
        </div>
```

- [ ] **Step 11: Manual verification**

Open `raceflag/frontend/setup.html` in a browser (File → Open, or via the dev server) and verify:
- Tapping **Scan for networks** → selecting a network → entering a password: the "Show" button appears beside the password field; tapping it reveals the password and changes label to "Hide"; tapping again hides it
- Same behaviour in the manual entry screen
- Tapping **Connect** shows "RaceFlag is attempting to connect…" with a countdown bar ticking down from 45
- The note below the bar reads: "If the LED strip is still flashing white after the countdown, the connection was unsuccessful. Reconnect to RaceFlag-Setup and try again."
- The old "Connected!" screen is gone

- [ ] **Step 12: Update `CHANGELOG.md`**

In `CHANGELOG.md`, replace the empty `[Unreleased]` section:

```markdown
## [Unreleased]
```

With:

```markdown
## [Unreleased]

### Added
- Password show/hide toggle on the WiFi setup page password fields

### Fixed
- Wrong password during WiFi setup no longer leaves the device in a dark period — the setup hotspot re-enables within 35 seconds (previously up to 2 minutes) and the LED strip resumes flashing white
- WiFi connectivity monitoring now tolerates up to 5 minutes of outage before re-enabling the setup hotspot, preventing false triggers during router reboots (previously 60 seconds)
- Repeated wrong-password auto-retries in the monitor loop stop after 3 consecutive failures — saved credentials are cleared so the device stays in setup mode cleanly

### Changed
- WiFi setup connecting state now shows a 45-second countdown bar instead of a spinner; the LED strip is the authoritative success/failure signal
```

- [ ] **Step 13: Commit**

```
git add raceflag/frontend/setup.html CHANGELOG.md
git commit -m "feat: setup page countdown + password reveal toggle; remove false Connected screen"
```
