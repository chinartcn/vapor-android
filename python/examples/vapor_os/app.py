"""Vapor OS 演示的装配入口（执行文档 1）。

``build_app()`` 是其余文档/脚本唯一入口：按「主题 → 中文 → provider → routes →
Shell → mount」的顺序组装出可运行 App。所有对已落地能力的调用均为只读 import，
本文件不修改任何 ``Vapor/*`` 库文件。
"""

from __future__ import annotations

import os

__all__ = ["build_app", "install_all"]

_READY = False


def _env_bool(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return value in ("1", "true", "yes", "on")


def _env_int(name: str, default=None):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _install_cjk() -> bool:
    """安装中文字体；无字体环境下返回 False，属正常。"""
    try:
        from Vapor.vos_base import cjk

        return bool(cjk.install_cjk())
    except Exception:
        return False


def install_all() -> None:
    """注册 md3（24）与 vos（15）全部 provider；进程内幂等。"""
    global _READY
    if _READY:
        return
    from Vapor import md3, vos

    for fn in (md3.install, vos.install):
        try:
            fn()
        except Exception:
            # 组件名已注册（如其他用例先行装配）时忽略，保证演示仍可启动。
            pass
    _READY = True


def _resolve_theme(seed, dark: bool):
    from Vapor.theme import Theme, baseline_dark, baseline_light, seed_to_scheme

    if seed is not None:
        value = int(seed, 0) if isinstance(seed, str) else int(seed)
        return seed_to_scheme(value, dark=bool(dark))
    return Theme(baseline_dark() if dark else baseline_light())


def _shell_class(routes: dict, initial_route: str):
    """动态子类化 VaporShell，把 routes 作为 props 默认值注入。

    ``App.mount()`` 固定以空 props 实例化根组件，因此 routes 必须走类默认值。
    """
    from Vapor.vos.shell import VaporShell

    props = dict(VaporShell.props)
    props["routes"] = routes
    props["initial_route"] = initial_route
    # 渲染器只认类自身 __dict__ 里的 build/setup（不向上继承），故显式带上。
    return type("OSShell", (VaporShell,), {
        "name": "OSShell",
        "props": props,
        "build": VaporShell.build,
        "setup": VaporShell.setup,
    })


def _default_routes():
    """延迟导入文档 4 的默认数据；未落地时返回空字典（Shell 显示提示，不崩）。"""
    try:
        from . import data  # type: ignore

        routes = getattr(data, "ROUTES", None) or getattr(data, "routes", None)
        return dict(routes or {})
    except Exception:
        return {}


def build_app(width=None, height=None, *, seed=None, dark=None,
              routes=None, use_native=True):
    """装配并返回已 mount 的 App。"""
    if width is None:
        width = _env_int("VAPOR_OS_W")
    if height is None:
        height = _env_int("VAPOR_OS_H")
    if dark is None:
        dark = _env_bool("VAPOR_OS_DARK")
    if seed is None:
        seed = os.environ.get("VAPOR_OS_SEED", "").strip() or None

    # 1. 主题先行：组件 build 时即从 current_theme() 取色。
    from Vapor.theme import set_theme

    set_theme(_resolve_theme(seed, dark))

    # 2. 中文装配（失败可退回点阵字体）。
    _install_cjk()

    # 3. provider 装配。
    install_all()

    # 4. routes 准备（延迟 import 文档 4）。
    if routes is None:
        routes = _default_routes()
    routes = dict(routes or {})
    initial_route = "home" if not routes or "home" in routes else next(iter(routes))

    # 5. 挂载 Shell（routes 走类默认值注入）。
    shell_cls = _shell_class(routes, initial_route)
    from Vapor.ui import create_app

    app = create_app(
        shell_cls, width=width, height=height,
        use_native=use_native, dirty_rects=True,
    )
    app.mount()
    return app
