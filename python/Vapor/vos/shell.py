"""Vapor OS 根容器：状态栏 + 内容区 + 手势导航骨架。"""

from __future__ import annotations

from .. import core
from ..ui import Component
from ..theme import current_theme
from ..ui.renderer import queue_dirty

try:  # 文档 2（系统 Chrome）
    from .chrome import MdStatusBar as _MdStatusBar
except Exception:  # pragma: no cover - 文档 2 未合入时使用本地骨架
    _MdStatusBar = None

try:  # 文档 3（桌面/手势导航）
    from .launcher import MdGestureNav as _MdGestureNav
except Exception:  # pragma: no cover
    _MdGestureNav = None

try:  # 文档 1（中文装配）
    from ..vos_base.cjk import install_cjk
except Exception:  # pragma: no cover
    def install_cjk(prefer_latin: bool = False) -> bool:  # type: ignore
        return False

__all__ = ["VaporShell"]

_STATE_KEY = "vapor.vos.shell.state"
_cjk_ready = False


def _ensure_cjk() -> None:
    global _cjk_ready
    if _cjk_ready:
        return
    _cjk_ready = True
    try:
        install_cjk()
    except Exception:
        pass


class VaporShell(Component):
    """OS 外壳：Column[状态栏, 路由内容, 手势条]。"""

    name = "VaporShell"
    props = {
        "status_bar": True,
        "gesture_nav": True,
        "initial_route": "home",
        "routes": {},
        "on_route_change": None,
    }

    @staticmethod
    def setup(props, ctx):
        state = {"route": core.ref(str(props.get("initial_route") or "home")),
                 "navigate": None}
        core.provide(_STATE_KEY, state)
        ctx.on_mounted(lambda: _ensure_cjk())
        return {}

    @staticmethod
    def build(r, props, slots):
        state = core.inject(_STATE_KEY, None) or {
            "route": core.ref(str(props.get("initial_route") or "home")),
            "navigate": None,
        }

        root = r.element("view", {
            "dir": "column", "w": "100%", "h": "100%",
            "bg": current_theme().role("surface"),
        })
        r.bind_attr(root, "bg", lambda: current_theme().role("surface"))

        if props.get("status_bar", True):
            root.add(_status_bar(r))

        content = r.element("view", {"grow": 1, "w": "100%"})
        root.add(content)

        if props.get("gesture_nav", True):
            root.add(_gesture_nav(r))

        def open_route(route):
            state["route"].value = str(route)
            callback = props.get("on_route_change")
            if callable(callback):
                callback(str(route))

        state["navigate"] = open_route

        def render_route():
            route = state["route"].value
            for child in list(content.children):
                child.dispose()
            content.children.clear()
            entry = (props.get("routes") or {}).get(route)
            if entry is None:
                hint = r.text(f"未注册路由: {route}")
                r.bind_attr(hint, "color", lambda: current_theme().role("onSurfaceVariant"))
                content.add(hint)
                return
            node = _instantiate_route(r, entry, open_route)
            if node is not None:
                content.add(node)
                box = content.box
                if box.w > 0 and box.h > 0:
                    queue_dirty(box.x, box.y, box.w, box.h)

        core.effect(render_route)

        root.props["_state"] = state
        root.props["_content"] = content
        return [root]


def _instantiate_route(r, entry, on_open):
    if isinstance(entry, type):
        return entry.instantiate(r, {"on_open": on_open}, {})
    if callable(entry):
        try:
            value = entry({"on_open": on_open})
        except TypeError:
            value = entry()
        if isinstance(value, type):
            return value.instantiate(r, {"on_open": on_open}, {})
        if value is not None:
            return value
    return None


def _status_bar(r):
    if _MdStatusBar is not None:
        try:
            return _MdStatusBar.instantiate(r, {}, {})
        except Exception:
            pass
    bar = r.element("view", {
        "dir": "row", "align": "center", "justify": "space-between",
        "w": "100%", "h": 28, "pad": 12,
    })
    r.bind_attr(bar, "bg", lambda: current_theme().role("surfaceContainer"))
    left = r.text("Vapor OS")
    r.bind_attr(left, "size", lambda: 11)
    r.bind_attr(left, "color", lambda: current_theme().role("onSurfaceVariant"))
    right = r.text("100%")
    r.bind_attr(right, "size", lambda: 11)
    r.bind_attr(right, "color", lambda: current_theme().role("onSurfaceVariant"))
    bar.add(left)
    bar.add(right)
    return bar


def _gesture_nav(r):
    if _MdGestureNav is not None:
        try:
            return _MdGestureNav.instantiate(r, {}, {})
        except Exception:
            pass
    bar = r.element("view", {
        "dir": "row", "align": "center", "justify": "center",
        "w": "100%", "h": 24,
    })
    r.bind_attr(bar, "bg", lambda: current_theme().role("surface"))
    handle = r.element("view", {"w": 108, "h": 4, "radius": 2})
    r.bind_attr(handle, "bg", lambda: current_theme().role("outlineVariant"))
    bar.add(handle)
    return bar
