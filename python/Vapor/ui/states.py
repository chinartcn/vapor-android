"""MD3 交互状态层（state layer）与 ripple 辅助。

规则来源：``01-Foundations/interaction-states.md``（核验 2026-11-16）。

- 状态层颜色取内容色（通常是 ``on*`` 色），由调用方注入，本模块不 import theme；
- 同一时刻只解析出一层；
- 透明度固定：hover 8% / focus 10% / press 10% / drag 16% / disabled 38%；
- state layer 视觉 40dp，可交互热区 48dp，二者不可混用；
- disabled 优先并屏蔽 hover 等其它状态；disabled 不要求对比度。
"""

from __future__ import annotations

import time
from typing import Callable

from .colors import with_alpha

__all__ = [
    "State", "StateLayer", "STATE_OPACITY", "STATE_LAYER_SIZE", "TOUCH_TARGET_SIZE",
    "apply_state_layer", "Ripple", "attach_ripple",
]


class State:
    ENABLED = "enabled"
    DISABLED = "disabled"
    HOVER = "hover"
    FOCUSED = "focused"
    PRESSED = "pressed"
    DRAGGED = "dragged"


STATE_OPACITY: dict[str, float] = {
    "hover": 0.08,
    "focused": 0.10,
    "pressed": 0.10,
    "drag": 0.16,
    "disabled": 0.38,
}

STATE_LAYER_SIZE = 40          # dp
TOUCH_TARGET_SIZE = 48         # dp

# 解析优先级：disabled 最先（屏蔽其它）；其余按交互强度降序。
_PRIORITY = ("disabled", "drag", "pressed", "focused", "hover")


class StateLayer:
    """持有一组激活状态，解析出唯一的 state layer。"""

    __slots__ = ("on_color", "_states")

    def __init__(self, on_color: int, user_states: set[str] | None = None):
        self.on_color = int(on_color)
        self._states = set(user_states or ())

    def set(self, state: str, active: bool) -> None:
        if state in (State.ENABLED, State.DISABLED) and active:
            if state == State.DISABLED:
                self._states.add(State.DISABLED)
            else:
                self._states.discard(State.DISABLED)
            return
        if active:
            self._states.add(state)
        else:
            self._states.discard(state)

    @property
    def states(self) -> set[str]:
        return set(self._states)

    def _name(self) -> str:
        for name in _PRIORITY:
            if name in self._states:
                return name
        return State.ENABLED

    @property
    def priority(self) -> str:
        """当前生效的状态名（enabled 表示无状态层）。"""
        return self._name()

    def resolved_priority_text(self) -> str:
        """当前生效状态名；测试/调试用。disabled 时不会出现 hover。"""
        return self._name()

    def resolved(self) -> tuple[int, float] | None:
        """返回 ``(内容色, 透明度)``；enabled 时返回 None。"""
        name = self._name()
        if name == State.ENABLED:
            return None
        return self.on_color, STATE_OPACITY[name]


def _channels(c: int) -> tuple[int, int, int, int]:
    return (c >> 24) & 0xFF, (c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF


def apply_state_layer(container_color: int, on_color: int, states: set[str]) -> int:
    """把状态层叠加到容器色，返回合成后的 ``0xAARRGGBB``（纯函数）。

    ``on_color`` 由调用方注入（如 ``theme.role("onPrimary")``），本模块不依赖
    theme。容器透明度保持不变；一屏内只叠加一层。
    """
    layer = StateLayer(on_color, states)
    resolved = layer.resolved()
    if resolved is None:
        return int(container_color) & 0xFFFFFFFF

    src, opacity = resolved
    ca, cr, cg, cb = _channels(int(container_color) & 0xFFFFFFFF)
    sa, sr, sg, sb = _channels(src)
    alpha = opacity * (sa / 255.0)
    out_r = round(sr * alpha + cr * (1.0 - alpha))
    out_g = round(sg * alpha + cg * (1.0 - alpha))
    out_b = round(sb * alpha + cb * (1.0 - alpha))
    return (ca << 24) | (out_r << 16) | (out_g << 8) | out_b


class Ripple:
    """按压水波纹状态：以触点为中心扩散并淡出。

    仅持有视觉参数，实际绘制由组件在 ``paint`` 回调里调用 :meth:`paint`。
    """

    __slots__ = ("on_color", "opacity", "duration", "x", "y", "radius", "_start", "_active")

    def __init__(self, on_color: int, opacity: float = 0.10, duration: float = 0.4):
        self.on_color = int(on_color)
        self.opacity = float(opacity)
        self.duration = float(duration)
        self.x = self.y = 0.0
        self.radius = 0.0
        self._start = 0.0
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def start(self, x: float, y: float, now: float | None = None) -> None:
        self.x, self.y = float(x), float(y)
        self._start = time.monotonic() if now is None else float(now)
        self._active = True

    def stop(self) -> None:
        self._active = False

    def progress(self, now: float | None = None) -> float:
        if not self._active:
            return 1.0
        now = time.monotonic() if now is None else float(now)
        return max(0.0, min(1.0, (now - self._start) / self.duration))

    def paint(self, canvas, box, now: float | None = None) -> bool:
        """绘制当前波纹；返回 False 表示已结束、可从绘制列表移除。"""
        if not self._active:
            return False
        t = self.progress(now)
        max_r = max(box.w, box.h) * 0.75
        radius = max_r * t
        alpha = self.opacity * (1.0 - t)
        color = with_alpha(self.on_color, alpha)
        cx = box.x + self.x
        cy = box.y + self.y
        try:
            canvas.disc(cx, cy, radius, color)
        except Exception:
            return False
        if t >= 1.0:
            self._active = False
        return self._active


def attach_ripple(node, on_color: int, opacity: float = 0.10) -> Ripple:
    """给节点挂上 ripple（主键点击与触摸一致）。

    本阶段只维护状态与绘制辅助，不修改 ``widgets.py``；组件库在 paint 时
    调用 ``node.extra['ripple'].paint(canvas, box)`` 即可。
    """
    ripple = Ripple(on_color, opacity)
    node.extra["ripple"] = ripple

    def on_press(ev) -> None:
        box = getattr(node, "box", None)
        ripple.start(ev.x - (box.x if box else 0.0), ev.y - (box.y if box else 0.0))

    def on_release(ev) -> None:
        ripple.stop()

    def _provider(handler: Callable) -> Callable[[], Callable]:
        return lambda: handler

    for event, handler in (("press", on_press), ("release", on_release)):
        node.events.setdefault(event, []).append((_provider(handler), []))
    return ripple
