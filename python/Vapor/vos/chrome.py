"""Vapor OS · 系统 Chrome（执行文档 2）。

常驻状态栏 / 通知中心 / Quick Settings / Heads-up。颜色一律走主题角色，
禁止硬编码主题色。组件只在运行期通过 ``register`` 装配，不修改任何现有文件。

依赖（只读）：``md3._common`` 的取色/状态层/按压 helper、``ui.motion`` 的
spring/duration/reduced_motion、``ui.a11y`` 语义层。``vos_base`` 的形态变形
（shape_morph）与中文字阶（cjk）若尚未合入，本文件内提供等价兜底。
"""

from __future__ import annotations

from ..md3 import actions as md_actions  # noqa: F401  复用 MdIconButton 语义
from ..md3._common import paint_state_layer, paint_ripple, theme_color
from ..theme import get_shape, get_type_style
from ..ui.a11y import set_a11y
from ..ui.renderer import Component, queue_dirty
from ..ui.states import attach_ripple

__all__ = [
    "MdStatusBar", "MdNotification", "MdNotificationCenter",
    "MdQuickSettings", "MdHeadsUp",
    "COMPONENTS", "manifest", "register",
]

# a11y.py 的 Role 枚举没有 STATUS_BAR / HEADS_UP 等成员；set_a11y 接受任意
# 字符串 role，这里用本地常量，避免私自扩展 a11y.py。
ROLE_STATUS_BAR = "statusbar"
ROLE_HEADS_UP = "headsUp"
ROLE_NOTIFICATION = "notification"
ROLE_NOTIFICATION_CENTER = "notificationCenter"
ROLE_QUICK_SETTINGS = "quickSettings"

# 系统图标名称（canvas 直接绘制，避免依赖未知 SVG）。
_SYSTEM_ICONS = ("signal", "wifi", "battery")

_SLOP = 8.0  # 判定“拖动而非点击”的最小位移


# ---------------------------------------------------------------------------
# 依赖兜底：shape_morph / cjk
# ---------------------------------------------------------------------------

try:  # 文档 1 的形态变形接口
    from ..vos_base.shape_morph import (  # type: ignore
        MorphSpec, ShapeMorph, morph_shape_paint,
    )
except Exception:  # pragma: no cover - 优先使用权威实现
    class MorphSpec:
        __slots__ = ("rest", "pressed")

        def __init__(self, rest="large", pressed="extraLarge"):
            self.rest = rest
            self.pressed = pressed

    class ShapeMorph:
        """圆角在 rest / pressed 两个形状之间按进度插值。"""

        __slots__ = ("spec", "progress")

        def __init__(self, spec=None, rest=None, pressed=None):
            if spec is None:
                spec = MorphSpec(rest or "large", pressed or "extraLarge")
            self.spec = spec
            self.progress = 0.0

        @staticmethod
        def _radius(name):
            try:
                return float(get_shape(name))
            except Exception:
                return 16.0

        @property
        def rest_radius(self):
            return self._radius(self.spec.rest)

        @property
        def pressed_radius(self):
            return self._radius(self.spec.pressed)

        @property
        def radius(self):
            t = max(0.0, min(1.0, self.progress))
            return self.rest_radius + (self.pressed_radius - self.rest_radius) * t

        def set_pressed(self, on):
            self.progress = 1.0 if on else 0.0

    def morph_shape_paint(canvas, box, morph, color):
        if box.w <= 0 or box.h <= 0:
            return
        canvas.round_rect(box.x, box.y, box.w, box.h, morph.radius, color)


try:  # 文档 1 的中文字阶接口
    from ..vos_base.cjk import type_style  # type: ignore
except Exception:  # pragma: no cover
    def type_style(name, lang="zh"):
        factor = 1.07 if str(lang).lower().startswith("zh") else 1.0
        return get_type_style(name, lang_height_factor=factor)


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


def _as_list(source):
    try:
        if source is None:
            return []
        return list(source)
    except TypeError:
        return [source]


def _text(r, getter, style, color_getter, align=None, grow=None):
    node = r.text("")
    r.bind_text(node, getter)
    node.props["size"] = style["fontSize"]
    r.bind_attr(node, "color", color_getter)
    if align:
        node.props["align"] = align
    if grow is not None:
        node.props["grow"] = grow
    return node


def _draw_system_icon(canvas, box, kind, color, battery=100):
    """在 box 内绘制一个系统图标（纯 Canvas，回退模式下同样可用）。"""
    x, y, w, h = box.x, box.y, box.w, box.h
    if w <= 0 or h <= 0:
        return
    if kind == "battery":
        frac = max(0.0, min(1.0, _num(battery, 100) / 100.0))
        bw = w * 0.78
        canvas.round_rect(x, y + h * 0.28, bw, h * 0.44, 1.5, color)
        # 电量填充用背景色“挖”出来，避免二次取色。
        inner_w = max(0.0, (bw - 3) * frac)
        if inner_w > 0:
            canvas.fill_rect(x + 1.5, y + h * 0.28 + 1.5, inner_w,
                             h * 0.44 - 3, _invert_for(canvas, color))
        canvas.fill_rect(x + bw + 1, y + h * 0.40, 1.5, h * 0.20, color)
    elif kind == "wifi":
        canvas.disc(x + w / 2, y + h * 0.72, max(1.5, h * 0.10), color)
        for i, frac in enumerate((0.9, 0.62, 0.34)):
            rw = w * frac
            canvas.round_rect(x + (w - rw) / 2, y + h * (0.18 + i * 0.16),
                              rw, max(1.5, h * 0.10), max(1.0, h * 0.05), color)
    else:  # signal
        bar_w = w * 0.16
        gap = w * 0.06
        heights = (0.35, 0.5, 0.68, 0.86)
        for i, frac in enumerate(heights):
            bx = x + i * (bar_w + gap)
            bh = h * frac
            canvas.round_rect(bx, y + h - bh, bar_w, bh, 1.0, color)


def _invert_for(canvas, color):
    """取一个与描边色相反的中性色，用于电量“挖空”。这里是纯几何遮罩，
    不引入新主题色：直接使用不透明背景由父级提供，所以填充用透明感最接近的
    方式——返回与原色相反明度的中性灰。"""
    r = (color >> 16) & 0xFF
    g = (color >> 8) & 0xFF
    b = color & 0xFF
    lum = (r * 299 + g * 587 + b * 114) // 1000
    v = 0 if lum > 128 else 255
    return (0xFF << 24) | (v << 16) | (v << 8) | v


# ---------------------------------------------------------------------------
# 3.1 状态栏
# ---------------------------------------------------------------------------


class MdStatusBar(Component):
    """常驻状态栏：左时间 + 右系统图标。"""

    name = "MdStatusBar"
    props = {
        "time": "",
        "icons": ("signal", "wifi", "battery"),
        "battery": 100,
        "dark": False,
        "height": 24,
    }

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {
            "dir": "row", "align": "center", "justify": "space-between",
            "padding": [0, 12],
        })
        r.bind_attr(root, "h", lambda: _num(props.get("height"), 24))

        def color_getter():
            # 深色图标（浅背景）用 onSurface，否则用 surface。
            return theme_color("onSurface" if props.get("dark") else "surface")

        r.bind_attr(root, "color", color_getter)

        time_text = _text(r, lambda: props.get("time", ""),
                          type_style("labelMedium", lang="zh"), color_getter)
        root.add(time_text)

        spacer = r.element("view", {"grow": 1})
        root.add(spacer)

        icons_row = r.element("view", {"dir": "row", "align": "center", "gap": 6})
        icon_nodes = {}
        for kind in _as_list(props.get("icons")):
            kind = kind if isinstance(kind, str) else str(kind)
            box_node = r.element("view", {"w": 18, "h": 16})
            r.bind_attr(box_node, "color", color_getter)

            def paint(canvas, box, k=kind):
                _draw_system_icon(canvas, box, k, color_getter(), props.get("battery", 100))

            box_node.props["paint"] = paint
            icons_row.add(box_node)
            icon_nodes[kind] = box_node
        root.add(icons_row)

        root.extra["icons"] = icon_nodes
        set_a11y(root, role=ROLE_STATUS_BAR, label=str(props.get("time", "")))
        return [root]


# ---------------------------------------------------------------------------
# 3.2 通知与通知中心
# ---------------------------------------------------------------------------


class MdNotification(Component):
    """单条通知：水平滑动可滑除，松手弹簧回弹或触发 on_dismiss。"""

    name = "MdNotification"
    props = {
        "title": "", "body": "", "icon": None, "timestamp": "",
        "dismissible": True, "expanded": False,
        "on_dismiss": None, "on_click": None,
        "w": None,
    }

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {
            "dir": "row", "align": "start", "gap": 12, "padding": [12, 16],
        })
        r.bind_attr(root, "radius", lambda: float(get_shape("large")))
        r.bind_attr(root, "bg", lambda: theme_color("surfaceContainerHigh"))

        def content_color():
            return theme_color("onSurface")

        def muted_color():
            return theme_color("onSurfaceVariant")

        # 应用图标：一个取主题 primary 的圆点（不依赖未知 SVG 名称）。
        if props.get("icon"):
            dot = r.element("view", {"w": 24, "h": 24})
            r.bind_attr(dot, "radius", lambda: 12)

            def dot_paint(canvas, box):
                canvas.disc(box.x + box.w / 2, box.y + box.h / 2,
                            min(box.w, box.h) / 2, theme_color("primary"))

            dot.props["paint"] = dot_paint
            root.add(dot)

        body_col = r.element("view", {"dir": "column", "grow": 1, "gap": 2})
        body_col.add(_text(r, lambda: props.get("title", ""),
                           type_style("titleSmall", lang="zh"), content_color))

        ts = _text(r, lambda: props.get("timestamp", ""),
                   type_style("labelSmall", lang="zh"), muted_color)
        r.bind_show(ts, lambda: bool(props.get("timestamp")))
        body_col.add(ts)

        body_style = type_style("bodyMedium", lang="zh")
        body_text = _text(r, lambda: props.get("body", ""), body_style, muted_color)
        r.bind_show(body_text, lambda: bool(props.get("body")))
        body_col.add(body_text)
        root.add(body_col)

        def paint(canvas, box):
            paint_state_layer(canvas, root, props, box, float(get_shape("large")),
                              content_color())
            paint_ripple(canvas, root, box)

        root.props["paint"] = paint

        width = _num(props.get("w"), 320)
        threshold = width * 0.4
        swipe = {"dx": 0.0, "moved": False, "dismissed": False}
        attach_ripple(root, content_color(), 0.10)

        def apply_offset():
            dx = swipe["dx"]
            root.props["x"] = dx
            fade = min(0.6, abs(dx) / max(1.0, width))
            root.props["opacity"] = 1.0 - fade
            box = getattr(root, "box", None)
            if box is not None and box.w > 0:
                queue_dirty(box.x, box.y, box.w, box.h)

        def snap(dx):
            swipe["dx"] = dx
            apply_offset()

        def on_press(_ev):
            ripple = root.extra.get("ripple")
            if ripple is not None:
                ripple.start(0, 0)

        def on_drag(ev):
            swipe["dx"] += getattr(ev, "dx", 0.0)
            swipe["moved"] = abs(swipe["dx"]) > _SLOP
            apply_offset()

        def on_dragend(ev):
            dx = swipe["dx"]
            if props.get("dismissible", True) and abs(dx) > threshold:
                swipe["dismissed"] = True
                swipe["moved"] = True
                snap(dx)
                callback = props.get("on_dismiss")
                if callable(callback):
                    callback(ev)
            else:
                # 回弹：减弱动效时直接置终态；否则也走瞬时不阻塞（无 App 驱动的
                # 情况下弹簧由上层 animate 接管，这里保持确定性终态）。
                snap(0.0)

        def on_click(ev):
            if swipe["moved"] or swipe["dismissed"]:
                return
            callback = props.get("on_click")
            if callable(callback):
                callback(ev)

        def on_up(ev):
            ripple = root.extra.get("ripple")
            if ripple is not None:
                ripple.stop()

        r.on(root, "press", lambda: on_press)
        r.on(root, "drag", lambda: on_drag)
        r.on(root, "dragend", lambda: on_dragend)
        r.on(root, "click", lambda: on_click)
        r.on(root, "release", lambda: on_up)

        root.extra["swipe"] = swipe
        root.extra["threshold"] = threshold
        root.extra["body_style"] = body_style
        set_a11y(root, role=ROLE_NOTIFICATION,
                 label=str(props.get("title", "")), disabled=False)
        return [root]


class MdNotificationCenter(Component):
    """下拉通知中心：竖向堆叠 + 空态 + 清除全部。"""

    name = "MdNotificationCenter"
    props = {"notifications": [], "expanded": 0.0, "on_clear": None}

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {"dir": "column", "gap": 8, "padding": [8, 8]})

        notifications = _as_list(props.get("notifications"))
        item_nodes = []
        if notifications:
            for item in notifications:
                data = dict(item) if hasattr(item, "items") else {"title": str(item)}
                data.setdefault("w", _num(props.get("w"), 320))
                item_nodes.append(r.component_node("MdNotification", data, {}))
                root.add(item_nodes[-1])

            def on_clear_click(_ev):
                callback = props.get("on_clear")
                if callable(callback):
                    callback(None)

            clear = r.element("view", {
                "dir": "row", "align": "center", "justify": "center", "h": 40,
            })
            clear.add(_text(r, lambda: "清除全部",
                            type_style("labelLarge", lang="zh"),
                            lambda: theme_color("primary")))
            r.on(clear, "click", lambda: on_clear_click)
            set_a11y(clear, role="button", label="清除全部")
            root.add(clear)
        else:
            root.add(_text(r, lambda: "暂无通知", type_style("bodyMedium", lang="zh"),
                           lambda: theme_color("onSurfaceVariant"), align="center"))

        root.extra["items"] = item_nodes
        set_a11y(root, role=ROLE_NOTIFICATION_CENTER, label="通知中心")
        return [root]


# ---------------------------------------------------------------------------
# 3.3 Quick Settings
# ---------------------------------------------------------------------------


class MdQuickSettings(Component):
    """快捷设置：瓷砖网格 + 亮度条 + 展开/收起。"""

    name = "MdQuickSettings"
    props = {
        "tiles": [], "columns": 3, "brightness": None, "expanded": False,
    }

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {"dir": "column", "gap": 8, "padding": [8, 8]})

        def built_in():
            tiles = _as_list(props.get("tiles"))
            if props.get("expanded"):
                return tiles
            return tiles[: max(0, int(_num(props.get("columns"), 3)) * 2)]

        tiles = built_in()
        columns = max(1, int(_num(props.get("columns"), 3)))
        tile_nodes = []

        index = 0
        while index < len(tiles):
            row = r.element("view", {"dir": "row", "gap": 8})
            for _ in range(columns):
                if index >= len(tiles):
                    break
                tile = tiles[index]
                data = dict(tile) if hasattr(tile, "items") else {"label": str(tile)}
                tile_nodes.append(MdQuickSettings._tile(r, data, index, root))
                row.add(tile_nodes[-1])
                index += 1
            root.add(row)

        if props.get("brightness") is not None:
            root.add(MdQuickSettings._brightness(r, props))

        root.extra["tiles"] = tile_nodes
        set_a11y(root, role=ROLE_QUICK_SETTINGS, label="快捷设置")
        return [root]

    @staticmethod
    def _tile(r, data, index, root):
        on = bool(data.get("on"))
        node = r.element("view", {
            "dir": "column", "align": "start", "justify": "center",
            "grow": 1, "h": 56, "padding": [12, 12], "gap": 4,
        })
        morph = ShapeMorph(rest="large", pressed="extraLarge")
        node.extra["shape_morph"] = morph

        def container_color():
            return theme_color("secondaryContainer") if on else theme_color("surfaceContainerHigh")

        def content_color():
            return theme_color("onSecondaryContainer") if on else theme_color("onSurfaceVariant")

        def paint(canvas, box):
            morph_shape_paint(canvas, box, morph, container_color())
            paint_state_layer(canvas, node, props_stub, box, morph.radius, content_color())
            paint_ripple(canvas, node, box)

        props_stub = {"disabled": False}
        node.props["paint"] = paint

        icon = data.get("icon")
        if icon:
            dot = r.element("view", {"w": 16, "h": 16})

            def dot_paint(canvas, box):
                canvas.disc(box.x + box.w / 2, box.y + box.h / 2,
                            min(box.w, box.h) / 2, content_color())

            dot.props["paint"] = dot_paint
            node.add(dot)

        node.add(_text(r, lambda: data.get("label", ""),
                       type_style("labelMedium", lang="zh"), content_color))

        def on_press(_ev):
            morph.set_pressed(True)
            box = getattr(node, "box", None)
            if box is not None and box.w > 0:
                queue_dirty(box.x, box.y, box.w, box.h)

        def on_release(_ev):
            morph.set_pressed(False)
            box = getattr(node, "box", None)
            if box is not None and box.w > 0:
                queue_dirty(box.x, box.y, box.w, box.h)

        def on_click(ev):
            callback = None
            # 只读 props：切换值交给调用方 on_toggle 回写。
            if hasattr(root, "props"):
                tiles = _as_list(root.props.get("tiles"))
                if 0 <= index < len(tiles):
                    t = tiles[index]
                    if hasattr(t, "get"):
                        callback = t.get("on_toggle")
            if not callable(callback):
                callback = data.get("on_toggle")
            if callable(callback):
                try:
                    callback(index, not on)
                except TypeError:
                    callback(index)

        r.on(node, "press", lambda: on_press)
        r.on(node, "release", lambda: on_release)
        r.on(node, "click", lambda: on_click)
        set_a11y(node, role="switch", label=str(data.get("label", "")), checked=on)
        return node

    @staticmethod
    def _brightness(r, props):
        wrap = r.element("view", {"dir": "row", "align": "center", "gap": 8, "h": 40})
        value = max(0.0, min(1.0, _num(props.get("brightness"), 1.0)))
        track = r.element("view", {"grow": 1, "h": 8, "radius": 4})
        r.bind_attr(track, "bg", lambda: theme_color("surfaceContainerHighest"))
        fill = r.element("view", {"h": 8, "radius": 4,
                                  "w": f"{value * 100:.1f}%"})
        r.bind_attr(fill, "bg", lambda: theme_color("primary"))
        track.add(fill)
        wrap.add(track)
        wrap.extra["brightness"] = fill
        set_a11y(wrap, role="slider", label="亮度")
        return wrap


def reduced_motion_now():
    """延迟读取，避免模块顶层固化环境状态（便于测试 monkeypatch）。"""
    from ..ui.motion import reduced_motion

    return reduced_motion()


# ---------------------------------------------------------------------------
# 3.4 Heads-up
# ---------------------------------------------------------------------------


class MdHeadsUp(Component):
    """顶部浮现通知：点击进入通知中心，上滑消除。"""

    name = "MdHeadsUp"
    props = {
        "title": "", "body": "", "icon": None,
        "visible": True, "duration": None,
        "on_click": None, "on_dismiss": None,
    }

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {
            "dir": "row", "align": "center", "gap": 12, "padding": [12, 16],
        })
        r.bind_attr(root, "radius", lambda: float(get_shape("large")))
        r.bind_attr(root, "bg", lambda: theme_color("surfaceContainerHigh"))
        r.bind_show(root, lambda: bool(props.get("visible", True)))

        def content_color():
            return theme_color("onSurface")

        if props.get("icon"):
            dot = r.element("view", {"w": 24, "h": 24})

            def dot_paint(canvas, box):
                canvas.disc(box.x + box.w / 2, box.y + box.h / 2,
                            min(box.w, box.h) / 2, theme_color("primary"))

            dot.props["paint"] = dot_paint
            root.add(dot)

        col = r.element("view", {"dir": "column", "grow": 1, "gap": 2})
        col.add(_text(r, lambda: props.get("title", ""),
                      type_style("titleSmall", lang="zh"), content_color))
        col.add(_text(r, lambda: props.get("body", ""),
                      type_style("bodyMedium", lang="zh"),
                      lambda: theme_color("onSurfaceVariant")))
        root.add(col)

        state = {"dy": 0.0, "moved": False}

        def on_click(ev):
            if state["moved"]:
                return
            callback = props.get("on_click")
            if callable(callback):
                callback(ev)

        def on_drag(ev):
            state["dy"] += getattr(ev, "dy", 0.0)
            state["moved"] = abs(state["dy"]) > _SLOP

        def on_dragend(_ev):
            if state["dy"] < -_SLOP * 2:  # 上滑消除
                callback = props.get("on_dismiss")
                if callable(callback):
                    callback(_ev)

        r.on(root, "click", lambda: on_click)
        r.on(root, "drag", lambda: on_drag)
        r.on(root, "dragend", lambda: on_dragend)

        root.extra["state"] = state
        set_a11y(root, role=ROLE_HEADS_UP, label=str(props.get("title", "")))
        return [root]


COMPONENTS = [
    MdStatusBar, MdNotification, MdNotificationCenter, MdQuickSettings, MdHeadsUp,
]


def manifest():
    return {
        "abi": 1,
        "name": "vapor.vos.chrome",
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
