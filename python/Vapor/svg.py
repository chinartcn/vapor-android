"""Vapor.svg —— 把 SVG 当作组件渲染（无位图，直接走矢量光栅化）。

支持：path / rect / circle / ellipse / line / polyline / polygon / g（嵌套变换）。
颜色支持 #rgb / #rrggbb / #aarrggbb / rgb() / 常用命名色。

    from Vapor.svg import parse_svg, ICONS
    doc = parse_svg(ICONS["home"])
    doc.render(canvas, x, y, size, size, color=0xFFFFFFFF)
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

from . import _native

_geom = _native.ui

__all__ = ["Shape", "SvgDoc", "parse_svg", "parse_transform", "ICONS", "color_value"]

NAMED_COLORS = {
    "black": 0x000000, "white": 0xFFFFFF, "red": 0xFF0000, "green": 0x008000,
    "blue": 0x0000FF, "yellow": 0xFFFF00, "cyan": 0x00FFFF, "magenta": 0xFF00FF,
    "gray": 0x808080, "grey": 0x808080, "orange": 0xFFA500, "purple": 0x800080,
    "pink": 0xFFC0CB, "transparent": None, "none": None,
    "silver": 0xC0C0C0, "navy": 0x000080, "teal": 0x008080, "lime": 0x00FF00,
}

_RGB_RE = re.compile(r"rgba?\(([^)]+)\)", re.I)


def _parse_hex(value: str):
    s = value.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) == 6:
        return (int(s[0:2], 16) << 16) | (int(s[2:4], 16) << 8) | int(s[4:6], 16)
    if len(s) == 8:  # #AARRGGBB
        a = int(s[0:2], 16)
        r = int(s[2:4], 16)
        g = int(s[4:6], 16)
        b = int(s[6:8], 16)
        return (a << 24) | (r << 16) | (g << 8) | b
    return None


def color_value(value, default=None):
    """把 CSS 颜色转成 0xRRGGBB（不带 alpha）。"""
    if value is None:
        return default
    if isinstance(value, int):
        return value & 0xFFFFFF if (value >> 24) == 0 else value
    v = value.strip()
    if not v or v == "none" or v == "transparent":
        return None
    if v.startswith("#"):
        parsed = _parse_hex(v)
        if parsed is None:
            return default
        if len(v) == 9:  # #AARRGGBB -> 丢掉 alpha
            return parsed & 0xFFFFFF
        return parsed
    m = _RGB_RE.match(v)
    if m:
        parts = [p.strip() for p in m.group(1).replace("/", " ").replace(",", " ").split()]
        try:
            nums = [float(p.rstrip("%")) for p in parts[:3]]
        except ValueError:
            return default
        r, g, b = (int(n * 2.55) if "%" in parts[i] else int(n) for i, n in enumerate(nums))
        return (r << 16) | (g << 8) | b
    return NAMED_COLORS.get(v.lower(), default)


def _pack(rgb: int, alpha: int = 255) -> int:
    """0xRRGGBB + alpha -> 0xAARRGGBB。"""
    return ((alpha & 0xFF) << 24) | (rgb & 0xFFFFFF)


# ---------------------------------------------------------------------------
# transform
# ---------------------------------------------------------------------------

_TRANSFORM_RE = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")


def parse_transform(value) -> "object":
    """解析 SVG transform 属性，返回 Mat2D（顺序从左到右复合）。"""
    m = _geom.Mat2D.identity()
    if not value:
        return m
    for name, args in _TRANSFORM_RE.findall(value):
        nums = [float(x) for x in re.split(r"[\s,]+", args.strip()) if x]
        if name == "matrix" and len(nums) == 6:
            t = _geom.Mat2D(*nums)
        elif name == "translate":
            t = _geom.Mat2D.translate(nums[0], nums[1] if len(nums) > 1 else 0.0)
        elif name == "scale":
            t = _geom.Mat2D.scale(nums[0], nums[1] if len(nums) > 1 else None)
        elif name == "rotate":
            t = _geom.Mat2D.rotate(nums[0])
            if len(nums) >= 3:
                t = _geom.Mat2D.translate(nums[1], nums[2]).mul(t).mul(_geom.Mat2D.translate(-nums[1], -nums[2]))
        elif name == "skewX":
            t = _geom.Mat2D.skew(nums[0])
        elif name == "skewY":
            t = _geom.Mat2D.skew(0.0, nums[0])
        else:
            continue
        m = m.mul(t)
    return m


# ---------------------------------------------------------------------------
# 形状
# ---------------------------------------------------------------------------

class Shape:
    __slots__ = ("d", "fill", "stroke", "stroke_width", "opacity", "transform")

    def __init__(self, d, fill=None, stroke=None, stroke_width=1.0, opacity=1.0, transform=None):
        self.d = d
        self.fill = fill
        self.stroke = stroke
        self.stroke_width = stroke_width
        self.opacity = opacity
        self.transform = transform

    def __repr__(self):
        return f"Shape({self.d[:24]!r} fill={self.fill} stroke={self.stroke})"


def _num(el, name, default=0.0):
    raw = el.get(name)
    if raw is None:
        return default
    try:
        return float(re.sub(r"[a-zA-Z%]+$", "", raw.strip()))
    except ValueError:
        return default


def _primitive_to_path(tag, el):
    if tag == "rect":
        x, y = _num(el, "x"), _num(el, "y")
        w, h = _num(el, "width"), _num(el, "height")
        rx = _num(el, "rx", 0.0)
        ry = _num(el, "ry", rx)
        if w <= 0 or h <= 0:
            return None
        if rx <= 0 and ry <= 0:
            return f"M{x} {y}H{x + w}V{y + h}H{x}Z"
        rx = min(rx or ry, w / 2)
        ry = min(ry or rx, h / 2)
        return (
            f"M{x + rx} {y}H{x + w - rx}A{rx} {ry} 0 0 1 {x + w} {y + ry}"
            f"V{y + h - ry}A{rx} {ry} 0 0 1 {x + w - rx} {y + h}"
            f"H{x + rx}A{rx} {ry} 0 0 1 {x} {y + h - ry}"
            f"V{y + ry}A{rx} {ry} 0 0 1 {x + rx} {y}Z"
        )
    if tag == "circle":
        cx, cy, r = _num(el, "cx"), _num(el, "cy"), _num(el, "r")
        if r <= 0:
            return None
        return (
            f"M{cx - r} {cy}A{r} {r} 0 1 0 {cx + r} {cy}"
            f"A{r} {r} 0 1 0 {cx - r} {cy}Z"
        )
    if tag == "ellipse":
        rx, ry = _num(el, "rx"), _num(el, "ry")
        cx, cy = _num(el, "cx"), _num(el, "cy")
        if rx <= 0 or ry <= 0:
            return None
        return (
            f"M{cx - rx} {cy}A{rx} {ry} 0 1 0 {cx + rx} {cy}"
            f"A{rx} {ry} 0 1 0 {cx - rx} {cy}Z"
        )
    if tag == "line":
        return (
            f"M{_num(el, 'x1')} {_num(el, 'y1')}"
            f"L{_num(el, 'x2')} {_num(el, 'y2')}"
        )
    if tag in ("polyline", "polygon"):
        raw = el.get("points", "")
        nums = [float(v) for v in re.split(r"[\s,]+", raw.strip()) if v]
        if len(nums) < 4:
            return None
        pts = list(zip(nums[0::2], nums[1::2]))
        d = "M" + " L".join(f"{x} {y}" for x, y in pts)
        if tag == "polygon":
            d += "Z"
        return d
    if tag == "path":
        return el.get("d")
    return None


class SvgDoc:
    __slots__ = ("width", "height", "view_box", "shapes", "source")

    def __init__(self, width=24.0, height=24.0, view_box=None, shapes=None, source=""):
        self.width = width
        self.height = height
        self.view_box = view_box or (0.0, 0.0, width, height)
        self.shapes = shapes or []
        self.source = source

    def bounds(self):
        vx, vy, vw, vh = self.view_box
        return (vx, vy, vw, vh)

    def render(self, canvas, x=0, y=0, width=None, height=None, color=None, alpha=255):
        """把整份 SVG 画进 canvas（坐标 x,y 宽 width,height）。"""
        width = self.width if width is None else width
        height = self.height if height is None else height
        vx, vy, vw, vh = self.view_box
        if vw <= 0 or vh <= 0:
            return
        sx = width / vw
        sy = height / vh
        scale = min(sx, sy)  # preserveAspectRatio: meet
        ox = x + (width - vw * scale) / 2 - vx * scale
        oy = y + (height - vh * scale) / 2 - vy * scale
        base = _geom.Mat2D(scale, 0, 0, scale, ox, oy)

        for shape in self.shapes:
            mat = base
            if shape.transform is not None:
                mat = base.mul(shape.transform)
            polys = _geom.flatten_path(shape.d, 0.35, mat)

            fill_rgb = color_value(shape.fill, None)
            stroke_rgb = color_value(shape.stroke, None)
            tint = color_value(color, None) if color is not None else None
            if tint is not None:
                if fill_rgb is not None:
                    fill_rgb = tint
                if stroke_rgb is not None:
                    stroke_rgb = tint

            shape_alpha = int(alpha * max(0.0, min(1.0, shape.opacity)))
            if shape_alpha <= 0:
                continue

            if fill_rgb is not None:
                fill_px = _pack(fill_rgb, shape_alpha)
                for closed, pts in polys:
                    if len(pts) >= 3:
                        canvas.polygon([(px, py) for px, py in pts], fill_px)
            if stroke_rgb is not None and shape.stroke_width > 0:
                stroke_px = _pack(stroke_rgb, shape_alpha)
                w = max(1, int(round(shape.stroke_width * scale)))
                for closed, pts in polys:
                    if len(pts) >= 2:
                        canvas.polyline([(px, py) for px, py in pts], stroke_px, w, closed)

    def __repr__(self):
        return f"SvgDoc(view_box={self.view_box}, shapes={len(self.shapes)})"


def parse_svg(source: str) -> SvgDoc:
    """解析 SVG 源码为 SvgDoc（只保留可矢量的形状）。"""
    if not source or not source.strip():
        raise ValueError("SVG 源码为空")
    try:
        root = ET.fromstring(source.strip())
    except ET.ParseError as exc:
        raise ValueError(f"SVG 解析失败: {exc}") from exc

    def localname(tag):
        return tag.split("}")[-1] if "}" in tag else tag

    def inherited(el, key, default=None):
        cur = el
        while cur is not None:
            v = cur.get(key)
            if v is not None:
                return v
            cur = cur.get("__parent__")
        return default

    view_box = None
    vb = root.get("viewBox")
    if vb:
        nums = [float(v) for v in re.split(r"[\s,]+", vb.strip()) if v]
        if len(nums) == 4:
            view_box = tuple(nums)
    width = _num(root, "width", 24.0) or 24.0
    height = _num(root, "height", 24.0) or 24.0
    if view_box:
        width, height = view_box[2], view_box[3]

    shapes: list[Shape] = []

    def walk(el, parent_mat, style):
        tag = localname(el.tag)
        mat = parent_mat.mul(parse_transform(el.get("transform")))
        own = dict(style)
        for key in ("fill", "stroke", "stroke-width", "opacity", "fill-opacity", "stroke-opacity"):
            if el.get(key) is not None:
                own[key] = el.get(key)
        for child in list(el):
            walk(child, mat, own)
        d = _primitive_to_path(tag, el)
        if not d:
            return
        fill = own.get("fill", "black")
        stroke = own.get("stroke", "none")
        stroke_width = 1.0
        if own.get("stroke-width"):
            try:
                stroke_width = float(re.sub(r"[a-zA-Z%]+$", "", own["stroke-width"]))
            except ValueError:
                pass
        opacity = 1.0
        try:
            opacity = float(own.get("opacity", 1.0))
        except ValueError:
            pass
        fill_opacity = float(own.get("fill-opacity", 1.0) or 1.0)
        stroke_opacity = float(own.get("stroke-opacity", 1.0) or 1.0)
        shapes.append(
            Shape(
                d=d,
                fill=None if fill in ("none", "transparent") else fill,
                stroke=None if stroke in ("none", "transparent") else stroke,
                stroke_width=stroke_width,
                opacity=opacity,
                transform=mat,
            )
        )
        # 单独保存透明度（简化：取两者较小值）
        shapes[-1].opacity = opacity * min(fill_opacity, stroke_opacity) if stroke_opacity < 1 or fill_opacity < 1 else opacity

    walk(root, parse_transform(root.get("transform")), {})
    return SvgDoc(width=width, height=height, view_box=view_box, shapes=shapes, source=source)


# ---------------------------------------------------------------------------
# 内置图标（Material 风格 24x24）
# ---------------------------------------------------------------------------

ICONS = {
    "home": '<svg viewBox="0 0 24 24"><path d="M12 3 L3 10.5 V21 H9 V15 H15 V21 H21 V10.5 Z"/></svg>',
    "menu": '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="2"/><rect x="3" y="11" width="18" height="2"/><rect x="3" y="17" width="18" height="2"/></svg>',
    "search": '<svg viewBox="0 0 24 24"><path d="M10 3 A7 7 0 1 0 10 17 A7 7 0 1 0 10 3 Z M15 15 L21 21" fill="none" stroke="black" stroke-width="2"/></svg>',
    "settings": '<svg viewBox="0 0 24 24"><path d="M12 8 A4 4 0 1 0 12 16 A4 4 0 1 0 12 8 Z M12 2 L13.5 5 H17 L18 8 L21 9.5 V14 L18.5 15.5 L18 19 H14 L12 22 L10 19 H6 L5.5 15.5 L3 14 V9.5 L6 8 L7 5 H10.5 Z" fill="none" stroke="black" stroke-width="1.6"/></svg>',
    "close": '<svg viewBox="0 0 24 24"><path d="M5 5 L19 19 M19 5 L5 19" fill="none" stroke="black" stroke-width="2"/></svg>',
    "back": '<svg viewBox="0 0 24 24"><path d="M15 4 L7 12 L15 20" fill="none" stroke="black" stroke-width="2"/></svg>',
    "check": '<svg viewBox="0 0 24 24"><path d="M4 12 L10 18 L20 6" fill="none" stroke="black" stroke-width="2"/></svg>',
    "star": '<svg viewBox="0 0 24 24"><path d="M12 2 L15 9 L22 10 L17 15 L18 22 L12 18.5 L6 22 L7 15 L2 10 L9 9 Z"/></svg>',
    "heart": '<svg viewBox="0 0 24 24"><path d="M12 21 C6 16 2 12.5 2 8.5 A4.5 4.5 0 0 1 12 7 A4.5 4.5 0 0 1 22 8.5 C22 12.5 18 16 12 21 Z"/></svg>',
    "plus": '<svg viewBox="0 0 24 24"><path d="M12 4 V20 M4 12 H20" fill="none" stroke="black" stroke-width="2"/></svg>',
    "phone": '<svg viewBox="0 0 24 24"><rect x="6" y="2" width="12" height="20" rx="2"/><rect x="10" y="18" width="4" height="2" fill="white"/></svg>',
}
