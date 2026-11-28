"""文本度量、字体切换与点阵回退绘制。"""

from __future__ import annotations

from .. import _native
from .dirty import ledger

_geom = _native.ui

__all__ = [
    "set_text_renderer", "set_active_font", "active_font",
    "text_width", "line_height", "wrap_text",
]

_TEXT_RENDERER = None
_ACTIVE_FONT = None   # 真实字体（Vapor._ui.Font / FontStack），由 Vapor.font.install() 设置

try:  # 复用 rust.Vapor 的内置 5x7 点阵（没有原生库时它自带回退实现）
    from rust.Vapor import _FONT as _BITMAP_FONT  # type: ignore
except Exception:  # pragma: no cover
    _BITMAP_FONT = {}


def set_text_renderer(fn):
    """替换文本绘制实现（扩展可接入真实字体 / 中文点阵）。

    fn(canvas, text, x, y, color, size) 每帧被调用。
    """
    global _TEXT_RENDERER
    _TEXT_RENDERER = fn


def set_active_font(font):
    """设置全局字体（测量 + 绘制）；传 None 恢复内置点阵。"""
    global _ACTIVE_FONT
    _ACTIVE_FONT = font


def active_font():
    return _ACTIVE_FONT


def text_width(text, size=16.0) -> float:
    if _ACTIVE_FONT is not None:
        return _ACTIVE_FONT.text_width(str(text), float(size))
    return _geom.text_width(str(text), float(size))


def line_height(size=16.0) -> float:
    if _ACTIVE_FONT is not None:
        return _ACTIVE_FONT.line_height(float(size))
    return _geom.line_height(float(size))


def wrap_text(text, size, max_width) -> list:
    if _ACTIVE_FONT is not None:
        return _ACTIVE_FONT.wrap_text(str(text), float(size), float(max_width))
    return _geom.wrap_text(str(text), float(size), float(max_width))


def draw_text(canvas, s, x, y, c, size=16, scale=None):
    """供 Canvas.text 调用：优先自定义渲染器，否则用内置 5x7 点阵。"""
    if _TEXT_RENDERER is not None:
        # 自定义渲染器范围未知，按全帧兜底，避免漏帧。
        ledger.mark_full()
        _TEXT_RENDERER(canvas, s, x, y, c, size)
        return
    sc = int(scale) if scale else max(1, int(round(size / 7.0)))
    cx = int(x)
    # 文本整体外接框（5x7 点阵，字距 6*sc）；先标记再绘制。
    ledger.mark_rect(cx, int(y), max(0, len(str(s)) * 6 * sc), 7 * sc)
    for ch in str(s):
        rows = _BITMAP_FONT.get(ch.upper())
        if rows is None:
            cx += 6 * sc
            continue
        for ry, row in enumerate(rows):
            for bit in range(5):
                if (row >> (4 - bit)) & 1:
                    canvas._c.fill_rect(cx + bit * sc, int(y) + ry * sc, sc, sc, int(c))
        cx += 6 * sc
