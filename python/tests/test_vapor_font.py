"""Vapor.font 字体扩展测试。

有原生 Vapor._ui 且系统里有字体时才做光栅化断言，否则自动跳过，
保证纯 Python 回退环境也能跑通。

    PYTHONPATH=python python3 python/tests/test_vapor_font.py
"""

from __future__ import annotations

import os
import sys

try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Vapor import font  # noqa: E402
from Vapor import ui as V  # noqa: E402


def _stack():
    fonts = font.system_fonts()
    if not fonts:
        return None
    return fonts[0] if len(fonts) == 1 else font.FontStack(fonts)


def _painted(canvas):
    buf = canvas.buffer
    return sum(1 for i in range(0, len(buf), 4) if buf[i] or buf[i + 1] or buf[i + 2])


def test_available_flag():
    assert isinstance(font.available(), bool)


def test_system_fonts_list():
    if not font.available():
        return
    assert isinstance(font.system_fonts(), list)


def test_metrics():
    stack = _stack()
    if stack is None:
        return
    assert stack.text_width("Hello", 20) > 0
    assert stack.text_width("中文测试", 20) > 0
    assert stack.line_height(20) > 0
    assert stack.ascent(20) > 0


def test_wrap_text():
    stack = _stack()
    if stack is None:
        return
    lines = stack.wrap_text("中文排版需要按字折行，英文按单词折行", 16, 120)
    assert len(lines) >= 2
    assert all(stack.text_width(line, 16) <= 130 for line in lines)


def test_draw_pixels():
    stack = _stack()
    if stack is None:
        return
    surface = V.Surface(220, 80, use_native=False)
    canvas = surface.canvas()
    canvas.fill_rect(0, 0, 220, 80, V.rgb(0, 0, 0))
    advance = stack.draw(canvas, "Ag中", 8, 8, V.rgb(255, 255, 255), 32)
    assert advance > 0
    assert _painted(canvas) > 20, "字体应该画出可见像素"


def test_install_and_uninstall():
    if not font.available() or font.default_font() is None:
        return
    try:
        assert font.install() is True
        assert V.active_font() is not None
        width = V.text_width("中文宽度", 20)
        assert width > 0
        surface = V.Surface(240, 80, use_native=False)
        canvas = surface.canvas()
        canvas.fill_rect(0, 0, 240, 80, V.rgb(0, 0, 0))
        canvas.text("中文", 10, 10, V.rgb(255, 255, 255), 28)
        assert _painted(canvas) > 10
    finally:
        font.uninstall()
    assert V.active_font() is None


def test_has_cjk():
    assert font.has_cjk("中文")
    assert not font.has_cjk("hello")


def test_font_stack_repr():
    stack = _stack()
    if stack is None:
        return
    assert "Font" in repr(stack) or "FontStack" in repr(stack)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    skipped = 0
    for fn in tests:
        fn()
        print(f"[ok] {fn.__name__}")
    print(f"\n{len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
