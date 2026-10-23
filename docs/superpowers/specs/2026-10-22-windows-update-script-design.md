# Windows update script — design

**Date:** 2026-10-22 · **Branch:** beta · **Feature:** 011 OTA updates (Pi), manual path

## Problem

Units already protected by overlayroot can't update over the air (their installed updater fails
before reading the release; see `2026-10-22-ota-overlayroot-fix-design.md`). They need one
update by another route to get the fixed updater. Re-flashing loses settings (Wi-Fi, delay,
F1 login). Every unit has SSH on, with the same credentials, and customers run the update at home
on Windows.

## Goal

A customer double-clicks a file on a Windows 10/11 PC on the same network as the unit, types the
username and (once) the password, confirms, and the unit ends on the chosen release with its
settings kept — installed exactly the way the fixed updater installs, or left unchanged with one
plain sentence saying why.

## Constraints

- **No credentials in any file.** The script asks for the username every run; `ssh` asks for the
  password. Nothing is stored. The repo is public.
- **Release format unchanged** (CLAUDE.md top rule): the script is not a release asset; it lives
  in the repo and is linked from the user guide. It works with releases from the fixed one on
  (they contain the pieces it reuses).
- `sudo` needs no password on units (owner checked: `sudo -n true` prints ok).
- Windows 10/11 built-in OpenSSH client (`ssh.exe`) and Windows PowerShell 5.1; nothing to install.

## Design

### Files (repo `tools/`)

- `Update RaceFlag.cmd` — what the customer double-clicks: runs
  `powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-raceflag.ps1" %*` and pauses at
  the end so the window stays open.
- `update-raceflag.ps1` — the Windows side.
- `raceflag-remote-update.sh` — the unit side (bash, runs as root through `sudo`). The PC never
  sends it: the unit downloads it from GitHub **at the release tag being installed**, so it always
  matches that release.

### Windows side (`update-raceflag.ps1`)

1. Parameters: `-Address` (default `raceflag.local`), `-Tag` (default: the latest release from
   `https://api.github.com/repos/prometheusprintingyyc/raceflag/releases/latest`; a tag such as
   `v0.2.35` also installs a release marked pre-release), `-DryRun` (checks and shows, changes
   nothing), `-Repo` (default `prometheusprintingyyc/raceflag`; for testing against a test repo).
2. Checks `ssh.exe` exists ("Windows' SSH client is missing: Settings → Apps → Optional features →
   OpenSSH Client").
3. Reads `http://<address>:8080/api/update/check` (no password): shows the unit's version. If it
   doesn't answer: "Can't reach RaceFlag at <address>. Check it's on and on this network, or run
   again with its IP address."
4. Asks for the username (required, not stored). Shows "Install v<tag> on RaceFlag v<current> at
   <address>? Its settings are kept. (Y/N)".
5. One `ssh` connection (so one password prompt):
   `ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="%LOCALAPPDATA%\RaceFlag\known_hosts"
   <user>@<address> "curl -fsSL https://raw.githubusercontent.com/prometheusprintingyyc/raceflag/<tag>/tools/raceflag-remote-update.sh | sudo bash -s -- <tag>"`.
   A separate known_hosts file keeps the user's own SSH setup untouched; if a unit was re-flashed
   (new host key) the script explains and offers to forget the old key for that address.
6. Streams the unit's `STEP <name>` / `ERROR <sentence>` lines as progress. On failure it prints
   the sentence and stops.
7. On success: "Installed. RaceFlag is restarting — this can take a few minutes. Don't unplug it.",
   then polls the check URL every 5 s (4 s timeout each): first it must see the unit go away, then
   answer again with the new version, up to 15 min. Then "RaceFlag updated to v<tag>. You can
   close this window." or "RaceFlag hasn't come back yet. Wait a minute, then open its page."

### Unit side (`raceflag-remote-update.sh <tag>`, as root)

Reuses the release's own code so the result is identical to an over-the-air update:

1. `STEP check` — `<tag>` is `vX.Y.Z`; `/opt/raceflag` exists; overlayroot active? (then
   `overlayroot-chroot` must exist).
2. `STEP download` — clean `/run/raceflag-update/`; download the release asset
   `raceflag-<tag>.tar.gz` (GitHub `releases/download/<tag>/…`) into it; extract a copy to
   `/run/raceflag-update/release/` (for its Python modules only).
3. `STEP verify` — `python3` with `PYTHONPATH` = that copy: `raceflag.ota_archive.check_archive`.
4. `STEP packages` — same copy: `raceflag.ota_deps.fetch_missing(requirements, wheels)` (outside
   the chroot, network available).
5. `STEP install` — the release's own `raceflag/ota_install.sh` with the archive on stdin: inside
   `overlayroot-chroot` when protected (same env as the updater), directly otherwise. `STEP done`
   with a non-zero wrapper exit counts as installed (same rule as the updater).
6. `STEP version` — write `/boot/firmware/raceflag/version.txt` (`<tag>` without `v`) atomically.
7. `STEP restart` — remove `/run/raceflag-update/`, `sync`, schedule a reboot in 5 s
   (`systemd-run --on-active=5 systemctl reboot`) so the SSH session ends cleanly; print `DONE`.

Every failure prints `ERROR <plain sentence>` (e.g. "Could not download the update. Check the
unit's internet connection." / "The download is not a RaceFlag release." / "Could not install the
update (<step>). Nothing was changed.") and exits non-zero; before `install` nothing on the SD
card changes. Commands and paths come from environment variables with the real defaults (`CURL`,
`PYTHON`, `OVERLAYROOT_CHROOT`, `PROC_MOUNTS`, `INSTALL`, `BOOT_DIR`, `WORK_DIR`, `REBOOT`) so
tests can run it with fakes.

## Testing

- **Unit-side script, for real** (bash in temp dirs, fakes for curl/chroot/reboot, the real
  `ota_install.sh` and Python modules from this checkout as the "release"): success path, bad tag,
  download failure, not a release, packages failure, install failure — each leaves the install
  unchanged and prints the right `ERROR`; protected vs unprotected branch; version.txt written only
  on success.
- **Windows side:** parse check (`[System.Management.Automation.Language.Parser]::ParseFile`) and
  `-DryRun` against a local fake check URL; `.cmd` runs it from a folder with spaces.
- **Hardware (release gate):** run it from a Windows PC against the protected test unit with a test
  release (`-Tag`, test repo via `-Repo` for testing only): settings kept, new version, safety net
  quiet, reboot.

## Out of scope

Storing credentials; per-unit passwords or SSH keys (a later project); macOS/Linux versions (the
unit-side script would work from any SSH client; only the wrapper is Windows-specific).
