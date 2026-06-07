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

    def trigger(self, flag_state: str) -> None:
        self._queue.put((flag_state, time.monotonic()))

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

    def _step_idle_animation(self) -> None:
        """Chase effect: red on segments 1+2, white on segment 3, with a 2-pixel fading tail."""
        t = time.monotonic()
        speed = 4.0  # pixels per second
        tail = 2

        _IDLE_SEGMENTS = [
            (0,  10, 255,   0,   0),   # seg1: red
            (11, 16, 255,   0,   0),   # seg2: red
            (17, 20, 255, 255, 255),   # seg3: white
        ]

        for i in range(self._strip.num_pixels()):
            self._strip.set_pixel(i, 0, 0, 0)

        for start, end, r, g, b in _IDLE_SEGMENTS:
            length = end - start + 1
            head = int(t * speed) % length
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
                self._apply_effect(flag_state)
            else:
                self._queue.put((flag_state, arrival))

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._maybe_reload_effects()
            self._drain_queue()
            if self._idle_active and self._queue.empty():
                self._step_idle_animation()
            time.sleep(0.05)
