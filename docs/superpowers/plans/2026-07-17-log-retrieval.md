# Log Retrieval (Send Logs) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Send Logs" button to the Settings panel that fetches the last 150 lines of the systemd journal and opens a pre-addressed mailto: link so the user can send diagnostic logs in one tap.

**Architecture:** A module-level `_fetch_logs()` async helper in `web_server.py` runs `journalctl -u raceflag -n 150 --no-pager` and returns the output as a string (with a graceful fallback if journalctl is unavailable). A new `GET /api/logs` endpoint wraps that helper and returns `{ lines, timestamp }`. The frontend "Send Logs" button fetches that endpoint, constructs the mailto: URL, and opens it — resetting the button immediately so it's already back to normal when the user returns from their mail client.

**Tech Stack:** Python 3 / FastAPI / asyncio (backend); vanilla JS / HTML (frontend); pytest + pytest-mock (tests)

## Global Constraints

- Button label: exactly `Send Logs`
- Recipient email: `prometheusprinting.yyc@gmail.com`
- Log line count: 150 (`-n 150`)
- systemd unit name: `raceflag`
- Fallback message (exact): `journalctl not available — unit may be running in Docker or a non-systemd environment.`
- Button style: reuse existing `.btn-shutdown` CSS class — no new CSS
- Button placement: same `.settings-row` as the existing Shut Down button
- Button resets to `Send Logs` immediately after `window.location.href` is set (before user returns from mail client)
- Run tests with: `pytest tests/test_web_server.py -v`

---

## File Map

| File | Change |
|---|---|
| `raceflag/web_server.py` | Add module-level `_LOGS_UNAVAILABLE` constant + `_fetch_logs()` async helper + `GET /api/logs` endpoint inside `create_app` |
| `tests/test_web_server.py` | Add two new tests for `/api/logs` |
| `raceflag/frontend/index.html` | Add `#btn-send-logs` button to the existing shutdown `.settings-row` |
| `raceflag/frontend/app.js` | Add click handler for `#btn-send-logs` |
| `CHANGELOG.md` | Update `[Unreleased]` section |

---

## Task 1: Backend — `/api/logs` endpoint

**Files:**
- Modify: `raceflag/web_server.py`
- Modify: `tests/test_web_server.py`

**Interfaces:**
- Produces: `GET /api/logs` → `{ "lines": str, "timestamp": str }` (always HTTP 200)
- `_fetch_logs() -> str` — module-level async function, patched by name in tests

---

- [ ] **Step 1: Write two failing tests in `tests/test_web_server.py`**

Append these two tests at the bottom of the file. They use the existing `client` and `mocker` fixtures — no new fixtures needed.

```python
def test_logs_returns_lines_and_timestamp(client, mocker):
    mocker.patch(
        "raceflag.web_server._fetch_logs",
        return_value="INFO starting\nINFO feed connected\n",
    )
    resp = client.get("/api/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["lines"] == "INFO starting\nINFO feed connected\n"
    assert "timestamp" in data


def test_logs_returns_fallback_when_journalctl_unavailable(client, mocker):
    mocker.patch(
        "raceflag.web_server._fetch_logs",
        return_value="journalctl not available — unit may be running in Docker or a non-systemd environment.",
    )
    resp = client.get("/api/logs")
    assert resp.status_code == 200
    assert "not available" in resp.json()["lines"]
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_web_server.py::test_logs_returns_lines_and_timestamp tests/test_web_server.py::test_logs_returns_fallback_when_journalctl_unavailable -v
```

Expected: both FAIL with `404 Not Found` (endpoint doesn't exist yet).

- [ ] **Step 3: Add `_LOGS_UNAVAILABLE`, `_fetch_logs()`, and `GET /api/logs` to `web_server.py`**

After the existing module-level constants (`VALID_FLAG_STATES`, `_TIMED_TEST_EFFECTS`, `FRONTEND_DIR`) and before the Pydantic model definitions, add:

```python
_LOGS_UNAVAILABLE = (
    "journalctl not available — unit may be running in Docker or a non-systemd environment."
)


async def _fetch_logs() -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "journalctl", "-u", "raceflag", "-n", "150", "--no-pager",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0 and stdout:
            return stdout.decode("utf-8", errors="replace")
        return _LOGS_UNAVAILABLE
    except FileNotFoundError:
        return _LOGS_UNAVAILABLE
```

Then inside `create_app`, after the existing `@app.post("/api/shutdown")` endpoint, add:

```python
    @app.get("/api/logs")
    async def get_logs():
        from datetime import datetime
        lines = await _fetch_logs()
        return {"lines": lines, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")}
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_web_server.py::test_logs_returns_lines_and_timestamp tests/test_web_server.py::test_logs_returns_fallback_when_journalctl_unavailable -v
```

Expected: both PASS.

- [ ] **Step 5: Run the full web server test suite to check for regressions**

```
pytest tests/test_web_server.py -v
```

Expected: all tests PASS (no regressions).

- [ ] **Step 6: Commit**

```
git add raceflag/web_server.py tests/test_web_server.py
git commit -m "feat: add GET /api/logs endpoint — runs journalctl and returns last 150 lines"
```

---

## Task 2: Frontend — Send Logs button and click handler

**Files:**
- Modify: `raceflag/frontend/index.html`
- Modify: `raceflag/frontend/app.js`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `GET /api/logs` → `{ lines: string, timestamp: string }` (from Task 1)

---

- [ ] **Step 1: Add the button to `index.html`**

Find the existing shutdown row (near the bottom of the settings panel):

```html
      <div class="settings-row">
        <button class="btn-shutdown" id="btn-shutdown">Shut Down</button>
      </div>
```

Replace it with:

```html
      <div class="settings-row">
        <button class="btn-shutdown" id="btn-shutdown">Shut Down</button>
        <button class="btn-shutdown" id="btn-send-logs">Send Logs</button>
      </div>
```

- [ ] **Step 2: Add the click handler to `app.js`**

Find the existing shutdown handler:

```javascript
document.getElementById('btn-shutdown').addEventListener('click', async () => {
```

After the closing `});` of that handler, add:

```javascript
document.getElementById('btn-send-logs').addEventListener('click', async () => {
  const btn = document.getElementById('btn-send-logs');
  btn.textContent = 'Sending…';
  btn.disabled = true;
  try {
    const resp = await fetch('/api/logs');
    if (!resp.ok) throw new Error('fetch failed');
    const data = await resp.json();
    const subject = encodeURIComponent(`RaceFlag Diagnostic Logs — ${data.timestamp}`);
    const body = encodeURIComponent(data.lines);
    window.location.href = `mailto:prometheusprinting.yyc@gmail.com?subject=${subject}&body=${body}`;
  } catch (e) {}
  btn.textContent = 'Send Logs';
  btn.disabled = false;
});
```

Note: `window.location.href` on a mailto: URL opens the mail client without navigating away from the page. The two lines after it (`btn.textContent = 'Send Logs'; btn.disabled = false;`) execute immediately, so the button is already reset by the time the user returns from their mail client.

- [ ] **Step 3: Update `CHANGELOG.md`**

In the `[Unreleased]` section, add under `### Added` (create the heading if it doesn't exist yet):

```
### Added
- Send Logs button in Settings — fetches the last 150 lines of the systemd journal and opens a pre-addressed mailto: link so the user can email diagnostic logs in one tap
```

- [ ] **Step 4: Manual verification**

Start the dev server (or connect to a unit) and open the Settings panel:
- Confirm "Send Logs" appears to the right of "Shut Down" with matching red-border style
- Tap "Send Logs" — button should briefly show "Sending…" then open the mail client pre-addressed to `prometheusprinting.yyc@gmail.com` with subject `RaceFlag Diagnostic Logs — <timestamp>` and log content in the body
- Return to RaceFlag — button should already read "Send Logs" (not "Sending…")
- On a Docker instance, confirm the body contains the fallback message instead of crashing

- [ ] **Step 5: Commit**

```
git add raceflag/frontend/index.html raceflag/frontend/app.js CHANGELOG.md
git commit -m "feat: add Send Logs button — opens pre-addressed mailto with diagnostic logs"
```
