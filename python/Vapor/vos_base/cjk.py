"""中文排版装配：CJK 优先 FontStack + emphasized 稳定策略。"""

from __future__ import annotations

from .. import font
from ..theme import get_type_style

__all__ = ["install_cjk", "type_style", "has_cjk", "CJK_HEIGHT_FACTOR"]

CJK_HEIGHT_FACTOR = 1.07
_CJK_LANG_PREFIXES = ("zh", "cmn", "ja", "ko")
_installed = False


def _is_cjk_lang(lang: str) -> bool:
    value = str(lang or "").strip().lower()
    return any(value.startswith(prefix) for prefix in _CJK_LANG_PREFIXES)


def install_cjk(prefer_latin: bool = False) -> bool:
    """把 CJK 优先的 FontStack 装成全局文本渲染器；找不到字体返回 False。"""
    global _installed
    if _installed:
        return True
    try:
        if not font.available():
            return False
        if prefer_latin:
            fonts = list(font.system_fonts())
            if not fonts:
                return False
            stack = fonts[0] if len(fonts) == 1 else font.FontStack(list(reversed(fonts)))
        else:
            stack = font.default_font()
            if stack is None:
                return False
        if not font.install(stack):
            return False
        _installed = True
        return True
    except Exception:
        return False


def type_style(name: str, emphasized: bool = False, lang: str = "zh") -> dict:
    """按语言选择行高系数的字阶包装；emphasized 走强调字阶。"""
    factor = CJK_HEIGHT_FACTOR if _is_cjk_lang(lang) else 1.0
    return get_type_style(name, emphasized=emphasized, lang_height_factor=factor)


def has_cjk(text: str) -> bool:
    return font.has_cjk(text)
