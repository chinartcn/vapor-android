"""MD3 动效（motion）基础设施。

数值来源：``05-Design-Tokens/motion.json``（核验 2026-11-14 / 2026-11-22）。

方向：当前首选 **spring**（motion physics）；``EASING`` + ``DURATION`` 是
legacy 方案，仅用于平台转场兼容，官方已标为不再维护。新代码不要从 legacy
数值表里抄默认值。

规则：
- 退出动画快于进入，约为 60–70%；
- 同屏并列动画时长差不超过 3 倍；超过 400ms 必须有中间态；
- 颜色/透明度只能用 effects 类（dampingRatio = 1.0），不允许过冲；
- spatial 类（位置/尺寸/圆角）允许欠阻尼回弹。
"""

from __future__ import annotations

import math
import os
from typing import Callable

__all__ = [
    "SPRING", "EASING", "DURATION",
    "spring", "cubic_bezier", "reduced_motion",
]

# ---- spring: md.sys.motion.spring.{speed}.{type} ----

SPRING: dict[str, dict[str, dict[str, float]]] = {
    "standard": {
        "fastSpatial": {"dampingRatio": 0.9, "stiffness": 1400},
        "defaultSpatial": {"dampingRatio": 0.9, "stiffness": 700},
        "slowSpatial": {"dampingRatio": 0.9, "stiffness": 300},
        "fastEffects": {"dampingRatio": 1.0, "stiffness": 3800},
        "defaultEffects": {"dampingRatio": 1.0, "stiffness": 1600},
        "slowEffects": {"dampingRatio": 1.0, "stiffness": 800},
    },
    "expressive": {
        "fastSpatial": {"dampingRatio": 0.6, "stiffness": 800},
        "defaultSpatial": {"dampingRatio": 0.8, "stiffness": 380},
        "slowSpatial": {"dampingRatio": 0.8, "stiffness": 200},
        "fastEffects": {"dampingRatio": 1.0, "stiffness": 3800},
        "defaultEffects": {"dampingRatio": 1.0, "stiffness": 1600},
        "slowEffects": {"dampingRatio": 1.0, "stiffness": 800},
    },
}

_SPEEDS = ("fast", "default", "slow")
_KINDS = {"spatial": "Spatial", "effects": "Effects"}


def spring(scheme: str, speed: str, kind: str) -> Callable[[float], float]:
    """返回 ``0..1 归一化时间 -> 0..1(+过冲) 进度`` 的求值器。

    使用阻尼弹簧物理模拟（单位质量）。effects 类阻尼比为 1.0，保证单调不
    过冲；spatial 类可回弹。结果可直接作为 ``App.animate`` 的 ``easing``。
    """
    if scheme not in SPRING:
        raise KeyError(f"未知 spring scheme: {scheme}")
    if speed not in _SPEEDS:
        raise KeyError(f"未知 spring speed: {speed}")
    if kind not in _KINDS:
        raise KeyError(f"未知 spring kind: {kind}")
    token = SPRING[scheme][speed + _KINDS[kind]]
    zeta = float(token["dampingRatio"])
    omega0 = math.sqrt(float(token["stiffness"]))
    settle = 6.0 / max(zeta * omega0, 1e-6)
    end = _spring_raw(settle, zeta, omega0)

    def evaluate(t: float) -> float:
        u = max(0.0, min(1.0, float(t)))
        if u <= 0.0:
            return 0.0
        if u >= 1.0:
            return 1.0
        return _spring_raw(u * settle, zeta, omega0) / end

    return evaluate


def _spring_raw(t: float, zeta: float, omega0: float) -> float:
    """阻尼弹簧从位移 0 到 1 的解析解（初速度为 0）。"""
    if zeta < 1.0:
        wd = omega0 * math.sqrt(1.0 - zeta * zeta)
        decay = math.exp(-zeta * omega0 * t)
        return 1.0 - decay * (
            math.cos(wd * t) + (zeta * omega0 / wd) * math.sin(wd * t)
        )
    # 临界/过阻尼：临界阻尼足够（本表 effects 全为 1.0）。
    decay = math.exp(-omega0 * t)
    return 1.0 - decay * (1.0 + omega0 * t)


# ---- legacy easing ----

EASING: dict[str, tuple[float, float, float, float]] = {
    "standard": (0.2, 0.0, 0.0, 1.0),
    "standardDecelerate": (0.0, 0.0, 0.0, 1.0),
    "standardAccelerate": (0.3, 0.0, 1.0, 1.0),
    "emphasizedDecelerate": (0.05, 0.7, 0.1, 1.0),
    "emphasizedAccelerate": (0.3, 0.0, 0.8, 0.15),
    # Android 的 emphasized 是多段 pathInterpolator，CSS/iOS 无对应曲线，
    # 按文档用 standard 兜底。
    "emphasized": (0.2, 0.0, 0.0, 1.0),
}


def cubic_bezier(x1: float, y1: float, x2: float, y2: float) -> Callable[[float], float]:
    """返回 CSS ``cubic-bezier(x1,y1,x2,y2)`` 的求值函数。"""

    def bezier(t: float, p1: float, p2: float) -> float:
        mt = 1.0 - t
        return 3.0 * mt * mt * t * p1 + 3.0 * mt * t * t * p2 + t * t * t

    def evaluate(x: float) -> float:
        x = max(0.0, min(1.0, float(x)))
        if x <= 0.0:
            return 0.0
        if x >= 1.0:
            return 1.0
        lo, hi = 0.0, 1.0
        t = x
        for _ in range(40):
            t = (lo + hi) / 2.0
            if bezier(t, x1, x2) < x:
                lo = t
            else:
                hi = t
        return bezier((lo + hi) / 2.0, y1, y2)

    return evaluate


# ---- duration（16 档，ms） ----

DURATION: dict[str, int] = {
    "short1": 50, "short2": 100, "short3": 150, "short4": 200,
    "medium1": 250, "medium2": 300, "medium3": 350, "medium4": 400,
    "long1": 450, "long2": 500, "long3": 550, "long4": 600,
    "extraLong1": 700, "extraLong2": 800, "extraLong3": 900, "extraLong4": 1000,
}


def reduced_motion() -> bool:
    """宿主/系统是否要求减弱动效。默认 False。

    当前读取 ``VAPOR_REDUCED_MOTION`` 环境变量；后续可由 Android
    accessibility 设置注入。
    """
    value = os.environ.get("VAPOR_REDUCED_MOTION", "").strip().lower()
    return value in ("1", "true", "yes", "on")
