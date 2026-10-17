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

LOGIN_NEEDED = "F1 login needed or expired. Sign in again."
RENEW_WINDOW_S = 86400.0  # renew when less than a day is left


class LoginError(ValueError):
    """The pasted value can't be used; the message is shown to the user."""


def _claims(token: str) -> dict:
    """The JWT's payload claims, or {} if it can't be read."""
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except ValueError:
        return {}
    return claims if isinstance(claims, dict) else {}


def token_expiry(token: str) -> float:
    """The token's `exp` claim (epoch seconds). Raises LoginError if it isn't a JWT with one."""
    parts = token.split(".")
    exp = _claims(token).get("exp") if len(parts) == 3 and all(parts) else None
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        raise LoginError("That isn't an F1 login. Paste the login-session cookie or its token.")
    return float(exp)


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
    if token_expiry(text) <= now:
        raise LoginError("That F1 login has expired. Sign in on formula1.com again and copy a fresh one.")
    return text


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


def auth_headers(token: str, now: float) -> dict:
    """Authorization header for archive requests, or {} without a usable token."""
    if account_status(token, now)["signed_in"]:
        return {"Authorization": f"Bearer {token}"}
    return {}
