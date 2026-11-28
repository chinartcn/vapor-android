"""Vapor OS 系统组件与应用统一装配入口。"""

from __future__ import annotations

from importlib import import_module

from .. import component as _comp
from . import apps, shell

__all__ = ["apps", "shell", "install"]

_OPTIONAL = ("chrome", "launcher")
_MODULES = []
for _name in _OPTIONAL:
    try:
        _MODULES.append(import_module(f"{__name__}.{_name}"))
    except Exception:  # 文档 2/3 尚未合入
        continue
__all__.extend(m.__name__.rsplit(".", 1)[-1] for m in _MODULES)


def install(host=None):
    """把已落地的 provider 注册进宿主；host=None 用全局注册。"""
    mods = list(_MODULES) + [apps]
    names = []
    for module in mods:
        if host is not None:
            names += _comp.register_provider(module)
            continue
        from Vapor.ui import register

        for cls in getattr(module, "COMPONENTS", ()):
            register(cls)
        names += [c.name for c in getattr(module, "COMPONENTS", ())]
    return names
