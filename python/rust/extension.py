"""rust.Vapor.ext —— C/Python ABI 扩展加载与管理。

扩展清单 ``lib/abi.json``::

    {
      "schema": 1,
      "extensions": [
        {"name": "mylib", "kind": "c", "symbol": "vapor_ext_init", "arch": "all"},
        {"name": "myui", "kind": "python", "path": "myui.py"}
      ]
    }

C 扩展导出 ``int vapor_ext_init(VaporApi* api)``，拿到整张函数表后即可调用
渲染器的全部能力。Python 扩展是普通模块，import 后可用 ``rust.Vapor`` 做任意事。
"""

from __future__ import annotations

import ctypes
import json
import os

from . import _abi, fs
from ._abi import lib, VAPOR_OK


def load_manifest(path: str = "abi.json") -> int:
    """读取并应用 lib/abi.json。返回已启用扩展数量；-1 表示清单缺失。"""
    try:
        text = fs.read_text(path)
    except FileNotFoundError:
        return -1
    manifest = json.loads(text)
    enabled = [e for e in manifest.get("extensions", []) if e.get("enabled", True)]
    rc = lib().vapor_ext_load(text.encode())
    if rc != VAPOR_OK:
        # 回退：逐个加载（原生库不可用时用 Python 侧模拟）
        for e in enabled:
            try:
                load_python(e["name"]) if e.get("kind") in ("python", "py") else load_c(e["name"], e.get("symbol"))
            except Exception:
                pass
    return len(enabled)


def load_c(name_or_path: str, symbol: str = "vapor_ext_init") -> int:
    path = name_or_path if name_or_path.endswith(".so") else f"lib{name_or_path}.so"
    if not os.path.isabs(path):
        path = os.path.join(fs.app_dir() or ".", "lib", path)
    return lib().vapor_ext_load_c(path.encode(), symbol.encode())


def load_python(module: str) -> int:
    return lib().vapor_ext_load_py(module.encode())


def symbol(name: str) -> int:
    """按名字取进程内任意导出符号地址（"啥都能调"的兜底通道）。"""
    return lib().vapor_ext_symbol(name.encode())


def call_symbol(name: str, *args, restype=ctypes.c_int, argtypes=None):
    """取符号并按给定签名调用。高级用法，供 C 扩展互操作。"""
    addr = symbol(name)
    if not addr:
        raise AttributeError(name)
    fn = ctypes.CFUNCTYPE(restype, *(argtypes or []))(addr)
    return fn(*args)


def write_manifest(extensions: list[dict], path: str = "abi.json") -> int:
    data = {"schema": 1, "extensions": extensions}
    return fs.write(path, json.dumps(data, ensure_ascii=False, indent=2))
