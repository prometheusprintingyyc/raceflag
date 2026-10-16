"""Live delay Sync (feature 016). Pure functions — no I/O, no clocks."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

SYNC_WINDOW_S = 90.0  # the delay slider's maximum

NO_RACE = "Sync needs a live race or sprint"
NOT_STARTED = "The race hasn't started yet"
TOO_LATE = "Sync works within 90 s of the race start"

_RACE_SESSION_TYPES = frozenset({"race", "sprint"})


class SyncError(Exception):
    """Sync can't set a delay; the message is shown to the user."""


def is_live_race(session_type: str, is_active: bool, replay_mode: bool) -> bool:
    return is_active and not replay_mode and session_type.lower() in _RACE_SESSION_TYPES


def sync_delay(now: float, lights_out_at: float | None, race_live: bool) -> float:
    """Seconds from the feed's lights-out to the user's press, to 0.1 s (half up), 0–90."""
    if not race_live:
        raise SyncError(NO_RACE)
    if lights_out_at is None:
        raise SyncError(NOT_STARTED)
    elapsed = now - lights_out_at
    if elapsed > SYNC_WINDOW_S:
        raise SyncError(TOO_LATE)
    rounded = float(Decimal(repr(elapsed)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    return min(max(rounded, 0.0), SYNC_WINDOW_S)
