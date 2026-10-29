"""Make sure only one ledctl drives the strip, and spot the vendor app."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

MUTEX_NAME = "Local\\ledctl-strip-owner"
VENDOR_EXES = ("dxlight", "dx light", "dx-light", "synclight", "quiklight")

_held = None  # keeps the lock alive for the life of the process


class AlreadyRunning(RuntimeError):
    pass


def acquire() -> None:
    """Take the single-instance lock or raise AlreadyRunning."""
    global _held
    if _held is not None:
        return
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
        ctypes.set_last_error(0)  # CreateMutexW doesn't clear it on success
        h = k32.CreateMutexW(None, False, MUTEX_NAME)
        if not h:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            k32.CloseHandle(ctypes.c_void_p(h))
            raise AlreadyRunning()
        _held = h
    else:
        import fcntl
        f = open(os.path.join(tempfile.gettempdir(), "ledctl.lock"), "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            raise AlreadyRunning()
        _held = f


def release() -> None:
    global _held
    if _held is None:
        return
    if sys.platform == "win32":
        import ctypes
        ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(_held))
    else:
        _held.close()
    _held = None


def other_running() -> bool:
    """True if another ledctl currently holds the lock (doesn't keep it)."""
    try:
        acquire()
    except AlreadyRunning:
        return True
    release()
    return False


def vendor_running() -> str | None:
    """Name of a running DX Light / SyncLight process, if any (Windows only)."""
    if sys.platform != "win32":
        return None
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                             timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        name = line.split(",")[0].strip('"')
        if any(v in name.lower() for v in VENDOR_EXES):
            return name
    return None
