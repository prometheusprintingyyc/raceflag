import json
import time
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from raceflag.led_controller import LEDController, MockStrip


@pytest.fixture
def effects_file(tmp_path):
    data = {
        "track_clear": {
            "segments": [{"start": 0, "end": 9, "color": "#00FF00", "pattern": "solid"}],
            "transition": "fade",
            "transition_ms": 100,
        },
        "red_flag": {
            "segments": [{"start": 0, "end": 9, "color": "#FF0000", "pattern": "solid"}],
            "transition": "instant",
            "transition_ms": 0,
        },
    }
    p = tmp_path / "effects.json"
    p.write_text(json.dumps(data))
    return p


@pytest.fixture
def controller(effects_file):
    strip = MockStrip(10)
    ctrl = LEDController(strip=strip, effects_path=effects_file, delay_seconds=0.0)
    return ctrl


def test_load_effects_parses_all_flag_states(controller):
    effects = controller._load_effects()
    assert "track_clear" in effects
    assert "red_flag" in effects


def test_load_effects_returns_empty_dict_on_missing_file(tmp_path):
    strip = MockStrip(10)
    ctrl = LEDController(strip=strip, effects_path=tmp_path / "missing.json", delay_seconds=0.0)
    assert ctrl._load_effects() == {}


def test_load_effects_returns_empty_dict_on_invalid_json(tmp_path):
    p = tmp_path / "effects.json"
    p.write_text("not json")
    strip = MockStrip(10)
    ctrl = LEDController(strip=strip, effects_path=p, delay_seconds=0.0)
    assert ctrl._load_effects() == {}


def test_hex_to_rgb_converts_correctly(controller):
    assert controller._hex_to_rgb("#FF0000") == (255, 0, 0)
    assert controller._hex_to_rgb("#00FF00") == (0, 255, 0)
    assert controller._hex_to_rgb("#0000FF") == (0, 0, 255)
    assert controller._hex_to_rgb("#FFD700") == (255, 215, 0)


def test_apply_solid_effect_sets_all_pixels(controller, effects_file):
    controller._effects = controller._load_effects()
    controller._apply_effect("track_clear")
    r, g, b = controller._strip.pixels[0]
    assert (r, g, b) == (0, 255, 0)
    assert all(p == (0, 255, 0) for p in controller._strip.pixels)


def test_apply_unknown_effect_does_not_raise(controller):
    controller._effects = {}
    controller._apply_effect("nonexistent")  # must not raise


def test_trigger_queues_event(controller):
    controller.trigger("red_flag")
    assert not controller._queue.empty()
    item = controller._queue.get_nowait()
    assert item[0] == "red_flag"


def test_set_delay_updates_delay(controller):
    controller.set_delay(30.0)
    assert controller._delay_seconds == 30.0


def test_delay_queue_holds_event_until_delay_elapsed(controller):
    controller._effects = controller._load_effects()
    controller.set_delay(0.05)
    controller.trigger("track_clear")
    controller._drain_queue()
    assert all(p == (0, 0, 0) for p in controller._strip.pixels)
    time.sleep(0.1)
    controller._drain_queue()
    assert controller._strip.pixels[0] == (0, 255, 0)


def test_start_stop(controller):
    controller._effects = controller._load_effects()
    controller.start()
    assert controller._thread is not None and controller._thread.is_alive()
    controller.stop()
    assert not controller._thread.is_alive()


def test_controller_starts_in_idle(controller):
    assert controller._idle_active is True


def test_set_idle_true(controller):
    controller._idle_active = False
    controller.set_idle(True)
    assert controller._idle_active is True


def test_set_idle_false(controller):
    controller.set_idle(False)
    assert controller._idle_active is False


def test_trigger_does_not_immediately_disable_idle(controller):
    """Idle keeps running until the queued effect actually fires, not on trigger()."""
    assert controller._idle_active is True
    controller.trigger("track_clear")
    assert controller._idle_active is True


def test_drain_queue_disables_idle_when_effect_fires(controller):
    controller._effects = controller._load_effects()
    controller.trigger("track_clear")
    assert controller._idle_active is True
    controller._drain_queue()
    assert controller._idle_active is False


def test_idle_animation_runs_and_shows(controller):
    show_before = controller._strip.show_calls
    controller._step_idle_animation()
    assert controller._strip.show_calls == show_before + 1
    # Chase has at least one non-zero pixel and no blue in the red segments (0-10)
    assert any(r > 0 or g > 0 for r, g, b in controller._strip.pixels)
    assert all(b == 0 for r, g, b in controller._strip.pixels[:11])


def test_trigger_timed_sets_timed_effect(controller):
    controller.trigger_timed("track_clear", 30.0)
    assert controller._timed_effect == "track_clear"
    assert controller._timed_effect_expiry > time.monotonic()


def test_trigger_timed_disables_idle(controller):
    assert controller._idle_active is True
    controller.trigger_timed("track_clear", 30.0)
    assert controller._idle_active is False


def test_track_clear_animation_shows_green_or_red(controller):
    controller._step_track_clear_animation()
    assert controller._strip.show_calls == 1
    first = controller._strip.pixels[0]
    assert all(p == first for p in controller._strip.pixels)
    r, g, b = first
    assert b == 0
    assert (r == 0 and g == 255) or (r == 255 and g == 0)


def test_drain_queue_cancels_timed_effect(controller):
    controller._effects = controller._load_effects()
    controller.trigger_timed("track_clear", 30.0)
    controller.trigger("red_flag")
    controller._drain_queue()
    assert controller._timed_effect == ""


def test_timed_effect_expires_and_restores_idle(controller):
    controller._effects = controller._load_effects()
    controller.trigger_timed("track_clear", 0.05)
    controller.start()
    time.sleep(0.3)
    controller.stop()
    assert controller._idle_active is True


def test_red_flag_trigger_sets_active_animation(controller):
    controller._effects = controller._load_effects()
    controller.trigger("red_flag")
    controller._drain_queue()
    assert controller._active_animation == "red_flag"
    assert controller._idle_active is False


def test_red_flag_animation_only_red_channel(controller):
    controller._strip = MockStrip(21)
    controller._step_red_flag_animation()
    assert controller._strip.show_calls == 1
    for r, g, b in controller._strip.pixels:
        assert g == 0 and b == 0
    assert any(r > 0 for r, g, b in controller._strip.pixels)


def test_red_flag_animation_varies_brightness(controller):
    controller._strip = MockStrip(21)
    controller._step_red_flag_animation()
    reds = [r for r, g, b in controller._strip.pixels]
    assert len(set(reds)) > 1  # not all the same brightness


def test_set_idle_clears_active_animation(controller):
    controller._active_animation = "red_flag"
    controller.set_idle(True)
    assert controller._active_animation == ""


def test_set_idle_clears_timed_effect(controller):
    controller.trigger_timed("track_clear", 30.0)
    controller.set_idle(True)
    assert controller._timed_effect == ""


def test_set_idle_true_flushes_queue(controller):
    controller.trigger("yellow_flag")
    controller.set_idle(True)
    assert controller._queue.empty()


def test_trigger_timed_clears_active_animation(controller):
    controller._active_animation = "red_flag"
    controller.trigger_timed("track_clear", 30.0)
    assert controller._active_animation == ""


def test_trigger_timed_flushes_stale_queue_events(controller):
    """Queued continuous events must not cancel a timed effect set afterward."""
    controller._effects = controller._load_effects()
    controller.trigger("yellow_flag")
    controller.trigger_timed("track_clear", 30.0)
    # Queue should be empty — stale yellow_flag discarded
    assert controller._queue.empty()
    # Draining should NOT cancel the timed effect
    controller._drain_queue()
    assert controller._timed_effect == "track_clear"


def test_drain_queue_clears_active_animation_on_non_continuous(controller):
    controller._effects = controller._load_effects()
    controller._active_animation = "red_flag"
    controller.trigger("track_clear")
    controller._drain_queue()
    assert controller._active_animation == ""


def test_yellow_flag_trigger_sets_active_animation(controller):
    controller._effects = controller._load_effects()
    controller.trigger("yellow_flag")
    controller._drain_queue()
    assert controller._active_animation == "yellow_flag"


def test_yellow_flag_animation_only_yellow_channel(controller):
    controller._strip = MockStrip(21)
    controller._step_yellow_flag_animation()
    for r, g, b in controller._strip.pixels:
        assert b == 0
    assert any(r > 0 for r, g, b in controller._strip.pixels)


def test_yellow_flag_animation_varies_brightness(controller):
    controller._strip = MockStrip(21)
    controller._step_yellow_flag_animation()
    reds = [r for r, g, b in controller._strip.pixels]
    assert len(set(reds)) > 1


def test_virtual_sc_trigger_sets_active_animation(controller):
    controller._effects = controller._load_effects()
    controller.trigger("virtual_sc")
    controller._drain_queue()
    assert controller._active_animation == "virtual_sc"


def test_virtual_sc_animation_all_yellow_or_off(controller):
    controller._strip = MockStrip(21)
    controller._step_virtual_sc_animation()
    assert controller._strip.show_calls == 1
    first = controller._strip.pixels[0]
    assert all(p == first for p in controller._strip.pixels)
    r, g, b = first
    assert b == 0
    assert (r == 255 and g == 215) or (r == 0 and g == 0)


def test_safety_car_trigger_sets_active_animation(controller):
    controller._effects = controller._load_effects()
    controller.trigger("safety_car")
    controller._drain_queue()
    assert controller._active_animation == "safety_car"


def test_safety_car_animation_only_yellow_or_off(controller):
    controller._strip = MockStrip(21)
    controller._step_safety_car_animation()
    assert controller._strip.show_calls == 1
    for r, g, b in controller._strip.pixels:
        assert b == 0
        assert (r == 255 and g == 215) or (r == 0 and g == 0)


def test_safety_car_animation_seg12_and_seg3_opposite(controller):
    controller._strip = MockStrip(21)
    controller._step_safety_car_animation()
    seg12_on = controller._strip.pixels[0][0] == 255
    seg3_on = controller._strip.pixels[17][0] == 255
    assert seg12_on != seg3_on  # they must be opposite


def test_checkered_animation_white_channel_only(controller):
    controller._strip = MockStrip(21)
    controller._step_checkered_animation()
    for r, g, b in controller._strip.pixels:
        assert r == g == b
    assert any(r > 0 for r, g, b in controller._strip.pixels)


def test_checkered_animation_varies_brightness(controller):
    controller._strip = MockStrip(21)
    controller._step_checkered_animation()
    vals = [r for r, g, b in controller._strip.pixels]
    assert len(set(vals)) > 1


def test_race_start_animation_flashes_green_or_off(controller):
    controller._step_race_start_animation()
    assert controller._strip.show_calls == 1
    first = controller._strip.pixels[0]
    assert all(p == first for p in controller._strip.pixels)
    r, g, b = first
    assert b == 0 and r == 0  # never has red or blue
    assert g == 255 or g == 0  # either full green or off


def test_get_pixel_state_returns_pixels_for_mock_strip(controller):
    controller._strip.set_pixel(0, 255, 0, 0)
    pixels = controller.get_pixel_state()
    assert pixels is not None
    assert pixels[0] == (255, 0, 0)


def test_get_pixel_state_returns_none_for_real_strip(controller):
    from raceflag.led_controller import LEDStrip
    class FakeRealStrip:
        def begin(self): pass
        def set_pixel(self, n, r, g, b): pass
        def show(self): pass
        def num_pixels(self): return 10
        def fill(self, r, g, b): pass
    controller._strip = FakeRealStrip()
    assert controller.get_pixel_state() is None


def test_run_dispatches_race_start_animation(controller):
    controller._effects = controller._load_effects()
    controller.trigger_timed("race_start", 30.0)
    controller.start()
    time.sleep(0.1)
    controller.stop()
    # At least one frame was rendered — pixels should be green or off
    assert all(b == 0 and r == 0 for r, g, b in controller._strip.pixels)


def test_led_enabled_defaults_to_true(controller):
    assert controller._led_enabled is True


def test_set_led_enabled_false_blanks_strip(controller):
    """set_led_enabled(False) sets the flag; the _run() loop blanks the strip."""
    controller.set_led_enabled(False)
    assert controller._led_enabled is False


def test_set_led_enabled_true_does_not_blank_strip(controller):
    controller._strip.set_pixel(0, 255, 0, 0)
    controller.set_led_enabled(True)
    assert controller._strip.pixels[0] == (255, 0, 0)


def test_run_blanks_strip_when_led_disabled(controller):
    controller._effects = controller._load_effects()
    controller.set_led_enabled(False)
    controller.start()
    time.sleep(0.15)
    controller.stop()
    assert all(p == (0, 0, 0) for p in controller._strip.pixels)


def test_run_hotspot_animation_runs_when_led_disabled(controller):
    from unittest.mock import patch
    controller._effects = controller._load_effects()
    controller.set_led_enabled(False)
    controller.set_hotspot_mode(True)
    with patch.object(controller, '_step_hotspot_animation') as mock_anim:
        controller.start()
        time.sleep(0.15)
        controller.stop()
    assert mock_anim.call_count > 0


def test_drain_queue_skips_apply_effect_when_led_disabled(controller):
    controller._effects = controller._load_effects()
    controller.set_led_enabled(False)
    controller.trigger("track_clear")
    controller._drain_queue()
    assert all(p == (0, 0, 0) for p in controller._strip.pixels)


def test_drain_queue_tracks_continuous_animation_when_led_disabled(controller):
    controller._effects = controller._load_effects()
    controller.set_led_enabled(False)
    controller.trigger("yellow_flag")
    controller._drain_queue()
    assert controller._active_animation == "yellow_flag"
