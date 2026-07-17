# Log Retrieval — Design Spec

**Date:** 2026-07-17  
**Status:** Approved

---

## Overview

Add a "Send Logs" button to the RaceFlag Settings panel. When tapped, it fetches the last 150 lines of the systemd service log from the Pi and opens a pre-addressed mailto: link so the user can send the logs to the developer with one tap in their own email client. No credentials are stored on the device.

---

## Goals

- Allow a customer with a problem unit to send diagnostic logs to the developer in one tap.
- Require zero configuration on the Pi (no SMTP credentials, no third-party services).
- Work on any phone or computer connected to the RaceFlag network.

## Non-Goals

- Remote access by the developer to the unit.
- Automatic/scheduled log sending.
- Log storage or history beyond what systemd already keeps.

---

## Architecture

Two changes only — no new files, no new dependencies.

### 1. Backend — `/api/logs` endpoint (`web_server.py`)

New GET endpoint. Runs `journalctl -u raceflag -n 150 --no-pager` via `asyncio.create_subprocess_exec` (same subprocess pattern used by the existing `/api/shutdown` endpoint).

Returns:
```json
{
  "lines": "<150 lines of log text>",
  "timestamp": "2026-07-17T14:32:00"
}
```

**Fallback:** If `journalctl` exits with a non-zero code or raises `FileNotFoundError` (e.g. running in Docker or a non-systemd environment), return:
```json
{
  "lines": "journalctl not available — unit may be running in Docker or a non-systemd environment.",
  "timestamp": "2026-07-17T14:32:00"
}
```
Return HTTP 200 in both cases so the frontend always gets something useful to include in the email.

The timestamp is the server's local time at the moment the endpoint is called, formatted as ISO 8601.

---

### 2. Frontend — Settings panel (`index.html` + `app.js`)

#### `index.html`

The existing shutdown row:
```html
<div class="settings-row">
  <button class="btn-shutdown" id="btn-shutdown">Shut Down</button>
</div>
```

Becomes:
```html
<div class="settings-row">
  <button class="btn-shutdown" id="btn-shutdown">Shut Down</button>
  <button class="btn-shutdown" id="btn-send-logs">Send Logs</button>
</div>
```

Both buttons share `.btn-shutdown` styling (red border, red text, `#2a2a2a` background, 6px radius). No new CSS class needed.

#### `app.js`

New click handler for `#btn-send-logs`:

1. Disable button, set label to `Sending…`
2. `fetch('/api/logs')`
3. On success:
   - Build mailto: URL:
     - **To:** `prometheusprinting.yyc@gmail.com`
     - **Subject:** `RaceFlag Diagnostic Logs — {timestamp}`
     - **Body:** the log text
   - Open the URL via `window.location.href = mailtoUrl`
   - Reset button to `Send Logs` and re-enable immediately after opening the link
4. On fetch error:
   - Reset button to `Send Logs` and re-enable
   - (Silent failure — the mailto: link simply won't open; user can try again)

**Timing:** The button resets to "Send Logs" as soon as the mailto: link is opened — before the user switches to their email app. When they return to RaceFlag after sending, the button is already back to its default state.

---

## Data Flow

```
User taps "Send Logs"
  → button: "Sending…" + disabled
  → GET /api/logs
    → Pi: journalctl -u raceflag -n 150 --no-pager
    → returns { lines, timestamp }
  → build mailto: URL (To, Subject, Body)
  → window.location.href = mailtoUrl
  → button resets to "Send Logs" + re-enabled
  → email app opens (pre-addressed, pre-filled)
  → user taps Send
  → developer receives email
```

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| `journalctl` not available (Docker/dev) | Returns friendly message in `lines`; email still opens with that message as body |
| Fetch fails (network error) | Button resets silently; user can try again |
| mailto: URL too long for mail client | Unlikely at 150 lines; no special handling needed at this scope |

---

## Files Changed

| File | Change |
|---|---|
| `raceflag/web_server.py` | Add `GET /api/logs` endpoint |
| `raceflag/frontend/index.html` | Add `#btn-send-logs` button to shutdown row |
| `raceflag/frontend/app.js` | Add click handler for `#btn-send-logs` |
| `CHANGELOG.md` | Update `[Unreleased]` section |
