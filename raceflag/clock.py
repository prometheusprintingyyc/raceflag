"""Local time for the LED schedule, and clock/timezone sync from the browser.

The system timezone is never changed: overlayroot would discard it at reboot. RaceFlag
stores the browser's IANA timezone in config.json and computes local time itself.
"""
from __future__ import annotations

import logging
import subprocess
import time
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from raceflag.config import Config, save as save_config

logger = logging.getLogger(__name__)

CLOCK_TOLERANCE_S = 60
MIN_EPOCH_MS = 1_735_689_600_000  # 2025-01-01T00:00:00Z — older browser clocks are wrong


def _zone(tz_name: str) -> tzinfo | None:
    """Return the ZoneInfo for tz_name, or None if it is empty or unknown."""
    if not tz_name:
        return None
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def local_minutes(tz_name: str, now: datetime | None = None) -> int:
    """Minutes since local midnight in tz_name. UTC when tz_name is empty or unknown."""
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(_zone(tz_name) or timezone.utc)
    return local.hour * 60 + local.minute


def _set_system_clock(epoch_s: float) -> None:
    subprocess.run(
        ["date", "-u", "-s", f"@{int(epoch_s)}"],
        check=False, capture_output=True, timeout=5,
    )


def sync(
    tz_name: str,
    epoch_ms: int,
    cfg: Config,
    cfg_path: Path | None,
    set_clock: Callable[[float], None] | None = None,
    now: Callable[[], float] = time.time,
) -> dict:
    """Store the browser's timezone and correct the clock if it is more than 60 s off.

    Raises ValueError for an unknown timezone or an implausibly old epoch.
    """
    if _zone(tz_name) is None:
        raise ValueError(f"Unknown timezone: {tz_name!r}")
    if epoch_ms < MIN_EPOCH_MS:
        raise ValueError("epoch_ms is before 2025-01-01; the browser clock looks wrong")

    if tz_name != cfg.timezone:
        cfg.timezone = tz_name
        if cfg_path:
            save_config(cfg, cfg_path)
        logger.info("Timezone set to %s", tz_name)

    epoch_s = epoch_ms / 1000
    adjusted = abs(now() - epoch_s) > CLOCK_TOLERANCE_S
    if adjusted:
        logger.info("System clock off by %.0f s — setting from browser", now() - epoch_s)
        (set_clock or _set_system_clock)(epoch_s)
    return {"timezone": tz_name, "clock_adjusted": adjusted}
