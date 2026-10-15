import pytest

from raceflag.led_modes import (
    DEFAULT_LAMP, DEFAULT_SCHEDULE, MODES,
    decide_output, in_window, is_hex_color, lamp_from_config,
    parse_hhmm, scale_rgb, schedule_from_config, session_live,
)


def test_modes():
    assert MODES == ("on", "auto", "lamp", "off")


@pytest.mark.parametrize("mode,session,effect,sched,preview,expected", [
    ("off", True, True, True, True, "dark"),
    ("off", False, False, True, False, "dark"),
    ("on", False, False, True, False, "normal"),
    ("on", True, False, True, False, "normal"),
    ("auto", False, False, True, False, "normal"),
    ("auto", False, False, False, False, "dark"),
    ("auto", True, False, False, False, "normal"),    # session lights the off window
    ("auto", False, True, False, False, "normal"),    # test effect / timed effect plays out
    ("lamp", False, False, True, False, "lamp"),
    ("lamp", True, False, True, False, "normal"),
    ("lamp", False, True, True, False, "normal"),
    ("lamp", False, False, True, True, "lamp_preview"),
    ("lamp", True, False, True, True, "normal"),      # session beats preview
    ("on", False, False, True, True, "normal"),       # preview only in Lamp mode
    ("auto", False, False, False, True, "dark"),
])
def test_decide_output(mode, session, effect, sched, preview, expected):
    assert decide_output(mode, session, effect, sched, preview) == expected


@pytest.mark.parametrize("now,on,off,expected", [
    (480, 480, 1380, True),    # on time itself is lit
    (1379, 480, 1380, True),
    (1380, 480, 1380, False),  # off time itself is dark
    (479, 480, 1380, False),
    (1080, 1080, 60, True),    # 18:00 → 01:00 crosses midnight
    (0, 1080, 60, True),
    (59, 1080, 60, True),
    (60, 1080, 60, False),
    (1079, 1080, 60, False),
])
def test_in_window(now, on, off, expected):
    assert in_window(now, on, off) is expected


@pytest.mark.parametrize("value,expected", [("00:00", 0), ("08:30", 510), ("23:59", 1439)])
def test_parse_hhmm_valid(value, expected):
    assert parse_hhmm(value) == expected


@pytest.mark.parametrize("value", [
    "8:30", "24:00", "12:60", "", "08:30:00", " 08:30", "08:30\n", "abc", None, 830,
])
def test_parse_hhmm_invalid(value):
    with pytest.raises(ValueError):
        parse_hhmm(value)


@pytest.mark.parametrize("value,expected", [
    ("#FF00AA", True), ("#ff00aa", True), ("FF00AA", False), ("#FFF", False),
    ("#GG0000", False), ("", False), (None, False), (0xFF0000, False),
])
def test_is_hex_color(value, expected):
    assert is_hex_color(value) is expected


@pytest.mark.parametrize("hex_color,brightness,expected", [
    ("#FFFFFF", 100, (255, 255, 255)),
    ("#FFFFFF", 0, (0, 0, 0)),
    ("#FFD700", 50, (128, 108, 0)),   # 127.5 → 128, 107.5 → 108 (half up)
    ("#ff0000", 1, (3, 0, 0)),
])
def test_scale_rgb(hex_color, brightness, expected):
    assert scale_rgb(hex_color, brightness) == expected


def test_schedule_from_config_valid():
    assert schedule_from_config("18:00", "01:00") == (1080, 60)


@pytest.mark.parametrize("on,off", [("bogus", "23:00"), ("09:00", "09:00"), (None, "23:00"), ("08:00", 2300)])
def test_schedule_from_config_falls_back_to_default(on, off):
    assert schedule_from_config(on, off) == DEFAULT_SCHEDULE == (480, 1380)


def test_lamp_from_config_valid_normalises_case():
    assert lamp_from_config("#00ff00", 40) == ("#00FF00", 40)


@pytest.mark.parametrize("color,brightness,expected", [
    ("green", 40, ("#FFFFFF", 40)),
    ("#00FF00", 300, ("#00FF00", 100)),
    ("#00FF00", -1, ("#00FF00", 100)),
    ("#00FF00", "50", ("#00FF00", 100)),
    ("#00FF00", True, ("#00FF00", 100)),
    (None, None, DEFAULT_LAMP),
])
def test_lamp_from_config_falls_back_per_field(color, brightness, expected):
    assert lamp_from_config(color, brightness) == expected


@pytest.mark.parametrize("active,replay,status,expected", [
    (False, False, "unknown", False),
    (True, False, "track_clear", True),
    (True, False, "finished", False),
    (True, False, "break", False),
    (False, True, "finished", True),
    (True, False, "unknown", True),
])
def test_session_live(active, replay, status, expected):
    assert session_live(active, replay, status) is expected
