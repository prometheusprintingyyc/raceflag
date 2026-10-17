"""F1 account login (feature 017, beta). Pure functions — no I/O, no clocks.

Formula 1's live-timing archive (used by replay) needs a login token. A free F1 account
works. The user pastes either the token itself or the whole `login-session` cookie from
formula1.com; the token is a JWT whose `exp` claim says when it stops working.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from urllib.parse import unquote

LOGIN_NEEDED = "F1 login needed or expired. Paste a new one in Settings › F1 Account."


class LoginError(ValueError):
    """The pasted value can't be used; the message is shown to the user."""


def _token_expiry(token: str) -> float:
    """The token's `exp` claim (epoch seconds). Raises LoginError if it isn't a JWT with one."""
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise LoginError("That isn't an F1 login. Paste the login-session cookie or its token.")
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        exp = payload["exp"]
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):
            raise TypeError(exp)
    except (ValueError, KeyError, TypeError) as exc:
        raise LoginError("That isn't an F1 login. Paste the login-session cookie or its token.") from exc
    return float(exp)


def parse_login(value: str, now: float) -> str:
    """The token from a pasted cookie or token. Raises LoginError if unusable or expired."""
    text = value.strip().strip('"').strip()
    if text[:7].lower() == "bearer ":
        text = text[7:].strip()
    if text.startswith("{") or text[:3].upper() == "%7B":
        try:
            token = json.loads(unquote(text))["data"]["subscriptionToken"]
            if not isinstance(token, str):
                raise TypeError(token)
        except (ValueError, KeyError, TypeError) as exc:
            raise LoginError("That cookie has no F1 login in it. Sign in on formula1.com first.") from exc
        text = token.strip()
    if _token_expiry(text) <= now:
        raise LoginError("That F1 login has expired. Sign in on formula1.com again and copy a fresh one.")
    return text


def account_status(token: str, now: float) -> dict:
    """{"signed_in", "expires_at"}: signed in only while the saved token is unexpired."""
    if not token:
        return {"signed_in": False, "expires_at": None}
    try:
        exp = _token_expiry(token)
    except LoginError:
        return {"signed_in": False, "expires_at": None}
    expires_at = datetime.fromtimestamp(exp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"signed_in": exp > now, "expires_at": expires_at}


def auth_headers(token: str, now: float) -> dict:
    """Authorization header for archive requests, or {} without a usable token."""
    if account_status(token, now)["signed_in"]:
        return {"Authorization": f"Bearer {token}"}
    return {}
