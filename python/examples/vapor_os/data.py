"""Vapor OS 演示 · 示例数据与路由总装（执行文档 4）。

把文档 2 的主屏、文档 3 的系统场景与四个内置应用收进同一张路由表，
供文档 1 的 ``build_app(routes=...)`` 注入 ``VaporShell``。

本文件只读 import 前三份文档与库组件，不修改任何现有文件；颜色字段只写
主题角色名，绝不写 hex。文档 3 尚未落地时用同名占位场景兜底，合入后自动
切换到真实实现。
"""

from __future__ import annotations

from Vapor import core
from Vapor.theme import current_theme  # noqa: F401  只读，路由/数据不写主题
from Vapor.ui import Component
from Vapor.ui.a11y import Role as A11yRole, set_a11y
from Vapor.vos.apps import (
    MdCalculatorApp,
    MdClockApp,
    MdGalleryApp,
    MdSettingsApp,
)
from Vapor.vos_base.cjk import type_style

from .screens.home import HomeScreen

try:  # 文档 3：系统交互场景
    from .screens.system import (  # type: ignore
        LockScreenScene,
        RecentsScreen,
        ShadeScreen,
    )
except Exception:  # pragma: no cover - 文档 3 未合入时的占位场景
    def _fallback_scene(name, title):
        def build(r, props, slots):
            root = r.element("view", {
                "dir": "column", "w": "100%", "h": "100%",
                "align": "center", "justify": "center", "gap": 8, "padding": 24,
            })
            r.bind_attr(root, "bg", lambda: current_theme().role("surface"))

            node = r.text(title)
            node.props["size"] = type_style("titleLarge", lang="zh")["fontSize"]
            r.bind_attr(node, "color", lambda: current_theme().role("onSurface"))
            root.add(node)

            hint = r.text("场景实现待文档 3 合入")
            hint.props["size"] = type_style("bodyMedium", lang="zh")["fontSize"]
            r.bind_attr(hint, "color",
                        lambda: current_theme().role("onSurfaceVariant"))
            root.add(hint)

            set_a11y(root, role=A11yRole.TEXT, label=title)
            return [root]

        return type(name, (Component,), {
            "name": name,
            "props": {"on_open": None},
            "build": staticmethod(build),
        })

    ShadeScreen = _fallback_scene("ShadeScreen", "通知中心")
    LockScreenScene = _fallback_scene("LockScreenScene", "锁屏")
    RecentsScreen = _fallback_scene("RecentsScreen", "最近任务")

__all__ = [
    "sample_apps",
    "sample_dock",
    "sample_notifications",
    "sample_qs_tiles",
    "sample_tasks",
    "HomeRoute",
    "build_routes",
    "make_open",
    "ROUTES",
]


# ---------------------------------------------------------------------------
# 示例数据
# ---------------------------------------------------------------------------

def sample_apps() -> list[dict]:
    """桌面/抽屉 App 列表：``route`` 供 Shell 路由，``on_open`` 由总装填充。"""
    return [
        {"label": "设置", "icon": "settings", "route": "settings"},
        {"label": "时钟", "icon": "clock", "route": "clock"},
        {"label": "相册", "icon": "gallery", "route": "gallery"},
        {"label": "计算器", "icon": "calculator", "route": "calculator"},
        {"label": "通知", "icon": "shade", "route": "shade"},
        {"label": "锁屏", "icon": "lock", "route": "lock"},
        {"label": "最近任务", "icon": "recents", "route": "recents"},
        {"label": "主屏", "icon": "home", "route": "home"},
    ]


def sample_dock() -> list[dict]:
    """Dock 常驻入口（演示用，全部指向已注册路由）。"""
    return [
        {"label": "设置", "icon": "settings", "route": "settings"},
        {"label": "时钟", "icon": "clock", "route": "clock"},
        {"label": "相册", "icon": "gallery", "route": "gallery"},
        {"label": "计算器", "icon": "calculator", "route": "calculator"},
    ]


def sample_notifications() -> list[dict]:
    """通知中心示例数据，文案中文。"""
    return [
        {
            "title": "消息",
            "body": "小林：晚上的演示准备好了吗？",
            "icon": "message",
            "timestamp": "刚刚",
            "dismissible": True,
        },
        {
            "title": "日历",
            "body": "10:30 产品评审 · 会议室 A",
            "icon": "calendar",
            "timestamp": "5 分钟前",
            "dismissible": True,
        },
        {
            "title": "系统更新",
            "body": "Vapor OS 1.0 已可安装",
            "icon": "system",
            "timestamp": "1 小时前",
            "dismissible": False,
        },
        {
            "title": "健康",
            "body": "今日已走 6,240 步",
            "icon": "health",
            "timestamp": "2 小时前",
            "dismissible": True,
        },
    ]


def sample_qs_tiles() -> list[dict]:
    """快捷设置瓷砖：7 个常用开关。"""
    return [
        {"label": "无线网络", "icon": "wifi", "on": True},
        {"label": "蓝牙", "icon": "bluetooth", "on": False},
        {"label": "飞行模式", "icon": "flight", "on": False},
        {"label": "手电筒", "icon": "flashlight", "on": False},
        {"label": "勿扰模式", "icon": "dnd", "on": True},
        {"label": "自动旋转", "icon": "rotate", "on": True},
        {"label": "省电模式", "icon": "battery", "on": False},
    ]


def sample_tasks() -> list[dict]:
    """最近任务卡片。"""
    return [
        {"id": "settings", "title": "设置", "preview": "显示与亮度"},
        {"id": "gallery", "title": "相册", "preview": "最近 9 张照片"},
        {"id": "calculator", "title": "计算器", "preview": "128 × 8 = 1024"},
    ]


# ---------------------------------------------------------------------------
# 总装：路由表
# ---------------------------------------------------------------------------

def make_open(route):
    """返回只记录目标 route 的 ``on_open`` 闭包（真正的切页交给宿主）。"""

    def open_route(_ev=None):
        return route

    return open_route


def _dispatch(nav, target):
    """把 App 点击（dict 带 route 或直接 route 字符串）交给 Shell 的导航回调。"""
    route = target.get("route") if isinstance(target, dict) else target
    if route and callable(nav):
        nav(str(route))


def _scene_data(component_cls):
    """按组件声明的 props 名注入示例数据；未声明的键不会传。"""
    schema = getattr(component_cls, "props", None) or {}
    data = {}
    if "notifications" in schema:
        data["notifications"] = sample_notifications()
    if "tiles" in schema:
        data["tiles"] = sample_qs_tiles()
    if "tasks" in schema:
        data["tasks"] = sample_tasks()
    if "time" in schema and not schema.get("time"):
        data["time"] = "09:41"
    return data


def _isolated(component_cls):
    """把场景组件包一层：内部建树不进入 Shell 路由 effect 的依赖收集。

    ``VaporShell`` 在 route effect 里直接实例化页面；若页面在 build 中写
    组件 props（如计算器 ``bind_attr(enabled)``），该 props signal 会被
    route effect 订阅，写入即触发整页重建，形成重入。这里用 ``untrack``
    把内部读取隔离开，路由 effect 只依赖 route 本身。
    """

    class _Route(Component):
        name = f"Route{component_cls.name}"
        props = dict(component_cls.props)
        wrapped = component_cls

        @staticmethod
        def build(r, props, slots):
            data = {key: props.get(key) for key in props.keys()}
            data.update(_scene_data(component_cls))
            node = core.untrack(
                lambda: component_cls.instantiate(r, data, {}))
            return [node]

    return _Route


class HomeRoute(HomeScreen):
    """主屏路由：给 ``HomeScreen`` 注入示例 App/Dock，并接上 Shell 导航。"""

    name = "HomeRoute"
    props = dict(HomeScreen.props)
    setup = HomeScreen.setup

    @staticmethod
    def build(r, props, slots):
        nav = props.get("on_open")
        apps = sample_apps()
        for app in apps:
            app["on_open"] = make_open(app.get("route", "home"))
        # 用普通 dict 承载派生 props：写 Reactive props 会触发 Shell 的
        # render_route effect，造成重入建树。
        local = {key: props.get(key) for key in props.keys()}
        local["apps"] = apps
        local["dock"] = sample_dock()
        # HomeScreen 点击时回传整个 app dict，这里统一映射成 route。
        local["on_open"] = lambda target: _dispatch(nav, target)
        return core.untrack(lambda: HomeScreen.build(r, local, slots))


def build_routes() -> dict[str, object]:
    """返回 ``{route_name: 组件类}``，供 ``build_app(routes=...)`` 使用。"""
    return {
        "home": HomeRoute,
        "settings": _isolated(MdSettingsApp),
        "clock": _isolated(MdClockApp),
        "gallery": _isolated(MdGalleryApp),
        "calculator": _isolated(MdCalculatorApp),
        "shade": _isolated(ShadeScreen),
        "lock": _isolated(LockScreenScene),
        "recents": _isolated(RecentsScreen),
    }


# 文档 1 的 ``build_app()`` 在未显式传 routes 时会读取本常量。
ROUTES = build_routes()
