"""脏区响应式标记测试（执行文档 B）。

    PYTHONPATH=python python3 -m pytest python/tests/test_dirty_reactive.py -q

文档 A 的 ui/dirty.py 尚未合入时，各模块使用空账本；测试通过替换各模块的
`ledger` 为记录型 Spy 来验证标记行为（不依赖 A 的真实实现）。
"""

from __future__ import annotations

import os
import sys

try:  # 已安装时优先用安装版
    import Vapor  # noqa: F401
except ImportError:  # 本地源码运行
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Vapor import core  # noqa: E402
from Vapor.ui.base import Node, Rect  # noqa: E402

# ui/__init__.py 会把 layout 函数重导出，直接 import 子模块更可靠。
import importlib  # noqa: E402

layout_mod = importlib.import_module("Vapor.ui.layout")
paint_mod = importlib.import_module("Vapor.ui.paint")
renderer_mod = importlib.import_module("Vapor.ui.renderer")


class Spy:
    """记录 mark_rect 调用的假账本。"""

    def __init__(self):
        self._rects = []
        self.is_full = False

    def mark_rect(self, x, y, w, h):
        self._rects.append((float(x), float(y), float(w), float(h)))

    def mark_full(self):
        self.is_full = True

    def rects(self):
        return self._rects

    def clear(self):
        self._rects.clear()


class FakeCanvas:
    def __init__(self):
        self.fills = []

    def fill_rect(self, x, y, w, h, color):
        self.fills.append((x, y, w, h))


# ---------------------------------------------------------------------------
# B1：effect apply 前标记节点当前 box（内容变化但位置不变）
# ---------------------------------------------------------------------------

def test_bind_text_marks_node_box(monkeypatch):
    spy = Spy()
    monkeypatch.setattr(renderer_mod, "ledger", spy)

    r = renderer_mod.Renderer()
    node = r.text("a")
    node.box = Rect(10, 20, 80, 24)
    value = core.ref("a")
    r.bind_text(node, lambda: value.value)
    core.flush_jobs()

    spy.clear()
    value.value = "hello"
    core.flush_jobs()

    assert (10.0, 20.0, 80.0, 24.0) in spy.rects()


def test_bind_attr_marks_node_box(monkeypatch):
    spy = Spy()
    monkeypatch.setattr(renderer_mod, "ledger", spy)

    r = renderer_mod.Renderer()
    node = r.element("View")
    node.box = Rect(4, 6, 30, 12)
    color = core.ref("#111111")
    r.bind_attr(node, "bg", lambda: color.value)
    core.flush_jobs()

    spy.clear()
    color.value = "#FFFFFF"
    core.flush_jobs()

    assert (4.0, 6.0, 30.0, 12.0) in spy.rects()


# ---------------------------------------------------------------------------
# B2：layout 对比 prev_box，旧区域 + 新区域都标脏
# ---------------------------------------------------------------------------

def test_layout_marks_old_and_new_box(monkeypatch):
    spy = Spy()
    monkeypatch.setattr(layout_mod, "ledger", spy)

    node = Node("#text", {"size": 16}, "a")
    layout_mod.layout(node, 0, 0)
    old = (node.box.x, node.box.y, node.box.w, node.box.h)

    spy.clear()
    node.text = "a much longer text"
    layout_mod.layout(node, 0, 0)
    new = (node.box.x, node.box.y, node.box.w, node.box.h)

    assert old != new
    assert old in spy.rects()
    assert new in spy.rects()


def test_scroll_shift_marks_old_and_new_position(monkeypatch):
    spy = Spy()
    monkeypatch.setattr(layout_mod, "ledger", spy)

    parent = Node("View", {})
    parent.clip = True
    child = Node("View", {"w": 50, "h": 20})
    parent.add(child)

    layout_mod.layout(parent, 0, 0, 100, 50)  # 首帧：只记录快照
    layout_mod.layout(parent, 0, 0, 100, 50)  # 稳定 prev_box

    spy.clear()
    parent.scroll_y = 10
    layout_mod.layout(parent, 0, 0, 100, 50)

    rects = spy.rects()
    assert any(r[1] == 0.0 for r in rects), rects
    assert any(r[1] == -10.0 for r in rects), rects


# ---------------------------------------------------------------------------
# B3：节点消失前标记整棵子树
# ---------------------------------------------------------------------------

def test_bind_if_removal_marks_subtree(monkeypatch):
    spy = Spy()
    monkeypatch.setattr(renderer_mod, "ledger", spy)

    r = renderer_mod.Renderer()
    frag = r.fragment()
    flag = core.ref(True)

    def build():
        box = r.element("View")
        text = r.text("bye")
        box.add(text)
        return [box]

    r.bind_if(frag, [(lambda: flag.value, build)])
    root = frag.children[0]
    text = root.children[0]
    root.box = Rect(10, 10, 100, 40)
    text.box = Rect(12, 12, 60, 20)

    spy.clear()
    flag.value = False
    core.flush_jobs()

    rects = spy.rects()
    assert (10.0, 10.0, 100.0, 40.0) in rects
    assert (12.0, 12.0, 60.0, 20.0) in rects
    assert frag.children == []


# ---------------------------------------------------------------------------
# B4：脏区裁剪（默认关闭）
# ---------------------------------------------------------------------------

def test_paint_cull_skips_nodes_outside_dirty_rects(monkeypatch):
    spy = Spy()
    spy.mark_rect(0, 0, 10, 10)
    monkeypatch.setattr(paint_mod, "ledger", spy)
    monkeypatch.setattr(paint_mod, "CULL", True)

    outside = Node("View", {"bg": "#FFFFFF"})
    outside.box = Rect(100, 100, 20, 20)
    canvas = FakeCanvas()
    paint_mod._paint_node(canvas, outside)
    assert canvas.fills == []

    inside = Node("View", {"bg": "#FFFFFF"})
    inside.box = Rect(2, 2, 20, 20)
    paint_mod._paint_node(canvas, inside)
    assert canvas.fills
