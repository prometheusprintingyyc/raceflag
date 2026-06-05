import json
from pathlib import Path
import pytest
from raceflag import config


def test_load_returns_defaults_when_file_missing(tmp_path):
    cfg = config.load(tmp_path / "config.json")
    assert cfg.led_count == 60
    assert cfg.led_gpio_pin == 18
    assert cfg.led_brightness == 128
    assert cfg.delay_seconds == 0.0
    assert cfg.wifi_ssid == ""
    assert cfg.wifi_password == ""


def test_load_reads_values_from_file(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"led_count": 120, "delay_seconds": 15.0}))
    cfg = config.load(p)
    assert cfg.led_count == 120
    assert cfg.delay_seconds == 15.0
    assert cfg.led_gpio_pin == 18  # default preserved


def test_load_ignores_unknown_keys(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"led_count": 30, "unknown_key": "ignored"}))
    cfg = config.load(p)
    assert cfg.led_count == 30


def test_save_writes_all_fields(tmp_path):
    p = tmp_path / "config.json"
    cfg = config.Config(led_count=90, delay_seconds=5.0, wifi_ssid="MyNet")
    config.save(cfg, p)
    data = json.loads(p.read_text())
    assert data["led_count"] == 90
    assert data["delay_seconds"] == 5.0
    assert data["wifi_ssid"] == "MyNet"


def test_save_load_roundtrip(tmp_path):
    p = tmp_path / "config.json"
    original = config.Config(led_count=45, delay_seconds=7.5, wifi_ssid="Net", wifi_password="pw")
    config.save(original, p)
    loaded = config.load(p)
    assert loaded == original
