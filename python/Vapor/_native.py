"""原生扩展加载：优先 maturin 打包的 Rust 模块，缺失时回退纯 Python。

三个原生模块分别由三个 maturin crate 提供：
    vapor-reactive -> Vapor._core   响应式内核 + 光栅化
    vapor-ui       -> Vapor._ui      SVG path / 矩阵 / 文本度量
    vapor-sdk      -> Vapor._sdk     dlopen 稳定 C ABI，JNI 桥接

设置环境变量 VAPOR_STRICT_NATIVE=1 可禁用回退（生产环境推荐）。
"""

from __future__ import annotations

import importlib
import os

STRICT = os.environ.get("VAPOR_STRICT_NATIVE", "").strip() not in ("", "0", "false", "False")


def _load(name: str, fallback: str | None):
    try:
        mod = importlib.import_module(f".{name}", __package__)
        mod.FALLBACK = False
        return mod
    except ImportError as exc:
        if STRICT or fallback is None:
            raise
        mod = importlib.import_module(f".{fallback}", __package__)
        mod.FALLBACK = True
        mod.IMPORT_ERROR = exc
        return mod


core = _load("_core", "_fallback")
ui = _load("_ui", "_fallback_ui")

try:
    sdk = importlib.import_module("._sdk", __package__)
    sdk.FALLBACK = False
except ImportError:
    sdk = None


def status() -> dict:
    return {
        "core": "rust" if not getattr(core, "FALLBACK", False) else "python-fallback",
        "ui": "rust" if not getattr(ui, "FALLBACK", False) else "python-fallback",
        "sdk": ("rust" if sdk is not None else "missing"),
        "strict": STRICT,
    }


def require_native() -> None:
    """确保三个原生模块都可用（Android 部署时调用）。"""
    problems = []
    if getattr(core, "FALLBACK", False):
        problems.append("Vapor._core")
    if getattr(ui, "FALLBACK", False):
        problems.append("Vapor._ui")
    if sdk is None:
        problems.append("Vapor._sdk")
    if problems:
        raise RuntimeError(
            "缺少原生扩展: " + ", ".join(problems) + "；请用 maturin 构建并安装（见 docs/REACTIVE.md）"
        )
