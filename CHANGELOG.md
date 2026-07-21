# Changelog

All notable changes to RaceFlag are documented here.

---

## [Unreleased]

### Added
- Replay Mode — select any completed 2025 F1 race session from the F1 livetiming archive, press Play at lights out, and the LED strip reacts to flag events identically to live mode
- LED delay is bypassed in Replay mode — flag events fire immediately since the replay engine already handles timing
- Debug logging in replay engine shows loaded event count, lights-out detection offset, and per-event schedule timing
- Sprint races now appear in the Replay session list labelled with `(Sprint)` — previously they appeared as unlabelled duplicates alongside the main race for the same Grand Prix weekend
- Sync Offset slider replaces LED Delay when in Replay mode (±30 s range, centred at 0); LED Delay is restored on return to Live mode
- Pause and Resume replay without losing sync — pause both TV broadcast and RaceFlag simultaneously
- REPLAY pill appears on the Session section title while a replay is active
- Six `/api/replay/*` endpoints (`GET sessions`, `POST load/play/pause/resume/stop/offset`) gated on `replay_manager` presence in `create_app`; `create_app` gains optional `replay_manager`, `listener`, and `on_replay_event` params; 7 new tests
- `ReplayManager` playback engine: `play`, `pause`, `resume`, `stop`, `set_sync_offset` — pause/resume uses wall-clock origin shifting so the replay position stays frozen during a pause; sync offset clamped to ±30 s; 6 new async/sync tests
- `ReplayManager` data layer: `get_sessions` fetches Race sessions from F1 livetiming Index.json, `load_session` downloads TrackStatus + RaceControlMessages streams and builds a sorted `_events` list anchored to lights-out, `_find_lights_out` detects race start via "RACE STARTED" RC message with a ≥300s formation-lap gap fallback
- Password show/hide toggle on the WiFi setup page password fields
- LED Strip on/off toggle in Settings — darkens the LED strip immediately while keeping the app and web UI active; hotspot setup mode always shows regardless of toggle state

### Fixed
- Setup hotspot no longer activates when the device already has an active WiFi connection — startup now always checks NM first via a local nmcli query; if NM is connected the hotspot is skipped without calling `nmcli device wifi connect` (which errors when the interface is already on that network and was triggering the hotspot)
- Ongoing connectivity monitoring now uses IP address detection instead of ICMP ping — prevents false "WiFi lost" triggers on corporate/enterprise networks that block outbound ping, which previously caused the setup hotspot to re-enable every 5 minutes even on a healthy connection
- Hotspot's own IP (192.168.4.1) is now excluded from the routable-address check — prevents the monitor loop from counting the hotspot itself as a "real" internet connection
- When an existing NM connection is adopted, the active WiFi SSID and password are read from the NetworkManager profile and written to config.json, so subsequent restarts use the normal configured path
- WiFiManager now logs at startup so the configured SSID and startup path are always visible in the journal
- Wrong password during WiFi setup no longer leaves the device in a dark period — the setup hotspot re-enables within 35 seconds (previously up to 2 minutes) and the LED strip resumes flashing white
- WiFi connectivity monitoring now tolerates up to 5 minutes of outage before re-enabling the setup hotspot, preventing false triggers during router reboots (previously 60 seconds)
- Repeated wrong-password auto-retries in the monitor loop stop after 3 consecutive failures — saved credentials are cleared so the device stays in setup mode cleanly

### Changed
- WiFi setup connecting state now shows a 45-second countdown bar instead of a spinner; the LED strip is the authoritative success/failure signal

---

## [v0.2.18] — 2026-07-17

### Added
- `GET /api/logs` endpoint — runs `journalctl -u raceflag -n 150 --no-pager` and returns `{ "lines": str, "timestamp": str }`; falls back to a descriptive message when journalctl is unavailable (Docker / non-systemd)
- Send Logs button in Settings — fetches the last 150 lines of the systemd journal and opens a pre-addressed mailto: link so the user can email diagnostic logs in one tap
- Direct unit tests for `_fetch_logs()` covering the success path, non-zero exit code, and `FileNotFoundError` (journalctl not installed)

### Fixed
- Send Logs mailto: body capped at 2,000 characters (last N chars kept, truncation notice prepended) to prevent mail client failure on mobile

### Changed
- Weather section replaced with a single divided-cell panel instead of five individual cards
- View toggle uses a sliding pill indicator instead of a background colour swap on the active button
- Nav logo uses an inline SVG checkered flag instead of the 🏁 emoji — renders consistently across all platforms
- `font-variant-numeric: tabular-nums` applied to all numeric fields (gaps, lap times, positions, points, countdown, timestamps) so digits align in columns

---

## [v0.2.17] — 2026-06-28

### Fixed
- Next race countdown now shows time until the actual race start (in UTC from the F1 calendar API) instead of time until midnight on race day
- Test effect buttons (Yellow Flag, Safety Car, VSC, Red Flag) now trigger immediately regardless of the configured LED delay — the delay is only for live F1 feed events
- Lap counter now shows laps remaining (e.g. 27/71) instead of laps completed
- Checkered flag LED now triggers when the Race Control message text contains "Checkered" or "Chequered", not only when the Flag field is set — fixes missed triggers where the flag field was empty

### Added
- `WIFI_ENABLED=0` env var to disable the WiFi manager (set by default in the Docker image) — keeps demo mode and WiFi management as independent settings

---

## [v0.2.16] — 2026-06-27

### Fixed
- Driver and constructor standings now retry after 60 seconds if the first fetch fails (previously waited 4 hours, which meant standings were empty if WiFi wasn't ready at startup)
- LEDs no longer replay the last flag state on service restart — the initial state snapshot from the F1 feed no longer triggers LED callbacks, so LEDs start in idle

---

## [v0.2.15] — 2026-06-27

### Fixed
- Hotspot now assigns IP addresses to clients — dnsmasq is restarted (not just started) so it picks up the DHCP config written by RaceFlag at hotspot enable time
- WiFi setup page now offers manual SSID entry when network scan returns no results (single WiFi chip cannot scan while broadcasting the setup hotspot)

### Added
- LEDs flash white at 1 Hz while the RaceFlag-Setup hotspot is active, providing physical feedback that the device is in setup mode

---

## [v0.2.14] — 2026-06-27

### Changed
- Removed redundant "Shut Down Pi" label from the shutdown button row in Settings

### Fixed
- RaceFlag-Setup hotspot now starts immediately on boot when no WiFi is configured, instead of waiting for two ping timeouts (which could reset if the service restarted)

---

## [v0.2.13] — 2026-06-26

### Added
- Shut Down button in the Settings panel — safely halts the Raspberry Pi via `shutdown -h now` with a browser confirmation dialog

### Fixed
- Continuous LED animations (yellow flag, red flag, safety car, VSC) now return to idle when a session ends instead of staying stuck indefinitely
- Stray flag messages arriving after a session is marked inactive are ignored, preventing LEDs from re-triggering after a session ends

---

## [v0.2.12] — 2026-06-26

### Fixed
- OTA updates now preserve `config.json` (delay, LED settings, WiFi credentials are no longer reset after an update)

### Changed
- Maximum LED delay increased from 60 s to 90 s

### Added
- Debug logging for flag change events and LED delay queue (visible via `sudo journalctl -u raceflag -f`)

### Docs
- Added "Preparing to Ship a Unit" section to INSTALL.md covering WiFi credential cleanup before shipping units

---

## [v0.2.11] — 2026-06-18

### Added
- First-boot WiFi setup UI — when no WiFi is configured the Pi broadcasts a `RaceFlag-Setup` hotspot; connecting to it opens a setup page at `http://192.168.4.1:8080` to scan and connect to a home network
- Checkered flag favicon on all web UI pages
- Raspberry Pi installation guide (INSTALL.md)

---

## [v0.2.10] — 2026-06-07

### Fixed
- Race positions now show gap to the car ahead instead of gap to leader
- Remaining lap count is shown correctly during race sessions
- Session ending with "Finished" or "Finalised" now shows FINISHED instead of BREAK

---

## [v0.2.9] — 2026-06-05

### Added
- Segment dividers on the virtual LED strip display
- Live F1 timing feed connection status indicator replaces the WiFi network row

### Fixed
- Checkered flag LEDs now trigger when P1 crosses the finish line (via Race Control messages)
- Delay slider value is restored on page load
- BREAK state is cleared correctly when a new qualifying segment starts

---

## [v0.2.8] — 2026-05-30

### Added
- Virtual LED strip panel in the web UI (shows live LED colours without hardware)
- Demo mode toggle in Settings
- Docker support

---

## [v0.2.7] — 2026-05-28

### Fixed
- Flushing the delay queue in `trigger_timed` and `set_idle` to prevent stale events cancelling transitions

---

## [v0.2.6] — 2026-05-27

### Fixed
- Country flag emoji rendering with correct font-family fallbacks
- Ergast country name aliases for circuits with non-standard names
- Version number and update notice now shown in nav bar

### Changed
- Wave animations use half-rectified sine for more visible effect and faster speed
- Track Clear and Checkered test effect buttons use `trigger_timed` for correct animated version

---

## [v0.2.5] — 2026-05-26

### Added
- Continuous LED animations for each flag state:
  - Red flag — rolling red wave
  - Yellow flag — rolling yellow wave
  - Safety Car — alternating yellow segments
  - Virtual Safety Car — full strip yellow flash
  - Checkered — rolling white wave for 30 s then idle
  - Track Clear — alternating green/red for 30 s then idle
- Race Start green flash animation (30 s) triggered on first Track Clear of a Race or Sprint session
- Race Start and Idle buttons in the test effects panel

---

## [v0.2.4] — 2026-05-24

### Added
- Idle chase animation — red on segments 1 & 2, white on segment 3
- Idle button in the test effects panel
- LED layout updated to 21 LEDs (segment 1: 11, segment 2: 6, segment 3: 4)

---

## [v0.2.3] — 2026-05-23

### Added
- Live countdown timer for time remaining in session
- Delay slider persisted in UI and applied to track status display

### Fixed
- Browser cache busted on OTA update via versioned JS/CSS URLs

---

## [v0.2.2] — 2026-05-22

### Fixed
- Session status "Finished" vs "Finalised" handling

---

## [v0.2.1] — 2026-05-21

### Added
- Idle LED breathing animation when no active session
- BREAK state between qualifying segments (distinct from FINISHED)
- Last Lap column in the race positions table

---

## [v0.2.0] — 2026-05-20

### Fixed
- Driver positions now accumulate across incremental timing updates (all drivers shown)
- Removed OPTIONS preflight request that always returned 405

---

## [v0.1.x] — Initial releases

Early releases establishing the core architecture: SignalR live timing feed, FastAPI web server, LED controller with delay queue, OTA updater, WiFi manager with hotspot fallback, and the web UI frontend.
