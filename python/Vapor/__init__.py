"""Vapor —— 无 vdom 的响应式 UI 框架（Vue 3 Vapor 风格）。

    import Vapor.ui as V
    from Vapor.core import ref

框架分三层：
    Vapor.core      响应式原语（ref / reactive / computed / watch / effect scope）
    Vapor.template  模板编译器（v-if / v-for / v-model / 插槽 / 插值 → 直接建节点）
    Vapor.ui        组件与渲染（Canvas / 布局 / 事件 / 页面栈 / SVG 组件）
    Vapor.sdk       通过稳定 C ABI 调 JNI 的 Android SDK 封装
    Vapor.font      字体扩展：TTF/OTF/TTC 真实字形（含中文），按字符回退

底层绘制来自 rust.Vapor（共享内存帧缓冲），细粒度更新来自 Rust 的
Vapor._core（alien-signals 风格依赖图），几何计算来自 Vapor._ui。
"""

from __future__ import annotations

__version__ = "2.0.0"
__all__ = ["core", "template", "ui", "sdk", "svg", "font", "component", "presets", "native", "__version__"]

_LAZY = {"core", "template", "ui", "sdk", "svg", "font", "component", "presets", "native"}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        mod = importlib.import_module(f".{name}", __name__)
        globals()[name] = mod
        return mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def status() -> dict:
    """报告原生扩展 / 回退状态。"""
    from . import _native

    return _native.status()
