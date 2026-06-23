# RaceFlag — Raspberry Pi Installation Guide

## What You Need

**Hardware**
- Raspberry Pi Zero 2W (or Zero 1 W)
- MicroSD card (8GB minimum, Class 10 recommended)
- WS2812B LED strip
- 5V power supply rated for your LED strip (5V 2A minimum for up to ~60 LEDs)
- Three jumper wires (power, ground, data)

**Software**
- [Raspberry Pi Imager](https://www.raspberrypi.com/software/) (on your PC/Mac)

---

## Step 1 — Wire the LED Strip

The LED data wire connects to **GPIO 18** (BCM numbering), which is **physical pin 12** on the 40-pin header.

```
Pi Zero 40-pin header (top-left = pin 1)

 [1]  [2]
 [3]  [4]  ← 5V power (pin 4) → LED strip 5V/VCC
 [5]  [6]  ← Ground (pin 6)   → LED strip GND
 ...
[11] [12]  ← GPIO 18 (pin 12) → LED strip DATA/DIN
```

> **Power note:** For strips longer than ~20 LEDs, power the strip directly from your 5V supply rather than through the Pi. Still connect the grounds together (Pi GND to supply GND).

> **Level shifter:** WS2812B data runs at 5V logic. GPIO 18 outputs 3.3V. Most WS2812B strips accept 3.3V data reliably, but if you see flickering or dropped signals, add a 74AHCT125 level shifter between GPIO 18 and the strip's DATA pin.

---

## Step 2 — Flash Raspberry Pi OS

1. Open **Raspberry Pi Imager**
2. Click **Choose Device** → select your Pi model
3. Click **Choose OS** → **Raspberry Pi OS (other)** → **Raspberry Pi OS Lite (64-bit)**
   - Choose **Bookworm** (Debian 12) — the current stable release
   - Use **32-bit** instead if you have a Pi Zero 1 (ARMv6, which requires the 32-bit image)
4. Click **Choose Storage** → select your SD card
5. Click **Next**, then **Edit Settings**:
   - Set **hostname** (e.g. `raceflag`)
   - Set **username** and **password** (e.g. user `pi`)
   - Under **Services**, enable **SSH**
   - Enter your **WiFi SSID and password**
6. Click **Save** → **Yes** to apply settings → **Yes** to write

Wait for the write and verification to complete, then insert the card into your Pi.

---

## Step 3 — Disable Onboard Audio (Required)

The `rpi_ws281x` LED library uses the PWM hardware on GPIO 18, which conflicts with the Pi's onboard audio. You must disable audio before RaceFlag will drive the LEDs correctly.

After first boot and SSH-ing in, edit the boot config:

```bash
sudo nano /boot/firmware/config.txt
```

Find the line `dtparam=audio=on` and change it to:

```
dtparam=audio=off
```

Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`), then reboot:

```bash
sudo reboot
```

> **Note:** On older Raspberry Pi OS releases the file is at `/boot/config.txt` instead.

---

## Step 4 — Install RaceFlag

SSH into your Pi:

```bash
ssh pi@raceflag.local
```

Run the installer as root (it handles everything — cloning from GitHub, installing dependencies, and starting the service):

```bash
curl -fsSL https://raw.githubusercontent.com/prometheusprintingyyc/raceflag/main/install.sh | sudo bash
```

The installer will:
- Install system packages (`git`, `python3`, `hostapd`, `dnsmasq`)
- Install `rpi_ws281x` (the LED driver library)
- Clone the RaceFlag repository to `/opt/raceflag`
- Install Python dependencies (FastAPI, uvicorn, httpx, websockets)
- Create a default `config.json`
- Enable and start the `raceflag` systemd service

---

## Step 5 — Configure

The config file is at `/opt/raceflag/config.json`. Edit it to match your setup:

```bash
sudo nano /opt/raceflag/config.json
```

```json
{
  "led_count": 21,
  "led_gpio_pin": 18,
  "led_brightness": 128,
  "delay_seconds": 0.0,
  "wifi_ssid": "",
  "wifi_password": ""
}
```

| Field | Description |
|---|---|
| `led_count` | Total number of LEDs across all segments (default: `21` — segment 1: 11 LEDs, segment 2: 6 LEDs, segment 3: 4 LEDs) |
| `led_gpio_pin` | Leave as `18` unless you've rewired |
| `led_brightness` | 0–255 (128 = 50%) |
| `delay_seconds` | Artificial delay to sync LEDs with your broadcast (e.g. `5.0` for a 5-second delay) |

After saving, restart the service:

```bash
sudo systemctl restart raceflag
```

---

## Step 6 — Access the Web UI

Open a browser on any device on the same network and go to:

```
http://raceflag.local:8080
```

Or use the Pi's IP address if mDNS isn't available:

```
http://<pi-ip-address>:8080
```

The web UI shows live F1 timing, current flag state, and session info.

---

## Useful Commands

```bash
# View live service logs
sudo journalctl -u raceflag -f

# Check service status
sudo systemctl status raceflag

# Stop / start / restart
sudo systemctl stop raceflag
sudo systemctl start raceflag
sudo systemctl restart raceflag
```

---

## Updating RaceFlag

To update to a newer release:

```bash
sudo git -C /opt/raceflag pull --ff-only
sudo systemctl restart raceflag
```
