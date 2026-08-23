#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/raceflag"
BOOT_DIR="/boot/firmware/raceflag"
SERVICE_FILE="/etc/systemd/system/raceflag.service"
REPO_URL="https://github.com/prometheusprintingyyc/raceflag"

echo "=== RaceFlag Installer ==="

# 1. System packages
echo "Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq git python3-pip python3-venv hostapd dnsmasq overlayroot

# 2. rpi_ws281x needs build tools and the library
apt-get install -y -qq python3-dev gcc
pip3 install rpi_ws281x \
  --extra-index-url https://www.piwheels.org/simple/ \
  --break-system-packages

# 3. Unmask hostapd (masked by default on Raspberry Pi OS)
systemctl unmask hostapd

# 4. Clone or update repo, then check out the latest release tag
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "Updating existing installation..."
  git -C "$INSTALL_DIR" fetch --tags
else
  echo "Cloning repository..."
  git clone "$REPO_URL" "$INSTALL_DIR"
  git -C "$INSTALL_DIR" fetch --tags
fi

LATEST_TAG=$(git -C "$INSTALL_DIR" describe --tags "$(git -C "$INSTALL_DIR" rev-list --tags --max-count=1)" 2>/dev/null || true)
if [ -n "$LATEST_TAG" ]; then
  echo "Checking out latest release: $LATEST_TAG"
  git -C "$INSTALL_DIR" checkout "$LATEST_TAG"
fi

# 5. Install Python dependencies
# piwheels provides pre-compiled ARMv6/ARMv7 wheels (e.g. pydantic-core) that aren't on PyPI
pip3 install -r "$INSTALL_DIR/requirements.txt" \
  --extra-index-url https://www.piwheels.org/simple/ \
  --break-system-packages

# 6. Set up /boot/raceflag/ as persistent storage for config and version.
#    This directory lives on the FAT32 boot partition, which remains writable even
#    when overlayroot makes the root filesystem read-only.
mkdir -p "$BOOT_DIR"

# Write version file to boot partition
if [ -n "$LATEST_TAG" ]; then
  echo "${LATEST_TAG#v}" > "$BOOT_DIR/version.txt"
fi

# Create default config if absent
CONFIG="$BOOT_DIR/config.json"
if [ ! -f "$CONFIG" ]; then
  cat > "$CONFIG" <<EOF
{
  "led_count": 60,
  "led_gpio_pin": 18,
  "led_brightness": 128,
  "delay_seconds": 0.0,
  "wifi_ssid": "",
  "wifi_password": ""
}
EOF
  echo "Created default config at $CONFIG"
fi

# 7. Ensure NM connections directory exists (WiFi credentials are stored in
#    config.json on the boot partition and applied by the app on startup).
mkdir -p /etc/NetworkManager/system-connections
chmod 700 /etc/NetworkManager/system-connections

# Remove any legacy symlink from the previous NM migration approach.
if [ -L /etc/NetworkManager/system-connections ]; then
  rm /etc/NetworkManager/system-connections
  mkdir -p /etc/NetworkManager/system-connections
  chmod 700 /etc/NetworkManager/system-connections
  echo "Removed legacy NM symlink"
fi

# 8. Move systemd journal logs to RAM to reduce SD card writes.
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/raceflag-volatile.conf <<EOF
[Journal]
Storage=volatile
EOF

# 9. Configure overlayroot to protect the root filesystem from SD card corruption.
#    Root becomes read-only with a tmpfs overlay; writes are lost on reboot unless
#    they target /boot/raceflag/ or go through overlayroot-chroot (used by OTA).
if [ ! -f /etc/overlayroot.conf ]; then
  echo 'overlayroot="tmpfs"' > /etc/overlayroot.conf
  echo 'overlayroot_cfgdisk="disabled"' >> /etc/overlayroot.conf
  echo "overlayroot configured — will activate on next reboot"
fi

# 10. GPIO group permissions for non-root (optional hardening)
usermod -aG gpio root 2>/dev/null || true

# 11. Install and enable systemd service
cp "$INSTALL_DIR/raceflag.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable raceflag
systemctl restart raceflag

echo ""
echo "=== Installation complete ==="
echo "RaceFlag is running. Access the web UI at http://$(hostname -I | awk '{print $1}'):8080"
echo "If WiFi is not configured, connect to 'RaceFlag-Setup' to set it up."
echo ""
echo "NOTE: SD card protection (overlayroot) will activate on the next reboot."
echo "      Reboot now with: sudo reboot"
