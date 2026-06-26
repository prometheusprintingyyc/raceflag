# Changelog

All notable changes to RaceFlag are documented here.

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
