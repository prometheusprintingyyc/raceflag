#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/raceflag"
SERVICE_FILE="/etc/systemd/system/raceflag.service"
REPO_URL="https://github.com/prometheusprintingyyc/raceflag"

echo "=== RaceFlag Installer ==="

# 1. System packages
echo "Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq git python3-pip python3-venv hostapd dnsmasq

# 2. rpi_ws281x needs build tools and the library
apt-get install -y -qq python3-dev gcc
pip3 install rpi_ws281x --break-system-packages 2>/dev/null || pip3 install rpi_ws281x

# 3. Unmask hostapd (masked by default on Raspberry Pi OS)
systemctl unmask hostapd

# 4. Clone or update repo
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "Updating existing installation..."
  git -C "$INSTALL_DIR" pull --ff-only
else
  echo "Cloning repository..."
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

# 5. Install Python dependencies
pip3 install -r "$INSTALL_DIR/requirements.txt" --break-system-packages 2>/dev/null \
  || pip3 install -r "$INSTALL_DIR/requirements.txt"

# 6. Create default config if absent
CONFIG="$INSTALL_DIR/config.json"
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

# 7. GPIO group permissions for non-root (optional hardening)
usermod -aG gpio root 2>/dev/null || true

# 8. Install and enable systemd service
cp "$INSTALL_DIR/raceflag.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable raceflag
systemctl restart raceflag

echo ""
echo "=== Installation complete ==="
echo "RaceFlag is running. Access the web UI at http://$(hostname -I | awk '{print $1}'):8080"
echo "If WiFi is not configured, connect to 'RaceFlag-Setup' to set it up."
