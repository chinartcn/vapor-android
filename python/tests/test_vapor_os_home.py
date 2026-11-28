"""执行文档 2：主屏与 App 抽屉验收测试（纯逻辑 + 节点树断言）。"""

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
from Vapor.theme import current_theme, seed_to_scheme, set_theme  # noqa: E402
from Vapor.ui.renderer import Renderer  # noqa: E402
from Vapor.vos import chrome, launcher  # noqa: E402
from examples.vapor_os.screens.home import HomeScreen  # noqa: E402

_REG = {c.name: c for c in launcher.COMPONENTS}
_REG.update({c.name: c for c in chrome.COMPONENTS})
_REG[HomeScreen.name] = HomeScreen


def build(**props):
    r = Renderer(registry=_REG)
    node = r.component_node(HomeScreen.name, props, {})
    core.flush_jobs()
    return node.children[0]


def fire(node, event="click", **attrs):
    ev = SimpleNamespace(
        x=attrs.get("x", 0.0), y=attrs.get("y", 0.0),
        dx=attrs.get("dx", 0.0), dy=attrs.get("dy", 0.0),
    )
    for provider, _modifiers in list(node.events.get(event, ())):
        handler = provider()
        if callable(handler):
            handler(ev)


class _Canvas:
    def __init__(self):
        self.gradients = []

    def gradient(self, x, y, w, h, c1, c2, vertical=False):
        self.gradients.append((c1, c2, vertical))

    def round_rect(self, *args):
        pass

    def line(self, *args):
        pass

    def disc(self, *args):
        pass


def _apps(count=8):
    return [{"label": f"应用{i}", "icon": None} for i in range(count)]


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

def test_home_renders_grid():
    home = build(apps=_apps(8), columns=4)
    grid = home.extra["state"]["last_grid"]
    assert grid is not None
    assert len(grid.extra["icons"]) == 8


def test_app_click_fires_callback_only(monkeypatch):
    opened = []
    navigated = []
    monkeypatch.setattr(V, "navigate", lambda *a, **k: navigated.append(a))

    apps = [{"label": "设置", "icon": None}]
    home = build(apps=apps, on_open=lambda app: opened.append(app))
    grid = home.extra["state"]["last_grid"]
    icon = grid.extra["icons"][0]
    fire(icon.children[0].extra["tile"], "click")

    assert opened == [apps[0]]
    assert navigated == []


def test_swipe_up_opens_drawer():
    home = build(apps=_apps(4))
    drawer = home.extra["drawer"]
    threshold = drawer["threshold"]

    fire(home, "dragstart")
    fire(home, "drag", dy=-threshold * 0.25)
    fire(home, "dragend")
    assert drawer["open"].value is False

    fire(home, "dragstart")
    fire(home, "drag", dy=-(threshold + 20))
    fire(home, "dragend")
    assert drawer["open"].value is True


def test_paging_updates_indicator():
    pages = [_apps(4), [{"label": "第二页", "icon": None}]]
    home = build(apps=_apps(4), pages=pages)
    pager = home.extra["pager"]
    assert pager["count"] == 2

    fire(home, "dragstart")
    fire(home, "drag", dx=-220)
    fire(home, "dragend")
    core.flush_jobs()

    assert pager["page"].value == 1
    assert pager["dots"][1].props["bg"] == current_theme().role("primary")
    assert pager["dots"][0].props["bg"] == current_theme().role("outlineVariant")


def test_reduced_motion_snap(monkeypatch):
    monkeypatch.setenv("VAPOR_REDUCED_MOTION", "1")
    pages = [_apps(2), _apps(2)]
    home = build(apps=_apps(2), pages=pages)
    drawer = home.extra["drawer"]
    pager = home.extra["pager"]

    fire(home, "dragstart")
    fire(home, "drag", dx=-260)
    fire(home, "dragend")

    fire(home, "dragstart")
    fire(home, "drag", dy=-260)
    fire(home, "dragend")

    assert pager["page"].value == 1
    assert drawer["open"].value is True
    assert home.extra["state"]["pager_offset"] == 0
    assert home.extra["state"]["drawer_offset"] == 0


def test_widget_slot_contract():
    received = []
    widgets = [{"id": "w1", "span": 2, "title": "天气"}]
    home = build(apps=_apps(2), widgets=widgets,
                 on_widget=lambda widget_id: received.append(widget_id))

    slot = home.extra["widgets"][0]
    assert slot.extra["placeholder"] is True
    assert slot.extra["widget_id"] == "w1"
    # 只渲染占位标题，不含具体 widget 内容节点。
    assert len(slot.children) == 1

    fire(slot, "click")
    assert received == ["w1"]


def test_wallpaper_from_theme():
    original = current_theme()
    try:
        home = build(apps=_apps(1))
        paint = home.extra["wallpaper_paint"]

        canvas = _Canvas()
        paint(canvas, SimpleNamespace(x=0, y=0, w=120, h=80))
        assert canvas.gradients
        c1, c2, vertical = canvas.gradients[-1]
        assert c1 == current_theme().role("surfaceContainerLow")
        assert c2 == current_theme().role("surfaceContainerHigh")
        assert vertical is True

        set_theme(seed_to_scheme(0xFF0000FF))
        canvas = _Canvas()
        paint(canvas, SimpleNamespace(x=0, y=0, w=120, h=80))
        n1, n2, _ = canvas.gradients[-1]
        assert (n1, n2) == (current_theme().role("surfaceContainerLow"),
                            current_theme().role("surfaceContainerHigh"))
        assert (n1, n2) != (c1, c2)
    finally:
        set_theme(original)


def test_no_hardcoded_color():
    path = os.path.join(os.path.dirname(Vapor.__file__),
                        "..", "examples", "vapor_os", "screens", "home.py")
    with open(os.path.abspath(path), "r", encoding="utf-8") as fh:
        source = fh.read()
    assert not re.search(r"0xFF6750A4|#6750A4", source)
    assert not re.search(r"#[0-9A-Fa-f]{6}\b", source)
