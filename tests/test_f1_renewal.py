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
