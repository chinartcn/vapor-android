"""Vapor OS MD3 组件库。四份执行文档各建一个 provider 模块，install() 统一装配。"""

from __future__ import annotations

from . import actions

__all__ = ["actions"]


def install(host=None):
    """把所有已落地的 provider 注册进宿主；host=None 时用全局 ui.register。"""
    from Vapor import component

    mods = [actions]
    # 其余三份由编排层在此追加：selection / navigation / feedback
    names = []
    for m in mods:
        if host is not None:
            names += component.register_provider(m)
        else:
            from Vapor.ui import register

            for cls in getattr(m, "COMPONENTS", ()):
                register(cls)
            names += [c.name for c in getattr(m, "COMPONENTS", ())]
    return names
