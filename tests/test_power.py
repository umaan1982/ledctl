"""Shutdown / sleep / display-off handling (logic only; the Win32 window is exercised on Windows)."""

import threading

from ledctl import power as W
from ledctl.power import Dispatcher


def make():
    events = []
    d = Dispatcher(lambda r: events.append(("off", r)), lambda r: events.append(("on", r)), log=lambda *_: None)
    return d, events


def test_shutdown_turns_off_and_allows_shutdown():
    d, ev = make()
    assert d.handle(W.WM_QUERYENDSESSION, 0) == 1
    d.handle(W.WM_ENDSESSION, 1)
    assert ev == [("off", "shutdown/sign-out")]  # once, not twice
    d.handle(W.WM_POWERBROADCAST, W.PBT_POWERSETTINGCHANGE, W.DISPLAY_ON)
    assert ev == [("off", "shutdown/sign-out")]  # no turning back on while shutting down


def test_cancelled_shutdown_restores():
    d, ev = make()
    d.handle(W.WM_QUERYENDSESSION, 0)
    d.handle(W.WM_ENDSESSION, 0)
    assert ev == [("off", "shutdown/sign-out"), ("on", "shutdown cancelled")]


def test_sleep_and_wake():
    d, ev = make()
    d.handle(W.WM_POWERBROADCAST, W.PBT_APMSUSPEND)
    d.handle(W.WM_POWERBROADCAST, W.PBT_APMRESUMESUSPEND)
    d.handle(W.WM_POWERBROADCAST, W.PBT_APMRESUMEAUTOMATIC)  # Windows sends both; fire once
    assert ev == [("off", "sleep"), ("on", "wake")]


def test_display_off_on_and_dimmed_ignored():
    d, ev = make()
    d.handle(W.WM_POWERBROADCAST, W.PBT_POWERSETTINGCHANGE, W.DISPLAY_DIMMED)
    d.handle(W.WM_POWERBROADCAST, W.PBT_POWERSETTINGCHANGE, W.DISPLAY_OFF)
    d.handle(W.WM_POWERBROADCAST, W.PBT_POWERSETTINGCHANGE, W.DISPLAY_ON)
    assert ev == [("off", "display off"), ("on", "display on")]


def test_display_events_can_be_disabled():
    ev = []
    d = Dispatcher(lambda r: ev.append(r), lambda r: ev.append(r), display=False, log=lambda *_: None)
    d.handle(W.WM_POWERBROADCAST, W.PBT_POWERSETTINGCHANGE, W.DISPLAY_OFF)
    assert ev == []


def test_handler_errors_dont_propagate():
    def boom(r):
        raise OSError("unplugged")
    d = Dispatcher(boom, boom, log=lambda *_: None)
    assert d.handle(W.WM_QUERYENDSESSION, 0) == 1


def test_other_messages_untouched():
    d, ev = make()
    assert d.handle(0x0001, 0) is None and ev == []
