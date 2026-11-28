"""帧缓冲绘制封装：Canvas / Surface。"""

from __future__ import annotations

from .. import _native
from . import text as _text
from .dirty import ledger

_core = _native.core

__all__ = ["Canvas", "Surface"]


def _buffer_address(buffer):
    """取可写 C 连续缓冲的 (地址, 长度)：原生 Canvas 直接按地址写内存。

    这样 Rust 侧不需要依赖缓冲协议绑定（PyO3 0.30 在 abi3 下不提供），
    Python 侧用 ctypes 拿地址，帧缓冲本身仍然由 Python 对象持有。
    """
    import ctypes

    view = memoryview(buffer)
    if view.readonly:
        raise TypeError("帧缓冲必须是可写缓冲区")
    if not view.c_contiguous:
        raise TypeError("帧缓冲必须是 C 连续缓冲区")
    return ctypes.addressof(ctypes.c_char.from_buffer(view)), view.nbytes


class Canvas:
    """帧缓冲绘制封装：坐标统一取整，原生可用时走 Rust 光栅化。"""

    __slots__ = ("_c", "width", "height", "buffer", "address", "length")

    def __init__(self, buffer, width, height):
        self.width, self.height = int(width), int(height)
        self.buffer = buffer
        if getattr(_core, "FALLBACK", False):
            self.address, self.length = 0, 0
            self._c = _core.Canvas(buffer, self.width, self.height)
        else:
            self.address, self.length = _buffer_address(buffer)
            self._c = _core.Canvas(self.address, self.length, self.width, self.height)

    # -- 脏区 --
    def _mark(self, x, y, w, h):
        ledger.mark_rect(x, y, w, h)

    # -- 图元 --
    def clear(self, c):
        self._c.clear(int(c))
        ledger.mark_full()          # clear 是全局脏

    def fill_rect(self, x, y, w, h, c):
        x, y, w, h = int(x), int(y), int(w), int(h)
        self._c.fill_rect(x, y, w, h, int(c))
        self._mark(x, y, w, h)

    rect = fill_rect

    def pixel(self, x, y, c):
        x, y = int(x), int(y)
        self._c.pixel(x, y, int(c))
        self._mark(x, y, 1, 1)

    def line(self, x0, y0, x1, y1, c, width=1):
        x0, y0, x1, y1, width = int(x0), int(y0), int(x1), int(y1), int(width)
        self._c.line(x0, y0, x1, y1, int(c), width)
        bw = max(1, width)
        self._mark(min(x0, x1) - bw, min(y0, y1) - bw,
                   abs(x1 - x0) + 2 * bw, abs(y1 - y0) + 2 * bw)

    def hline(self, x, y, w, c):
        x, y, w = int(x), int(y), int(w)
        self._c.hline(x, y, w, int(c))
        self._mark(x, y, w, 1)

    def vline(self, x, y, h, c):
        x, y, h = int(x), int(y), int(h)
        self._c.vline(x, y, h, int(c))
        self._mark(x, y, 1, h)

    def disc(self, cx, cy, r, c):
        cx, cy, r = int(cx), int(cy), int(r)
        self._c.disc(cx, cy, r, int(c))
        self._mark(cx - r, cy - r, 2 * r, 2 * r)

    def ring(self, cx, cy, r, c, width=1):
        cx, cy, r, width = int(cx), int(cy), int(r), int(width)
        self._c.ring(cx, cy, r, int(c), width)
        pad = max(1, width)
        self._mark(cx - r - pad, cy - r - pad, 2 * (r + pad), 2 * (r + pad))

    def circle(self, cx, cy, r, c, width=1):
        cx, cy, r, width = int(cx), int(cy), int(r), int(width)
        self._c.circle(cx, cy, r, int(c), width)
        pad = max(1, width)
        self._mark(cx - r - pad, cy - r - pad, 2 * (r + pad), 2 * (r + pad))

    def polygon(self, points, c, fill_rule="nonzero"):
        pts = [(int(x), int(y)) for x, y in points]
        self._c.polygon(pts, int(c), fill_rule)
        _mark_points(self, pts, 0)

    def polyline(self, points, c, width=1, closed=False):
        pts = [(int(x), int(y)) for x, y in points]
        width = int(width)
        self._c.polyline(pts, int(c), width, bool(closed))
        _mark_points(self, pts, max(1, width))

    def round_rect(self, x, y, w, h, radius, c):
        x, y, w, h, radius = int(x), int(y), int(w), int(h), int(radius)
        self._c.round_rect(x, y, w, h, radius, int(c))
        self._mark(x, y, w, h)

    def gradient(self, x, y, w, h, c1, c2, vertical=False):
        x, y, w, h = int(x), int(y), int(w), int(h)
        self._c.gradient(x, y, w, h, int(c1), int(c2), bool(vertical))
        self._mark(x, y, w, h)

    def text(self, s, x, y, c, size=16, scale=None):
        _text.draw_text(self, s, x, y, c, size=size, scale=scale)


def _mark_points(canvas, points, pad):
    if not points:
        return
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, x1 = min(xs) - pad, max(xs) + pad
    y0, y1 = min(ys) - pad, max(ys) + pad
    canvas._mark(x0, y0, x1 - x0, y1 - y0)


class Surface:
    """帧缓冲来源。原生可用时用 rust.Vapor 的共享内存，否则用内存缓冲。"""

    def __init__(self, width=None, height=None, use_native=True):
        self._native = False
        self._rv = None
        buf = None
        w = h = 0
        if use_native:
            try:
                import rust.Vapor as rv

                if width and height:
                    try:
                        rv.resize(int(width), int(height))
                    except Exception:
                        rv.init(width=int(width), height=int(height))
                buf = rv.framebuffer()
                w, h = rv.size()
                self._rv = rv
                self._native = True
            except Exception:
                buf = None
        if buf is None:
            w = int(width or 360)
            h = int(height or 640)
            buf = bytearray(w * h * 4)
        self.width, self.height = int(w), int(h)
        self.buffer = buf

    def canvas(self) -> Canvas:
        # 每帧重新加锁：Rust 在 lock→unlock 期间拒绝 resize，避免帧缓冲被
        # 重新分配后 Canvas 仍持有旧指针；同时刷新 buffer/size，防止 resize 后
        # 继续使用已释放的旧 memoryview（UAF）。
        if self._native and self._rv is not None:
            try:
                self.buffer = self._rv.framebuffer()
                w, h = self._rv.size()
                self.width, self.height = int(w), int(h)
            except Exception:
                pass
        return Canvas(self.buffer, self.width, self.height)

    def present(self, rects=None):
        if self._native and self._rv is not None:
            try:
                dirty = getattr(self._rv, "present_dirty", None) if rects else None
                if dirty is not None:
                    dirty(rects)
                else:
                    # 空/None 或旧版绑定 → 全帧（安全兜底）。
                    self._rv.present()
            except Exception:
                pass

    @property
    def native(self) -> bool:
        return self._native
