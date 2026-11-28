"""Vapor MD3 主题系统（运行时 + 动态取色）。"""

from .runtime import ROLE_NAMES, THEME_KEY, Theme, current_theme, set_theme, use_theme
from .dynamic_color import ContrastLevel, argb_from_hct, hct_from_argb, seed_to_scheme

__all__ = [
    "Theme",
    "current_theme",
    "set_theme",
    "use_theme",
    "THEME_KEY",
    "ROLE_NAMES",
    "seed_to_scheme",
    "ContrastLevel",
    "hct_from_argb",
    "argb_from_hct",
]
