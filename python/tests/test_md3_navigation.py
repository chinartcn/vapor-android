"""执行文档 3：MD3 导航与容器类组件验收测试（纯逻辑 + 节点树断言）。"""

from __future__ import annotations

import os
import re
import sys
from types import SimpleNamespace

try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import Vapor.ui as V  # noqa: E402
from Vapor import core  # noqa: E402
from Vapor.md3 import navigation  # noqa: E402
from Vapor.theme import (  # noqa: E402
    Theme, baseline_dark, current_theme, get_shape, get_type_style, set_theme,
)
from Vapor.ui.a11y import get_a11y  # noqa: E402
from Vapor.ui.base import _TEXT  # noqa: E402
from Vapor.ui.navigation import Navigator  # noqa: E402
from Vapor.ui.renderer import Renderer  # noqa: E402

_REG = {c.name: c for c in navigation.COMPONENTS}


def build(cls, **props):
    r = Renderer(registry=_REG)
    node = r.component_node(cls.name, props, {})
    core.flush_jobs()
    return node.children[0]


def fire(node, event="click", **attrs):
    ev = SimpleNamespace(x=attrs.get("x", 0.0), y=attrs.get("y", 0.0))
    for provider, _modifiers in list(node.events.get(event, ())):
        handler = provider()
        if callable(handler):
            handler(ev)


def first_text(node):
    for n in node.walk():
        if n.tag == _TEXT:
            return n
    return None


NAV_ITEMS = [
    {"label": "首页", "icon": "home", "value": "home"},
    {"label": "搜索", "icon": "search", "value": "search"},
    {"label": "设置", "icon": "settings", "value": "settings"},
]


def test_manifest_no_conflict():
    names = {item["name"] for item in navigation.manifest()["components"]}
    assert len(names) == len(navigation.COMPONENTS)
    existing = set(V.COMPONENTS)
    try:
        from Vapor import presets

        existing |= {c.name for c in presets.PRESETS}
    except Exception:
        pass
    assert names.isdisjoint(existing)


def test_navbar_3_to_5_items():
    for count in (3, 4, 5):
        items = [{"label": f"项{i}", "icon": "home", "value": i} for i in range(count)]
        bar = build(navigation.MdNavigationBar, items=items, active=0)
        assert len(bar.extra["items"]) == count

    six = [{"label": f"项{i}", "icon": "home", "value": i} for i in range(6)]
    bar = build(navigation.MdNavigationBar, items=six, active=0)  # 告警但不崩溃
    assert len(bar.extra["items"]) == 6


def test_navbar_indicator_follows_active():
    bar = build(navigation.MdNavigationBar, items=NAV_ITEMS, active="search")
    indicators = bar.extra["indicators"]
    assert indicators["search"].visible is True
    assert indicators["home"].visible is False
    assert indicators["settings"].visible is False


def test_navbar_theme_colors():
    old = current_theme()
    try:
        bar = build(navigation.MdNavigationBar, items=NAV_ITEMS, active="home")
        assert bar.props["bg"] == current_theme().role("surfaceContainer")
        assert bar.extra["icons"]["home"].props["color"] == current_theme().role("primary")
        assert bar.extra["icons"]["search"].props["color"] == current_theme().role("onSurfaceVariant")

        set_theme(Theme(baseline_dark()))
        core.flush_jobs()
        new_theme = current_theme()
        assert bar.props["bg"] == new_theme.role("surfaceContainer")
        assert bar.extra["icons"]["home"].props["color"] == new_theme.role("primary")
    finally:
        set_theme(old)


def test_tabs_primary_secondary():
    tabs = [{"label": "全部", "value": "all"}, {"label": "未读", "value": "unread"}]

    primary = build(navigation.MdTabs, tabs=tabs, active="all", variant="primary")
    assert primary.props["h"] == 48
    assert primary.extra["indicator_span"] == "full"
    assert primary.extra["indicators"]["all"].props["bg"] == current_theme().role("primary")
    assert first_text(primary).props["size"] == get_type_style("labelLarge")["fontSize"]

    secondary = build(navigation.MdTabs, tabs=tabs, active="all", variant="secondary")
    assert secondary.props["h"] == 40
    assert secondary.extra["indicator_span"] == "text"
    assert secondary.extra["indicators"]["all"].visible is True


def test_topappbar_back_optional():
    Navigator.current = None
    bar = build(navigation.MdTopAppBar, title="标题", show_back=False)
    assert bar.extra["back"].visible is False
    # Navigator.current 为 None 时点击不应报错。
    fire(bar.extra["back"], "click")

    seen = []
    bar2 = build(navigation.MdTopAppBar, title="标题", show_back=True,
                 on_back=lambda ev: seen.append(ev))
    assert bar2.extra["back"].visible is True
    fire(bar2.extra["back"], "click")
    assert len(seen) == 1

    large = build(navigation.MdTopAppBar, title="大标题", variant="large")
    assert large.props["h"] == 128
    assert build(navigation.MdTopAppBar, variant="small").props["h"] == 64


def test_card_variants():
    elevated = build(navigation.MdCard)
    assert elevated.props["bg"] == current_theme().role("surfaceContainerLow")
    assert elevated.props["radius"] == get_shape("medium")

    filled = build(navigation.MdCard, variant="filled")
    assert filled.props["bg"] == current_theme().role("surfaceContainerHighest")

    outlined = build(navigation.MdCard, variant="outlined")
    assert outlined.props["bg"] == current_theme().role("surface")
    assert tuple(outlined.props["border"]) == (1, current_theme().role("outline"))


def test_card_click_a11y():
    seen = []
    card = build(navigation.MdCard, on_click=lambda ev: seen.append(ev))
    a = get_a11y(card)
    assert a is not None and a.role == "button"
    fire(card, "click")
    assert len(seen) == 1


def test_listitem_one_two_line():
    single = build(navigation.MdListItem, title="单行")
    assert single.props["h"] == 56
    assert get_a11y(single).label == "单行"

    double = build(navigation.MdListItem, title="双行", subtitle="副标题")
    assert double.props["h"] == 72


def test_badge_error_color():
    items = [{"label": "消息", "icon": "home", "value": "m", "badge": 5}]
    bar = build(navigation.MdNavigationBar, items=items, active="m")
    badge = bar.extra["badges"]["m"]
    assert badge.props["bg"] == current_theme().role("error")


def test_a11y_on_all():
    expected = {
        navigation.MdNavigationBar: "navigation",
        navigation.MdNavigationRail: "navigation",
        navigation.MdTabs: "navigation",
        navigation.MdTopAppBar: "navigation",
        navigation.MdCard: "text",
        navigation.MdList: "navigation",
        navigation.MdListItem: "listItem",
    }
    for cls, role in expected.items():
        node = build(cls)
        a = get_a11y(node)
        assert a is not None, cls.name
        assert a.role == role, cls.name


def test_navbar_navigation_callback():
    changed = []
    bar = build(navigation.MdNavigationBar, items=NAV_ITEMS, active="home",
                on_change=changed.append)
    fire(bar.extra["items"][1], "click")
    assert changed[-1] == "search"


def test_no_hardcoded_hex():
    path = os.path.join(os.path.dirname(Vapor.__file__), "md3", "navigation.py")
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    assert not re.search(r"0xFF6750A4|#6750A4", source)
    assert not re.search(r"#[0-9A-Fa-f]{6}\b", source)
