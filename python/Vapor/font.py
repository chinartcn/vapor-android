"""Vapor.font —— 字体扩展：TTF / OTF / TTC 真实字体渲染（含中文）。

默认文本走 rust.Vapor 的 5×7 点阵（只有 ASCII）。装上真实字体后：

  * 布局测量改用字体真实 advance（中文/英文等宽比例都对）；
  * 绘制改为逐字形 α 混合光栅化（由 Rust 的 Vapor._ui 完成）；
  * FontStack 支持按字符回退（中文用 CJK 字体、拉丁用拉丁字体）。

用法::

    from Vapor import font
    font.install()                      # 自动找系统字体（Android / 桌面）

    font.install(font.load("/sdcard/my.ttf"))
    font.install(font.FontStack([font.load(cjk_path), font.load(latin_path)]))

    font.uninstall()                    # 回到点阵字体
    font.available()                    # 原生字体模块是否可用

环境变量：
    VAPOR_FONT      指定字体文件（可写 "path:index" 选择 TTC 内的 face）
    VAPOR_FONT_DIR  额外的字体搜索目录（会扫描 *.ttf / *.otf / *.ttc）
"""

from __future__ import annotations

import os
import sys

from . import _native

_ui = _native.ui

__all__ = [
    "FontUnavailable", "Font", "FontStack", "available",
    "load", "load_bytes", "system_fonts", "default_font",
    "install", "uninstall", "active", "has_cjk", "SYSTEM_FONT_CANDIDATES",
]


class FontUnavailable(RuntimeError):
    """没有可用的字体光栅化后端（原生 Vapor._ui 未安装）或没有找到字体文件。"""


def available() -> bool:
    """字体扩展是否可用（需要 Rust 版 Vapor._ui 提供 Font / FontStack）。"""
    return hasattr(_ui, "Font") and hasattr(_ui, "FontStack")


def has_cjk(text: str) -> bool:
    """文本里是否含 CJK 字符（用于提示"需要中文字体"）。"""
    return any(ord(ch) >= 0x2E80 for ch in str(text))


# ---------------------------------------------------------------------------
# 系统字体候选（按优先级：CJK 在前，保证中文优先命中）
# ---------------------------------------------------------------------------

SYSTEM_FONT_CANDIDATES = [
    # Android
    "/system/fonts/NotoSansCJK-Regular.ttc",
    "/system/fonts/NotoSansSC-Regular.otf",
    "/system/fonts/NotoSansHans-Regular.otf",
    "/system/fonts/NotoSerifCJK-Regular.ttc",
    "/system/fonts/DroidSansFallback.ttf",
    "/system/fonts/DroidSans.ttf",
    "/system/fonts/Roboto-Regular.ttf",
    # Linux（CI / 桌面）
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    # Windows
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\arial.ttf",
]

_CACHE: dict = {}
_STACK_CACHE = {"stack": None, "failed": False}


def _require():
    if not available():
        raise FontUnavailable(
            "字体扩展需要 Rust 版 Vapor._ui（maturin 构建）；当前是纯 Python 回退"
        )


# ---------------------------------------------------------------------------
# Font / FontStack
# ---------------------------------------------------------------------------

class Font:
    """单个字体面。path 仅用于展示；TTC 用 collection_index 选具体 face。"""

    __slots__ = ("raw", "path", "collection_index")

    def __init__(self, data=None, path=None, collection_index=0, _raw=None):
        _require()
        if _raw is not None:
            self.raw = _raw
            self.path = path or "<bytes>"
            self.collection_index = collection_index
            return
        if path is not None:
            self.raw = _ui.Font.from_path(str(path), int(collection_index))
            self.path = str(path)
        elif data is not None:
            self.raw = _ui.Font(bytes(data), int(collection_index))
            self.path = "<bytes>"
        else:
            raise ValueError("Font 需要 path 或 data")
        self.collection_index = int(collection_index)

    # -- 度量 --

    def text_width(self, text, size):
        return self.raw.text_width(str(text), float(size))

    def line_height(self, size):
        return self.raw.line_height(float(size))

    def ascent(self, size):
        return self.raw.ascent(float(size))

    def descent(self, size):
        return self.raw.descent(float(size))

    def wrap_text(self, text, size, max_width):
        return list(self.raw.wrap_text(str(text), float(size), float(max_width)))

    # -- 字形 --

    def has_glyph(self, ch) -> bool:
        return bool(self.raw.has_glyph(str(ch)[:1]))

    def coverage(self, text) -> float:
        return float(self.raw.coverage(str(text)))

    # -- 绘制 --

    def draw(self, canvas, text, x, y, color, size):
        """把一行文本画进 Canvas；y 是行顶部。返回推进宽度。"""
        return self.raw.draw(canvas.buffer, canvas.width, canvas.height,
                             str(text), float(x), float(y), float(size), int(color))

    def __repr__(self):
        return f"Font({self.path}#{self.collection_index})"


class FontStack:
    """多个字体按字符回退：每个字符挑第一个有字形的字体。"""

    __slots__ = ("fonts", "raw")

    def __init__(self, fonts):
        _require()
        self.fonts = [f for f in fonts if isinstance(f, Font)]
        if not self.fonts:
            raise ValueError("FontStack 至少需要一个 Font")
        self.raw = _ui.FontStack([f.raw for f in self.fonts])

    def text_width(self, text, size):
        return self.raw.text_width(str(text), float(size))

    def line_height(self, size):
        return self.raw.line_height(float(size))

    def ascent(self, size):
        return self.raw.ascent(float(size))

    def wrap_text(self, text, size, max_width):
        return list(self.raw.wrap_text(str(text), float(size), float(max_width)))

    def has_glyph(self, ch) -> bool:
        return bool(self.raw.has_glyph(str(ch)[:1]))

    def coverage(self, text) -> float:
        return float(self.raw.coverage(str(text)))

    def draw(self, canvas, text, x, y, color, size):
        return self.raw.draw(canvas.buffer, canvas.width, canvas.height,
                             str(text), float(x), float(y), float(size), int(color))

    def __len__(self):
        return len(self.fonts)

    def __repr__(self):
        return "FontStack(" + ", ".join(f.path for f in self.fonts) + ")"


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------

def _split_path(spec: str):
    """支持 "path:index" 形式指定 TTC face（Windows 盘符不会被误切）。"""
    head, sep, tail = str(spec).rpartition(":")
    if sep and tail.isdigit():
        return head, int(tail)
    return str(spec), 0


def load(path, collection_index=0) -> Font:
    path, index = _split_path(str(path))
    key = ("path", path, int(collection_index or index))
    if key not in _CACHE:
        _CACHE[key] = Font(path=path, collection_index=int(collection_index or index))
    return _CACHE[key]


def load_bytes(data, collection_index=0) -> Font:
    return Font(data=data, collection_index=collection_index)


def _extra_candidates():
    out = []
    env = os.environ.get("VAPOR_FONT")
    if env:
        out.append(env)
    font_dir = os.environ.get("VAPOR_FONT_DIR")
    if font_dir and os.path.isdir(font_dir):
        for name in sorted(os.listdir(font_dir)):
            if name.lower().endswith((".ttf", ".otf", ".ttc")):
                out.append(os.path.join(font_dir, name))
    return out


def system_fonts(limit=3) -> list:
    """按优先级加载存在的系统字体（最多 limit 个）。"""
    if not available():
        return []
    found = []
    for spec in _extra_candidates() + SYSTEM_FONT_CANDIDATES:
        if len(found) >= limit:
            break
        path, index = _split_path(spec)
        if not os.path.isfile(path):
            continue
        try:
            found.append(load(path, index))
        except Exception as exc:  # 字体损坏 / 不支持的格式
            print(f"[Vapor.font] 跳过 {path}: {exc}", file=sys.stderr)
    return found


def default_font():
    """系统字体组成的 FontStack（找不到返回 None，结果会缓存）。"""
    if _STACK_CACHE["stack"] is not None:
        return _STACK_CACHE["stack"]
    if _STACK_CACHE["failed"] or not available():
        return None
    fonts = system_fonts()
    if not fonts:
        _STACK_CACHE["failed"] = True
        return None
    stack = fonts[0] if len(fonts) == 1 else FontStack(fonts)
    _STACK_CACHE["stack"] = stack
    return stack


# ---------------------------------------------------------------------------
# 安装到 UI
# ---------------------------------------------------------------------------

def install(font=None) -> bool:
    """把字体设为全局文本渲染器（测量 + 绘制）。找不到字体返回 False。"""
    from . import ui

    if font is None:
        font = default_font()
    if font is None:
        return False

    def render(canvas, text, x, y, color, size):
        return font.draw(canvas, text, x, y, color, size)

    ui.set_text_renderer(render)
    ui.set_active_font(font)
    _STATE["font"] = font
    return True


def uninstall() -> None:
    """恢复内置点阵字体。"""
    from . import ui

    ui.set_text_renderer(None)
    ui.set_active_font(None)
    _STATE["font"] = None


def active():
    return _STATE["font"]


_STATE = {"font": None}
