"""rust.Vapor.ui —— 基础 UI 组件（构建在共享内存帧缓冲之上）。

默认主界面由 Rust 绘制（IMPORT / FILES / SETTINGS），这里提供 Python 侧的
组件，逻辑包可用它搭自己的界面，或用 C/Python 扩展完全替换。
"""

from __future__ import annotations

from . import Vapor as V
from . import fs


def button(x: int, y: int, w: int, h: int, label: str,
           color: int = V.rgb(58, 123, 213), text_color: int = V.rgb(255, 255, 255),
           scale: int = 2) -> bool:
    """即时模式按钮：返回本次是否被按下（基于最近一次触摸抬起事件）。"""
    V.rect(x, y, w, h, color)
    V.text(label, x + 12, y + h // 2 - 4 * scale, text_color, scale)
    for e in _drain():
        if e.type == V.EVT_BUTTON and e.action == 1:  # up
            if x <= e.x <= x + w and y <= e.y <= y + h:
                return True
    return False


def panel(x: int, y: int, w: int, h: int,
          bg: int = V.rgb(42, 50, 60), border: int = V.rgb(90, 104, 120)) -> None:
    V.rect(x, y, w, h, bg)
    V.hline(x, y, w, border)
    V.hline(x, y + h - 1, w, border)
    V.vline(x, y, h, border)
    V.vline(x + w - 1, y, h, border)


def label(text: str, x: int, y: int, color: int = V.rgb(255, 255, 255), scale: int = 2) -> None:
    V.text(text, x, y, color, scale)


def _drain():
    out = []
    while True:
        e = V.poll_event()
        if e is None:
            break
        out.append(e)
    return out


def file_browser(path: str = "") -> list[str]:
    """简单文件列表（供 FILES 入口使用）。"""
    return [e["name"] for e in fs.list_dir(path)]


def home() -> None:
    """绘制默认主界面，返回被点击的入口名（'IMPORT'/'FILES'/'SETTINGS'/None）。"""
    w, h = V.size()
    V.clear(V.rgb(16, 20, 24))
    V.rect(0, 0, w, 72, V.rgb(32, 38, 46))
    V.text("VAPOR RENDERER", 24, 30, V.rgb(255, 255, 255), 3)
    cards = [("IMPORT", V.rgb(58, 123, 213)), ("FILES", V.rgb(46, 139, 87)),
             ("SETTINGS", V.rgb(139, 107, 46))]
    y = 112
    for name, color in cards:
        if button(24, y, w - 48, 96, name, color, scale=3):
            return name
        y += 120
    V.text("CPYTHON 3.15.2 / ABI 1", 24, h - 56, V.rgb(138, 151, 166), 2)
    return None
