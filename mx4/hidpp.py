"""Minimal HID++ 2.0 transport over /dev/hidraw for Logitech devices.

Speaks long (0x11, 20-byte) HID++ reports directly to a hidraw node —
either a Logitech receiver (device index = pairing slot 1..6) or a
directly connected Bluetooth device (device index 0xFF).

Safe to use alongside Solaar: the kernel duplicates input reports to
every open hidraw fd, and replies are matched on our own software-id
nibble, so the two never consume each other's responses.
"""

from __future__ import annotations

import glob
import json
import os
import select
import struct
import time
from collections import deque
from pathlib import Path

LOGITECH_VENDOR = 0x046D
REPORT_SHORT = 0x10
REPORT_LONG = 0x11
SWID = 0x0A  # our software id; notifications use 0x0, Solaar uses its own

FEATURE_ROOT = 0x0000

CACHE_FILE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "mx4ctl" / "device.json"


class HidppError(Exception):
    pass


class DeviceNotFound(HidppError):
    pass


class FeatureNotSupported(HidppError):
    pass


class RequestTimeout(HidppError):
    pass


class FeatureCallError(HidppError):
    """HID++ 2.0 error reply (feature index 0xFF)."""

    NAMES = {
        0x01: "UNKNOWN",
        0x02: "INVALID_ARGUMENT",
        0x03: "OUT_OF_RANGE",
        0x04: "HW_ERROR",
        0x05: "LOGITECH_INTERNAL",
        0x06: "INVALID_FEATURE_INDEX",
        0x07: "INVALID_FUNCTION_ID",
        0x08: "BUSY",
        0x09: "UNSUPPORTED",
    }

    def __init__(self, code: int):
        self.code = code
        super().__init__(f"HID++ error {code:#04x} ({self.NAMES.get(code, '?')})")


class ReceiverError(HidppError):
    """HID++ 1.0 style error reply (sub-id 0x8F), e.g. device offline."""

    def __init__(self, code: int):
        self.code = code
        super().__init__(f"receiver error {code:#04x}")


class Channel:
    """One open hidraw node that carries HID++ long reports."""

    def __init__(self, path: str):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        # notifications that arrived while we were waiting for a reply;
        # the daemon drains these, one-shot CLI commands just let them rotate out
        self.events: deque[bytes] = deque(maxlen=64)

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass

    def write(self, report: bytes) -> None:
        os.write(self.fd, report)

    def read_report(self, timeout: float) -> bytes | None:
        """Return one raw HID++ report, or None on timeout."""
        end = time.monotonic() + timeout
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([self.fd], [], [], remaining)
            if not ready:
                return None
            try:
                data = os.read(self.fd, 32)
            except BlockingIOError:
                continue
            if data and data[0] in (REPORT_SHORT, REPORT_LONG):
                return data

    def read_pending(self) -> list[bytes]:
        """Drain queued notifications plus whatever is readable right now."""
        out = list(self.events)
        self.events.clear()
        while True:
            try:
                data = os.read(self.fd, 32)
            except (BlockingIOError, OSError):
                break
            if not data:
                break
            if data[0] in (REPORT_SHORT, REPORT_LONG):
                out.append(data)
        return out


class Device:
    """A HID++ 2.0 device reachable through `channel` at `index`."""

    def __init__(self, channel: Channel, index: int):
        self.channel = channel
        self.index = index
        self._feature_index: dict[int, int] = {FEATURE_ROOT: 0x00}

    def request(self, feature_idx: int, fn: int, payload: bytes = b"", timeout: float = 2.0) -> bytes:
        # fn is the already-shifted function byte (0x10 = function 1), as in Logitech docs
        head = (fn & 0xF0) | SWID
        report = struct.pack("BBBB16s", REPORT_LONG, self.index, feature_idx, head, bytes(payload))
        self.channel.write(report)
        end = time.monotonic() + timeout
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise RequestTimeout(f"no reply to feature {feature_idx:#04x} fn {fn:#03x} on {self.channel.path}")
            data = self.channel.read_report(remaining)
            if data is None:
                continue
            if data[1] != self.index:
                continue
            if data[0] == REPORT_LONG and data[2] == feature_idx and data[3] == head:
                return data[4:]
            if data[0] == REPORT_LONG and data[2] == 0xFF and data[3] == feature_idx and data[4] == head:
                raise FeatureCallError(data[5])
            if data[0] == REPORT_SHORT and data[2] == 0x8F and data[3] == feature_idx and data[4] == head:
                raise ReceiverError(data[5])
            # something addressed to us but not our reply: an event or another
            # program's traffic — keep events (software id 0) for the daemon
            if data[0] == REPORT_LONG and (data[3] & 0x0F) == 0x00 or data[0] == REPORT_SHORT:
                self.channel.events.append(data)

    def feature_index(self, feature_id: int) -> int:
        idx = self._feature_index.get(feature_id)
        if idx is None:
            resp = self.request(0x00, 0x00, struct.pack("!H", feature_id))
            idx = resp[0]
            if idx == 0:
                raise FeatureNotSupported(f"feature {feature_id:#06x} not present")
            self._feature_index[feature_id] = idx
        return idx

    def call(self, feature_id: int, fn: int, payload: bytes = b"", timeout: float = 2.0) -> bytes:
        return self.request(self.feature_index(feature_id), fn, payload, timeout)

    def ping(self, timeout: float = 0.4, retries: int = 2) -> bool:
        """True if a HID++ 2.0 device answers at this index.

        The first packet on an idle wireless link is often dropped while the
        link wakes up, so a timeout is retried; a definite error is not.
        """
        marker = 0x5A
        for attempt in range(retries):
            try:
                resp = self.request(0x00, 0x10, bytes([0, 0, marker]), timeout=timeout * (attempt + 2))
            except RequestTimeout:
                continue
            except (FeatureCallError, ReceiverError):
                return False
            return resp[0] >= 2 and resp[2] == marker
        return False


def candidate_nodes():
    """Yield hidraw paths of Logitech nodes whose descriptor has the long HID++ report."""
    for uevent in sorted(glob.glob("/sys/class/hidraw/hidraw*/device/uevent")):
        name = uevent.split("/")[4]
        try:
            with open(uevent) as f:
                keys = dict(line.strip().split("=", 1) for line in f if "=" in line)
            hid_id = keys.get("HID_ID", "::").split(":")
            if int(hid_id[1], 16) != LOGITECH_VENDOR:
                continue
            with open(f"/sys/class/hidraw/{name}/device/report_descriptor", "rb") as f:
                desc = f.read()
        except (OSError, ValueError, IndexError):
            continue
        if b"\x85\x11" not in desc:  # no report-id 0x11 → plain mouse/kbd interface
            continue
        yield f"/dev/{name}"


def _load_cache() -> dict | None:
    try:
        return json.loads(CACHE_FILE.read_text())
    except (OSError, ValueError):
        return None


def _save_cache(node: str, index: int) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({"node": node, "index": index}))
    except OSError:
        pass


def find_device(feature_id: int = 0x19B0) -> Device:
    """Find the first HID++ 2.0 device exposing `feature_id` (default: HAPTIC)."""
    cached = _load_cache()
    if cached:
        try:
            ch = Channel(cached["node"])
            dev = Device(ch, cached["index"])
            if dev.ping():
                dev.feature_index(feature_id)
                return dev
            ch.close()
        except (OSError, HidppError, KeyError):
            pass

    for node in candidate_nodes():
        try:
            ch = Channel(node)
        except OSError:
            continue
        direct = Device(ch, 0xFF)
        if direct.ping():
            # a directly connected (Bluetooth) device — single occupant
            try:
                direct.feature_index(feature_id)
                _save_cache(node, 0xFF)
                return direct
            except HidppError:
                ch.close()
                continue
        found = False
        for index in range(1, 7):  # receiver pairing slots
            dev = Device(ch, index)
            if not dev.ping():
                continue
            try:
                dev.feature_index(feature_id)
            except HidppError:
                continue
            _save_cache(node, index)
            found = True
            break
        if found:
            return dev
        ch.close()
    raise DeviceNotFound(f"no Logitech device with HID++ feature {feature_id:#06x} found")
