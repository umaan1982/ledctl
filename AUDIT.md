# Dependency audit (2026-10-27)

Every third-party package `setup.bat` installs was checked at the pinned version, using the
Windows / Python 3.12 wheels:
hidapi 0.15.0, numpy 2.5.4, mss 10.2.0, pystray 0.19.5, pillow 13.0.0, six 1.17.0,
dxcam 0.3.0, comtypes 1.4.17.

| Check | Result |
|---|---|
| SHA-256 of each wheel matches PyPI | 8/8 match. None yanked, no known vulnerabilities listed |
| PyPI build provenance (Trusted Publishing attestations) | numpy, pillow and dxcam were built by their own GitHub Actions. The rest predate or don't use attestations |
| Wheel RECORD manifest (every file's hash, no extra files) | 8/8 intact |
| `.pth` auto-run files / install-time code | None. Wheels run no code at install |
| Pure-Python files vs the project's tagged GitHub source | mss, pystray, six, dxcam, comtypes: 203/203 files byte-identical |
| Network modules in runtime Python code | None used by ledctl. numpy's `DataSource` downloads only when explicitly given a URL. mss uses `urllib.parse` (string parsing) on Linux only |
| Dynamic exec/eval | Only documented features (comtypes type-library codegen, six py2 shim, Pillow ImageMath, numpy f2py), none on ledctl's code path |
| Native binaries (31 .pyd/.dll): imported system DLLs | No networking DLLs (ws2_32, wininet, winhttp, urlmon…). hidapi loads only hid.dll and cfgmgr32.dll |
| URLs inside binaries | Documentation links, Microsoft code-signing certificate chain (msvcp140.dll), Brotli's standard dictionary (FreeType) |

Notes: OpenBLAS (numpy) imports `OpenProcess`/`GetProcessAffinityMask` for CPU-thread
detection. pystray writes its tray icon to a temp file and deletes it.
Limits: compiled code was checked by import tables and strings, not disassembled. numpy and
pillow were not diffed line by line (they're huge), but their wheels carry PyPI provenance
from their official CI.
