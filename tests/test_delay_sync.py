import pytest

from raceflag.delay_sync import (
    NO_RACE, NOT_STARTED, TOO_LATE, SYNC_WINDOW_S, SyncError, is_live_race, sync_delay,
)


def test_messages():
    assert NO_RACE == "Sync needs a live race or sprint"
    assert NOT_STARTED == "The race hasn't started yet"
    assert TOO_LATE == "Sync works within 90 s of the race start"
    assert SYNC_WINDOW_S == 90.0


@pytest.mark.parametrize("session_type,active,replay,expected", [
    ("Race", True, False, True),
    ("Sprint", True, False, True),
    ("race", True, False, True),
    ("Qualifying", True, False, False),
    ("Practice 1", True, False, False),
    ("Race", False, False, False),
    ("Race", True, True, False),
    ("", True, False, False),
])
def test_is_live_race(session_type, active, replay, expected):
    assert is_live_race(session_type, active, replay) is expected


def test_delay_is_time_since_lights_out_rounded():
    assert sync_delay(now=1034.24, lights_out_at=1000.0, race_live=True) == 34.2


def test_rounds_half_up_to_tenth():
    assert sync_delay(now=1012.36, lights_out_at=1000.0, race_live=True) == 12.4


def test_exactly_90_s_is_allowed():
    assert sync_delay(now=1090.0, lights_out_at=1000.0, race_live=True) == 90.0


def test_just_over_90_s_is_refused():
    with pytest.raises(SyncError) as exc:
        sync_delay(now=1090.05, lights_out_at=1000.0, race_live=True)
    assert str(exc.value) == TOO_LATE


def test_no_race_live_is_refused_first():
    with pytest.raises(SyncError) as exc:
        sync_delay(now=1010.0, lights_out_at=1000.0, race_live=False)
    assert str(exc.value) == NO_RACE


def test_no_lights_out_yet_is_refused():
    with pytest.raises(SyncError) as exc:
        sync_delay(now=1010.0, lights_out_at=None, race_live=True)
    assert str(exc.value) == NOT_STARTED


def test_press_at_lights_out_gives_zero():
    assert sync_delay(now=1000.0, lights_out_at=1000.0, race_live=True) == 0.0


def test_clock_before_lights_out_clamps_to_zero():
    assert sync_delay(now=999.5, lights_out_at=1000.0, race_live=True) == 0.0
