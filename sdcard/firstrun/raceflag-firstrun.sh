#!/usr/bin/env bash
# RaceFlag first-boot installer.
# Triggered once by raceflag-firstrun.service after Raspberry Pi Imager sets up
# WiFi credentials. Logs to /boot/firmware/raceflag-install.log so the file is
# readable from any PC if something goes wrong (FAT32 boot partition).

LOG=/boot/firmware/raceflag-install.log
exec > >(tee -a "$LOG") 2>&1

echo "=== RaceFlag First Boot Installer ==="
echo "Started: $(date)"
echo "Hostname: $(hostname)"

# Prevent debconf/dpkg-preconfigure from trying to open a TTY (we have none)
export DEBIAN_FRONTEND=noninteractive
export DEBCONF_NONINTERACTIVE_SEEN=true

# Prevent apt-daily from racing with the installer
systemctl stop apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
systemctl stop apt-daily.service apt-daily-upgrade.service 2>/dev/null || true

# Disable WiFi power management to prevent mid-download corruption
iwconfig wlan0 power off 2>/dev/null || true

# Wait for network (up to 90 seconds)
echo "Waiting for network connectivity..."
CONNECTED=0
for i in $(seq 1 45); do
    if curl -sf --max-time 5 --head https://github.com > /dev/null 2>&1; then
        CONNECTED=1
        echo "Network ready after $((i * 2))s"
        break
    fi
    sleep 2
done

if [ "$CONNECTED" -eq 0 ]; then
    echo "ERROR: No network after 90 seconds."
    echo "Check that WiFi credentials were set correctly in Raspberry Pi Imager."
    systemctl disable raceflag-firstrun.service 2>/dev/null || true
    exit 1
fi

# Disable onboard audio (required for rpi_ws281x on GPIO 18)
sed -i 's/dtparam=audio=on/dtparam=audio=off/' /boot/firmware/config.txt

# Clean apt state for a fresh start
apt-get clean
rm -rf /var/lib/apt/lists/*
rm -f /var/lib/apt/extended_states

# Install RaceFlag — up to 3 attempts
MAX_ATTEMPTS=3
INSTALL_OK=0

for attempt in $(seq 1 $MAX_ATTEMPTS); do
    echo ""
    echo "--- Install attempt $attempt / $MAX_ATTEMPTS ($(date)) ---"

    curl -fsSL https://raw.githubusercontent.com/prometheusprintingyyc/raceflag/main/install.sh \
        -o /tmp/raceflag-install.sh

    if bash /tmp/raceflag-install.sh; then
        INSTALL_OK=1
        break
    fi

    echo "Attempt $attempt failed"
    rm -f /tmp/raceflag-install.sh
    apt-get install -f -y 2>/dev/null || true
    apt-get clean
    rm -rf /var/lib/apt/lists/*
    rm -f /var/lib/apt/extended_states

    if [ $attempt -lt $MAX_ATTEMPTS ]; then
        echo "Retrying in 30 seconds..."
        sleep 30
    fi
done

rm -f /tmp/raceflag-install.sh
systemctl disable raceflag-firstrun.service 2>/dev/null || true

if [ "$INSTALL_OK" -eq 1 ]; then
    # Set LED count for this hardware configuration
    sed -i 's/"led_count": 60/"led_count": 21/' /boot/firmware/raceflag/config.json
    echo ""
    echo "=== Installation complete: $(date) ==="
    echo "Rebooting to activate SD card protection (overlayroot)..."
    reboot
else
    echo ""
    echo "=== ERROR: Installation failed after $MAX_ATTEMPTS attempts: $(date) ==="
    echo "SSH in and review: $LOG"
    exit 1
fi
