"""ledctl: local-only controller for DX Light / SyncLight USB monitor light bars."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

from . import protocol as P
from .device import DeviceError, Strip, list_interfaces

CONFIG = Path(os.environ.get("APPDATA", Path.home() / ".config")) / "ledctl" / "config.json"
DEFAULTS = {"layout": None, "reverse": False, "mode": "chunked", "max_reports": 0,
            "fps": 30, "smoothing": 0.5, "saturation": 1.3, "gamma": 1.0, "depth": 0.12,
            "monitor": 1, "brightness": 255, "backend": "auto", "color": [128, 0, 255]}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG.read_text()))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2))


def parse_color(vals: list[str]) -> tuple[int, int, int]:
    if len(vals) == 1:
        h = vals[0].lstrip("#")
        if len(h) != 6:
            raise argparse.ArgumentTypeError("use #RRGGBB or R G B")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    if len(vals) == 3:
        return tuple(max(0, min(255, int(v))) for v in vals)
    raise argparse.ArgumentTypeError("use #RRGGBB or R G B")


def open_strip(args) -> Strip:
    log = (lambda f: print("TX", f.hex(" "))) if args.verbose or args.dry_run else None
    s = Strip(dry_run=args.dry_run, log=log)
    info = s.handshake(disable_keyboard=not args.keep_keyboard)
    if args.dry_run and info is None:
        s.info = P.DeviceInfo(args.leds or 54, (1, 9, 4), "DRYRUN", b"")
    elif args.leds:
        s.info = P.DeviceInfo(args.leds, info.firmware if info else (0, 0, 0), "", b"")
    return s


def make_layout(cfg: dict, n: int):
    from .screen import Layout
    if cfg.get("layout"):
        parts = [int(x) for x in cfg["layout"]]
        parts += [0] * (4 - len(parts))
        return Layout(*parts[:4], reverse=bool(cfg.get("reverse")))
    lay = Layout.default(n)
    lay.reverse = bool(cfg.get("reverse"))
    return lay


# ---- commands ---------------------------------------------------------------

def cmd_list(args, cfg):
    devs = list_interfaces()
    if not devs:
        print("No 1A86:FE07/FE0C devices found.")
    for d in devs:
        mark = "  <- control interface" if d.get("usage_page") == P.VENDOR_USAGE_PAGE else ""
        print(f"{d['vendor_id']:04X}:{d['product_id']:04X} if={d.get('interface_number')} "
              f"usage_page={d.get('usage_page', 0):04X} {d.get('product_string') or ''}{mark}")


def cmd_info(args, cfg):
    s = open_strip(args)
    if s.info:
        print(f"LEDs: {s.info.led_count}  firmware: {s.info.firmware_str}  model: {s.info.model}")
        print(f"raw: {s.info.raw.hex(' ')}")
    else:
        print("Controller opened but didn't answer the info request.")
    s.close()


def cmd_color(args, cfg):
    rgb = parse_color(args.rgb)
    s = open_strip(args)
    s.solid(rgb)
    cfg["color"] = list(rgb)
    save_config(cfg)
    s.close()


def cmd_brightness(args, cfg):
    s = open_strip(args)
    s.brightness(args.value, resend_rgb=None if args.no_resend else tuple(cfg["color"]))
    cfg["brightness"] = args.value
    save_config(cfg)
    s.close()


def cmd_off(args, cfg):
    s = open_strip(args)
    s.off()
    s.close()


def cmd_effect(args, cfg):
    s = open_strip(args)
    s.effect(2 if args.type == "dynamic" else 3, args.index, args.speed)
    s.close()


def cmd_calibrate(args, cfg):
    s = open_strip(args)
    n = s.led_count or 54
    lay = make_layout(cfg, n)
    colors = []
    side_rgb = {"left": (255, 0, 0), "top": (0, 255, 0), "right": (0, 0, 255), "bottom": (255, 0, 255)}
    for i, (side, _, _) in enumerate(lay.leds()):
        colors.append((255, 255, 255) if i < 3 else side_rgb[side])
    colors += [(0, 0, 0)] * max(0, n - len(colors))
    s.frame(colors, cfg["mode"], cfg["max_reports"])
    print(f"Strip reports {n} LEDs; layout L={lay.left} T={lay.top} R={lay.right} B={lay.bottom}"
          f"{' reversed' if lay.reverse else ''}.")
    print("Expected: LEFT red, TOP green, RIGHT blue, first 3 LEDs WHITE at the bottom of the left side.")
    print("If it's mirrored:   ledctl config --reverse on")
    print("If borders are off: ledctl config --layout L T R [B]   (counts per side)")
    s.close()


def cmd_config(args, cfg):
    if args.layout:
        cfg["layout"] = args.layout
    if args.auto_layout:
        cfg["layout"] = None
    for k in ("mode", "max_reports", "fps", "smoothing", "saturation", "gamma", "depth",
              "monitor", "backend"):
        v = getattr(args, k)
        if v is not None:
            cfg[k] = v
    if args.reverse is not None:
        cfg["reverse"] = args.reverse == "on"
    save_config(cfg)
    print(f"{CONFIG}\n{json.dumps(cfg, indent=2)}")


def run_sync(args, cfg, stop: threading.Event, strip: Strip | None = None):
    from .screen import Capture, Processor, Sampler
    s = strip or open_strip(args)
    n = s.led_count or 54
    lay = make_layout(cfg, n)
    sampler = Sampler(lay, cfg["depth"])
    proc = Processor(cfg["smoothing"], cfg["saturation"], cfg["gamma"])
    cap = Capture(cfg["monitor"], backend=cfg["backend"])
    s.solid(tuple(cfg["color"]))  # wake the strip in case it was switched off
    if cfg.get("brightness"):
        s.brightness(cfg["brightness"])
    print(f"Screen sync: {n} LEDs, capture={cap.backend}, mode={cfg['mode']}, "
          f"{cfg['fps']} fps. Ctrl+C to stop.")
    period = 1.0 / max(1, cfg["fps"])
    pad = max(0, n - lay.count)
    frames, sent, t_stat, cpu_stat = 0, 0, time.monotonic(), time.process_time()
    last, last_sent = None, 0.0
    stats = getattr(args, "stats", False) or args.verbose
    try:
        while not stop.is_set():
            t0 = time.monotonic()
            img = cap.grab()
            if img is not None:
                cols = [tuple(c) for c in proc(sampler.sample(img)).astype(int).tolist()]
                frames += 1
                # Only talk USB when the colours changed (or once a second as a keepalive).
                if cols != last or t0 - last_sent > 1.0:
                    s.frame(cols + [(0, 0, 0)] * pad, cfg["mode"], cfg["max_reports"])
                    last, last_sent = cols, t0
                    sent += 1
            if stats and time.monotonic() - t_stat > 5:
                dt = time.monotonic() - t_stat
                cpu = (time.process_time() - cpu_stat) / dt * 100
                print(f"{frames / dt:.1f} fps captured, {sent / dt:.1f} updates/s sent, "
                      f"CPU {cpu:.1f}% of one core")
                frames, sent, t_stat, cpu_stat = 0, 0, time.monotonic(), time.process_time()
            stop.wait(max(0.0, period - (time.monotonic() - t0)))
    finally:
        cap.close()
        if strip is None:
            s.solid(tuple(cfg["color"]))  # back to your saved colour
            s.close()


def cmd_sync(args, cfg):
    for k in ("fps", "mode", "max_reports", "monitor", "smoothing", "saturation"):
        v = getattr(args, k, None)
        if v is not None:
            cfg[k] = v
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    run_sync(args, cfg, stop)


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def cmd_autostart(args, cfg):
    try:
        import winreg
    except ImportError:
        print("Autostart is Windows-only.")
        return
    root = Path(__file__).resolve().parents[1]
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as k:
        if args.what == "off":
            try:
                winreg.DeleteValue(k, "ledctl")
            except FileNotFoundError:
                pass
            print("Autostart removed.")
            return
        if args.what == "status":
            try:
                print("Autostart:", winreg.QueryValueEx(k, "ledctl")[0])
            except FileNotFoundError:
                print("Autostart is off.")
            return
        launcher = root / "ledctl_tray.pyw"
        cmd = f'"{pythonw}" "{launcher}"' + (" --sync" if args.what == "sync" else "")
        winreg.SetValueEx(k, "ledctl", 0, winreg.REG_SZ, cmd)
        print(f"Autostart on ({'tray + screen sync' if args.what == 'sync' else 'tray + your saved colour'}).")
        print("Remove with: ledctl autostart off")


def cmd_tray(args, cfg):
    from .tray import run_tray
    run_tray(args, cfg)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ledctl", description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="don't touch USB; print frames")
    p.add_argument("-v", "--verbose", action="store_true", help="print every frame sent")
    p.add_argument("--leds", type=int, help="override LED count reported by the strip")
    p.add_argument("--keep-keyboard", action="store_true",
                   help="don't disable the controller's keyboard/URL feature")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list matching USB interfaces").set_defaults(fn=cmd_list)
    sub.add_parser("info", help="read LED count and firmware").set_defaults(fn=cmd_info)
    c = sub.add_parser("color", help="static colour: #RRGGBB or R G B")
    c.add_argument("rgb", nargs="+")
    c.set_defaults(fn=cmd_color)
    b = sub.add_parser("brightness", help="1-255")
    b.add_argument("value", type=int)
    b.add_argument("--no-resend", action="store_true")
    b.set_defaults(fn=cmd_brightness)
    sub.add_parser("off").set_defaults(fn=cmd_off)
    e = sub.add_parser("effect", help="built-in firmware effects")
    e.add_argument("--type", choices=("dynamic", "music"), default="dynamic")
    e.add_argument("--index", type=int, default=0, choices=range(7))
    e.add_argument("--speed", type=int, help="0-100")
    e.set_defaults(fn=cmd_effect)
    sub.add_parser("calibrate", help="colour each side to check layout").set_defaults(fn=cmd_calibrate)

    cf = sub.add_parser("config", help="save settings")
    cf.add_argument("--layout", type=int, nargs="+", metavar="N", help="LEDs per side: L T R [B]")
    cf.add_argument("--auto-layout", action="store_true")
    cf.add_argument("--reverse", choices=("on", "off"))
    cf.add_argument("--mode", choices=("chunked", "split"))
    cf.add_argument("--max-reports", type=int)
    cf.add_argument("--fps", type=int)
    cf.add_argument("--smoothing", type=float)
    cf.add_argument("--saturation", type=float)
    cf.add_argument("--gamma", type=float)
    cf.add_argument("--depth", type=float)
    cf.add_argument("--monitor", type=int)
    cf.add_argument("--backend", choices=("auto", "dxcam", "mss"))
    cf.set_defaults(fn=cmd_config)

    sy = sub.add_parser("sync", help="screen sync (ambilight)")
    sy.add_argument("--fps", type=int)
    sy.add_argument("--mode", choices=("chunked", "split"))
    sy.add_argument("--max-reports", type=int)
    sy.add_argument("--monitor", type=int)
    sy.add_argument("--smoothing", type=float)
    sy.add_argument("--saturation", type=float)
    sy.add_argument("--stats", action="store_true", help="print fps and CPU use every 5 s")
    sy.set_defaults(fn=cmd_sync)
    tr = sub.add_parser("tray", help="system tray app")
    tr.add_argument("--sync", action="store_true", help="start with screen sync on")
    tr.set_defaults(fn=cmd_tray)
    au = sub.add_parser("autostart", help="start the tray app at Windows login")
    au.add_argument("what", choices=("sync", "color", "off", "status"))
    au.set_defaults(fn=cmd_autostart)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.fn(args, load_config())
    except DeviceError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
