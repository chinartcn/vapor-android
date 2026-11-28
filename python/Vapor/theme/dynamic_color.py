"""种子色 → HCT tonal palette → MD3 ColorScheme（26 角色）。

HCT/CAM16 采用 vendor 的 material-color-utilities 纯 Python 移植
（``theme/_mcu``，MIT，见其 LICENSE），不使用 HSL 近似。
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from ._mcu.hct import Hct
from .runtime import ROLE_NAMES, Theme

__all__ = [
    "ContrastLevel",
    "seed_to_scheme",
    "hct_from_argb",
    "argb_from_hct",
]

# 错误色保持静态语义色，不随种子变化；浅/深主题各自取 tone。
ERROR_HUE = 25.0
ERROR_CHROMA = 84.0

_CONTRAST_LEVELS = ("standard", "medium", "high")


class ContrastLevel(str, Enum):
    STANDARD = "standard"
    MEDIUM = "medium"
    HIGH = "high"


def hct_from_argb(argb: int) -> tuple[float, float, float]:
    """``0xAARRGGBB`` → (hue, chroma, tone)。"""
    h = Hct.from_int(int(argb) & 0xFFFFFFFF)
    return (h.hue, h.chroma, h.tone)


def argb_from_hct(hue: float, chroma: float, tone: float) -> int:
    """(hue, chroma, tone) → ``0xAARRGGBB``。"""
    hue = float(hue) % 360.0
    chroma = max(0.0, min(120.0, float(chroma)))
    tone = max(0.0, min(100.0, float(tone)))
    return int(Hct.from_hct(hue, chroma, tone).to_int()) & 0xFFFFFFFF


def _palette(seed: int) -> dict[str, tuple[float, float]]:
    s = Hct.from_int(seed & 0xFFFFFFFF)
    hue = s.hue
    chroma = max(0.0, s.chroma)
    return {
        "primary": (hue, chroma),
        "secondary": (hue, max(chroma / 3.0, 1.0)),
        "tertiary": ((hue + 60.0) % 360.0, max(chroma / 2.0, 1.0)),
        "neutral": (hue, 4.0),
        "neutralVariant": (hue, 8.0),
        "error": (ERROR_HUE, ERROR_CHROMA),
    }


@lru_cache(maxsize=1024)
def _tone(hue: float, chroma: float, tone: float) -> int:
    return argb_from_hct(hue, chroma, tone)


# role -> (palette key, tone)
_LIGHT = {
    "primary": ("primary", 40), "onPrimary": ("primary", 100),
    "primaryContainer": ("primary", 90), "onPrimaryContainer": ("primary", 10),
    "secondary": ("secondary", 40), "onSecondary": ("secondary", 100),
    "secondaryContainer": ("secondary", 90), "onSecondaryContainer": ("secondary", 10),
    "tertiary": ("tertiary", 40), "onTertiary": ("tertiary", 100),
    "tertiaryContainer": ("tertiary", 90), "onTertiaryContainer": ("tertiary", 10),
    "error": ("error", 40), "onError": ("error", 100),
    "errorContainer": ("error", 90), "onErrorContainer": ("error", 10),
    "background": ("neutral", 99), "onBackground": ("neutral", 10),
    "surface": ("neutral", 99), "onSurface": ("neutral", 10),
    "onSurfaceVariant": ("neutralVariant", 30),
    "outline": ("neutralVariant", 50), "outlineVariant": ("neutralVariant", 80),
    "shadow": ("neutral", 0), "scrim": ("neutral", 0),
    # 文档 1 若扩展了角色，这里也覆盖，避免 KeyError。
    "surfaceTint": ("primary", 40),
    "inverseSurface": ("neutral", 20), "inverseOnSurface": ("neutral", 95),
    "inversePrimary": ("primary", 80),
    "surfaceDim": ("neutral", 87), "surfaceBright": ("neutral", 98),
    "surfaceContainerLowest": ("neutral", 100), "surfaceContainerLow": ("neutral", 96),
    "surfaceContainer": ("neutral", 94), "surfaceContainerHigh": ("neutral", 92),
    "surfaceContainerHighest": ("neutral", 90),
}

_DARK = {
    "primary": ("primary", 80), "onPrimary": ("primary", 20),
    "primaryContainer": ("primary", 30), "onPrimaryContainer": ("primary", 90),
    "secondary": ("secondary", 80), "onSecondary": ("secondary", 20),
    "secondaryContainer": ("secondary", 30), "onSecondaryContainer": ("secondary", 90),
    "tertiary": ("tertiary", 80), "onTertiary": ("tertiary", 20),
    "tertiaryContainer": ("tertiary", 30), "onTertiaryContainer": ("tertiary", 90),
    "error": ("error", 80), "onError": ("error", 20),
    "errorContainer": ("error", 30), "onErrorContainer": ("error", 90),
    "background": ("neutral", 10), "onBackground": ("neutral", 90),
    "surface": ("neutral", 10), "onSurface": ("neutral", 90),
    "onSurfaceVariant": ("neutralVariant", 80),
    "outline": ("neutralVariant", 60), "outlineVariant": ("neutralVariant", 30),
    "shadow": ("neutral", 0), "scrim": ("neutral", 0),
    "surfaceTint": ("primary", 80),
    "inverseSurface": ("neutral", 90), "inverseOnSurface": ("neutral", 20),
    "inversePrimary": ("primary", 40),
    "surfaceDim": ("neutral", 6), "surfaceBright": ("neutral", 24),
    "surfaceContainerLowest": ("neutral", 4), "surfaceContainerLow": ("neutral", 10),
    "surfaceContainer": ("neutral", 12), "surfaceContainerHigh": ("neutral", 17),
    "surfaceContainerHighest": ("neutral", 22),
}

# 对比度等级只调整关键内容/容器 tone，保证更高对比度。
_LIGHT_CONTRAST = {
    "standard": {},
    "medium": {"primary": 35, "secondary": 35, "tertiary": 35, "error": 35},
    "high": {
        "primary": 30, "secondary": 30, "tertiary": 30, "error": 30,
        "surface": 100, "background": 100, "onSurface": 0,
        "onSurfaceVariant": 20, "outline": 40, "outlineVariant": 70,
    },
}

_DARK_CONTRAST = {
    "standard": {},
    "medium": {"primary": 85, "secondary": 85, "tertiary": 85, "error": 85},
    "high": {
        "primary": 90, "secondary": 90, "tertiary": 90, "error": 90,
        "onPrimary": 10, "onSurface": 95, "onSurfaceVariant": 90,
        "outline": 70, "outlineVariant": 40,
    },
}


def seed_to_scheme(seed: int, dark: bool = False, contrast="standard") -> Theme:
    """由种子色生成完整 MD3 角色方案。"""
    level = contrast if isinstance(contrast, ContrastLevel) else ContrastLevel(contrast)
    palettes = _palette(seed)
    specs = dict(_DARK if dark else _LIGHT)
    for role, tone in (_DARK_CONTRAST if dark else _LIGHT_CONTRAST)[level.value].items():
        key = specs.get(role, ("neutral", 50))[0]
        specs[role] = (key, tone)

    colors: dict[str, int] = {}
    for role in ROLE_NAMES:
        key, tone = specs.get(role, ("neutral", 50))
        hue, chroma = palettes[key]
        colors[role] = _tone(round(hue, 6), round(chroma, 6), float(tone))

    mode = "dark" if dark else "light"
    return Theme(colors, contrast=level.value, name=f"dynamic-{seed & 0xFFFFFFFF:08X}-{mode}-{level.value}")
