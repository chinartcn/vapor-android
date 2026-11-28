"""MD3 Expressive 形状变形（shape morphing）。

独立于 ``ui.states`` 的透明度状态层：本模块只负责圆角插值，组件通过
``node.extra["shape_morph"]`` opt-in，不挂载则完全无副作用。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from ..theme import get_shape
from ..ui.motion import SPRING, reduced_motion, spring
from ..ui.renderer import queue_dirty

__all__ = ["MorphSpec", "morph_radius", "ShapeMorph", "morph_shape_paint"]


@dataclass(frozen=True)
class MorphSpec:
    """from/to 是两个 shape token 名；press 时 rest->pressed，release 反向。"""

    rest: str
    pressed: str
    speed: str = "fast"


def _spring_ease(spec: MorphSpec):
    return spring("expressive", spec.speed, "spatial")


def _spring_duration(spec: MorphSpec) -> float:
    """由 spring token 推导稳定时间，避免另写一套动效时长常量。"""
    token = SPRING["expressive"][spec.speed + "Spatial"]
    zeta = float(token["dampingRatio"])
    omega0 = math.sqrt(float(token["stiffness"]))
    return max(0.001, 6.0 / max(zeta * omega0, 1e-6))


def _radius_at(spec: MorphSpec, shortest_side: float, progress: float) -> float:
    rest = get_shape(spec.rest, shortest_side)
    pressed = get_shape(spec.pressed, shortest_side)
    value = rest + (pressed - rest) * float(progress)
    # 欠阻尼可能过冲；负半径会让 canvas.round_rect 直接抛异常。
    return max(0.0, min(float(shortest_side) * 0.6, value))


def morph_radius(spec: MorphSpec, shortest_side: float, t: float) -> float:
    """给定归一化时间 t（0..1，可过冲），返回当前圆角半径。"""
    if t <= 0.0:
        return _radius_at(spec, shortest_side, 0.0)
    if t >= 1.0:
        return _radius_at(spec, shortest_side, 1.0)
    return _radius_at(spec, shortest_side, _spring_ease(spec)(float(t)))


class ShapeMorph:
    """挂在 node.extra['shape_morph']，由 press/release 驱动。"""

    __slots__ = ("spec", "_from", "_target", "_progress", "_t0", "_active", "_duration")

    def __init__(self, spec: MorphSpec):
        self.spec = spec
        self._from = 0.0
        self._target = 0.0
        self._progress = 0.0
        self._t0 = None
        self._active = False
        self._duration = _spring_duration(spec)

    def _now(self, now):
        return time.monotonic() if now is None else float(now)

    def _current(self, now=None) -> float:
        if not self._active or self._t0 is None:
            return self._progress
        u = (self._now(now) - self._t0) / self._duration
        if u >= 1.0:
            self._progress = self._target
            self._active = False
            return self._progress
        eased = _spring_ease(self.spec)(max(0.0, u))
        self._progress = self._from + (self._target - self._from) * eased
        return self._progress

    def press(self, now=None) -> None:
        if reduced_motion():
            self._progress = 1.0
            self._active = False
            self._t0 = None
            return
        self._from = self._current(now)
        self._target = 1.0
        self._t0 = self._now(now)
        self._active = True

    def release(self, now=None) -> None:
        if reduced_motion():
            self._progress = 0.0
            self._active = False
            self._t0 = None
            return
        self._from = self._current(now)
        self._target = 0.0
        self._t0 = self._now(now)
        self._active = True

    def radius(self, shortest_side: float, now=None) -> float:
        return _radius_at(self.spec, shortest_side, self._current(now))

    @property
    def active(self) -> bool:
        return self._active


def morph_shape_paint(canvas, box, node) -> None:
    """把节点当前的形变圆角画成一层容器；未挂 ShapeMorph 时无副作用。"""
    morph = getattr(node, "extra", {}).get("shape_morph")
    if morph is None or box.w <= 0 or box.h <= 0:
        return
    radius = morph.radius(min(box.w, box.h))
    background = node.props.get("bg")
    if background in (None, "none", 0):
        if morph.active:
            queue_dirty(box.x, box.y, box.w, box.h)
        return
    try:
        from ..ui.colors import color as parse_color

        canvas.round_rect(box.x, box.y, box.w, box.h, radius,
                          parse_color(background))
    except Exception:
        pass
    if morph.active:
        queue_dirty(box.x, box.y, box.w, box.h)
