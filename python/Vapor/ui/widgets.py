"""内置组件：容器、文本、按钮、图标、表单控件、页面。"""

from __future__ import annotations

from .. import core
from .. import svg as svg_mod
from .base import Node
from .renderer import Component, _warn, register
from .navigation import Navigator

__all__ = [
    "View", "Row", "Column", "Center", "Stack", "Spacer", "Divider", "Text",
    "Button", "Icon", "Svg", "ProgressBar", "Badge", "Switch", "Slider",
    "Scroll", "ListView", "TextInput", "Page",
]


# 这些属性描述「组件自身在父容器中的位置」，不应下传给组件内部的根元素
_LAYOUT_OWN = frozenset({
    "absolute", "x", "y", "grow", "align", "stretch",
    "margin", "margin_x", "margin_y", "justify",
})


def _bind_pointer_callbacks(renderer, node, props):
    """把 on_press / on_release / on_hover / on_leave 绑到节点事件上。"""

    def make(key):
        def handler(ev):
            callback = props.get(key)
            if callable(callback):
                callback(ev)
        return handler

    for event, key in (
        ("press", "on_press"),
        ("release", "on_release"),
        ("hover", "on_hover"),
        ("leave", "on_leave"),
    ):
        if key in props:
            renderer.on(node, event, lambda k=key: make(k))


def _bind_all(renderer, node, props, skip=()):
    """把组件 props 上的动态值绑到元素节点（每个 key 一个 effect）。"""
    skip_keys = set(skip) | _LAYOUT_OWN
    for key in list(props.keys()):
        if key in skip_keys:
            continue
        renderer.bind_attr(node, key, lambda k=key: props.get(k))


_SVG_CACHE: dict = {}


def _svg_doc(source):
    """按 source / 图标名取 SvgDoc（缓存解析结果）。"""
    doc = _SVG_CACHE.get(source)
    if doc is not None:
        return doc
    if isinstance(source, str) and source.strip().startswith("<"):
        text = source
    elif source in svg_mod.ICONS:
        text = svg_mod.ICONS[source]
    else:
        raise ValueError(f"未知 SVG: {source!r}")
    doc = svg_mod.parse_svg(text)
    _SVG_CACHE[source] = doc
    return doc


class View(Component):
    """通用容器（Android 的 LinearLayout + 背景 + 圆角）。"""

    name = "View"
    props = {}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": props.get("dir", "column")})
        _bind_all(r, node, props)
        _bind_pointer_callbacks(r, node, props)
        node.add(r.slot("default"))
        return [node]


class Row(View):
    name = "Row"

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "row", "align": "center"})
        _bind_all(r, node, props)
        node.add(r.slot("default"))
        return [node]


class Column(View):
    name = "Column"

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "column"})
        _bind_all(r, node, props)
        node.add(r.slot("default"))
        return [node]


class Center(View):
    """居中容器（主轴与交叉轴都居中）。"""

    name = "Center"

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": props.get("dir", "column"), "justify": "center", "align": "center"})
        _bind_all(r, node, props)
        node.add(r.slot("default"))
        return [node]


class Stack(View):
    """层叠容器：子节点全部绝对定位在 (0,0)。"""

    name = "Stack"

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "column"})
        _bind_all(r, node, props)
        slot = r.slot("default")
        for child in list(slot.children):
            child.props.setdefault("absolute", True)
            child.props.setdefault("x", 0)
            child.props.setdefault("y", 0)
        node.add(slot)
        return [node]


class Spacer(View):
    name = "Spacer"

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"grow": 1})
        _bind_all(r, node, props)
        node.props["grow"] = props.get("grow", 1)
        return [node]


class Divider(View):
    name = "Divider"

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {})
        _bind_all(r, node, props)
        node.props.setdefault("h", props.get("h", 1))
        node.props.setdefault("w", props.get("w", "100%"))
        node.props.setdefault("bg", props.get("bg", "#252B33"))
        return [node]


class Text(Component):
    """文本节点：插值/绑定都直接写 node.text，不重建节点。"""

    name = "Text"
    props = {"text": "", "size": 16, "color": "#E6EAF0"}

    @staticmethod
    def build(r, props, slots):
        node = r.text("")
        slot = slots.get("default")
        slot_getter = slot.text_getter() if slot is not None else None
        if slot_getter is not None:
            # <Text>{{ msg }}</Text>：插槽里的纯文本直接作为文本绑定
            r.bind_text(node, slot_getter)
        else:
            r.bind_text(node, lambda: props.get("text", ""))
        for key in ("size", "color", "align", "wrap", "max_lines", "opacity", "letter_spacing"):
            if key in props:
                r.bind_attr(node, key, lambda k=key: props.get(k))
        # 有显式宽度时让文本节点填满宽度，wrap 才有折行依据
        r.bind_attr(node, "stretch", lambda: props.get("w") is not None)
        return [node]


class Button(Component):
    """按钮：背景 + 文本 + 点击；支持 default 插槽替换内容。"""

    name = "Button"
    props = {"text": "", "bg": "#2D7FF9", "color": "#FFFFFF", "radius": 12, "size": 16, "disabled": False}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "row", "align": "center", "justify": "center", "padding": [12, 20]})
        _bind_all(r, node, props, skip=("text", "size", "color"))

        if slots.get("default") is not None:
            node.add(r.slot("default"))
        else:
            label = r.text("")
            r.bind_text(label, lambda: props.get("text", ""))
            r.bind_attr(label, "size", lambda: props.get("size", 16))
            r.bind_attr(label, "color", lambda: props.get("color", "#FFFFFF"))
            node.add(label)

        def handler(ev):
            if props.get("disabled"):
                return
            callback = props.get("on_click")
            if callable(callback):
                callback(ev)

        pressed = core.ref(False)

        def on_down(ev):
            if not props.get("disabled"):
                pressed.value = True

        def on_up(ev):
            pressed.value = False

        r.on(node, "click", lambda: handler)
        r.on(node, "pointerdown", lambda: on_down)
        r.on(node, "pointerup", lambda: on_up)
        r.bind_attr(
            node,
            "opacity",
            lambda: 0.5 if props.get("disabled") else (0.82 if pressed.value else 1.0),
        )
        return [node]


class Icon(Component):
    """SVG 图标组件（内置 Material 风格图标，也可传 src 源码）。"""

    name = "Icon"
    props = {"name": "home", "size": 24, "color": "#E6EAF0"}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"w": props.get("size", 24), "h": props.get("size", 24)})
        r.bind_attr(node, "w", lambda: props.get("size", 24))
        r.bind_attr(node, "h", lambda: props.get("size", 24))

        def paint(canvas, box):
            try:
                doc = _svg_doc(props.get("src") or props.get("name") or "home")
            except ValueError as exc:
                _warn(f"Icon: {exc}")
                return
            doc.render(canvas, box.x, box.y, box.w, box.h, color=props.get("color", "#E6EAF0"))

        node.props["paint"] = paint

        def handler(ev):
            callback = props.get("on_click")
            if callable(callback):
                callback(ev)

        r.on(node, "click", lambda: handler)
        return [node]


class Svg(Component):
    """把任意 SVG 当组件渲染（矢量，非位图）。"""

    name = "Svg"
    props = {"source": "home", "w": 24, "h": 24, "color": None}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"w": props.get("w", 24), "h": props.get("h", 24)})
        r.bind_attr(node, "w", lambda: props.get("w", 24))
        r.bind_attr(node, "h", lambda: props.get("h", 24))

        def paint(canvas, box):
            try:
                doc = _svg_doc(props.get("source") or "home")
            except ValueError as exc:
                _warn(f"Svg: {exc}")
                return
            doc.render(canvas, box.x, box.y, box.w, box.h, color=props.get("color"))

        node.props["paint"] = paint
        return [node]


class ProgressBar(Component):
    name = "ProgressBar"
    props = {"value": 0.0, "color": "#2D7FF9", "bg": "#232A33", "h": 6, "radius": 3}

    @staticmethod
    def build(r, props, slots):
        track = r.element("view", {"w": "100%", "radius": props.get("radius", 3)})
        r.bind_attr(track, "h", lambda: props.get("h", 6))
        r.bind_attr(track, "bg", lambda: props.get("bg", "#232A33"))
        r.bind_attr(track, "radius", lambda: props.get("radius", 3))

        fill = r.element("view", {"absolute": True, "x": 0, "y": 0, "h": "100%", "radius": props.get("radius", 3)})
        r.bind_attr(fill, "bg", lambda: props.get("color", "#2D7FF9"))
        r.bind_attr(fill, "w", lambda: f"{max(0.0, min(1.0, float(props.get('value') or 0))) * 100:.2f}%")
        track.add(fill)
        return [track]


class Badge(Component):
    name = "Badge"
    props = {"text": "", "bg": "#E5484D", "color": "#FFFFFF", "size": 12}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "row", "align": "center", "justify": "center", "padding": [2, 8], "radius": 10})
        _bind_all(r, node, props, skip=("text", "size", "color"))
        label = r.text("")
        r.bind_text(label, lambda: props.get("text", ""))
        r.bind_attr(label, "size", lambda: props.get("size", 12))
        r.bind_attr(label, "color", lambda: props.get("color", "#FFFFFF"))
        node.add(label)
        return [node]


class Switch(Component):
    """开关：点击切换 value，并通过 on_change 回调新值。"""

    name = "Switch"
    props = {"value": False, "color": "#2D7FF9", "w": 46, "h": 26}

    @staticmethod
    def build(r, props, slots):
        track = r.element("view", {"radius": 13, "w": 46, "h": 26})
        r.bind_attr(track, "w", lambda: props.get("w", 46))
        r.bind_attr(track, "h", lambda: props.get("h", 26))
        r.bind_attr(track, "radius", lambda: float(props.get("h", 26)) / 2)
        r.bind_attr(track, "bg", lambda: props.get("color", "#2D7FF9") if props.get("value") else "#2A3038")

        knob = r.element("view", {"absolute": True, "y": 3, "radius": 10, "bg": "#FFFFFF"})
        r.bind_attr(knob, "w", lambda: float(props.get("h", 26)) - 6)
        r.bind_attr(knob, "h", lambda: float(props.get("h", 26)) - 6)
        r.bind_attr(knob, "radius", lambda: (float(props.get("h", 26)) - 6) / 2)
        r.bind_attr(knob, "x", lambda: (float(props.get("w", 46)) - float(props.get("h", 26)) + 3) if props.get("value") else 3)
        track.add(knob)

        def handler(ev):
            new_value = not bool(props.get("value"))
            callback = props.get("on_change")
            if callable(callback):
                callback(new_value)
            else:
                props["value"] = new_value

        r.on(track, "click", lambda: handler)
        return [track]


class Slider(Component):
    """滑块：点击轨道按位置取值（0..1）。"""

    name = "Slider"
    props = {"value": 0.0, "color": "#2D7FF9", "w": "100%", "h": 32}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"w": "100%", "h": 32})
        r.bind_attr(node, "w", lambda: props.get("w", "100%"))
        r.bind_attr(node, "h", lambda: props.get("h", 32))

        track = r.element("view", {"absolute": True, "x": 0, "y": 14, "w": "100%", "h": 4, "radius": 2, "bg": "#2A3038"})
        fill = r.element("view", {"absolute": True, "x": 0, "y": 14, "h": 4, "radius": 2})
        r.bind_attr(fill, "bg", lambda: props.get("color", "#2D7FF9"))
        r.bind_attr(fill, "w", lambda: f"{max(0.0, min(1.0, float(props.get('value') or 0))) * 100:.2f}%")

        knob = r.element("view", {"absolute": True, "y": 8, "w": 16, "h": 16, "radius": 8, "bg": "#FFFFFF"})
        r.bind_attr(knob, "x", lambda: f"{max(0.0, min(1.0, float(props.get('value') or 0))) * 100:.2f}%")
        node.props["_knob"] = knob

        node.add(track)
        node.add(fill)
        node.add(knob)

        def handler(ev):
            box = node.box
            if box.w <= 0:
                return
            ratio = max(0.0, min(1.0, (ev.x - box.x) / box.w))
            callback = props.get("on_change")
            if callable(callback):
                callback(ratio)
            else:
                props["value"] = ratio

        r.on(node, "click", lambda: handler)
        r.on(node, "pointerdown", lambda: handler)
        r.on(node, "drag", lambda: handler)
        return [node]


class Scroll(Component):
    """滚动容器：拖动滚动 + 抬手惯性（由 PointerRouter 驱动 offset）。

    也可以用 scroll_y 属性从外部控制（与手势叠加）；可滚动范围按
    「内容高度 - 视口高度」自动计算并做边界钳制。
    """

    name = "Scroll"
    props = {"scroll_y": 0}

    @staticmethod
    def build(r, props, slots):
        viewport = r.element("view", {"dir": "column", "clip": True})
        _bind_all(r, viewport, props, skip=("scroll_y",))
        viewport.clip = True

        content = r.element("view", {"dir": "column"})
        content.add(r.slot("default"))
        viewport.add(content)

        # offset 是手势层直接改的（0=顶部，正值=内容上移），scroll_y 属性可做外部控制
        offset = core.ref(0.0)
        viewport.extra["scroll"] = {"offset": offset}

        def apply_scroll():
            viewport.scroll_y = float(props.get("scroll_y") or 0) + offset.value

        r._new_effect(apply_scroll)
        return [viewport]


class ListView(Component):
    """列表（非 keyed 版本）。需要精确复用请用模板里的 v-for（keyed）。"""

    name = "ListView"
    props = {"items": [], "gap": 8, "dir": "column"}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "column"})
        r.bind_attr(node, "dir", lambda: props.get("dir", "column"))
        r.bind_attr(node, "gap", lambda: props.get("gap", 8))
        frag = r.fragment()
        node.add(frag)

        state = {"items": []}

        def apply():
            items = list(props.get("items") or [])
            render_item = props.get("render_item")
            if not callable(render_item):
                return
            for child in list(frag.children):
                child.dispose()
                child.remove()
            for index, item in enumerate(items):
                out = render_item(item, index, r)
                if out is None:
                    continue
                nodes = out if isinstance(out, (list, tuple)) else [out]
                frag.add(*nodes)
            state["items"] = items

        r._new_effect(apply)
        return [node]


class TextInput(Component):
    """单行输入框（展示型；输入法由扩展/宿主接入）。"""

    name = "TextInput"
    props = {"value": "", "placeholder": "请输入", "bg": "#171D25", "color": "#E6EAF0", "radius": 10}

    @staticmethod
    def build(r, props, slots):
        box = r.element("view", {"dir": "row", "align": "center", "padding": [12, 14], "radius": 10})
        _bind_all(r, box, props, skip=("value", "placeholder", "color"))
        label = r.text("")
        r.bind_text(label, lambda: props.get("value") or props.get("placeholder") or "")
        r.bind_attr(label, "size", lambda: props.get("size", 15))
        r.bind_attr(label, "color", lambda: props.get("color", "#E6EAF0") if props.get("value") else "#6B7683")
        box.add(label)

        def handler(ev):
            callback = props.get("on_focus")
            if callable(callback):
                callback(ev)

        r.on(box, "click", lambda: handler)
        return [box]


class Page(Component):
    """一个「Android 页面」：可选标题栏 + 返回按钮 + 内容区。"""

    name = "Page"
    props = {"title": "", "show_bar": True, "bg": "#0B0E13", "bar_bg": "#151A21"}

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {"dir": "column", "w": "100%", "h": "100%", "bg": "#0B0E13"})
        r.bind_attr(root, "bg", lambda: props.get("bg", "#0B0E13"))

        if props.get("show_bar", True):
            bar = r.element("view", {"dir": "row", "align": "center", "h": 56, "padding": [0, 12], "gap": 8})
            r.bind_attr(bar, "bg", lambda: props.get("bar_bg", "#151A21"))
            back = r.element("view", {"w": 40, "h": 40, "dir": "row", "align": "center", "justify": "center"})
            back_label = r.text("‹")
            back_label.props.update({"size": 26, "color": "#E6EAF0"})
            back.add(back_label)

            def on_back(ev):
                callback = props.get("on_back")
                if callable(callback):
                    callback(ev)
                navigator = Navigator.current
                if navigator is not None:
                    navigator.pop()

            r.on(back, "click", lambda: on_back)
            bar.add(back)

            title = r.text("")
            r.bind_text(title, lambda: props.get("title", ""))
            title.props.update({"size": 18, "color": "#E6EAF0", "grow": 1})
            bar.add(title)
            root.add(bar)

        content = r.element("view", {"dir": "column", "grow": 1, "w": "100%"})
        content.add(r.slot("default"))
        root.add(content)
        return [root]


for _c in (
    View, Row, Column, Center, Stack, Spacer, Divider, Text, Button, Icon, Svg,
    ProgressBar, Badge, Switch, Slider, Scroll, ListView, TextInput, Page,
):
    register(_c)
