"""Vapor OS 演示 · 主屏（执行文档 2）。

壁纸 / 搜索栏 / 分页 App 网格 / Widget 占位槽 / 页面指示器 / Dock，以及
上滑打开 App 抽屉、左右翻页。颜色全部来自主题角色，路由一律通过
``on_open`` / ``on_search`` / ``on_widget`` 回调交给调用方。

本文件只新增自身，不修改任何现有文件；``screens/__init__.py`` 归文档 1。
"""

from __future__ import annotations

from Vapor import core
from Vapor.theme import current_theme, get_shape, seed_to_scheme
from Vapor.ui.a11y import Role as A11yRole, set_a11y
from Vapor.ui.motion import reduced_motion, spring
from Vapor.ui.renderer import Component, queue_dirty

from Vapor.vos.chrome import MdStatusBar  # noqa: F401  只读复用
from Vapor.vos.launcher import MdAppGrid, MdAppIcon  # noqa: F401  只读复用
from Vapor.vos_base.cjk import type_style

__all__ = ["HomeScreen"]

_KEY = "vapor.os.home"
_PAGE_THRESHOLD = 0.3   # 翻页：跟手位移超过宽度的 30%
_DRAWER_RATIO = 0.2     # 抽屉：上滑超过高度的 20%


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _role(name):
    """主题角色取色；每次调用现取，主题切换后重绘即生效。"""
    return current_theme().role(name)


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


def _label(r, getter, style="bodyMedium", role="onSurface", align=None):
    node = r.text("")
    r.bind_text(node, getter)
    node.props["size"] = type_style(style, lang="zh")["fontSize"]
    r.bind_attr(node, "color", lambda: _role(role))
    if align:
        node.props["align"] = align
    return node


def _data(item):
    return dict(item) if hasattr(item, "items") else {"label": str(item)}


def _box(node):
    box = getattr(node, "box", None)
    if box is None:
        return None
    return box


def _width(node, fallback=360.0):
    box = _box(node)
    if box is not None and box.w > 0:
        return float(box.w)
    return float(fallback)


def _height(node, fallback=720.0):
    box = _box(node)
    if box is not None and box.h > 0:
        return float(box.h)
    return float(fallback)


def _dirty(node):
    box = _box(node)
    if box is not None and box.w > 0 and box.h > 0:
        queue_dirty(box.x, box.y, box.w, box.h)


# ---------------------------------------------------------------------------
# HomeScreen
# ---------------------------------------------------------------------------

class HomeScreen(Component):
    """主屏：壁纸 + 分页图标网格 + Widget 槽 + Dock + App 抽屉。"""

    name = "HomeScreen"
    props = {
        "apps": [],
        "widgets": [],
        "columns": 4,
        "pages": [],
        "dock": [],
        "on_open": None,
        "on_widget": None,
        "on_search": None,
        "wallpaper_seed": None,
        "time": "",
    }

    @staticmethod
    def setup(props, ctx):
        state = {
            "page": core.ref(0),
            "drawer": core.ref(False),
            "pager_offset": 0.0,
            "drawer_offset": 0.0,
            "last_grid": None,
        }
        core.provide(_KEY, state)
        return {}

    @staticmethod
    def build(r, props, slots):
        state = core.inject(_KEY, None) or {
            "page": core.ref(0),
            "drawer": core.ref(False),
            "pager_offset": 0.0,
            "drawer_offset": 0.0,
            "last_grid": None,
        }

        pages = _as_list(props.get("pages"))
        page_count = max(1, len(pages))
        columns = max(1, int(_num(props.get("columns"), 4)))

        root = r.element("view", {"dir": "column", "w": "100%", "h": "100%"})

        # ---- 壁纸：主题角色渐变（不写死图片/颜色） ----
        seed = props.get("wallpaper_seed")

        def wallpaper_colors():
            try:
                theme = seed_to_scheme(int(seed)) if seed is not None else current_theme()
            except Exception:
                theme = current_theme()
            return theme.role("surfaceContainerLow"), theme.role("surfaceContainerHigh")

        def paint_wallpaper(canvas, box):
            if box.w <= 0 or box.h <= 0:
                return
            c1, c2 = wallpaper_colors()
            canvas.gradient(box.x, box.y, box.w, box.h, c1, c2, vertical=True)

        root.props["paint"] = paint_wallpaper

        # ---- 状态栏 ----
        root.add(r.component_node("MdStatusBar", {
            "time": props.get("time", ""),
            "dark": False,
        }, {}))

        # ---- 搜索栏 ----
        search = r.element("view", {
            "dir": "row", "align": "center", "h": 44, "radius": 22,
            "margin": [16, 8], "padding": [0, 14], "gap": 10,
        })
        r.bind_attr(search, "bg", lambda: _role("surfaceContainerHigh"))

        search_glyph = r.element("view", {"w": 18, "h": 18, "radius": 9})
        search_glyph.props["paint"] = (
            lambda canvas, box: canvas.disc(
                box.x + box.w / 2, box.y + box.h / 2,
                min(box.w, box.h) / 2, _role("onSurfaceVariant"))
        )
        search.add(search_glyph)
        search.add(_label(r, lambda: "搜索应用与设置", "bodyMedium", "onSurfaceVariant"))

        def on_search_click(ev):
            callback = props.get("on_search")
            if callable(callback):
                callback(ev)

        r.on(search, "click", lambda c=on_search_click: (lambda e=None: c(e)))
        set_a11y(search, role=A11yRole.BUTTON, label="搜索应用与设置")
        root.add(search)

        # ---- Widget 占位槽（只给契约，不做具体内容） ----
        widget_nodes = []
        widget_row = r.element("view", {"dir": "row", "w": "100%", "gap": 8,
                                        "padding": [16, 4]})
        for index, widget in enumerate(_as_list(props.get("widgets"))):
            data = _data(widget)
            widget_id = data.get("id", index)
            span = max(1, int(_num(data.get("span"), 1)))
            slot = r.element("view", {
                "grow": span, "h": 84,
                "radius": float(get_shape("medium")),
                "padding": 10, "justify": "end",
            })

            def slot_paint(canvas, box):
                if box.w <= 0 or box.h <= 0:
                    return
                radius = float(get_shape("medium"))
                canvas.round_rect(box.x, box.y, box.w, box.h, radius,
                                  _role("surfaceContainerLow"))
                outline = _role("outlineVariant")
                canvas.line(box.x + radius, box.y, box.x + box.w - radius, box.y, outline)
                canvas.line(box.x + radius, box.y + box.h - 1,
                            box.x + box.w - radius, box.y + box.h - 1, outline)
                canvas.line(box.x, box.y + radius, box.x, box.y + box.h - radius, outline)
                canvas.line(box.x + box.w - 1, box.y + radius,
                            box.x + box.w - 1, box.y + box.h - radius, outline)

            slot.props["paint"] = slot_paint
            slot.add(_label(r, lambda d=data: d.get("title", "Widget"),
                            "labelSmall", "onSurfaceVariant"))

            def on_widget_click(ev, widget_id=widget_id):
                callback = props.get("on_widget")
                if callable(callback):
                    callback(widget_id)

            r.on(slot, "click", lambda c=on_widget_click: (lambda e=None: c(e)))
            set_a11y(slot, role=A11yRole.BUTTON, label=str(data.get("title", "Widget")))
            slot.extra["widget_id"] = widget_id
            slot.extra["placeholder"] = True
            widget_nodes.append(slot)
            widget_row.add(slot)
        if widget_nodes:
            root.add(widget_row)

        # ---- 页面区：只构建当前页 ----
        pager = r.element("view", {"dir": "column", "grow": 1, "w": "100%",
                                   "overflow": "hidden"})
        page_frag = r.fragment()
        pager.add(page_frag)

        def open_app(app):
            callback = props.get("on_open")
            if callable(callback):
                callback(app)

        def wrap(apps_page):
            out = []
            for app in _as_list(apps_page):
                data = _data(app)
                data["on_open"] = lambda ev=None, a=app: open_app(a)
                out.append(data)
            return out

        def page_source():
            index = min(max(0, int(state["page"].value)), page_count - 1)
            if pages:
                return _as_list(pages[index]) if index < len(pages) else []
            return _as_list(props.get("apps"))

        def build_page():
            grid = r.component_node("MdAppGrid", {
                "apps": wrap(page_source()),
                "columns": columns,
            }, {})
            state["last_grid"] = grid.children[0] if grid.children else grid
            return [grid]

        # 条件恒真，但 build_page() 读取 page ref —— 翻页时 effect 重跑，
        # 旧页节点被 dispose，避免一次构建全部分页。
        r.bind_if(page_frag, [(lambda: True, build_page)])
        root.add(pager)

        # ---- 页面指示器 ----
        indicator = r.element("view", {
            "dir": "row", "justify": "center", "align": "center",
            "gap": 6, "h": 18,
        })
        dots = []
        for index in range(page_count):
            dot = r.element("view", {"w": 6, "h": 6, "radius": 3})
            r.bind_attr(dot, "bg", lambda i=index: (
                _role("primary") if state["page"].value == i else _role("outlineVariant")
            ))
            dots.append(dot)
            indicator.add(dot)
        root.add(indicator)

        # ---- Dock ----
        dock_nodes = []
        dock_apps = _as_list(props.get("dock"))
        if dock_apps:
            dock = r.element("view", {
                "dir": "row", "align": "center", "justify": "space-around",
                "margin": [16, 8], "padding": [8, 6],
                "radius": float(get_shape("extraLarge")),
            })
            r.bind_attr(dock, "bg", lambda: _role("surfaceContainerHigh"))
            for app in dock_apps:
                data = _data(app)
                data["size"] = 52
                data["on_open"] = lambda ev=None, a=app: open_app(a)
                dock_nodes.append(r.component_node("MdAppIcon", data, {}))
                dock.add(dock_nodes[-1])
            root.add(dock)

        # ---- App 抽屉（全屏，按名称排序） ----
        drawer = r.element("view", {
            "absolute": True, "x": 0, "y": 0, "w": "100%", "h": "100%",
            "dir": "column", "padding": [16, 28], "gap": 12,
        })
        r.bind_attr(drawer, "bg", lambda: _role("surface"))
        drawer.add(_label(r, lambda: "全部应用", "titleLarge", "onSurface"))

        sorted_apps = sorted(
            _as_list(props.get("apps")),
            key=lambda a: str(_data(a).get("label", "")),
        )
        drawer_grid = r.component_node("MdAppGrid", {
            "apps": wrap(sorted_apps),
            "columns": columns,
        }, {})
        drawer.add(drawer_grid)
        r.bind_show(drawer, lambda: state["drawer"].value)
        set_a11y(drawer, role=A11yRole.NAVIGATION, label="应用抽屉")
        root.add(drawer)

        # ---- 手势：横滑翻页 / 上滑抽屉 ----
        drag = {"dx": 0.0, "dy": 0.0, "active": False}
        drawer_threshold = _height(root) * _DRAWER_RATIO

        def settle():
            state["pager_offset"] = 0.0
            state["drawer_offset"] = 0.0
            pager.props["x"] = 0
            pager.props["y"] = 0
            _dirty(pager)

        def animate_home(start_x, start_y):
            """释放后按 expressive spatial 弹簧回位；reduced_motion 直接落位。"""
            if reduced_motion():
                settle()
                return
            ease = spring("expressive", "default", "spatial")
            frames = 8

            def frame(index=0):
                progress = ease(min(1.0, index / frames))
                pager.props["x"] = start_x * (1 - progress)
                pager.props["y"] = start_y * (1 - progress)
                _dirty(pager)
                if index >= frames:
                    settle()
                    return
                core.next_tick(lambda: frame(index + 1))

            frame(0)

        def on_dragstart(_ev):
            drag["dx"] = 0.0
            drag["dy"] = 0.0
            drag["active"] = True

        def on_drag(ev):
            drag["dx"] += _num(getattr(ev, "dx", 0.0), 0)
            drag["dy"] += _num(getattr(ev, "dy", 0.0), 0)
            if abs(drag["dy"]) > abs(drag["dx"]):
                if not state["drawer"].value:
                    state["drawer_offset"] = min(0.0, drag["dy"])
                    pager.props["y"] = state["drawer_offset"]
            else:
                state["pager_offset"] = drag["dx"] * 0.35
                pager.props["x"] = state["pager_offset"]
            _dirty(pager)

        def on_dragend(_ev):
            drag["active"] = False
            width = _width(pager)
            horizontal = abs(drag["dx"]) >= abs(drag["dy"])

            if horizontal and abs(drag["dx"]) >= width * _PAGE_THRESHOLD:
                delta = 1 if drag["dx"] < 0 else -1
                next_page = min(max(0, state["page"].value + delta), page_count - 1)
                state["page"].value = next_page

            if not horizontal and -drag["dy"] >= drawer_threshold:
                state["drawer"].value = True
            elif not horizontal and state["drawer"].value and drag["dy"] >= drawer_threshold * 0.5:
                state["drawer"].value = False

            # 状态立即落到目标页/抽屉；跟手位移用弹簧回位。
            animate_home(state["pager_offset"], state["drawer_offset"])

        r.on(root, "dragstart", lambda: on_dragstart)
        r.on(root, "drag", lambda: on_drag)
        r.on(root, "dragend", lambda: on_dragend)

        root.extra["wallpaper_paint"] = paint_wallpaper
        root.extra["search"] = search
        root.extra["widgets"] = widget_nodes
        root.extra["pager"] = {
            "page": state["page"],
            "count": page_count,
            "dots": dots,
            "node": pager,
            "threshold": _PAGE_THRESHOLD,
            "offset_key": "pager_offset",
        }
        root.extra["drawer"] = {
            "open": state["drawer"],
            "node": drawer,
            "grid": drawer_grid,
            "threshold": drawer_threshold,
        }
        root.extra["dock"] = dock_nodes
        root.extra["state"] = state
        set_a11y(root, role=A11yRole.NAVIGATION, label="主屏")
        return [root]
