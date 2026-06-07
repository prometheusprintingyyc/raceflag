from __future__ import annotations
import json
import math
import queue
import threading
import time
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class LEDStrip(Protocol):
    def begin(self) -> None: ...
    def set_pixel(self, n: int, r: int, g: int, b: int) -> None: ...
    def show(self) -> None: ...
    def num_pixels(self) -> int: ...
    def fill(self, r: int, g: int, b: int) -> None: ...


class MockStrip:
    def __init__(self, count: int):
        self._count = count
        self.pixels: list[tuple[int, int, int]] = [(0, 0, 0)] * count
        self.show_calls = 0

    def begin(self) -> None:
        pass

    def set_pixel(self, n: int, r: int, g: int, b: int) -> None:
        if 0 <= n < self._count:
            self.pixels[n] = (r, g, b)

    def show(self) -> None:
        self.show_calls += 1

    def num_pixels(self) -> int:
        return self._count

    def fill(self, r: int, g: int, b: int) -> None:
        for i in range(self._count):
            self.set_pixel(i, r, g, b)


class LEDController:
    _CONTINUOUS_ANIMATIONS = frozenset({"red_flag", "yellow_flag", "safety_car", "virtual_sc"})

    def __init__(self, strip: LEDStrip, effects_path: Path, delay_seconds: float = 0.0):
        self._strip = strip
        self._effects_path = Path(effects_path)
        self._delay_seconds = delay_seconds
        self._effects: dict = self._load_effects()
        self._effects_mtime: float = 0.0
        self._queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._idle_active: bool = True
        self._timed_effect: str = ""
        self._timed_effect_expiry: float = 0.0
        self._active_animation: str = ""  # continuous animation, no auto-expiry

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                import logging
                logging.getLogger(__name__).warning("LED controller thread did not stop within 2s")

    def set_delay(self, seconds: float) -> None:
        self._delay_seconds = seconds

    def set_idle(self, active: bool) -> None:
        self._idle_active = active
        if active:
            self._active_animation = ""

    def trigger(self, flag_state: str) -> None:
        self._queue.put((flag_state, time.monotonic()))

    def trigger_timed(self, flag_state: str, duration: float) -> None:
        self._timed_effect = flag_state
        self._timed_effect_expiry = time.monotonic() + duration
        self._idle_active = False
        self._active_animation = ""

    def _hex_to_rgb(self, hex_color: str) -> tuple[int, int, int]:
        h = hex_color.lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    def _load_effects(self) -> dict:
        try:
            mtime = self._effects_path.stat().st_mtime
            data = json.loads(self._effects_path.read_text())
            self._effects_mtime = mtime
            return data
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _maybe_reload_effects(self) -> None:
        try:
            mtime = self._effects_path.stat().st_mtime
            if mtime != self._effects_mtime:
                loaded = self._load_effects()
                if loaded:
                    self._effects = loaded
        except OSError:
            pass

    def _apply_effect(self, flag_state: str) -> None:
        effect = self._effects.get(flag_state)
        if not effect:
            return
        for seg in effect.get("segments", []):
            r, g, b = self._hex_to_rgb(seg["color"])
            pattern = seg.get("pattern", "solid")
            start, end = seg["start"], seg["end"]
            if pattern == "solid":
                for i in range(start, min(end + 1, self._strip.num_pixels())):
                    self._strip.set_pixel(i, r, g, b)
            elif pattern in ("blink", "pulse", "chase", "rainbow"):
                for i in range(start, min(end + 1, self._strip.num_pixels())):
                    self._strip.set_pixel(i, r, g, b)
        self._strip.show()

    def _step_red_flag_animation(self) -> None:
        """Sine-wave brightness rolling across all LEDs in red."""
        t = time.monotonic()
        wave_length = 7.0   # pixels per brightness cycle
        speed = 0.5         # cycles per second
        for i in range(self._strip.num_pixels()):
            phase = (i / wave_length - t * speed) * 2 * math.pi
            brightness = 0.2 + ((math.sin(phase) + 1) / 2) * 0.8  # 20 %–100 %
            self._strip.set_pixel(i, int(255 * brightness), 0, 0)
        self._strip.show()

    def _step_virtual_sc_animation(self) -> None:
        """All LEDs flash yellow on/off every 0.5 seconds."""
        t = time.monotonic()
        on = int(t * 2) % 2 == 0
        for i in range(self._strip.num_pixels()):
            self._strip.set_pixel(i, 255 if on else 0, 215 if on else 0, 0)
        self._strip.show()

    def _step_safety_car_animation(self) -> None:
        """Segments 1+2 and segment 3 alternate yellow every 0.5 seconds."""
        t = time.monotonic()
        seg12_on = int(t * 2) % 2 == 0  # flips every 0.5 s
        for i in range(self._strip.num_pixels()):
            on = seg12_on if i <= 16 else not seg12_on
            self._strip.set_pixel(i, 255 if on else 0, 215 if on else 0, 0)
        self._strip.show()

    def _step_yellow_flag_animation(self) -> None:
        """Sine-wave brightness rolling across all LEDs in yellow."""
        t = time.monotonic()
        wave_length = 7.0
        speed = 0.5
        for i in range(self._strip.num_pixels()):
            phase = (i / wave_length - t * speed) * 2 * math.pi
            brightness = 0.2 + ((math.sin(phase) + 1) / 2) * 0.8
            self._strip.set_pixel(i, int(255 * brightness), int(215 * brightness), 0)
        self._strip.show()

    def _step_track_clear_animation(self) -> None:
        """Alternates all LEDs between green and red every 0.5 seconds."""
        t = time.monotonic()
        if int(t * 2) % 2 == 0:
            r, g, b = 0, 255, 0
        else:
            r, g, b = 255, 0, 0
        for i in range(self._strip.num_pixels()):
            self._strip.set_pixel(i, r, g, b)
        self._strip.show()

    def _step_checkered_animation(self) -> None:
        """Sine-wave brightness rolling across all LEDs in white."""
        t = time.monotonic()
        wave_length = 7.0
        speed = 0.5
        for i in range(self._strip.num_pixels()):
            phase = (i / wave_length - t * speed) * 2 * math.pi
            brightness = 0.2 + ((math.sin(phase) + 1) / 2) * 0.8
            v = int(255 * brightness)
            self._strip.set_pixel(i, v, v, v)
        self._strip.show()

    def _step_race_start_animation(self) -> None:
        """Flashes all LEDs green at 2 Hz for the race start."""
        t = time.monotonic()
        on = int(t * 4) % 2 == 0  # 4 transitions/sec → 2 Hz
        r, g, b = (0, 255, 0) if on else (0, 0, 0)
        for i in range(self._strip.num_pixels()):
            self._strip.set_pixel(i, r, g, b)
        self._strip.show()

    def _step_idle_animation(self) -> None:
        """Chase effect: red on segments 1+2 (shared period), white on segment 3."""
        t = time.monotonic()
        # Segments 1+2 share a cycle period so they loop in sync regardless of length.
        # Segment 3 runs independently at its own rate.
        SHARED_PERIOD = 2.75  # seconds per cycle for segments 1 & 2
        SEG3_PERIOD   = 1.0
        tail = 2

        _IDLE_SEGMENTS = [
            (0,  10, 255,   0,   0, SHARED_PERIOD),
            (11, 16, 255,   0,   0, SHARED_PERIOD),
            (17, 20, 255, 255, 255, SEG3_PERIOD),
        ]

        for i in range(self._strip.num_pixels()):
            self._strip.set_pixel(i, 0, 0, 0)

        for start, end, r, g, b, period in _IDLE_SEGMENTS:
            length = end - start + 1
            head = int((t % period) / period * length)
            for j in range(tail + 1):
                idx = (head - j) % length
                fade = 1.0 - j / (tail + 1)
                self._strip.set_pixel(
                    start + idx,
                    int(r * fade), int(g * fade), int(b * fade),
                )

        self._strip.show()

    def _drain_queue(self) -> None:
        now = time.monotonic()
        # Snapshot the queue so items not yet due can be re-enqueued without blocking trigger().
        pending = []
        while not self._queue.empty():
            try:
                pending.append(self._queue.get_nowait())
            except queue.Empty:
                break
        for flag_state, arrival in pending:
            if now - arrival >= self._delay_seconds:
                self._idle_active = False
                self._timed_effect = ""
                if flag_state in self._CONTINUOUS_ANIMATIONS:
                    self._active_animation = flag_state
                else:
                    self._active_animation = ""
                    self._apply_effect(flag_state)
            else:
                self._queue.put((flag_state, arrival))

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._maybe_reload_effects()
            self._drain_queue()
            now = time.monotonic()
            if self._timed_effect:
                if now >= self._timed_effect_expiry:
                    self._timed_effect = ""
                    self._idle_active = True
                elif self._queue.empty():
                    if self._timed_effect == "race_start":
                        self._step_race_start_animation()
                    elif self._timed_effect == "checkered":
                        self._step_checkered_animation()
                    else:
                        self._step_track_clear_animation()
            elif self._active_animation and self._queue.empty():
                if self._active_animation == "red_flag":
                    self._step_red_flag_animation()
                elif self._active_animation == "yellow_flag":
                    self._step_yellow_flag_animation()
                elif self._active_animation == "safety_car":
                    self._step_safety_car_animation()
                elif self._active_animation == "virtual_sc":
                    self._step_virtual_sc_animation()
            elif self._idle_active and self._queue.empty():
                self._step_idle_animation()
            time.sleep(0.05)
