"""Byte-for-byte checks against packets observed from the official app and
against independent open-source implementations (cloned to ../re for the
cross-check tests; those are skipped if absent)."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from ledctl import protocol as P
from ledctl.screen import Layout, Processor, Sampler

RE = Path(__file__).resolve().parents[2] / "re"


def test_observed_init_packets():
    # Captured from the stock app (DynamicLight's protocol tests).
    assert P.cmd_device_info(2) == bytes([0x52, 0x42, 6, 2, 0x82, 0x1E])
    assert P.cmd_disable_keyboard_url(3) == bytes([0x52, 0x42, 7, 3, 0x93, 0, 0x31])


def test_seq_matches_stock():
    s = P.Seq()
    ids = [s() for _ in range(260)]
    assert ids[0] == 2 and max(ids) == 254 and min(ids) == 1 and 0 not in ids


def test_solid_matches_openlamp_layout():
    # OpenLamp: 86 01 r g b n n+1 00 00 00 FE
    f = P.cmd_solid(5, (10, 20, 30), 54)
    assert f[4:-1] == bytes([0x86, 1, 10, 20, 30, 54, 55, 0, 0, 0, 0xFE])
    assert f[2] == len(f) and f[-1] == sum(f[:-1]) & 0xFF


def test_reports_padded():
    reps = P.reports(bytes(range(100)))
    assert [len(r) for r in reps] == [65, 65]
    assert all(r[0] == 0 for r in reps)
    assert reps[1][1:37] == bytes(range(64, 100)) and reps[1][37:] == bytes(28)


def test_sc_frame_and_split():
    cols = [(i, 255 - i, i * 2 % 256) for i in range(54)]  # all distinct
    one = P.screen_frames(P.Seq(), cols, "chunked")
    assert len(one) == 1 and one[0][:2] == b"SC"
    assert int.from_bytes(one[0][2:4], "big") == len(one[0]) == 7 + 54 * 5
    split = P.screen_frames(P.Seq(), cols, "split")
    assert len(split) == 5 and all(len(f) <= 64 for f in split)
    # same sections either way
    assert b"".join(f[6:-1] for f in split) == one[0][6:-1]
    fit = P.screen_frames(P.Seq(), cols, "split", max_reports=1)
    assert len(fit) == 1 and len(fit[0]) <= 64


def test_collapse_merges_runs():
    cols = [(1, 2, 3)] * 10 + [(9, 9, 9)] * 5
    assert P.collapse(cols) == [P.Section(1, 10, (1, 2, 3)), P.Section(11, 15, (9, 9, 9))]


def test_parse_info():
    resp = bytearray(64)
    resp[:5] = b"RB\x18\x02\x82"
    resp[5:8] = b"\x01\x02\x03"
    resp[11] = 54
    resp[21:24] = b"\x01\x09\x04"
    info = P.parse_device_info(bytes(resp))
    assert info.led_count == 54 and info.firmware_str == "1.9.4" and info.model == "010203"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(not (RE / "src_KorbSoft_AmbiEdge").exists(), reason="reference not cloned")
def test_matches_ambiedge():
    sys.path.insert(0, str(RE / "src_KorbSoft_AmbiEdge"))
    from ambiedge.drivers import robobloq as A
    cols = [(i * 4 % 256, 30, 200 - i) for i in range(54)]
    ranges = A.color_ranges(np.array(cols))
    assert A.sc_packet(7, ranges) == P.sc(7, P.encode_sections(P.collapse(cols)))
    assert A.rb_packet(9, 0x82) == P.rb(9, 0x82)


def test_matches_synclight_macos_encoding_inline():
    # synclight-macos encode_led_colors: [n, r, g, b, n] per LED, 1-based
    cols = [(1, 2, 3), (4, 5, 6)]
    secs = [P.Section(i + 1, i + 1, c) for i, c in enumerate(cols)]
    assert P.encode_sections(secs) == bytes([1, 1, 2, 3, 1, 2, 4, 5, 6, 2])


def test_layout_and_sampler():
    lay = Layout.default(54)
    assert (lay.left, lay.top, lay.right) == (18, 18, 18)
    leds = lay.leds()
    assert leds[0][0] == "left" and leds[0][1] > 0.9  # starts bottom-left
    assert leds[18][0] == "top" and leds[18][1] == 0  # top runs left->right
    img = np.zeros((100, 200, 3), np.uint8)
    img[:, :20] = (255, 0, 0)      # left edge red
    img[:10, :] = (0, 255, 0)      # top edge green
    img[:, 180:] = (0, 0, 255)     # right edge blue
    c = Sampler(lay, depth=0.1).sample(img)
    assert c[5].argmax() == 0 and c[27].argmax() == 1 and c[45].argmax() == 2
    out = Processor(smoothing=0)(c)
    assert out.shape == (54, 3)
    assert Layout.default(54).leds()[::-1] == Layout(18, 18, 18, reverse=True).leds()
