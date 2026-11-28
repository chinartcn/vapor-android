"""节点绘制与滚动辅助。"""

from __future__ import annotations

from collections.abc import Mapping

from .base import Node, Rect, _TEXT, _FRAGMENT, _COMPONENT
from .colors import color, _with_alpha
from .text import text_width, line_height, wrap_text
from .renderer import _warn

try:  # 文档 A 尚未合入时的空账本：CULL 默认关闭，不影响绘制。
    from .dirty import ledger
except ImportError:  # pragma: no cover - A 合入后走真实账本
    class _NullLedger:
        is_full = True

        def mark_rect(self, x, y, w, h):
            pass

        def mark_full(self):
            pass

        def rects(self):
            return ()

    ledger = _NullLedger()

__all__ = ["_paint_node"]

# B4：按脏区裁剪遍历。默认关闭，需与文档 A 的像素一致性验收后再开启。
CULL = False


def _any_intersect(box: Rect, rects) -> bool:
    for r in rects:
        if hasattr(r, "x"):
            rx, ry, rw, rh = r.x, r.y, r.w, r.h
        else:
            rx, ry, rw, rh = r[0], r[1], r[2], r[3]
        if not (box.x + box.w <= rx or box.x >= rx + rw
                or box.y + box.h <= ry or box.y >= ry + rh):
            return True
    return False


def _intersects(a: Rect, b: Rect) -> bool:
    return not (a.x + a.w <= b.x or a.x >= b.x + b.w or a.y + a.h <= b.y or a.y >= b.y + b.h)


def _paint_node(canvas, node: Node, clip=None):
    if not node.visible:
        return
    props = node.props
    if props.get("display") == "none":
        return

    if CULL and clip is None and not ledger.is_full:
        # 空列表表示“本帧没有任何变化”，不能当作裁剪区域，否则会跳过整棵树。
        clip = ledger.rects() or None

    box = node.box
    if clip and box.w > 0 and box.h > 0 and not _any_intersect(box, clip):
        return

    # 容器壳（插槽 fragment / 组件节点）自己没有绘制，但必须继续向下遍历，
    # 否则插槽里的内容永远不会上屏。它们的 box 可能是 0x0，不能按尺寸提前返回。
    if node.tag in (_FRAGMENT, _COMPONENT):
        for child in node.children:
            _paint_node(canvas, child, clip)
        return

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
        _paint_node(canvas, child, clip)


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
