"""Vapor OS · 桌面与锁屏（执行文档 3）。

锁屏 / 桌面 App Grid / 手势导航 / 最近任务。颜色一律走主题角色，禁止硬编码
主题色；路由与数据重排一律通过回调交给调用方（高自由）。只新建本文件，
不修改任何现有文件（``vos/__init__.py`` 归文档 4）。
"""

from __future__ import annotations

from ..md3._common import paint_ripple, paint_state_layer, theme_color
from ..theme import get_shape, get_type_style
from ..ui import go_back, navigate  # noqa: F401  路由归调用方，本文件不调用
from ..ui.a11y import set_a11y
from ..ui.renderer import Component, queue_dirty
from ..ui.states import attach_ripple
from .chrome import MdNotification, MdStatusBar  # noqa: F401  只读复用文档 2

__all__ = [
    "MdLockScreen", "MdAppGrid", "MdAppIcon",
    "MdGestureNav", "MdRecents", "MdTaskCard",
    "COMPONENTS", "manifest", "register",
]

ROLE_LOCK_SCREEN = "lockScreen"
ROLE_GESTURE_NAV = "gestureNav"
ROLE_RECENTS = "recents"

_SLOP = 8.0  # 判定“拖动而非点击”的最小位移


# ---------------------------------------------------------------------------
# 依赖兜底：shape_morph / cjk（vos_base 未合入时回退到 chrome 的等价实现）
# ---------------------------------------------------------------------------

try:  # 文档 1 的形态变形接口
    from ..vos_base.shape_morph import MorphSpec, ShapeMorph  # type: ignore
except Exception:  # pragma: no cover
    from .chrome import MorphSpec, ShapeMorph  # type: ignore

try:  # 文档 1 的中文字阶接口（需支持 emphasized）
    from ..vos_base.cjk import type_style  # type: ignore
except Exception:  # pragma: no cover
    def type_style(name, emphasized=False, lang="zh"):
        factor = 1.07 if str(lang).lower().startswith("zh") else 1.0
        return get_type_style(name, emphasized=emphasized, lang_height_factor=factor)


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


def _morph_shape(canvas, box, morph, color):
    if box.w > 0 and box.h > 0:
        canvas.round_rect(box.x, box.y, box.w, box.h,
                          morph.radius(min(box.w, box.h)), color)


# ---------------------------------------------------------------------------
# 3.1 锁屏
# ---------------------------------------------------------------------------


class MdLockScreen(Component):
    """锁屏：状态栏 + 中文强调时钟 + 通知预览 + widget 槽 + 上滑解锁。"""

    name = "MdLockScreen"
    props = {
        "time": "", "date": "", "notifications": [], "widgets": [],
        "locked": True, "on_unlock": None, "on_widget": None, "height": 800,
    }

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {"dir": "column", "gap": 20, "padding": [0, 16]})
        r.bind_attr(root, "bg", lambda: theme_color("surface"))

        # 顶部状态栏（只读复用文档 2）。
        root.add(r.component_node("MdStatusBar", {
            "time": props.get("time", ""),
            "dark": False,
        }, {}))

        clock_style = type_style("displayLarge", emphasized=True, lang="zh")
        clock = _text(r, lambda: props.get("time", ""), clock_style,
                      lambda: theme_color("onSurface"), align="center")
        root.add(clock)
        root.add(_text(r, lambda: props.get("date", ""),
                       type_style("titleMedium", lang="zh"),
                       lambda: theme_color("onSurfaceVariant"), align="center"))

        # 通知预览（复用 MdNotification 的数据结构）。
        notes = r.element("view", {"dir": "column", "gap": 8})
        for item in _as_list(props.get("notifications")):
            data = dict(item) if hasattr(item, "items") else {"title": str(item)}
            notes.add(r.component_node("MdNotification", data, {}))
        root.add(notes)

        # widget 槽位（占位契约，只提供布局与回调）。
        widget_nodes = []
        for index, widget in enumerate(_as_list(props.get("widgets"))):
            data = widget if hasattr(widget, "get") else {"label": str(widget)}
            slot = r.element("view", {
                "dir": "column", "align": "center", "justify": "center",
                "h": _num(data.get("h"), 96), "radius": float(get_shape("large")),
                "padding": [12, 12],
            })
            r.bind_attr(slot, "bg", lambda: theme_color("surfaceContainerHigh"))
            slot.add(_text(r, lambda d=data: d.get("label", "widget"),
                           type_style("labelMedium", lang="zh"),
                           lambda: theme_color("onSurfaceVariant"), align="center"))

            def make_widget_click(i=index):
                def handler(ev):
                    callback = props.get("on_widget")
                    if callable(callback):
                        callback(i, ev)
                return handler

            r.on(slot, "click", lambda c=make_widget_click(): (lambda e=None: c(e)))
            set_a11y(slot, role="button", label="widget")
            widget_nodes.append(slot)
            root.add(slot)

        # 底部手势提示。
        hint = r.element("view", {"dir": "row", "align": "center",
                                  "justify": "center", "h": 24})
        pill = r.element("view", {"w": 108, "h": 4, "radius": 2})
        r.bind_attr(pill, "bg", lambda: theme_color("onSurfaceVariant"))
        hint.add(pill)
        root.add(hint)

        threshold = _num(props.get("height"), 800) * 0.2
        gesture = {"dy": 0.0, "unlocked": False}

        def apply_offset():
            dy = gesture["dy"]
            root.props["y"] = dy
            box = getattr(root, "box", None)
            if box is not None and box.w > 0:
                queue_dirty(box.x, box.y, box.w, box.h)

        def on_drag(ev):
            gesture["dy"] += getattr(ev, "dy", 0.0)
            apply_offset()

        def on_dragend(ev):
            if -gesture["dy"] > threshold:
                gesture["unlocked"] = True
                apply_offset()
                callback = props.get("on_unlock")
                if callable(callback):
                    callback(ev)
            else:
                gesture["dy"] = 0.0
                apply_offset()

        r.on(root, "drag", lambda: on_drag)
        r.on(root, "dragend", lambda: on_dragend)

        root.extra["clock"] = clock
        root.extra["time_style"] = clock_style
        root.extra["widgets"] = widget_nodes
        root.extra["unlock_threshold"] = threshold
        root.extra["gesture"] = gesture
        set_a11y(root, role=ROLE_LOCK_SCREEN, label=str(props.get("time", "")))
        return [root]


# ---------------------------------------------------------------------------
# 3.2 桌面图标网格
# ---------------------------------------------------------------------------


class MdAppIcon(Component):
    """单个桌面图标：圆角底板 + 图标 + 单行中文标签。"""

    name = "MdAppIcon"
    props = {"label": "", "icon": None, "on_open": None,
             "on_long_press": None, "size": 60}

    @staticmethod
    def build(r, props, slots):
        size = _num(props.get("size"), 60)

        root = r.element("view", {"dir": "column", "align": "center", "gap": 6})
        r.bind_attr(root, "w", lambda: size)

        tile = r.element("view", {"w": size, "h": size})
        morph = ShapeMorph(MorphSpec("large", "extraLarge"))
        tile.extra["shape_morph"] = morph

        def container_color():
            return theme_color("surfaceContainerHigh")

        def content_color():
            return theme_color("primary")

        def paint(canvas, box):
            _morph_shape(canvas, box, morph, container_color())
            paint_state_layer(canvas, tile, props, box, morph.radius, content_color())
            paint_ripple(canvas, tile, box)

        tile.props["paint"] = paint

        glyph = r.element("view", {"w": 28, "h": 28, "radius": 14})

        def glyph_paint(canvas, box):
            canvas.disc(box.x + box.w / 2, box.y + box.h / 2,
                        min(box.w, box.h) / 2, content_color())

        glyph.props["paint"] = glyph_paint
        tile.add(glyph)
        root.add(tile)

        root.add(_text(r, lambda: props.get("label", ""),
                       type_style("labelSmall", lang="zh"),
                       lambda: theme_color("onSurface"), align="center"))

        state = {"moved": False}

        def on_press(_ev):
            morph.press()
            box = getattr(tile, "box", None)
            if box is not None and box.w > 0:
                queue_dirty(box.x, box.y, box.w, box.h)

        def on_release(_ev):
            morph.release()
            box = getattr(tile, "box", None)
            if box is not None and box.w > 0:
                queue_dirty(box.x, box.y, box.w, box.h)

        def on_drag(ev):
            if abs(getattr(ev, "dx", 0.0)) + abs(getattr(ev, "dy", 0.0)) > _SLOP:
                state["moved"] = True

        def on_click(ev):
            if state["moved"]:
                return
            callback = props.get("on_open")
            if callable(callback):
                callback(ev)

        r.on(tile, "press", lambda: on_press)
        r.on(tile, "release", lambda: on_release)
        r.on(tile, "drag", lambda: on_drag)
        r.on(tile, "click", lambda: on_click)

        root.extra["tile"] = tile
        root.extra["morph"] = morph
        set_a11y(root, role="button", label=str(props.get("label", "")))
        return [root]


class MdAppGrid(Component):
    """桌面图标网格（按列数分行）+ 拖拽排序（回调重排）。"""

    name = "MdAppGrid"
    props = {"apps": [], "columns": 4, "on_reorder": None, "on_drag_end": None}

    @staticmethod
    def build(r, props, slots):
        apps = _as_list(props.get("apps"))
        columns = max(1, int(_num(props.get("columns"), 4)))

        root = r.element("view", {"dir": "column", "gap": 16, "padding": [8, 8]})

        icon_nodes = []
        index = 0
        while index < len(apps):
            row = r.element("view", {"dir": "row", "gap": 8, "justify": "start"})
            for _ in range(columns):
                if index >= len(apps):
                    break
                data = dict(apps[index]) if hasattr(apps[index], "items") else {"label": str(apps[index])}
                data.setdefault("on_open", None)
                icon = r.component_node("MdAppIcon", data, {})
                MdAppGrid._attach_drag(r, icon, index, props)
                icon_nodes.append(icon)
                row.add(icon)
                index += 1
            root.add(row)

        root.extra["icons"] = icon_nodes
        root.extra["apps"] = props.get("apps")
        set_a11y(root, role="grid", label="应用网格")
        return [root]

    @staticmethod
    def _attach_drag(r, icon, index, props):
        drag = {"dx": 0.0, "dy": 0.0}

        def on_dragstart(_ev):
            drag["dx"] = 0.0
            drag["dy"] = 0.0

        def on_drag(ev):
            drag["dx"] += getattr(ev, "dx", 0.0)
            drag["dy"] += getattr(ev, "dy", 0.0)

        def on_dragend(ev):
            delta = 0
            if abs(drag["dx"]) > 20:
                delta = 1 if drag["dx"] > 0 else -1
            target = index + delta
            callback = props.get("on_reorder")
            if callable(callback):
                callback(index, target)
            done = props.get("on_drag_end")
            if callable(done):
                done(ev)

        r.on(icon, "dragstart", lambda: on_dragstart)
        r.on(icon, "drag", lambda: on_drag)
        r.on(icon, "dragend", lambda: on_dragend)


# ---------------------------------------------------------------------------
# 3.3 手势导航条
# ---------------------------------------------------------------------------


class MdGestureNav(Component):
    """底部手势导航：可点击三段热区（返回/主页/多任务）+ 上滑手势。"""

    name = "MdGestureNav"
    props = {"on_back": None, "on_home": None, "on_recents": None,
             "height": 16, "color": None}

    @staticmethod
    def build(r, props, slots):
        bar_h = max(_num(props.get("height"), 16), 24.0)
        root = r.element("view", {"dir": "row", "align": "center", "h": bar_h})

        def bar_color():
            return theme_color("onSurface", props.get("color"))

        zones = {}

        def make_zone(name, color_owner=False):
            zone = r.element("view", {
                "dir": "row", "align": "center", "justify": "center",
                "grow": 1, "h": bar_h,
            })
            if color_owner:
                pill = r.element("view", {"w": 108, "h": 4, "radius": 2})
                r.bind_attr(pill, "bg", bar_color)
                zone.add(pill)
            zones[name] = zone
            return zone

        back = make_zone("back")
        home = make_zone("home", color_owner=True)
        recents = make_zone("recents")

        def bind_cb(zone, key):
            def handler(ev):
                callback = props.get(key)
                if callable(callback):
                    callback(ev)
            r.on(zone, "click", lambda c=handler: (lambda e=None: c(e)))

        bind_cb(back, "on_back")
        bind_cb(home, "on_home")
        bind_cb(recents, "on_recents")

        for name, zone in zones.items():
            set_a11y(zone, role="button", label=name)

        root.add(back)
        root.add(home)
        root.add(recents)

        root.extra["zones"] = zones
        set_a11y(root, role=ROLE_GESTURE_NAV, label="手势导航")
        return [root]


# ---------------------------------------------------------------------------
# 3.4 最近任务
# ---------------------------------------------------------------------------


class MdTaskCard(Component):
    """最近任务卡片：点击选中，上滑滑除。"""

    name = "MdTaskCard"
    props = {"title": "", "preview": "", "on_select": None, "on_dismiss": None,
             "w": 240, "h": 360}

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {"dir": "column", "gap": 8, "padding": [8, 8]})
        r.bind_attr(root, "w", lambda: _num(props.get("w"), 240))
        r.bind_attr(root, "h", lambda: _num(props.get("h"), 360))
        r.bind_attr(root, "radius", lambda: float(get_shape("large")))
        r.bind_attr(root, "bg", lambda: theme_color("surfaceContainerHigh"))

        morph = ShapeMorph(MorphSpec("large", "extraLarge"))
        root.extra["shape_morph"] = morph

        def content_color():
            return theme_color("onSurface")

        def paint(canvas, box):
            paint_state_layer(canvas, root, props, box, float(get_shape("large")),
                              content_color())
            paint_ripple(canvas, root, box)

        root.props["paint"] = paint

        preview = r.element("view", {"grow": 1, "radius": float(get_shape("medium"))})
        r.bind_attr(preview, "bg", lambda: theme_color("surfaceContainerLow"))
        preview.add(_text(r, lambda: props.get("preview", ""),
                          type_style("bodySmall", lang="zh"),
                          lambda: theme_color("onSurfaceVariant"), align="center"))
        root.add(preview)

        root.add(_text(r, lambda: props.get("title", ""),
                       type_style("labelMedium", lang="zh"), content_color,
                       align="center"))

        threshold = _num(props.get("h"), 360) * 0.4
        swipe = {"dy": 0.0, "moved": False, "dismissed": False}
        attach_ripple(root, content_color(), 0.10)

        def apply_offset():
            root.props["y"] = swipe["dy"]
            box = getattr(root, "box", None)
            if box is not None and box.w > 0:
                queue_dirty(box.x, box.y, box.w, box.h)

        def on_press(_ev):
            morph.press()
            ripple = root.extra.get("ripple")
            if ripple is not None:
                ripple.start(0, 0)

        def on_release(_ev):
            morph.release()
            ripple = root.extra.get("ripple")
            if ripple is not None:
                ripple.stop()

        def on_drag(ev):
            swipe["dy"] += getattr(ev, "dy", 0.0)
            swipe["moved"] = abs(swipe["dy"]) > _SLOP
            apply_offset()

        def on_dragend(ev):
            if -swipe["dy"] > threshold:
                swipe["dismissed"] = True
                swipe["moved"] = True
                apply_offset()
                callback = props.get("on_dismiss")
                if callable(callback):
                    callback(ev)
            else:
                swipe["dy"] = 0.0
                apply_offset()

        def on_click(ev):
            if swipe["moved"] or swipe["dismissed"]:
                return
            callback = props.get("on_select")
            if callable(callback):
                callback(ev)

        r.on(root, "press", lambda: on_press)
        r.on(root, "release", lambda: on_release)
        r.on(root, "drag", lambda: on_drag)
        r.on(root, "dragend", lambda: on_dragend)
        r.on(root, "click", lambda: on_click)

        root.extra["swipe"] = swipe
        root.extra["threshold"] = threshold
        set_a11y(root, role="button", label=str(props.get("title", "")))
        return [root]


class MdRecents(Component):
    """最近任务卡片栈（横排）+ 全部清除。"""

    name = "MdRecents"
    props = {"tasks": [], "on_clear": None}

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {"dir": "column", "gap": 12, "padding": [8, 8]})
        tasks = _as_list(props.get("tasks"))

        stack = r.element("view", {"dir": "row", "gap": 12, "align": "center"})
        card_nodes = []
        for task in tasks:
            data = dict(task) if hasattr(task, "items") else {"title": str(task)}
            card_nodes.append(r.component_node("MdTaskCard", data, {}))
            stack.add(card_nodes[-1])
        root.add(stack)

        if tasks:
            clear = r.element("view", {
                "dir": "row", "align": "center", "justify": "center", "h": 40,
            })
            clear.add(_text(r, lambda: "全部清除", type_style("labelLarge", lang="zh"),
                            lambda: theme_color("primary")))

            def on_clear_click(ev):
                callback = props.get("on_clear")
                if callable(callback):
                    callback(ev)

            r.on(clear, "click", lambda c=on_clear_click: (lambda e=None: c(e)))
            set_a11y(clear, role="button", label="全部清除")
            root.add(clear)

        root.extra["items"] = card_nodes
        set_a11y(root, role=ROLE_RECENTS, label="最近任务")
        return [root]


COMPONENTS = [
    MdLockScreen, MdAppGrid, MdAppIcon, MdGestureNav, MdRecents, MdTaskCard,
]


def manifest():
    return {
        "abi": 1,
        "name": "vapor.vos.launcher",
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
