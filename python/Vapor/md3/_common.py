"""MD3 组件库共享 helper（四份执行文档共用，接口冻结）。"""

from __future__ import annotations

from ..ui import rgb, with_alpha
from ..ui import color as parse_color
from ..theme import current_theme
from ..ui.motion import reduced_motion
from ..ui.renderer import queue_dirty
from ..ui.states import StateLayer

MD_PREFIX = "Md"

__all__ = ["theme_color", "state_mask", "bind_node", "pressable", "focus_ring", "MD_PREFIX"]


def theme_color(role: str, override=None) -> int:
    """取主题角色色；override 非 None 时按普通颜色解析。

    必须在 effect / paint 闭包里调用，不能在模块顶层求值。
    """
    if override is not None:
        return parse_color(override)
    return current_theme().role(role)


def state_mask(node, props) -> set[str]:
    """从节点与 props 汇总当前交互状态（disabled 优先）。"""
    if props.get("disabled") or props.get("enabled") is False:
        return {"disabled"}
    states: set[str] = set()
    if getattr(node, "pressed", False):
        states.add("pressed")
    if getattr(node, "hover", False):
        states.add("hover")
    return states


def bind_node(r, node, props, skip=()) -> None:
    """统一的属性绑定 + 指针回调转发（沿用 widgets 的语义）。"""
    from ..ui.widgets import _bind_all, _bind_pointer_callbacks

    _bind_all(r, node, props, skip=skip)
    _bind_pointer_callbacks(r, node, props)


def _queue_box(node) -> None:
    box = getattr(node, "box", None)
    if box is not None and box.w > 0 and box.h > 0:
        queue_dirty(box.x, box.y, box.w, box.h)


def pressable(r, node, props, on_click_key="on_click") -> None:
    """绑定 press/release/cancel/click；disabled 时短路，并驱动 ripple。"""

    def disabled() -> bool:
        return bool(props.get("disabled")) or props.get("enabled") is False

    def on_click(ev):
        if disabled():
            return
        callback = props.get(on_click_key)
        if callable(callback):
            callback(ev)

    def on_press(ev):
        if disabled():
            return
        ripple = getattr(node, "extra", {}).get("ripple")
        if ripple is not None:
            box = getattr(node, "box", None)
            x = ev.x - (box.x if box else 0.0)
            y = ev.y - (box.y if box else 0.0)
            if reduced_motion():
                ripple.start(x, y)
                ripple.stop()          # 减弱动效：直接到终态
            else:
                ripple.start(x, y)
        _queue_box(node)

    def on_release(ev):
        ripple = getattr(node, "extra", {}).get("ripple")
        if ripple is not None:
            ripple.stop()
        _queue_box(node)

    r.on(node, "click", lambda: on_click)
    r.on(node, "press", lambda: on_press)
    r.on(node, "release", lambda: on_release)
    r.on(node, "cancel", lambda: on_release)


def focus_ring(canvas, box, color: int, radius: float) -> None:
    """焦点环：10% state layer + 2dp 描边（满足双视觉指示）。"""
    if box.w <= 0 or box.h <= 0:
        return
    canvas.round_rect(box.x, box.y, box.w, box.h, radius, with_alpha(color, 0.10))
    for i in range(2):
        canvas.round_rect(
            box.x - 1 - i, box.y - 1 - i, box.w + 2 + 2 * i, box.h + 2 + 2 * i,
            max(0.0, radius + 1 + i), color,
        )


def soft_shadow(canvas, box, radius, layers=5, spread=8, strength=0.10) -> None:
    """轻量柔和阴影；纯 Python 回退光栅化下跳过。"""
    from .. import ui as V

    if getattr(getattr(V, "_core", None), "FALLBACK", False):
        return
    base = rgb(0, 0, 0)
    for i in range(layers, 0, -1):
        grow = spread * (i / layers)
        alpha = strength * (1.0 - i / (layers + 1.0))
        if alpha <= 0:
            continue
        canvas.round_rect(
            box.x - grow, box.y - grow + 2, box.w + 2 * grow, box.h + 2 * grow,
            radius + grow, with_alpha(base, alpha),
        )


def paint_state_layer(canvas, node, props, box, radius, content: int) -> None:
    """按当前状态绘制一层 state layer（不处理 disabled 的父级合成）。"""
    states = state_mask(node, props)
    if not states:
        return
    resolved = StateLayer(content, states).resolved()
    if resolved is None:
        return
    layer_color, opacity = resolved
    canvas.round_rect(box.x, box.y, box.w, box.h, radius, with_alpha(layer_color, opacity))


def paint_ripple(canvas, node, box) -> None:
    ripple = getattr(node, "extra", {}).get("ripple")
    if ripple is not None and ripple.active:
        ripple.paint(canvas, box)
