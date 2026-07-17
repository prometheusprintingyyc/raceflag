# 🏁 RaceFlag

**Real-time F1 flag states on a WS2812B LED strip, driven by a Raspberry Pi Zero 2W.**

RaceFlag connects to the official Formula 1 live timing feed and translates on-track flag conditions into LED animations — green for track clear, rolling yellow for caution, red wave for a red flag, and more. A mobile-friendly web interface shows live timing, race positions, weather, and championship standings.

---

## Features

- **Live flag animations** — Track Clear, Yellow Flag, Safety Car, VSC, Red Flag, Checkered Flag, Race Start, and Idle, each with a distinct LED animation
- **Broadcast delay** — Adjustable 0–90 s LED delay to sync with your stream lag
- **Web UI** — Mobile-friendly interface showing live timing, race positions, driver gaps, tyre compounds, weather, and championship standings
- **First-boot WiFi setup** — Broadcasts a `RaceFlag-Setup` hotspot on first boot; configure your network through the browser, no SSH required
- **OTA updates** — One-tap firmware updates from the web UI
- **Diagnostic logs** — Send logs to support directly from the Settings panel
- **Demo mode** — Virtual LED strip preview in the browser without hardware
- **Docker support** — Run the web UI and timing feed on any machine for development

---

## Hardware

| Component | Notes |
|---|---|
| Raspberry Pi Zero 2W | Pi Zero 1 W also supported (use 32-bit OS image) |
| WS2812B LED strip | Any length; default config uses 21 LEDs across 3 segments |
| 5V power supply | 2A minimum for up to ~60 LEDs |
| 3× jumper wires | 5V, GND, GPIO 18 (data) |

---

## Quick Start

### Raspberry Pi

```bash
# Flash Raspberry Pi OS Lite (64-bit, Bookworm) with SSH enabled, then:
ssh pi@raceflag.local

# Install RaceFlag
curl -fsSL https://raw.githubusercontent.com/prometheusprintingyyc/raceflag/main/install.sh | sudo bash
```

Open **http://raceflag.local:8080** in any browser on the same network.

See [INSTALL.md](INSTALL.md) for full wiring instructions, OS configuration, and config reference.

### Docker (development / demo)

```bash
docker compose up
```

Open **http://localhost:8080**. LED hardware is not used; enable Demo Mode in Settings to see the virtual strip.

---

## Web Interface

| View | Contents |
|---|---|
| **Live Race** | Flag status, LED delay slider, session info, weather, race positions, race control messages |
| **Standings** | Next race countdown, Drivers Championship, Constructors Championship |
| **Settings** | OTA update, Demo Mode, test effects, Send Logs, Shut Down |

The interface switches between Live Race and Standings automatically when a session starts or ends.

---

## Configuration

`/opt/raceflag/config.json`

```json
{
  "led_count": 21,
  "led_gpio_pin": 18,
  "led_brightness": 128,
  "delay_seconds": 0.0
}
```

| Field | Description |
|---|---|
| `led_count` | Total LEDs across all segments |
| `led_gpio_pin` | Data pin — leave as `18` unless rewired |
| `led_brightness` | 0–255 (128 = 50%) |
| `delay_seconds` | LED delay to match your broadcast lag (0–90 s) |

---

## Development

```bash
# Install dependencies
pip install -r requirements-dev.txt

# Run tests
pytest

# Run locally (no hardware required)
DEMO_MODE=1 WIFI_ENABLED=0 python -m raceflag.main
```

---

## How It Works

RaceFlag connects to the F1 live timing SignalR feed. Flag state changes are routed through a configurable delay queue before triggering LED animations and updating the web UI. The web server (FastAPI + uvicorn) serves the frontend and exposes a REST API polled every 2 seconds by the browser.

```
F1 Live Timing Feed
       ↓
  F1Listener (SignalR/WebSocket)
       ↓
   AppState (in-memory)
       ↓ (delay queue)
  LEDController (WS2812B via rpi_ws281x)
       ↓
  Web Server (FastAPI) ← polling browser
```

---

## License

MIT
