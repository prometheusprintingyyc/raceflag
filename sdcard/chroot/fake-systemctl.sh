#!/bin/bash
# Fake systemctl for use inside a QEMU chroot where systemd is not running.
# 'enable'  -> creates the wanted symlink so the service starts on real boot.
# 'unmask'  -> removes the mask symlink (hostapd is masked by default on Pi OS).
# Everything else (daemon-reload, restart, stop, etc.) is a silent no-op.
case "$1" in
  enable)
    UNIT="${2%.service}.service"
    for dir in /etc/systemd/system /lib/systemd/system; do
      if [ -f "$dir/$UNIT" ]; then
        mkdir -p /etc/systemd/system/multi-user.target.wants
        ln -sf "$dir/$UNIT" "/etc/systemd/system/multi-user.target.wants/$UNIT"
        break
      fi
    done
    ;;
  unmask)
    rm -f "/etc/systemd/system/${2%.service}.service"
    ;;
esac
exit 0
