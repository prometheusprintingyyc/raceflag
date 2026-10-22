#!/bin/bash
# Installs a RaceFlag release from the .tar.gz on stdin (feature 011). Run inside
# overlayroot-chroot so it writes the real SD card; the chroot can't see /boot/firmware,
# so the archive comes on stdin and package files come from $WHEELS under /run.
# Prints "STEP <name>" before each step; the updater reports the last one on failure.
# A failure before "swap" changes nothing; a failed swap puts the current code back.
set -euo pipefail

INSTALL="${INSTALL:-/opt/raceflag}"
STAGING="${STAGING:-/opt/raceflag-staging}"
BACKUP="${BACKUP:-/opt/raceflag.bak}"
WHEELS="${WHEELS:-/run/raceflag-update/wheels}"
PIP="${PIP:-pip3}"
SERVICE="${SERVICE:-/etc/systemd/system/raceflag.service}"

echo "STEP extract"
rm -rf "$STAGING"
mkdir -p "$STAGING"
tar xzf - -C "$STAGING"

echo "STEP check"
test -f "$STAGING/requirements.txt"
test -f "$STAGING/raceflag/main.py"

echo "STEP packages"
mkdir -p "$WHEELS"
# Offline: requirements already installed are satisfied; new ones come from $WHEELS.
$PIP install --no-index --find-links "$WHEELS" -r "$STAGING/requirements.txt" \
  --break-system-packages -q

echo "STEP swap"
# The backup is always this attempt's: the code that is running now.
rm -rf "$BACKUP"
mv "$INSTALL" "$BACKUP"
if ! mv "$STAGING" "$INSTALL"; then
  mv "$BACKUP" "$INSTALL"
  exit 1
fi

echo "STEP service"
if [ -f "$SERVICE" ]; then
  sed -i \
    's|RACEFLAG_CONFIG=/opt/raceflag/config.json|RACEFLAG_CONFIG=/boot/firmware/raceflag/config.json|g; s|RACEFLAG_VERSION=/opt/raceflag/version.txt|RACEFLAG_VERSION=/boot/firmware/raceflag/version.txt|g' \
    "$SERVICE" || true
fi
sync || true

echo "STEP done"
