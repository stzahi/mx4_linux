# Logitech MX Master 4 haptics & extras for Linux

The MX Master 4's headline features (haptic feedback, the thumb-panel
"Actions Ring" button) only work with Logi Options+, which doesn't exist on
Linux. This tool talks HID++ 2.0 directly to the mouse over `/dev/hidraw`
and brings them back:

- **Play haptics** — all 16 hardware waveforms, from the CLI or scripts
- **Haptic intensity & force threshold** — tune the vibration level and how
  hard you must press the thumb button
- **DPI & scroll wheel** — sensitivity, ratchet/freespin, torque, auto-disengage
- **Timer** — `mx4ctl timer 25m` buzzes the mouse when time is up
- **Daemon**
  - **mouse gestures**: hold the gesture button and flick — left/right
    switch workspaces, up shows the desktop, down minimizes, tap opens the
    overview (all configurable), each with haptic confirmation
  - desktop **notifications buzz the mouse** (per-app waveforms:
    Slack → knock, Thunderbird → jingle, …), silent during GNOME
    Do Not Disturb
  - the **thumb button works**: short press pops an actions menu at the
    cursor, long press runs a command — with haptic feedback
  - **workspace tick** — a soft buzz whenever you change workspace
  - **low-battery buzz** + notification

No dependencies beyond the Python standard library for the CLI; the daemon
uses PyGObject and dbus-python (preinstalled on Ubuntu GNOME). Coexists
fine with Solaar.

## Usage

```
mx4ctl status              # device, battery, haptic level, force threshold
mx4ctl waveforms           # list the 16 waveforms
mx4ctl play happy_alert    # buzz!  (-n 3 repeats)
mx4ctl demo                # feel every waveform in sequence
mx4ctl level 80            # haptic intensity 0..100 (0 = off)
mx4ctl force 5000          # thumb-button press threshold (see `mx4ctl force`)
mx4ctl dpi 1600            # pointer sensitivity (mx4ctl dpi shows the range)
mx4ctl ratchet off         # wheel freespin; on|torque N|speed N also available
mx4ctl timer 25m           # buzz `completed` in 25 minutes (90s, 1h, ...)
mx4ctl watch gesture       # debug: print a button's press/release events
mx4ctl daemon -v           # run the daemon in the foreground
```

### Interactive tuning — `mx4-wizard`

DPI, ratchet torque and the thumb-button force threshold are things you pick
by feel, not by number, and they're split between the mouse and GNOME.
`mx4-wizard` puts them on one screen. Installing the .deb or running
`install.sh` puts it on your PATH; it also runs straight from a checkout —
nothing to install:

```
git clone https://github.com/A-Common-Guy/mx4_linux
cd mx4_linux
./mx4-wizard
```

Every run checks its own setup and completes only what is missing, so it is
safe to run again at any time:

```
mx4-wizard --check     # report setup state, exit non-zero if incomplete
mx4-wizard --setup     # complete the missing steps, then exit
```

```
  Setup
    ✓ device access          hidraw node is readable
    ✗ restore on reconnect   settings will not come back when the receiver is replugged
    ✓ restore at login       mx4ctl-restore.service enabled
    ✓ on PATH                ~/.local/bin/mx4-wizard
    ✓ settings saved         ~/.config/mx4ctl/settings.conf

  1 step(s) to complete (one sudo for the system ones).
  Do it now? [Y/n]
```

The steps that need root are batched into a single `sudo` call. Then tune,
press `s`, and you're done — no package, no daemon, standard library only.

```
  MX Master 4 — settings   changes apply live

  POINTER
    · DPI                                1600  █████·······················
      Pointer speed                      +0.0  ██████████████··············
      Acceleration         adaptive (default)

  WHEEL
    · Mode                   ratchet (clicky)
  ▸ · Torque                               50  ██████████████··············
    · Auto-disengage                      off  ····························
      Natural scrolling                    off

  THUMB BUTTON
    · Force threshold                    6342  ███████████·················
    · Haptic level                         60  █████████████████···········
```

`↑↓` selects, `←→` adjusts, `⇧←→` takes bigger steps. Every change is written
to the mouse immediately, so you keep moving it and spinning the wheel while
you tune. `esc` reverts everything and quits; `q` keeps the changes for this
session without saving.

Settings marked `·` live in the mouse's volatile memory and are lost when it
power-cycles. Pressing `s` records them to `~/.config/mx4ctl/settings.conf`
and enables a `mx4ctl-restore.service` user unit that replays them at login
(it exits quietly when the mouse is asleep). The unmarked rows are GNOME
settings, which persist on their own.

The wizard is standalone — it needs no daemon, and works fine alongside one.

## Install

### As a package (recommended — Ubuntu 20.04+)

Grab the latest `.deb` from the
[releases page](https://github.com/A-Common-Guy/mx4_linux/releases) —
every push to `main` builds, tests and publishes one, with a changelog —
or build it yourself:

```
./packaging/build-deb.sh              # → dist/mx4ctl_<version>_all.deb
sudo apt install ./dist/mx4ctl_*.deb  # on any machine
```

The package installs the CLI to `/usr/bin/mx4ctl` and the wizard to
`/usr/bin/mx4-wizard`, a udev rule granting logged-in users access to
Logitech hidraw devices (receiver and Bluetooth), and enables the daemon
for all graphical sessions — it starts at next login, or immediately with
`systemctl --user start mx4ctl`. On first run the daemon creates
`~/.config/mx4ctl/config.ini` from the packaged example; edit it and
`systemctl --user restart mx4ctl`.

The wizard's setup comes with the package too: the udev rule replays saved
settings whenever the mouse (re)appears, and `mx4ctl-restore.service` is
enabled for all users — so `mx4-wizard` needs no sudo step, just run it
and tune.

**Uninstall:** `sudo apt remove mx4ctl` (per-user configs in
`~/.config/mx4ctl` stay; delete them by hand if wanted).

### From the repo (development)

```
./install.sh
```

Symlinks `mx4ctl` and `mx4-wizard` into `~/.local/bin`, copies
`config.example.ini` to `~/.config/mx4ctl/config.ini`, and enables a
systemd user service pointing at the repo. Remove with
`systemctl --user disable --now mx4ctl`.

### X11 and Wayland

Everything over HID++ (haptics, notifications, timers, DPI/ratchet) is
session-independent. Desktop actions use `@built-in` names
(`@workspace-next`, `@overview`, `@minimize`, …) that pick a backend at
runtime: a **uinput virtual keyboard** (works on X11 *and* Wayland, any
compositor — the packaged udev rule grants access to `/dev/uinput`), with
an `xdotool` fallback on X11 when uinput isn't accessible. The built-ins
press the standard GNOME shortcuts (Super+PgUp/PgDn, Super, Super+D,
Super+H) — remap or use plain shell commands in the config for other
desktops. The thumb-button menu appears at the cursor on X11; Wayland
doesn't let a daemon position windows globally, so there it opens as a
small centered action window instead. The workspace haptic tick uses
X11/XWayland properties and may stay silent on some Wayland setups.
After installing the .deb on Wayland, reboot (or reload udev rules and
re-login) so the uinput permission takes effect.

## Configuration

See `config.example.ini` — thumb-button actions (`menu`/`command`/`none`,
long-press timing), the popup-menu entries, per-app notification waveforms,
and battery warning threshold.

## Protocol notes (HID++ 2.0)

Everything rides on 20-byte long reports (`0x11`) to the hidraw node of the
Bolt receiver (device index = pairing slot) or the BLE device (`0xFF`).
Replies are matched on our software-id nibble (`0x0A`), so Solaar's traffic
and ours don't collide.

| Feature | ID | Functions used |
|---|---|---|
| HAPTIC | `0x19B0` | `0x00` caps (waveform bitmask at bytes 4–8), `0x10`/`0x20` get/set `[enabled, level]`, `0x40` play `[waveform]` |
| FORCE_SENSING_BUTTON | `0x19C0` | `0x10` caps `!HHHH changeable,default,max,min`, `0x20` get `!H`, `0x30` set `!BH button,value` |
| REPROG_CONTROLS_V4 | `0x1B04` | `0x30` setCidReporting `!HBH cid,flags,remap` (divert = flags `0x03`, +rawXY = `0x33`); event `0x00` = `!4H` CIDs down; event `0x10` = `!hh` raw dx,dy while a rawXY-diverted key is held (cursor freezes — the gesture mechanism) |
| UNIFIED_BATTERY | `0x1004` | `0x10` status `[percent, level, charging-state]` |
| ADJUSTABLE_DPI | `0x2201` | `0x10` dpi list (`0xE000+step` range encoding), `0x20` get `[sensor, dpi:2, default:2]`, `0x30` set `!BH sensor,dpi` |
| SMART_SHIFT_ENHANCED | `0x2111` | `0x10` get / `0x20` set `[mode, autoDisengage, torque]` — mode 1=freespin 2=ratchet, 0=leave unchanged |

The thumb-panel button is CID `0x01A0` ("Haptic"). Diversion is temporary —
it resets when the mouse reconnects (the daemon re-applies it on the
receiver's `0x41` connection notification), so nothing sticks if the daemon
dies.

Waveform IDs: `0x00 sharp_state_change, 0x01 damp_state_change,
0x02 sharp_collision, 0x03 damp_collision, 0x04 subtle_collision,
0x05 happy_alert, 0x06 angry_alert, 0x07 completed, 0x08 square, 0x09 wave,
0x0A firework, 0x0B mad, 0x0C knock, 0x0D jingle, 0x0E ringing,
0x1B whisper_collision`.

## Permissions

On Ubuntu with Solaar installed, its udev rules already grant the seated
user access to the hidraw node. Otherwise add a udev rule such as:

```
KERNEL=="hidraw*", ATTRS{idVendor}=="046d", TAG+="uaccess"
```
