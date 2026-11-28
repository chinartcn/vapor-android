#!/usr/bin/env python3
"""验证 C 扩展经 VaporApi 全功能表调用渲染器 + 符号解析（"啥都能调"）。

运行：
    VAPOR_NATIVE_LIB=/workspace/vapor/target/debug/libvapor.so \
    PYTHONPATH=/workspace/vapor/python python3 python/tests/test_ext.py
"""
from __future__ import annotations

import ctypes
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "python"))

import rust.Vapor as V
from rust import _abi

lib = _abi.lib()
EXT_SO = os.path.join(ROOT, "protocol", "example", "lib", "libhello_c.so")
if _abi.USING_FALLBACK or not os.path.exists(EXT_SO):
    pytest.skip(
        "test_ext 需要原生 libvapor.so 与已编译的 libhello_c.so",
        allow_module_level=True,
    )
V.init(app_dir=os.path.join(ROOT, "protocol", "example"), width=64, height=48)
V.clear(V.rgb(0, 0, 0))

so = EXT_SO.encode()
rc = lib.vapor_ext_load_c(so, b"vapor_ext_init")
assert rc == 0, f"C 扩展加载失败: {rc}"
print("  [ok] C 扩展加载成功")

fb = V.framebuffer()
i = (10 * 64 + 10) * 4
assert fb[i] == 255 and fb[i + 1] == 0 and fb[i + 2] == 255, \
    f"C 扩展写像素失败: {fb[i]},{fb[i+1]},{fb[i+2]}"
print("  [ok] C 扩展经函数表写共享内存像素 = 品红")

addr = lib.vapor_ext_symbol(b"vapor_c_add")
assert addr, "符号解析失败"
fn = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_int32, ctypes.c_int32)(addr)
assert fn(3, 4) == 7
print("  [ok] 扩展符号按名解析并调用: vapor_c_add(3,4)=7")

V.shutdown()
print("[PASS] C 扩展'啥都能调'验证通过 ✔")
