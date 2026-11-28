"""执行文档 3：Vapor OS 系统交互场景验收测试。"""

from __future__ import annotations

import os
import re
import sys
from types import SimpleNamespace

try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Vapor import core  # noqa: E402
from Vapor.ui.renderer import Renderer  # noqa: E402
from Vapor.vos import chrome, launcher  # noqa: E402

from examples.vapor_os.screens import system  # noqa: E402

_REG = {c.name: c for c in chrome.COMPONENTS}
_REG.update({c.name: c for c in launcher.COMPONENTS})


def build(cls, **props):
    r = Renderer(registry=_REG)
    node = cls.instantiate(r, props, {})
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


def find_component(root, name):
    for n in root.walk():
        cls = getattr(n, "component", None)
        if cls is not None and getattr(cls, "name", "") == name:
            return n
    return None


NOTES = [
    {"id": 1, "title": "消息", "body": "你好"},
    {"id": 2, "title": "邮件", "body": "会议提醒"},
]
TILES = [
    {"label": "WiFi", "icon": "wifi", "on": True, "on_toggle": lambda i, v: None},
    {"label": "蓝牙", "icon": "phone", "on": False, "on_toggle": lambda i, v: None},
]


def test_shade_renders():
    shade = build(system.ShadeScreen, notifications=NOTES, tiles=TILES)
    assert find_component(shade, "MdQuickSettings") is not None
    assert find_component(shade, "MdNotificationCenter") is not None
    assert find_component(shade, "MdNotification") is not None


def test_pull_threshold():
    opened = []
    shade = build(system.ShadeScreen, notifications=NOTES, tiles=TILES,
                  on_open=lambda ev: opened.append(ev))
    threshold = shade.extra["threshold"]

    fire(shade, "drag", dy=threshold * 0.3)
    fire(shade, "dragend")
    assert shade.extra["state"]["expanded"] is False
    assert shade.extra["state"]["pull"] == 0

    fire(shade, "drag", dy=threshold + 30)
    fire(shade, "dragend")
    assert shade.extra["state"]["expanded"] is True
    assert len(opened) == 1


def test_notification_dismiss_delegates():
    dismissed = []
    shade = build(system.ShadeScreen, notifications=NOTES, tiles=TILES,
                  on_dismiss=lambda nid: dismissed.append(nid))
    notif = find_component(shade, "MdNotification")
    inner = notif.children[0]
    fire(inner, "press")
    fire(inner, "drag", dx=200)
    fire(inner, "dragend")
    assert dismissed == [1]
    # props 只读：原始通知数据未被改写。
    assert shade.extra["notifications"][0]["id"] == 1


def test_lock_unlock_callback(monkeypatch):
    unlocked = []
    nav_calls = []
    import Vapor.ui as V

    monkeypatch.setattr(V, "navigate", lambda *a, **k: nav_calls.append(a), raising=False)
    monkeypatch.setattr(V, "go_back", lambda *a, **k: nav_calls.append(a), raising=False)

    scene = build(system.LockScreenScene, time="09:41",
                  on_unlock=lambda ev: unlocked.append(ev))
    lock = find_component(scene, "MdLockScreen")
    inner = lock.children[0]
    threshold = inner.extra["unlock_threshold"]

    fire(inner, "drag", dy=-(threshold + 30))
    fire(inner, "dragend")
    assert len(unlocked) == 1
    assert nav_calls == []


def test_recents_select_callback():
    selected = []
    tasks = [{"id": 7, "title": "任务A", "preview": ""}]
    scene = build(system.RecentsScreen, tasks=tasks,
                  on_select=lambda tid: selected.append(tid))
    recents = find_component(scene, "MdRecents")
    card = recents.children[0].extra["items"][0]
    fire(card.children[0], "click")
    assert selected == [7]


def test_gesture_wiring():
    calls = []
    node = build(system.SystemGestures,
                 on_back=lambda ev: calls.append("back"),
                 on_home=lambda ev: calls.append("home"),
                 on_recents=lambda ev: calls.append("recents"))
    zones = node.children[0].extra["zones"]
    fire(zones["back"], "click")
    fire(zones["home"], "click")
    fire(zones["recents"], "click")
    assert calls == ["back", "home", "recents"]


def test_reduced_motion_snap(monkeypatch):
    monkeypatch.setenv("VAPOR_REDUCED_MOTION", "1")
    shade = build(system.ShadeScreen, notifications=NOTES, tiles=TILES)
    fire(shade, "drag", dy=20)
    fire(shade, "dragend")
    assert shade.extra["state"]["pull"] == 0
    assert shade.extra["state"]["expanded"] is False


def test_no_hardcoded_color():
    path = os.path.join(os.path.dirname(system.__file__), "system.py")
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    assert not re.search(r"0xFF6750A4|#6750A4", source)
    assert not re.search(r"#[0-9A-Fa-f]{6}\b", source)
