import sys
import types

from raceflag.config import Config
from raceflag.main import _make_strip


class _FakePixelStrip:
    def __init__(self, count, pin, brightness=255):
        self._colors = [0] * count

    def begin(self): pass
    def show(self): pass
    def numPixels(self): return len(self._colors)
    def setPixelColor(self, n, color): self._colors[n] = color
    def getPixelColor(self, n): return self._colors[n]


def _fake_color(r, g, b, white=0):
    return (white << 24) | (r << 16) | (g << 8) | b


def test_rpi_strip_get_pixels_reads_back_colours(monkeypatch):
    fake = types.ModuleType("rpi_ws281x")
    fake.PixelStrip = _FakePixelStrip
    fake.Color = _fake_color
    monkeypatch.setitem(sys.modules, "rpi_ws281x", fake)

    strip = _make_strip(Config(led_count=3))
    strip.set_pixel(0, 255, 0, 0)
    strip.set_pixel(2, 1, 2, 3)

    assert strip.get_pixels() == [(255, 0, 0), (0, 0, 0), (1, 2, 3)]
