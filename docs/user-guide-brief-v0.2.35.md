# Brief: customer guide for the RaceFlag v0.2.35 update

You are writing a short, friendly guide that Prometheus Printing will email to every RaceFlag owner. It explains a one-time manual update to RaceFlag v0.2.35 and what's new in it. Everything you need is below; don't invent features, steps or messages that aren't here. Where something is marked `[PLACEHOLDER]`, leave it as a clearly visible placeholder for the owner to fill in.

## The product and the reader

- **RaceFlag** is an LED lightbox that shows Formula 1 track flags live (green, yellow, safety car, red flag, chequered flag) and can replay past races. It runs on a small computer (Raspberry Pi) inside the box and has a web page you open in a browser on the same Wi-Fi (`http://raceflag.local:8080`, or the box's IP address with `:8080`).
- **The reader** is a home customer, not technical, using a Windows 10 or 11 PC. Plain words, short steps, no jargon (say "the box" or "RaceFlag", not "the Pi"; "the update file", not "the script"; avoid "SSH" except where the customer sees it on screen).

## Why this update is manual (explain in two or three sentences)

RaceFlag boxes protect their memory card against sudden power loss. A fault meant the box's own **Update** button can't install updates on protected boxes. v0.2.35 fixes that, but the fix has to be installed once from a Windows computer. After this, future updates work from the Update button again.

Important for the reader: **for this update, don't use the Update button in RaceFlag's Settings. Use the Windows update tool below.** (Pressing it won't fix anything and can make the box restart on older software until the Windows tool is run.)

## What the customer needs

- RaceFlag plugged in, turned on, on the same Wi-Fi/network as the PC.
- A Windows 10 or 11 PC.
- The RaceFlag **username and password**, which Prometheus Printing sends separately: `[PLACEHOLDER: how credentials are delivered]`. **Never put the password in the guide.**
- The update tool: a zip file from `[PLACEHOLDER: download link]`. It contains two files, `Update RaceFlag.cmd` and `update-raceflag.ps1`, which must stay together in the same folder.
- About 10 minutes. The box restarts by itself at the end.

## Steps (keep this order and these exact on-screen texts)

1. Download the zip from the link. Right-click it → **Extract All** → **Extract**. (Running it from inside the zip doesn't work; the tool then says: "Extract all the files first, then double-click Update RaceFlag.cmd again.")
2. In the extracted folder, double-click **Update RaceFlag.cmd**.
   - Windows may show **"Windows protected your PC"** or an unknown-publisher warning. Click **More info**, then **Run anyway**.
3. The window shows `Looking for RaceFlag at raceflag.local...` and then asks `Username (from Prometheus Printing):`. Type the username and press Enter.
4. It shows a line like `Install v0.2.35 on RaceFlag v0.2.34 at raceflag.local? Its settings are kept. (Y/N):`. Type `Y` and press Enter.
5. It says `Connecting. When asked, type the password from Prometheus Printing (it does not show as you type).` and then asks for the password. Type it and press Enter. **Nothing appears while typing — that's normal.**
6. Progress lines appear one after another: `Checking the unit...`, `Downloading the update...`, `Checking the download...`, `Getting the packages it needs...`, `Installing...`, `Saving the version number...`, `Restarting RaceFlag...`. Some steps take a minute or two; that's normal.
7. `Installed. RaceFlag is restarting - this can take a few minutes. Don't unplug it.` — **don't unplug the box or close the window now.**
8. Done when it says `RaceFlag updated to v0.2.35. You can close this window.` Press any key to close.

Settings (Wi-Fi, LED delay, LED mode, F1 sign-in) are kept.

## If something goes wrong (a short table: message → what to do)

| The window says | What it means / what to do |
|---|---|
| Windows' SSH client is missing | Turn on Settings > Apps > Optional features > OpenSSH Client, then try again. |
| Can't reach RaceFlag at … | The box is off or on another network. Check it, or use its IP address (see "Finding your RaceFlag"). |
| RaceFlag answered too slowly … | The box may not be reaching the internet. Try again in a minute. |
| RaceFlag is already on …; this will install it again | Already updated. Continuing just installs it again; settings are kept. |
| This RaceFlag has a different security key than last time | Normal if the box was replaced or reset. Answer `Y` to forget the old key, then `Y` to run again. |
| The username or password was not accepted | Nothing was changed. Run it again and check the details. |
| Couldn't connect to RaceFlag over SSH … | Check the box is on and on this network, or use its IP address. |
| Could not download the update script … | The box couldn't reach the internet. Nothing was changed; try again later. |
| The connection to RaceFlag was lost during the update … | The box finishes the update by itself. Wait two minutes, then run the tool again. |
| RaceFlag hasn't come back yet … | Wait a minute or two, then open RaceFlag's page. If it still doesn't load after 10 minutes, unplug RaceFlag, wait 10 seconds and plug it back in. |

Any other message comes from the box and explains what went wrong; nothing changes on the box unless a message says the update was installed. For anything else: `[PLACEHOLDER: support contact]`.

## Finding your RaceFlag (only if `raceflag.local` doesn't work, or you own more than one box)

If the tool can't find the box, or you have **more than one RaceFlag** on your network (then `raceflag.local` may point at the wrong one), use the box's IP address. You can find it in your router's list of devices (look for "raceflag"). Then, in the extracted folder, click the address bar of File Explorer, type `cmd`, press Enter, and type (with your box's address):

    "Update RaceFlag.cmd" -Address 192.168.1.50

## What's new in v0.2.35 (a short, friendly section after the update steps)

- **LED Mode** (Settings): **On** (as before), **Off**, **Lamp** — use the box as a lamp in a colour you pick (red, yellow, green, white or any colour, with brightness), and **Auto** — the box lights up only between an on time and an off time you set each day. Live sessions and replays always light the box. Lamp mode has a **Use schedule** option to follow the same times.
- **Sync button** next to the LED delay: press it the moment the lights go out on your TV and RaceFlag matches its delay to your broadcast. The delay is saved for later races.
- **Race start animation** now plays reliably when the lights go out, once per race (and again after a red-flag restart).
- **Replays need a free F1 account**: Formula 1 now requires a login for its replay data. In Settings → **F1 Account**, choose **Set up** and follow the steps to add a "Send to RaceFlag" bookmark; sign in on f1tv.formula1.com and tap the bookmark. A free account works, and RaceFlag keeps you signed in (sign in again about once a month when it asks). The replay **Load** button now shows download progress.
- **Updates**: from now on the **Update** button in Settings works again. When you update, the box restarts by itself and the page tells you when it's done — don't unplug it while it says it's restarting.

## Style and format

- One page if possible, two at most. Headings: Why this update · What you need · Update steps · If something goes wrong · What's new.
- Numbered steps, on-screen texts in a monospace or quoted style exactly as above.
- Output as a single self-contained HTML file (inline CSS, no external fonts or scripts) that also prints well, plus a plain-text version suitable for pasting into an email body. Optionally leave spots marked `[SCREENSHOT: …]` where a screenshot would help (the Windows warning, the finished window).
