"""Turn the lights off when Windows shuts down, sleeps or turns the display off, and
back on when it returns. Laptops that keep USB powered while off/charging would
otherwise leave the strip lit.

`Dispatcher` holds the decision logic (platform-independent, unit-tested);
`PowerWatcher` feeds it real Windows messages from a hidden window.
"""

from __future__ import annotations

import sys
import threading

WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_POWERBROADCAST = 0x0218
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_POWERSETTINGCHANGE = 0x8013
DISPLAY_OFF, DISPLAY_ON, DISPLAY_DIMMED = 0, 1, 2


class Dispatcher:
    """Maps power messages to on_down(reason) / on_up(reason), firing only on transitions."""

    def __init__(self, on_down, on_up, display: bool = True, log=print):
        self.on_down, self.on_up, self.display, self.log = on_down, on_up, display, log
        self.down = False
        self.shutting_down = False
        self._lock = threading.Lock()

    def _fire(self, down: bool, reason: str) -> None:
        with self._lock:
            if down == self.down or (not down and self.shutting_down):
                return
            self.down = down
        self.log(f"Power event: {reason} -> lights {'off' if down else 'on'}")
        try:
            (self.on_down if down else self.on_up)(reason)
        except Exception as e:  # never let a USB hiccup break shutdown
            self.log(f"Power handler failed: {type(e).__name__}: {e}")

    def handle(self, msg: int, wparam: int, display_state: int | None = None) -> int | None:
        """Returns a value for the window procedure, or None to use the default."""
        if msg == WM_QUERYENDSESSION:
            self.shutting_down = True
            self._fire(True, "shutdown/sign-out")
            return 1  # allow the shutdown
        if msg == WM_ENDSESSION:
            if wparam:
                self.shutting_down = True
                self._fire(True, "shutdown/sign-out")
            else:  # shutdown was cancelled
                self.shutting_down = False
                self._fire(False, "shutdown cancelled")
            return 0
        if msg == WM_POWERBROADCAST:
            if wparam == PBT_APMSUSPEND:
                self._fire(True, "sleep")
            elif wparam in (PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC):
                self._fire(False, "wake")
            elif wparam == PBT_POWERSETTINGCHANGE and self.display and display_state is not None:
                if display_state == DISPLAY_OFF:
                    self._fire(True, "display off")
                elif display_state == DISPLAY_ON:
                    self._fire(False, "display on")
            return 1
        return None


class PowerWatcher:
    """Hidden top-level window (message-only windows don't get these broadcasts)."""

    def __init__(self, dispatcher: Dispatcher):
        self.dispatcher = dispatcher
        self._hwnd = None
        self._thread = None
        self._ready = threading.Event()

    def start(self) -> bool:
        if sys.platform != "win32":
            return False
        self._thread = threading.Thread(target=self._run, name="ledctl-power", daemon=True)
        self._thread.start()
        self._ready.wait(5)
        return self._hwnd is not None

    def stop(self) -> None:
        if self._hwnd:
            import ctypes
            ctypes.windll.user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes as W

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", W.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", W.HINSTANCE), ("hIcon", W.HICON),
                        ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH),
                        ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR)]

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", W.DWORD), ("Data2", W.WORD), ("Data3", W.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]

        class POWERBROADCAST_SETTING(ctypes.Structure):
            _fields_ = [("PowerSetting", GUID), ("DataLength", W.DWORD), ("Data", ctypes.c_ubyte * 4)]

        # GUID_CONSOLE_DISPLAY_STATE {6FE69556-704A-47A0-8F24-C28D936FDA47}
        display_guid = GUID(0x6FE69556, 0x704A, 0x47A0,
                            (ctypes.c_ubyte * 8)(0x8F, 0x24, 0xC2, 0x8D, 0x93, 0x6F, 0xDA, 0x47))

        user32.DefWindowProcW.argtypes = (W.HWND, W.UINT, W.WPARAM, W.LPARAM)
        user32.DefWindowProcW.restype = LRESULT
        user32.RegisterClassW.argtypes = (ctypes.POINTER(WNDCLASSW),)
        user32.RegisterClassW.restype = W.ATOM
        user32.CreateWindowExW.argtypes = (W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND,
                                           W.HMENU, W.HINSTANCE, W.LPVOID)
        user32.CreateWindowExW.restype = W.HWND
        user32.RegisterPowerSettingNotification.argtypes = (W.HANDLE, ctypes.POINTER(GUID), W.DWORD)
        user32.RegisterPowerSettingNotification.restype = W.HANDLE
        user32.UnregisterPowerSettingNotification.argtypes = (W.HANDLE,)
        user32.GetMessageW.argtypes = (ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT)
        user32.GetMessageW.restype = W.BOOL
        user32.TranslateMessage.argtypes = (ctypes.POINTER(W.MSG),)
        user32.DispatchMessageW.argtypes = (ctypes.POINTER(W.MSG),)
        user32.DestroyWindow.argtypes = (W.HWND,)
        user32.PostQuitMessage.argtypes = (ctypes.c_int,)
        kernel32.GetModuleHandleW.argtypes = (W.LPCWSTR,)
        kernel32.GetModuleHandleW.restype = W.HMODULE
        user32.ShutdownBlockReasonCreate.argtypes = (W.HWND, W.LPCWSTR)
        user32.ShutdownBlockReasonDestroy.argtypes = (W.HWND,)
        # Ask to be told about shutdown first (highest app level) so the strip goes off early.
        kernel32.SetProcessShutdownParameters(0x3FF, 0)

        def wndproc(hwnd, msg, wparam, lparam):
            try:
                if msg in (WM_QUERYENDSESSION, WM_ENDSESSION):
                    self.dispatcher.log(f"Session end message 0x{msg:04X} wParam={wparam} lParam=0x{lparam or 0:X}")
                if msg == WM_QUERYENDSESSION:
                    # Shown in Windows' "apps are preventing shutdown" list only if we're slow.
                    user32.ShutdownBlockReasonCreate(hwnd, "Switching off the LED strip")
                    try:
                        return self.dispatcher.handle(msg, wparam) or 1
                    finally:
                        user32.ShutdownBlockReasonDestroy(hwnd)
                display_state = None
                if msg == WM_POWERBROADCAST and wparam == PBT_POWERSETTINGCHANGE and lparam:
                    s = ctypes.cast(lparam, ctypes.POINTER(POWERBROADCAST_SETTING)).contents
                    if bytes(s.PowerSetting) == bytes(display_guid):
                        display_state = s.Data[0]
                res = self.dispatcher.handle(msg, wparam, display_state)
                if res is not None:
                    return res
                if msg == WM_CLOSE:
                    user32.DestroyWindow(hwnd)
                    return 0
                if msg == WM_DESTROY:
                    user32.PostQuitMessage(0)
                    return 0
            except Exception as e:
                self.dispatcher.log(f"Power watcher error: {type(e).__name__}: {e}")
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = WNDPROC(wndproc)  # keep a reference: ctypes callbacks must outlive the window
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW(lpfnWndProc=self._wndproc, hInstance=hinst, lpszClassName="ledctl-power-watch")
        user32.RegisterClassW(ctypes.byref(wc))
        hwnd = user32.CreateWindowExW(0, "ledctl-power-watch", "ledctl power", 0, 0, 0, 0, 0,
                                      None, None, hinst, None)
        if not hwnd:
            self.dispatcher.log(f"Power watcher: CreateWindowExW failed ({ctypes.get_last_error()})")
            self._ready.set()
            return
        notify = user32.RegisterPowerSettingNotification(hwnd, ctypes.byref(display_guid), 0)
        self._hwnd = hwnd
        self._ready.set()
        msg = W.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if notify:
            user32.UnregisterPowerSettingNotification(notify)
        self._hwnd = None
