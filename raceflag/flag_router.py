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
        # A red flag since the last start: the next lights-out (the restart) plays the start again.
        self._red_since_start = False
        # Every pending delayed display/timed-effect task, so Sync can cancel them all.
        self._pending: set[asyncio.Future] = set()
        # Live flag changes since the last live race_start: (status, effective, received_at).
        # None until a live race_start; cleared by an idle status. Sync re-times these.
        self._live_log: list[tuple[str, str, float]] | None = None

    def resolve(self, status: str) -> tuple[str, str | None]:
        """(status to display, effect to play) for a flag change; updates the race-start latch.

        The effect is None for a repeated lights-out: the page shows All Clear, nothing plays.
        """
        if status in IDLE_STATUSES:
            self._race_started = False
            self._red_since_start = False
        if status == "red_flag" and self._race_started:
            self._red_since_start = True
        if status == "race_start":
            # Lights-out (F1's Started), reported by the listener. The page shows All Clear.
            # One start per race; a restart after a red flag plays it again (feature 004).
            if self._race_started and not self._red_since_start:
                return "track_clear", None
            self._race_started = True
            self._red_since_start = False
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

    def _goes_to_led_queue(self, effective: str) -> bool:
        state = self._state
        return (effective not in IDLE_STATUSES and effective not in TIMED_EFFECTS
                and (state.session.is_active or state.replay_mode))

    def _schedule(self, status: str, effective: str | None, delay: float, received_at: float) -> None:
        task = asyncio.ensure_future(self._delayed(status, effective, delay, received_at))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def on_flag_change(self, status: str) -> None:
        received_at = time.monotonic()
        state = self._state
        delay = 0.0 if state.replay_mode else self._config.delay_seconds
        logger.info("Flag change received: %s  delay=%.1fs", status, delay)
        raw_status = status
        status, effective = self.resolve(status)

        if effective is None:
            # Repeated lights-out: update the page (after the delay), play nothing new.
            if delay <= 0:
                state.set_display_track_status(status)
            else:
                self._schedule(status, effective, delay, received_at)
            return

        if not state.replay_mode:
            if raw_status == "race_start":
                self._live_log = []
            elif status in IDLE_STATUSES:
                self._live_log = None
            elif self._live_log is not None:
                self._live_log.append((status, effective, received_at))

        if self._goes_to_led_queue(effective):
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
            self._schedule(status, effective, delay, received_at)

    async def _delayed(self, status: str, effective: str | None, delay: float, received_at: float) -> None:
        # trigger_timed only flushes queue items that predate this status's arrival —
        # items queued during the delay window (e.g. a yellow flag that arrived while
        # waiting to fire track_clear) are preserved.
        await asyncio.sleep(delay)
        self._state.set_display_track_status(status)
        if status in IDLE_STATUSES:
            self._led.set_idle(True)
        elif effective in TIMED_EFFECTS:
            self._led.trigger_timed(effective, TIMED_EFFECTS[effective], flush_before=received_at)

    def sync(self, delay: float) -> None:
        """Live delay Sync (feature 016): apply the delay, play the race start now, and
        re-time every live flag change since lights-out onto the new delay."""
        for task in list(self._pending):
            task.cancel()
        self._pending.clear()
        self._led.set_delay(delay)
        self._state.set_display_track_status("track_clear")
        # Full flush: every flag that arrived after lights-out is re-added below.
        self._led.trigger_timed("race_start", TIMED_EFFECTS["race_start"], flush_before=None)
        if not self._live_log:
            return
        now = time.monotonic()
        for status, effective, received_at in self._live_log:
            if self._goes_to_led_queue(effective):
                self._led.trigger_at(effective, received_at)
            self._schedule(status, effective, max(0.0, received_at + delay - now), received_at)
