"""MD3 Design Token 静态数据层（纯数据 + 纯函数，无运行时状态）。

数据来源：lwhhz/Android-MD3-Knowledge 经核验的快照，见 ``data/*.json`` 的
``source`` / ``checked_on`` / ``verified`` 字段。

对比度契约（组件库阶段校验用）：
- 小字 4.5:1；大字/图形 3:1；成组容器对背景 3:1；
- disabled 态不要求；独立组件（如 FAB）容器对背景不要求；
- 只有 ``on*`` 与父色的配对是保证足够的。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

__all__ = [
    "ROLE_NAMES", "ROLE_GROUPS", "ADD_ON_ROLES",
    "TYPE_SCALE", "TYPE_SCALE_COMPOSE", "TYPE_SCALE_EMPHASIZED",
    "SHAPES", "SHAPE_COMPONENT_DEFAULTS",
    "SPACING", "get_role_names", "get_type_style", "get_shape", "get_spacing",
]

_DATA = Path(__file__).resolve().parent / "data"


def _load(name: str) -> dict[str, Any]:
    with (_DATA / name).open("r", encoding="utf-8") as fh:
        return json.load(fh)


_colors = _load("colors_roles.json")
_typography = _load("typography.json")
_shapes = _load("shapes.json")
_spacing = _load("spacing.json")

# ---- 色彩角色 ----

ROLE_GROUPS: dict[str, tuple[str, ...]] = {
    group: tuple(roles) for group, roles in _colors["groups"].items()
}
ROLE_NAMES: tuple[str, ...] = tuple(
    role for roles in ROLE_GROUPS.values() for role in roles
)
ADD_ON_ROLES: dict[str, tuple[str, ...]] = {
    group: tuple(roles) for group, roles in _colors["add_on"].items()
}
ADD_ON_ROLES["functional"] = tuple(_colors.get("functional", ()))

# ---- 字阶 ----

TYPE_SCALE: dict[str, dict[str, Any]] = copy.deepcopy(_typography["styles"])
TYPE_SCALE_EMPHASIZED: dict[str, dict[str, Any]] = copy.deepcopy(
    _typography["emphasized"]
)

# 官网值 vs Compose 实现的对照（仅覆盖存在差异的档位）。
TYPE_SCALE_COMPOSE: dict[str, dict[str, Any]] = copy.deepcopy(TYPE_SCALE)
for _name, _override in _typography["compose_values"]["baseline"].items():
    if _name in TYPE_SCALE_COMPOSE:
        TYPE_SCALE_COMPOSE[_name].update(_override)

# ---- 形状 / 间距 ----

SHAPES: dict[str, int | str] = dict(_shapes["shapes"])
SHAPE_COMPONENT_DEFAULTS: dict[str, Any] = copy.deepcopy(
    _shapes["component_defaults"]
)
SPACING: dict[str, int] = dict(_spacing["spacing"])


def get_role_names(group: str | None = None) -> tuple[str, ...]:
    """返回 26 个标准角色；传 group 时只返回该组。"""
    if group is None:
        return ROLE_NAMES
    return ROLE_GROUPS[group]


def get_type_style(
    name: str,
    emphasized: bool = False,
    lang_height_factor: float = _typography["language_height"]["medium"],
) -> dict[str, Any]:
    """取一档字阶。``name`` 用 baseline 名（如 ``bodyMedium``）。

    ``lang_height_factor`` 默认 1.07（中文/东亚 medium 行高系数）；拉丁场景
    请显式传 1.0。返回的是副本，调用方可自由修改。
    """
    source = TYPE_SCALE_EMPHASIZED if emphasized else TYPE_SCALE
    key = f"{name}Emphasized" if emphasized else name
    if key not in source:
        raise KeyError(f"未知字阶: {key}")
    style = dict(source[key])
    if lang_height_factor and lang_height_factor != 1.0:
        style["lineHeight"] = round(style["lineHeight"] * lang_height_factor)
    return style


def get_shape(name: str, shortest_side: float | None = None) -> float:
    """取形状圆角。``full`` 需要传入容器最短边，返回其一半。"""
    if name not in SHAPES:
        raise KeyError(f"未知形状: {name}")
    value = SHAPES[name]
    if value == "shortest-side / 2":
        if shortest_side is None:
            raise ValueError("shape 'full' 需要 shortest_side")
        return float(shortest_side) / 2.0
    return float(value)


def get_spacing(name: str) -> int:
    """取 4dp 网格间距档位。"""
    return SPACING[name]
