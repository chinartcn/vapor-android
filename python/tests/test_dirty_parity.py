"""脏区渲染一致性 / 局部提交接线测试。

核心断言：同一份界面在 dirty_rects=False（全帧）与 True（脏区管线）下
渲染出的帧缓冲逐字节一致；并验证 Canvas 标记与 Surface.present 的转发。

运行：PYTHONPATH=python python3 -m pytest python/tests/test_dirty_parity.py
"""

from __future__ import annotations

import os
import sys

try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Vapor import ui as V           # noqa: E402
from Vapor.ui.canvas import Surface  # noqa: E402
from Vapor.ui.dirty import ledger    # noqa: E402


def _scene(holder):
    class Root(V.Component):
        template = (
            '<View w="200" h="120" bg="#101418">'
            '<View bg="#FF0000" w="60" h="40" radius="8" />'
            '<Text :size="20" color="#FFFFFF">{{ label }}</Text>'
            '</View>'
        )

        def setup(self, props, ctx):
            holder["label"] = V.ref("AB")
            return {"label": holder["label"]}

    return Root


def _render(dirty):
    holder = {}
    surface = Surface(200, 120, use_native=False)
    app = V.App(_scene(holder), surface=surface, dirty_rects=dirty)
    app.mount()
    first = bytes(surface.buffer)
    holder["label"].value = "CD"
    app.tick()
    second = bytes(surface.buffer)
    app.unmount()
    return first, second


def test_dirty_and_full_render_identical():
    full_first, full_second = _render(False)
    dirty_first, dirty_second = _render(True)
    assert full_first == dirty_first
    assert full_second == dirty_second
    assert full_first != full_second        # 确认动态更新确实生效


def test_canvas_marks_drawn_rect():
    surface = Surface(100, 100, use_native=False)
    ledger.set_canvas(100, 100)
    ledger.end_frame()                      # 脱离全帧兜底
    ledger.begin_frame()
    canvas = surface.canvas()
    canvas.fill_rect(10, 10, 20, 20, V.rgb(255, 255, 255))
    assert ledger.rects() == [(10, 10, 20, 20)]
    ledger.end_frame()


def test_clear_marks_full():
    surface = Surface(64, 64, use_native=False)
    ledger.set_canvas(64, 64)
    ledger.end_frame()
    ledger.begin_frame()
    surface.canvas().clear(V.rgb(0, 0, 0))
    assert ledger.is_full
    assert ledger.rects() == []
    ledger.end_frame()


def test_surface_present_forwards_dirty():
    class FakeRV:
        def __init__(self):
            self.calls = []

        def present(self):
            self.calls.append(("full",))

        def present_dirty(self, rects):
            self.calls.append(("dirty", list(rects)))

    surface = Surface.__new__(Surface)
    surface._native = True
    surface._rv = FakeRV()

    surface.present([(1, 2, 3, 4)])
    surface.present([])
    surface.present()
    assert surface._rv.calls == [
        ("dirty", [(1, 2, 3, 4)]),
        ("full",),
        ("full",),
    ]
