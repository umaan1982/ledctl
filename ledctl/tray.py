"""Minimal system-tray front end (pip install pystray pillow)."""

from __future__ import annotations

import threading
import time

from .cli import open_strip, run_sync, save_config
from .device import DeviceError

PRESETS = {"Purple (mine)": (128, 0, 255), "Lilac": (176, 96, 255), "Warm white": (255, 140, 60),
           "Cool white": (200, 220, 255), "Red": (255, 0, 0), "Green": (0, 255, 0),
           "Blue": (0, 0, 255), "Cyan": (0, 200, 255)}


class TrayController:
    """Everything the tray does, without the UI, so it can be tested."""

    def __init__(self, args, cfg, opener=open_strip, sync_fn=run_sync, log=print):
        self.args, self.cfg, self.opener, self.sync_fn, self.log = args, cfg, opener, sync_fn, log
        self.strip = opener(args)
        self.stop_ev: threading.Event | None = None
        self.thread: threading.Thread | None = None
        self.resume_sync = False  # what to restore after a power-down
        self.lock = threading.RLock()

    @property
    def syncing(self) -> bool:
        return self.thread is not None

    def start_sync(self) -> None:
        with self.lock:
            if self.thread:
                return
            self.stop_ev = threading.Event()
            self.thread = threading.Thread(target=self._sync_runner, args=(self.stop_ev,), daemon=True)
            self.thread.start()

    def _sync_runner(self, stop: threading.Event) -> None:
        """Run sync; if the strip drops off USB (e.g. after modern standby), reconnect and carry on."""
        while not stop.is_set():
            try:
                self.sync_fn(self.args, self.cfg, stop, self.strip)
                return
            except (DeviceError, OSError) as e:
                self.log(f"Strip connection lost ({e}); reconnecting")
                if stop.wait(1):
                    return
                try:
                    self._reopen()
                except (DeviceError, OSError) as e2:
                    self.log(f"Reconnect failed ({e2}); retrying")

    def _reopen(self) -> None:
        try:
            self.strip.close()
        except Exception:
            pass
        self.strip = self.opener(self.args)

    def stop_sync(self, timeout: float = 2.0) -> None:
        with self.lock:
            if self.stop_ev:
                self.stop_ev.set()
                self.thread.join(timeout=timeout)
                self.stop_ev = self.thread = None

    def toggle_sync(self) -> None:
        with self.lock:
            if self.thread:
                self.stop_sync()
                self.strip.solid(tuple(self.cfg["color"]))
            else:
                self.start_sync()

    def set_color(self, rgb) -> None:
        with self.lock:
            self.stop_sync()
            self.strip.solid(tuple(rgb))
            self.cfg["color"] = list(rgb)
            save_config(self.cfg)

    def set_brightness(self, v: int) -> None:
        with self.lock:
            self.strip.brightness(v, resend_rgb=None if self.thread else tuple(self.cfg["color"]))
            self.cfg["brightness"] = v
            save_config(self.cfg)

    def off(self) -> None:
        with self.lock:
            self.stop_sync()
            self.strip.off()

    def quit(self) -> None:
        with self.lock:
            self.stop_sync()
            try:
                self.strip.solid(tuple(self.cfg["color"]))
            finally:
                self.strip.close()

    # -- power events (shutdown, sleep, display off) --------------------------
    def power_down(self, reason: str) -> None:
        with self.lock:
            self.resume_sync = self.syncing
            self.stop_sync(timeout=1.0)
            try:
                self.strip.off()
            except Exception as e:
                self.log(f"Couldn't switch the strip off ({e})")

    def power_up(self, reason: str) -> None:
        with self.lock:
            # After sleep the USB device may have been re-enumerated: reconnect.
            for attempt in range(10):
                try:
                    # Always reconnect: after sleep / modern standby the USB device may be new.
                    self._reopen()
                    if self.resume_sync:
                        self.start_sync()
                    else:
                        self.strip.solid(tuple(self.cfg["color"]))
                    return
                except (DeviceError, OSError) as e:
                    self.log(f"Strip not ready after {reason} ({e}); retrying")
                    time.sleep(1)


def run_tray(args, cfg):
    import pystray
    from PIL import Image, ImageDraw

    from .power import Dispatcher, PowerWatcher

    ctl = TrayController(args, cfg)

    def act(fn, *a):
        return lambda icon, item: fn(*a)

    def quit_(icon, item):
        watcher.stop()
        ctl.quit()
        icon.stop()

    img = Image.new("RGB", (64, 64), (20, 20, 20))
    d = ImageDraw.Draw(img)
    d.rectangle((6, 18, 58, 46), outline=(0, 200, 255), width=6)

    menu = pystray.Menu(
        pystray.MenuItem("Screen sync", act(ctl.toggle_sync), checked=lambda i: ctl.syncing,
                         default=True),
        pystray.MenuItem("Colour", pystray.Menu(*[pystray.MenuItem(k, act(ctl.set_color, v))
                                                  for k, v in PRESETS.items()])),
        pystray.MenuItem("Brightness", pystray.Menu(*[pystray.MenuItem(f"{p}%", act(ctl.set_brightness, max(1, p * 255 // 100)))
                                                      for p in (100, 75, 50, 25, 10)])),
        pystray.MenuItem("Off", act(ctl.off)),
        pystray.MenuItem("Quit", quit_),
    )
    watcher = PowerWatcher(Dispatcher(ctl.power_down, ctl.power_up,
                                      display=cfg.get("off_with_display", True)))
    if not watcher.start():
        print("Power watcher not running: lights won't switch off automatically at shutdown/sleep.")
    if getattr(args, "sync", False):
        ctl.start_sync()
    else:
        ctl.strip.solid(tuple(cfg["color"]))
    pystray.Icon("ledctl", img, "LED control", menu).run()
