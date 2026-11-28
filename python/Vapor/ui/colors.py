"""颜色与数值解析工具。"""

from __future__ import annotations

from .. import _native
from .. import svg as svg_mod

_core = _native.core

__all__ = ["rgb", "color", "with_alpha"]


def rgb(r, g, b, a=255) -> int:
    return _core.rgb(int(r), int(g), int(b), int(a))


def color(value, default=None) -> int:
    """把颜色（int / #hex / rgb() / 命名色）转成 0xAABBGGRR。"""
    if value is None:
        return default if default is not None else 0
    if isinstance(value, int):
        if value <= 0xFFFFFF:  # 纯 RGB
            return 0xFF000000 | ((value & 0xFF) << 16) | (((value >> 8) & 0xFF) << 8) | ((value >> 16) & 0xFF)
        return value & 0xFFFFFFFF
    if isinstance(value, (tuple, list)):
        parts = list(value) + [255] * (4 - len(value))
        return rgb(*parts[:4])
    text = str(value).strip()
    if text.startswith("#") or text.startswith("rgb") or text.lower() in svg_mod.NAMED_COLORS:
        rgb_val = svg_mod.color_value(text, None)
        if rgb_val is None:
            return default or 0
        return svg_mod._pack(rgb_val, 255)
    try:
        return int(text, 0) & 0xFFFFFFFF
    except ValueError:
        return default or 0


def with_alpha(c: int, factor: float) -> int:
    """给颜色乘一个透明度系数（阴影/蒙层用）。"""
    return _with_alpha(c, factor)


def _with_alpha(c: int, factor: float) -> int:
    if factor >= 0.999:
        return c
    a = int(((c >> 24) & 0xFF) * max(0.0, min(1.0, factor)))
    return (a << 24) | (c & 0x00FFFFFF)


def _num(value, total=0.0, default=None):
    """把布局数值（像素 / 百分比）解析为浮点数；auto 返回 default。"""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if s == "auto" or s == "":
        return default
    if s.endswith("%"):
        try:
            return total * float(s[:-1]) / 100.0
        except ValueError:
            return default
    try:
        return float(s)
    except ValueError:
        return default
