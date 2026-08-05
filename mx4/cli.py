"""mx4ctl command line interface."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time

from . import hidpp
from .mouse import CONTROLS, MX4, WAVEFORM_NAMES, parse_waveform


def _connect() -> MX4:
    try:
        return MX4(hidpp.find_device())
    except hidpp.DeviceNotFound as e:
        sys.exit(f"mx4ctl: {e}\nIs the mouse on and connected (receiver plugged / Bluetooth paired)?")
    except PermissionError as e:
        sys.exit(f"mx4ctl: {e}\nNo permission on the hidraw node — see README (udev rules).")


def cmd_status(_args) -> None:
    m = _connect()
    percent, state = m.battery()
    enabled, level = m.haptic_level()
    force = m.force_button_info()
    waves = m.haptic_supported_waveforms()
    print(f"device        : {m.name()}  ({m.dev.channel.path}, index {m.dev.index})")
    print(f"battery       : {percent}% ({state})")
    print(f"haptics       : {'on' if enabled else 'off'}, level {level}/100, {len(waves)} waveforms")
    print(f"force button  : {force['current']} (range {force['min']}..{force['max']}, default {force['default']})")


def cmd_waveforms(_args) -> None:
    m = _connect()
    for wid in m.haptic_supported_waveforms():
        print(f"  {wid:#04x}  {WAVEFORM_NAMES.get(wid, 'unnamed')}")


def cmd_play(args) -> None:
    m = _connect()
    wid = parse_waveform(args.waveform)
    for i in range(args.count):
        if i:
            time.sleep(args.interval)
        m.play(wid)
    print(f"played {WAVEFORM_NAMES.get(wid, hex(wid))}" + (f" ×{args.count}" if args.count > 1 else ""))


def cmd_demo(args) -> None:
    m = _connect()
    print("Hold the mouse — playing every waveform:")
    for wid in m.haptic_supported_waveforms():
        print(f"  {wid:#04x}  {WAVEFORM_NAMES.get(wid, 'unnamed')}")
        m.play(wid)
        time.sleep(args.interval)


def cmd_level(args) -> None:
    m = _connect()
    if args.value is None:
        enabled, level = m.haptic_level()
        print(f"haptics {'on' if enabled else 'off'}, level {level}/100")
    else:
        m.set_haptic_level(args.value)
        print(f"haptic level set to {args.value}" + (" (off)" if args.value == 0 else ""))


def cmd_force(args) -> None:
    m = _connect()
    info = m.force_button_info()
    if args.value is None:
        print(f"force threshold: {info['current']} (range {info['min']}..{info['max']}, default {info['default']})")
    else:
        m.set_force(args.value)
        print(f"force threshold set to {args.value}")


def cmd_watch(args) -> None:
    m = _connect()
    cid = CONTROLS[args.button]
    ctl_idx = m.controls_feature_index()
    m.divert(cid, True)
    print(f"'{args.button}' button diverted (cid {cid:#06x}) — press it; Ctrl-C to stop.")
    down: set[int] = set()
    pressed_at = 0.0
    try:
        while True:
            data = m.dev.channel.read_report(timeout=3600)
            if data is None or data[0] != hidpp.REPORT_LONG or data[1] != m.dev.index:
                continue
            if data[2] != ctl_idx or data[3] != 0x00:  # event 0x00, swid 0
                continue
            now_down = MX4.decode_keys_down(data[4:])
            for c in now_down - down:
                pressed_at = time.monotonic()
                print(f"  press   cid {c:#06x}")
            for c in down - now_down:
                print(f"  release cid {c:#06x}  ({time.monotonic() - pressed_at:.2f}s)")
            down = now_down
    except KeyboardInterrupt:
        pass
    finally:
        m.divert(cid, False)
        print("\nbutton restored")


def cmd_dpi(args) -> None:
    m = _connect()
    if args.value is None:
        values = m.dpi_list()
        span = f"{values[0]}..{values[-1]}" if values else "?"
        print(f"dpi: {m.dpi()} (allowed {span})")
    else:
        values = m.dpi_list()
        if values and args.value not in values:
            nearest = min(values, key=lambda v: abs(v - args.value))
            sys.exit(f"mx4ctl: {args.value} not allowed (nearest: {nearest}, range {values[0]}..{values[-1]})")
        m.set_dpi(args.value)
        print(f"dpi set to {args.value}")


def cmd_ratchet(args) -> None:
    m = _connect()
    if args.what is None:
        mode, speed, torque = m.ratchet()
        state = {1: "freespin", 2: "ratchet"}.get(mode, f"mode {mode}")
        print(f"wheel: {state}, auto-disengage speed {speed}, torque {torque}")
    elif args.what in ("on", "off"):
        m.set_ratchet(mode=2 if args.what == "on" else 1)
        print(f"wheel ratchet {args.what}")
    elif args.what in ("torque", "speed"):
        if args.value is None:
            sys.exit(f"mx4ctl: ratchet {args.what} needs a value")
        m.set_ratchet(**{args.what: args.value})
        print(f"ratchet {args.what} set to {args.value}")


DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)([smh]?)$")


def cmd_timer(args) -> None:
    match = DURATION_RE.match(args.duration)
    if not match:
        sys.exit("mx4ctl: duration like 90s, 25m, 1h (bare number = minutes)")
    seconds = float(match.group(1)) * {"s": 1, "m": 60, "h": 3600, "": 60}[match.group(2)]
    wid = parse_waveform(args.waveform)
    if os.fork():
        print(f"timer set: {args.duration} → {WAVEFORM_NAMES.get(wid, hex(wid))} buzz")
        return
    # detached child: outlive the terminal, then buzz
    os.setsid()
    time.sleep(seconds)
    try:
        MX4(hidpp.find_device()).play(wid)
    except hidpp.HidppError:
        pass
    subprocess.run(["notify-send", "-i", "alarm-clock", "mx4ctl timer", args.message],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os._exit(0)


def cmd_daemon(args) -> None:
    from .daemon import run
    run(config_path=args.config, verbose=args.verbose)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mx4ctl", description="Logitech MX Master 4 haptics & extras for Linux")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="device, battery, haptic level, force threshold").set_defaults(fn=cmd_status)
    sub.add_parser("waveforms", help="list supported haptic waveforms").set_defaults(fn=cmd_waveforms)

    sp = sub.add_parser("play", help="play a haptic waveform")
    sp.add_argument("waveform", help="name (e.g. happy_alert) or number (e.g. 0x05)")
    sp.add_argument("-n", "--count", type=int, default=1)
    sp.add_argument("-i", "--interval", type=float, default=0.35, help="seconds between repeats")
    sp.set_defaults(fn=cmd_play)

    sp = sub.add_parser("demo", help="play every supported waveform in sequence")
    sp.add_argument("-i", "--interval", type=float, default=1.2)
    sp.set_defaults(fn=cmd_demo)

    sp = sub.add_parser("level", help="get or set haptic intensity (0 turns haptics off)")
    sp.add_argument("value", nargs="?", type=int, help="0..100")
    sp.set_defaults(fn=cmd_level)

    sp = sub.add_parser("force", help="get or set the force-button press threshold")
    sp.add_argument("value", nargs="?", type=int)
    sp.set_defaults(fn=cmd_force)

    sp = sub.add_parser("dpi", help="get or set pointer sensitivity")
    sp.add_argument("value", nargs="?", type=int)
    sp.set_defaults(fn=cmd_dpi)

    sp = sub.add_parser("ratchet", help="scroll wheel: on|off|torque N|speed N")
    sp.add_argument("what", nargs="?", choices=["on", "off", "torque", "speed"])
    sp.add_argument("value", nargs="?", type=int)
    sp.set_defaults(fn=cmd_ratchet)

    sp = sub.add_parser("timer", help="buzz after a delay (e.g. mx4ctl timer 25m)")
    sp.add_argument("duration", help="90s, 25m, 1h — bare number = minutes")
    sp.add_argument("waveform", nargs="?", default="completed")
    sp.add_argument("-m", "--message", default="Time's up!")
    sp.set_defaults(fn=cmd_timer)

    sp = sub.add_parser("watch", help="divert a button and print its events (debug)")
    sp.add_argument("button", nargs="?", default="haptic", choices=sorted(CONTROLS))
    sp.set_defaults(fn=cmd_watch)

    sp = sub.add_parser("daemon", help="notification haptics + thumb-button actions")
    sp.add_argument("-c", "--config", default=None, help="path to config.ini")
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.set_defaults(fn=cmd_daemon)

    args = p.parse_args(argv)
    try:
        args.fn(args)
    except hidpp.HidppError as e:
        sys.exit(f"mx4ctl: {e}")
    except ValueError as e:
        sys.exit(f"mx4ctl: {e}")
    return 0
