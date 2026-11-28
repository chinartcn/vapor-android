"""MD3 反馈与叠层类内建组件（MdDialog / MdSnackbar / MdBadge /
MdProgressIndicator / MdTooltip / MdBottomSheet）。

显隐完全由 props（``open`` / ``visible``）驱动，不引入全局弹窗单例；
颜色一律走 ``current_theme().role(...)``，不写死颜色字面量。
"""

from __future__ import annotations

import math
import time

from .. import core
from ..theme import current_theme
from ..ui import Component
from ..ui.renderer import queue_dirty

__all__ = [
    "MdDialog",
    "MdSnackbar",
    "MdBadge",
    "MdProgressIndicator",
    "MdTooltip",
    "MdBottomSheet",
    "COMPONENTS",
    "manifest",
    "register",
    "get_a11y",
]

_SCRIMMED_BLACK = 0xFF << 24


# ---------------------------------------------------------------------------
# 基础层兜底
# ---------------------------------------------------------------------------

try:
    from ..theme import get_shape as _theme_get_shape
except ImportError:  # pragma: no cover
    _theme_get_shape = None

try:
    from ..theme import get_type_style as _theme_get_type_style
except ImportError:  # pragma: no cover
    _theme_get_type_style = None

try:
    from ..ui.motion import reduced_motion as _reduced_motion
    from ..ui.motion import spring as _spring
except ImportError:  # pragma: no cover
    _reduced_motion = None
    _spring = None

try:
    from ..ui.a11y import Role as A11yRole
    from ..ui.a11y import get_a11y as _real_get_a11y
    from ..ui.a11y import set_a11y as _real_set_a11y
except ImportError:  # pragma: no cover
    class A11yRole:  # 与文档 3 的 Role 成员同名
        BUTTON = "button"
        TEXT = "text"
        NAVIGATION = "navigation"

    _real_set_a11y = None
    _real_get_a11y = None


def theme_color(name: str) -> int:
    return current_theme().role(name)


def _role_or(name: str, *fallbacks: str) -> int:
    """取角色；权威方案缺省 add-on 角色时按顺序回退。"""
    theme = current_theme()
    for candidate in (name,) + fallbacks:
        try:
            return theme.role(candidate)
        except KeyError:
            continue
    return theme.role("onSurface")


def _with_alpha(color: int, opacity: float) -> int:
    factor = max(0.0, min(1.0, float(opacity)))
    return (int(((color >> 24) & 0xFF) * factor) << 24) | (color & 0x00FFFFFF)


def _scrim_color() -> int:
    theme = current_theme()
    try:
        return theme.with_opacity("scrim", 0.32)
    except KeyError:
        return _with_alpha(_SCRIMMED_BLACK, 0.32)


def _shape(name: str, default: float) -> float:
    if _theme_get_shape is None:
        return default
    shape = _theme_get_shape(name)
    if isinstance(shape, (int, float)):
        return float(shape)
    return float(getattr(shape, "radius", default))


def _type_size(name: str, default: float) -> float:
    if _theme_get_type_style is None:
        return default
    style = _theme_get_type_style(name)
    if isinstance(style, dict):
        return float(style.get("size", style.get("fontSize", default)))
    return float(getattr(style, "size", getattr(style, "fontSize", default)))


def reduced_motion() -> bool:
    return bool(_reduced_motion()) if _reduced_motion is not None else False


def get_a11y(node):
    if _real_get_a11y is not None:
        return _real_get_a11y(node)
    return node.extra.get("a11y")


def _set_a11y(node, **info):
    if _real_set_a11y is not None:
        _real_set_a11y(node, **info)
    else:
        node.extra["a11y"] = dict(info)


def _register_tick(fn):
    """注册逐帧回调；无 App（纯组件测试）时静默跳过。"""
    try:
        from ..ui.app import on_tick
        on_tick(fn)
        return True
    except Exception:
        return False


_now = time.monotonic
_STATE_KEY = "vapor.md3.feedback.state"


def _fade_in(*nodes):
    """接入既有帧循环做淡入；无 App / 减少动效时保持终态。"""
    if reduced_motion() or _spring is None:
        return
    try:
        from ..ui.app import App
        app = App.current
        if app is None:
            return
        easing = _spring("standard", "fast", "effects")

        def frame(t):
            for node in nodes:
                node.props["opacity"] = t
                box = node.box
                if box.w > 0 and box.h > 0:
                    queue_dirty(box.x, box.y, box.w, box.h)

        app.animate(0.18, frame, easing=easing)
    except Exception:  # 动画是增强项，任何异常都不影响静态渲染
        pass


def _bind_open_animation(props, key, *nodes):
    last = {"open": None}

    def sync():
        opened = bool(props.get(key))
        if opened and last["open"] is not True:
            _fade_in(*nodes)
        elif not opened:
            for node in nodes:
                node.props["opacity"] = 1
        last["open"] = opened

    core.effect(sync)


# ---------------------------------------------------------------------------
# MdDialog
# ---------------------------------------------------------------------------

class MdDialog(Component):
    """对话框：open 驱动 scrim + 居中面板，动作通过 on_action 回写。"""

    name = "MdDialog"
    props = {
        "open": False,
        "title": "",
        "text": "",
        "actions": [],
        "dismissible": True,
        "on_action": None,
        "on_dismiss": None,
    }

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {
            "w": "100%", "h": "100%",
            "dir": "column", "justify": "center", "align": "center",
        })
        r.bind_show(root, lambda: bool(props.get("open")))

        scrim = r.element("view", {
            "absolute": True, "x": 0, "y": 0, "w": "100%", "h": "100%",
            "bg": _scrim_color(),
        })
        r.bind_attr(scrim, "bg", _scrim_color)
        r.on(scrim, "click", lambda: (lambda ev: _dismiss_dialog(props)))

        panel = r.element("view", {
            "w": 280, "min_h": 120, "pad": 24, "radius": _shape("extraLarge", 28),
            "dir": "column", "gap": 16,
            "bg": theme_color("surfaceContainerHigh"),
        })
        r.bind_attr(panel, "bg", lambda: theme_color("surfaceContainerHigh"))

        title = r.text("")
        r.bind_text(title, lambda: str(props.get("title") or ""))
        r.bind_attr(title, "size", lambda: _type_size("headlineSmall", 24))
        r.bind_attr(title, "color", lambda: theme_color("onSurface"))
        r.bind_show(title, lambda: bool(props.get("title")))
        panel.add(title)

        body = r.text("")
        r.bind_text(body, lambda: str(props.get("text") or ""))
        r.bind_attr(body, "size", lambda: _type_size("bodyMedium", 14))
        r.bind_attr(body, "color", lambda: theme_color("onSurfaceVariant"))
        r.bind_show(body, lambda: bool(props.get("text")))
        panel.add(body)

        actions = r.element("view", {"dir": "row", "justify": "end", "gap": 8})
        for index, action in enumerate(props.get("actions") or []):
            label = str(action.get("label", ""))
            value = action.get("value")
            primary = action.get("kind") in ("primary", "filled") or index == 0
            button = r.text(label)
            r.bind_attr(button, "size", lambda: _type_size("labelLarge", 14))
            r.bind_attr(button, "color", lambda p=primary: theme_color("primary")
                        if p else theme_color("onSurface"))
            r.on(button, "click", lambda v=value: (lambda ev: _dialog_action(props, v)))
            actions.add(button)
        panel.add(actions)

        root.add(scrim)
        root.add(panel)
        root.props["_scrim"] = scrim
        root.props["_panel"] = panel

        _bind_open_animation(props, "open", scrim, panel)

        _set_a11y(root, role=A11yRole.NAVIGATION,
                  label=str(props.get("title") or ""), expanded=bool(props.get("open")))
        return [root]


def _dismiss_dialog(props):
    if props.get("dismissible"):
        cb = props.get("on_dismiss")
        if callable(cb):
            cb()


def _dialog_action(props, value):
    cb = props.get("on_action")
    if callable(cb):
        cb(value)


# ---------------------------------------------------------------------------
# MdSnackbar
# ---------------------------------------------------------------------------

class MdSnackbar(Component):
    """提示条：visible 驱动，duration 后自动 on_dismiss，卸载自动停表。"""

    name = "MdSnackbar"
    props = {
        "message": "",
        "visible": False,
        "action_label": None,
        "duration": 4000,
        "on_action": None,
        "on_dismiss": None,
    }

    @staticmethod
    def setup(props, ctx):
        state = {"alive": True, "start": None, "registered": False, "tick": None}

        def tick():
            if not state["alive"] or not props.get("visible"):
                return
            start = state.get("start")
            if start is None:
                return
            duration = float(props.get("duration") or 0) / 1000.0
            if duration > 0 and _now() - start >= duration:
                state["start"] = None
                cb = props.get("on_dismiss")
                if callable(cb):
                    cb()

        state["tick"] = tick
        core.provide(_STATE_KEY, state)
        ctx.on_cleanup(lambda: state.update(alive=False))
        return {}

    @staticmethod
    def build(r, props, slots):
        state = core.inject(_STATE_KEY, None) or {"tick": None, "alive": False}

        root = r.element("view", {
            "absolute": True, "left": 16, "right": 16, "bottom": 16, "h": 48,
            "pad": 12, "radius": _shape("small", 8),
            "dir": "row", "align": "center", "gap": 8,
            "bg": _role_or("inverseSurface", "onSurface"),
        })
        r.bind_show(root, lambda: bool(props.get("visible")))
        r.bind_attr(root, "bg", lambda: _role_or("inverseSurface", "onSurface"))

        message = r.text("")
        r.bind_text(message, lambda: str(props.get("message") or ""))
        r.bind_attr(message, "size", lambda: _type_size("bodyMedium", 14))
        r.bind_attr(message, "color", lambda: _role_or("inverseOnSurface", "surface"))
        root.add(message)

        action = r.text("")
        r.bind_text(action, lambda: str(props.get("action_label") or ""))
        r.bind_attr(action, "size", lambda: _type_size("labelLarge", 14))
        r.bind_attr(action, "color", lambda: _role_or("inversePrimary", "primary"))
        r.bind_show(action, lambda: bool(props.get("action_label")))
        r.on(action, "click", lambda: (lambda ev: _snackbar_action(props)))
        root.add(action)

        def arm():
            if props.get("visible"):
                if state.get("start") is None:
                    state["start"] = _now()
                if not state.get("registered"):
                    state["registered"] = True
                    _register_tick(state.get("tick") or (lambda: None))
            else:
                state["start"] = None

        core.effect(arm)
        root.props["_state"] = state
        root.props["_action"] = action
        root.props["_message"] = message

        _bind_open_animation(props, "visible", root)

        _set_a11y(root, role=A11yRole.TEXT, label=str(props.get("message") or ""))
        return [root]


def _snackbar_action(props):
    cb = props.get("on_action")
    if callable(cb):
        cb()


# ---------------------------------------------------------------------------
# MdBadge
# ---------------------------------------------------------------------------

class MdBadge(Component):
    """角标：数字 / 点状，超过 max 显示 "max+"。"""

    name = "MdBadge"
    props = {
        "value": None,
        "max": 99,
        "visible": True,
        "color": None,
        "text_color": None,
    }

    @staticmethod
    def build(r, props, slots):
        dot_mode = lambda: props.get("value") is None
        root = r.element("view", {
            "w": 6, "h": 6, "radius": 3,
            "dir": "row", "align": "center", "justify": "center",
        })
        r.bind_show(root, lambda: bool(props.get("visible")))
        r.bind_attr(root, "w", lambda: 6 if dot_mode() else "auto")
        r.bind_attr(root, "h", lambda: 6 if dot_mode() else 16)
        r.bind_attr(root, "radius", lambda: 3 if dot_mode() else 8)
        r.bind_attr(root, "pad", lambda: 0 if dot_mode() else 4)
        r.bind_attr(root, "bg", lambda: props.get("color")
                    if props.get("color") is not None else theme_color("error"))

        text = r.text("")
        r.bind_text(text, lambda: _badge_text(props))
        r.bind_attr(text, "size", lambda: _type_size("labelSmall", 11))
        r.bind_attr(text, "color", lambda: props.get("text_color")
                    if props.get("text_color") is not None else theme_color("onError"))
        r.bind_show(text, lambda: not dot_mode())
        root.add(text)

        root.props["_text"] = text

        _set_a11y(root, role=A11yRole.TEXT, label=_badge_text(props),
                  hidden=not bool(props.get("visible")))
        return [root]


def _badge_text(props) -> str:
    value = props.get("value")
    if value is None:
        return ""
    maximum = props.get("max")
    try:
        maximum = int(maximum)
    except (TypeError, ValueError):
        maximum = 99
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 0
    return f"{maximum}+" if number > maximum else str(number)


# ---------------------------------------------------------------------------
# MdProgressIndicator
# ---------------------------------------------------------------------------

class MdProgressIndicator(Component):
    """进度指示器：linear / circular，确定与不确定两态。"""

    name = "MdProgressIndicator"
    props = {
        "kind": "linear",
        "determinate": True,
        "value": 0.0,
        "color": None,
        "track": None,
        "size": 48,
    }

    @staticmethod
    def setup(props, ctx):
        state = {"alive": True, "phase": core.ref(0.0), "registered": False}
        core.provide(_STATE_KEY, state)
        ctx.on_cleanup(lambda: state.update(alive=False))
        return {}

    @staticmethod
    def build(r, props, slots):
        state = core.inject(_STATE_KEY, None)
        if state is None:  # 理论不可达；防御性兜底
            state = {"alive": False, "phase": core.ref(0.0), "registered": False}

        circular = props.get("kind") == "circular"
        determinate = bool(props.get("determinate"))
        accent = lambda: props.get("color") if props.get("color") is not None else theme_color("primary")
        track_color = lambda: props.get("track") if props.get("track") is not None else theme_color("surfaceContainerHighest")

        if circular:
            root = r.element("view", {
                "w": float(props.get("size") or 48),
                "h": float(props.get("size") or 48),
            })
            r.bind_attr(root, "w", lambda: float(props.get("size") or 48))
            r.bind_attr(root, "h", lambda: float(props.get("size") or 48))

            def paint(canvas, box):
                _paint_circular(canvas, box, props, state, determinate, accent(), track_color())

            root.props["paint"] = paint
        else:
            root = r.element("view", {"w": "100%", "h": 4})
            track = r.element("view", {
                "absolute": True, "x": 0, "y": 0, "w": "100%", "h": 4,
                "radius": 2, "bg": track_color(),
            })
            r.bind_attr(track, "bg", track_color)
            fill = r.element("view", {
                "absolute": True, "x": 0, "y": 0, "h": 4, "radius": 2,
                "bg": accent(),
            })
            r.bind_attr(fill, "bg", accent)
            r.bind_attr(fill, "x", lambda: "0.00%" if (determinate or reduced_motion())
                        else f"{state['phase'].value * 100:.2f}%")
            r.bind_attr(fill, "w", lambda: f"{_progress_ratio(props) * 100:.2f}%"
                        if determinate else "33.33%")
            root.add(track)
            root.add(fill)
            root.props["_fill"] = fill
            root.props["_track"] = track

        if not determinate and not reduced_motion():
            phase = state["phase"]

            def tick():
                if not state.get("alive"):
                    return
                phase.value = (phase.value + 0.02) % 1.0
                box = root.box
                if box.w > 0 and box.h > 0:
                    queue_dirty(box.x, box.y, box.w, box.h)

            if not state.get("registered"):
                state["registered"] = True
                _register_tick(tick)

        root.props["_state"] = state

        _set_a11y(root, role=A11yRole.TEXT,
                  label=f"progress {int(_progress_ratio(props) * 100)}%",
                  busy=not determinate, expanded=False)
        return [root]


def _progress_ratio(props) -> float:
    value = props.get("value")
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0
    return max(0.0, min(1.0, value))


def _paint_circular(canvas, box, props, state, determinate, accent, track_color):
    cx = box.x + box.w / 2.0
    cy = box.y + box.h / 2.0
    radius = max(2.0, min(box.w, box.h) / 2.0 - 2.0)
    canvas.ring(int(cx), int(cy), int(radius), track_color, 3)
    if determinate:
        ratio = _progress_ratio(props)
        steps = max(1, int(60 * ratio)) if ratio > 0 else 0
        start = -math.pi / 2.0
        for i in range(steps):
            angle = start + (2.0 * math.pi) * (i / 60.0)
            canvas.disc(int(cx + radius * math.cos(angle)),
                        int(cy + radius * math.sin(angle)), 2, accent)
    else:
        phase = float(state["phase"].value) * 2.0 * math.pi
        span = math.pi * 0.5
        for i in range(16):
            angle = -math.pi / 2.0 + phase + span * (i / 16.0)
            canvas.disc(int(cx + radius * math.cos(angle)),
                        int(cy + radius * math.sin(angle)), 2, accent)


# ---------------------------------------------------------------------------
# MdTooltip
# ---------------------------------------------------------------------------

class MdTooltip(Component):
    """提示气泡：visible 驱动显隐，触发动作由调用方回写。"""

    name = "MdTooltip"
    props = {
        "text": "",
        "visible": False,
        "below": False,
        "on_visible_change": None,
    }

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {
            "pad": 8, "radius": _shape("small", 8),
            "bg": _role_or("inverseSurface", "onSurface"),
        })
        r.bind_show(root, lambda: bool(props.get("visible")))
        r.bind_attr(root, "bg", lambda: _role_or("inverseSurface", "onSurface"))

        text = r.text("")
        r.bind_text(text, lambda: str(props.get("text") or ""))
        r.bind_attr(text, "size", lambda: _type_size("bodySmall", 12))
        r.bind_attr(text, "color", lambda: _role_or("inverseOnSurface", "surface"))
        root.add(text)

        def toggle(ev):
            cb = props.get("on_visible_change")
            if callable(cb):
                cb(not bool(props.get("visible")))

        r.on(root, "click", lambda: toggle)
        root.props["_text"] = text

        _bind_open_animation(props, "visible", root)

        _set_a11y(root, role=A11yRole.TEXT, label=str(props.get("text") or ""),
                  expanded=bool(props.get("visible")))
        return [root]


# ---------------------------------------------------------------------------
# MdBottomSheet
# ---------------------------------------------------------------------------

class MdBottomSheet(Component):
    """底部抽屉：open 驱动 scrim + 底部面板；上滑关闭为非目标。"""

    name = "MdBottomSheet"
    props = {
        "open": False,
        "title": "",
        "dismissible": True,
        "on_dismiss": None,
        "height": None,
    }

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {
            "w": "100%", "h": "100%",
            "dir": "column", "justify": "end", "align": "center",
        })
        r.bind_show(root, lambda: bool(props.get("open")))

        scrim = r.element("view", {
            "absolute": True, "x": 0, "y": 0, "w": "100%", "h": "100%",
            "bg": _scrim_color(),
        })
        r.bind_attr(scrim, "bg", _scrim_color)
        r.on(scrim, "click", lambda: (lambda ev: _dismiss_sheet(props)))

        panel = r.element("view", {
            "w": "100%", "pad": 16, "gap": 12,
            "radius": _shape("large", 16),
            "bg": theme_color("surfaceContainerLow"),
            "dir": "column", "align": "center",
        })
        r.bind_attr(panel, "bg", lambda: theme_color("surfaceContainerLow"))
        r.bind_attr(panel, "h", lambda: props.get("height") or "auto")

        handle = r.element("view", {"w": 40, "h": 4, "radius": 2,
                                    "bg": theme_color("outlineVariant")})
        r.bind_attr(handle, "bg", lambda: theme_color("outlineVariant"))

        title = r.text("")
        r.bind_text(title, lambda: str(props.get("title") or ""))
        r.bind_attr(title, "size", lambda: _type_size("titleLarge", 20))
        r.bind_attr(title, "color", lambda: theme_color("onSurface"))
        r.bind_show(title, lambda: bool(props.get("title")))

        for slot in slots.values():
            panel.add(slot)

        panel.add(handle)
        panel.add(title)
        root.add(scrim)
        root.add(panel)
        root.props["_scrim"] = scrim
        root.props["_panel"] = panel
        root.props["_handle"] = handle

        _bind_open_animation(props, "open", scrim, panel)

        _set_a11y(root, role=A11yRole.NAVIGATION,
                  label=str(props.get("title") or ""), expanded=bool(props.get("open")))
        return [root]


def _dismiss_sheet(props):
    if props.get("dismissible"):
        cb = props.get("on_dismiss")
        if callable(cb):
            cb()


COMPONENTS = [MdDialog, MdSnackbar, MdBadge, MdProgressIndicator,
              MdTooltip, MdBottomSheet]


def manifest():
    return {
        "abi": 1,
        "name": "vapor.md3.feedback",
        "version": "1.0.0",
        "components": [
            {
                "name": c.name,
                "props": dict(c.props),
                "description": (c.__doc__ or "").strip().splitlines()[0],
            }
            for c in COMPONENTS
        ],
    }


def register(host):
    for cls in COMPONENTS:
        host.register(cls)
