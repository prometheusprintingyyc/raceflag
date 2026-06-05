# RaceFlag — Design Spec

**Date:** 2026-06-04  
**Status:** Approved

---

## Overview

RaceFlag is a reactive LED lightbox in the shape of an F1 logo that responds to live Formula 1 race events. It runs on a Raspberry Pi Zero 2W and consists of a single Python service that drives an addressable LED strip, listens to the official F1 SignalR timing feed, and serves a responsive web UI. Devices are deployed remotely and updated over-the-air via GitHub Releases.

---

## Architecture

A single Python service (`raceflag`) runs under systemd. All components live in one process, connected via shared in-memory state. No message broker or external database is required.

```
raceflag/
├── main.py               # Entry point, wires all components together
├── f1_listener.py        # SignalR connection, parses F1 data streams
├── led_controller.py     # GPIO LED strip driver, delay queue, effect loader
├── web_server.py         # FastAPI app, REST API, serves frontend
├── state.py              # Shared in-memory state (thread-safe dataclass)
├── wifi_manager.py       # WiFi monitor, hostapd/dnsmasq hotspot fallback
├── ota.py                # GitHub Releases checker and updater
├── config.py             # Loads/saves config.json
├── effects/
│   └── effects.json      # Editable LED effects library
├── frontend/             # Static HTML/CSS/JS web UI
├── version.txt           # Current installed version
└── install.sh            # First-run installer
```

**Runtime wiring:**
- `f1_listener` writes parsed F1 data into `state` (thread-safe in-memory dataclass)
- `led_controller` subscribes to state changes and applies effects after the configured delay
- `web_server` reads from `state` for display; writes to `config` for settings changes
- `wifi_manager` and `ota` run as independent background asyncio tasks

**Concurrency model:** Python asyncio for the SignalR listener, web server, WiFi monitor, and OTA tasks. The LED GPIO controller runs on a dedicated background thread (GPIO requires a real OS thread for timing precision). State changes from `f1_listener` to `led_controller` are communicated via a `threading.Event` + shared queue so the LED thread can wake immediately without polling.

---

## LED Effects System

### Hardware

Single WS2812B (NeoPixel-compatible) addressable LED strip running through the F1 logo shape, driven directly from a Raspberry Pi GPIO pin via the `rpi_ws281x` library (based on `wled_rpi`). LED count and GPIO pin are configurable in `config.json`.

### Effects Library

Effects are stored in `effects/effects.json` — a plain JSON file editable without touching code. The LED controller checks the file's modification time on each loop tick and reloads it when it changes; no service restart required.

**Effect structure:**
```json
{
  "track_clear": {
    "segments": [
      { "start": 0, "end": 59, "color": "#00FF00", "pattern": "solid" }
    ],
    "transition": "fade",
    "transition_ms": 500
  },
  "yellow_flag": {
    "segments": [
      { "start": 0, "end": 59, "color": "#FFD700", "pattern": "solid" }
    ],
    "transition": "instant",
    "transition_ms": 0
  },
  "safety_car": {
    "segments": [
      { "start": 0, "end": 29, "color": "#FFD700", "pattern": "solid" },
      { "start": 30, "end": 59, "color": "#00FF00", "pattern": "solid" }
    ],
    "transition": "fade",
    "transition_ms": 800
  },
  "virtual_sc": {
    "segments": [
      { "start": 0, "end": 59, "color": "#FFD700", "pattern": "pulse" }
    ],
    "transition": "fade",
    "transition_ms": 1000
  },
  "red_flag": {
    "segments": [
      { "start": 0, "end": 59, "color": "#FF0000", "pattern": "solid" }
    ],
    "transition": "instant",
    "transition_ms": 0
  },
  "checkered": {
    "segments": [
      { "start": 0, "end": 59, "color": "#FFFFFF", "pattern": "chase" }
    ],
    "transition": "instant",
    "transition_ms": 0
  }
}
```

**Supported patterns:** `solid`, `blink`, `pulse`, `chase`, `rainbow`

**Flag state mapping** (from F1 TrackStatus feed):

| TrackStatus value | State key |
|---|---|
| 1 | `track_clear` |
| 2 | `yellow_flag` |
| 4 | `safety_car` |
| 5 | `red_flag` |
| 6 | `virtual_sc` |
| 7 | `virtual_sc` (ending) |
| Chequered | `checkered` |

### Delay Queue

When a flag state change arrives, the LED controller places it on an internal queue with an arrival timestamp. A background loop holds each item until `arrival_time + delay_seconds` is reached, then applies the effect. The delay is set via the web UI slider (0–60 seconds), persisted in `config.json`, and takes effect immediately without a service restart.

### Test Mode

The web UI Settings panel includes a "Test Effects" section that manually triggers any flag state, bypassing the live feed. This is used for testing LED effects without a live race.

---

## Web UI

A dark-themed, mobile-responsive single-page app served as static files by FastAPI. The frontend polls a `/api/state` endpoint every 2 seconds for live data updates (no WebSocket needed for this update frequency).

### Layout

**Navigation bar:** RaceFlag logo (red), firmware version, ⚙ Settings button.

**View toggle:** Two buttons — `🔴 Live Race` and `📊 Standings` — with a note "Switches automatically · Manual override above". The active view is driven by session state from the backend; buttons allow manual override.

### Live Race View

Shown when a session is active (Practice, Qualifying, Sprint, Race).

1. **Flag status banner** — full-width, color-coded by flag state (green/yellow/red/white), shows flag name and last race control message
2. **LED delay slider** — inline control, 0–60 seconds, persisted on change
3. **Session card** — country flag emoji, circuit name, venue, session type, current lap / total laps, time remaining
4. **Weather grid** — air temp, track temp, humidity, wind speed/direction, rain indicator
5. **Race Positions table** (LIVE pill) — columns: POS, team colour bar, driver code + full name, gap to leader, tyre compound badge, pit stop count
6. **Race Control messages** — scrollable log of timestamped race control messages with coloured flag dot indicators

### Standings View

Shown when no session is active.

1. **Next Race card** — country flag emoji, grand prix name, circuit, round number, race date (e.g. "Sunday 15 June 2025"), countdown timer (dd h mm)
2. **Drivers Championship table** — columns: POS (gold/silver/bronze for top 3), team colour bar, full driver name + team sub-line, points
3. **Constructors Championship table** — columns: POS, team colour bar + name, points

### Settings Panel

Slide-up sheet triggered by ⚙ Settings:
- Firmware version (read-only)
- LED count (configurable)
- WiFi network (read-only, shows connected SSID)
- **Update Now** button — checks GitHub Releases and applies update if available
- **Test Effects** — buttons to manually trigger each flag state

---

## Data Sources

### F1 SignalR Feed (live sessions)
Based on the [`f1_sensor`](https://github.com/Nicxe/f1_sensor) project. Provides real-time:
- `TrackStatus` → flag state
- `RaceControlMessages` → race control log
- `SessionInfo` → circuit, session type, year
- `LapCount` → current lap, total laps
- `ExtrapolatedClock` → session time remaining
- `WeatherData` → air/track temp, humidity, wind, rain
- `TimingData` → driver positions, gaps, lap times
- `TimingAppData` → tyre compounds, pit stop counts
- `DriverList` → driver names and three-letter codes

### Jolpica-Ergast REST API (between sessions)
Free public API (the maintained successor to the Ergast API). Polled on startup and refreshed every 4 hours:
- Championship standings (drivers and constructors) — `/api/f1/current/driverStandings`, `/api/f1/current/constructorStandings`
- Race calendar (next race name, circuit, date, round number) — `/api/f1/current.json`

OpenF1 is used by `f1_listener` for any historical session replay (test mode), but does not provide standings or calendar data.

---

## WiFi Management

`wifi_manager.py` monitors network connectivity and falls back to a hotspot when the configured network is unavailable.

**Normal operation:** Connect to the configured WiFi SSID on boot. Monitor connectivity every 30 seconds.

**Hotspot fallback:** If the configured network is unreachable for 60 seconds, switch to AP mode:
- SSID: `RaceFlag-Setup` (no password)
- Pi IP: `192.168.4.1`
- `hostapd` handles the access point; `dnsmasq` provides DHCP and DNS
- All DNS queries redirect to `192.168.4.1` (captive portal)

**Captive portal:** A setup page served at `http://192.168.4.1/setup` by the FastAPI app:
- Scans for nearby WiFi networks
- User selects SSID and enters password
- On save: writes credentials to `config.json`, disables hotspot, reconnects to WiFi

**Reconnection:** If the configured WiFi becomes available again while in hotspot mode, automatically switch back (checked every 2 minutes).

---

## OTA Updates

`ota.py` implements GitHub Releases-based updates triggered from the Settings panel.

**Update flow:**
1. Query `https://api.github.com/repos/prometheusprintingyyc/raceflag/releases/latest`
2. Compare `tag_name` against `version.txt`
3. If newer: download the release `.tar.gz` asset
4. Extract to a staging directory
5. Back up the current install to `raceflag.bak/`
6. Swap in the new version
7. Restart the `raceflag` systemd service via `systemctl restart raceflag`

**Rollback:** If the service fails to start after update (checked after 10 seconds), automatically restore from `raceflag.bak/` and restart.

**UI states:** The Settings panel shows current version, latest available version (fetched on page load), and a button that reads "Up to date" (disabled) or "Update to vX.Y.Z" (enabled).

---

## Installation

A single `install.sh` script handles first-run setup on Raspberry Pi OS Lite (64-bit).

**Steps:**
1. Update system packages
2. Install system dependencies: `python3-pip`, `git`, `hostapd`, `dnsmasq`, `python3-pigpio`
3. Clone the RaceFlag repo to `/opt/raceflag`
4. Install Python dependencies: `pip install -r requirements.txt`
5. Configure GPIO permissions for `rpi_ws281x`
6. Write systemd service file to `/etc/systemd/system/raceflag.service` and enable it
7. Write default `config.json` (LED count: 60, GPIO pin: 18, delay: 0)
8. Copy default `effects/effects.json`
9. Start the service

**One-line install on a fresh Pi:**
```bash
curl -sSL https://github.com/prometheusprintingyyc/raceflag/releases/latest/download/install.sh | bash
```
*(Replace `OWNER` with the actual GitHub username/org once the repo is created.)*

**Multi-unit deployment:** Flash Raspberry Pi OS Lite to each SD card, SSH in, run the install command. WiFi is configured on first boot via the `RaceFlag-Setup` hotspot.

---

## Configuration

`config.json` — persisted on disk, loaded at startup:

```json
{
  "led_count": 60,
  "led_gpio_pin": 18,
  "led_brightness": 128,
  "delay_seconds": 0,
  "wifi_ssid": "",
  "wifi_password": ""
}
```

---

## Key Constraints

- **Platform:** Raspberry Pi Zero 2W — quad-core ARM Cortex-A53, 512MB RAM. Keep dependencies lean; avoid heavy frameworks.
- **Language:** Python 3.11+
- **LED library:** `rpi_ws281x` (requires root or GPIO group membership)
- **Web framework:** FastAPI with uvicorn
- **SignalR client:** `signalrcore` or equivalent async Python client
- **No external database** — all state is in-memory; config persisted in JSON
