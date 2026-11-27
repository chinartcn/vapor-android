#!/usr/bin/env python3
"""端到端验证：Python 侧 rust.Vapor 通过真实 libvapor.so 调用 C ABI。

运行：
    VAPOR_NATIVE_LIB=/workspace/vapor/target/debug/libvapor.so \
    PYTHONPATH=/workspace/vapor/python python3 tests/test_e2e.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rust
import rust.Vapor as V
from rust import _abi


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  [ok] {msg}")


def main():
    print("== 1. import rust.Vapor ==")
    check(rust.__version__ == "1.0.0", "rust 包版本")
    check(V.abi_version() == 1, f"ABI 版本 = {V.abi_version()}")
    check(V.cpython_version() == "3.15.2", f"CPython 目标版本 = {V.cpython_version()}")
    check(not _abi.USING_FALLBACK, "使用真实原生库（非回退模式）")

    print("== 2. 生命周期 + 共享内存帧缓冲 ==")
    with tempfile.TemporaryDirectory() as d:
        rc = V.init(app_dir=d, private_dir=d, width=64, height=48)
        check(rc == 0, f"vapor_init 返回 {rc}")
        w, h = V.size()
        check((w, h) == (64, 48), f"帧缓冲尺寸 {w}x{h}")

        V.clear(V.rgb(16, 20, 24))
        V.rect(5, 5, 10, 8, V.rgb(255, 0, 0))
        V.pixel(20, 20, V.rgb(0, 255, 0))
        V.line(0, 0, 30, 30, V.rgb(0, 0, 255))
        V.present()
        check(True, "清屏/矩形/像素/直线/present 全部调用成功")

        fb = V.framebuffer()
        # 检查 (12,6) 位置的红色像素（矩形内部，且不被对角线覆盖）
        idx = (6 * 64 + 12) * 4
        r, g, b, a = fb[idx], fb[idx + 1], fb[idx + 2], fb[idx + 3]
        check((r, g, b) == (255, 0, 0), f"共享内存像素 (12,6) = RGB({r},{g},{b})")

        print("== 3. 文件系统 ==")
        V.fs.write("test.txt", "hello vapor")
        check(V.fs.exists("test.txt"), "文件写入后存在")
        check(V.fs.read_text("test.txt") == "hello vapor", "读回内容一致")

    print("== 4. 扩展/事件/pip API 存在性 ==")
    for mod, name in [(V, "ext"), (V, "pip"), (V, "ui"), (V, "fs")]:
        check(hasattr(mod, name), f"Vapor.{name} 可用")
    check(callable(V.ext.load_manifest), "ext.load_manifest 可调用")
    check(callable(V.pip.install), "pip.install 可调用")
    check(V.event_count() >= 0, "event_count 可调用")

    V.shutdown()
    print("\n[PASS] 端到端验证全部通过 ✔")


if __name__ == "__main__":
    main()
