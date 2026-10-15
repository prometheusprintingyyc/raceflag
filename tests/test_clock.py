import json
import logging
import subprocess
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from raceflag import clock
from raceflag.config import Config

NOW_S = 1_791_996_000.0  # 2026-10-14, after MIN_EPOCH_MS


def test_local_minutes_utc_when_timezone_empty():
    now = datetime(2026, 1, 15, 18, 30, tzinfo=timezone.utc)
    assert clock.local_minutes("", now) == 18 * 60 + 30


def test_local_minutes_converts_to_timezone():
    now = datetime(2026, 1, 15, 18, 30, tzinfo=timezone.utc)
    # Edmonton is UTC-7 in January
    assert clock.local_minutes("America/Edmonton", now) == 11 * 60 + 30


def test_local_minutes_follows_dst():
    winter = datetime(2026, 1, 15, 18, 0, tzinfo=timezone.utc)
    summer = datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc)
    assert clock.local_minutes("America/Edmonton", winter) == 11 * 60  # MST, UTC-7
    assert clock.local_minutes("America/Edmonton", summer) == 12 * 60  # MDT, UTC-6


def test_local_minutes_unknown_timezone_falls_back_to_utc():
    now = datetime(2026, 1, 15, 18, 30, tzinfo=timezone.utc)
    assert clock.local_minutes("Mars/Olympus_Mons", now) == 18 * 60 + 30


def test_sync_saves_timezone_when_changed(tmp_path):
    cfg = Config()
    path = tmp_path / "config.json"
    calls = []
    result = clock.sync("America/Edmonton", int(NOW_S * 1000), cfg, path,
                        set_clock=calls.append, now=lambda: NOW_S)
    assert result == {"timezone": "America/Edmonton", "clock_adjusted": False}
    assert cfg.timezone == "America/Edmonton"
    assert json.loads(path.read_text())["timezone"] == "America/Edmonton"
    assert calls == []


def test_sync_does_not_write_config_when_timezone_unchanged(tmp_path):
    cfg = Config(timezone="America/Edmonton")
    path = tmp_path / "config.json"
    clock.sync("America/Edmonton", int(NOW_S * 1000), cfg, path,
               set_clock=lambda s: None, now=lambda: NOW_S)
    assert not path.exists()


def test_sync_leaves_clock_alone_within_tolerance():
    calls = []
    result = clock.sync("UTC", int((NOW_S + 59) * 1000), Config(), None,
                        set_clock=calls.append, now=lambda: NOW_S)
    assert result["clock_adjusted"] is False
    assert calls == []


def test_sync_sets_clock_when_more_than_60s_off():
    calls = []
    result = clock.sync("UTC", int((NOW_S + 61) * 1000), Config(), None,
                        set_clock=calls.append, now=lambda: NOW_S)
    assert result["clock_adjusted"] is True
    assert calls == [pytest.approx(NOW_S + 61)]


def test_sync_sets_clock_when_pi_is_ahead():
    calls = []
    clock.sync("UTC", int((NOW_S - 600) * 1000), Config(), None,
               set_clock=calls.append, now=lambda: NOW_S)
    assert calls == [pytest.approx(NOW_S - 600)]


def test_sync_rejects_unknown_timezone(tmp_path):
    cfg = Config()
    calls = []
    with pytest.raises(ValueError):
        clock.sync("Mars/Olympus_Mons", int((NOW_S + 3600) * 1000), cfg, tmp_path / "c.json",
                   set_clock=calls.append, now=lambda: NOW_S)
    assert cfg.timezone == ""
    assert calls == []


def test_sync_rejects_implausible_epoch():
    cfg = Config()
    calls = []
    with pytest.raises(ValueError):
        clock.sync("America/Edmonton", 1_000_000_000_000, cfg, None,  # 2001
                   set_clock=calls.append, now=lambda: NOW_S)
    assert cfg.timezone == ""
    assert calls == []


def test_sync_uses_module_setter_by_default(mocker):
    setter = mocker.patch("raceflag.clock._set_system_clock")
    clock.sync("UTC", int((NOW_S + 3600) * 1000), Config(), None, now=lambda: NOW_S)
    setter.assert_called_once()


def test_non_string_timezone_falls_back_to_utc():
    now = datetime(2026, 1, 15, 18, 30, tzinfo=timezone.utc)
    assert clock.local_minutes(5, now) == 18 * 60 + 30
    with pytest.raises(ValueError):
        clock.sync(5, 1_791_996_000_000, Config(), None)


def test_set_system_clock_logs_nonzero_exit(monkeypatch, caplog):
    monkeypatch.setattr(
        clock.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stderr=b"date: cannot set date  "),
    )
    with caplog.at_level(logging.WARNING, logger="raceflag.clock"):
        clock._set_system_clock(NOW_S)
    assert "Setting the system clock failed (exit 1): date: cannot set date" in caplog.text


@pytest.mark.parametrize("exc", [FileNotFoundError("date"), subprocess.TimeoutExpired("date", 5)])
def test_set_system_clock_logs_exceptions(monkeypatch, caplog, exc):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(clock.subprocess, "run", boom)
    with caplog.at_level(logging.WARNING, logger="raceflag.clock"):
        clock._set_system_clock(NOW_S)
    assert "Setting the system clock failed" in caplog.text
