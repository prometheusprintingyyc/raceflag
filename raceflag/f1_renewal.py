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
        """One renewal check: "skipped", "renewed", "rejected" or "retry"."""
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
