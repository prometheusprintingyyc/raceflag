# F1 Account Bookmark Sign-in & Renewal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sign in to F1 with a one-tap "Send to RaceFlag" bookmark, renew the 4-day token automatically, and apply the agreed Settings UI.

**Architecture:** `f1_account.py` (pure) gains session-ID, renewal-window and three-state status helpers. A new `f1_renewal.py` holds `F1LoginRenewer`, an hourly asyncio task that calls F1's account service to swap a token nearing expiry for a fresh one and remembers a refusal. The web routes report the three states; the page gets a Set up dialog with the bookmark code and receives the login from `#f1login=…` on load.

**Tech Stack:** Python 3.11+, FastAPI (<0.100) + pydantic v1, httpx, asyncio, pytest (`asyncio_mode = auto`), vanilla JS/HTML/CSS.

**Spec:** `docs/superpowers/specs/2026-10-17-f1-account-signin-design.md`

## Global Constraints

- All work on the `beta` branch of the Pi repo; spec changes in `../raceflag_specs` (branch `master`) as their own commit naming the feature; stage only your own files there (another session edits PARITY.md and contracts/api.md).
- Commit trailers on every commit (use your own model name in Co-Authored-By):
  `Co-Authored-By: Claude <Model> 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01UQ9hM8siPUL1nmzWsUhytk`
- Never stage `Notes.txt`, `Notes.txt.bak`, `.claude/settings.local.json` or any untracked user file. Never push.
- The token and the session ID are never logged, never returned by a route, never shown on the page.
- Renewal request, exactly: `POST https://api.formula1.com/v1/account/Subscriber/RetrieveSubscriber`, JSON body `{}`, headers `apikey: fCUCjWrKPu9ylJwRAv8BpGLEgiAuThx7`, `CD-SystemId: 60a9ad84-e93d-480f-80d6-af37494f2e22`, `cd-sessionid: <SessionId claim>`, `orderSubmitted: true`, `Content-Type: application/json`, `User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36`. Attribute to f1_sensor (MIT, https://github.com/Nicxe/f1_sensor) in the module header.
- Renewal window: less than 24 h (86400 s) left, or expired. Checks: 30 s after start-up, then every 3600 s.
- Statuses: `signed_out` "Not signed in" red `#ff5a52` ✕; `signed_in` "Signed in" green `#3ecf6b` ✓; `sign_in_again` "Sign in again" yellow `#ffd700` ⚠.
- Bookmark target: `http://raceflag.local:8080` (fixed for every unit).
- Sign-in site in copy and links: `f1tv.formula1.com`.
- Run tests with `PYTHONUTF8=1 python -m pytest tests -q -p no:warnings`. Six tests fail before this work and are not yours: `test_handle_feed_session_status_finished_sets_break`, `test_handle_feed_session_status_finished_resets_track_status`, `test_play_snapshot_phase_fires_with_is_snapshot_true`, `test_set_delay_rejects_above_60`, `test_test_effect_triggers_led`, `test_check_connectivity_returns_true_when_ping_succeeds`.

## Review Focus

1. A renewal response whose token is not newer (or unreadable) must not replace the saved token. (Task 2 `test_renewal_ignores_token_that_is_not_newer`.)
2. A refused renewal stops further calls until a new login is saved — no hourly hammering of F1. (Task 2 `test_rejected_stops_further_calls`, `test_login_saved_clears_rejected`.)
3. The user saves a new login while a renewal is in flight: the renewal result must not overwrite the new login. (Task 2 `test_renewal_does_not_overwrite_a_login_saved_meanwhile`.)
4. Copying the bookmark on plain http (no `navigator.clipboard`) still works or leaves the code selected. (Task 4 manual check.)
5. `#f1login=` with a malformed percent-encoding must not break page load. (Task 4: `decodeURIComponent` inside try.)

---

### Task 1: Session ID, renewal window and three-state status

**Files:**
- Modify: `raceflag/f1_account.py`
- Test: `tests/test_f1_account.py`

**Interfaces:**
- Produces:
  - `token_expiry(token: str) -> float` (public rename of `_token_expiry`; raises `LoginError`)
  - `session_id(token: str) -> str | None` — the JWT's `SessionId` claim if a non-empty string
  - `needs_renewal(token: str, now: float) -> bool` — readable token with `exp - now < 86400`
  - `RENEW_WINDOW_S = 86400.0`
  - `account_status(token: str, now: float, rejected: bool = False) -> dict` with keys `status`, `signed_in`, `expires_at`

- [ ] **Step 1: Update and add tests** in `tests/test_f1_account.py`

Change the import line to:

```python
from raceflag.f1_account import (
    LOGIN_NEEDED, RENEW_WINDOW_S, LoginError, account_status, auth_headers, needs_renewal,
    parse_login, session_id, token_expiry,
)
```

Replace the four `test_status_*` tests with:

```python
def test_status_not_signed_in():
    assert account_status("", NOW) == {"status": "signed_out", "signed_in": False, "expires_at": None}


def test_status_signed_in_reports_expiry_iso_utc():
    status = account_status(TOKEN, NOW)
    assert status["status"] == "signed_in"
    assert status["signed_in"] is True
    assert status["expires_at"].endswith("Z")


def test_status_expired_token_is_sign_in_again():
    status = account_status(_jwt(NOW - 10), NOW)
    assert status["status"] == "sign_in_again"
    assert status["signed_in"] is False
    assert status["expires_at"] is not None


def test_status_rejected_token_is_sign_in_again():
    status = account_status(TOKEN, NOW, rejected=True)
    assert status["status"] == "sign_in_again"
    assert status["signed_in"] is False


def test_status_unreadable_token_is_sign_in_again():
    assert account_status("garbage", NOW) == {"status": "sign_in_again", "signed_in": False, "expires_at": None}
```

Append:

```python
def test_token_expiry_is_public():
    assert token_expiry(TOKEN) == float(int(NOW + 4 * 86400))


def test_session_id_present():
    assert session_id(TOKEN) == "abc"


@pytest.mark.parametrize("token", [_jwt(NOW + 100), _jwt(NOW + 100, SessionId=""), _jwt(NOW + 100, SessionId=5), "garbage"])
def test_session_id_absent(token):
    assert session_id(token) is None


def test_renew_window_constant():
    assert RENEW_WINDOW_S == 86400.0


@pytest.mark.parametrize("offset,expected", [
    (4 * 86400, False),
    (86400 + 1, False),
    (86400 - 1, True),
    (60, True),
    (-60, True),
])
def test_needs_renewal(offset, expected):
    assert needs_renewal(_jwt(NOW + offset, SessionId="s"), NOW) is expected


def test_needs_renewal_false_for_unreadable_token():
    assert needs_renewal("garbage", NOW) is False
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONUTF8=1 python -m pytest tests/test_f1_account.py -q -p no:warnings`
Expected: collection error — `cannot import name 'RENEW_WINDOW_S'`.

- [ ] **Step 3: Implement** in `raceflag/f1_account.py`

Rename `_token_expiry` to `token_expiry` everywhere in the file (definition and the two call sites). Add after `LOGIN_NEEDED`:

```python
RENEW_WINDOW_S = 86400.0  # renew when less than a day is left
```

Add `_claims` (below) above `token_expiry`, and rewrite `token_expiry` to use it so the JWT decoding lives in one place:

```python
def token_expiry(token: str) -> float:
    """The token's `exp` claim (epoch seconds). Raises LoginError if it isn't a JWT with one."""
    parts = token.split(".")
    exp = _claims(token).get("exp") if len(parts) == 3 and all(parts) else None
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        raise LoginError("That isn't an F1 login. Paste the login-session cookie or its token.")
    return float(exp)
```

Then add after `token_expiry`:

```python
def _claims(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except ValueError:
        return {}
    return claims if isinstance(claims, dict) else {}


def session_id(token: str) -> str | None:
    """The F1 sign-in session the token belongs to (used to renew it), if present."""
    value = _claims(token).get("SessionId")
    return value.strip() if isinstance(value, str) and value.strip() else None


def needs_renewal(token: str, now: float) -> bool:
    """True when a readable token has less than RENEW_WINDOW_S left (or has expired)."""
    try:
        return token_expiry(token) - now < RENEW_WINDOW_S
    except LoginError:
        return False
```

Replace `account_status` with:

```python
def account_status(token: str, now: float, rejected: bool = False) -> dict:
    """{"status", "signed_in", "expires_at"}.

    status: "signed_out" (nothing saved), "signed_in" (saved, unexpired, not refused) or
    "sign_in_again" (expired, unreadable, or F1 refused a renewal).
    """
    if not token:
        return {"status": "signed_out", "signed_in": False, "expires_at": None}
    try:
        exp = token_expiry(token)
    except LoginError:
        return {"status": "sign_in_again", "signed_in": False, "expires_at": None}
    expires_at = datetime.fromtimestamp(exp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    signed_in = exp > now and not rejected
    return {"status": "signed_in" if signed_in else "sign_in_again",
            "signed_in": signed_in, "expires_at": expires_at}
```

`auth_headers` keeps using `account_status(token, now)["signed_in"]` (a refused-but-unexpired token is still sent: F1 may accept it for the archive even if renewal was refused).

- [ ] **Step 4: Run the tests**

Run: `PYTHONUTF8=1 python -m pytest tests/test_f1_account.py tests/test_web_server.py -q -p no:warnings`
Expected: `test_f1_account.py` all pass. In `test_web_server.py` the account tests that compare whole dicts now fail (`test_account_starts_signed_out`, `test_account_sign_out_clears_token`) — update those two expectations to include `"status": "signed_out"`:

```python
    assert c.get("/api/f1/account").json() == {"status": "signed_out", "signed_in": False, "expires_at": None}
```
```python
    assert resp.json() == {"status": "signed_out", "signed_in": False, "expires_at": None}
```

Re-run; expected: only the two baseline failures in `test_web_server.py`.

- [ ] **Step 5: Commit**

```bash
git add raceflag/f1_account.py tests/test_f1_account.py tests/test_web_server.py
git commit -m "feat: F1 login session ID, renewal window and sign-in-again status"
```

---

### Task 2: F1LoginRenewer

**Files:**
- Create: `raceflag/f1_renewal.py`
- Test: `tests/test_f1_renewal.py`

**Interfaces:**
- Consumes: `token_expiry`, `session_id`, `needs_renewal`, `LoginError` (Task 1); `raceflag.config.save`.
- Produces:
  - `class F1LoginRenewer(config, config_path=None, save=save_config, client_factory=httpx.AsyncClient, clock=time.time)`
    - `rejected: bool` (runtime only, starts False)
    - `login_saved() -> None` — clears `rejected` (call after a new login is saved or on sign-out)
    - `async check_once() -> str` — one of `"skipped"`, `"renewed"`, `"rejected"`, `"retry"`
    - `async run() -> None` — sleep `FIRST_CHECK_S`, then `check_once()` every `CHECK_INTERVAL_S`, forever; never raises
  - `RENEW_URL`, `FIRST_CHECK_S = 30.0`, `CHECK_INTERVAL_S = 3600.0`

- [ ] **Step 1: Write the failing tests** (`tests/test_f1_renewal.py`)

```python
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from raceflag.f1_renewal import RENEW_URL, F1LoginRenewer

NOW = 1_792_000_000.0


def _jwt(exp: float, **claims) -> str:
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'x'})}.{part({'exp': int(exp), **claims})}.sig"


OLD = _jwt(NOW + 3600, SessionId="sess-1")          # 1 h left: inside the renewal window
NEW = _jwt(NOW + 4 * 86400, SessionId="sess-1")


def _client(status=200, body=None, exc=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json = MagicMock(return_value=body if body is not None else {})
    client = AsyncMock()
    client.post = AsyncMock(side_effect=exc) if exc else AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=ctx)
    return factory, client


def _renewer(token=OLD, **client_kwargs):
    config = SimpleNamespace(f1_token=token)
    save = MagicMock()
    factory, client = _client(**client_kwargs)
    r = F1LoginRenewer(config, config_path="cfg.json", save=save, client_factory=factory, clock=lambda: NOW)
    return r, config, save, client


async def test_renews_and_saves_new_token():
    r, config, save, client = _renewer(body={"data": {"subscriptionToken": NEW}})
    assert await r.check_once() == "renewed"
    assert config.f1_token == NEW
    save.assert_called_once_with(config, "cfg.json")
    args, kwargs = client.post.call_args
    assert args[0] == RENEW_URL
    assert kwargs["json"] == {}
    headers = kwargs["headers"]
    assert headers["cd-sessionid"] == "sess-1"
    assert headers["apikey"] == "fCUCjWrKPu9ylJwRAv8BpGLEgiAuThx7"
    assert headers["CD-SystemId"] == "60a9ad84-e93d-480f-80d6-af37494f2e22"
    assert headers["orderSubmitted"] == "true"


async def test_skips_when_not_in_window():
    r, config, save, client = _renewer(token=NEW)
    assert await r.check_once() == "skipped"
    client.post.assert_not_called()


async def test_skips_without_token_or_session_id():
    for token in ("", _jwt(NOW + 60)):
        r, _, _, client = _renewer(token=token)
        assert await r.check_once() == "skipped"
        client.post.assert_not_called()


async def test_renews_an_already_expired_token():
    r, config, _, _ = _renewer(token=_jwt(NOW - 3600, SessionId="s"), body={"data": {"subscriptionToken": NEW}})
    assert await r.check_once() == "renewed"
    assert config.f1_token == NEW


@pytest.mark.parametrize("status", [401, 403])
async def test_refusal_marks_rejected_and_keeps_token(status):
    r, config, save, _ = _renewer(status=status)
    assert await r.check_once() == "rejected"
    assert r.rejected is True
    assert config.f1_token == OLD
    save.assert_not_called()


async def test_rejected_stops_further_calls():
    r, _, _, client = _renewer(status=401)
    await r.check_once()
    client.post.reset_mock()
    assert await r.check_once() == "skipped"
    client.post.assert_not_called()


async def test_login_saved_clears_rejected():
    r, _, _, _ = _renewer(status=401)
    await r.check_once()
    r.login_saved()
    assert r.rejected is False


@pytest.mark.parametrize("kwargs", [
    {"status": 500},
    {"status": 200, "body": {}},
    {"status": 200, "body": {"data": {"subscriptionToken": 5}}},
    {"exc": httpx.ConnectError("down")},
    {"exc": httpx.ReadTimeout("slow")},
])
async def test_transient_problems_retry_and_change_nothing(kwargs):
    r, config, save, _ = _renewer(**kwargs)
    assert await r.check_once() == "retry"
    assert r.rejected is False
    assert config.f1_token == OLD
    save.assert_not_called()


async def test_renewal_ignores_token_that_is_not_newer():
    r, config, save, _ = _renewer(body={"data": {"subscriptionToken": _jwt(NOW + 60, SessionId="sess-1")}})
    assert await r.check_once() == "retry"
    assert config.f1_token == OLD
    save.assert_not_called()


async def test_renewal_does_not_overwrite_a_login_saved_meanwhile():
    r, config, save, client = _renewer(body={"data": {"subscriptionToken": NEW}})
    replacement = _jwt(NOW + 2 * 86400, SessionId="other")

    async def post(*args, **kwargs):
        config.f1_token = replacement      # the user saves a new login during the request
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(return_value={"data": {"subscriptionToken": NEW}})
        return resp
    client.post = AsyncMock(side_effect=post)
    assert await r.check_once() == "skipped"
    assert config.f1_token == replacement
    save.assert_not_called()


async def test_save_failure_keeps_renewed_token_in_memory():
    r, config, save, _ = _renewer(body={"data": {"subscriptionToken": NEW}})
    save.side_effect = OSError("read-only")
    assert await r.check_once() == "renewed"
    assert config.f1_token == NEW


async def test_never_logs_the_token(caplog):
    r, _, _, _ = _renewer(body={"data": {"subscriptionToken": NEW}})
    with caplog.at_level("DEBUG"):
        await r.check_once()
    assert NEW not in caplog.text and OLD not in caplog.text and "sess-1" not in caplog.text
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONUTF8=1 python -m pytest tests/test_f1_renewal.py -q -p no:warnings`
Expected: `ModuleNotFoundError: No module named 'raceflag.f1_renewal'`.

- [ ] **Step 3: Implement** `raceflag/f1_renewal.py`

```python
"""Automatic F1 login renewal (feature 017, beta).

The F1 login token lasts 4 days; its SessionId claim names an F1 sign-in session that lasts
about 30 days. Before the token expires, F1's account service swaps it for a fresh one.

The renewal request (endpoint, API key, system ID, headers) follows the open-source
f1_sensor project (https://github.com/Nicxe/f1_sensor, custom_components/f1_sensor/auth.py,
MIT licence, Copyright (c) Nicxe). The API key and system ID are the public values that
formula1.com's own web pages send.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Callable

import httpx

from raceflag.config import save as save_config
from raceflag.f1_account import LoginError, needs_renewal, session_id, token_expiry

logger = logging.getLogger(__name__)

RENEW_URL = "https://api.formula1.com/v1/account/Subscriber/RetrieveSubscriber"
_API_KEY = "fCUCjWrKPu9ylJwRAv8BpGLEgiAuThx7"
_SYSTEM_ID = "60a9ad84-e93d-480f-80d6-af37494f2e22"
_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/133.0.0.0 Safari/537.36")

FIRST_CHECK_S = 30.0
CHECK_INTERVAL_S = 3600.0


class F1LoginRenewer:
    def __init__(self, config, config_path=None, save: Callable = save_config,
                 client_factory: Callable = httpx.AsyncClient, clock: Callable[[], float] = time.time) -> None:
        self._config = config
        self._config_path = config_path
        self._save = save
        self._client_factory = client_factory
        self._clock = clock
        self.rejected = False  # F1 refused a renewal; cleared when a new login is saved

    def login_saved(self) -> None:
        self.rejected = False

    async def check_once(self) -> str:
        token = self._config.f1_token
        if not token or self.rejected or not needs_renewal(token, self._clock()):
            return "skipped"
        sid = session_id(token)
        if not sid:
            return "skipped"
        headers = {
            "User-Agent": _USER_AGENT,
            "Content-Type": "application/json",
            "apikey": _API_KEY,
            "CD-SystemId": _SYSTEM_ID,
            "cd-sessionid": sid,
            "orderSubmitted": "true",
        }
        try:
            async with self._client_factory(timeout=15.0) as client:
                resp = await client.post(RENEW_URL, headers=headers, json={})
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            logger.info("F1 login renewal: no answer (%s) — will retry", type(exc).__name__)
            return "retry"
        if resp.status_code in (401, 403):
            self.rejected = True
            logger.warning("F1 login renewal refused — sign in again in Settings › F1 Account")
            return "rejected"
        if resp.status_code != 200:
            logger.info("F1 login renewal: HTTP %s — will retry", resp.status_code)
            return "retry"
        try:
            new = resp.json()["data"]["subscriptionToken"]
            if not isinstance(new, str) or token_expiry(new) <= token_expiry(token):
                raise ValueError("not newer")
        except (ValueError, KeyError, TypeError, LoginError):
            logger.info("F1 login renewal: unusable answer — will retry")
            return "retry"
        if self._config.f1_token != token:
            return "skipped"  # a new login was saved during the request; keep it
        self._config.f1_token = new
        if self._config_path:
            try:
                self._save(self._config, self._config_path)
            except OSError as exc:
                logger.warning("F1 login renewed but not saved: %s", type(exc).__name__)
        until = datetime.fromtimestamp(token_expiry(new), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        logger.info("F1 login renewed until %s", until)
        return "renewed"

    async def run(self) -> None:
        await asyncio.sleep(FIRST_CHECK_S)
        while True:
            try:
                await self.check_once()
            except Exception as exc:  # never let the loop die; never log token details
                logger.warning("F1 login renewal check failed: %s", type(exc).__name__)
            await asyncio.sleep(CHECK_INTERVAL_S)
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONUTF8=1 python -m pytest tests/test_f1_renewal.py -q -p no:warnings`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add raceflag/f1_renewal.py tests/test_f1_renewal.py
git commit -m "feat: renew the F1 login automatically before it expires"
```

---

### Task 3: Routes report the status; start the renewer

**Files:**
- Modify: `raceflag/web_server.py` (`create_app` signature; the three `/api/f1/account` routes)
- Modify: `raceflag/main.py` (create the renewer, pass it, add `renewer.run()` to `tasks`)
- Test: `tests/test_web_server.py`

**Interfaces:**
- Consumes: `account_status(token, now, rejected)` (Task 1); `F1LoginRenewer.rejected`, `.login_saved()`, `.run()` (Task 2).
- Produces: `create_app(..., f1_renewer=None)`.

- [ ] **Step 1: Add the failing tests** (append to the F1 account section of `tests/test_web_server.py`)

```python
@pytest.fixture
def renewer_env(tmp_path, app_state, config, led):
    renewer = MagicMock()
    renewer.rejected = False
    path = tmp_path / "config.json"
    c = TestClient(create_app(state=app_state, config=config, led=led, config_path=path, f1_renewer=renewer))
    return c, renewer


def test_account_reports_sign_in_again_when_renewal_refused(renewer_env, config):
    c, renewer = renewer_env
    config.f1_token = _account_token()
    renewer.rejected = True
    data = c.get("/api/f1/account").json()
    assert data["status"] == "sign_in_again"
    assert data["signed_in"] is False


def test_saving_a_login_clears_the_refusal(renewer_env):
    c, renewer = renewer_env
    resp = c.post("/api/f1/account", json={"login": _account_token()})
    assert resp.status_code == 200
    renewer.login_saved.assert_called_once_with()


def test_sign_out_clears_the_refusal(renewer_env):
    c, renewer = renewer_env
    c.delete("/api/f1/account")
    renewer.login_saved.assert_called_once_with()


def test_rejected_login_does_not_call_login_saved(renewer_env):
    c, renewer = renewer_env
    c.post("/api/f1/account", json={"login": "junk"})
    renewer.login_saved.assert_not_called()
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONUTF8=1 python -m pytest tests/test_web_server.py -q -p no:warnings -k "renewal or refusal or rejected_login"`
Expected: FAIL — `create_app() got an unexpected keyword argument 'f1_renewer'`.

- [ ] **Step 3: Implement** in `raceflag/web_server.py`

Add `f1_renewer=None,` to the `create_app` signature after `flag_router=None,`. Replace the three F1 account routes with:

```python
    # F1 account (feature 017). The token's expiry is wall-clock epoch time.
    def _f1_status(token: str) -> dict:
        rejected = bool(f1_renewer and f1_renewer.rejected)
        return account_status(token, time.time(), rejected=rejected)

    @app.get("/api/f1/account")
    async def get_f1_account():
        return _f1_status(config.f1_token)

    @app.post("/api/f1/account")
    async def set_f1_account(req: F1LoginRequest):
        try:
            token = parse_login(req.login, time.time())
        except LoginError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        config.f1_token = token
        if config_path:
            save_config(config, config_path)
        if f1_renewer is not None:
            f1_renewer.login_saved()
        return _f1_status(token)

    @app.delete("/api/f1/account")
    async def clear_f1_account():
        config.f1_token = ""
        if config_path:
            save_config(config, config_path)
        if f1_renewer is not None:
            f1_renewer.login_saved()
        return _f1_status("")
```

In `raceflag/main.py`: add `from raceflag.f1_renewal import F1LoginRenewer` with the other raceflag imports; after the `replay = ReplayManager(...)` line add

```python
    f1_renewer = F1LoginRenewer(config, CONFIG_PATH)
```

pass `f1_renewer=f1_renewer,` to `create_app(...)` (after `flag_router=flag_router,`), and add `f1_renewer.run()` to the `tasks` list.

- [ ] **Step 4: Run the full suite**

Run: `PYTHONUTF8=1 python -m pytest tests -q -p no:warnings`
Expected: only the six baseline failures.

- [ ] **Step 5: Commit**

```bash
git add raceflag/web_server.py raceflag/main.py tests/test_web_server.py
git commit -m "feat: F1 account routes report sign-in-again; start login renewal"
```

---

### Task 4: Settings row, Set up dialog, bookmark, receiver, Replay button

**Files:**
- Modify: `raceflag/frontend/index.html` (the `f1-account-row` block; add an `f1-setup-overlay` dialog after `schedule-overlay`; the `replay-login-msg` element)
- Modify: `raceflag/frontend/style.css` (the "F1 account (feature 017)" block)
- Modify: `raceflag/frontend/app.js` (the "F1 account (feature 017)" section and `_showReplayLoginMsg`)

**Interfaces:**
- Consumes: `GET/POST/DELETE /api/f1/account` returning `{status, signed_in, expires_at}` (Task 3); 422 `{"detail"}`.

- [ ] **Step 1: Settings row** — in `index.html` replace the whole `<div class="settings-row f1-account-row"> … </div>` block with:

```html
      <div class="settings-row f1-account-row">
        <div class="f1-account-head">
          <div class="f1-account-text">
            <span class="settings-label">F1 Account</span>
            <span class="f1-status" id="f1-account-status" data-status="signed_out">
              <svg class="f1-icon f1-icon-signed_out" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12"></path><path d="M18 6L6 18"></path></svg>
              <svg class="f1-icon f1-icon-signed_in" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12l5 5L20 7"></path></svg>
              <svg class="f1-icon f1-icon-sign_in_again" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l10 18H2z"></path><path d="M12 10v5"></path><path d="M12 18v.01"></path></svg>
              <span id="f1-account-status-text">Not signed in</span>
            </span>
          </div>
          <button type="button" class="btn-update" id="btn-f1-setup">Set up</button>
          <button type="button" class="btn-f1-signout" id="btn-f1-signout" hidden>Sign out</button>
        </div>
        <span class="f1-account-msg" id="f1-account-msg" role="status" hidden></span>
      </div>
```

- [ ] **Step 2: Set up dialog** — in `index.html`, after the closing `</div>` of `schedule-overlay`, add:

```html
  <div class="overlay" id="f1-setup-overlay">
    <div class="confirm-dialog mode-dialog" role="dialog" aria-modal="true" aria-labelledby="f1-setup-title">
      <div class="mode-dialog-head">
        <div class="confirm-title" id="f1-setup-title">Connect your F1 account</div>
        <div class="confirm-body">Add a “Send to RaceFlag” bookmark once. To sign in, tap it while signed in on f1tv.formula1.com.</div>
      </div>
      <ol class="f1-steps">
        <li>Tap <b>Copy bookmark code</b> below.</li>
        <li>Bookmark any page, then edit that bookmark: name it <b>Send to RaceFlag</b> and replace its address with the copied code.</li>
        <li>Open <a href="https://f1tv.formula1.com/" target="_blank" rel="noopener">f1tv.formula1.com</a>, sign in, then open the bookmark. On Android Chrome, type “Send to RaceFlag” in the address bar and tap it.</li>
      </ol>
      <textarea class="f1-bookmark-code" id="f1-bookmark-code" readonly rows="3" aria-label="Bookmark code"></textarea>
      <button type="button" class="btn-confirm-stop" id="btn-f1-copy">Copy bookmark code</button>
      <div class="f1-setup-note">On a computer you can drag this link to the bookmarks bar: <a class="f1-bookmark-link" id="f1-bookmark-link" href="#">Send to RaceFlag</a></div>
      <div class="f1-setup-note">This bookmark sends your login to <span class="f1-addr">raceflag.local:8080</span></div>
      <div class="f1-paste">
        <label class="dialog-field-label" for="f1-login-input">OR PASTE YOUR LOGIN</label>
        <div class="f1-account-form">
          <input type="text" id="f1-login-input" placeholder="login-session cookie or token"
                 autocomplete="off" autocapitalize="off" spellcheck="false">
          <button type="button" class="btn-f1-signout" id="btn-f1-login-save">Save</button>
        </div>
        <span class="f1-account-msg" id="f1-setup-msg" role="status" hidden></span>
      </div>
      <div class="confirm-actions">
        <button type="button" class="btn-confirm-cancel" id="btn-f1-setup-close">Close</button>
      </div>
    </div>
  </div>
```

Also add `#f1-setup-overlay` to the selector list on `style.css`'s line that centres dialogs (`#confirm-replay-overlay, #confirm-shutdown-overlay, #lamp-overlay, #schedule-overlay { align-items: center; padding: 0 16px; }`).

- [ ] **Step 3: Replay message with a button** — in `index.html` replace `<div class="replay-login-msg" id="replay-login-msg" role="status" hidden></div>` with:

```html
    <div class="replay-login-msg" id="replay-login-msg" role="status" hidden>
      <span id="replay-login-text"></span>
      <button type="button" class="btn-f1-signout" id="btn-replay-open-settings">Open Settings</button>
    </div>
```

- [ ] **Step 4: Styles** — in `style.css` replace the whole `/* F1 account (feature 017) */` block (keep the `.replay-login-msg` lines above it but change `.replay-login-msg` to `display: flex; flex-direction: column; align-items: flex-start; gap: 8px; font-size: 12px; color: #ffd700; line-height: 1.4;`) with:

```css
/* F1 account (feature 017) */
.settings-row.f1-account-row { flex-direction: column; align-items: stretch; gap: 8px; }
.f1-account-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.f1-account-text { display: flex; flex-direction: column; gap: 2px; }
.f1-status { display: flex; align-items: center; gap: 8px; font-size: 15px; font-weight: 600; }
.f1-status[data-status="signed_out"] { color: #ff5a52; }
.f1-status[data-status="signed_in"] { color: #3ecf6b; }
.f1-status[data-status="sign_in_again"] { color: #ffd700; }
.f1-icon { display: none; flex-shrink: 0; }
.f1-status[data-status="signed_out"] .f1-icon-signed_out,
.f1-status[data-status="signed_in"] .f1-icon-signed_in,
.f1-status[data-status="sign_in_again"] .f1-icon-sign_in_again { display: block; }
#btn-f1-setup { min-height: 44px; flex-shrink: 0; }
#btn-f1-setup[hidden] { display: none; }
.btn-f1-signout {
  flex-shrink: 0; background: #2a2a2a; border: 1px solid #555; color: #ddd; border-radius: 6px;
  padding: 0 14px; min-height: 44px; font-size: 12px; font-weight: 700; font-family: inherit; cursor: pointer;
}
.btn-f1-signout[hidden] { display: none; }
.f1-account-msg { font-size: 12px; color: #aaa; }
.f1-account-msg.error { color: #ff5a52; }
.f1-account-msg[hidden] { display: none; }
.f1-steps { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 8px; font-size: 13px; line-height: 1.45; color: #ddd; }
.f1-steps a, .f1-setup-note a { color: #ff5a52; }
.f1-bookmark-code {
  width: 100%; box-sizing: border-box; background: #111; border: 1px solid #333; color: #bbb; border-radius: 6px;
  padding: 8px; font-size: 11px; font-family: Consolas, 'Cascadia Mono', monospace; resize: none;
}
.f1-setup-note { font-size: 12px; color: #999; }
.f1-addr { color: #ddd; font-family: Consolas, 'Cascadia Mono', monospace; }
.f1-paste { border-top: 1px solid #2a2a2a; padding-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.f1-account-form { display: flex; gap: 8px; }
.f1-account-form input {
  flex: 1; min-width: 0; background: #2a2a2a; border: 1px solid #444; color: #eee;
  border-radius: 6px; padding: 0 10px; min-height: 44px; font-size: 13px; font-family: inherit;
}
.f1-account-form input:focus-visible, .f1-bookmark-code:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }
.replay-login-msg[hidden] { display: none; }
```

- [ ] **Step 5: Script** — in `app.js` replace the whole `// ── F1 account (feature 017)` section (from that comment down to, but not including, `// ── Replay`) with:

```js
// ── F1 account (feature 017) ───────────────────────────────────────────────
const F1_BOOKMARK_CODE = "javascript:(()=>{const c=document.cookie.split('; ').find(x=>x.startsWith('login-session='));if(!c){alert('Sign in on F1 TV first, then tap Send to RaceFlag again.');return;}location.href='http://raceflag.local:8080/#f1login='+encodeURIComponent(c.slice(14));})()";
const F1_STATUS_TEXT = { signed_out: 'Not signed in', signed_in: 'Signed in', sign_in_again: 'Sign in again' };

function _renderF1Account(acct) {
  const status = F1_STATUS_TEXT[acct.status] ? acct.status : 'signed_out';
  document.getElementById('f1-account-status').dataset.status = status;
  document.getElementById('f1-account-status-text').textContent = F1_STATUS_TEXT[status];
  document.getElementById('btn-f1-setup').hidden = status === 'signed_in';
  document.getElementById('btn-f1-signout').hidden = status !== 'signed_in';
}

function _showMsg(id, text, isError) {
  const el = document.getElementById(id);
  el.textContent = text;
  el.classList.toggle('error', !!isError);
  el.hidden = !text;
}

async function _loadF1Account() {
  try {
    const resp = await fetch('/api/f1/account');
    if (resp.ok) _renderF1Account(await resp.json());
  } catch (e) {}
}

async function _saveF1Login(value, msgId) {
  try {
    const resp = await fetch('/api/f1/account', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ login: value }),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok) {
      _renderF1Account(data);
      _showMsg(msgId, 'Signed in. Replays will use this login.', false);
      document.getElementById('replay-login-msg').hidden = true;
      return true;
    }
    _showMsg(msgId, data.detail || "Couldn't save that login.", true);
  } catch (e) {
    _showMsg(msgId, "Couldn't reach RaceFlag. Check the connection.", true);
  }
  return false;
}

function _openF1Setup() {
  document.getElementById('f1-bookmark-code').value = F1_BOOKMARK_CODE;
  document.getElementById('f1-bookmark-link').setAttribute('href', F1_BOOKMARK_CODE);
  _showMsg('f1-setup-msg', '', false);
  document.getElementById('f1-setup-overlay').classList.add('open');
}

function _closeF1Setup() {
  document.getElementById('f1-setup-overlay').classList.remove('open');
}

document.getElementById('btn-f1-setup').addEventListener('click', _openF1Setup);
document.getElementById('btn-f1-setup-close').addEventListener('click', _closeF1Setup);
document.getElementById('f1-setup-overlay').addEventListener('click', function (e) {
  if (e.target === this) _closeF1Setup();
});
document.getElementById('f1-bookmark-link').addEventListener('click', e => {
  e.preventDefault();  // the link is for dragging to the bookmarks bar, not for clicking here
  _showMsg('f1-setup-msg', 'Drag this link to your bookmarks bar.', false);
});

document.getElementById('btn-f1-copy').addEventListener('click', () => {
  // Plain http has no navigator.clipboard, so use the selection + execCommand route.
  const box = document.getElementById('f1-bookmark-code');
  box.focus();
  box.select();
  box.setSelectionRange(0, box.value.length);
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) {}
  _showMsg('f1-setup-msg', ok ? 'Copied. Now create the bookmark.' : 'Press and hold the code, then copy it.', !ok);
});

document.getElementById('btn-f1-login-save').addEventListener('click', async function () {
  const input = document.getElementById('f1-login-input');
  if (!input.value.trim()) return;
  this.disabled = true;
  if (await _saveF1Login(input.value, 'f1-setup-msg')) input.value = '';
  this.disabled = false;
});

document.getElementById('btn-f1-signout').addEventListener('click', async () => {
  try {
    const resp = await fetch('/api/f1/account', { method: 'DELETE' });
    if (resp.ok) {
      _renderF1Account(await resp.json());
      _showMsg('f1-account-msg', 'Signed out.', false);
    }
  } catch (e) {}
});

function _showReplayLoginMsg(text) {
  document.getElementById('replay-login-text').textContent = text;
  document.getElementById('replay-login-msg').hidden = false;
}

document.getElementById('btn-replay-open-settings').addEventListener('click', () => {
  document.getElementById('settings-overlay').classList.add('open');
  loadSettings();
  _loadF1Account();
});
```

Then append this block at the **very end of `app.js`** (after everything else has been defined and initialised, so `loadSettings` and the state it reads are ready):

```js
// "Send to RaceFlag" bookmark lands here as #f1login=<login-session cookie>.
(function _receiveF1Login() {
  const m = /^#f1login=(.*)$/.exec(location.hash);
  if (!m) return;
  history.replaceState(null, '', location.pathname + location.search);  // drop the login from the address bar
  let value;
  try { value = decodeURIComponent(m[1]); } catch (e) { return; }
  document.getElementById('settings-overlay').classList.add('open');
  loadSettings();
  _saveF1Login(value, 'f1-account-msg');
})();
```

(The first code block replaces the F1 account section in place; the `_receiveF1Login` block goes at the end of the file.)

- [ ] **Step 6: Check**

Run: `node --check raceflag/frontend/app.js` — expected: no output.

Start the dev server (`$env:PYTHONUTF8='1'; python <scratchpad>\rf_dev_server.py <repo>`, http://localhost:8080) and check in a browser:
- Settings shows ✕ "Not signed in" in red with **Set up**.
- **Set up** opens the dialog; the code box holds the bookmark code; **Copy bookmark code** reports "Copied" (or the fallback message).
- Pasting a valid token (generate one: `python -c "import base64,json,time;p=lambda o:base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b'=').decode();print(p({'a':1})+'.'+p({'exp':int(time.time())+3600,'SessionId':'x'})+'.s')"`) and **Save** → the row shows ✓ "Signed in" in green with **Sign out**.
- Opening `http://localhost:8080/#f1login=<that token>` signs in, opens Settings, and the address bar no longer shows `#f1login`.
- Opening `http://localhost:8080/#f1login=%E0%A4%A` (bad encoding) loads the page normally.

Stop only the dev server process you started.

- [ ] **Step 7: Commit**

```bash
git add raceflag/frontend/index.html raceflag/frontend/style.css raceflag/frontend/app.js
git commit -m "feat: F1 Account Set up dialog with Send to RaceFlag bookmark"
```

---

### Task 5: Specs and changelog

**Files:**
- Modify: `../raceflag_specs/features/017-f1-account.md`, `../raceflag_specs/contracts/api.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: 017 page** — update `features/017-f1-account.md` so it describes the built behaviour (read it first, keep its structure and `**Status:** beta on Pi`):
  - Behaviour: the three statuses with icon and colour (table from the design doc); no hint text; **Set up** dialog contents; the bookmark code verbatim and what it does (`#f1login=`, sent to `http://raceflag.local:8080`, address bar cleared); paste as fallback inside Set up; Sign out.
  - A new "Automatic renewal" subsection: the hourly check (30 s after start), the 24 h window, the request (endpoint and headers as in Global Constraints), outcomes (saved / refused → Sign in again and no more attempts until a new login / retried), the f1_sensor MIT attribution.
  - Remove "No automatic renewal yet" and the two Open questions (renewal, bookmark); add open questions: "Is `raceflag.local` reliable enough on phones, or is an IP fallback needed?" and "Does renewal work for free accounts? (unverified until tested on a unit)".
- [ ] **Step 2: api.md** — in `contracts/api.md` update the three `/api/f1/account` sections: responses gain `"status": "signed_in"|"sign_in_again"|"signed_out"` (example `{"status": "signed_in", "signed_in": true, "expires_at": "2026-10-20T21:55:38Z"}`); `sign_in_again` = expired, unreadable, or a renewal refused by F1. Change nothing else in the file.
- [ ] **Step 3: CHANGELOG** — under `[Unreleased]` → `### Added`, append:

```markdown
- F1 Account (beta): **Send to RaceFlag** bookmark sign-in — Set up gives you the bookmark code; sign in on f1tv.formula1.com and tap the bookmark to send your login to `raceflag.local:8080` (paste stays as a fallback). The login now **renews automatically** before its 4-day expiry, so you sign in again only when F1 ends the session (about a month). Settings shows ✕ Not signed in, ✓ Signed in or ⚠ Sign in again
```

- [ ] **Step 4: Commit both repos**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog for F1 bookmark sign-in and renewal"
cd ../raceflag_specs
git add features/017-f1-account.md contracts/api.md
git commit -m "spec: 017 F1 account — bookmark sign-in and automatic renewal (beta on Pi)"
```

- [ ] **Step 5: Final run**

Run: `PYTHONUTF8=1 python -m pytest tests -q -p no:warnings`
Expected: only the six baseline failures.
