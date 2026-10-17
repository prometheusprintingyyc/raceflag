# F1 Account — Bookmark Sign-in & Automatic Renewal — Design

**Date:** 2026-10-17
**Branch:** `beta`
**Shared spec:** `features/017-f1-account.md` (update)
**Builds on:** the paste login already on beta (commit 03ecfc2), verified on a unit with a free F1 account.

## Goal

Replace the DevTools copy-and-paste with a one-tap "Send to RaceFlag" bookmark, renew the 4-day
F1 token automatically so the owner signs in about once a month, and apply the agreed Settings UI.

## Background (verified 2026-10-16/17)

- The replay archive needs `Authorization: Bearer <token>`; a free F1 account's token works.
- The token is `data.subscriptionToken` inside the formula1.com `login-session` cookie (URL-encoded
  JSON). The cookie's Domain is `.formula1.com` (so it is visible on f1tv.formula1.com) and it is
  **not** HttpOnly (so page scripts can read it).
- The token is a JWT valid for 4 days. Its `SessionId` claim identifies the F1 sign-in session,
  which lasts about 30 days.
- Renewal (from the MIT-licensed f1_sensor project, `custom_components/f1_sensor/auth.py`): `POST
  https://api.formula1.com/v1/account/Subscriber/RetrieveSubscriber` with body `{}` and headers
  `apikey: fCUCjWrKPu9ylJwRAv8BpGLEgiAuThx7`, `CD-SystemId: 60a9ad84-e93d-480f-80d6-af37494f2e22`,
  `cd-sessionid: <SessionId>`, `orderSubmitted: true`, `Content-Type: application/json` and a
  desktop browser `User-Agent`. 200 → `data.subscriptionToken` is a fresh token; 401/403 → the
  session has ended or was revoked.
- Not yet verified: that a free account's token carries `SessionId` and that renewal works for a
  free account. The unit test after this change verifies both.

## Decisions (agreed with the user)

- Login lives in Settings (so live timing can use it later), not on the Replay screen.
- No explainer text under the F1 Account status; the user guide explains it.
- Status, with icon and colour: ✕ **Not signed in** (red `#ff5a52`), ✓ **Signed in** (green
  `#3ecf6b`), ⚠ **Sign in again** (yellow `#ffd700`).
- Sign-in page is **f1tv.formula1.com**.
- The bookmark always sends to **`http://raceflag.local:8080`** (the same code for every unit).
  Trial: hostnames sometimes fail (some Android phones, duplicate names); an IP fallback may follow.
- Paste stays as a fallback inside Set up.
- No TV-style device activation (legal exposure; see conversation of 2026-10-17).

## Behaviour

### Settings → F1 Account row

| State | When | Status | Button |
|---|---|---|---|
| Signed out | no token saved | ✕ Not signed in (red) | **Set up** (red) |
| Signed in | token saved, unexpired, not rejected | ✓ Signed in (green) | **Sign out** (grey) |
| Sign in again | token expired, or F1 refused a renewal | ⚠ Sign in again (yellow) | **Set up** (red) |

### Set up dialog

- Intro: "Add a “Send to RaceFlag” bookmark once. To sign in, tap it while signed in on
  f1tv.formula1.com."
- Steps (phone): 1) Tap **Copy bookmark code**. 2) Bookmark any page, then edit that bookmark:
  name it **Send to RaceFlag** and replace its address with the copied code. 3) Open
  f1tv.formula1.com (link), sign in, then open the bookmark (on Android Chrome: type "Send to
  RaceFlag" in the address bar and tap it).
- On a computer, a **Send to RaceFlag** link can be dragged to the bookmarks bar (same code).
- "This bookmark sends your login to raceflag.local:8080".
- **Or paste your login**: text field + **Save** (the existing paste path).
- **Close**.
- Copying works on plain `http://` (no secure context): select the code in a read-only field and
  use `document.execCommand('copy')`; if that fails, the code stays selected for a manual copy.

### The bookmark

```
javascript:(()=>{const c=document.cookie.split('; ').find(x=>x.startsWith('login-session='));if(!c){alert('Sign in on F1 TV first, then tap Send to RaceFlag again.');return;}location.href='http://raceflag.local:8080/#f1login='+encodeURIComponent(c.slice(14));})()
```

- Runs on the F1 page; reads `login-session`; opens RaceFlag with the value after `#` (never sent
  to any server, never in server logs).
- No cookie (signed out) → an alert, nothing is sent.

### Receiving it (RaceFlag page load)

- If the address has `#f1login=<value>`: remove it from the address bar
  (`history.replaceState`; the browser's own history may still hold it), `POST /api/f1/account` with the decoded value, open Settings, and show
  the result in the F1 Account row ("Signed in" or the 422 reason).

### Automatic renewal

- A background task checks once an hour (and 30 s after start-up).
- If a token is saved, not rejected, and has **less than 24 h left (or has expired)**, and it has a
  `SessionId`, the box calls the renewal endpoint.
  - 200 with a new, later-expiring token → save it (one `config.json` write, about every 3 days).
  - 401/403 → mark the login **rejected** (status "Sign in again"); stop trying until a new login
    is saved. The rejected mark is runtime-only: after a reboot one more attempt is made, which
    is refused again.
  - Network error, timeout, any other status, or a token that isn't newer → try again at the next
    hourly check.
- A token without `SessionId` is never renewed; it simply expires to "Sign in again".
- Saving a new login (bookmark or paste) clears the rejected mark.
- The token and session ID are never logged; log lines say only "F1 login renewed until <date>"
  or "F1 login renewal refused — sign in again".

### API

- `GET /api/f1/account` → `{"status": "signed_in"|"sign_in_again"|"signed_out", "signed_in":
  bool, "expires_at": "…Z"|null}`.
- `POST /api/f1/account` → same shape; clears the rejected mark.
- `DELETE /api/f1/account` → `{"status": "signed_out", "signed_in": false, "expires_at": null}`.

### Replay

- Unchanged download behaviour. The "F1 login needed or expired…" message under the race list
  gains an **Open Settings** button.

## Components

| File | Change |
|---|---|
| `raceflag/f1_account.py` | `session_id(token)`, `needs_renewal(token, now)`, `status` field with a `rejected` input |
| `raceflag/f1_renewal.py` (new) | `F1LoginRenewer`: the hourly check and the renewal request (attribution to f1_sensor, MIT) |
| `raceflag/web_server.py` | Account routes return `status`; POST clears rejected (via the renewer) |
| `raceflag/main.py` | Create and start the renewer |
| `raceflag/frontend/*` | Settings row states, Set up dialog, bookmark code, `#f1login` receiver, Replay button |
| `NOTICE` or code header | f1_sensor MIT attribution for the renewal constants |

## Testing

- Unit: session ID extraction; renewal window (24 h boundary, expired, no SessionId); renewer
  outcomes (200 new token saved, 200 not-newer ignored, 401/403 rejected, network error retried,
  rejected stops further calls, saving a login clears rejected); routes return `status`.
- Page: `node --check`; dev-server check of Set up, the copy fallback and the `#f1login` receiver.
- Unit (user): bookmark from a phone and a computer on f1tv.formula1.com; renewal observed by
  leaving the unit running past the token's last day (or by pasting a token with < 24 h left).

## Out of scope

- An IP-address fallback for the bookmark (only if `raceflag.local` proves unreliable).
- Using the login for live timing.
