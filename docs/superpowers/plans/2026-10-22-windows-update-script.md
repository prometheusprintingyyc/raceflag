# Windows Update Script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A customer double-clicks `Update RaceFlag.cmd` on Windows, types the username and the SSH password once, confirms, and their unit is updated the way the fixed over-the-air updater does it, settings kept.

**Architecture:** A bash script that runs on the unit (`tools/raceflag-remote-update.sh`, fetched by the unit from GitHub at the release tag) reuses the release's own `raceflag/ota_archive.py`, `raceflag/ota_deps.py` and `raceflag/ota_install.sh`. A PowerShell script (`tools/update-raceflag.ps1`, started by `tools/Update RaceFlag.cmd`) checks the unit over HTTP, runs the remote script over one `ssh` connection, and waits for the reboot.

**Tech Stack:** bash + GNU tar + curl + python3 on Raspberry Pi OS Bookworm; Windows PowerShell 5.1 + built-in OpenSSH `ssh.exe`; pytest (bash tests via Git Bash, PowerShell tests via `powershell.exe`).

**Spec:** `docs/superpowers/specs/2026-10-22-windows-update-script-design.md`

## Global Constraints

- No credentials in any file: the username is typed every run (or `-User` for tests); the password is asked by `ssh` only. Nothing is stored except SSH host keys in `%LOCALAPPDATA%\RaceFlag\known_hosts`.
- Release format unchanged; do not touch `.github/workflows/release.yml`, `raceflag.service`, `raceflag/ota.py`, `raceflag/ota_install.sh`, `raceflag/ota_archive.py`, `raceflag/ota_deps.py`. The scripts are not release assets.
- Defaults: address `raceflag.local`, port `8080`, repo `prometheusprintingyyc/raceflag`, release asset `https://github.com/<repo>/releases/download/<tag>/raceflag-<tag>.tar.gz`, remote script `https://raw.githubusercontent.com/<repo>/<tag>/tools/raceflag-remote-update.sh`, latest release `https://api.github.com/repos/<repo>/releases/latest`, tag pattern `^v\d+\.\d+\.\d+$`.
- Remote command (one ssh connection): `curl -fsSL <remote-script-url> | sudo bash -s -- <tag> <repo>`; ssh options `-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=<known_hosts>`.
- Unit-side markers, one per line: `STEP check|download|verify|packages|install|version|restart`, then `DONE`; failures `ERROR <sentence>` and exit 1. Optional `LOG <line>` lines carry the install script's last output on an install failure.
- ERROR sentences, verbatim: "Run this with sudo." · "That isn't a RaceFlag release number (expected vX.Y.Z)." · "This doesn't look like a RaceFlag unit (no /opt/raceflag)." · "SD card protection tools are missing." · "Could not download the update. Check the unit's internet connection." · "The download is not a RaceFlag release." · "Could not download the packages this update needs." · "Could not install the update (<step>). Nothing was changed." · "The update was installed, but its version number couldn't be saved. Restart RaceFlag and run this again."
- Windows messages, verbatim: "Windows' SSH client is missing: Settings > Apps > Optional features > OpenSSH Client." · "Can't reach RaceFlag at <address>. Check it's on and on this network, or run again with its IP address." · "Install <tag> on RaceFlag v<current> at <address>? Its settings are kept. (Y/N)" · "Installed. RaceFlag is restarting - this can take a few minutes. Don't unplug it." · "RaceFlag updated to <tag>. You can close this window." · "RaceFlag hasn't come back yet. Wait a minute, then open its page." · "Dry run: nothing was changed."
- `update-raceflag.ps1` must run on Windows PowerShell 5.1: ASCII only (no em dashes or curly quotes — 5.1 reads BOM-less files as ANSI), no `&&`/`||`/`??`/ternary. `.ps1` and `.cmd` files use CRLF (`.gitattributes`).
- After success: wait up to 15 minutes; the unit must first be seen gone (a failed or timed-out check, 4 s timeout, every 5 s), then answer with `current` equal to the tag without `v`.
- Tests: `PYTHONUTF8=1 python -m pytest tests -q -p no:warnings`; six pre-existing failures are not ours (test_handle_feed_session_status_finished_sets_break, test_handle_feed_session_status_finished_resets_track_status, test_play_snapshot_phase_fires_with_is_snapshot_true, test_set_delay_rejects_above_60, test_test_effect_triggers_led, test_check_connectivity_returns_true_when_ping_succeeds).
- Commits on `beta`; never stage Notes.txt, Notes.txt.bak, .claude/settings.local.json or untracked user files (RaceFlag_User_Guide.html is untracked — don't touch it). Trailers: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01UQ9hM8siPUL1nmzWsUhytk`.

## Review Focus

1. A re-flashed unit at the same address has a new SSH host key: ssh refuses with "REMOTE HOST IDENTIFICATION HAS CHANGED"; the customer must get a plain explanation and a one-key reset, not a wall of ssh text (Task 2 test `ssh output with a changed host key is explained`).
2. The unit can't reach GitHub (Wi-Fi up, internet down) — the remote script must fail at `download` with nothing changed (Task 1 test `test_download_failure_changes_nothing`).
3. The customer runs it against a unit that is already on that version — it must still work (reinstall) and say so, not refuse (Task 2 test: confirmation text when current equals tag).
4. The ssh connection drops after `STEP restart` (the unit rebooting) — the Windows side must treat output that reached `DONE` as success even if ssh's exit code is non-zero (Task 2 test `done marker counts as success despite ssh exit code`).
5. A tag that doesn't exist on GitHub (typo with `-Tag`) — the remote-script fetch fails before anything runs; the Windows side must say the download failed, not "installed" (Task 2 test `no DONE and no ERROR is a failure`).

---

### Task 1: Unit-side script

**Files:**
- Create: `tools/raceflag-remote-update.sh`
- Test: `tests/test_remote_update_script.py`

**Interfaces:**
- Produces: `raceflag-remote-update.sh <tag> [<repo>]` (as root). Environment overrides with real defaults: `CURL=curl`, `PYTHON=python3`, `PIP=pip3`, `OVERLAYROOT_CHROOT=overlayroot-chroot`, `PROC_MOUNTS=/proc/mounts`, `INSTALL=/opt/raceflag`, `BOOT_DIR=/boot/firmware/raceflag`, `WORK_DIR=/run/raceflag-update`, `REBOOT="systemd-run --on-active=5 systemctl reboot"`, `REQUIRE_ROOT=1`. Markers and ERROR sentences as in Global Constraints. Task 2 relies on the marker lines only.

Behaviour, in order:
1. `STEP check`: root check (`id -u` = 0 unless `REQUIRE_ROOT=0`); tag pattern; `$INSTALL` is a directory; protected = `grep -q overlayroot "$PROC_MOUNTS"`; if protected, `command -v "$OVERLAYROOT_CHROOT"` must succeed.
2. `STEP download`: `rm -rf "$WORK_DIR"; mkdir -p "$WORK_DIR/release"`; `$CURL -fsSL -o "$WORK_DIR/raceflag-<tag>.tar.gz" <asset url>`.
3. `STEP verify`: `tar xzf` the archive into `$WORK_DIR/release` (failure = not a release); then `"$PYTHON" -c` with `sys.path.insert(0, <release dir>)` calling `raceflag.ota_archive.check_archive(<archive>)`.
4. `STEP packages`: `"$PYTHON" -c` with the same path calling `asyncio.run(raceflag.ota_deps.fetch_missing(<release>/requirements.txt, <WORK_DIR>/wheels, pip=os.environ.get("PIP", "pip3")))`.
5. `STEP install`: run `<release>/raceflag/ota_install.sh` with the archive on stdin and env `INSTALL`, `STAGING=<dirname INSTALL>/raceflag-staging`, `BACKUP=<dirname INSTALL>/raceflag.bak`, `WHEELS=<WORK_DIR>/wheels`, `PIP` — through `"$OVERLAYROOT_CHROOT" /usr/bin/env <vars> /bin/bash -c "<script text>"` when protected, else `env <vars> bash <script>`. Capture its output; failure unless its last `STEP` line is `done` (same rule as the updater). On failure print up to 20 `LOG ` lines, then the install ERROR with that last step.
6. `STEP version`: write `<tag without v>` to `$BOOT_DIR/version.txt.tmp`, `mv` over `version.txt`.
7. `STEP restart`: `rm -rf "$WORK_DIR"`, `sync`, run `$REBOOT`, print `DONE`.
On any failure before `install` nothing outside `$WORK_DIR` changes; `$WORK_DIR` is removed on every exit (trap).

- [ ] **Step 1: Write the failing tests** — run the real script with Git Bash in a temp dir (skip if `bash` is missing or is WSL's System32 bash, as `tests/test_ota_install_script.py` does). Fixture builds a "release" `.tar.gz` from this checkout's real `raceflag/__init__.py`, `raceflag/ota_archive.py`, `raceflag/ota_deps.py`, `raceflag/ota_install.sh` plus a `raceflag/main.py` containing `NEW` and `requirements.txt`; a fake curl (bash) that copies `$FAKE_RELEASE` to its `-o` path or exits `$FAKE_CURL_RC`; a fake pip that writes `{"install": []}` to any `--report` path, appends its args to a log and exits `$FAKE_PIP_RC` (or `$FAKE_PIP_INSTALL_RC` when `--no-index` is in its args); a fake chroot that logs its args then `exec "$@"`; a fake reboot that logs; `PYTHON` = `sys.executable` as posix; `REQUIRE_ROOT=0`; `PROC_MOUNTS` a temp file. Tests:
  - `test_success_installs_writes_version_and_reboots`: unprotected → `INSTALL/raceflag/main.py` == `NEW`, `BOOT_DIR/version.txt` == `9.9.9` for tag `v9.9.9`, reboot log written, `WORK_DIR` gone, marker lines == `[check, download, verify, packages, install, version, restart]` and last line `DONE`.
  - `test_protected_installs_through_overlayroot_chroot`: `PROC_MOUNTS` contains `overlayroot` → chroot log contains `/usr/bin/env` and `INSTALL=`; install happened.
  - `test_protected_without_chroot_tool_refuses`: `OVERLAYROOT_CHROOT=does-not-exist` → `ERROR SD card protection tools are missing.`, install unchanged (`OLD`).
  - `test_bad_tag_refused_before_download`: tag `latest` → tag ERROR; curl never called.
  - `test_no_install_dir_refused`.
  - `test_not_root_refused`: `REQUIRE_ROOT=1` (the test process isn't root) → `ERROR Run this with sudo.`; skip if `os.geteuid` exists and is 0.
  - `test_download_failure_changes_nothing`: `FAKE_CURL_RC=22` → download ERROR, `OLD`, version.txt unchanged, no reboot.
  - `test_not_a_release`: fake release is `<html>` → `ERROR The download is not a RaceFlag release.`
  - `test_packages_failure`: `FAKE_PIP_RC=1` → packages ERROR, `OLD`.
  - `test_install_failure_reports_step`: `FAKE_PIP_INSTALL_RC=1` → `ERROR Could not install the update (packages). Nothing was changed.`, at least one `LOG ` line, `OLD`, version unchanged.
  Every test also asserts the exit code (0 only on success) and that `WORK_DIR` is removed.
- [ ] **Step 2: Run** `PYTHONUTF8=1 python -m pytest tests/test_remote_update_script.py -q -p no:warnings` — expected: all fail (script missing).
- [ ] **Step 3: Implement `tools/raceflag-remote-update.sh`** per the behaviour list (bash, `set -uo pipefail` with explicit error checks so every failure prints its ERROR sentence; LF line endings — `.gitattributes` already has `*.sh text eol=lf`).
- [ ] **Step 4: Run the tests** — expected: all pass. Then the full suite once.
- [ ] **Step 5: Commit** `tools/raceflag-remote-update.sh tests/test_remote_update_script.py` — "feat: unit-side update script for updates over SSH".

---

### Task 2: Windows side

**Files:**
- Create: `tools/update-raceflag.ps1`, `tools/Update RaceFlag.cmd`, `tools/README.md`
- Modify: `.gitattributes` (add `*.ps1 text eol=crlf` and `*.cmd text eol=crlf`)
- Test: `tests/test_windows_updater.py`

**Interfaces:**
- Consumes: the remote script's marker lines (Task 1).
- Produces: `update-raceflag.ps1 [-Address raceflag.local] [-Port 8080] [-Tag vX.Y.Z] [-Repo prometheusprintingyyc/raceflag] [-User <name>] [-Yes] [-DryRun] [-WaitMinutes 15]`. Exit code 0 on success (or a successful dry run), 1 otherwise. When the environment variable `RACEFLAG_UPDATER_NO_MAIN=1` is set, the script only defines these functions and returns (tests dot-source it):
  - `Get-RemoteCommand -Tag <string> -Repo <string> -> string` — exactly `curl -fsSL https://raw.githubusercontent.com/<repo>/<tag>/tools/raceflag-remote-update.sh | sudo bash -s -- <tag> <repo>`.
  - `Get-SshArguments -User <string> -Address <string> -KnownHosts <string> -Command <string> -> string[]`.
  - `Read-RemoteResult -Lines <string[]> -> hashtable @{ Ok = [bool]; Error = [string] }` — `Ok` when a `DONE` line is present (regardless of ssh's exit code); otherwise `Error` = the first `ERROR ` sentence, or "Could not download the update script. Check the unit's internet connection and the release number." when there is neither `DONE` nor `ERROR`.
  - `Test-HostKeyChanged -Lines <string[]> -> bool` — true when any line contains `REMOTE HOST IDENTIFICATION HAS CHANGED`.
  - `Get-UnitVersion -Address <string> -Port <int> -> string or $null` — `current` from `http://<address>:<port>/api/update/check` (4 s timeout), `$null` when it can't be reached.
  - `Wait-ForRestart -Address <string> -Port <int> -Version <string> -WaitSeconds <int> -PollSeconds <int=5> -> bool` — true once the unit was seen gone and then reports `current` == Version; the main flow passes `-WaitSeconds ($WaitMinutes * 60)`.

Main flow: SSH client check (`Get-Command ssh.exe`) → resolve `-Tag` (default: GitHub latest `tag_name`) and check the tag pattern → `Get-UnitVersion` (unreachable → message, exit 1) → ask the username (unless `-User`) → confirmation line (unless `-Yes`; anything but `Y`/`y` exits 1 with no change) → `-DryRun`: print the ssh command line and "Dry run: nothing was changed.", exit 0 → create `%LOCALAPPDATA%\RaceFlag`, run `ssh.exe` with `Get-SshArguments`, echo `STEP` lines as progress ("Checking the unit...", "Downloading...", etc., one friendly line per step) → `Test-HostKeyChanged` → explain that the unit was probably re-flashed and offer `ssh-keygen -R <address> -f <known_hosts>` (Y/N), then ask to run again → `Read-RemoteResult` → failure: print `Error`, exit 1 → success: the "Installed…" line, `Wait-ForRestart`, then the "updated" or "hasn't come back yet" line.

`Update RaceFlag.cmd`: `@echo off`, `powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-raceflag.ps1" %*`, then `pause`.

`tools/README.md`: for customers — what it does, that the unit must be on and on the same network, they need the username and password from Prometheus Printing, double-click `Update RaceFlag.cmd`, what the messages mean, `-Address <IP>` if `raceflag.local` isn't found.

- [ ] **Step 1: Write the failing tests** (`tests/test_windows_updater.py`, skip unless `shutil.which("powershell")`): run `powershell -NoProfile -ExecutionPolicy Bypass -Command` with `RACEFLAG_UPDATER_NO_MAIN=1`, dot-source the script, call a function and print JSON (`ConvertTo-Json`). Tests:
  - `test_script_parses`: `[System.Management.Automation.Language.Parser]::ParseFile` reports no errors; the file is ASCII only.
  - `test_remote_command`: `Get-RemoteCommand -Tag v9.9.9 -Repo a/b` equals the exact Global Constraints string.
  - `test_ssh_arguments_use_raceflag_known_hosts`: args contain `StrictHostKeyChecking=accept-new`, `UserKnownHostsFile=<given path>` and `user@host`, and the command as the last argument.
  - `test_done_marker_counts_as_success_despite_ssh_exit_code` (Review Focus 4): `Read-RemoteResult` on `STEP check … STEP restart, DONE` → `Ok` true.
  - `test_error_line_is_reported`: lines with `ERROR The download is not a RaceFlag release.` → `Ok` false, that sentence.
  - `test_no_done_and_no_error_is_a_failure` (Review Focus 5).
  - `test_ssh_output_with_a_changed_host_key_is_explained` (Review Focus 1): `Test-HostKeyChanged` true for ssh's real warning text, false otherwise.
  - `test_unit_version_and_unreachable`: a local `http.server` thread answering `/api/update/check` with `{"current": "0.2.34", ...}` → `0.2.34`; a closed port → `$null`.
  - `test_wait_for_restart_needs_the_unit_to_go_away`: fake server answers the new version and stays up → `Wait-ForRestart -WaitSeconds 3 -PollSeconds 1` returns false; a port with no server for the first 2 s, then a server answering the new version → `-WaitSeconds 10 -PollSeconds 1` returns true.
  - `test_dry_run_changes_nothing` (full script, `-Address 127.0.0.1 -Port <fake> -Tag v9.9.9 -User u -Yes -DryRun`): exit 0, output contains the ssh command and "Dry run: nothing was changed.", and the confirmation line text for the case where the unit is already on 9.9.9 (Review Focus 3).
  - `test_cmd_wrapper`: `Update RaceFlag.cmd` contains `-ExecutionPolicy Bypass`, `%~dp0update-raceflag.ps1` and `pause`.
- [ ] **Step 2: Run** `PYTHONUTF8=1 python -m pytest tests/test_windows_updater.py -q -p no:warnings` — expected: all fail (script missing).
- [ ] **Step 3: Implement** the three files and the `.gitattributes` lines (Windows PowerShell 5.1 syntax; `Invoke-RestMethod -TimeoutSec 4`; `& ssh.exe @args 2>&1 | ForEach-Object { ... }` capturing every line).
- [ ] **Step 4: Run the tests** — expected: all pass. Full suite once.
- [ ] **Step 5: Commit** the four files and the test — "feat: Windows script to update a RaceFlag over SSH".

---

### Task 3: Changelog and specs

**Files:**
- Modify: `CHANGELOG.md` ([Unreleased] → Added), `../raceflag_specs/features/011-ota-updates.md` (the existing Pi beta callout)

- [ ] **Step 1: CHANGELOG** bullet: "**Update RaceFlag over SSH from Windows** (beta): `tools/Update RaceFlag.cmd` updates a unit on the same network — it asks for the unit's username, SSH asks for the password once, and the unit installs the release exactly as the Update button does, keeping its settings. For units with SD card protection whose updater can't update them. Nothing is stored; the password is never saved".
- [ ] **Step 2: Spec** — in 011's Pi beta callout add a bullet: "Manual update from Windows (`tools/Update RaceFlag.cmd` + `update-raceflag.ps1`): one SSH connection runs `tools/raceflag-remote-update.sh <tag>` fetched from GitHub at that tag, which reuses the release's own archive check, package download and `ota_install.sh` (through `overlayroot-chroot` when protected), writes `version.txt` and reboots; the PC waits for the unit to go away and come back on the new version. Username typed each run; the password only ever asked by `ssh`." Stage only that file in the specs repo; commit "spec: OTA updates — Pi manual update from Windows over SSH (beta)".
- [ ] **Step 3: Commit** `CHANGELOG.md` — "docs: changelog for the Windows update script".

---

### Task 4 (owner, on hardware): release gate addition

After the branch is reviewed and pushed (owner's go-ahead), on a Windows PC on the test unit's network, with a test release in the test repo: run `Update RaceFlag.cmd -Repo prometheusprintingyyc/raceflag-ota-test -Tag v0.2.92` against the protected test unit → one password prompt, progress lines, "RaceFlag updated to v0.2.92."; settings still there; `journalctl -u raceflag | grep -i "safety net\|only in RAM"` shows no save after the reboot. Also run once with `raceflag.local` and once with the IP.
