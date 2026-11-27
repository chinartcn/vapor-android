"""Vapor Python 运行时命名空间。

内置安装到 CPython site-packages 后：:

    import rust.Vapor as V

本包是渲染器逻辑的唯一官方入口。它封装 Rust 导出的稳定 C ABI（共享内存
帧缓冲、文件系统、扩展加载、pip），并提供一套纯 Python 的基础渲染 API，
使得新增功能只需写一个扩展模块，无需改动 Rust 侧。
"""

__version__ = "1.0.0"
CPYTHON_TARGET = "3.15.2"
ABI_VERSION = 1

try:
    from . import Vapor  # noqa: F401  —— 方便 `from rust import Vapor`
except Exception:  # pragma: no cover —— 在未加载原生库的环境下也允许 import rust
    pass

__all__ = ["Vapor", "__version__", "CPYTHON_TARGET", "ABI_VERSION"]
