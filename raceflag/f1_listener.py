from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime

import httpx
import websockets

from raceflag.state import (
    AppState, WeatherInfo, RaceControlMessage,
    TRACK_STATUS_MAP, FLAG_COLORS,
)

logger = logging.getLogger(__name__)

SIGNALR_BASE = "https://livetiming.formula1.com/signalr"
WS_BASE = "wss://livetiming.formula1.com/signalr"
CONNECTION_DATA = '[{"name":"streaming"}]'
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

    def _handle_feed(self, topic: str, data: dict) -> None:
        if topic == "TrackStatus":
            status = parse_track_status(data)
            self._state.set_track_status(status)
            if self._on_track_status_change:
                self._on_track_status_change(status)
        elif topic == "WeatherData":
            self._state.set_weather(parse_weather(data))
        elif topic == "RaceControlMessages":
            messages = data.get("Messages", {})
            items = messages.items() if isinstance(messages, dict) else []
            for _, msg_data in items:
                if isinstance(msg_data, dict):
                    self._state.add_race_control_message(parse_race_control(msg_data))

    async def _negotiate(self) -> str:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SIGNALR_BASE}/negotiate",
                params={"connectionData": CONNECTION_DATA, "clientProtocol": "1.5"},
                headers={"User-Agent": "BestHTTP", "Accept-Encoding": "gzip, identity"},
            )
            resp.raise_for_status()
            return resp.json()["ConnectionToken"]

    async def start(self) -> None:
        self._running = True
        while self._running:
            try:
                token = await self._negotiate()
                ws_url = (
                    f"{WS_BASE}/connect"
                    f"?transport=webSockets&clientProtocol=1.5"
                    f"&connectionToken={token}"
                    f"&connectionData={CONNECTION_DATA}&tid=10"
                )
                async with websockets.connect(
                    ws_url,
                    extra_headers={"User-Agent": "BestHTTP"},
                ) as ws:
                    subscribe = json.dumps({
                        "H": "streaming", "M": "Subscribe",
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
