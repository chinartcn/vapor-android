"""Vapor OS Expressive 基础层：形状变形 + 中文排版装配。"""

from __future__ import annotations

from .cjk import CJK_HEIGHT_FACTOR, has_cjk, install_cjk, type_style
from .shape_morph import MorphSpec, ShapeMorph, morph_radius, morph_shape_paint

__version__ = "1.0.0"

__all__ = [
    "MorphSpec",
    "ShapeMorph",
    "morph_radius",
    "morph_shape_paint",
    "CJK_HEIGHT_FACTOR",
    "has_cjk",
    "install_cjk",
    "type_style",
    "__version__",
]
