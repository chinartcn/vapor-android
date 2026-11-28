"""MD3 baseline 静态 ColorScheme（浅/深）。

来源：Material Theme Builder 导出
- 种子色：``#6750A4``（MD3 baseline 紫）
- 对比度等级：Standard
- 导出日期：2026-11-28
- 格式：``0xAARRGGBB``（与 ``Vapor.ui.colors.color()`` 一致）

说明：这些是 dynamic color 关闭时的兜底值；组件库应优先按角色名取值，
不要在组件里硬编码这里的 hex。
"""

from __future__ import annotations

__all__ = ["baseline_light", "baseline_dark"]


def baseline_light() -> dict[str, int]:
    """浅色 baseline（26 个标准角色）。"""
    return {
        "primary": 0xFF6750A4,
        "onPrimary": 0xFFFFFFFF,
        "primaryContainer": 0xFFEADDFF,
        "onPrimaryContainer": 0xFF21005D,
        "secondary": 0xFF625B71,
        "onSecondary": 0xFFFFFFFF,
        "secondaryContainer": 0xFFE8DEF8,
        "onSecondaryContainer": 0xFF1D192B,
        "tertiary": 0xFF7D5260,
        "onTertiary": 0xFFFFFFFF,
        "tertiaryContainer": 0xFFFFD8E4,
        "onTertiaryContainer": 0xFF31111D,
        "error": 0xFFB3261E,
        "onError": 0xFFFFFFFF,
        "errorContainer": 0xFFF9DEDC,
        "onErrorContainer": 0xFF410E0B,
        "surface": 0xFFFFFBFE,
        "onSurface": 0xFF1C1B1F,
        "onSurfaceVariant": 0xFF49454F,
        "surfaceContainerLowest": 0xFFFFFFFF,
        "surfaceContainerLow": 0xFFF7F2FA,
        "surfaceContainer": 0xFFF3EDF7,
        "surfaceContainerHigh": 0xFFECE6F0,
        "surfaceContainerHighest": 0xFFE6E0E9,
        "outline": 0xFF79747E,
        "outlineVariant": 0xFFCAC4D0,
    }


def baseline_dark() -> dict[str, int]:
    """深色 baseline（26 个标准角色）。"""
    return {
        "primary": 0xFFD0BCFF,
        "onPrimary": 0xFF381E72,
        "primaryContainer": 0xFF4F378B,
        "onPrimaryContainer": 0xFFEADDFF,
        "secondary": 0xFFCCC2DC,
        "onSecondary": 0xFF332D41,
        "secondaryContainer": 0xFF4A4458,
        "onSecondaryContainer": 0xFFE8DEF8,
        "tertiary": 0xFFEFB8C8,
        "onTertiary": 0xFF492532,
        "tertiaryContainer": 0xFF633B48,
        "onTertiaryContainer": 0xFFFFD8E4,
        "error": 0xFFF2B8B5,
        "onError": 0xFF601410,
        "errorContainer": 0xFF8C1D18,
        "onErrorContainer": 0xFFF9DEDC,
        "surface": 0xFF1C1B1F,
        "onSurface": 0xFFE6E1E5,
        "onSurfaceVariant": 0xFFCAC4D0,
        "surfaceContainerLowest": 0xFF0F0D13,
        "surfaceContainerLow": 0xFF1D1B20,
        "surfaceContainer": 0xFF211F26,
        "surfaceContainerHigh": 0xFF2B2930,
        "surfaceContainerHighest": 0xFF36343B,
        "outline": 0xFF938F99,
        "outlineVariant": 0xFF49454F,
    }
