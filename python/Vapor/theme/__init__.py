"""Vapor MD3 Theme —— 静态 Design Token + 运行时 / 动态取色。"""

from __future__ import annotations

from .runtime import THEME_KEY, Theme, current_theme, set_theme, use_theme
from .dynamic_color import ContrastLevel, argb_from_hct, hct_from_argb, seed_to_scheme
from .static_schemes import baseline_dark, baseline_light
from .tokens import (
    ADD_ON_ROLES,
    ROLE_GROUPS,
    ROLE_NAMES,
    SHAPE_COMPONENT_DEFAULTS,
    SHAPES,
    SPACING,
    TYPE_SCALE,
    TYPE_SCALE_COMPOSE,
    TYPE_SCALE_EMPHASIZED,
    get_role_names,
    get_shape,
    get_spacing,
    get_type_style,
)

__all__ = [
    # 静态 token（文档 1）
    "ROLE_NAMES", "ROLE_GROUPS", "ADD_ON_ROLES",
    "TYPE_SCALE", "TYPE_SCALE_COMPOSE", "TYPE_SCALE_EMPHASIZED",
    "SHAPES", "SHAPE_COMPONENT_DEFAULTS", "SPACING",
    "get_role_names", "get_type_style", "get_shape", "get_spacing",
    "baseline_light", "baseline_dark",
    # 运行时 / 动态取色（文档 2）
    "Theme",
    "current_theme",
    "set_theme",
    "use_theme",
    "THEME_KEY",
    "seed_to_scheme",
    "ContrastLevel",
    "hct_from_argb",
    "argb_from_hct",
]
