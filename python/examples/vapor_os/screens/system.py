"""Vapor OS 演示 · 系统交互场景（执行文档 3）。

下拉通知中心（ShadeScreen）、锁屏（LockScreenScene）、最近任务（RecentsScreen）
三张场景，外加共用的手势接线层（SystemGestures）。复用已落地的 ``vos.chrome``
与 ``vos.launcher`` 组件，所有场景切换只发回调，不在本文件内导航。
"""

from __future__ import annotations

from Vapor.theme import current_theme
from Vapor.ui import with_alpha
from Vapor.ui.a11y import set_a11y
from Vapor.ui.renderer import Component, queue_dirty
from Vapor.vos.launcher import MdGestureNav, MdLockScreen, MdRecents
from Vapor.vos.chrome import (
    MdNotificationCenter,
    MdQuickSettings,
    MdStatusBar,
)

__all__ = [
    "ShadeScreen", "LockScreenScene", "RecentsScreen", "SystemGestures",
    "SCENES",
]

_SLOP = 8.0


def _num(value, default):
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _as_list(source):
    try:
        if source is None:
            return []
        return list(source)
    except TypeError:
        return [source]


def _call(props, key, *args):
    callback = props.get(key)
    if callable(callback):
        callback(*args)


class SystemGestures(Component):
    """手势接线层：把 MdGestureNav 的三回调转发到调用方 props。"""

    name = "SystemGestures"
    props = {"on_back": None, "on_home": None, "on_recents": None}

    @staticmethod
    def build(r, props, slots):
        def forward(key):
            def handler(ev):
                _call(props, key, ev)
            return handler

        node = MdGestureNav.instantiate(r, {
            "on_back": forward("on_back"),
            "on_home": forward("on_home"),
            "on_recents": forward("on_recents"),
        }, {})
        return [node]


# ---------------------------------------------------------------------------
# 3.1 Shade：下拉通知中心 + Quick Settings
# ---------------------------------------------------------------------------


class ShadeScreen(Component):
    """下拉通知中心：状态栏 + QS + 亮度 + 通知列表 + 手势条。"""

    name = "ShadeScreen"
    props = {
        "notifications": [], "tiles": [], "expanded": False, "brightness": None,
        "on_dismiss": None, "on_tile": None, "on_close": None, "on_open": None,
        "on_home": None, "on_back": None, "on_recents": None,
        "height": 800,
    }

    @staticmethod
    def build(r, props, slots):
        state = {
            "pull": 0.0,
            "expanded": bool(props.get("expanded")),
            "closed": False,
        }

        root = r.element("view", {"dir": "column", "w": "100%", "gap": 8,
                                  "padding": [8, 8]})
        r.bind_attr(root, "h", lambda: _num(props.get("height"), 800))
        r.bind_attr(root, "bg", lambda: current_theme().role("surface"))

        def paint(canvas, box):
            # scrim：随下拉进度加深，主题切换后自动更新。
            alpha = 0.2 * max(0.0, min(1.0, state["pull"]))
            if alpha > 0 and box.w > 0 and box.h > 0:
                canvas.fill_rect(box.x, box.y, box.w, box.h,
                                 with_alpha(current_theme().role("onSurface"), alpha))

        root.props["paint"] = paint

        # 顶部状态栏。
        root.add(MdStatusBar.instantiate(r, {}, {}))

        # Quick Settings。
        root.add(MdQuickSettings.instantiate(r, {
            "tiles": _as_list(props.get("tiles")),
            "expanded": state["expanded"],
            "brightness": props.get("brightness"),
        }, {}))

        # 通知列表：为每条通知注入 on_dismiss（回调只传 id）。
        notes = []
        for item in _as_list(props.get("notifications")):
            data = dict(item) if hasattr(item, "items") else {"title": str(item)}
            nid = data.get("id", data.get("title", ""))

            def make_dismiss(nid=nid):
                def handler(_ev):
                    _call(props, "on_dismiss", nid)
                return handler

            data["on_dismiss"] = make_dismiss()
            notes.append(data)

        root.add(MdNotificationCenter.instantiate(r, {
            "notifications": notes,
            "on_clear": lambda _ev: _call(props, "on_close"),
        }, {}))

        # 共用底部手势条。
        root.add(SystemGestures.instantiate(r, {
            "on_back": lambda ev: _call(props, "on_back", ev),
            "on_home": lambda ev: _call(props, "on_home", ev),
            "on_recents": lambda ev: _call(props, "on_recents", ev),
        }, {}))

        threshold = _num(props.get("height"), 800) * 0.15

        def apply_pull():
            root.props["y"] = state["pull"]
            box = getattr(root, "box", None)
            if box is not None and box.w > 0:
                queue_dirty(box.x, box.y, box.w, box.h)

        def on_drag(ev):
            state["pull"] = max(0.0, state["pull"] + getattr(ev, "dy", 0.0))
            apply_pull()

        def on_dragend(ev):
            if state["pull"] > threshold:
                state["expanded"] = True
                _call(props, "on_open", ev)
            else:
                state["pull"] = 0.0
            apply_pull()

        r.on(root, "drag", lambda: on_drag)
        r.on(root, "dragend", lambda: on_dragend)

        root.extra["state"] = state
        root.extra["threshold"] = threshold
        root.extra["notifications"] = props.get("notifications")
        set_a11y(root, role="shade", label="通知中心")
        return [root]


# ---------------------------------------------------------------------------
# 3.2 锁屏场景
# ---------------------------------------------------------------------------


class LockScreenScene(Component):
    """锁屏场景：直接嵌 MdLockScreen，解锁只发 on_unlock。"""

    name = "LockScreenScene"
    props = {
        "time": "", "date": "", "notifications": [], "locked": True,
        "on_unlock": None,
    }

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {"dir": "column", "w": "100%", "h": "100%"})
        r.bind_attr(root, "bg", lambda: current_theme().role("surface"))

        lock = MdLockScreen.instantiate(r, {
            "time": props.get("time", ""),
            "date": props.get("date", ""),
            "notifications": _as_list(props.get("notifications")),
            "locked": props.get("locked", True),
            "on_unlock": lambda ev: _call(props, "on_unlock", ev),
        }, {})
        root.add(lock)

        root.extra["lock"] = lock
        set_a11y(root, role="lockScreen", label=str(props.get("time", "")))
        return [root]


# ---------------------------------------------------------------------------
# 3.3 最近任务场景
# ---------------------------------------------------------------------------


class RecentsScreen(Component):
    """最近任务场景：直接嵌 MdRecents，选中只发 on_select(id)。"""

    name = "RecentsScreen"
    props = {"tasks": [], "on_clear": None, "on_select": None, "on_dismiss": None}

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {"dir": "column", "w": "100%", "h": "100%"})
        r.bind_attr(root, "bg", lambda: current_theme().role("surface"))

        tasks = []
        for item in _as_list(props.get("tasks")):
            data = dict(item) if hasattr(item, "items") else {"title": str(item)}
            tid = data.get("id", data.get("title", ""))

            def make_select(tid=tid):
                def handler(_ev):
                    _call(props, "on_select", tid)
                return handler

            def make_dismiss(tid=tid):
                def handler(_ev):
                    _call(props, "on_dismiss", tid)
                return handler

            data["on_select"] = make_select()
            data["on_dismiss"] = make_dismiss()
            tasks.append(data)

        recents = MdRecents.instantiate(r, {
            "tasks": tasks,
            "on_clear": lambda ev: _call(props, "on_clear", ev),
        }, {})
        root.add(recents)

        root.extra["recents"] = recents
        set_a11y(root, role="recents", label="最近任务")
        return [root]


SCENES = [ShadeScreen, LockScreenScene, RecentsScreen]
