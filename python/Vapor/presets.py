"""Vapor.presets —— 内建现代化圆角组件（全部走底层 Canvas 直接绘制）。

这些组件不依赖任何图片资源：圆角、阴影、圆形、渐变、进度弧都直接写进
rust.Vapor 的共享内存帧缓冲（Canvas -> Vapor._core 光栅化器）。

它们同时是「组件 ABI 的预设」：可以按原样用，也可以被组件扩展继承/组合
（见 Vapor.component；manifest 里声明依赖即可）。

用法::

    import Vapor.presets          # 导入即注册 <Card/> <Fab/> <Chip/> ...
    import Vapor.ui as V

    class Home(V.Component):
        template = '''
          <View padding="20" gap="14">
            <Card>
              <Text size="18">今日步数</Text>
              <Text size="34">8,204</Text>
            </Card>
            <Row gap="10">
              <Chip text="全部" :selected="True" />
              <Chip text="关注" />
            </Row>
            <ProgressRing :value="0.62" :size="72" />
          </View>
        '''
"""

from __future__ import annotations

import math

from . import ui as V

__all__ = [
    "Card", "Fab", "Chip", "SegmentedControl", "Dialog", "Snackbar",
    "Avatar", "ProgressRing", "Skeleton", "ListItem", "AppBar", "BottomSheet",
    "manifest", "register", "PRESETS",
]


# ---------------------------------------------------------------------------
# 绘制辅助
# ---------------------------------------------------------------------------

def _num(value, default):
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _radius(props, default=16.0):
    return _num(props.get("radius"), default)


# 纯 Python 回退光栅化做多层半透明混合非常慢（开发/CI 用），此时跳过阴影；
# 原生 Vapor._core 是 Rust 实现，保留完整阴影效果。
_FALLBACK = bool(getattr(getattr(V, "_core", None), "FALLBACK", False))


def _soft_shadow(canvas, box, radius, color="#000000", layers=6, spread=10, strength=0.10):
    """用多层半透明圆角矩形模拟柔和阴影（纯 Canvas 绘制，无位图）。"""
    if _FALLBACK:
        return
    base = V.color(color)
    for i in range(layers, 0, -1):
        grow = spread * (i / layers)
        alpha = strength * (1.0 - i / (layers + 1.0))
        if alpha <= 0:
            continue
        canvas.round_rect(
            box.x - grow, box.y - grow + 2, box.w + 2 * grow, box.h + 2 * grow,
            radius + grow, V.with_alpha(base, alpha),
        )


def _press_feedback(renderer, node, pressed):
    """按下/抬起维护一个 Ref，供 paint 里做高亮反馈。"""

    def down(ev):
        pressed.value = True

    def up(ev):
        pressed.value = False

    renderer.on(node, "press", lambda: down)
    renderer.on(node, "release", lambda: up)
    renderer.on(node, "cancel", lambda: up)


def _bind_box(renderer, node, props):
    """把 w/h 绑到节点（预设默认 auto，传了就生效）。"""
    renderer.bind_attr(node, "w", lambda: props.get("w"))
    renderer.bind_attr(node, "h", lambda: props.get("h"))


def _forward(renderer, node, props, key, event):
    """把 :on_xxx 属性转发成节点事件。"""

    def make(k):
        def handler(ev):
            callback = props.get(k)
            if callable(callback):
                callback(ev)
        return handler

    if key in props:
        renderer.on(node, event, lambda k=key: make(k))


def _children_or_text(renderer, node, props, slots, fallback_key="text"):
    """有默认插槽就用插槽，否则用 text 属性生成一个文本节点（居中）。"""
    slot = slots.get("default")
    if slot is not None:
        node.add(renderer.slot("default"))
        return
    label = renderer.text("")
    renderer.bind_text(label, lambda: props.get(fallback_key, ""))
    label.props["align"] = "center"
    node.add(label)


# ---------------------------------------------------------------------------
# 组件
# ---------------------------------------------------------------------------

class Card(V.Component):
    """圆角卡片：柔和阴影 + 圆角面板 + 可选渐变/边框 + 默认插槽。"""

    name = "Card"
    props = {"bg": "#161C24", "radius": 16, "padding": 16, "shadow": True}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "column"})
        _bind_box(r, node, props)
        r.bind_attr(node, "padding", lambda: props.get("padding", 16))
        pressed = V.ref(False)
        _press_feedback(r, node, pressed)

        def paint(canvas, box):
            radius = _radius(props, 16)
            if props.get("shadow", True):
                _soft_shadow(canvas, box, radius)
            gradient = props.get("gradient")
            if gradient:
                c1, c2 = gradient[0], gradient[1]
                vertical = len(gradient) < 3 or gradient[2] != "h"
                canvas.round_rect(box.x, box.y, box.w, box.h, radius, V.color(c1))
                canvas.gradient(box.x, box.y, box.w, box.h, V.color(c1), V.color(c2), vertical)
            else:
                canvas.round_rect(box.x, box.y, box.w, box.h, radius,
                                  V.color(props.get("bg", "#161C24")))
            if pressed.value:
                canvas.round_rect(box.x, box.y, box.w, box.h, radius,
                                  V.with_alpha(V.color("#FFFFFF"), 0.06))
            border = props.get("border")
            if border:
                color = border[1] if isinstance(border, (list, tuple)) and len(border) > 1 else border
                width = border[0] if isinstance(border, (list, tuple)) else 1
                for i in range(int(_num(width, 1))):
                    canvas.round_rect(box.x + i, box.y + i, box.w - 2 * i, box.h - 2 * i,
                                      max(0.0, radius - i), V.color(color))

        node.props["paint"] = paint
        node.add(r.slot("default"))
        _forward(r, node, props, "on_press", "press")
        _forward(r, node, props, "on_click", "click")
        return [node]


class Fab(V.Component):
    """圆形浮动按钮：圆形阴影 + 圆形主体 + 按下高亮。"""

    name = "Fab"
    props = {"bg": "#2D7FF9", "color": "#FFFFFF", "size": 56, "text": ""}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "row", "align": "center", "justify": "center"})
        r.bind_attr(node, "w", lambda: _num(props.get("size"), 56))
        r.bind_attr(node, "h", lambda: _num(props.get("size"), 56))
        pressed = V.ref(False)
        _press_feedback(r, node, pressed)

        def paint(canvas, box):
            radius = min(box.w, box.h) / 2.0
            cx, cy = box.x + box.w / 2.0, box.y + box.h / 2.0
            for i in range(5, 0, -1):
                canvas.disc(cx, cy + 1.5, radius + i,
                            V.with_alpha(V.color("#000000"), 0.05 * (1.0 - i / 6.0)))
            canvas.disc(cx, cy, radius, V.color(props.get("bg", "#2D7FF9")))
            if pressed.value:
                canvas.disc(cx, cy, radius, V.with_alpha(V.color("#FFFFFF"), 0.14))

        node.props["paint"] = paint
        slot = slots.get("default")
        if slot is not None:
            node.add(r.slot("default"))
        else:
            label = r.text("")
            r.bind_text(label, lambda: props.get("text", ""))
            r.bind_attr(label, "color", lambda: props.get("color", "#FFFFFF"))
            r.bind_attr(label, "size", lambda: max(12.0, _num(props.get("size"), 56) * 0.36))
            node.add(label)
        _forward(r, node, props, "on_click", "click")
        return [node]


class Chip(V.Component):
    """胶囊标签：可选中、可点击，圆角由高度决定。"""

    name = "Chip"
    props = {"text": "", "selected": False, "accent": "#2D7FF9", "bg": "#1E252E"}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "row", "align": "center", "justify": "center",
                                  "padding": [6, 14]})
        _bind_box(r, node, props)
        pressed = V.ref(False)
        _press_feedback(r, node, pressed)

        def paint(canvas, box):
            radius = box.h / 2.0
            selected = bool(props.get("selected"))
            base = V.color(props.get("accent", "#2D7FF9") if selected else props.get("bg", "#1E252E"))
            canvas.round_rect(box.x, box.y, box.w, box.h, radius, base)
            if pressed.value:
                canvas.round_rect(box.x, box.y, box.w, box.h, radius,
                                  V.with_alpha(V.color("#FFFFFF"), 0.10))

        node.props["paint"] = paint
        label = r.text("")
        r.bind_text(label, lambda: props.get("text", ""))
        r.bind_attr(label, "size", lambda: props.get("size", 13))
        r.bind_attr(label, "color", lambda: "#FFFFFF" if props.get("selected") else props.get("color", "#C7D0DB"))
        node.add(label)
        _forward(r, node, props, "on_click", "click")
        return [node]


class SegmentedControl(V.Component):
    """分段控件：圆角轨道 + 选中滑块（底层圆角矩形绘制）。"""

    name = "SegmentedControl"
    props = {"items": [], "value": 0, "accent": "#2D7FF9"}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "row", "padding": 3})
        _bind_box(r, node, props)
        r.bind_attr(node, "h", lambda: props.get("h", 36))
        r.bind_attr(node, "radius", lambda: props.get("radius", 12))

        def track_paint(canvas, box):
            radius = _num(props.get("radius"), 12)
            canvas.round_rect(box.x, box.y, box.w, box.h, radius, V.color(props.get("bg", "#1A212A")))
        node.props["paint"] = track_paint

        frag = r.fragment()
        node.add(frag)

        def rebuild():
            for child in list(frag.children):
                child.dispose()
                child.remove()
            items = list(props.get("items") or [])
            if not items:
                return
            current = props.get("value", 0)
            grow = 1.0 / len(items)
            for index, item in enumerate(items):
                seg = r.element("view", {"dir": "row", "align": "center", "justify": "center",
                                         "grow": 1, "radius": 10})
                selected = (index == current) or (item == current)
                def paint(canvas, box, is_sel=selected):
                    if is_sel:
                        canvas.round_rect(box.x, box.y, box.w, box.h, 10,
                                          V.color(props.get("accent", "#2D7FF9")))
                seg.props["paint"] = paint
                label = r.text(str(item))
                label.props.update({"size": 13, "align": "center"})
                def color_for(i=index, item=item):
                    current = props.get("value", 0)
                    return "#FFFFFF" if (i == current or str(item) == str(current)) else "#8A94A6"
                r.bind_attr(label, "color", color_for)
                seg.add(label)

                def on_click(ev, i=index, value=item):
                    callback = props.get("on_change")
                    if callable(callback):
                        callback(value, i)
                    props["value"] = value

                r.on(seg, "click", lambda f=on_click: f)
                frag.add(seg)
            del grow

        r._new_effect(rebuild)
        return [node]


class Dialog(V.Component):
    """模态对话框：半透明遮罩 + 圆角面板 + 标题/内容/操作区。"""

    name = "Dialog"
    props = {
        "visible": False, "title": "", "bg": "#1A212A", "radius": 20, "panel_w": "84%",
        # 组件节点本身铺满父容器，内部再用 100% 对齐
        "absolute": True, "x": 0, "y": 0, "w": "100%", "h": "100%",
    }

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {"w": "100%", "h": "100%",
                                  "dir": "row", "align": "center", "justify": "center"})
        r.bind_show(root, lambda: bool(props.get("visible")))

        def scrim(canvas, box):
            canvas.fill_rect(box.x, box.y, box.w, box.h, V.with_alpha(V.color("#000000"), 0.55))
        root.props["paint"] = scrim

        panel = r.element("view", {"dir": "column", "padding": 20, "gap": 12, "w": "84%"})
        r.bind_attr(panel, "w", lambda: props.get("panel_w", "84%"))
        r.bind_attr(panel, "radius", lambda: _num(props.get("radius"), 20))

        def panel_paint(canvas, box):
            radius = _num(props.get("radius"), 20)
            _soft_shadow(canvas, box, radius, strength=0.16, spread=16)
            canvas.round_rect(box.x, box.y, box.w, box.h, radius,
                              V.color(props.get("bg", "#1A212A")))
        panel.props["paint"] = panel_paint

        title = r.text("")
        r.bind_text(title, lambda: props.get("title", ""))
        r.bind_attr(title, "size", lambda: props.get("title_size", 18))
        r.bind_attr(title, "color", lambda: "#E6EAF0")
        panel.add(title)

        content = r.element("view", {"dir": "column", "gap": 8})
        content.add(r.slot("default"))
        panel.add(content)

        actions = r.element("view", {"dir": "row", "justify": "end", "gap": 8})
        actions.add(r.slot("actions"))
        panel.add(actions)

        def close(ev):
            callback = props.get("on_close")
            if callable(callback):
                callback(ev)
            else:
                props["visible"] = False

        def swallow(ev):
            ev.stop()

        r.on(panel, "click", lambda: swallow)
        r.on(root, "click", lambda: close)
        root.add(panel)
        return [root]


class Snackbar(V.Component):
    """底部提示条：圆角深色条 + 文本 + 可选操作。"""

    name = "Snackbar"
    props = {"text": "", "visible": True, "bg": "#222A34", "action": ""}

    @staticmethod
    def build(r, props, slots):
        bar = r.element("view", {"dir": "row", "align": "center", "padding": [12, 16],
                                 "gap": 12, "w": "100%"})
        _bind_box(r, bar, props)
        r.bind_show(bar, lambda: bool(props.get("visible")))

        def paint(canvas, box):
            radius = min(14.0, box.h / 2.0)
            _soft_shadow(canvas, box, radius, layers=4, spread=8, strength=0.08)
            canvas.round_rect(box.x, box.y, box.w, box.h, radius,
                              V.color(props.get("bg", "#222A34")))
        bar.props["paint"] = paint

        label = r.text("")
        r.bind_text(label, lambda: props.get("text", ""))
        label.props.update({"size": 13, "color": "#E6EAF0", "grow": 1})
        bar.add(label)

        action = r.text("")
        r.bind_text(action, lambda: props.get("action", ""))
        action.props.update({"size": 13, "color": "#7BD88F"})
        r.on(action, "click", lambda: (props.get("on_action") or (lambda ev: None))(None))
        bar.add(action)
        return [bar]


class Avatar(V.Component):
    """圆形头像：圆形底色 + 首字母/文字 + 可选描边。"""

    name = "Avatar"
    props = {"text": "", "size": 40, "bg": "#2A3542", "ring": None}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "row", "align": "center", "justify": "center"})
        r.bind_attr(node, "w", lambda: _num(props.get("size"), 40))
        r.bind_attr(node, "h", lambda: _num(props.get("size"), 40))

        def paint(canvas, box):
            radius = min(box.w, box.h) / 2.0
            cx, cy = box.x + box.w / 2.0, box.y + box.h / 2.0
            canvas.disc(cx, cy, radius, V.color(props.get("bg", "#2A3542")))
            ring = props.get("ring")
            if ring:
                canvas.ring(cx, cy, radius - 1, V.color(ring), 2)
        node.props["paint"] = paint

        label = r.text("")
        r.bind_text(label, lambda: props.get("text", ""))
        r.bind_attr(label, "size", lambda: max(11.0, _num(props.get("size"), 40) * 0.4))
        r.bind_attr(label, "color", lambda: props.get("color", "#E6EAF0"))
        node.add(label)
        return [node]


class ProgressRing(V.Component):
    """环形进度：底环 + 进度弧（折线拟合）+ 中心百分比。"""

    name = "ProgressRing"
    props = {"value": 0.0, "size": 64, "width": 6, "color": "#7BD88F", "bg": "#232B35"}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "row", "align": "center", "justify": "center"})
        r.bind_attr(node, "w", lambda: _num(props.get("size"), 64))
        r.bind_attr(node, "h", lambda: _num(props.get("size"), 64))

        def paint(canvas, box):
            radius = min(box.w, box.h) / 2.0 - _num(props.get("width"), 6) / 2.0
            cx, cy = box.x + box.w / 2.0, box.y + box.h / 2.0
            width = int(max(1.0, _num(props.get("width"), 6)))
            canvas.ring(cx, cy, radius, V.color(props.get("bg", "#232B35")), width)

            value = max(0.0, min(1.0, _num(props.get("value"), 0.0)))
            if value <= 0:
                return
            steps = max(8, int(96 * value))
            points = []
            for i in range(steps + 1):
                angle = math.radians(-90.0 + 360.0 * value * (i / steps))
                points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
            canvas.polyline(points, V.color(props.get("color", "#7BD88F")), width, False)

        node.props["paint"] = paint
        label = r.text("")
        r.bind_text(label, lambda: f"{int(max(0.0, min(1.0, _num(props.get('value'), 0.0))) * 100)}%")
        r.bind_attr(label, "size", lambda: max(10.0, _num(props.get("size"), 64) * 0.22))
        r.bind_attr(label, "color", lambda: "#E6EAF0")
        node.add(label)
        return [node]


class Skeleton(V.Component):
    """骨架屏占位：圆角渐变块。"""

    name = "Skeleton"
    props = {"w": "100%", "h": 14, "radius": 7}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "column"})
        r.bind_attr(node, "w", lambda: props.get("w", "100%"))
        r.bind_attr(node, "h", lambda: props.get("h", 14))

        def paint(canvas, box):
            radius = _num(props.get("radius"), 7)
            c1 = V.color(props.get("color", "#1C242E"))
            c2 = V.color(props.get("highlight", "#26303C"))
            canvas.round_rect(box.x, box.y, box.w, box.h, radius, c1)
            canvas.gradient(box.x, box.y, box.w, box.h, c1, c2, True)
        node.props["paint"] = paint
        return [node]


class ListItem(V.Component):
    """列表项：圆角行 + 前置/后置插槽 + 标题/副标题 + 按下高亮。"""

    name = "ListItem"
    props = {"title": "", "subtitle": "", "radius": 12, "bg": None}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "row", "align": "center", "padding": [12, 12], "gap": 12})
        _bind_box(r, node, props)
        pressed = V.ref(False)
        _press_feedback(r, node, pressed)

        def paint(canvas, box):
            radius = _num(props.get("radius"), 12)
            bg = props.get("bg")
            if bg:
                canvas.round_rect(box.x, box.y, box.w, box.h, radius, V.color(bg))
            if pressed.value:
                canvas.round_rect(box.x, box.y, box.w, box.h, radius,
                                  V.with_alpha(V.color("#FFFFFF"), 0.06))
        node.props["paint"] = paint

        leading = r.element("view", {"dir": "column", "align": "center", "justify": "center"})
        leading.add(r.slot("leading"))
        node.add(leading)

        body = r.element("view", {"dir": "column", "gap": 2, "grow": 1})
        title = r.text("")
        r.bind_text(title, lambda: props.get("title", ""))
        title.props.update({"size": 15, "color": "#E6EAF0"})
        body.add(title)
        subtitle = r.text("")
        r.bind_text(subtitle, lambda: props.get("subtitle", ""))
        subtitle.props.update({"size": 12, "color": "#8A94A6"})
        r.bind_show(subtitle, lambda: bool(props.get("subtitle")))
        body.add(subtitle)
        node.add(body)

        trailing = r.element("view", {"dir": "row", "align": "center"})
        trailing.add(r.slot("trailing"))
        node.add(trailing)

        _forward(r, node, props, "on_click", "click")
        return [node]


class AppBar(V.Component):
    """顶部应用栏：圆角条 + 标题 + 可选返回。"""

    name = "AppBar"
    props = {"title": "", "show_back": False, "bg": "#151A21", "radius": 0}

    @staticmethod
    def build(r, props, slots):
        bar = r.element("view", {"dir": "row", "align": "center", "h": 56,
                                 "padding": [0, 12], "gap": 8})
        _bind_box(r, bar, props)

        def paint(canvas, box):
            radius = _num(props.get("radius"), 0)
            if radius > 0:
                canvas.round_rect(box.x, box.y, box.w, box.h, radius,
                                  V.color(props.get("bg", "#151A21")))
            else:
                canvas.fill_rect(box.x, box.y, box.w, box.h, V.color(props.get("bg", "#151A21")))
        bar.props["paint"] = paint

        back = r.element("view", {"dir": "row", "align": "center", "justify": "center",
                                  "w": 40, "h": 40})
        r.bind_show(back, lambda: bool(props.get("show_back")))
        back_label = r.text("‹")
        back_label.props.update({"size": 26, "color": "#E6EAF0"})
        back.add(back_label)
        r.on(back, "click", lambda: (props.get("on_back") or (lambda ev: None))(None))
        bar.add(back)

        title = r.text("")
        r.bind_text(title, lambda: props.get("title", ""))
        title.props.update({"size": 18, "color": "#E6EAF0", "grow": 1})
        bar.add(title)
        bar.add(r.slot("default"))
        return [bar]


class BottomSheet(V.Component):
    """底部面板：上圆角 + 拖拽把手 + 遮罩。"""

    name = "BottomSheet"
    props = {
        "visible": False, "radius": 24, "bg": "#1A212A", "height": None,
        "absolute": True, "x": 0, "y": 0, "w": "100%", "h": "100%",
    }

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {"w": "100%", "h": "100%",
                                  "dir": "column", "justify": "end"})
        r.bind_show(root, lambda: bool(props.get("visible")))

        def scrim(canvas, box):
            canvas.fill_rect(box.x, box.y, box.w, box.h, V.with_alpha(V.color("#000000"), 0.45))
        root.props["paint"] = scrim

        panel = r.element("view", {"dir": "column", "padding": [16, 12, 16, 20], "gap": 10,
                                   "w": "100%"})
        r.bind_attr(panel, "h", lambda: props.get("height") or "auto")

        def paint(canvas, box):
            radius = _num(props.get("radius"), 24)
            _soft_shadow(canvas, box, radius, strength=0.14, spread=14, layers=5)
            canvas.round_rect(box.x, box.y, box.w, box.h, radius,
                              V.color(props.get("bg", "#1A212A")))
            # 顶部拖拽把手
            handle_w = 36.0
            canvas.round_rect(box.x + (box.w - handle_w) / 2, box.y + 6, handle_w, 4, 2,
                              V.with_alpha(V.color("#FFFFFF"), 0.22))
        panel.props["paint"] = paint

        def swallow(ev):
            ev.stop()

        r.on(panel, "click", lambda: swallow)
        panel.add(r.slot("default"))
        root.add(panel)
        return [root]


# ---------------------------------------------------------------------------
# 注册 + 组件 ABI 清单
# ---------------------------------------------------------------------------

PRESETS = (
    Card, Fab, Chip, SegmentedControl, Dialog, Snackbar,
    Avatar, ProgressRing, Skeleton, ListItem, AppBar, BottomSheet,
)

for _cls in PRESETS:
    V.register(_cls)


def manifest():
    """作为组件提供者的清单：扩展可以据此声明依赖/继承这些预设。"""
    return {
        "abi": 1,
        "name": "vapor.presets",
        "version": "1.0.0",
        "components": [
            {"name": c.name, "props": dict(c.props), "description": (c.__doc__ or "").strip().splitlines()[0]}
            for c in PRESETS
        ],
    }


def register(host):
    for cls in PRESETS:
        host.register(cls)


