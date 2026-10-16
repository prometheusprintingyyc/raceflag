"""Routes live and replay flag changes to the LEDs and the displayed status."""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

IDLE_STATUSES = frozenset({"unknown", "break", "finished"})
TIMED_EFFECTS = {"track_clear": 30.0, "race_start": 30.0, "checkered": 30.0}
RACE_SESSION_TYPES = frozenset({"race", "sprint"})


class FlagRouter:
    def __init__(self, state, led, config) -> None:
        self._state = state
        self._led = led
        self._config = config
        self._race_started = False
        self._pending_race_start: asyncio.Future | None = None

    def resolve(self, status: str) -> tuple[str, str]:
        """(status to display, effect to play) for a flag change; updates the race-start latch."""
        if status in IDLE_STATUSES:
            self._race_started = False
        if status == "race_start":
            # Live lights-out, reported by the listener. The page shows All Clear.
            self._race_started = True
            return "track_clear", "race_start"
        if (status == "track_clear"
                and not self._race_started
                and self._state.replay_mode
                and self._state.session.session_type.lower() in RACE_SESSION_TYPES):
            # Replay only: the last pre-race TrackStatus is re-fired at lights-out
            # (feature 006), so the first AllClear is the start.
            self._race_started = True
            return status, "race_start"
        return status, status

    def on_flag_change(self, status: str) -> None:
        state = self._state
        delay = 0.0 if state.replay_mode else self._config.delay_seconds
        logger.info("Flag change received: %s  delay=%.1fs", status, delay)
        status, effective = self.resolve(status)

        if (effective not in IDLE_STATUSES and effective not in TIMED_EFFECTS
                and (state.session.is_active or state.replay_mode)):
            if state.replay_mode:
                self._led.force_trigger(effective)
            else:
                self._led.trigger(effective)

        if delay <= 0:
            state.set_display_track_status(status)
            if status in IDLE_STATUSES:
                self._led.set_idle(True)
            elif effective in TIMED_EFFECTS:
                self._led.trigger_timed(effective, TIMED_EFFECTS[effective])
        else:
            task = asyncio.ensure_future(self._delayed(status, effective, delay))
            if effective == "race_start":
                self._pending_race_start = task

    async def _delayed(self, status: str, effective: str, delay: float) -> None:
        # Record when this status was received so trigger_timed only flushes items that
        # predate it — items queued during the delay window (e.g. a yellow flag that
        # arrived while waiting to fire track_clear) are preserved.
        scheduled_at = time.monotonic()
        await asyncio.sleep(delay)
        self._state.set_display_track_status(status)
        if status in IDLE_STATUSES:
            self._led.set_idle(True)
        elif effective in TIMED_EFFECTS:
            self._led.trigger_timed(effective, TIMED_EFFECTS[effective], flush_before=scheduled_at)

    def sync(self, delay: float) -> None:
        """Live delay Sync (feature 016): apply the delay and play the race start now."""
        if self._pending_race_start is not None:
            self._pending_race_start.cancel()
            self._pending_race_start = None
        self._led.set_delay(delay)
        self._state.set_display_track_status("track_clear")
        self._led.trigger_timed("race_start", TIMED_EFFECTS["race_start"])
