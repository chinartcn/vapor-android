"""MD3 按钮与操作类组件（执行文档 1）。

颜色全部来自 ``current_theme().role()``，圆角/字阶来自 ``theme.tokens``，
状态层与动效来自 ``ui.states`` / ``ui.motion``。禁止硬编码主题色。
"""

from __future__ import annotations

from .. import core
from ..theme import current_theme, get_shape, get_type_style
from ..ui.a11y import Role as A11yRole, set_a11y
from ..ui import with_alpha
from ..ui.renderer import Component, queue_dirty
from ..ui.states import attach_ripple
from ._common import (
    MD_PREFIX,
    bind_node,
    focus_ring,
    paint_ripple,
    paint_state_layer,
    pressable,
    soft_shadow,
    state_mask,
    theme_color,
)

__all__ = [
    "MdButton", "MdIconButton", "MdFAB", "MdExtendedFAB", "MdSegmentedButton",
    "COMPONENTS", "manifest", "register", "resolve_button_colors",
]

# variant -> (容器角色, 内容角色)；None 表示透明容器。
BUTTON_ROLES = {
    "filled": ("primary", "onPrimary"),
    "tonal": ("secondaryContainer", "onSecondaryContainer"),
    "outlined": (None, "primary"),
    "text": (None, "primary"),
    "elevated": ("surfaceContainerLow", "primary"),
}

ICON_BUTTON_ROLES = {
    "standard": (None, "onSurfaceVariant"),
    "filled": ("primary", "onPrimary"),
    "tonal": ("secondaryContainer", "onSecondaryContainer"),
    "outlined": (None, "onSurfaceVariant"),
}


def resolve_button_colors(variant="filled", bg=None, color=None):
    """返回 ``(容器色|None, 内容色)``；容器 None 表示透明。"""
    container_role, content_role = BUTTON_ROLES.get(variant, BUTTON_ROLES["filled"])
    container = None if container_role is None else theme_color(container_role, bg)
    content = theme_color(content_role, color)
    return container, content


def _num(value, default):
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _disabled(props) -> bool:
    return bool(props.get("disabled")) or props.get("enabled") is False


def _icon(r, name, size, color_getter):
    """图标节点；取色随主题变化。"""
    if not name:
        return None
    node = r.component_node("Icon", {"name": name, "size": size}, {})
    r.bind_attr(node, "color", color_getter)
    return node


def _label(r, props, color_getter, size_key="labelLarge", text_key="label"):
    text = r.text("")
    r.bind_text(text, lambda: props.get(text_key, ""))
    style = get_type_style(size_key)
    text.props["size"] = style["fontSize"]
    r.bind_attr(text, "color", color_getter)
    return text


class MdButton(Component):
    """MD3 按钮：filled / tonal / outlined / text / elevated。"""

    name = "MdButton"
    props = {
        "variant": "filled",
        "label": "",
        "enabled": True,
        "disabled": False,
        "leading_icon": None,
        "trailing_icon": None,
        "on_click": None,
        "w": None,
        "h": 40,
        "bg": None,
        "color": None,
    }

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {
            "dir": "row", "align": "center", "justify": "center",
            "gap": 8, "padding": [0, 16],
        })
        bind_node(r, node, props, skip=(
            "variant", "label", "enabled", "disabled", "leading_icon",
            "trailing_icon", "on_click", "text", "bg", "color", "h", "w", "radius",
        ))
        r.bind_attr(node, "h", lambda: _num(props.get("h"), 40))
        r.bind_attr(node, "radius", lambda: _num(props.get("h"), 40) / 2.0)
        if props.get("w") is not None:
            r.bind_attr(node, "w", lambda: props.get("w"))

        def container_getter():
            container, _ = resolve_button_colors(
                props.get("variant", "filled"), props.get("bg"), props.get("color"))
            return "none" if container is None else container

        def content_getter():
            _, content = resolve_button_colors(
                props.get("variant", "filled"), props.get("bg"), props.get("color"))
            return with_alpha(content, 0.38) if _disabled(props) else content

        r.bind_attr(node, "bg", container_getter)
        r.bind_attr(node, "color", content_getter)
        if props.get("variant", "filled") == "outlined":
            r.bind_attr(node, "border", lambda: (1, theme_color("outline")))

        def paint(canvas, box):
            radius = min(box.w, box.h) / 2.0
            content = content_getter()
            paint_state_layer(canvas, node, props, box, radius, content)
            if getattr(node, "extra", {}).get("focus"):
                focus_ring(canvas, box, content, radius)
            paint_ripple(canvas, node, box)

        node.props["paint"] = paint

        leading = _icon(r, props.get("leading_icon"), 18, content_getter)
        if leading is not None:
            node.add(leading)
        if slots.get("default") is not None:
            node.add(r.slot("default"))
        else:
            node.add(_label(r, props, content_getter))
        trailing = _icon(r, props.get("trailing_icon"), 18, content_getter)
        if trailing is not None:
            node.add(trailing)

        attach_ripple(node, content_getter(), 0.10)
        pressable(r, node, props)
        set_a11y(
            node, role=A11yRole.BUTTON,
            label=str(props.get("label", "")),
            disabled=_disabled(props),
        )
        return [node]


class MdIconButton(Component):
    """MD3 图标按钮：standard / filled / tonal / outlined，可 toggle。"""

    name = "MdIconButton"
    props = {
        "icon": "home",
        "variant": "standard",
        "toggle": False,
        "selected": False,
        "enabled": True,
        "disabled": False,
        "on_click": None,
        "size": 40,
    }

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {
            "dir": "row", "align": "center", "justify": "center",
        })
        bind_node(r, node, props, skip=(
            "icon", "variant", "toggle", "selected", "enabled", "disabled", "on_click",
        ))
        r.bind_attr(node, "w", lambda: _num(props.get("size"), 40))
        r.bind_attr(node, "h", lambda: _num(props.get("size"), 40))
        r.bind_attr(node, "radius", lambda: _num(props.get("size"), 40) / 2.0)
        selected = core.ref(bool(props.get("selected")))

        def roles():
            variant = props.get("variant", "standard")
            if props.get("toggle") and selected.value:
                return "secondaryContainer", "onSecondaryContainer"
            return ICON_BUTTON_ROLES.get(variant, ICON_BUTTON_ROLES["standard"])

        def container_getter():
            role, _ = roles()
            return "none" if role is None else theme_color(role)

        def content_getter():
            _, role = roles()
            c = theme_color(role)
            return with_alpha(c, 0.38) if _disabled(props) else c

        r.bind_attr(node, "bg", container_getter)
        r.bind_attr(node, "color", content_getter)
        if props.get("variant", "standard") == "outlined":
            r.bind_attr(node, "border", lambda: (1, theme_color("outline")))

        def paint(canvas, box):
            radius = min(box.w, box.h) / 2.0
            paint_state_layer(canvas, node, props, box, radius, content_getter())
            paint_ripple(canvas, node, box)

        node.props["paint"] = paint

        icon_node = _icon(r, props.get("icon"), 24, content_getter)
        if icon_node is not None:
            node.add(icon_node)

        def click(ev):
            if _disabled(props):
                return
            if props.get("toggle"):
                selected.value = not selected.value
                set_a11y(
                    node, role=A11yRole.BUTTON,
                    label=str(props.get("icon", "")),
                    checked=selected.value, disabled=False,
                )
            callback = props.get("on_click")
            if callable(callback):
                callback(ev)

        r.on(node, "click", lambda: click)
        attach_ripple(node, content_getter(), 0.10)
        set_a11y(
            node, role=A11yRole.BUTTON,
            label=str(props.get("icon", "")),
            checked=selected.value,
            disabled=_disabled(props),
        )
        return [node]


def _fab_paint(node, props, container_getter, content_getter, radius_getter, extended):
    def paint(canvas, box):
        radius = radius_getter(box)
        soft_shadow(canvas, box, radius)
        paint_state_layer(canvas, node, props, box, radius, content_getter())
        paint_ripple(canvas, node, box)

    node.props["paint"] = paint


def _build_fab(r, props, slots, extended):
    node = r.element("view", {
        "dir": "row", "align": "center", "justify": "center",
        "gap": 12, "padding": [0, 16] if extended else [0, 0],
    })
    bind_node(r, node, props, skip=("icon", "label", "size", "bg", "color", "on_click"))

    if extended:
        r.bind_attr(node, "h", lambda: 56)
        r.bind_attr(node, "min_w", lambda: 80)
        shape = "full"
    else:
        size = _num(props.get("size"), 56) if not isinstance(props.get("size"), str) \
            else (40.0 if props.get("size") == "mini" else 56.0)
        r.bind_attr(node, "w", lambda: size)
        r.bind_attr(node, "h", lambda: size)
        shape = "medium" if size <= 40 else "large"

    def radius_getter(box):
        if shape == "full":
            return min(box.w, box.h) / 2.0
        return float(get_shape(shape))

    def container_getter():
        return theme_color("primaryContainer", props.get("bg"))

    def content_getter():
        c = theme_color("onPrimaryContainer", props.get("color"))
        return with_alpha(c, 0.38) if _disabled(props) else c

    r.bind_attr(node, "bg", container_getter)
    r.bind_attr(node, "color", content_getter)
    r.bind_attr(node, "radius", lambda: radius_getter(node.box))
    _fab_paint(node, props, container_getter, content_getter, radius_getter, extended)

    icon_node = _icon(r, props.get("icon"), 24, content_getter)
    if icon_node is not None:
        node.add(icon_node)
    if extended:
        node.add(_label(r, props, content_getter, "labelLarge"))

    attach_ripple(node, content_getter(), 0.10)
    pressable(r, node, props)
    label = props.get("label") or props.get("icon") or ""
    set_a11y(node, role=A11yRole.BUTTON, label=str(label), disabled=_disabled(props))
    return [node]


class MdFAB(Component):
    """MD3 浮动按钮（default / mini）。"""

    name = "MdFAB"
    props = {"icon": "add", "size": "default", "bg": None, "color": None,
             "on_click": None, "enabled": True, "disabled": False}

    @staticmethod
    def build(r, props, slots):
        return _build_fab(r, props, slots, extended=False)


class MdExtendedFAB(Component):
    """MD3 扩展浮动按钮（图标 + 文案）。"""

    name = "MdExtendedFAB"
    props = {"icon": "add", "label": "", "bg": None, "color": None,
             "on_click": None, "enabled": True, "disabled": False}

    @staticmethod
    def build(r, props, slots):
        return _build_fab(r, props, slots, extended=True)


class MdSegmentedButton(Component):
    """MD3 分段按钮：单选互斥 / 多选并存。"""

    name = "MdSegmentedButton"
    props = {"segments": [], "selected": [], "multiple": False, "on_change": None}

    @staticmethod
    def build(r, props, slots):
        segments = list(props.get("segments") or [])
        multiple = bool(props.get("multiple"))
        initial = props.get("selected")
        if initial is None:
            selected = []
        elif isinstance(initial, (str, int, float)):
            selected = [initial]
        else:
            try:
                selected = list(initial)
            except TypeError:
                selected = [initial]
        selected = list(dict.fromkeys(selected))

        root = r.element("view", {
            "dir": "row", "h": 40,
            "gap": 2 if multiple else 0,
        })

        def emit():
            callback = props.get("on_change")
            if callable(callback):
                callback(list(selected))

        records = []
        for seg in segments:
            value = seg.get("value", seg.get("label", ""))
            label = seg.get("label", "")
            icon = seg.get("icon")
            node = r.element("view", {
                "dir": "row", "align": "center", "justify": "center",
                "grow": 1, "h": 40, "gap": 6,
                "padding": [0, 12],
            })
            # 单选整体胶囊：每段也取 full；多选左右分离（small）。
            radius = 20.0 if not multiple else float(get_shape("small"))
            node.props["radius"] = radius

            def make_is_selected(v):
                return lambda: v in selected

            is_selected = make_is_selected(value)

            def container_getter(s=is_selected):
                return theme_color("secondaryContainer") if s() else "none"

            def content_getter(s=is_selected):
                return theme_color("onSecondaryContainer" if s() else "onSurface")

            r.bind_attr(node, "bg", container_getter)
            r.bind_attr(node, "color", content_getter)

            text_node = None
            if label:
                text_node = _label(r, {"label": label}, content_getter)
                node.add(text_node)
            if icon:
                icon_node = _icon(r, icon, 18, content_getter)
                if icon_node is not None:
                    node.add(icon_node)

            def paint(canvas, box, s=is_selected, cg=content_getter):
                if s():
                    canvas.round_rect(box.x, box.y, box.w, box.h,
                                      node.props.get("radius", 8),
                                      theme_color("secondaryContainer"))
                paint_state_layer(canvas, node, props, box,
                                  node.props.get("radius", 8), cg())
                paint_ripple(canvas, node, box)

            node.props["paint"] = paint
            set_a11y(
                node,
                role=A11yRole.CHECKBOX if multiple else A11yRole.BUTTON,
                label=label or str(value),
                checked=value in selected,
            )

            def click(ev, v=value, n=node, t=text_node):
                if multiple:
                    if v in selected:
                        selected.remove(v)
                    else:
                        selected.append(v)
                else:
                    if selected == [v]:
                        return
                    selected.clear()
                    selected.append(v)
                for rec in records:
                    checked = rec["value"] in selected
                    set_a11y(
                        rec["node"],
                        role=A11yRole.CHECKBOX if multiple else A11yRole.BUTTON,
                        label=rec["label"] or str(rec["value"]),
                        checked=checked,
                    )
                    rec["node"].props["selected"] = checked
                    if rec["text"] is not None:
                        rec["text"].props["color"] = (
                            theme_color("onSecondaryContainer") if checked
                            else theme_color("onSurface")
                        )
                    b = rec["node"].box
                    if b.w > 0 and b.h > 0:
                        queue_dirty(b.x, b.y, b.w, b.h)
                emit()

            r.on(node, "click", lambda c=click: (lambda e=None: c(e)))
            records.append({"node": node, "value": value, "label": label, "text": text_node})
            root.add(node)

        root.extra["segments"] = [rec["node"] for rec in records]
        root.extra["selected"] = selected
        set_a11y(root, role=A11yRole.NAVIGATION, label="segmented")
        return [root]


COMPONENTS = [MdButton, MdIconButton, MdFAB, MdExtendedFAB, MdSegmentedButton]


def manifest():
    return {
        "abi": 1,
        "name": "vapor.md3.actions",
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
