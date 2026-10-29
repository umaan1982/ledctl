"""USB HID transport. The only module that talks to hardware. No network code anywhere."""

from __future__ import annotations

import threading
import time

from . import protocol as P


class DeviceError(RuntimeError):
    pass


def _hid():
    try:
        import hid  # pip install hidapi
    except ImportError as e:
        raise DeviceError("Missing dependency: pip install hidapi") from e
    return hid


def list_interfaces() -> list[dict]:
    hid = _hid()
    return [d for pid in P.PIDS for d in hid.enumerate(P.VID, pid)]


class Strip:
    def __init__(self, path: bytes | None = None, dry_run: bool = False, log=None):
        self.dry_run = dry_run
        self.log = log
        self.seq = P.Seq()
        self.info: P.DeviceInfo | None = None
        self._lock = threading.Lock()
        self._dev = None
        if dry_run:
            return
        hid = _hid()
        if path is None:
            cands = [d for d in list_interfaces() if d.get("usage_page") == P.VENDOR_USAGE_PAGE]
            if not cands:
                raise DeviceError(
                    "No LED controller found (USB 1A86:FE07/FE0C, usage page FF00).\n"
                    "Is it plugged in, and is DX Light closed?")
            path = cands[0]["path"]
        self._dev = hid.device()
        try:
            self._dev.open_path(path)
        except OSError as e:
            raise DeviceError(f"Found the controller but couldn't open it: {e}. "
                              "Close DX Light / SyncLight and try again.") from e
        self._dev.set_nonblocking(True)

    # -- low level ----------------------------------------------------------
    def send(self, frame: bytes) -> None:
        if self.log:
            self.log(frame)
        if self.dry_run:
            return
        with self._lock:  # never interleave the reports of two frames
            for rep in P.reports(frame):
                if self._dev.write(rep) < 0:
                    raise DeviceError("Write failed (unplugged?)")

    def _read(self, timeout: float = 1.0, want: int = 24) -> bytes:
        if self.dry_run:
            return b""
        buf, end = b"", time.monotonic() + timeout
        while time.monotonic() < end and len(buf) < want:
            r = self._dev.read(P.REPORT_SIZE)
            if r:
                buf += bytes(r)
            else:
                time.sleep(0.005)
        return buf

    def _read_info(self, timeout: float = 1.0) -> P.DeviceInfo | None:
        """Read reports until one is a device-info reply (skips unrelated reports)."""
        if self.dry_run:
            return None
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            r = self._dev.read(P.REPORT_SIZE)
            if r:
                info = P.parse_device_info(bytes(r))
                if info:
                    return info
            else:
                time.sleep(0.005)
        return None

    # -- commands -----------------------------------------------------------
    def handshake(self, disable_keyboard: bool = True) -> P.DeviceInfo | None:
        if not self.dry_run:
            while self._dev.read(P.REPORT_SIZE):  # drop stale replies
                pass
        self.info = None
        for _ in range(3):  # the controller sometimes misses the first request (e.g. right after login)
            self.send(P.cmd_device_info(self.seq()))
            self.info = self._read_info()
            if self.info or self.dry_run:
                break
            time.sleep(0.3)
        if disable_keyboard:
            # Stops the controller from acting as a keyboard that opens the vendor URL.
            self.send(P.cmd_disable_keyboard_url(self.seq()))
        return self.info

    @property
    def led_count(self) -> int:
        return self.info.led_count if self.info and self.info.led_count else 0

    def solid(self, rgb) -> None:
        self.send(P.cmd_solid(self.seq(), tuple(rgb), self.led_count))

    def brightness(self, value: int, resend_rgb=None) -> None:
        self.send(P.cmd_brightness(self.seq(), value))
        if resend_rgb is not None:  # stock app re-sends static colour so it takes effect
            time.sleep(0.02)
            self.solid(resend_rgb)

    def effect(self, kind: int, index: int, speed: int | None = None) -> None:
        self.send(P.cmd_effect(self.seq(), kind, index))
        if speed is not None:
            self.send(P.cmd_speed(self.seq(), speed))

    def off(self) -> None:
        self.send(P.cmd_off(self.seq()))

    def frame(self, colors, mode: str = "chunked", max_reports: int = 0, gap: float = 0.002) -> None:
        frames = P.screen_frames(self.seq, colors, mode, max_reports)
        for i, f in enumerate(frames):
            self.send(f)
            if gap and i < len(frames) - 1:
                time.sleep(gap)

    def close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            finally:
                self._dev = None
