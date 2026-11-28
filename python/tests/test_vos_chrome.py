"""执行文档 2：系统 Chrome 验收测试（纯逻辑 + 节点树断言）。"""

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
from Vapor.theme import (  # noqa: E402
    TYPE_SCALE, Theme, baseline_dark, current_theme, get_shape, get_type_style, set_theme,
)
from Vapor.ui.a11y import get_a11y  # noqa: E402
from Vapor.ui.renderer import Renderer  # noqa: E402
from Vapor.vos import chrome  # noqa: E402

_REG = {c.name: c for c in chrome.COMPONENTS}


def build(cls, **props):
    r = Renderer(registry=_REG)
    node = r.component_node(cls.name, props, {})
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


def test_manifest_names_no_conflict():
    names = {item["name"] for item in chrome.manifest()["components"]}
    assert len(names) == len(chrome.COMPONENTS)
    assert all(n.startswith("Md") for n in names)
    existing = set(V.COMPONENTS)
    try:
        from Vapor import presets

        existing |= {c.name for c in presets.PRESETS}
    except Exception:
        pass
    assert names.isdisjoint(existing)


def test_status_bar_theme_follow():
    old = current_theme()
    try:
        light_bg = build(chrome.MdStatusBar, dark=False)
        assert light_bg.extra["icons"]["wifi"].props["color"] == current_theme().role("surface")

        dark_icons = build(chrome.MdStatusBar, dark=True)
        assert dark_icons.extra["icons"]["wifi"].props["color"] == current_theme().role("onSurface")

        set_theme(Theme(baseline_dark()))
        core.flush_jobs()
        assert dark_icons.extra["icons"]["wifi"].props["color"] == current_theme().role("onSurface")
    finally:
        set_theme(old)


def test_notification_dismiss_threshold():
    dismissed = []
    note = build(chrome.MdNotification, title="消息",
                 on_dismiss=lambda ev: dismissed.append(ev))
    threshold = note.extra["threshold"]

    fire(note, "press")
    fire(note, "drag", dx=threshold * 0.2)
    fire(note, "dragend")
    assert dismissed == []
    assert note.extra["swipe"]["dx"] == 0

    fire(note, "press")
    fire(note, "drag", dx=threshold + 20)
    fire(note, "dragend")
    assert len(dismissed) == 1
    assert note.extra["swipe"]["dismissed"] is True


def test_notification_reduced_motion_snap(monkeypatch):
    monkeypatch.setenv("VAPOR_REDUCED_MOTION", "1")
    dismissed = []
    note = build(chrome.MdNotification, title="消息",
                 on_dismiss=lambda ev: dismissed.append(ev))
    threshold = note.extra["threshold"]

    fire(note, "press")
    fire(note, "drag", dx=5)
    fire(note, "dragend")
    assert note.extra["swipe"]["dx"] == 0  # 瞬时复位

    fire(note, "press")
    fire(note, "drag", dx=threshold + 50)
    fire(note, "dragend")
    assert len(dismissed) == 1


def test_swipe_not_click():
    clicks = []
    note = build(chrome.MdNotification, title="消息",
                 on_click=lambda ev: clicks.append(ev))
    threshold = note.extra["threshold"]

    fire(note, "press")
    fire(note, "drag", dx=threshold + 20)
    fire(note, "dragend")
    fire(note, "click")
    assert clicks == []

    tap = build(chrome.MdNotification, title="消息",
                on_click=lambda ev: clicks.append(ev))
    fire(tap, "click")
    assert len(clicks) == 1


def test_quick_settings_toggle_readonly():
    calls = []
    tiles = [{"label": "WiFi", "icon": "wifi", "on": False,
              "on_toggle": lambda i, v: calls.append((i, v))}]
    qs = build(chrome.MdQuickSettings, tiles=tiles)
    tile = qs.extra["tiles"][0]

    fire(tile, "click")
    assert calls == [(0, True)]
    # props 只读：a11y 的 checked 仍为初始值，说明组件未改写数据。
    assert get_a11y(tile).state["checked"] is False


def test_qs_shape_morph():
    qs = build(chrome.MdQuickSettings, tiles=[{"label": "A", "on": False}])
    tile = qs.extra["tiles"][0]
    morph = tile.extra["shape_morph"]
    assert morph.radius == get_shape("large")
    fire(tile, "press")
    assert morph.radius == get_shape("extraLarge")
    fire(tile, "release")
    assert morph.radius == get_shape("large")


def test_chinese_type_style():
    note = build(chrome.MdNotification, body="正文内容")
    style = note.extra["body_style"]
    base = TYPE_SCALE["bodyMedium"]["lineHeight"]
    assert style["lineHeight"] == round(base * 1.07)
    assert style["lineHeight"] != get_type_style(
        "bodyMedium", lang_height_factor=1.0)["lineHeight"]


def test_status_bar_a11y():
    bar = build(chrome.MdStatusBar, time="12:00")
    a = get_a11y(bar)
    assert a is not None and a.role == "statusbar" and a.label == "12:00"


def test_no_hardcoded_color():
    path = os.path.join(os.path.dirname(Vapor.__file__), "vos", "chrome.py")
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    assert not re.search(r"0xFF6750A4|#6750A4", source)
    assert not re.search(r"#[0-9A-Fa-f]{6}\b", source)
