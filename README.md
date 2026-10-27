# ledctl

Open-source, local-only controller for the **DX Light / SyncLight / QuikLight** USB monitor
light bars (Robobloq controller, USB `1A86:FE07`). It replaces the vendor app on Windows with
static colours, brightness, the built-in firmware effects, and **screen sync (ambilight)**.

- **No network code.** It uses USB, screen capture and nothing else.
  Check with `findstr /s /i "socket urllib requests http" ledctl\*.py`, which should find nothing.
- It disables the controller's built-in "act as a USB keyboard and open a URL" feature on connect
  (action `0x93`). Pass `--keep-keyboard` to leave it alone.
- It's light on resources. Colour sampling and encoding take about 0.7 ms per frame
  (about 2% of one core at 30 fps). USB updates are only sent when the colours change.

> Unofficial project, not affiliated with Robobloq or DX Light. Use at your own risk.

## Is my strip supported?
Device Manager → Human Interface Devices → Properties → Details → **Hardware Ids** should show
`VID_1A86&PID_FE07` (or `PID_FE0C`). Tested with an 80-LED bar on firmware 1.8.2.

## Setup (Windows)
1. Install Python 3.10+ from python.org (tick **Add python.exe to PATH**).
2. Download or clone this repo. In the folder that has `setup.bat`, run `setup.bat` once.
3. **Quit DX Light / SyncLight** (including the tray icon). Only one program can drive the strip.
4. Run `ledctl info`, which should print the LED count and firmware.

## Everyday use
| What | Command |
|---|---|
| Tray icon, no console window, sync on | double-click `start-tray.bat` |
| Start automatically at Windows login | `ledctl autostart sync` (or `color` for your saved colour) |
| Turn autostart off | `ledctl autostart off` |
| Fixed colour (saved as your default) | `ledctl color #8000FF` |
| Brightness | `ledctl brightness 120` (1-255) |
| Screen sync in this console | `ledctl sync` (Ctrl+C returns to your colour) |
| Show CPU use and fps | `ledctl sync --stats` |
| Firmware effects | `ledctl effect --type dynamic --index 3 --speed 70` |
| Music via the strip's microphone | `ledctl effect --type music --index 0` |
| Off | `ledctl off` |

The tray menu has screen sync on/off, colour presets, brightness, Off and Quit.
If the tray app hits an error, it's logged to `%APPDATA%\ledctl\ledctl.log`.

## Tuning screen sync
- `ledctl calibrate` lights the LEFT side red, TOP green and RIGHT blue, with the first 3 LEDs white.
  - Mirrored? Run `ledctl config --reverse on`.
  - Corners off? Set LEDs per side with `ledctl config --layout 26 28 26` (left top right [bottom]).
- Look: `--smoothing 0.6` (calmer), `--saturation 1.5` (punchier), `--depth 0.15` (how far in to sample).
- Strip stays dark or flickers in sync? Run `ledctl config --mode split --max-reports 1`.
- Games: borderless/windowed fullscreen is captured. Some exclusive-fullscreen games and DRM video
  (e.g. Netflix) capture as black, which is a Windows restriction.
  `ledctl config --fps 20` lowers the load further.
- Sync samples the edges of the **whole screen**, so YouTube theater mode shows the page, not the video.
  Use full screen.

Settings live in `%APPDATA%\ledctl\config.json`. Use `--dry-run -v` on any command to see the bytes
without touching USB.

## How it works
- `ledctl/protocol.py`: frame building (pure functions, unit-tested)
- `ledctl/device.py`: the only code that touches USB (hidapi)
- `ledctl/screen.py`: capture (dxcam/DXGI with mss fallback), LED layout, summed-area-table sampling
- `ledctl/cli.py`, `ledctl/tray.py`, `ledctl_tray.pyw`: front ends
- [`PROTOCOL.md`](PROTOCOL.md): the USB protocol
- [`AUDIT.md`](AUDIT.md): audit of the pinned dependencies
- `tests/`: `python -m pytest` (protocol vectors, simulated device, capture fallback)

## Credits
The protocol is documented from the interoperability work of these projects. No code is copied from them:
[OpenRGB](https://gitlab.com/CalcProgrammer1/OpenRGB),
[AmbiEdge](https://github.com/KorbSoft/AmbiEdge),
[DynamicLight](https://github.com/mskutin/DynamicLight),
[Open-LumaSync-Linux](https://github.com/Sudaishii/Open-LumaSync-Linux),
[bloqsync](https://github.com/niemanma/bloqsync),
[synclight-macos](https://github.com/DreamX188/synclight-macos),
[hypr-quicklight](https://github.com/shim80/hypr-quicklight),
[OpenLamp](https://github.com/danodoms/openlamp-robobloq-macos).

## License
MIT
