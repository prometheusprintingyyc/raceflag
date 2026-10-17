import base64
import json
from urllib.parse import quote

import pytest

from raceflag.f1_account import (
    LOGIN_NEEDED, LoginError, account_status, auth_headers, parse_login,
)

NOW = 1_792_000_000.0


def _jwt(exp: float, **claims) -> str:
    def part(obj):
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return f"{part({'alg': 'RS256'})}.{part({'exp': int(exp), **claims})}.c2ln"


TOKEN = _jwt(NOW + 4 * 86400, SessionId="abc")


def _cookie(token: str) -> str:
    return quote(json.dumps({"data": {"subscriptionToken": token, "subscriptionStatus": None}}))


def test_login_needed_message():
    assert LOGIN_NEEDED == "F1 login needed or expired. Paste a new one in Settings › F1 Account."


def test_parse_raw_token():
    assert parse_login(TOKEN, NOW) == TOKEN


def test_parse_url_encoded_cookie():
    assert parse_login(_cookie(TOKEN), NOW) == TOKEN


def test_parse_decoded_cookie():
    raw = json.dumps({"data": {"subscriptionToken": TOKEN}})
    assert parse_login(raw, NOW) == TOKEN


@pytest.mark.parametrize("wrap", [
    lambda t: f"  {t}\n",
    lambda t: f'"{t}"',
    lambda t: f"Bearer {t}",
    lambda t: f"bearer {t}",
])
def test_parse_tolerates_whitespace_quotes_and_bearer(wrap):
    assert parse_login(wrap(TOKEN), NOW) == TOKEN


def test_parse_quoted_cookie():
    assert parse_login(f'"{_cookie(TOKEN)}"', NOW) == TOKEN


@pytest.mark.parametrize("value", [
    "",
    "   ",
    "hello",
    "a.b",
    "not.a.jwt",
    quote(json.dumps({"data": {}})),
    quote(json.dumps({"something": "else"})),
    "%7Bbroken",
])
def test_parse_rejects_garbage(value):
    with pytest.raises(LoginError):
        parse_login(value, NOW)


def test_parse_rejects_expired_token():
    with pytest.raises(LoginError) as exc:
        parse_login(_jwt(NOW - 1), NOW)
    assert "expired" in str(exc.value)


def test_parse_rejects_token_without_expiry():
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    with pytest.raises(LoginError):
        parse_login(f"{part({'alg': 'x'})}.{part({'sub': 1})}.sig", NOW)


def test_status_not_signed_in():
    assert account_status("", NOW) == {"signed_in": False, "expires_at": None}


def test_status_signed_in_reports_expiry_iso_utc():
    status = account_status(TOKEN, NOW)
    assert status["signed_in"] is True
    assert status["expires_at"].endswith("Z")


def test_status_expired_token_is_not_signed_in():
    status = account_status(_jwt(NOW - 10), NOW)
    assert status["signed_in"] is False
    assert status["expires_at"] is not None


def test_status_unreadable_token_is_not_signed_in():
    assert account_status("garbage", NOW) == {"signed_in": False, "expires_at": None}


def test_auth_headers_with_valid_token():
    assert auth_headers(TOKEN, NOW) == {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.parametrize("token", ["", "garbage", _jwt(NOW - 1)])
def test_auth_headers_empty_without_usable_token(token):
    assert auth_headers(token, NOW) == {}
