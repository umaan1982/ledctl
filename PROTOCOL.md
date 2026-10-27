# Robobloq USB LED controller protocol (DX Light / SyncLight / QuikLight)

Compiled for interoperability from seven independent open-source implementations
(OpenRGB's RobobloqLightStripController, AmbiEdge, DynamicLight, Open-LumaSync,
bloqsync, synclight-macos, hypr-quicklight, OpenLamp). Two of these were checked against the
official SyncLight app's JavaScript (`app.asar`). Packets observed from the stock app are reproduced byte for byte in
`tests/test_protocol.py`.

## Transport
- USB HID, VID `1A86`, PID `FE07` (also `FE0C`). Use the interface with **usage page `FF00`**.
  The device also exposes a keyboard interface (see `0x93`).
- Each write is a 65-byte output report: report ID `00` + 64 data bytes, zero padded.
- Frames longer than 64 bytes are split into consecutive reports. Never interleave two frames.

## Frames (checksum = sum of all preceding bytes & 0xFF)
| Family | Layout |
|---|---|
| RB | `52 42 LEN ID ACT payload… CHK`, where LEN = total length (u8) |
| SC | `53 43 LENhi LENlo ID 80 payload… CHK`, where LEN = total length (u16 BE) |

ID: the first is 2, then it cycles 1…254. Colour payload is 5-byte sections
`[first, R, G, B, last]`, 1-based and inclusive. `last = 254` means "to the end".

## Actions
| Act | Name | Payload |
|---|---|---|
| 80 | Screen sync (SC) | sections for the whole strip |
| 82 | Device info | none. Reply: LED count at byte 11, firmware at bytes 21–23, model at bytes 5–7 |
| 85 | Firmware effect | type (2 = dynamic, 3 = onboard-mic music), index 0–6 |
| 86 | Static sections | `01 R G B N N+1 00 00 00 FE` = whole strip, rest cleared |
| 87 | Brightness | 1–255 (0 = max!). The stock app re-sends 86 about 20 ms later in static mode |
| 89 | Auto-off | enabled, duration |
| 8A | Effect speed | 0 = fast, 100 = slow |
| 8B | Mic sensitivity | 5–100 |
| 93 | Keyboard/URL feature | `00` disables it. The controller can act as a USB keyboard that opens the vendor URL |
| 95 | Set LED count | count |
| 97 | Turn off | none. **Not** a keepalive |

## Known variation
Firmware ≤ 1.0.2 has no SC. For that firmware, send 86 sections in chunks of at most 11.
One project (bloqsync, fw 1.9.4 on Linux) reports that its unit did not reassemble SC frames spanning
several reports. `ledctl config --mode split` sends multiple small SC frames instead.
