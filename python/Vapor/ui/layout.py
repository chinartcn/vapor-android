"""精简 flex 布局：递归计算并写回每个节点的 box。"""

from __future__ import annotations

from .base import Rect, Node, _TEXT, _FRAGMENT, _COMPONENT, _flow
from .colors import _num
from .text import text_width, line_height, wrap_text

__all__ = ["layout"]


def _padding(node):
    props = node.props
    base = props.get("padding", 0)
    if isinstance(base, (list, tuple)):
        vals = list(base)
        while len(vals) < 4:
            vals.append(vals[-1] if vals else 0)
        return tuple(float(v or 0) for v in vals[:4])
    p = float(base or 0)
    px = float(props.get("padding_x", p) or 0)
    py = float(props.get("padding_y", p) or 0)
    return (px, py, px, py)


def _margin(node):
    props = node.props
    base = props.get("margin", 0)
    if isinstance(base, (list, tuple)):
        vals = list(base)
        while len(vals) < 4:
            vals.append(vals[-1] if vals else 0)
        return tuple(float(v or 0) for v in vals[:4])
    m = float(base or 0)
    mx = float(props.get("margin_x", m) or 0)
    my = float(props.get("margin_y", m) or 0)
    return (mx, my, mx, my)


def _intrinsic(node, avail_w=0.0, avail_h=0.0):
    """节点在内容上的自然尺寸（不含 margin）。"""
    if node.tag == _TEXT:
        size = float(node.props.get("size", 16))
        if node.props.get("wrap") and avail_w > 0:
            lines = wrap_text(node.text, size, avail_w) or [""]
            return (avail_w, len(lines) * line_height(size))
        return (text_width(node.text, size), line_height(size))
    pad = _padding(node)
    kids = [k for k in _flow(node) if not k.props.get("absolute")]
    if not kids:
        return (pad[0] + pad[2], pad[1] + pad[3])
    row = node.props.get("dir", "column") == "row"
    gap = float(node.props.get("gap", 0) or 0)
    if row:
        w = sum(_measure(k, avail_w, avail_h)[0] for k in kids) + gap * (len(kids) - 1) + pad[0] + pad[2]
        h = max(_measure(k, avail_w, avail_h)[1] for k in kids) + pad[1] + pad[3]
    else:
        w = max(_measure(k, avail_w, avail_h)[0] for k in kids) + pad[0] + pad[2]
        h = sum(_measure(k, avail_w, avail_h)[1] for k in kids) + gap * (len(kids) - 1) + pad[1] + pad[3]
    return (w, h)


def _measure(node, avail_w=0.0, avail_h=0.0):
    """节点尺寸：显式 w/h 优先，否则用内容自然尺寸。"""
    if node.tag == _FRAGMENT:
        kids = _flow(node)
        if not kids:
            return (0.0, 0.0)
        return (max(_measure(k)[0] for k in kids), sum(_measure(k)[1] for k in kids))
    auto_w, auto_h = _intrinsic(node, avail_w, avail_h)
    w = _num(node.props.get("w"), avail_w or auto_w, None)
    h = _num(node.props.get("h"), avail_h or auto_h, None)
    return (auto_w if w is None else w, auto_h if h is None else h)


def _align_offset(align, free):
    if align == "center":
        return free / 2.0
    if align == "end":
        return free
    return 0.0


def layout(node, x=0.0, y=0.0, w=None, h=None):
    """递归计算并写回每个节点的 box。"""
    if w is None:
        w = _measure(node)[0]
    if h is None:
        h = _measure(node)[1]
    node.box = Rect(x, y, w, h)
    if node.tag in (_TEXT,):
        return

    pad = _padding(node)
    inner_x = x + pad[0]
    inner_y = y + pad[1]
    inner_w = max(0.0, w - pad[0] - pad[2])
    inner_h = max(0.0, h - pad[1] - pad[3])

    kids = _flow(node)
    if not kids:
        return

    flow_kids = [k for k in kids if not k.props.get("absolute")]
    abs_kids = [k for k in kids if k.props.get("absolute")]

    row = node.props.get("dir", "column") == "row"
    gap = float(node.props.get("gap", 0) or 0)
    main_total = inner_w if row else inner_h

    margins = [_margin(k) for k in flow_kids]
    sizes = []
    for i, kid in enumerate(flow_kids):
        mw, mh = _measure(kid, inner_w, inner_h)
        m = margins[i]
        sizes.append((mw + m[0] + m[2], mh + m[1] + m[3]))

    used = sum((s[0] if row else s[1]) for s in sizes) + gap * max(0, len(sizes) - 1)
    free = main_total - used

    grows = [float(k.props.get("grow", 0) or 0) for k in flow_kids]
    grow_total = sum(grows)
    if free > 0 and grow_total > 0:
        extra = [(free * g / grow_total) for g in grows]
    else:
        extra = [0.0] * len(flow_kids)

    justify = node.props.get("justify", "start")
    offset = 0.0
    if grow_total <= 0 and free > 0:
        offset = _align_offset(justify, free)

    cursor = (inner_x if row else inner_y) + offset
    for i, kid in enumerate(flow_kids):
        m = margins[i]
        sw, sh = sizes[i]
        kw = sw - m[0] - m[2]
        kh = sh - m[1] - m[3]
        if row:
            kw += extra[i]
        else:
            kh += extra[i]

        align = kid.props.get("align", node.props.get("align", "start"))
        if kid.props.get("stretch", False) or align == "stretch":
            if row:
                kh = inner_h - m[1] - m[3]
            else:
                kw = inner_w - m[0] - m[2]

        if kid.tag in (_FRAGMENT, _COMPONENT):
            # 容器壳：内部子节点按同样可用空间排布
            pass

        if row:
            kx = cursor + m[0]
            free_cross = inner_h - sh
            ky = inner_y + _align_offset(align, free_cross) + m[1]
            layout(kid, kx, ky, kw, kh)
            # 注意：主轴游标要加上 grow 分配到的空间，否则 grow 子节点会互相重叠
            cursor += sw + extra[i] + gap
        else:
            free_cross = inner_w - sw
            kx = inner_x + _align_offset(align, free_cross) + m[0]
            ky = cursor + m[1]
            layout(kid, kx, ky, kw, kh)
            cursor += sh + extra[i] + gap

    for kid in abs_kids:
        m = _margin(kid)
        aw = _num(kid.props.get("w"), inner_w, None)
        ah = _num(kid.props.get("h"), inner_h, None)
        ax = _num(kid.props.get("x"), inner_w, 0.0)
        ay = _num(kid.props.get("y"), inner_h, 0.0)
        if aw is None or ah is None:
            mw, mh = _measure(kid, inner_w, inner_h)
            aw = mw if aw is None else aw
            ah = mh if ah is None else ah
        layout(kid, inner_x + ax + m[0], inner_y + ay + m[1], aw, ah)

    if node.clip and node.scroll_y:
        _shift_children(node, 0.0, -node.scroll_y)


def _shift_children(node: Node, dx: float, dy: float):
    """把整棵子树平移（滚动用；每帧重新布局所以不会累积）。"""
    for child in node.children:
        child.box.x += dx
        child.box.y += dy
        _shift_children(child, dx, dy)
