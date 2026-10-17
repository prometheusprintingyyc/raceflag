from dataclasses import dataclass, asdict
import json
from pathlib import Path

DEFAULT_PATH = Path("/boot/firmware/raceflag/config.json")


@dataclass
class Config:
    led_count: int = 21
    led_gpio_pin: int = 18
    led_brightness: int = 128
    delay_seconds: float = 0.0
    wifi_ssid: str = ""
    wifi_password: str = ""
    schedule_on: str = "08:00"
    schedule_off: str = "23:00"
    lamp_color: str = "#FFFFFF"
    lamp_brightness: int = 100
    lamp_use_schedule: bool = False
    timezone: str = ""
    f1_token: str = ""  # F1 login token for the replay archive; never sent to the page


# install.sh used to write led_count 60, left over from before the 21-LED layout.
# Every RaceFlag box has 21 LEDs, so that value is always wrong.
_OLD_INSTALLER_LED_COUNT = 60


def fix_led_count(cfg: Config) -> bool:
    """Replace the old installer's led_count with 21. Returns True if it changed."""
    if cfg.led_count != _OLD_INSTALLER_LED_COUNT:
        return False
    cfg.led_count = Config.led_count
    return True


def load(path: Path = DEFAULT_PATH) -> Config:
    if not path.exists():
        return Config()
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed config file: {path}") from exc
    known = set(Config.__dataclass_fields__)
    return Config(**{k: v for k, v in data.items() if k in known})


def save(cfg: Config, path: Path = DEFAULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), indent=2))
