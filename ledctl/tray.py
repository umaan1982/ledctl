"""Minimal system-tray front end (pip install pystray pillow)."""

from __future__ import annotations

import threading

from .cli import open_strip, run_sync, save_config

PRESETS = {"Warm white": (255, 140, 60), "Cool white": (200, 220, 255), "Red": (255, 0, 0),
           "Green": (0, 255, 0), "Blue": (0, 0, 255), "Purple": (160, 0, 255), "Cyan": (0, 200, 255)}


PRESETS = {"Purple (mine)": (128, 0, 255), "Lilac": (176, 96, 255), **PRESETS}


def run_tray(args, cfg):
    import pystray
    from PIL import Image, ImageDraw

    strip = open_strip(args)
    state = {"stop": None, "thread": None}

    def stop_sync():
        if state["stop"]:
            state["stop"].set()
            state["thread"].join(timeout=2)
            state["stop"] = state["thread"] = None

    def start_sync(icon=None, item=None):
        if state["thread"]:
            stop_sync()
            return strip.solid(tuple(cfg["color"]))
        state["stop"] = threading.Event()
        state["thread"] = threading.Thread(target=run_sync, args=(args, cfg, state["stop"], strip),
                                           daemon=True)
        state["thread"].start()

    def set_color(rgb):
        def _f(icon, item):
            stop_sync()
            strip.solid(rgb)
            cfg["color"] = list(rgb)
            save_config(cfg)
        return _f

    def set_bright(v):
        def _f(icon, item):
            strip.brightness(v, resend_rgb=None if state["thread"] else tuple(cfg["color"]))
            cfg["brightness"] = v
            save_config(cfg)
        return _f

    def off(icon, item):
        stop_sync()
        strip.off()

    def quit_(icon, item):
        stop_sync()
        strip.solid(tuple(cfg["color"]))
        strip.close()
        icon.stop()

    img = Image.new("RGB", (64, 64), (20, 20, 20))
    d = ImageDraw.Draw(img)
    d.rectangle((6, 18, 58, 46), outline=(0, 200, 255), width=6)

    menu = pystray.Menu(
        pystray.MenuItem("Screen sync", start_sync, checked=lambda i: state["thread"] is not None,
                         default=True),
        pystray.MenuItem("Colour", pystray.Menu(*[pystray.MenuItem(k, set_color(v))
                                                  for k, v in PRESETS.items()])),
        pystray.MenuItem("Brightness", pystray.Menu(*[pystray.MenuItem(f"{p}%", set_bright(max(1, p * 255 // 100)))
                                                      for p in (100, 75, 50, 25, 10)])),
        pystray.MenuItem("Off", off),
        pystray.MenuItem("Quit", quit_),
    )
    if getattr(args, "sync", False):
        start_sync()
    else:
        strip.solid(tuple(cfg["color"]))
    pystray.Icon("ledctl", img, "LED control", menu).run()
