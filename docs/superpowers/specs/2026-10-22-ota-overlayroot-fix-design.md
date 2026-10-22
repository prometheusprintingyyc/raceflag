# OTA updates on overlayroot units — design

**Date:** 2026-10-22 · **Branch:** beta · **Feature:** 011 OTA updates (Pi)

## Problem

On a unit with overlayroot active, every OTA update fails, and some units can be silently
downgraded by the failure:

- The updater saves the release to `/boot/firmware/raceflag/` and extracts it inside
  `overlayroot-chroot`. That chroot only bind-mounts `/proc`, `/run` and `/sys` (confirmed on the
  test unit, `overlayroot-chroot` line 72), so `/boot/firmware` there is an empty mount point and
  `tar` fails.
- The failure path then copies `/opt/raceflag.bak` over the install if one exists. On units that
  took a direct (pre-overlayroot) OTA, that backup is an older version: it runs after the next
  restart while `version.txt` shows the newer one.
- `pip3 install` inside the chroot has no DNS (the lower filesystem's `/etc/resolv.conf` is a
  30-byte NetworkManager header with no nameserver), and its errors are ignored (`|| true`), so a
  release that adds a dependency would install without it.
- The page ignores the apply result and reloads after ~2 minutes still offering the update.

Deployed units can't be fixed over the air (the broken step runs from their installed code).
Affected units are re-flashed with the SD card image built from the fixed release (owner's
decision); the image's `install.sh` never creates `/opt/raceflag.bak`.

## Goal

A unit running the fixed updater installs any later release through the page's Update button,
with overlayroot active, and either ends on the new version or stays exactly on its current
version with a reason shown. A failed update never installs older code.

## Constraints (CLAUDE.md top rule)

- The release format does not change: numeric `vX.Y.Z` tags, one `.tar.gz` asset with
  `raceflag/`, `requirements.txt`, `version.txt`, `install.sh`, `raceflag.service` at its top
  level, GitHub "latest release". Old updaters keep seeing releases exactly as today.
- `POST /api/update/apply` keeps `success`; the reason is an added field.
- New code must start without any newly added package.
- Release gate: verified on a unit with overlayroot active before tagging (see Testing).

## Design

### 1. Download into RAM

Both paths download the asset to `/run/raceflag-update/raceflag-<tag>.tar.gz` (tmpfs; the
directory is emptied first and removed when the attempt ends, success or failure). Nothing is
written to the boot partition.

Before anything is installed, the archive is checked in Python: it opens as a gzip tar, no member
escapes the extraction directory (absolute paths, `..`, links pointing outside), and it contains
`requirements.txt` and `raceflag/main.py`. Otherwise: "The download is not a RaceFlag release."

### 2. Dependencies (overlayroot path)

Outside the chroot (network available):

1. `pip3 install --dry-run --report <file> -r requirements.txt --break-system-packages
   --extra-index-url https://www.piwheels.org/simple/ -q`. The running system's packages are the
   same as the lower filesystem's, so the report's `install` list is what the update needs.
2. If the list is empty, skip to the chroot. Otherwise
   `pip3 download --no-deps -d /run/raceflag-update/wheels <name>==<version> …` with the same
   index options.
3. Either command failing ends the update: "Could not download the packages this update needs."

Inside the chroot, `pip3 install --no-index --find-links /run/raceflag-update/wheels -r
<staging>/requirements.txt --break-system-packages` (already-installed requirements are satisfied
without files). Its failure is an update failure, before the code is swapped.

### 3. One chroot script, archive on stdin

`overlayroot-chroot /bin/bash -c <script>` with the archive on stdin. Paths come from variables at
the top of the script (`INSTALL`, `STAGING`, `BACKUP`, `WHEELS`, `PIP`) so the same script runs
in tests. `set -e`; each step prints a marker line (`STEP extract` …) so Python can name the step
that failed:

1. `rm -rf $STAGING && mkdir -p $STAGING && tar xzf - -C $STAGING`
2. `test -f $STAGING/requirements.txt && test -f $STAGING/raceflag/main.py`
3. offline pip install (above)
4. swap by rename — the current code becomes the backup made by **this** attempt:
   `rm -rf $BACKUP; mv $INSTALL $BACKUP; mv $STAGING $INSTALL || { mv $BACKUP $INSTALL; exit 1; }`
5. the existing service-file path fix (`sed … raceflag.service || true`)
6. `sync`, then `echo STEP done`

Failure before step 4 leaves the install untouched (a stale backup is never restored).
`/opt/raceflag.bak` after success is the previous version (kept for the future "won't start"
watchdog to-do; only the attempt that made it ever restores it).

**Timeout** (300 s, unchanged): the chroot process is killed, then a recovery chroot runs
`[ -d $INSTALL ] || { [ -d $BACKUP ] && mv $BACKUP $INSTALL; }` — only possible to need if the kill
landed between the two renames. Reason: "The update took too long and was cancelled."

### 4. After the chroot

Success: write `version.txt`, then the existing deferred service restart (overlay service-file
patch + `systemctl restart raceflag`). Failure: `version.txt` untouched, no restart.

### 5. Direct path (overlayroot not active)

Unchanged except: downloads to `/run/raceflag-update/`, uses the same archive check, and treats a
pip failure in `_deferred_restart` as logged-only as today (that path has network and writes the
real disk). The existing restore on failure uses the backup made by the same attempt (already so).

### 6. Result and page

`OTAUpdater.apply()` returns `(ok: bool, reason: str | None)`; `POST /api/update/apply` answers
`{"success": ok, "detail": reason}` (`detail` is `null` on success). Reasons (plain language):

- "Already up to date." · "The release has no update file." · "Could not download the update.
  Check the connection." · "The download is not a RaceFlag release." · "Could not download the
  packages this update needs." · "Could not install the update (<step>). Nothing was changed." ·
  "The update took too long and was cancelled." · "SD card protection tools are missing."

The page: on `success: false` it shows the reason under the Update button and re-enables it
(no polling); on success it polls as today.

### 7. Start-up clean-up

At start, delete `/boot/firmware/raceflag/raceflag-*.tar.gz` (left by failed attempts of the old
updater) and `/run/raceflag-update/`. Errors are logged and ignored.

## Releasing the first fixed version

Re-flash the two units that took a direct OTA before overlayroot (they hold an older backup).
Until they are re-flashed their owners shouldn't tap Update; if that can't be relied on, mark the
first fixed GitHub release as a pre-release by hand (old updaters only see "latest"; the SD image
build is unaffected). No code depends on that choice.

## Testing

- **Unit tests** (mocked network/subprocess): archive check (good, missing files, traversal,
  links); dependency planning from a `--report` JSON (none needed, some needed, pip failing);
  apply on each path returns the right reason and never writes `version.txt` on failure;
  timeout runs the recovery; start-up clean-up; route answers `detail`.
- **Script tests** (bash in a temp directory, fake `pip`): success swaps and keeps the previous
  version as backup; a bad archive, a missing file or pip failing leaves the install and an old
  backup untouched; a failing `mv` restores the install.
- **Hardware, the release gate** on the test unit, overlayroot active, `RACEFLAG_REPO` pointed at
  a public test repo by a persistent drop-in: (1) install the fixed beta by SSH; (2) publish a
  higher test release whose `requirements.txt` adds a small new package; (3) Update button →
  new version; reboot → still new version, new code, package present; (4) a test release with a
  broken archive → reason shown, install unchanged after reboot; (5) the direct path once with
  overlayroot disabled. Only then is a real release tagged.

## Out of scope

The "update installed but won't start" watchdog (separate to-do); updating the OS; releases from
other repos.
