from __future__ import annotations
import asyncio
import logging

logger = logging.getLogger(__name__)

HOLD_SECONDS = 10
POLL_INTERVAL = 0.1
FEEDBACK_AFTER = 3.0  # seconds of hold before visual feedback starts


class ButtonManager:
    """Monitors a GPIO button and triggers a WiFi reset on a 10-second hold.

    Visual feedback: red animation after 3s, white hotspot flash on trigger.
    Gracefully disables itself when GPIO is unavailable (non-Pi hardware).
    """

    def __init__(self, gpio_pin: int, wifi_manager, led):
        self._pin = gpio_pin
        self._wifi = wifi_manager
        self._led = led
        self._running = False

    async def start(self) -> None:
        try:
            from gpiozero import Button
            button = Button(self._pin, pull_up=True, bounce_time=0.05)
        except Exception as e:
            logger.warning("ButtonManager: GPIO unavailable (%s) — button disabled", e)
            return

        self._running = True
        logger.info("ButtonManager: monitoring GPIO%d (hold %ds to reset WiFi)", self._pin, HOLD_SECONDS)

        total_ticks = int(HOLD_SECONDS / POLL_INTERVAL)
        feedback_ticks = int(FEEDBACK_AFTER / POLL_INTERVAL)
        hold_ticks = 0
        feedback_shown = False

        while self._running:
            await asyncio.sleep(POLL_INTERVAL)
            if button.is_pressed:
                hold_ticks += 1
                if hold_ticks >= feedback_ticks and not feedback_shown:
                    feedback_shown = True
                    self._led.force_trigger("red_flag")
                    logger.debug("ButtonManager: hold feedback started at %ds", FEEDBACK_AFTER)
                if hold_ticks >= total_ticks:
                    logger.info("ButtonManager: WiFi reset triggered after %ds hold", HOLD_SECONDS)
                    hold_ticks = 0
                    feedback_shown = False
                    await self._wifi.reset()
                    await asyncio.sleep(2)  # prevent immediate re-trigger while button is still held
            else:
                if feedback_shown:
                    self._led.set_idle(True)
                hold_ticks = 0
                feedback_shown = False

    async def stop(self) -> None:
        self._running = False
