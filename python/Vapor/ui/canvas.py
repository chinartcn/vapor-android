"""帧缓冲绘制封装：Canvas / Surface。"""

from __future__ import annotations

from .. import _native
from . import text as _text

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

    # -- 图元 --
    def clear(self, c):
        self._c.clear(int(c))

    def fill_rect(self, x, y, w, h, c):
        self._c.fill_rect(int(x), int(y), int(w), int(h), int(c))

    rect = fill_rect

    def pixel(self, x, y, c):
        self._c.pixel(int(x), int(y), int(c))

    def line(self, x0, y0, x1, y1, c, width=1):
        self._c.line(int(x0), int(y0), int(x1), int(y1), int(c), int(width))

    def hline(self, x, y, w, c):
        self._c.hline(int(x), int(y), int(w), int(c))

    def vline(self, x, y, h, c):
        self._c.vline(int(x), int(y), int(h), int(c))

    def disc(self, cx, cy, r, c):
        self._c.disc(int(cx), int(cy), int(r), int(c))

    def ring(self, cx, cy, r, c, width=1):
        self._c.ring(int(cx), int(cy), int(r), int(c), int(width))

    def circle(self, cx, cy, r, c, width=1):
        self._c.circle(int(cx), int(cy), int(r), int(c), int(width))

    def polygon(self, points, c):
        self._c.polygon([(int(x), int(y)) for x, y in points], int(c))

    def polyline(self, points, c, width=1, closed=False):
        self._c.polyline([(int(x), int(y)) for x, y in points], int(c), int(width), bool(closed))

    def round_rect(self, x, y, w, h, radius, c):
        self._c.round_rect(int(x), int(y), int(w), int(h), int(radius), int(c))

    def gradient(self, x, y, w, h, c1, c2, vertical=False):
        self._c.gradient(int(x), int(y), int(w), int(h), int(c1), int(c2), bool(vertical))

    def text(self, s, x, y, c, size=16, scale=None):
        _text.draw_text(self, s, x, y, c, size=size, scale=scale)


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
        # 重新分配后 Canvas 仍持有旧指针。
        if self._native and self._rv is not None:
            try:
                self._rv.framebuffer()
            except Exception:
                pass
        return Canvas(self.buffer, self.width, self.height)

    def present(self):
        if self._native and self._rv is not None:
            try:
                self._rv.present()
            except Exception:
                pass

    @property
    def native(self) -> bool:
        return self._native
