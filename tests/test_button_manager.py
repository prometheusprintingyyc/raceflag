import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from raceflag.button_manager import ButtonManager, HOLD_SECONDS, FEEDBACK_AFTER, POLL_INTERVAL

FEEDBACK_TICKS = int(FEEDBACK_AFTER / POLL_INTERVAL)
TOTAL_TICKS = int(HOLD_SECONDS / POLL_INTERVAL)


class _FakeButton:
    """Fake gpiozero Button whose is_pressed returns values from a sequence."""

    def __init__(self, sequence, *, pull_up=True, bounce_time=0.0):
        self._values = list(sequence)
        self._index = 0

    @property
    def is_pressed(self):
        if self._index < len(self._values):
            val = self._values[self._index]
            self._index += 1
            return val
        return False


def _make_manager():
    wifi = AsyncMock()
    led = MagicMock()
    return ButtonManager(gpio_pin=21, wifi_manager=wifi, led=led)


async def _run(bm, sequence, extra_ticks=3):
    """Run bm.start() with faked gpiozero and instant sleep that stops after the sequence."""
    fake_button = _FakeButton(sequence)
    mock_gpiozero = MagicMock()
    mock_gpiozero.Button.return_value = fake_button

    total = len(sequence) + extra_ticks
    tick = [0]
    real_sleep = asyncio.sleep  # capture before patching

    async def fast_sleep(_duration):
        tick[0] += 1
        if tick[0] >= total:
            bm._running = False
        await real_sleep(0)  # yield to event loop without actually sleeping

    with patch.dict('sys.modules', {'gpiozero': mock_gpiozero}):
        with patch('asyncio.sleep', side_effect=fast_sleep):
            await bm.start()


@pytest.mark.asyncio
async def test_short_press_does_nothing():
    bm = _make_manager()
    # Press for 1 tick (well under 3 s threshold) then release
    sequence = [True] * 1 + [False] * 5
    await _run(bm, sequence)
    bm._led.force_trigger.assert_not_called()
    bm._wifi.reset.assert_not_called()
    bm._led.set_idle.assert_not_called()


@pytest.mark.asyncio
async def test_feedback_fires_after_3s():
    bm = _make_manager()
    # Hold just past feedback threshold but release before WiFi reset
    sequence = [True] * (FEEDBACK_TICKS + 2) + [False] * 5
    await _run(bm, sequence)
    bm._led.force_trigger.assert_called_once_with("red_flag")


@pytest.mark.asyncio
async def test_release_after_feedback_triggers_shutdown():
    bm = _make_manager()
    # Hold past 3 s, release before 10 s → shutdown
    sequence = [True] * (FEEDBACK_TICKS + 2) + [False] * 5

    shutdown_called = [False]

    async def mock_shutdown():
        shutdown_called[0] = True

    bm._shutdown = mock_shutdown
    await _run(bm, sequence)
    assert shutdown_called[0]


@pytest.mark.asyncio
async def test_hold_10s_triggers_wifi_reset():
    bm = _make_manager()
    # Hold for the full 10 s
    sequence = [True] * (TOTAL_TICKS + 1) + [False] * 5
    await _run(bm, sequence)
    bm._wifi.reset.assert_called_once()


@pytest.mark.asyncio
async def test_hold_10s_does_not_trigger_shutdown():
    bm = _make_manager()
    sequence = [True] * (TOTAL_TICKS + 1) + [False] * 5

    shutdown_called = [False]

    async def mock_shutdown():
        shutdown_called[0] = True

    bm._shutdown = mock_shutdown
    await _run(bm, sequence)
    assert not shutdown_called[0]


@pytest.mark.asyncio
async def test_gpio_unavailable_disables_button():
    bm = _make_manager()
    # None in sys.modules causes 'from gpiozero import Button' to raise ImportError
    with patch.dict('sys.modules', {'gpiozero': None}):
        await bm.start()  # should return cleanly without error
    bm._led.force_trigger.assert_not_called()
    bm._wifi.reset.assert_not_called()
