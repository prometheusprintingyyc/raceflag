from __future__ import annotations

import json
import logging
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
        # Playback state (populated by Task 4)
        self._play_wall_origin: float = 0.0
        self._paused: bool = False
        self._pause_wall: float = 0.0
        self._sync_offset: float = 0.0
        self._task = None
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
