"""rust.Vapor —— Vapor 渲染器的官方 Python 基础渲染 API。

设计目标：所有功能都通过共享内存帧缓冲 + 稳定 C ABI 完成，Python 直接写像素，
零拷贝到 Rust/GLES 上屏。新增能力只需写一个 ``lib/`` 下的 C/Python 扩展。

典型用法（逻辑包 main.py）::

    import rust.Vapor as V
    V.init()
    V.clear(V.rgb(16, 20, 24))
    V.rect(20, 20, 200, 80, V.rgb(58, 123, 213))
    V.text("HELLO", 30, 50, V.rgb(255, 255, 255), scale=3)
    V.present()
"""

from __future__ import annotations

import json
import os
import struct
import sys
import time
from typing import Iterable

from . import _abi
from ._abi import (
    VAPOR_OK,
    VAPOR_ERR,
    FMT_RGBA8888,
    VaporConfig,
    VaporFramebuffer,
    VaporEvent,
)

# 事件类型常量（与头文件一致）
EVT_NONE, EVT_MOTION, EVT_BUTTON, EVT_KEY = 0, 1, 2, 3
EVT_RESIZE, EVT_RESUME, EVT_PAUSE, EVT_DESTROY = 4, 5, 6, 7

_initialized = False
_fb: VaporFramebuffer | None = None
_view: memoryview | None = None


# ---------------------------------------------------------------------------
# 颜色与生命周期
# ---------------------------------------------------------------------------

def rgb(r: int, g: int, b: int, a: int = 255) -> int:
    """打包为 0xAARRGGBB，与 CSS / Android Color.argb() 一致。"""
    return ((a & 255) << 24) | ((r & 255) << 16) | ((g & 255) << 8) | (b & 255)


def abi_version() -> int:
    return _abi.lib().vapor_abi_version()


def cpython_version() -> str:
    v = _abi.lib().vapor_cpython_version()
    return v.decode() if isinstance(v, bytes) else str(v)


def init(app_dir: str = "", private_dir: str = "", external_dir: str = "",
         width: int = 0, height: int = 0, enable_python: bool = False) -> int:
    """初始化渲染器。逻辑包通常无需显式调用——宿主已初始化；此处供独立运行/测试。"""
    global _initialized
    cfg = VaporConfig(
        app_dir=app_dir.encode() if app_dir else None,
        private_dir=private_dir.encode() if private_dir else None,
        external_dir=external_dir.encode() if external_dir else None,
        width=width,
        height=height,
        enable_python=1 if enable_python else 0,
    )
    rc = _abi.lib().vapor_init(_abi.byref(cfg) if hasattr(_abi, "byref") else _ctypes_byref(cfg))
    _initialized = rc == VAPOR_OK
    return rc


def _ctypes_byref(x):
    import ctypes
    return ctypes.byref(x)


def shutdown() -> int:
    global _initialized, _fb, _view
    _view = None
    _fb = None
    _initialized = False
    return _abi.lib().vapor_shutdown()


def size() -> tuple[int, int]:
    fb = _lock()
    return fb.width, fb.height


def resize(width: int, height: int) -> int:
    global _fb, _view
    _view = None
    _fb = None
    return _abi.lib().vapor_fb_resize(width, height)


# ---------------------------------------------------------------------------
# 共享内存帧缓冲
# ---------------------------------------------------------------------------

def _lock() -> VaporFramebuffer:
    global _fb, _view
    fb = VaporFramebuffer()
    rc = _abi.lib().vapor_fb_lock(_abi.byref(fb) if hasattr(_abi, "byref") else _ctypes_byref(fb))
    if rc != VAPOR_OK:
        raise RuntimeError(f"vapor_fb_lock 失败: {rc}")
    _fb = fb
    n = fb.width * fb.height * 4
    # 把原生指针 cast 成定长数组再取 memoryview：零拷贝读写。
    PtrArray = __import__("ctypes").c_uint8 * n
    arr = __import__("ctypes").cast(fb.pixels, __import__("ctypes").POINTER(PtrArray)).contents
    _view = memoryview(arr).cast("B")
    return fb


def framebuffer() -> memoryview:
    """返回共享内存帧缓冲的 memoryview（RGBA8888），可直接切片写入像素。"""
    fb = _lock()
    return _view


def present() -> int:
    """提交当前帧：Rust 上传纹理并用 GLES 绘制一帧。"""
    return _abi.lib().vapor_fb_unlock()


def clear(color: int) -> None:
    _abi.lib().vapor_fb_clear(color & 0xFFFFFFFF)


def pixel(x: int, y: int, color: int) -> None:
    fb = _fb or _lock()
    if x < 0 or y < 0 or x >= fb.width or y >= fb.height:
        return
    i = (y * fb.width + x) * 4
    v = _view if _view is not None else framebuffer()
    v[i] = (color >> 16) & 255
    v[i + 1] = (color >> 8) & 255
    v[i + 2] = color & 255
    v[i + 3] = (color >> 24) & 255


def rect(x: int, y: int, w: int, h: int, color: int) -> None:
    fb = _fb or _lock()
    v = _view if _view is not None else framebuffer()
    width, height = fb.width, fb.height
    r, g, b, a = (color >> 16) & 255, (color >> 8) & 255, color & 255, (color >> 24) & 255
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + w), min(height, y + h)
    row = bytes((r, g, b, a))
    for yy in range(y0, y1):
        start = (yy * width + x0) * 4
        v[start:start + (x1 - x0) * 4] = row * (x1 - x0)


def hline(x: int, y: int, w: int, color: int) -> None:
    rect(x, y, w, 1, color)


def vline(x: int, y: int, h: int, color: int) -> None:
    rect(x, y, 1, h, color)


def line(x0: int, y0: int, x1: int, y1: int, color: int) -> None:
    """Bresenham 直线。"""
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    while True:
        pixel(x0, y0, color)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x0 += sx
        if e2 < dx:
            err += dx
            y0 += sy


# ---------------------------------------------------------------------------
# 圆（纯整数 Bresenham 中点算法：不依赖 math / 浮点，arm32 也能用）
# ---------------------------------------------------------------------------

def circle(cx: int, cy: int, r: int, color: int) -> None:
    """画空心圆轮廓（圆心 cx,cy，半径 r）。纯整数算法，无 math 依赖。"""
    fb = _fb or _lock()
    x, y, d = r, 0, 1 - r
    while x >= y:
        for px, py in ((x, y), (y, x), (-x, y), (-y, x),
                       (x, -y), (y, -x), (-x, -y), (-y, -x)):
            pixel(cx + px, cy + py, color)
        y += 1
        if d < 0:
            d += 2 * y + 1
        else:
            x -= 1
            d += 2 * (y - x) + 1


def disc(cx: int, cy: int, r: int, color: int) -> None:
    """画实心圆（圆心 cx,cy，半径 r）。纯整数扫描线填充，无 math 依赖。"""
    fb = _fb or _lock()
    x, y, d = r, 0, 1 - r
    while x >= y:
        rect(cx - x, cy + y, 2 * x + 1, 1, color)
        rect(cx - x, cy - y, 2 * x + 1, 1, color)
        rect(cx - y, cy + x, 2 * y + 1, 1, color)
        rect(cx - y, cy - x, 2 * y + 1, 1, color)
        y += 1
        if d < 0:
            d += 2 * y + 1
        else:
            x -= 1
            d += 2 * (y - x) + 1


def square_outline(x: int, y: int, s: int, color: int, thickness: int = 1) -> None:
    """画空心正方形（左上角 x,y，边长 s，线宽 thickness）。"""
    rect(x, y, s, thickness, color)
    rect(x, y + s - thickness, s, thickness, color)
    rect(x, y, thickness, s, color)
    rect(x + s - thickness, y, thickness, s, color)


# ---------------------------------------------------------------------------
# 基础文本（内置 5x7 点阵，无字体依赖；扩展可覆盖）
# ---------------------------------------------------------------------------

_FONT: dict[str, tuple[int, ...]] = {
    "A": (0x0E,0x11,0x11,0x1F,0x11,0x11,0x11), "B": (0x1E,0x11,0x11,0x1E,0x11,0x11,0x1E),
    "C": (0x0E,0x11,0x10,0x10,0x10,0x11,0x0E), "D": (0x1E,0x11,0x11,0x11,0x11,0x11,0x1E),
    "E": (0x1F,0x10,0x10,0x1E,0x10,0x10,0x1F), "F": (0x1F,0x10,0x10,0x1E,0x10,0x10,0x10),
    "G": (0x0E,0x11,0x10,0x17,0x11,0x11,0x0F), "H": (0x11,0x11,0x11,0x1F,0x11,0x11,0x11),
    "I": (0x0E,0x04,0x04,0x04,0x04,0x04,0x0E), "J": (0x07,0x02,0x02,0x02,0x02,0x12,0x0C),
    "K": (0x11,0x12,0x14,0x18,0x14,0x12,0x11), "L": (0x10,0x10,0x10,0x10,0x10,0x10,0x1F),
    "M": (0x11,0x1B,0x15,0x15,0x11,0x11,0x11), "N": (0x11,0x19,0x15,0x13,0x11,0x11,0x11),
    "O": (0x0E,0x11,0x11,0x11,0x11,0x11,0x0E), "P": (0x1E,0x11,0x11,0x1E,0x10,0x10,0x10),
    "Q": (0x0E,0x11,0x11,0x11,0x15,0x12,0x0D), "R": (0x1E,0x11,0x11,0x1E,0x14,0x12,0x11),
    "S": (0x0F,0x10,0x10,0x0E,0x01,0x01,0x1E), "T": (0x1F,0x04,0x04,0x04,0x04,0x04,0x04),
    "U": (0x11,0x11,0x11,0x11,0x11,0x11,0x0E), "V": (0x11,0x11,0x11,0x11,0x11,0x0A,0x04),
    "W": (0x11,0x11,0x11,0x15,0x15,0x1B,0x11), "X": (0x11,0x11,0x0A,0x04,0x0A,0x11,0x11),
    "Y": (0x11,0x11,0x0A,0x04,0x04,0x04,0x04), "Z": (0x1F,0x01,0x02,0x04,0x08,0x10,0x1F),
    "0": (0x0E,0x11,0x13,0x15,0x19,0x11,0x0E), "1": (0x04,0x0C,0x04,0x04,0x04,0x04,0x0E),
    "2": (0x0E,0x11,0x01,0x02,0x04,0x08,0x1F), "3": (0x1F,0x02,0x04,0x0E,0x01,0x11,0x0E),
    "4": (0x02,0x06,0x0A,0x12,0x1F,0x02,0x02), "5": (0x1F,0x10,0x1E,0x01,0x01,0x11,0x0E),
    "6": (0x06,0x08,0x10,0x1E,0x11,0x11,0x0E), "7": (0x1F,0x01,0x02,0x04,0x08,0x08,0x08),
    "8": (0x0E,0x11,0x11,0x0E,0x11,0x11,0x0E), "9": (0x0E,0x11,0x11,0x0F,0x01,0x02,0x0C),
    ".": (0,0,0,0,0,0x0C,0x0C), "-": (0,0,0,0x1F,0,0,0), " ": (0,0,0,0,0,0,0),
}


def text(s: str, x: int, y: int, color: int, scale: int = 1) -> None:
    cx = x
    for ch in s.upper():
        rows = _FONT.get(ch, _FONT[" "])
        for ry, row in enumerate(rows):
            for bit in range(5):
                if (row >> (4 - bit)) & 1:
                    rect(cx + bit * scale, y + ry * scale, scale, scale, color)
        cx += 6 * scale


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------

def poll_event() -> VaporEvent | None:
    e = VaporEvent()
    rc = _abi.lib().vapor_poll_event(_ctypes_byref(e))
    return e if rc == VAPOR_OK else None


def event_count() -> int:
    return _abi.lib().vapor_event_count()


# ---------------------------------------------------------------------------
# 完整触控数据（给需要自定义手势/调试的开发者；Vapor.ui 内部也用它）
# ---------------------------------------------------------------------------

# 事件类型（与 include/vapor_abi.h 一致；EVT_WHEEL 为 v1.2 追加，不影响旧结构布局）
EVT_WHEEL = 8

# 指针动作
ACTION_DOWN, ACTION_UP, ACTION_MOVE, ACTION_CANCEL = 0, 1, 2, 3


class TouchEvent:
    """一条归一化后的输入事件（触摸 / 按键 / 滚轮）。"""

    __slots__ = (
        "kind", "action", "x", "y", "x2", "y2",
        "pointer_id", "pointer_count", "keycode", "timestamp_ms", "raw",
    )

    def __init__(self, kind, action=0, x=0.0, y=0.0, x2=0.0, y2=0.0,
                 pointer_id=0, pointer_count=1, keycode=0, timestamp_ms=0, raw=None):
        self.kind = kind              # "down" / "up" / "move" / "cancel" / "key" / "wheel"
        self.action = action          # 原始 action 值
        self.x = x
        self.y = y
        self.x2 = x2                  # 第二根手指（单指时等于 x / y）
        self.y2 = y2
        self.pointer_id = pointer_id
        self.pointer_count = pointer_count
        self.keycode = keycode
        self.timestamp_ms = timestamp_ms
        self.raw = raw

    # ---- 便捷判断 ----

    @property
    def is_down(self):
        return self.kind == "down"

    @property
    def is_up(self):
        return self.kind == "up"

    @property
    def is_move(self):
        return self.kind == "move"

    @property
    def is_cancel(self):
        return self.kind == "cancel"

    @property
    def is_multi_touch(self):
        return self.pointer_count > 1

    def distance_to(self, other) -> float:
        dx = other.x - self.x
        dy = other.y - self.y
        return (dx * dx + dy * dy) ** 0.5

    def spread(self) -> float:
        """两指之间的距离（单指为 0），用于捏合缩放。"""
        if self.pointer_count < 2:
            return 0.0
        dx = self.x - self.x2
        dy = self.y - self.y2
        return (dx * dx + dy * dy) ** 0.5

    @property
    def center(self):
        if self.pointer_count < 2:
            return (self.x, self.y)
        return ((self.x + self.x2) / 2, (self.y + self.y2) / 2)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__ if k != "raw"}

    def __repr__(self):
        extra = f" ptr={self.pointer_id}/{self.pointer_count}" if self.pointer_count > 1 else ""
        return f"TouchEvent({self.kind} {self.x:.0f},{self.y:.0f}{extra})"


def _normalize_event(raw) -> TouchEvent | None:
    """把 ABI 的 VaporEvent 归一化成 TouchEvent。"""
    etype = getattr(raw, "type", 0)
    action = getattr(raw, "action", 0)
    keycode = getattr(raw, "keycode", 0)
    ts = getattr(raw, "timestamp_ms", 0)
    if etype == EVT_KEY:
        return TouchEvent("key", action=action, keycode=keycode, timestamp_ms=ts, raw=raw)
    if etype == EVT_WHEEL:
        return TouchEvent("wheel", action=action, x=getattr(raw, "x", 0),
                          y=getattr(raw, "y", 0), timestamp_ms=ts, raw=raw)
    if etype == EVT_BUTTON:
        if action == ACTION_DOWN:
            kind = "down"
        elif action == ACTION_UP:
            kind = "up"
        elif action == ACTION_CANCEL:
            kind = "cancel"
        elif action == 4:          # 追加：第二根手指按下（ACTION_POINTER_DOWN）
            kind = "pointerdown"
        elif action == 5:          # 追加：第二根手指抬起（ACTION_POINTER_UP）
            kind = "pointerup"
        else:
            return None
        count = keycode or 1
    elif etype == EVT_MOTION:
        kind = "move"
        count = keycode or 1
    else:
        return None
    return TouchEvent(
        kind,
        action=action,
        x=getattr(raw, "x", 0),
        y=getattr(raw, "y", 0),
        x2=getattr(raw, "x2", 0),
        y2=getattr(raw, "y2", 0),
        pointer_id=getattr(raw, "button", 0),
        pointer_count=count,
        keycode=keycode,
        timestamp_ms=ts,
        raw=raw,
    )


def poll_touch() -> TouchEvent | None:
    """取一条归一化的输入事件（触摸/按键/滚轮）；没有则返回 None。

    与 poll_event() 的区别：poll_event 返回 ABI 原始结构，poll_touch 返回带
    kind / pointer_id / pointer_count / spread() / center 的友好对象。
    两者共享同一个事件队列，**不要在同一帧里混用**（会互相抢事件）。
    """
    raw = poll_event()
    if raw is None:
        return None
    return _normalize_event(raw)


def drain_touches(limit: int = 256) -> list:
    """一次取空事件队列（最多 limit 条），适合自己写手势识别器。"""
    out = []
    for _ in range(limit):
        ev = poll_touch()
        if ev is None:
            break
        out.append(ev)
    return out


def touch_stream(limit: int = 256):
    """事件生成器：while True 里没有事件时返回 None。"""
    while True:
        ev = poll_touch()
        if ev is None:
            return
        yield ev


# ---------------------------------------------------------------------------
# 运行时权限（声明在 AndroidManifest，真正用到时才申请）
# ---------------------------------------------------------------------------

_PERMISSION_CODE = [1000]


def activity() -> int:
    """当前 Activity 的 JNI 句柄（0 表示非 Android / 未就绪）。"""
    fn = getattr(_abi.lib(), "vapor_activity", None)
    return int(fn()) if fn else 0


def poll_permission(code: int) -> int:
    """权限申请结果：-1 等待中，0 拒绝，1 授予（取走后清空）。"""
    fn = getattr(_abi.lib(), "vapor_permission_poll", None)
    return int(fn(int(code))) if fn else -1


def request_permission(permission: str, timeout: float = 30.0) -> bool:
    """申请一个运行时权限（会弹系统对话框，阻塞等待用户操作）。

    权限必须先在 AndroidManifest 里声明；正常权限（VIBRATE 等）安装即授予，
    这里会立刻返回 True。非 Android 环境返回 False。
    """
    if _abi.USING_FALLBACK:
        return False
    act = activity()
    if not act:
        return False
    from . import jni

    _PERMISSION_CODE[0] += 1
    code = _PERMISSION_CODE[0]
    jni.call(
        int(act),
        "requestPermissionFromNative",
        "(Ljava/lang/String;I)V",
        [jni.string(permission), jni.i(code)],
        ret="v",
    )
    deadline = time.monotonic() + max(0.1, float(timeout))
    while time.monotonic() < deadline:
        result = poll_permission(code)
        if result >= 0:
            return bool(result)
        time.sleep(0.05)
    return False


def set_title(title: str) -> None:
    _abi.lib().vapor_set_title(title.encode())


# ---------------------------------------------------------------------------
# 文件系统 / 扩展 / pip（转发到子模块）
# ---------------------------------------------------------------------------

from . import fs as fs            # noqa: E402
from . import extension as ext    # noqa: E402
from . import piputil as pip      # noqa: E402
from . import ui as ui            # noqa: E402
from . import jni                 # noqa: E402  —— JNI 桥接：C/Python 扩展调任意 JNI

__all__ = [
    "rgb", "abi_version", "cpython_version", "init", "shutdown", "size", "resize",
    "framebuffer", "present", "clear", "pixel", "rect", "hline", "vline", "line",
    "circle", "disc", "square_outline",
    "text", "poll_event", "poll_touch", "drain_touches", "touch_stream",
    "TouchEvent", "event_count", "set_title", "activity", "request_permission",
    "poll_permission", "fs", "ext", "pip", "ui", "jni",
    "EVT_NONE", "EVT_MOTION", "EVT_BUTTON", "EVT_KEY", "EVT_RESIZE",
    "EVT_RESUME", "EVT_PAUSE", "EVT_DESTROY", "EVT_WHEEL",
    "ACTION_DOWN", "ACTION_UP", "ACTION_MOVE", "ACTION_CANCEL",
]
