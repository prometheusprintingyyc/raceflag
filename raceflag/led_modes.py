"""LED mode rules (feature 014). Pure functions — no I/O, no clocks."""
from __future__ import annotations

import re

MODES: tuple[str, ...] = ("on", "auto", "lamp", "off")

DEFAULT_SCHEDULE: tuple[int, int] = (8 * 60, 23 * 60)
DEFAULT_LAMP: tuple[str, int] = ("#FFFFFF", 100)

_HHMM = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")
_HEX = re.compile(r"#[0-9A-Fa-f]{6}")


def in_window(now_min: int, on_min: int, off_min: int) -> bool:
    """True if now_min is inside the half-open window [on, off). off < on crosses midnight."""
    if on_min < off_min:
        return on_min <= now_min < off_min
    return now_min >= on_min or now_min < off_min


def decide_output(mode: str, session_live: bool, effect_active: bool,
                  scheduled_on: bool, preview_active: bool) -> str:
    """What the strip shows this tick.

    "normal" = existing flag / timed-effect / idle-chase logic, "lamp_preview" = draft lamp
    colour, "lamp" = saved lamp colour, "dark" = all off. Hotspot is handled by the caller.
    """
    if mode == "off":
        return "dark"
    if session_live or effect_active:
        return "normal"
    if preview_active and mode == "lamp":
        return "lamp_preview"
    if mode == "on":
        return "normal"
    if mode == "auto":
        return "normal" if scheduled_on else "dark"
    return "lamp"


def session_live(is_active: bool, replay_mode: bool, track_status: str) -> bool:
    """A session or replay is running. A finished/break status wins over is_active,
    which the feed re-sets on every message (e.g. Heartbeat) after a session ends."""
    return replay_mode or (is_active and track_status not in ("finished", "break"))


def parse_hhmm(value) -> int:
    """"21:30" -> 1290. Raises ValueError unless value is a 24 h HH:MM string."""
    m = _HHMM.fullmatch(value) if isinstance(value, str) else None
    if not m:
        raise ValueError(f"Invalid time {value!r}; expected HH:MM (24 h)")
    return int(m.group(1)) * 60 + int(m.group(2))


def is_hex_color(value) -> bool:
    return isinstance(value, str) and _HEX.fullmatch(value) is not None


def scale_rgb(hex_color: str, brightness: int) -> tuple[int, int, int]:
    """Scale #RRGGBB by brightness 0–100, rounding half up."""
    h = hex_color.lstrip("#")
    return tuple((int(h[i:i + 2], 16) * brightness + 50) // 100 for i in (0, 2, 4))


def schedule_from_config(on, off) -> tuple[int, int]:
    """Stored schedule as minutes; DEFAULT_SCHEDULE if either time is invalid or they match."""
    try:
        on_min, off_min = parse_hhmm(on), parse_hhmm(off)
    except ValueError:
        return DEFAULT_SCHEDULE
    return DEFAULT_SCHEDULE if on_min == off_min else (on_min, off_min)


def lamp_from_config(color, brightness) -> tuple[str, int]:
    """Stored lamp settings, each field falling back to its default if invalid."""
    color = color.upper() if is_hex_color(color) else DEFAULT_LAMP[0]
    valid = isinstance(brightness, int) and not isinstance(brightness, bool) and 0 <= brightness <= 100
    return color, brightness if valid else DEFAULT_LAMP[1]
