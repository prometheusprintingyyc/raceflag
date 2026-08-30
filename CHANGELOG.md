# Changelog

All notable changes to RaceFlag are documented here.

---

## [Unreleased]

### Changed
- overlayroot startup check now logs its result in all cases — `overlayroot active — SD card protection OK` on healthy boots, `overlayroot configured — SD card protection active on next reboot` if configured but not yet active

### Fixed
- Corrected two stale `/boot/raceflag/` path references in `install.sh` comments to `/boot/firmware/raceflag/`

---

### Added
- On every boot, RaceFlag now checks whether overlayroot SD card protection is configured and sets it up automatically if not — units updated from v0.2.21 via the old OTA path (which did not call `_setup_overlayroot`) will activate SD card protection on their first boot of new code without any manual intervention; units that already have overlayroot active skip the check instantly

### Fixed
- OTA on units with overlayroot already active now updates the service file both on the real underlying filesystem (via overlayroot-chroot, persists across reboots) and on the live overlay (so the corrected `RACEFLAG_CONFIG` and `RACEFLAG_VERSION` paths take effect immediately without a reboot)

---

## [v0.2.26] — 2026-08-30

### Fixed
- Service file (`raceflag.service`) now uses `/boot/firmware/raceflag/` for `RACEFLAG_CONFIG` and `RACEFLAG_VERSION` — units installed before v0.2.26 had these hardcoded to `/opt/raceflag/`, causing config and version reads/writes to bypass the boot partition entirely
- OTA overlayroot setup now updates the service file on existing installs when the old paths are detected, so field units get corrected paths on their next OTA without a manual reinstall

---

## [v0.2.25] — 2026-08-30

### Fixed
- Config migration at startup now also runs when `config.json` exists at the new path but has an empty `wifi_ssid` (e.g. leftover default file from a partial setup), overwriting it with the old credentials rather than silently leaving the unit without WiFi
- A default `config.json` is now created at `/boot/firmware/raceflag/` on every startup if no config file exists after migration — ensures the WiFi setup page always has somewhere to persist credentials

---

## [v0.2.24] — 2026-08-30

### Fixed
- Units updated from v0.2.21 via the old OTA path now migrate `config.json` and `version.txt` from `/opt/raceflag/` to `/boot/firmware/raceflag/` on first boot — without this, WiFi credentials were lost after the update and the unit fell into hotspot mode, making it unreachable

---

## [v0.2.23] — 2026-08-30

### Added
- User Manual button in settings panel — opens raceflag.prometheusprinting.ca in a new tab; sits between Shut Down and Send Logs in a neutral grey style

### Changed
- Demo Mode and LED Strip settings now use a sliding segmented ON/OFF control — the active state pill slides between OFF (red highlight) and ON (green highlight) to give a clearer indication of current state

### Fixed
- Driver standings now retry every 60 seconds until both driver and constructor fetches succeed — previously if driver standings failed silently at startup (e.g. DNS not ready yet) but constructor standings succeeded, the loop would sleep 4 hours before retrying, leaving the drivers tab empty until the next cycle
- Boot partition path corrected to `/boot/firmware/raceflag/` for Raspberry Pi OS Bookworm and later, where the FAT32 boot partition is mounted at `/boot/firmware/` instead of `/boot/`; affects `main.py`, `ota.py`, and `install.sh`
- Replaced NM connections symlink approach with a plain directory — the symlink to `/boot/firmware/raceflag/nm-connections/` caused NetworkManager to reject profiles due to FAT32 not supporting the `chmod 600` permissions NM requires; WiFi credentials are now persisted via `config.json` on the boot partition instead (already saved there by `wifi_manager.connect()`)
- Corrected `DEFAULT_PATH` in `config.py` to `/boot/firmware/raceflag/config.json`
- WiFi scan cache now populated before hotspot fallback in all paths — connectivity loss during normal operation, failed connect on boot, and fresh install with no credentials; previously only the hardware reset button path cached available networks, leaving the setup page empty in other scenarios
- Fixed overlayroot activation — `install.sh` and `ota.py` now write to `/etc/overlayroot.local.conf` instead of `/etc/overlayroot.conf` (which is a dpkg-managed conffile that resets on install); `update-initramfs -u` is now run after writing the conf so the initramfs includes the overlayroot hook on next boot

---

## [v0.2.22] — 2026-08-22

### Added
- overlayroot SD card protection — root filesystem is now mounted read-only with a tmpfs overlay on boot, protecting against corruption from unexpected power loss; implemented via the `overlayroot` Debian package (installed automatically by `install.sh` and by OTA on first update); already-deployed units receive protection automatically on the first OTA update without any manual intervention
- Persistent storage partition at `/boot/raceflag/` — config, version file, and NetworkManager WiFi profiles now live on the FAT32 boot partition which remains writable under overlayroot; existing installations are migrated automatically during OTA
- NM connection symlink — `/etc/NetworkManager/system-connections/` is replaced with a symlink to `/boot/raceflag/nm-connections/` so saved WiFi credentials survive reboots with overlayroot active; existing profiles are copied across during migration
- Volatile journald logging — systemd journal now uses RAM storage, eliminating continuous SD card writes from system logs
- OTA overlayroot awareness — `OTAUpdater.apply()` detects whether overlayroot is active and routes accordingly: active units use `overlayroot-chroot` to write new files to the real underlying filesystem; units without overlayroot use the existing direct write path and then enable overlayroot as part of the same update cycle; the enabling update issues a full reboot (rather than a service restart) so protection activates immediately
- Graceful shutdown via button — holding the GPIO reset button for 3 seconds then releasing (before the 10-second WiFi reset threshold) triggers a clean `shutdown -h now`; the red LED feedback at 3 seconds now serves as a dual-purpose confirmation: release to shut down, keep holding to reset WiFi
- Local IP address in web UI — the unit's IP address is now shown beside the live timing feed status (e.g. `Connected / 192.168.1.42`), making it easier to find the device on the local network

---

## [v0.2.21] — 2026-08-03

### Added
- Hardware WiFi reset button — connect a momentary pushbutton between GPIO21 and GND; hold for 10 seconds to clear saved WiFi credentials and enable the RaceFlag-Setup hotspot; LEDs show a red animation after 3 seconds of holding as confirmation, then switch to the white hotspot flash at 10 seconds; scan results are snapshotted before the hotspot starts so the setup page network list is populated after a reset; GPIO pin is configurable via `RACEFLAG_BUTTON_GPIO` env var (default 21); gracefully disables on non-Pi hardware

### Fixed
- Live mode now reliably fires the green race_start animation at lights-out — previously the animation only triggered if TrackStatus changed to AllClear at that exact moment; in most races TrackStatus is already AllClear throughout the formation lap so no TrackStatus event arrives at lights-out and the animation never fired; `SessionStatus "Started"` is now used as the lights-out signal for race/sprint sessions
- Replay now shows the green race_start animation at lights-out instead of the red/green track_clear animation — the race_start promotion was checking `session.is_active` which is `False` before `SessionStatus "Started"` is processed
- Replay Play button now starts instantly on slow hardware (Pi Zero W) — the pre-lights-out snapshot was being processed twice (once at load time, once at play time); eliminated a 10+ second blocking loop on single-core ARMv6
- Checkered flag animation now plays its full 30 seconds even when `SessionStatus "Finished"` arrives immediately after — `set_idle` was clearing `_timed_effect` which cancelled the animation
- WiFi setup connect now waits for NetworkManager to bring wlan0 back up and complete a scan before attempting to join the network — previously nmcli failed immediately with "network not found" causing the hotspot to silently re-enable
- `WiFiManager.stop()` now calls `disable_hotspot()` before exiting — previously if the Pi shut down while the hotspot was active, `nmcli device set wlan0 managed no` would persist across reboots and prevent NetworkManager from reconnecting to WiFi on the next boot
- `WiFiManager.start()` now checks whether wlan0 was unmanaged on entry — if so, it restores NM control and waits 5 s before running connection checks, giving NM time to reconnect after an unclean shutdown that left `managed no` in place; normal boots where wlan0 is already managed are unaffected
- OTA updater pip reinstall now includes piwheels as an extra index URL, matching the initial installer — previously OTA could silently fail to find ARMv6/ARMv7 wheels for packages that aren't on PyPI

---

## [v0.2.20] — 2026-07-25

### Changed
- `requirements.txt` now pins `pydantic<2` and `fastapi<0.100.0` — pydantic v1 is pure Python (no Rust/Cargo required) which fixes installation on ARMv6 (Pi Zero W) where pydantic-core v2 fails to compile; pydantic usage in the codebase is limited to `BaseModel` and `Field(ge=, le=)`, both of which are identical in v1 and v2

### Fixed
- Stopping replay (via Stop button or natural end of playback) now clears the time remaining countdown — `clear_time_remaining` was not being called in either path
- WiFi manager no longer enters hotspot mode when the device has an active connection but `nmcli` fails to return a clean profile name at startup — a routable IP check now gates the hotspot decision before trying stored credentials
- WiFi monitor loop no longer stays stuck in hotspot mode when the device reconnects to a network that differs from `config.json` — the routable IP check now fires regardless of whether a configured SSID is present
- `enable_hotspot` now tells NetworkManager to stop managing wlan0 before starting hostapd, preventing NM from killing the AP seconds after it starts; `disable_hotspot` hands wlan0 back to NM afterward
- Stopping or cancelling replay while a live session is active no longer leaves the frontend countdown stuck on the replay's timer — `_clockBase` (the JS clock reference) was never cleared when `time_remaining` was empty, so the stale replay timestamp kept ticking; it is now nulled out whenever the server reports no time remaining
- Yellow flag (and other continuous animations) no longer get silently discarded when they arrive while the LED delay is active and a timed effect (track_clear, race_start, checkered) is also pending — `trigger_timed` previously called `_flush_queue()` unconditionally when its delayed callback fired, wiping any flag that had queued during the delay window; it now only flushes events that predate the current status, so a yellow flag received 5 s after track_clear correctly cancels the track_clear animation once the configured LED delay expires

---

## [v0.2.19] — 2026-07-21

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
- `ReplayManager` data layer: `get_sessions` fetches Race sessions from F1 livetiming Index.json, `load_session` now downloads all 10 timing streams in parallel (TrackStatus, RaceControlMessages, SessionInfo, SessionStatus, WeatherData, TimingData, TimingAppData, DriverList, LapCount, ExtrapolatedClock), builds a unified chronological event list anchored to lights-out, and replays every event through the same `F1Listener._handle_feed` handler that live mode uses — so weather, driver positions, circuit info, countdown, and lap counts all update identically to a live session
- `_find_lights_out` now uses four cascading detection methods: (1) "RACE STARTED" RC message, (2) `SessionStatus "Started"` — fires exactly at lights-out, (3) `LapCount CurrentLap=1` — also fires at lights-out, (4) first AllClear after a non-clear formation-lap state; methods 2 and 3 reliably catch races like the 2026 Belgian GP that have no "RACE STARTED" message and a formation-lap that is pure AllClear
- Pre-race snapshot phase: all events before lights-out are replayed instantly with `is_snapshot=True` to restore weather, driver positions, and tyre state before playback begins — no LED callbacks fire during the snapshot
- Fixed: replay mode now uses `led.force_trigger` instead of `led.trigger` so the LED hardware bypasses its internal delay queue — previously `led.trigger` still waited `delay_seconds` even though the UI delay was already suppressed in replay mode
- Fixed: pressing Play now fires the race_start green-flash animation and updates the track status display immediately, even when the formation lap was pure AllClear and no new TrackStatus event exists right at lights-out (e.g. Belgian GP) — the last pre-race TrackStatus is re-fired as a live event at the start of playback
- Driver positions, weather, and session info now populate in the UI immediately after a session loads (before Play is pressed) — the pre-lights-out snapshot is applied at load time so the user can verify the correct race is loaded
- Fixed: stopping replay (via Stop button or natural end of playback) now resets the LED to idle, clears the track status to "unknown", and clears accumulated timing data from the listener so stale replay positions don't persist into the next live session
- Switching away from Replay mode while a session is loaded or playing now shows a confirmation dialog ("Cancel Replay? / Keep Watching / Stop Replay") — confirming calls `/api/replay/stop`, resets the LED to idle, and navigates to the target view; cancelling leaves the replay running
- Session time remaining countdown now works in replay mode — `ExtrapolatedClock` stream is downloaded alongside the other 8 timing streams; pausing replay freezes the countdown at the correct remaining time, and resuming restarts it from that same point (preserving the original extrapolating state so red-flag clock freezes are respected)
- `ReplayManager(on_feed=...)` constructor parameter replaces the internal `on_event` callback; `on_feed` receives `(topic, data, is_snapshot)` and is wired to `listener.process_replay_event` in `main.py`
- `F1Listener.process_replay_event(topic, data, is_snapshot)` — new method that calls `_handle_feed` with `_bypass_suspended=True` so replay events reach the handler while the live WebSocket feed is blocked
- Fixed: chequered flag LED callback no longer fires during the live-feed snapshot (initial state restore on connection) — the `is_snapshot` guard was missing from the RaceControlMessages chequered branch
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
