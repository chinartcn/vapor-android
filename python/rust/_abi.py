"""ctypes 绑定：把 include/vapor_abi.h 的稳定 C ABI 映射为 Python 类型。

加载顺序：先尝试环境变量 VAPOR_NATIVE_LIB，再尝试常见路径（同目录、
libvapor.so / libvapor.dll / libvapor.dylib）。找不到时进入“纯 Python 回退
模式”，用一块 bytes 模拟帧缓冲，便于在没有原生库的机器上开发与单元测试。
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import (
    POINTER,
    Structure,
    c_char,
    c_char_p,
    c_int32,
    c_int64,
    c_uint8,
    c_uint32,
    c_uint64,
    c_void_p,
    CFUNCTYPE,
)

# ---- 常量（与 vapor_abi.h 一致） ----
VAPOR_ABI_VERSION = 1
VAPOR_OK = 0
VAPOR_ERR = -1
VAPOR_ERR_ARG = -2
VAPOR_ERR_STATE = -3
VAPOR_ERR_NOT_FOUND = -4
VAPOR_ERR_PERMISSION = -5
VAPOR_NO_EVENT = 1
FMT_RGBA8888 = 0

LOG_VERBOSE, LOG_DEBUG, LOG_INFO, LOG_WARN, LOG_ERROR = 2, 3, 4, 5, 6


class VaporConfig(Structure):
    _fields_ = [
        ("app_dir", c_char_p),
        ("private_dir", c_char_p),
        ("external_dir", c_char_p),
        ("width", c_int32),
        ("height", c_int32),
        ("enable_python", c_int32),
    ]


class VaporFramebuffer(Structure):
    _fields_ = [
        ("pixels", POINTER(c_uint8)),
        ("width", c_int32),
        ("height", c_int32),
        ("stride", c_int32),
        ("format", c_int32),
        ("fd", c_int32),
        ("generation", c_uint64),
    ]


class VaporEvent(Structure):
    _fields_ = [
        ("type", c_int32),
        ("x", c_int32),
        ("y", c_int32),
        ("x2", c_int32),
        ("y2", c_int32),
        ("button", c_int32),
        ("action", c_int32),
        ("keycode", c_int32),
        ("timestamp_ms", c_uint64),
    ]


# C 扩展 init 函数签名: int (*)(VaporApi*)
EXT_INIT = CFUNCTYPE(c_int32, c_void_p)


class _Fallback:
    """无原生库时的内存回退，保持 API 形态一致，便于逻辑开发。"""

    def __init__(self) -> None:
        self._w, self._h = 720, 1280
        self._buf = bytearray(self._w * self._h * 4)
        self._generation = 0
        self._events: list[VaporEvent] = []
        self._paths = ("", "", "")

    def vapor_abi_version(self): return VAPOR_ABI_VERSION
    def vapor_version(self, a, b, c):
        a[0], b[0], c[0] = 1, 0, 0
    def vapor_cpython_version(self): return b"3.15.2"
    def vapor_init(self, cfg):
        self._paths = (cfg.app_dir or b"", cfg.private_dir or b"", cfg.external_dir or b"")
        return VAPOR_OK
    def vapor_shutdown(self): return VAPOR_OK
    def vapor_fb_resize(self, w, h):
        self._w, self._h = w, h
        self._buf = bytearray(w * h * 4)
        return VAPOR_OK
    def vapor_fb_lock(self, out):
        out.pixels = (c_uint8 * len(self._buf)).from_buffer(self._buf)
        out.width, out.height = self._w, self._h
        out.stride = self._w * 4
        out.format, out.fd, out.generation = FMT_RGBA8888, -1, self._generation
        return VAPOR_OK
    def vapor_fb_unlock(self):
        self._generation += 1
        return VAPOR_OK
    def vapor_fb_clear(self, rgba):
        r, g, b, a = (rgba >> 16) & 255, (rgba >> 8) & 255, rgba & 255, (rgba >> 24) & 255
        self._buf[:] = bytes((r, g, b, a)) * (self._w * self._h)
        return VAPOR_OK
    def vapor_poll_event(self, out):
        if self._events:
            out[0] = self._events.pop(0)
            return VAPOR_OK
        return VAPOR_NO_EVENT
    def vapor_event_count(self): return len(self._events)
    def vapor_set_title(self, title): return VAPOR_OK
    def vapor_set_bg_color(self, rgba): return VAPOR_OK
    def vapor_fs_app_dir(self): return self._paths[0]
    def vapor_fs_private_dir(self): return self._paths[1]
    def vapor_fs_external_dir(self): return self._paths[2]
    # v1.2：没有 Activity / 权限系统
    def vapor_activity(self): return 0
    def vapor_permission_poll(self, code): return -1


def _candidate_paths() -> list[str]:
    env = os.environ.get("VAPOR_NATIVE_LIB")
    paths = []
    if env:
        paths.append(env)
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("libvapor.so", "libvapor.dll", "libvapor.dylib"):
        paths.append(os.path.join(here, name))
        paths.append(os.path.join(here, "..", name))
    paths += ["libvapor.so", "/data/local/tmp/libvapor.so"]
    return paths


_LIB = None
USING_FALLBACK = False


def _load():
    global _LIB, USING_FALLBACK
    if _LIB is not None:
        return _LIB
    for p in _candidate_paths():
        if not os.path.exists(p):
            continue
        try:
            lib = ctypes.CDLL(p)
            _bind(lib)
            _LIB = lib
            return lib
        except OSError as exc:  # pragma: no cover
            print(f"[rust.Vapor] 加载 {p} 失败: {exc}", file=sys.stderr)
    USING_FALLBACK = True
    _LIB = _Fallback()
    return _LIB


def _bind(lib: ctypes.CDLL) -> None:
    """按头文件顺序绑定所有导出函数（设置 argtypes/restype 保证指针宽度正确）。"""

    def bind(name, restype, argtypes):
        fn = getattr(lib, name)
        fn.restype = restype
        fn.argtypes = argtypes
        return fn

    bind("vapor_abi_version", c_int32, [])
    bind("vapor_version", None, [POINTER(c_int32)] * 3)
    bind("vapor_cpython_version", c_char_p, [])
    bind("vapor_init", c_int32, [POINTER(VaporConfig)])
    bind("vapor_shutdown", c_int32, [])
    bind("vapor_fb_lock", c_int32, [POINTER(VaporFramebuffer)])
    bind("vapor_fb_unlock", c_int32, [])
    bind("vapor_fb_resize", c_int32, [c_int32, c_int32])
    bind("vapor_fb_clear", c_int32, [c_uint32])
    bind("vapor_poll_event", c_int32, [POINTER(VaporEvent)])
    bind("vapor_event_count", c_int32, [])
    bind("vapor_set_title", c_int32, [c_char_p])
    bind("vapor_set_bg_color", c_int32, [c_uint32])
    bind("vapor_fs_read", c_int32, [c_char_p, POINTER(POINTER(c_uint8)), POINTER(c_int32)])
    bind("vapor_fs_write", c_int32, [c_char_p, POINTER(c_uint8), c_int32])
    bind("vapor_fs_exists", c_int32, [c_char_p])
    bind("vapor_fs_list", c_int32, [c_char_p, c_char_p, c_int32])
    bind("vapor_fs_free", None, [POINTER(c_uint8)])
    bind("vapor_fs_app_dir", c_char_p, [])
    bind("vapor_fs_private_dir", c_char_p, [])
    bind("vapor_fs_external_dir", c_char_p, [])
    bind("vapor_py_init", c_int32, [c_char_p])
    bind("vapor_py_run_file", c_int32, [c_char_p])
    bind("vapor_py_eval", c_int32, [c_char_p])
    bind("vapor_py_install_requirements", c_int32, [c_char_p])
    bind("vapor_ext_load", c_int32, [c_char_p])
    bind("vapor_ext_load_c", c_int32, [c_char_p, c_char_p])
    bind("vapor_ext_load_py", c_int32, [c_char_p])
    bind("vapor_ext_symbol", c_void_p, [c_char_p])
    bind("vapor_log", None, [c_int32, c_char_p])
    bind("vapor_api", c_void_p, [])
    # JNI 桥接
    bind("vapor_jni_set_vm", c_int32, [c_void_p])
    bind("vapor_jni_get_vm", c_void_p, [])
    bind("vapor_jni_get_env", c_void_p, [])
    bind("vapor_jni_detach", c_int32, [])
    bind("vapor_jni_call", c_int32, [c_char_p, c_char_p, c_int32])
    bind("vapor_jni_release", c_int32, [c_int64])
    # v1.2 追加（可选）：Activity 句柄 + 运行时权限结果。
    # 老版本 libvapor 没有这些符号，用 hasattr 兼容而不是直接崩。
    for _name, _restype, _argtypes in (
        ("vapor_activity", c_int64, []),
        ("vapor_permission_poll", c_int32, [c_int32]),
    ):
        if hasattr(lib, _name):
            bind(_name, _restype, _argtypes)


def lib():
    """返回已绑定的原生库对象（原生不可用时返回内存回退对象）。"""
    return _load()
