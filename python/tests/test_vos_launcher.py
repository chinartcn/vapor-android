"""执行文档 3：桌面与锁屏验收测试（纯逻辑 + 节点树断言）。"""

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
from Vapor.theme import get_shape, get_type_style  # noqa: E402
from Vapor.ui.renderer import Renderer  # noqa: E402
from Vapor.vos import chrome, launcher  # noqa: E402

_REG = {c.name: c for c in launcher.COMPONENTS}
_REG.update({c.name: c for c in chrome.COMPONENTS})


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
    names = {item["name"] for item in launcher.manifest()["components"]}
    assert len(names) == len(launcher.COMPONENTS)
    assert all(n.startswith("Md") for n in names)
    existing = set(V.COMPONENTS)
    try:
        from Vapor import presets

        existing |= {c.name for c in presets.PRESETS}
    except Exception:
        pass
    existing |= {c.name for c in chrome.COMPONENTS}
    assert names.isdisjoint(existing)


def test_lockscreen_unlock_threshold():
    unlocked = []
    lock = build(launcher.MdLockScreen, time="09:41", height=800,
                 on_unlock=lambda ev: unlocked.append(ev))
    threshold = lock.extra["unlock_threshold"]
    assert threshold == 160

    fire(lock, "drag", dy=-threshold * 0.4)
    fire(lock, "dragend")
    assert unlocked == []
    assert lock.extra["gesture"]["dy"] == 0

    fire(lock, "drag", dy=-(threshold + 30))
    fire(lock, "dragend")
    assert len(unlocked) == 1


def test_lockscreen_emph_time():
    lock = build(launcher.MdLockScreen, time="09:41")
    style = lock.extra["time_style"]
    expected = get_type_style("displayLarge", emphasized=True, lang_height_factor=1.07)
    assert style["fontSize"] == expected["fontSize"]
    assert style["lineHeight"] == expected["lineHeight"]
    assert lock.extra["clock"].props["size"] == expected["fontSize"]


def test_appgrid_reorder_readonly():
    apps = [{"label": f"应用{i}", "icon": None} for i in range(5)]
    reordered = []
    grid = build(launcher.MdAppGrid, apps=apps, columns=4,
                 on_reorder=lambda a, b: reordered.append((a, b)))

    icon0 = grid.extra["icons"][0]
    fire(icon0, "dragstart")
    fire(icon0, "drag", dx=30)
    fire(icon0, "dragend")
    assert reordered == [(0, 1)]
    # props 只读：应用数据未被改写。
    assert [a["label"] for a in grid.extra["apps"]] == [a["label"] for a in apps]


def test_appicon_shape_morph():
    icon = build(launcher.MdAppIcon, label="相机")
    morph = icon.extra["morph"]
    tile = icon.extra["tile"]
    assert morph.radius == get_shape("large")
    fire(tile, "press")
    assert morph.radius == get_shape("extraLarge")
    fire(tile, "release")
    assert morph.radius == get_shape("large")


def test_gesture_callbacks(monkeypatch):
    calls = []
    nav = build(launcher.MdGestureNav,
                on_back=lambda ev: calls.append("back"),
                on_home=lambda ev: calls.append("home"),
                on_recents=lambda ev: calls.append("recents"))

    navigated = []
    monkeypatch.setattr(launcher, "navigate", lambda *a, **k: navigated.append(a))

    zones = nav.extra["zones"]
    fire(zones["back"], "click")
    fire(zones["home"], "click")
    fire(zones["recents"], "click")
    assert calls == ["back", "home", "recents"]
    assert navigated == []  # 组件内部不得直接切页


def test_recents_dismiss(monkeypatch):
    dismissed = []
    card = build(launcher.MdTaskCard, title="任务", w=240, h=360,
                 on_dismiss=lambda ev: dismissed.append(ev))
    threshold = card.extra["threshold"]
    assert threshold == 144

    fire(card, "press")
    fire(card, "drag", dy=-(threshold + 20))
    fire(card, "dragend")
    assert len(dismissed) == 1
    assert card.extra["swipe"]["dismissed"] is True

    # reduced_motion：小位移瞬时复位。
    monkeypatch.setenv("VAPOR_REDUCED_MOTION", "1")
    snap = build(launcher.MdTaskCard, title="任务", w=240, h=360)
    fire(snap, "press")
    fire(snap, "drag", dy=-5)
    fire(snap, "dragend")
    assert snap.extra["swipe"]["dy"] == 0


def test_recents_selection():
    selected = []
    recents = build(launcher.MdRecents, tasks=[
        {"id": 1, "title": "任务A", "on_select": lambda ev: selected.append(1)},
    ])
    card = recents.extra["items"][0]
    fire(card.children[0], "click")
    assert selected == [1]


def test_no_hardcoded_color():
    path = os.path.join(os.path.dirname(Vapor.__file__), "vos", "launcher.py")
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    assert not re.search(r"0xFF6750A4|#6750A4", source)
    assert not re.search(r"#[0-9A-Fa-f]{6}\b", source)
