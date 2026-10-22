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


# ── One start per race, again after a red flag (feature 004, ESP32 rule) ─────

def test_repeated_lights_out_shows_all_clear_and_plays_nothing():
    router, state, led = _router()
    router.on_flag_change("race_start")
    led.reset_mock()
    state.set_display_track_status("yellow_flag")
    router.on_flag_change("race_start")
    assert state.display_track_status == "track_clear"
    led.trigger_timed.assert_not_called()
    led.trigger.assert_not_called()
    led.force_trigger.assert_not_called()


def test_repeated_lights_out_resolves_to_no_effect():
    router, _, _ = _router()
    router.resolve("race_start")
    assert router.resolve("race_start") == ("track_clear", None)


def test_lights_out_after_red_flag_plays_start_again():
    router, _, led = _router()
    router.on_flag_change("race_start")
    router.on_flag_change("red_flag")
    led.reset_mock()
    router.on_flag_change("race_start")
    led.trigger_timed.assert_called_once_with("race_start", TIMED_EFFECTS["race_start"])


def test_red_flag_allows_only_one_restart():
    router, _, _ = _router()
    router.resolve("race_start")
    router.resolve("red_flag")
    assert router.resolve("race_start") == ("track_clear", "race_start")
    assert router.resolve("race_start") == ("track_clear", None)


def test_red_flag_before_the_start_does_not_allow_a_second_start():
    router, _, _ = _router()
    router.resolve("red_flag")
    router.resolve("race_start")
    assert router.resolve("race_start") == ("track_clear", None)


def test_session_end_allows_the_next_start():
    router, _, _ = _router()
    router.resolve("race_start")
    router.resolve("finished")
    assert router.resolve("race_start") == ("track_clear", "race_start")


def test_replay_play_with_promoted_allclear_then_started_plays_once():
    # At Play the re-fired pre-race AllClear is promoted, then F1's Started arrives.
    router, _, led = _router(replay=True)
    router.on_flag_change("track_clear")
    router.on_flag_change("race_start")
    assert [c.args[0] for c in led.trigger_timed.call_args_list] == ["race_start"]


def test_replay_restart_after_red_flag_plays_start():
    router, _, led = _router(replay=True)
    router.on_flag_change("track_clear")      # promoted start
    router.on_flag_change("red_flag")
    led.reset_mock()
    router.on_flag_change("race_start")       # Started at the restart
    led.trigger_timed.assert_called_once_with("race_start", TIMED_EFFECTS["race_start"])


async def test_delayed_repeated_lights_out_updates_page_only():
    router, state, led = _router(delay=0.05)
    router.on_flag_change("race_start")
    await asyncio.sleep(0.08)
    led.reset_mock()
    state.set_display_track_status("yellow_flag")
    router.on_flag_change("race_start")
    assert state.display_track_status == "yellow_flag"  # waits for the delay
    await asyncio.sleep(0.08)
    assert state.display_track_status == "track_clear"
    led.trigger_timed.assert_not_called()


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
        "race_start", TIMED_EFFECTS["race_start"], flush_before=None)
    await asyncio.sleep(0.12)
    led.trigger_timed.assert_called_once()  # the cancelled original never fires


def test_sync_without_pending_race_start_plays_once():
    router, state, led = _router(delay=0.0)
    state.set_lights_out_at(1000.0)
    router.on_flag_change("race_start")  # delay 0: played immediately, nothing pending
    led.trigger_timed.reset_mock()
    router.sync(0.0)
    led.trigger_timed.assert_called_once_with(
        "race_start", TIMED_EFFECTS["race_start"], flush_before=None)


def test_sync_without_lights_out_flushes_whole_queue():
    router, _, led = _router()
    router.sync(5.0)
    led.trigger_timed.assert_called_once_with(
        "race_start", TIMED_EFFECTS["race_start"], flush_before=None)


def _real_router(tmp_path, delay):
    """FlagRouter on a real LEDController (thread not started) in a live race."""
    state = AppState()
    state.set_session(SessionInfo(session_type="Race", is_active=True))
    led = LEDController(strip=MockStrip(10), effects_path=tmp_path / "missing.json",
                        delay_seconds=delay)
    router = FlagRouter(state=state, led=led, config=SimpleNamespace(delay_seconds=delay))
    return router, state, led


def _queued(led):
    items = []
    while not led._queue.empty():
        items.append(led._queue.get_nowait())
    return items


def _record_display(state):
    seen = []
    original = state.set_display_track_status

    def spy(status):
        seen.append(status)
        original(status)

    state.set_display_track_status = spy
    return seen


def _other_tasks():
    return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]


async def test_sync_keeps_flags_that_arrived_after_lights_out(tmp_path):
    # A lap-1 yellow received after lights-out is re-added to the LED queue from the
    # router's log; a pre-lights-out item is flushed.
    router, state, led = _real_router(tmp_path, delay=30.0)
    state.set_lights_out_at(time.monotonic())
    router.on_flag_change("race_start")
    led._queue.put(("safety_car", time.monotonic() - 50.0))  # stale, predates the start
    router.on_flag_change("yellow_flag")

    router.sync(5.0)

    assert [flag for flag, _ in _queued(led)] == ["yellow_flag"]
    for task in _other_tasks():
        task.cancel()


async def test_sync_retimes_flag_that_already_fired_on_old_delay_zero(tmp_path):
    # First use: delay 0. A lap-1 yellow has already fired and left the LED queue
    # before Sync; it must come back on the new delay, LEDs and displayed status.
    router, state, led = _real_router(tmp_path, delay=0.0)
    state.set_lights_out_at(time.monotonic())
    router.on_flag_change("race_start")
    before = time.monotonic()
    router.on_flag_change("yellow_flag")
    after = time.monotonic()
    led._drain_queue()  # delay 0: the yellow fires and leaves the queue
    assert led._queue.empty()

    router.sync(0.05)

    queued = _queued(led)
    assert [flag for flag, _ in queued] == ["yellow_flag"]
    assert before <= queued[0][1] <= after  # original arrival time, not the Sync press
    await asyncio.sleep(0.1)
    assert state.display_track_status == "yellow_flag"


async def test_sync_retimes_display_from_old_long_delay(tmp_path):
    # Old delay 5 s: the yellow and the AllClear are waiting on 5 s display tasks.
    # Sync re-times both onto 0.05 s and cancels the old tasks.
    router, state, led = _real_router(tmp_path, delay=5.0)
    state.set_lights_out_at(time.monotonic())
    router.on_flag_change("race_start")
    router.on_flag_change("yellow_flag")
    router.on_flag_change("track_clear")
    seen = _record_display(state)

    router.sync(0.05)

    assert [flag for flag, _ in _queued(led)] == ["yellow_flag"]
    await asyncio.sleep(0.2)
    assert seen == ["track_clear", "yellow_flag", "track_clear"]
    assert state.display_track_status == "track_clear"
    assert _other_tasks() == []  # the old 5 s tasks were cancelled


async def test_idle_status_clears_the_sync_log(tmp_path):
    router, state, led = _real_router(tmp_path, delay=0.0)
    state.set_lights_out_at(time.monotonic())
    router.on_flag_change("race_start")
    router.on_flag_change("yellow_flag")
    router.on_flag_change("finished")
    seen = _record_display(state)

    router.sync(0.05)

    assert _queued(led) == []
    assert led._timed_effect == "race_start"
    await asyncio.sleep(0.1)
    assert seen == ["track_clear"]  # only the race start; nothing re-timed
    assert _other_tasks() == []


async def test_replay_changes_are_never_logged_for_sync():
    router, state, led = _router(replay=True)
    router.on_flag_change("race_start")
    router.on_flag_change("yellow_flag")
    router.sync(0.05)
    led.trigger_at.assert_not_called()
    assert _other_tasks() == []
    assert state.display_track_status == "track_clear"


async def test_sync_cancels_every_pending_race_start():
    router, _, led = _router(delay=0.05)
    router.on_flag_change("race_start")
    router.on_flag_change("race_start")  # second lights-out while the first is pending
    router.sync(10.0)
    await asyncio.sleep(0.12)
    led.trigger_timed.assert_called_once()  # only Sync's race start plays


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
