"""节点绘制与滚动辅助。"""

from __future__ import annotations

from collections.abc import Mapping

from .base import Node, Rect, _TEXT, _FRAGMENT, _COMPONENT
from .colors import color, _with_alpha
from .text import text_width, line_height, wrap_text
from .renderer import _warn

__all__ = ["_paint_node"]


def _intersects(a: Rect, b: Rect) -> bool:
    return not (a.x + a.w <= b.x or a.x >= b.x + b.w or a.y + a.h <= b.y or a.y >= b.y + b.h)


def _paint_node(canvas, node: Node):
    if not node.visible:
        return
    props = node.props
    if props.get("display") == "none":
        return

    # 容器壳（插槽 fragment / 组件节点）自己没有绘制，但必须继续向下遍历，
    # 否则插槽里的内容永远不会上屏。它们的 box 可能是 0x0，不能按尺寸提前返回。
    if node.tag in (_FRAGMENT, _COMPONENT):
        for child in node.children:
            _paint_node(canvas, child)
        return

    box = node.box
    if box.w <= 0 or box.h <= 0:
        return

    opacity = float(props.get("opacity", 1) or 1)

    if node.tag == _TEXT:
        size = float(props.get("size", 16))
        c = _with_alpha(color(props.get("color", "#FFFFFF")), opacity)
        lines = [node.text]
        if props.get("wrap") and box.w > 0:
            lines = wrap_text(node.text, size, box.w) or [""]
            max_lines = props.get("max_lines")
            if max_lines and len(lines) > int(max_lines):
                lines = lines[: int(max_lines)]
                lines[-1] = lines[-1] + "…"
        align = props.get("align")
        lh = line_height(size)
        for index, line in enumerate(lines):
            tx = box.x
            lw = text_width(line, size)
            if align == "center":
                tx = box.x + (box.w - lw) / 2
            elif align == "end":
                tx = box.x + box.w - lw
            canvas.text(line, int(tx), int(box.y + index * lh), c, size)
        return

    # 背景
    bg = props.get("bg")
    if bg is not None and not (isinstance(bg, str) and bg in ("none", "transparent")):
        if isinstance(bg, (tuple, list)) and bg and bg[0] == "linear":
            _, c1, c2, *rest = bg
            vertical = not rest or rest[0] != "h"
            radius = float(props.get("radius", 0) or 0)
            if radius > 0:
                canvas.round_rect(box.x, box.y, box.w, box.h, radius, _with_alpha(color(c1), opacity))
            canvas.gradient(box.x, box.y, box.w, box.h,
                            _with_alpha(color(c1), opacity), _with_alpha(color(c2), opacity), vertical)
        else:
            c = _with_alpha(color(bg), opacity)
            radius = float(props.get("radius", 0) or 0)
            if radius > 0:
                canvas.round_rect(box.x, box.y, box.w, box.h, radius, c)
            else:
                canvas.fill_rect(box.x, box.y, box.w, box.h, c)

    # 边框
    border = props.get("border")
    if border:
        bw = 1
        bc = "#3A4048"
        if isinstance(border, (tuple, list)):
            if len(border) >= 1 and isinstance(border[0], (int, float)):
                bw = int(border[0])
            if len(border) >= 2:
                bc = border[1]
        elif isinstance(border, Mapping):
            bw = int(border.get("width", 1))
            bc = border.get("color", bc)
        bc_px = _with_alpha(color(bc), opacity)
        radius = float(props.get("radius", 0) or 0)
        if radius > 0:
            for i in range(bw):
                canvas.round_rect(box.x + i, box.y + i, box.w - 2 * i, box.h - 2 * i, max(0, radius - i), bc_px)
        else:
            canvas.fill_rect(box.x, box.y, box.w, bw, bc_px)
            canvas.fill_rect(box.x, box.y + box.h - bw, box.w, bw, bc_px)
            canvas.fill_rect(box.x, box.y, bw, box.h, bc_px)
            canvas.fill_rect(box.x + box.w - bw, box.y, bw, box.h, bc_px)

    # 自定义绘制
    painter = props.get("paint")
    if callable(painter):
        try:
            painter(canvas, box)
        except Exception as exc:  # pragma: no cover
            _warn(f"paint 回调失败: {exc}")

    for child in node.children:
        if node.clip and not _intersects(child.box, box):
            continue
        _paint_node(canvas, child)


def _scroll_max(viewport: Node) -> float:
    content = viewport.children[0] if viewport.children else None
    content_h = content.box.h if content is not None else 0.0
    return max(0.0, content_h - viewport.box.h)


def _scroll_by(viewport: Node, delta: float) -> float:
    """按像素滚动一个滚动容器：offset 0 表示顶部，正值表示内容上移（向下滚）。"""
    state = viewport.extra.get("scroll")
    if not state:
        return 0.0
    ref = state["offset"]
    max_off = _scroll_max(viewport)
    new_value = min(max_off, max(0.0, ref.value + delta))
    ref.value = new_value
    return new_value
