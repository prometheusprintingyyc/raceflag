import asyncio
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from raceflag.flag_router import FlagRouter, TIMED_EFFECTS
from raceflag.led_controller import LEDController, MockStrip
from raceflag.state import AppState, SessionInfo


def _router(session_type="Race", delay=0.0, replay=False, active=True):
    state = AppState()
    state.set_session(SessionInfo(session_type=session_type, is_active=active))
    if replay:
        state.set_replay_state(True, "playing")
    led = MagicMock()
    config = SimpleNamespace(delay_seconds=delay)
    return FlagRouter(state=state, led=led, config=config), state, led


def test_live_race_start_shows_track_clear_and_plays_race_start():
    router, state, led = _router()
    router.on_flag_change("race_start")
    assert state.display_track_status == "track_clear"
    led.trigger_timed.assert_called_once_with("race_start", TIMED_EFFECTS["race_start"])


def test_live_first_allclear_is_not_promoted():
    router, _, _ = _router()
    assert router.resolve("track_clear") == ("track_clear", "track_clear")


def test_replay_first_allclear_is_promoted_once():
    router, _, _ = _router(replay=True)
    assert router.resolve("track_clear") == ("track_clear", "race_start")
    assert router.resolve("track_clear") == ("track_clear", "track_clear")


def test_replay_promotion_resets_after_session_end():
    router, _, _ = _router(replay=True)
    router.resolve("track_clear")
    router.resolve("finished")
    assert router.resolve("track_clear") == ("track_clear", "race_start")


def test_live_race_start_blocks_later_replay_style_promotion():
    router, _, _ = _router()
    assert router.resolve("race_start") == ("track_clear", "race_start")
    assert router.resolve("track_clear") == ("track_clear", "track_clear")


async def test_delayed_race_start_waits_for_delay():
    router, state, led = _router(delay=0.05)
    router.on_flag_change("race_start")
    led.trigger_timed.assert_not_called()
    await asyncio.sleep(0.12)
    assert state.display_track_status == "track_clear"
    led.trigger_timed.assert_called_once()
    assert led.trigger_timed.call_args.args[0] == "race_start"


async def test_sync_cancels_pending_race_start_and_plays_once():
    router, state, led = _router(delay=0.05)
    state.set_lights_out_at(1000.0)
    router.on_flag_change("race_start")
    router.sync(12.3)
    led.set_delay.assert_called_once_with(12.3)
    assert state.display_track_status == "track_clear"
    led.trigger_timed.assert_called_once_with(
        "race_start", TIMED_EFFECTS["race_start"], flush_before=1000.0)
    await asyncio.sleep(0.12)
    led.trigger_timed.assert_called_once()  # the cancelled original never fires


def test_sync_without_pending_race_start_plays_once():
    router, state, led = _router(delay=0.0)
    state.set_lights_out_at(1000.0)
    router.on_flag_change("race_start")  # delay 0: played immediately, nothing pending
    led.trigger_timed.reset_mock()
    router.sync(0.0)
    led.trigger_timed.assert_called_once_with(
        "race_start", TIMED_EFFECTS["race_start"], flush_before=1000.0)


def test_sync_without_lights_out_flushes_whole_queue():
    router, _, led = _router()
    router.sync(5.0)
    led.trigger_timed.assert_called_once_with(
        "race_start", TIMED_EFFECTS["race_start"], flush_before=None)


def test_sync_keeps_flags_that_arrived_after_lights_out(tmp_path):
    # Real LEDController (thread not started): a lap-1 yellow queued after lights-out
    # must survive Sync and fire on the new delay; a pre-lights-out item is flushed.
    state = AppState()
    state.set_session(SessionInfo(session_type="Race", is_active=True))
    led = LEDController(strip=MockStrip(10), effects_path=tmp_path / "missing.json",
                        delay_seconds=30.0)
    router = FlagRouter(state=state, led=led, config=SimpleNamespace(delay_seconds=30.0))
    lights_out = time.monotonic() - 10.0
    state.set_lights_out_at(lights_out)
    led._queue.put(("safety_car", lights_out - 5.0))  # pre-race, predates lights-out
    led.trigger("yellow_flag")                         # arrives after lights-out

    router.sync(5.0)

    queued = []
    while not led._queue.empty():
        queued.append(led._queue.get_nowait()[0])
    assert queued == ["yellow_flag"]


async def test_second_sync_recalculates():
    router, _, led = _router(delay=0.05)
    router.on_flag_change("race_start")
    router.sync(10.0)
    router.sync(11.5)
    assert [c.args[0] for c in led.set_delay.call_args_list] == [10.0, 11.5]
    assert led.trigger_timed.call_count == 2
    await asyncio.sleep(0.12)
    assert led.trigger_timed.call_count == 2


def test_continuous_flag_goes_to_led_queue_live():
    router, _, led = _router()
    router.on_flag_change("yellow_flag")
    led.trigger.assert_called_once_with("yellow_flag")


def test_idle_status_sets_idle():
    router, state, led = _router()
    router.on_flag_change("finished")
    assert state.display_track_status == "finished"
    led.set_idle.assert_called_once_with(True)
