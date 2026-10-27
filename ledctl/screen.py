"""Screen capture, LED layout and colour sampling."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SIDES = ("left", "top", "right", "bottom")


@dataclass
class Layout:
    """LEDs run clockwise starting at the bottom of the left side:
    left (bottom->top), top (left->right), right (top->bottom), bottom (right->left).
    Sides with 0 LEDs are skipped. reverse=True runs the whole chain the other way."""
    left: int
    top: int
    right: int
    bottom: int = 0
    reverse: bool = False

    @classmethod
    def default(cls, n: int) -> "Layout":
        third = n // 3
        return cls(left=third, top=n - 2 * third, right=third)

    @property
    def count(self) -> int:
        return self.left + self.top + self.right + self.bottom

    def leds(self) -> list[tuple[str, float, float]]:
        """(side, t0, t1) per LED, t measured left->right / top->bottom."""
        out = []
        for side, n, backwards in (("left", self.left, True), ("top", self.top, False),
                                   ("right", self.right, False), ("bottom", self.bottom, True)):
            for i in range(n):
                k = n - 1 - i if backwards else i
                out.append((side, k / n, (k + 1) / n))
        return out[::-1] if self.reverse else out


class Sampler:
    def __init__(self, layout: Layout, depth: float = 0.12):
        self.layout, self.depth = layout, depth
        self._shape = None
        self._slices = []

    def _build(self, h: int, w: int) -> None:
        dy, dx = max(1, int(h * self.depth)), max(1, int(w * self.depth))
        rects = []  # (y0, y1, x0, x1) per LED
        for side, t0, t1 in self.layout.leds():
            if side in ("left", "right"):
                y0, y1 = int(t0 * h), max(int(t0 * h) + 1, int(t1 * h))
                x0, x1 = (0, dx) if side == "left" else (w - dx, w)
            else:
                x0, x1 = int(t0 * w), max(int(t0 * w) + 1, int(t1 * w))
                y0, y1 = (0, dy) if side == "top" else (h - dy, h)
            rects.append((y0, y1, x0, x1))
        r = np.array(rects, dtype=np.intp).reshape(-1, 4)
        self._y0, self._y1, self._x0, self._x1 = r.T
        self._area = ((r[:, 1] - r[:, 0]) * (r[:, 3] - r[:, 2]))[:, None].astype(np.float64)
        self._shape = (h, w)

    def sample(self, img: np.ndarray) -> np.ndarray:
        """img: HxWx3 RGB uint8 -> (n_leds, 3) float. Uses a summed-area table,
        so the cost is one pass over the (small) image regardless of LED count."""
        h, w = img.shape[:2]
        if self._shape != (h, w):
            self._build(h, w)
        img = np.ascontiguousarray(img)
        sat = np.zeros((h + 1, w + 1, 3), np.int64)
        np.cumsum(np.cumsum(img, axis=0, dtype=np.int64), axis=1, out=sat[1:, 1:])
        y0, y1, x0, x1 = self._y0, self._y1, self._x0, self._x1
        sums = sat[y1, x1] - sat[y0, x1] - sat[y1, x0] + sat[y0, x0]
        return (sums / self._area).astype(np.float32)


class Processor:
    """Saturation boost, gamma, black cutoff and exponential smoothing."""

    def __init__(self, smoothing: float = 0.5, saturation: float = 1.3,
                 gamma: float = 1.0, black: int = 8):
        self.smoothing, self.saturation, self.gamma, self.black = smoothing, saturation, gamma, black
        self._prev = None

    def __call__(self, c: np.ndarray) -> np.ndarray:
        if self.saturation != 1.0:
            grey = c.mean(axis=1, keepdims=True)
            c = grey + (c - grey) * self.saturation
        c = np.clip(c, 0, 255)
        if self.gamma != 1.0:
            c = 255.0 * (c / 255.0) ** self.gamma
        c[c.max(axis=1) < self.black] = 0
        if self._prev is not None and self._prev.shape == c.shape and self.smoothing > 0:
            c = self._prev * self.smoothing + c * (1 - self.smoothing)
        self._prev = c
        return c


class Capture:
    """dxcam (DXGI, fast, Windows) if installed, else mss (GDI, works everywhere).
    Returns a downscaled RGB frame."""

    def __init__(self, monitor: int = 1, width: int = 192, backend: str = "auto"):
        self.width, self.monitor = width, monitor
        self.backend, self._last, self._cam = None, None, None
        dxcam = None
        if backend in ("auto", "dxcam"):
            try:
                import dxcam  # optional
            except ImportError:
                if backend == "dxcam":
                    raise
        if dxcam is not None:
            try:
                # BGRA = raw desktop format; avoids dxcam's OpenCV dependency.
                self._cam = dxcam.create(output_idx=monitor - 1, output_color="BGRA")
                if self._cam is None:
                    raise RuntimeError("dxcam could not open the display")
                self.backend = "dxcam"
                self._grab_dxcam()  # probe now so failures fall back immediately
            except Exception as e:
                if backend == "dxcam":
                    raise
                self._fallback(e)
        if self.backend is None:
            self._open_mss()

    def _open_mss(self) -> None:
        import mss
        self._mss = mss.mss()
        self._mon = self._mss.monitors[self.monitor]
        self.backend = "mss"

    def _fallback(self, err: Exception) -> None:
        print(f"Fast capture (dxcam) unavailable ({type(err).__name__}: {err}); using mss.")
        self.close()
        self.backend = None
        self._open_mss()

    def _shrink(self, img: np.ndarray) -> np.ndarray:
        step = max(1, img.shape[1] // self.width)
        return img[::step, ::step]

    def _grab_dxcam(self) -> np.ndarray | None:
        img = self._cam.grab(copy=False)  # view of dxcam's buffer; we copy only the thumbnail
        if img is None:          # screen unchanged since last grab
            return self._last
        self._last = np.ascontiguousarray(self._shrink(img)[:, :, 2::-1])
        return self._last

    def grab(self) -> np.ndarray | None:
        if self.backend == "dxcam":
            try:
                return self._grab_dxcam()
            except Exception as e:
                self._fallback(e)
        raw = np.asarray(self._mss.grab(self._mon))  # BGRA
        return self._shrink(raw)[:, :, 2::-1]

    def close(self) -> None:
        if self.backend == "dxcam" and self._cam is not None:
            try:
                self._cam.release()
            except Exception:
                pass
