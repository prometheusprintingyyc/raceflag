from dataclasses import dataclass, asdict
import json
from pathlib import Path

DEFAULT_PATH = Path("/opt/raceflag/config.json")


@dataclass
class Config:
    led_count: int = 60
    led_gpio_pin: int = 18
    led_brightness: int = 128
    delay_seconds: float = 0.0
    wifi_ssid: str = ""
    wifi_password: str = ""


def load(path: Path = DEFAULT_PATH) -> Config:
    if not path.exists():
        return Config()
    data = json.loads(path.read_text())
    known = {f for f in Config.__dataclass_fields__}
    return Config(**{k: v for k, v in data.items() if k in known})


def save(cfg: Config, path: Path = DEFAULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), indent=2))
