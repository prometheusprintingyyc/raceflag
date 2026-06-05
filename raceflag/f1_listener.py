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

# SignalR Core endpoint — works without auth for public streams (f1_sensor SIGNALR_USE_CORE=True)
SIGNALR_NEGOTIATE = "https://livetiming.formula1.com/signalrcore/negotiate"
SIGNALR_WS = "wss://livetiming.formula1.com/signalrcore"
RECORD_SEP = "\x1e"

# Kept for test compatibility; not used in the Core connect flow
CONNECTION_DATA = '[{"name":"Streaming"}]'

TOPICS = [
    "TrackStatus", "RaceControlMessages", "SessionInfo", "SessionStatus",
    "LapCount", "ExtrapolatedClock", "WeatherData", "TimingData",
    "TimingAppData", "DriverList", "Heartbeat", "SessionData",
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

        # Session-ending status must be handled before _ensure_active to avoid
        # briefly marking the session active only to immediately clear it
        if topic == "SessionStatus":
            status_msg = str(data.get("Status") or data.get("Message") or "").strip()
            if status_msg in ("Finished", "Finalised", "Ends"):
                session = self._state.session
                self._state.set_session(SessionInfo(
                    name=session.name,
                    circuit=session.circuit,
                    venue=session.venue,
                    country_flag=session.country_flag,
                    session_type=session.session_type,
                    is_active=False,
                    current_lap=session.current_lap,
                    total_laps=session.total_laps,
                    time_remaining=session.time_remaining,
                ))
                self._state.set_track_status("unknown")
                logger.info("Session ended: %s", status_msg)
                return

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
        """Returns (connection_token, cookie) using SignalR Core negotiate.

        Step 1: OPTIONS to obtain the AWS load-balancer (AWSALBCORS) cookie.
        Step 2: POST to obtain the connectionToken, forwarding the cookie.
        """
        cookie = ""
        async with httpx.AsyncClient() as client:
            try:
                opts = await client.options(
                    SIGNALR_NEGOTIATE,
                    params={"negotiateVersion": "1"},
                    headers={"User-Agent": "BestHTTP"},
                )
                for part in opts.headers.get("set-cookie", "").split(","):
                    part = part.strip()
                    if "AWSALBCORS=" in part:
                        cookie = "AWSALBCORS=" + part.split("AWSALBCORS=")[1].split(";")[0]
                        break
            except Exception:
                pass

            headers = {"User-Agent": "BestHTTP"}
            if cookie:
                headers["Cookie"] = cookie
            resp = await client.post(
                SIGNALR_NEGOTIATE,
                params={"negotiateVersion": "1"},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("connectionToken") or data.get("ConnectionToken", "")
            # Some responses set the cookie on the POST rather than OPTIONS
            if not cookie:
                for part in resp.headers.get("set-cookie", "").split(","):
                    part = part.strip()
                    if "AWSALBCORS=" in part:
                        cookie = "AWSALBCORS=" + part.split("AWSALBCORS=")[1].split(";")[0]
                        break
            return token, cookie

    def _process_message(self, raw: str) -> list[str]:
        """Process a Core protocol frame. Returns any response frames to send (e.g. pong)."""
        responses: list[str] = []
        for segment in raw.split(RECORD_SEP):
            segment = segment.strip()
            if not segment:
                continue
            try:
                payload = json.loads(segment)
            except json.JSONDecodeError:
                continue
            msg_type = payload.get("type")
            if msg_type == 1:
                # Invocation: server pushing feed data
                target = payload.get("target", "")
                arguments = payload.get("arguments", [])
                if target == "feed" and len(arguments) >= 2:
                    self._handle_feed(arguments[0], arguments[1])
            elif msg_type == 3:
                # Completion: initial state snapshot returned after Subscribe
                result = payload.get("result")
                if isinstance(result, dict):
                    for topic, data in result.items():
                        if isinstance(data, dict):
                            self._handle_feed(topic, data)
            elif msg_type == 6:
                # Ping: respond with pong to keep connection alive
                responses.append(json.dumps({"type": 6}) + RECORD_SEP)
            elif msg_type == 7:
                logger.warning("SignalR Core server closed: %s", payload.get("error", ""))
        return responses

    async def start(self) -> None:
        self._running = True
        while self._running:
            try:
                token, cookie = await self._negotiate()
                ws_url = f"{SIGNALR_WS}?id={quote(token, safe='')}"
                ws_headers = {"User-Agent": "BestHTTP"}
                if cookie:
                    ws_headers["Cookie"] = cookie
                logger.info("Connecting to F1 live timing feed...")
                async with websockets.connect(ws_url, extra_headers=ws_headers) as ws:
                    # Protocol handshake
                    await ws.send(json.dumps({"protocol": "json", "version": 1}) + RECORD_SEP)
                    hs_raw = await ws.recv()
                    for seg in hs_raw.split(RECORD_SEP):
                        seg = seg.strip()
                        if seg:
                            hs = json.loads(seg)
                            if "error" in hs:
                                raise ConnectionError(f"Handshake error: {hs['error']}")

                    # Subscribe to timing topics
                    await ws.send(json.dumps({
                        "type": 1,
                        "target": "Subscribe",
                        "arguments": [TOPICS],
                        "invocationId": "0",
                    }) + RECORD_SEP)
                    logger.info("Connected — subscribed to timing topics")

                    async for raw in ws:
                        if not self._running:
                            break
                        responses = self._process_message(raw)
                        for resp in responses:
                            await ws.send(resp)
            except Exception as e:
                logger.warning("SignalR connection lost: %s — reconnecting in 5s", e)
                if self._running:
                    await asyncio.sleep(5)

    async def stop(self) -> None:
        self._running = False
