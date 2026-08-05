"""Pure-stdlib virtual keyboard via /dev/uinput.

Key combos injected at kernel level work on any display server — X11 or
Wayland, GNOME or KDE — unlike xdotool (X11 only). Requires write access
to /dev/uinput (the packaged udev rule grants it to logged-in users).
"""

from __future__ import annotations

import fcntl
import os
import struct
import time

# linux/uinput.h ioctls
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_DEV_SETUP = 0x405C5503
UI_DEV_CREATE = 0x00005501
UI_DEV_DESTROY = 0x00005502

EV_SYN = 0x00
EV_KEY = 0x01
SYN_REPORT = 0

# linux/input-event-codes.h
KEY_ESC = 1
KEY_D = 32
KEY_H = 35
KEY_SYSRQ = 99  # PrtScn
KEY_PAGEUP = 104
KEY_PAGEDOWN = 109
KEY_LEFTMETA = 125
KEY_PLAYPAUSE = 164

ALL_KEYS = (KEY_ESC, KEY_D, KEY_H, KEY_SYSRQ, KEY_PAGEUP, KEY_PAGEDOWN, KEY_LEFTMETA, KEY_PLAYPAUSE)


class VirtualKeyboard:
    """A uinput keyboard that can press key combinations."""

    def __init__(self, name: str = "mx4ctl virtual keyboard"):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
            for key in ALL_KEYS:
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, key)
            # struct uinput_setup: input_id{bustype,vendor,product,version}, name[80], ff_effects_max
            setup = struct.pack("=HHHH80sI", 0x03, 0x046D, 0xB042, 1, name.encode(), 0)
            fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
        except OSError:
            os.close(self.fd)
            raise
        time.sleep(0.3)  # give the compositor time to pick up the new device

    def _emit(self, etype: int, code: int, value: int) -> None:
        # struct input_event with 64-bit timeval; kernel stamps zero times itself
        os.write(self.fd, struct.pack("qqHHi", 0, 0, etype, code, value))

    def combo(self, *keys: int) -> None:
        """Press the keys in order, then release in reverse (e.g. Super+PgDn)."""
        for key in keys:
            self._emit(EV_KEY, key, 1)
            self._emit(EV_SYN, SYN_REPORT, 0)
        time.sleep(0.03)
        for key in reversed(keys):
            self._emit(EV_KEY, key, 0)
            self._emit(EV_SYN, SYN_REPORT, 0)

    def close(self) -> None:
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            os.close(self.fd)
        except OSError:
            pass
