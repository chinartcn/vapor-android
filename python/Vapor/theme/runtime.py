"""MD3 Theme 运行时：主题对象、全局切换与局部覆盖。

只负责“颜色角色 → 颜色”的运行时与响应式传播；静态 token 数值由
``theme/tokens.py``（文档 1）提供，动效/状态层由文档 3 提供。
"""

from __future__ import annotations

from contextlib import contextmanager

from .. import core
from .tokens import ROLE_NAMES

__all__ = ["Theme", "current_theme", "set_theme", "use_theme", "THEME_KEY", "ROLE_NAMES"]

THEME_KEY = "vapor.theme"
_CONTRAST_LEVELS = ("standard", "medium", "high")


class Theme:
    """一组 MD3 颜色角色（``0xAARRGGBB``）。"""

    __slots__ = ("_colors", "_contrast", "_name")

    def __init__(self, colors: dict[str, int], *, contrast: str = "standard", name: str = ""):
        if contrast not in _CONTRAST_LEVELS:
            raise ValueError(f"未知对比度等级: {contrast!r}")
        self._colors = {str(k): int(v) & 0xFFFFFFFF for k, v in dict(colors).items()}
        self._contrast = contrast
        self._name = name

    def role(self, name: str) -> int:
        """返回角色颜色；未知角色抛 ``KeyError``。"""
        return self._colors[name]

    def with_opacity(self, name: str, opacity: float) -> int:
        """返回该角色乘上透明度系数的颜色（供 state layer 使用）。"""
        c = self.role(name)
        factor = max(0.0, min(1.0, float(opacity)))
        alpha = int(((c >> 24) & 0xFF) * factor)
        return (alpha << 24) | (c & 0x00FFFFFF)

    @property
    def colors(self) -> dict[str, int]:
        return self._colors

    @property
    def contrast(self) -> str:
        return self._contrast

    @property
    def name(self) -> str:
        return self._name

    def __repr__(self) -> str:
        return f"Theme(name={self._name!r}, contrast={self._contrast!r}, roles={len(self._colors)})"


_current = core.ref(None)


def _make_baseline() -> Theme:
    from .dynamic_color import seed_to_scheme  # 延迟导入，避免模块循环

    return seed_to_scheme(0xFF6750A4)  # MD3 baseline primary


# 模块导入即建立 baseline light，避免首个 effect 内写 ref 造成二次触发。
_current.value = _make_baseline()


def current_theme() -> Theme:
    """当前主题：局部 ``use_theme`` 优先，否则全局响应式主题。"""
    local = core.inject(THEME_KEY, None)
    if local is not None:
        return local
    return _current.value


def set_theme(theme: Theme) -> None:
    """全局切换主题；依赖 ``current_theme()`` 的 effect 会自动重跑。"""
    if not isinstance(theme, Theme):
        raise TypeError("set_theme() 需要 Theme 实例")
    _current.value = theme


@contextmanager
def use_theme(theme: Theme):
    """上下文管理器：在当前实例栈内临时覆盖主题，退出后自动还原。"""
    if not isinstance(theme, Theme):
        raise TypeError("use_theme() 需要 Theme 实例")
    core.push_instance({"name": "theme-scope"})
    try:
        core.provide(THEME_KEY, theme)
        yield theme
    finally:
        core.pop_instance()
