from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime
from urllib.parse import quote

import httpx
import websockets

from raceflag.state import (
    AppState, WeatherInfo, RaceControlMessage, SessionInfo, DriverPosition,
    TRACK_STATUS_MAP, FLAG_COLORS, TEAM_COLORS, COUNTRY_FLAGS,
)

logger = logging.getLogger(__name__)

SIGNALR_BASE = "https://livetiming.formula1.com/signalr"
WS_BASE = "wss://livetiming.formula1.com/signalr"
# Hub name must be "Streaming" (capital S) — f1_sensor confirms this
CONNECTION_DATA = '[{"name":"Streaming"}]'
TOPICS = [
    "TrackStatus", "RaceControlMessages", "SessionInfo", "LapCount",
    "ExtrapolatedClock", "WeatherData", "TimingData", "TimingAppData", "DriverList",
]

FLAG_COLOR_MAP: dict[str, str] = {
    "GREEN": FLAG_COLORS["track_clear"],
    "YELLOW": FLAG_COLORS["yellow_flag"],
    "RED": FLAG_COLORS["red_flag"],
    "CHEQUERED": FLAG_COLORS["checkered"],
    "SAFETY CAR": FLAG_COLORS["safety_car"],
    "VSC": FLAG_COLORS["virtual_sc"],
}


def parse_track_status(data: dict) -> str:
    return TRACK_STATUS_MAP.get(str(data.get("Status", "")), "unknown")


def parse_weather(data: dict) -> WeatherInfo:
    return WeatherInfo(
        air_temp=float(data.get("AirTemp", 0)),
        track_temp=float(data.get("TrackTemp", 0)),
        humidity=float(data.get("Humidity", 0)),
        wind_speed=float(data.get("WindSpeed", 0)),
        wind_direction=str(data.get("WindDirection", "")),
        rain=str(data.get("Rainfall", "0")) != "0",
    )


def parse_session_info(data: dict) -> SessionInfo:
    meeting = data.get("Meeting", {})
    country = meeting.get("Country", {}).get("Name", "")
    return SessionInfo(
        name=meeting.get("Name", ""),
        circuit=meeting.get("Circuit", {}).get("ShortName", ""),
        venue=meeting.get("Location", ""),
        country_flag=COUNTRY_FLAGS.get(country, ""),
        session_type=data.get("Name", data.get("Type", "")),
        is_active=True,
    )


def parse_race_control(data: dict) -> RaceControlMessage:
    utc_str = data.get("Utc", "")
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        time_str = dt.strftime("%H:%M:%S")
    except ValueError:
        time_str = utc_str
    flag_key = str(data.get("Flag", "")).upper()
    return RaceControlMessage(
        time=time_str,
        message=str(data.get("Message", "")),
        flag_color=FLAG_COLOR_MAP.get(flag_key),
    )


class F1Listener:
    def __init__(self, state: AppState, on_track_status_change=None):
        self._state = state
        self._on_track_status_change = on_track_status_change
        self._running = False
        self._driver_list: dict = {}

    def _ensure_active(self) -> None:
        """Mark session active on first feed message — any data means a live session is running."""
        if not self._state.session.is_active:
            session = self._state.session
            self._state.set_session(SessionInfo(
                name=session.name,
                circuit=session.circuit,
                venue=session.venue,
                country_flag=session.country_flag,
                session_type=session.session_type,
                is_active=True,
                current_lap=session.current_lap,
                total_laps=session.total_laps,
                time_remaining=session.time_remaining,
            ))
            logger.info("Session marked active from incoming feed data")

    def _update_driver_positions(self, data: dict) -> None:
        lines = data.get("Lines", {})
        if not isinstance(lines, dict):
            return
        positions: list[DriverPosition] = []
        for racing_number, line in lines.items():
            if not isinstance(line, dict):
                continue
            driver_info = self._driver_list.get(str(racing_number), {})
            team = driver_info.get("TeamName", "")
            team_colour = "#" + driver_info.get("TeamColour", "888888")
            pos_str = line.get("Position", "0")
            try:
                pos = int(pos_str)
            except (ValueError, TypeError):
                continue
            if pos == 0:
                continue
            positions.append(DriverPosition(
                position=pos,
                code=driver_info.get("Tla", str(racing_number)),
                full_name=driver_info.get("FullName", "").title(),
                team=team,
                team_color=TEAM_COLORS.get(team, team_colour),
                gap=line.get("GapToLeader", line.get("IntervalToPositionAhead", {}).get("Value", "")),
                tyre="",
                pit_count=int(line.get("NumberOfPitStops", 0)),
            ))
        if positions:
            positions.sort(key=lambda p: p.position)
            self._state.set_driver_positions(positions)

    def _handle_feed(self, topic: str, data: dict) -> None:
        logger.debug("Feed received: %s", topic)
        self._ensure_active()

        if topic == "TrackStatus":
            status = parse_track_status(data)
            self._state.set_track_status(status)
            if self._on_track_status_change:
                self._on_track_status_change(status)
        elif topic == "SessionInfo":
            self._state.set_session(parse_session_info(data))
        elif topic == "WeatherData":
            self._state.set_weather(parse_weather(data))
        elif topic == "RaceControlMessages":
            messages = data.get("Messages", {})
            items = messages.items() if isinstance(messages, dict) else []
            for _, msg_data in items:
                if isinstance(msg_data, dict):
                    self._state.add_race_control_message(parse_race_control(msg_data))
        elif topic == "LapCount":
            session = self._state.session
            self._state.set_session(SessionInfo(
                name=session.name,
                circuit=session.circuit,
                venue=session.venue,
                country_flag=session.country_flag,
                session_type=session.session_type,
                is_active=session.is_active,
                current_lap=int(data.get("CurrentLap", session.current_lap)),
                total_laps=int(data.get("TotalLaps", session.total_laps)),
                time_remaining=session.time_remaining,
            ))
        elif topic == "ExtrapolatedClock":
            session = self._state.session
            self._state.set_session(SessionInfo(
                name=session.name,
                circuit=session.circuit,
                venue=session.venue,
                country_flag=session.country_flag,
                session_type=session.session_type,
                is_active=session.is_active,
                current_lap=session.current_lap,
                total_laps=session.total_laps,
                time_remaining=str(data.get("Remaining", "")),
            ))
        elif topic == "DriverList":
            self._driver_list = data
        elif topic == "TimingData":
            self._update_driver_positions(data)

    async def _negotiate(self) -> tuple[str, str]:
        """Returns (connection_token, cookie) from the SignalR negotiate endpoint."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SIGNALR_BASE}/negotiate",
                params={"connectionData": CONNECTION_DATA, "clientProtocol": "1.5"},
                headers={"User-Agent": "BestHTTP", "Accept-Encoding": "gzip,identity"},
            )
            resp.raise_for_status()
            token = resp.json()["ConnectionToken"]
            cookie = resp.headers.get("set-cookie", "")
            return token, cookie

    async def start(self) -> None:
        self._running = True
        while self._running:
            try:
                token, cookie = await self._negotiate()
                ws_url = (
                    f"{WS_BASE}/connect"
                    f"?transport=webSockets&clientProtocol=1.5"
                    f"&connectionToken={quote(token, safe='')}"
                    f"&connectionData={quote(CONNECTION_DATA, safe='')}&tid=10"
                )
                ws_headers = {"User-Agent": "BestHTTP", "Accept-Encoding": "gzip,identity"}
                if cookie:
                    ws_headers["Cookie"] = cookie
                logger.info("Connecting to F1 live timing feed...")
                async with websockets.connect(ws_url, extra_headers=ws_headers) as ws:
                    logger.info("Connected — subscribing to timing topics")
                    subscribe = json.dumps({
                        "H": "Streaming", "M": "Subscribe",
                        "A": [TOPICS], "I": 1,
                    })
                    await ws.send(subscribe)
                    async for raw in ws:
                        if not self._running:
                            break
                        self._process_message(raw)
            except Exception as e:
                logger.warning("SignalR connection lost: %s — reconnecting in 5s", e)
                if self._running:
                    await asyncio.sleep(5)

    def _process_message(self, raw: str) -> None:
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError:
            return
        for msg in envelope.get("M", []):
            if msg.get("M") == "feed" and len(msg.get("A", [])) >= 2:
                self._handle_feed(msg["A"][0], msg["A"][1])

    async def stop(self) -> None:
        self._running = False
