# Updating your RaceFlag from a Windows computer

Use this when the Update button on RaceFlag's own page doesn't work for your unit. It installs the newest RaceFlag release over your network. Your settings are kept.

## Before you start

- RaceFlag must be plugged in, turned on and connected to the same network as your computer.
- You need the username and password Prometheus Printing gave you for your RaceFlag.
- Your computer needs Windows' OpenSSH Client (it is included in Windows 10 and 11; if it is missing, the script tells you where to turn it on).

## How to update

1. Double-click `Update RaceFlag.cmd`.
2. Type the username when asked, then press Enter.
3. Check the line that says which version will be installed, type `Y` and press Enter.
4. When asked, type the password and press Enter. Nothing shows on screen while you type; that is normal.
5. Wait. Do not unplug RaceFlag or close the window. The unit restarts by itself and it can take a few minutes.

When it says "RaceFlag updated to ...", you are done and can close the window.

## What the messages mean

- "Windows' SSH client is missing" - turn on Settings > Apps > Optional features > OpenSSH Client, then try again.
- "Can't reach RaceFlag at ..." - the unit is off, or on a different network. Check it, or see "Finding RaceFlag" below.
- "RaceFlag is already on ...; this will install it again" - you already have this version. Continuing just installs it again; your settings are kept.
- "This RaceFlag has a different security key than last time" - normal if the unit was replaced or re-flashed. Answer `Y` to forget the old key and run the update again.
- "The username or password was not accepted" - nothing was changed. Try again and check the details.
- "Could not download the update script..." - the unit couldn't reach the internet, or that release doesn't exist. Nothing was changed.
- "RaceFlag hasn't come back yet" - wait a minute or two, then open RaceFlag's page. If the page doesn't load after 10 minutes, unplug RaceFlag, wait 10 seconds and plug it back in.

Any other message comes from the unit and says what went wrong; nothing is changed unless the window says "Installed".

## Finding RaceFlag

The script looks for `raceflag.local`. If your network doesn't find it, run the script again with the unit's IP address (shown in your router's list of devices), from a PowerShell window in this folder:

    powershell -ExecutionPolicy Bypass -File .\update-raceflag.ps1 -Address 192.168.1.50

Other options: `-Tag v1.2.3` to install a specific release instead of the latest, `-User name` to skip the username question, `-Yes` to skip the confirmation, `-DryRun` to show what would happen without changing anything.
