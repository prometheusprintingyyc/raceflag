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


def _parse_ts(ts: str) -> float:
    """Parse HH:MM:SS.mmm into total seconds."""
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _parse_jsonstream_line(line: str) -> tuple[float, dict] | None:
    """Parse one .jsonStream line into (seconds, payload). Returns None on bad input."""
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
    def __init__(self) -> None:
        self._events: list[tuple[float, str]] = []  # (race_time_seconds, flag_state)
        self._session_name: str = ""
        self._play_wall_origin: float = 0.0
        self._paused: bool = False
        self._pause_wall: float = 0.0
        self._sync_offset: float = 0.0
        self._task: asyncio.Task | None = None
        self._on_event: Callable[[str], None] | None = None

    async def get_sessions(self, year: int = 2025) -> list[dict]:
        """Fetch Index.json fresh and return Race sessions only."""
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
                if session.get("Type") != "Race":
                    continue
                start = session.get("StartDate", "")
                year_str = start[:4] if start else str(year)
                sessions.append({
                    "name": f"{year_str} {meeting_name}",
                    "path": session.get("Path", ""),
                    "date": start[:10],
                    "circuit": circuit,
                })
        return sessions

    async def load_session(self, path: str, session_name: str = "") -> int:
        """Fetch TrackStatus + RaceControlMessages streams, parse events, return event count."""
        base = f"{BASE_URL}/{path}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            ts_resp = await client.get(base + "TrackStatus.jsonStream")
            rc_resp = await client.get(base + "RaceControlMessages.jsonStream")
            ts_resp.raise_for_status()
            rc_resp.raise_for_status()

        ts_lines = ts_resp.text.strip().splitlines()
        rc_lines = rc_resp.text.strip().splitlines()

        lights_out = self._find_lights_out(ts_lines, rc_lines)
        logger.info("Replay lights-out offset: %.3fs", lights_out)

        events: list[tuple[float, str]] = [(0.0, "race_start")]
        for line in ts_lines:
            parsed = _parse_jsonstream_line(line)
            if parsed is None:
                continue
            abs_ts, payload = parsed
            race_time = abs_ts - lights_out
            if race_time <= 0:
                continue
            flag_state = TRACK_STATUS_MAP.get(str(payload.get("Status", "")))
            if flag_state:
                events.append((race_time, flag_state))

        events.sort(key=lambda x: x[0])
        self._events = events
        self._session_name = session_name or path
        preview = [(f"{t:.1f}s", s) for t, s in events[:6]]
        logger.info("Replay loaded %d events (lights_out=%.3fs); first events: %s", len(events), lights_out, preview)
        return len(events)

    def _find_lights_out(self, ts_lines: list[str], rc_lines: list[str]) -> float:
        """Return the absolute session timestamp of race lights-out (seconds)."""
        # Primary: "RACE STARTED" anywhere in a RaceControlMessages line
        for line in rc_lines:
            parsed = _parse_jsonstream_line(line)
            if parsed is None:
                continue
            ts, payload = parsed
            if "RACE STARTED" in json.dumps(payload):
                return ts

        # Fallback: first AllClear after a >= 5 min gap (end of formation lap)
        prev_ts = 0.0
        for line in ts_lines:
            parsed = _parse_jsonstream_line(line)
            if parsed is None:
                continue
            ts, payload = parsed
            if str(payload.get("Status", "")) == "1" and (ts - prev_ts) >= 300:
                return ts
            prev_ts = ts

        return 0.0

    async def play(self, on_event: Callable[[str], None]) -> None:
        """Start playback from the beginning of the loaded events."""
        self._on_event = on_event
        self._paused = False
        self._play_wall_origin = time.monotonic()
        self._task = asyncio.create_task(self._playback_loop())

    async def _playback_loop(self) -> None:
        for race_time, flag_state in self._events:
            target = self._play_wall_origin + race_time + self._sync_offset
            secs_from_now = target - time.monotonic()
            logger.info("Replay next: %s at race_time=%.1fs (fires in %.1fs)", flag_state, race_time, secs_from_now)
            while True:
                if self._paused:
                    await asyncio.sleep(0.05)
                    continue
                target = self._play_wall_origin + race_time + self._sync_offset
                remaining = target - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(remaining, 0.05))
            if self._on_event:
                self._on_event(flag_state)

    def pause(self) -> None:
        """Freeze the replay clock at the current race position."""
        if not self._paused:
            self._pause_wall = time.monotonic()
            self._paused = True

    def resume(self) -> None:
        """Unfreeze the replay clock, shifting origin forward by the pause duration."""
        if self._paused:
            self._play_wall_origin += time.monotonic() - self._pause_wall
            self._paused = False

    def stop(self) -> None:
        """Cancel playback and clear all loaded data."""
        if self._task:
            self._task.cancel()
            self._task = None
        self._paused = False
        self._sync_offset = 0.0
        self._events = []
        self._session_name = ""

    def set_sync_offset(self, seconds: float) -> None:
        """Set a timing offset in seconds, clamped to [-30.0, 30.0]."""
        self._sync_offset = max(-30.0, min(30.0, float(seconds)))
