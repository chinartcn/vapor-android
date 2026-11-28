"""MD3 导航与容器类组件（执行文档 3）。

颜色全部来自 ``current_theme().role()``，圆角/字阶来自 ``theme.tokens``，
状态层/动效来自 ``ui.states`` / ``ui.motion``。禁止硬编码主题色。
导航项点击只通过 ``on_change(value)`` 上报，路由跳转归调用方。
"""

from __future__ import annotations

from ..theme import get_shape, get_type_style
from ..ui.a11y import Role as A11yRole, set_a11y
from ..ui.renderer import Component, _warn
from ..ui.states import attach_ripple
from ._common import (
    focus_ring,
    paint_ripple,
    paint_state_layer,
    pressable,
    soft_shadow,
    theme_color,
)

__all__ = [
    "MdNavigationBar", "MdNavigationRail", "MdTabs", "MdTopAppBar",
    "MdCard", "MdList", "MdListItem",
    "COMPONENTS", "manifest", "register",
]

# 容器色角色映射。
CARD_ROLES = {
    "elevated": "surfaceContainerLow",
    "filled": "surfaceContainerHighest",
    "outlined": "surface",
}


# ---------------------------------------------------------------------------
# 私有 helper
# ---------------------------------------------------------------------------


def _num(value, default):
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _as_dict(item):
    """把导航项 / 列表项归一化成可 ``get`` 的映射。"""
    if isinstance(item, dict):
        return item
    if hasattr(item, "get"):
        return item
    return {"label": str(item)}


def _as_list(source):
    try:
        if source is None:
            return []
        return list(source)
    except TypeError:
        return [source]


def _icon(r, name, size, color_getter):
    """图标节点；取色随主题变化。"""
    if not name:
        return None
    node = r.component_node("Icon", {"name": name, "size": size}, {})
    r.bind_attr(node, "color", color_getter)
    return node


def _label(r, getter, size_key, color_getter, align=None):
    """文本节点；字阶取自 ``get_type_style``。"""
    text = r.text("")
    r.bind_text(text, getter)
    text.props["size"] = get_type_style(size_key)["fontSize"]
    r.bind_attr(text, "color", color_getter)
    if align:
        text.props["align"] = align
    return text


def _make_click(r, node, props, value):
    """点击回调：把 ``value`` 通过 ``on_change`` 上报。"""

    def click(ev):
        callback = props.get("on_change")
        if callable(callback):
            callback(value)

    r.on(node, "click", lambda c=click: (lambda e=None: c(e)))


def _badge(r, number):
    """最小数字角标（取 ``error`` 角色），视觉对齐 MdBadge。"""
    wrap = r.element("view", {
        "dir": "row", "align": "center", "justify": "center",
        "min_w": 16, "h": 16, "padding": [0, 4], "radius": 8,
    })
    r.bind_attr(wrap, "bg", lambda: theme_color("error"))
    text = r.text(str(number))
    text.props["size"] = get_type_style("labelSmall")["fontSize"]
    r.bind_attr(text, "color", lambda: theme_color("onError"))
    wrap.add(text)
    return wrap


# ---------------------------------------------------------------------------
# 导航栏
# ---------------------------------------------------------------------------


class MdNavigationBar(Component):
    """MD3 底部导航栏（3-5 项，超出告警）。"""

    name = "MdNavigationBar"
    props = {"items": [], "active": None, "on_change": None, "bg": None}

    @staticmethod
    def build(r, props, slots):
        items = [_as_dict(it) for it in _as_list(props.get("items"))]
        if len(items) > 5:
            _warn(f"MdNavigationBar 项数为 {len(items)}，MD3 建议 3-5 项")

        root = r.element("view", {
            "dir": "row", "align": "center", "h": 80,
        })
        r.bind_attr(root, "bg", lambda: theme_color("surfaceContainer", props.get("bg")))

        icon_nodes = {}
        indicator_nodes = {}
        badge_nodes = {}
        item_nodes = []

        for it in items:
            value = it.get("value", it.get("label", ""))
            icon = it.get("icon")
            label = it.get("label", "")
            badge = it.get("badge")

            item = r.element("view", {
                "dir": "column", "align": "center", "justify": "center",
                "grow": 1, "h": 80, "gap": 4,
            })

            def is_active(v=value):
                return props.get("active") == v

            def icon_color(v=value):
                if props.get("active") == v:
                    return theme_color("primary")
                return theme_color("onSurfaceVariant")

            top = r.element("view", {"dir": "row", "align": "center", "gap": 4})
            icon_node = _icon(r, icon, 24, icon_color)
            if icon_node is not None:
                top.add(icon_node)
                icon_nodes[value] = icon_node
            if badge is not None and badge != "" and badge is not False:
                badge_node = _badge(r, badge)
                top.add(badge_node)
                badge_nodes[value] = badge_node
            item.add(top)

            item.add(_label(r, lambda v=value: label, "labelMedium", icon_color))

            indicator = r.element("view", {"w": 32, "h": 3, "radius": 1.5})
            r.bind_attr(indicator, "bg", lambda: theme_color("primary"))
            r.bind_show(indicator, is_active)
            item.add(indicator)
            indicator_nodes[value] = indicator

            _make_click(r, item, props, value)
            set_a11y(item, role=A11yRole.NAVIGATION, label=str(label),
                     selected=is_active())
            item_nodes.append(item)
            root.add(item)

        root.extra["items"] = item_nodes
        root.extra["icons"] = icon_nodes
        root.extra["indicators"] = indicator_nodes
        root.extra["badges"] = badge_nodes
        set_a11y(root, role=A11yRole.NAVIGATION, label="navigationBar")
        return [root]


class MdNavigationRail(Component):
    """MD3 侧边导航栏（图标 + 可选标签）。"""

    name = "MdNavigationRail"
    props = {"items": [], "active": None, "on_change": None, "width": 80, "bg": None}

    @staticmethod
    def build(r, props, slots):
        items = [_as_dict(it) for it in _as_list(props.get("items"))]
        root = r.element("view", {"dir": "column", "align": "center", "gap": 8,
                                  "padding": [0, 8]})
        r.bind_attr(root, "w", lambda: _num(props.get("width"), 80))
        r.bind_attr(root, "bg", lambda: theme_color("surfaceContainer", props.get("bg")))

        icon_nodes = {}
        pill_nodes = {}
        item_nodes = []

        for it in items:
            value = it.get("value", it.get("label", ""))
            icon = it.get("icon")
            label = it.get("label", "")

            def is_active(v=value):
                return props.get("active") == v

            def pill_bg(v=value):
                if props.get("active") == v:
                    return theme_color("secondaryContainer")
                return "none"

            def icon_color(v=value):
                if props.get("active") == v:
                    return theme_color("onSecondaryContainer")
                return theme_color("onSurfaceVariant")

            item = r.element("view", {
                "dir": "column", "align": "center", "justify": "center",
                "h": 72, "gap": 4,
            })
            r.bind_attr(item, "w", lambda: _num(props.get("width"), 80))

            pill = r.element("view", {
                "dir": "row", "align": "center", "justify": "center",
                "w": 56, "h": 32, "radius": 16,
            })
            r.bind_attr(pill, "bg", pill_bg)
            pill_nodes[value] = pill

            icon_node = _icon(r, icon, 24, icon_color)
            if icon_node is not None:
                pill.add(icon_node)
                icon_nodes[value] = icon_node
            item.add(pill)

            if label:
                item.add(_label(r, lambda v=value: label, "labelMedium",
                                lambda: theme_color("onSurface")))

            _make_click(r, item, props, value)
            set_a11y(item, role=A11yRole.NAVIGATION, label=str(label),
                     selected=is_active())
            item_nodes.append(item)
            root.add(item)

        root.extra["items"] = item_nodes
        root.extra["icons"] = icon_nodes
        root.extra["pills"] = pill_nodes
        set_a11y(root, role=A11yRole.NAVIGATION, label="navigationRail")
        return [root]


# ---------------------------------------------------------------------------
# 标签页
# ---------------------------------------------------------------------------


class MdTabs(Component):
    """MD3 标签页（primary / secondary，固定或滚动）。"""

    name = "MdTabs"
    props = {"tabs": [], "active": None, "variant": "primary",
             "scrollable": False, "on_change": None}

    @staticmethod
    def build(r, props, slots):
        tabs = [_as_dict(t) for t in _as_list(props.get("tabs"))]
        scrollable = bool(props.get("scrollable"))
        root = r.element("view", {"dir": "row", "align": "center"})

        def height_getter():
            return 48.0 if props.get("variant", "primary") == "primary" else 40.0

        r.bind_attr(root, "h", height_getter)

        indicator_nodes = {}
        item_nodes = []
        for tab in tabs:
            value = tab.get("value", tab.get("label", ""))
            icon = tab.get("icon")
            label = tab.get("label", "")

            def is_active(v=value):
                return props.get("active") == v

            def content_color(v=value):
                if props.get("active") == v:
                    return theme_color("primary")
                return theme_color("onSurfaceVariant")

            item = r.element("view", {
                "dir": "column", "align": "center", "h": 48,
            })
            r.bind_attr(item, "h", height_getter)
            if scrollable:
                r.bind_attr(item, "w", lambda: 96)
            else:
                item.props["grow"] = 1

            content = r.element("view", {
                "dir": "column", "align": "center", "justify": "center",
                "grow": 1, "gap": 2,
            })
            if icon:
                icon_node = _icon(r, icon, 24, content_color)
                if icon_node is not None:
                    content.add(icon_node)
            content.add(_label(r, lambda v=value: label, "labelLarge", content_color))
            item.add(content)

            # primary：整段宽指示条；secondary：文字下方短条。
            width = "100%" if props.get("variant", "primary") == "primary" else 32
            indicator = r.element("view", {"w": width, "h": 3, "radius": 1.5})
            r.bind_attr(indicator, "bg", lambda: theme_color("primary"))
            r.bind_show(indicator, is_active)
            item.add(indicator)
            indicator_nodes[value] = indicator

            _make_click(r, item, props, value)
            set_a11y(item, role=A11yRole.NAVIGATION, label=str(label),
                     selected=is_active())
            item_nodes.append(item)
            root.add(item)

        root.extra["items"] = item_nodes
        root.extra["indicators"] = indicator_nodes
        root.extra["indicator_span"] = (
            "full" if props.get("variant", "primary") == "primary" else "text"
        )
        set_a11y(root, role=A11yRole.NAVIGATION, label="tabs")
        return [root]


# ---------------------------------------------------------------------------
# 顶部应用栏
# ---------------------------------------------------------------------------


class MdTopAppBar(Component):
    """MD3 顶部应用栏（small / center / large）。"""

    name = "MdTopAppBar"
    props = {"title": "", "variant": "small", "show_back": False,
             "actions": [], "on_back": None, "on_action": None, "bg": None}

    @staticmethod
    def _action_button(r, props, index, icon_name):
        btn = r.element("view", {
            "dir": "row", "align": "center", "justify": "center",
            "w": 48, "h": 48,
        })
        icon = _icon(r, icon_name, 24, lambda: theme_color("onSurface"))
        if icon is not None:
            btn.add(icon)

        def click(ev):
            callback = props.get("on_action")
            if callable(callback):
                callback(index, ev)

        r.on(btn, "click", lambda c=click: (lambda e=None: c(e)))
        set_a11y(btn, role=A11yRole.BUTTON, label=str(icon_name))
        return btn

    @staticmethod
    def build(r, props, slots):
        variant = props.get("variant", "small")
        large = variant == "large"

        root = r.element("view", {"dir": "column", "padding": [0, 8]})
        r.bind_attr(root, "bg", lambda: theme_color("surface", props.get("bg")))
        r.bind_attr(root, "h", lambda: 128.0 if props.get("variant", "small") == "large" else 64.0)

        top = r.element("view", {
            "dir": "row", "align": "center", "gap": 8,
            "h": 56, "padding": [0, 4],
        })

        back = r.element("view", {
            "dir": "row", "align": "center", "justify": "center",
            "w": 48, "h": 48,
        })
        back_icon = _icon(r, "back", 24, lambda: theme_color("onSurface"))
        if back_icon is not None:
            back.add(back_icon)
        r.bind_show(back, lambda: bool(props.get("show_back")))

        def back_click(ev):
            callback = props.get("on_back")
            if callable(callback):
                callback(ev)
            # 默认不自动 pop：避免误伤页面栈（Navigator.current 可能为 None）。

        r.on(back, "click", lambda c=back_click: (lambda e=None: c(e)))
        set_a11y(back, role=A11yRole.BUTTON, label="back")
        top.add(back)

        size_key = "headlineMedium" if large else "titleLarge"
        title = _label(r, lambda: props.get("title", ""), size_key,
                       lambda: theme_color("onSurface"))
        if large:
            # large：标题沉到底部，单独一行。
            title.props["align"] = "start"
            title_row = r.element("view", {
                "dir": "row", "align": "center", "grow": 1, "padding": [0, 16],
            })
            title_row.add(title)
            root.add(top)
            root.add(title_row)
        else:
            title.props["grow"] = 1
            if variant == "center":
                title.props["align"] = "center"
            top.add(title)
            root.add(top)

        action_nodes = []
        for index, action in enumerate(_as_list(props.get("actions"))):
            icon_name = action.get("icon") if hasattr(action, "get") else str(action)
            btn = MdTopAppBar._action_button(r, props, index, icon_name)
            top.add(btn)
            action_nodes.append(btn)

        root.extra["back"] = back
        root.extra["title"] = title
        root.extra["actions"] = action_nodes
        set_a11y(root, role=A11yRole.NAVIGATION,
                 label=str(props.get("title", "") or "topAppBar"))
        return [root]


# ---------------------------------------------------------------------------
# 卡片
# ---------------------------------------------------------------------------


class MdCard(Component):
    """MD3 卡片（elevated / filled / outlined）。"""

    name = "MdCard"
    props = {"variant": "elevated", "bg": None, "radius": None,
             "padding": 16, "on_click": None, "label": "card"}

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {"dir": "column"})
        r.bind_attr(root, "padding", lambda: props.get("padding", 16))

        def variant():
            return props.get("variant", "elevated")

        def radius_getter():
            override = props.get("radius")
            if override is not None:
                return _num(override, get_shape("medium"))
            return float(get_shape("medium"))

        def bg_getter():
            role = CARD_ROLES.get(variant(), CARD_ROLES["elevated"])
            return theme_color(role, props.get("bg"))

        r.bind_attr(root, "radius", radius_getter)
        r.bind_attr(root, "bg", bg_getter)
        if props.get("variant", "elevated") == "outlined":
            r.bind_attr(root, "border", lambda: (1, theme_color("outline")))

        clickable = props.get("on_click") is not None

        def paint(canvas, box):
            rad = radius_getter()
            if variant() == "elevated":
                soft_shadow(canvas, box, rad)
            if clickable:
                paint_state_layer(canvas, root, props, box, rad,
                                  theme_color("onSurface"))
                if getattr(root, "extra", {}).get("focus"):
                    focus_ring(canvas, box, theme_color("onSurface"), rad)
                paint_ripple(canvas, root, box)

        root.props["paint"] = paint

        if slots.get("default") is not None:
            root.add(r.slot("default"))

        if clickable:
            attach_ripple(root, theme_color("onSurface"), 0.10)
            pressable(r, root, props)
            set_a11y(root, role=A11yRole.BUTTON, label=str(props.get("label", "card")))
        else:
            set_a11y(root, role=A11yRole.TEXT, label=str(props.get("label", "card")))
        return [root]


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------


class MdListItem(Component):
    """MD3 列表项（单行 56dp / 双行 72dp）。"""

    name = "MdListItem"
    props = {"title": "", "subtitle": "", "leading_icon": None,
             "trailing": "", "selected": False, "on_click": None,
             "disabled": False}

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {
            "dir": "row", "align": "center", "gap": 16, "padding": [16, 16],
        })
        r.bind_attr(root, "h", lambda: 72.0 if props.get("subtitle") else 56.0)

        def selected():
            return bool(props.get("selected"))

        def bg_getter():
            return theme_color("secondaryContainer") if selected() else "none"

        def title_color():
            return theme_color("onSecondaryContainer" if selected() else "onSurface")

        def content_color():
            return theme_color("onSecondaryContainer" if selected() else "onSurfaceVariant")

        r.bind_attr(root, "bg", bg_getter)

        leading = _icon(r, props.get("leading_icon"), 24, content_color)
        if leading is not None:
            root.add(leading)

        body = r.element("view", {"dir": "column", "grow": 1, "gap": 2})
        body.add(_label(r, lambda: props.get("title", ""), "bodyLarge", title_color))

        subtitle = _label(r, lambda: props.get("subtitle", ""), "bodySmall", content_color)
        r.bind_show(subtitle, lambda: bool(props.get("subtitle")))
        body.add(subtitle)
        root.add(body)

        root.add(_label(r, lambda: props.get("trailing", ""), "labelSmall", content_color))

        def paint(canvas, box):
            paint_state_layer(canvas, root, props, box, 0.0, content_color())
            paint_ripple(canvas, root, box)

        root.props["paint"] = paint

        if props.get("on_click") is not None:
            attach_ripple(root, content_color(), 0.10)
            pressable(r, root, props)

        set_a11y(root, role=A11yRole.LIST_ITEM, label=str(props.get("title", "")),
                 selected=selected(), disabled=bool(props.get("disabled")))
        return [root]


class MdList(Component):
    """MD3 列表容器（column + 可选分隔线）。"""

    name = "MdList"
    props = {"items": [], "divider": True, "gap": 0}

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {"dir": "column"})
        r.bind_attr(root, "gap", lambda: _num(props.get("gap"), 0))

        items = _as_list(props.get("items"))
        if items:
            for index, item in enumerate(items):
                data = dict(item) if hasattr(item, "items") else {"title": str(item)}
                root.add(r.component_node("MdListItem", data, {}))
                if props.get("divider", True) and index < len(items) - 1:
                    line = r.element("view", {"h": 1})
                    r.bind_attr(line, "bg", lambda: theme_color("outlineVariant"))
                    root.add(line)
        elif slots.get("default") is not None:
            root.add(r.slot("default"))

        set_a11y(root, role=A11yRole.NAVIGATION, label="list")
        return [root]


COMPONENTS = [
    MdNavigationBar, MdNavigationRail, MdTabs, MdTopAppBar,
    MdCard, MdList, MdListItem,
]


def manifest():
    return {
        "abi": 1,
        "name": "vapor.md3.navigation",
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
