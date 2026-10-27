"""Wire protocol for the Robobloq USB LED controller (USB HID 1A86:FE07 / FE0C).

This is the controller inside "DX Light" / "SyncLight" / "QuikLight" monitor
light bars. Pure functions only: nothing here touches hardware or the network.

Frame families (checksum = sum of all preceding bytes mod 256):

  RB  52 42 LEN ID ACT payload.. CHK          LEN = total bytes (u8)
  SC  53 43 LENhi LENlo ID 80 payload.. CHK   LEN = total bytes (u16 BE)

Colour payloads are 5-byte sections [first_led, R, G, B, last_led],
1-based and inclusive. An end of 254 means "to the end of the strip".

Every frame goes out as 64-byte HID output reports with report ID 0, padded
with zeros.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

VID = 0x1A86
PIDS = (0xFE07, 0xFE0C)
VENDOR_USAGE_PAGE = 0xFF00
REPORT_SIZE = 64
SECTION_SIZE = 5
MAX_LED = 254  # LED indices are single bytes; 254 doubles as "end of strip"
SC_OVERHEAD = 7
MAX_SECTIONS_PER_REPORT = (REPORT_SIZE - SC_OVERHEAD) // SECTION_SIZE  # 11

# Actions
SYNC_SCREEN = 0x80
DEVICE_INFO = 0x82
LED_EFFECT = 0x85
SECTION_LED = 0x86
BRIGHTNESS = 0x87
AUTO_OFF = 0x89
DYNAMIC_SPEED = 0x8A
SOUND_SENSITIVITY = 0x8B
KEYBOARD_URL = 0x93  # controller can act as a USB keyboard and open a URL; 0 disables
TURN_OFF = 0x97

Rgb = tuple[int, int, int]


def checksum(data: bytes | bytearray) -> int:
    return sum(data) & 0xFF


def rb(seq: int, action: int, payload: bytes = b"") -> bytes:
    total = 6 + len(payload)
    if total > 255:
        raise ValueError("RB frame too long")
    buf = bytearray(b"RB") + bytes([total, seq & 0xFF, action]) + bytes(payload)
    buf.append(checksum(buf))
    return bytes(buf)


def sc(seq: int, payload: bytes, action: int = SYNC_SCREEN) -> bytes:
    total = SC_OVERHEAD + len(payload)
    buf = bytearray(b"SC") + total.to_bytes(2, "big") + bytes([seq & 0xFF, action]) + bytes(payload)
    buf.append(checksum(buf))
    return bytes(buf)


def reports(frame: bytes) -> list[bytes]:
    """Split a frame into 65-byte writes: report ID 0 + 64 data bytes (zero padded)."""
    out = []
    for i in range(0, len(frame), REPORT_SIZE):
        chunk = frame[i:i + REPORT_SIZE]
        out.append(b"\x00" + chunk + b"\x00" * (REPORT_SIZE - len(chunk)))
    return out


@dataclass(frozen=True)
class Section:
    first: int  # 1-based, inclusive
    last: int   # 1-based, inclusive
    rgb: Rgb

    def encode(self) -> bytes:
        r, g, b = (max(0, min(255, int(c))) for c in self.rgb)
        return bytes((self.first, r, g, b, self.last))


def encode_sections(sections: Iterable[Section]) -> bytes:
    return b"".join(s.encode() for s in sections)


def _close(a: Rgb, b: Rgb, tol: int) -> bool:
    return all(abs(int(x) - int(y)) <= tol for x, y in zip(a, b))


def collapse(colors: Sequence[Rgb], tol: int = 0) -> list[Section]:
    """Merge runs of (nearly) identical neighbouring LEDs into sections."""
    n = min(len(colors), MAX_LED - 1)
    out: list[Section] = []
    i = 0
    while i < n:
        j = i + 1
        while j < n and _close(colors[j], colors[i], tol):
            j += 1
        out.append(Section(i + 1, j, tuple(int(c) for c in colors[i])))
        i = j
    return out


def collapse_to_fit(colors: Sequence[Rgb], max_sections: int) -> list[Section]:
    """Raise the merge tolerance until the frame fits in `max_sections`."""
    tol = 0
    while True:
        secs = collapse(colors, tol)
        if len(secs) <= max_sections or tol >= 255:
            return secs
        tol += 2


# ---- high-level command builders -------------------------------------------

def cmd_device_info(seq: int) -> bytes:
    return rb(seq, DEVICE_INFO)


def cmd_disable_keyboard_url(seq: int) -> bytes:
    return rb(seq, KEYBOARD_URL, b"\x00")


def cmd_solid(seq: int, rgb: Rgb, led_count: int) -> bytes:
    """Whole strip one colour; LEDs past led_count are cleared (stock behaviour)."""
    r, g, b = rgb
    if 0 < led_count < MAX_LED - 1:
        payload = bytes([1, r, g, b, led_count, led_count + 1, 0, 0, 0, MAX_LED])
    else:
        payload = bytes([1, r, g, b, MAX_LED])
    return rb(seq, SECTION_LED, payload)


def cmd_brightness(seq: int, value: int) -> bytes:
    """1..255. The firmware treats 0 as maximum, so it is clamped to 1."""
    return rb(seq, BRIGHTNESS, bytes([max(1, min(255, int(value)))]))


def cmd_effect(seq: int, kind: int, index: int) -> bytes:
    """Built-in firmware effect. kind 2 = dynamic, 3 = onboard-mic music; index 0..6."""
    return rb(seq, LED_EFFECT, bytes([kind & 0xFF, index & 0xFF]))


def cmd_speed(seq: int, speed: int) -> bytes:
    """speed 0..100, 100 = fastest (the firmware counts the other way)."""
    s = max(0, min(100, int(speed)))
    return rb(seq, DYNAMIC_SPEED, bytes([100 - s]))


def cmd_off(seq: int) -> bytes:
    return rb(seq, TURN_OFF)


def screen_frames(next_seq, colors: Sequence[Rgb], mode: str = "chunked",
                  max_reports: int = 0) -> list[bytes]:
    """SC frames for one screen-sync update.

    mode "chunked": one SC frame for the whole strip, split across 64-byte
                    reports (what the official app does).
    mode "split":   several small SC frames, each fitting one report. Use this
                    if your firmware doesn't reassemble multi-report frames.
    max_reports > 0 merges similar colours so an update needs at most that
    many reports (1 = atomic, tear-free updates).
    """
    if max_reports > 0:
        budget = max_reports * MAX_SECTIONS_PER_REPORT if mode == "split" else \
            (max_reports * REPORT_SIZE - SC_OVERHEAD) // SECTION_SIZE
        sections = collapse_to_fit(colors, budget)
    else:
        sections = collapse(colors)
    if mode == "split":
        return [sc(next_seq(), encode_sections(sections[i:i + MAX_SECTIONS_PER_REPORT]))
                for i in range(0, len(sections), MAX_SECTIONS_PER_REPORT)]
    return [sc(next_seq(), encode_sections(sections))]


@dataclass
class DeviceInfo:
    led_count: int
    firmware: tuple[int, int, int]
    model: str
    raw: bytes

    @property
    def firmware_str(self) -> str:
        return ".".join(map(str, self.firmware))


def parse_device_info(resp: bytes) -> DeviceInfo | None:
    if len(resp) < 24 or resp[:2] != b"RB" or resp[4] != DEVICE_INFO:
        return None
    return DeviceInfo(led_count=resp[11], firmware=tuple(resp[21:24]),
                      model=resp[5:8].hex().upper(), raw=bytes(resp[:24]))


class Seq:
    """Sequence IDs as the stock app issues them: first is 2, cycles 1..254."""

    def __init__(self) -> None:
        self._v = 1

    def __call__(self) -> int:
        self._v = 1 if self._v >= 254 else self._v + 1
        return self._v
