from __future__ import annotations

from dataclasses import dataclass, field, asdict, replace
from datetime import datetime, timezone
from threading import Lock
from typing import List, Optional


TEAM_COLORS: dict[str, str] = {
    "Red Bull Racing": "#3671C6",
    "Ferrari": "#E8002D",
    "McLaren": "#FF8000",
    "Mercedes": "#27F4D2",
    "Aston Martin": "#358C75",
    "Alpine": "#FF87BC",
    "Williams": "#64C4FF",
    "RB": "#6692FF",
    "Kick Sauber": "#52E252",
    "Haas": "#B6BABD",
}

COUNTRY_FLAGS: dict[str, str] = {
    # Full country names
    "Monaco": "🇲🇨", "Italy": "🇮🇹", "United Kingdom": "🇬🇧",
    "Spain": "🇪🇸", "Bahrain": "🇧🇭", "Saudi Arabia": "🇸🇦",
    "Australia": "🇦🇺", "Japan": "🇯🇵", "China": "🇨🇳",
    "United States": "🇺🇸", "Canada": "🇨🇦", "Austria": "🇦🇹",
    "Hungary": "🇭🇺", "Belgium": "🇧🇪", "Netherlands": "🇳🇱",
    "Singapore": "🇸🇬", "Mexico": "🇲🇽", "Brazil": "🇧🇷",
    "Abu Dhabi": "🇦🇪", "Azerbaijan": "🇦🇿", "Qatar": "🇶🇦",
    # Ergast/Jolpica API aliases
    "UK": "🇬🇧", "USA": "🇺🇸", "UAE": "🇦🇪",
}

FLAG_COLORS: dict[str, str] = {
    "track_clear": "#00C853",
    "yellow_flag": "#FFD600",
    "safety_car": "#FFD600",
    "virtual_sc": "#FFD600",
    "red_flag": "#FF1744",
    "checkered": "#FFFFFF",
    "break": "#444444",
    "finished": "#444444",
    "unknown": "#444444",
}

TRACK_STATUS_MAP: dict[str, str] = {
    "1": "track_clear",
    "2": "yellow_flag",
    "4": "safety_car",
    "5": "red_flag",
    "6": "virtual_sc",
    "7": "virtual_sc",
}


@dataclass
class DriverPosition:
    position: int = 0
    code: str = ""
    full_name: str = ""
    team: str = ""
    team_color: str = "#888888"
    gap: str = ""
    last_lap_time: str = ""
    tyre: str = ""
    pit_count: int = 0


@dataclass
class RaceControlMessage:
    time: str = ""
    message: str = ""
    flag_color: Optional[str] = None


@dataclass
class SessionInfo:
    name: str = ""
    circuit: str = ""
    venue: str = ""
    country_flag: str = ""
    session_type: str = ""
    is_active: bool = False
    current_lap: int = 0
    total_laps: int = 0
    time_remaining: str = ""
    time_remaining_at: str = ""
    extrapolating: bool = False


@dataclass
class WeatherInfo:
    air_temp: float = 0.0
    track_temp: float = 0.0
    humidity: float = 0.0
    wind_speed: float = 0.0
    wind_direction: str = ""
    rain: bool = False


@dataclass
class DriverStanding:
    position: int = 0
    full_name: str = ""
    team: str = ""
    team_color: str = "#888888"
    points: int = 0


@dataclass
class ConstructorStanding:
    position: int = 0
    name: str = ""
    team_color: str = "#888888"
    points: int = 0


@dataclass
class NextRace:
    name: str = ""
    circuit: str = ""
    venue: str = ""
    country_flag: str = ""
    round_number: int = 0
    race_date: str = ""
    race_datetime_utc: str = ""


@dataclass
class AppState:
    track_status: str = "unknown"
    display_track_status: str = "unknown"
    demo_mode: bool = False
    led_enabled: bool = True
    feed_connected: bool = False
    replay_mode: bool = False
    replay_status: str = "idle"
    replay_session_name: str = ""
    replay_time_elapsed: str = ""
    session: SessionInfo = field(default_factory=SessionInfo)
    weather: WeatherInfo = field(default_factory=WeatherInfo)
    race_control_messages: List[RaceControlMessage] = field(default_factory=list)
    driver_positions: List[DriverPosition] = field(default_factory=list)
    driver_standings: List[DriverStanding] = field(default_factory=list)
    constructor_standings: List[ConstructorStanding] = field(default_factory=list)
    next_race: NextRace = field(default_factory=NextRace)
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def set_track_status(self, status: str) -> None:
        with self._lock:
            self.track_status = status

    def set_display_track_status(self, status: str) -> None:
        with self._lock:
            self.display_track_status = status

    def set_demo_mode(self, enabled: bool) -> None:
        with self._lock:
            self.demo_mode = enabled

    def set_led_enabled(self, enabled: bool) -> None:
        with self._lock:
            self.led_enabled = enabled

    def set_feed_connected(self, connected: bool) -> None:
        with self._lock:
            self.feed_connected = connected

    def set_replay_state(
        self,
        mode: bool,
        status: str,
        session_name: str = "",
        elapsed: str = "",
    ) -> None:
        with self._lock:
            self.replay_mode = mode
            self.replay_status = status
            self.replay_session_name = session_name
            self.replay_time_elapsed = elapsed

    def set_session(self, session: SessionInfo) -> None:
        with self._lock:
            self.session = session

    def set_weather(self, weather: WeatherInfo) -> None:
        with self._lock:
            self.weather = weather

    def add_race_control_message(self, msg: RaceControlMessage) -> None:
        with self._lock:
            self.race_control_messages.insert(0, msg)
            self.race_control_messages = self.race_control_messages[:50]

    def set_driver_positions(self, positions: List[DriverPosition]) -> None:
        with self._lock:
            self.driver_positions = positions

    def set_standings(
        self,
        drivers: List[DriverStanding],
        constructors: List[ConstructorStanding],
    ) -> None:
        with self._lock:
            self.driver_standings = drivers
            self.constructor_standings = constructors

    def set_next_race(self, race: NextRace) -> None:
        with self._lock:
            self.next_race = race

    def freeze_countdown(self) -> None:
        """Compute current remaining time and freeze extrapolation for replay pause."""
        with self._lock:
            if not self.session.time_remaining or not self.session.time_remaining_at:
                return
            try:
                parts = self.session.time_remaining.split(":")
                remaining_secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                at_time = datetime.fromisoformat(
                    self.session.time_remaining_at.replace("Z", "+00:00")
                )
                elapsed = (datetime.now(timezone.utc) - at_time).total_seconds()
                frozen_secs = max(0.0, remaining_secs - elapsed)
                frozen_str = "{:02d}:{:02d}:{:02d}".format(
                    int(frozen_secs // 3600),
                    int((frozen_secs % 3600) // 60),
                    int(frozen_secs % 60),
                )
                now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                self.session = replace(
                    self.session,
                    time_remaining=frozen_str,
                    time_remaining_at=now_utc,
                    extrapolating=False,
                )
            except Exception:
                pass

    def unfreeze_countdown(self, extrapolating: bool = True) -> None:
        """Restart the frontend countdown from the frozen remaining time."""
        with self._lock:
            if not self.session.time_remaining:
                return
            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            self.session = replace(
                self.session,
                time_remaining_at=now_utc,
                extrapolating=extrapolating,
            )

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "track_status": self.display_track_status,
                "flag_color": FLAG_COLORS.get(self.display_track_status, FLAG_COLORS["unknown"]),
                "demo_mode": self.demo_mode,
                "led_enabled": self.led_enabled,
                "feed_connected": self.feed_connected,
                "replay_mode": self.replay_mode,
                "replay_status": self.replay_status,
                "replay_session_name": self.replay_session_name,
                "replay_time_elapsed": self.replay_time_elapsed,
                "session": asdict(self.session),
                "weather": asdict(self.weather),
                "race_control_messages": [asdict(m) for m in self.race_control_messages],
                "driver_positions": [asdict(p) for p in self.driver_positions],
                "driver_standings": [asdict(s) for s in self.driver_standings],
                "constructor_standings": [asdict(s) for s in self.constructor_standings],
                "next_race": asdict(self.next_race),
            }
