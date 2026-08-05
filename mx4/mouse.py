"""MX Master 4 feature wrappers on top of the raw HID++ transport.

Feature map (from the device's own feature table):
  0x19B0 HAPTIC               fn 0x00 caps → bytes 4:8 = waveform bitmask
                              fn 0x10 get  → [enabled, level, four-levels-flag]
                              fn 0x20 set  ← [enabled, level 0-100]
                              fn 0x40 play ← [waveform id]
  0x19C0 FORCE_SENSING_BUTTON fn 0x10 caps(button)   → !HHHH changeable, default, max, min
                              fn 0x20 get(button)    → !H current
                              fn 0x30 set            ← !BH button, value
  0x1B04 REPROG_CONTROLS_V4   fn 0x30 setCidReporting ← !HBH cid, flags, remap
                              event 0x00 → !4H CIDs currently pressed
  0x1004 UNIFIED_BATTERY      fn 0x10 status → [percent, level, charging-status]
"""

from __future__ import annotations

import struct

from . import hidpp

FEATURE_HAPTIC = 0x19B0
FEATURE_FORCE_BUTTON = 0x19C0
FEATURE_CONTROLS = 0x1B04
FEATURE_BATTERY = 0x1004
FEATURE_NAME = 0x0005
FEATURE_DPI = 0x2201
FEATURE_SMARTSHIFT = 0x2111  # smart shift enhanced

WAVEFORMS = {
    "sharp_state_change": 0x00,
    "damp_state_change": 0x01,
    "sharp_collision": 0x02,
    "damp_collision": 0x03,
    "subtle_collision": 0x04,
    "happy_alert": 0x05,
    "angry_alert": 0x06,
    "completed": 0x07,
    "square": 0x08,
    "wave": 0x09,
    "firework": 0x0A,
    "mad": 0x0B,
    "knock": 0x0C,
    "jingle": 0x0D,
    "ringing": 0x0E,
    "whisper_collision": 0x1B,
}
WAVEFORM_NAMES = {v: k for k, v in WAVEFORMS.items()}

# control IDs of the divertable buttons (HID++ CID table)
CONTROLS = {
    "middle": 0x0052,
    "back": 0x0053,
    "forward": 0x0056,
    "gesture": 0x00C3,
    "smartshift": 0x00C4,
    "haptic": 0x01A0,  # the thumb-panel button — does nothing on stock Linux
}

BATTERY_STATUS = {
    0: "discharging",
    1: "charging",
    2: "charging (slow)",
    3: "full",
    4: "error",
}


def parse_waveform(text: str) -> int:
    """Accept a waveform name (any case, - or space) or a number."""
    key = text.strip().lower().replace("-", "_").replace(" ", "_")
    if key in WAVEFORMS:
        return WAVEFORMS[key]
    try:
        return int(text, 0)
    except ValueError:
        raise ValueError(f"unknown waveform {text!r} (see `mx4ctl waveforms`)") from None


class MX4:
    def __init__(self, dev: hidpp.Device):
        self.dev = dev

    # -- identity / battery --------------------------------------------------

    def name(self) -> str:
        length = self.dev.call(FEATURE_NAME, 0x00)[0]
        chunks = []
        while len(b"".join(chunks)) < length:
            chunks.append(self.dev.call(FEATURE_NAME, 0x10, bytes([len(b"".join(chunks))])))
        return b"".join(chunks)[:length].decode(errors="replace")

    def battery(self) -> tuple[int, str]:
        resp = self.dev.call(FEATURE_BATTERY, 0x10)
        return resp[0], BATTERY_STATUS.get(resp[2], f"status {resp[2]}")

    # -- haptics (0x19B0) ----------------------------------------------------

    def haptic_supported_waveforms(self) -> list[int]:
        resp = self.dev.call(FEATURE_HAPTIC, 0x00)
        mask = int.from_bytes(resp[4:8], "big")
        return [wid for wid in range(32) if mask & (1 << wid)]

    def haptic_level(self) -> tuple[bool, int]:
        resp = self.dev.call(FEATURE_HAPTIC, 0x10)
        return bool(resp[0] & 0x01), resp[1]

    def set_haptic_level(self, level: int) -> None:
        level = max(0, min(100, level))
        if level == 0:
            self.dev.call(FEATURE_HAPTIC, 0x20, bytes([0x00, 50]))  # disable
        else:
            self.dev.call(FEATURE_HAPTIC, 0x20, bytes([0x01, level]))

    def play(self, waveform: int) -> None:
        self.dev.call(FEATURE_HAPTIC, 0x40, bytes([waveform]))

    # -- force sensing button (0x19C0) ----------------------------------------

    def force_button_info(self, button: int = 0) -> dict:
        caps = self.dev.call(FEATURE_FORCE_BUTTON, 0x10, bytes([button]))
        changeable, default, max_v, min_v = struct.unpack("!HHHH", caps[:8])
        current = struct.unpack("!H", self.dev.call(FEATURE_FORCE_BUTTON, 0x20, bytes([button]))[:2])[0]
        return {
            "changeable": bool(changeable & 0x01),
            "default": default,
            "min": min_v,
            "max": max_v,
            "current": current,
        }

    def set_force(self, value: int, button: int = 0) -> None:
        info = self.force_button_info(button)
        if not info["changeable"]:
            raise hidpp.FeatureNotSupported("this button's force threshold is not changeable")
        if not info["min"] <= value <= info["max"]:
            raise ValueError(f"force must be within {info['min']}..{info['max']}")
        self.dev.call(FEATURE_FORCE_BUTTON, 0x30, struct.pack("!BH", button, value))

    # -- reprogrammable controls (0x1B04) --------------------------------------

    def divert(self, cid: int, enable: bool = True, raw_xy: bool = False) -> None:
        """Temporarily divert a control: presses arrive as HID++ events instead
        of HID input. With raw_xy, mouse motion while the control is held is
        also diverted (event 0x10, cursor freezes) — that's how gestures work.
        Resets when the device reconnects — nothing persists."""
        flags = 0x03 if enable else 0x02  # divert bit + its valid bit
        if raw_xy or not enable:
            flags |= 0x30 if enable else 0x20  # rawXY bit + its valid bit
        self.dev.call(FEATURE_CONTROLS, 0x30, struct.pack("!HBH", cid, flags, 0))

    def controls_feature_index(self) -> int:
        return self.dev.feature_index(FEATURE_CONTROLS)

    @staticmethod
    def decode_keys_down(payload: bytes) -> set[int]:
        """Decode a 0x1B04 event 0x00 payload into the set of pressed CIDs."""
        return {cid for cid in struct.unpack("!4H", payload[:8]) if cid}

    @staticmethod
    def decode_raw_xy(payload: bytes) -> tuple[int, int]:
        """Decode a 0x1B04 event 0x10 payload into (dx, dy) sensor counts."""
        return struct.unpack("!hh", payload[:4])

    # -- DPI (0x2201) ----------------------------------------------------------

    def dpi_list(self) -> list[int]:
        raw = b""
        for i in range(16):
            raw += self.dev.call(FEATURE_DPI, 0x10, bytes([0x00, i]))[1:]
            if raw[-2:] == b"\x00\x00":
                break
        values, i = [], 0
        while i + 1 < len(raw):
            val = int.from_bytes(raw[i:i + 2], "big")
            if val == 0:
                break
            if val >> 13 == 0b111:  # range marker: previous..next in steps of (val & 0x1FFF)
                step = val & 0x1FFF
                last = int.from_bytes(raw[i + 2:i + 4], "big")
                values.extend(range(values[-1] + step, last + 1, step))
                i += 4
            else:
                values.append(val)
                i += 2
        return values

    def dpi(self) -> int:
        resp = self.dev.call(FEATURE_DPI, 0x20, bytes([0x00]))
        current = int.from_bytes(resp[1:3], "big")
        return current or int.from_bytes(resp[3:5], "big")  # 0 → default in use

    def set_dpi(self, value: int) -> None:
        self.dev.call(FEATURE_DPI, 0x30, struct.pack("!BH", 0x00, value))

    # -- smart shift / ratchet (0x2111) ------------------------------------------

    def ratchet(self) -> tuple[int, int, int]:
        """Return (mode, auto-disengage speed, torque). Mode 1=freespin 2=ratchet."""
        resp = self.dev.call(FEATURE_SMARTSHIFT, 0x10)
        return resp[0], resp[1], resp[2]

    def set_ratchet(self, mode: int = 0, speed: int = 0, torque: int = 0) -> None:
        """Set wheel mode (1=freespin, 2=ratchet), auto-disengage speed (1..50)
        and/or ratchet torque (1..100). Zero means leave unchanged."""
        self.dev.call(FEATURE_SMARTSHIFT, 0x20, bytes([mode, speed, torque]))
