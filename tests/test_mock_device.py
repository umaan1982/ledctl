"""End-to-end through the CLI with a fake `hid` module standing in for the controller."""

import sys
from pathlib import Path
import threading
import types

import numpy as np
import pytest

from ledctl import protocol as P


class FakeDev:
    def __init__(self):
        self.writes, self.pending = [], []

    def open_path(self, path):
        assert path == b"fake-path"

    def set_nonblocking(self, v):
        pass

    def write(self, rep):
        assert len(rep) == 65 and rep[0] == 0
        self.writes.append(bytes(rep))
        data = bytes(rep[1:])
        if data[:2] == b"RB" and data[4] == P.DEVICE_INFO:
            r = bytearray(64)
            r[:5] = b"RB\x18" + bytes([data[3], 0x82])
            r[11], r[21:24] = 54, b"\x01\x09\x04"
            self.pending.append(list(r))
        return len(rep)

    def read(self, n):
        return self.pending.pop(0) if self.pending else []

    def close(self):
        pass


@pytest.fixture
def fake(monkeypatch, tmp_path):
    dev = FakeDev()
    mod = types.SimpleNamespace(
        device=lambda: dev,
        enumerate=lambda vid, pid: [{"path": b"fake-path", "usage_page": 0xFF00, "vendor_id": vid,
                                     "product_id": pid, "interface_number": 0}] if pid == 0xFE07 else [])
    monkeypatch.setitem(sys.modules, "hid", mod)
    from ledctl import cli
    monkeypatch.setattr(cli, "CONFIG", tmp_path / "config.json")
    return dev


def frames(dev):
    return [w[1:] for w in dev.writes]


def test_color(fake):
    from ledctl.cli import main
    assert main(["color", "#FF8000"]) == 0
    f = frames(fake)
    assert f[0][4] == 0x82 and f[1][4] == 0x93 and f[2][4] == 0x86
    assert f[2][5:15] == bytes([1, 255, 128, 0, 54, 55, 0, 0, 0, 254])


def test_brightness_resends_colour(fake):
    from ledctl.cli import main
    main(["brightness", "100"])
    acts = [f[4] for f in frames(fake)]
    assert acts[-2:] == [0x87, 0x86]


def test_keep_keyboard(fake):
    from ledctl.cli import main
    main(["--keep-keyboard", "off"])
    assert [f[4] for f in frames(fake)] == [0x82, 0x97]


def test_sync_loop(fake, monkeypatch):
    from ledctl import cli, screen

    class FakeCap:
        backend = "fake"

        def __init__(self, *a, **k):
            pass

        def grab(self):
            img = np.zeros((90, 160, 3), np.uint8)
            img[:, :16] = (255, 0, 0)
            return img

        def close(self):
            pass

    monkeypatch.setattr(screen, "Capture", FakeCap)
    stop = threading.Event()
    args = cli.build_parser().parse_args(["sync"])
    cfg = cli.load_config()
    cfg["fps"] = 200
    t = threading.Thread(target=cli.run_sync, args=(args, cfg, stop))
    t.start()
    threading.Timer(0.3, stop.set).start()
    t.join(3)
    sc = [f for f in frames(fake) if f[:2] == b"SC"]
    assert 1 <= len(sc) <= 3  # static picture: sent once (+ keepalive), not 30x/s
    # all-red left side + black elsewhere collapses into a single-report frame
    assert int.from_bytes(sc[0][2:4], "big") <= 64
    assert sc[0][6:10] == bytes([1, 255, 0, 0]) and sc[0][10] >= 18  # left side (+ corner) red
    assert frames(fake)[-1][4] == 0x86  # back to saved colour on exit


def test_capture_falls_back_when_dxcam_breaks(monkeypatch):
    import types as T
    from ledctl import screen

    class BrokenCam:
        def grab(self, **kw):
            raise ModuleNotFoundError("No module named 'cv2'")

        def release(self):
            pass

    calls = {}

    def create(**kw):
        calls.update(kw)
        return BrokenCam()

    monkeypatch.setitem(sys.modules, "dxcam", T.SimpleNamespace(create=create))

    class FakeMss:
        monitors = [None, {"left": 0, "top": 0, "width": 4, "height": 2}]

        def grab(self, mon):
            return np.zeros((2, 4, 4), np.uint8)

    monkeypatch.setitem(sys.modules, "mss", T.SimpleNamespace(mss=FakeMss))
    cap = screen.Capture(backend="auto")
    assert calls["output_color"] == "BGRA"
    assert cap.backend == "mss" and cap.grab().shape == (2, 4, 3)


def test_dxcam_bgra_converted(monkeypatch):
    import types as T
    from ledctl import screen
    frame = np.zeros((4, 8, 4), np.uint8)
    frame[..., 0] = 200  # B
    frame[..., 2] = 10   # R
    cam = T.SimpleNamespace(grab=lambda **k: frame, release=lambda: None)
    monkeypatch.setitem(sys.modules, "dxcam", T.SimpleNamespace(create=lambda **k: cam))
    cap = screen.Capture(backend="auto")
    img = cap.grab()
    assert cap.backend == "dxcam" and tuple(img[0, 0]) == (10, 0, 200)


def test_autostart_registry(monkeypatch, capsys):
    import types as T
    from ledctl import cli
    store = {}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def delete(k, name):
        if name not in store:
            raise FileNotFoundError
        del store[name]

    fake = T.SimpleNamespace(
        HKEY_CURRENT_USER=1, KEY_SET_VALUE=2, KEY_QUERY_VALUE=4, REG_SZ=1,
        OpenKey=lambda *a: Key(), SetValueEx=lambda k, n, r, t, v: store.__setitem__(n, v),
        QueryValueEx=lambda k, n: (store[n], 1) if n in store else (_ for _ in ()).throw(FileNotFoundError()),
        DeleteValue=delete)
    monkeypatch.setitem(sys.modules, "winreg", fake)
    cli.main(["autostart", "sync"])
    assert store["ledctl"].endswith('ledctl_tray.pyw" --sync') and "pythonw" in store["ledctl"]
    cli.main(["autostart", "color"])
    assert not store["ledctl"].endswith("--sync")
    cli.main(["autostart", "off"])
    assert store == {}


def _hold_lock_in_other_process():
    import subprocess
    code = ("import sys; sys.path.insert(0, %r); from ledctl import instance; "
            "instance.acquire(); print('locked', flush=True); sys.stdin.read()") % str(Path(__file__).parents[1])
    p = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "locked"
    return p


def test_single_instance_lock(fake, capsys):
    from ledctl import cli
    p = _hold_lock_in_other_process()  # pretend the tray app is running
    try:
        assert cli.main(["sync"]) == 2
        assert "already running" in capsys.readouterr().err
        assert cli.main(["color", "#00FF00"]) == 0  # one-shot still works, with a note
        assert "tray app is running" in capsys.readouterr().err
    finally:
        p.stdin.close(); p.wait(5)


def test_lock_blocks_second_process(tmp_path):
    import subprocess
    code = ("import sys; sys.path.insert(0, %r); from ledctl import instance; "
            "instance.acquire(); print('locked', flush=True); sys.stdin.read()") % str(Path(__file__).parents[1])
    p = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "locked"
        from ledctl import instance
        assert instance.other_running()
    finally:
        p.stdin.close(); p.wait(5)
    assert not instance.other_running()


def test_snapshot(monkeypatch, tmp_path, capsys):
    from ledctl import cli, screen

    class FakeCap:
        backend = "fake"

        def __init__(self, *a, **k):
            pass

        def grab(self):
            img = np.full((90, 160, 3), 40, np.uint8)
            img[:12, :] = (120, 60, 200)  # purple-ish top bar, like a themed title bar
            return img

        def close(self):
            pass

    monkeypatch.setattr(screen, "Capture", FakeCap)
    monkeypatch.setattr(cli, "CONFIG", tmp_path / "c.json")
    out = tmp_path / "s.png"
    assert cli.main(["snapshot", "--out", str(out)]) == 0
    txt = capsys.readouterr().out
    assert out.exists() and "top" in txt and "left" in txt


class SilentDev(FakeDev):
    """A controller that never answers the info request (seen right after Windows login)."""

    def write(self, rep):
        self.writes.append(bytes(rep))
        return len(rep)


def _install(monkeypatch, dev):
    mod = types.SimpleNamespace(
        device=lambda: dev,
        enumerate=lambda vid, pid: [{"path": b"fake-path", "usage_page": 0xFF00}] if pid == 0xFE07 else [])
    monkeypatch.setitem(sys.modules, "hid", mod)


def test_led_count_remembered_when_info_fails(monkeypatch, tmp_path, capsys):
    from ledctl import cli
    monkeypatch.setattr(cli, "CONFIG", tmp_path / "c.json")
    good = FakeDev()  # first run: strip answers 54... pretend it's an 80-LED bar
    orig = good.write

    def write80(rep):
        n = orig(rep)
        if good.pending:
            good.pending[-1][11] = 80
        return n
    good.write = write80
    _install(monkeypatch, good)
    cli.main(["info"])
    assert cli.load_config()["led_count"] == 80
    silent = SilentDev()
    _install(monkeypatch, silent)
    s = cli.open_strip(cli.build_parser().parse_args(["off"]))
    assert s.led_count == 80  # not the old silent fallback of 54
    assert "remembered 80" in capsys.readouterr().err
    assert sum(1 for w in silent.writes if w[1:3] == b"RB" and w[5] == 0x82) == 3  # retried


def test_info_reply_found_after_unrelated_report(monkeypatch, tmp_path):
    from ledctl import cli
    monkeypatch.setattr(cli, "CONFIG", tmp_path / "c.json")
    dev = FakeDev()
    dev.pending.append(list(b"RB\x07\x09\x87\x01\x00" + bytes(57)))  # stale brightness ack
    _install(monkeypatch, dev)
    s = cli.open_strip(cli.build_parser().parse_args(["off"]))
    assert s.led_count == 54 and s.info.firmware == (1, 9, 4)


def test_sync_survives_capture_failure(fake, monkeypatch):
    from ledctl import cli, screen
    monkeypatch.setattr(cli, "RETRY_S", 0.01)
    calls = {"open": 0, "grab": 0}

    class FlakyCap:
        backend = "fake"

        def __init__(self, *a, **k):
            calls["open"] += 1
            if calls["open"] == 1:
                raise OSError("Access is denied")  # login screen

        def grab(self):
            calls["grab"] += 1
            if calls["grab"] == 2:
                raise OSError("BitBlt failed")      # lock screen
            img = np.zeros((90, 160, 3), np.uint8)
            img[:, :, 0] = calls["grab"] % 200      # keep changing so frames are sent
            return img

        def close(self):
            pass

    monkeypatch.setattr(screen, "Capture", FlakyCap)
    stop = threading.Event()
    cfg = cli.load_config()
    cfg["fps"] = 200
    t = threading.Thread(target=cli.run_sync, args=(cli.build_parser().parse_args(["sync"]), cfg, stop))
    t.start()
    threading.Timer(0.4, stop.set).start()
    t.join(3)
    assert calls["open"] >= 3 and calls["grab"] > 5
    assert sum(1 for f in frames(fake) if f[:2] == b"SC") > 3
