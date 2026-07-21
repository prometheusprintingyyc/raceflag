from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable

import httpx

from raceflag.state import TRACK_STATUS_MAP

logger = logging.getLogger(__name__)

BASE_URL = "https://livetiming.formula1.com/static"

# All streams consumed by live mode — replayed identically so state stays in sync
REPLAY_STREAMS = [
    "TrackStatus",
    "RaceControlMessages",
    "SessionStatus",
    "WeatherData",
    "TimingData",
    "TimingAppData",
    "DriverList",
    "LapCount",
]


def _parse_ts(ts: str) -> float:
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _parse_jsonstream_line(line: str) -> tuple[float, dict] | None:
    line = line.strip()
    if not line or len(line) < 13:
        return None
    try:
        ts = _parse_ts(line[:12])
        payload = json.loads(line[12:])
        return ts, payload
    except (ValueError, json.JSONDecodeError):
        return None


class ReplayManager:
    def __init__(
        self,
        on_feed: Callable[[str, dict, bool], None] | None = None,
    ) -> None:
        # Called with (topic, data, is_snapshot) for every replayed event.
        # When set, all topics route through the same F1Listener that live mode uses,
        # so LED callbacks, weather, positions, and session state all update identically.
        self._on_feed = on_feed
        # (race_time_seconds, topic, payload) — sorted chronologically, anchored to lights-out
        self._events: list[tuple[float, str, dict]] = []
        self._session_name: str = ""
        self._play_wall_origin: float = 0.0
        self._paused: bool = False
        self._pause_wall: float = 0.0
        self._sync_offset: float = 0.0
        self._task: asyncio.Task | None = None

    async def get_sessions(self, year: int = 2025) -> list[dict]:
        """Fetch Index.json and return Race and Sprint sessions."""
        url = f"{BASE_URL}/{year}/Index.json"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        sessions = []
        for meeting in data.get("Meetings", []):
            meeting_name = meeting.get("Name", "")
            circuit = meeting.get("Circuit", {}).get("ShortName", "")
            for session in meeting.get("Sessions", []):
                session_type = session.get("Type", "")
                session_name = session.get("Name", "")
                if session_type != "Race":
                    continue
                is_sprint = "sprint" in session_name.lower()
                start = session.get("StartDate", "")
                year_str = start[:4] if start else str(year)
                label = f"{year_str} {meeting_name}"
                if is_sprint:
                    label += " (Sprint)"
                sessions.append({
                    "name": label,
                    "path": session.get("Path", ""),
                    "date": start[:10],
                    "circuit": circuit,
                })
        return sessions

    async def _fetch_stream(
        self,
        client: httpx.AsyncClient,
        base: str,
        topic: str,
    ) -> list[tuple[float, str, dict]]:
        """Download one .jsonStream and return (abs_time, topic, payload) tuples."""
        events: list[tuple[float, str, dict]] = []
        try:
            resp = await client.get(base + f"{topic}.jsonStream")
            if resp.status_code != 200:
                logger.debug("Stream %s returned %s — skipping", topic, resp.status_code)
                return events
            text = resp.text.lstrip("﻿")
            for line in text.strip().splitlines():
                parsed = _parse_jsonstream_line(line)
                if parsed is not None:
                    abs_ts, payload = parsed
                    events.append((abs_ts, topic, payload))
        except Exception as exc:
            logger.warning("Failed to fetch %s stream: %s", topic, exc)
        return events

    def _find_lights_out(self, events: list[tuple[float, str, dict]]) -> float:
        """Return the absolute session timestamp (seconds) of race lights-out."""
        # Primary: "RACE STARTED" anywhere in a RaceControlMessages payload
        for abs_ts, topic, payload in events:
            if topic == "RaceControlMessages" and "RACE STARTED" in json.dumps(payload):
                logger.info("Lights-out: RACE STARTED message at %.1fs", abs_ts)
                return abs_ts

        # Secondary: SessionStatus changes to "Started" — fires at lights-out
        for abs_ts, topic, payload in events:
            if topic == "SessionStatus":
                status_msg = str(payload.get("Status", "") or payload.get("Message", "")).strip()
                if status_msg == "Started":
                    logger.info("Lights-out: SessionStatus Started at %.1fs", abs_ts)
                    return abs_ts

        # Tertiary: LapCount CurrentLap transitions to 1 — fires at lights-out
        for abs_ts, topic, payload in events:
            if topic == "LapCount":
                try:
                    if int(payload.get("CurrentLap", 0)) == 1:
                        logger.info("Lights-out: LapCount CurrentLap=1 at %.1fs", abs_ts)
                        return abs_ts
                except (ValueError, TypeError):
                    pass

        # Quaternary: first AllClear after a non-clear formation-lap state
        saw_non_clear = False
        for abs_ts, topic, payload in events:
            if topic != "TrackStatus":
                continue
            status = str(payload.get("Status", ""))
            if status in ("2", "4", "5", "6", "7"):
                saw_non_clear = True
            elif status == "1" and saw_non_clear and abs_ts >= 60:
                logger.info("Lights-out: first post-formation AllClear at %.1fs", abs_ts)
                return abs_ts

        logger.warning("Could not detect lights-out — defaulting to session start (t=0)")
        return 0.0

    async def load_session(self, path: str, session_name: str = "") -> int:
        """Download all timing streams and return the count of real-time events (race_time >= 0)."""
        base = f"{BASE_URL}/{path}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            results = await asyncio.gather(
                *[self._fetch_stream(client, base, topic) for topic in REPLAY_STREAMS],
                return_exceptions=True,
            )

        all_events: list[tuple[float, str, dict]] = []
        for result in results:
            if isinstance(result, list):
                all_events.extend(result)

        all_events.sort(key=lambda x: x[0])

        lights_out = self._find_lights_out(all_events)

        self._events = [(ts - lights_out, topic, data) for ts, topic, data in all_events]
        self._session_name = session_name or path

        realtime_count = sum(1 for rt, _, _ in self._events if rt >= 0)
        logger.info(
            "Replay loaded: %d total events, %d real-time, lights_out=%.1fs, session=%r",
            len(self._events), realtime_count, lights_out, self._session_name,
        )
        return realtime_count

    async def play(self, on_event: Callable[[str], None] | None = None) -> None:
        """Start playback. on_event is a legacy flag-only callback used when no on_feed is set."""
        self._paused = False
        self._play_wall_origin = time.monotonic()
        self._task = asyncio.create_task(self._playback_loop(on_event))

    async def _playback_loop(self, on_event: Callable[[str], None] | None = None) -> None:
        # Phase 1 — instant snapshot: replay everything before lights-out to restore
        # pre-race state (driver list, weather, session info, tyre data) without
        # firing any LED callbacks.
        for race_time, topic, data in self._events:
            if race_time >= 0:
                break
            if self._on_feed:
                self._on_feed(topic, data, True)

        # Phase 2 — real-time playback from lights-out onwards
        for race_time, topic, data in self._events:
            if race_time < 0:
                continue

            # Wait until the wall clock matches this event's race time (accounting for
            # pause durations and the sync offset slider)
            while True:
                if self._paused:
                    await asyncio.sleep(0.05)
                    continue
                remaining = (self._play_wall_origin + race_time + self._sync_offset) - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(remaining, 0.05))

            if self._on_feed:
                self._on_feed(topic, data, False)
            elif on_event and topic == "TrackStatus":
                # Legacy path: no on_feed callback, drive LEDs directly via flag state
                flag_state = TRACK_STATUS_MAP.get(str(data.get("Status", "")))
                if flag_state:
                    on_event(flag_state)

    def pause(self) -> None:
        if not self._paused:
            self._pause_wall = time.monotonic()
            self._paused = True

    def resume(self) -> None:
        if self._paused:
            # Shift the wall-clock origin forward by the pause duration so
            # race_time positions stay correct after resuming
            self._play_wall_origin += time.monotonic() - self._pause_wall
            self._paused = False

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        self._paused = False
        self._sync_offset = 0.0
        self._events = []
        self._session_name = ""

    def set_sync_offset(self, seconds: float) -> None:
        self._sync_offset = max(-30.0, min(30.0, float(seconds)))
