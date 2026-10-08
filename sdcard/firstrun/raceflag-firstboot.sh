#!/bin/bash
# Runs once on Boot 2 (after Pi Imager's firstrun.sh has already applied WiFi
# credentials to the real filesystem and rebooted). Creates overlayroot.local.conf
# so overlayroot activates on Boot 3.
#
# No update-initramfs needed — the overlayroot initramfs hooks were already baked
# into the image by 'apt-get install overlayroot' during the CI build.

LOG=/boot/firmware/raceflag/firstboot.log
exec >> "$LOG" 2>&1

echo "=== RaceFlag First Boot Setup ==="
echo "$(date)"

echo 'overlayroot="tmpfs"' > /etc/overlayroot.local.conf || {
  echo "ERROR: failed to write /etc/overlayroot.local.conf — check filesystem"
  exit 1
}
echo "overlayroot configured."

touch /boot/firmware/raceflag/.firstboot-done || true
systemctl disable raceflag-firstboot.service || true

echo "Rebooting to activate overlayroot..."
reboot
