"""MD3 选择与输入类内建组件（MdCheckbox / MdRadioButton / MdSwitch /
MdSlider / MdTextField / MdChip）。

只依赖已落地的主题运行时与渲染 API；颜色一律走 ``current_theme().role(...)``，
不写死任何颜色字面量。基础层（states / motion / a11y / md3._common）尚未合入时
使用本文件内的等价兜底，合入后自动切换到权威实现。
"""

from __future__ import annotations

from .. import core
from ..theme import current_theme
from ..ui import Component

__all__ = [
    "MdCheckbox",
    "MdRadioButton",
    "MdSwitch",
    "MdSlider",
    "MdTextField",
    "MdChip",
    "COMPONENTS",
    "manifest",
    "register",
    "get_a11y",
]


# ---------------------------------------------------------------------------
# 基础层兜底（存在真实实现时优先使用）
# ---------------------------------------------------------------------------

try:  # 文档 1 的 _common 尚未合入时
    from ._common import theme_color as _common_theme_color
except ImportError:  # pragma: no cover
    _common_theme_color = None

try:
    from ..theme import get_shape as _theme_get_shape
except ImportError:  # pragma: no cover
    _theme_get_shape = None

try:
    from ..theme import get_type_style as _theme_get_type_style
except ImportError:  # pragma: no cover
    _theme_get_type_style = None

try:
    from ..ui.states import (
        STATE_OPACITY as _STATE_OPACITY,
        TOUCH_TARGET_SIZE as _TOUCH_TARGET_SIZE,
    )
except ImportError:  # pragma: no cover
    _STATE_OPACITY = {
        "pressed": 0.12, "hover": 0.08, "focus": 0.12,
        "dragged": 0.16, "disabled": 0.38,
    }
    _TOUCH_TARGET_SIZE = 48

try:
    from ..ui.motion import reduced_motion as _reduced_motion
except ImportError:  # pragma: no cover
    _reduced_motion = None

try:
    from ..ui.a11y import Role as A11yRole  # type: ignore
    from ..ui.a11y import get_a11y as _real_get_a11y
    from ..ui.a11y import set_a11y as _real_set_a11y
except ImportError:  # pragma: no cover
    class A11yRole:  # 与文档 3 的 Role 成员保持同名
        CHECKBOX = "checkbox"
        SWITCH = "switch"
        BUTTON = "button"
        TEXT = "text"

    _real_set_a11y = None
    _real_get_a11y = None


def theme_color(name: str) -> int:
    if _common_theme_color is not None:
        return _common_theme_color(name)
    return current_theme().role(name)


def state_mask(color: int, opacity: float) -> int:
    factor = max(0.0, min(1.0, float(opacity)))
    alpha = int(((color >> 24) & 0xFF) * factor)
    return (alpha << 24) | (color & 0x00FFFFFF)


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
    if _reduced_motion is not None:
        return bool(_reduced_motion())
    return False


def get_a11y(node):
    """读取本组件挂载的无障碍语义。"""
    if _real_get_a11y is not None:
        return _real_get_a11y(node)
    return node.extra.get("a11y")


def _set_a11y(node, **info):
    if _real_set_a11y is not None:
        _real_set_a11y(node, **info)
    else:
        node.extra["a11y"] = dict(info)


def _attach_press(r, node, on_click, enabled):
    pressed = core.ref(False)

    def down(ev):
        if enabled():
            pressed.value = True

    def up(ev):
        pressed.value = False

    def click(ev):
        pressed.value = False
        if enabled():
            on_click(ev)

    r.on(node, "pointerdown", lambda: down)
    r.on(node, "pointerup", lambda: up)
    r.on(node, "click", lambda: click)
    return pressed


def _state_overlay(r, parent, enabled, pressed):
    overlay = r.element("view", {"absolute": True, "x": 0, "y": 0, "w": "100%", "h": "100%"})
    r.bind_attr(overlay, "bg", lambda: state_mask(
        theme_color("onSurface"),
        _STATE_OPACITY["disabled"] if not enabled() else _STATE_OPACITY["pressed"],
    ))
    r.bind_show(overlay, lambda: (not enabled()) or bool(pressed.value))
    parent.add(overlay)
    return overlay


def _label(r, text_getter, size, color):
    node = r.text("")
    r.bind_text(node, text_getter)
    if callable(color):
        r.bind_attr(node, "color", color)
    else:
        r.bind_attr(node, "color", lambda: theme_color(color))
    r.bind_attr(node, "size", lambda: size)
    return node


def _measure_height(default=48):
    return max(float(_TOUCH_TARGET_SIZE), default)


# ---------------------------------------------------------------------------
# MdCheckbox
# ---------------------------------------------------------------------------

class MdCheckbox(Component):
    """复选框：点击切换 checked，并通过 on_change 回传新值。"""

    name = "MdCheckbox"
    props = {
        "checked": False,
        "indeterminate": False,
        "enabled": True,
        "label": "",
        "on_change": None,
    }

    @staticmethod
    def build(r, props, slots):
        enabled = lambda: bool(props.get("enabled", True))
        muted = lambda: not enabled()

        root = r.element("view", {"dir": "row", "align": "center", "gap": 8,
                                  "h": _measure_height()})
        box = r.element("view", {
            "w": 18, "h": 18, "radius": _shape("small", 8),
            "dir": "column", "justify": "center", "align": "center",
        })
        check = r.text("✓")
        r.bind_attr(check, "color", lambda: theme_color("onPrimary"))
        r.bind_attr(check, "size", lambda: 14)
        check.props["absolute"] = True
        check.props["x"] = 3
        check.props["y"] = 0
        r.bind_show(check, lambda: bool(props.get("checked")) and not bool(props.get("indeterminate")))

        dash = r.element("view", {
            "absolute": True, "x": 4, "y": 8, "w": 10, "h": 2,
            "radius": 1, "bg": theme_color("onPrimary"),
        })
        r.bind_show(dash, lambda: bool(props.get("indeterminate")))

        r.bind_attr(box, "bg", lambda: theme_color("primary")
                    if (props.get("checked") or props.get("indeterminate")) else 0)
        r.bind_attr(box, "border", lambda: (
            2,
            theme_color("outline") if muted() else (
                theme_color("primary") if (props.get("checked") or props.get("indeterminate"))
                else theme_color("outline")
            ),
        ))

        pressed = _attach_press(r, root, lambda ev: _toggle_checkbox(props), enabled)
        _state_overlay(r, box, enabled, pressed)
        box.add(check)
        box.add(dash)
        root.add(box)
        root.add(_label(r, lambda: str(props.get("label") or ""), 16, "onSurface"))
        root.props["_box"] = box

        _set_a11y(root, role=A11yRole.CHECKBOX, label=str(props.get("label") or ""),
                  checked=bool(props.get("checked")), disabled=muted())
        return [root]


def _toggle_checkbox(props):
    new_value = True if props.get("indeterminate") else not bool(props.get("checked"))
    cb = props.get("on_change")
    if callable(cb):
        cb(new_value)
    else:
        props["indeterminate"] = False
        props["checked"] = new_value


# ---------------------------------------------------------------------------
# MdRadioButton
# ---------------------------------------------------------------------------

class MdRadioButton(Component):
    """单选按钮：group 仅作语义标签，互斥由调用方维护（无全局单例）。"""

    name = "MdRadioButton"
    props = {
        "checked": False,
        "enabled": True,
        "label": "",
        "value": None,
        "on_change": None,
        "group": None,
    }

    @staticmethod
    def build(r, props, slots):
        enabled = lambda: bool(props.get("enabled", True))

        root = r.element("view", {"dir": "row", "align": "center", "gap": 8,
                                  "h": _measure_height()})
        ring = r.element("view", {
            "w": 20, "h": 20, "radius": 10, "dir": "column",
            "justify": "center", "align": "center",
        })
        dot = r.element("view", {"w": 10, "h": 10, "radius": 5})
        r.bind_show(dot, lambda: bool(props.get("checked")))
        r.bind_attr(dot, "bg", lambda: theme_color("primary"))
        r.bind_attr(ring, "border", lambda: (
            2,
            theme_color("primary") if props.get("checked") else theme_color("outline"),
        ))

        def on_click(ev):
            cb = props.get("on_change")
            value = props.get("value")
            if callable(cb):
                cb(value)
            else:
                props["checked"] = True

        pressed = _attach_press(r, root, on_click, enabled)
        _state_overlay(r, ring, enabled, pressed)
        ring.add(dot)
        root.add(ring)
        root.add(_label(r, lambda: str(props.get("label") or ""), 16, "onSurface"))
        root.props["_ring"] = ring

        _set_a11y(root, role=A11yRole.CHECKBOX, label=str(props.get("label") or ""),
                  checked=bool(props.get("checked")), selected=bool(props.get("checked")))
        return [root]


# ---------------------------------------------------------------------------
# MdSwitch
# ---------------------------------------------------------------------------

class MdSwitch(Component):
    """开关：轨道 52x32 / handle 24；reduced_motion 下位移直接到位。"""

    name = "MdSwitch"
    props = {
        "checked": False,
        "enabled": True,
        "label": "",
        "on_change": None,
    }

    @staticmethod
    def build(r, props, slots):
        enabled = lambda: bool(props.get("enabled", True))

        root = r.element("view", {"dir": "row", "align": "center", "gap": 8,
                                  "h": _measure_height()})
        track = r.element("view", {"w": 52, "h": 32, "radius": 16})
        knob = r.element("view", {
            "absolute": True, "y": 4, "w": 24, "h": 24, "radius": 12,
        })

        r.bind_attr(track, "bg", lambda: theme_color("primary")
                    if props.get("checked") else theme_color("surfaceContainerHighest"))
        r.bind_attr(knob, "bg", lambda: theme_color("onPrimary")
                    if props.get("checked") else theme_color("outline"))
        r.bind_attr(knob, "x", lambda: _switch_handle_x(props.get("checked"), reduced_motion()))

        def on_click(ev):
            new_value = not bool(props.get("checked"))
            cb = props.get("on_change")
            if callable(cb):
                cb(new_value)
            else:
                props["checked"] = new_value

        pressed = _attach_press(r, root, on_click, enabled)
        _state_overlay(r, track, enabled, pressed)
        track.add(knob)
        root.add(track)
        root.add(_label(r, lambda: str(props.get("label") or ""), 16, "onSurface"))
        root.props["_track"] = track
        root.props["_knob"] = knob

        _set_a11y(root, role=A11yRole.SWITCH, label=str(props.get("label") or ""),
                  checked=bool(props.get("checked")))
        return [root]


def _switch_handle_x(checked, reduce):
    target = 24.0 if checked else 4.0
    if reduce:
        return target
    # motion 层合入后可在此接入 spring 插值；当前保持确定性终态。
    return target


# ---------------------------------------------------------------------------
# MdSlider
# ---------------------------------------------------------------------------

class MdSlider(Component):
    """滑块：连续或离散刻度；点击 / 拖动按轨道比例取值。"""

    name = "MdSlider"
    props = {
        "value": 0.0,
        "min": 0.0,
        "max": 1.0,
        "step": None,
        "enabled": True,
        "label": "",
        "on_change": None,
    }

    @staticmethod
    def build(r, props, slots):
        enabled = lambda: bool(props.get("enabled", True))

        root = r.element("view", {"w": "100%", "h": 32})
        track = r.element("view", {
            "absolute": True, "x": 0, "y": 14, "w": "100%", "h": 4,
            "radius": 2, "bg": theme_color("surfaceContainerHighest"),
        })
        fill = r.element("view", {
            "absolute": True, "x": 0, "y": 14, "h": 4, "radius": 2,
            "bg": theme_color("primary"),
        })
        knob = r.element("view", {
            "absolute": True, "y": 6, "w": 20, "h": 20, "radius": 10,
            "bg": theme_color("primary"),
        })
        r.bind_attr(fill, "w", lambda: f"{_slider_ratio(props) * 100:.2f}%")
        r.bind_attr(knob, "x", lambda: f"{_slider_ratio(props) * 100:.2f}%")

        root.add(track)
        root.add(fill)
        root.add(knob)

        ticks = _build_ticks(r, props)
        for dot in ticks:
            root.add(dot)

        def update(ev):
            if not enabled():
                return
            box = root.box
            if box.w <= 0:
                return
            ratio = max(0.0, min(1.0, (ev.x - box.x) / box.w))
            value = _quantize_value(ratio, props)
            cb = props.get("on_change")
            if callable(cb):
                cb(value)
            else:
                props["value"] = value

        r.on(root, "pointerdown", lambda: update)
        r.on(root, "drag", lambda: update)
        r.on(root, "click", lambda: update)

        root.props["_track"] = track
        root.props["_fill"] = fill
        root.props["_knob"] = knob

        _set_a11y(root, role=A11yRole.SWITCH, label=str(props.get("label") or ""),
                  checked=False, selected=True)
        return [root]


def _clamp(value, low, high):
    return max(low, min(high, value))


def _slider_ratio(props) -> float:
    low = float(props.get("min", 0.0) or 0.0)
    high = float(props.get("max", 1.0) or 1.0)
    if high <= low:
        return 0.0
    value = _clamp(float(props.get("value", 0.0) or 0.0), low, high)
    return (value - low) / (high - low)


def _quantize_value(ratio, props):
    low = float(props.get("min", 0.0) or 0.0)
    high = float(props.get("max", 1.0) or 1.0)
    value = low + _clamp(float(ratio), 0.0, 1.0) * (high - low)
    step = props.get("step")
    if step:
        step = abs(float(step))
        if step > 0:
            value = low + round((value - low) / step) * step
    return _clamp(value, low, high)


def _build_ticks(r, props):
    step = props.get("step")
    dots = []
    if not step:
        return dots
    step = abs(float(step))
    low = float(props.get("min", 0.0) or 0.0)
    high = float(props.get("max", 1.0) or 1.0)
    if step <= 0 or high <= low:
        return dots
    count = int((high - low) / step) + 1
    if count > 64:  # 回退/极端输入下避免生成过多节点
        return dots
    for i in range(count):
        ratio = i / (count - 1) if count > 1 else 0.0
        dot = r.element("view", {
            "absolute": True, "x": f"{ratio * 100:.2f}%", "y": 26,
            "w": 2, "h": 2, "radius": 1, "bg": theme_color("outlineVariant"),
        })
        dots.append(dot)
    return dots


# ---------------------------------------------------------------------------
# MdTextField
# ---------------------------------------------------------------------------

class MdTextField(Component):
    """文本框：展示型（不接 IME），focused/error/label 浮动由 props 驱动。"""

    name = "MdTextField"
    props = {
        "value": "",
        "label": "",
        "placeholder": "",
        "variant": "filled",
        "error": False,
        "errorText": "",
        "enabled": True,
        "leading_icon": None,
        "on_change": None,
        "on_focus": None,
    }

    @staticmethod
    def build(r, props, slots):
        focused = core.ref(False)
        enabled = lambda: bool(props.get("enabled", True))
        outlined = lambda: props.get("variant") == "outlined"
        floated = lambda: bool(props.get("value")) or bool(focused.value)
        accent = lambda: theme_color("error") if props.get("error") else (
            theme_color("primary") if focused.value else theme_color("outline")
        )

        root = r.element("view", {"w": "100%", "h": 56})
        container = r.element("view", {
            "w": "100%", "h": 56, "radius": _shape("small", 8),
        })
        line = r.element("view", {
            "absolute": True, "bottom": 0, "x": 0, "h": 1, "w": "100%",
            "bg": theme_color("outline"),
        })
        r.bind_attr(line, "bg", lambda: theme_color("error") if props.get("error") else (
            theme_color("primary") if focused.value else theme_color("outline")
        ))
        r.bind_show(line, lambda: not outlined())

        label = _label(r, lambda: str(props.get("label") or ""), 16,
                       "onSurfaceVariant")
        r.bind_attr(label, "size", lambda: _type_size("bodySmall", 12)
                    if floated() else _type_size("bodyLarge", 16))
        label.props["absolute"] = True
        label.props["x"] = 12
        r.bind_attr(label, "y", lambda: 8 if floated() else 20)
        r.bind_attr(label, "color", lambda: accent())

        value = r.text("")
        r.bind_text(value, lambda: str(props.get("value") or ""))
        r.bind_attr(value, "color", lambda: theme_color("onSurface"))
        r.bind_attr(value, "size", lambda: _type_size("bodyLarge", 16))
        value.props["absolute"] = True
        value.props["x"] = 12
        value.props["y"] = 28
        r.bind_show(value, lambda: bool(props.get("value")))

        placeholder = r.text("")
        r.bind_text(placeholder, lambda: str(props.get("placeholder") or ""))
        r.bind_attr(placeholder, "color", lambda: theme_color("onSurfaceVariant"))
        r.bind_attr(placeholder, "size", lambda: _type_size("bodyLarge", 16))
        placeholder.props["absolute"] = True
        placeholder.props["x"] = 12
        placeholder.props["y"] = 28
        r.bind_show(placeholder, lambda: not bool(props.get("value")))

        r.bind_attr(container, "bg", lambda: 0 if outlined() else theme_color("surfaceContainerHighest"))
        r.bind_attr(container, "border", lambda: (1, accent()) if outlined() else None)

        def focus(ev):
            if not enabled():
                return
            focused.value = True
            cb = props.get("on_focus")
            if callable(cb):
                cb(ev)

        r.on(container, "pointerdown", lambda: focus)

        container.add(line)
        container.add(label)
        container.add(value)
        container.add(placeholder)
        root.add(container)
        root.props["_container"] = container
        root.props["_line"] = line
        root.props["_label"] = label

        _set_a11y(root, role=A11yRole.TEXT,
                  label=str(props.get("label") or props.get("placeholder") or ""),
                  focused=bool(focused.value), disabled=not enabled())
        return [root]


# ---------------------------------------------------------------------------
# MdChip
# ---------------------------------------------------------------------------

class MdChip(Component):
    """芯片：assist / filter / input / select 四种变体。"""

    name = "MdChip"
    props = {
        "label": "",
        "variant": "assist",
        "selected": False,
        "enabled": True,
        "leading_icon": None,
        "trailing_icon": None,
        "on_close": None,
        "on_click": None,
    }

    @staticmethod
    def build(r, props, slots):
        enabled = lambda: bool(props.get("enabled", True))
        selectable = lambda: props.get("variant") in ("filter", "select")
        selected = lambda: bool(props.get("selected")) and selectable()

        root = r.element("view", {
            "dir": "row", "align": "center", "gap": 6,
            "h": 32, "radius": _shape("small", 8), "pad": 12,
        })
        r.bind_attr(root, "bg", lambda: theme_color("secondaryContainer")
                    if selected() else 0)
        r.bind_attr(root, "border", lambda: (
            1,
            theme_color("secondaryContainer") if selected() else theme_color("outline"),
        ))

        content_color = lambda: theme_color("onSecondaryContainer") if selected() else theme_color("onSurfaceVariant")

        if props.get("variant") in ("filter", "select"):
            mark = r.text("✓")
            r.bind_attr(mark, "color", content_color)
            r.bind_attr(mark, "size", lambda: 14)
            r.bind_show(mark, selected)
            root.add(mark)

        if props.get("leading_icon") is not None:
            icon = r.text(str(props.get("leading_icon")))
            r.bind_attr(icon, "color", content_color)
            r.bind_attr(icon, "size", lambda: 16)
            root.add(icon)

        label = _label(r, lambda: str(props.get("label") or ""), 14, content_color)

        def on_click(ev):
            cb = props.get("on_click")
            if callable(cb):
                cb(ev)
            elif selectable():
                props["selected"] = not bool(props.get("selected"))

        pressed = _attach_press(r, root, on_click, enabled)
        _state_overlay(r, root, enabled, pressed)
        root.add(label)

        if props.get("variant") == "input":
            close = r.text("×")
            r.bind_attr(close, "color", content_color)
            r.bind_attr(close, "size", lambda: 16)
            r.on(close, "click", lambda: (lambda ev: _fire_close(props, ev)))
            root.add(close)
        elif props.get("trailing_icon") is not None:
            icon = r.text(str(props.get("trailing_icon")))
            r.bind_attr(icon, "color", content_color)
            r.bind_attr(icon, "size", lambda: 16)
            root.add(icon)

        role = A11yRole.CHECKBOX if selectable() else A11yRole.BUTTON
        _set_a11y(root, role=role, label=str(props.get("label") or ""),
                  checked=selected())
        root.props["_container"] = root
        return [root]


def _fire_close(props, ev):
    cb = props.get("on_close")
    if callable(cb):
        cb(ev)


COMPONENTS = [MdCheckbox, MdRadioButton, MdSwitch, MdSlider, MdTextField, MdChip]


def manifest():
    return {
        "abi": 1,
        "name": "vapor.md3.selection",
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
