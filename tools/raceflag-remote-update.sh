#!/bin/bash
# Updates a RaceFlag unit to a release over SSH, for units whose over-the-air update can't
# work (SD card protection on). Run as root:
#   curl -fsSL <this script's url> | sudo bash -s -- <tag> [<repo>]
# It uses the release's own checks, package download and install script, exactly as the
# unit's updater does. Prints "STEP <name>" markers, then "DONE", or "ERROR <sentence>"
# (exit 1). Nothing outside $WORK_DIR changes before the install step.
set -uo pipefail

CURL="${CURL:-curl}"
PYTHON="${PYTHON:-python3}"
PIP="${PIP:-pip3}"
OVERLAYROOT_CHROOT="${OVERLAYROOT_CHROOT:-overlayroot-chroot}"
PROC_MOUNTS="${PROC_MOUNTS:-/proc/mounts}"
INSTALL="${INSTALL:-/opt/raceflag}"
BOOT_DIR="${BOOT_DIR:-/boot/firmware/raceflag}"
WORK_DIR="${WORK_DIR:-/run/raceflag-update}"
REBOOT="${REBOOT:-systemd-run --on-active=5 systemctl reboot}"
REQUIRE_ROOT="${REQUIRE_ROOT:-1}"
DONE_MARKER="${DONE_MARKER:-/run/raceflag-update.done}"
REBOOT_FALLBACK="${REBOOT_FALLBACK:-reboot}"
export PIP

trap 'rm -rf "$WORK_DIR"' EXIT
# Once started, finish (version, then reboot) even if the SSH client goes away. A failed echo
# to a dead stdout is harmless: nothing here uses set -e.
trap '' PIPE HUP

fail() {
  echo "ERROR $1"
  exit 1
}

main() {
  TAG="${1:-}"
  REPO="${2:-prometheusprintingyyc/raceflag}"

  # --- check ---------------------------------------------------------------------------
  echo "STEP check"
  if [ "$REQUIRE_ROOT" = 1 ] && [ "$(id -u)" != 0 ]; then
    fail "Run this with sudo."
  fi
  if ! [[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    fail "That isn't a RaceFlag release number (expected vX.Y.Z)."
  fi
  if [ ! -d "$INSTALL" ]; then
    fail "This doesn't look like a RaceFlag unit (no /opt/raceflag)."
  fi
  protected=0
  if grep -q overlayroot "$PROC_MOUNTS" 2>/dev/null; then
    protected=1
    if ! command -v "$OVERLAYROOT_CHROOT" >/dev/null 2>&1; then
      fail "SD card protection tools are missing."
    fi
  fi

  # --- download ------------------------------------------------------------------------
  echo "STEP download"
  ARCHIVE="$WORK_DIR/raceflag-$TAG.tar.gz"
  RELEASE_DIR="$WORK_DIR/release"
  WHEELS="$WORK_DIR/wheels"
  rm -rf "$WORK_DIR"
  mkdir -p "$RELEASE_DIR" || fail "Could not download the update. Check the unit's internet connection."
  # $CURL is unquoted on purpose (it may carry arguments).
  if ! $CURL -fsSL --connect-timeout 20 --max-time 600 -o "$ARCHIVE" "https://github.com/$REPO/releases/download/$TAG/raceflag-$TAG.tar.gz"; then
    fail "Could not download the update. Check the unit's internet connection."
  fi

  # --- verify --------------------------------------------------------------------------
  echo "STEP verify"
  if ! tar xzf - -C "$RELEASE_DIR" < "$ARCHIVE" >/dev/null 2>&1; then
    fail "The download is not a RaceFlag release."
  fi
  if ! "$PYTHON" -c '
import sys
sys.path.insert(0, sys.argv[1])
from raceflag.ota_archive import check_archive
check_archive(sys.argv[2])
  ' "$RELEASE_DIR" "$ARCHIVE" >/dev/null 2>&1; then
    fail "The download is not a RaceFlag release."
  fi

  # --- packages ------------------------------------------------------------------------
  echo "STEP packages"
  if ! "$PYTHON" -c '
import asyncio, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from raceflag.ota_deps import fetch_missing
asyncio.run(fetch_missing(Path(sys.argv[2]), Path(sys.argv[3]),
                          pip=os.environ.get("PIP", "pip3")))
  ' "$RELEASE_DIR" "$RELEASE_DIR/requirements.txt" "$WHEELS" >/dev/null 2>&1; then
    fail "Could not download the packages this update needs."
  fi

  # --- install -------------------------------------------------------------------------
  echo "STEP install"
  INSTALL_SH="$RELEASE_DIR/raceflag/ota_install.sh"
  PARENT="$(dirname "$INSTALL")"
  VARS=("INSTALL=$INSTALL" "STAGING=$PARENT/raceflag-staging" "BACKUP=$PARENT/raceflag.bak"
        "WHEELS=$WHEELS" "PIP=$PIP")
  OUT="$WORK_DIR/install.out"
  if [ "$protected" = 1 ]; then
    "$OVERLAYROOT_CHROOT" /usr/bin/env "${VARS[@]}" /bin/bash -c "$(cat "$INSTALL_SH")" \
      < "$ARCHIVE" > "$OUT" 2>&1
  else
    env "${VARS[@]}" bash "$INSTALL_SH" < "$ARCHIVE" > "$OUT" 2>&1
  fi
  last_step="$(grep '^STEP ' "$OUT" | tail -n 1 | cut -c6- | tr -d '\r')"
  # Same rule as the updater: a last "STEP done" is installed even if the wrapper exited non-zero.
  if [ "${last_step:-start}" != "done" ]; then
    tail -n 20 "$OUT" | tr -d '\r' | sed 's/^/LOG /'
    fail "Could not install the update (${last_step:-start}). Nothing was changed."
  fi

  if [ "$protected" = 1 ]; then
    # Tells the unit's SD-card safety net to stand down until the reboot (as the updater does).
    touch "$DONE_MARKER" 2>/dev/null || true
  fi

  # --- version -------------------------------------------------------------------------
  echo "STEP version"
  mkdir -p "$BOOT_DIR" 2>/dev/null
  if ! { echo "${TAG#v}" > "$BOOT_DIR/version.txt.tmp" \
         && mv "$BOOT_DIR/version.txt.tmp" "$BOOT_DIR/version.txt"; }; then
    fail "The update was installed, but its version number couldn't be saved. Restart RaceFlag and run this again."
  fi

  # --- restart -------------------------------------------------------------------------
  echo "STEP restart"
  rm -rf "$WORK_DIR"
  sync
  # $REBOOT and $REBOOT_FALLBACK are unquoted on purpose (commands with arguments).
  if ! $REBOOT >/dev/null 2>&1; then
    if ! $REBOOT_FALLBACK >/dev/null 2>&1; then
      fail "The update was installed, but RaceFlag couldn't restart itself. Unplug it, wait 10 seconds and plug it back in."
    fi
  fi
  echo "DONE" || true
}

# Everything runs from here, so a truncated "curl | bash" download runs nothing.
main "$@"
